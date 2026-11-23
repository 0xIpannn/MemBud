"""Executable findings: why LlamaIndex fact memory cannot be put behind the black-box adapter as-is.

Pinned: llama-index-core 0.14.26 (see requirements-llamaindex.txt).  Skipped when it is not installed.
"""
import asyncio

import pytest

pytest.importorskip("llama_index.core")
from llama_index.core.llms import ChatMessage, ChatResponse, MockLLM  # noqa: E402
from llama_index.core.memory import Memory  # noqa: E402
from llama_index.core.memory.memory_blocks.fact import FactExtractionMemoryBlock  # noqa: E402


class Scripted(MockLLM):
    async def achat(self, messages, **kw):
        return ChatResponse(
            message=ChatMessage(role="assistant", content="<facts><fact>Alice lives in Berlin</fact></facts>")
        )


def run(coro):
    return asyncio.run(coro)


def test_facts_are_bare_strings_with_no_identity():
    """No ids: update/delete are list edits, so the adapter's id-based diff cannot tell an update from delete+add."""
    block = FactExtractionMemoryBlock(llm=Scripted())
    run(block.aput([ChatMessage(role="user", content="I live in Berlin")]))
    assert block.facts == ["Alice lives in Berlin"]
    assert all(isinstance(f, str) for f in block.facts)


def test_the_memory_front_door_does_not_write_facts_when_the_user_message_arrives():
    """Memory.aput stores chat history; facts are extracted later, when the token budget overflows.

    So there is no synchronous 'native write' at the Memory level to stage, only block.aput below it.
    """
    block = FactExtractionMemoryBlock(llm=Scripted())
    memory = Memory.from_defaults(session_id="s", memory_blocks=[block], token_limit=30000)
    run(memory.aput(ChatMessage(role="user", content="I live in Berlin")))
    assert block.facts == []
    assert [m.content for m in run(memory.aget_all())] == ["I live in Berlin"]


def test_exceeding_max_facts_replaces_the_whole_list_with_a_model_condensation():
    """A write can rewrite every stored fact (wholesale replacement), an unmodeled cascade."""

    class Condensing(Scripted):
        async def achat(self, messages, **kw):
            text = messages[-1].content or ""
            if "max_facts" in text or "condense" in text.lower():
                body = "<facts><fact>condensed</fact></facts>"
            else:
                body = "<facts><fact>one</fact><fact>two</fact><fact>three</fact></facts>"
            return ChatResponse(message=ChatMessage(role="assistant", content=body))

    block = FactExtractionMemoryBlock(llm=Condensing(), max_facts=2)
    block.facts = ["old one", "old two"]
    run(block.aput([ChatMessage(role="user", content="hello")]))
    assert "old one" not in block.facts and "old two" not in block.facts, block.facts
