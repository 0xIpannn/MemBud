"""Harness: real ``graphiti-core`` over a real Neo4j server, scripted LLM + hash embedder, through the black-box adapter.

Needs a reachable Neo4j (bolt://127.0.0.1:7687, auth disabled; see docs); skipped otherwise.  The
harness may inspect the database directly to fingerprint write surfaces; the adapter uses
Graphiti's public classes and driver only.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from adapter_harness import SCOPE, SEED, Harness, Out, say

BOLT = ("127.0.0.1", 7687)


def _neo4j_up() -> bool:
    try:
        with socket.create_connection(BOLT, timeout=0.5):
            return True
    except OSError:
        return False


class GraphitiBlackBox(Harness):
    name = "graphiti-blackbox"
    has_history = False
    #: nodes, episodes, summaries, embeddings, provenance, temporal fields and indexes are not observed
    detects = {"foreign_scope": True, "history_rewrite": False, "schema_change": False, "hidden_surface": False}

    def __init__(self, root: Path) -> None:
        import pytest

        pytest.importorskip("graphiti_core")
        if not _neo4j_up():
            pytest.skip("no Neo4j at 127.0.0.1:7687")
        os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
        logging.getLogger("neo4j").setLevel(logging.ERROR)
        logging.getLogger("graphiti_core").setLevel(logging.ERROR)
        from graphiti_core import Graphiti
        from graphiti_core.driver.neo4j_driver import Neo4jDriver

        from membud.adapters import BlackBoxStaged
        from membud.adapters.blackbox_backends import graphiti_ops

        self.root = root
        self.loop = asyncio.new_event_loop()
        self.prefix = f"t{uuid.uuid4().hex[:8]}-"
        self.queue: list[str] = []
        self.llm_down = False
        self.native_hook: Callable[[str], None] | None = None
        self.twins = 0
        self._ops_builder = graphiti_ops
        self.g = Graphiti(
            graph_driver=Neo4jDriver(uri=f"bolt://{BOLT[0]}:{BOLT[1]}", user="neo4j", password="x"),
            llm_client=self._llm(),
            embedder=self._embedder(),
            cross_encoder=self._cross_encoder(),
        )
        self.run(self.g.build_indices_and_constraints())
        self.guard = BlackBoxStaged(
            self._ops(""), self._factory, workdir=root / "membud", scratch_closer=self._close_twin
        )

    # -- plumbing ---------------------------------------------------------------------------
    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def q(self, cypher, **params):
        records, _, _ = self.run(self.g.driver.execute_query(cypher, params=params))
        return records

    def _ops(self, suffix, name="graphiti"):
        return self._ops_builder(
            self.g, self.run, self._native_add, group_prefix=self.prefix, group_suffix=suffix, name=name
        )

    def _factory(self):
        self.twins += 1
        return self._ops(f"__twin{self.twins}", name=f"graphiti-twin{self.twins}")

    def _close_twin(self, ops):
        suffix = "__" + ops.name.rsplit("-", 1)[1]
        self.q("MATCH (n) WHERE n.group_id STARTS WITH $p AND n.group_id ENDS WITH $s DETACH DELETE n",
               p=self.prefix, s=suffix)

    def _native_add(self, graphiti, group, messages):
        if self.native_hook is not None and "__twin" in group:
            self.native_hook(group)
        body = " ".join(m["content"] for m in messages)
        return self.run(
            graphiti.add_episode(
                name="ep", episode_body=body, source_description="chat",
                reference_time=datetime(2026, 1, 1, tzinfo=timezone.utc), group_id=group,
            )
        )

    # -- scripted model endpoints -----------------------------------------------------------
    def _llm(self):
        from graphiti_core.llm_client.client import LLMClient
        from graphiti_core.llm_client.config import LLMConfig

        harness = self

        class Scripted(LLMClient):
            def __init__(self):
                super().__init__(LLMConfig(api_key="x", model="scripted"), cache=False)
                self.entities: list[str] = []

            async def _generate_response(self, messages, response_model=None, max_tokens=0, model_size=None, *, model=None):
                if harness.llm_down:
                    raise RuntimeError("native write failed")
                name = response_model.__name__ if response_model else None
                claims = list(harness.queue)
                if name == "ExtractedEntities":
                    self.entities = ["User"] + sorted({c.split()[-1] for c in claims})
                    return {"extracted_entities": [{"name": n, "entity_type_id": 0} for n in self.entities]}
                if name == "NodeResolutions":
                    return {"entity_resolutions": [
                        {"id": i, "name": n, "duplicate_candidate_id": -1} for i, n in enumerate(self.entities)]}
                if name == "ExtractedEdges":
                    return {"edges": [{"source_entity_name": "User", "target_entity_name": c.split()[-1],
                                       "relation_type": "HAS_FACT", "fact": c} for c in claims]}
                if name == "EdgeDuplicate":
                    return {"duplicate_facts": [], "contradicted_facts": []}
                if name == "EdgeTimestamps":
                    return {"valid_at": None, "invalid_at": None}
                if name == "BatchEdgeTimestamps":
                    return {"timestamps": [{"valid_at": None, "invalid_at": None} for _ in claims]}
                if name == "SummarizedEntities":
                    return {"summaries": []}
                return {}

        return Scripted()

    @staticmethod
    def _embedder():
        from graphiti_core.embedder.client import EmbedderClient

        class Hash(EmbedderClient):
            async def create(self, input_data):
                return [b / 255 for b in hashlib.sha256(str(input_data).encode()).digest()[:16]]

            async def create_batch(self, items):
                return [await self.create(i) for i in items]

        return Hash()

    @staticmethod
    def _cross_encoder():
        from graphiti_core.cross_encoder.client import CrossEncoderClient

        class Flat(CrossEncoderClient):
            async def rank(self, query, passages):
                return [(p, 1.0) for p in passages]

        return Flat()

    # -- harness API -------------------------------------------------------------------------
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
        self.queue[:] = list(claims)
        msgs = say(*(supported_by if supported_by is not None else claims))
        ev = None if supported_by is None else self._evidence(claims, supported_by)
        return self._run(lambda: self.guard.add(msgs, scope=SCOPE, evidence=ev))

    def _id(self, text):
        return next(r["id"] for r in self._ops("").list(SCOPE) if r["text"] == text)

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
        self._ops("").add(scope, text, raw=True)

    def texts(self, scope=SCOPE):
        return sorted(r["text"] for r in self._ops("").list(scope))

    def _dump(self, extra_filter="NOT n.group_id CONTAINS '__twin'"):
        nodes = self.q(
            f"MATCH (n) WHERE n.group_id STARTS WITH $p AND {extra_filter} "
            "RETURN labels(n) AS l, properties(n) AS p ORDER BY n.uuid", p=self.prefix)
        rels = self.q(
            f"MATCH (a)-[r]->(b) WHERE a.group_id STARTS WITH $p AND {extra_filter.replace('n.', 'a.')} "
            "RETURN type(r) AS t, properties(r) AS p, a.uuid AS a, b.uuid AS b ORDER BY r.uuid, t", p=self.prefix)
        return json.dumps(
            {"nodes": sorted([[r["l"], r["p"]] for r in nodes], key=lambda x: json.dumps(x, default=str)),
             "rels": sorted([[r["t"], r["p"], r["a"], r["b"]] for r in rels], key=lambda x: json.dumps(x, default=str))},
            default=str, sort_keys=True)

    def surfaces(self):
        return self._dump()

    def control_changes_surfaces(self):
        before = self._dump()
        self.queue[:] = list(SEED)
        self._native_add(self.g, f"{self.prefix}ctl", say(*SEED))
        self.queue.clear()
        return self._dump() != before

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
        from graphiti_core.edges import EntityEdge

        @contextlib.contextmanager
        def cm():
            real_save, real_delete = EntityEdge.save, EntityEdge.delete
            had_delete = "delete" in EntityEdge.__dict__
            calls = {"n": 0}

            def gate(edge):
                if edge.group_id.startswith(self.prefix) and "__twin" not in edge.group_id:
                    calls["n"] += 1
                    if calls["n"] == 2 or (permanent and calls["n"] > 2):
                        raise RuntimeError("injected write failure")

            async def save(edge, driver):
                gate(edge)
                return await real_save(edge, driver)

            async def delete(edge, driver):
                gate(edge)
                return await real_delete(edge, driver)

            EntityEdge.save, EntityEdge.delete = save, delete
            try:
                yield
            finally:
                EntityEdge.save = real_save
                if had_delete:
                    EntityEdge.delete = real_delete
                else:
                    del EntityEdge.delete

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
        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EntityNode

        def foreign(group):
            other = group.replace(f"{self.prefix}u1", f"{self.prefix}u2")
            a, b = (EntityNode(name=n, group_id=other, labels=["Entity"], summary="") for n in ("X", "Y"))
            for n in (a, b):
                self.run(n.generate_name_embedding(self.g.embedder))
                self.run(n.save(self.g.driver))
            e = EntityEdge(source_node_uuid=a.uuid, target_node_uuid=b.uuid, group_id=other, name="PLANTED",
                           fact="planted", episodes=[], created_at=datetime.now(timezone.utc))
            self.run(e.generate_embedding(self.g.embedder))
            self.run(e.save(self.g.driver))

        actions = {
            "foreign_scope": foreign,
            "history_rewrite": lambda g: None,  # no history surface exists (has_history = False)
            "schema_change": lambda g: self.q("CREATE INDEX tamper_idx IF NOT EXISTS FOR (n:Ghost) ON (n.x)"),
            "hidden_surface": lambda g: self.q("CREATE (:Ghost {x: 1})"),
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
        with contextlib.suppress(Exception):
            self.q("MATCH (n) WHERE n.group_id STARTS WITH $p DETACH DELETE n", p=self.prefix)
            self.q("MATCH (n:Ghost) DETACH DELETE n")
            self.q("DROP INDEX tamper_idx IF EXISTS")
            self.run(self.g.close())
        self.loop.close()
