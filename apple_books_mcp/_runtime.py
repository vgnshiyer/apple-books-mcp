"""Run the server's sync tools in worker threads (mcp 1.x FastMCP).

FastMCP 1.x calls a plain ``def`` tool on the event loop, so one slow
call (a big search, parsing a large EPUB) holds up every other request,
pings included, and every Claude session sharing the process.
``install(server)`` turns each registered sync tool, and each sync
function resource, into a coroutine that runs the function in a worker
thread (``anyio.to_thread.run_sync``), so the loop keeps serving:

* At most ``APPLE_BOOKS_MCP_THREADS`` calls run at once (default 8, the
  size of py-apple-books' connection pool); the others wait their turn.
  ``0`` keeps every tool on the event loop, as before 0.9.
* Each call runs under py-apple-books' ``query_deadline``, set to the
  library's query timeout (``APPLE_BOOKS_QUERY_TIMEOUT``, default 30 s):
  a query still running, or still waiting for a connection, that long
  after the call started is stopped with ``QueryTimeoutError``.
* A cancelled call is answered at once ("Request cancelled") and gives
  up its turn. Its thread can't be interrupted: it runs on in the
  background, its queries bounded by the deadline, and its result is
  dropped. (Like any running thread, it also delays the process's exit
  until it is done.)

The function gets the arguments FastMCP validated, and what it returns
or raises reaches FastMCP unchanged, so results and errors are the same
as on the event loop. Tools that take a FastMCP ``Context`` stay on the
loop. ``install()`` only applies to mcp 1.x, and only if FastMCP's
internals are as expected; otherwise it logs at debug level and leaves
everything as it is.
"""
import functools
import importlib.metadata
import inspect
import logging
import os

import anyio
import anyio.lowlevel
import anyio.to_thread
from anyio.lowlevel import RunVar

logger = logging.getLogger("apple-books-mcp")

ENV_THREADS = "APPLE_BOOKS_MCP_THREADS"
#: Calls run at once by default: py-apple-books' LibraryDB opens at most
#: 8 connections, so more threads would only wait for one.
DEFAULT_THREADS = 8

# install()'s default deadline: the library's query timeout.
_LIBRARY_TIMEOUT = object()


def thread_count() -> int:
    """The number of calls ``APPLE_BOOKS_MCP_THREADS`` lets run at once.

    Unset or empty: :data:`DEFAULT_THREADS`. ``0``: none, tools run on
    the event loop. Anything but a whole number >= 0 is logged and
    ignored.
    """
    raw = os.environ.get(ENV_THREADS, "").strip()
    if not raw:
        return DEFAULT_THREADS
    try:
        count = int(raw)
    except ValueError:
        count = -1
    if count >= 0:
        return count
    logger.warning(
        "Ignoring %s=%r: expected a whole number of threads, or 0 to run "
        "tools on the event loop. Using %d.",
        ENV_THREADS, raw, DEFAULT_THREADS,
    )
    return DEFAULT_THREADS


def _mcp_version() -> str:
    try:
        return importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _library_timeout():
    """Seconds the default library lets a query run, or None."""
    from py_apple_books.db import default_library

    return default_library().query_timeout


def _limiter(total: int):
    """A function returning the event loop's CapacityLimiter of ``total``.

    The limiter is made on first use in each event loop (anyio's own
    default thread limiter works the same way): it belongs to the loop
    it was made in.
    """
    var = RunVar("apple_books_mcp_limiter")

    def get():
        try:
            return var.get()
        except LookupError:
            limiter = anyio.CapacityLimiter(total)
            var.set(limiter)
            return limiter

    return get


def _in_thread(fn, limiter, deadline):
    """``fn`` as a coroutine function that runs it in a worker thread,
    under ``query_deadline(deadline)``."""
    from py_apple_books.db import query_deadline

    def run(kwargs):
        with query_deadline(deadline):
            return fn(**kwargs)

    @functools.wraps(fn)
    async def call(**kwargs):
        result = await anyio.to_thread.run_sync(
            run, kwargs, abandon_on_cancel=True, limiter=limiter()
        )
        # anyio doesn't interrupt a wait that is already over: a cancel
        # that arrived as the thread finished is raised here, like any
        # other, rather than the result being sent after "Request
        # cancelled".
        await anyio.lowlevel.checkpoint_if_cancelled()
        return result

    return call


def _is_sync(fn) -> bool:
    return callable(fn) and not inspect.iscoroutinefunction(fn)


def _replace(obj, **values) -> bool:
    """Set ``values`` on ``obj``, all or none; whether they were set."""
    old = {name: getattr(obj, name) for name in values}
    try:
        for name, value in values.items():
            setattr(obj, name, value)
    except Exception:
        for name, value in old.items():
            try:
                setattr(obj, name, value)
            except Exception:
                pass
        return False
    return True


def install(server, threads=None, deadline=_LIBRARY_TIMEOUT) -> int:
    """Run ``server``'s (a FastMCP's) sync tools and function resources
    in worker threads; return how many tools now do.

    :param threads: calls run at once; default :func:`thread_count`.
        0 changes nothing.
    :param deadline: seconds each call's queries may take, or None for
        no limit; default the library's query timeout.

    Idempotent: a tool already moved is async and is left alone.
    """
    version = _mcp_version()
    if not version.startswith("1."):
        logger.debug("mcp %s: worker threads only apply to mcp 1.x, skipping", version)
        return 0
    if threads is None:
        threads = thread_count()
    if threads == 0:
        logger.info("%s=0: tools run on the event loop", ENV_THREADS)
        return 0
    if "abandon_on_cancel" not in inspect.signature(anyio.to_thread.run_sync).parameters:
        logger.debug("anyio too old for abandon_on_cancel, tools stay on the event loop")
        return 0
    try:
        from mcp.server.fastmcp.resources import FunctionResource

        tools = server._tool_manager.list_tools()
        resources = server._resource_manager.list_resources()
    except (AttributeError, ImportError):
        logger.debug("mcp %s: FastMCP internals have changed, tools stay on the event loop", version)
        return 0

    if deadline is _LIBRARY_TIMEOUT:
        deadline = _library_timeout()
    limiter = _limiter(threads)
    moved = 0
    for tool in tools:
        fn = getattr(tool, "fn", None)
        if (
            getattr(tool, "is_async", None) is False
            and getattr(tool, "context_kwarg", "missing") is None
            and _is_sync(fn)
            and _replace(tool, fn=_in_thread(fn, limiter, deadline), is_async=True)
        ):
            moved += 1
    for resource in resources:
        fn = getattr(resource, "fn", None)
        if isinstance(resource, FunctionResource) and _is_sync(fn):
            _replace(resource, fn=_in_thread(fn, limiter, deadline))

    logger.info(
        "%d tools run in worker threads, up to %d at a time; %s",
        moved,
        threads,
        f"a call's queries are stopped after {deadline:g} s" if deadline is not None
        else "no time limit on a call's queries",
    )
    return moved
