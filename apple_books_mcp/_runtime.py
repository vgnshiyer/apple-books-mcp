"""Run the server's sync tools in worker threads (mcp 1.x FastMCP).

FastMCP 1.x calls a plain ``def`` tool on the event loop, so one slow
call (a big search, parsing a large EPUB) holds up every other request,
pings included, and every Claude session sharing the process.
``install(server)`` turns each registered sync tool, and each sync
function resource, into a coroutine that runs the function in a worker
thread (``anyio.to_thread.run_sync``), so the loop keeps serving:

* At most ``APPLE_BOOKS_MCP_THREADS`` tool functions run at once
  (default 8, the size of py-apple-books' connection pool); other calls
  wait their turn. ``0`` keeps every tool on the event loop, as before
  0.9.
* The collection write tools (:data:`WRITE_TOOLS`) run one at a time,
  as they did on the event loop: py-apple-books reuses the backup taken
  before the first write of a burst, and that check isn't safe for
  writes made at once (each would take, and rotate in, a backup of its
  own).
* Each function runs under py-apple-books' ``query_deadline``, set to
  the library's query timeout (``APPLE_BOOKS_QUERY_TIMEOUT``, default
  30 s): a query still running, or still waiting for a connection, that
  long after the function started is stopped with ``QueryTimeoutError``.
* A cancelled call is answered at once ("Request cancelled"). If its
  function hasn't started yet, it never does. If it has, its thread
  can't be interrupted: it runs on in the background (a write still
  happens), its queries bounded by the deadline, its result dropped,
  and it keeps its turn until it is done. (Like any running thread, it
  also delays the process's exit until it is done.)

The function gets the arguments FastMCP validated, and what it returns
or raises reaches FastMCP unchanged, so results and errors are the same
as on the event loop. Tools that take a FastMCP ``Context`` stay on the
loop. ``install()`` only applies to mcp 1.x, and only if FastMCP's
internals are as expected (:func:`skip_reason`); otherwise it logs why
and leaves everything as it is.
"""
import contextlib
import functools
import importlib.metadata
import inspect
import itertools
import logging
import os
import threading

import anyio
import anyio.lowlevel
import anyio.to_thread
from anyio.lowlevel import RunVar

logger = logging.getLogger("apple-books-mcp")

ENV_THREADS = "APPLE_BOOKS_MCP_THREADS"
#: Calls run at once by default: py-apple-books' LibraryDB opens at most
#: 8 connections, so more threads would only wait for one.
DEFAULT_THREADS = 8

#: The tools that change the library; they run one at a time. So does
#: any tool annotated as not read-only (``readOnlyHint=False``).
WRITE_TOOLS = frozenset({
    "create_collection",
    "rename_collection",
    "delete_collection",
    "add_book_to_collection",
    "remove_book_from_collection",
})

# Held by a write tool's thread while its function runs. A lock in the
# thread rather than a limiter on the event loop: a cancelled write
# keeps it until it has finished, so the next write can't overlap it.
_WRITE_LOCK = threading.Lock()

# install()'s default deadline: the library's query timeout.
_LIBRARY_TIMEOUT = object()

_limiter_names = itertools.count()


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


class _Turns:
    """Who may run: ``threads`` calls at once.

    Two gates. On the event loop, a CapacityLimiter (one per loop) that a
    call holds while its thread runs; anyio frees it as soon as the call
    is cancelled. In the thread, a semaphore held until the function
    returns, so a cancelled call's function keeps its turn while it runs
    on in the background.
    """

    def __init__(self, threads: int):
        self.threads = threads
        self.running = threading.BoundedSemaphore(threads)
        # Older anyio releases (4.9, for one) keep a RunVar's value under
        # its name, so each _Turns needs a name of its own.
        self._limiter = RunVar(f"apple_books_mcp_limiter_{next(_limiter_names)}")

    def limiter(self):
        """The running event loop's CapacityLimiter, made on first use
        (anyio's own default thread limiter works the same way)."""
        try:
            return self._limiter.get()
        except LookupError:
            limiter = anyio.CapacityLimiter(self.threads)
            self._limiter.set(limiter)
            return limiter


def _in_thread(fn, turns, deadline, write=False):
    """``fn`` as a coroutine function that runs it in a worker thread,
    on one of ``turns``' turns and under ``query_deadline(deadline)``;
    with ``write``, also one write at a time."""
    from py_apple_books.db import query_deadline

    def run(kwargs, cancelled):
        with _WRITE_LOCK if write else contextlib.nullcontext(), turns.running:
            if cancelled.is_set():
                # Cancelled while waiting its turn: the host already has
                # its answer, and the result would be dropped.
                return None
            with query_deadline(deadline):
                return fn(**kwargs)

    @functools.wraps(fn)
    async def call(**kwargs):
        cancelled = threading.Event()
        try:
            result = await anyio.to_thread.run_sync(
                run, kwargs, cancelled, abandon_on_cancel=True, limiter=turns.limiter()
            )
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise
        # anyio doesn't interrupt a wait that is already over: a cancel
        # that arrived as the thread finished is raised here, like any
        # other, rather than the result being sent after "Request
        # cancelled".
        await anyio.lowlevel.checkpoint_if_cancelled()
        return result

    return call


def _is_sync(fn) -> bool:
    return callable(fn) and not inspect.iscoroutinefunction(fn)


def _writes(tool) -> bool:
    """Whether ``tool`` may change the library."""
    hints = getattr(tool, "annotations", None)
    return (
        getattr(tool, "name", None) in WRITE_TOOLS
        or getattr(hints, "readOnlyHint", None) is False
    )


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


def _parts(server):
    """``server``'s tools and resources, and FastMCP's FunctionResource;
    None if FastMCP's internals have changed."""
    try:
        from mcp.server.fastmcp.resources import FunctionResource

        tools = server._tool_manager.list_tools()
        resources = server._resource_manager.list_resources()
    except (AttributeError, ImportError):
        return None
    return tools, resources, FunctionResource


def skip_reason(server, threads=None):
    """Why :func:`install` would leave ``server``'s (a FastMCP's) tools
    on the event loop, or None if it would move them. Changes nothing.

    :param threads: calls run at once; default :func:`thread_count`.
    """
    version = _mcp_version()
    if not version.startswith("1."):
        return f"worker threads only apply to mcp 1.x, not {version}"
    if threads is None:
        threads = thread_count()
    if threads == 0:
        return f"{ENV_THREADS}=0"
    if "abandon_on_cancel" not in inspect.signature(anyio.to_thread.run_sync).parameters:
        return "this anyio is too old for worker threads"
    if _parts(server) is None:
        return f"mcp {version}'s FastMCP internals have changed"
    return None


def install(server, threads=None, deadline=_LIBRARY_TIMEOUT) -> int:
    """Run ``server``'s (a FastMCP's) sync tools and function resources
    in worker threads; return how many tools now do.

    :param threads: calls run at once; default :func:`thread_count`.
        0 changes nothing.
    :param deadline: seconds each call's queries may take, or None for
        no limit; default the library's query timeout.

    Idempotent: a tool already moved is async and is left alone.
    """
    if threads is None:
        threads = thread_count()
    reason = skip_reason(server, threads)
    if reason is not None:
        logger.info("Tools run on the event loop: %s", reason)
        return 0
    tools, resources, FunctionResource = _parts(server)

    if deadline is _LIBRARY_TIMEOUT:
        deadline = _library_timeout()
    turns = _Turns(threads)
    moved = 0
    for tool in tools:
        fn = getattr(tool, "fn", None)
        if (
            getattr(tool, "is_async", None) is False
            and getattr(tool, "context_kwarg", "missing") is None
            and _is_sync(fn)
            and _replace(
                tool, fn=_in_thread(fn, turns, deadline, write=_writes(tool)), is_async=True
            )
        ):
            moved += 1
    for resource in resources:
        fn = getattr(resource, "fn", None)
        if isinstance(resource, FunctionResource) and _is_sync(fn):
            _replace(resource, fn=_in_thread(fn, turns, deadline))

    logger.info(
        "%d tools run in worker threads, up to %d at a time (writes one at a "
        "time); %s",
        moved,
        threads,
        f"a call's queries are stopped after {deadline:g} s" if deadline is not None
        else "no time limit on a call's queries",
    )
    return moved
