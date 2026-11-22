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

__all__ = ["mem0_ops", "langgraph_store_ops", "graphiti_ops"]

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


def graphiti_ops(
    graphiti: Any,
    run: Any,
    native_add: Any,
    *,
    group_prefix: str,
    group_suffix: str = "",
    name: str = "graphiti",
) -> BlackBoxOps:
    """Black-box ops over ``graphiti_core.Graphiti`` (Neo4j), using only its public classes and driver.

    The unit of memory is a fact edge (``EntityEdge``: id = uuid, text = ``fact``).  Scope
    ``{"user_id": u}`` is the graph partition ``group_id = f"{group_prefix}{u}{group_suffix}"``; a
    twin uses another ``group_suffix`` in the same database (Neo4j Community has one database, so
    partitions are the only isolation the public API offers).  ``run(coro)`` drives Graphiti's async
    API from sync code.  ``native_add(graphiti, group_id, messages)`` runs Graphiti's own
    ``add_episode``.

    What is observed: each fact edge's text, relation name, endpoint names and whether it has been
    expired (``expired_at``), so a native write that invalidates an existing fact shows up as a
    metadata change and is refused.  What is NOT observed (and therefore neither verified nor
    replayed): episode nodes and their ``MENTIONS`` edges, entity summaries and embeddings, the
    ``episodes`` provenance list, ``valid_at``/``invalid_at``/``reference_time``, and any index.
    A raw replay creates the fact edge and (if absent) its two entity nodes only.
    """
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode

    driver = graphiti.driver
    base = f"{group_prefix}"

    def gid(scope: Mapping[str, str]) -> str:
        if set(scope) != {"user_id"}:
            raise BlackBoxError(f"unsupported scope {dict(scope)!r}")
        return f"{base}{scope['user_id']}{group_suffix}"

    def scope_of(group: str) -> dict[str, str] | None:
        if not group.startswith(base) or (group_suffix and not group.endswith(group_suffix)):
            return None
        core = group[len(base):]
        if group_suffix:
            core = core[: -len(group_suffix)]
        if not group_suffix and "__twin" in core:
            return None  # another instance's partition
        return {"user_id": core}

    def groups(scope: Mapping[str, str] | None) -> list[str]:
        if scope is not None:
            return [gid(scope)]
        records, _, _ = run(driver.execute_query("MATCH (n) WHERE n.group_id IS NOT NULL RETURN DISTINCT n.group_id AS g"))
        return sorted(r["g"] for r in records if scope_of(r["g"]) is not None)

    def list_(scope: Mapping[str, str] | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for group in groups(scope):
            names = {n.uuid: n.name for n in run(EntityNode.get_by_group_ids(driver, [group]))}
            for e in run(EntityEdge.get_by_group_ids(driver, [group])):
                out.append(
                    {
                        "id": e.uuid,
                        "text": e.fact,
                        "scope": scope_of(group),
                        "extra": {
                            "relation": e.name,
                            "src": names.get(e.source_node_uuid),
                            "dst": names.get(e.target_node_uuid),
                            "expired": e.expired_at is not None,
                        },
                    }
                )
        return out

    def node(group: str, node_name: str) -> Any:
        for n in run(EntityNode.get_by_group_ids(driver, [group])):
            if n.name == node_name:
                return n
        n = EntityNode(name=node_name, group_id=group, labels=["Entity"], summary="")
        run(n.generate_name_embedding(graphiti.embedder))
        run(n.save(driver))
        return n

    def add(scope: Mapping[str, str], content: Any, *, raw: bool = False, extra: Any = None) -> Any:
        group = gid(scope)
        if not raw:
            return native_add(graphiti, group, content)
        from datetime import datetime, timezone

        extra = extra if isinstance(extra, Mapping) else {}
        src = node(group, extra.get("src") or "User")
        dst = node(group, extra.get("dst") or "Memory")
        edge = EntityEdge(
            source_node_uuid=src.uuid, target_node_uuid=dst.uuid, group_id=group,
            name=extra.get("relation") or "HAS_FACT", fact=content, episodes=[],
            created_at=datetime.now(timezone.utc),
        )
        run(edge.generate_embedding(graphiti.embedder))
        run(edge.save(driver))
        return edge.uuid

    def modify(op: str, memory_id: str, text: str | None = None) -> Any:
        edges = run(EntityEdge.get_by_uuids(driver, [memory_id]))
        if len(edges) != 1:
            raise BlackBoxError(f"edge {memory_id!r} not found")
        edge = edges[0]
        if op == "update":
            edge.fact = text or ""
            run(edge.generate_embedding(graphiti.embedder))
            return run(edge.save(driver))
        if op == "delete":
            return run(edge.delete(driver))
        raise ValueError(op)

    return BlackBoxOps(add=add, list=list_, modify=modify, schema=None, history=None, name=name, handle=graphiti)
