"""Small local runner for paired unguarded, graph, and text pilot conditions."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from .schemas import Transition
from .serialization import transition_dict
from .store import run_case

CONDITIONS = ("unguarded", "graph", "text")


def source_fingerprint() -> str:
    """Hash executable package/config files for a reproducible dirty-tree label."""
    root = Path(__file__).resolve().parents[2]
    paths = sorted((root / "src/membud").rglob("*.py")) + [root / "pyproject.toml"]
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative + b"\0" + path.read_bytes() + b"\0")
    return "working-tree-sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class ManifestCase:
    """One frozen case: a validated transition, or a recorded invalid input.

    ``transition is None`` means ``Transition.from_dict`` rejected the raw case.
    Invalid inputs are expected for v2 manifests that declare
    ``expected_invalid`` cases; they are recorded as ``INVALID_INPUT`` rows and
    never verified or written to a memory store.
    """

    case_id: str
    raw: dict[str, Any] = field(default_factory=dict)
    split: str | None = None
    expected_invalid: bool | None = None
    transition: Transition | None = None
    invalid_reason: str = ""

    @property
    def invalid(self) -> bool:
        return self.transition is None


def _read_manifest(manifest_path: str | Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = Path(manifest_path)
    return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))


def _manifest_cases_path(manifest_path: Path, manifest: dict[str, Any]) -> Path:
    return (manifest_path.parent / manifest["cases_file"]).resolve()


def load_manifest_entries(manifest_path: str | Path) -> list[ManifestCase]:
    """Load raw v1/v2 manifest cases, recording invalid ones instead of aborting.

    The raw case file is SHA-256 checked against the manifest. A case that fails
    ``Transition.from_dict`` becomes an invalid entry; the manifest must mark
    exactly those cases ``expected_invalid`` when it carries ``case_entries``
    split metadata. A mismatch between the manifest flag and actual schema
    validation is a data-integrity error, not a scored outcome.
    """
    manifest_path, manifest = _read_manifest(manifest_path)
    cases_path = _manifest_cases_path(manifest_path, manifest)
    actual_hash = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    if actual_hash != manifest["cases_sha256"]:
        raise ValueError("case file hash does not match manifest")
    raw_cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = [str(raw.get("case_id", "")) for raw in raw_cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case IDs must be unique within the case file")
    if case_ids != manifest["case_ids"] or len(case_ids) != manifest["case_count"]:
        raise ValueError("case IDs/count do not match manifest")

    entries_meta = manifest.get("case_entries")
    meta_by_id: dict[str, dict[str, Any]] = {}
    if entries_meta is not None:
        if [entry.get("case_id") for entry in entries_meta] != case_ids:
            raise ValueError("manifest case_entries do not match case IDs")
        meta_by_id = {entry["case_id"]: entry for entry in entries_meta}
        seen_splits = {entry.get("split") for entry in entries_meta}
        if not seen_splits <= {"dev", "heldout", None}:
            raise ValueError("manifest case_entries have unsupported split values")

    entries: list[ManifestCase] = []
    for raw in raw_cases:
        case_id = str(raw["case_id"])
        meta = meta_by_id.get(case_id, {})
        expected_invalid = meta.get("expected_invalid")
        split = meta.get("split")
        transition: Transition | None = None
        invalid_reason = ""
        try:
            transition = Transition.from_dict(raw)
        except ValueError as exc:
            invalid_reason = f"{type(exc).__name__}: {exc}"
        if transition is None and expected_invalid is False:
            raise ValueError(
                f"case {case_id} failed schema validation but the manifest marks "
                f"expected_invalid=false: {invalid_reason}"
            )
        if transition is not None and expected_invalid is True:
            raise ValueError(
                f"case {case_id} validates as a transition but the manifest marks "
                "expected_invalid=true; update the v2 case set or the schema contract"
            )
        entries.append(
            ManifestCase(
                case_id=case_id,
                raw=raw,
                split=split,
                expected_invalid=expected_invalid,
                transition=transition,
                invalid_reason=invalid_reason,
            )
        )
    return entries


def load_manifest_cases(manifest_path: str | Path) -> list[Transition]:
    """Load only transition inputs from a hash-pinned manifest, never its labels.

    Strict v1-compatible loader: a case that fails validation raises instead of
    being recorded as invalid input. Use :func:`load_manifest_entries` for v2
    manifests that intentionally include ``expected_invalid`` cases.
    """
    entries = load_manifest_entries(manifest_path)
    invalid = [entry for entry in entries if entry.invalid]
    if invalid:
        details = "; ".join(f"{entry.case_id}: {entry.invalid_reason}" for entry in invalid)
        raise ValueError("manifest contains invalid transition inputs: " + details)
    return [entry.transition for entry in entries if entry.transition is not None]


def load_manifest_labels(
    manifest_path: str | Path, *, verify_hash: bool = True
) -> dict[str, dict[str, Any]]:
    """Load the gold-label file for reporting only; never passed to a verifier."""
    manifest_path, manifest = _read_manifest(manifest_path)
    labels_path = (manifest_path.parent / manifest["labels_file"]).resolve()
    if verify_hash:
        if hashlib.sha256(labels_path.read_bytes()).hexdigest() != manifest["labels_sha256"]:
            raise ValueError("label file hash does not match manifest")
    rows = [
        json.loads(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = {str(row["case_id"]): row for row in rows}
    if list(labels) != manifest["case_ids"]:
        raise ValueError("manifest case IDs do not match label rows")
    return labels


def _require_fresh_output(output_dir: Path, entries: list[ManifestCase]) -> None:
    """Refuse to mix a new run into stores/logs left by an earlier run."""
    if (output_dir / "pilot-summary.json").exists():
        raise ValueError(
            "output directory already contains a pilot summary; use a fresh --output directory"
        )
    for condition in CONDITIONS:
        audit_path = output_dir / condition / "audit.jsonl"
        if audit_path.exists() and audit_path.stat().st_size > 0:
            raise ValueError(
                f"output directory already contains a {condition} audit log; "
                "use a fresh --output directory"
            )
    for entry in entries:
        filename = quote(entry.case_id, safe="") + ".json"
        for condition in CONDITIONS:
            if (output_dir / condition / filename).exists():
                raise ValueError(
                    f"output directory already contains a store for {entry.case_id}/{condition}; "
                    "use a fresh --output directory"
                )


def _as_entries(items: Iterable[Transition | ManifestCase]) -> list[ManifestCase]:
    entries: list[ManifestCase] = []
    for item in items:
        if isinstance(item, ManifestCase):
            entries.append(item)
        elif isinstance(item, Transition):
            entries.append(ManifestCase(case_id=item.case_id, raw={}, transition=item))
        else:
            raise TypeError("run_pilot accepts Transition or ManifestCase entries")
    return entries


def run_pilot(
    transitions: Iterable[Transition | ManifestCase],
    output_dir: str | Path,
    *,
    run_id: str | None = None,
    code_revision: str = "",
    manifest_id: str = "",
    manifest_sha256: str = "",
    verifier: Callable[[Transition, str, Any], Any] | None = None,
    verifier_name: str = "rules",
    expected_decisions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Run the same fixed entries under three isolated pilot conditions.

    ``ManifestCase`` entries whose construction failed validation are recorded
    with decision ``INVALID_INPUT`` and status ``invalid_input`` for every
    condition, without aborting the run and without any store write. When
    ``expected_decisions`` is supplied (reporting metadata only, never verifier
    input) rows carry ``label_expected_decision``.
    """
    entries = _as_entries(transitions)
    for entry in entries:
        if entry.transition is not None:
            entry.transition.validate()
    case_ids = [entry.case_id for entry in entries]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case IDs must be unique within a pilot run")

    output_dir = Path(output_dir)
    _require_fresh_output(output_dir, entries)
    run_id = run_id or str(uuid.uuid4())
    results: list[dict[str, Any]] = []
    for entry in entries:
        filename = quote(entry.case_id, safe="") + ".json"
        for condition in CONDITIONS:
            condition_dir = output_dir / condition
            condition_dir.mkdir(parents=True, exist_ok=True)
            audit_path = condition_dir / "audit.jsonl"
            if entry.invalid:
                result = _record_invalid_input(
                    entry,
                    condition,
                    audit_path,
                    run_id,
                    code_revision,
                    manifest_id,
                    manifest_sha256,
                )
            elif condition == "unguarded":
                result = _apply_unguarded(
                    entry.transition,
                    condition_dir / filename,
                    audit_path,
                    run_id,
                    code_revision,
                    manifest_id,
                    manifest_sha256,
                )
            else:
                result = _run_guarded(
                    entry.transition,
                    condition,
                    condition_dir / filename,
                    audit_path,
                    run_id,
                    code_revision,
                    manifest_id,
                    manifest_sha256,
                    verifier,
                )
            result["split"] = entry.split
            result["invalid_input"] = entry.invalid
            if expected_decisions is not None:
                result["label_expected_decision"] = expected_decisions.get(entry.case_id)
            results.append(result)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pilot-summary.json").write_text(
        json.dumps({
            "run_id": run_id,
            "code_revision": code_revision,
            "manifest_id": manifest_id,
            "manifest_sha256": manifest_sha256,
            "verifier": verifier_name,
            "case_count": len(entries),
            "invalid_input_case_count": sum(entry.invalid for entry in entries),
            "conditions": list(CONDITIONS),
            "results": results,
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return results


def _run_guarded(
    transition: Transition,
    condition: str,
    store_path: Path,
    log_path: Path,
    run_id: str,
    code_revision: str,
    manifest_id: str,
    manifest_sha256: str,
    verifier: Callable[[Transition, str, Any], Any] | None,
) -> dict[str, Any]:
    try:
        verification = run_case(
            transition,
            store_path,
            log_path,
            representation=condition,
            run_id=run_id,
            code_revision=code_revision,
            manifest_id=manifest_id,
            manifest_sha256=manifest_sha256,
            verifier=verifier,
        )
        result = {
            "case_id": transition.case_id,
            "condition": condition,
            "decision": verification.decision,
            "status": "completed",
        }
        event = _last_event(log_path)
        result["status"] = event["status"]
        result.update({
            key: event[key]
            for key in (
                "latency_ms", "preparation_ms", "verification_ms",
                "graph_node_count", "graph_edge_count", "result",
            )
        })
        return result
    except Exception as exc:
        result = {
            "case_id": transition.case_id,
            "condition": condition,
            "decision": "ERROR",
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
        if log_path.exists():
            event = _last_event(log_path)
            result.update({
                key: event.get(key)
                for key in (
                    "latency_ms", "preparation_ms", "verification_ms",
                    "graph_node_count", "graph_edge_count", "result",
                )
            })
        return result


def _record_invalid_input(
    entry: ManifestCase,
    condition: str,
    log_path: Path,
    run_id: str,
    code_revision: str,
    manifest_id: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    """Record a schema-invalid transition without verifying or writing memory."""
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    event: dict[str, Any] = {
        "run_id": run_id,
        "code_revision": code_revision,
        "manifest_id": manifest_id,
        "manifest_sha256": manifest_sha256,
        "started_at": started_at,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "preparation_ms": None,
        "verification_ms": None,
        "graph_node_count": None,
        "graph_edge_count": None,
        "status": "invalid_input",
        "condition": condition,
        "decision": "INVALID_INPUT",
        "invalid_input": True,
        "split": entry.split,
        "input": entry.raw,
        "error_type": "ValueError",
        "error": entry.invalid_reason,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "case_id": entry.case_id,
        "condition": condition,
        "decision": "INVALID_INPUT",
        "status": "invalid_input",
        "latency_ms": event["latency_ms"],
        "preparation_ms": None,
        "verification_ms": None,
        "graph_node_count": None,
        "graph_edge_count": None,
        "error": entry.invalid_reason,
    }


def _apply_unguarded(
    transition: Transition,
    store_path: Path,
    log_path: Path,
    run_id: str,
    code_revision: str,
    manifest_id: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    before = [asdict(record) for record in transition.before]
    after = [asdict(record) for record in transition.candidate]
    temporary = store_path.with_suffix(store_path.suffix + ".tmp")
    temporary.write_text(json.dumps(after, indent=2) + "\n", encoding="utf-8")
    temporary.replace(store_path)
    event = {
        "run_id": run_id,
        "code_revision": code_revision,
        "manifest_id": manifest_id,
        "manifest_sha256": manifest_sha256,
        "started_at": started_at,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "preparation_ms": None,
        "verification_ms": None,
        "graph_node_count": None,
        "graph_edge_count": None,
        "status": "completed",
        "condition": "unguarded",
        "decision": "APPLIED",
        "input": transition_dict(transition),
        "store_before": before,
        "store_after": after,
    }
    with log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "case_id": transition.case_id,
        "condition": "unguarded",
        "decision": "APPLIED",
        "status": "completed",
        "latency_ms": event["latency_ms"],
        "preparation_ms": None,
        "verification_ms": None,
        "graph_node_count": None,
        "graph_edge_count": None,
    }


def _last_event(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


def main() -> None:
    """Run development fixtures or a hash-pinned manifest through pilot paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="pilot-output", help="output directory")
    parser.add_argument("--code-revision", default="", help="source revision identifier")
    parser.add_argument("--manifest", help="optional hash-pinned transition manifest")
    parser.add_argument("--verifier", choices=("rules", "llm"), default="rules")
    parser.add_argument("--provider", choices=("deepseek", "commandcode"), default="deepseek")
    args = parser.parse_args()

    expected_decisions: dict[str, str] | None = None
    if args.manifest:
        entries: list[Transition | ManifestCase] = load_manifest_entries(args.manifest)
        expected_decisions = {
            case_id: label["expected_decision"]
            for case_id, label in load_manifest_labels(args.manifest).items()
        }
        source = f"manifest {args.manifest}"
        invalid_count = sum(entry.invalid for entry in entries)  # type: ignore[union-attr]
    else:
        from .demo import cases

        entries = [Transition.from_dict(raw) for raw in cases()]
        source = "development fixtures"
        invalid_count = 0
    manifest_id = ""
    manifest_sha256 = ""
    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest_id = json.loads(manifest_path.read_text(encoding="utf-8"))["manifest_id"]
        manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    verifier = None
    if args.verifier == "llm":
        from .llm import OpenAICompatibleChatClient
        from .verification import verify_with_llm

        client = (
            OpenAICompatibleChatClient.from_deepseek_env()
            if args.provider == "deepseek"
            else OpenAICompatibleChatClient.from_env()
        )
        verifier = lambda t, representation, prepared: verify_with_llm(
            t, client, representation, prepared_input=prepared
        )
    code_revision = args.code_revision or source_fingerprint()
    results = run_pilot(
        entries,
        args.output,
        code_revision=code_revision,
        manifest_id=manifest_id,
        manifest_sha256=manifest_sha256,
        verifier=verifier,
        verifier_name=(f"llm:{args.provider}" if verifier is not None else args.verifier),
        expected_decisions=expected_decisions,
    )
    print(
        f"Ran {len(entries)} {source} across 3 conditions; summary: "
        f"{Path(args.output) / 'pilot-summary.json'}"
    )
    execution_errors = sum(row["status"] == "error" for row in results)
    invalid_rows = sum(row.get("status") == "invalid_input" for row in results)
    print(
        f"Condition runs: {len(results)}; errors: "
        f"{sum(row['status'] in {'error', 'verifier_error'} for row in results)} "
        f"({execution_errors} execution errors); invalid-input rows: {invalid_rows} "
        f"({invalid_count} invalid cases x 3 conditions)"
    )
    unexpected_invalid = [
        row for row in results
        if row.get("status") == "invalid_input"
        and expected_decisions is not None
        and expected_decisions.get(row["case_id"]) != "INVALID_INPUT"
    ]
    if unexpected_invalid:
        raise SystemExit(
            "unexpected invalid-input rows for gold cases: "
            + ", ".join(sorted({row['case_id'] for row in unexpected_invalid}))
        )
    if execution_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
