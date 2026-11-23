#!/usr/bin/env python3
"""Compare the DeepSeek billing export with the token-based estimates (offline, $0).

Input: the usage export the owner downloaded from the DeepSeek platform (zip with
``amount-*.csv`` and ``cost-*.csv``) and ``data/results/rq3-cost-v1/token-attribution.json``.
Output: aggregate totals only (per day and token type).  The export's user id and masked key
fragment are never written.

Comparisons
1. Price-basis error: the repository's estimate rule (every input token at the peak cache-miss
   list price, output at peak) applied to the *billed* token counts, against the billed USD.
   This isolates the error of the price assumption from token coverage.
2. Coverage: tokens and requests recorded in the membud response cache against the billed
   counts (the cache misses failed calls and calls made outside ``membud.llm``).
3. A calibration factor (billed USD / peak-rule estimate on the same tokens) for converting
   list-price estimates; it is valid only for this workload mix and window.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

PEAK = {"input": 0.30, "output": 1.20}       # USD / 1M, src/membud/llm/client.py (list price, cache miss)
OFFPEAK = {"input": 0.15, "output": 0.60}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export-zip", required=True)
    ap.add_argument("--attribution", default="data/results/rq3-cost-v1/token-attribution.json")
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    zf = zipfile.ZipFile(a.export_zip)
    amount = cost = None
    for name in zf.namelist():
        text = zf.read(name).decode("utf-8-sig")
        if Path(name).name.startswith("amount-"):
            amount = list(csv.DictReader(io.StringIO(text)))
        elif Path(name).name.startswith("cost-"):
            cost = list(csv.DictReader(io.StringIO(text)))
    if amount is None or cost is None:
        raise SystemExit("export must contain amount-*.csv and cost-*.csv")
    models = sorted({r["model"] for r in amount})
    days: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for r in amount:
        d, t = r["start_time_iso"][:10], r["type"]
        v = float(r["amount"])
        days[d][t] += v
        if r["price"]:
            days[d]["usd_from_amount_x_price"] += v * float(r["price"])
            days[d][f"usd_{t}"] += v * float(r["price"])
    for r in cost:
        days[r["start_time_iso"][:10]]["billed_usd"] += float(r["cost"])
    tot: dict[str, float] = defaultdict(float)
    for d in days.values():
        for k, v in d.items():
            tot[k] += v
    tin = tot["input_cache_hit_tokens"] + tot["input_cache_miss_tokens"]
    tout = tot["output_tokens"]
    peak_rule = (tin * PEAK["input"] + tout * PEAK["output"]) / 1e6
    offpeak_rule = (tin * OFFPEAK["input"] + tout * OFFPEAK["output"]) / 1e6
    billed = tot["billed_usd"]
    attr = json.loads(Path(a.attribution).read_text())
    cache = attr["deepseek_cache_total"]
    out = {
        "source": "DeepSeek platform usage export supplied by the project owner (amount and cost CSVs); aggregated here",
        "models": models,
        "window": [min(days), max(days)],
        "per_day": {d: {k: round(v, 6) for k, v in sorted(x.items())} for d, x in sorted(days.items())},
        "totals": {
            "billed_usd": round(billed, 6),
            "usd_from_amount_x_price": round(tot["usd_from_amount_x_price"], 6),
            "requests": int(tot["request_count"]),
            "input_cache_hit_tokens": int(tot["input_cache_hit_tokens"]),
            "input_cache_miss_tokens": int(tot["input_cache_miss_tokens"]),
            "output_tokens": int(tout),
            "cache_hit_share_of_input": tot["input_cache_hit_tokens"] / tin,
            "usd_by_type": {k[4:]: round(v, 6) for k, v in tot.items() if k.startswith("usd_") and k != "usd_from_amount_x_price"},
        },
        "price_basis_error_on_billed_tokens": {
            "peak_cache_miss_rule_usd": round(peak_rule, 4),
            "offpeak_cache_miss_rule_usd": round(offpeak_rule, 4),
            "peak_rule_over_billed": round(peak_rule / billed, 3),
            "offpeak_rule_over_billed": round(offpeak_rule / billed, 3),
            "peak_rule_relative_error": round((peak_rule - billed) / billed, 3),
            "calibration_factor_billed_over_peak_rule": round(billed / peak_rule, 4),
            "note": "same tokens, different price assumption: the difference is the price-basis error alone",
        },
        "cache_coverage": {
            "cache_requests": cache["calls_matched"], "billed_requests": int(tot["request_count"]),
            "cache_input_tokens": cache["input_tokens"], "billed_input_tokens": int(tin),
            "cache_output_tokens": cache["output_tokens"], "billed_output_tokens": int(tout),
            "input_coverage": round(cache["input_tokens"] / tin, 3),
            "output_coverage": round(cache["output_tokens"] / tout, 3),
            "cache_peak_rule_usd": round(cache["est_usd_deepseek_peak_cache_miss"], 4),
            "cache_peak_rule_over_billed": round(cache["est_usd_deepseek_peak_cache_miss"] / billed, 3),
            "note": ("The cache snapshot was taken before the RQ3 LongMemEval run (step 5) started on 2026-11-24; the "
                     "billed 2026-11-24 row includes part of that run. Billed > cache because failed calls and calls "
                     "outside membud.llm (live-run official evaluation.py judge/answer calls) are not cached."),
        },
    }
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("totals", "price_basis_error_on_billed_tokens", "cache_coverage")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
