"""End-to-end tests for the reference guarded adapter integration.

These tests demonstrate ACTUAL write interception over stdlib-only reference
backends: MemBud verification runs through ``store.run_case`` (graph path) and
the host backend is mutated only on ACCEPT.  They do not claim compatibility
with any third-party backend.
"""
import json
from pathlib import Path

import pytest

from membud.adapters import (
    GuardedMemory,
    InMemoryBackend,
    MemoryAdapter,
    SQLiteBackend,
)
from membud.schemas import Evidence, Record

SCOPE = {"user_id": "u1"}
BASE = [{"id": "r1", "claims": ["tinggal di Bandung"]}]
BASE_RECORDS = (Record("r1", ("tinggal di Bandung",)),)
ACCEPTED_REVISION = (
    Record("r1", ("tinggal di Bandung", "bekerja di Tokopedia")),
    Record("r2", ("bekerja di Tokopedia",)),
)
EVIDENCE = {
    "id": "e1",
    "supports": ["bekerja di Tokopedia"],
    "source": "chat:42",
    "text": "Ia bekerja di Tokopedia.",
}


def make_sqlite(tmp_path: Path, records=BASE, scope=SCOPE) -> SQLiteBackend:
    return SQLiteBackend(tmp_path / "memory.sqlite", records, scope=scope)


def make_guard(backend, tmp_path: Path) -> GuardedMemory:
    return GuardedMemory(
        backend,
        store_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def valid_revision_kwargs() -> dict:
    return {
        "case_id": "valid-revise",
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung", "bekerja di Tokopedia"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "operations": [
            {"type": "REVISE", "record_id": "r1", "target_id": "r1"},
            {"type": "WRITE", "record_id": "r2"},
        ],
        "evidence": [dict(EVIDENCE)],
        "required_claims": ["tinggal di Bandung"],
    }


def unsupported_addition_kwargs() -> dict:
    return {
        "case_id": "unsupported-addition",
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["memiliki kapal pesiar"]},
        ],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    }


def test_reference_backends_implement_the_structural_protocol(tmp_path):
    assert isinstance(InMemoryBackend(BASE, scope=SCOPE), MemoryAdapter)
    assert isinstance(make_sqlite(tmp_path), MemoryAdapter)


def test_reference_backends_scope_state_and_round_trip_records(tmp_path):
    backend = InMemoryBackend(BASE, scope=SCOPE)
    assert backend.snapshot(SCOPE) == BASE_RECORDS
    assert backend.snapshot({"user_id": "other"}) == ()

    sqlite_backend = make_sqlite(tmp_path)
    sqlite_backend.replace_state({"user_id": "other"}, [{"id": "r9", "claims": ["unrelated"]}])
    assert sqlite_backend.snapshot(SCOPE) == BASE_RECORDS
    assert sqlite_backend.snapshot({"user_id": "other"}) == (Record("r9", ("unrelated",)),)


@pytest.mark.parametrize("kind", ["in-memory", "sqlite"])
def test_accept_applies_exactly_the_candidate(tmp_path, kind):
    backend = InMemoryBackend(BASE, scope=SCOPE) if kind == "in-memory" else make_sqlite(tmp_path)
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(SCOPE, **valid_revision_kwargs())

    assert decision.decision == "ACCEPT"
    assert decision.applied is True
    assert isinstance(decision.evidence[0], Evidence)
    assert decision.evidence[0].source == "chat:42"
    assert decision.candidate == ACCEPTED_REVISION
    assert tuple(backend.snapshot(SCOPE)) == ACCEPTED_REVISION
    assert decision.run_id
    assert decision.result is not None and decision.result.decision == "ACCEPT"


def test_accept_audit_log_records_decision_representation_and_provenance(tmp_path):
    backend = make_sqlite(tmp_path)
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(SCOPE, **valid_revision_kwargs())

    assert decision.audit_event is not None
    audit = decision.audit_event
    assert audit["decision"] == "ACCEPT"
    assert audit["representation"] == "graph"
    assert audit["run_id"] == decision.run_id
    assert {check["name"] for check in audit["result"]["checks"]} == {
        "coverage",
        "preservation",
        "faithfulness",
    }
    # Evidence/provenance is carried into the canonical transition and audit.
    assert audit["input"]["evidence"] == [
        {
            "id": "e1",
            "supports": ["bekerja di Tokopedia"],
            "source": "chat:42",
            "text": "Ia bekerja di Tokopedia.",
            "contradicts": [],
        }
    ]
    assert decision.transition is not None
    assert decision.transition.evidence == (
        Evidence(
            id="e1",
            supports=("bekerja di Tokopedia",),
            source="chat:42",
            text="Ia bekerja di Tokopedia.",
        ),
    )
    decision_lines = read_jsonl(guard.decision_log_path)
    assert decision_lines[-1]["decision"] == "ACCEPT"
    assert decision_lines[-1]["applied"] is True
    assert decision_lines[-1]["transition"]["evidence"] == audit["input"]["evidence"]


def test_hold_leaves_sqlite_backend_bytes_identical(tmp_path):
    backend = make_sqlite(tmp_path)
    digest_before = backend.bytes_digest()
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(SCOPE, **unsupported_addition_kwargs())

    assert decision.decision == "HOLD"
    assert decision.applied is False
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS
    # Verification HOLD must not create or rewrite the controlled store file.
    assert not guard.store_path.exists()
    audit = read_jsonl(guard.audit_log_path)
    assert audit[-1]["decision"] == "HOLD"
    assert read_jsonl(guard.decision_log_path)[-1]["decision"] == "HOLD"


def test_hold_leaves_in_memory_backend_records_identical(tmp_path):
    backend = InMemoryBackend(BASE, scope=SCOPE)
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(SCOPE, **unsupported_addition_kwargs())

    assert decision.decision == "HOLD"
    assert backend.snapshot(SCOPE) == BASE_RECORDS


@pytest.mark.parametrize(
    "case_id,candidate,operations",
    [
        ("undeclared-modification", [{"id": "r1", "claims": ["tinggal di Surabaya"]}], []),
        (
            "undeclared-insertion",
            BASE + [{"id": "r2", "claims": ["bekerja di Tokopedia"]}],
            [],
        ),
        ("undeclared-removal", [], []),
    ],
)
def test_undeclared_state_changes_are_rejected_before_any_write(
    tmp_path, case_id, candidate, operations
):
    backend = make_sqlite(tmp_path)
    digest_before = backend.bytes_digest()
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(
        SCOPE, case_id=case_id, candidate=candidate, operations=operations
    )

    assert decision.decision == "REJECTED"
    assert decision.applied is False
    assert decision.error_type == "ValueError"
    assert decision.transition is None
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS
    assert not guard.store_path.exists()
    assert not guard.audit_log_path.exists()  # run_case never entered
    assert read_jsonl(guard.decision_log_path)[-1]["decision"] == "REJECTED"


def test_unauthorized_delete_is_rejected_before_any_write(tmp_path):
    backend = make_sqlite(tmp_path)
    digest_before = backend.bytes_digest()
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(
        SCOPE,
        case_id="unauthorized-delete",
        candidate=[],
        operations=[{"type": "DELETE", "target_id": "r1", "reason": "cleanup"}],
    )

    assert decision.decision == "REJECTED"
    assert decision.applied is False
    assert "authorization" in decision.reason.lower()
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS
    assert not guard.store_path.exists()


def test_delete_authorization_must_reference_transition_evidence(tmp_path):
    backend = make_sqlite(tmp_path)
    digest_before = backend.bytes_digest()
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(
        SCOPE,
        case_id="dangling-delete-authorization",
        candidate=[],
        operations=[
            {
                "type": "DELETE",
                "target_id": "r1",
                "reason": "user asked",
                "source": "chat:42",
                "evidence_id": "e-missing",
                "rationale": "user asked to forget",
            }
        ],
    )

    assert decision.decision == "REJECTED"
    assert "evidence" in decision.reason.lower()
    assert backend.bytes_digest() == digest_before
    assert tuple(backend.snapshot(SCOPE)) == BASE_RECORDS


def test_authorized_delete_accepts_and_clears_exactly_the_candidate(tmp_path):
    backend = make_sqlite(tmp_path, records=[{"id": "r1", "claims": ["data pribadi"]}])
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(
        SCOPE,
        case_id="authorized-delete",
        candidate=[],
        operations=[
            {
                "type": "DELETE",
                "target_id": "r1",
                "reason": "user asked to forget",
                "source": "chat:42",
                "evidence_id": "e1",
                "rationale": "user explicitly requested deletion",
            }
        ],
        evidence=[
            {
                "id": "e1",
                "supports": ["user requested deletion"],
                "source": "chat:42",
                "text": "tolong lupakan data saya",
            }
        ],
    )

    assert decision.decision == "ACCEPT"
    assert decision.applied is True
    assert tuple(backend.snapshot(SCOPE)) == ()
    assert decision.transition is not None
    operation = decision.transition.operations[0]
    assert operation.type == "DELETE"
    assert operation.authorization is not None
    assert operation.authorization.source == "chat:42"
    assert operation.authorization.evidence_id == "e1"
    assert operation.authorization.target_id == "r1"
    assert operation.authorization.rationale == "user explicitly requested deletion"
    # The structured authorization is visible in the audited transition input.
    audit = read_jsonl(guard.audit_log_path)[-1]
    audited_operation = audit["input"]["operations"][0]
    assert audited_operation["authorization"]["source"] == "chat:42"
    assert audited_operation["authorization"]["evidence_id"] == "e1"


def test_invalid_candidate_is_rejected_and_leaves_backend_identical(tmp_path):
    backend = make_sqlite(tmp_path)
    digest_before = backend.bytes_digest()
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(
        SCOPE, case_id="invalid-empty-claims", candidate=[{"id": "r2", "claims": []}]
    )

    assert decision.decision == "REJECTED"
    assert decision.applied is False
    assert backend.bytes_digest() == digest_before
    assert not guard.store_path.exists()


def test_stale_prior_state_is_refused_before_persisting(tmp_path):
    class RacingBackend(InMemoryBackend):
        """Mutates its own state immediately after the first prior snapshot."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.snapshots = 0

        def snapshot(self, scope):
            state = super().snapshot(scope)
            self.snapshots += 1
            if self.snapshots == 1:
                super().replace_state(scope, [Record("r1", ("tinggal di Surabaya",))])
            return state

    backend = RacingBackend(BASE, scope=SCOPE)
    guard = make_guard(backend, tmp_path)

    decision = guard.propose_update(SCOPE, **valid_revision_kwargs())

    assert decision.decision == "REJECTED"
    assert decision.applied is False
    assert decision.error_type == "StalePriorState"
    # The raced state is preserved; the accepted candidate was NOT persisted.
    assert tuple(backend.snapshot(SCOPE)) == (Record("r1", ("tinggal di Surabaya",)),)


def test_decision_log_appends_every_outcome_in_order(tmp_path):
    backend = make_sqlite(tmp_path)
    guard = make_guard(backend, tmp_path)

    guard.propose_update(SCOPE, **valid_revision_kwargs())
    guard.propose_update(
        SCOPE,
        case_id="unsupported-addition-after-accept",
        candidate=[
            {"id": "r1", "claims": ["tinggal di Bandung", "bekerja di Tokopedia"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
            {"id": "r3", "claims": ["memiliki kapal pesiar"]},
        ],
        operations=[{"type": "WRITE", "record_id": "r3"}],
    )
    guard.propose_update(
        SCOPE,
        case_id="undeclared-removal",
        candidate=[],
        operations=[],
    )

    outcomes = [record["decision"] for record in read_jsonl(guard.decision_log_path)]
    assert outcomes == ["ACCEPT", "HOLD", "REJECTED"]
    # Each decision record captures the explicit prior state it was based on.
    for record in read_jsonl(guard.decision_log_path):
        assert record["prior_state"]["records"]
        assert record["prior_state"]["scope"] == dict(SCOPE)
        assert record["backend_name"] == "reference-sqlite"
