"""Run the frozen v2 evaluation manifest under unguarded/graph/text (rules only).

This is the reproducible command behind ``data/results/evaluation-v2/``. It makes
NO paid API/LLM calls: the default rules verifier is used for both guarded
conditions, with graph verifying structurally and text using the claim-set
baseline. Schema-invalid (``expected_invalid``) cases are recorded as
``INVALID_INPUT`` / ``invalid_input`` without aborting the run or writing memory.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/run_evaluation_v2.py \
        --output data/results/evaluation-v2

The output directory must be fresh; the runner refuses to mix runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from membud.experiment import (  # noqa: E402
    CONDITIONS,
    load_manifest_entries,
    load_manifest_labels,
    run_pilot,
    source_fingerprint,
)
from membud.metrics import analyze_manifest_run  # noqa: E402


def _cleanup_transient_locks(output: Path) -> int:
    """Remove empty per-store flock artifacts so the results artifact stays tidy."""
    removed = 0
    for lock in sorted(output.glob("*/*.json.lock")):
        try:
            lock.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _decision_table(report: dict) -> str:
    lines = []
    for condition in ("graph", "text", "unguarded"):
        metrics = report["condition_metrics"][condition]
        counts = metrics.get("decision_counts", {})
        if condition == "unguarded":
            counts = {"APPLIED": counts.get("APPLIED", 0),
                      "INVALID_INPUT": counts.get("INVALID_INPUT", 0)}
        lines.append(
            f"{condition:10s} " + " ".join(f"{name}={value}" for name, value in counts.items())
        )
        for split in ("dev", "heldout"):
            split_metrics = metrics.get("splits", {}).get(split)
            if split_metrics is None:
                continue
            split_counts = split_metrics.get("decision_counts", {})
            if condition == "unguarded":
                split_counts = {
                    "APPLIED": split_counts.get("APPLIED", 0),
                    "INVALID_INPUT": split_counts.get("INVALID_INPUT", 0),
                }
            lines.append(
                f"  {split:8s} " + " ".join(
                    f"{name}={value}" for name, value in split_counts.items()
                )
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(ROOT / "data/manifests/evaluation-v2.json"))
    parser.add_argument("--output", default=str(ROOT / "data/results/evaluation-v2"))
    parser.add_argument("--run-id", default="evaluation-v2-rules")
    parser.add_argument("--code-revision", default="")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    entries = load_manifest_entries(manifest_path)
    labels = load_manifest_labels(manifest_path)
    expected_decisions = {
        case_id: label["expected_decision"] for case_id, label in labels.items()
    }
    code_revision = args.code_revision or source_fingerprint()
    output = Path(args.output)

    print(
        f"manifest={manifest['manifest_id']} sha256={manifest_sha[:12]} "
        f"cases={len(entries)} invalid_cases={sum(entry.invalid for entry in entries)} "
        f"conditions={list(CONDITIONS)}"
    )
    print(f"code_revision={code_revision}")
    results = run_pilot(
        entries,
        output,
        run_id=args.run_id,
        code_revision=code_revision,
        manifest_id=manifest["manifest_id"],
        manifest_sha256=manifest_sha,
        expected_decisions=expected_decisions,
        verifier=None,  # rules verifier: graph -> structural, text -> claim-set baseline
        verifier_name="rules",
    )
    print(
        f"ran {len(results)} rows; "
        f"invalid_input_rows={sum(row.get('status') == 'invalid_input' for row in results)}; "
        f"execution_errors={sum(row.get('status') in {'error', 'verifier_error'} for row in results)}"
    )
    report = analyze_manifest_run(manifest_path, output / "pilot-summary.json")
    (output / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "run-config.json").write_text(
        json.dumps({
            "manifest_id": manifest["manifest_id"],
            "manifest_sha256": manifest_sha,
            "manifest_path": str(manifest_path),
            "run_id": args.run_id,
            "code_revision": code_revision,
            "verifier": "rules",
            "conditions": list(CONDITIONS),
            "paid_api_calls": 0,
            "note": (
                "Rules-only evaluation run. Graph condition uses structural graph verification; "
                "text condition uses the claim-set baseline. Invalid inputs fail schema "
                "construction before verification."
            ),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    print("\ndecision counts by condition and split:")
    print(_decision_table(report))
    print(f"\nwrote {output / 'pilot-summary.json'}")
    print(f"wrote {output / 'metrics.json'}")
    removed_locks = _cleanup_transient_locks(output)
    if removed_locks:
        print(f"removed {removed_locks} transient store-lock artifacts")


if __name__ == "__main__":
    main()
