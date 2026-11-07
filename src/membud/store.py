"""Guarded, versioned JSON persistence and append-only audit logging.

Store format
------------
Accepted state is written as a versioned JSON object::

    {"schema_version": 2, "version": <int>, "records": [{"id": ..., "claims": [...]}]}

``version`` starts at 1 for the first accepted write and increments by exactly
one per accepted write. A legacy store is a bare JSON list of records (schema
version 1, version 1); it is still loaded for backward compatibility and is
rewritten in the versioned format on the next ACCEPT. An absent store counts as
version 0. Duplicate record IDs are always rejected.

Writes are atomic (same-directory temp file, fsync, ``os.replace``) and only
happen on ACCEPT; a HOLD never creates, rewrites, or reformats a store file.
Any stray atomic-write temp file (``.<name>.<random>.tmp``) left by an earlier
crashed process is ignored and removed before loading; it is never treated as
state. A persistent ``<store>.lock`` file is used for an exclusive POSIX
``flock`` around load -> verify -> write -> audit; the lock file itself carries
no state.
"""
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # pragma: no cover - exercised explicitly by tests via monkeypatching
    import fcntl
except ImportError:  # Windows and other non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from .graph import build_graph
from .schemas import Record, Result, Transition
from .serialization import textual_representation, transition_dict
from .verification import verify

STORE_SCHEMA_VERSION = 2
LEGACY_STORE_SCHEMA_VERSION = 1
_REQUIRED_CHECKS = frozenset({"coverage", "preservation", "faithfulness"})
_OPTIONAL_CHECKS = frozenset({"semantic"})
_ALLOWED_CHECKS = _REQUIRED_CHECKS | _OPTIONAL_CHECKS
_ACCEPT_CHECK_ERROR = (
    "ACCEPT requires every required check to pass; coverage, preservation, and "
    "faithfulness are mandatory and an optional semantic check must pass too"
)


def _claims_by_id(records: Any) -> dict[str, frozenset[str]]:
    return {record.id: frozenset(record.claims) for record in records}


def _require_matching_prior_state(store: dict[str, Record], t: Transition) -> None:
    """Refuse to verify or commit against a stale or divergent prior state."""
    if _claims_by_id(store.values()) != _claims_by_id(t.before):
        raise ValueError(
            "persisted store does not match transition.before; "
            "refusing to verify against stale state"
        )


def _parse_store(raw: str, path: Path) -> tuple[dict[str, Record], int | None, int]:
    """Parse persisted JSON, returning records plus (schema_version, version).

    ``schema_version`` is ``None`` for an absent store, which is version 0.
    Legacy stores (bare JSON lists) are schema version 1 / version 1.
    """
    payload = json.loads(raw)
    if isinstance(payload, list):
        rows, schema_version, version = payload, LEGACY_STORE_SCHEMA_VERSION, 1
    elif isinstance(payload, dict):
        if set(payload) != {"schema_version", "version", "records"}:
            raise ValueError(
                f"invalid persisted store {path}: expected exactly "
                "schema_version, version, and records"
            )
        schema_version = payload["schema_version"]
        version = payload["version"]
        rows = payload["records"]
        if schema_version != STORE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported persisted store schema_version {schema_version!r} in {path}"
            )
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            raise ValueError(f"invalid persisted store version {version!r} in {path}")
        if not isinstance(rows, list):
            raise ValueError(
                f"invalid persisted store {path}: records must be a JSON array"
            )
    else:
        raise ValueError(
            f"persisted store must be a JSON array (v1) or versioned object (v2): {path}"
        )
    try:
        records = [Record.from_dict(row) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid record in persisted store {path}: {exc}") from exc
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError(f"persisted store has duplicate record IDs: {path}")
    return {record.id: record for record in records}, schema_version, version


def _serialize_store(records: dict[str, Record], version: int) -> str:
    payload = {
        "schema_version": STORE_SCHEMA_VERSION,
        "version": version,
        "records": [asdict(record) for record in records.values()],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _recover_stray_temp_files(path: Path) -> None:
    """Remove atomic-write temp files left behind by an interrupted writer.

    Only files named like this store's own temp files (``.<name>.*.tmp``) or
    the older ``<name>.tmp`` convention are removed. A temp file is never read
    as state; recovery failures are ignored so a read-only leftover cannot
    block the run.
    """
    try:
        entries = list(path.parent.iterdir())
    except OSError:
        return
    prefix = f".{path.name}."
    legacy_name = f"{path.name}.tmp"
    for entry in entries:
        name = entry.name
        if not (name == legacy_name or (name.startswith(prefix) and name.endswith(".tmp"))):
            continue
        try:
            entry.unlink()
        except OSError:
            pass


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


def _acquire_store_lock(store_path: Path) -> tuple[Any, bool]:
    """Take an exclusive flock on ``<store>.lock``; degrade to a no-op.

    Returns ``(handle, acquired)``. Locking is advisory and local-filesystem
    based; when ``fcntl`` is unavailable (e.g. Windows) or the lock file cannot
    be opened/flocked, the run proceeds unlocked and reports
    ``lock_acquired`` false instead of failing.
    """
    if fcntl is None:
        return None, False
    lock_path = Path(str(store_path) + ".lock")
    try:
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError:
        return None, False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError:
        try:
            handle.close()
        except OSError:
            pass
        return None, False
    return handle, True


def _release_store_lock(lock: tuple[Any, bool] | None) -> None:
    if lock is None:
        return
    handle, acquired = lock
    if not acquired:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def commit(store: dict[str, Record], t: Transition, result: Result) -> dict[str, Record]:
    """Replace store only on a valid ACCEPT; return a copy and never mutate input.

    The three deterministic checks (coverage, preservation, faithfulness) are
    mandatory and must PASS. An optional fourth check literally named
    ``semantic`` may be present; if present it must PASS too and only appear
    once. Unknown or duplicate check names are rejected.
    """
    t.validate()
    _require_matching_prior_state(store, t)
    if result.case_id != t.case_id:
        raise ValueError("result does not belong to transition")
    if result.decision == "ACCEPT":
        check_names = [check.name for check in result.checks]
        if (
            len(check_names) != len(set(check_names))
            or not _REQUIRED_CHECKS.issubset(check_names)
            or not set(check_names).issubset(_ALLOWED_CHECKS)
        ):
            raise ValueError(_ACCEPT_CHECK_ERROR)
        if any(check.status != "PASS" or not check.passed for check in result.checks):
            raise ValueError(_ACCEPT_CHECK_ERROR)
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
    expected_version: int | None = None,
    verifier: Callable[[Transition, str, Any], Result] | None = None,
) -> Result:
    """Load a versioned JSON store, verify, conditionally commit, and audit.

    The whole load -> verify -> write -> audit sequence runs under an exclusive
    POSIX ``flock`` on ``<store_path>.lock`` when locking is available. The
    loaded store must describe the same prior state as ``t.before`` or the run
    fails closed with an audit error. When ``expected_version`` is given, the
    on-disk store version (0 when the store is absent) must match it or the run
    fails closed with a version-conflict audit error and leaves the store
    untouched (optimistic concurrency control).

    Accepted state is written atomically in the versioned format and the version
    increments by exactly one; held state is never written, so a HOLD cannot
    reformat or create a store file. The audit event is appended after the
    write and the write is rolled back if the append fails.
    """
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = run_id or str(uuid.uuid4())
    store_path, log_path = Path(store_path), Path(log_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _acquire_store_lock(store_path)
    lock_acquired = bool(lock and lock[1])
    previous_store_text: str | None = None
    schema_version_before: int | None = None
    version_before = 0
    schema_version_after: int | None = schema_version_before
    version_after = version_before
    try:
        try:
            _recover_stray_temp_files(store_path)
            if store_path.exists():
                previous_store_text = store_path.read_text(encoding="utf-8")
                store, schema_version_before, version_before = _parse_store(
                    previous_store_text, store_path
                )
            else:
                store = {record.id: record for record in t.before}
            if expected_version is not None and expected_version != version_before:
                raise ValueError(
                    "state version conflict: expected "
                    f"{expected_version}, found on-disk version {version_before}"
                )
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
            if result.decision == "ACCEPT":
                schema_version_after = STORE_SCHEMA_VERSION
                version_after = version_before + 1
            else:
                schema_version_after = schema_version_before
                version_after = version_before
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
                "decision": result.decision,
                "manifest_id": manifest_id,
                "manifest_sha256": manifest_sha256,
                "input": transition_dict(t),
                "representation": representation,
                "result": result.to_dict(),
                "store_before": [asdict(record) for record in store.values()],
                "store_after": [asdict(record) for record in after.values()],
                "store_schema_version_before": schema_version_before,
                "store_schema_version_after": schema_version_after,
                "store_version_before": version_before,
                "store_version_after": version_after,
                "expected_version": expected_version,
                "lock_acquired": lock_acquired,
            }
            if result.decision == "ACCEPT":
                _atomic_write(store_path, _serialize_store(after, version_after))
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
                "store_schema_version_before": schema_version_before,
                "store_schema_version_after": schema_version_after,
                "store_version_before": version_before,
                "store_version_after": version_after,
                "expected_version": expected_version,
                "lock_acquired": lock_acquired,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _append_event(log_path, event)
            raise
    finally:
        _release_store_lock(lock)
