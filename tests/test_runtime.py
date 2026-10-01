"""Tools run in worker threads (``apple_books_mcp._runtime``).

The first tests drive a small FastMCP in-process; the stdio tests start
the real server and check what a host sees: a slow call no longer holds
up pings and other calls, and calls made at once return what the same
calls return one at a time on the event loop.
"""
import json
import logging
import threading
import time

import anyio
import pytest
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from py_apple_books.db import LibraryDB, default_library
from py_apple_books.testing import FixtureLibrary, seed_demo

from apple_books_mcp import _runtime
from tests.test_cancel_guard import _StdioServer


def _texts(result):
    # mcp >= 1.10 returns (content, structured) for a tool with a return
    # annotation.
    if isinstance(result, tuple):
        result = result[0]
    return [block.text for block in result]


def _call(server, name, arguments=None):
    return _texts(anyio.run(server.call_tool, name, arguments or {}))


def _toy_server():
    server = FastMCP("runtime-test")

    @server.tool()
    def where(x: int = 1):
        on_main = threading.current_thread() is threading.main_thread()
        return f"{x} {'main' if on_main else 'worker'}"

    @server.tool()
    def fails(kind: str = "value"):
        if kind == "tool":
            raise ToolError("explicit tool error")
        raise ValueError("bad input")

    @server.tool()
    async def already_async() -> str:
        return "async"

    @server.tool()
    def with_context(ctx: Context) -> str:
        return "context"

    @server.resource("test://where")
    def where_resource() -> str:
        return "main" if threading.current_thread() is threading.main_thread() else "worker"

    return server


def _tool(server, name):
    return server._tool_manager.get_tool(name)


@pytest.mark.parametrize("value, expected", [
    (None, 8), ("", 8), ("4", 4), (" 2 ", 2), ("0", 0),
    ("-1", 8), ("many", 8), ("1.5", 8),
])
def test_thread_count(monkeypatch, caplog, value, expected):
    if value is None:
        monkeypatch.delenv(_runtime.ENV_THREADS, raising=False)
    else:
        monkeypatch.setenv(_runtime.ENV_THREADS, value)
    with caplog.at_level(logging.WARNING, logger="apple-books-mcp"):
        assert _runtime.thread_count() == expected
    ignored = value not in (None, "") and expected == 8
    assert ("Ignoring" in caplog.text) == ignored


def test_install_moves_sync_tools_and_resources():
    server = _toy_server()
    assert _call(server, "where") == ["1 main"]
    already_async = _tool(server, "already_async").fn
    with_context = _tool(server, "with_context").fn

    assert _runtime.install(server, threads=2, deadline=None) == 2
    assert _tool(server, "where").is_async
    assert _tool(server, "fails").is_async
    # Left alone: already async, or using the FastMCP Context.
    assert _tool(server, "already_async").fn is already_async
    assert _tool(server, "with_context").fn is with_context
    assert not _tool(server, "with_context").is_async

    assert _call(server, "where", {"x": 5}) == ["5 worker"]
    contents = anyio.run(server.read_resource, "test://where")
    assert [c.content for c in contents] == ["worker"]
    assert _call(server, "already_async") == ["async"]
    assert _call(server, "with_context") == ["context"]

    # Idempotent: nothing left to move, nothing wrapped twice.
    fn = _tool(server, "where").fn
    assert _runtime.install(server, threads=2, deadline=None) == 0
    assert _tool(server, "where").fn is fn


def test_install_skips(monkeypatch):
    server = _toy_server()
    assert _runtime.install(server, threads=0) == 0
    assert not _tool(server, "where").is_async

    monkeypatch.setattr(_runtime, "_mcp_version", lambda: "2.2.0")
    assert _runtime.install(server, threads=2) == 0
    assert not _tool(server, "where").is_async

    monkeypatch.setattr(_runtime, "_mcp_version", lambda: "1.99.0")
    assert _runtime.install(object(), threads=2) == 0


def test_results_and_errors_are_unchanged():
    inline, threaded = _toy_server(), _toy_server()
    _runtime.install(threaded, threads=2, deadline=None)

    assert _call(inline, "where", {"x": 3}) == ["3 main"]
    assert _call(threaded, "where", {"x": 3}) == ["3 worker"]
    for kind in ("value", "tool"):
        errors = []
        for server in (inline, threaded):
            with pytest.raises(ToolError) as caught:
                _call(server, "fails", {"kind": kind})
            errors.append(str(caught.value))
        assert errors[0] == errors[1]
        assert errors[0].startswith("Error executing tool fails: ")
    # Arguments are still validated before the call.
    with pytest.raises(Exception, match="valid integer"):
        _call(threaded, "where", {"x": "three"})


def test_calls_run_at_once_up_to_the_limit():
    server = FastMCP("runtime-test")
    running, peak, lock = [0], [0], threading.Lock()

    @server.tool()
    def busy(seconds: float):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(seconds)  # the GIL is released, as during SQLite work
        with lock:
            running[0] -= 1
        return "done"

    _runtime.install(server, threads=3, deadline=None)

    async def main():
        gaps, elapsed = [], []
        done = anyio.Event()

        async def tick():
            # The loop keeps running while the calls are in threads.
            last = time.monotonic()
            while not done.is_set():
                await anyio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        async def calls():
            start = time.monotonic()
            async with anyio.create_task_group() as tg:
                for _ in range(6):
                    tg.start_soon(server.call_tool, "busy", {"seconds": 0.3})
            elapsed.append(time.monotonic() - start)
            done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(tick)
            tg.start_soon(calls)
        return elapsed[0], max(gaps)

    elapsed, longest_gap = anyio.run(main)
    assert peak[0] == 3
    assert 0.55 < elapsed < 1.5
    assert longest_gap < 0.2


def test_cancelled_call_returns_at_once_and_its_thread_finishes():
    server = FastMCP("runtime-test")
    finished = threading.Event()

    @server.tool()
    def slow():
        time.sleep(1.0)
        finished.set()
        return "late"

    _runtime.install(server, threads=1, deadline=None)

    async def main():
        start = time.monotonic()
        with anyio.move_on_after(0.1):
            await server.call_tool("slow", {})
        cancelled_after = time.monotonic() - start
        # The cancelled call gave up its turn: the next one starts at once.
        start = time.monotonic()
        with anyio.move_on_after(0.1):
            await server.call_tool("slow", {})
        return cancelled_after, time.monotonic() - start

    first, second = anyio.run(main)
    assert first < 0.5
    assert second < 0.5
    assert not finished.is_set()
    assert finished.wait(3)


@pytest.fixture
def fixture_library(tmp_path):
    lib = FixtureLibrary.create(tmp_path / "home")
    lib.populate(books=3, annotations_per_book=2)
    return lib


def test_deadline_stops_a_call_s_queries(fixture_library):
    # The library's own limit per query is 20 s; the call's deadline of
    # 0.5 s counts from the start of the call, and stops the query the
    # tool starts after 0.3 s of other work.
    db = LibraryDB(fixture_library.data_dir, query_timeout=20)
    server = FastMCP("runtime-test")

    @server.tool()
    def heavy():
        time.sleep(0.3)
        return db.execute(
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c "
            "LIMIT 1000000000) SELECT count(*) FROM c"
        )

    _runtime.install(server, threads=1, deadline=0.5)
    start = time.monotonic()
    try:
        with pytest.raises(ToolError, match=r"stopped \(limit 0.5 s\)"):
            _call(server, "heavy")
    finally:
        db.close()
    assert time.monotonic() - start < 2


def test_default_deadline_is_the_library_query_timeout(monkeypatch):
    seen = []
    monkeypatch.setattr(_runtime, "_in_thread",
                        lambda fn, limiter, deadline: seen.append(deadline) or fn)
    server = FastMCP("runtime-test")

    @server.tool()
    def tool():
        return "x"

    _runtime.install(server, threads=1)
    assert seen == [default_library().query_timeout]


def test_every_server_tool_moves(monkeypatch):
    # A tool written as ``async def`` (or wrapped in one) would stay on the
    # event loop and block it again: the server's tools must be plain
    # functions.
    from apple_books_mcp.server import mcp

    tools = mcp._tool_manager.list_tools()
    for item in tools + mcp._resource_manager.list_resources():
        for name in ("fn", "is_async"):
            if hasattr(item, name):
                monkeypatch.setattr(item, name, getattr(item, name))
    assert _runtime.install(mcp, threads=2, deadline=None) == len(tools)


# -- over stdio ---------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_home(tmp_path_factory):
    """A FixtureLibrary filled with py-apple-books' demo library."""
    root = tmp_path_factory.mktemp("demo")
    lib = FixtureLibrary.create(root / "home")
    seed_demo(lib, root / "work")
    return lib.root


def _started(env):
    server = _StdioServer(env=env)
    try:
        server.initialize()
    except BaseException:
        server.close()
        raise
    return server


def test_slow_call_does_not_hold_up_others(demo_home):
    server = _started({"HOME": str(demo_home)})
    try:
        slow = server.call_sleep(3.0)
        time.sleep(0.3)
        start = time.monotonic()
        ping = server.request("ping")
        listed = server.request("tools/list")
        stats = server.request("tools/call", {"name": "get_library_stats", "arguments": {}})
        books = server.request("tools/call", {"name": "list_all_books", "arguments": {}})
        for request_id in (ping, listed, stats, books):
            response = server.wait_for(request_id, timeout=1.5)
            assert response and "error" not in response, server.stderr()
            assert not response.get("result", {}).get("isError"), response
        fast = time.monotonic() - start
        assert fast < 1.5
        assert slow not in server.responses
        assert server.wait_for(slow, timeout=10), server.stderr()
    finally:
        server.close()


def test_on_the_event_loop_a_slow_call_holds_up_a_ping(demo_home):
    # What the test above guards against (and the behaviour with
    # APPLE_BOOKS_MCP_THREADS=0).
    server = _started({"HOME": str(demo_home), "APPLE_BOOKS_MCP_THREADS": "0"})
    try:
        slow = server.call_sleep(1.5)
        time.sleep(0.3)
        ping = server.request("ping")
        assert server.wait_for(ping, timeout=0.6) is None
        assert server.wait_for(ping, timeout=10), server.stderr()
        assert slow in server.responses
    finally:
        server.close()


def _schema_value(tool, name, value):
    """``value`` as the tool's schema types parameter ``name``."""
    schema = tool["inputSchema"]["properties"][name]
    return str(value) if schema.get("type") == "string" else value


def _calls(tools):
    """Tool calls covering every read tool that runs without arguments,
    and those that take an id or a search term, on the demo library."""
    by_name = {tool["name"]: tool for tool in tools}
    calls = [(name, {}) for name, tool in by_name.items()
             if name != "sleep" and not tool["inputSchema"].get("required")]
    with_args = [
        ("describe_book", {"book_id": 1}),
        ("describe_book", {"book_id": 999999}),
        ("list_annotations", {"book_id": 1}),
        ("list_book_chapters", {"book_id": 1}),
        ("get_current_reading_position", {"book_id": 1}),
        ("describe_annotation", {"annotation_id": 1}),
        ("get_annotation_context", {"annotation_id": 1}),
        ("search_annotations", {"text": "synthetic"}),
        ("search_notes", {"note": "note"}),
        ("get_highlights_by_color", {"color": "yellow"}),
        ("search_books_by_title", {"title": "Synthetic"}),
        ("get_books_by_genre", {"genre": "Fiction"}),
        ("describe_collection", {"collection_id": 9}),
        ("get_collection_books", {"collection_id": 9}),
        ("search_collections_by_title", {"title": "shelf"}),
    ]
    for name, arguments in with_args:
        if name in by_name:
            tool = by_name[name]
            calls.append((name, {k: _schema_value(tool, k, v) for k, v in arguments.items()}))
    return calls


def _result(response):
    assert response is not None
    return json.dumps(response.get("result", response.get("error")), sort_keys=True)


def test_calls_made_at_once_return_what_they_return_one_at_a_time(demo_home):
    # Baseline: the event loop, one call at a time (as before 0.9).
    inline = _started({"HOME": str(demo_home), "APPLE_BOOKS_MCP_THREADS": "0"})
    try:
        tools = inline.wait_for(inline.request("tools/list"), timeout=10)["result"]["tools"]
        calls = _calls(tools)
        expected = []
        for name, arguments in calls:
            request_id = inline.request("tools/call", {"name": name, "arguments": arguments})
            expected.append(_result(inline.wait_for(request_id, timeout=30)))
    finally:
        inline.close()
    assert len(calls) >= 20
    # Most calls must succeed for the comparison to mean something.
    assert sum('"isError": false' in result for result in expected) >= len(calls) - 3

    # Worker threads: every call twice, all sent at once (more calls
    # than threads, so some wait their turn).
    threaded = _started({"HOME": str(demo_home)})
    try:
        sent = [(index, threaded.request("tools/call", {"name": name, "arguments": arguments}))
                for _ in range(2) for index, (name, arguments) in enumerate(calls)]
        for index, request_id in sent:
            got = _result(threaded.wait_for(request_id, timeout=30))
            assert got == expected[index], calls[index]
    finally:
        threaded.close()
