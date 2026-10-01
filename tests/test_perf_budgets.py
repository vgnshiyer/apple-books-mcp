"""Performance budgets (G2.3): time, output size and SQL statements per
call for the read tools, on a synthetic library of 2,000 books and
50,000 annotations, plus the server's peak memory over stdio.

Slow and timing-based, so skipped unless APPLE_BOOKS_MCP_PERF=1; CI's
perf job runs them against the lockfile and against a fresh resolution
(what users get). The budgets are generous (several times what a CI
runner takes) so only a real regression trips them: a paged call that
scans the whole library, a query per row, a page that outgrows Claude
Code's output cap. Lower a budget when a change makes a tool cheaper;
raising one needs a reason.
"""
import asyncio
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary, seed_demo, write_epub

from apple_books_mcp import server
from apple_books_mcp.server import mcp

pytestmark = pytest.mark.skipif(
    os.environ.get("APPLE_BOOKS_MCP_PERF") != "1",
    reason="performance budgets run in CI's perf job; set APPLE_BOOKS_MCP_PERF=1",
)

ROOT = Path(__file__).resolve().parent.parent

BOOKS = 2_000
PER_BOOK = 25  # 50,000 annotations

# Claude Code truncates MCP output past 25k tokens (MAX_MCP_OUTPUT_TOKENS);
# at ~3.5 characters a token that is about this many characters.
MAX_CHARS = 87_500

# Characters per row of a book listing ("[id] title by author"),
# measured on a real library in September 2026: 63-68. populate()'s
# rows are about 37, which let the unpaged search_books_by_title pass
# at 76k characters on 2,000 books when a real library that size gets
# about 130k. _build() lengthens the synthetic titles and authors, and
# test_book_rows_are_realistic keeps them in this range. (Paged
# listings stop at the server's 40k-character output budget whatever
# the row length.)
BOOK_ROW_CHARS = (60, 75)

# Peak resident memory of the server process over the stdio calls below.
MAX_RSS_MB = 300

# Time and SQL statements for one call. The annotation tools look up
# each row's book separately (one query per distinct book on the page,
# so about 50 for a default page); batching that would cut them to a
# handful.
BOOK_CALL = (1.0, 5)
ANNOTATION_PAGE = (3.0, 60)
# Parsing an EPUB the first time (and importing the parser) is the slow part.
EPUB_CALL = (3.0, 5)

# (tool, arguments as a client sends them, (seconds, statements)).
# Arguments are callables of the library's ids.
CASES = [
    ("list_all_collections", lambda ids: {}, BOOK_CALL),
    ("get_collection_books", lambda ids: {"collection_id": str(ids["collection"])}, BOOK_CALL),
    ("describe_collection", lambda ids: {"collection_id": str(ids["collection"])}, BOOK_CALL),
    ("search_collections_by_title", lambda ids: {"title": "Shelf"}, BOOK_CALL),
    ("list_all_books", lambda ids: {}, BOOK_CALL),
    ("list_all_books", lambda ids: {"limit": 500, "offset": 1_000}, BOOK_CALL),
    ("describe_book", lambda ids: {"book_id": str(ids["heavy_book"])}, (1.0, 10)),
    # Matches every book: the one book listing without paging (see UNPAGED).
    ("search_books_by_title", lambda ids: {"title": "Book"}, BOOK_CALL),
    ("get_books_by_genre", lambda ids: {"genre": "Fiction"}, BOOK_CALL),
    ("get_books_in_progress", lambda ids: {}, BOOK_CALL),
    ("get_finished_books", lambda ids: {}, BOOK_CALL),
    ("get_unstarted_books", lambda ids: {}, BOOK_CALL),
    ("get_recently_read_books", lambda ids: {}, BOOK_CALL),
    ("list_all_annotations", lambda ids: {}, ANNOTATION_PAGE),
    ("list_all_annotations", lambda ids: {"offset": 40_000}, ANNOTATION_PAGE),
    ("list_annotations", lambda ids: {"book_id": ids["heavy_book"]}, (3.0, 10)),
    ("get_highlights_by_color", lambda ids: {"color": "yellow"}, ANNOTATION_PAGE),
    ("search_notes", lambda ids: {"note": "note"}, ANNOTATION_PAGE),
    ("search_annotations", lambda ids: {"text": "theme"}, ANNOTATION_PAGE),
    ("recent_annotations", lambda ids: {}, (3.0, 20)),
    ("get_annotations_by_date_range",
     lambda ids: {"after": "2023-03-20", "before": "2023-03-31"}, ANNOTATION_PAGE),
    ("describe_annotation", lambda ids: {"annotation_id": str(ids["highlight"])}, (1.0, 10)),
    ("get_annotation_context", lambda ids: {"annotation_id": ids["highlight"]}, (3.0, 10)),
    ("list_book_chapters", lambda ids: {"book_id": ids["long_book"]}, EPUB_CALL),
    ("get_chapter_content", lambda ids: {"book_id": ids["long_book"], "chapter_id": "c15"}, EPUB_CALL),
    ("get_current_reading_position", lambda ids: {"book_id": ids["epub_book"]}, (3.0, 10)),
    ("get_library_stats", lambda ids: {}, (3.0, 10)),
]


# Tools known to break the output budget on a library this size, with
# why. Their cases are strict xfails, so fixing the tool fails the test
# until it is taken off this list.
UNPAGED = {
    "search_books_by_title": "no limit/offset yet: a match-all search on 2,000 realistic "
                             "books is about 130k characters",
}


def _build(root: Path) -> dict:
    """The demo library (EPUB, notes, collections, edge cases) plus
    2,000 books with 25 highlights each; every 10th highlight has a
    note, a tenth of them belong to one book (a favourite, like real
    libraries have), and 10 collections hold 30 books each. A 30-chapter
    EPUB stands in for a full-length book."""
    lib = FixtureLibrary.create(root / "home")
    demo = seed_demo(lib, root / "work")
    first = max(demo["annotations"].values()) + 1
    made = lib.populate(books=BOOKS, annotations_per_book=PER_BOOK)
    heavy = made["books"][0]
    # populate()'s "Populated Book 12 by Author 5" is half as long as a
    # real book row; see BOOK_ROW_CHARS.
    lib.execute(
        "library",
        "UPDATE ZBKLIBRARYASSET SET ZTITLE = ZTITLE || ': Typical Subtitle', "
        "ZSORTTITLE = ZTITLE || ': Typical Subtitle', ZAUTHOR = 'Synthetic ' || ZAUTHOR, "
        "ZSORTAUTHOR = 'Synthetic ' || ZAUTHOR WHERE Z_PK >= ?",
        (heavy["id"],),
    )
    lib.execute(
        "annotations",
        "UPDATE ZAEANNOTATION SET ZANNOTATIONNOTE = 'synthetic note ' || Z_PK "
        "WHERE Z_PK >= ? AND Z_PK % 10 = 0",
        (first,),
    )
    lib.execute(
        "annotations",
        "UPDATE ZAEANNOTATION SET ZANNOTATIONASSETID = ? WHERE Z_PK >= ? AND Z_PK % 10 = 1",
        (heavy["asset_id"], first),
    )
    collection = None
    for n in range(10):
        collection = lib.add_collection(f"Synthetic Shelf {n}")
        for book in made["books"][n * 30:(n + 1) * 30]:
            lib.add_to_collection(collection, book)

    paragraph = "A long synthetic paragraph about the theme of the book. " * 8
    chapters = [(f"c{n}", f"Chapter {n}", [paragraph] * 100) for n in range(30)]
    epub = write_epub(root / "work" / "books" / "Long Book.epub", "Long Synthetic Book", chapters,
                      identifier="urn:uuid:00000000-0000-4000-8000-0000000000aa")
    long_book = lib.add_book("Long Synthetic Book", path=epub, progress=0.3, genre="History")
    return {
        "lib": lib,
        "heavy_book": heavy["id"],
        "epub_book": demo["books"]["synthetic"]["id"],
        "long_book": long_book["id"],
        "highlight": demo["annotations"]["highlight"],
        "collection": collection["id"],
    }


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("perf"))


class _Statements:
    """sqlite3 trace callback counting the SQL statements run."""

    def __init__(self):
        self.count = 0

    def __call__(self, statement):
        self.count += 1


@pytest.fixture(scope="module")
def statements(library):
    """Points the server at ``library`` and counts the SQL statements
    run on every connection py-apple-books opens."""
    counter = _Statements()
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(counter)
        return conn

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sqlite3, "connect", traced_connect)
        api = PyAppleBooks(data_dir=library["lib"].data_dir)
        mp.setattr(server, "apple_books", api)
        # Open the connection and read the schema up front, so the
        # first case measured isn't charged for them.
        _call("list_all_collections", {})
        before = counter.count
        _call("list_all_books", {"limit": 1})
        assert counter.count > before, (
            "no SQL statements traced: py-apple-books no longer connects through "
            "sqlite3.connect, so the statement budgets measure nothing")
        yield counter
        api.close()


def _call(name: str, arguments: dict) -> str:
    """Call a tool through FastMCP, as a client would; its text."""
    result = asyncio.run(mcp.call_tool(name, arguments))
    if isinstance(result, tuple):  # (content, structured) on newer mcp
        result = result[0]
    if isinstance(result, dict):
        return json.dumps(result)
    return "".join(getattr(block, "text", "") for block in result)


def _case_id(case) -> str:
    """The tool's name, plus its arguments when it is measured twice."""
    name, arguments, _ = case
    if sum(c[0] == name for c in CASES) == 1:
        return name
    return "-".join([name] + [f"{k}={v}" for k, v in arguments({}).items()])


def _param(case):
    marks = ()
    if case[0] in UNPAGED:
        marks = pytest.mark.xfail(strict=True, reason=UNPAGED[case[0]])
    return pytest.param(*case, id=_case_id(case), marks=marks)


@pytest.mark.parametrize("name, arguments, budget", [_param(c) for c in CASES])
def test_budget(library, statements, name, arguments, budget):
    seconds, max_statements = budget
    before = statements.count
    start = time.perf_counter()
    text = _call(name, arguments(library))
    elapsed = time.perf_counter() - start
    used = statements.count - before

    assert text.strip(), f"{name} returned nothing"
    assert len(text) <= MAX_CHARS, f"{name}: {len(text):,} chars (budget {MAX_CHARS:,})"
    assert elapsed <= seconds, f"{name}: {elapsed:.2f} s (budget {seconds} s)"
    assert used <= max_statements, f"{name}: {used} SQL statements (budget {max_statements})"


def test_book_rows_are_realistic(library, statements):
    """The output budgets only hold for real libraries if synthetic rows
    are as long as real ones (BOOK_ROW_CHARS)."""
    text = _call("list_all_books", {"limit": 500})
    rows = re.findall(r"^\[\d+\] ", text, re.M)
    per_row = len(text) / len(rows)
    low, high = BOOK_ROW_CHARS
    assert low <= per_row <= high, f"{per_row:.1f} characters per book row, not {low}-{high}"


def _stdio_server(home: Path):
    """The server started the way Claude starts it, reading the library
    under ``home``; reports its peak RSS on stderr when sent SIGTERM."""
    code = (
        "import os, resource, signal, sys\n"
        "def report(*_):\n"
        "    sys.stderr.write('MAXRSS %d\\n' % resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)\n"
        "    sys.stderr.flush()\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGTERM, report)\n"
        "from apple_books_mcp import main\n"
        "main(args=[])\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("APPLE_BOOKS_")}
    env["HOME"] = str(home)
    return subprocess.Popen(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_stdio_peak_memory(library):
    """The largest pages of the heaviest tools, over real stdio, keep
    the server process under MAX_RSS_MB."""
    proc = _stdio_server(library["lib"].root)
    watchdog = threading.Timer(120, proc.kill)
    watchdog.start()
    calls = [
        ("list_all_annotations", {"limit": 500}),
        ("search_annotations", {"text": "theme", "limit": 500}),
        ("get_highlights_by_color", {"color": "blue", "limit": 500}),
        ("get_annotations_by_date_range", {"after": "2023-03-01", "limit": 500}),
        ("list_all_books", {"limit": 500}),
        ("search_books_by_title", {"title": "Book"}),
        ("get_library_stats", {}),
    ]
    messages = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "perf-budgets", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ] + [
        {"jsonrpc": "2.0", "id": n, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}}
        for n, (name, arguments) in enumerate(calls, start=1)
    ]
    responses = {}
    try:
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            if "id" in message:
                line = proc.stdout.readline()
                assert line, "server closed stdout"
                response = json.loads(line)
                responses[response["id"]] = response
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=30)
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()

    for n, (name, _) in enumerate(calls, start=1):
        result = responses[n].get("result", {})
        assert not result.get("isError"), (name, result)
        text = "".join(block.get("text", "") for block in result.get("content", []))
        assert text, name
        if name not in UNPAGED:  # test_budget covers those
            assert len(text) <= MAX_CHARS, (name, len(text))

    match = re.search(r"^MAXRSS (\d+)$", stderr, re.M)
    assert match, stderr[-2000:]
    # ru_maxrss is in bytes on macOS and KiB on Linux.
    peak = int(match.group(1)) / (2**20 if sys.platform == "darwin" else 2**10)
    assert peak <= MAX_RSS_MB, f"server peak RSS {peak:.0f} MB (budget {MAX_RSS_MB} MB)"
