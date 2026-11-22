"""Relational graph hypothesis evaluation (deterministic, rules-only, no API calls).

Question: on decisions that require *relational* reasoning - multi-hop
provenance, cross-record entity-relation contradictions through aliases, and
supersession chains - does a graph verifier add detection power over a fair
text verifier that receives exactly the same information?

Strategies (all read the same canonical facts; text strategies read the
lossless flat-text serialization):

``unguarded``            no gate (every schema-valid proposal is applied)
``text_k1_optimistic``   fair text, 1 relational hop, undecidable -> PASS
``text_k1_pessimistic``  fair text, 1 relational hop, undecidable -> HOLD
``text_k2_optimistic``   fair text, 2 relational hops, undecidable -> PASS
``text_closure``         fair text, follows chains to closure
``graph``                typed property graph + graph algorithms

Usage:
    PYTHONPATH=src .venv/bin/python scripts/run_relational_eval.py --freeze
    PYTHONPATH=src .venv/bin/python scripts/run_relational_eval.py \
        --output data/results/relational-v1

``--freeze`` (re)writes the hash-pinned case package
(``data/cases|labels|manifests/relational-v1*``). The evaluation regenerates
the suite from the manifest seed and refuses to run if it no longer matches
the frozen package. ``evaluation.json`` is byte-stable (no timings or
timestamps); ``timing.json`` holds latency measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from membud.paired_stats import mcnemar_exact, wilson  # noqa: E402
from membud.relational import RelationalTransition, relational_text  # noqa: E402
from membud.relational_cases import generate_suite  # noqa: E402
from membud.relational_verify import STRATEGIES, build_relational_graph, run_strategy  # noqa: E402

SUITE_ID = "relational-v1"
SEED = 20261119
PER_CELL = 8
CASES_PATH = ROOT / "data/cases/relational-v1.jsonl"
LABELS_PATH = ROOT / "data/labels/relational-v1.jsonl"
MANIFEST_PATH = ROOT / "data/manifests/relational-v1.json"
ALL_STRATEGIES = ("unguarded", *STRATEGIES)
LABEL_KEYS = ("mechanism", "family", "depth", "required_hops", "valid", "expected_decision", "fault_code", "pair_id", "split")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _package(cases: list[RelationalTransition]) -> tuple[bytes, bytes]:
    case_lines = [json.dumps(c.to_dict(), sort_keys=True, ensure_ascii=False) for c in cases]
    label_lines = [
        json.dumps({"case_id": c.case_id, **{k: c.metadata[k] for k in LABEL_KEYS}}, sort_keys=True)
        for c in cases
    ]
    return ("\n".join(case_lines) + "\n").encode(), ("\n".join(label_lines) + "\n").encode()


def freeze() -> None:
    cases = generate_suite(SEED, PER_CELL)
    case_bytes, label_bytes = _package(cases)
    CASES_PATH.write_bytes(case_bytes)
    LABELS_PATH.write_bytes(label_bytes)
    manifest = {
        "manifest_id": SUITE_ID,
        "format": "membud-relational",
        "format_version": 1,
        "generator": "membud.relational_cases.generate_suite",
        "seed": SEED,
        "per_cell": PER_CELL,
        "case_count": len(cases),
        "cases": {"path": CASES_PATH.relative_to(ROOT).as_posix(), "sha256": _sha(case_bytes)},
        "labels": {"path": LABELS_PATH.relative_to(ROOT).as_posix(), "sha256": _sha(label_bytes)},
        "labelling": "by construction from the injected fault (minimal pairs); no verifier is consulted",
        "review": {
            "status": "generator-authored, not human-reviewed",
            "note": "cases and verifiers were written by the same author; see docs/relational-hypothesis.md",
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"froze {len(cases)} cases -> {MANIFEST_PATH.relative_to(ROOT)}")


def load_frozen() -> tuple[dict[str, Any], list[RelationalTransition], dict[str, dict[str, Any]]]:
    manifest = json.loads(MANIFEST_PATH.read_text())
    case_bytes, label_bytes = CASES_PATH.read_bytes(), LABELS_PATH.read_bytes()
    if _sha(case_bytes) != manifest["cases"]["sha256"] or _sha(label_bytes) != manifest["labels"]["sha256"]:
        raise SystemExit("frozen relational package does not match its manifest hashes")
    regenerated = _package(generate_suite(manifest["seed"], manifest["per_cell"]))
    if regenerated != (case_bytes, label_bytes):
        raise SystemExit("generator output drifted from the frozen package; re-freeze deliberately")
    cases = [RelationalTransition.from_dict(json.loads(line)) for line in case_bytes.decode().splitlines()]
    labels = {row["case_id"]: row for row in map(json.loads, label_bytes.decode().splitlines())}
    return manifest, cases, labels


def _decision(strategy: str, case: RelationalTransition) -> tuple[str, list[str]]:
    if strategy == "unguarded":
        return "ACCEPT", []
    result = run_strategy(strategy, case)
    return result.decision, sorted({c.reason_code for c in result.checks if not c.passed})


def _summary(rows: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    valid = [r for r in rows if r["valid"]]
    faulty = [r for r in rows if not r["valid"]]
    fa = sum(r["decisions"][strategy]["decision"] == "ACCEPT" for r in faulty)
    fh = sum(r["decisions"][strategy]["decision"] != "ACCEPT" for r in valid)
    correct = sum(r["decisions"][strategy]["decision"] == r["expected_decision"] for r in rows)
    return {
        "cases": len(rows),
        "label_agreement": wilson(correct, len(rows)),
        "false_accept_rate": wilson(fa, len(faulty)),
        "false_hold_rate": wilson(fh, len(valid)),
    }


def _paired(rows: list[dict[str, Any]], left: str, right: str) -> dict[str, Any]:
    """Correctness-paired comparison: b = left right & right wrong, c = reverse."""
    b = c = 0
    for r in rows:
        lc = r["decisions"][left]["decision"] == r["expected_decision"]
        rc = r["decisions"][right]["decision"] == r["expected_decision"]
        b += lc and not rc
        c += rc and not lc
    return {"left": left, "right": right, "left_only_correct": b, "right_only_correct": c, "mcnemar_exact_p": mcnemar_exact(b, c)}


def evaluate(output: Path) -> None:
    manifest, cases, labels = load_frozen()
    rows: list[dict[str, Any]] = []
    timings: dict[str, list[float]] = defaultdict(list)
    graph_sizes: list[tuple[int, int]] = []
    for case in cases:
        label = labels[case.case_id]
        decisions = {}
        for strategy in ALL_STRATEGIES:
            start = time.perf_counter()
            decision, codes = _decision(strategy, case)
            timings[strategy].append((time.perf_counter() - start) * 1000)
            decisions[strategy] = {"decision": decision, "fail_codes": codes}
        g = build_relational_graph(case)
        graph_sizes.append((len(g["nodes"]), len(g["edges"])))
        rows.append({"case_id": case.case_id, **{k: label[k] for k in LABEL_KEYS}, "decisions": decisions,
                     "text_bytes": len(relational_text(case).encode())})

    overall = {s: _summary(rows, s) for s in ALL_STRATEGIES}
    by_mechanism: dict[str, Any] = {}
    for mech in sorted({r["mechanism"] for r in rows}):
        subset = [r for r in rows if r["mechanism"] == mech]
        by_mechanism[mech] = {s: _summary(subset, s) for s in ALL_STRATEGIES}
    by_hops: dict[str, Any] = {}
    for hops in sorted({r["required_hops"] for r in rows}):
        subset = [r for r in rows if r["required_hops"] == hops]
        by_hops[str(hops)] = {s: _summary(subset, s) for s in ALL_STRATEGIES}
    by_family: dict[str, Any] = {}
    for key in sorted({(r["mechanism"], r["family"], r["required_hops"]) for r in rows}):
        subset = [r for r in rows if (r["mechanism"], r["family"], r["required_hops"]) == key]
        by_family["/".join(map(str, key))] = {
            s: {
                "false_accepts": sum(r["decisions"][s]["decision"] == "ACCEPT" for r in subset if not r["valid"]),
                "false_holds": sum(r["decisions"][s]["decision"] != "ACCEPT" for r in subset if r["valid"]),
                "faulty": sum(not r["valid"] for r in subset),
                "valid": sum(r["valid"] for r in subset),
            }
            for s in ALL_STRATEGIES
        }
    by_split = {
        split: {s: _summary([r for r in rows if r["split"] == split], s) for s in ALL_STRATEGIES}
        for split in ("dev", "heldout")
    }
    graph_vs_closure_identical = sum(
        r["decisions"]["graph"] == r["decisions"]["text_closure"] for r in rows
    )
    fault_code_match = Counter()
    for r in rows:
        if not r["valid"]:
            fault_code_match[r["fault_code"] in r["decisions"]["graph"]["fail_codes"]] += 1
    comparisons = [
        _paired(rows, "graph", other) for other in ALL_STRATEGIES if other != "graph"
    ] + [_paired(rows, "text_closure", "text_k1_optimistic"), _paired(rows, "text_closure", "text_k1_pessimistic")]
    report = {
        "evaluation_id": SUITE_ID,
        "manifest": {
            "path": MANIFEST_PATH.relative_to(ROOT).as_posix(),
            "sha256": _sha(MANIFEST_PATH.read_bytes()),
            "cases_sha256": manifest["cases"]["sha256"],
            "labels_sha256": manifest["labels"]["sha256"],
        },
        "paid_api_calls": 0,
        "strategies": {"unguarded": {"kind": "no_gate"}, **STRATEGIES},
        "case_counts": {
            "total": len(rows),
            "valid": sum(r["valid"] for r in rows),
            "faulty": sum(not r["valid"] for r in rows),
            "by_required_hops": dict(sorted(Counter(str(r["required_hops"]) for r in rows).items())),
        },
        "overall": overall,
        "by_mechanism": by_mechanism,
        "by_required_hops": by_hops,
        "by_family_and_hops": by_family,
        "by_split": by_split,
        "paired_comparisons": comparisons,
        "graph_vs_text_closure": {
            "identical_decisions_and_fail_codes": graph_vs_closure_identical,
            "cases": len(rows),
        },
        "graph_fault_code_attribution": {
            "faulty_cases_with_expected_code": fault_code_match[True],
            "faulty_cases": fault_code_match[True] + fault_code_match[False],
        },
        "representation_size": {
            "graph_nodes_mean": round(statistics.fmean(n for n, _ in graph_sizes), 3),
            "graph_edges_mean": round(statistics.fmean(e for _, e in graph_sizes), 3),
            "text_bytes_mean": round(statistics.fmean(r["text_bytes"] for r in rows), 3),
        },
        "cases": [{k: v for k, v in r.items() if k != "text_bytes"} for r in rows],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "evaluation.json").write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    timing = {
        s: {
            "median_ms": round(statistics.median(v), 4),
            "p95_ms": round(sorted(v)[int(0.95 * (len(v) - 1))], 4),
            "mean_ms": round(statistics.fmean(v), 4),
        }
        for s, v in timings.items()
    }
    (output / "timing.json").write_text(json.dumps({"note": "wall-clock, machine dependent; not byte-stable", "per_case": timing}, indent=2) + "\n")

    print(f"{SUITE_ID}: {len(rows)} cases ({report['case_counts']['valid']} valid / {report['case_counts']['faulty']} faulty)")
    print(f"{'strategy':22} {'agree':>8} {'false acc':>10} {'false hold':>11}")
    for s in ALL_STRATEGIES:
        o = overall[s]
        print(f"{s:22} {o['label_agreement']['rate']:8.3f} {o['false_accept_rate']['rate']:10.3f} {o['false_hold_rate']['rate']:11.3f}")
    print(f"graph == text_closure on {graph_vs_closure_identical}/{len(rows)} cases (decision + fail codes)")
    for comp in comparisons:
        print(f"  {comp['left']} vs {comp['right']}: +{comp['left_only_correct']} / -{comp['right_only_correct']} (p={comp['mcnemar_exact_p']:.3g})")
    print(f"wrote {output / 'evaluation.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--freeze", action="store_true", help="regenerate and hash-pin the case package")
    parser.add_argument("--output", type=Path, help="evaluation output directory")
    args = parser.parse_args()
    if not args.freeze and not args.output:
        parser.error("choose --freeze and/or --output")
    if args.freeze:
        freeze()
    if args.output:
        evaluate(args.output)


if __name__ == "__main__":
    main()
