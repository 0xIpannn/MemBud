"""Host-owned *staged write* wrapper around a real ``mem0ai==2.2.1`` ``Memory``.

This module turns the Phase 0 mapping bridge (:mod:`membud.adapters.mem0_bridge`)
into an end-to-end guarded integration with a real third-party memory backend.
It does **not** fork, patch, subclass, or monkeypatch Mem0, and it does not
depend on any Mem0 hook (there is none; see ``data/backend-feasibility.md``).

How it works
------------
For every guarded operation (``add`` / ``update`` / ``delete``) the wrapper:

1. **Snapshots** the complete host state: every Qdrant collection (memory *and*
   entity store, with vectors and payloads) and every SQLite table of Mem0's
   history database (``history`` and ``messages``) plus the table schemas.
2. **Stages**: builds a throw-away *shadow* Mem0 (same Python object, same
   configured LLM/embedder, but private in-memory Qdrant + SQLite loaded from
   the snapshot) and runs the **real, unmodified Mem0 method** on it.  Because
   the real code path runs, extraction, hash-dedup, entity linking, history,
   message persistence, and Mem0's own read-your-writes behaviour are exact.
3. **Diffs** the shadow state against the snapshot.  The diff is the complete set
   of pending host write side effects, derived from state rather than from a
   hand-maintained list of write calls, so an unknown extra write is detected,
   not silently missed.
4. **Classifies** the diff.  Memory-point changes become Mem0-style
   ``ADD``/``UPDATE``/``DELETE`` proposals for :class:`Mem0ProposalBridge`;
   entity/history/message changes must be *consistent consequences* of those
   proposals (history 1:1 with memory events, entity links only to existing
   memories, no foreign-scope mutation, no DDL, no unknown collection/table).
   Anything else is an ``UnmodeledSideEffect`` and the operation is REJECTED.
5. **Verifies** the transition through the existing reference
   :class:`GuardedMemory` (graph verifier).  The only object that can write to the
   live host is :class:`Mem0HostAdapter`, and only from ``replace_state``, which
   GuardedMemory calls solely after ``ACCEPT``.
6. **Commits** on ACCEPT: re-checks that the live state still equals the snapshot
   (stale write refused), applies exactly the verified change set (one SQLite
   transaction + Qdrant point operations), proves live == shadow-after by digest,
   and compensates with the inverse change set on any failure.
7. **HOLD / REJECTED / error**: the shadow is discarded.  The live Mem0 stores
   were never written to at any point; the live Mem0 object is never even
   handed to the extractor path (only read when the snapshot is taken).

What this is not
----------------
* Supported stack only: sync ``mem0.Memory`` with the ``qdrant`` vector store and
  Mem0's default SQLite history DB, release ``2.2.1`` (source hash pinned).
* Telemetry must be disabled (``MEM0_TELEMETRY=False``): Mem0 telemetry is an
  outward network side effect that cannot be staged.
* Concurrent writers are detected (stale-state refusal), not serialized.
* Crash atomicity across Qdrant and SQLite is best-effort compensation, not a
  distributed transaction; see ``docs/mem0-guarded-integration.md``.
* Verification only judges what MemBud models (claims, provenance, DELETE
  authorization).  It does not judge extraction quality beyond that.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..schemas import Record
from .base import AdapterContractError
from .mem0_bridge import (
    MEM0_RELEASE,
    MEM0_WHEEL_SHA256,
    Mem0BridgeError,
    Mem0ProposalBridge,
)
from .reference import GuardedDecision, GuardedMemory

#: SHA-256 of the pinned release's write-path sources inside the installed wheel
#: (``mem0ai-2.2.1-py3-none-any.whl``, wheel digest :data:`MEM0_WHEEL_SHA256`).
#: The wrapper refuses to run against any other source because the staging
#: argument is made against exactly this code.
MEM0_SOURCE_SHA256 = {
    "mem0/memory/main.py": "f329ca4a17af1fbefb32ac10d4cc7ae7ddbdb7435a87eb74c30915cff98b8b7c",
    "mem0/memory/storage.py": "5f5ecdadd2b260fd14f36a0f338336eb760275465dd1056a99a47e1ef07702bd",
    "mem0/vector_stores/qdrant.py": "da570579d5cad69e6f123fdfe32b725235a25604e70047666bec763ba6e2bf43",
}

_SCOPE_KEYS = ("user_id", "agent_id", "run_id")


class StagedWriteError(AdapterContractError):
    """The staged-write wrapper refused to proceed (configuration/contract error)."""


class UnmodeledSideEffect(StagedWriteError):
    """The staged run produced a host write that MemBud cannot account for."""


class StalePriorState(StagedWriteError):
    """Live host state changed between staging and commit."""


class PartialCommitError(StagedWriteError):
    """Commit failed and the compensating rollback could not restore the host."""


# ---------------------------------------------------------------------------
# Canonical host-state model
# ---------------------------------------------------------------------------


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _vector_json(vector: Any) -> Any:
    if vector is None:
        return None
    if isinstance(vector, Mapping):
        return {str(k): _vector_json(v) for k, v in vector.items()}
    if hasattr(vector, "model_dump"):  # qdrant SparseVector
        return vector.model_dump()
    return [float(x) for x in vector]


@dataclass(frozen=True)
class HostState:
    """Complete, comparable snapshot of every Mem0 store the wrapper manages.

    ``collections`` maps collection -> point id -> (vector json, payload json).
    ``params`` maps collection -> canonical vector-config json.
    ``tables`` maps table -> rowid -> canonical row json (``rowid`` included).
    ``schema`` maps sqlite object name -> its ``CREATE`` SQL.
    """

    collections: Mapping[str, Mapping[str, tuple[str, str]]]
    params: Mapping[str, str]
    tables: Mapping[str, Mapping[str, str]]
    schema: Mapping[str, str]

    def digest(self) -> str:
        """Digest of ids, payloads, collection params, rows and schema.

        Vector *values* are deliberately not hashed: Qdrant (cosine) re-normalizes
        on every upsert, which is not bit-idempotent, so a vector copied through
        the API can differ by a float32 ulp.  Vector shape is hashed and vector
        values are compared with a tolerance in :meth:`equivalent`.  (HOLD
        immutability does not rely on this digest: it is proven on raw file bytes.)
        """
        return _sha(
            {
                "collections": {
                    c: {k: [_vector_shape(v[0]), v[1]] for k, v in p.items()}
                    for c, p in self.collections.items()
                },
                "params": dict(self.params),
                "tables": {t: dict(r) for t, r in self.tables.items()},
                "schema": dict(self.schema),
            }
        )


VECTOR_TOLERANCE = 1e-5


def _vector_shape(vector_json: str) -> Any:
    raw = json.loads(vector_json)
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        return {
            name: (len(v) if isinstance(v, list) else {k: len(x) for k, x in v.items()})
            for name, v in raw.items()
        }
    return raw


def _numbers_close(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_numbers_close(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_numbers_close(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= VECTOR_TOLERANCE
    return a == b


def _point_equal(a: tuple[str, str], b: tuple[str, str]) -> bool:
    if a[1] != b[1]:
        return False
    if a[0] == b[0]:
        return True
    return _numbers_close(json.loads(a[0]), json.loads(b[0]))


def _collection_params(client: Any, name: str) -> Any:
    return client.get_collection(name).config.params


def _params_json(params: Any) -> str:
    vectors = params.vectors
    sparse = params.sparse_vectors
    return _canon(
        {
            "vectors": vectors.model_dump() if hasattr(vectors, "model_dump") else vectors,
            "sparse": {k: v.model_dump() for k, v in (sparse or {}).items()},
        }
    )


def _dump_points(client: Any, name: str) -> dict[str, tuple[str, str]]:
    points: dict[str, tuple[str, str]] = {}
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for record in records:
            points[str(record.id)] = (_canon(_vector_json(record.vector)), _canon(record.payload or {}))
        if offset is None:
            break
    return points


def dump_qdrant(client: Any) -> tuple[dict[str, dict[str, tuple[str, str]]], dict[str, str]]:
    collections: dict[str, dict[str, tuple[str, str]]] = {}
    params: dict[str, str] = {}
    for name in sorted(item.name for item in client.get_collections().collections):
        collections[name] = _dump_points(client, name)
        params[name] = _params_json(_collection_params(client, name))
    return collections, params


def dump_sqlite(connection: Any) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    schema = {
        row[0]: row[1] or ""
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    }
    tables: dict[str, dict[str, str]] = {}
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in names:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        rows: dict[str, str] = {}
        for row in connection.execute(f'SELECT rowid, * FROM "{table}" ORDER BY rowid'):
            values = list(row)
            for value in values:
                if isinstance(value, (bytes, bytearray)):
                    raise StagedWriteError(f"binary column values are not supported in table {table!r}")
            rows[str(values[0])] = _canon(dict(zip(["rowid", *columns], values)))
        tables[table] = rows
    return tables, schema


def dump_host_state(memory: Any) -> HostState:
    """Dump every store of a Mem0 ``Memory`` (live or shadow)."""
    client = memory.vector_store.client
    collections, params = dump_qdrant(client)
    lock = getattr(memory.db, "_lock", None)
    if lock is not None:
        with lock:
            tables, schema = dump_sqlite(memory.db.connection)
    else:
        tables, schema = dump_sqlite(memory.db.connection)
    return HostState(collections, params, tables, schema)


@dataclass(frozen=True)
class Change:
    """One pending host write side effect (derived from a state diff)."""

    store: str  # "qdrant" | "sqlite"
    container: str  # collection or table
    key: str  # point id or rowid
    kind: str  # "insert" | "update" | "delete"
    before: Any  # canonical json (vector, payload) / row json, or None
    after: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "store": self.store,
            "container": self.container,
            "key": self.key,
            "kind": self.kind,
            "before": self.before,
            "after": self.after,
        }


def diff_states(before: HostState, after: HostState) -> tuple[tuple[Change, ...], tuple[str, ...]]:
    """Return ``(changes, structural_problems)`` between two host states."""
    problems: list[str] = []
    if set(before.collections) != set(after.collections):
        problems.append(
            "qdrant collection set changed: "
            f"{sorted(set(before.collections) ^ set(after.collections))}"
        )
    for name in sorted(set(before.params) & set(after.params)):
        if before.params[name] != after.params[name]:
            problems.append(f"qdrant collection {name!r} parameters changed")
    if dict(before.schema) != dict(after.schema):
        problems.append("sqlite schema (DDL) changed")
    changes: list[Change] = []
    for name in sorted(set(before.collections) & set(after.collections)):
        old, new = before.collections[name], after.collections[name]
        for key in sorted(set(old) | set(new)):
            if key not in old:
                changes.append(Change("qdrant", name, key, "insert", None, new[key]))
            elif key not in new:
                changes.append(Change("qdrant", name, key, "delete", old[key], None))
            elif not _point_equal(old[key], new[key]):
                changes.append(Change("qdrant", name, key, "update", old[key], new[key]))
    for table in sorted(set(before.tables) & set(after.tables)):
        old_rows, new_rows = before.tables[table], after.tables[table]
        for key in sorted(set(old_rows) | set(new_rows), key=lambda item: int(item)):
            if key not in old_rows:
                changes.append(Change("sqlite", table, key, "insert", None, new_rows[key]))
            elif key not in new_rows:
                changes.append(Change("sqlite", table, key, "delete", old_rows[key], None))
            elif old_rows[key] != new_rows[key]:
                changes.append(Change("sqlite", table, key, "update", old_rows[key], new_rows[key]))
    if set(before.tables) != set(after.tables):
        problems.append(f"sqlite table set changed: {sorted(set(before.tables) ^ set(after.tables))}")
    return tuple(changes), tuple(problems)


def states_equivalent(left: HostState, right: HostState) -> bool:
    """Exact on ids/payloads/rows/schema; vectors equal within ``VECTOR_TOLERANCE``."""
    if left.digest() != right.digest():
        return False
    return not diff_states(left, right)[0]


def _describe_difference(left: HostState, right: HostState) -> str:
    changes, problems = diff_states(left, right)
    parts = list(problems) + [f"{c.store}:{c.container}:{c.key}:{c.kind}" for c in changes[:5]]
    return ", ".join(parts) or "no structural difference"


def changeset_digest(changes: Iterable[Change]) -> str:
    return _sha([change.to_dict() for change in changes])


def _invert(change: Change) -> Change:
    kind = {"insert": "delete", "delete": "insert", "update": "update"}[change.kind]
    return Change(change.store, change.container, change.key, kind, change.after, change.before)


def _point_id(key: str) -> str:
    try:
        return str(uuid.UUID(key))
    except ValueError as exc:
        raise StagedWriteError(f"unsupported (non-UUID) point id {key!r}") from exc


def _vector_from_json(raw: Any) -> Any:
    from qdrant_client import models

    if isinstance(raw, dict):
        out = {}
        for name, value in raw.items():
            if isinstance(value, dict) and set(value) == {"indices", "values"}:
                out[name] = models.SparseVector(indices=value["indices"], values=value["values"])
            else:
                out[name] = value
        return out
    return raw


def apply_changes(memory: Any, changes: Sequence[Change]) -> None:
    """Apply ``changes`` to ``memory``'s stores (qdrant points, then sqlite rows).

    SQLite rows are applied in a single transaction that is rolled back if any
    statement or the later Qdrant step fails; Qdrant operations are issued
    first so that a Qdrant failure never leaves a committed SQLite side.
    """
    from qdrant_client import models

    client = memory.vector_store.client
    by_collection: dict[str, tuple[list[Any], list[str]]] = {}
    for change in changes:
        if change.store != "qdrant":
            continue
        upserts, deletes = by_collection.setdefault(change.container, ([], []))
        if change.kind == "delete":
            deletes.append(_point_id(change.key))
        else:
            vector_json, payload_json = change.after
            upserts.append(
                models.PointStruct(
                    id=_point_id(change.key),
                    vector=_vector_from_json(json.loads(vector_json)),
                    payload=json.loads(payload_json),
                )
            )
    for name in sorted(by_collection):
        upserts, deletes = by_collection[name]
        if upserts:
            client.upsert(collection_name=name, points=upserts, wait=True)
        if deletes:
            client.delete(
                collection_name=name,
                points_selector=models.PointIdsList(points=deletes),
                wait=True,
            )

    sql_changes = [change for change in changes if change.store == "sqlite"]
    if not sql_changes:
        return
    lock = getattr(memory.db, "_lock", None) or threading.Lock()
    connection = memory.db.connection
    with lock:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for change in sql_changes:
                table = f'"{change.container}"'
                if change.kind == "delete":
                    connection.execute(f"DELETE FROM {table} WHERE rowid = ?", (int(change.key),))
                    continue
                row = json.loads(change.after)
                columns = list(row)
                if change.kind == "insert":
                    names = ", ".join(f'"{c}"' for c in columns)
                    marks = ", ".join("?" for _ in columns)
                    connection.execute(
                        f"INSERT INTO {table} ({names}) VALUES ({marks})", [row[c] for c in columns]
                    )
                else:
                    assignments = ", ".join(f'"{c}" = ?' for c in columns if c != "rowid")
                    connection.execute(
                        f"UPDATE {table} SET {assignments} WHERE rowid = ?",
                        [row[c] for c in columns if c != "rowid"] + [int(change.key)],
                    )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise


# ---------------------------------------------------------------------------
# Evidence (host policy)
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "a an the and or of to in on at for with by from is are was were be been being am i me my we our you your "
    "he she it his her its they them their this that these those as has have had do does did not no".split()
)


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    if len(token) > 3 and token.endswith("e"):
        token = token[:-1]
    return token


def _content_tokens(text: str) -> frozenset[str]:
    return frozenset(
        _stem(token) for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in _STOPWORDS
    )


@dataclass(frozen=True)
class LexicalGrounding:
    """Conservative deterministic host policy deciding which trusted source supports a claim.

    A claim is *supported* by a source iff every content token of the claim
    (lower-cased, stop-words removed, light suffix stemming) occurs in that
    source's tokens.  It never invents support: a hallucinated claim whose
    content words are absent from every trusted source gets no evidence and the
    verifier HOLDs it.  This is a stand-in for a real host grounding policy and
    is deliberately simple; swap in any callable with the same signature.
    """

    def __call__(
        self, claims: Sequence[str], sources: Sequence[tuple[str, str]]
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for index, (source_id, text) in enumerate(sources):
            tokens = _content_tokens(text)
            supported = [
                claim
                for claim in claims
                if _content_tokens(claim) and _content_tokens(claim) <= tokens
            ]
            if supported:
                evidence.append(
                    {
                        "id": f"ev-{index}",
                        "supports": supported,
                        "source": source_id,
                        "text": text,
                    }
                )
        return evidence


GroundingPolicy = Callable[[Sequence[str], Sequence[tuple[str, str]]], list[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Staged results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StagedOutcome:
    """Result of one guarded operation on the live Mem0."""

    operation: str
    decision: str  # "ACCEPT" | "HOLD" | "REJECTED"
    applied: bool
    reason: str
    case_id: str
    scope: dict[str, str]
    pre_state_digest: str
    post_state_digest: str  # digest of the *staged* (shadow) state, applied only on ACCEPT
    live_state_digest: str  # digest of the live host after this call
    changeset_digest: str
    changes: tuple[Change, ...]
    proposals: tuple[dict[str, Any], ...]
    staged_result: Any  # what real Mem0 returned in the shadow (persisted only if applied)
    guarded: GuardedDecision | None = None
    error_type: str = ""
    latency_ms: float = 0.0

    @property
    def mem0_result(self) -> Any:
        """Mem0's return value, only when it was actually persisted."""
        return self.staged_result if self.applied else None

    def summary(self) -> dict[str, Any]:
        counts = Counter((c.store, c.container, c.kind) for c in self.changes)
        return {
            "release": MEM0_RELEASE,
            "wheel_sha256": MEM0_WHEEL_SHA256,
            "operation": self.operation,
            "decision": self.decision,
            "applied": self.applied,
            "reason": self.reason,
            "case_id": self.case_id,
            "scope": self.scope,
            "error_type": self.error_type,
            "pre_state_digest": self.pre_state_digest,
            "post_state_digest": self.post_state_digest,
            "live_state_digest": self.live_state_digest,
            "changeset_digest": self.changeset_digest,
            "pending_side_effects": {
                f"{store}:{container}:{kind}": n for (store, container, kind), n in sorted(counts.items())
            },
            "proposals": list(self.proposals),
        }


@dataclass
class _Pending:
    scope: tuple[tuple[str, str], ...]
    pre_digest: str
    pre_state: HostState
    post_digest: str
    post_state: HostState
    changes: tuple[Change, ...]
    candidate: dict[str, tuple[str, ...]]
    order: tuple[str, ...]


# ---------------------------------------------------------------------------
# Host adapter (the only path that can write the live Mem0)
# ---------------------------------------------------------------------------


class Mem0HostAdapter:
    """``MemoryAdapter`` over a live Mem0 whose only write path is an armed staged commit.

    ``snapshot`` reads the memory collection (never writes).  ``replace_state``
    refuses unless the wrapper armed a staged change set *and* the records
    handed in by :class:`GuardedMemory` equal the verified staged candidate,
    the live state still equals the staging snapshot, and the applied result
    digest-matches the shadow.  Direct calls cannot write.
    """

    backend_name = f"mem0-{MEM0_RELEASE.split('==')[-1]}-qdrant-sqlite-staged"

    def __init__(self, memory: Any) -> None:
        self.memory = memory
        self._pending: _Pending | None = None
        self._order: dict[tuple[tuple[str, str], ...], tuple[str, ...]] = {}
        self.commits = 0

    @property
    def memory_collection(self) -> str:
        return self.memory.collection_name

    def arm(self, pending: _Pending) -> None:
        self._pending = pending

    def disarm(self) -> None:
        self._pending = None

    def snapshot(self, scope: Mapping[str, str]) -> tuple[Record, ...]:
        scope_pairs = tuple(sorted(scope.items()))
        points = _dump_points(self.memory.vector_store.client, self.memory_collection)
        found: dict[str, Record] = {}
        for key, (_, payload_json) in points.items():
            payload = json.loads(payload_json)
            if not all(payload.get(k) == v for k, v in scope_pairs):
                continue
            data = payload.get("data")
            if not isinstance(data, str) or not data:
                raise StagedWriteError(
                    f"memory {key!r} has no canonical text payload; refusing guarded writes on a "
                    "non-canonical store"
                )
            found[key] = Record(key, (data,))
        order = {key: index for index, key in enumerate(self._order.get(scope_pairs, ()))}
        return tuple(found[key] for key in sorted(found, key=lambda k: (order.get(k, len(order)), k)))

    def replace_state(self, scope: Mapping[str, str], records: Sequence[Record]) -> None:
        pending, self._pending = self._pending, None  # single use, even if this attempt fails
        if pending is None:
            raise AdapterContractError(
                "no staged Mem0 change is armed; direct writes to the live host are refused"
            )
        scope_pairs = tuple(sorted(scope.items()))
        if scope_pairs != pending.scope:
            raise AdapterContractError("replace_state scope does not match the staged scope")
        candidate = {record.id: tuple(record.claims) for record in records}
        if candidate != pending.candidate:
            raise AdapterContractError(
                "records to persist differ from the verified staged candidate; refusing"
            )
        live = dump_host_state(self.memory)
        if not states_equivalent(live, pending.pre_state):
            raise StalePriorState("live Mem0 state changed since staging; refusing stale commit")
        try:
            apply_changes(self.memory, pending.changes)
            after = dump_host_state(self.memory)
            if not states_equivalent(after, pending.post_state):
                raise StagedWriteError(
                    "live state after commit does not equal the staged state: "
                    + _describe_difference(after, pending.post_state)
                )
        except BaseException as exc:
            try:
                apply_changes(self.memory, [_invert(change) for change in reversed(pending.changes)])
                restored = states_equivalent(dump_host_state(self.memory), pending.pre_state)
            except BaseException:
                restored = False
            if not restored:
                raise PartialCommitError(
                    f"commit failed ({exc!r}) and compensation could not restore the pre-state"
                ) from exc
            raise
        self._order[scope_pairs] = pending.order
        self.commits += 1


# ---------------------------------------------------------------------------
# Shadow construction
# ---------------------------------------------------------------------------


class _Shadow:
    """A throw-away Mem0 sharing the live Mem0's logic/LLM/embedder but private stores."""

    def __init__(self, live: Any, pre: HostState) -> None:
        from mem0.memory.main import _safe_deepcopy_config
        from mem0.memory.storage import SQLiteManager
        from mem0.utils.factory import VectorStoreFactory
        from qdrant_client import QdrantClient, models

        live_client = live.vector_store.client
        client = QdrantClient(":memory:")
        for name in pre.collections:
            params = _collection_params(live_client, name)
            client.create_collection(
                collection_name=name,
                vectors_config=params.vectors,
                sparse_vectors_config=params.sparse_vectors,
            )
        for name, points in pre.collections.items():
            batch = [
                models.PointStruct(
                    id=_point_id(key),
                    vector=_vector_from_json(json.loads(vector_json)),
                    payload=json.loads(payload_json),
                )
                for key, (vector_json, payload_json) in points.items()
            ]
            for start in range(0, len(batch), 256):
                client.upsert(collection_name=name, points=batch[start : start + 256], wait=True)

        config = _safe_deepcopy_config(live.config.vector_store.config)
        config.client = client
        shadow = copy.copy(live)
        shadow.vector_store = VectorStoreFactory.create(live.config.vector_store.provider, config)
        shadow.vector_store.is_local = live.vector_store.is_local
        # Mem0 skips entity cleanup when its entity store was never initialized in
        # the process; the live Mem0 initialized it in StagedMem0.__init__, so the
        # shadow must be initialized too or its behaviour would diverge from live.
        shadow._entity_store = None
        _ = shadow.entity_store
        shadow.db = SQLiteManager(":memory:")
        lock = getattr(live.db, "_lock", None) or threading.Lock()
        with lock:
            live.db.connection.backup(shadow.db.connection)
        self.memory = shadow
        self._client = client
        if not states_equivalent(dump_host_state(shadow), pre):
            self.close()
            raise StagedWriteError(
                "shadow clone does not reproduce the live host snapshot (host changed while staging?)"
            )

    def close(self) -> None:
        try:
            self.memory.db.connection.close()
        except Exception:
            pass
        try:
            self._client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------


def _check_environment(memory: Any) -> None:
    import importlib.metadata as metadata
    import mem0
    from mem0.memory import telemetry
    from mem0.memory.main import Memory

    expected = MEM0_RELEASE.split("==", 1)[1]
    installed = metadata.version("mem0ai")
    if installed != expected:
        raise StagedWriteError(f"mem0ai {installed} installed; this wrapper is pinned to {expected}")
    root = Path(mem0.__file__).resolve().parent.parent
    for relative, digest in MEM0_SOURCE_SHA256.items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != digest:
            raise StagedWriteError(f"{relative} does not match the pinned {expected} source hash")
    if telemetry.MEM0_TELEMETRY:
        raise StagedWriteError(
            "Mem0 telemetry is enabled (an outward side effect that cannot be staged); "
            "set MEM0_TELEMETRY=False before importing mem0"
        )
    if type(memory) is not Memory:
        raise StagedWriteError("only the sync mem0.Memory class is supported (no subclass/AsyncMemory)")
    if memory.config.vector_store.provider != "qdrant":
        raise StagedWriteError("only the qdrant vector store is supported")
    if getattr(memory.vector_store, "client", None) is None:
        raise StagedWriteError("live vector store exposes no qdrant client")


def _scope_from_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    return {key: payload[key] for key in _SCOPE_KEYS if isinstance(payload.get(key), str) and payload.get(key)}


class StagedMem0:
    """Guarded ``add`` / ``update`` / ``delete`` over a real ``mem0.Memory``.

    Parameters
    ----------
    memory:
        A live, configured ``mem0.Memory`` (qdrant + SQLite history).  It is only
        written through :class:`Mem0HostAdapter` after an ACCEPT.
    workdir:
        Directory for MemBud's *own* files (shadow JSON store, audit log, decision
        log, staged-outcome log).  These are MemBud side effects, not host
        (Mem0) side effects; HOLD still appends an audit/decision record.
    grounding:
        Host evidence policy ``(claims, [(source_id, text)]) -> evidence dicts``.
    """

    def __init__(
        self,
        memory: Any,
        *,
        workdir: str | Path,
        grounding: GroundingPolicy | None = None,
        representation: str = "graph",
        trusted_roles: Sequence[str] = ("user",),
    ) -> None:
        _check_environment(memory)
        self.memory = memory
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.grounding = grounding or LexicalGrounding()
        self.trusted_roles = tuple(trusted_roles)
        # Force lazy entity-store creation now so that creating the entity
        # collection is a setup side effect, never a first-operation one.
        _ = memory.entity_store
        self.adapter = Mem0HostAdapter(memory)
        if representation not in {"graph", "text"}:
            raise ValueError("representation must be 'graph' or 'text'")
        self.representation = representation
        self.bridge = Mem0ProposalBridge()
        self.outcome_log_path = self.workdir / "staged-outcomes.jsonl"
        self._lock = threading.RLock()

    # -- public guarded API ------------------------------------------------
    def add(
        self,
        messages: Any,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
        prompt: str | None = None,
        evidence: Sequence[Mapping[str, Any]] | None = None,
        case_id: str | None = None,
    ) -> StagedOutcome:
        scope = {k: v for k, v in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)) if v}
        sources = self._message_sources(messages)

        def run(memory: Any) -> Any:
            return memory.add(
                messages,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                metadata=metadata,
                infer=infer,
                prompt=prompt,
            )

        return self._execute(
            "add", run, scope=scope, sources=sources, evidence=evidence, authorizations=None, case_id=case_id
        )

    def update(
        self,
        memory_id: str,
        text: str,
        *,
        source_text: str | None = None,
        evidence: Sequence[Mapping[str, Any]] | None = None,
        case_id: str | None = None,
    ) -> StagedOutcome:
        sources = [("update-source", source_text)] if source_text else []
        return self._execute(
            "update",
            lambda memory: memory.update(memory_id, text=text),
            scope=self._scope_of(memory_id),
            sources=sources,
            evidence=evidence,
            authorizations=None,
            case_id=case_id,
        )

    def delete(
        self,
        memory_id: str,
        *,
        authorization: Mapping[str, Any] | None,
        evidence: Sequence[Mapping[str, Any]] | None,
        case_id: str | None = None,
    ) -> StagedOutcome:
        return self._execute(
            "delete",
            lambda memory: memory.delete(memory_id),
            scope=self._scope_of(memory_id),
            sources=[],
            evidence=evidence,
            authorizations={memory_id: authorization} if authorization is not None else None,
            case_id=case_id,
        )

    # -- internals ---------------------------------------------------------
    def _scope_of(self, memory_id: str) -> dict[str, str]:
        existing = self.memory.vector_store.get(vector_id=memory_id)
        if existing is None:
            raise ValueError(f"Memory with id {memory_id} not found")
        scope = _scope_from_payload(existing.payload or {})
        if not scope:
            raise StagedWriteError(f"memory {memory_id!r} has no user_id/agent_id/run_id scope")
        return scope

    def _message_sources(self, messages: Any) -> list[tuple[str, str]]:
        if isinstance(messages, str):
            return [("messages[0]:user", messages)]
        sources: list[tuple[str, str]] = []
        if isinstance(messages, Sequence):
            for index, message in enumerate(messages):
                if (
                    isinstance(message, Mapping)
                    and message.get("role") in self.trusted_roles
                    and isinstance(message.get("content"), str)
                ):
                    sources.append((f"messages[{index}]:{message['role']}", message["content"]))
        return sources

    def _execute(
        self,
        operation: str,
        run: Callable[[Any], Any],
        *,
        scope: dict[str, str],
        sources: Sequence[tuple[str, str]],
        evidence: Sequence[Mapping[str, Any]] | None,
        authorizations: Mapping[str, Mapping[str, Any]] | None,
        case_id: str | None,
    ) -> StagedOutcome:
        started = time.perf_counter()
        with self._lock:
            pre = dump_host_state(self.memory)
            pre_digest = pre.digest()
            shadow = _Shadow(self.memory, pre)
            try:
                staged_result = run(shadow.memory)
                post = dump_host_state(shadow.memory)
            finally:
                shadow.close()
            post_digest = post.digest()
            changes, structural = diff_states(pre, post)
            cs_digest = changeset_digest(changes)
            case = case_id or f"mem0-{operation}-{_sha([operation, pre_digest, cs_digest])[:12]}"

            def finish(
                decision: str,
                reason: str,
                *,
                applied: bool = False,
                proposals: Sequence[dict[str, Any]] = (),
                guarded: GuardedDecision | None = None,
                error_type: str = "",
            ) -> StagedOutcome:
                outcome = StagedOutcome(
                    operation=operation,
                    decision=decision,
                    applied=applied,
                    reason=reason,
                    case_id=case,
                    scope=dict(scope),
                    pre_state_digest=pre_digest,
                    post_state_digest=post_digest,
                    live_state_digest=dump_host_state(self.memory).digest(),
                    changeset_digest=cs_digest,
                    changes=changes,
                    proposals=tuple(proposals),
                    staged_result=staged_result,
                    guarded=guarded,
                    error_type=error_type,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                self._append_outcome(outcome)
                return outcome

            try:
                analysis = self._analyze(pre, post, changes, structural, scope)
            except UnmodeledSideEffect as exc:
                return finish(
                    "REJECTED", f"unmodeled host side effect: {exc}", error_type="UnmodeledSideEffect"
                )

            claims = [p["memory"] for p in analysis.proposals if p["event"] in {"ADD", "UPDATE"}]
            evidence_items = (
                list(evidence) if evidence is not None else self.grounding(claims, list(sources))
            )
            # MemBud's JSON baseline store is per-operation and ephemeral: the
            # authoritative prior state is the live Mem0 (re-checked at commit),
            # and a long-lived single-state baseline would be wrong for a
            # multi-scope host and could go stale if a host commit fails.
            verify_dir = Path(tempfile.mkdtemp(prefix="verify-", dir=self.workdir))
            guard = GuardedMemory(
                self.adapter,
                store_path=verify_dir / "baseline.json",
                audit_log_path=self.workdir / "membud-audit.jsonl",
                decision_log_path=self.workdir / "guarded-decisions.jsonl",
                representation=self.representation,
            )
            try:
                try:
                    prior = guard.capture_prior(scope)
                    if {r.id: r.claims for r in prior.records} != analysis.prior_claims:
                        raise StagedWriteError("live scoped state differs from the staged snapshot")
                    staged = self.bridge.prepare_proposals(
                        scope=scope,
                        prior=prior,
                        proposals=analysis.proposals,
                        case_id=case,
                        evidence=evidence_items,
                        delete_authorizations=authorizations,
                    )
                except (Mem0BridgeError, StagedWriteError) as exc:
                    return finish(
                        "REJECTED",
                        f"invalid staged proposal: {exc}",
                        proposals=analysis.proposals,
                        error_type=type(exc).__name__,
                    )
                self.adapter.arm(
                    _Pending(
                        scope=tuple(sorted(scope.items())),
                        pre_digest=pre_digest,
                        pre_state=pre,
                        post_digest=post_digest,
                        post_state=post,
                        changes=changes,
                        candidate={r.id: tuple(r.claims) for r in staged.candidate},
                        order=tuple(r.id for r in staged.candidate),
                    )
                )
                try:
                    guarded = staged.apply(guard)
                finally:
                    self.adapter.disarm()
                return finish(
                    guarded.decision,
                    guarded.reason,
                    applied=guarded.applied,
                    proposals=analysis.proposals,
                    guarded=guarded,
                    error_type=guarded.error_type,
                )
            finally:
                shutil.rmtree(verify_dir, ignore_errors=True)

    def _append_outcome(self, outcome: StagedOutcome) -> None:
        line = json.dumps(outcome.summary(), ensure_ascii=False, sort_keys=True) + "\n"
        with self.outcome_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    # -- diff classification -----------------------------------------------
    def _analyze(
        self,
        pre: HostState,
        post: HostState,
        changes: Sequence[Change],
        structural: Sequence[str],
        scope: Mapping[str, str],
    ) -> "_Analysis":
        if structural:
            raise UnmodeledSideEffect("; ".join(structural))
        memory_collection = self.memory.collection_name
        entity_collection = self._entity_collection()
        scope_items = tuple(scope.items())

        def in_scope(payload: Mapping[str, Any]) -> bool:
            return all(payload.get(k) == v for k, v in scope_items)

        known_containers = {memory_collection, entity_collection, "history", "messages"}
        for change in changes:
            if change.container not in known_containers:
                raise UnmodeledSideEffect(f"write to unknown store {change.store}:{change.container}")

        prior_claims: dict[str, tuple[str, ...]] = {}
        for key, (_, payload_json) in pre.collections[memory_collection].items():
            payload = json.loads(payload_json)
            if in_scope(payload):
                data = payload.get("data")
                if isinstance(data, str) and data:
                    prior_claims[key] = (data,)
        candidate_claims = dict(prior_claims)

        proposals: list[dict[str, Any]] = []
        expected_history: Counter[tuple[Any, ...]] = Counter()
        changed_memory_ids: set[str] = set()
        for change in (c for c in changes if c.container == memory_collection):
            before = json.loads(change.before[1]) if change.before else None
            after = json.loads(change.after[1]) if change.after else None
            if before is not None and not in_scope(before) or after is not None and not in_scope(after):
                raise UnmodeledSideEffect(f"memory point {change.key} changed outside the operation scope")
            if change.kind == "update" and any(before.get(k) != after.get(k) for k in _SCOPE_KEYS):
                raise UnmodeledSideEffect(f"memory point {change.key} changed its scope identifiers")
            old = None if before is None else before.get("data")
            new = None if after is None else after.get("data")
            for label, value in (("old", old), ("new", new)):
                if value is not None and (not isinstance(value, str) or not value):
                    raise UnmodeledSideEffect(f"memory point {change.key} has a non-text {label} payload")
            changed_memory_ids.add(change.key)
            if change.kind == "insert":
                proposals.append({"event": "ADD", "id": change.key, "memory": new})
                candidate_claims[change.key] = (new,)
                expected_history[(change.key, "ADD", None, new)] += 1
            elif change.kind == "delete":
                proposals.append({"event": "DELETE", "id": change.key, "memory": old})
                candidate_claims.pop(change.key, None)
                expected_history[(change.key, "DELETE", old, None)] += 1
            else:
                if old != new:
                    proposals.append({"event": "UPDATE", "id": change.key, "memory": new})
                    candidate_claims[change.key] = (new,)
                expected_history[(change.key, "UPDATE", old, new)] += 1
        if not proposals:
            proposals.append({"event": "NONE"})

        # history must be exactly the 1:1 consequence of the memory events
        actual_history: Counter[tuple[Any, ...]] = Counter()
        for change in (c for c in changes if c.container == "history"):
            if change.kind != "insert":
                raise UnmodeledSideEffect(f"history row {change.key} was {change.kind}d; history is append-only")
            row = json.loads(change.after)
            actual_history[(row["memory_id"], row["event"], row["old_memory"], row["new_memory"])] += 1
        if actual_history != expected_history:
            raise UnmodeledSideEffect(
                "history rows are not the 1:1 consequence of the memory events "
                f"(expected {sorted(map(str, expected_history.elements()))}, "
                f"got {sorted(map(str, actual_history.elements()))})"
            )

        # entity links may only reference memories that exist after the commit
        all_memory_ids = set(post.collections[memory_collection])
        for change in (c for c in changes if c.container == entity_collection):
            for state in (change.before, change.after):
                if state is None:
                    continue
                payload = json.loads(state[1])
                if not in_scope(payload):
                    raise UnmodeledSideEffect(f"entity {change.key} changed outside the operation scope")
                if change.after is state:
                    linked = payload.get("linked_memory_ids", [])
                    if not isinstance(linked, list) or not set(linked) <= all_memory_ids:
                        raise UnmodeledSideEffect(f"entity {change.key} links to a non-existent memory")

        # raw messages: insert (and Mem0's own per-scope eviction of old rows) only
        inserted_scopes = {
            json.loads(c.after)["session_scope"] for c in changes if c.container == "messages" and c.kind == "insert"
        }
        for change in (c for c in changes if c.container == "messages"):
            if change.kind == "update":
                raise UnmodeledSideEffect(f"message row {change.key} was modified")
            if change.kind == "delete" and json.loads(change.before)["session_scope"] not in inserted_scopes:
                raise UnmodeledSideEffect(f"message row {change.key} was evicted outside the written session scope")

        return _Analysis(tuple(proposals), prior_claims, candidate_claims)

    def _entity_collection(self) -> str:
        from mem0.memory.main import _entity_collection_name

        return _entity_collection_name(self.memory.config.vector_store.provider, self.memory.collection_name)


@dataclass(frozen=True)
class _Analysis:
    proposals: tuple[dict[str, Any], ...]
    prior_claims: dict[str, tuple[str, ...]]
    candidate_claims: dict[str, tuple[str, ...]]


# ---------------------------------------------------------------------------
# Verification helper (used by tests and the evidence script)
# ---------------------------------------------------------------------------


def file_tree_digest(*roots: str | Path) -> dict[str, str]:
    """SHA-256 of every file under ``roots`` (relative path -> hex digest)."""
    digests: dict[str, str] = {}
    for root in roots:
        root = Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in files:
            name = path.name if root.is_file() else str(path.relative_to(root.parent))
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


__all__ = [
    "Change",
    "HostState",
    "LexicalGrounding",
    "MEM0_SOURCE_SHA256",
    "Mem0HostAdapter",
    "PartialCommitError",
    "StagedMem0",
    "StagedOutcome",
    "StagedWriteError",
    "StalePriorState",
    "UnmodeledSideEffect",
    "apply_changes",
    "changeset_digest",
    "diff_states",
    "states_equivalent",
    "dump_host_state",
    "file_tree_digest",
]
