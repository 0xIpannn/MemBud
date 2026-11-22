"""Public-API ``BlackBoxOps`` builders for concrete memory systems.

Each builder uses ONLY the backend's documented public methods and imports nothing at module
import time, so this module is safe in the stdlib-only default install.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .blackbox import BlackBoxError, BlackBoxOps

__all__ = ["mem0_ops"]

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
