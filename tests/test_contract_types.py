"""The tool contract MCP clients see (F26, F47): integer ids,
enumerations published as enums, a title and behaviour hints on every
tool, and the tool and parameter names 0.8 clients call.

Checked through an in-memory client session, on whichever mcp 1.x is
installed (the lock pins the floor, a fresh install gets the latest).
"""
import asyncio
from unittest.mock import patch

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary, write_epub

from apple_books_mcp import server

# The 0.8.4 surface: every tool and its parameters, in order.
TOOLS = {
    "list_all_collections": ["limit", "offset"],
    "get_collection_books": ["collection_id"],
    "describe_collection": ["collection_id"],
    "search_collections_by_title": ["title"],
    "create_collection": ["title", "details"],
    "rename_collection": ["collection_id", "new_title"],
    "delete_collection": ["collection_id"],
    "add_book_to_collection": ["collection_id", "book_id"],
    "remove_book_from_collection": ["collection_id", "book_id"],
    "list_all_books": ["limit", "offset"],
    "describe_book": ["book_id"],
    "search_books_by_title": ["title"],
    "get_books_by_genre": ["genre", "limit", "offset"],
    "get_books_in_progress": ["limit", "offset"],
    "get_finished_books": ["limit", "offset"],
    "get_unstarted_books": ["limit", "offset"],
    "get_recently_read_books": ["limit", "offset"],
    "list_all_annotations": ["limit", "offset"],
    "list_annotations": ["book_id", "limit", "offset"],
    "get_highlights_by_color": ["color", "limit", "offset", "order_by"],
    "search_notes": ["note", "limit", "offset", "order_by"],
    "search_annotations": ["text", "limit", "offset", "order_by"],
    "recent_annotations": ["limit", "offset"],
    "describe_annotation": ["annotation_id"],
    "get_annotation_context": ["annotation_id", "chars_before", "chars_after"],
    "get_annotations_by_date_range": ["after", "before", "limit", "offset", "order_by"],
    "list_book_chapters": ["book_id"],
    "get_chapter_content": ["book_id", "chapter_id", "offset", "max_chars"],
    "get_current_reading_position": ["book_id"],
    "get_library_stats": [],
}

# Write tools: (destructiveHint, idempotentHint).
WRITES = {
    "create_collection": (False, False),
    "add_book_to_collection": (False, True),
    "rename_collection": (True, True),
    "remove_book_from_collection": (True, True),
    "delete_collection": (True, True),
}


def _with_session(work):
    """Run ``work(session)`` against the server over an in-memory
    client session."""
    async def main():
        async with create_connected_server_and_client_session(
            server.mcp._mcp_server
        ) as session:
            return await work(session)
    return asyncio.run(main())


def _list_tools():
    async def work(session):
        return (await session.list_tools()).tools
    return {tool.name: tool for tool in _with_session(work)}


def _call(name, arguments):
    async def work(session):
        return await session.call_tool(name, arguments)
    result = _with_session(work)
    return result.isError, "\n".join(block.text for block in result.content)


@pytest.fixture(scope="module")
def tools():
    return _list_tools()


def test_tool_and_parameter_names_are_unchanged(tools):
    assert {name: list(t.inputSchema.get("properties", {})) for name, t in tools.items()} == TOOLS


def test_ids_are_integers(tools):
    """F26: every id the listings print as ``[N]`` is an integer;
    chapter ids are strings."""
    ids = 0
    for name, tool in tools.items():
        for param, schema in tool.inputSchema.get("properties", {}).items():
            if param == "chapter_id":
                assert schema["type"] == "string"
            elif param.endswith("_id"):
                assert schema.get("type") == "integer", (name, param, schema)
                ids += 1
    assert ids == 15


def test_enumerations_are_enums(tools):
    color = tools["get_highlights_by_color"].inputSchema["properties"]["color"]
    assert color["enum"] == ["yellow", "green", "blue", "pink", "purple"]
    for name in ("get_highlights_by_color", "search_notes", "search_annotations",
                 "get_annotations_by_date_range"):
        order_by = tools[name].inputSchema["properties"]["order_by"]
        assert order_by["enum"] == ["newest", "oldest"]
        assert order_by["default"] == "newest"


def test_none_defaults_accept_null(tools):
    for name, param in (("get_annotations_by_date_range", "after"),
                        ("get_annotations_by_date_range", "before"),
                        ("create_collection", "details")):
        schema = tools[name].inputSchema["properties"][param]
        assert {"type": "null"} in schema["anyOf"], (name, param)
        assert schema["default"] is None


def test_required_parameters_are_unchanged(tools):
    required = {name: sorted(t.inputSchema.get("required", [])) for name, t in tools.items()}
    assert {name: r for name, r in required.items() if r} == {
        "get_collection_books": ["collection_id"],
        "describe_collection": ["collection_id"],
        "search_collections_by_title": ["title"],
        "create_collection": ["title"],
        "rename_collection": ["collection_id", "new_title"],
        "delete_collection": ["collection_id"],
        "add_book_to_collection": ["book_id", "collection_id"],
        "remove_book_from_collection": ["book_id", "collection_id"],
        "describe_book": ["book_id"],
        "search_books_by_title": ["title"],
        "get_books_by_genre": ["genre"],
        "list_annotations": ["book_id"],
        "get_highlights_by_color": ["color"],
        "search_notes": ["note"],
        "search_annotations": ["text"],
        "describe_annotation": ["annotation_id"],
        "get_annotation_context": ["annotation_id"],
        "list_book_chapters": ["book_id"],
        "get_chapter_content": ["book_id", "chapter_id"],
        "get_current_reading_position": ["book_id"],
    }


def test_every_tool_has_a_title_and_hints(tools):
    """F47: a human title, and hints that tell reads from writes."""
    titles = set()
    for name, tool in tools.items():
        hints = tool.annotations
        assert tool.title and tool.title[0].isupper(), name
        assert hints.title == tool.title, name
        assert hints.openWorldHint is False, name
        titles.add(tool.title)
        if name in WRITES:
            destructive, idempotent = WRITES[name]
            assert hints.readOnlyHint is False, name
            assert hints.destructiveHint is destructive, name
            assert hints.idempotentHint is idempotent, name
        else:
            assert hints.readOnlyHint is True, name
            assert hints.destructiveHint is None, name
    assert len(titles) == len(tools)
    assert len(tools) - len(WRITES) == 25


@pytest.fixture
def library(tmp_path, monkeypatch):
    """One downloaded EPUB with a highlight, in a collection."""
    lib = FixtureLibrary.create(tmp_path / "home")
    epub = write_epub(tmp_path / "Typed.epub", "Typed", [
        ("c1", "One", ["Some text before. The typed highlight. Some text after."]),
    ])
    book = lib.add_book("Typed", path=epub, progress=0.5)
    anno = lib.add_annotation(
        book, "The typed highlight.", location="epubcfi(/6/4[c1]!/4/4,/1:18,/1:38)")
    collection = lib.add_collection("Typed Collection")
    lib.add_to_collection(collection, book)
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    monkeypatch.delenv("APPLE_BOOKS_MCP_ENABLE_WRITES", raising=False)
    yield {"book": book["id"], "annotation": anno, "collection": collection["id"]}
    api.close()


ID_CALLS = [
    ("get_collection_books", {"collection_id": "collection"}),
    ("describe_collection", {"collection_id": "collection"}),
    ("describe_book", {"book_id": "book"}),
    ("list_annotations", {"book_id": "book"}),
    ("describe_annotation", {"annotation_id": "annotation"}),
    ("get_annotation_context", {"annotation_id": "annotation"}),
    ("list_book_chapters", {"book_id": "book"}),
    ("get_chapter_content", {"book_id": "book", "chapter_id": "c1"}),
    ("get_current_reading_position", {"book_id": "book"}),
]


@pytest.mark.parametrize("name, arguments", ID_CALLS)
def test_ids_accept_ints_and_numeric_strings(library, name, arguments):
    """F26: a JSON integer and a numeric string (what 0.8 took for
    four of these tools) give the same answer, and so does a whole
    number like 3.0 (which 0.8's int ids took)."""
    def resolve(kind):
        return library[kind] if kind in library else kind

    as_int = {k: resolve(v) for k, v in arguments.items()}
    as_str = {k: str(v) for k, v in as_int.items()}
    as_float = {k: float(v) if isinstance(v, int) else v for k, v in as_int.items()}
    is_error, text = _call(name, as_int)
    if name == "get_current_reading_position":
        # No bookmark yet: it falls back to the highlight's chapter.
        assert "c1" in text
    assert not is_error, text
    assert _call(name, as_str) == (False, text)
    assert _call(name, as_float) == (False, text)


@pytest.mark.parametrize("name, arguments", [
    ("rename_collection", {"collection_id": "collection", "new_title": "X"}),
    ("delete_collection", {"collection_id": "collection"}),
    ("add_book_to_collection", {"collection_id": "collection", "book_id": "book"}),
    ("remove_book_from_collection", {"collection_id": "collection", "book_id": "book"}),
    ("create_collection", {"title": "X", "details": None}),
])
def test_write_tool_arguments_validate(library, name, arguments):
    """Ints, numeric strings and an explicit null all pass validation;
    with writes off, the answer is the enable instructions."""
    for convert in (lambda v: v, str):
        call = {k: (convert(library[v]) if v in library else v) for k, v in arguments.items()}
        is_error, text = _call(name, call)
        assert is_error and "--enable-writes" in text, text


@pytest.mark.parametrize("value", ["abc", "12abc", "", "1.5"])
def test_a_non_numeric_id_gets_a_clear_error(library, value):
    is_error, text = _call("describe_book", {"book_id": value})
    assert is_error
    assert f"book_id must be a numeric id, like the 175 in \"[175] Title\", not {value!r}." in text
    assert "validation error" not in text


@pytest.mark.parametrize("value, shown", [(1.5, "'1.5'"), (None, "'null'")])
def test_a_fractional_or_null_id_gets_a_clear_error(library, value, shown):
    is_error, text = _call("describe_book", {"book_id": value})
    assert is_error
    assert text.endswith(
        f"book_id must be a numeric id, like the 175 in \"[175] Title\", not {shown}.")


@pytest.mark.parametrize("details", ['["to read", "maybe"]', '{"a": 1}', "null", "5", None])
def test_details_are_passed_on_as_given(monkeypatch, details):
    """0.8 passed details on verbatim; a JSON-looking string is not
    decoded into a list, an object or a null."""
    monkeypatch.setenv("APPLE_BOOKS_MCP_ENABLE_WRITES", "1")
    with patch.object(server, "apple_books") as api:
        api.create_collection.return_value.id = 9
        api.create_collection.return_value.title = "X"
        is_error, text = _call("create_collection", {"title": "X", "details": details})
    assert not is_error, text
    api.create_collection.assert_called_once_with("X", details)


@pytest.mark.parametrize("name, param", [
    ("describe_book", "book_id"),
    ("list_annotations", "book_id"),
    ("describe_collection", "collection_id"),
    ("get_collection_books", "collection_id"),
    ("describe_annotation", "annotation_id"),
])
@pytest.mark.parametrize("value", [True, False])
def test_true_and_false_are_not_ids(library, name, param, value):
    """JSON true would otherwise pass as id 1 (the first collection)."""
    is_error, text = _call(name, {param: value})
    assert is_error
    assert text.endswith(
        f"{param} must be a numeric id, like the 175 in \"[175] Title\", "
        f"not {str(value).lower()!r}.")


def test_enumerations_ignore_case(library):
    is_error, text = _call(
        "get_highlights_by_color", {"color": " Yellow", "order_by": "OLDEST"})
    assert not is_error and "The typed highlight." in text


COLORS = "Valid colors: yellow, green, blue, pink, purple."


@pytest.mark.parametrize("arguments, message", [
    ({"color": "orange"}, f"Unknown highlight color 'orange'. {COLORS}"),
    ({"color": "underline"}, f"Unknown highlight color 'underline'. {COLORS}"),
    ({"color": ""}, f"Unknown highlight color ''. {COLORS}"),
    ({"color": "yellow", "order_by": "sideways"},
     "order_by must be 'newest' or 'oldest', not 'sideways'."),
])
def test_bad_enumeration_values_name_the_valid_ones(library, arguments, message):
    """F27: a short message from the tool, not pydantic's."""
    assert _call("get_highlights_by_color", arguments) == (
        True, f"Error executing tool get_highlights_by_color: {message}")


def test_explicit_null_dates(library):
    is_error, text = _call(
        "get_annotations_by_date_range", {"after": None, "before": None})
    assert not is_error and "The typed highlight." in text
