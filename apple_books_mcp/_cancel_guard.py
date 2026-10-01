"""Keep a cancelled request from shutting down the stdio server (mcp 1.x).

In mcp 1.x, ``RequestResponder.__exit__`` exits the request's cancel
scope but throws away its return value, which says whether the scope
handled the cancellation. When a host cancels a request whose response
is already being sent (e.g. a call that was queued behind a slow tool),
the cancellation therefore escapes the request handler and cancels the
server's task group: the process stops answering and exits on the next
message it reads. Before mcp added its own check (it is in 1.30, not in
1.6), the same happens whenever a cancellation interrupts a call that
is awaiting something, such as a tool running in a worker thread
(``_runtime``), so tools only move to threads with this guard in place.

``install()`` replaces that ``__exit__`` with one that returns the
scope's verdict, and that treats a cancellation raised while closing an
already-answered request as handled (modelcontextprotocol/python-sdk
#2610).

It also replaces ``RequestResponder.respond``, for tools in worker
threads: when a cancel arrives just as the thread finishes, anyio
doesn't interrupt the handler (what it awaited is already done), so the
handler goes on to send the result of a request ``cancel()`` has already
answered, and ``respond``'s ``assert not self._completed`` takes the
server down (in every mcp 1.x). The replacement sends nothing then.

Each patch is applied only on mcp 1.x, and only if the method still uses
the internals it relies on; otherwise it logs at debug level and leaves
that method alone.
"""
import functools
import importlib.metadata
import logging

import anyio

logger = logging.getLogger("apple-books-mcp")

_PATCHED = "_apple_books_mcp_patch"


def _exit_cancel_scope(responder, exc_type, exc_val, exc_tb) -> bool:
    try:
        return responder._cancel_scope.__exit__(exc_type, exc_val, exc_tb)
    except BaseException as exc:
        if responder._completed and isinstance(
            exc, anyio.get_cancelled_exc_class()
        ):
            return True
        raise


def _responder_exit(self, exc_type, exc_val, exc_tb) -> bool:
    """RequestResponder.__exit__ that propagates the cancel scope's verdict."""
    try:
        if self._completed:
            self._on_complete(self)
    finally:
        self._entered = False
        handled = _exit_cancel_scope(self, exc_type, exc_val, exc_tb)
    return handled


setattr(_responder_exit, _PATCHED, True)


def _respond_unless_cancelled(original):
    """``original`` RequestResponder.respond, made to send nothing for a
    request that ``cancel()`` has already answered."""

    @functools.wraps(original)
    async def respond(self, response):
        if self._completed and self.cancelled:
            return
        await original(self, response)

    setattr(respond, _PATCHED, True)
    return respond


# (method, the RequestResponder attributes the replacement relies on, a
# function making the replacement from the current method). If upstream's
# method no longer refers to all of them, the internals have changed.
_PATCHES = (
    ("__exit__", {"_completed", "_on_complete", "_entered", "_cancel_scope"},
     lambda current: _responder_exit),
    ("respond", {"_completed", "cancelled"}, _respond_unless_cancelled),
)


def _mcp_version() -> str:
    try:
        return importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _responder_class(version):
    """mcp 1.x's RequestResponder, or None."""
    if not version.startswith("1."):
        logger.debug("mcp %s: cancel guard only applies to mcp 1.x, skipping", version)
        return None
    try:
        from mcp.shared.session import RequestResponder
    except ImportError:
        logger.debug("mcp %s: RequestResponder not found, skipping cancel guard", version)
        return None
    return RequestResponder


def _plan(responder):
    """The replacements still to make ({method: replacement}) and the
    methods whose internals have changed."""
    todo, changed = {}, []
    for name, required, replacement in _PATCHES:
        current = getattr(responder, name, None)
        if getattr(current, _PATCHED, False):
            continue
        code = getattr(current, "__code__", None)
        if code is None or not required <= set(code.co_names):
            changed.append(name)
        else:
            todo[name] = replacement(current)
    return todo, changed


def can_install() -> bool:
    """Whether :func:`install` would return True here; patches nothing."""
    responder = _responder_class(_mcp_version())
    return responder is not None and not _plan(responder)[1]


def install() -> bool:
    """Patch mcp 1.x's RequestResponder; return True if both patches are
    active. Idempotent."""
    version = _mcp_version()
    responder = _responder_class(version)
    if responder is None:
        return False
    todo, changed = _plan(responder)
    for name, method in todo.items():
        setattr(responder, name, method)
    if changed:
        logger.debug(
            "mcp %s: RequestResponder.%s has changed, skipping that part of "
            "the cancel guard",
            version, " and .".join(changed),
        )
        return False
    logger.debug("mcp %s: cancel guard installed", version)
    return True
