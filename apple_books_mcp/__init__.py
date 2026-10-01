import click
import importlib.metadata
import logging
import os
import platform
import sys

__version__ = "0.8.4"

logger = logging.getLogger("apple-books-mcp")

ENV_ENABLE_WRITES = "APPLE_BOOKS_MCP_ENABLE_WRITES"


def _writes_from_env() -> bool:
    """APPLE_BOOKS_MCP_ENABLE_WRITES as set by --enable-writes ("1") or by
    a client's boolean setting (Claude Desktop's extension sends "true")."""
    value = os.environ.get(ENV_ENABLE_WRITES, "")
    return value.strip().lower() in ("1", "true", "yes")


def _dist_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _version_text() -> str:
    return (
        f"apple-books-mcp {__version__} (mcp {_dist_version('mcp')}, "
        f"py-apple-books {_dist_version('py-apple-books')}, "
        f"Python {platform.python_version()})"
    )


def _print_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(_version_text())
    ctx.exit()


def _configure_logging(verbose: int) -> None:
    """Log to stderr: warnings by default, -v for info, -vv for debug."""
    logging_level = logging.WARNING
    if verbose == 1:
        logging_level = logging.INFO
    elif verbose >= 2:
        logging_level = logging.DEBUG

    # FastMCP configures root logging (INFO) when the server module is
    # imported, so force=True is needed for the chosen level to apply.
    logging.basicConfig(
        level=logging_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


@click.command()
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Log more to stderr: -v for info, -vv for debug.",
)
@click.option(
    "--enable-writes",
    is_flag=True,
    default=False,
    help=(
        "Allow collection write tools (create/rename/delete collections, "
        "add/remove books) to modify the Apple Books library. Off by "
        "default; when off the tools explain how to enable them."
    ),
)
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_version,
    help="Show the apple-books-mcp, mcp and py-apple-books versions and exit.",
)
@click.option(
    "--doctor",
    is_flag=True,
    default=False,
    help=(
        "Check that the server can run here and read the Apple Books "
        "library, print what was found and exit (1 if a check failed). "
        "Reads only; reports counts, never titles or text."
    ),
)
def main(verbose: int, enable_writes: bool, doctor: bool) -> None:
    """Apple Books MCP Server"""
    if enable_writes:
        os.environ[ENV_ENABLE_WRITES] = "1"

    if doctor:
        from apple_books_mcp import doctor as _doctor

        _configure_logging(verbose)
        sys.exit(_doctor.run())

    # Imported here so --help, --version and --doctor work even when the
    # server can't be imported (e.g. an incompatible mcp release).
    from apple_books_mcp import _cancel_guard, _runtime
    from apple_books_mcp.server import mcp, serve

    _configure_logging(verbose)
    writes = _writes_from_env()
    logger.info(
        "%s, collection writes %s",
        _version_text(),
        "enabled" if writes else "disabled",
    )

    # Without this, serverInfo.version reports the mcp SDK's version.
    mcp._mcp_server.version = __version__
    # Tools move to worker threads only with the cancel guard in place:
    # older mcp 1.x releases (1.6, for one) let the cancellation of any
    # call that isn't blocking the event loop shut the server down, and
    # every 1.x does when a cancel arrives just as a tool's thread
    # finishes. (doctor._check_server reports the outcome of this.)
    if _cancel_guard.install():
        _runtime.install(mcp)
    else:
        logger.info("Tools run on the event loop: no cancel guard for this mcp")
    serve()


if __name__ == "__main__":
    main()
