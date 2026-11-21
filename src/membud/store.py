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
On ACCEPT the state file is written first and the completed audit event is
appended afterwards; if the append raises, the write is rolled back to the
previous bytes (and the error event records ``store_written``/``rolled_back``).
A process death between the write and the append is not catchable: it can leave
an accepted state with no completed audit event. That window is documented, not
claimed away; the audit log is append-only but it is not write-ahead.
Any stray atomic-write temp file (``.<name>.<random>.tmp``) left by an earlier
crashed process is ignored and removed before loading; it is never treated as
state. A persistent ``<store>.lock`` file is used for an exclusive POSIX
``flock`` around load -> verify -> write -> audit; the lock file itself carries
no state.
"""
import errno
import json
import os
import tempfile
import time
import uuid
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # pragma: no cover - exercised explicitly by tests via monkeypatching
    import fcntl
except ImportError:  # Windows and other non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from .graph import build_graph
from .schemas import SCHEMA_VERSION, Record, Result, Transition
from .serialization import textual_representation, transition_dict
from .verification import verify

STORE_SCHEMA_VERSION = 2
LEGACY_STORE_SCHEMA_VERSION = 1
_REQUIRED_CHECKS = frozenset({"coverage", "preservation", "faithfulness"})
_OPTIONAL_CHECKS = frozenset({"semantic", "transcript_grounding"})
_ALLOWED_CHECKS = _REQUIRED_CHECKS | _OPTIONAL_CHECKS
_ACCEPT_CHECK_ERROR = (
    "ACCEPT requires every required check to pass; coverage, preservation, and "
    "faithfulness are mandatory and optional semantic/transcript_grounding checks must pass too"
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
    """Append one audit event, creating the PII-bearing log owner-only (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        try:
            path.chmod(0o600)  # tighten a pre-existing world-readable log
        except OSError:
            pass
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _acquire_store_lock(
    store_path: Path, *, timeout_seconds: float | None = None
) -> tuple[Any, bool]:
    """Take an exclusive flock on ``<store>.lock``; degrade to a no-op by default.

    Returns ``(handle, acquired)``. Locking is advisory and local-filesystem
    based; when ``fcntl`` is unavailable (e.g. Windows) or the lock file cannot
    be opened/flocked, the run proceeds unlocked and reports
    ``lock_acquired`` false instead of failing unless the caller passes
    ``require_lock=True`` to :func:`run_case`.

    When ``timeout_seconds`` is provided, polls ``LOCK_EX | LOCK_NB`` until the
    deadline expires and raises :class:`TimeoutError` instead of blocking
    indefinitely across a slow verifier callback.
    """
    if timeout_seconds is not None and timeout_seconds < 0:
        raise ValueError("lock_timeout_seconds must be non-negative")
    if fcntl is None:
        return None, False
    lock_path = Path(str(store_path) + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "a+", encoding="utf-8")
    except OSError:
        return None, False
    if timeout_seconds is None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError:
            try:
                handle.close()
            except OSError:
                pass
            return None, False
        return handle, True

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle, True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    try:
                        handle.close()
                    except OSError:
                        pass
                    raise TimeoutError(
                        f"timed out after {timeout_seconds}s waiting for exclusive store lock on {lock_path}"
                    ) from exc
                time.sleep(min(0.01, max(0.001, remaining)))
                continue
            try:
                handle.close()
            except OSError:
                pass
            return None, False


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


def chained_legacy_version_error(t: Transition) -> str:
    return (
        f"refusing to commit legacy schema_version {t.schema_version}: the commit "
        "path requires the current transition format (schema_version "
        f"{STORE_SCHEMA_VERSION}); call membud.migrations.migrate_transition_to_v2() "
        "explicitly (or pass allow_legacy=True for an archival replay)"
    )


def commit(
    store: dict[str, Record],
    t: Transition,
    result: Result,
    *,
    allow_legacy: bool = False,
) -> dict[str, Record]:
    """Replace store only on a valid ACCEPT; return a copy and never mutate input.

    The three deterministic checks (coverage, preservation, faithfulness) are
    mandatory and must PASS. Optional checks named ``semantic`` and/or
    ``transcript_grounding`` may be present; if present each must PASS too and
    only appear once. Unknown or duplicate check names are rejected.

    Legacy v1 transitions are archival-only: committing one requires an explicit
    ``allow_legacy=True`` (normal callers should migrate first), so the weaker
    legacy operation-coverage semantics cannot authorize a write by accident.
    """
    t.validate()
    if t.schema_version != SCHEMA_VERSION and not allow_legacy:
        raise ValueError(chained_legacy_version_error(t))
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
    allow_legacy: bool = False,
    require_lock: bool = False,
    lock_timeout_seconds: float | None = None,
) -> Result:
    """Load a versioned JSON store, verify, conditionally commit, and audit.

    The whole load -> verify -> write -> audit sequence runs under an exclusive
    POSIX ``flock`` on ``<store_path>.lock`` when locking is available.  By
    default (``require_lock=False``), platforms or filesystems without working
    ``flock`` degrade to unlocked execution and record ``lock_acquired: false``;
    pass ``require_lock=True`` to fail closed if the lock cannot be acquired,
    and ``lock_timeout_seconds`` to bound waiting when a lock is held across a
    slow verifier callback (such as an LLM call).  The
    loaded store must describe the same prior state as ``t.before`` or the run
    fails closed with an audit error. When ``expected_version`` is given, the
    on-disk store version (0 when the store is absent) must match it or the run
    fails closed with a version-conflict audit error and leaves the store
    untouched (optimistic concurrency control).

    Accepted state is written atomically in the versioned format and the version
    increments by exactly one; held state is never written, so a HOLD cannot
    reformat or create a store file. The state file is written first and the
    completed audit event appended after it; a caught append failure rolls the
    state file back and the error event records ``store_written=False`` and
    ``rolled_back=True``. A process death between the two cannot be caught and
    is the documented unaudited-ACCEPT crash window.
    """
    if lock_timeout_seconds is not None and lock_timeout_seconds < 0:
        raise ValueError("lock_timeout_seconds must be non-negative")
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = run_id or str(uuid.uuid4())
    store_path, log_path = Path(store_path), Path(log_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock: tuple[Any, bool] | None = None
    lock_acquired = False
    previous_store_text: str | None = None
    schema_version_before: int | None = None
    version_before = 0
    schema_version_after: int | None = schema_version_before
    version_after = version_before
    write_attempted = False
    store_write_completed = False
    store_committed = False
    rollback_performed = False
    rollback_failed = False
    try:
        try:
            lock = _acquire_store_lock(store_path, timeout_seconds=lock_timeout_seconds)
            lock_acquired = bool(lock and lock[1])
            if require_lock and not lock_acquired:
                raise ValueError(
                    f"failed to acquire exclusive store lock on {store_path}.lock"
                )
            _recover_stray_temp_files(store_path)
            if store_path.exists():
                previous_store_text = store_path.read_text(encoding="utf-8")
                store, schema_version_before, version_before = _parse_store(
                    previous_store_text, store_path
                )
            else:
                store = {record.id: record for record in t.before}
            t.validate()
            if t.schema_version != SCHEMA_VERSION and not allow_legacy:
                raise ValueError(chained_legacy_version_error(t))
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
            after = commit(store, t, result, allow_legacy=allow_legacy)
            verification_ms = round((time.perf_counter() - verification_started) * 1000, 3)
            planned_accept = result.decision == "ACCEPT"
            if planned_accept:
                planned_schema_version_after = STORE_SCHEMA_VERSION
                planned_version_after = version_before + 1
            else:
                planned_schema_version_after = schema_version_before
                planned_version_after = version_before
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
                "store_schema_version_after": planned_schema_version_after,
                "store_version_before": version_before,
                "store_version_after": planned_version_after,
                "store_written": planned_accept,
                "rolled_back": False,
                "expected_version": expected_version,
                "lock_acquired": lock_acquired,
            }
            if planned_accept:
                write_attempted = True
                _atomic_write(store_path, _serialize_store(after, planned_version_after))
                store_write_completed = True
                try:
                    _append_event(log_path, event)
                except BaseException:
                    try:
                        _restore_store(store_path, previous_store_text)
                        rollback_performed = True
                    except BaseException:
                        rollback_failed = True
                    raise
                store_committed = True
            else:
                _append_event(log_path, event)
            schema_version_after = planned_schema_version_after
            version_after = planned_version_after
            return result
        except Exception as exc:
            # The error event describes the *observed* state: a failed or
            # rolled-back write must not claim an advanced version.  Callers can
            # distinguish the cases through ``store_written``/``rolled_back``.
            # A completed audit append implies a committed write.  If the write
            # completed but the audit append and its rollback both failed, the
            # store is durably advanced and must be reported as written.
            store_written = store_committed or (
                store_write_completed and not rollback_performed
            )
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
                "store_schema_version_after": (
                    STORE_SCHEMA_VERSION if store_written else schema_version_before
                ),
                "store_version_before": version_before,
                "store_version_after": (
                    version_before + 1 if store_written else version_before
                ),
                "store_written": store_written,
                "rolled_back": rollback_performed,
                "rollback_failed": rollback_failed,
                "expected_version": expected_version,
                "lock_acquired": lock_acquired,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            try:
                _append_event(log_path, event)
            except Exception:
                # Never let a failing audit append hide the original error.
                pass
            raise
    finally:
        _release_store_lock(lock)


def resync_store(
    store_path: str | Path,
    records: Iterable[Record],
    *,
    log_path: str | Path | None = None,
    run_id: str = "",
    code_revision: str = "",
    only_if_diverged: bool = True,
) -> dict[str, Any]:
    """Explicitly rewrite the shadow baseline from an authoritative host snapshot.

    This is the documented recovery path for the guarded-adapter crash window in
    which the host ``replace_state`` succeeded but the shadow baseline commit did
    not (so the baseline is behind the host).  It is never called automatically:
    the caller must have established that the host state is authoritative.

    The snapshot is written atomically in the current store format, incrementing
    the on-disk version once, and an append-only ``resync`` audit event is
    recorded.  With ``only_if_diverged`` (the default) a baseline that already
    matches the requested snapshot is left untouched and no version is bumped.

    Raises ``ValueError`` if the existing store cannot be parsed (a corrupt or
    unknown-version store must be resolved explicitly, never overwritten here).
    """
    requested_records = tuple(records)
    requested = {record.id: record for record in requested_records}
    if len(requested) != len(requested_records):
        raise ValueError("resync records must have unique ids")
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _acquire_store_lock(store_path)
    lock_acquired = bool(lock and lock[1])
    try:
        _recover_stray_temp_files(store_path)
        existing: dict[str, Record] | None = None
        version_before = 0
        schema_version_before: int | None = None
        if store_path.exists():
            existing, schema_version_before, version_before = _parse_store(
                store_path.read_text(encoding="utf-8"), store_path
            )
        if (
            only_if_diverged
            and existing is not None
            and _claims_by_id(existing.values()) == _claims_by_id(requested.values())
        ):
            return {
                "store_path": str(store_path),
                "resynced": False,
                "reason": "shadow baseline already matches the requested snapshot",
                "store_version_before": version_before,
                "store_version_after": version_before,
                "lock_acquired": lock_acquired,
            }
        version_after = version_before + 1
        _atomic_write(store_path, _serialize_store(requested, version_after))
        if log_path is not None:
            _append_event(
                Path(log_path),
                {
                    "run_id": run_id or str(uuid.uuid4()),
                    "code_revision": code_revision,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "status": "resync",
                    "decision": "RESYNC",
                    "representation": "host_snapshot",
                    "store_before": [asdict(record) for record in (existing or {}).values()],
                    "store_after": [asdict(record) for record in requested.values()],
                    "store_schema_version_before": (
                        None if existing is None else schema_version_before
                    ),
                    "store_schema_version_after": STORE_SCHEMA_VERSION,
                    "store_version_before": version_before,
                    "store_version_after": version_after,
                    "expected_version": None,
                    "lock_acquired": lock_acquired,
                },
            )
        return {
            "store_path": str(store_path),
            "resynced": True,
            "reason": "shadow baseline rewritten from the authoritative host snapshot",
            "store_version_before": version_before,
            "store_version_after": version_after,
            "record_count": len(requested),
            "lock_acquired": lock_acquired,
        }
    finally:
        _release_store_lock(lock)
