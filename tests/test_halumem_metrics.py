"""Tests for the HaluMem official-metric aggregation, including a differential check
against the reference implementation when a local clone of MemTensor/HaluMem exists."""
from __future__ import annotations

import importlib.util
import os
import random
import sys
import types
from pathlib import Path

import pytest

from membud.halumem_metrics import (
    AccuracyRecord,
    IntegrityRecord,
    aggregate_extraction,
    aggregate_qa,
    aggregate_updates,
    apply_commit_decision,
)


def test_hand_computed_extraction():
    integ = [
        IntegrityRecord("system", 1.0, 2),
        IntegrityRecord("system", 0.5, 1),
        IntegrityRecord("system", 0.5, 0),
        IntegrityRecord("interference", 0.5, 0),  # resisted
        IntegrityRecord("interference", 0.5, 2),  # fell for it
    ]
    acc = [AccuracyRecord(2, True), AccuracyRecord(1, True), AccuracyRecord(0, False)]
    m = aggregate_extraction(integ, acc)
    assert m["recall(all)"] == pytest.approx(1 / 3)
    # (0.5*2*1.0 + 0.5*1*0.5 + 0) / 2.0
    assert m["weighted_recall(all)"] == pytest.approx(1.25 / 2.0)
    assert m["fmr(all)"] == pytest.approx(0.5)
    assert m["target_precision(all)"] == pytest.approx((1.0 + 0.5) / 2)
    assert m["weighted_accuracy(all)"] == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    p, r = (1.0 + 0.5) / 2, 1 / 3
    assert m["f1"] == pytest.approx(2 * p * r / (p + r))


def test_failed_judge_calls_stay_in_all_denominator_only():
    m = aggregate_extraction([IntegrityRecord("system", 1.0, 2), IntegrityRecord("system", 1.0, None)], [])
    assert m["recall(all)"] == pytest.approx(0.5)
    assert m["recall(valid)"] == pytest.approx(1.0)
    assert m["target_precision(all)"] is None and m["f1"] is None


def test_held_session_is_an_omission():
    integ = [IntegrityRecord("system", 1.0, 2), IntegrityRecord("interference", 0.5, 2)]
    acc = [AccuracyRecord(2, True)]
    held_i, held_a = apply_commit_decision(integ, acc, committed=False)
    assert held_a == [] and all(r.score == 0 for r in held_i)
    kept_i, kept_a = apply_commit_decision(integ, acc, committed=True)
    assert kept_i == integ and kept_a == acc
    assert aggregate_extraction(held_i, held_a)["recall(all)"] == 0.0


def test_update_and_qa_ratios_and_invalid_labels():
    u = aggregate_updates(["Correct", "Omission", "Hallucination", "Other", None, "garbage"])
    assert u["update_memory_num"] == 6 and u["update_memory_valid_num"] == 4
    assert u["correct_update_memory_ratio(all)"] == pytest.approx(1 / 6)
    assert u["correct_update_memory_ratio(valid)"] == pytest.approx(1 / 4)
    q = aggregate_qa(["Correct", "Correct", "Hallucination", "Omission"])
    assert q["correct_qa_ratio(all)"] == 0.5 and q["omission_qa_ratio(valid)"] == 0.25


def test_bad_scores_rejected():
    with pytest.raises(ValueError):
        aggregate_extraction([IntegrityRecord("system", 1.0, 3)], [])


def _load_reference():
    repo = Path(os.environ.get("HALUMEM_REPO", Path.home() / "Repositories" / "HaluMem-repo"))
    path = repo / "eval" / "evaluation.py"
    if not path.is_file():
        pytest.skip("reference clone of MemTensor/HaluMem not available")
    for name in ("tqdm", "eval_tools"):
        mod = types.ModuleType(name)
        mod.tqdm = lambda *a, **k: a[0]
        for fn in ("evaluation_for_memory_accuracy", "evaluation_for_memory_integrity",
                   "evaluation_for_question", "evaluation_for_update_memory"):
            setattr(mod, fn, None)
        sys.modules.setdefault(name, mod)
    spec = importlib.util.spec_from_file_location("halumem_reference_eval", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("seed", range(5))
def test_matches_reference_aggregate(seed):
    ref = _load_reference()
    rng = random.Random(seed)
    types_ = ["Persona Memory", "Event Memory", "Relationship Memory"]
    integ_ref, accs_ref, integ, accs = [], [], [], []
    for _ in range(60):
        src = rng.choice(["system", "system", "interference"])
        imp = rng.choice([0.25, 0.5, 0.75, 1.0])
        score = rng.choice([0, 1, 2, None])
        integ_ref.append({"memory_source": src, "importance": imp, "memory_integrity_score": score,
                          "memory_type": rng.choice(types_)})
        integ.append(IntegrityRecord(src, imp, score))
    integ_ref.append({"memory_source": "system", "importance": 1.0, "memory_integrity_score": 2, "memory_type": types_[0]})
    integ.append(IntegrityRecord("system", 1.0, 2))
    integ_ref.append({"memory_source": "interference", "importance": 1.0, "memory_integrity_score": 0, "memory_type": types_[0]})
    integ.append(IntegrityRecord("interference", 1.0, 0))
    for _ in range(40):
        score = rng.choice([0, 1, 2, None])
        inc = rng.choice(["true", "false"])
        accs_ref.append({"memory_accuracy_score": score, "is_included_in_golden_memories": inc})
        accs.append(AccuracyRecord(score, inc == "true"))
    accs_ref.append({"memory_accuracy_score": 2, "is_included_in_golden_memories": "true"})
    accs.append(AccuracyRecord(2, True))
    upd = [rng.choice(["Correct", "Hallucination", "Omission", "Other", "bad"]) for _ in range(20)]
    qa = [rng.choice(["Correct", "Hallucination", "Omission", "bad"]) for _ in range(20)]
    results = {
        "memory_integrity_records": integ_ref,
        "memory_accuracy_records": accs_ref,
        "memory_update_records": [{"memory_update_type": u, "importance": 1.0, "memory_type": types_[0]} for u in upd],
        "question_answering_records": [{"result_type": q} for q in qa],
        "overall_score": {
            "memory_integrity": {}, "memory_accuracy": {}, "memory_update": {}, "question_answering": {},
            "memory_type_accuracy": {t: {"memory_integrity_acc": 0, "memory_update_acc": 0, "total_num": 0, "memory_acc": 0}
                                     for t in types_},
        },
    }
    out = ref.aggregate_eval_results(results)["overall_score"]
    mine = aggregate_extraction(integ, accs)
    for k, v in out["memory_integrity"].items():
        if k in mine:
            assert mine[k] == pytest.approx(v), k
    ma = out["memory_accuracy"]
    assert mine["fmr(all)"] == pytest.approx(ma["interference_accuracy(all)"])
    assert mine["fmr(valid)"] == pytest.approx(ma["interference_accuracy(valid)"])
    assert mine["target_precision(all)"] == pytest.approx(ma["target_accuracy(all)"])
    assert mine["target_precision(valid)"] == pytest.approx(ma["target_accuracy(valid)"])
    assert mine["weighted_accuracy(all)"] == pytest.approx(ma["weighted_accuracy(all)"])
    assert mine["weighted_accuracy(valid)"] == pytest.approx(ma["weighted_accuracy(valid)"])
    assert mine["f1"] == pytest.approx(out["memory_extraction_f1"])
    ru = aggregate_updates(upd)
    for k, v in out["memory_update"].items():
        assert ru[k] == pytest.approx(v), k
    rq = aggregate_qa(qa)
    for k, v in out["question_answering"].items():
        assert rq[k] == pytest.approx(v), k


def test_paired_bootstrap_script_detects_known_effect_and_zero_effect():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "score_official", Path(__file__).resolve().parents[1] / "scripts" / "score_halumem_official.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def item(i, committed):
        return {"case_id": f"c{i}", "extracted": ["m"], "gold": [{"index": 1, "memory_source": "system", "importance": 1.0}],
                "guard_judge_commits": committed}

    cache = {}
    for i in range(20):
        cache[f"I|c{i}|1"] = {"score": "2"}
        cache[f"A|c{i}|0"] = {"score": "2", "included": "true"}
    same = [item(i, True) for i in range(20)]
    r = mod.paired_bootstrap(same, cache, resamples=200)
    assert r["recall(all)"]["diff"] == 0 and r["recall(all)"]["ci95"] == [0, 0]
    assert r["fmr(all)"]["ci95"] is None  # no interference points: undefined, not zero
    half = [item(i, i % 2 == 0) for i in range(20)]  # guard holds half the sessions
    r = mod.paired_bootstrap(half, cache, resamples=200)
    assert r["recall(all)"]["diff"] == pytest.approx(-0.5)
    assert r["recall(all)"]["ci95"][1] < 0


def test_dual_review_kappa():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "dual", Path(__file__).resolve().parents[1] / "scripts" / "halumem_dual_review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    s, u = "SUPPORTED", "UNSUPPORTED"
    assert mod.kappa([(s, s), (u, u), (s, s), (u, u)]) == pytest.approx(1.0)
    assert mod.kappa([(s, u), (u, s), (s, u), (u, s)]) == pytest.approx(-1.0)
    assert mod.kappa([(s, s)] * 3) is None  # no variation: undefined
