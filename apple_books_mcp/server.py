import logging
import json
from datetime import datetime
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
def list_all_collections(limit: int = None) -> TextContent:
    """
    List all collections in my Apple Books library.

    Args:
        limit: Maximum number of collections to return.
    """
    collections = apple_books.list_collections(limit=limit)
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
def list_all_books(limit: int = None) -> TextContent:
    """
    List all books in my Apple Books library.

    Args:
        limit: Maximum number of books to return.
    """
    books = apple_books.list_books(limit=limit)
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
def get_books_in_progress(limit: int = None) -> TextContent:
    """
    Get all books currently being read (progress > 0% and < 100%).

    Args:
        limit: Maximum number of books to return.
    """
    books = apple_books.get_books_in_progress(limit=limit)
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Books in progress:\n{books_str}"
    )


@mcp.tool()
def get_finished_books(limit: int = None) -> TextContent:
    """
    Get all books that have been finished.

    Args:
        limit: Maximum number of books to return.
    """
    books = apple_books.get_finished_books(limit=limit)
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Finished books:\n{books_str}"
    )


@mcp.tool()
def get_unstarted_books(limit: int = None) -> TextContent:
    """
    Get all books that haven't been started yet (0% progress).

    Args:
        limit: Maximum number of books to return.
    """
    books = apple_books.get_unstarted_books(limit=limit)
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Unstarted books:\n{books_str}"
    )


@mcp.tool()
def get_recently_read_books(limit: int = 10) -> TextContent:
    """
    Get most recently opened books, ordered by last opened date.

    Args:
        limit: Maximum number of books to return. Defaults to 10.
    """
    books = apple_books.get_recently_read_books(limit=limit)
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
def list_all_annotations(limit: int = None) -> TextContent:
    """
    List all my annotations in my Apple Books library.

    Args:
        limit: Maximum number of annotations to return.
    """
    annotations = apple_books.list_annotations(limit=limit)
    annotations_str = "\n".join([
        f"ID: {annotation.id} - Selected Text: {annotation.selected_text}\n"
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def get_highlights_by_color(color: str, limit: int = None) -> TextContent:
    """
    Get all annotations/highlights by color.

    Args:
        color: The color of the annotations/highlights.
        limit: Maximum number of annotations to return.
    """
    annotations = apple_books.get_annotations_by_color(color, limit=limit)
    annotations_str = "\n".join([
        _format_annotation_with_book(annotation)
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def search_highlighted_text(text: str, limit: int = None) -> TextContent:
    """
    Search for annotations/highlights by highlighted text.

    Args:
        text: The text to search for.
        limit: Maximum number of annotations to return.
    """
    annotations = apple_books.search_annotation_by_highlighted_text(text, limit=limit)
    annotations_str = "\n".join([
        _format_annotation_with_book(annotation)
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def search_notes(note: str, limit: int = None) -> TextContent:
    """
    Search for annotations by note.

    Args:
        note: The note to search for.
        limit: Maximum number of annotations to return.
    """
    annotations = apple_books.search_annotation_by_note(note, limit=limit)
    annotations_str = "\n".join([
        f"{annotation.selected_text}\n"
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def full_text_search(text: str, limit: int = None) -> TextContent:
    """
    Search for annotations by any text that contains the given text.

    Args:
        text: The text to search for.
        limit: Maximum number of annotations to return.
    """
    annotations = apple_books.search_annotation_by_text(text, limit=limit)
    annotations_str = "\n".join([
        _format_annotation_with_book(annotation, include_chapter=True)
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


@mcp.tool()
def recent_annotations(limit: int = 10) -> TextContent:
    """
    Get most recent annotations.

    Args:
        limit: Maximum number of annotations to return. Defaults to 10.
    """
    annotations = apple_books.list_annotations(limit=limit, order_by="-creation_date")
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


@mcp.tool()
def get_annotations_by_date_range(after: str = None, before: str = None, limit: int = None) -> TextContent:
    """
    Get annotations created within a date range.

    Args:
        after: Only include annotations created after this date (YYYY-MM-DD).
        before: Only include annotations created before this date (YYYY-MM-DD).
        limit: Maximum number of annotations to return.
    """
    after_dt = datetime.strptime(after, "%Y-%m-%d") if after else None
    before_dt = datetime.strptime(before, "%Y-%m-%d") if before else None

    annotations = apple_books.get_annotations_by_date_range(
        after=after_dt, before=before_dt, limit=limit
    )
    annotations_str = "\n".join([
        _format_annotation_with_book(annotation, include_chapter=True)
        for annotation in annotations
    ])
    return TextContent(
        type="text",
        text=f"Annotations:\n{annotations_str}"
    )


# -- Library Stats Tools --
@mcp.tool()
def get_library_stats() -> TextContent:
    """Get a summary of your Apple Books library with reading stats."""
    books = list(apple_books.list_books())
    annotations = list(apple_books.list_annotations())

    total_books = len(books)
    finished = sum(1 for b in books if getattr(b, "is_finished", False))
    in_progress = sum(
        1 for b in books
        if (getattr(b, "reading_progress", 0) or 0) > 0
        and not getattr(b, "is_finished", False)
    )
    unstarted = total_books - finished - in_progress

    total_annotations = len(annotations)

    # Count annotations per book
    anno_counts = {}
    for anno in annotations:
        title = _get_book_title(anno)
        anno_counts[title] = anno_counts.get(title, 0) + 1

    top_annotated = sorted(anno_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_str = "\n".join([f"  {title}: {count}" for title, count in top_annotated])

    stats = (
        f"Total books: {total_books}\n"
        f"Finished: {finished}\n"
        f"In progress: {in_progress}\n"
        f"Unstarted: {unstarted}\n"
        f"Total annotations: {total_annotations}\n"
        f"Most annotated books:\n{top_str}"
    )

    return TextContent(
        type="text",
        text=stats
    )


def serve():
    """Serve the Apple Books MCP server."""
    logger.info("--- Started Apple Books MCP server ---")
    mcp.run(transport="stdio")
