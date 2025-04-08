import pytest
from unittest.mock import patch
from apple_books_mcp.server import (
    list_all_collections, get_collection_books, describe_collection,
    list_all_books, get_book_annotations, describe_book,
    list_all_annotations, get_highlights_by_color, search_highlighted_text,
    search_notes, full_text_search, recent_annotations,
    describe_annotation
)


class MockBook:
    def __init__(self):
        self.id = "book1"
        self.title = "Book 1"
        self.annotations = []

    def __str__(self):
        return "Book 1"

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
        self.creation_date = "2023-01-01"
        self.modification_date = "2023-01-02"

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

        yield mock


def test_list_all_collections(mock_apple_books):
    result = list_all_collections()
    assert "Collection 1" in result.text
    mock_apple_books.list_collections.assert_called_once()


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
    mock_apple_books.list_books.assert_called_once()


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
    mock_apple_books.list_annotations.assert_called_once()


def test_get_highlights_by_color(mock_apple_books):
    result = get_highlights_by_color("yellow")
    assert "Test text" in result.text
    mock_apple_books.get_annotations_by_color.assert_called_once_with("yellow")


def test_search_highlighted_text(mock_apple_books):
    result = search_highlighted_text("test")
    assert "Test text" in result.text
    mock_apple_books.search_annotation_by_highlighted_text.assert_called_once_with("test")


def test_search_notes(mock_apple_books):
    result = search_notes("note")
    assert "Test text" in result.text
    mock_apple_books.search_annotation_by_note.assert_called_once_with("note")


def test_full_text_search(mock_apple_books):
    result = full_text_search("test")
    assert "Test text" in result.text
    mock_apple_books.search_annotation_by_text.assert_called_once_with("test")


def test_recent_annotations(mock_apple_books):
    result = recent_annotations()
    assert "Test text" in result.text
    mock_apple_books.list_annotations.assert_called_once()


def test_describe_annotation(mock_apple_books):
    result = describe_annotation("anno1")
    assert "anno1" in result.text
    mock_apple_books.get_annotation_by_id.assert_called_once_with("anno1")
