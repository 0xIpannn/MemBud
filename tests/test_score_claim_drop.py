import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("score_official", Path(__file__).resolve().parents[1] / "scripts" / "score_halumem_official.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _item(drop_kept, commits):
    return {"case_id": "c", "extracted": ["a", "b", "c"], "drop_kept": drop_kept, "guard_judge_commits": commits,
            "repair_claims": None, "gold": [{"index": 0, "memory_source": "primary", "importance": 1}]}


def _cache():
    return {"I|c|0": {"score": 2}, "DI|c|0": {"score": 0}, "A|c|0": {"score": 2, "included": "true"},
            "A|c|2": {"score": 2, "included": "true"}, "A|c|1": {"score": 0, "included": "false"}}


def test_claim_drop_rescoring_uses_kept_claims_and_original_accuracy_keys():
    conds, missing = sc._session_conditions(_item([(0, "a"), (2, "c")], False), _cache())
    integ, acc, committed = conds["guard_claim_drop"]
    assert committed and missing == 0
    assert [r.score for r in integ] == [0]  # re-judged against kept claims only (DI key)
    assert len(acc) == 2  # claim "b" (index 1) dropped; indices 0 and 2 reuse A| keys
    assert conds["guard_judge_claims"][2] is False  # session hold unchanged


def test_claim_drop_equals_unguarded_when_nothing_flagged():
    conds, _ = sc._session_conditions(_item(None, True), _cache())
    assert conds["guard_claim_drop"][0] == conds["unguarded"][0]
    assert conds["guard_claim_drop"][1] == conds["unguarded"][1]
