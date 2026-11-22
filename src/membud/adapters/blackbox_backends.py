"""Public-API ``BlackBoxOps`` builders for concrete memory systems.

Each builder uses ONLY the backend's documented public methods and imports nothing at module
import time, so this module is safe in the stdlib-only default install.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from .blackbox import BlackBoxError, BlackBoxOps

__all__ = ["mem0_ops", "langgraph_store_ops"]

_MEM0_LIST_LIMIT = 100_000
_SCOPE_KEYS = ("user_id", "agent_id", "run_id")


def mem0_ops(memory: Any, scopes: Sequence[Mapping[str, str]], *, name: str = "mem0") -> BlackBoxOps:
    """Black-box ops over a ``mem0.Memory`` (``add`` / ``get_all`` / ``update`` / ``delete`` / ``history``).

    Mem0's ``get_all`` cannot enumerate every scope, so the scopes to observe must be listed: a
    write to a scope that is not in ``scopes`` is invisible to the guard.  ``get_all`` truncates at
    ``top_k`` (default 20), which would silently look like deletions, so it is raised to a large
    value and a result that fills it is treated as truncation and refused.
    """
    known = [dict(s) for s in scopes]

    def add(scope: Mapping[str, str], content: Any, *, raw: bool = False, extra: Any = None) -> Any:
        kwargs = {k: v for k, v in scope.items() if k in _SCOPE_KEYS}
        if raw:
            result = memory.add(content, infer=False, metadata=extra, **kwargs)
            return [r["id"] for r in result["results"]]
        return memory.add(content, infer=True, **kwargs)

    def list_(scope: Mapping[str, str] | None) -> list[dict[str, Any]]:
        wanted = known if scope is None else [dict(scope)]
        out: dict[str, dict[str, Any]] = {}
        for filters in wanted:
            results = memory.get_all(filters=filters, top_k=_MEM0_LIST_LIMIT)["results"]
            if len(results) >= _MEM0_LIST_LIMIT:
                raise BlackBoxError("Mem0 get_all result filled top_k; refusing a possibly truncated listing")
            for item in results:
                out[item["id"]] = {
                    "id": item["id"],
                    "text": item["memory"],
                    "scope": {k: item[k] for k in _SCOPE_KEYS if item.get(k)},
                    "extra": item.get("metadata"),
                }
        return list(out.values())

    def modify(op: str, memory_id: str, text: str | None = None) -> Any:
        if op == "update":
            return memory.update(memory_id, text=text)
        if op == "delete":
            return memory.delete(memory_id)
        raise ValueError(op)

    def history(memory_id: str) -> list[Any]:
        return [[h["event"], h["old_memory"], h["new_memory"]] for h in memory.history(memory_id)]

    return BlackBoxOps(add=add, list=list_, modify=modify, history=history, name=name, handle=memory)


_LG_LIMIT = 100_000


def langgraph_store_ops(
    store: Any,
    native_add: Any,
    *,
    prefix: str = "memories",
    name: str = "langgraph-store",
) -> BlackBoxOps:
    """Black-box ops over a LangGraph ``BaseStore`` (``put`` / ``search`` / ``delete`` / ``list_namespaces``).

    This is the storage layer LangMem writes to.  ``native_add(store, namespace, messages)`` runs the
    backend's own write (e.g. a LangMem ``create_memory_store_manager(...).invoke``) against *this*
    store.  Scope ``{"user_id": u}`` is the namespace ``(prefix, u)``; any other namespace is
    reported as ``{"namespace": "a/b"}`` so a write outside the modeled scopes is visible (this
    store, unlike Mem0, can enumerate every namespace).  The store keeps no history and no schema,
    so history and schema changes are undetectable here; timestamps are deliberately not part of
    ``extra``.
    """

    def to_ns(scope: Mapping[str, str]) -> tuple[str, ...]:
        if "user_id" in scope and len(scope) == 1:
            return (prefix, scope["user_id"])
        if "namespace" in scope and len(scope) == 1:
            return tuple(scope["namespace"].split("/"))
        raise BlackBoxError(f"unsupported scope {dict(scope)!r}")

    def to_scope(ns: Sequence[str]) -> dict[str, str]:
        if len(ns) == 2 and ns[0] == prefix:
            return {"user_id": ns[1]}
        return {"namespace": "/".join(ns)}

    def text_of(value: Mapping[str, Any]) -> str:
        content = value.get("content")
        inner = content.get("content") if isinstance(content, Mapping) else content
        return inner if isinstance(inner, str) else json.dumps(content, sort_keys=True)

    def namespaces(scope: Mapping[str, str] | None) -> list[tuple[str, ...]]:
        if scope is not None:
            return [to_ns(scope)]
        found = store.list_namespaces(limit=_LG_LIMIT)
        if len(found) >= _LG_LIMIT:
            raise BlackBoxError("list_namespaces filled its limit; refusing a possibly truncated listing")
        return [tuple(n) for n in found]

    def list_(scope: Mapping[str, str] | None) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for ns in namespaces(scope):
            items = store.search(ns, limit=_LG_LIMIT)
            if len(items) >= _LG_LIMIT:
                raise BlackBoxError("search filled its limit; refusing a possibly truncated listing")
            for it in items:
                if tuple(it.namespace) != ns:  # search is prefix based: only direct members
                    continue
                if it.key in out:
                    raise BlackBoxError(f"memory key {it.key!r} appears in two namespaces; ids must be unique")
                out[it.key] = {
                    "id": it.key,
                    "text": text_of(it.value),
                    "scope": to_scope(ns),
                    "extra": {"kind": it.value.get("kind")},
                }
        return list(out.values())

    def add(scope: Mapping[str, str], content: Any, *, raw: bool = False, extra: Any = None) -> Any:
        ns = to_ns(scope)
        if raw:
            key = uuid.uuid4().hex
            kind = (extra or {}).get("kind", "Memory") if isinstance(extra, Mapping) else "Memory"
            store.put(ns, key, {"kind": kind, "content": {"content": content}})
            return key
        return native_add(store, ns, content)

    def locate(memory_id: str) -> tuple[tuple[str, ...], Any]:
        hits = [(tuple(i.namespace), i) for ns in namespaces(None) for i in [store.get(ns, memory_id)] if i]
        if len(hits) != 1:
            raise BlackBoxError(f"memory {memory_id!r} found in {len(hits)} namespaces")
        return hits[0]

    def modify(op: str, memory_id: str, text: str | None = None) -> Any:
        ns, item = locate(memory_id)
        if op == "update":
            value = dict(item.value)
            value["content"] = {"content": text}
            return store.put(ns, memory_id, value)
        if op == "delete":
            return store.delete(ns, memory_id)
        raise ValueError(op)

    return BlackBoxOps(add=add, list=list_, modify=modify, schema=None, history=None, name=name, handle=store)
