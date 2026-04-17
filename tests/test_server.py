import pytest
from datetime import datetime
from unittest.mock import patch
from apple_books_mcp.server import (
    list_all_collections, get_collection_books, describe_collection,
    list_all_books, get_book_annotations, describe_book,
    search_books_by_title, search_collections_by_title, get_books_by_genre,
    get_books_in_progress, get_finished_books, get_unstarted_books,
    get_recently_read_books,
    list_all_annotations, get_highlights_by_color, search_highlighted_text,
    search_notes, full_text_search, recent_annotations,
    describe_annotation,
    get_annotations_by_date_range,
    get_library_stats
)


class MockBook:
    def __init__(self):
        self.id = "book1"
        self.title = "Book 1"
        self.author = "Author 1"
        self.annotations = []
        self.reading_progress = 45.0
        self.is_finished = False
        self.last_opened_date = None
        self.duration = 3600
        self.genre = "Romance"

    def __str__(self):
        return "Book 1"

    def format_progress_summary(self):
        return "Progress: In Progress (45.0%) | Time Spent: 1.0h"

    @property
    def __dict__(self):
        return {"id": "book1", "title": "Book 1"}


class MockAnnotation:
    def __init__(self):
        self.id = "anno1"
        self.selected_text = "Test text"

        self.book = MockBook()
        self.chapter = "Chapter 1"
        self.location = "Page 1"
        self.creation_date = datetime(2026, 4, 16, 14, 23, 45)
        self.modification_date = datetime(2026, 4, 16, 14, 24, 0)

    def __str__(self):
        return "Highlight: Test text"

    @property
    def __dict__(self):
        return {"id": "anno1", "text": "Test text"}


class MockCollection:
    def __init__(self):
        self._books = [MockBook()]

    def __str__(self):
        return "Collection 1"

    @property
    def books(self):
        return self._books


@pytest.fixture
def mock_apple_books():
    with patch('apple_books_mcp.server.apple_books') as mock:
        book = MockBook()
        anno = MockAnnotation()
        book.annotations = [anno]

        mock.list_collections.return_value = [MockCollection()]
        mock.get_collection_by_id.return_value = MockCollection()
        mock.list_books.return_value = [book]
        mock.get_book_by_id.return_value = book
        mock.list_annotations.return_value = [anno]
        mock.get_annotations_by_color.return_value = [anno]
        mock.search_annotation_by_highlighted_text.return_value = [anno]
        mock.search_annotation_by_note.return_value = [anno]
        mock.search_annotation_by_text.return_value = [anno]
        mock.get_annotation_by_id.return_value = anno
        mock.get_book_by_title.return_value = [book]
        mock.get_collection_by_title.return_value = [MockCollection()]
        mock.get_books_in_progress.return_value = [book]
        mock.get_finished_books.return_value = [book]
        mock.get_unstarted_books.return_value = [book]
        mock.get_recently_read_books.return_value = [book]
        mock.get_annotations_by_date_range.return_value = [anno]
        mock.get_books_by_genre.return_value = [book]

        yield mock


def test_list_all_collections(mock_apple_books):
    result = list_all_collections()
    assert "Collection 1" in result.text
    mock_apple_books.list_collections.assert_called_once_with(limit=None)


def test_get_collection_books(mock_apple_books):
    result = get_collection_books("col1")
    assert "Book 1" in result.text
    mock_apple_books.get_collection_by_id.assert_called_once_with("col1")


def test_describe_collection(mock_apple_books):
    result = describe_collection("col1")
    assert isinstance(result.text, str)
    mock_apple_books.get_collection_by_id.assert_called_once_with("col1")


def test_list_all_books(mock_apple_books):
    result = list_all_books()
    assert "Book 1" in result.text
    mock_apple_books.list_books.assert_called_once_with(limit=None)


def test_get_book_annotations(mock_apple_books):
    result = get_book_annotations("book1")
    assert "Test text" in result.text
    mock_apple_books.get_book_by_id.assert_called_once_with("book1")


def test_describe_book(mock_apple_books):
    result = describe_book("book1")
    assert "book1" in result.text
    mock_apple_books.get_book_by_id.assert_called_once_with("book1")


def test_list_all_annotations(mock_apple_books):
    result = list_all_annotations()
    assert "Test text" in result.text
    mock_apple_books.list_annotations.assert_called_once_with(limit=None)


def test_get_highlights_by_color(mock_apple_books):
    result = get_highlights_by_color("yellow")
    assert "Test text" in result.text
    mock_apple_books.get_annotations_by_color.assert_called_once_with("yellow", limit=None)


def test_search_highlighted_text(mock_apple_books):
    result = search_highlighted_text("test")
    assert "Test text" in result.text
    mock_apple_books.search_annotation_by_highlighted_text.assert_called_once_with("test", limit=None)


def test_search_notes(mock_apple_books):
    result = search_notes("note")
    assert "Test text" in result.text
    mock_apple_books.search_annotation_by_note.assert_called_once_with("note", limit=None)


def test_full_text_search(mock_apple_books):
    result = full_text_search("test")
    assert "Test text" in result.text
    mock_apple_books.search_annotation_by_text.assert_called_once_with("test", limit=None)


def test_recent_annotations(mock_apple_books):
    result = recent_annotations()
    assert "Test text" in result.text
    mock_apple_books.list_annotations.assert_called_once_with(limit=10, order_by="-creation_date")


def test_recent_annotations_handles_missing_book(mock_apple_books):
    orphaned_annotation = MockAnnotation()
    orphaned_annotation.book = None
    mock_apple_books.list_annotations.return_value = [orphaned_annotation]

    result = recent_annotations()

    assert "Unknown Book" in result.text
    mock_apple_books.list_annotations.assert_called_once_with(limit=10, order_by="-creation_date")


def test_search_books_by_title(mock_apple_books):
    result = search_books_by_title("Book")
    assert "Book 1" in result.text
    mock_apple_books.get_book_by_title.assert_called_once_with("Book")


def test_get_books_by_genre(mock_apple_books):
    result = get_books_by_genre("Romance")
    assert "Book 1" in result.text
    assert "Romance" in result.text
    mock_apple_books.get_books_by_genre.assert_called_once_with("Romance", limit=None)


def test_search_collections_by_title(mock_apple_books):
    result = search_collections_by_title("Collection")
    assert "Collection 1" in result.text
    mock_apple_books.get_collection_by_title.assert_called_once_with("Collection")


def test_get_books_in_progress(mock_apple_books):
    result = get_books_in_progress()
    assert "Book 1" in result.text
    assert "In Progress" in result.text
    mock_apple_books.get_books_in_progress.assert_called_once_with(limit=None)


def test_get_finished_books(mock_apple_books):
    result = get_finished_books()
    assert "Book 1" in result.text
    assert "Author 1" in result.text
    mock_apple_books.get_finished_books.assert_called_once_with(limit=None)


def test_get_unstarted_books(mock_apple_books):
    result = get_unstarted_books()
    assert "Book 1" in result.text
    mock_apple_books.get_unstarted_books.assert_called_once_with(limit=None)


def test_get_recently_read_books(mock_apple_books):
    result = get_recently_read_books()
    assert "Book 1" in result.text
    mock_apple_books.get_recently_read_books.assert_called_once_with(limit=10)


def test_limit_parameter(mock_apple_books):
    list_all_books(limit=5)
    mock_apple_books.list_books.assert_called_once_with(limit=5)

    recent_annotations(limit=3)
    mock_apple_books.list_annotations.assert_called_with(limit=3, order_by="-creation_date")

    get_books_in_progress(limit=2)
    mock_apple_books.get_books_in_progress.assert_called_with(limit=2)


def test_annotation_output_includes_timestamp(mock_apple_books):
    """Timestamps are required for Claude to cluster annotations into sessions."""
    result = recent_annotations()
    assert "2026-04-16T14:23:45" in result.text


def test_get_annotations_by_date_range(mock_apple_books):
    from datetime import datetime
    result = get_annotations_by_date_range(after="2025-01-01", before="2025-12-31")
    assert "Test text" in result.text
    mock_apple_books.get_annotations_by_date_range.assert_called_once_with(
        after=datetime(2025, 1, 1), before=datetime(2025, 12, 31), limit=None
    )


def test_get_annotations_by_date_range_after_only(mock_apple_books):
    from datetime import datetime
    result = get_annotations_by_date_range(after="2025-06-01")
    assert "Test text" in result.text
    mock_apple_books.get_annotations_by_date_range.assert_called_once_with(
        after=datetime(2025, 6, 1), before=None, limit=None
    )


def test_describe_annotation(mock_apple_books):
    result = describe_annotation("anno1")
    assert "anno1" in result.text
    mock_apple_books.get_annotation_by_id.assert_called_once_with("anno1")


def test_get_library_stats(mock_apple_books):
    result = get_library_stats()
    assert "Total books: 1" in result.text
    assert "Total annotations: 1" in result.text
    assert "Most annotated books:" in result.text
    assert "Book 1" in result.text
    mock_apple_books.list_books.assert_called_once()
    mock_apple_books.list_annotations.assert_called_once()
