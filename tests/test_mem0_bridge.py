"""Tests for the host-owned Mem0 staged-proposal bridge.

These tests exercise the *bridge mapping* end to end with a fake Mem0-like
extractor (a plain callable returning plain dicts): no Mem0 package is imported
or called, no network/model call happens, and the only persistence path is the
existing reference ``GuardedMemory`` interception layer.  They demonstrate
ACCEPT-only persistence of the staged candidate and byte-identical HOLD for
unsupported/contradicted proposals; they do NOT demonstrate interception inside
unmodified Mem0 (see ``data/mem0-bridge.md``).
"""
import hashlib
import inspect
import json

import pytest

import membud.adapters.mem0_bridge as mem0_bridge_module
from membud.adapters import (
    GuardedMemory,
    InMemoryBackend,
    MEM0_RELEASE,
    MEM0_SHAPE_CONTRACT,
    MEM0_WHEEL,
    MEM0_WHEEL_SHA256,
    Mem0BridgeError,
    Mem0ProposalBridge,
    SQLiteBackend,
    StagedProposal,
    apply_staged,
    mem0_precommit_contract,
)
from membud.schemas import Evidence, Record

SCOPE = {"user_id": "u1"}
BASE = [{"id": "r1", "claims": ["tinggal di Bandung"]}]
BASE_RECORDS = (Record("r1", ("tinggal di Bandung",)),)
EVIDENCE = {
    "id": "e1",
    "supports": ["bekerja di Tokopedia"],
    "source": "chat:42",
    "text": "Ia bekerja di Tokopedia.",
}
BRIDGE = Mem0ProposalBridge()


# ---------------------------------------------------------------------------
# Helpers and fake extractor
# ---------------------------------------------------------------------------


def fake_mem0_extractor(proposals):
    """A fake Mem0-like extractor: a callable returning plain dicts.

    Stands in for a host-side extractor; it makes no network/model call and has
    no knowledge of MemBud.
    """

    def extract(*args, **kwargs):
        return [dict(proposal) for proposal in proposals]

    return extract


def make_guard(kind, tmp_path):
    backend = (
        InMemoryBackend(BASE, scope=SCOPE)
        if kind == "in-memory"
        else SQLiteBackend(tmp_path / "memory.sqlite", BASE, scope=SCOPE)
    )
    guard = GuardedMemory(
        backend,
        store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    return backend, guard


def prepare(guard, proposals, *, case_id="mem0-case", evidence=(), required_claims=(),
            delete_authorizations=None, scope=SCOPE, extractor=None):
    extractor = extractor or fake_mem0_extractor(proposals)
    return BRIDGE.prepare_proposals(
        scope=scope,
        prior=guard.capture_prior(scope),
        proposals=extractor({"messages": [{"role": "user", "content": "..."}]}),
        case_id=case_id,
        required_claims=required_claims,
        evidence=evidence,
        delete_authorizations=delete_authorizations,
    )


def read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Contract honesty and provenance pin
# ---------------------------------------------------------------------------


def test_bridge_declares_host_owned_contract_and_pinned_provenance():
    assert MEM0_RELEASE == "mem0ai==2.2.1"
    assert MEM0_WHEEL == "mem0ai-2.2.1-py3-none-any.whl"
    assert MEM0_WHEEL_SHA256 == (
        "fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d"
    )
    assert MEM0_SHAPE_CONTRACT["runtime_tested_against_mem0"] is False
    assert MEM0_SHAPE_CONTRACT["imports_mem0"] is False
    assert MEM0_SHAPE_CONTRACT["guard_is_a_hook_into_unmodified_mem0"] is False
    contract = mem0_precommit_contract.lower()
    assert "not a hook into unmodified mem0" in contract
    assert "host must own extraction and staging" in contract
    assert "no\nmem0 compatibility is claimed" in mem0_precommit_contract.lower()
    # This module must never import the Mem0 package (not installed, no runtime).
    source = inspect.getsource(mem0_bridge_module)
    assert "import mem0" not in source
    assert "from mem0" not in source
    assert MEM0_WHEEL_SHA256 in source


# ---------------------------------------------------------------------------
# Mapping: proposals -> canonical schema-v2 transition
# ---------------------------------------------------------------------------


def test_add_proposal_maps_to_schema_v2_write_transition(tmp_path):
    _, guard = make_guard("in-memory", tmp_path)
    staged = prepare(
        guard,
        [{"event": "add", "memory": "bekerja di Tokopedia", "metadata": {"confidence": 0.9}}],
        case_id="add-case",
        evidence=[EVIDENCE],
        required_claims=["tinggal di Bandung"],
    )

    assert isinstance(staged, StagedProposal)
    assert staged.case_id == "add-case"
    assert staged.transition.schema_version == 2
    assert staged.transition.before == BASE_RECORDS
    assert staged.prior_records == BASE_RECORDS
    assert staged.transition.candidate[0] == BASE_RECORDS[0]
    added = staged.transition.candidate[1]
    assert added.id.startswith("mem0-")
    assert added.claims == ("bekerja di Tokopedia",)
    assert [operation.type for operation in staged.transition.operations] == ["WRITE"]
    assert staged.transition.operations[0].record_id == added.id
    # Host metadata/provenance is retained on the staging object, not invented
    # into the canonical transition as evidence.
    assert staged.provenance["release"] == MEM0_RELEASE
    assert staged.provenance["wheel_sha256"] == MEM0_WHEEL_SHA256
    assert staged.provenance["proposals"][0]["metadata"] == {"confidence": 0.9}
    assert staged.transition.evidence == (Evidence.from_dict(EVIDENCE),)


def test_prepare_is_pure_and_writes_nothing(tmp_path):
    backend, guard = make_guard("sqlite", tmp_path)
    digest_before = backend.bytes_digest()

    staged = prepare(guard, [{"event": "add", "memory": "bekerja di Tokopedia"}])

    assert isinstance(staged, StagedProposal)
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS
    assert not guard.store_path.exists()
    assert not guard.audit_log_path.exists()
    # The staged candidate is inspectable but has not been persisted.
    assert staged.candidate[1].claims == ("bekerja di Tokopedia",)
    assert tuple(backend.snapshot(SCOPE)) != staged.candidate


def test_determinism_and_proposal_order_independence(tmp_path):
    _, guard = make_guard("in-memory", tmp_path)
    first = prepare(
        guard,
        [
            {"event": "add", "memory": "bekerja di Tokopedia"},
            {"event": "add", "memory": "punya kucing"},
        ],
    )
    second = prepare(
        guard,
        [
            {"event": "add", "memory": "bekerja di Tokopedia"},
            {"event": "add", "memory": "punya kucing"},
        ],
    )
    reversed_order = prepare(
        guard,
        [
            {"event": "add", "memory": "punya kucing"},
            {"event": "add", "memory": "bekerja di Tokopedia"},
        ],
    )

    assert first.transition_payload() == second.transition_payload()
    assert first.digest == second.digest == reversed_order.digest
    assert first.candidate == reversed_order.candidate


def test_stable_ids_are_scope_dependent(tmp_path):
    _, guard = make_guard("in-memory", tmp_path)
    user_one = prepare(guard, [{"event": "add", "memory": "bekerja di Tokopedia"}])
    user_two = BRIDGE.prepare_proposals(
        scope={"user_id": "u2"},
        prior=[],
        proposals=[{"event": "add", "memory": "bekerja di Tokopedia"}],
        case_id="other-user",
    )
    assert user_one.candidate[1].id != user_two.candidate[0].id


def test_none_event_produces_noop_transition(tmp_path):
    backend, guard = make_guard("in-memory", tmp_path)
    staged = prepare(guard, [{"event": "none", "memory": "bekerja di Tokopedia"}])

    assert [operation.type for operation in staged.transition.operations] == ["NOOP"]
    assert staged.candidate == BASE_RECORDS

    decision = staged.apply(guard)
    assert decision.decision == "ACCEPT"
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS


def test_scope_and_evidence_mapping_are_validated_and_passed_through(tmp_path):
    backend, guard = make_guard("sqlite", tmp_path)

    # Proposal scope identifiers matching the bridge scope are accepted.
    staged = prepare(
        guard,
        [
            {
                "event": "add",
                "memory": "bekerja di Tokopedia",
                "user_id": "u1",
                "agent_id": None,
                "run_id": None,
            }
        ],
        evidence=[EVIDENCE],
    )
    decision = apply_staged(guard, staged)
    assert decision.decision == "ACCEPT"
    assert decision.evidence == (Evidence.from_dict(EVIDENCE),)
    audit = decision.audit_event
    assert audit is not None
    assert audit["input"]["evidence"] == [
        {
            "id": "e1",
            "supports": ["bekerja di Tokopedia"],
            "source": "chat:42",
            "text": "Ia bekerja di Tokopedia.",
            "contradicts": [],
        }
    ]
    assert tuple(backend.snapshot(SCOPE)) == staged.candidate

    # A proposal scope that contradicts the bridge scope fails closed.
    with pytest.raises(Mem0BridgeError, match="does not match the bridge scope"):
        prepare(
            guard,
            [{"event": "add", "memory": "bekerja di Tokopedia", "user_id": "u2"}],
        )
    # Declaring a scope identifier the bridge scope does not define fails closed.
    with pytest.raises(Mem0BridgeError, match="does not"):
        prepare(
            guard,
            [{"event": "add", "memory": "bekerja di Tokopedia", "agent_id": "a1"}],
        )


# ---------------------------------------------------------------------------
# ACCEPT-only persistence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["in-memory", "sqlite"])
def test_accept_persists_exactly_the_staged_candidate(tmp_path, kind):
    backend, guard = make_guard(kind, tmp_path)
    staged = prepare(
        guard,
        [
            {
                "event": "add",
                "memory": "bekerja di Tokopedia",
                "metadata": {"confidence": 0.9},
            }
        ],
        evidence=[EVIDENCE],
        required_claims=["tinggal di Bandung"],
    )

    decision = apply_staged(guard, staged)

    assert decision.decision == "ACCEPT"
    assert decision.applied is True
    assert decision.transition == staged.transition
    assert decision.candidate == staged.candidate
    assert tuple(backend.snapshot(SCOPE)) == staged.candidate
    assert len(staged.candidate) == len(BASE_RECORDS) + 1
    # The controlled baseline is written in the versioned format exactly once.
    store = json.loads(guard.store_path.read_text(encoding="utf-8"))
    assert store["schema_version"] == 2
    assert store["version"] == 1
    assert [record["id"] for record in store["records"]] == [
        record.id for record in staged.candidate
    ]
    assert read_jsonl(guard.decision_log_path)[-1]["decision"] == "ACCEPT"


def test_update_proposal_maps_to_revise_and_accepts(tmp_path):
    backend, guard = make_guard("sqlite", tmp_path)
    staged = prepare(
        guard,
        [{"event": "update", "id": "r1", "memory": "tinggal di Surabaya"}],
        case_id="update-case",
        evidence=[
            {
                "id": "e2",
                "supports": ["tinggal di Surabaya"],
                "source": "chat:7",
                "text": "Saya pindah ke Surabaya.",
            }
        ],
    )
    assert [operation.type for operation in staged.transition.operations] == ["REVISE"]
    decision = apply_staged(guard, staged)
    assert decision.decision == "ACCEPT"
    assert tuple(backend.snapshot(SCOPE)) == (Record("r1", ("tinggal di Surabaya",)),)


def test_delete_requires_and_records_structured_authorization(tmp_path):
    backend, guard = make_guard("sqlite", tmp_path)
    evidence = [
        {
            "id": "e1",
            "supports": ["user requested deletion"],
            "source": "chat:42",
            "text": "tolong lupakan data saya",
        }
    ]
    authorization = {
        "r1": {
            "source": "chat:42",
            "evidence_id": "e1",
            "rationale": "user explicitly requested deletion",
        }
    }
    staged = prepare(
        guard,
        [{"event": "delete", "id": "r1"}],
        case_id="delete-case",
        evidence=evidence,
        delete_authorizations=authorization,
    )
    operation = staged.transition.operations[0]
    assert operation.type == "DELETE"
    assert operation.authorization is not None
    assert operation.authorization.target_id == "r1"
    assert operation.authorization.evidence_id == "e1"

    decision = apply_staged(guard, staged)
    assert decision.decision == "ACCEPT"
    assert tuple(backend.snapshot(SCOPE)) == ()
    audit = read_jsonl(guard.audit_log_path)[-1]
    audited_operation = audit["input"]["operations"][0]
    assert audited_operation["authorization"]["source"] == "chat:42"
    assert audited_operation["authorization"]["evidence_id"] == "e1"


# ---------------------------------------------------------------------------
# HOLD leaves the target untouched (records and SQLite bytes)
# ---------------------------------------------------------------------------


def test_hold_unsupported_claim_leaves_records_and_sqlite_bytes_identical(tmp_path):
    backend, guard = make_guard("sqlite", tmp_path)
    staged = prepare(
        guard,
        [{"event": "add", "memory": "memiliki kapal pesiar"}],
        case_id="unsupported-addition",
    )
    digest_before = backend.bytes_digest()

    decision = apply_staged(guard, staged)

    assert decision.decision == "HOLD"
    assert decision.applied is False
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS
    assert not guard.store_path.exists()
    assert {check.reason_code for check in decision.result.checks} >= {"UNSUPPORTED_CLAIM"}
    assert read_jsonl(guard.decision_log_path)[-1]["decision"] == "HOLD"


def test_hold_contradicted_evidence_leaves_records_and_sqlite_bytes_identical(tmp_path):
    backend, guard = make_guard("sqlite", tmp_path)
    staged = prepare(
        guard,
        [{"event": "add", "memory": "memiliki kapal pesiar"}],
        case_id="contradicted-addition",
        evidence=[
            {
                "id": "e9",
                "supports": ["tinggal di Bandung"],
                "contradicts": ["memiliki kapal pesiar"],
                "source": "chat:9",
                "text": "Saya tidak punya kapal pesiar.",
            }
        ],
    )
    digest_before = backend.bytes_digest()

    decision = apply_staged(guard, staged)

    assert decision.decision == "HOLD"
    assert decision.applied is False
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS
    assert not guard.store_path.exists()
    faith = [check for check in decision.result.checks if check.name == "faithfulness"][0]
    assert faith.status == "FAIL"
    assert faith.reason_code == "CONTRADICTED_CLAIM"


def test_hold_on_unauthorized_delete_leaves_state_identical(tmp_path):
    backend, guard = make_guard("sqlite", tmp_path)
    digest_before = backend.bytes_digest()
    with pytest.raises(Mem0BridgeError, match="structured authorization"):
        prepare(guard, [{"event": "delete", "id": "r1"}], case_id="unauthorized-delete")
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS
    assert not guard.store_path.exists()


def test_stale_staged_prior_is_refused(tmp_path):
    backend, guard = make_guard("in-memory", tmp_path)
    staged = prepare(
        guard,
        [{"event": "add", "memory": "bekerja di Tokopedia"}],
        evidence=[EVIDENCE],
    )
    # A concurrent writer changes the state after staging but before apply.
    raced = (Record("r1", ("tinggal di Surabaya",)),)
    backend.replace_state(SCOPE, raced)

    decision = apply_staged(guard, staged)

    assert decision.decision == "REJECTED"
    assert decision.applied is False
    # Either the transition no longer describes the changed prior state
    # (invalid input) or the post-verification stale-prior check fires; both are
    # fail-closed and neither writes the staged candidate.
    assert decision.error_type in {"ValueError", "StalePriorState"}
    assert backend.snapshot(SCOPE) == raced
    assert decision.candidate != tuple(backend.snapshot(SCOPE))
    assert not guard.store_path.exists()
    assert not guard.audit_log_path.exists()


# ---------------------------------------------------------------------------
# Fail-closed rejection before any write path is entered
# ---------------------------------------------------------------------------


INVALID_DELETE_AUTH = {
    "r1": {
        "source": "chat:42",
        "evidence_id": "e-missing",
        "rationale": "lost evidence",
    }
}
VALID_DELETE_AUTH = {
    "r1": {
        "source": "chat:42",
        "evidence_id": "e1",
        "rationale": "user requested deletion",
    }
}
DELETE_EVIDENCE = [
    {"id": "e1", "supports": ["user requested deletion"], "source": "chat:42", "text": "..."}
]

INVALID_CASES = [
    pytest.param(
        [{"memory": "bekerja di Tokopedia"}], {}, None, "event", id="missing-event"
    ),
    pytest.param(
        [{"event": "add", "memory": "bekerja di Tokopedia", "surprise": True}],
        {},
        None,
        "unknown keys",
        id="unknown-field",
    ),
    pytest.param(
        [{"event": "upsert", "memory": "bekerja di Tokopedia"}],
        {},
        None,
        "unsupported event",
        id="unknown-event",
    ),
    pytest.param(
        [{"event": "add"}], {}, None, "requires a memory text", id="missing-memory"
    ),
    pytest.param(
        [{"event": "add", "memory": "  "}], {}, None, "non-empty string", id="empty-memory"
    ),
    pytest.param(
        [{"event": "add", "memory": " bekerja di Tokopedia "}],
        {},
        None,
        "whitespace",
        id="non-canonical-memory",
    ),
    pytest.param(
        [{"event": "add", "id": "r1", "memory": "bekerja di Tokopedia"}],
        {},
        None,
        "already exists",
        id="add-collides-with-prior",
    ),
    pytest.param(
        [{"event": "update", "id": "missing", "memory": "x"}],
        {},
        None,
        "does not reference",
        id="update-unknown-record",
    ),
    pytest.param(
        [{"event": "update", "id": "r1", "memory": "tinggal di Bandung"}],
        {},
        None,
        "valid schema-v2 transition",
        id="update-without-change-invalidates-schema",
    ),
    pytest.param(
        [{"event": "delete", "id": "r1"}],
        DELETE_EVIDENCE,
        None,
        "structured authorization",
        id="delete-without-authorization",
    ),
    pytest.param(
        [{"event": "delete", "id": "r1", "memory": "data lain"}],
        DELETE_EVIDENCE,
        VALID_DELETE_AUTH,
        "stale or inconsistent",
        id="delete-stale-memory",
    ),
    pytest.param(
        [{"event": "delete", "id": "r1"}],
        [],
        INVALID_DELETE_AUTH,
        "valid schema-v2 transition",
        id="delete-authorization-dangling-evidence",
    ),
    pytest.param(
        [{"event": "add", "memory": "x"}],
        DELETE_EVIDENCE,
        VALID_DELETE_AUTH,
        "not deleted",
        id="unused-delete-authorization",
    ),
    pytest.param([], {}, None, "no proposals", id="empty-proposal-set"),
    pytest.param(
        [{"event": "add", "memory": "x", "metadata": {"bad": object()}}],
        {},
        None,
        "JSON-serializable",
        id="non-json-metadata",
    ),
]


@pytest.mark.parametrize("proposals,evidence,auth,match", INVALID_CASES)
def test_invalid_proposal_is_rejected_before_any_write(
    tmp_path, proposals, evidence, auth, match
):
    backend, guard = make_guard("sqlite", tmp_path)
    digest_before = backend.bytes_digest()

    with pytest.raises(Mem0BridgeError, match=match):
        prepare(
            guard,
            proposals,
            case_id="invalid-case",
            evidence=evidence,
            delete_authorizations=auth,
        )

    # Rejected during preparation: no verification, audit, store, or backend write.
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS
    assert not guard.store_path.exists()
    assert not guard.audit_log_path.exists()


def test_non_canonical_prior_snapshot_is_rejected(tmp_path):
    _, guard = make_guard("in-memory", tmp_path)
    with pytest.raises(Mem0BridgeError, match="prior snapshot is not canonical"):
        BRIDGE.prepare_proposals(
            scope=SCOPE,
            prior=[{"id": "r1", "claims": []}],
            proposals=[{"event": "add", "memory": "x"}],
            case_id="bad-prior",
        )
    other_scope_prior = guard.capture_prior({"user_id": "other"})
    with pytest.raises(Mem0BridgeError, match="scope does not match"):
        BRIDGE.prepare_proposals(
            scope=SCOPE,
            prior=other_scope_prior,
            proposals=[{"event": "add", "memory": "x"}],
            case_id="wrong-prior-scope",
        )


@pytest.mark.parametrize(
    "proposals,match",
    [
        pytest.param(
            [
                {"event": "add", "id": "new-1", "memory": "bekerja di Tokopedia"},
                {"event": "add", "id": "new-1", "memory": "punya kucing"},
            ],
            "duplicates the staged record id",
            id="duplicate-explicit-add-id",
        ),
        pytest.param(
            [
                {"event": "add", "memory": "bekerja di Tokopedia"},
                {"event": "add", "memory": "bekerja di Tokopedia"},
            ],
            "duplicates the staged record id",
            id="duplicate-derived-add-id",
        ),
        pytest.param(
            [
                {"event": "update", "id": "r1", "memory": "x"},
                {"event": "delete", "id": "r1"},
            ],
            "more than once",
            id="duplicate-target-id",
        ),
    ],
)
def test_duplicate_proposal_ids_are_rejected(tmp_path, proposals, match):
    backend, guard = make_guard("sqlite", tmp_path)
    digest_before = backend.bytes_digest()
    # Authorization is supplied so the DELETE case fails on the duplicate target
    # rather than on missing authorization.
    with pytest.raises(Mem0BridgeError, match=match):
        prepare(
            guard,
            proposals,
            delete_authorizations=VALID_DELETE_AUTH,
            evidence=DELETE_EVIDENCE,
        )
    assert backend.bytes_digest() == digest_before
    assert not guard.store_path.exists()


def test_apply_helper_rejects_wrong_types(tmp_path):
    _, guard = make_guard("in-memory", tmp_path)
    with pytest.raises(TypeError, match="GuardedMemory"):
        apply_staged(object(), "not-staged")
    staged = prepare(guard, [{"event": "add", "memory": "punya kucing"}])
    with pytest.raises(TypeError, match="StagedProposal"):
        apply_staged(guard, {"not": "staged"})


def test_staged_digest_matches_canonical_transition_payload(tmp_path):
    _, guard = make_guard("in-memory", tmp_path)
    staged = prepare(guard, [{"event": "add", "memory": "punya kucing"}])
    payload = staged.transition_payload()
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    assert staged.digest == expected
    assert staged.to_dict()["digest"] == expected
