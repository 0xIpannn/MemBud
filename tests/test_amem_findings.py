"""Findings: why A-Mem (agiresearch/A-mem @ ceffb860) cannot be staged safely through its public API.

These tests assert the *defects*, so a future A-Mem that fixes them will fail them loudly and the
decision can be revisited.  Needs the optional env (``requirements-amem.txt``); skipped otherwise.
Only the two model endpoints are faked (hash embedder, scripted LLM); no network.
"""
from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("agentic_memory.memory_system")
pytest.importorskip("chromadb")

import agentic_memory.retrievers as retrievers  # noqa: E402
from agentic_memory.memory_system import AgenticMemorySystem  # noqa: E402


class HashEmbedding:
    def __init__(self, model_name=None, **kwargs):
        pass

    def name(self):
        return "hash"

    def __call__(self, input):
        return [[b / 255 + 0.01 for b in hashlib.sha256(t.encode()).digest()[:16]] for t in input]

    def embed_query(self, input):
        return self(input)

    @staticmethod
    def build_from_config(config):
        return HashEmbedding()

    def get_config(self):
        return {}


class ScriptedLLM:
    """Evolution decision: always 'update_neighbor' with new tags/context for every neighbour."""

    def __init__(self):
        self.calls = 0

    def get_completion(self, prompt, response_format=None, temperature=0.7):
        self.calls += 1
        return json.dumps(
            {
                "should_evolve": True,
                "actions": ["update_neighbor"],
                "suggested_connections": [],
                "tags_to_update": [],
                "new_context_neighborhood": ["REWRITTEN BY EVOLUTION"] * 5,
                "new_tags_neighborhood": [["evolved"]] * 5,
            }
        )


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(retrievers, "SentenceTransformerEmbeddingFunction", HashEmbedding)


def make(llm=None) -> AgenticMemorySystem:
    system = AgenticMemorySystem(api_key="offline-fake")
    system.llm_controller.llm = llm or ScriptedLLM()
    return system


def test_constructing_a_second_instance_destroys_the_first_ones_vector_store():
    live = make()
    live.add_note("Alice lives in Berlin")
    assert live.retriever.collection.count() == 1
    make()  # e.g. a scratch twin built in the same process
    with pytest.raises(Exception, match="does not exist"):
        live.retriever.collection.count()
    assert len(live.memories) == 1  # the dict survives; the vector index does not: silent divergence


def test_every_add_runs_the_evolution_llm_and_rewrites_existing_notes_before_the_new_note_is_stored():
    llm = ScriptedLLM()
    system = make(llm)
    first = system.add_note("Alice lives in Berlin")
    before = (system.read(first).context, list(system.read(first).tags))
    system.add_note("Alice works at Acme")
    assert llm.calls == 1  # there is no public insert-without-evolution path
    after = (system.read(first).context, list(system.read(first).tags))
    assert after != before and after[0] == "REWRITTEN BY EVOLUTION"  # a neighbour was mutated as a side effect
