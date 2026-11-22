import json
from pathlib import Path

import pytest

from membud.longmemeval import (
    aggregate,
    is_abstention,
    judge_prompt,
    load_anscheck,
    load_instances,
    retrieve,
    session_text,
    verdict_is_correct,
)

FAKE_EVAL = '''
def get_anscheck_prompt(task, question, answer, response, abstention=False):
    if abstention:
        return "ABS {} | {} | {}".format(question, answer, response)
    return "{}:{} | {} | {}".format(task, question, answer, response)
'''


def test_load_anscheck_without_importing_module(tmp_path):
    (tmp_path / "src" / "evaluation").mkdir(parents=True)
    (tmp_path / "src" / "evaluation" / "evaluate_qa.py").write_text("import openai_not_installed\n" + FAKE_EVAL)
    fn = load_anscheck(tmp_path)
    q = {"question_id": "q1", "question_type": "multi-session", "question": "Q", "answer": 7}
    assert judge_prompt(fn, q, "R") == "multi-session:Q | 7 | R"
    q2 = dict(q, question_id="q1_abs")
    assert judge_prompt(fn, q2, "R").startswith("ABS")


def test_verdict_parsing_matches_reference_substring_rule():
    assert verdict_is_correct("Yes") and verdict_is_correct(" yes.\n") and verdict_is_correct("not sure, yes")
    assert not verdict_is_correct("no") and not verdict_is_correct("")


def test_aggregate_matches_print_qa_metrics_definitions():
    res = [("a", "multi-session", True), ("b", "multi-session", False), ("c_abs", "temporal-reasoning", True),
           ("d", "knowledge-update", False), ("e", "knowledge-update", False), ("f", "knowledge-update", True)]
    m = aggregate(res)
    assert m["overall_accuracy"] == pytest.approx(3 / 6)
    assert m["per_type"]["multi-session"]["accuracy"] == 0.5 and m["per_type"]["knowledge-update"]["n"] == 3
    # task-averaged = unweighted mean of per-type means: (0.5 + 1.0 + 1/3) / 3
    assert m["task_averaged_accuracy"] == pytest.approx((0.5 + 1.0 + 1 / 3) / 3)
    assert m["abstention_accuracy"] == 1.0 and m["abstention_n"] == 1
    assert is_abstention("x_abs") and not is_abstention("x")


def test_retrieve_ranks_by_rare_token_overlap_and_is_deterministic():
    mem = ["User adopted a dog named Rex", "User likes tea", "User moved to Lisbon in 2020", "User likes coffee and tea"]
    assert retrieve("What is the dog called?", mem, 1) == ["User adopted a dog named Rex"]
    assert retrieve("who moved to Lisbon", mem, 2)[0] == "User moved to Lisbon in 2020"
    assert retrieve("anything", [], 3) == []
    assert retrieve("tea", mem, 4) == retrieve("tea", mem, 4)


def test_load_instances_validates(tmp_path):
    good = {"question_id": "1", "question_type": "multi-session", "question": "q", "answer": "a",
            "haystack_sessions": [[{"role": "user", "content": "hi"}]], "haystack_dates": ["2023/01/01 (Sun) 10:00"]}
    p = tmp_path / "d.json"
    p.write_text(json.dumps([good]))
    assert len(load_instances(p)) == 1
    p.write_text(json.dumps([dict(good, question_type="bogus")]))
    with pytest.raises(ValueError):
        load_instances(p)
    p.write_text(json.dumps([dict(good, haystack_dates=[])]))
    with pytest.raises(ValueError):
        load_instances(p)


def test_session_text_hides_has_answer():
    t = session_text([{"role": "user", "content": "x", "has_answer": True}], "D")
    assert "has_answer" not in t and t.startswith("Session date: D") and "user: x" in t
