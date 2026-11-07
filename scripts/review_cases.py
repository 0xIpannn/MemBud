"""Generate a per-case human review sheet for a frozen transition manifest.

The generated sheet contains the verifier-facing case inputs plus the current
single-reviewer proposed labels, with BLANK second-reviewer and adjudication
columns. It intentionally does not fabricate independent annotations: filling the
second-reviewer columns requires a real human review (see
``data/review/README.md`` and ``data/review/adjudication-template.md``).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/review_cases.py \
        --manifest data/manifests/evaluation-v2.json \
        --output data/review/review-sheet-v2.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from membud.experiment import load_manifest_labels  # noqa: E402

STATUS_BANNER = """\
> **Review status: single reviewer only — second independent human review PENDING.**
>
> The "proposed label" columns below are the existing single-reviewer project
> annotations. All second-reviewer and adjudication fields are intentionally
> blank. Do not fill them without a real independent reviewer; do not report
> agreement or double-annotation statistics until the process in
> `data/review/README.md` has actually been executed.
"""


def _records(records: list[dict]) -> str:
    return "; ".join(
        f"`{record['id']}`: " + ", ".join(f"\u201c{claim}\u201d" for claim in record["claims"])
        for record in records
    ) or "(none)"


def _evidence(evidence: list[dict]) -> str:
    if not evidence:
        return "(none)"
    lines = []
    for item in evidence:
        supports = ", ".join(f"\u201c{claim}\u201d" for claim in item.get("supports", [])) or "(none)"
        contradicts = ", ".join(
            f"\u201c{claim}\u201d" for claim in item.get("contradicts", [])
        )
        detail = f"supports {supports}"
        if contradicts:
            detail += f"; contradicts {contradicts}"
        lines.append(
            f"- `{item['id']}` (source `{item.get('source', '')}`): "
            f"\u201c{item.get('text', '')}\u201d — {detail}"
        )
    return "\n".join(lines)


def _operations(operations: list[dict]) -> str:
    if not operations:
        return "(none declared)"
    lines = []
    for operation in operations:
        parts = [operation["type"]]
        if operation.get("record_id"):
            parts.append(f"record_id={operation['record_id']}")
        if operation.get("target_id"):
            parts.append(f"target_id={operation['target_id']}")
        if operation.get("reason"):
            parts.append(f"reason=\u201c{operation['reason']}\u201d")
        authorization = operation.get("authorization")
        if authorization:
            parts.append(
                "authorization={source=%s, evidence_id=%s, target_id=%s, rationale=\u201c%s\u201d}"
                % (
                    authorization.get("source"),
                    authorization.get("evidence_id"),
                    authorization.get("target_id"),
                    authorization.get("rationale"),
                )
            )
        lines.append("- " + "; ".join(parts))
    return "\n".join(lines)


def render_sheet(manifest_path: Path) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases_path = (manifest_path.parent / manifest["cases_file"]).resolve()
    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = load_manifest_labels(manifest_path)
    case_entries = {
        entry["case_id"]: entry for entry in manifest.get("case_entries", [])
    }

    lines = [
        f"# Human review sheet — `{manifest['manifest_id']}`",
        "",
        STATUS_BANNER,
        "",
        f"- Manifest: `{manifest_path}`",
        f"- Cases: {manifest['case_count']}",
        f"- Conditions under evaluation: {', '.join(manifest['conditions'])}",
        "- Instructions: verify each case's inputs independently of the proposed label; write your "
        "own decision (`ACCEPT`, `HOLD`, or `INVALID_INPUT`) and notes; do not edit the case files.",
        "- Disagreements are resolved by a third adjudicator using "
        "`data/review/adjudication-template.md`.",
        "",
        "## Agreement summary (to be completed only after real second review)",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Second reviewer ID | _(blank — pending)_ |",
        f"| Cases reviewed / total | _0 / {manifest['case_count']} (pending)_ |",
        "| Raw agreement | _not computed — no second-review data_ |",
        "| Cohen's kappa | _not computed — no second-review data_ |",
        "| Adjudicated disagreements | _none recorded_ |",
        "",
        "---",
        "",
    ]
    for case in cases:
        case_id = case["case_id"]
        label = labels[case_id]
        entry = case_entries.get(case_id, {})
        split = entry.get("split") or label.get("split") or "(unspecified)"
        lines.extend([
            f"## {case_id} — {label.get('category', 'uncategorised')} — split: {split}",
            "",
            f"**Proposed decision (single reviewer):** `{label['expected_decision']}`",
            f"**Proposed fault types:** {', '.join(label.get('fault_types', [])) or '(none)'}",
            f"**Valid prior records expected to persist:** "
            f"{', '.join(label.get('valid_prior_records_expected_to_persist', [])) or '(none)'}",
            f"**Corrupted prior record IDs:** "
            f"{', '.join(label.get('corrupted_prior_record_ids', [])) or '(none)'}",
            f"**Reviewer note:** {label.get('notes', '') or '(none)'}",
            "",
            f"**Prior state:** {_records(case['before'])}",
            "",
            "**Evidence:**",
            "",
            _evidence(case.get("evidence", [])),
            "",
            f"**Candidate state:** {_records(case['candidate'])}",
            "",
            f"**Required claims (verifier input):** "
            f"{', '.join(f'\u201c{claim}\u201d' for claim in case.get('required_claims', [])) or '(none)'}",
            "",
            "**Declared operations:**",
            "",
            _operations(case.get("operations", [])),
            "",
            "**Second-reviewer fields (blank by design):**",
            "",
            "| Field | Value |",
            "|---|---|",
            "| Second reviewer decision | |",
            "| Second reviewer notes | |",
            "| Agrees with proposed label? | |",
            "| Adjudicated decision (if disagreement) | |",
            "| Adjudicator / date | |",
            "",
            "---",
            "",
        ])
    lines.extend([
        "## Fabrication warning",
        "",
        "An empty second-reviewer column is an explicit record that independent review has not "
        "happened. Any downstream analysis must treat these labels as single-reviewer exploratory "
        "annotations until a real reviewer completes the sheet and adjudication is recorded.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(ROOT / "data/manifests/evaluation-v2.json"))
    parser.add_argument("--output", default=str(ROOT / "data/review/review-sheet-v2.md"))
    args = parser.parse_args()
    manifest_path = Path(args.manifest)
    sheet = render_sheet(manifest_path)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(sheet, encoding="utf-8")
    case_count = json.loads(manifest_path.read_text(encoding="utf-8"))["case_count"]
    print(f"wrote review sheet for {case_count} cases to {output}")


if __name__ == "__main__":
    main()
