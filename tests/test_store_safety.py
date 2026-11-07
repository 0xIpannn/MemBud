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

    assert json.loads(store.read_text()) == [
        {"id": record.id, "claims": list(record.claims)} for record in transition.candidate
    ]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["audit.jsonl", "state.json"]
    assert not list(tmp_path.glob(".*.tmp"))


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
