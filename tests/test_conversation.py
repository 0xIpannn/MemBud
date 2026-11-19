"""Tests for conversation ingestion, transcript grounding, extractors, and session evaluation."""
from __future__ import annotations

import hashlib
import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from membud import core, service
from membud.conversation import (
    CONVERSATION_EXTRACTOR_PROMPT_VERSION,
    TRANSCRIPT_CHECK_NAME,
    ConversationError,
    ConversationTranscript,
    ConversationTurn,
    LLMConversationExtractor,
    RuleBasedConversationExtractor,
    build_transition_from_conversation,
    load_conversation,
    load_conversations_jsonl,
    main as conversation_cli_main,
    parse_conversation,
    run_conversation_session,
    verify_transcript_grounding,
    verify_with_transcript,
)
from membud.llm.client import JSONCompletion
from membud.schemas import Check, Record, Result, Transition
from membud.semantic import SemanticRelation
from membud.store import commit, run_case

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers & fakes
# ---------------------------------------------------------------------------


class _FakeSemanticComparator:
    def __init__(self, relation: SemanticRelation = SemanticRelation.EQUIVALENT) -> None:
        self.relation = relation
        self.prompt_version = "fake-semantic-v1"

    @property
    def name(self) -> str:
        return "fake-semantic"

    @property
    def configuration(self) -> dict[str, Any]:
        return {"type": "fake-semantic"}

    @property
    def usage(self) -> dict[str, int]:
        return {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    def compare(self, a: str, b: str) -> SemanticRelation:
        return self.relation


class _FakeJSONClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.model = "fake-extractor-model"
        self.payload = payload
        self.calls: list[list[dict[str, str]]] = []

    def complete_json(self, messages: list[dict[str, str]], validator=None) -> JSONCompletion:
        self.calls.append(messages)
        if validator is not None:
            validator(self.payload)
        return JSONCompletion(
            json=self.payload,
            content=json.dumps(self.payload),
            model=self.model,
            input_tokens=120,
            output_tokens=40,
            total_tokens=160,
            request_id="req-fake-1",
            latency_ms=12.5,
            request_attempts=1,
            json_retries=0,
            cached=False,
        )


def _sample_transcript() -> ConversationTranscript:
    return parse_conversation(
        {
            "conversation_id": "conv-sample",
            "scope": {"user_id": "u1"},
            "turns": [
                {"turn_id": "t1", "role": "user", "content": "Saya tinggal di Bandung."},
                {"turn_id": "t2", "role": "assistant", "content": "Baik, apakah Anda punya kapal pesiar?"},
                {"turn_id": "t3", "role": "user", "content": "Saya sekarang bekerja di Tokopedia."},
                {"turn_id": "t4", "role": "tool", "content": '{"result": "salary=500jt"}'},
                {"turn_id": "t5", "role": "user", "content": "Tolong hapus catatan Bandung saya."},
            ],
        },
        format="membud",
    )


# ---------------------------------------------------------------------------
# Transcript model & multi-format parsers
# ---------------------------------------------------------------------------


def test_conversation_turn_and_transcript_validation_and_slicing():
    t1 = ConversationTurn(turn_id="t1", role="Human", content="Hello, I live in Seattle.", session_id="s1")
    t2 = ConversationTurn(turn_id="t2", role="Pengguna", content="I work at Northwind.")
    assert t1.role == "user"
    assert t2.role == "user"
    assert t1.source_ref == "session:s1:turn:t1"
    assert t1.matches_source_ref("t1")
    assert t1.matches_source_ref("turn:t1")
    assert t1.matches_source_ref("chat:t1")
    assert t1.matches_source_ref("session:s1:turn:t1")
    assert t1.matches_source_ref("s1:t1")
    assert not t1.matches_source_ref("session:s2:turn:t1")

    transcript = ConversationTranscript(conversation_id="c1", turns=(t1, t2), scope={"user_id": "u1"})
    round_trip = ConversationTranscript.from_dict(transcript.to_dict())
    assert round_trip == transcript
    assert transcript.turn_by_id("session:s1:turn:t1") == t1
    assert len(transcript.user_turns()) == 2

    sliced = transcript.slice_turns(up_to_turn_id="t1")
    assert [turn.turn_id for turn in sliced.turns] == ["t1"]
    subset = transcript.slice_turns(turn_ids=["t2"])
    assert [turn.turn_id for turn in subset.turns] == ["t2"]

    with pytest.raises(ConversationError):
        ConversationTurn(turn_id=" ", role="user", content="x")
    with pytest.raises(ConversationError):
        ConversationTurn(turn_id="t1", role="moderator", content="x")
    with pytest.raises(ConversationError):
        ConversationTranscript(conversation_id="c1", turns=(t1, ConversationTurn("t1", "user", "dup")))
    with pytest.raises(ConversationError):
        ConversationTranscript(conversation_id="c1", turns=(t1,), scope={"uid": 123})  # type: ignore[dict-item]
    with pytest.raises(ConversationError):
        transcript.slice_turns(up_to_turn_id="missing")


def test_parse_all_conversation_formats_and_fail_closed(tmp_path):
    # 1. OpenAI format (list + content blocks)
    openai_raw = [
        {"role": "user", "content": [{"type": "text", "text": "I live in Austin."}]},
        {"role": "assistant", "content": "Noted Austin.", "name": "helper"},
    ]
    openai_t = parse_conversation(openai_raw, conversation_id="oa-1")
    assert [t.turn_id for t in openai_t.turns] == ["t1", "t2"]
    assert openai_t.turns[0].content == "I live in Austin."
    assert openai_t.turns[1].speaker == "helper"

    # 2. opencode JSONL event stream
    opencode_jsonl = "\n".join(
        [
            json.dumps({"type": "step_start", "sessionID": "ses_1", "timestamp": 100}),
            json.dumps({"type": "user", "sessionID": "ses_1", "timestamp": 101, "part": {"id": "u1", "text": "I live in Bandung."}}),
            json.dumps({"type": "tool_use", "sessionID": "ses_1", "timestamp": 102, "part": {"callID": "call_1", "tool": "memory_write", "state": {"status": "completed", "output": "ok"}}}),
            json.dumps({"type": "text", "sessionID": "ses_1", "timestamp": 103, "part": {"id": "p1", "messageID": "m1", "text": "Saved"}}),
            json.dumps({"type": "text", "sessionID": "ses_1", "timestamp": 104, "part": {"id": "p2", "messageID": "m1", "text": "your location."}}),
            json.dumps({"type": "step_finish", "sessionID": "ses_1", "timestamp": 105}),
        ]
    )
    oc_t = parse_conversation(opencode_jsonl)
    assert oc_t.conversation_id == "ses_1"
    assert [(t.turn_id, t.role) for t in oc_t.turns] == [
        ("u1", "user"),
        ("call_1", "tool"),
        ("p1", "assistant"),
    ]
    assert oc_t.turns[2].content == "Saved\nyour location."

    # 3. HaluMem-shaped sessions object
    halumem_raw = {
        "uuid": "hm-user-1",
        "persona_info": "Fictional test persona",
        "sessions": [
            {
                "session_id": "s1",
                "dialogue": [
                    {"role": "user", "content": "I work at Gojek.", "timestamp": "2026-01-01T10:00:00Z"},
                    {"role": "assistant", "content": "Got it.", "timestamp": "2026-01-01T10:00:05Z"},
                ],
            },
            {
                "session_id": "s2",
                "dialogue": [
                    {"role": "user", "content": "I moved to Surabaya.", "timestamp": "2026-01-02T09:00:00Z"},
                ],
            },
        ],
    }
    hm_t = parse_conversation(halumem_raw)
    assert hm_t.conversation_id == "hm-user-1"
    assert [(t.turn_id, t.session_id, t.timestamp) for t in hm_t.turns] == [
        ("s1_t1", "s1", "2026-01-01T10:00:00Z"),
        ("s1_t2", "s1", "2026-01-01T10:00:05Z"),
        ("s2_t1", "s2", "2026-01-02T09:00:00Z"),
    ]

    # 4. Plain text / Markdown transcript
    text_raw = "[u_1] Pengguna: Saya tinggal di Bogor.\nSekarang bekerja di PLN.\nAsisten: Baik, tercatat."
    txt_file = tmp_path / "chat.txt"
    txt_file.write_text(text_raw, encoding="utf-8")
    txt_t = load_conversation(txt_file)
    assert [(t.turn_id, t.role) for t in txt_t.turns] == [("u_1", "user"), ("t2", "assistant")]
    assert "Sekarang bekerja di PLN." in txt_t.turns[0].content

    # 5. JSONL multi-conversation loader
    convs = load_conversations_jsonl(ROOT / "data/conversations/conversations-v1.jsonl")
    assert len(convs) == 8

    # 6. Fail-closed on unsupported format or empty/malformed inputs
    with pytest.raises(ConversationError):
        parse_conversation({"unknown": []})
    with pytest.raises(ConversationError):
        parse_conversation("No role prefix line", format="text")
    with pytest.raises(ConversationError):
        parse_conversation([], format="openai")


# ---------------------------------------------------------------------------
# Transcript-grounded evidence verification
# ---------------------------------------------------------------------------


def _write_transition(
    *,
    source: str,
    text: str,
    supports: list[str],
    claim: str = "bekerja di Tokopedia",
) -> Transition:
    return Transition.from_dict(
        {
            "schema_version": 2,
            "case_id": "ground-test",
            "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
            "candidate": [
                {"id": "r1", "claims": ["tinggal di Bandung"]},
                {"id": "r2", "claims": [claim]},
            ],
            "evidence": [{"id": "e1", "source": source, "text": text, "supports": supports}],
            "required_claims": ["tinggal di Bandung"],
            "operations": [{"type": "WRITE", "record_id": "r2"}],
        }
    )


def test_verify_transcript_grounding_all_reason_codes():
    transcript = _sample_transcript()

    # 1. Grounded PASS (explicit turn ref & empty source resolved by quote)
    ok_t = _write_transition(source="turn:t3", text="Saya sekarang bekerja di Tokopedia.", supports=["bekerja di Tokopedia"])
    ok_check = verify_transcript_grounding(ok_t, transcript)
    assert ok_check.status == "PASS"
    assert ok_check.reason_code == "TRANSCRIPT_GROUNDING_OK"

    empty_src_t = _write_transition(source="", text="bekerja di Tokopedia", supports=["bekerja di Tokopedia"])
    assert verify_transcript_grounding(empty_src_t, transcript).reason_code == "TRANSCRIPT_GROUNDING_OK"

    # 2. UNGROUNDED_EVIDENCE_SOURCE
    bad_src = _write_transition(source="turn:t999", text="Saya sekarang bekerja di Tokopedia.", supports=["bekerja di Tokopedia"])
    chk = verify_transcript_grounding(bad_src, transcript)
    assert chk.status == "FAIL"
    assert chk.reason_code == "UNGROUNDED_EVIDENCE_SOURCE"
    assert chk.affected_ids == ["e1"]

    # 3. DISALLOWED_EVIDENCE_ROLE (assistant & tool)
    asst_t = _write_transition(source="turn:t2", text="punya kapal pesiar", supports=["memiliki kapal pesiar"], claim="memiliki kapal pesiar")
    chk = verify_transcript_grounding(asst_t, transcript)
    assert chk.reason_code == "DISALLOWED_EVIDENCE_ROLE"
    assert chk.affected_ids == ["e1"]

    tool_t = _write_transition(source="turn:t4", text="salary=500jt", supports=["salary=500jt"], claim="salary=500jt")
    assert verify_transcript_grounding(tool_t, transcript).reason_code == "DISALLOWED_EVIDENCE_ROLE"
    assert verify_transcript_grounding(asst_t, transcript, allowed_roles=("user", "assistant")).reason_code == "TRANSCRIPT_GROUNDING_OK"

    # 4. UNGROUNDED_EVIDENCE_QUOTE
    bad_quote = _write_transition(source="turn:t3", text="Saya adalah direktur utama.", supports=["bekerja di Tokopedia"])
    chk = verify_transcript_grounding(bad_quote, transcript)
    assert chk.reason_code == "UNGROUNDED_EVIDENCE_QUOTE"
    assert chk.affected_ids == ["e1"]

    # 5. UNGROUNDED_EVIDENCE_CLAIM (decoy quote)
    decoy = _write_transition(source="turn:t3", text="Saya sekarang bekerja di Tokopedia.", supports=["memiliki kapal pesiar"], claim="memiliki kapal pesiar")
    chk = verify_transcript_grounding(decoy, transcript)
    assert chk.reason_code == "UNGROUNDED_EVIDENCE_CLAIM"
    assert chk.affected_ids == ["memiliki kapal pesiar"]
    assert verify_transcript_grounding(decoy, transcript, require_claim_lexical_overlap=False).reason_code == "TRANSCRIPT_GROUNDING_OK"

    # 6. UNGROUNDED_DELETE_AUTHORIZATION
    bad_del = Transition.from_dict(
        {
            "schema_version": 2,
            "case_id": "del-bad",
            "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
            "candidate": [],
            "evidence": [{"id": "e1", "source": "turn:t3", "text": "Saya sekarang bekerja di Tokopedia.", "supports": []}],
            "required_claims": [],
            "operations": [
                {
                    "type": "DELETE",
                    "target_id": "r1",
                    "reason": "hapus",
                    "authorization": {
                        "source": "turn:t3",
                        "evidence_id": "e1",
                        "target_id": "r1",
                        "rationale": "hapus",
                    },
                }
            ],
        }
    )
    chk = verify_transcript_grounding(bad_del, transcript)
    assert chk.reason_code == "UNGROUNDED_DELETE_AUTHORIZATION"
    assert chk.affected_ids == ["r1"]

    good_del = Transition.from_dict(
        {
            "schema_version": 2,
            "case_id": "del-good",
            "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
            "candidate": [],
            "evidence": [{"id": "e1", "source": "turn:t5", "text": "Tolong hapus catatan Bandung saya.", "supports": []}],
            "required_claims": [],
            "operations": [
                {
                    "type": "DELETE",
                    "target_id": "r1",
                    "reason": "user requested deletion",
                    "authorization": {
                        "source": "turn:t5",
                        "evidence_id": "e1",
                        "target_id": "r1",
                        "rationale": "user requested deleting Bandung record",
                    },
                }
            ],
        }
    )
    assert verify_transcript_grounding(good_del, transcript).reason_code == "TRANSCRIPT_GROUNDING_OK"


def test_verify_with_transcript_fair_text_and_semantic_paths():
    transcript = parse_conversation(
        [
            {"turn_id": "t1", "role": "user", "content": "I live in Austin."},
            {"turn_id": "t2", "role": "user", "content": "Correction: I live in Chicago now, not Austin."},
        ],
        conversation_id="contradiction-conv",
    )
    contradiction_noop = Transition.from_dict(
        {
            "schema_version": 2,
            "case_id": "c-noop",
            "before": [{"id": "r1", "claims": ["lives in Austin"]}],
            "candidate": [{"id": "r1", "claims": ["lives in Austin"]}],
            "evidence": [
                {
                    "id": "e1",
                    "source": "turn:t2",
                    "text": "Correction: I live in Chicago now, not Austin.",
                    "supports": ["lives in Chicago"],
                    "contradicts": ["lives in Austin"],
                }
            ],
            "required_claims": ["lives in Austin"],
            "operations": [{"type": "NOOP"}],
        }
    )
    assert verify_with_transcript(contradiction_noop, transcript, "text").decision == "ACCEPT"
    assert verify_with_transcript(contradiction_noop, transcript, "graph").decision == "HOLD"
    assert verify_with_transcript(contradiction_noop, transcript, "text", fair_text=True).decision == "HOLD"

    sem_res = verify_with_transcript(
        contradiction_noop,
        transcript,
        "text",
        semantic_comparator=_FakeSemanticComparator(SemanticRelation.EQUIVALENT),
    )
    assert [c.name for c in sem_res.checks] == [
        "coverage", "preservation", "faithfulness", "semantic", TRANSCRIPT_CHECK_NAME,
    ]
    assert sem_res.decision == "HOLD"  # semantic deterministically catches evidence.contradicts


# ---------------------------------------------------------------------------
# Store & /verify sidecar integration
# ---------------------------------------------------------------------------


def test_store_commit_and_run_case_support_transcript_grounding(tmp_path):
    transcript = _sample_transcript()
    transition = _write_transition(
        source="turn:t3",
        text="Saya sekarang bekerja di Tokopedia.",
        supports=["bekerja di Tokopedia"],
    )
    store = {r.id: r for r in transition.before}

    ok_checks = [
        Check("coverage", True, "ok"),
        Check("preservation", True, "ok"),
        Check("faithfulness", True, "ok"),
        Check("transcript_grounding", True, "ok", reason_code="TRANSCRIPT_GROUNDING_OK"),
    ]
    committed = commit(store, transition, Result(transition.case_id, "ACCEPT", ok_checks))
    assert "r2" in committed

    all5_checks = [
        Check("coverage", True, "ok"),
        Check("preservation", True, "ok"),
        Check("faithfulness", True, "ok"),
        Check("semantic", True, "ok"),
        Check("transcript_grounding", True, "ok", reason_code="TRANSCRIPT_GROUNDING_OK"),
    ]
    assert "r2" in commit(store, transition, Result(transition.case_id, "ACCEPT", all5_checks))

    bad_checks = [
        Check("coverage", True, "ok"),
        Check("preservation", True, "ok"),
        Check("faithfulness", True, "ok"),
        Check("transcript_grounding", False, "spoofed", status="FAIL", reason_code="UNGROUNDED_EVIDENCE_QUOTE"),
    ]
    with pytest.raises(ValueError, match="ACCEPT requires every required check to pass"):
        commit(store, transition, Result(transition.case_id, "ACCEPT", bad_checks))

    dup_checks = [*ok_checks, Check("transcript_grounding", True, "dup")]
    with pytest.raises(ValueError, match="ACCEPT requires every required check to pass"):
        commit(store, transition, Result(transition.case_id, "ACCEPT", dup_checks))

    # End-to-end run_case: HOLD leaves store absent, ACCEPT writes v2 store.
    store_path = tmp_path / "state.json"
    log_path = tmp_path / "audit.jsonl"
    spoof = _write_transition(
        source="turn:t3",
        text="Fabricated quote.",
        supports=["bekerja di Tokopedia"],
    )
    hold_res = run_case(
        spoof,
        store_path,
        log_path,
        verifier=lambda t, rep, _: verify_with_transcript(t, transcript, rep),
    )
    assert hold_res.decision == "HOLD"
    assert not store_path.exists()

    accept_res = run_case(
        transition,
        store_path,
        log_path,
        verifier=lambda t, rep, _: verify_with_transcript(t, transcript, rep),
    )
    assert accept_res.decision == "ACCEPT"
    assert store_path.exists()


def _post_verify(port: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            "POST",
            "/verify",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


def test_service_verify_with_optional_transcript():
    server = service.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/healthz")
        health = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        assert health["limits"]["max_turns"] == service.MAX_TURNS

        base_payload = {
            "schema_version": 2,
            "case_id": "svc-conv",
            "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
            "candidate": [
                {"id": "r1", "claims": ["tinggal di Bandung"]},
                {"id": "r2", "claims": ["bekerja di Tokopedia"]},
            ],
            "evidence": [
                {
                    "id": "e1",
                    "source": "turn:t1",
                    "text": "Saya bekerja di Tokopedia.",
                    "supports": ["bekerja di Tokopedia"],
                }
            ],
            "required_claims": ["tinggal di Bandung"],
            "operations": [{"type": "WRITE", "record_id": "r2"}],
        }

        # Without transcript -> 3 checks (backward-compatible).
        st, res = _post_verify(port, base_payload)
        assert st == 200
        assert res["decision"] == "ACCEPT"
        assert [c["name"] for c in res["checks"]] == ["coverage", "preservation", "faithfulness"]

        # With grounded transcript -> 4 checks ACCEPT.
        st, res = _post_verify(
            port,
            {
                **base_payload,
                "transcript": {
                    "conversation_id": "svc-c1",
                    "turns": [{"turn_id": "t1", "role": "user", "content": "Saya bekerja di Tokopedia."}],
                },
            },
        )
        assert st == 200
        assert res["decision"] == "ACCEPT"
        assert [c["name"] for c in res["checks"]] == [
            "coverage", "preservation", "faithfulness", "transcript_grounding",
        ]

        # OpenAI list format via "conversation" key also accepted.
        st, res = _post_verify(
            port,
            {
                **base_payload,
                "conversation": [{"role": "user", "content": "Saya bekerja di Tokopedia."}],
            },
        )
        assert st == 200
        assert res["decision"] == "ACCEPT"

        # Spoofed quote -> 200 HOLD (UNGROUNDED_EVIDENCE_QUOTE).
        st, res = _post_verify(
            port,
            {
                **base_payload,
                "transcript": [{"role": "user", "content": "Hari ini cerah."}],
            },
        )
        assert st == 200
        assert res["decision"] == "HOLD"
        assert res["checks"][-1]["reason_code"] == "UNGROUNDED_EVIDENCE_QUOTE"

        # Malformed transcript -> 400 INVALID_TRANSCRIPT HOLD.
        st, res = _post_verify(port, {**base_payload, "transcript": {"turns": []}})
        assert st == 400
        assert res["decision"] == "HOLD"
        assert res["error"]["code"] == "INVALID_TRANSCRIPT"

        # Both transcript & conversation -> 400 INVALID_TRANSCRIPT.
        st, res = _post_verify(
            port,
            {
                **base_payload,
                "transcript": [{"role": "user", "content": "x"}],
                "conversation": [{"role": "user", "content": "x"}],
            },
        )
        assert st == 400
        assert res["error"]["code"] == "INVALID_TRANSCRIPT"

        # Oversized transcript -> 400 INPUT_TOO_LARGE.
        too_many = [{"role": "user", "content": "hi"}] * (service.MAX_TURNS + 1)
        st, res = _post_verify(port, {**base_payload, "transcript": too_many})
        assert st == 400
        assert res["error"]["code"] == "INPUT_TOO_LARGE"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Builders, extractors, session runner, CLI, and evaluation
# ---------------------------------------------------------------------------


def test_build_transition_from_conversation_and_extractors():
    transcript = parse_conversation(
        [
            {"turn_id": "t1", "role": "user", "content": "Halo! Saya tinggal di Bandung."},
            {"turn_id": "t2", "role": "assistant", "content": "Halo! Mungkin Anda suka yacht?"},
            {"turn_id": "t3", "role": "user", "content": "Cuaca hari ini cerah ya?"},
            {"turn_id": "t4", "role": "user", "content": "Saya sekarang bekerja di Tokopedia dan memelihara dua kucing."},
            {"turn_id": "t5", "role": "user", "content": "Koreksi: saya pindah ke Surabaya, bukan Bandung."},
            {"turn_id": "t6", "role": "user", "content": "Tolong hapus catatan kucing saya."},
        ],
        conversation_id="extract-test",
    )

    # Explicit proposal builder + fail-closed checks
    built = build_transition_from_conversation(
        transcript,
        case_id="built-1",
        before=[],
        proposals=[{"type": "WRITE", "turn_id": "t1", "quote": "Saya tinggal di Bandung.", "claims": ["tinggal di Bandung"]}],
    )
    assert built.schema_version == 2
    assert verify_with_transcript(built, transcript).decision == "ACCEPT"

    with pytest.raises(ConversationError):
        build_transition_from_conversation(
            transcript,
            case_id="bad-role",
            proposals=[{"type": "WRITE", "turn_id": "t2", "quote": "yacht", "claims": ["likes yacht"]}],
        )

    # Deterministic RuleBasedConversationExtractor step-by-step
    extractor = RuleBasedConversationExtractor()
    state: tuple[Record, ...] = ()

    tr1 = extractor.extract(transcript.slice_turns(up_to_turn_id="t1"), before=state, focus_turn_ids=["t1"])
    assert verify_with_transcript(tr1, transcript).decision == "ACCEPT"
    assert [op.type for op in tr1.operations] == ["WRITE"]
    state = tr1.candidate

    # Question / chatter turn produces NOOP
    tr3 = extractor.extract(transcript.slice_turns(up_to_turn_id="t3"), before=state, focus_turn_ids=["t3"])
    assert [op.type for op in tr3.operations] == ["NOOP"]
    assert verify_with_transcript(tr3, transcript).decision == "ACCEPT"

    # Compound turn writes two independent facts
    tr4 = extractor.extract(transcript.slice_turns(up_to_turn_id="t4"), before=state, focus_turn_ids=["t4"])
    assert [op.type for op in tr4.operations] == ["WRITE", "WRITE"]
    assert verify_with_transcript(tr4, transcript).decision == "ACCEPT"
    state = tr4.candidate

    # Relocation with contrast supersedes residence while preserving Tokopedia and pets
    tr5 = extractor.extract(transcript.slice_turns(up_to_turn_id="t5"), before=state, focus_turn_ids=["t5"])
    assert any(op.type == "SUPERSEDE" for op in tr5.operations)
    assert any("tinggal di Bandung" in ev.contradicts for ev in tr5.evidence)
    assert verify_with_transcript(tr5, transcript).decision == "ACCEPT"
    state = tr5.candidate

    # Explicit user deletion deletes pet record with structured authorization
    tr6 = extractor.extract(transcript.slice_turns(up_to_turn_id="t6"), before=state, focus_turn_ids=["t6"])
    assert [op.type for op in tr6.operations] == ["DELETE"]
    assert verify_with_transcript(tr6, transcript).decision == "ACCEPT"
    final_claims = {c for r in tr6.candidate for c in r.claims}
    assert final_claims == {"tinggal di Surabaya", "bekerja di Tokopedia"}

    # LLMConversationExtractor with fake JSON-mode client
    fake_client = _FakeJSONClient(
        {
            "operations": [
                {
                    "type": "WRITE",
                    "turn_id": "t1",
                    "quote": "Saya tinggal di Bandung.",
                    "claims": ["tinggal di Bandung"],
                }
            ]
        }
    )
    llm_ext = LLMConversationExtractor(fake_client)
    assert llm_ext.prompt_version == CONVERSATION_EXTRACTOR_PROMPT_VERSION
    llm_tr = llm_ext.extract(transcript.slice_turns(up_to_turn_id="t1"))
    assert verify_with_transcript(llm_tr, transcript).decision == "ACCEPT"
    assert llm_ext.last_extraction_metadata["total_tokens"] == 160

    bad_llm = LLMConversationExtractor(
        _FakeJSONClient(
            {
                "operations": [
                    {
                        "type": "WRITE",
                        "turn_id": "t2",  # assistant role!
                        "quote": "yacht",
                        "claims": ["owns yacht"],
                    }
                ]
            }
        )
    )
    with pytest.raises(ConversationError, match="LLM conversation extraction failed"):
        bad_llm.extract(transcript)


def test_run_conversation_session_cli_and_manifest_evaluation(tmp_path):
    # CLI smoke test
    cli_out = tmp_path / "cli-out"
    rc = conversation_cli_main(["--condition", "graph", "--output", str(cli_out)])
    assert rc == 0
    cli_summary = json.loads((cli_out / "conversation-summary.json").read_text(encoding="utf-8"))
    assert cli_summary["step_count"] == 3
    assert all(s["decision"] == "ACCEPT" for s in cli_summary["steps"])

    # core facade exports
    assert core.parse_conversation is parse_conversation
    assert core.verify_with_transcript is verify_with_transcript
    assert core.run_conversation_session is run_conversation_session

    # Priority order when multiple grounding violations exist together:
    # UNGROUNDED_EVIDENCE_SOURCE > DISALLOWED_EVIDENCE_ROLE > UNGROUNDED_EVIDENCE_QUOTE
    multi_bad = Transition.from_dict(
        {
            "schema_version": 2,
            "case_id": "priority-test",
            "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
            "candidate": [
                {"id": "r1", "claims": ["tinggal di Bandung"]},
                {"id": "r2", "claims": ["a"]},
                {"id": "r3", "claims": ["b"]},
            ],
            "evidence": [
                {"id": "e1", "source": "turn:t2", "text": "wrong quote", "supports": ["a"]},
                {"id": "e2", "source": "turn:t999", "text": "missing", "supports": ["b"]},
            ],
            "required_claims": ["tinggal di Bandung"],
            "operations": [
                {"type": "WRITE", "record_id": "r2"},
                {"type": "WRITE", "record_id": "r3"},
            ],
        }
    )
    assert verify_transcript_grounding(multi_bad, _sample_transcript()).reason_code == "UNGROUNDED_EVIDENCE_SOURCE"

    # Multi-turn session isolation: guarded HOLD preserves clean store for subsequent steps,
    # whereas unguarded persists the spoofed write.
    seq_transcript = parse_conversation(
        [
            {"turn_id": "t1", "role": "user", "content": "Saya tinggal di Bandung."},
            {"turn_id": "t2", "role": "assistant", "content": "Apakah Anda memiliki kapal pesiar?"},
            {"turn_id": "t3", "role": "user", "content": "Saya bekerja di Tokopedia."},
        ],
        conversation_id="seq-test",
    )
    seq_steps = [
        {
            "step_id": "s1",
            "up_to_turn_id": "t1",
            "proposals": [{"type": "WRITE", "record_id": "r1", "turn_id": "t1", "quote": "Saya tinggal di Bandung.", "claims": ["tinggal di Bandung"]}],
        },
        {
            "step_id": "s2",
            "up_to_turn_id": "t2",
            "transition": {
                "schema_version": 2,
                "case_id": "s2",
                "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
                "candidate": [{"id": "r1", "claims": ["tinggal di Bandung"]}, {"id": "r_spoof", "claims": ["memiliki kapal pesiar"]}],
                "evidence": [{"id": "e1", "source": "turn:t2", "text": "memiliki kapal pesiar", "supports": ["memiliki kapal pesiar"]}],
                "required_claims": ["tinggal di Bandung"],
                "operations": [{"type": "WRITE", "record_id": "r_spoof"}],
            },
        },
        {
            "step_id": "s3",
            "up_to_turn_id": "t3",
            "proposals": [{"type": "WRITE", "record_id": "r2", "turn_id": "t3", "quote": "Saya bekerja di Tokopedia.", "claims": ["bekerja di Tokopedia"]}],
        },
    ]
    guarded_session = run_conversation_session(seq_transcript, tmp_path / "sess-graph", condition="graph", steps=seq_steps)
    unguarded_session = run_conversation_session(seq_transcript, tmp_path / "sess-unguarded", condition="unguarded", steps=seq_steps)
    assert [s["decision"] for s in guarded_session["steps"]] == ["ACCEPT", "HOLD", "ACCEPT"]
    assert [r["id"] for r in guarded_session["final_records"]] == ["r1", "r2"]
    assert [r["id"] for r in unguarded_session["final_records"]] == ["r1", "r_spoof", "r2"]

    # Manifest pin & evaluation script end-to-end check
    manifest_path = ROOT / "data/manifests/conversations-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    conv_path = (manifest_path.parent / manifest["conversations_file"]).resolve()
    labels_path = (manifest_path.parent / manifest["labels_file"]).resolve()
    assert hashlib.sha256(conv_path.read_bytes()).hexdigest() == manifest["conversations_sha256"]
    assert hashlib.sha256(labels_path.read_bytes()).hexdigest() == manifest["labels_sha256"]

    import scripts.run_conversation_evaluation as conv_eval

    eval_out = tmp_path / "conv-eval"
    metrics = conv_eval.run_conversation_evaluation(manifest_path, eval_out, code_revision="test-rev")
    assert metrics["condition_metrics"]["unguarded"]["safe_final_state_conversations"] == {
        "numerator": 4, "denominator": 8, "rate": 0.5,
    }
    for cond in ("graph", "text_fair"):
        m = metrics["condition_metrics"][cond]
        assert m["valid_acceptance_rate"] == {"numerator": 17, "denominator": 17, "rate": 1.0}
        assert m["faulty_detection_rate"] == {"numerator": 10, "denominator": 10, "rate": 1.0}
        assert m["false_accept_rate"] == {"numerator": 0, "denominator": 10, "rate": 0.0}
        assert m["safe_final_state_conversations"] == {"numerator": 8, "denominator": 8, "rate": 1.0}
    assert metrics["condition_metrics"]["text"]["decision_counts"]["ACCEPT"] == 18
    assert metrics["paired_differences"]["graph_vs_text"]["discordant_count"] == 1
    assert metrics["paired_differences"]["graph_vs_text"]["discordant_steps"][0]["step_id"] == "CONV-06-S3"
    assert metrics["paired_differences"]["text_fair_vs_graph"]["discordant_count"] == 0
    assert metrics["extraction_replay"]["conversations_converged_to_expected_state"] == 8
    assert metrics["extraction_replay"]["accepted_steps"] == 20
