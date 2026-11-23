#!/usr/bin/env python3
"""Dose-response of the guard on injected faults (offline: no model calls, cached judge scores only).

Injection (scripts/run_halumem_inject.py) is seeded and nested: session u in [0,1) is injected at dose t iff u < t, so a
lower dose is a subset of a higher one. For each dose t this script builds the 97-session sample in which sessions with
u < t use their injected version (official judge scores from the injected run) and the rest use the recorded Mem0 version
(scores from the Mem0 run), then reports the paired effect of claim-drop and guard+repair vs unguarded with bootstrap CIs.
Dose 0 is Mem0's natural output. Doses whose injected sessions were not scored are skipped.
Injected labels are circular (the injector creates fault and label together): this shows when the guard pays off if
errors exist at a given rate, not how often errors occur naturally. Official-metric judge is DeepSeek, unvalidated.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "halumem-runs"
SEED_MOD = ROOT / "scripts" / "run_halumem_inject.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--halumem-path", default=str(Path.home() / "Repositories/HaluMem/HaluMem-Medium.jsonl"))
    ap.add_argument("--base-run", default=str(RUNS / "phase2-deepseek"))
    ap.add_argument("--injected-runs", nargs="+", default=[str(RUNS / "phase3-injected"), str(RUNS / "phase3-injected-b")])
    ap.add_argument("--doses", nargs="+", type=float, default=[0.07, 0.21, 0.5, 1.0])
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--out", default=str(RUNS / "dose-response.json"))
    a = ap.parse_args()
    sc = _load(ROOT / "scripts/score_halumem_official.py", "score_official")
    inj = _load(SEED_MOD, "inj")
    hp = Path(a.halumem_path)

    base_items = {i["case_id"]: i for i in sc.build_items(Path(a.base_run), hp)}
    base_cache = sc.load_cache(Path(a.base_run) / "official" / "judge-results.jsonl")
    inj_items: dict[str, dict] = {}
    cache = dict(base_cache)
    for rd in a.injected_runs:
        rd = Path(rd)
        if not (rd / "llm-claim-verdicts-accepted-replyctx.jsonl").exists():
            continue
        c = sc.load_cache(rd / "official" / "judge-results.jsonl") if (rd / "official" / "judge-results.jsonl").exists() else {}
        for it in sc.build_items(rd, hp):
            if not it["injected"]:
                continue
            full = [f"A|{it['case_id']}|{k}" for k in range(len(it["extracted"]))] + [f"I|{it['case_id']}|{g['index']}" for g in it["gold"]]
            if not all(k in c for k in full):
                continue  # injected session not scored in this run
            new_id = "INJ:" + it["case_id"]
            it = dict(it, case_id=new_id)
            inj_items[it["case_id"][4:]] = it
            for k, v in c.items():
                parts = k.split("|")
                if parts[1] == it["case_id"][4:]:
                    parts[1] = new_id
                    cache["|".join(parts)] = dict(v, key="|".join(parts))
    out = {"doses": {}, "sessions_injected_scored": len(inj_items),
           "note": "dose = fraction of sessions whose claims were injected (2 faults per injected session); the claim rate is faults / all claims"}
    for t in [0.0] + a.doses:
        sel = [cid for cid in base_items if cid in inj_items and inj.session_u(cid) < t]
        want = sum(1 for cid in base_items if inj.session_u(cid) < t)
        if len(sel) < want:
            out["doses"][str(t)] = {"skipped": f"{want - len(sel)} injected sessions not scored"}
            continue
        items = [inj_items[cid] if cid in sel else base_items[cid] for cid in base_items]
        faults = sum(len(i["injected"]) for i in items)
        claims = sum(len(i["extracted"]) for i in items)
        full = sc.fully_judged(items, cache)
        row = {"sessions_injected": len(sel), "faults": faults, "claims": claims, "fault_rate": round(faults / claims, 4),
               "sessions": len(items), "fully_judged": len(full)}
        for name in ("guard_claim_drop", "guard_repair"):
            res = sc.paired_bootstrap(full, cache, other=name, resamples=a.resamples)
            row[name + "_minus_unguarded"] = {m: v for m, v in res.items() if m in ("weighted_accuracy(all)", "recall(all)", "target_precision(all)", "f1")}
        out["doses"][str(t)] = row
    Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
