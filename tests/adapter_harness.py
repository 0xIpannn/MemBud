"""Harnesses that give every guarded adapter the same operational surface for the contract suite.

A harness hides *how* a backend is driven (scripted LLM, store layout, guard construction) and
exposes only: guarded operations, the public view, an exhaustive write-surface fingerprint, and
fault injection.  The contract in ``test_adapter_contract.py`` is written against this interface.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCOPE = {"user_id": "u1"}
OTHER = {"user_id": "u2"}
SEED = ("Alice lives in Berlin", "Alice works at Acme Corp")


@dataclass
class Out:
    decision: str  # ACCEPT | HOLD | REJECTED | ERROR (backend exception propagated)
    applied: bool
    reason: str = ""
    error_type: str = ""


def say(*claims: str) -> list[dict[str, str]]:
    """User messages that support every given claim."""
    return [{"role": "user", "content": " ".join(f"{c}." for c in claims)}]


class Harness:
    name = "abstract"
    #: effects this adapter can detect when a native write performs them (capability matrix)
    detects: dict[str, bool] = {}

    # guarded ops -----------------------------------------------------------
    def add(self, claims: list[str], *, supported_by: list[str] | None = None) -> Out: ...
    def update(self, old: str, new: str, *, supported: bool) -> Out: ...
    def delete(self, text: str, *, authorized: bool) -> Out: ...
    # unguarded access ------------------------------------------------------
    def unguarded_add(self, text: str, scope: dict[str, str] = SCOPE) -> None: ...
    def texts(self, scope: dict[str, str] = SCOPE) -> list[str]: ...
    def surfaces(self) -> Any:
        """Fingerprint of EVERY write surface of the backend, bytes and logical (comparable with ==)."""
    def control_changes_surfaces(self) -> bool:
        """Run the same supported add UNGUARDED on an identical twin; True iff its surfaces changed."""
    # fault injection (context managers that restore on exit) -----------------
    def race(self): ...        # an unguarded writer commits between staging and commit
    def fail_write(self, *, permanent: bool): ...  # live backend write fails during commit
    def native_error(self): ...  # the backend's own write path raises while staging
    def tamper(self, kind: str): ...  # native write performs an extra, unmodeled effect
    def direct_replace(self) -> None: ...  # try to write through the guard's host adapter directly
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Stdlib fake backend, through the black-box adapter
# ---------------------------------------------------------------------------


class FakeBlackBox(Harness):
    name = "fake-blackbox"
    detects = {"foreign_scope": True, "history_rewrite": True, "schema_change": True, "hidden_surface": False}

    def __init__(self, root: Path) -> None:
        from blackbox_fakes import FakeStore, make_ops
        from membud.adapters import BlackBoxStaged

        self.root = root
        self._FakeStore, self._make_ops = FakeStore, make_ops
        self.store = FakeStore(root / "live.sqlite")
        self.twins = 0
        self.native_hook: Callable[[Any], None] | None = None
        self.llm_error = False
        self.guard = BlackBoxStaged(make_ops(self.store), self._factory, workdir=root / "membud")

    def _factory(self):
        self.twins += 1
        twin = self._FakeStore(self.root / f"twin{self.twins}.sqlite")

        def hook(t):
            if self.llm_error:
                raise RuntimeError("native write failed")
            if self.native_hook:
                self.native_hook(t)

        twin.native_hook = hook
        return self._make_ops(twin)

    @staticmethod
    def _out(o) -> Out:
        return Out(o.decision, o.applied, o.reason, o.error_type)

    def _run(self, fn) -> Out:
        try:
            return self._out(fn())
        except Exception as exc:  # backend error propagated; fail-closed means no write
            return Out("ERROR", False, repr(exc), type(exc).__name__)

    def add(self, claims, *, supported_by=None):
        # the fake's extractor splits the content into sentences; "hallucination" is modelled by
        # trusted evidence that covers only ``supported_by`` while the content carries ``claims``
        ev = None if supported_by is None else self._evidence(claims, supported_by)
        return self._run(lambda: self.guard.add(say(*claims), scope=SCOPE, evidence=ev))

    @staticmethod
    def _evidence(claims, supported_by):
        from membud.adapters.mem0_staged import LexicalGrounding

        return LexicalGrounding()(claims, [("messages[0]:user", " ".join(supported_by))]) if supported_by else []

    def _id(self, text):
        return next(r["id"] for r in self.store.list(SCOPE) if r["text"] == text)

    def update(self, old, new, *, supported):
        return self._run(lambda: self.guard.update(self._id(old), new, source_text=new if supported else "unrelated chatter"))

    def delete(self, text, *, authorized):
        mid = self._id(text)
        if authorized:
            return self._run(lambda: self.guard.delete(
                mid,
                authorization={"source": "chat:1", "evidence_id": "e1", "rationale": "user asked to forget"},
                evidence=[{"id": "e1", "supports": [], "source": "chat:1", "text": "please forget this"}],
            ))
        return self._run(lambda: self.guard.delete(mid, authorization=None, evidence=[]))

    def unguarded_add(self, text, scope=SCOPE):
        self.store.add(scope, text, raw=True)

    def texts(self, scope=SCOPE):
        return [r["text"] for r in self.store.list(scope)]

    def surfaces(self):
        return (hashlib.sha256(self.store.path.read_bytes()).hexdigest(), json.dumps(self.store.dump_all(), sort_keys=True))

    def control_changes_surfaces(self):
        ctl = self._FakeStore(self.root / "control.sqlite")
        before = json.dumps(ctl.dump_all(), sort_keys=True)
        ctl.add(SCOPE, "Alice lives in Berlin.", raw=False)
        changed = json.dumps(ctl.dump_all(), sort_keys=True) != before
        tables = ctl.dump_all()
        return changed and all(tables[t] for t in ("memories", "history", "hidden_index"))

    # fault injection
    def race(self):
        import contextlib

        @contextlib.contextmanager
        def cm():
            orig = self.guard.adapter.replace_state

            def racing(scope, records):
                self.unguarded_add("Intruder wrote this")
                return orig(scope, records)

            self.guard.adapter.replace_state = racing
            try:
                yield
            finally:
                self.guard.adapter.replace_state = orig

        return cm()

    def fail_write(self, *, permanent):
        import contextlib

        @contextlib.contextmanager
        def cm():
            start = self.store.writes
            if permanent:
                self.store.fail_from = start + 2
            else:
                self.store.fail_at = {start + 2}
            try:
                yield
            finally:
                self.store.fail_from, self.store.fail_at = None, set()

        return cm()

    def native_error(self):
        import contextlib

        @contextlib.contextmanager
        def cm():
            self.llm_error = True
            try:
                yield
            finally:
                self.llm_error = False

        return cm()

    def tamper(self, kind):
        import contextlib

        actions = {
            "foreign_scope": lambda t: t.add(OTHER, "planted", raw=True),
            "history_rewrite": lambda t: t.db.execute("UPDATE history SET new='tampered'"),
            "schema_change": lambda t: t.db.execute("CREATE TABLE side(x)"),
            "hidden_surface": lambda t: t.db.execute("INSERT INTO hidden_index VALUES('ghost','secret')"),
        }

        @contextlib.contextmanager
        def cm():
            self.native_hook = actions[kind]
            try:
                yield
            finally:
                self.native_hook = None

        return cm()

    def direct_replace(self):
        self.guard.adapter.replace_state(SCOPE, [])

    def close(self):
        self.store.close()


# ---------------------------------------------------------------------------
# Real Mem0 2.2.1 (offline model fakes), through the black-box adapter and through mem0_staged
# ---------------------------------------------------------------------------


class _Mem0Base(Harness):
    def __init__(self, root: Path) -> None:
        os.environ["MEM0_TELEMETRY"] = "False"
        os.environ.setdefault("MEM0_DIR", tempfile.mkdtemp(prefix="membud-mem0-dir-"))
        warnings.filterwarnings("ignore")
        from membud.adapters.mem0_offline import OfflineHost

        self.root = root
        self.OfflineHost = OfflineHost
        self.hosts = []
        self.host = self._host("live")

    def _host(self, name):
        h = self.OfflineHost(self.root / name)
        self.hosts.append(h)
        return h

    @staticmethod
    def _out(o) -> Out:
        return Out(o.decision, o.applied, o.reason, o.error_type)

    def _run(self, fn) -> Out:
        try:
            return self._out(fn())
        except Exception as exc:
            return Out("ERROR", False, repr(exc), type(exc).__name__)

    def _evidence(self, claims, supported_by):
        from membud.adapters.mem0_staged import LexicalGrounding

        return LexicalGrounding()(claims, [("messages[0]:user", " ".join(supported_by))]) if supported_by else []

    def add(self, claims, *, supported_by=None):
        if not getattr(self, "llm_down", False):
            self.host.llm.push(*claims)
        msgs = say(*(supported_by if supported_by is not None else claims))
        ev = None if supported_by is None else self._evidence(claims, supported_by)
        return self._run(lambda: self._guarded_add(msgs, ev))

    def update(self, old, new, *, supported):
        return self._run(lambda: self._guarded_update(self.host.ids(old), new, new if supported else "unrelated chatter"))

    def delete(self, text, *, authorized):
        mid = self.host.ids(text)
        auth = {"source": "chat:1", "evidence_id": "e1", "rationale": "user asked to forget"} if authorized else None
        ev = [{"id": "e1", "supports": [], "source": "chat:1", "text": "please forget this"}] if authorized else []
        return self._run(lambda: self._guarded_delete(mid, auth, ev))

    def unguarded_add(self, text, scope=SCOPE):
        self.host.memory.add(text, infer=False, **scope)

    def texts(self, scope=SCOPE):
        return sorted(i["memory"] for i in self.host.memory.get_all(filters=scope)["results"])

    def surfaces(self):
        return self.host.snapshot()

    def control_changes_surfaces(self):
        ctl = self._host("control")
        before = ctl.snapshot()
        ctl.llm.push(*SEED)
        ctl.memory.add(say(*SEED), user_id="u1")
        after = ctl.surfaces()
        return after != before[2] and all(after[k] != before[2][k] for k in ("membud_test", "history", "messages"))

    def race(self):
        import contextlib

        @contextlib.contextmanager
        def cm():
            orig = self.guard.adapter.replace_state

            def racing(scope, records):
                self.unguarded_add("Intruder wrote this")
                return orig(scope, records)

            self.guard.adapter.replace_state = racing
            try:
                yield
            finally:
                self.guard.adapter.replace_state = orig

        return cm()

    def fail_write(self, *, permanent):
        import contextlib

        @contextlib.contextmanager
        def cm():
            client = self.host.memory.vector_store.client
            real_upsert, real_delete = client.upsert, client.delete
            calls = {"n": 0}

            def flaky(collection_name, points, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2 or (permanent and calls["n"] > 2):
                    raise RuntimeError("injected write failure")
                return real_upsert(collection_name=collection_name, points=points, **kwargs)

            def broken_delete(*a, **k):
                raise RuntimeError("injected rollback failure")

            client.upsert = flaky
            if permanent:
                client.delete = broken_delete
            try:
                yield
            finally:
                client.upsert, client.delete = real_upsert, real_delete

        return cm()

    def native_error(self):
        import contextlib

        @contextlib.contextmanager
        def cm():
            self.llm_down = True  # nothing is queued, so real Mem0's extractor raises
            try:
                yield
            finally:
                self.llm_down = False

        return cm()

    def tamper(self, kind):
        import contextlib
        from mem0 import Memory
        from membud.adapters.mem0_offline import DIMS

        def foreign(memory):
            memory.vector_store.insert(
                vectors=[[0.5] * DIMS], ids=["00000000-0000-0000-0000-0000000000aa"],
                payloads=[{"data": "Mallory was here", "user_id": "u2"}],
            )

        def history(memory):
            memory.db.connection.execute("UPDATE history SET new_memory = 'tampered'")
            memory.db.connection.commit()

        def schema(memory):
            memory.db.connection.execute("CREATE TABLE exfil (x TEXT)")
            memory.db.connection.commit()

        def hidden(memory):  # a dangling entity link: invisible through get_all/history
            memory.entity_store.insert(
                vectors=[[0.25] * DIMS], ids=["00000000-0000-0000-0000-0000000000bb"],
                payloads=[{"data": "Phantom", "entity_type": "PROPER", "linked_memory_ids": ["no-such"], "user_id": "u1"}],
            )

        action = {"foreign_scope": foreign, "history_rewrite": history, "schema_change": schema, "hidden_surface": hidden}[kind]

        @contextlib.contextmanager
        def cm():
            original = Memory._add_to_vector_store

            def patched(self, *args, **kwargs):
                result = original(self, *args, **kwargs)
                infer = kwargs.get("infer", args[3] if len(args) > 3 else True)
                if infer:  # only the native (extraction) write, never raw replays/seeding
                    action(self)
                return result

            Memory._add_to_vector_store = patched
            try:
                yield
            finally:
                Memory._add_to_vector_store = original

        return cm()

    def direct_replace(self):
        self.guard.adapter.replace_state(SCOPE, [])

    def close(self):
        for h in self.hosts:
            h.close()


class Mem0BlackBox(_Mem0Base):
    name = "mem0-blackbox"
    # history is visible through Memory.history; DDL, extra collections and entity-store rows are not
    detects = {"foreign_scope": True, "history_rewrite": True, "schema_change": False, "hidden_surface": False}

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        from membud.adapters import BlackBoxStaged
        from membud.adapters.blackbox_backends import mem0_ops

        self.n = 0
        self.guard = BlackBoxStaged(
            mem0_ops(self.host.memory, [SCOPE, OTHER]),
            self._factory,
            workdir=root / "membud",
            scratch_closer=lambda ops: ops.handle_host.close(),
        )

    def _factory(self):
        from membud.adapters.blackbox_backends import mem0_ops

        self.n += 1
        twin = self.OfflineHost(self.root / f"twin{self.n}")
        twin.llm = self.host.llm
        twin.memory.llm = self.host.llm  # the twin's native write consumes the same scripted extraction
        ops = mem0_ops(twin.memory, [SCOPE, OTHER], name="mem0-twin")
        ops.handle_host = twin
        return ops

    def _guarded_add(self, msgs, ev):
        return self.guard.add(msgs, scope=SCOPE, evidence=ev)

    def _guarded_update(self, mid, new, source):
        return self.guard.update(mid, new, source_text=source)

    def _guarded_delete(self, mid, auth, ev):
        return self.guard.delete(mid, authorization=auth, evidence=ev)


class Mem0Staged(_Mem0Base):
    name = "mem0-staged"
    detects = {"foreign_scope": True, "history_rewrite": True, "schema_change": True, "hidden_surface": True}

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        from membud.adapters import StagedMem0

        self.guard = StagedMem0(self.host.memory, workdir=root / "membud")

    def _guarded_add(self, msgs, ev):
        return self.guard.add(msgs, user_id="u1", evidence=ev)

    def _guarded_update(self, mid, new, source):
        return self.guard.update(mid, new, source_text=source)

    def _guarded_delete(self, mid, auth, ev):
        return self.guard.delete(mid, authorization=auth, evidence=ev)


HARNESSES = {"fake-blackbox": FakeBlackBox, "mem0-blackbox": Mem0BlackBox, "mem0-staged": Mem0Staged}


from adapter_harness_langmem import LangMemBlackBox  # noqa: E402  (registered after the base classes exist)

HARNESSES["langmem-blackbox"] = LangMemBlackBox
