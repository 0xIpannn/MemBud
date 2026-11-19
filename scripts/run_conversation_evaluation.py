"""Run the frozen multi-turn conversation evaluation manifest (rules only).

Executes two complementary tracks over ``data/manifests/conversations-v1.json``
with ZERO paid API calls:

1. **Paired step-proposal evaluation** across ``unguarded``, ``graph``, ``text``,
   and ``text_fair`` using :func:`membud.conversation.run_conversation_session`,
   enforcing :func:`membud.conversation.verify_transcript_grounding` on all
   guarded conditions.
2. **End-to-end raw-transcript extraction replay** feeding only the raw
   multi-turn transcripts into :class:`membud.conversation.RuleBasedConversationExtractor`
   and verifying convergence to the expected final memory records under ``graph``
   + ``transcript_grounding``.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/run_conversation_evaluation.py \\
        --output data/results/conversations-v1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud.conversation import (  # noqa: E402
    RuleBasedConversationExtractor,
    parse_conversation,
    run_conversation_session,
)
from membud.experiment import source_fingerprint  # noqa: E402

PAIRED_CONDITIONS = ("unguarded", "graph", "text", "text_fair")


def _relative(path: str | Path) -> str:
    """Repository-relative display path; absolute only when outside the repo."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _cleanup_transient_locks(output: Path) -> int:
    """Remove empty per-store flock artifacts so results stay tidy."""
    removed = 0
    for lock in sorted(output.rglob("*.json.lock")):
        try:
            lock.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _records_signature(records: list[dict[str, Any]]) -> dict[str, frozenset[str]]:
    return {str(rec["id"]): frozenset(str(c) for c in rec["claims"]) for rec in records}


def load_conversation_manifest(
    manifest_path: str | Path,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load and SHA-256 verify the frozen conversation manifest, entries, and labels."""
    m_path = Path(manifest_path)
    manifest = json.loads(m_path.read_text(encoding="utf-8"))
    manifest_sha = hashlib.sha256(m_path.read_bytes()).hexdigest()

    conv_path = (m_path.parent / manifest["conversations_file"]).resolve()
    labels_path = (m_path.parent / manifest["labels_file"]).resolve()
    if hashlib.sha256(conv_path.read_bytes()).hexdigest() != manifest["conversations_sha256"]:
        raise ValueError("conversations file SHA-256 does not match manifest")
    if hashlib.sha256(labels_path.read_bytes()).hexdigest() != manifest["labels_sha256"]:
        raise ValueError("labels file SHA-256 does not match manifest")

    entries = [
        json.loads(line)
        for line in conv_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels_rows = [
        json.loads(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    conv_ids = [row["conversation_id"] for row in entries]
    if conv_ids != manifest["conversation_ids"] or len(entries) != manifest["conversation_count"]:
        raise ValueError("conversation IDs/count do not match manifest")
    if [row["conversation_id"] for row in labels_rows] != conv_ids:
        raise ValueError("label conversation IDs do not match manifest")

    total_steps = sum(len(row["steps"]) for row in entries)
    if total_steps != manifest["step_count"]:
        raise ValueError(f"step count {total_steps} != manifest {manifest['step_count']}")

    labels_by_id = {row["conversation_id"]: row for row in labels_rows}
    return manifest, manifest_sha, entries, labels_by_id


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": (numerator / denominator) if denominator else None,
    }


def run_conversation_evaluation(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    run_id: str = "conversations-v1-rules",
    code_revision: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run paired conversation sessions and raw-transcript extraction replay."""
    m_path = Path(manifest_path)
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        if not overwrite:
            raise ValueError(
                f"output directory {output} already exists and is not empty; "
                "use a fresh directory or pass --overwrite"
            )
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    manifest, manifest_sha, entries, labels_by_id = load_conversation_manifest(m_path)
    revision = code_revision or source_fingerprint()

    # Flatten step labels for lookup.
    step_labels: dict[str, dict[str, Any]] = {}
    for conv_id, label_row in labels_by_id.items():
        for sl in label_row["step_labels"]:
            step_labels[sl["step_id"]] = {**sl, "conversation_id": conv_id}

    session_summaries: list[dict[str, Any]] = []
    step_rows_by_condition: dict[str, list[dict[str, Any]]] = {c: [] for c in PAIRED_CONDITIONS}
    final_match_by_condition: dict[str, int] = {c: 0 for c in PAIRED_CONDITIONS}

    for condition in PAIRED_CONDITIONS:
        for entry in entries:
            conv_id = entry["conversation_id"]
            transcript = parse_conversation(entry["transcript"], conversation_id=conv_id, format="membud")
            session_dir = output / condition / conv_id
            session = run_conversation_session(
                transcript,
                session_dir,
                condition=condition,
                steps=entry["steps"],
                initial_records=entry.get("initial_records", ()),
                run_id=run_id,
                code_revision=revision,
                manifest_id=manifest["manifest_id"],
                manifest_sha256=manifest_sha,
            )
            expected_final = labels_by_id[conv_id]["expected_final_records"]
            final_matched = _records_signature(session["final_records"]) == _records_signature(expected_final)
            if final_matched:
                final_match_by_condition[condition] += 1
            session["final_records_match_expected"] = final_matched
            session["store_path"] = _relative(session["store_path"])
            session["audit_path"] = _relative(session["audit_path"])
            session_summaries.append(session)

            for step in session["steps"]:
                label = step_labels[step["step_id"]]
                failed_checks = {
                    chk["name"]: chk["reason_code"]
                    for chk in step["checks"]
                    if chk.get("status") != "PASS"
                }
                step_rows_by_condition[condition].append(
                    {
                        "conversation_id": conv_id,
                        "step_id": step["step_id"],
                        "condition": condition,
                        "decision": step["decision"],
                        "expected_decision": label["expected_decision"],
                        "category": label["category"],
                        "failed_checks": failed_checks,
                        "store_written": step["store_written"],
                        "latency_ms": step.get("latency_ms"),
                    }
                )

    # Track 2: End-to-end raw-transcript extraction replay (no pre-authored steps).
    extractor = RuleBasedConversationExtractor()
    extraction_sessions: list[dict[str, Any]] = []
    converged_count = 0
    total_extracted_steps = 0
    accepted_extracted_steps = 0
    held_extracted_steps = 0
    for entry in entries:
        conv_id = entry["conversation_id"]
        transcript = parse_conversation(entry["transcript"], conversation_id=conv_id, format="membud")
        extract_dir = output / "extracted_graph" / conv_id
        session = run_conversation_session(
            transcript,
            extract_dir,
            condition="graph",
            steps=None,
            extractor=extractor,
            initial_records=entry.get("initial_records", ()),
            run_id=f"{run_id}-extract",
            code_revision=revision,
            manifest_id=manifest["manifest_id"],
            manifest_sha256=manifest_sha,
        )
        expected_final = labels_by_id[conv_id]["expected_final_records"]
        converged = _records_signature(session["final_records"]) == _records_signature(expected_final)
        if converged:
            converged_count += 1
        total_extracted_steps += session["step_count"]
        accepted_extracted_steps += sum(s["decision"] == "ACCEPT" for s in session["steps"])
        held_extracted_steps += sum(s["decision"] == "HOLD" for s in session["steps"])
        extraction_sessions.append(
            {
                "conversation_id": conv_id,
                "step_count": session["step_count"],
                "decisions": [s["decision"] for s in session["steps"]],
                "final_records": session["final_records"],
                "converged_to_expected_final_records": converged,
                "store_path": _relative(session["store_path"]),
                "audit_path": _relative(session["audit_path"]),
            }
        )

    # Build condition metrics.
    condition_metrics: dict[str, Any] = {}
    for condition in PAIRED_CONDITIONS:
        rows = step_rows_by_condition[condition]
        decision_counts: dict[str, int] = {}
        for row in rows:
            decision_counts[row["decision"]] = decision_counts.get(row["decision"], 0) + 1
        if condition == "unguarded":
            condition_metrics[condition] = {
                "step_count": len(rows),
                "decision_counts": decision_counts,
                "safe_final_state_conversations": _ratio(
                    final_match_by_condition[condition], len(entries)
                ),
            }
            continue

        valid_rows = [r for r in rows if r["expected_decision"] == "ACCEPT"]
        hold_rows = [r for r in rows if r["expected_decision"] == "HOLD"]
        invalid_rows = [r for r in rows if r["expected_decision"] == "INVALID_INPUT"]

        valid_accepted = sum(r["decision"] == "ACCEPT" for r in valid_rows)
        false_holds = sum(r["decision"] == "HOLD" for r in valid_rows)
        faulty_held = sum(r["decision"] == "HOLD" for r in hold_rows)
        false_accepts = sum(r["decision"] == "ACCEPT" for r in hold_rows)
        invalid_caught = sum(r["decision"] == "INVALID_INPUT" for r in invalid_rows)
        agreement = sum(r["decision"] == r["expected_decision"] for r in rows)

        reason_counts: dict[str, int] = {}
        for row in rows:
            for code in row["failed_checks"].values():
                reason_counts[code] = reason_counts.get(code, 0) + 1

        condition_metrics[condition] = {
            "step_count": len(rows),
            "decision_counts": decision_counts,
            "valid_acceptance_rate": _ratio(valid_accepted, len(valid_rows)),
            "false_hold_rate": _ratio(false_holds, len(valid_rows)),
            "faulty_detection_rate": _ratio(faulty_held, len(hold_rows)),
            "false_accept_rate": _ratio(false_accepts, len(hold_rows)),
            "invalid_input_handled": _ratio(invalid_caught, len(invalid_rows)),
            "label_agreement": _ratio(agreement, len(rows)),
            "safe_final_state_conversations": _ratio(
                final_match_by_condition[condition], len(entries)
            ),
            "failed_check_reason_counts": dict(sorted(reason_counts.items())),
        }

    def _paired_diff(cond_a: str, cond_b: str) -> dict[str, Any]:
        by_a = {r["step_id"]: r["decision"] for r in step_rows_by_condition[cond_a]}
        by_b = {r["step_id"]: r["decision"] for r in step_rows_by_condition[cond_b]}
        discordant = [
            {
                "step_id": sid,
                cond_a: by_a[sid],
                cond_b: by_b[sid],
            }
            for sid in by_a
            if by_a[sid] != by_b[sid]
        ]
        return {
            "total_steps": len(by_a),
            "discordant_count": len(discordant),
            "discordant_steps": discordant,
        }

    paired_differences = {
        "graph_vs_text": _paired_diff("graph", "text"),
        "text_fair_vs_graph": _paired_diff("text_fair", "graph"),
        "text_vs_text_fair": _paired_diff("text", "text_fair"),
    }

    extraction_summary = {
        "extractor": RuleBasedConversationExtractor.name,
        "condition": "graph",
        "conversations_total": len(entries),
        "conversations_converged_to_expected_state": converged_count,
        "total_steps": total_extracted_steps,
        "accepted_steps": accepted_extracted_steps,
        "held_steps": held_extracted_steps,
        "per_conversation": extraction_sessions,
    }

    summary_payload = {
        "run_id": run_id,
        "code_revision": revision,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha,
        "verifier": "rules+transcript_grounding",
        "conditions": list(PAIRED_CONDITIONS),
        "sessions": session_summaries,
        "extraction_replay": extraction_summary,
    }
    metrics_payload = {
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha,
        "run_id": run_id,
        "code_revision": revision,
        "conversation_count": len(entries),
        "step_count": manifest["step_count"],
        "expected_step_decision_counts": manifest["expected_step_decision_counts"],
        "condition_metrics": condition_metrics,
        "paired_differences": paired_differences,
        "extraction_replay": {
            key: value
            for key, value in extraction_summary.items()
            if key != "per_conversation"
        },
        "extraction_replay_conversations": extraction_sessions,
    }
    run_config_payload = {
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha,
        "manifest_path": _relative(m_path),
        "run_id": run_id,
        "code_revision": revision,
        "verifier": "rules+transcript_grounding",
        "conditions": list(PAIRED_CONDITIONS),
        "paid_api_calls": 0,
        "note": (
            "Rules-only multi-turn conversation evaluation with transcript-grounding checks, "
            "plus end-to-end raw-transcript extraction replay via RuleBasedConversationExtractor."
        ),
    }

    (output / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "run-config.json").write_text(
        json.dumps(run_config_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _cleanup_transient_locks(output)
    return metrics_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data/manifests/conversations-v1.json"),
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "data/results/conversations-v1"),
    )
    parser.add_argument("--run-id", default="conversations-v1-rules")
    parser.add_argument("--code-revision", default="")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    metrics = run_conversation_evaluation(
        args.manifest,
        args.output,
        run_id=args.run_id,
        code_revision=args.code_revision,
        overwrite=args.overwrite,
    )
    for condition in PAIRED_CONDITIONS:
        block = metrics["condition_metrics"][condition]
        print(
            f"{condition:10s} counts={block['decision_counts']} "
            f"safe_final={block['safe_final_state_conversations']['numerator']}/"
            f"{block['safe_final_state_conversations']['denominator']}"
        )
    replay = metrics["extraction_replay"]
    print(
        f"extraction_replay: converged={replay['conversations_converged_to_expected_state']}/"
        f"{replay['conversations_total']} steps={replay['total_steps']} "
        f"accepted={replay['accepted_steps']} held={replay['held_steps']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
