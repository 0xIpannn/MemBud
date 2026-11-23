"""Harness: real ``langmem`` store manager + real LangGraph ``InMemoryStore`` through the black-box adapter.

Only the chat model is scripted (offline).  The harness may inspect store internals to fingerprint
write surfaces; the adapter itself uses the public store API only.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from pathlib import Path
from typing import Any, Callable

from adapter_harness import SCOPE, SEED, Harness, Out, say


class LangMemBlackBox(Harness):
    name = "langmem-blackbox"
    #: the store keeps no history and no schema: those effects are not observable here
    has_history = False
    detects = {"foreign_scope": True, "history_rewrite": False, "schema_change": False, "hidden_surface": False}

    def __init__(self, root: Path) -> None:
        import pytest

        pytest.importorskip("langmem")
        pytest.importorskip("langgraph")
        from langgraph.store.memory import InMemoryStore

        from membud.adapters import BlackBoxStaged
        from membud.adapters.blackbox_backends import langgraph_store_ops

        self.root = root
        self.queue: list[list[str]] = []
        self.llm_down = False
        self.native_hook: Callable[[Any], None] | None = None
        self.store = InMemoryStore()
        self._ops_builder = langgraph_store_ops
        self.guard = BlackBoxStaged(
            self._ops(self.store), self._factory, workdir=root / "membud", scratch_closer=lambda ops: None
        )

    def _model(self, store):
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        harness = self

        class Scripted(BaseChatModel):
            @property
            def _llm_type(self):
                return "scripted"

            def bind_tools(self, tools, **kw):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kw):
                if harness.llm_down:
                    raise RuntimeError("native write failed")
                if harness.native_hook is not None and store is not harness.store:
                    harness.native_hook(store)
                claims = harness.queue[0] if harness.queue else []
                calls = [{"name": "Memory", "args": {"content": c}, "id": uuid.uuid4().hex} for c in claims]
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=calls))])

        return Scripted()

    def _native_add(self, store, ns, messages):
        from langmem import create_memory_store_manager

        manager = create_memory_store_manager(self._model(store), namespace=ns, store=store)
        return manager.invoke({"messages": messages})

    def _ops(self, store, name="langmem"):
        return self._ops_builder(store, self._native_add, name=name)

    def _factory(self):
        from langgraph.store.memory import InMemoryStore

        return self._ops(InMemoryStore(), name="langmem-twin")

    @staticmethod
    def _out(o) -> Out:
        return Out(o.decision, o.applied, o.reason, o.error_type)

    def _run(self, fn) -> Out:
        try:
            return self._out(fn())
        except Exception as exc:
            return Out("ERROR", False, repr(exc), type(exc).__name__)
        finally:
            self.queue.clear()

    @staticmethod
    def _evidence(claims, supported_by):
        from membud.adapters.mem0_staged import LexicalGrounding

        return LexicalGrounding()(claims, [("messages[0]:user", " ".join(supported_by))]) if supported_by else []

    def add(self, claims, *, supported_by=None):
        self.queue[:] = [list(claims)]
        msgs = say(*(supported_by if supported_by is not None else claims))
        ev = None if supported_by is None else self._evidence(claims, supported_by)
        return self._run(lambda: self.guard.add(msgs, scope=SCOPE, evidence=ev))

    def _id(self, text):
        return next(r["id"] for r in self._ops(self.store).list(SCOPE) if r["text"] == text)

    def update(self, old, new, *, supported):
        return self._run(
            lambda: self.guard.update(self._id(old), new, source_text=new if supported else "unrelated chatter")
        )

    def delete(self, text, *, authorized):
        mid = self._id(text)
        if authorized:
            return self._run(
                lambda: self.guard.delete(
                    mid,
                    authorization={"source": "chat:1", "evidence_id": "e1", "rationale": "user asked to forget"},
                    evidence=[{"id": "e1", "supports": [], "source": "chat:1", "text": "please forget this"}],
                )
            )
        return self._run(lambda: self.guard.delete(mid, authorization=None, evidence=[]))

    def unguarded_add(self, text, scope=SCOPE):
        self._ops(self.store).add(scope, text, raw=True)

    def texts(self, scope=SCOPE):
        return sorted(r["text"] for r in self._ops(self.store).list(scope))

    @staticmethod
    def _dump(store):
        data = {
            "/".join(ns): {k: [dict(v.value), str(v.created_at), str(v.updated_at)] for k, v in d.items()}
            for ns, d in store._data.items()
        }
        return json.dumps(
            {"data": data, "vectors": repr(getattr(store, "_vectors", None)),
             "index": repr(getattr(store, "index_config", None))},
            sort_keys=True, default=str,
        )

    def surfaces(self):
        return self._dump(self.store)

    def control_changes_surfaces(self):
        from langgraph.store.memory import InMemoryStore

        ctl = InMemoryStore()
        before = self._dump(ctl)
        self.queue[:] = [list(SEED)]
        self._native_add(ctl, ("memories", "u1"), say(*SEED))
        self.queue.clear()
        return self._dump(ctl) != before

    def race(self):
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
        @contextlib.contextmanager
        def cm():
            cls = type(self.store)
            real_put, real_delete, calls, live = cls.put, cls.delete, {"n": 0}, self.store

            def gate(store):
                if store is live:  # inject only on the live store's writes, never on twins
                    calls["n"] += 1
                    if calls["n"] == 2 or (permanent and calls["n"] > 2):
                        raise RuntimeError("injected write failure")

            def flaky_put(store, *a, **kw):
                gate(store)
                return real_put(store, *a, **kw)

            def flaky_delete(store, *a, **kw):
                gate(store)
                return real_delete(store, *a, **kw)

            cls.put, cls.delete = flaky_put, flaky_delete
            try:
                yield
            finally:
                cls.put, cls.delete = real_put, real_delete

        return cm()

    def native_error(self):
        @contextlib.contextmanager
        def cm():
            self.llm_down = True
            try:
                yield
            finally:
                self.llm_down = False

        return cm()

    def tamper(self, kind):
        actions = {
            "foreign_scope": lambda t: t.put(
                ("memories", "u2"), "planted", {"kind": "Memory", "content": {"content": "planted"}}
            ),
            "history_rewrite": lambda t: None,  # no history surface exists (has_history = False)
            "schema_change": lambda t: setattr(t, "index_config", {"dims": 3}),
            "hidden_surface": lambda t: t._vectors.__setitem__("ghost", {"secret": {}}),
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
        pass
