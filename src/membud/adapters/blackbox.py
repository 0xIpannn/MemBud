"""Backend-agnostic guarded writes through a black-box (public API only) adapter.

``StagedMem0`` needs the backend's storage layout (Qdrant points, SQLite tables).  This
module needs only what any memory system already exposes: an ``add``, a ``list`` and an
``update``/``delete`` callable (:class:`BlackBoxOps`).  Mechanism, per guarded write:

1. **Observe** the live backend through ``list`` (all scopes), plus the optional ``schema``
   and ``history`` callables.  The live backend is only ever *read* until ACCEPT.
2. **Stage on a scratch twin.**  ``scratch_factory()`` returns a fresh, empty, isolated
   instance (a second constructor call on the same public API; no fork, no internals).  The
   twin is seeded from the observation with raw ``add`` calls and must observe identically
   (same scope/text/extra multiset) or the write is refused.
3. **Run the native write on the twin** (``add(..., raw=False)``, i.e. the backend's own
   extraction path, or ``update`` / ``delete``) and observe again.
4. **Diff** twin-after vs twin-before.  The diff, not a list of call names, is the side-effect
   set.  It must be a modelled one: only ADD/UPDATE/DELETE of in-scope memory text; nothing
   in a foreign scope; no schema change; history append-only; no change of record metadata
   beyond the modelled records.  Otherwise the write is REJECTED (``UnmodeledSideEffect``).
5. **Verify** with the existing ``Mem0ProposalBridge`` + ``GuardedMemory`` path (the bridge is
   a pure mapping layer; its release pin concerns Mem0's *event vocabulary* only).
6. **Commit, ACCEPT only.**  ``BlackBoxHostAdapter.replace_state`` is the only code that
   writes the live backend.  It requires an armed, single-use change set equal to the verified
   candidate, re-observes the live backend and refuses if it differs from the staging
   observation (stale), replays exactly the verified set through the public raw ``add`` /
   ``update`` / ``delete``, checks the result, and applies compensating writes on failure.

What this CANNOT protect against (see also ``data/adapter-contract.md``):

* Side effects invisible through the public API (vector indexes, entity/graph stores,
  caches, internal history if ``history`` is not supplied, telemetry, network calls) are
  neither detected nor replayed.  They happen on the twin and are discarded; on commit the
  live backend regenerates its own from the *raw* replay, which may differ from what the
  native path would have produced.
* Twin divergence: a twin seeded through public raw adds does not carry hidden state
  (dedupe hashes, embeddings of other memories, rate/cost counters), so the native write may
  behave differently on the twin than it would have live.  What is verified is the twin's
  visible result, and that is exactly what is replayed.
* Compensation restores the *public* state (texts, scope, metadata).  Record ids re-created
  by compensation are new, and hidden surfaces touched by a partly failed commit are not
  rolled back.  Public-API stores are not transactional: a crash mid-commit can leave a
  partial write.
* Concurrent writers are detected (stale check at commit), not serialized.
* Bypass: anyone holding the raw backend can still write it.
* Cost is O(store size) per guarded write (observe + seed + diff).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import Record
from .base import AdapterContractError
from .mem0_bridge import Mem0BridgeError, Mem0ProposalBridge
from .mem0_staged import GroundingPolicy, LexicalGrounding
from .reference import GuardedDecision, GuardedMemory

__all__ = [
    "BlackBoxHostAdapter",
    "BlackBoxOps",
    "BlackBoxOutcome",
    "BlackBoxStaged",
    "Observation",
    "BlackBoxError",
    "StaleObservation",
    "TwinSeedError",
    "UnmodeledChange",
    "PartialCommit",
    "observe",
]


class BlackBoxError(AdapterContractError):
    """Base class for black-box adapter refusals."""


class UnmodeledChange(BlackBoxError):
    """The staged write changed something the guard does not model."""


class StaleObservation(BlackBoxError):
    """The live backend changed between staging and commit."""


class TwinSeedError(BlackBoxError):
    """The scratch twin could not be seeded to observe identically to the live backend."""


class PartialCommit(BlackBoxError):
    """A commit failed and compensation could not restore the pre-state."""


# ---------------------------------------------------------------------------
# The three callables
# ---------------------------------------------------------------------------


@dataclass
class BlackBoxOps:
    """The whole public surface the guard needs from one backend instance.

    ``add(scope, content, *, raw, extra=None)``
        ``raw=True``: insert ``content`` (a string) verbatim as one memory, no model call;
        return its id (``str``, ``{"id": ...}`` or a one-element list of either).
        ``raw=False``: the backend's *native* write for ``content`` (messages or text),
        including whatever extraction/dedup/merge it does; the return value is ignored (the
        diff is the truth).
    ``list(scope_or_None)``
        every memory in ``scope`` (``None`` = every scope) as mappings with ``id``, ``text``,
        ``scope`` and optional ``extra`` (JSON-serialisable *stable* metadata: exclude
        timestamps and other fields that legitimately differ between instances).
    ``modify(op, memory_id, text=None)``
        ``op`` is ``"update"`` (set text) or ``"delete"``.
    ``schema()`` / ``history(memory_id)``
        optional.  ``schema`` returns any JSON describing collections/config; ``history``
        returns the ordered, append-only audit entries of one memory.  When absent, schema and
        history changes are undetectable and this is stated in the evidence.
    """

    add: Callable[..., Any]
    list: Callable[[Mapping[str, str] | None], Sequence[Mapping[str, Any]]]
    modify: Callable[..., Any]
    schema: Callable[[], Any] | None = None
    history: Callable[[str], Sequence[Any]] | None = None
    name: str = "blackbox"
    handle: Any = None  # the backend object behind these callables (for ``scratch_closer`` etc.)


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Item:
    id: str
    text: str
    scope: tuple[tuple[str, str], ...]
    extra: str  # canonical JSON

    def scope_dict(self) -> dict[str, str]:
        return dict(self.scope)

    def content_key(self) -> tuple[Any, ...]:
        return (self.scope, self.text, self.extra)


@dataclass(frozen=True)
class Observation:
    items: Mapping[str, Item]
    schema: str
    history: Mapping[str, tuple[str, ...]]

    def digest(self) -> str:
        return _sha(
            {
                "items": {k: [v.scope, v.text, v.extra] for k, v in sorted(self.items.items())},
                "schema": self.schema,
                "history": {k: list(v) for k, v in sorted(self.history.items())},
            }
        )

    def in_scope(self, scope: Mapping[str, str]) -> dict[str, Item]:
        want = tuple(scope.items())
        return {k: v for k, v in self.items.items() if all(v.scope_dict().get(a) == b for a, b in want)}

    def multiset(self) -> Counter:
        return Counter(item.content_key() for item in self.items.values())


def _normalize_item(raw: Mapping[str, Any]) -> Item:
    if not isinstance(raw, Mapping):
        raise BlackBoxError(f"list() entries must be mappings, got {type(raw).__name__}")
    rid, text, scope = raw.get("id"), raw.get("text"), raw.get("scope")
    if not isinstance(rid, str) or not rid:
        raise BlackBoxError("list() entry has no string id")
    if not isinstance(text, str) or not text:
        raise BlackBoxError(f"memory {rid!r} has no canonical text; refusing a non-canonical store")
    if not isinstance(scope, Mapping) or not scope:
        raise BlackBoxError(f"memory {rid!r} has no scope; unscoped memories cannot be guarded")
    pairs = tuple(sorted((str(k), str(v)) for k, v in scope.items()))
    return Item(rid, text, pairs, _canon(raw.get("extra")))


def observe(ops: BlackBoxOps, scopes: Sequence[Mapping[str, str]] | None = None) -> Observation:
    """Read the whole observable state through the public API (never writes)."""
    if scopes is None:
        entries = list(ops.list(None))
    else:
        entries, seen = [], set()
        for scope in scopes:
            for entry in ops.list(scope):
                if entry.get("id") not in seen:
                    seen.add(entry.get("id"))
                    entries.append(entry)
    items: dict[str, Item] = {}
    for raw in entries:
        item = _normalize_item(raw)
        if item.id in items:
            raise BlackBoxError(f"duplicate memory id {item.id!r} in list()")
        items[item.id] = item
    schema = _canon(ops.schema()) if ops.schema is not None else ""
    history: dict[str, tuple[str, ...]] = {}
    if ops.history is not None:
        for rid in items:
            history[rid] = tuple(_canon(entry) for entry in ops.history(rid))
    return Observation(items, schema, history)


def _new_id(result: Any) -> str:
    if isinstance(result, (list, tuple)):
        if len(result) != 1:
            raise BlackBoxError(f"raw add must create exactly one memory, returned {len(result)}")
        result = result[0]
    if isinstance(result, Mapping):
        result = result.get("id")
    if not isinstance(result, str) or not result:
        raise BlackBoxError("raw add did not return the new memory id")
    return result


# ---------------------------------------------------------------------------
# Staged change set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    kind: str  # "add" | "update" | "delete"
    live_id: str  # live id for update/delete; twin id (provisional) for add
    scope: tuple[tuple[str, str], ...]
    old_text: str | None
    new_text: str | None
    old_extra: str
    new_extra: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.live_id,
            "scope": dict(self.scope),
            "old_text": self.old_text,
            "new_text": self.new_text,
        }


@dataclass
class _Pending:
    scope: tuple[tuple[str, str], ...]
    pre: Observation
    changes: tuple[Change, ...]
    candidate: dict[str, tuple[str, ...]]  # keys: live id, or "staged:<twin id>" for adds
    id_map: dict[str, str] = field(default_factory=dict)  # staged id -> real live id after commit


def _diff(pre: Observation, post: Observation, scope: Mapping[str, str]) -> tuple[Change, ...]:
    """Derive the modelled change set or raise ``UnmodeledChange``.  Ids are the twin's."""
    want = tuple(scope.items())

    def inside(item: Item) -> bool:
        return all(item.scope_dict().get(a) == b for a, b in want)

    if pre.schema != post.schema:
        raise UnmodeledChange("schema/config changed during the staged write")
    changes: list[Change] = []
    for rid in sorted(set(pre.items) | set(post.items)):
        before, after = pre.items.get(rid), post.items.get(rid)
        if before == after:
            continue
        for item in (before, after):
            if item is not None and not inside(item):
                raise UnmodeledChange(f"memory {rid!r} changed outside the operation scope {dict(scope)}")
        if before is not None and after is not None:
            if before.scope != after.scope:
                raise UnmodeledChange(f"memory {rid!r} changed its scope identifiers")
            if before.extra != after.extra:
                raise UnmodeledChange(f"memory {rid!r} changed metadata, which is not modelled")
            changes.append(Change("update", rid, before.scope, before.text, after.text, before.extra, after.extra))
        elif after is not None:
            changes.append(Change("add", rid, after.scope, None, after.text, "", after.extra))
        else:
            assert before is not None
            changes.append(Change("delete", rid, before.scope, before.text, None, before.extra, ""))
    touched = {c.live_id for c in changes}
    # history: append-only, and only for touched memories
    for rid, entries in pre.history.items():
        later = post.history.get(rid)
        if later is None:
            if rid in post.items:
                raise UnmodeledChange(f"history of {rid!r} disappeared")
            continue
        if later[: len(entries)] != entries:
            raise UnmodeledChange(f"history of {rid!r} was rewritten (not append-only)")
        if len(later) != len(entries) and rid not in touched:
            raise UnmodeledChange(f"history of unchanged memory {rid!r} grew")
    for rid, entries in post.history.items():
        if rid not in pre.history and entries and rid not in touched:
            raise UnmodeledChange(f"history appeared for unchanged memory {rid!r}")
    return tuple(changes)


# ---------------------------------------------------------------------------
# Host adapter (only writer of the live backend)
# ---------------------------------------------------------------------------


class BlackBoxHostAdapter:
    """``MemoryAdapter`` over a live backend whose only write path is an armed commit."""

    def __init__(self, ops: BlackBoxOps, scopes: Sequence[Mapping[str, str]] | None = None) -> None:
        self.ops = ops
        self.scopes = scopes
        self.backend_name = f"blackbox:{ops.name}"
        self._pending: _Pending | None = None
        # real id -> provisional id, valid only between a successful commit and disarm(), so
        # GuardedMemory's post-apply equality check sees the ids the verified candidate used
        self._alias: dict[str, str] = {}
        self.commits = 0

    def arm(self, pending: _Pending) -> None:
        self._pending = pending

    def disarm(self) -> None:
        self._pending = None
        self._alias = {}

    def snapshot(self, scope: Mapping[str, str]) -> tuple[Record, ...]:
        items = observe(self.ops, self.scopes).in_scope(scope)
        return tuple(
            Record(self._alias.get(i.id, i.id), (i.text,)) for i in sorted(items.values(), key=lambda i: i.id)
        )

    def replace_state(self, scope: Mapping[str, str], records: Sequence[Record]) -> None:
        pending, self._pending = self._pending, None  # single use even if this attempt fails
        if pending is None:
            raise AdapterContractError("no staged change is armed; direct writes to the live backend are refused")
        if tuple(sorted(scope.items())) != pending.scope:
            raise AdapterContractError("replace_state scope does not match the staged scope")
        if {r.id: tuple(r.claims) for r in records} != pending.candidate:
            raise AdapterContractError("records to persist differ from the verified candidate; refusing")
        live = observe(self.ops, self.scopes)
        if live.digest() != pending.pre.digest():
            raise StaleObservation("live backend changed since staging; refusing a stale commit")
        undo: list[Callable[[], None]] = []
        created: dict[str, str] = {}
        try:
            for change in pending.changes:
                if change.kind == "delete":
                    self.ops.modify("delete", change.live_id)
                    undo.append(lambda c=change: self.ops.add(dict(c.scope), c.old_text, raw=True, extra=json.loads(c.old_extra)))
                elif change.kind == "update":
                    self.ops.modify("update", change.live_id, change.new_text)
                    undo.append(lambda c=change: self.ops.modify("update", c.live_id, c.old_text))
                else:
                    new_id = _new_id(
                        self.ops.add(dict(change.scope), change.new_text, raw=True, extra=json.loads(change.new_extra))
                    )
                    created[f"staged:{change.live_id}"] = new_id
                    undo.append(lambda i=new_id: self.ops.modify("delete", i))
            after = observe(self.ops, self.scopes)
            self._check_result(pending, live, after, created)
        except BaseException as exc:
            restored = False
            try:
                for action in reversed(undo):
                    action()
                restored = self._same_public_state(observe(self.ops, self.scopes), live)
            except BaseException:
                restored = False
            if not restored:
                raise PartialCommit(f"commit failed ({exc!r}) and compensation could not restore the pre-state") from exc
            raise
        pending.id_map = created
        self._alias = {real: staged for staged, real in created.items()}
        self.commits += 1

    @staticmethod
    def _same_public_state(a: Observation, b: Observation) -> bool:
        # ids of re-created records legitimately differ after compensation
        return a.multiset() == b.multiset() and a.schema == b.schema

    @staticmethod
    def _check_result(pending: _Pending, pre: Observation, after: Observation, created: Mapping[str, str]) -> None:
        if after.schema != pre.schema:
            raise BlackBoxError("schema changed during commit")
        expected = {k: v for k, v in pre.items.items() if k not in {c.live_id for c in pending.changes if c.kind != "add"}}
        for change in pending.changes:
            if change.kind == "update":
                base = pre.items[change.live_id]
                expected[change.live_id] = Item(base.id, change.new_text or "", base.scope, base.extra)
            elif change.kind == "add":
                rid = created[f"staged:{change.live_id}"]
                expected[rid] = Item(rid, change.new_text or "", change.scope, change.new_extra)
        got = {k: v for k, v in after.items.items()}
        if got != expected:
            raise BlackBoxError("live state after commit does not equal the verified staged state")


# ---------------------------------------------------------------------------
# Outcome + guard
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlackBoxOutcome:
    operation: str
    decision: str  # "ACCEPT" | "HOLD" | "REJECTED"
    applied: bool
    reason: str
    case_id: str
    scope: dict[str, str]
    pre_digest: str
    live_digest: str
    changes: tuple[Change, ...]
    proposals: tuple[dict[str, Any], ...]
    id_map: dict[str, str]
    guarded: GuardedDecision | None = None
    error_type: str = ""
    latency_ms: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "decision": self.decision,
            "applied": self.applied,
            "reason": self.reason,
            "case_id": self.case_id,
            "scope": self.scope,
            "error_type": self.error_type,
            "pre_digest": self.pre_digest,
            "live_digest": self.live_digest,
            "pending_side_effects": dict(sorted(Counter(c.kind for c in self.changes).items())),
            "changes": [c.to_dict() for c in self.changes],
            "id_map": self.id_map,
        }


def _message_sources(messages: Any, trusted_roles: Sequence[str]) -> list[tuple[str, str]]:
    if isinstance(messages, str):
        return [("messages[0]:user", messages)]
    sources: list[tuple[str, str]] = []
    if isinstance(messages, Sequence):
        for index, message in enumerate(messages):
            if (
                isinstance(message, Mapping)
                and message.get("role") in trusted_roles
                and isinstance(message.get("content"), str)
            ):
                sources.append((f"messages[{index}]:{message['role']}", message["content"]))
    return sources


class BlackBoxStaged:
    """Guarded ``add`` / ``update`` / ``delete`` over any backend reachable through ``BlackBoxOps``.

    ``live`` is the real backend; ``scratch_factory`` builds a fresh isolated twin of the same
    kind (same public API) for each staged write.  ``scopes`` optionally restricts observation
    to known scopes for backends that cannot list every scope (foreign-scope writes outside the
    list are then undetectable).
    """

    def __init__(
        self,
        live: BlackBoxOps,
        scratch_factory: Callable[[], BlackBoxOps],
        *,
        workdir: str | Path,
        grounding: GroundingPolicy | None = None,
        representation: str = "graph",
        trusted_roles: Sequence[str] = ("user",),
        scopes: Sequence[Mapping[str, str]] | None = None,
        scratch_closer: Callable[[BlackBoxOps], None] | None = None,
    ) -> None:
        self.live = live
        self.scratch_factory = scratch_factory
        self.scratch_closer = scratch_closer
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.grounding = grounding or LexicalGrounding()
        self.trusted_roles = tuple(trusted_roles)
        self.scopes = scopes
        if representation not in {"graph", "text"}:
            raise ValueError("representation must be 'graph' or 'text'")
        self.representation = representation
        self.adapter = BlackBoxHostAdapter(live, scopes)
        self.bridge = Mem0ProposalBridge()
        self.outcome_log_path = self.workdir / "blackbox-outcomes.jsonl"
        self._lock = threading.RLock()

    # -- public guarded API ------------------------------------------------
    def add(
        self,
        content: Any,
        *,
        scope: Mapping[str, str],
        evidence: Sequence[Mapping[str, Any]] | None = None,
        case_id: str | None = None,
    ) -> BlackBoxOutcome:
        sources = _message_sources(content, self.trusted_roles)
        return self._execute(
            "add",
            lambda ops, ids: ops.add(dict(scope), content, raw=False),
            scope=dict(scope),
            sources=sources,
            evidence=evidence,
            authorizations=None,
            case_id=case_id,
        )

    def update(
        self,
        memory_id: str,
        text: str,
        *,
        source_text: str | None = None,
        evidence: Sequence[Mapping[str, Any]] | None = None,
        case_id: str | None = None,
    ) -> BlackBoxOutcome:
        return self._execute(
            "update",
            lambda ops, ids: ops.modify("update", ids[memory_id], text),
            scope=self._scope_of(memory_id),
            sources=[("update-source", source_text)] if source_text else [],
            evidence=evidence,
            authorizations=None,
            case_id=case_id,
            target=memory_id,
        )

    def delete(
        self,
        memory_id: str,
        *,
        authorization: Mapping[str, Any] | None,
        evidence: Sequence[Mapping[str, Any]] | None,
        case_id: str | None = None,
    ) -> BlackBoxOutcome:
        return self._execute(
            "delete",
            lambda ops, ids: ops.modify("delete", ids[memory_id]),
            scope=self._scope_of(memory_id),
            sources=[],
            evidence=evidence,
            authorizations={memory_id: authorization} if authorization is not None else None,
            case_id=case_id,
            target=memory_id,
        )

    # -- internals ---------------------------------------------------------
    def _scope_of(self, memory_id: str) -> dict[str, str]:
        item = observe(self.live, self.scopes).items.get(memory_id)
        if item is None:
            raise ValueError(f"memory {memory_id!r} not found")
        return item.scope_dict()

    def _seed(self, twin: BlackBoxOps, pre: Observation) -> dict[str, str]:
        """Copy ``pre`` into the twin via raw adds; return live id -> twin id."""
        ids: dict[str, str] = {}
        for rid in sorted(pre.items):
            item = pre.items[rid]
            ids[rid] = _new_id(twin.add(item.scope_dict(), item.text, raw=True, extra=json.loads(item.extra)))
        seeded = observe(twin, self.scopes)
        if seeded.multiset() != pre.multiset():
            raise TwinSeedError("scratch twin does not observe identically to the live backend after seeding")
        return ids

    def _execute(
        self,
        operation: str,
        run: Callable[[BlackBoxOps, Mapping[str, str]], Any],
        *,
        scope: dict[str, str],
        sources: Sequence[tuple[str, str]],
        evidence: Sequence[Mapping[str, Any]] | None,
        authorizations: Mapping[str, Mapping[str, Any]] | None,
        case_id: str | None,
        target: str | None = None,
    ) -> BlackBoxOutcome:
        started = time.perf_counter()
        with self._lock:
            pre = observe(self.live, self.scopes)
            pre_digest = pre.digest()
            case = case_id or f"bb-{operation}-{_sha([operation, pre_digest, scope, sources])[:12]}"

            def finish(
                decision: str,
                reason: str,
                *,
                applied: bool = False,
                changes: Sequence[Change] = (),
                proposals: Sequence[dict[str, Any]] = (),
                guarded: GuardedDecision | None = None,
                error_type: str = "",
                id_map: Mapping[str, str] | None = None,
            ) -> BlackBoxOutcome:
                outcome = BlackBoxOutcome(
                    operation=operation,
                    decision=decision,
                    applied=applied,
                    reason=reason,
                    case_id=case,
                    scope=dict(scope),
                    pre_digest=pre_digest,
                    live_digest=observe(self.live, self.scopes).digest(),
                    changes=tuple(changes),
                    proposals=tuple(proposals),
                    id_map=dict(id_map or {}),
                    guarded=guarded,
                    error_type=error_type,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                self._append_outcome(outcome)
                return outcome

            twin = self.scratch_factory()
            try:
                try:
                    ids = self._seed(twin, pre)
                except BlackBoxError as exc:
                    return finish("REJECTED", f"scratch twin unusable: {exc}", error_type=type(exc).__name__)
                if target is not None and target not in ids:
                    raise ValueError(f"memory {target!r} not found")
                before = observe(twin, self.scopes)
                run(twin, ids)  # backend errors propagate; the live backend was never written
                after = observe(twin, self.scopes)
            finally:
                if self.scratch_closer is not None:
                    self.scratch_closer(twin)

            twin_to_live = {v: k for k, v in ids.items()}
            try:
                twin_changes = _diff(before, after, scope)
            except UnmodeledChange as exc:
                return finish("REJECTED", f"unmodeled side effect: {exc}", error_type="UnmodeledChange")
            changes = tuple(
                Change(
                    c.kind,
                    twin_to_live.get(c.live_id, c.live_id),
                    c.scope,
                    c.old_text,
                    c.new_text,
                    c.old_extra,
                    c.new_extra,
                )
                for c in twin_changes
            )

            proposals: list[dict[str, Any]] = []
            candidate: dict[str, tuple[str, ...]] = {
                r.id: tuple(r.claims) for r in self.adapter.snapshot(scope)
            }
            for c in changes:
                if c.kind == "add":
                    pid = f"staged:{c.live_id}"
                    proposals.append({"event": "ADD", "id": pid, "memory": c.new_text})
                    candidate[pid] = (c.new_text or "",)
                elif c.kind == "update":
                    if c.old_text != c.new_text:
                        proposals.append({"event": "UPDATE", "id": c.live_id, "memory": c.new_text})
                        candidate[c.live_id] = (c.new_text or "",)
                else:
                    proposals.append({"event": "DELETE", "id": c.live_id, "memory": c.old_text})
                    candidate.pop(c.live_id, None)
            if not proposals:
                proposals.append({"event": "NONE"})

            claims = [p["memory"] for p in proposals if p["event"] in {"ADD", "UPDATE"}]
            evidence_items = list(evidence) if evidence is not None else self.grounding(claims, list(sources))
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
                    staged = self.bridge.prepare_proposals(
                        scope=scope,
                        prior=prior,
                        proposals=proposals,
                        case_id=case,
                        evidence=evidence_items,
                        delete_authorizations=authorizations,
                    )
                except (Mem0BridgeError, BlackBoxError) as exc:
                    return finish(
                        "REJECTED",
                        f"invalid staged proposal: {exc}",
                        changes=changes,
                        proposals=proposals,
                        error_type=type(exc).__name__,
                    )
                pending = _Pending(
                    scope=tuple(sorted(scope.items())),
                    pre=pre,
                    changes=changes,
                    candidate={r.id: tuple(r.claims) for r in staged.candidate},
                )
                self.adapter.arm(pending)
                try:
                    guarded = staged.apply(guard)
                finally:
                    self.adapter.disarm()
                return finish(
                    guarded.decision,
                    guarded.reason,
                    applied=guarded.applied,
                    changes=changes,
                    proposals=proposals,
                    guarded=guarded,
                    error_type=guarded.error_type,
                    id_map=pending.id_map if guarded.applied else None,
                )
            finally:
                shutil.rmtree(verify_dir, ignore_errors=True)

    def _append_outcome(self, outcome: BlackBoxOutcome) -> None:
        line = json.dumps(outcome.summary(), ensure_ascii=False, sort_keys=True) + "\n"
        with self.outcome_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
