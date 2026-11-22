"""Offline tests for the HaluMem loader, transition builder, injector, and silver labeler.

All fixtures are synthetic HaluMem-shaped records produced by
``membud.halumem.synthetic_halumem_records``; no HaluMem content is used.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from membud.halumem import (
    FAULT_TYPES,
    INJECTOR_CIRCULARITY_LIMITATION,
    LEXICAL_EVIDENCE_ASSUMPTION,
    ORIGINAL_RESOLUTION_ASSUMPTION,
    SILVER_LABEL_LIMITATION,
    HaluMemValidationError,
    LLMClaimMatchJudge,
    SilverLabelResult,
    build_halumem_transitions,
    build_phase2_blind_quiz,
    compute_file_sha256,
    derive_silver_label,
    inject_halumem_faults,
    iter_halumem_users,
    load_halumem_dataset,
    parse_halumem_user,
    select_stratified_audit_sample,
    synthetic_halumem_records,
    write_synthetic_halumem_fixture,
)
from membud.schemas import SCHEMA_VERSION, Operation, Record, Transition
from membud.serialization import transition_dict
from membud.verification import verify
from membud.conversation import verify_with_transcript


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixture(tmp_path: Path) -> Path:
    return write_synthetic_halumem_fixture(tmp_path / "synthetic-halumem.jsonl")


def _load(tmp_path: Path):
    path = _fixture(tmp_path)
    return load_halumem_dataset(path, dataset_revision="synthetic-test-v1")


def _build(tmp_path: Path, *, update_operation: str = "REVISE"):
    meta, users = _load(tmp_path)
    return meta, users, build_halumem_transitions(
        users, dataset_meta=meta, update_operation=update_operation
    )


def _case(report, case_id: str):
    for case in report.cases:
        if case.case_id == case_id:
            return case
    raise AssertionError(f"case {case_id!r} not found")


def _session(users, user_uuid: str, session_id: str):
    user = next(u for u in users if u.uuid == user_uuid)
    return user, next(s for s in user.sessions if s.session_id == session_id)


def _replace_candidate(t: Transition, records) -> Transition:
    """Build a deliberately faulty candidate without re-validating the schema.

    The silver labeler is a label-derivation layer, not the schema gate; it must
    classify proposals that a real memory system could emit even when their
    declared operations do not line up perfectly with the candidate diff.
    """
    return Transition(
        case_id=t.case_id,
        before=t.before,
        candidate=tuple(records),
        evidence=t.evidence,
        required_claims=t.required_claims,
        operations=t.operations,
        schema_version=SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Loader: strict validation and content-free provenance
# ---------------------------------------------------------------------------


def test_synthetic_fixture_loads_and_records_content_free_metadata(tmp_path):
    path = _fixture(tmp_path)
    meta, users = load_halumem_dataset(
        path, dataset_revision="fixture-rev-1", is_synthetic_fixture=True
    )

    assert len(users) == 6
    assert meta.user_count == 6
    assert meta.session_count == 18
    assert meta.update_point_count > 0
    assert meta.question_count == 18
    assert meta.dataset_revision == "fixture-rev-1"
    assert meta.is_synthetic_fixture is True
    assert meta.file_sha256 == compute_file_sha256(path)

    serialized = json.dumps(meta.to_dict())
    assert "Hi, my name is Nadia" not in serialized
    assert "Lives in Bandung" not in serialized
    assert meta.file_sha256 in serialized


def test_iter_halumem_users_is_a_streaming_generator(tmp_path):
    path = _fixture(tmp_path)
    iterator = iter_halumem_users(path)
    assert hasattr(iterator, "__next__")
    first = next(iterator)
    assert first.uuid == "syn-user-1"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda row: row.update({"extra": 1}), "unexpected top-level"),
        (lambda row: row.pop("uuid"), "missing required top-level"),
        (lambda row: row.update({"persona_info": ""}), "persona_info"),
        (lambda row: row.update({"sessions": []}), "non-empty list"),
        (
            lambda row: row["sessions"][0].pop("memory_points"),
            "missing required field 'memory_points'",
        ),
        (
            lambda row: [
                turn.update({"role": "assistant"})
                for turn in row["sessions"][0]["dialogue"]
            ],
            "at least one user-role turn",
        ),
        (
            lambda row: row["sessions"][0]["memory_points"][0].update(
                {"is_update": "maybe"}
            ),
            "must be a boolean",
        ),
        (
            lambda row: row["sessions"][0]["memory_points"][0].update(
                {"is_update": True, "original_memories": []}
            ),
            "must be non-empty when is_update is True",
        ),
        (
            lambda row: row["sessions"][0]["memory_points"][0].update(
                {"is_update": False, "original_memories": ["something"]}
            ),
            "must be empty when is_update is False",
        ),
        (
            lambda row: row["sessions"][0]["dialogue"][0].update({"role": "wizard"}),
            "role",
        ),
        (
            lambda row: row["sessions"][0]["memory_points"][0].update(
                {"unknown_field": "x"}
            ),
            "unexpected fields",
        ),
    ],
)
def test_loader_fails_closed_on_malformed_entries(tmp_path, mutate, message):
    row = synthetic_halumem_records()[0]
    mutate(row)
    with pytest.raises(HaluMemValidationError, match=message):
        parse_halumem_user(row)


def test_loader_rejects_blank_lines_and_duplicate_uuids(tmp_path):
    rows = synthetic_halumem_records()[:2]
    blank = tmp_path / "blank.jsonl"
    blank.write_text(
        json.dumps(rows[0]) + "\n\n" + json.dumps(rows[1]) + "\n", encoding="utf-8"
    )
    with pytest.raises(HaluMemValidationError, match="blank"):
        list(iter_halumem_users(blank))

    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(
        json.dumps(rows[0]) + "\n" + json.dumps(rows[0]) + "\n", encoding="utf-8"
    )
    with pytest.raises(HaluMemValidationError, match="duplicate user uuid"):
        list(iter_halumem_users(duplicate))


def test_loader_rejects_missing_file_and_empty_file(tmp_path):
    with pytest.raises(HaluMemValidationError, match="not found"):
        load_halumem_dataset(tmp_path / "missing.jsonl")

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(HaluMemValidationError, match="empty"):
        list(iter_halumem_users(empty))


def test_loader_accepts_original_memories_as_objects_and_memory_source_lists():
    row = synthetic_halumem_records()[0]
    point = row["sessions"][1]["memory_points"][0]
    point["original_memories"] = [{"memory_content": "Lives in Bandung"}]
    point["memory_source"] = ["user", "relocation turn"]
    parsed = parse_halumem_user(row)
    assert parsed.sessions[1].memory_points[0].original_memories == ("Lives in Bandung",)
    assert parsed.sessions[1].memory_points[0].memory_source == "user; relocation turn"


# ---------------------------------------------------------------------------
# Transition builder
# ---------------------------------------------------------------------------


def test_builder_accumulates_gold_state_and_derives_evidence(tmp_path):
    _, users, report = _build(tmp_path)

    s1 = _case(report, "syn-user-1__s1")
    assert s1.transition.before == ()
    assert len(s1.transition.candidate) == 3
    assert {op.type for op in s1.transition.operations} == {"WRITE"}
    assert s1.transition.required_claims == ()
    assert all(ev.text for ev in s1.transition.evidence)

    s2 = _case(report, "syn-user-1__s2")
    assert len(s2.transition.before) == 3
    assert "Lives in Surabaya" in {
        claim for record in s2.transition.candidate for claim in record.claims
    }
    assert "Lives in Bandung" not in {
        claim for record in s2.transition.candidate for claim in record.claims
    }
    revise = [op for op in s2.transition.operations if op.type == "REVISE"]
    assert len(revise) == 1
    assert revise[0].reason == "halumem_is_update:s2_m1"
    assert set(s2.transition.required_claims) == {
        "Works as a backend engineer at Tokopedia",
        "Allergic to shrimp",
    }
    quote = next(ev.text for ev in s2.transition.evidence if "Surabaya" in ev.text)
    assert quote in {turn.content for turn in s2.transcript.user_turns()}
    contradicted = {
        claim for ev in s2.transition.evidence for claim in ev.contradicts
    }
    assert "Lives in Bandung" in contradicted
    assert s2.grounding_method == "lexical_match"
    assert report.assumptions == (
            LEXICAL_EVIDENCE_ASSUMPTION,
            ORIGINAL_RESOLUTION_ASSUMPTION,
        )


def test_builder_reports_ungroundable_points_and_advances_gold_state(tmp_path):
    _, _, report = _build(tmp_path)

    assert "syn-user-6__s3" not in {case.case_id for case in report.cases}
    ungroundable = [u for u in report.ungroundable if u.case_id == "syn-user-6__s3"]
    assert len(ungroundable) == 1
    assert ungroundable[0].reason == "no_user_turn_lexical_match"
    assert ungroundable[0].point_id == "s3_m1"
    assert report.summary_dict()["groundable_transition_count"] == 17
    assert report.summary_dict()["ungroundable_count"] == 1
    assert "Published a monograph" not in json.dumps(report.summary_dict())


def test_builder_supersede_mode_uses_replacement_records(tmp_path):
    _, _, report = _build(tmp_path, update_operation="SUPERSEDE")
    s2 = _case(report, "syn-user-1__s2")
    supersede = [op for op in s2.transition.operations if op.type == "SUPERSEDE"]
    assert len(supersede) == 1
    assert supersede[0].target_id != supersede[0].record_id
    assert s2.update_operation_mode == "SUPERSEDE"
    assert all(
        record.id != supersede[0].target_id for record in s2.transition.candidate
    )


def test_builder_rejects_unknown_update_operation(tmp_path):
    meta, users = _load(tmp_path)
    with pytest.raises(ValueError, match="update_operation"):
        build_halumem_transitions(users, dataset_meta=meta, update_operation="DELETE")


# ---------------------------------------------------------------------------
# Seeded fault injector
# ---------------------------------------------------------------------------


def test_injector_is_deterministic_and_seeded(tmp_path):
    _, _, report = _build(tmp_path)
    first = inject_halumem_faults(report.cases, seed=123)
    second = inject_halumem_faults(report.cases, seed=123)
    other = inject_halumem_faults(report.cases, seed=124)

    assert [c.label_dict() for c in first] == [c.label_dict() for c in second]
    assert [transition_dict(c.transition) for c in first] == [
        transition_dict(c.transition) for c in second
    ]
    assert any(
        transition_dict(a.transition) != transition_dict(b.transition)
        for a, b in zip(first, other)
    )

    counts = {ft: sum(c.fault_type == ft for c in first) for ft in FAULT_TYPES}
    assert counts == {"omission": 11, "corruption": 21, "unsupported_addition": 17}
    assert sum(c.fault_type is None for c in first) == 17
    assert all(c.seed == 123 for c in first)
    assert all(c.label_source == "seeded_fault_injection" for c in first)
    assert all(c.circularity_limitation == INJECTOR_CIRCULARITY_LIMITATION for c in first)


def test_injector_rejects_non_integer_seed(tmp_path):
    _, _, report = _build(tmp_path)
    with pytest.raises(ValueError, match="seed"):
        inject_halumem_faults(report.cases, seed=True)


def test_injector_labels_match_verifier_outcomes_with_transcript_grounding(tmp_path):
    _, _, report = _build(tmp_path)
    suite = inject_halumem_faults(report.cases, seed=7)

    for case in suite:
        result = verify_with_transcript(case.transition, case.transcript, "graph")
        codes = {check.reason_code for check in result.checks if not check.passed}
        if case.fault_type is None:
            assert result.decision == "ACCEPT", case.case_id
            assert codes == set()
        else:
            assert result.decision == "HOLD", case.case_id
            if case.fault_type == "omission":
                assert "REQUIRED_CLAIM_OMITTED" in codes
            elif case.fault_type == "corruption":
                assert codes & {
                    "PRIOR_RECORD_UNAUTHORIZED_CHANGE",
                    "CONTRADICTED_CLAIM",
                }
            else:
                assert codes & {"UNSUPPORTED_CLAIM", "UNGROUNDED_EVIDENCE_CLAIM"}


def test_injector_spoofed_evidence_variant_needs_transcript_grounding(tmp_path):
    _, _, report = _build(tmp_path)
    suite = inject_halumem_faults(report.cases, seed=7)
    spoofed = [c for c in suite if c.fault_variant == "unsupported_spoofed_evidence"]
    assert spoofed

    ungrounded = verify(spoofed[0].transition, "graph")
    grounded = verify_with_transcript(
        spoofed[0].transition, spoofed[0].transcript, "graph"
    )
    assert ungrounded.decision == "ACCEPT"
    assert grounded.decision == "HOLD"
    assert any(
        check.reason_code == "UNGROUNDED_EVIDENCE_CLAIM" and not check.passed
        for check in grounded.checks
    )


# ---------------------------------------------------------------------------
# Silver labeling rules
# ---------------------------------------------------------------------------


def test_silver_label_marks_gold_transitions_valid(tmp_path):
    _, users, report = _build(tmp_path)
    for case_id in ("syn-user-1__s1", "syn-user-1__s2"):
        case = _case(report, case_id)
        user, session = _session(users, case.user_uuid, case.session_id)
        label = derive_silver_label(case.transition, session, user_uuid=user.uuid)
        assert label.primary_label == "valid"
        assert label.fault_types == ()
        assert label.expected_decision == "ACCEPT"
        assert label.label_status == "provisional_silver_unvalidated"
        assert label.is_ground_truth is False
        assert label.limitation == SILVER_LABEL_LIMITATION


def test_silver_label_detects_omitted_gold_point(tmp_path):
    _, users, report = _build(tmp_path)
    case = _case(report, "syn-user-1__s1")
    user, session = _session(users, case.user_uuid, case.session_id)
    kept = [r for r in case.transition.candidate if "Tokopedia" not in " ".join(r.claims)]
    faulty = _replace_candidate(case.transition, kept)

    label = derive_silver_label(faulty, session, user_uuid=user.uuid)
    assert label.primary_label == "omission"
    assert "omission" in label.fault_types
    assert label.omitted_gold_points
    assert label.expected_decision == "HOLD"


def test_silver_label_detects_unsupported_addition(tmp_path):
    _, users, report = _build(tmp_path)
    case = _case(report, "syn-user-1__s1")
    user, session = _session(users, case.user_uuid, case.session_id)
    hallucinated = "Owns a private Gulfstream jet moored in Zurich"
    faulty = _replace_candidate(
        case.transition,
        (*case.transition.candidate, Record(id="r_halluc", claims=(hallucinated,))),
    )

    label = derive_silver_label(faulty, session, user_uuid=user.uuid)
    assert label.primary_label == "unsupported_addition"
    assert label.unsupported_added_claims == (hallucinated,)


def test_silver_label_detects_corrupted_prior_record(tmp_path):
    _, users, report = _build(tmp_path)
    case = _case(report, "syn-user-2__s3")
    user, session = _session(users, case.user_uuid, case.session_id)
    corrupted = [
        Record(id=r.id, claims=("Preparing for a half marathon in November",))
        if r.id == "r_syn-user-2_4"
        else r
        for r in case.transition.candidate
    ]
    faulty = _replace_candidate(case.transition, corrupted)

    label = derive_silver_label(faulty, session, user_uuid=user.uuid)
    assert "corruption" in label.fault_types
    assert label.corrupted_prior_record_ids == ("r_syn-user-2_4",)
    assert label.primary_label == "corruption"


def test_silver_label_detects_stale_retired_claim(tmp_path):
    _, users, report = _build(tmp_path)
    case = _case(report, "syn-user-1__s2")
    user, session = _session(users, case.user_uuid, case.session_id)
    stale = [
        Record(id=r.id, claims=("Lives in Bandung", "Lives in Surabaya"))
        if r.id == "r_syn-user-1_1"
        else r
        for r in case.transition.candidate
    ]
    faulty = _replace_candidate(case.transition, stale)

    label = derive_silver_label(faulty, session, user_uuid=user.uuid)
    assert "corruption" in label.fault_types
    assert label.corrupted_prior_record_ids == ("r_syn-user-1_1",)


def test_silver_label_records_paraphrase_lexical_grounding_gap(tmp_path):
    _, users, report = _build(tmp_path)
    case = _case(report, "syn-user-1__s1")
    user, session = _session(users, case.user_uuid, case.session_id)
    no_evidence = Transition(
        case_id=case.transition.case_id,
        before=case.transition.before,
        candidate=case.transition.candidate,
        evidence=(),
        required_claims=case.transition.required_claims,
        operations=case.transition.operations,
        schema_version=SCHEMA_VERSION,
    )
    no_evidence.validate()

    label = derive_silver_label(no_evidence, session, user_uuid=user.uuid)
    assert label.primary_label == "valid"
    assert label.paraphrased_valid_claims_ungrounded_by_lexical


def test_llm_judge_is_only_consulted_for_ambiguous_matches(tmp_path):
    class FakeJudge:
        def __init__(self):
            self.calls = 0
            self.queries = []

        def matches(self, proposed, gold):
            self.calls += 1
            self.queries.append((proposed, gold))
            return True

    _, users, report = _build(tmp_path)
    case = _case(report, "syn-user-1__s1")
    user, session = _session(users, case.user_uuid, case.session_id)
    ambiguous = "Bandung vicinity district southern"
    faulty = _replace_candidate(
        case.transition,
        (*case.transition.candidate, Record(id="r_amb", claims=(ambiguous,))),
    )

    judge = FakeJudge()
    label = derive_silver_label(faulty, session, user_uuid=user.uuid, llm_judge=judge)
    assert judge.calls > 0
    assert label.judge_method == "deterministic+llm_claim_judge"
    assert label.judge_calls == judge.calls
    assert ambiguous not in label.unsupported_added_claims


def test_llm_claim_match_judge_validates_and_logs(tmp_path):
    from membud.llm.client import JSONCompletion

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def complete_json(self, messages, validator=None):
            self.calls += 1
            payload = {"equivalent": True, "reason": "same core fact"}
            if validator is not None:
                validator(payload)
            return JSONCompletion(
                json=payload,
                content=json.dumps(payload),
                model="fake-free-model",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                request_id=None,
                latency_ms=1.0,
                request_attempts=1,
                json_retries=0,
                cached=False,
            )

    log_path = tmp_path / "judge-calls.jsonl"
    judge = LLMClaimMatchJudge(FakeClient(), log_path=log_path)
    assert judge.matches("proposed", "gold") is True
    assert judge.calls == 1
    assert judge.total_tokens == 15
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["equivalent"] is True
    assert entry["prompt_version"] == "membud-halumem-claim-match-v1"
    assert entry["model"] == "fake-free-model"


# ---------------------------------------------------------------------------
# Audit sampling and blind quiz
# ---------------------------------------------------------------------------


def _silver(case_id: str, primary: str) -> SilverLabelResult:
    return SilverLabelResult(
        case_id=case_id,
        user_uuid="u",
        session_id="s",
        primary_label=primary,
        fault_types=() if primary == "valid" else (primary,),
        expected_decision="ACCEPT" if primary == "valid" else "HOLD",
        matched_gold_points=(),
        omitted_gold_points=(),
        omitted_prior_claims=(),
        corrupted_prior_record_ids=(),
        unsupported_added_claims=(),
        paraphrased_valid_claims_ungrounded_by_lexical=(),
        judge_method="deterministic",
    )


def test_stratified_audit_sample_is_seeded_and_covers_rare_categories():
    labels = [
        *(_silver(f"valid-{i}", "valid") for i in range(20)),
        *(_silver(f"omission-{i}", "omission") for i in range(2)),
        *(_silver(f"corruption-{i}", "corruption") for i in range(2)),
        *(_silver(f"unsupported-{i}", "unsupported_addition") for i in range(2)),
    ]
    first = select_stratified_audit_sample(labels, target_size=8, seed=99)
    second = select_stratified_audit_sample(labels, target_size=8, seed=99)

    assert [s.case_id for s in first] == [s.case_id for s in second]
    assert len(first) == 8
    assert {s.primary_label for s in first} == {
        "valid",
        "omission",
        "corruption",
        "unsupported_addition",
    }


def test_blind_quiz_keeps_labels_out_of_case_file(tmp_path):
    _, users, report = _build(tmp_path)
    transitions = {case.case_id: case.transition for case in report.cases}
    sampled = [_silver("syn-user-1__s1", "valid"), _silver("syn-user-1__s2", "valid")]

    info = build_phase2_blind_quiz(transitions, sampled, tmp_path / "audit")
    cases_text = Path(info["cases_path"]).read_text(encoding="utf-8")
    labels_text = Path(info["labels_path"]).read_text(encoding="utf-8")
    html_text = Path(info["quiz_html_path"]).read_text(encoding="utf-8")
    manifest = json.loads(Path(info["manifest_path"]).read_text(encoding="utf-8"))

    for forbidden in (
        "expected_decision",
        "category",
        "fault_types",
        "label_status",
        "notes",
    ):
        assert forbidden not in cases_text
    assert "expected_decision" in labels_text
    assert "expected_decision" not in html_text
    assert "silver" not in html_text.lower()
    assert manifest["status"] == "provisional_silver_pending_human_review"
    assert manifest["review"]["second_review_status"] == (
        "pending_human_audit_of_silver_labels"
    )
    assert info["case_count"] == 2


# ---------------------------------------------------------------------------
# License / repository hygiene
# ---------------------------------------------------------------------------


def test_gitignore_blocks_halumem_artifacts():
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
        encoding="utf-8"
    )
    for pattern in (
        "halumem-output/",
        "*halumem*.jsonl",
        "*HaluMem*.jsonl",
        "data/halumem/",
        "data/results/halumem*/",
    ):
        assert pattern in gitignore


# ---------------------------------------------------------------------------
# Real-format behaviors (synthetic stand-ins; no HaluMem content)
# ---------------------------------------------------------------------------


def _real_shape_user(sessions):
    return {
        "uuid": "u-real",
        "persona_info": "synthetic persona",
        "sessions": sessions,
        "total_dialogue_token_length": 10,
        "total_question_count": 0,
        "token_cost": {"input": 1},
    }


def _real_point(content, *, source="secondary", update=False, originals=(), **extra):
    return {
        "index": 1,
        "memory_content": content,
        "memory_type": "Persona Memory",
        "memory_source": source,
        "is_update": "True" if update else "False",
        "original_memories": list(originals),
        "timestamp": "t",
        "importance": 0.5,
        **extra,
    }


def _real_session(points, turns, **extra):
    return {
        "start_time": "t0",
        "end_time": "t1",
        "dialogue": [
            {"role": r, "content": c, "timestamp": "t0", "dialogue_turn": i}
            for i, (r, c) in enumerate(turns)
        ],
        "memory_points": points,
        "memory_points_count": len(points),
        "dialogue_turn_num": len(turns),
        "dialogue_token_length": 5,
        **extra,
    }


def _tiny_meta():
    from membud.halumem import HaluMemDatasetMeta

    return HaluMemDatasetMeta("0" * 64, "test", 1, 1, 0, 0, 0, True)


def test_loader_accepts_real_shape_fields_and_start_time():
    user = parse_halumem_user(
        _real_shape_user(
            [
                _real_session(
                    [_real_point("Plays the cello", event_source=1)],
                    [("user", "I play the cello every day"), ("assistant", "Nice")],
                )
            ]
        )
    )
    assert user.sessions[0].timestamp == "t0"
    assert user.sessions[0].questions == ()


def test_loader_rejects_inconsistent_memory_points_count():
    session = _real_session(
        [_real_point("Plays the cello")],
        [("user", "I play the cello"), ("assistant", "ok")],
    )
    session["memory_points_count"] = 7
    with pytest.raises(HaluMemValidationError, match="memory_points_count"):
        parse_halumem_user(_real_shape_user([session]))


def test_loader_rejects_interference_update_points():
    bad = _real_point("Plays the oboe", source="interference", update=True, originals=["x"])
    with pytest.raises(HaluMemValidationError, match="interference"):
        parse_halumem_user(
            _real_shape_user(
                [_real_session([bad], [("user", "I play the oboe"), ("assistant", "ok")])]
            )
        )


def test_builder_excludes_interference_points_from_gold_state():
    distractor = _real_point(
        "Plays the oboe professionally",
        source="interference",
        reference_memory_content="Plays the cello",
        reason="synthetic",
    )
    session = _real_session(
        [_real_point("Plays the cello every day"), distractor],
        [("user", "I play the cello every day"), ("assistant", "Nice")],
    )
    user = parse_halumem_user(_real_shape_user([session]))
    assert len(user.sessions[0].interference_points) == 1
    report = build_halumem_transitions([user], dataset_meta=_tiny_meta())
    claims = {c for r in report.cases[0].transition.candidate for c in r.claims}
    assert claims == {"Plays the cello every day"}
    assert report.ungroundable == ()


def test_builder_resolves_paraphrased_original_but_reports_unresolvable():
    s1 = _real_session(
        [_real_point("Alice lives in the city of Bandung")],
        [("user", "I live in the city of Bandung"), ("assistant", "ok")],
    )
    s2 = _real_session(
        [
            _real_point(
                "Alice lives in the city of Jakarta",
                update=True,
                originals=["Alice lives in Bandung city"],
            )
        ],
        [("user", "I moved to the city of Jakarta"), ("assistant", "ok")],
    )
    s3 = _real_session(
        [
            _real_point(
                "Alice works as a pilot",
                update=True,
                originals=["Completely unrelated old fact about astronomy"],
            )
        ],
        [("user", "I work as a pilot"), ("assistant", "ok")],
    )
    user = parse_halumem_user(_real_shape_user([s1, s2, s3]))
    report = build_halumem_transitions([user], dataset_meta=_tiny_meta())
    by_session = {c.session_id: c for c in report.cases}
    case2 = by_session["s2"]
    assert {c for r in case2.transition.candidate for c in r.claims} == {
        "Alice lives in the city of Jakarta"
    }
    assert [op.type for op in case2.transition.operations] == ["REVISE"]
    assert "s3" not in by_session
    assert [(u.session_id, u.reason) for u in report.ungroundable] == [
        ("s3", "missing_original_memory_in_prior_state")
    ]


def test_builder_reports_noop_update_instead_of_crashing():
    s1 = _real_session(
        [_real_point("Alice plays the cello daily")],
        [("user", "I play the cello daily"), ("assistant", "ok")],
    )
    s2 = _real_session(
        [
            _real_point(
                "Alice plays the cello daily",
                update=True,
                originals=["Alice plays the cello daily"],
            )
        ],
        [("user", "I still play the cello daily"), ("assistant", "ok")],
    )
    user = parse_halumem_user(_real_shape_user([s1, s2]))
    report = build_halumem_transitions([user], dataset_meta=_tiny_meta())
    assert [u.reason for u in report.ungroundable] == ["update_does_not_change_prior_record"]


def test_silver_label_flags_claim_closer_to_interference_distractor():
    gold = _real_point("Alice plays the cello every day")
    distractor = _real_point(
        "Alice plays the oboe every night",
        source="interference",
        reference_memory_content="Alice plays the cello every day",
    )
    session = parse_halumem_user(
        _real_shape_user(
            [
                _real_session(
                    [gold, distractor],
                    [("user", "I play the cello and the oboe"), ("assistant", "ok")],
                )
            ]
        )
    ).sessions[0]
    transition = Transition(
        case_id="c",
        before=(),
        candidate=(Record(id="r1", claims=("Alice plays the oboe every night",)),),
        evidence=(),
        required_claims=(),
        operations=(Operation(type="WRITE", record_id="r1"),),
        schema_version=SCHEMA_VERSION,
    )
    result = derive_silver_label(transition, session, user_uuid="u-real")
    assert result.primary_label == "unsupported_addition"
    assert result.unsupported_added_claims == ("Alice plays the oboe every night",)


def test_injected_omission_labels_are_true_by_construction(tmp_path):
    _, _, report = _build(tmp_path)
    injected = inject_halumem_faults(report.cases, seed=7)
    omissions = [e for e in injected if e.fault_type == "omission"]
    assert omissions
    for e in omissions:
        candidate_claims = {c for r in e.transition.candidate for c in r.claims}
        assert e.omitted_required_claims
        assert not (set(e.omitted_required_claims) & candidate_claims)


# ---------------------------------------------------------------------------
# Silver v2 omission scope and normalized evidence policy
# ---------------------------------------------------------------------------


def _scope_session():
    return parse_halumem_user(
        _real_shape_user(
            [
                _real_session(
                    [
                        _real_point("Alice plays the cello every day"),
                        _real_point("Alice owns a red bicycle"),
                    ],
                    [
                        ("user", "Alice plays the cello every day. I own a red bicycle."),
                        ("assistant", "ok"),
                    ],
                )
            ]
        )
    ).sessions[0]


def _write_only(claim):
    return Transition(
        case_id="c",
        before=(),
        candidate=(Record(id="r1", claims=(claim,)),),
        evidence=(),
        required_claims=(),
        operations=(Operation(type="WRITE", record_id="r1"),),
        schema_version=SCHEMA_VERSION,
    )


def test_silver_omission_scope_prior_only_treats_missing_gold_as_recall_gap():
    session = _scope_session()
    t = _write_only("Alice plays the cello every day")  # misses the bicycle point
    v1 = derive_silver_label(t, session, user_uuid="u")
    v2 = derive_silver_label(t, session, user_uuid="u", omission_scope="prior_only")
    assert v1.primary_label == "omission"
    assert v2.primary_label == "valid"
    assert v2.omitted_gold_points  # still recorded as a recall gap
    with pytest.raises(ValueError):
        derive_silver_label(t, session, user_uuid="u", omission_scope="nope")


def test_normalized_evidence_respects_theta_numerals_and_verbatim_quote():
    from membud.halumem import rederive_evidence_normalized

    session = _scope_session()
    transcript = session.to_transcript(user_uuid="u")
    full = _write_only("Alice plays the cello every day")
    partial = _write_only("Alice plays the cello every day in Paris")  # extra tokens
    numeric = _write_only("Alice plays the cello 5 times")

    ev = rederive_evidence_normalized(full, transcript, min_coverage=1.0).evidence
    assert len(ev) == 1 and ev[0].text == session.dialogue[0].content
    assert ev[0].supports == ("Alice plays the cello every day",)
    assert not rederive_evidence_normalized(partial, transcript, min_coverage=1.0).evidence
    assert rederive_evidence_normalized(partial, transcript, min_coverage=0.6).evidence
    # numeral absent from the turn: never grounded, whatever theta is
    assert not rederive_evidence_normalized(numeric, transcript, min_coverage=0.1).evidence
    with pytest.raises(ValueError):
        rederive_evidence_normalized(full, transcript, min_coverage=0.0)


def test_normalized_evidence_passes_unchanged_verifier_when_grounded():
    from membud.halumem import rederive_evidence_normalized

    session = _scope_session()
    transcript = session.to_transcript(user_uuid="u")
    t = rederive_evidence_normalized(
        _write_only("Alice plays the cello every day"), transcript, min_coverage=1.0
    )
    assert verify_with_transcript(t, transcript, "graph").decision == "ACCEPT"
    unsupported = _write_only("Alice flies a private jet")
    t2 = rederive_evidence_normalized(unsupported, transcript, min_coverage=0.6)
    assert verify_with_transcript(t2, transcript, "graph").decision == "HOLD"


def test_persona_name_extraction():
    from membud.halumem import persona_name_from_info

    assert (
        persona_name_from_info("[Recorded on X] Name: Martin Mark; Gender: Male; Age: 3")
        == "Martin Mark"
    )
    assert persona_name_from_info("Gender: Male; Age: 3") is None


def test_normalization_n_first_person_name_and_inflection_fold():
    from membud.halumem import rederive_evidence_normalized

    session = parse_halumem_user(
        _real_shape_user(
            [
                _real_session(
                    [_real_point("x")],
                    [("user", "I play the cello every day"), ("assistant", "ok")],
                )
            ]
        )
    ).sessions[0]
    transcript = session.to_transcript(user_uuid="u")
    claim = _write_only("Martin Mark plays the cello every day")
    # Without N: the name and "plays" vs "play" both block full coverage.
    assert not rederive_evidence_normalized(claim, transcript, min_coverage=1.0).evidence
    # Name only is not enough (inflection still differs); fold only is not enough either.
    assert not rederive_evidence_normalized(
        claim, transcript, min_coverage=1.0, persona_name="Martin Mark"
    ).evidence
    assert not rederive_evidence_normalized(
        claim, transcript, min_coverage=1.0, fold_inflection=True
    ).evidence
    ev = rederive_evidence_normalized(
        claim, transcript, min_coverage=1.0, persona_name="Martin Mark", fold_inflection=True
    ).evidence
    assert len(ev) == 1 and ev[0].text == "I play the cello every day"
    # N1 applies only to claims that BEGIN with the persona name.
    other = _write_only("Anna Lee plays the cello every day")
    assert not rederive_evidence_normalized(
        other, transcript, min_coverage=1.0, persona_name="Martin Mark", fold_inflection=True
    ).evidence
    # N never excuses an unrelated claim.
    jet = _write_only("Martin Mark flies a private jet")
    assert not rederive_evidence_normalized(
        jet, transcript, min_coverage=0.6, persona_name="Martin Mark", fold_inflection=True
    ).evidence
