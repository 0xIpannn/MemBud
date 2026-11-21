import json
from dataclasses import FrozenInstanceError, replace

import pytest

from membud.demo import cases
from membud.schemas import Check, Record, Result, Transition
from membud.store import commit, run_case


def case(case_id):
    return next(raw for raw in cases() if raw["case_id"] == case_id)


def passing_result(case_id):
    return Result(
        case_id,
        "ACCEPT",
        [
            Check("coverage", True, "ok"),
            Check("preservation", True, "ok"),
            Check("faithfulness", True, "ok"),
        ],
    )


def test_run_case_rejects_a_store_that_diverges_from_transition_before(tmp_path):
    transition = Transition.from_dict(case("valid"))
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(json.dumps([{"id": "r9", "claims": ["unrelated secret"]}]))
    original = store.read_bytes()

    with pytest.raises(ValueError, match="does not match transition.before"):
        run_case(transition, store, log)

    assert store.read_bytes() == original
    event = json.loads(log.read_text())
    assert event["status"] == "error"
    assert event["error_type"] == "ValueError"


def test_stale_before_cannot_wipe_persisted_records(tmp_path):
    stale = Transition("stale", before=(), candidate=(), evidence=(), operations=())
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(json.dumps([{"id": "r9", "claims": ["keep me"]}]))

    with pytest.raises(ValueError, match="does not match transition.before"):
        run_case(stale, store, log)

    assert json.loads(store.read_text()) == [{"id": "r9", "claims": ["keep me"]}]


def test_commit_rejects_store_before_divergence():
    transition = Transition.from_dict(case("valid"))
    store = {record.id: record for record in transition.before}
    store["extra"] = Record("extra", ("injected",))

    with pytest.raises(ValueError, match="does not match transition.before"):
        commit(store, transition, passing_result(transition.case_id))


def test_commit_validates_the_transition():
    transition = Transition.from_dict(case("remove-record"))
    invalid = replace(transition, operations=(replace(transition.operations[0], reason=""),))

    with pytest.raises(ValueError, match="DELETE requires an explicit reason"):
        commit(
            {record.id: record for record in transition.before},
            invalid,
            passing_result(transition.case_id),
        )


def test_commit_rejects_inconsistent_passed_flag():
    transition = Transition.from_dict(case("valid"))
    checks = [
        Check("coverage", True, "ok"),
        Check("preservation", True, "ok"),
        Check("faithfulness", True, "ok"),
    ]
    object.__setattr__(checks[2], "passed", False)  # simulate a forged result
    result = Result(transition.case_id, "ACCEPT", checks)
    store = {record.id: record for record in transition.before}

    with pytest.raises(ValueError, match="ACCEPT requires every required check to pass"):
        commit(store, transition, result)


def test_check_and_result_are_immutable():
    check = Check("coverage", True, "ok")
    with pytest.raises(FrozenInstanceError):
        check.status = "FAIL"
    result = Result("case", "HOLD", [check])
    with pytest.raises(FrozenInstanceError):
        result.decision = "ACCEPT"


def test_accept_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    transition = Transition.from_dict(case("valid"))
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    run_case(transition, store, log)

    payload = json.loads(store.read_text())
    assert payload["schema_version"] == 2
    assert payload["version"] == 1
    assert payload["records"] == [
        {"id": record.id, "claims": list(record.claims)} for record in transition.candidate
    ]
    # The lock file is a persistent, stateless flock target; no temp files remain.
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "audit.jsonl",
        "state.json",
        "state.json.lock",
    ]
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob("*.tmp"))


def test_hold_does_not_create_a_store_file(tmp_path):
    transition = Transition.from_dict(cases()[2])
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"

    assert run_case(transition, store, log).decision == "HOLD"
    assert not store.exists()
    assert len(log.read_text().splitlines()) == 1


def test_duplicate_store_ids_fail_closed(tmp_path):
    transition = Transition.from_dict(case("valid"))
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(json.dumps([
        {"id": "r1", "claims": ["tinggal di Bandung"]},
        {"id": "r1", "claims": ["claim lain"]},
    ]))
    original = store.read_bytes()

    with pytest.raises(ValueError, match="duplicate record IDs"):
        run_case(transition, store, log)
    assert store.read_bytes() == original


def test_replayed_transition_fails_closed(tmp_path):
    transition = Transition.from_dict(case("valid"))
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    run_case(transition, store, log)

    with pytest.raises(ValueError, match="does not match transition.before"):
        run_case(transition, store, log)
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert [event["status"] for event in events] == ["completed", "error"]


def test_from_dict_rejects_string_claims():
    raw = case("valid")
    raw["before"][0]["claims"] = "abc"
    with pytest.raises(ValueError, match="record claims must be a list of strings"):
        Transition.from_dict(raw)


def test_from_dict_rejects_string_required_claims():
    raw = case("valid")
    raw["required_claims"] = "tinggal di Bandung"
    with pytest.raises(ValueError, match="required_claims must be a list of strings"):
        Transition.from_dict(raw)


def test_supersede_must_use_a_new_replacement_record():
    raw = {
        "case_id": "supersede-reuse",
        "before": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "candidate": [{"id": "r2", "claims": ["tinggal di Surabaya"]}],
        "evidence": [{
            "id": "e1",
            "source": "demo",
            "text": "Sekarang ia tinggal di Surabaya.",
            "supports": ["tinggal di Surabaya"],
        }],
        "required_claims": ["tinggal di Surabaya"],
        "operations": [{"type": "SUPERSEDE", "record_id": "r2", "target_id": "r1"}],
    }
    with pytest.raises(ValueError, match="new replacement record"):
        Transition.from_dict(raw)


# ---------------------------------------------------------------------------
# Write/audit ordering: honest write-status fields and rollback (M3/m9)
# ---------------------------------------------------------------------------


def _accept_transition(case_id="t-accept"):
    return Transition.from_dict(
        {
            "schema_version": 2,
            "case_id": case_id,
            "before": [],
            "candidate": [{"id": "r1", "claims": ["A"]}],
            "evidence": [{"id": "e1", "supports": ["A"]}],
            "required_claims": [],
            "operations": [{"type": "WRITE", "record_id": "r1"}],
        }
    )


def _hold_transition(case_id="t-hold"):
    return Transition.from_dict(
        {
            "schema_version": 2,
            "case_id": case_id,
            "before": [],
            "candidate": [{"id": "r1", "claims": ["unsupported"]}],
            "evidence": [],
            "required_claims": [],
            "operations": [{"type": "WRITE", "record_id": "r1"}],
        }
    )


def test_hold_audit_event_reports_store_not_written(tmp_path):
    import membud.store as store_module

    store_path, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    result = store_module.run_case(_hold_transition(), store_path, log)
    assert result.decision == "HOLD"
    assert not store_path.exists()
    event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "completed"
    assert event["store_written"] is False
    assert event["rolled_back"] is False
    assert event["store_version_after"] == event["store_version_before"] == 0
    assert event["store_schema_version_after"] is None


def test_accept_write_failure_reports_no_write(tmp_path, monkeypatch):
    import membud.store as store_module

    def fail_write(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store_module, "_atomic_write", fail_write)
    store_path, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    with pytest.raises(OSError):
        store_module.run_case(_accept_transition(), store_path, log)
    assert not store_path.exists()
    event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "error"
    assert event["store_written"] is False
    assert event["rolled_back"] is False  # nothing was written, nothing to roll back
    assert event["store_version_after"] == 0
    assert event["store_schema_version_after"] is None


def test_accept_append_failure_rolls_back_and_reports_write_status(tmp_path, monkeypatch):
    import membud.store as store_module

    calls = {"n": 0}
    original_append = store_module._append_event

    def flaky_append(path, event):
        if event.get("status") == "completed" and calls["n"] == 0:
            calls["n"] += 1
            raise OSError(5, "Input/output error")
        return original_append(path, event)

    monkeypatch.setattr(store_module, "_append_event", flaky_append)
    store_path, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    with pytest.raises(OSError):
        store_module.run_case(_accept_transition(), store_path, log)
    # The state write was rolled back; the error event tells the truth.
    assert not store_path.exists()
    event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "error"
    assert event["store_written"] is False
    assert event["rolled_back"] is True
    assert event["store_version_after"] == 0
    assert event["store_schema_version_after"] is None


def test_accept_append_failure_restores_previous_store_bytes(tmp_path, monkeypatch):
    import membud.store as store_module

    store_path, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store_module.run_case(_accept_transition("first"), store_path, log)
    original_bytes = store_path.read_bytes()

    calls = {"n": 0}
    original_append = store_module._append_event

    def flaky_append(path, event):
        if event.get("status") == "completed" and calls["n"] == 0:
            calls["n"] += 1
            raise OSError(5, "Input/output error")
        return original_append(path, event)

    second = Transition.from_dict(
        {
            "schema_version": 2,
            "case_id": "second",
            "before": [{"id": "r1", "claims": ["A"]}],
            "candidate": [
                {"id": "r1", "claims": ["A"]},
                {"id": "r2", "claims": ["B"]},
            ],
            "evidence": [{"id": "e2", "supports": ["B"]}],
            "required_claims": ["A"],
            "operations": [{"type": "WRITE", "record_id": "r2"}],
        }
    )
    monkeypatch.setattr(store_module, "_append_event", flaky_append)
    with pytest.raises(OSError):
        store_module.run_case(second, store_path, log)
    assert store_path.read_bytes() == original_bytes
    event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "error"
    assert event["store_written"] is False
    assert event["rolled_back"] is True
    assert event["store_version_before"] == 1
    assert event["store_version_after"] == 1


def test_pii_bearing_logs_are_created_owner_only(tmp_path):
    import os
    import stat

    import membud.store as store_module

    store_path, log_path = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store_module.run_case(_accept_transition(), store_path, log_path)
    modes = {
        "store": stat.S_IMODE(os.stat(store_path).st_mode),
        "audit": stat.S_IMODE(os.stat(log_path).st_mode),
        "lock": stat.S_IMODE(os.stat(str(store_path) + ".lock").st_mode),
    }
    assert modes == {"store": 0o600, "audit": 0o600, "lock": 0o600}


def test_run_case_require_lock_fails_closed_when_fcntl_unavailable(monkeypatch, tmp_path):
    import membud.store as store_module

    monkeypatch.setattr(store_module, "fcntl", None)
    store_path, log_path = tmp_path / "state.json", tmp_path / "audit.jsonl"
    with pytest.raises(ValueError, match="exclusive store lock"):
        store_module.run_case(
            _accept_transition(), store_path, log_path, require_lock=True
        )
    assert not store_path.exists()
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "error"
    assert event["store_written"] is False
    assert event["lock_acquired"] is False


def test_run_case_lock_timeout_fails_closed_when_lock_is_held(tmp_path):
    import fcntl
    import os
    from pathlib import Path
    import membud.store as store_module

    store_path, log_path = tmp_path / "state.json", tmp_path / "audit.jsonl"
    lock_path = Path(str(store_path) + ".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError, match="timed out"):
            store_module.run_case(
                _accept_transition(),
                store_path,
                log_path,
                lock_timeout_seconds=0.05,
            )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert not store_path.exists()
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "error"
    assert event["store_written"] is False
    assert event["lock_acquired"] is False
