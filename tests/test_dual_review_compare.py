import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dual_review", ROOT / "scripts" / "halumem_dual_review.py")
dr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dr)


def _setup(tmp_path):
    run = tmp_path / "run"
    (run / "proposals").mkdir(parents=True)
    users = [{"uuid": "u1", "sessions": [{"dialogue": [{"role": "user", "content": "I like tea."}]}]}]
    hp = tmp_path / "halu.jsonl"
    hp.write_text("".join(json.dumps(u) + "\n" for u in users))
    (run / "proposals" / "u1__s1.json").write_text(json.dumps(
        {"case_id": "u1__s1", "user_uuid": "u1", "session_index": 1, "transition": {"before": []}, "mem0_proposals": []}))
    claims = [f"c{i}" for i in range(12)]
    # old: c0-c5 flagged; new: c0-c2 flagged (c3-c5 newly kept); c6-c11 supported by both
    old = [{"case_id": "u1__s1", "claim": c, "judge_supported": i >= 6} for i, c in enumerate(claims)]
    new = [{"case_id": "u1__s1", "claim": c, "judge_supported": i >= 3} for i, c in enumerate(claims)]
    (run / "old.jsonl").write_text("".join(json.dumps(r) + "\n" for r in old))
    (run / "new.jsonl").write_text("".join(json.dumps(r) + "\n" for r in new))
    excl = tmp_path / "labelled.jsonl"
    excl.write_text(json.dumps({"case_id": "u1__s1", "claim": "c0"}) + "\n" + json.dumps({"case_id": "u1__s1", "claim": "c6"}) + "\n")
    return run, hp, excl


def test_build_compare_groups_and_exclusion(tmp_path):
    run, hp, excl = _setup(tmp_path)
    a = argparse.Namespace(run_dir=str(run), halumem_path=str(hp), new_verdicts="new.jsonl", old_verdicts="old.jsonl",
                           exclude_items=[str(excl)], n_new_flagged=24, n_kept=2, n_neither=3, seed=1,
                           manifest_id="t-v1", out_name="quiz")
    dr.build_compare(a)
    out = run / "quiz"
    man = json.loads((out / "claim-manifest.json").read_text())
    assert man["available"] == {"new_flagged": 2, "old_flagged_new_kept": 3, "neither_flagged": 5}
    assert man["drawn"] == {"new_flagged": 2, "old_flagged_new_kept": 2, "neither_flagged": 3}
    items = [json.loads(l) for l in (out / "claim-items.jsonl").read_text().splitlines()]
    assert {i["claim"] for i in items}.isdisjoint({"c0", "c6"})  # already-labelled claims never reappear
    keys = {k["item_id"]: k for k in map(json.loads, (out / "claim-judge-key.jsonl").read_text().splitlines())}
    assert all("judge" not in i and "group" not in i for i in items)  # items stay blind
    by_group = {}
    for k in keys.values():
        by_group.setdefault(k["group"], set()).add(k["judge"])
    assert by_group == {"new_flagged": {"UNSUPPORTED"}, "old_flagged_new_kept": {"SUPPORTED"}, "neither_flagged": {"SUPPORTED"}}
