"""Guarded JSON persistence and append-only audit logging."""
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .graph import build_graph
from .schemas import Record, Result, Transition
from .serialization import textual_representation, transition_dict
from .verification import verify

_REQUIRED_CHECKS = frozenset({"coverage", "preservation", "faithfulness"})


def _claims_by_id(records: Any) -> dict[str, frozenset[str]]:
    return {record.id: frozenset(record.claims) for record in records}


def _require_matching_prior_state(store: dict[str, Record], t: Transition) -> None:
    """Refuse to verify or commit against a stale or divergent prior state."""
    if _claims_by_id(store.values()) != _claims_by_id(t.before):
        raise ValueError(
            "persisted store does not match transition.before; "
            "refusing to verify against stale state"
        )


def _parse_store(raw: str, path: Path) -> dict[str, Record]:
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError(f"persisted store must be a JSON array: {path}")
    try:
        records = [Record.from_dict(row) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid record in persisted store {path}: {exc}") from exc
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError(f"persisted store has duplicate record IDs: {path}")
    return {record.id: record for record in records}


def _atomic_write(path: Path, text: str) -> None:
    """Durably write text: same-directory temp file, fsync, then atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _restore_store(path: Path, raw: str | None) -> None:
    """Put the previous store bytes back after a failed audit append."""
    if raw is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    _atomic_write(path, raw)


def _append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def commit(store: dict[str, Record], t: Transition, result: Result) -> dict[str, Record]:
    """Replace store only on a valid ACCEPT; return a copy and never mutate input."""
    t.validate()
    _require_matching_prior_state(store, t)
    if result.case_id != t.case_id:
        raise ValueError("result does not belong to transition")
    if result.decision == "ACCEPT":
        check_names = [check.name for check in result.checks]
        if (
            set(check_names) != _REQUIRED_CHECKS
            or len(check_names) != len(_REQUIRED_CHECKS)
            or any(check.status != "PASS" or not check.passed for check in result.checks)
        ):
            raise ValueError("ACCEPT requires every required check to pass")
        return {record.id: record for record in t.candidate}
    return dict(store)


def run_case(
    t: Transition,
    store_path: str | Path,
    log_path: str | Path,
    representation: str = "graph",
    *,
    run_id: str | None = None,
    code_revision: str = "",
    manifest_id: str = "",
    manifest_sha256: str = "",
    verifier: Callable[[Transition, str, Any], Result] | None = None,
) -> Result:
    """Load a JSON store, verify, conditionally commit, and append a JSONL audit event.

    The loaded store must describe the same prior state as ``t.before`` or the run
    fails closed with an audit error. Accepted state is written atomically; held
    state is never written, so a HOLD cannot reformat or create a store file.
    The audit event is appended after the write and the write is rolled back if
    the append fails.
    """
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = run_id or str(uuid.uuid4())
    store_path, log_path = Path(store_path), Path(log_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    previous_store_text: str | None = None
    try:
        if store_path.exists():
            previous_store_text = store_path.read_text(encoding="utf-8")
            store = _parse_store(previous_store_text, store_path)
        else:
            store = {record.id: record for record in t.before}
        _require_matching_prior_state(store, t)
        preparation_started = time.perf_counter()
        graph_node_count = graph_edge_count = None
        if representation == "graph":
            prepared_input = build_graph(t)
            graph_node_count = len(prepared_input["nodes"])
            graph_edge_count = len(prepared_input["edges"])
        else:
            prepared_input = textual_representation(t)
        preparation_ms = round((time.perf_counter() - preparation_started) * 1000, 3)
        verification_started = time.perf_counter()
        result = (
            verify(t, representation, prepared_input=prepared_input)
            if verifier is None
            else verifier(t, representation, prepared_input)
        )
        after = commit(store, t, result)
        verification_ms = round((time.perf_counter() - verification_started) * 1000, 3)
        event_status = (
            "verifier_error"
            if any(check.reason_code == "INVALID_VERIFIER_OUTPUT" for check in result.checks)
            else "completed"
        )
        event = {
            "run_id": run_id,
            "code_revision": code_revision,
            "started_at": started_at,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "preparation_ms": preparation_ms,
            "verification_ms": verification_ms,
            "graph_node_count": graph_node_count,
            "graph_edge_count": graph_edge_count,
            "status": event_status,
            "manifest_id": manifest_id,
            "manifest_sha256": manifest_sha256,
            "input": transition_dict(t),
            "representation": representation,
            "result": result.to_dict(),
            "store_after": [asdict(record) for record in after.values()],
        }
        if result.decision == "ACCEPT":
            payload = json.dumps([asdict(record) for record in after.values()], indent=2) + "\n"
            _atomic_write(store_path, payload)
            try:
                _append_event(log_path, event)
            except BaseException:
                _restore_store(store_path, previous_store_text)
                raise
        else:
            _append_event(log_path, event)
        return result
    except Exception as exc:
        event = {
            "run_id": run_id,
            "code_revision": code_revision,
            "started_at": started_at,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "status": "error",
            "preparation_ms": None,
            "verification_ms": None,
            "graph_node_count": None,
            "graph_edge_count": None,
            "manifest_id": manifest_id,
            "manifest_sha256": manifest_sha256,
            "input": transition_dict(t),
            "representation": representation,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _append_event(log_path, event)
        raise
