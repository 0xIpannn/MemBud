"""Downstream conversational QA utility: guarded vs unguarded memory stores.

Runs :mod:`membud.qa_benchmark` (seeded simulated multi-session conversations,
noisy relational extractor, per-policy gating, deterministic QA reader) for a
sweep of extractor fault rates, and reports utility with paired bootstrap 95%
confidence intervals over conversations. Rules-only, no API calls.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/run_qa_utility.py \
        --output data/results/qa-utility-v1

``summary.json`` is byte-stable (no timings/timestamps); ``timing.json`` holds
per-proposal verification latency (machine dependent).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from membud.paired_stats import paired_bootstrap, wilson  # noqa: E402
from membud.qa_benchmark import POLICIES, BenchmarkConfig, conversation_metrics, run_benchmark  # noqa: E402

FAULT_RATES = (0.15, 0.35, 0.55)
PRIMARY_FAULT_RATE = 0.35
METRICS = ("overall_em", "user_em", "final_user_em", "friend_em", "likes_f1", "user_stale", "user_wrong", "user_missing")
COMPARISONS = (
    ("graph", "unguarded"),
    ("text_closure", "unguarded"),
    ("graph", "text_k1_optimistic"),
    ("graph", "text_k1_pessimistic"),
    ("graph", "text_k2_optimistic"),
    ("graph", "text_closure"),
    ("oracle_gate", "graph"),
)


def _run(cfg: BenchmarkConfig) -> tuple[dict[str, Any], dict[str, list[float]]]:
    rows = run_benchmark(cfg)
    per_policy = {p: [conversation_metrics(r, p) for r in rows] for p in POLICIES}
    aggregates: dict[str, Any] = {}
    latencies: dict[str, list[float]] = defaultdict(list)
    for p in POLICIES:
        commits: Counter = Counter()
        by_kind: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            commits.update(r["policies"][p]["commits"])
            for kind, counts in r["policies"][p]["by_kind"].items():
                by_kind[kind].update(counts)
            latencies[p].extend(r["policies"][p]["latency_ms"])
        valid = commits["accepted_valid"] + commits["held_valid"]
        invalid = commits["accepted_invalid"] + commits["held_invalid"]
        aggregates[p] = {
            "metrics_mean": {m: round(statistics.fmean(x[m] for x in per_policy[p]), 6) for m in METRICS},
            "commits": dict(sorted(commits.items())),
            "false_accept_rate": wilson(commits["accepted_invalid"], invalid),
            "false_hold_rate": wilson(commits["held_valid"], valid),
            "decisions_by_proposal_kind": {k: dict(sorted(v.items())) for k, v in sorted(by_kind.items())},
        }
    comparisons = []
    for left, right in COMPARISONS:
        entry: dict[str, Any] = {"left": left, "right": right}
        for m in METRICS:
            entry[m] = paired_bootstrap(
                [x[m] for x in per_policy[left]],
                [x[m] for x in per_policy[right]],
                seed=hash_seed(cfg.seed, left, right, m),
            )
        comparisons.append(entry)
    return {"config": cfg.to_dict(), "policies": aggregates, "paired_comparisons": comparisons}, latencies


def hash_seed(*parts: Any) -> int:
    """Stable (process-independent) seed derived from labels."""
    text = "|".join(map(str, parts))
    value = 0
    for ch in text:
        value = (value * 131 + ord(ch)) % (2**31 - 1)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--conversations", type=int, default=200)
    args = parser.parse_args()

    runs = []
    all_latency: dict[str, list[float]] = defaultdict(list)
    for rate in FAULT_RATES:
        cfg = BenchmarkConfig(conversations=args.conversations, fault_rate=rate)
        result, latencies = _run(cfg)
        runs.append(result)
        if rate == PRIMARY_FAULT_RATE:
            for p, values in latencies.items():
                all_latency[p].extend(values)
    summary = {
        "benchmark_id": "qa-utility-v1",
        "paid_api_calls": 0,
        "unit_of_analysis": "conversation (paired across policies: identical proposals and questions)",
        "primary_fault_rate": PRIMARY_FAULT_RATE,
        "policies": list(POLICIES),
        "metrics": {
            "overall_em": "exact match over all functional questions (user + friend), averaged over sessions",
            "user_em": "exact match on the user's functional attributes, averaged over sessions",
            "final_user_em": "user exact match after the last session only",
            "friend_em": "exact match on the friend's functional attributes (collateral damage)",
            "likes_f1": "set F1 of the user's many-valued likes",
            "user_stale": "share of user answers that are a previously true value",
            "user_wrong": "share of user answers that were never true for the user",
            "user_missing": "share of user questions with no stored answer",
        },
        "runs": runs,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    timing = {
        p: {"proposals": len(v), "median_ms": round(statistics.median(v), 4), "mean_ms": round(statistics.fmean(v), 4),
            "p95_ms": round(sorted(v)[int(0.95 * (len(v) - 1))], 4)}
        for p, v in all_latency.items() if v
    }
    (args.output / "timing.json").write_text(json.dumps({
        "note": "per-proposal verification wall-clock at the primary fault rate; machine dependent",
        "per_policy": timing,
    }, indent=2) + "\n")

    for run in runs:
        print(f"fault_rate={run['config']['fault_rate']} conversations={run['config']['conversations']}")
        print(f"  {'policy':20} {'overall':>8} {'user':>7} {'friend':>7} {'likesF1':>8} {'stale':>7} {'wrong':>7} {'missing':>8} {'FA':>6} {'FH':>6}")
        for p in POLICIES:
            a = run["policies"][p]
            m = a["metrics_mean"]
            print(f"  {p:20} {m['overall_em']:8.3f} {m['user_em']:7.3f} {m['friend_em']:7.3f} {m['likes_f1']:8.3f} "
                  f"{m['user_stale']:7.3f} {m['user_wrong']:7.3f} {m['user_missing']:8.3f} "
                  f"{a['false_accept_rate']['rate']:6.3f} {a['false_hold_rate']['rate']:6.3f}")
        for c in run["paired_comparisons"]:
            o = c["overall_em"]
            print(f"  {c['left']} - {c['right']}: overall_em {o['mean_diff']:+.4f} [{o['ci95_low']:+.4f}, {o['ci95_high']:+.4f}]")
    print(f"wrote {args.output / 'summary.json'}")


if __name__ == "__main__":
    main()
