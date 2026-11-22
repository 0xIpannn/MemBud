"""A file-backed fake memory system used to test the black-box adapter.

It is deliberately *not* shaped like any real backend: SQLite file with a public memory table
plus hidden surfaces the public API never exposes (an index table, a counter, a history table).
Hidden surfaces let the tests prove what the black-box adapter can and cannot see.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from membud.adapters.blackbox import BlackBoxOps


class FakeStore:
    """Minimal memory system.  ``add`` extracts one memory per sentence and dedupes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, scope TEXT, text TEXT, extra TEXT);
            CREATE TABLE IF NOT EXISTS history(n INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT, event TEXT, old TEXT, new TEXT);
            CREATE TABLE IF NOT EXISTS hidden_index(memory_id TEXT, tokens TEXT);
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v INTEGER);
            INSERT OR IGNORE INTO meta VALUES('counter', 0);
            """
        )
        self.fail_at: set[int] = set()  # fail exactly these public write numbers (1-based)
        self.fail_from: int | None = None  # fail every public write from this number on
        self.writes = 0
        self.native_hook: Any = None  # called inside native add (foreign writes, DDL, ...)

    # -- internals --------------------------------------------------------
    def _next_id(self) -> str:
        self.db.execute("UPDATE meta SET v=v+1 WHERE k='counter'")
        return f"m{self.db.execute('SELECT v FROM meta WHERE k=?', ('counter',)).fetchone()[0]}"

    def _tick(self) -> None:
        self.writes += 1
        if self.writes in self.fail_at or (self.fail_from is not None and self.writes >= self.fail_from):
            raise RuntimeError("injected write failure")

    def _insert(self, scope: dict[str, str], text: str, extra: Any) -> str:
        self._tick()
        mid = self._next_id()
        self.db.execute("INSERT INTO memories VALUES(?,?,?,?)", (mid, json.dumps(scope, sort_keys=True), text, json.dumps(extra)))
        self.db.execute("INSERT INTO history(memory_id,event,old,new) VALUES(?,?,?,?)", (mid, "ADD", None, text))
        self.db.execute("INSERT INTO hidden_index VALUES(?,?)", (mid, " ".join(sorted(set(re.findall(r"\w+", text.lower()))))))
        return mid

    # -- public API (what a user of the memory system sees) ---------------
    def add(self, scope: dict[str, str], content: Any, *, raw: bool = False, extra: Any = None) -> Any:
        if raw:
            return self._insert(scope, content, extra)
        if self.native_hook is not None:
            self.native_hook(self)
        text = content if isinstance(content, str) else " ".join(
            m["content"] for m in content if m.get("role") == "user"
        )
        ids = []
        existing = {r[0] for r in self.db.execute("SELECT text FROM memories WHERE scope=?", (json.dumps(scope, sort_keys=True),))}
        for sentence in (s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()):
            if sentence not in existing:
                ids.append(self._insert(scope, sentence, None))
        return ids

    def list(self, scope: dict[str, str] | None) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id, scope, text, extra FROM memories ORDER BY CAST(substr(id,2) AS INTEGER)").fetchall()
        out = []
        for mid, sc, text, extra in rows:
            sc = json.loads(sc)
            if scope is None or all(sc.get(k) == v for k, v in scope.items()):
                out.append({"id": mid, "scope": sc, "text": text, "extra": json.loads(extra)})
        return out

    def update(self, memory_id: str, text: str) -> None:
        self._tick()
        old = self.db.execute("SELECT text FROM memories WHERE id=?", (memory_id,)).fetchone()
        if old is None:
            raise KeyError(memory_id)
        self.db.execute("UPDATE memories SET text=? WHERE id=?", (text, memory_id))
        self.db.execute("INSERT INTO history(memory_id,event,old,new) VALUES(?,?,?,?)", (memory_id, "UPDATE", old[0], text))
        self.db.execute("UPDATE hidden_index SET tokens=? WHERE memory_id=?", (" ".join(sorted(set(re.findall(r"\w+", text.lower())))), memory_id))

    def delete(self, memory_id: str) -> None:
        self._tick()
        old = self.db.execute("SELECT text FROM memories WHERE id=?", (memory_id,)).fetchone()
        if old is None:
            raise KeyError(memory_id)
        self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.db.execute("INSERT INTO history(memory_id,event,old,new) VALUES(?,?,?,?)", (memory_id, "DELETE", old[0], None))
        self.db.execute("DELETE FROM hidden_index WHERE memory_id=?", (memory_id,))

    def history(self, memory_id: str) -> list[Any]:
        return [list(r) for r in self.db.execute("SELECT event, old, new FROM history WHERE memory_id=? ORDER BY n", (memory_id,))]

    def schema(self) -> Any:
        return [r[0] for r in self.db.execute("SELECT sql FROM sqlite_master ORDER BY name")]

    # -- test-only access to hidden surfaces ------------------------------
    def dump_all(self) -> dict[str, Any]:
        """Every table, including the hidden ones (not part of the public API)."""
        out = {}
        for (name,) in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            out[name] = [list(r) for r in self.db.execute(f"SELECT * FROM {name} ORDER BY rowid")]
        return out

    def close(self) -> None:
        self.db.close()


def make_ops(store: FakeStore, *, with_history: bool = True, with_schema: bool = True, name: str = "fake") -> BlackBoxOps:
    def modify(op: str, memory_id: str, text: str | None = None) -> None:
        if op == "update":
            store.update(memory_id, text or "")
        elif op == "delete":
            store.delete(memory_id)
        else:
            raise ValueError(op)

    return BlackBoxOps(
        add=store.add,
        list=store.list,
        modify=modify,
        schema=store.schema if with_schema else None,
        history=store.history if with_history else None,
        name=name,
    )
