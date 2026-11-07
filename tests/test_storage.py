"""Storage hardening: versioned format, flock concurrency, recovery, semantic commit."""
import json
import threading
from pathlib import Path

import pytest

from membud.schemas import Check, Result, Transition
from membud.store import commit, run_case

PRIOR = [{"id": "r1", "claims": ["tinggal di Bandung"]}]
CANDIDATE_AFTER_VALID = [
    {"id": "r1", "claims": ["tinggal di Bandung"]},
    {"id": "r2", "claims": ["bekerja di Tokopedia"]},
]
EMPLOYMENT_EVIDENCE = [{
    "id": "e1",
    "source": "test:employment",
    "text": "Ia bekerja di Tokopedia.",
    "supports": ["bekerja di Tokopedia"],
}]
SECOND_EVIDENCE = [{
    "id": "e2",
    "source": "test:language",
    "text": "Ia mulai belajar bahasa Jepang.",
    "supports": ["belajar bahasa Jepang"],
}]


def make_valid(case_id="valid"):
    return Transition.from_dict({
        "case_id": case_id,
        "schema_version": 2,
        "before": PRIOR,
        "candidate": CANDIDATE_AFTER_VALID,
        "evidence": EMPLOYMENT_EVIDENCE,
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    })


def make_second_accept():
    return Transition.from_dict({
        "case_id": "second-accept",
        "schema_version": 2,
        "before": CANDIDATE_AFTER_VALID,
        "candidate": [
            *CANDIDATE_AFTER_VALID,
            {"id": "r3", "claims": ["belajar bahasa Jepang"]},
        ],
        "evidence": SECOND_EVIDENCE,
        "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia", "belajar bahasa Jepang"],
        "operations": [{"type": "WRITE", "record_id": "r3"}],
    })


def make_hold(before=None):
    return Transition.from_dict({
        "case_id": "hold",
        "schema_version": 2,
        "before": before if before is not None else PRIOR,
        "candidate": [*PRIOR, {"id": "r9", "claims": ["memiliki kapal pesiar"]}],
        "evidence": [],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r9"}],
    })


def expected_records(transition):
    return [{"id": record.id, "claims": list(record.claims)} for record in transition.candidate]


def v2_text(records, version):
    return json.dumps(
        {"schema_version": 2, "version": version, "records": records}, indent=2
    ) + "\n"


def audit_events(log_path):
    return [json.loads(line) for line in log_path.read_text().splitlines()]


def assert_no_temp_files(directory):
    assert not [entry.name for entry in directory.iterdir() if entry.name.endswith(".tmp")]


def assert_v2_store(store_path, expected, version):
    payload = json.loads(store_path.read_text())
    assert set(payload) == {"schema_version", "version", "records"}
    assert payload["schema_version"] == 2
    assert payload["version"] == version
    assert payload["records"] == expected


def test_legacy_v1_list_store_migrates_to_v2_on_accept(tmp_path):
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(json.dumps(PRIOR))

    assert run_case(transition, store, log).decision == "ACCEPT"

    assert_v2_store(store, expected_records(transition), version=2)
    event = audit_events(log)[-1]
    assert event["store_schema_version_before"] == 1
    assert event["store_version_before"] == 1
    assert event["store_schema_version_after"] == 2
    assert event["store_version_after"] == 2
    assert event["expected_version"] is None
    assert event["lock_acquired"] is True


def test_each_accept_increments_version_by_exactly_one(tmp_path):
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"

    first = make_valid()
    assert run_case(first, store, log, expected_version=0).decision == "ACCEPT"
    assert_v2_store(store, expected_records(first), version=1)

    second = make_second_accept()
    assert run_case(second, store, log, expected_version=1).decision == "ACCEPT"
    assert_v2_store(store, expected_records(second), version=2)

    events = audit_events(log)
    assert [(event["store_version_before"], event["store_version_after"]) for event in events] == [
        (0, 1),
        (1, 2),
    ]
    assert events[0]["store_schema_version_before"] is None
    assert events[1]["store_schema_version_before"] == 2
    assert all(event["store_schema_version_after"] == 2 for event in events)


def test_hold_leaves_store_bytes_and_version_untouched(tmp_path):
    transition = make_hold()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(v2_text(PRIOR, version=2))
    original = store.read_bytes()

    assert run_case(transition, store, log, expected_version=2).decision == "HOLD"

    assert store.read_bytes() == original
    event = audit_events(log)[-1]
    assert event["decision"] == "HOLD"
    assert event["store_schema_version_before"] == event["store_schema_version_after"] == 2
    assert event["store_version_before"] == event["store_version_after"] == 2
    assert_no_temp_files(tmp_path)


def test_hold_does_not_create_store_when_absent(tmp_path):
    transition = make_hold()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"

    assert run_case(transition, store, log, expected_version=0).decision == "HOLD"

    assert not store.exists()
    event = audit_events(log)[-1]
    assert event["store_schema_version_before"] is None
    assert event["store_schema_version_after"] is None
    assert event["store_version_before"] == event["store_version_after"] == 0
    assert Path(str(store) + ".lock").exists()  # lock file persists; it carries no state
    assert_no_temp_files(tmp_path)


def test_stale_expected_version_fails_closed_without_touching_store(tmp_path):
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(v2_text(PRIOR, version=2))
    original = store.read_bytes()

    with pytest.raises(ValueError, match="state version conflict"):
        run_case(transition, store, log, expected_version=1)

    assert store.read_bytes() == original
    event = audit_events(log)[-1]
    assert event["status"] == "error"
    assert event["error_type"] == "ValueError"
    assert "state version conflict" in event["error"]
    assert event["expected_version"] == 1
    assert event["store_version_before"] == 2
    assert event["store_schema_version_before"] == 2
    assert event["lock_acquired"] is True
    assert_no_temp_files(tmp_path)


def test_expected_version_zero_matches_an_absent_store(tmp_path):
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"

    with pytest.raises(ValueError, match="state version conflict"):
        run_case(transition, store, log, expected_version=5)
    assert not store.exists()

    store.unlink(missing_ok=True)
    log.unlink(missing_ok=True)
    assert run_case(transition, store, log, expected_version=0).decision == "ACCEPT"
    assert_v2_store(store, expected_records(transition), version=1)


def test_concurrent_writers_have_exactly_one_accept_winner(tmp_path):
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    barrier = threading.Barrier(2)
    guard = threading.Lock()
    outcomes = []

    def worker(index):
        barrier.wait()
        try:
            result = run_case(transition, store, log, run_id=f"run-{index}", expected_version=0)
            outcome = ("accepted", result.decision)
        except ValueError as exc:  # fail-closed loser
            outcome = ("rejected", str(exc))
        with guard:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)

    accepted = [outcome for outcome in outcomes if outcome[0] == "accepted"]
    rejected = [outcome for outcome in outcomes if outcome[0] == "rejected"]
    assert len(accepted) == 1 and accepted[0][1] == "ACCEPT"
    assert len(rejected) == 1 and "state version conflict" in rejected[0][1]

    assert_v2_store(store, expected_records(transition), version=1)
    events = audit_events(log)
    assert sorted(event["status"] for event in events) == ["completed", "error"]
    error_event = next(event for event in events if event["status"] == "error")
    assert error_event["expected_version"] == 0
    assert error_event["store_version_before"] == 1
    assert error_event["store_schema_version_before"] == 2
    assert_no_temp_files(tmp_path)


def test_stray_temp_files_are_removed_and_never_treated_as_state(tmp_path):
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    stray_atomic = tmp_path / ".state.json.deadbeef.tmp"
    stray_legacy = tmp_path / "state.json.tmp"
    # Bogus state that would fail reconciliation if a temp file were ever read.
    stray_atomic.write_text(json.dumps([{"id": "bogus", "claims": ["not state"]}]))
    stray_legacy.write_text("not json")

    assert run_case(transition, store, log).decision == "ACCEPT"

    assert not stray_atomic.exists()
    assert not stray_legacy.exists()
    assert_v2_store(store, expected_records(transition), version=1)
    assert_no_temp_files(tmp_path)


def test_accept_leaves_no_temp_files_and_persists_only_lock_file(tmp_path):
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"

    run_case(transition, store, log)

    assert sorted(entry.name for entry in tmp_path.iterdir()) == [
        "audit.jsonl",
        "state.json",
        "state.json.lock",
    ]
    assert_no_temp_files(tmp_path)


def test_commit_accepts_optional_semantic_check():
    transition = make_valid()
    store = {record.id: record for record in transition.before}
    checks = [
        Check("coverage", True, "ok"),
        Check("preservation", True, "ok"),
        Check("faithfulness", True, "ok"),
        Check("semantic", True, "ok"),
    ]

    result = commit(store, transition, Result(transition.case_id, "ACCEPT", checks))

    assert result == {record.id: record for record in transition.candidate}
    assert result is not store


@pytest.mark.parametrize(
    "semantic_check",
    [
        Check("semantic", False, "meaning changed"),
        Check("semantic", False, "cannot judge", status="UNCERTAIN"),
    ],
)
def test_commit_rejects_non_passing_semantic_check(semantic_check):
    transition = make_valid()
    store = {record.id: record for record in transition.before}
    checks = [
        Check("coverage", True, "ok"),
        Check("preservation", True, "ok"),
        Check("faithfulness", True, "ok"),
        semantic_check,
    ]

    with pytest.raises(ValueError, match="ACCEPT requires every required check to pass"):
        commit(store, transition, Result(transition.case_id, "ACCEPT", checks))


@pytest.mark.parametrize(
    "names",
    [
        ["coverage", "coverage", "preservation", "faithfulness"],  # duplicate
        ["coverage", "preservation", "faithfulness", "vibes"],  # unknown
        ["coverage", "preservation", "faithfulness", "semantic", "semantic"],  # duplicate optional
    ],
)
def test_commit_rejects_duplicate_or_unknown_check_names(names):
    transition = make_valid()
    store = {record.id: record for record in transition.before}
    checks = [Check(name, True, "ok") for name in names]

    with pytest.raises(ValueError, match="ACCEPT requires every required check to pass"):
        commit(store, transition, Result(transition.case_id, "ACCEPT", checks))


def test_v2_store_with_duplicate_ids_still_fails_closed(tmp_path):
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(v2_text(
        [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r1", "claims": ["claim lain"]},
        ],
        version=1,
    ))
    original = store.read_bytes()

    with pytest.raises(ValueError, match="duplicate record IDs"):
        run_case(transition, store, log)

    assert store.read_bytes() == original
    assert audit_events(log)[-1]["status"] == "error"


def test_unsupported_store_schema_version_fails_closed(tmp_path):
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(json.dumps({"schema_version": 3, "version": 1, "records": PRIOR}))
    original = store.read_bytes()

    with pytest.raises(ValueError, match="unsupported persisted store schema_version"):
        run_case(transition, store, log)

    assert store.read_bytes() == original
    assert audit_events(log)[-1]["status"] == "error"


def test_run_case_degrades_gracefully_without_fcntl(monkeypatch, tmp_path):
    import membud.store as store_module

    monkeypatch.setattr(store_module, "fcntl", None)
    transition = make_valid()
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"

    assert run_case(transition, store, log).decision == "ACCEPT"

    assert_v2_store(store, expected_records(transition), version=1)
    event = audit_events(log)[-1]
    assert event["lock_acquired"] is False
    assert not Path(str(store) + ".lock").exists()
