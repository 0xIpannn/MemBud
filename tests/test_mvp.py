import json

import pytest

from membud.core import Transition, build_graph, commit, run_case, textual_representation, verify
from membud.demo import cases


@pytest.mark.parametrize("raw", cases())
def test_demo_cases_work_in_both_paths(raw):
    transition = Transition.from_dict(raw)
    graph_result = verify(transition, "graph")
    text_result = verify(transition, "text")
    assert graph_result.to_dict() == text_result.to_dict()
    assert isinstance(build_graph(transition)["edges"], list)
    assert json.loads(textual_representation(transition))["case_id"] == raw["case_id"]


def test_valid_update_commits_and_held_update_does_not():
    valid, unsafe = [Transition.from_dict(x) for x in (cases()[0], cases()[2])]
    store = {r.id: r for r in valid.before}
    accepted = commit(store, valid, verify(valid))
    assert "r2" in accepted
    held = commit(store, unsafe, verify(unsafe))
    assert held == store
    assert held is not store


def test_guarded_json_store_and_append_only_log(tmp_path):
    t = Transition.from_dict(cases()[2])
    store, log = tmp_path / "store.json", tmp_path / "audit.jsonl"
    store.write_text(json.dumps(cases()[2]["before"]))
    result = run_case(t, store, log)
    assert result.decision == "HOLD"
    assert json.loads(store.read_text()) == cases()[2]["before"]
    assert len(log.read_text().splitlines()) == 1


def test_validation_rejects_duplicate_record_ids():
    raw = cases()[0]
    raw["candidate"] = [raw["candidate"][0], raw["candidate"][0]]
    with pytest.raises(ValueError, match="unique"):
        Transition.from_dict(raw)
