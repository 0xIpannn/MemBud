"""Deterministic offline harness around a REAL ``mem0.Memory`` (tests and evidence).

Everything except the two model endpoints is the real third-party stack: real
``mem0.Memory`` code, an embedded on-disk Qdrant store, Mem0's SQLite
history/messages database, its entity store, and real spaCy entity extraction.
The LLM is replaced by :class:`ScriptedLLM` (returns queued extraction JSON) and
the embedder by :class:`HashEmbedder` (SHA-256-derived vectors), so no network or
paid call can happen.

Importing this module requires ``mem0ai`` to be installed and ``MEM0_TELEMETRY``
to be disabled *before* ``mem0`` is first imported.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .mem0_staged import dump_host_state, file_tree_digest

DIMS = 16


class ScriptedLLM:
    """Returns queued extraction JSON; records every call."""

    def __init__(self) -> None:
        self.queue: list[str] = []
        self.calls: list[list[dict[str, Any]]] = []

    def push(self, *texts: str) -> None:
        self.queue.append(json.dumps({"memory": [{"text": text} for text in texts]}))

    def generate_response(self, messages, response_format=None, **kwargs):
        self.calls.append(messages)
        if not self.queue:
            raise AssertionError("ScriptedLLM called with an empty queue")
        return self.queue.pop(0)


class HashEmbedder:
    def embed(self, text, memory_action=None):
        digest = hashlib.sha256(str(text).encode("utf-8")).digest()
        return [b / 255 + 0.01 for b in digest[:DIMS]]

    def embed_batch(self, texts, memory_action=None):
        return [self.embed(text) for text in texts]


class OfflineHost:
    """A real Mem0 on real on-disk stores, plus host-surface fingerprint helpers."""

    collection = "membud_test"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.qdrant = self.root / "qdrant"
        self.history = self.root / "history.db"
        self.llm = ScriptedLLM()
        self.memory = self._open()

    @property
    def entity_collection(self) -> str:
        return f"{self.collection}_entities"

    def _open(self):
        from mem0 import Memory

        config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": str(self.qdrant),
                    "collection_name": self.collection,
                    "embedding_model_dims": DIMS,
                    "on_disk": True,
                },
            },
            "llm": {"provider": "openai", "config": {"api_key": "offline-fake"}},
            "embedder": {
                "provider": "openai",
                "config": {"api_key": "offline-fake", "embedding_dims": DIMS},
            },
            "history_db_path": str(self.history),
        }
        memory = Memory.from_config(config)
        memory.llm = self.llm
        memory.embedding_model = HashEmbedder()
        return memory

    def reopen(self) -> None:
        self.close()
        self.memory = self._open()

    def close(self) -> None:
        for closer in (self.memory.vector_store.client.close, self.memory.db.close):
            try:
                closer()
            except Exception:
                pass

    # -- fingerprints ---------------------------------------------------------
    def file_bytes(self) -> dict[str, str]:
        """SHA-256 of the raw bytes of every host file (Qdrant dir, history DB, Mem0 config dir)."""
        from mem0.memory.setup import mem0_dir

        return file_tree_digest(self.qdrant, self.history, mem0_dir)

    def logical(self):
        return dump_host_state(self.memory)

    def surfaces(self) -> dict[str, str]:
        """Digest per write surface: memory collection, entity collection, history, messages."""
        state = self.logical()
        out = {
            name: hashlib.sha256(json.dumps(dict(points), sort_keys=True).encode()).hexdigest()
            for name, points in state.collections.items()
        }
        for table, rows in state.tables.items():
            out[table] = hashlib.sha256(json.dumps(dict(rows), sort_keys=True).encode()).hexdigest()
        return out

    def view(self) -> dict[str, list]:
        """Id/timestamp-free semantic view, for guarded-vs-unguarded parity."""
        state = self.logical()
        memory_ids: dict[str, str] = {}
        memories = []
        for key, (_, payload_json) in state.collections[self.collection].items():
            payload = json.loads(payload_json)
            memories.append((payload["user_id"], payload["data"]))
            memory_ids[key] = payload["data"]
        entities = []
        for _, (_, payload_json) in state.collections.get(self.entity_collection, {}).items():
            payload = json.loads(payload_json)
            entities.append(
                (
                    payload["data"],
                    payload.get("entity_type"),
                    tuple(sorted(memory_ids.get(m, "<dangling>") for m in payload["linked_memory_ids"])),
                )
            )
        history = Counter(
            (r["event"], r["old_memory"], r["new_memory"])
            for r in (json.loads(v) for v in state.tables["history"].values())
        )
        messages = Counter(
            (r["role"], r["content"]) for r in (json.loads(v) for v in state.tables["messages"].values())
        )
        return {
            "memories": sorted(memories),
            "entities": sorted(entities),
            "history": sorted(history.elements()),
            "messages": sorted(messages.elements()),
        }

    def ids(self, text: str, user_id: str = "u1") -> str:
        for item in self.memory.get_all(filters={"user_id": user_id})["results"]:
            if item["memory"] == text:
                return item["id"]
        raise KeyError(text)

    def snapshot(self):
        return self.file_bytes(), self.logical().digest(), self.surfaces()

    def assert_untouched(self, snapshot) -> None:
        before_bytes, before_logical, before_surfaces = snapshot
        assert self.file_bytes() == before_bytes, "host file bytes changed"
        assert self.logical().digest() == before_logical, "host logical state changed"
        assert self.surfaces() == before_surfaces, "a host write surface changed"


__all__ = ["DIMS", "HashEmbedder", "OfflineHost", "ScriptedLLM"]
