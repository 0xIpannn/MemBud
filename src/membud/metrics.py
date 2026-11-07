"""Metrics for a frozen synthetic transition manifest and pilot summary."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any


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
        if label["expected_failed_checks"]:
            case_error_count += 1

    candidate_error_rates = {
        "omission": _rate(omitted_claim_count, required_claim_count),
        "unsupported_addition": _rate(unsupported_claim_count, material_candidate_count),
        "prior_record_corruption": _rate(corrupted_prior_record_count, valid_prior_record_count),
        "case_error": _rate(case_error_count, len(cases)),
    }

    condition_metrics: dict[str, Any] = {}
    for condition in ("graph", "text"):
        faulty_ids = [cid for cid in manifest["case_ids"] if labels[cid]["expected_decision"] == "HOLD"]
        valid_ids = [cid for cid in manifest["case_ids"] if labels[cid]["expected_decision"] == "ACCEPT"]

        def row_for(case_id: str) -> dict[str, Any]:
            return rows_by_key[(case_id, condition)]

        def is_completed(case_id: str) -> bool:
            return row_for(case_id).get("status") == "completed"

        def decision(case_id: str) -> str:
            return row_for(case_id).get("decision", "ERROR")

        # As-completed view: verifier errors leave BOTH numerator and denominator.
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

        # Fail-closed intention-to-treat: a verifier/execution error produced no
        # commit, so it counts as a non-accepting outcome. Detection still counts
        # only explicit HOLD decisions; error-hold counts are reported separately.
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

        condition_rows = [rows_by_key[(cid, condition)] for cid in manifest["case_ids"]]
        timing = {}
        for field in (
            "latency_ms", "preparation_ms", "verification_ms",
            "graph_node_count", "graph_edge_count",
        ):
            values = [row[field] for row in condition_rows if row.get(field) is not None]
            timing[field] = {
                "mean": statistics.fmean(values) if values else None,
                "case_count": len(values),
            }
        llm_rows = [
            row["result"]
            for row in condition_rows
            if isinstance(row.get("result"), dict) and row["result"].get("verifier") != "rules"
        ]
        llm_usage = {
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
        condition_metrics[condition] = {
            "timing_ms": timing,
            "llm_usage": llm_usage,
            "as_completed": as_completed,
            "fail_closed_itt": fail_closed_itt,
            "decision_counts": {
                decision: sum(
                    row.get("status") == "completed" and row["decision"] == decision
                    for (cid, cond), row in rows_by_key.items() if cond == condition
                )
                for decision in ("ACCEPT", "HOLD", "INVALID_INPUT", "ERROR")
            },
            "verifier_errors": sum(
                row.get("status") == "verifier_error"
                for (cid, cond), row in rows_by_key.items()
                if cond == condition
            ),
            "execution_errors": sum(
                row.get("status") in {"error", "verifier_error"}
                for (cid, cond), row in rows_by_key.items()
                if cond == condition
            ),
        }

    unguarded_rows = [rows_by_key[(cid, "unguarded")] for cid in manifest["case_ids"]]
    unguarded_timing = {}
    for field in (
        "latency_ms", "preparation_ms", "verification_ms",
        "graph_node_count", "graph_edge_count",
    ):
        values = [row[field] for row in unguarded_rows if row.get(field) is not None]
        unguarded_timing[field] = {
            "mean": statistics.fmean(values) if values else None,
            "case_count": len(values),
        }
    condition_metrics["unguarded"] = {
        "timing_ms": unguarded_timing,
        "applied_count": sum(row["decision"] == "APPLIED" for row in unguarded_rows),
        "execution_errors": sum(row.get("status") == "error" for row in unguarded_rows),
    }

    return {
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_hash,
        "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "run_id": summary.get("run_id"),
        "code_revision": summary.get("code_revision"),
        "verifier": summary.get("verifier"),
        "case_count": len(cases),
        "reviewer_count": manifest.get("review", {}).get("reviewer_count"),
        "candidate_error_rates": candidate_error_rates,
        "condition_metrics": condition_metrics,
        "interpretation": (
            "Exploratory case-level rates on a small synthetic single-reviewer set; not population "
            "estimates. Condition rates are reported both as-completed (verifier errors excluded from "
            "numerator and denominator) and as fail-closed intention-to-treat (non-accepting error "
            "outcomes retained; explicit HOLD detection and error-hold counts reported separately)."
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
