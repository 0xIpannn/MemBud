"""Reference host backends and the guarded write-interception layer.

This module demonstrates the adapter contract end to end with stdlib-only
backends and *no* third-party service.  It is the reference implementation
that proves interception is possible; it is NOT evidence that any third-party
memory backend (Mem0, A-Mem, ...) can be integrated safely without a fork or a
host-owned proposal pipeline.  See ``data/backend-feasibility.md``.

Flow implemented by :class:`GuardedMemory`:

1. snapshot the scoped prior state (explicit prior-state capture);
2. normalize the proposed candidate, evidence/provenance and declared
   operations, then build a canonical ``Transition`` (invalid input is
   rejected here, before any write path is entered);
3. run MemBud verification (graph path) and audit it through
   :func:`membud.store.run_case`;
4. if and only if the result is ACCEPT, re-check the prior state (optimistic
   concurrency) and call ``MemoryAdapter.replace_state`` with exactly the
   candidate records;
5. append a decision record to a decision audit log.

HOLD, REJECTED and invalid input never call ``replace_state``, so backend state
is left byte-for-byte unchanged by those outcomes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schemas import SCHEMA_VERSION, Evidence, Result, Transition
from ..serialization import transition_dict
from ..store import run_case
from .base import (
    AdapterContractError,
    MemoryAdapter,
    PriorState,
    canonical_records,
    capture_prior_state,
    prior_state_matches,
    scope_key,
)

__all__ = [
    "GuardedDecision",
    "GuardedMemory",
    "InMemoryBackend",
    "SQLiteBackend",
]


# ---------------------------------------------------------------------------
# Reference backends
# ---------------------------------------------------------------------------


class InMemoryBackend:
    """A minimal in-memory host backend implementing ``MemoryAdapter``.

    State is stored per scope as an immutable tuple of canonical ``Record``
    values.  ``replace_state`` validates the whole candidate before mutating, so
    a failed write leaves prior state untouched.
    """

    backend_name = "reference-in-memory"

    def __init__(
        self,
        records: Iterable[Any] | None = None,
        *,
        scope: Mapping[str, str] | None = None,
    ) -> None:
        self._state: dict[str, tuple[Any, ...]] = {}
        if records:
            self._state[scope_key(scope or {})] = canonical_records(records)

    def snapshot(self, scope: Mapping[str, str]) -> tuple[Any, ...]:
        return self._state.get(scope_key(scope), ())

    def replace_state(self, scope: Mapping[str, str], records: Sequence[Any]) -> None:
        key = scope_key(scope)
        normalized = canonical_records(records)  # validate before mutating
        self._state[key] = normalized

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        """Return a JSON-compatible debug view of all scopes."""
        from dataclasses import asdict

        return {
            key: [asdict(record) for record in self._state[key]]
            for key in sorted(self._state)
        }


class SQLiteBackend:
    """A stdlib SQLite host backend implementing ``MemoryAdapter``.

    Each scope is a set of rows replaced inside one ``BEGIN IMMEDIATE``
    transaction, so an accepted replacement is atomic.  Reads take no write
    lock and do not modify the database file, which lets tests prove that a
    HOLD leaves backend bytes identical.
    """

    backend_name = "reference-sqlite"
    _TABLE = "memory_records"

    def __init__(
        self,
        path: str | Path,
        records: Iterable[Any] | None = None,
        *,
        scope: Mapping[str, str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    scope TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    record_id TEXT NOT NULL,
                    claims TEXT NOT NULL,
                    PRIMARY KEY (scope, ordinal),
                    UNIQUE (scope, record_id)
                )
                """
            )
        if records:
            self.replace_state(scope or {}, records)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)

    def snapshot(self, scope: Mapping[str, str]) -> tuple[Any, ...]:
        from ..schemas import Record

        key = scope_key(scope)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT record_id, claims FROM {self._TABLE} "
                "WHERE scope = ? ORDER BY ordinal",
                (key,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            Record(record_id, tuple(json.loads(claims))) for record_id, claims in rows
        )

    def replace_state(self, scope: Mapping[str, str], records: Sequence[Any]) -> None:
        key = scope_key(scope)
        normalized = canonical_records(records)  # validate before opening a write transaction
        payload = [
            (key, ordinal, record.id, json.dumps(list(record.claims), ensure_ascii=False))
            for ordinal, record in enumerate(normalized)
        ]
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"DELETE FROM {self._TABLE} WHERE scope = ?", (key,))
            connection.executemany(
                f"INSERT INTO {self._TABLE} (scope, ordinal, record_id, claims) "
                "VALUES (?, ?, ?, ?)",
                payload,
            )
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def bytes_digest(self) -> str:
        """SHA-256 of the database file, for byte-identical HOLD assertions."""
        import hashlib

        return hashlib.sha256(self.path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Guarded interception
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardedDecision:
    """The outcome of one guarded proposal and the state it was based on."""

    case_id: str
    decision: str  # "ACCEPT" | "HOLD" | "REJECTED"
    applied: bool
    reason: str
    prior: PriorState
    transition: Transition | None
    result: Result | None
    run_id: str
    backend_name: str
    representation: str
    audit_event: dict[str, Any] | None = None
    error_type: str = ""
    latency_ms: float = 0.0
    created_at: str = ""

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return () if self.transition is None else self.transition.evidence

    @property
    def candidate(self) -> tuple[Any, ...]:
        return () if self.transition is None else self.transition.candidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event": "guarded_memory_decision",
            "created_at": self.created_at,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "backend_name": self.backend_name,
            "representation": self.representation,
            "decision": self.decision,
            "applied": self.applied,
            "reason": self.reason,
            "error_type": self.error_type,
            "latency_ms": self.latency_ms,
            "prior_state": self.prior.as_dict(),
            "transition": None if self.transition is None else transition_dict(self.transition),
            "result": None if self.result is None else self.result.to_dict(),
            "audit_event": self.audit_event,
        }


def _state_by_id(records: Sequence[Any]) -> dict[str, tuple[str, ...]]:
    return {record.id: tuple(record.claims) for record in records}


def _normalize_evidence(items: Iterable[Any]) -> tuple[Evidence, ...]:
    normalized: list[Evidence] = []
    for item in items:
        if isinstance(item, Evidence):
            normalized.append(item)
        elif isinstance(item, Mapping):
            normalized.append(Evidence.from_dict(dict(item)))
        else:
            raise AdapterContractError(
                f"evidence must be Evidence values or dicts, got {type(item).__name__}"
            )
    return tuple(normalized)


def _operation_payload(raw: Any) -> dict[str, Any]:
    """Translate a caller-declared operation into canonical transition JSON.

    Supports both a nested ``authorization`` object and flat ``source`` /
    ``evidence_id`` / ``rationale`` convenience keys.  The payload is still
    validated by ``Transition.from_dict`` / ``Operation.from_dict``; this
    helper only shapes the input.
    """
    if not isinstance(raw, Mapping):
        raise AdapterContractError(
            f"operations must be mappings, got {type(raw).__name__}"
        )
    kind = raw.get("type")
    if not isinstance(kind, str) or not kind.strip():
        raise AdapterContractError("operation type must be a non-empty string")
    kind = kind.strip().upper()
    payload: dict[str, Any] = {"type": kind}
    for key in ("record_id", "target_id"):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise AdapterContractError(f"operation {key} must be a string or null")
            payload[key] = value
    reason = raw.get("reason")
    rationale = raw.get("rationale")
    if isinstance(reason, str) and reason.strip():
        payload["reason"] = reason
    elif isinstance(rationale, str) and rationale.strip():
        payload["reason"] = rationale

    authorization = raw.get("authorization")
    if authorization is None and kind == "DELETE" and any(
        key in raw for key in ("source", "evidence_id", "rationale")
    ):
        authorization = {
            "source": raw.get("source"),
            "evidence_id": raw.get("evidence_id"),
            "target_id": payload.get("target_id"),
            "rationale": raw.get("rationale") or raw.get("reason"),
        }
    if authorization is not None:
        if not isinstance(authorization, Mapping):
            raise AdapterContractError("operation authorization must be a mapping")
        payload["authorization"] = dict(authorization)
    return payload


class GuardedMemory:
    """Apply proposals to a host backend only when MemBud verification accepts.

    Parameters
    ----------
    adapter:
        Any object implementing the structural ``MemoryAdapter`` protocol.
    store_path / audit_log_path:
        Paths used by :func:`membud.store.run_case` for the controlled JSON
        baseline and the run audit stream.  The store is a shadow/audit
        baseline, not the host backend itself.
    decision_log_path:
        Append-only JSONL log of every guarded decision (ACCEPT, HOLD and
        REJECTED, including invalid input).  Defaults to a sibling of
        ``store_path``.
    representation:
        ``"graph"`` (default, structural verifier) or ``"text"`` (baseline).
    """

    def __init__(
        self,
        adapter: MemoryAdapter,
        *,
        store_path: str | Path,
        audit_log_path: str | Path,
        decision_log_path: str | Path | None = None,
        representation: str = "graph",
        manifest_id: str = "",
        manifest_sha256: str = "",
        code_revision: str = "",
    ) -> None:
        if not isinstance(adapter, MemoryAdapter):
            raise AdapterContractError("adapter does not implement MemoryAdapter")
        if representation not in {"graph", "text"}:
            raise ValueError("representation must be 'graph' or 'text'")
        self.adapter = adapter
        self.store_path = Path(store_path)
        self.audit_log_path = Path(audit_log_path)
        self.decision_log_path = (
            Path(decision_log_path)
            if decision_log_path is not None
            else self.store_path.parent / "guarded-decisions.jsonl"
        )
        self.representation = representation
        self.manifest_id = manifest_id
        self.manifest_sha256 = manifest_sha256
        self.code_revision = code_revision

    # -- read side ---------------------------------------------------------
    def capture_prior(self, scope: Mapping[str, str]) -> PriorState:
        """Explicitly snapshot the scoped prior state before proposing a write."""
        return capture_prior_state(self.adapter, scope)

    # -- write side --------------------------------------------------------
    def propose_update(
        self,
        scope: Mapping[str, str],
        *,
        case_id: str,
        candidate: Sequence[Any],
        operations: Sequence[Mapping[str, Any]] = (),
        evidence: Sequence[Any] = (),
        required_claims: Sequence[str] = (),
        run_id: str | None = None,
    ) -> GuardedDecision:
        """Verify a proposed candidate and persist it only on ACCEPT.

        ``candidate`` is the complete proposed scoped state (not a delta) and
        ``operations`` must explicitly declare every change; undeclared
        insertion/modification/removal and DELETE without structured
        authorization make the proposal invalid and it is rejected before any
        write is attempted.  Returns a :class:`GuardedDecision`; invalid input
        and verifier HOLDs are decisions, not exceptions.
        """
        started = time.perf_counter()
        run_id = run_id or f"guarded-{uuid.uuid4()}"
        prior = self.capture_prior(scope)
        try:
            candidate_records = canonical_records(candidate)
            evidence_records = _normalize_evidence(evidence)
            payloads = [_operation_payload(operation) for operation in operations]
            if not payloads and _state_by_id(prior.records) == _state_by_id(candidate_records):
                payloads = [{"type": "NOOP"}]
            transition = Transition.from_dict(
                {
                    "schema_version": SCHEMA_VERSION,
                    "case_id": case_id,
                    "before": [
                        {"id": record.id, "claims": list(record.claims)}
                        for record in prior.records
                    ],
                    "candidate": [
                        {"id": record.id, "claims": list(record.claims)}
                        for record in candidate_records
                    ],
                    "evidence": [
                        {
                            "id": item.id,
                            "supports": list(item.supports),
                            "source": item.source,
                            "text": item.text,
                            "contradicts": list(item.contradicts),
                        }
                        for item in evidence_records
                    ],
                    "required_claims": list(required_claims),
                    "operations": payloads,
                }
            )
        except (AdapterContractError, KeyError, TypeError, ValueError) as exc:
            return self._record(
                case_id=str(case_id),
                decision="REJECTED",
                applied=False,
                reason=f"invalid proposed update: {exc}",
                prior=prior,
                transition=None,
                result=None,
                run_id=run_id,
                audit_event=None,
                error_type=type(exc).__name__,
                started=started,
            )

        try:
            result = run_case(
                transition,
                self.store_path,
                self.audit_log_path,
                self.representation,
                run_id=run_id,
                code_revision=self.code_revision,
                manifest_id=self.manifest_id,
                manifest_sha256=self.manifest_sha256,
            )
        except Exception as exc:  # run_case fails closed and audits the error
            return self._record(
                case_id=case_id,
                decision="REJECTED",
                applied=False,
                reason=f"verification path failed closed: {exc}",
                prior=prior,
                transition=transition,
                result=None,
                run_id=run_id,
                audit_event=self._last_audit_event(run_id),
                error_type=type(exc).__name__,
                started=started,
            )

        audit_event = self._last_audit_event(run_id)
        if result.decision != "ACCEPT":
            return self._record(
                case_id=case_id,
                decision="HOLD",
                applied=False,
                reason="verification did not accept the candidate; backend state unchanged",
                prior=prior,
                transition=transition,
                result=result,
                run_id=run_id,
                audit_event=audit_event,
                started=started,
            )

        # Optimistic concurrency: do not persist onto a changed prior state.
        if not prior_state_matches(self.adapter, scope, prior):
            return self._record(
                case_id=case_id,
                decision="REJECTED",
                applied=False,
                reason="backend state changed after the prior-state capture; refusing stale write",
                prior=prior,
                transition=transition,
                result=result,
                run_id=run_id,
                audit_event=audit_event,
                error_type="StalePriorState",
                started=started,
            )

        try:
            self.adapter.replace_state(scope, transition.candidate)
        except Exception as exc:
            self._restore_prior(scope, prior)
            return self._record(
                case_id=case_id,
                decision="REJECTED",
                applied=False,
                reason=f"backend apply failed: {exc}",
                prior=prior,
                transition=transition,
                result=result,
                run_id=run_id,
                audit_event=audit_event,
                error_type=type(exc).__name__,
                started=started,
            )

        applied_state = self.capture_prior(scope)
        if applied_state.records != transition.candidate:
            self._restore_prior(scope, prior)
            return self._record(
                case_id=case_id,
                decision="REJECTED",
                applied=False,
                reason="backend state after apply did not equal the accepted candidate; rolled back",
                prior=prior,
                transition=transition,
                result=result,
                run_id=run_id,
                audit_event=audit_event,
                error_type="ApplyVerificationError",
                started=started,
            )

        return self._record(
            case_id=case_id,
            decision="ACCEPT",
            applied=True,
            reason="verification accepted the candidate; candidate persisted exactly",
            prior=prior,
            transition=transition,
            result=result,
            run_id=run_id,
            audit_event=audit_event,
            started=started,
        )

    # -- internals ---------------------------------------------------------
    def _restore_prior(self, scope: Mapping[str, str], prior: PriorState) -> None:
        try:
            self.adapter.replace_state(scope, prior.records)
        except Exception:
            # The decision record still reports applied=False; a non-atomic
            # backend must not be claimed as guarded.
            pass

    def _record(
        self,
        *,
        case_id: str,
        decision: str,
        applied: bool,
        reason: str,
        prior: PriorState,
        transition: Transition | None,
        result: Result | None,
        run_id: str,
        audit_event: dict[str, Any] | None,
        started: float,
        error_type: str = "",
    ) -> GuardedDecision:
        event = GuardedDecision(
            case_id=case_id,
            decision=decision,
            applied=applied,
            reason=reason,
            prior=prior,
            transition=transition,
            result=result,
            run_id=run_id,
            backend_name=str(getattr(self.adapter, "backend_name", "")),
            representation=self.representation,
            audit_event=audit_event,
            error_type=error_type,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._append_decision(event)
        return event

    def _append_decision(self, decision: GuardedDecision) -> None:
        self.decision_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        with self.decision_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def _last_audit_event(self, run_id: str) -> dict[str, Any] | None:
        try:
            lines = self.audit_log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("run_id") == run_id:
                return event
        return None
