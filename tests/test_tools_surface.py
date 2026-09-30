"""The MCP surface of the tools and the resource (F06, F46).

Checked through FastMCP itself on whichever mcp 1.x is installed: the
lock pins 1.6.0, while a fresh install resolves the latest 1.x, which
derives output schemas from return annotations.
"""
import asyncio
from datetime import datetime, timezone

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


def test_currently_reading_description_matches_content(library):
    """F46: the resource's description lists what it actually holds —
    and the content holds it."""
    resources = asyncio.run(mcp.list_resources())
    (resource,) = [r for r in resources if str(r.uri) == "apple-books://currently-reading"]
    description = resource.description.lower()
    for item in ("title", "author", "book id", "progress", "chapter",
                 "chapter_id", "how many highlights"):
        assert item in description, item
    assert "no chapter text" in description

    book = library.add_book(
        "Pointer Book", "Pointer Author", progress=0.4,
        last_opened=datetime(2026, 9, 20, tzinfo=timezone.utc))
    library.add_annotation(book, "first pointer highlight")
    library.add_annotation(book, "second pointer highlight")
    library.add_annotation(book, "deleted pointer highlight", deleted=True)

    result = asyncio.run(mcp.read_resource("apple-books://currently-reading"))
    content = result[0].content
    assert "Currently Reading: Pointer Book by Pointer Author" in content
    assert f"Book id: {book['id']}" in content
    assert "Progress: In Progress (40.0%)" in content
    # Live highlights only, counted without loading them.
    assert f"Highlights in this book: 2  (use list_annotations({book['id']}) to browse)" in content
    assert "pointer highlight" not in content
