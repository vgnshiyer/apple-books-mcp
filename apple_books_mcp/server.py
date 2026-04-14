import logging
import json
from mcp.types import (
    TextContent
)
from mcp.server.fastmcp import FastMCP
from py_apple_books import PyAppleBooks

logger = logging.getLogger("apple-books-mcp")

mcp = FastMCP("apple-books")
apple_books = PyAppleBooks()


def _format_book_with_progress(book) -> str:
    author = getattr(book, "author", None) or "Unknown Author"
    title = getattr(book, "title", None) or "Unknown Title"
    progress = book.format_progress_summary()
    return f"{title} by {author}\n{progress}"


def _get_book_title(annotation) -> str:
    book = getattr(annotation, "book", None)
    return getattr(book, "title", None) or "Unknown Book"


def _format_annotation_with_book(annotation, include_chapter: bool = False) -> str:
    annotation_text = getattr(annotation, "selected_text", None)
    book_title = _get_book_title(annotation)

    if include_chapter:
        chapter = getattr(annotation, "chapter", None)
        return f"{annotation_text} from {book_title}, Chapter: {chapter}\n"

    return f"{annotation_text} from {book_title}\n"


# -- Collections Tools --
@mcp.tool()
def list_all_collections() -> TextContent:
    """List all collections in my Apple Books library."""
    collections = apple_books.list_collections()
    collections_str = "\n".join([f"{str(collection)}\n" for collection in collections])
    return TextContent(
        type="text",
        text=f"Collections:\n{collections_str}"
    )


@mcp.tool()
def get_collection_books(collection_id: str) -> TextContent:
    """
    Get all books in a particular collection.

    Args:
        collection_id: The ID of the collection to get books from.
    """
    collection = apple_books.get_collection_by_id(collection_id)
    books_str = "\n".join([f"{str(book)}\n" for book in collection.books])
    return TextContent(
        type="text",
        text=f"Books:\n{books_str}"
    )


@mcp.tool()
def describe_collection(collection_id: str) -> TextContent:
    """
    Get details of a particular collection.

    Args:
        collection_id: The ID of the collection to get details for.
    """
    collection = apple_books.get_collection_by_id(collection_id)
    return TextContent(
        type="text",
        text=f"{json.dumps(collection.__dict__, default=str)}"
    )


# -- Books Tools --
@mcp.tool()
def list_all_books() -> TextContent:
    """List all books in my Apple Books library."""
    books = apple_books.list_books()
    books_str = "\n".join([f"{str(book)}\n" for book in books])
    return TextContent(
        type="text",
        text=f"Books:\n{books_str}"
    )


@mcp.tool()
def get_book_annotations(book_id: str) -> TextContent:
    """
    Get all annotations for a particular book.

    Args:
        book_id: The ID of the book to get annotations for.
    """
    book = apple_books.get_book_by_id(book_id)
    annotations_str = "\n".join([
        f"{annotation.selected_text}\n"
        for annotation in book.annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def describe_book(book_id: str) -> TextContent:
    """
    Get details of a particular book.

    Args:
        book_id: The ID of the book to get.
    """
    book = apple_books.get_book_by_id(book_id)
    return TextContent(
        type="text",
        text=f"{json.dumps(book.__dict__, default=str)}"
    )


@mcp.tool()
def search_books_by_title(title: str) -> TextContent:
    """
    Search for books by title.

    Args:
        title: The title to search for.
    """
    books = apple_books.get_book_by_title(title)
    books_str = "\n".join([f"{str(book)}\n" for book in books])
    return TextContent(
        type="text",
        text=f"Books:\n{books_str}"
    )


@mcp.tool()
def search_collections_by_title(title: str) -> TextContent:
    """
    Search for collections by title.

    Args:
        title: The title to search for.
    """
    collections = apple_books.get_collection_by_title(title)
    collections_str = "\n".join([f"{str(collection)}\n" for collection in collections])
    return TextContent(
        type="text",
        text=f"Collections:\n{collections_str}"
    )


# -- Reading Status Tools --
@mcp.tool()
def get_books_in_progress() -> TextContent:
    """Get all books currently being read (progress > 0% and < 100%)."""
    books = apple_books.get_books_in_progress()
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Books in progress:\n{books_str}"
    )


@mcp.tool()
def get_finished_books() -> TextContent:
    """Get all books that have been finished."""
    books = apple_books.get_finished_books()
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Finished books:\n{books_str}"
    )


@mcp.tool()
def get_unstarted_books() -> TextContent:
    """Get all books that haven't been started yet (0% progress)."""
    books = apple_books.get_unstarted_books()
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Unstarted books:\n{books_str}"
    )


@mcp.tool()
def get_recently_read_books() -> TextContent:
    """Get 10 most recently opened books, ordered by last opened date."""
    books = apple_books.get_recently_read_books()
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Recently read books:\n{books_str}"
    )


# -- Annotations Tools --
@mcp.tool()
def list_all_annotations() -> TextContent:
    """List all my annotations in my Apple Books library."""
    annotations = apple_books.list_annotations()
    annotations_str = "\n".join([
        f"ID: {annotation.id} - Selected Text: {annotation.selected_text}\n"
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def get_highlights_by_color(color: str) -> TextContent:
    """
    Get all annotations/highlights by color.

    Args:
        color: The color of the annotations/highlights.
    """
    annotations = apple_books.get_annotations_by_color(color)
    annotations_str = "\n".join([
        _format_annotation_with_book(annotation)
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def search_highlighted_text(text: str) -> TextContent:
    """
    Search for annotations/highlights by highlighted text.

    Args:
        text: The text to search for.
    """
    annotations = apple_books.search_annotation_by_highlighted_text(text)
    annotations_str = "\n".join([
        _format_annotation_with_book(annotation)
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def search_notes(note: str) -> TextContent:
    """
    Search for annotations by note.

    Args:
        note: The note to search for.
    """
    annotations = apple_books.search_annotation_by_note(note)
    annotations_str = "\n".join([
        f"{annotation.selected_text}\n"
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def full_text_search(text: str) -> TextContent:
    """
    Search for annotations by any text that contains the given text.

    Args:
        text: The text to search for.
    """
    annotations = apple_books.search_annotation_by_text(text)
    annotations_str = "\n".join([
        _format_annotation_with_book(annotation, include_chapter=True)
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def recent_annotations() -> TextContent:
    """
    Get 10 most recent annotations.
    """
    annotations = apple_books.list_annotations(limit=10, order_by="-creation_date")
    annotations_str = "\n".join([
        _format_annotation_with_book(annotation, include_chapter=True)
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def describe_annotation(annotation_id: str) -> TextContent:
    """
    Get details of a particular annotation.

    Args:
        annotation_id: The ID of the annotation to get.
    """
    annotation = apple_books.get_annotation_by_id(annotation_id)
    return TextContent(
        type="text",
        text=f"{json.dumps(annotation.__dict__, default=str)}"
    )


def serve():
    """Serve the Apple Books MCP server."""
    logger.info("--- Started Apple Books MCP server ---")
    mcp.run(transport="stdio")
