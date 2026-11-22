"""Executable findings for AutoGen's memory interface (autogen-core 0.7.5, see requirements-autogen.txt)."""
import asyncio

import pytest

pytest.importorskip("autogen_core")
from autogen_core.memory import ListMemory, Memory, MemoryContent, MemoryMimeType  # noqa: E402


def test_the_interface_has_no_update_or_delete_by_id_and_no_native_write():
    methods = {m for m in dir(Memory) if not m.startswith("_")}
    assert {"add", "query", "clear", "update_context", "close"} <= methods
    assert not {"update", "delete", "remove", "get", "list"} & methods


def test_add_stores_exactly_what_it_is_given_so_there_is_no_native_write_to_stage():
    mem = ListMemory()
    asyncio.run(mem.add(MemoryContent(content="Alice lives in Berlin", mime_type=MemoryMimeType.TEXT)))
    assert [c.content for c in mem.content] == ["Alice lives in Berlin"]  # no extraction, no ids, no side effects
    assert not hasattr(mem.content[0], "id")
