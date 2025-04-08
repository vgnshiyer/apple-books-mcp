import click
import logging
import sys
try:
    from server import serve
except Exception:
    from .server import serve

__version__ = "0.1.3"


@click.command()
@click.option("-v", "--verbose", count=True)
def main(verbose: int) -> None:
    """Apple Books MCP Server"""
    logging_level = logging.WARN
    if verbose == 1:
        logging_level = logging.INFO
    elif verbose >= 2:
        logging_level = logging.DEBUG

    logging.basicConfig(level=logging_level, stream=sys.stderr)
    serve()


if __name__ == "__main__":
    main()
