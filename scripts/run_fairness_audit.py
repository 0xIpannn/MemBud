"""Deterministic fairness/equivalence audit of graph vs text verification.

The audit answers a narrow, honest question: *does structural graph verification
add decision-level detection power beyond a text verifier that has the same
information?* It evaluates four strategies on the same frozen v2 transitions:

``unguarded``
    No verification gate: the schema-valid candidate is applied unconditionally
    (recorded as ``ACCEPT``; no verifier was consulted).
``text``
    The exact claim-set baseline (``membud.verification.verify`` with
    ``representation="text"``), which never reads ``evidence.contradicts``.
``text_fair``
    The fairness control in :func:`membud.fairness.verify_text_fair`: the same
    baseline plus ``evidence.contradicts`` access, so text side information
    parity with the graph is restored.
``graph``
    Structural verification from graph adjacency
    (``membud.verification.verify`` with ``representation="graph"``).

Schema-invalid (``expected_invalid``) cases are recorded as ``INVALID_INPUT``
without calling any verifier and without writing memory. This runner is
rules-only, deterministic, and makes NO paid API/LLM calls. Gold labels are used
only for reporting/agreement, never passed to a verifier.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/run_fairness_audit.py \
        --output data/results/fairness-audit-v2

The output is ``<output>/audit.json``; the format is stable and contains no
timestamps or absolute output paths, so independent runs are byte-identical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from membud.experiment import (  # noqa: E402
    ManifestCase,
    load_manifest_entries,
    load_manifest_labels,
)
from membud.fairness import verify_text_fair  # noqa: E402
from membud.verification import verify  # noqa: E402

STRATEGIES = ("unguarded", "text", "text_fair", "graph")
DECISIONS = ("ACCEPT", "HOLD", "INVALID_INPUT")
AUDIT_ID = "fairness-audit-v2"

STRATEGY_DEFINITIONS = {
    "unguarded": {
        "kind": "no_verification_gate",
        "decision_semantics": (
            "schema-valid candidate applied unconditionally; recorded as ACCEPT because no "
            "verifier was consulted (equivalent to applying the candidate)"
        ),
        "reads_contradicts": False,
    },
    "text": {
        "kind": "claim_set_baseline",
        "decision_semantics": "exact claim-set coverage/preservation/faithfulness baseline",
        "reads_contradicts": False,
    },
    "text_fair": {
        "kind": "fair_text_control",
        "decision_semantics": (
            "claim-set baseline plus evidence.contradicts access: any candidate claim that "
            "appears in evidence.contradicts fails faithfulness with CONTRADICTED_CLAIM"
        ),
        "reads_contradicts": True,
    },
    "graph": {
        "kind": "structural_graph",
        "decision_semantics": "verification derived from canonical graph adjacency and contradiction edges",
        "reads_contradicts": True,
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    """Stable repo-relative path when possible, else the bare file name."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _reason_codes(result: Any) -> list[str]:
    return sorted({check.reason_code for check in result.checks if check.reason_code})


def _decision(
    entry: ManifestCase,
    strategy: str,
) -> dict[str, Any]:
    """Evaluate one case under one strategy, or classify invalid input directly."""
    if entry.invalid:
        return {"decision": "INVALID_INPUT", "reason_codes": [], "verified": False}
    transition = entry.transition
    assert transition is not None  # narrowed by ``entry.invalid``
    if strategy == "unguarded":
        return {"decision": "ACCEPT", "reason_codes": [], "verified": False}
    if strategy == "text":
        result = verify(transition, "text")
    elif strategy == "text_fair":
        result = verify_text_fair(transition)
    elif strategy == "graph":
        result = verify(transition, "graph")
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown strategy: {strategy}")
    return {
        "decision": result.decision,
        "reason_codes": _reason_codes(result),
        "verified": True,
    }


def _build_cases(
    entries: Iterable[ManifestCase],
    labels: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for entry in entries:
        label = labels[entry.case_id]
        expected = label["expected_decision"]
        decisions = {strategy: _decision(entry, strategy) for strategy in STRATEGIES}
        cases.append({
            "case_id": entry.case_id,
            "split": label["split"],
            "category": label["category"],
            "expected": expected,
            "expected_invalid": bool(label["expected_invalid"]),
            "invalid_input": entry.invalid,
            "invalid_reason": entry.invalid_reason,
            "decisions": decisions,
        })
    return cases


def _strategy_summary(cases: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    def decision(case: dict[str, Any]) -> str:
        return case["decisions"][strategy]["decision"]

    decision_counts = {name: sum(decision(case) == name for case in cases) for name in DECISIONS}
    mismatches = [
        {"case_id": case["case_id"], "expected": case["expected"], "decision": decision(case)}
        for case in cases
        if decision(case) != case["expected"]
    ]
    false_accepts = [
        case["case_id"] for case in cases
        if case["expected"] == "HOLD" and decision(case) == "ACCEPT"
    ]
    false_holds = [
        case["case_id"] for case in cases
        if case["expected"] == "ACCEPT" and decision(case) == "HOLD"
    ]
    valid_cases = [case for case in cases if case["expected"] == "ACCEPT"]
    accepted_valid = [case for case in valid_cases if decision(case) == "ACCEPT"]
    expected_invalid = [case for case in cases if case["expected"] == "INVALID_INPUT"]
    classified_invalid = [case for case in expected_invalid if decision(case) == "INVALID_INPUT"]
    return {
        **STRATEGY_DEFINITIONS[strategy],
        "decision_counts": decision_counts,
        "label_agreement": {
            "agreed": len(cases) - len(mismatches),
            "evaluated": len(cases),
            "rate": (len(cases) - len(mismatches)) / len(cases) if cases else None,
            "mismatches": mismatches,
        },
        "false_accepts": {
            "count": len(false_accepts),
            "denominator": len([case for case in cases if case["expected"] == "HOLD"]),
            "case_ids": false_accepts,
        },
        "false_holds": {
            "count": len(false_holds),
            "denominator": len(valid_cases),
            "case_ids": false_holds,
        },
        "valid_acceptance": {
            **_rate(len(accepted_valid), len(valid_cases)),
            "case_ids": [case["case_id"] for case in accepted_valid],
        },
        "invalid_input_handling": {
            "expected": len(expected_invalid),
            "classified_invalid_input": len(classified_invalid),
        },
    }


def _breakdown(cases: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        groups.setdefault(str(case[field]), []).append(case)
    breakdown: dict[str, Any] = {}
    for name in sorted(groups):
        rows = groups[name]
        strategies: dict[str, Any] = {}
        for strategy in STRATEGIES:
            decisions = [case["decisions"][strategy]["decision"] for case in rows]
            agreed = sum(
                case["decisions"][strategy]["decision"] == case["expected"] for case in rows
            )
            strategies[strategy] = {
                "decision_counts": {
                    decision: decisions.count(decision) for decision in DECISIONS
                },
                "agreement": {
                    "agreed": agreed,
                    "evaluated": len(rows),
                    "rate": agreed / len(rows) if rows else None,
                },
                "false_accept_case_ids": [
                    case["case_id"] for case in rows
                    if case["expected"] == "HOLD"
                    and case["decisions"][strategy]["decision"] == "ACCEPT"
                ],
            }
        breakdown[name] = {
            "case_count": len(rows),
            "expected_decision_counts": {
                decision: sum(case["expected"] == decision for case in rows)
                for decision in DECISIONS
            },
            "strategies": strategies,
        }
    return breakdown


def _paired_differences(
    cases: list[dict[str, Any]],
    left: str,
    right: str,
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for case in cases:
        left_decision = case["decisions"][left]["decision"]
        right_decision = case["decisions"][right]["decision"]
        if left_decision == right_decision:
            continue
        differences.append({
            "case_id": case["case_id"],
            "expected": case["expected"],
            "split": case["split"],
            "category": case["category"],
            "decisions": {left: left_decision, right: right_decision},
            "reason_codes": {
                left: case["decisions"][left]["reason_codes"],
                right: case["decisions"][right]["reason_codes"],
            },
        })
    return differences


def _analysis_note(
    cases: list[dict[str, Any]],
    text_vs_fair: list[dict[str, Any]],
    fair_vs_graph: list[dict[str, Any]],
) -> str:
    non_invalid = [case for case in cases if case["expected"] != "INVALID_INPUT"]
    graph_only_holds = [
        diff["case_id"] for diff in fair_vs_graph
        if diff["decisions"]["graph"] == "HOLD" and diff["decisions"]["text_fair"] == "ACCEPT"
    ]
    fair_only_holds = [
        diff["case_id"] for diff in fair_vs_graph
        if diff["decisions"]["text_fair"] == "HOLD" and diff["decisions"]["graph"] == "ACCEPT"
    ]
    fair_false_accepts = [
        case["case_id"] for case in non_invalid
        if case["expected"] == "HOLD"
        and case["decisions"]["text_fair"]["decision"] == "ACCEPT"
    ]
    plain_false_accepts = [
        case["case_id"] for case in non_invalid
        if case["expected"] == "HOLD"
        and case["decisions"]["text"]["decision"] == "ACCEPT"
    ]
    lines = [
        "Rules-only deterministic audit of graph vs text verification on the frozen "
        "membud-evaluation-v2 manifest (36 synthetic cases, 10 expected-invalid). "
        "No paid model calls were made and gold labels were used only for reporting.",
        "Information parity: plain text sees the claim sets but not evidence.contradicts; "
        "graph and the fair-text control both see contradiction annotations. Therefore "
        "text-vs-text_fair differences isolate the value of contradiction access, while "
        "text_fair-vs-graph differences isolate verifier construction.",
    ]
    if text_vs_fair:
        lines.append(
            "Adding contradiction access changes decisions on "
            + ", ".join(diff["case_id"] for diff in text_vs_fair)
            + "; plain text false-accepts "
            + (", ".join(plain_false_accepts) if plain_false_accepts else "no faulty cases")
            + ", fair text false-accepts "
            + (", ".join(fair_false_accepts) if fair_false_accepts else "no faulty cases")
            + "."
        )
    else:
        lines.append(
            "Adding contradiction access changed no decision on this manifest; neither text "
            "variant false-accepted a contradiction case."
        )
    if fair_vs_graph:
        parts = []
        if graph_only_holds:
            parts.append("graph HOLDs where fair text ACCEPTs: " + ", ".join(graph_only_holds))
        if fair_only_holds:
            parts.append("fair text HOLDs where graph ACCEPTs: " + ", ".join(fair_only_holds))
        lines.append(
            "Fair text and graph disagree on " + str(len(fair_vs_graph)) + " case(s): "
            + "; ".join(parts) + "."
        )
        if graph_only_holds:
            lines.append(
                "Conclusion: on this manifest graph does add decision-level detection power "
                "beyond fair text, but only on the listed cases; the single-reviewer labels "
                "mean this should be treated as exploratory evidence, not a benchmark claim."
            )
        else:
            lines.append(
                "Conclusion: this manifest provides no evidence that graph adds decision-level "
                "detection power beyond fair text; the differences are in the opposite "
                "direction or are graph-only misses."
            )
    else:
        lines.append(
            "Fair text and graph agree on all "
            + str(len(non_invalid))
            + " non-invalid cases."
        )
        lines.append(
            "Conclusion: on this manifest, once contradiction information access is equalized, "
            "structural graph verification adds no decision-level detection power beyond the "
            "fair text control. The graph-vs-plain-text gap observed on contradiction cases is "
            "explained by information access, not by representation. This is narrow evidence "
            "from a small, single-reviewer, synthetic set and does not establish equivalence "
            "in general; it does mean the earlier graph advantage should not be attributed to "
            "graph structure."
        )
    lines.append(
        "Limitations: 36 hand-authored synthetic Indonesian-themed cases, single-reviewer "
        "labels with second review pending, and rules-only verifiers. INVALID_INPUT rows are "
        "schema-construction failures recorded before verification, not verifier decisions."
    )
    return " ".join(lines)


def build_audit(manifest_path: str | Path) -> dict[str, Any]:
    """Load the manifest and return the complete deterministic audit payload."""
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = load_manifest_entries(manifest_path)
    labels = load_manifest_labels(manifest_path)
    cases = _build_cases(entries, labels)

    text_vs_fair = _paired_differences(cases, "text", "text_fair")
    fair_vs_graph = _paired_differences(cases, "text_fair", "graph")
    return {
        "audit_id": AUDIT_ID,
        "manifest": {
            "manifest_id": manifest["manifest_id"],
            "path": _relative(manifest_path),
            "sha256": _sha256(manifest_path),
            "schema_version": manifest["schema_version"],
            "status": manifest["status"],
            "case_count": manifest["case_count"],
            "case_count_loaded": len(entries),
            "expected_invalid_case_count": sum(entry.invalid for entry in entries),
            "cases_sha256": manifest["cases_sha256"],
            "labels_sha256": manifest["labels_sha256"],
            "label_review": {
                "reviewer_count": manifest["review"]["reviewer_count"],
                "second_independent_reviewer": manifest["review"]["second_independent_reviewer"],
                "second_review_status": manifest["review"]["second_review_status"],
            },
        },
        "strategies": {
            strategy: STRATEGY_DEFINITIONS[strategy] for strategy in STRATEGIES
        },
        "decision_vocabulary": list(DECISIONS),
        "paid_api_calls": 0,
        "verifier": "rules",
        "case_count": len(cases),
        "per_strategy": {
            strategy: _strategy_summary(cases, strategy) for strategy in STRATEGIES
        },
        "paired_differences": {
            "text_vs_text_fair": text_vs_fair,
            "text_fair_vs_graph": fair_vs_graph,
        },
        "breakdown": {
            "by_split": _breakdown(cases, "split"),
            "by_category": _breakdown(cases, "category"),
        },
        "cases": cases,
        "analysis_note": _analysis_note(cases, text_vs_fair, fair_vs_graph),
    }


def write_audit(manifest_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Build the audit and write ``<output_dir>/audit.json`` deterministically."""
    audit = build_audit(manifest_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return audit


def _print_summary(audit: dict[str, Any], output: str | Path) -> None:
    manifest = audit["manifest"]
    print(
        f"manifest={manifest['manifest_id']} sha256={manifest['sha256'][:12]} "
        f"cases={audit['case_count']} invalid={manifest['expected_invalid_case_count']} "
        f"paid_api_calls=0"
    )
    for strategy in STRATEGIES:
        summary = audit["per_strategy"][strategy]
        counts = summary["decision_counts"]
        agreement = summary["label_agreement"]
        print(
            f"{strategy:10s} " + " ".join(f"{name}={counts[name]}" for name in DECISIONS)
            + f"  agreement={agreement['agreed']}/{agreement['evaluated']}"
            + f"  false_accepts={summary['false_accepts']['count']}"
            + f"  false_holds={summary['false_holds']['count']}"
            + f"  valid_acceptance={summary['valid_acceptance']['numerator']}"
            f"/{summary['valid_acceptance']['denominator']}"
        )
    text_vs_fair = audit["paired_differences"]["text_vs_text_fair"]
    fair_vs_graph = audit["paired_differences"]["text_fair_vs_graph"]
    print(
        f"paired differences: text_vs_text_fair={len(text_vs_fair)} "
        f"text_fair_vs_graph={len(fair_vs_graph)}"
    )
    for name, differences in (
        ("text_vs_text_fair", text_vs_fair),
        ("text_fair_vs_graph", fair_vs_graph),
    ):
        for diff in differences:
            decisions = diff["decisions"]
            print(
                f"  {name} {diff['case_id']} expected={diff['expected']} "
                + " ".join(f"{key}={value}" for key, value in decisions.items())
            )
    print(f"wrote {Path(output) / 'audit.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", default=str(ROOT / "data/manifests/evaluation-v2.json")
    )
    parser.add_argument(
        "--output", default=str(ROOT / "data/results/fairness-audit-v2")
    )
    args = parser.parse_args()
    audit = write_audit(args.manifest, args.output)
    _print_summary(audit, args.output)


if __name__ == "__main__":
    main()
