"""Metrics for a frozen synthetic transition manifest and pilot summary."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any, Iterable

NON_ACCEPT_DECISIONS = {"HOLD", "INVALID_INPUT", "ERROR"}
EVALUATED_STATUSES = {"completed", "invalid_input"}
ERROR_STATUSES = {"error", "verifier_error"}

ESTIMATED_COST_NOTE = (
    "Estimated monetary cost is null: the provider did not report billed cost and no verified "
    "per-token price/billing data is recorded for this run. Token counts are usage telemetry, "
    "not a billing record; do not infer spend from them without matching account pricing."
)
RULES_COST_NOTE = (
    "No paid model calls were made for this condition (rules verifier). Estimated monetary "
    "cost is 0.00 USD for the run and no token usage was incurred."
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.fmean(values) if values else None


def _timing_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    timing: dict[str, Any] = {}
    for field in (
        "latency_ms", "preparation_ms", "verification_ms",
        "graph_node_count", "graph_edge_count",
    ):
        values = [row[field] for row in rows if row.get(field) is not None]
        timing[field] = {
            "mean": statistics.fmean(values) if values else None,
            "case_count": len(values),
        }
    return timing


def _llm_usage_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    llm_rows = [
        row["result"]
        for row in rows
        if isinstance(row.get("result"), dict) and row["result"].get("verifier") != "rules"
    ]
    llm_usage: dict[str, Any] = {
        field: sum(
            int((result.get("usage") or {}).get(field, 0) or 0)
            for result in llm_rows
        )
        for field in ("input_tokens", "output_tokens", "total_tokens", "api_attempts", "json_retries")
    }
    llm_usage["response_count"] = len(llm_rows)
    llm_usage["usage_reported_count"] = sum(
        (result.get("usage") or {}).get("total_tokens") is not None
        for result in llm_rows
    )
    llm_usage["model_counts"] = {
        model: sum(result.get("verifier") == model for result in llm_rows)
        for model in sorted({result.get("verifier", "unknown") for result in llm_rows})
    }
    return llm_usage


def _estimated_cost(llm_usage: dict[str, Any]) -> dict[str, Any]:
    if llm_usage.get("response_count", 0) == 0:
        return {
            "estimated_cost_usd": 0.0,
            "estimated": True,
            "billing_data_available": False,
            "basis": "rules_only_no_paid_calls",
            "note": RULES_COST_NOTE,
        }
    return {
        "estimated_cost_usd": None,
        "estimated": False,
        "billing_data_available": False,
        "basis": "provider_billing_unavailable",
        "note": ESTIMATED_COST_NOTE,
    }


def _timing_breakdown(timing: dict[str, Any]) -> dict[str, Any]:
    preparation_mean = timing["preparation_ms"]["mean"]
    verification_mean = timing["verification_ms"]["mean"]
    end_to_end_mean = timing["latency_ms"]["mean"]
    return {
        "end_to_end_ms_mean": end_to_end_mean,
        "preparation_ms_mean": preparation_mean,
        "verification_ms_mean": verification_mean,
        "graph_build_separated_from_verification": True,
        "note": (
            "preparation_ms is representation construction (graph build / text serialization); "
            "verification_ms is verifier execution only; latency_ms is end-to-end per case. "
            "Values are means over rows where instrumentation was recorded."
        ),
    }


def _reason_code_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fail: dict[str, int] = {}
    uncertain: dict[str, int] = {}
    checks_observed = 0
    for row in rows:
        result = row.get("result")
        if not isinstance(result, dict):
            continue
        for check in result.get("checks") or []:
            if not isinstance(check, dict):
                continue
            checks_observed += 1
            code = check.get("reason_code") or "(none)"
            if check.get("status") == "FAIL":
                fail[code] = fail.get(code, 0) + 1
            elif check.get("status") == "UNCERTAIN":
                uncertain[code] = uncertain.get(code, 0) + 1
    invalid_rows = sum(row.get("status") == "invalid_input" for row in rows)
    return {
        "fail": dict(sorted(fail.items())),
        "uncertain": dict(sorted(uncertain.items())),
        "invalid_input_rows": invalid_rows,
        "checks_observed": checks_observed,
    }


def _invalid_input_metrics(
    case_ids: list[str],
    condition: str,
    rows_by_key: dict[tuple[str, str], dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    def row(case_id: str) -> dict[str, Any]:
        return rows_by_key[(case_id, condition)]

    expected_invalid = [
        cid for cid in case_ids if labels[cid]["expected_decision"] == "INVALID_INPUT"
    ]
    other_ids = [cid for cid in case_ids if cid not in set(expected_invalid)]
    handled = [
        cid for cid in expected_invalid
        if row(cid).get("status") == "invalid_input" and row(cid).get("decision") == "INVALID_INPUT"
    ]
    missed = [
        cid for cid in expected_invalid
        if row(cid).get("status") == "completed"
    ]
    unexpected = [
        cid for cid in other_ids if row(cid).get("status") == "invalid_input"
    ]
    return {
        "expected_invalid_cases": len(expected_invalid),
        "handled_invalid_cases": len(handled),
        "handled_rate": _rate(len(handled), len(expected_invalid)),
        "schema_accepted_expected_invalid": len(missed),
        "unexpected_invalid_rows": len(unexpected),
        "unexpected_invalid_rate": _rate(len(unexpected), len(other_ids)),
        "rows_with_invalid_input_status": sum(
            1 for cid in case_ids if row(cid).get("status") == "invalid_input"
        ),
        "expected_invalid_case_ids": expected_invalid,
        "note": (
            "INVALID_INPUT is a construction failure recorded before verification: no "
            "ACCEPT/HOLD decision and no memory write. It is neither a verifier detection nor "
            "an execution error; expected-invalid rows are handled separately from precision/recall."
        ),
    }


def _condition_block(
    case_ids: list[str],
    condition: str,
    rows_by_key: dict[tuple[str, str], dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    def row_for(case_id: str) -> dict[str, Any]:
        return rows_by_key[(case_id, condition)]

    def is_completed(case_id: str) -> bool:
        return row_for(case_id).get("status") == "completed"

    def decision(case_id: str) -> str:
        return row_for(case_id).get("decision", "ERROR")

    faulty_ids = [
        cid for cid in case_ids if labels[cid]["expected_decision"] == "HOLD"
    ]
    valid_ids = [
        cid for cid in case_ids if labels[cid]["expected_decision"] == "ACCEPT"
    ]
    invalid_ids = [
        cid for cid in case_ids if labels[cid]["expected_decision"] == "INVALID_INPUT"
    ]

    # --- as-completed view (verifier errors leave both numerator and denominator)
    completed_faulty = [cid for cid in faulty_ids if is_completed(cid)]
    completed_valid = [cid for cid in valid_ids if is_completed(cid)]
    completed_faulty_flagged = [
        cid for cid in completed_faulty if decision(cid) in {"HOLD", "INVALID_INPUT"}
    ]
    completed_faulty_accepted = [cid for cid in completed_faulty if decision(cid) == "ACCEPT"]
    completed_valid_accepted = [cid for cid in completed_valid if decision(cid) == "ACCEPT"]
    completed_valid_held = [cid for cid in completed_valid if decision(cid) != "ACCEPT"]
    as_completed = {
        "faulty": {
            "completed": len(completed_faulty),
            "flagged": len(completed_faulty_flagged),
            "detection_rate": _rate(len(completed_faulty_flagged), len(completed_faulty)),
            "false_accepts": len(completed_faulty_accepted),
            "false_accept_rate": _rate(len(completed_faulty_accepted), len(completed_faulty)),
        },
        "valid": {
            "completed": len(completed_valid),
            "accepted": len(completed_valid_accepted),
            "acceptance_rate": _rate(len(completed_valid_accepted), len(completed_valid)),
            "held": len(completed_valid_held),
            "false_hold_rate": _rate(len(completed_valid_held), len(completed_valid)),
        },
    }

    # --- fail-closed intention-to-treat view (errors are non-accepting, not detections)
    error_faulty = [cid for cid in faulty_ids if not is_completed(cid)]
    error_valid = [cid for cid in valid_ids if not is_completed(cid)]
    itt_faulty_held = [
        cid for cid in faulty_ids if is_completed(cid) and decision(cid) in {"HOLD", "INVALID_INPUT"}
    ]
    itt_faulty_accepted = [cid for cid in faulty_ids if is_completed(cid) and decision(cid) == "ACCEPT"]
    itt_valid_accepted = [cid for cid in valid_ids if is_completed(cid) and decision(cid) == "ACCEPT"]
    fail_closed_itt = {
        "faulty": {
            "cases": len(faulty_ids),
            "held_with_decision": len(itt_faulty_held),
            "error_holds": len(error_faulty),
            "accepted": len(itt_faulty_accepted),
            "false_accept_rate": _rate(len(itt_faulty_accepted), len(faulty_ids)),
            "detection_rate": _rate(len(itt_faulty_held), len(faulty_ids)),
            "not_accepted_rate": _rate(
                len(faulty_ids) - len(itt_faulty_accepted), len(faulty_ids)
            ),
        },
        "valid": {
            "cases": len(valid_ids),
            "accepted": len(itt_valid_accepted),
            "error_holds": len(error_valid),
            "not_accepted": len(valid_ids) - len(itt_valid_accepted),
            "acceptance_rate": _rate(len(itt_valid_accepted), len(valid_ids)),
            "false_hold_rate": _rate(
                len(valid_ids) - len(itt_valid_accepted), len(valid_ids)
            ),
        },
    }

    # --- precision/recall for faulty detection, INVALID_INPUT rows handled explicitly
    gold_faulty = faulty_ids + invalid_ids
    evaluated = [cid for cid in case_ids if row_for(cid).get("status") in EVALUATED_STATUSES]
    error_rows = [cid for cid in case_ids if row_for(cid).get("status") in ERROR_STATUSES]
    true_positives = [
        cid for cid in gold_faulty
        if cid in set(evaluated) and decision(cid) in {"HOLD", "INVALID_INPUT"}
    ]
    false_positives = [
        cid for cid in valid_ids
        if cid in set(evaluated) and decision(cid) in {"HOLD", "INVALID_INPUT"}
    ]
    false_negatives = [
        cid for cid in gold_faulty
        if cid in set(evaluated) and decision(cid) == "ACCEPT"
    ]
    faulty_accepts = [cid for cid in gold_faulty if decision(cid) == "ACCEPT"]
    faulty_detection = {
        "gold_faulty_cases": len(gold_faulty),
        "evaluated_cases": len(evaluated),
        "error_rows": len(error_rows),
        "true_positives": len(true_positives),
        "false_positives": len(false_positives),
        "false_negatives": len(false_negatives),
        "precision": _rate(len(true_positives), len(true_positives) + len(false_positives)),
        "recall": _rate(len(true_positives), len(true_positives) + len(false_negatives)),
        "false_accept_rate": _rate(len(faulty_accepts), len(gold_faulty)),
        "fail_closed_recall": _rate(len(true_positives), len(gold_faulty)),
        "note": (
            "Precision/recall are over cases with an available decision (completed or "
            "invalid_input); execution/verifier errors are excluded from precision/recall and "
            "reported as error_rows. fail_closed_recall treats error rows as non-detections. "
            "Faulty means expected_decision is HOLD or INVALID_INPUT."
        ),
    }

    condition_rows = [rows_by_key[(cid, condition)] for cid in case_ids]
    timing = _timing_block(condition_rows)
    llm_usage = _llm_usage_block(condition_rows)
    accepted_valid = [
        cid for cid in valid_ids if is_completed(cid) and decision(cid) == "ACCEPT"
    ]
    decision_counts: dict[str, int] = {}
    for name in ("ACCEPT", "HOLD", "INVALID_INPUT", "ERROR"):
        decision_counts[name] = sum(
            1 for cid in case_ids
            if row_for(cid).get("status") in EVALUATED_STATUSES
            and row_for(cid).get("decision") == name
        )
    decision_counts["ERROR"] = sum(
        1 for cid in case_ids if row_for(cid).get("decision") == "ERROR"
    )
    return {
        "case_count": len(case_ids),
        "timing_ms": timing,
        "timing_breakdown": _timing_breakdown(timing),
        "llm_usage": llm_usage,
        "token_usage": dict(llm_usage),
        "estimated_cost": _estimated_cost(llm_usage),
        "as_completed": as_completed,
        "fail_closed_itt": fail_closed_itt,
        "faulty_detection": faulty_detection,
        "invalid_input": _invalid_input_metrics(case_ids, condition, rows_by_key, labels),
        "valid_update_acceptance": {
            "accepted": len(accepted_valid),
            "cases": len(valid_ids),
            "rate": _rate(len(accepted_valid), len(valid_ids)),
        },
        "decision_counts": decision_counts,
        "reason_code_counts": _reason_code_counts(condition_rows),
        "verifier_errors": sum(
            row_for(cid).get("status") == "verifier_error" for cid in case_ids
        ),
        "execution_errors": sum(
            row_for(cid).get("status") in ERROR_STATUSES for cid in case_ids
        ),
    }


def _unguarded_block(
    case_ids: list[str],
    rows_by_key: dict[tuple[str, str], dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = [rows_by_key[(cid, "unguarded")] for cid in case_ids]
    timing = _timing_block(rows)
    gold_faulty_non_invalid = [
        cid for cid in case_ids if labels[cid]["expected_decision"] == "HOLD"
    ]
    applied_faulty = [
        cid for cid in gold_faulty_non_invalid
        if rows_by_key[(cid, "unguarded")].get("decision") == "APPLIED"
    ]
    return {
        "case_count": len(case_ids),
        "timing_ms": timing,
        "timing_breakdown": _timing_breakdown(timing),
        "applied_count": sum(row["decision"] == "APPLIED" for row in rows),
        "raw_effect": {
            "gold_faulty_cases": len(gold_faulty_non_invalid),
            "faulty_updates_applied": len(applied_faulty),
            "false_accept_equivalent_rate": _rate(
                len(applied_faulty), len(gold_faulty_non_invalid)
            ),
            "note": (
                "Unguarded applies every constructible candidate without verification; it has "
                "no ACCEPT/HOLD decisions. The false-accept-equivalent rate is a raw-effect "
                "baseline over gold HOLD cases only (schema-invalid inputs cannot be applied)."
            ),
        },
        "decision_counts": {
            "APPLIED": sum(row["decision"] == "APPLIED" for row in rows),
            "INVALID_INPUT": sum(row.get("status") == "invalid_input" for row in rows),
        },
        "execution_errors": sum(row.get("status") in ERROR_STATUSES for row in rows),
    }


def _paired_differences(
    case_ids: list[str],
    rows_by_key: dict[tuple[str, str], dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    def decisions(condition: str) -> dict[str, str]:
        return {
            cid: rows_by_key[(cid, condition)].get("decision", "ERROR")
            for cid in case_ids
        }

    graph, text = decisions("graph"), decisions("text")
    differing = [cid for cid in case_ids if graph[cid] != text[cid]]
    correct = {
        cid: labels[cid]["expected_decision"] for cid in case_ids
    }
    return {
        "graph_vs_text": {
            "differing_case_count": len(differing),
            "differing_case_ids": differing,
            "differing_cases": {
                cid: {"graph": graph[cid], "text": text[cid], "expected": correct[cid]}
                for cid in differing
            },
            "note": (
                "Counts and lists only; with 36 single-reviewed synthetic cases no statistical "
                "superiority claim is made. Differences indicate verifier-construction differences "
                "on identical inputs."
            ),
        }
    }


def analyze_manifest_run(manifest_path: str | Path, summary_path: str | Path) -> dict[str, Any]:
    """Compute explicit-denominator metrics for a completed paired pilot run."""
    manifest_path, summary_path = Path(manifest_path), Path(summary_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases_path = (manifest_path.parent / manifest["cases_file"]).resolve()
    labels_path = (manifest_path.parent / manifest["labels_file"]).resolve()
    if hashlib.sha256(cases_path.read_bytes()).hexdigest() != manifest["cases_sha256"]:
        raise ValueError("case file hash does not match manifest")
    if hashlib.sha256(labels_path.read_bytes()).hexdigest() != manifest["labels_sha256"]:
        raise ValueError("label file hash does not match manifest")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if summary.get("manifest_id") != manifest["manifest_id"] or summary.get("manifest_sha256") != manifest_hash:
        raise ValueError("pilot summary does not match manifest identity/hash")

    cases = _read_jsonl(cases_path)
    labels = {row["case_id"]: row for row in _read_jsonl(labels_path)}
    case_by_id = {row["case_id"]: row for row in cases}
    if list(case_by_id) != manifest["case_ids"] or set(labels) != set(case_by_id):
        raise ValueError("manifest case IDs do not match cases and labels")

    expected_rows = {
        (case_id, condition)
        for case_id in manifest["case_ids"]
        for condition in manifest["conditions"]
    }
    results = summary.get("results", [])
    rows_by_key = {(row["case_id"], row["condition"]): row for row in results}
    if len(rows_by_key) != len(results) or set(rows_by_key) != expected_rows:
        raise ValueError("pilot summary has duplicate or missing case-condition results")

    split_by_id: dict[str, str | None] = {}
    entries = manifest.get("case_entries")
    if entries is not None:
        if [entry.get("case_id") for entry in entries] != manifest["case_ids"]:
            raise ValueError("manifest case_entries do not match case IDs")
        split_by_id = {entry["case_id"]: entry.get("split") for entry in entries}
    splits = [
        split for split in ("dev", "heldout")
        if any(value == split for value in split_by_id.values())
    ]

    required_claim_count = 0
    omitted_claim_count = 0
    material_candidate_count = 0
    unsupported_claim_count = 0
    valid_prior_record_count = 0
    corrupted_prior_record_count = 0
    case_error_count = 0
    for case_id in manifest["case_ids"]:
        case, label = case_by_id[case_id], labels[case_id]
        before_claims = {claim for record in case["before"] for claim in record["claims"]}
        candidate_claims = {claim for record in case["candidate"] for claim in record["claims"]}
        evidence_claims = {claim for evidence in case["evidence"] for claim in evidence["supports"]}
        required = set(case["required_claims"])
        omitted_claim_count += len(required - candidate_claims)
        required_claim_count += len(required)
        unsupported_claim_count += len(candidate_claims - before_claims - evidence_claims)
        material_candidate_count += len(candidate_claims)
        valid_prior_record_count += len(label["valid_prior_records_expected_to_persist"])
        corrupted_prior_record_count += len(label["corrupted_prior_record_ids"])
        if label.get("expected_failed_checks"):
            case_error_count += 1
        elif label["expected_decision"] == "HOLD":
            case_error_count += 1

    candidate_error_rates = {
        "omission": _rate(omitted_claim_count, required_claim_count),
        "unsupported_addition": _rate(unsupported_claim_count, material_candidate_count),
        "prior_record_corruption": _rate(corrupted_prior_record_count, valid_prior_record_count),
        "case_error": _rate(case_error_count, len(cases)),
    }

    category_counts: dict[str, int] = {}
    for label in labels.values():
        category = label.get("category", "unlabelled")
        category_counts[category] = category_counts.get(category, 0) + 1

    condition_metrics: dict[str, Any] = {}
    for condition in ("graph", "text"):
        block = _condition_block(manifest["case_ids"], condition, rows_by_key, labels)
        if splits:
            block["splits"] = {
                split: _condition_block(
                    [cid for cid in manifest["case_ids"] if split_by_id.get(cid) == split],
                    condition,
                    rows_by_key,
                    labels,
                )
                for split in splits
            }
        condition_metrics[condition] = block

    unguarded = _unguarded_block(manifest["case_ids"], rows_by_key, labels)
    if splits:
        unguarded["splits"] = {
            split: _unguarded_block(
                [cid for cid in manifest["case_ids"] if split_by_id.get(cid) == split],
                rows_by_key,
                labels,
            )
            for split in splits
        }
    condition_metrics["unguarded"] = unguarded

    invalid_case_ids = [
        case_id for case_id in manifest["case_ids"]
        if labels[case_id]["expected_decision"] == "INVALID_INPUT"
    ]
    split_counts = {
        split: sum(1 for value in split_by_id.values() if value == split)
        for split in splits
    } if splits else {}
    return {
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_hash,
        "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "schema_version": manifest.get("schema_version", 1),
        "run_id": summary.get("run_id"),
        "code_revision": summary.get("code_revision"),
        "verifier": summary.get("verifier"),
        "case_count": len(cases),
        "split_counts": split_counts,
        "reviewer_count": manifest.get("review", {}).get("reviewer_count"),
        "review": {
            key: manifest.get("review", {}).get(key)
            for key in (
                "reviewer_count", "second_independent_reviewer",
                "second_review_status", "limitation",
            )
        },
        "candidate_error_rates": candidate_error_rates,
        "case_category_counts": category_counts,
        "invalid_input_summary": {
            "case_count": len(invalid_case_ids),
            "case_ids": invalid_case_ids,
            "construction_failures_by_condition": {
                condition: sum(
                    rows_by_key[(cid, condition)].get("status") == "invalid_input"
                    for cid in invalid_case_ids
                )
                for condition in manifest["conditions"]
            },
            "note": (
                "Expected-invalid cases are rejected by schema construction for every condition "
                "before verification; they are not verifier detections and never write memory."
            ),
        },
        "condition_metrics": condition_metrics,
        "paired_differences": _paired_differences(manifest["case_ids"], rows_by_key, labels),
        "interpretation": (
            "Exploratory case-level rates on a small synthetic single-reviewer set; not population "
            "estimates. Labels were authored by one reviewer and second human review is pending. "
            "Condition rates are reported both as-completed (verifier errors excluded from "
            "numerator and denominator) and as fail-closed intention-to-treat (non-accepting error "
            "outcomes retained; explicit HOLD detection and error-hold counts reported separately). "
            "Precision/recall for faulty detection and the invalid-input section are additive "
            "views; INVALID_INPUT rows are construction failures, not verifier decisions. No "
            "statistical superiority claim is supported by this set."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a MemBud manifest pilot run")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", help="optional JSON report path")
    args = parser.parse_args()
    report = analyze_manifest_run(args.manifest, args.summary)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
