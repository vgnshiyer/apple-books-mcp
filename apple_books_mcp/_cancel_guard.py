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
#2610). It only patches mcp 1.x, and only if ``__exit__`` still uses the
internals this relies on; otherwise it logs at debug level and does
nothing.
"""
import importlib.metadata
import logging

import anyio

logger = logging.getLogger("apple-books-mcp")

# The replacement uses these RequestResponder attributes; if upstream's
# __exit__ no longer refers to all of them, the internals have changed.
_REQUIRED_NAMES = {"_completed", "_on_complete", "_entered", "_cancel_scope"}


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


_responder_exit._apple_books_mcp_patch = True


def _mcp_version() -> str:
    try:
        return importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def install() -> bool:
    """Patch mcp 1.x's RequestResponder; return True if the patch is active."""
    version = _mcp_version()
    if not version.startswith("1."):
        logger.debug("mcp %s: cancel guard only applies to mcp 1.x, skipping", version)
        return False
    try:
        from mcp.shared.session import RequestResponder
    except ImportError:
        logger.debug("mcp %s: RequestResponder not found, skipping cancel guard", version)
        return False

    current = RequestResponder.__exit__
    if getattr(current, "_apple_books_mcp_patch", False):
        return True
    code = getattr(current, "__code__", None)
    if code is None or not _REQUIRED_NAMES <= set(code.co_names):
        logger.debug(
            "mcp %s: RequestResponder.__exit__ has changed, skipping cancel guard",
            version,
        )
        return False

    RequestResponder.__exit__ = _responder_exit
    logger.debug("mcp %s: cancel guard installed", version)
    return True
