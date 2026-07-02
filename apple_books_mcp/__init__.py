import click
import logging
import os
import sys
try:
    from server import serve
except Exception:
    from .server import serve

__version__ = "0.8.0"


@click.command()
@click.option("-v", "--verbose", count=True)
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
def main(verbose: int, enable_writes: bool) -> None:
    """Apple Books MCP Server"""
    logging_level = logging.WARN
    if verbose == 1:
        logging_level = logging.INFO
    elif verbose >= 2:
        logging_level = logging.DEBUG

    if enable_writes:
        os.environ["APPLE_BOOKS_MCP_ENABLE_WRITES"] = "1"

    logging.basicConfig(level=logging_level, stream=sys.stderr)
    serve()


if __name__ == "__main__":
    main()
