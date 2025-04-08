import logging
from mcp.types import (
    TextContent
)
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("apple-books-mcp")

mcp = FastMCP("apple-books-mcp")


def serve():
    """Serve the Apple Books MCP server."""
    logger.info("--- Started Apple Books MCP server ---")
    mcp.run(transport="stdio")
