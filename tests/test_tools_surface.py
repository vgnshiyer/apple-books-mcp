"""The MCP surface of the tools (F06).

Checked through FastMCP itself on whichever mcp 1.x is installed: the
lock pins 1.6.0, while a fresh install resolves the latest 1.x, which
derives output schemas from return annotations.
"""
import asyncio

import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary

from apple_books_mcp import server
from apple_books_mcp.server import mcp


@pytest.fixture
def library(tmp_path, monkeypatch):
    """An empty synthetic library the server reads."""
    lib = FixtureLibrary.create(tmp_path)
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield lib
    api.close()


def test_thirty_tools_without_output_schema():
    """F06: from mcp 1.10 on, a ``-> TextContent`` return annotation
    made FastMCP publish a ~900-char outputSchema per tool. Unannotated
    tools publish none, on every mcp 1.x."""
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 30
    assert [t.name for t in tools if getattr(t, "outputSchema", None)] == []


@pytest.mark.parametrize("name, arguments", [
    ("list_all_books", {}),
    ("list_all_annotations", {"limit": 5}),
    ("get_library_stats", {}),
    ("describe_annotation", {"annotation_id": "1"}),
])
def test_tool_call_is_one_text_block(library, name, arguments):
    """F06: each call returns exactly one text block and no
    structuredContent (mcp >= 1.10 returns a ``(content, structured)``
    pair when a tool has an output schema)."""
    book = library.add_book("Surface Book", progress=0.5)
    library.add_annotation(book, "a surface highlight")
    result = asyncio.run(mcp.call_tool(name, arguments))
    assert not isinstance(result, tuple)
    assert len(result) == 1
    assert result[0].type == "text"
    assert result[0].text

