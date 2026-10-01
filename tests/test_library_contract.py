"""Contract tests: the MCP against the real py-apple-books API (F42).

The rest of the suite fakes the library with hand-written classes, so a
renamed facade method, a changed signature or a dropped model attribute
can pass it and still break every user: uvx resolves the newest
py-apple-books for every published release. These tests hold the
server to the library itself:

1. Every tool (and the resource) runs over the MCP protocol against
   ``create_autospec`` of the real ``PyAppleBooks`` and of the objects
   it returns. A call that doesn't fit a real signature fails, and so
   does reading an attribute the real class lacks, even through
   ``getattr(obj, name, default)`` or inside a broad ``except``.
2. Every read tool runs end to end, with no mocks, against a synthetic
   store built by ``py_apple_books.testing``.
3. The attributes the tool modules read off library objects (found in
   the source), the facade methods they call and the names they import
   from py_apple_books exist in the installed library.

They check contracts (calls, types, non-error results), never output
text, which the other test modules own.
"""
import ast
import asyncio
import dataclasses
import functools
import importlib
import inspect
import pathlib
import types
import typing
from datetime import datetime
from unittest.mock import create_autospec

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl
from py_apple_books import LibraryStats, PyAppleBooks
from py_apple_books.content import BookContent, Chapter
from py_apple_books.models import Annotation, Book, Collection
from py_apple_books.models.location import Location
from py_apple_books.models.manager import ModelIterable
from py_apple_books.testing import FixtureLibrary, seed_demo

import apple_books_mcp
from apple_books_mcp import server

PACKAGE = pathlib.Path(apple_books_mcp.__file__).parent
# The modules that handle library objects.
TOOL_MODULES = ("server.py", "utils.py")
RESOURCE = "apple-books://currently-reading"
# How the reading position resolves: from the ToC, from a bare CFI on
# Books' bookmark, or from the latest highlight.
TIERS = ("toc", "cfi", "recent_highlight")


# --------------------------------------------------------------------------
# Tool arguments
# --------------------------------------------------------------------------


def _read_calls(ids):
    """Realistic arguments for every read tool, by tool name."""
    return {
        "list_all_collections": {},
        "get_collection_books": {"collection_id": ids["collection"]},
        "describe_collection": {"collection_id": ids["collection"]},
        "search_collections_by_title": {"title": "Shelf"},
        "list_all_books": {"limit": 50},
        "describe_book": {"book_id": ids["book"]},
        "search_books_by_title": {"title": "Synthetic"},
        "get_books_by_genre": {"genre": "Fiction"},
        "get_books_in_progress": {},
        "get_finished_books": {},
        "get_unstarted_books": {},
        "get_recently_read_books": {"limit": 5},
        "list_all_annotations": {"limit": 20},
        "list_annotations": {"book_id": ids["book"]},
        "get_highlights_by_color": {"color": "yellow"},
        "search_notes": {"note": "synthetic"},
        "search_annotations": {"text": "synthetic", "order_by": "oldest"},
        "recent_annotations": {},
        "describe_annotation": {"annotation_id": ids["annotation"]},
        "get_annotation_context": {
            "annotation_id": ids["annotation"], "chars_before": 200, "chars_after": 200,
        },
        "get_annotations_by_date_range": {"after": "2026-09-01", "before": "2026-09-30"},
        "list_book_chapters": {"book_id": ids["book"]},
        "get_chapter_content": {
            "book_id": ids["book"], "chapter_id": ids["chapter"], "max_chars": 5000,
        },
        "get_current_reading_position": {"book_id": ids["book"]},
        "get_library_stats": {},
    }


def _write_calls(ids):
    """Arguments for every write tool, by tool name."""
    return {
        "create_collection": {"title": "Contract Shelf", "details": "made by a test"},
        "rename_collection": {"collection_id": ids["collection"], "new_title": "Renamed"},
        "delete_collection": {"collection_id": ids["collection"]},
        "add_book_to_collection": {"collection_id": ids["collection"], "book_id": ids["book"]},
        "remove_book_from_collection": {
            "collection_id": ids["collection"], "book_id": ids["book"],
        },
    }


# The ids of the fake library (see _fake_library).
FAKE_IDS = {"book": 1, "collection": 5, "annotation": 10, "chapter": "chap1"}


def _options(schema) -> tuple:
    """A parameter's schema and its ``anyOf``/``oneOf`` alternatives."""
    return (schema, *schema.get("anyOf", ()), *schema.get("oneOf", ()))


def _arguments(tool, values) -> dict:
    """``values`` as a client following ``tool``'s input schema sends
    them: an id as a JSON integer where the schema takes one, else as a
    string, and an enum value in the schema's spelling."""
    properties = tool.inputSchema.get("properties", {})
    arguments = {}
    for name, value in values.items():
        assert name in properties, f"{tool.name} has no parameter {name!r}"
        options = _options(properties[name])
        kinds = set()
        for option in options:
            kind = option.get("type")
            kinds.update(kind if isinstance(kind, list) else [kind])
        if isinstance(value, int) and "integer" not in kinds:
            value = str(value)
        enum = [v for option in options for v in option.get("enum", ())]
        if enum and value not in enum:
            value = next((v for v in enum if str(v).lower() == str(value).lower()), value)
        arguments[name] = value
    return arguments


# --------------------------------------------------------------------------
# Talking to the server over MCP
# --------------------------------------------------------------------------


def _session(work):
    """Run ``work(session)`` on an in-memory MCP session with the server."""
    async def main():
        async with create_connected_server_and_client_session(
            server.mcp._mcp_server
        ) as session:
            return await work(session)
    return asyncio.run(main())


def _call_tools(calls) -> list:
    """Call each ``(tool name, values)`` in turn; the results, in order."""
    async def work(session):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        return [
            await session.call_tool(name, _arguments(tools[name], values))
            for name, values in calls
        ]
    return _session(work)


def _read_resource() -> str:
    async def work(session):
        return await session.read_resource(AnyUrl(RESOURCE))
    result = _session(work)
    text = "\n".join(getattr(item, "text", "") for item in result.contents)
    assert text.strip(), result
    return text


def _ok_text(name, result) -> str:
    """The text of a successful tool result."""
    text = "\n".join(getattr(item, "text", "") for item in result.content)
    assert not result.isError, f"{name}: {text}"
    assert result.content, name
    assert all(item.type == "text" for item in result.content), result.content
    assert text.strip(), name
    return text


def _enable_writes(monkeypatch):
    """Let the write tools run (only ever against the fakes here)."""
    monkeypatch.setenv("APPLE_BOOKS_MCP_ENABLE_WRITES", "1")
    monkeypatch.setattr(server, "_writes_enabled", lambda: True, raising=False)


# --------------------------------------------------------------------------
# Autospecced library objects
# --------------------------------------------------------------------------


def _spec(cls):
    """What to autospec ``cls`` from: an instance (built without I/O)
    where ``__init__`` sets attributes the class doesn't declare, else
    the class."""
    if cls is BookContent:
        return BookContent("/nonexistent/Synthetic Book.epub")
    if cls is ModelIterable:
        return ModelIterable()
    return cls


def _attributes(cls) -> set:
    """Every attribute an instance of ``cls`` has: its dataclass fields
    and what ``dir()`` lists (methods, properties, relations)."""
    names = set(dir(_spec(cls)))
    if dataclasses.is_dataclass(cls):
        names.update(field.name for field in dataclasses.fields(cls))
    return names


class _Watch:
    """Builds autospecs of the library's classes and records what the
    MCP gets wrong about them, even where it swallows the error."""

    def __init__(self):
        # "Class.name" for every read of an attribute the real class lacks.
        self.misses = []
        # "Class.name" for every value a fake was given that the real
        # class lacks (left unset, so a read of it is a miss).
        self.stale = []
        # (class, fake) for every fake built.
        self.fakes = []

    def hook(self, fake, label):
        """Record reads of attributes ``fake``'s spec lacks.

        Mock gives every instance a class of its own (that is how it
        configures magic methods per mock), so this sees only this
        fake's lookups. A lookup reaching ``__getattr__`` and failing
        found nothing in the spec nor among the values set.
        """
        klass = type(fake)
        lookup = klass.__getattr__
        misses = self.misses

        def __getattr__(mock, name):
            try:
                return lookup(mock, name)
            except AttributeError:
                if not name.startswith("_"):
                    misses.append(f"{label}.{name}")
                raise

        klass.__getattr__ = __getattr__
        return fake

    def known(self, cls, names) -> list:
        """The ``names`` the real ``cls`` has; the rest go to ``stale``."""
        real = _attributes(cls)
        self.stale += [f"{cls.__name__}.{name}" for name in names if name not in real]
        return [name for name in names if name in real]

    def set(self, cls, fake, **values):
        """Give ``fake`` the ``values`` the real ``cls`` has."""
        for name in self.known(cls, values):
            setattr(fake, name, values[name])

    def fake(self, cls, *, returns=None, real_methods=(), **values):
        """An autospec of ``cls`` holding ``values``. ``returns`` maps
        methods to their return values; ``real_methods`` run the real
        implementation over the fake's values.

        Every dataclass field not given is None (a NULL column), and
        each property holds what the real property computes from those
        values, so the fake reads like a model built from a row.
        """
        fake = create_autospec(_spec(cls), instance=True)
        if dataclasses.is_dataclass(cls):
            values = {field.name: None for field in dataclasses.fields(cls)} | values
        self.set(cls, fake, **values)
        if dataclasses.is_dataclass(cls):
            for name, prop in inspect.getmembers(cls, lambda a: isinstance(a, property)):
                if name not in values:
                    try:
                        computed = prop.fget(fake)
                    except Exception:
                        computed = None
                    setattr(fake, name, computed)
        returns = returns or {}
        for name in self.known(cls, returns):
            getattr(fake, name).return_value = returns[name]
        for name in self.known(cls, real_methods):
            getattr(fake, name).side_effect = functools.partial(getattr(cls, name), fake)
        self.fakes.append((cls, fake))
        return self.hook(fake, cls.__name__)

    def iterable(self, items):
        """A ``ModelIterable`` of ``items``, as the queries return."""
        items = list(items)
        fake = self.fake(ModelIterable, returns={
            "count": len(items),
            "exists": bool(items),
            "first": items[0] if items else None,
        })
        fake.__iter__.side_effect = lambda: iter(items)
        fake.__getitem__.side_effect = items.__getitem__
        fake.__len__.return_value = len(items)
        fake.__bool__.return_value = bool(items)
        return fake

    def location(self, cfi):
        """A ``Location`` holding what the real one parses from ``cfi``."""
        real = Location(cfi)
        fake = self.fake(Location, **{
            field.name: getattr(real, field.name) for field in dataclasses.fields(Location)
        })
        fake.__str__.return_value = str(real)
        fake.__bool__.return_value = bool(real)
        return fake


CHAPTER_TEXT = (
    "Chapter 1\n\nOpening words. a synthetic highlight sits here in chapter 1. "
    "Closing words."
)


def _fake_library(watch, tier="toc"):
    """An autospec of ``PyAppleBooks`` over a small library: a book
    with a readable EPUB and two annotations, a finished and an
    unstarted book, an orphan annotation and a collection. ``tier``
    picks how the reading position resolves (see TIERS).

    Returns ``(api, factories)``; ``factories`` maps each facade method
    the fake answers to a callable making what it returns.
    """
    chapters = [
        watch.fake(Chapter, id="chap1", title="Chapter 1", href="OEBPS/chap1.xhtml",
                   fragment="", order=1, depth=0),
        watch.fake(Chapter, id="chap2", title="Chapter 2", href="OEBPS/chap2.xhtml",
                   fragment="", order=2, depth=0),
    ]
    content = watch.fake(BookContent, returns={
        "list_chapters": chapters, "get_chapter": CHAPTER_TEXT,
    })

    def book(**values):
        return watch.fake(
            Book, real_methods=("format_progress_summary",),
            annotations=watch.iterable([]), collections=watch.iterable([]), **values,
        )

    synthetic = book(
        id=FAKE_IDS["book"], asset_id="ASSET1", title="Synthetic Book",
        author="Test Author", description="A synthetic description.", genre="Fiction",
        page_count=120, reading_progress=42.0, duration=3600.0, rating=4,
        creation_date=datetime(2026, 9, 1, 12, 0),
        last_opened_date=datetime(2026, 9, 20, 12, 0),
        purchased_date=datetime(2026, 9, 1, 12, 0),
    )
    finished = book(
        id=2, asset_id="ASSET2", title="Finished Book", author=None, genre="History",
        is_finished=True, reading_progress=100.0,
        finished_date=datetime(2026, 9, 10, 12, 0),
        last_opened_date=datetime(2026, 9, 10, 12, 0),
    )
    unstarted = book(id=3, asset_id="ASSET3", title="Unopened Book", author="Test Author")

    def annotation(**values):
        defaults = dict(
            asset_id="ASSET1", is_deleted=False, type=2, style=3, color="YELLOW",
            is_underline=False, note=None, book=synthetic,
            creation_date=datetime(2026, 9, 21, 12, 0),
            modification_date=datetime(2026, 9, 21, 12, 0),
        )
        return watch.fake(Annotation, **(defaults | values))

    highlight = annotation(
        id=FAKE_IDS["annotation"], selected_text="a synthetic highlight",
        representative_text="Opening words. a synthetic highlight sits here.",
        location=watch.location("epubcfi(/6/4[chap1]!/4/4,/1:0,/1:21)"),
    )
    note = annotation(
        id=11, selected_text="Opening words.", representative_text="Opening words.",
        note="my synthetic note", style=1, color="GREEN",
        creation_date=datetime(2026, 9, 22, 12, 0),
        location=watch.location("epubcfi(/6/6[chap2]!/4/2,/1:0,/1:14)"),
    )
    orphan = annotation(
        id=12, asset_id="ORPHAN", selected_text="orphan synthetic highlight",
        representative_text="orphan synthetic highlight", book=None, location=None,
        creation_date=datetime(2026, 9, 19, 12, 0),
    )
    bookmark = annotation(
        id=13, type=3, style=0, color=None, selected_text=None,
        representative_text=None,
        location=watch.location("epubcfi(/6/6[chap2]!/4/2/1:0)"),
    )
    collection = watch.fake(
        Collection, id=FAKE_IDS["collection"], title="Shelf", details="To read.",
        is_deleted=False, is_hidden=False, books=watch.iterable([synthetic, finished]),
    )
    watch.set(Book, synthetic, annotations=watch.iterable([highlight, note]),
              collections=watch.iterable([collection]))
    stats = watch.fake(
        LibraryStats, total_books=3, finished_books=1, in_progress_books=1,
        unstarted_books=1, total_annotations=3, orphan_annotations=1,
        annotations_per_book=((FAKE_IDS["book"], "Synthetic Book", 2),),
    )
    annotations = [note, highlight, orphan]

    factories = {
        "list_collections": lambda: watch.iterable([collection]),
        "get_collection_by_id": lambda: collection,
        "get_collection_by_title": lambda: watch.iterable([collection]),
        "create_collection": lambda: collection,
        "rename_collection": lambda: collection,
        "delete_collection": lambda: None,
        "add_book_to_collection": lambda: True,
        "remove_book_from_collection": lambda: True,
        "list_books": lambda: watch.iterable([synthetic, finished, unstarted]),
        "get_book_by_id": lambda: synthetic,
        "get_book_by_title": lambda: watch.iterable([synthetic]),
        "get_books_by_genre": lambda: watch.iterable([synthetic]),
        "get_books_in_progress": lambda: watch.iterable([synthetic]),
        "get_finished_books": lambda: watch.iterable([finished]),
        "get_unstarted_books": lambda: watch.iterable([unstarted]),
        "get_recently_read_books": lambda: watch.iterable([synthetic, finished]),
        "list_annotations": lambda: watch.iterable(annotations),
        "get_annotation_by_id": lambda: highlight,
        "get_annotations_by_color": lambda: watch.iterable([highlight, orphan]),
        "search_annotation_by_note": lambda: watch.iterable([note]),
        "search_annotation_by_text": lambda: list(annotations),
        "get_annotations_by_date_range": lambda: watch.iterable(annotations),
        "get_annotation_surrounding_text": lambda: (
            "Opening words. a synthetic highlight sits here in chapter 1."
        ),
        "get_book_content": lambda: content,
        "get_current_reading_chapter": lambda: chapters[0] if tier == "toc" else None,
        "get_current_reading_location": lambda: bookmark if tier == "cfi" else None,
        "get_library_stats": lambda: stats,
    }
    api = watch.hook(create_autospec(PyAppleBooks, instance=True), "PyAppleBooks")
    for name in watch.known(PyAppleBooks, factories):
        make = factories[name]
        getattr(api, name).side_effect = lambda *args, _make=make, **kwargs: _make()
    return api, factories


def _facade_calls(api) -> list:
    """The facade methods ``api`` was called with, each call checked
    against the real method's signature (autospec checks it too; this
    names the call when it doesn't fit)."""
    names = []
    for name, args, kwargs in api.mock_calls:
        if name.startswith("__"):
            continue
        method = getattr(PyAppleBooks, name)
        try:
            inspect.signature(method).bind(None, *args, **kwargs)
        except TypeError as e:
            pytest.fail(f"PyAppleBooks.{name}(*{args}, **{kwargs}): {e}")
        names.append(name)
    return names


def _conforms(value, hint) -> bool:
    """Whether ``value`` is of the annotated type ``hint``."""
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        return any(_conforms(value, arg) for arg in typing.get_args(hint))
    if hint is type(None):
        return value is None
    return isinstance(value, origin or hint)


# --------------------------------------------------------------------------
# 1. Every tool against autospecs of the real classes
# --------------------------------------------------------------------------

AUTOSPEC_CASES = (
    [(name, "toc") for name in _read_calls(FAKE_IDS)]
    + [(name, "toc") for name in _write_calls(FAKE_IDS)]
    + [("get_current_reading_position", tier) for tier in TIERS[1:]]
    + [(RESOURCE, tier) for tier in TIERS]
)


def test_every_tool_has_contract_arguments():
    """The tables above cover exactly the tools the server registers, so
    a new tool can't skip the contract tests."""
    registered = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    covered = set(_read_calls(FAKE_IDS)) | set(_write_calls(FAKE_IDS))
    assert registered == covered


@pytest.mark.parametrize("name, tier", AUTOSPEC_CASES,
                         ids=[f"{name}-{tier}" for name, tier in AUTOSPEC_CASES])
def test_tool_against_autospecced_library(name, tier, monkeypatch):
    """Each tool runs to a non-error result against autospecs of the
    real library: every facade call fits the real signature, and no
    attribute the real classes lack is read."""
    watch = _Watch()
    api, _ = _fake_library(watch, tier)
    monkeypatch.setattr(server, "apple_books", api)
    _enable_writes(monkeypatch)

    if name == RESOURCE:
        _read_resource()
    else:
        calls = _read_calls(FAKE_IDS) | _write_calls(FAKE_IDS)
        (result,) = _call_tools([(name, calls[name])])
        _ok_text(name, result)

    assert _facade_calls(api), f"{name} made no library call"
    assert watch.misses == [], "read attributes the real classes lack"


@pytest.mark.parametrize("tier", TIERS)
def test_fakes_match_the_library(tier):
    """The autospec runs are only as good as the fakes: every attribute
    and method they are given exists in the library, and each fake
    facade method returns the type the real one is annotated to
    return."""
    watch = _Watch()
    _, factories = _fake_library(watch, tier)
    assert watch.stale == [], "py-apple-books lacks these: renamed or dropped?"
    for name, make in factories.items():
        hint = typing.get_type_hints(getattr(PyAppleBooks, name)).get("return")
        if hint is None:  # unannotated (search_annotation_by_text: a list)
            continue
        value = make()
        assert _conforms(value, hint), f"{name} returns {hint}, the fake {value!r}"


def test_every_library_call_in_the_source_succeeds(monkeypatch):
    """Across all tools and reading-position tiers, every facade method
    the tool modules reference, and every library method they call on
    a model or result, is called at least once with arguments its real
    signature accepts. (A call that fails autospec's signature check is
    not recorded, so this also catches one inside a broad ``except``.)"""
    watch = _Watch()
    called = set()
    _enable_writes(monkeypatch)
    calls = list((_read_calls(FAKE_IDS) | _write_calls(FAKE_IDS)).items())
    for tier in TIERS:
        api, _ = _fake_library(watch, tier)
        monkeypatch.setattr(server, "apple_books", api)
        for name, result in zip([n for n, _ in calls], _call_tools(calls)):
            _ok_text(name, result)
        _read_resource()
        called.update(_facade_calls(api))

    uncalled = sorted(_source_facade_names(TOOL_MODULES) - called)
    assert uncalled == [], (
        "the tool modules call these facade methods, but no tool reached them "
        "in this test; extend _fake_library so one does"
    )

    model_calls = {
        (classes, name) for classes, name in _source_reads()
        if any(inspect.isfunction(getattr(cls, name, None)) for cls in classes)
    }
    assert model_calls, "the source scan found no model method calls"
    missed = [
        f"{'/'.join(cls.__name__ for cls in classes)}.{name}"
        for classes, name in model_calls
        if not any(
            cls in classes and name in _attributes(cls) and getattr(fake, name).called
            for cls, fake in watch.fakes
        )
    ]
    assert missed == [], "never called successfully on a fake"
    assert watch.misses == [], "read attributes the real classes lack"


# --------------------------------------------------------------------------
# 2. Every read tool end to end on a synthetic store
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_store(tmp_path_factory):
    """``seed_demo``'s library: books in every reading state, a readable
    and a DRM EPUB, collections, notes, orphans and tombstones."""
    root = tmp_path_factory.mktemp("contract")
    lib = FixtureLibrary.create(root / "home")
    seeded = seed_demo(lib, root / "work")
    return lib, seeded


@pytest.fixture
def demo(demo_store, monkeypatch):
    """The server reading the demo store through a real PyAppleBooks;
    yields the ids to call the tools with."""
    lib, seeded = demo_store
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    books, annotations = seeded["books"], seeded["annotations"]
    yield {
        "book": books["synthetic"]["id"],
        "collection": seeded["collections"]["shelf"]["id"],
        "annotation": annotations["highlight"],
        "chapter": "chap1",
        "book_without_file": books["finished"]["id"],
        "orphan": annotations["orphan"],
        "note": annotations["note"],
    }
    api.close()


# More realistic calls, on the corners of the demo library.
E2E_VARIANTS = {
    "list_annotations-book_without_file": (
        "list_annotations", lambda ids: {"book_id": ids["book_without_file"]}),
    "describe_annotation-orphan": (
        "describe_annotation", lambda ids: {"annotation_id": ids["orphan"]}),
    "describe_annotation-note": (
        "describe_annotation", lambda ids: {"annotation_id": ids["note"]}),
    "search_annotations-quote_and_percent": (
        "search_annotations", lambda ids: {"text": "Don't panic, it's 100%"}),
    "get_annotations_by_date_range-open_end": (
        "get_annotations_by_date_range", lambda ids: {"after": "2026-09-20"}),
    "list_all_books-second_page": (
        "list_all_books", lambda ids: {"limit": 2, "offset": 2}),
    "get_chapter_content-by_order": (
        "get_chapter_content", lambda ids: {"book_id": ids["book"], "chapter_id": "2"}),
}


@pytest.mark.parametrize("name", list(_read_calls(FAKE_IDS)))
def test_read_tool_end_to_end(name, demo):
    """Each read tool, with realistic arguments, returns a non-error
    text result from a real store."""
    (result,) = _call_tools([(name, _read_calls(demo)[name])])
    _ok_text(name, result)


@pytest.mark.parametrize("label", list(E2E_VARIANTS))
def test_read_tool_variant_end_to_end(label, demo):
    name, values = E2E_VARIANTS[label]
    (result,) = _call_tools([(name, values(demo))])
    _ok_text(label, result)


def test_resource_end_to_end(demo):
    _read_resource()


# Calls whose id names nothing in the demo store.
MISSING_IDS = {
    "describe_book": {"book_id": 999},
    "list_annotations": {"book_id": 999},
    "get_current_reading_position": {"book_id": 999},
    "list_book_chapters": {"book_id": 999},
    "get_chapter_content": {"book_id": 999, "chapter_id": "chap1"},
    "get_collection_books": {"collection_id": 999},
    "describe_collection": {"collection_id": 999},
    "describe_annotation": {"annotation_id": 999},
    "get_annotation_context": {"annotation_id": 999},
}


@pytest.mark.parametrize("name", list(MISSING_IDS))
def test_missing_id_is_handled(name, demo):
    """The not-found error the library raises for a missing id is one
    the tool handles: it answers, or raises a ToolError, but never lets
    the library's exception escape."""
    try:
        getattr(server, name)(**MISSING_IDS[name])
    except ToolError:
        pass


# --------------------------------------------------------------------------
# 3. What the source uses exists in the library
# --------------------------------------------------------------------------


def _parse(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_getattr(node) -> bool:
    """``getattr(obj, "literal"[, default])`` or ``hasattr(obj, "literal")``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("getattr", "hasattr")
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    )


def _source_facade_names(modules) -> set:
    """Attributes the given package modules read off the facade:
    ``apple_books.x``, ``api.x`` and ``<module>.apple_books.x``."""
    names = set()
    for module in modules:
        for node in ast.walk(_parse(PACKAGE / module)):
            if not isinstance(node, ast.Attribute):
                continue
            base = node.value
            if (isinstance(base, ast.Name) and base.id in ("apple_books", "api")) or (
                isinstance(base, ast.Attribute) and base.attr == "apple_books"
            ):
                names.add(node.attr)
    return names


# How the tool modules name library objects. ``c`` is a chapter in
# utils and a collection in server, so it may be either.
_NAMES = {
    "book": (Book,), "b": (Book,),
    "anno": (Annotation,), "annotation": (Annotation,), "a": (Annotation,),
    "top": (Annotation,), "bookmark": (Annotation,),
    "collection": (Collection,),
    "chapter": (Chapter,), "ch": (Chapter,), "c": (Chapter, Collection),
    "location": (Location,),
    "content": (BookContent,),
    "stats": (LibraryStats,),
}
# Attributes that lead from one library object to another.
_LINKS = {
    (Annotation, "location"): Location,
    (Annotation, "book"): Book,
    (Book, "annotations"): ModelIterable,
    (Book, "collections"): ModelIterable,
    (Collection, "books"): ModelIterable,
}


def _classes(node) -> tuple:
    """The library classes an expression evaluates to, judged by the
    variable names above; () for anything else."""
    if isinstance(node, ast.Name):
        return _NAMES.get(node.id, ())
    if isinstance(node, ast.Attribute):
        base, name = node.value, node.attr
    elif _is_getattr(node):
        base, name = node.args[0], node.args[1].value
    else:
        return ()
    return tuple(dict.fromkeys(
        _LINKS[(cls, name)] for cls in _classes(base) if (cls, name) in _LINKS
    ))


def _source_reads() -> set:
    """``{(classes, attribute)}``: every attribute the tool modules read
    off a library object, directly or through ``getattr``/``hasattr``."""
    reads = set()
    for module in TOOL_MODULES:
        for node in ast.walk(_parse(PACKAGE / module)):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                base, name = node.value, node.attr
            elif _is_getattr(node):
                base, name = node.args[0], node.args[1].value
            else:
                continue
            classes = _classes(base)
            if classes:
                reads.add((classes, name))
    return reads


def test_model_attributes_the_source_reads_exist():
    """Every attribute the tool modules read off a Book, Annotation,
    Collection, Location, Chapter, BookContent, LibraryStats or query
    result exists on the real class: a dataclass field, property,
    relation or method. ``getattr(obj, name, default)`` would hide a
    removed one at runtime, so this checks the names in the source."""
    reads = _source_reads()
    seen = {cls for classes, _ in reads for cls in classes}
    expected = {Book, Annotation, Collection, Location, Chapter, BookContent,
                LibraryStats, ModelIterable}
    assert expected <= seen, (
        f"the scan found no reads of {sorted(c.__name__ for c in expected - seen)}; "
        "the variable names in _NAMES may be stale"
    )
    missing = sorted(
        f"{'/'.join(cls.__name__ for cls in classes)}.{name}"
        for classes, name in reads
        if not any(name in _attributes(cls) for cls in classes)
    )
    assert missing == [], "read by the MCP, absent from py-apple-books"


def test_facade_methods_the_source_uses_exist():
    """Every ``PyAppleBooks`` attribute any package module uses is a
    public method of the installed library."""
    modules = sorted(path.name for path in PACKAGE.glob("*.py"))
    names = _source_facade_names(modules)
    assert {"list_books", "get_book_by_id", "list_annotations", "get_book_content"} <= names
    missing = sorted(
        name for name in names
        if name.startswith("_") or not callable(getattr(PyAppleBooks, name, None))
    )
    assert missing == [], "used by the MCP, absent from PyAppleBooks"


def test_library_imports_resolve():
    """Every name any package module imports from py_apple_books, at
    module level or inside a function, exists in the installed
    library."""
    def ours(module):
        return (module or "").split(".")[0] == "py_apple_books"

    imported = []
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ImportFrom) and ours(node.module):
                imported += [(path.name, node.module, alias.name) for alias in node.names]
            elif isinstance(node, ast.Import):
                imported += [(path.name, alias.name, None)
                             for alias in node.names if ours(alias.name)]
    assert imported, "no py_apple_books import found"
    missing = []
    for source, module, name in imported:
        try:
            target = importlib.import_module(module)
            if name not in (None, "*") and not hasattr(target, name):
                importlib.import_module(f"{module}.{name}")  # a submodule
        except ImportError:
            missing.append(f"{source}: {module}" + (f" {name}" if name else ""))
    assert missing == [], "imported by the MCP, absent from py-apple-books"
