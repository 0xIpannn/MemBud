"""Small local runner for paired unguarded, graph, and text pilot conditions."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from .schemas import Transition
from .serialization import transition_dict
from .store import run_case


def source_fingerprint() -> str:
    """Hash executable package/config files for a reproducible dirty-tree label."""
    root = Path(__file__).resolve().parents[2]
    paths = sorted((root / "src/membud").rglob("*.py")) + [root / "pyproject.toml"]
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative + b"\0" + path.read_bytes() + b"\0")
    return "working-tree-sha256:" + digest.hexdigest()


def _require_fresh_output(output_dir: Path, transitions: list[Transition]) -> None:
    """Refuse to mix a new run into stores/logs left by an earlier run."""
    if (output_dir / "pilot-summary.json").exists():
        raise ValueError(
            "output directory already contains a pilot summary; use a fresh --output directory"
        )
    for transition in transitions:
        filename = quote(transition.case_id, safe="") + ".json"
        for condition in ("unguarded", "graph", "text"):
            if (output_dir / condition / filename).exists():
                raise ValueError(
                    f"output directory already contains a store for {transition.case_id}/{condition}; use a fresh --output directory"
                )


def load_manifest_cases(manifest_path: str | Path) -> list[Transition]:
    """Load only transition inputs from a hash-pinned manifest, never its labels."""
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases_path = (manifest_path.parent / manifest["cases_file"]).resolve()
    actual_hash = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    if actual_hash != manifest["cases_sha256"]:
        raise ValueError("case file hash does not match manifest")
    raw_cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    transitions = [Transition.from_dict(raw) for raw in raw_cases]
    case_ids = [transition.case_id for transition in transitions]
    if case_ids != manifest["case_ids"] or len(case_ids) != manifest["case_count"]:
        raise ValueError("case IDs/count do not match manifest")
    return transitions


def run_pilot(
    transitions: Iterable[Transition],
    output_dir: str | Path,
    *,
    run_id: str | None = None,
    code_revision: str = "",
    manifest_id: str = "",
    manifest_sha256: str = "",
    verifier: Callable[[Transition, str, Any], Any] | None = None,
    verifier_name: str = "rules",
) -> list[dict[str, Any]]:
    """Run the same fixed transitions under three isolated pilot conditions.

    This is a plumbing/pilot runner: it does not generate proposals, use gold
    labels, or make graph and text verification semantically distinct.
    """
    transitions = list(transitions)
    for transition in transitions:
        transition.validate()
    case_ids = [transition.case_id for transition in transitions]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case IDs must be unique within a pilot run")

    output_dir = Path(output_dir)
    _require_fresh_output(output_dir, transitions)
    run_id = run_id or str(uuid.uuid4())
    results: list[dict[str, Any]] = []
    for transition in transitions:
        filename = quote(transition.case_id, safe="") + ".json"
        for condition in ("unguarded", "graph", "text"):
            condition_dir = output_dir / condition
            condition_dir.mkdir(parents=True, exist_ok=True)
            store_path = condition_dir / filename
            if condition == "unguarded":
                result = _apply_unguarded(
                    transition,
                    store_path,
                    condition_dir / "audit.jsonl",
                    run_id,
                    code_revision,
                    manifest_id,
                    manifest_sha256,
                )
            else:
                try:
                    verification = run_case(
                        transition,
                        store_path,
                        condition_dir / "audit.jsonl",
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
                    event = _last_event(condition_dir / "audit.jsonl")
                    result["status"] = event["status"]
                    result.update({
                        key: event[key]
                        for key in (
                            "latency_ms", "preparation_ms", "verification_ms",
                            "graph_node_count", "graph_edge_count", "result",
                        )
                    })
                except Exception as exc:
                    result = {
                        "case_id": transition.case_id,
                        "condition": condition,
                        "decision": "ERROR",
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    if (condition_dir / "audit.jsonl").exists():
                        event = _last_event(condition_dir / "audit.jsonl")
                        result.update({
                            key: event.get(key)
                            for key in (
                                "latency_ms", "preparation_ms", "verification_ms",
                                "graph_node_count", "graph_edge_count", "result",
                            )
                        })
            results.append(result)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pilot-summary.json").write_text(
        json.dumps({
            "run_id": run_id,
            "code_revision": code_revision,
            "manifest_id": manifest_id,
            "manifest_sha256": manifest_sha256,
            "verifier": verifier_name,
            "results": results,
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return results


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

    if args.manifest:
        transitions = load_manifest_cases(args.manifest)
        source = f"manifest {args.manifest}"
    else:
        from .demo import cases

        transitions = [Transition.from_dict(raw) for raw in cases()]
        source = "development fixtures"
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
        transitions,
        args.output,
        code_revision=code_revision,
        manifest_id=manifest_id,
        manifest_sha256=manifest_sha256,
        verifier=verifier,
        verifier_name=(f"llm:{args.provider}" if verifier is not None else args.verifier),
    )
    print(f"Ran {len(transitions)} {source} across 3 conditions; summary: {Path(args.output) / 'pilot-summary.json'}")
    execution_errors = sum(row["status"] == "error" for row in results)
    print(
        f"Condition runs: {len(results)}; errors: "
        f"{sum(row['status'] in {'error', 'verifier_error'} for row in results)} "
        f"({execution_errors} execution errors)"
    )
    if execution_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
