"""Rerun only the failed rows of a pilot summary, cache-first and budget-capped.

The script reuses the persistent DeepSeek response cache for any completion that
already succeeded, so only previously failed case/condition rows make new API
calls. It aborts before the next call if the accumulated reported token usage
exceeds --max-total-tokens, and writes both a rerun summary and a merged summary
(original rows with now-completed reruns substituted).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from membud.experiment import _last_event, load_manifest_cases
from membud.llm import OpenAICompatibleChatClient
from membud.store import run_case
from membud.verification import verify_with_llm

ESTIMATED_TOKENS_PER_ATTEMPT = 2500  # conservative fallback when usage is absent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--summary", required=True, help="original pilot summary")
    parser.add_argument("--output", required=True, help="fresh output directory")
    parser.add_argument("--code-revision", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--max-total-tokens", type=int, default=200_000)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    transitions = {t.case_id: t for t in load_manifest_cases(manifest_path)}
    source_summary_path = Path(args.summary)
    original = json.loads(source_summary_path.read_text(encoding="utf-8"))
    failed = [
        (row["case_id"], row["condition"])
        for row in original["results"]
        if row.get("status") != "completed"
    ]
    if not failed:
        print("No failed rows; nothing to do.")
        return

    output = Path(args.output)
    if output.exists() and any(output.rglob("*")):
        raise SystemExit(f"{output} already exists and is not empty; use a fresh output directory")

    client = OpenAICompatibleChatClient.from_deepseek_env()
    client.max_attempts = args.max_attempts
    run_id = args.run_id or f"deepseek-rerun-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    print(f"rerunning {len(failed)} failed rows; token cap {args.max_total_tokens}; model {client.model}")

    rows: list[dict] = []
    total_tokens = 0
    cache_hits = 0
    aborted = False
    for case_id, condition in failed:
        if condition == "unguarded":
            continue
        transition = transitions[case_id]
        base = output / condition
        used = 0
        try:
            result = run_case(
                transition,
                base / f"{case_id}.json",
                base / "audit.jsonl",
                representation=condition,
                run_id=run_id,
                code_revision=args.code_revision,
                manifest_id=manifest["manifest_id"],
                manifest_sha256=manifest_sha,
                verifier=lambda t, rep, prepared: verify_with_llm(
                    t, client, rep, prepared_input=prepared
                ),
            )
            event = _last_event(base / "audit.jsonl")
            usage = result.usage or {}
            used = int(usage.get("total_tokens") or 0)
            if not used:
                used = ESTIMATED_TOKENS_PER_ATTEMPT * int(usage.get("api_attempts") or 0)
            total_tokens += used
            cache_hits += int(usage.get("cache_hit") or 0)
            row = {
                "case_id": case_id,
                "condition": condition,
                "decision": result.decision,
                "status": event["status"],
                "latency_ms": event["latency_ms"],
                "preparation_ms": event["preparation_ms"],
                "verification_ms": event["verification_ms"],
                "graph_node_count": event["graph_node_count"],
                "graph_edge_count": event["graph_edge_count"],
                "result": event["result"],
            }
        except Exception as exc:  # noqa: BLE001 - surfaced in the report
            row = {
                "case_id": case_id,
                "condition": condition,
                "decision": "ERROR",
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        rows.append(row)
        print(
            f"{case_id} {condition:5s} {row['decision']:6s} {row['status']:14s} "
            f"tokens={used} total={total_tokens} cache_hits={cache_hits}"
        )
        if total_tokens > args.max_total_tokens:
            aborted = True
            print(
                f"ABORT: token budget exceeded ({total_tokens} > {args.max_total_tokens})",
                file=sys.stderr,
            )
            break

    summary = {
        "run_id": run_id,
        "code_revision": args.code_revision,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha,
        "verifier": "llm:deepseek",
        "source_summary_sha256": hashlib.sha256(source_summary_path.read_bytes()).hexdigest(),
        "source_summary": str(source_summary_path),
        "max_total_tokens": args.max_total_tokens,
        "reported_tokens_used": total_tokens,
        "cache_hits": cache_hits,
        "aborted_on_budget": aborted,
        "results": rows,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "rerun-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    completed = {(r["case_id"], r["condition"]): r for r in rows if r["status"] == "completed"}
    merged = json.loads(source_summary_path.read_text(encoding="utf-8"))
    merged["results"] = [
        completed.get((row["case_id"], row["condition"]), row) for row in merged["results"]
    ]
    merged["merge_note"] = (
        f"Failed rows rerun as {run_id}; completed rerun rows replace the original "
        "failed rows. Original rows are preserved in the source summary."
    )
    (output / "merged-pilot-summary.json").write_text(
        json.dumps(merged, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"\nrows={len(rows)} reported_tokens={total_tokens} cache_hits={cache_hits} "
        f"aborted={aborted}\nwrote {output/'rerun-summary.json'} and {output/'merged-pilot-summary.json'}"
    )


if __name__ == "__main__":
    main()
