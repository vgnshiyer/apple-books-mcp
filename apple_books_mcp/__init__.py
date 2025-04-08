import click
import logging
import sys
from server import serve


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
