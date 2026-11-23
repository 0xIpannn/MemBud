#!/usr/bin/env python3
"""RQ3 trade-off model from existing runs (offline: cached judge scores and verdicts, $0).

Inputs (read only, gitignored run directories with dataset text): the recorded Mem0+DeepSeek run
(natural errors), the injected-fault run (nested doses 1.1 / 4.1 / 7.1 % of claims), the
Mem0+Llama-3.1-8B run (natural, weaker extractor), and the token attribution produced by
``scripts/rq3_token_attribution.py`` (deployed cost per call, measured tokens, list-price dollars).

What it computes
* For every dose and condition (claim drop, guard+repair, session hold), paired session-level
  bootstrap differences vs unguarded of weighted accuracy (WA), recall and F1, plus
  ``net = dWA + dRecall`` (equal-weight benefit minus cost, both official metrics, in points).
  One bootstrap draw resamples the 97 sessions once and is reused for all doses (doses are
  nested over the same sessions), so dose-to-dose fits get joint CIs.
* Linear fit of each difference on the injected claim rate over the measured doses
  (0, 1.1, 4.1, 7.1 %): slope, intercept, and the break-even rate where ``net`` crosses 0, with
  bootstrap percentile CIs.  A break-even outside 0-7.1 % is reported but marked as an
  extrapolation (assumption).
* Errors prevented: injected faults removed by the guard (judge flags, injector labels; circular
  by construction), valid claims removed (false holds on valid claims, all non-injected flagged
  claims treated as valid, which the reviewer checks support: 1/8 and 0/8 confirmed on this
  extractor), and deployed dollars per prevented error from measured tokens at list price.
* Natural runs: prevented errors estimated as flagged claims x reviewer-confirmed flag
  precision (Wilson 95 %), and dollars per prevented error from measured judge/repair tokens.

Output holds aggregate numbers only (no dataset text, no case ids).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261124
METRICS = ("weighted_accuracy(all)", "recall(all)", "f1")
CONDS = ("guard_claim_drop", "guard_repair", "guard_judge_claims")
COND_NAMES = {"guard_claim_drop": "claim_drop", "guard_repair": "guard_repair", "guard_judge_claims": "session_hold"}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return p, max(0.0, c - h), min(1.0, c + h)


def ci(xs: list[float]) -> list[float]:
    s = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not s:
        return [float("nan"), float("nan")]
    return [round(s[int(0.025 * len(s))], 5), round(s[max(0, int(0.975 * len(s)) - 1)], 5)]


def ols(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-root", required=True)
    ap.add_argument("--halumem-path", default=str(Path.home() / "Repositories/HaluMem/HaluMem-Medium.jsonl"))
    ap.add_argument("--attribution", default=str(ROOT / "data/results/rq3-cost-v1/token-attribution.json"))
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    runs, hp = Path(a.runs_root), Path(a.halumem_path)
    sc = _load(ROOT / "scripts/score_halumem_official.py", "score_official")
    inj = _load(ROOT / "scripts/run_halumem_inject.py", "inj")
    from membud.halumem_metrics import aggregate_extraction

    attr = json.loads(Path(a.attribution).read_text())
    peak = attr["prices_usd_per_mtok"]["deepseek_peak_cache_miss"]

    # ---- build per-session condition records for every dose (mirrors scripts/halumem_dose_response.py)
    base_run, inj_run = runs / "phase2-deepseek", runs / "phase3-injected"
    base_items = {i["case_id"]: i for i in sc.build_items(base_run, hp)}
    cache = dict(sc.load_cache(base_run / "official" / "judge-results.jsonl"))
    c_inj = sc.load_cache(inj_run / "official" / "judge-results.jsonl")
    inj_items = {}
    for it in sc.build_items(inj_run, hp):
        if not it["injected"]:
            continue
        cid = it["case_id"]
        new_id = "INJ:" + cid
        inj_items[cid] = dict(it, case_id=new_id)
        for k, v in c_inj.items():
            parts = k.split("|")
            if parts[1] == cid:
                parts[1] = new_id
                cache["|".join(parts)] = dict(v, key="|".join(parts))
    order = sorted(base_items)
    doses = {}
    for t in (0.0, 0.07, 0.21, 0.5):
        sel = {cid for cid in order if cid in inj_items and inj.session_u(cid) < t}
        items = [inj_items[cid] if cid in sel else base_items[cid] for cid in order]
        faults = sum(len(i["injected"]) for i in items)
        claims = sum(len(i["extracted"]) for i in items)
        per = []
        for it in items:
            conds, missing = sc._session_conditions(it, cache)
            if missing:
                raise SystemExit(f"missing judge results at dose {t}")
            per.append({c: conds[c][:2] for c in ("unguarded", *CONDS)})
        doses[t] = {"items": items, "per": per, "fault_rate": faults / claims, "faults": faults, "claims": claims,
                    "sessions_injected": len(sel)}

    def agg(per, idx, cond):
        integ = [x for i in idx for x in per[i][cond][0]]
        acc = [x for i in idx for x in per[i][cond][1]]
        return aggregate_extraction(integ, acc)

    def diffs(per, idx):
        base = agg(per, idx, "unguarded")
        out = {}
        for c in CONDS:
            o = agg(per, idx, c)
            d = {m: (o[m] or 0) - (base[m] or 0) for m in METRICS}
            d["net"] = d["weighted_accuracy(all)"] + d["recall(all)"]
            out[c] = d
        return out

    n = len(order)
    full_idx = list(range(n))
    point = {t: diffs(d["per"], full_idx) for t, d in doses.items()}
    rng = random.Random(SEED)
    boots = {t: {c: {m: [] for m in (*METRICS, "net")} for c in CONDS} for t in doses}
    fits = {c: {m: {"intercept": [], "slope": [], "breakeven": []} for m in (*METRICS, "net")} for c in CONDS}
    rates = [doses[t]["fault_rate"] for t in doses]
    for _ in range(a.resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        dd = {t: diffs(d["per"], idx) for t, d in doses.items()}
        for t in doses:
            for c in CONDS:
                for m in (*METRICS, "net"):
                    boots[t][c][m].append(dd[t][c][m])
        for c in CONDS:
            for m in (*METRICS, "net"):
                b0, b1 = ols(rates, [dd[t][c][m] for t in doses])
                fits[c][m]["intercept"].append(b0)
                fits[c][m]["slope"].append(b1)
                fits[c][m]["breakeven"].append(-b0 / b1 if b1 > 0 else float("inf") if b0 < 0 else float("-inf"))

    # ---- errors prevented, false holds, deployed dollars per dose
    def flags(run: Path) -> dict[str, dict[str, bool]]:
        out: dict[str, dict[str, bool]] = {}
        for v in map(json.loads, (run / "llm-claim-verdicts-accepted-replyctx.jsonl").read_text().splitlines()):
            if v.get("empty"):
                continue
            out.setdefault(v["case_id"], {})[v["claim"]] = v["judge_supported"]
        return out

    f_base, f_inj = flags(base_run), flags(inj_run)
    r2 = attr["runs"]["mem0_deepseek_medium"]
    ri = attr["runs"]["mem0_deepseek_medium_injected"]

    def usd(comp: dict) -> float:
        return (comp["input_tokens"] * peak["input"] + comp["output_tokens"] * peak["output"]) / 1e6

    # per-session deployed judge cost: natural sessions from the strict judge pass (one call each),
    # injected sessions from the injected judge pass; repair cost per held session from each run
    judge_nat = usd(r2["claim_judge_strict_all_sessions"]) / r2["claim_judge_strict_all_sessions"]["calls_matched"]
    judge_inj = usd(ri["claim_judge_injected_sessions"]) / ri["claim_judge_injected_sessions"]["calls_matched"]
    rep_nat = (usd(r2["repair_extractor"]) + usd(r2["repair_verifier"])) / r2["repair_stats"]["held_sessions"]
    rep_inj = (usd(ri["repair_extractor"]) + usd(ri["repair_verifier"])) / ri["repair_stats"]["held_sessions"]
    dose_rows = {}
    for t, d in doses.items():
        injected_flagged = injected_total = valid_flagged = valid_total = held = valid_in_held = 0
        cost_guard = cost_repair = 0.0
        for it in d["items"]:
            cid = it["case_id"].removeprefix("INJ:")
            is_inj = it["case_id"].startswith("INJ:")
            verdicts = (f_inj if is_inj else f_base).get(cid, {})
            inj_claims = {r["claim"] for r in it["injected"]}
            any_flag = False
            for claim in it["extracted"]:
                flagged = verdicts.get(claim) is False
                any_flag |= flagged
                if claim in inj_claims:
                    injected_total += 1
                    injected_flagged += flagged
                else:
                    valid_total += 1
                    valid_flagged += flagged
            cost_guard += judge_inj if is_inj else judge_nat
            if any_flag:
                held += 1
                valid_in_held += sum(1 for c in it["extracted"] if c not in inj_claims)
                cost_repair += rep_inj if is_inj else rep_nat
        prevented = injected_flagged
        row = {
            "dose_sessions_fraction": t, "fault_rate": round(d["fault_rate"], 4), "faults": d["faults"], "claims": d["claims"],
            "sessions_injected": d["sessions_injected"],
            "injected_faults_removed": prevented, "injected_faults_total": injected_total,
            "valid_claims_removed_claim_drop": valid_flagged, "valid_claims_total": valid_total,
            "false_hold_rate_valid_claims_claim_drop": wilson(valid_flagged, valid_total),
            "sessions_held_session_hold": held,
            "false_hold_rate_valid_claims_session_hold": wilson(valid_in_held, valid_total),
            "deployed_usd_guard_peak": cost_guard,
            "deployed_usd_guard_repair_peak": cost_guard + cost_repair,
            "usd_per_prevented_error_guard": cost_guard / prevented if prevented else None,
            "usd_per_prevented_error_guard_repair": (cost_guard + cost_repair) / prevented if prevented else None,
            "effects": {},
        }
        for c in CONDS:
            row["effects"][COND_NAMES[c]] = {
                m: {"diff": round(point[t][c][m], 5), "ci95": ci(boots[t][c][m])} for m in (*METRICS, "net")}
        dose_rows[str(t)] = row

    fit_rows = {}
    pfit = {c: {m: ols(rates, [point[t][c][m] for t in doses]) for m in (*METRICS, "net")} for c in CONDS}
    for c in CONDS:
        fit_rows[COND_NAMES[c]] = {}
        for m in (*METRICS, "net"):
            b0, b1 = pfit[c][m]
            be = -b0 / b1 if b1 > 0 else None
            fit_rows[COND_NAMES[c]][m] = {
                "intercept": round(b0, 5), "intercept_ci95": ci(fits[c][m]["intercept"]),
                "slope_per_unit_rate": round(b1, 4), "slope_ci95": ci(fits[c][m]["slope"]),
                "breakeven_rate": (round(be, 5) if be is not None else None),
                "breakeven_ci95": ci(fits[c][m]["breakeven"]),
                "breakeven_inside_measured_range": (be is not None and 0.0 <= be <= max(rates)),
                "share_of_bootstrap_with_positive_slope": sum(s > 0 for s in fits[c][m]["slope"]) / len(fits[c][m]["slope"]),
            }

    # ---- natural runs: estimated prevented errors via reviewer-confirmed flag precision
    rw = attr["runs"]["mem0_llama8b_medium"]
    nat = {}
    for name, comp_judge, comp_rep, flagged, sessions, reviewers in (
        ("mem0_deepseek_medium", r2["claim_judge_strict_all_sessions"], (r2["repair_extractor"], r2["repair_verifier"]), 20, 97,
         {"ifan": (1, 8), "nabhan": (0, 8)}),
        ("mem0_llama8b_medium", rw["claim_judge_accepted"], (rw["repair_extractor"], rw["repair_verifier"]), 68, 100,
         {"ifan": (13, 27), "nabhan": (9, 23)}),
    ):
        guard_usd = usd(comp_judge)
        repair_usd = sum(usd(x) for x in comp_rep)
        per_rev = {}
        for rev, (k, m) in reviewers.items():
            p, lo, hi = wilson(k, m)
            per_rev[rev] = {
                "flag_precision": [round(p, 4), round(lo, 4), round(hi, 4)],
                "est_true_errors_prevented": [round(flagged * p, 2), round(flagged * lo, 2), round(flagged * hi, 2)],
                "est_valid_claims_removed": [round(flagged * (1 - p), 2), round(flagged * (1 - hi), 2), round(flagged * (1 - lo), 2)],
                "usd_per_prevented_error_guard": [guard_usd / (flagged * x) if flagged * x > 0 else None for x in (p, hi, lo)],
                "usd_per_prevented_error_guard_repair": [(guard_usd + repair_usd) / (flagged * x) if flagged * x > 0 else None for x in (p, hi, lo)],
            }
        nat[name] = {"sessions": sessions, "flagged_claims": flagged, "deployed_usd_guard_peak": guard_usd,
                     "deployed_usd_repair_peak": repair_usd, "by_reviewer": per_rev,
                     "note": "usd_per_prevented_error lists [point, best case (upper precision), worst case (lower precision)]; None = no confirmed error, i.e. unbounded"}

    out = {
        "settings": {"resamples": a.resamples, "seed": SEED, "doses_sessions_fraction": list(doses),
                     "measured_fault_rate_range": [0.0, round(max(rates), 4)],
                     "net_definition": "dWA + dRecall (official HaluMem metrics, equal weight, fraction units)",
                     "price": "DeepSeek deepseek-flash peak list price, cache miss (estimate, not a bill)"},
        "per_dose": dose_rows,
        "linear_fit_vs_fault_rate": fit_rows,
        "natural_runs": nat,
        "per_session_deployed_usd_peak": {"judge_natural": judge_nat, "judge_injected": judge_inj,
                                          "repair_per_held_session_natural": rep_nat, "repair_per_held_session_injected": rep_inj},
        "caveats": [
            "Injected labels are circular: the injector creates the fault and the label.",
            "Official metric judge is DeepSeek (benchmark uses GPT-4o) and unvalidated; the claim judge is also DeepSeek.",
            "Valid-claim counts treat every non-injected claim as valid; reviewers confirmed 1/8 and 0/8 of natural flags on this extractor.",
            "The 14.5% dose was not scored; any statement above 7.1% is an extrapolation.",
        ],
    }
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for t, r in dose_rows.items():
        e = r["effects"]
        print(t, r["fault_rate"], {k: (v["net"]["diff"], v["net"]["ci95"]) for k, v in e.items()},
              "prevented", r["injected_faults_removed"], "valid removed", r["valid_claims_removed_claim_drop"],
              "usd/prev", r["usd_per_prevented_error_guard"])
    for c, f in fit_rows.items():
        print(c, {m: (v["slope_per_unit_rate"], v["breakeven_rate"], v["breakeven_ci95"]) for m, v in f.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
