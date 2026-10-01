"""``apple-books-mcp --doctor``: can the server run here and read the library?

Prints one line per check and returns the exit status: 1 if a check
failed (the server can't start, or can't read the library), else 0.

It only reads. It never writes to the library or creates files, and it
reports counts, never book titles or text. Paths are shown with the
home folder as ``~``.
"""
import logging
import os
import platform
import sys
import time

from apple_books_mcp import ENV_ENABLE_WRITES, _dist_version, _version_text, _writes_from_env

# py-apple-books' location and timeout variables (py_apple_books.db.client).
_LIBRARY_ENV = ("APPLE_BOOKS_DATA_DIR", "APPLE_BOOKS_LIBRARY_DB", "APPLE_BOOKS_ANNOTATION_DB")
_TIMEOUT_ENV = "APPLE_BOOKS_QUERY_TIMEOUT"

OK, NOTE, WARN, FAIL = "ok", "note", "warn", "FAIL"

_ACCESS_DENIED_HELP = (
    'macOS asks once per program whether it may "access data from other apps" '
    "(Apple Books keeps its library in its own app container), and this program "
    "was refused.",
    "Run by hand, this check runs as your terminal app; the server Claude starts "
    "runs as uvx (Claude Desktop) or claude (Claude Code), which macOS asks about "
    "separately.",
    "To be asked again: run `tccutil reset SystemPolicyAppData`, restart the "
    "program (quit and reopen Claude) and click Allow. Adding the program to "
    "System Settings > Privacy & Security > Full Disk Access also works, but "
    "grants far more access.",
)
_NOT_FOUND_HELP = (
    "Open Apple Books once on this Mac, as this user, so it creates its library; "
    "then run this again.",
)
_SERVER_IMPORT_HELP = (
    "If this names mcp.server.fastmcp, an mcp 2.x release was installed: "
    "apple-books-mcp needs mcp 1.x. Run `uv cache clean apple-books-mcp` and "
    "check `uvx apple-books-mcp --version`.",
)


def short_path(path) -> str:
    """``path`` with the home folder shown as ``~``."""
    text = os.fspath(path)
    home = os.path.expanduser("~").rstrip(os.sep)
    if home and (text == home or text.startswith(home + os.sep)):
        return "~" + text[len(home):]
    return text


def _tilde(text: str) -> str:
    """``text`` with paths in the home folder shown as ``~/...``."""
    home = os.path.expanduser("~").rstrip(os.sep)
    return text.replace(home + os.sep, "~" + os.sep) if home else text


def _error(e: BaseException) -> str:
    return f"{type(e).__name__}: {_tilde(str(e))}"


class _Report:
    """Checks printed as they run, and the failures and warnings counted."""

    def __init__(self, out):
        self.out = out
        self.failures = 0
        self.warnings = 0

    def __call__(self, status: str, message: str, details=()) -> None:
        if status == FAIL:
            self.failures += 1
        elif status == WARN:
            self.warnings += 1
        print(f"  {status:<5} {message}", file=self.out)
        for detail in details:
            print(f"        {detail}", file=self.out)
        self.out.flush()


class _Collect(logging.Handler):
    """Keeps py-apple-books' warnings, to report them as checks."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        try:
            self.messages.append(_tilde(record.getMessage()))
        except Exception:
            pass


def _check_environment(report: _Report) -> None:
    for name in ("mcp", "py-apple-books"):
        if _dist_version(name) == "not installed":
            report(FAIL, f"{name} is not installed")
    mcp_version = _dist_version("mcp")
    if mcp_version != "not installed" and not mcp_version.startswith("1."):
        report(FAIL, f"mcp {mcp_version} is installed; apple-books-mcp needs mcp 1.x")
    report(NOTE, f"Python at {short_path(sys.executable)}")
    macos = _macos_version()
    if macos is not None:
        report(OK, f"macOS {macos or 'version unknown'}")
    else:
        report(WARN, f"not macOS ({platform.system() or sys.platform}): the library is "
                     "only found where APPLE_BOOKS_DATA_DIR points")


def _macos_version():
    """The macOS version ('' if unknown), or None off macOS."""
    return platform.mac_ver()[0] if sys.platform == "darwin" else None


def _check_server(report: _Report, writes: bool) -> None:
    try:
        from apple_books_mcp import server
    except Exception as e:
        report(FAIL, f"the server can't load: {_error(e)}", _SERVER_IMPORT_HELP)
        return
    try:
        tools = f"{len(server.mcp._tool_manager.list_tools())} tools"
    except Exception:
        tools = "tools unknown"
    state = "on" if writes else "off (add --enable-writes to the server's args to turn them on)"
    report(OK, f"the server loads ({tools}); collection writes are {state}")

    from apple_books_mcp import _cancel_guard, _runtime

    # What the server's start-up (apple_books_mcp.main) would do, worked
    # out without patching or moving anything.
    threads = _runtime.thread_count()
    reason = _runtime.skip_reason(server.mcp, threads)
    if reason is None and not _cancel_guard.can_install():
        reason = f"no cancel guard for mcp {_dist_version('mcp')}"
    if reason is None:
        report(NOTE, f"tool calls run in worker threads, up to {threads} at a time "
                     f"({_runtime.ENV_THREADS}); collection writes one at a time")
    else:
        report(NOTE, f"tool calls run one at a time on the event loop ({reason})")


def _check_library(report: _Report, writes: bool) -> None:
    try:
        from py_apple_books import PyAppleBooks
        from py_apple_books.exceptions import (
            LibraryAccessDeniedError,
            LibraryNotFoundError,
        )
    except Exception as e:
        report(FAIL, f"py-apple-books can't load: {_error(e)}")
        return

    for name in _LIBRARY_ENV:
        value = os.environ.get(name)
        if value:
            report(NOTE, f"{name}={short_path(value)}: read instead of Apple Books' own library")

    api = PyAppleBooks()
    try:
        info = api.store_info()
    except LibraryAccessDeniedError as e:
        where = f" ({short_path(e.path)})" if getattr(e, "path", None) else ""
        report(FAIL, f"macOS denied access to the Apple Books library{where}",
               _ACCESS_DENIED_HELP)
        return
    except LibraryNotFoundError as e:
        where = f" in {short_path(e.path)}" if getattr(e, "path", None) else ""
        report(FAIL, f"no Apple Books library store found{where}", _NOT_FOUND_HELP)
        return
    except Exception as e:
        report(FAIL, f"can't read the library: {_error(e)}")
        return

    report(OK, f"library store: {short_path(info.library_path)}")
    if info.annotation_path is not None:
        report(OK, f"annotation store: {short_path(info.annotation_path)}")
    else:
        report(WARN, "no annotation store: books and collections work, highlights and "
                     "notes don't. Opening a book in Apple Books once creates it.")
    for kind, chosen in (("library", info.library_path), ("annotations", info.annotation_path)):
        others = [p.name for p in info.candidates.get(kind, ()) if p != chosen]
        if others:
            report(NOTE, f"other {kind} store files next to it: {', '.join(others)}")
    # Without an annotation store every Annotation field is "missing";
    # the warning above already says so.
    missing = [f"{model}.{field}" for model, fields in info.missing_columns.items()
               if not (model == "Annotation" and info.annotation_path is None)
               for field in fields]
    if missing:
        report(NOTE, f"this Apple Books version has no column for {', '.join(missing)} "
                     "(read as empty)")
    limit = (f"queries stop after {info.query_timeout:g} s" if info.query_timeout
             else "no query time limit")
    report(NOTE, f"SQLite {info.sqlite_version}; {limit} ({_TIMEOUT_ENV})")

    start = time.monotonic()
    try:
        books = sum(api.count_books_by_status().values())
        annotations = api.count_annotations() if info.annotation_path is not None else None
        collections = api.list_collections().count()
    except Exception as e:
        report(FAIL, f"reading the library failed: {_error(e)}")
        return
    counts = [f"{books:,} books"]
    if annotations is not None:
        counts.append(f"{annotations:,} highlights and notes")
    counts.append(f"{collections:,} collections")
    report(OK, f"read {', '.join(counts)} in {time.monotonic() - start:.2f} s")

    if writes:
        _check_backups(report, info.backup_dir)


def _check_backups(report: _Report, backup_dir) -> None:
    """Whether collection writes could back up the library, without
    creating anything: the backup folder, or the nearest folder above
    it that exists, must be writable."""
    folder = backup_dir
    while not os.path.exists(folder) and folder.parent != folder:
        folder = folder.parent
    if os.access(folder, os.W_OK | os.X_OK):
        report(OK, f"backups before each write go to {short_path(backup_dir)}")
    else:
        report(WARN, f"can't create backups in {short_path(backup_dir)}: collection "
                     "writes will fail")


def _check_books_app(report: _Report, writes: bool) -> None:
    try:
        from py_apple_books.write_safety import books_is_running
        running = books_is_running()
    except Exception as e:
        report(WARN if writes else NOTE,
               f"can't tell whether Apple Books is running: {_tilde(str(e))}")
        return
    if not running:
        report(NOTE, "Apple Books is not running")
    elif writes:
        report(WARN, "Apple Books is running: collection writes are refused until you "
                     "quit it (Cmd-Q)")
    else:
        report(NOTE, "Apple Books is running (only collection writes need it closed)")


def run(out=None) -> int:
    """Run every check, print the report to ``out`` (stdout) and return
    the exit status: 1 if a check failed, else 0."""
    out = sys.stdout if out is None else out
    writes = _writes_from_env()
    report = _Report(out)
    print(_version_text(), file=out)

    library_logger = logging.getLogger("py_apple_books")
    collect = _Collect()
    propagate = library_logger.propagate
    library_logger.addHandler(collect)
    library_logger.propagate = False
    try:
        _check_environment(report)
        _check_server(report, writes)
        _check_library(report, writes)
        for message in collect.messages:
            report(WARN, f"py-apple-books: {message}")
        _check_books_app(report, writes)
    finally:
        library_logger.removeHandler(collect)
        library_logger.propagate = propagate

    if report.failures:
        print(f"{report.failures} problem{'s' if report.failures != 1 else ''} found.", file=out)
        return 1
    if report.warnings:
        print(f"No problems found ({report.warnings} warning"
              f"{'s' if report.warnings != 1 else ''}).", file=out)
    else:
        print("No problems found.", file=out)
    return 0
