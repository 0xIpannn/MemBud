"""Cross-version format and migration tests (PLAN.md §19 item 5 / TODO Next up #5).

These tests pin the interchange policy in ``docs/format-policy.md``:

* transition v1 is archival: it loads with legacy semantics and round-trips as
  v1; upgrading to v2 is explicit and fails closed on undeclared state changes;
* store v1 (a bare JSON list) is read as schema v1 / version 1 and migrated to
  v2 by the next ACCEPT, while a HOLD never rewrites or reformats it;
* unknown future format versions are rejected rather than guessed at.

Every fixture used here is a hand-written literal, not produced by the current
writers, so a later serialization change cannot silently make these tests
trivial.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from membud.migrations import (
    MIGRATION_POLICY,
    SUPPORTED_STORE_SCHEMA_VERSIONS,
    SUPPORTED_TRANSITION_SCHEMA_VERSIONS,
    migrate_transition_to_v2,
    store_needs_migration,
    transition_needs_migration,
)
from membud.schemas import SCHEMA_VERSION, Check, Result, Transition
from membud.serialization import transition_dict
from membud.store import run_case

# ---------------------------------------------------------------------------
# Frozen literal fixtures (v1 archival formats)
# ---------------------------------------------------------------------------

LEGACY_V1_TRANSITION = {
    "case_id": "legacy-v1-supersede",
    "before": [{"id": "r1", "claims": ["bekerja di Tokopedia"]}],
    "candidate": [{"id": "r2", "claims": ["bekerja di Bukalapak"]}],
    "evidence": [
        {
            "id": "e1",
            "source": "synthetic:employment",
            "text": "Sekarang bekerja di Bukalapak.",
            "supports": ["bekerja di Bukalapak"],
        }
    ],
    "required_claims": ["bekerja di Bukalapak"],
    "supersedes": ["r1"],
}

LEGACY_V1_UNDECLARED = {
    "case_id": "legacy-v1-undeclared",
    "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
    "candidate": [
        {"id": "r1", "claims": ["tinggal di Bandung"]},
        {"id": "r2", "claims": ["memiliki kapal pesiar"]},
    ],
    "evidence": [],
    "required_claims": ["tinggal di Bandung"],
}

LEGACY_V1_STORE_JSON = '[{"id": "r1", "claims": ["tinggal di Bandung"]}]\n'


def _v2_transition(candidate_claims: tuple[str, ...]) -> Transition:
    return Transition.from_dict({
        "schema_version": 2,
        "case_id": "store-migration",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": list(candidate_claims)},
        ],
        "evidence": [
            {
                "id": "e1",
                "source": "synthetic:employment",
                "text": "Ia bekerja di Tokopedia.",
                "supports": ["bekerja di Tokopedia"],
            }
        ],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    })


def _hold_transition() -> Transition:
    return Transition.from_dict({
        "schema_version": 2,
        "case_id": "store-hold",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["memiliki kapal pesiar"]},
        ],
        "evidence": [],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    })


# ---------------------------------------------------------------------------
# Transition format policy
# ---------------------------------------------------------------------------


def test_policy_constants_are_consistent():
    assert SCHEMA_VERSION == 2
    assert SUPPORTED_TRANSITION_SCHEMA_VERSIONS == (1, 2)
    assert SUPPORTED_STORE_SCHEMA_VERSIONS == (1, 2)
    assert "archival-only" in MIGRATION_POLICY
    assert "explicitly" in MIGRATION_POLICY


def test_legacy_v1_transition_loads_and_round_trips_as_v1():
    t = Transition.from_dict(LEGACY_V1_TRANSITION)
    assert t.schema_version == 1
    # Archival round-trip must not silently upgrade the format.
    assert transition_dict(t)["schema_version"] == 1
    assert transition_needs_migration(t) is True
    # supersedes was normalized to an explicit operation on load.
    assert len(t.operations) == 1
    assert t.operations[0].type == "SUPERSEDE"


def test_explicit_migration_upgrades_to_v2_without_losing_operations():
    legacy = Transition.from_dict(LEGACY_V1_TRANSITION)
    upgraded = migrate_transition_to_v2(legacy)
    assert upgraded.schema_version == 2
    assert {record.id for record in upgraded.candidate} == {"r2"}
    assert [op.type for op in upgraded.operations] == ["SUPERSEDE"]
    assert transition_dict(upgraded)["schema_version"] == 2
    assert transition_needs_migration(upgraded) is False


def test_transition_migration_is_idempotent():
    legacy = Transition.from_dict(LEGACY_V1_TRANSITION)
    first = migrate_transition_to_v2(legacy)
    second = migrate_transition_to_v2(first)
    assert second is first


def test_transition_migration_rejects_legacy_undeclared_state_changes():
    legacy = Transition.from_dict(LEGACY_V1_UNDECLARED)
    assert legacy.schema_version == 1
    # It is admissible archival data under v1 semantics...
    with pytest.raises(ValueError, match="undeclared insertion"):
        migrate_transition_to_v2(legacy)
    # ...but it is not a valid v2 candidate without declaring the change.
    with pytest.raises(ValueError, match="undeclared insertion"):
        Transition.from_dict(dict(LEGACY_V1_UNDECLARED, schema_version=2))


def test_unknown_future_transition_versions_are_rejected():
    for version in (3, 99, -1):
        with pytest.raises(ValueError, match="unsupported schema_version"):
            Transition.from_dict(dict(LEGACY_V1_TRANSITION, schema_version=version))


def test_transition_migration_requires_a_transition():
    with pytest.raises(TypeError):
        transition_needs_migration("not-a-transition")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        migrate_transition_to_v2("not-a-transition")  # type: ignore[arg-type]


def test_store_needs_migration_policy():
    assert store_needs_migration(None) is True  # absent store == version 0
    assert store_needs_migration(1) is True
    assert store_needs_migration(2) is False
    with pytest.raises(ValueError, match="unsupported store schema_version"):
        store_needs_migration(3)


# ---------------------------------------------------------------------------
# Store format migration / refusal matrix
# ---------------------------------------------------------------------------


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_legacy_store_migrates_on_accept_and_records_audit_versions(tmp_path):
    store_path = tmp_path / "store.json"
    audit_path = tmp_path / "audit.jsonl"
    store_path.write_text(LEGACY_V1_STORE_JSON, encoding="utf-8")

    result = run_case(_v2_transition(("bekerja di Tokopedia",)), store_path, audit_path)
    assert result.decision == "ACCEPT"

    payload = _read_json(store_path)
    assert set(payload) == {"schema_version", "version", "records"}
    assert payload["schema_version"] == 2
    assert payload["version"] == 2  # legacy store read as v1 -> one ACCEPT increment
    assert {record["id"] for record in payload["records"]} == {"r1", "r2"}

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "completed"
    assert event["store_schema_version_before"] == 1
    assert event["store_schema_version_after"] == 2
    assert event["store_version_before"] == 1
    assert event["store_version_after"] == 2


def test_hold_never_rewrites_or_reformats_a_legacy_store(tmp_path):
    store_path = tmp_path / "store.json"
    audit_path = tmp_path / "audit.jsonl"
    store_path.write_text(LEGACY_V1_STORE_JSON, encoding="utf-8")
    before_bytes = store_path.read_bytes()

    result = run_case(_hold_transition(), store_path, audit_path)
    assert result.decision == "HOLD"
    assert store_path.read_bytes() == before_bytes
    assert isinstance(_read_json(store_path), list)  # still legacy v1

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["store_schema_version_before"] == 1
    assert event["store_schema_version_after"] == 1
    assert event["store_version_before"] == 1
    assert event["store_version_after"] == 1


def test_migration_happens_once_then_versions_increment_normally(tmp_path):
    store_path = tmp_path / "store.json"
    audit_path = tmp_path / "audit.jsonl"
    store_path.write_text(LEGACY_V1_STORE_JSON, encoding="utf-8")

    first = _v2_transition(("bekerja di Tokopedia",))
    assert run_case(first, store_path, audit_path).decision == "ACCEPT"
    assert _read_json(store_path)["version"] == 2

    second = Transition.from_dict({
        "schema_version": 2,
        "case_id": "store-migration-second",
        "before": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia", "bekerja di Gojek"]},
        ],
        "evidence": [
            {
                "id": "e2",
                "source": "synthetic:employment",
                "text": "Ia juga bekerja di Gojek.",
                "supports": ["bekerja di Gojek"],
            }
        ],
        "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia"],
        "operations": [{"type": "REVISE", "record_id": "r2", "target_id": "r2"}],
    })
    assert run_case(second, store_path, audit_path).decision == "ACCEPT"
    payload = _read_json(store_path)
    assert payload["schema_version"] == 2
    assert payload["version"] == 3
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert events[-1]["store_schema_version_before"] == 2
    assert events[-1]["store_version_before"] == 2


def test_unknown_future_store_version_is_rejected_without_writing(tmp_path):
    store_path = tmp_path / "store.json"
    audit_path = tmp_path / "audit.jsonl"
    future = json.dumps({
        "schema_version": 3,
        "version": 9,
        "records": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
    })
    store_path.write_text(future, encoding="utf-8")
    before_bytes = store_path.read_bytes()

    with pytest.raises(ValueError, match="unsupported persisted store schema_version"):
        run_case(_v2_transition(("bekerja di Tokopedia",)), store_path, audit_path)

    assert store_path.read_bytes() == before_bytes
    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "error"
    assert "unsupported persisted store schema_version" in event["error"]


def test_legacy_store_with_duplicate_ids_is_rejected(tmp_path):
    store_path = tmp_path / "store.json"
    audit_path = tmp_path / "audit.jsonl"
    store_path.write_text(
        '[{"id": "r1", "claims": ["a"]}, {"id": "r1", "claims": ["b"]}]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate record IDs"):
        run_case(_v2_transition(("bekerja di Tokopedia",)), store_path, audit_path)


# ---------------------------------------------------------------------------
# Commit-path version gate and strict schema_version typing (M4)
# ---------------------------------------------------------------------------


def _legacy_undeclared_transition():
    """Version-less archival transition with an undeclared insertion."""
    return Transition.from_dict(
        {
            "case_id": "legacy-insertion",
            "before": [],
            "candidate": [{"id": "r2", "claims": ["fabricated"]}],
            "evidence": [{"id": "e1", "supports": ["fabricated"]}],
        }
    )


def test_schema_version_must_be_an_integer(tmp_path):
    raw = dict(LEGACY_V1_TRANSITION)
    for value in (True, False, 1.0, 2.0, "2", "1", [], {}):
        raw["schema_version"] = value
        with pytest.raises(ValueError, match="schema_version must be an integer"):
            Transition.from_dict(raw)


def test_direct_construction_rejects_non_integer_schema_version():
    transition = Transition(
        "typed",
        before=(),
        candidate=(),
        evidence=(),
        schema_version=True,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="schema_version must be an integer"):
        transition.validate()


def test_run_case_refuses_legacy_transition_by_default(tmp_path):
    from membud.store import run_case

    legacy = _legacy_undeclared_transition()
    store_path, log_path = tmp_path / "state.json", tmp_path / "audit.jsonl"
    with pytest.raises(ValueError, match="refusing to commit legacy schema_version 1"):
        run_case(legacy, store_path, log_path)
    assert not store_path.exists()
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "error"
    assert event["error_type"] == "ValueError"
    assert event["store_written"] is False


def test_commit_refuses_legacy_transition_by_default():
    from membud.store import commit

    legacy = _legacy_undeclared_transition()
    result = Result(
        "legacy-insertion",
        "ACCEPT",
        [
            Check("coverage", True, "ok"),
            Check("preservation", True, "ok"),
            Check("faithfulness", True, "ok"),
        ],
    )
    with pytest.raises(ValueError, match="refusing to commit legacy schema_version 1"):
        commit({}, legacy, result)


def test_allow_legacy_is_an_explicit_archival_replay(tmp_path):
    from membud.store import run_case

    legacy = _legacy_undeclared_transition()
    store_path, log_path = tmp_path / "state.json", tmp_path / "audit.jsonl"
    result = run_case(legacy, store_path, log_path, allow_legacy=True)
    assert result.decision == "ACCEPT"
    # The committed envelope is always current-format v2.
    envelope = json.loads(store_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == 2
    assert any(record["id"] == "r2" for record in envelope["records"])


def test_migrations_reject_non_integer_versions():
    from membud.migrations import store_needs_migration

    for value in (True, False, 1.0, 2.0):
        with pytest.raises(ValueError, match="store schema_version must be an integer"):
            store_needs_migration(value)  # type: ignore[arg-type]
