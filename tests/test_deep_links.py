"""Apple Books deep links (F18 step 1): an "Open in Books" line with
the book's ibooks://assetid/ link, only for a plain asset id, never
inside a book_text envelope and never with a CFI fragment.
"""
from types import SimpleNamespace

import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary, write_epub

from apple_books_mcp import server
from apple_books_mcp.server import (
    describe_annotation,
    describe_book,
    get_current_reading_position,
)
from apple_books_mcp.utils import _deep_link

CHAPTERS = [
    ("c1", "One", ["First chapter text."]),
    ("c2", "Two", ["Second chapter text. " * 20]),
]


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = FixtureLibrary.create(tmp_path / "home")
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield lib
    api.close()


@pytest.fixture
def epub(tmp_path):
    return write_epub(tmp_path / "Readable.epub", "Readable", CHAPTERS)


def _position(library, book, chapter="c2", step=6):
    """Apple Books' reading-position row, in ``chapter``."""
    library.add_annotation(book, None, kind="reading_position",
                           location=f"epubcfi(/6/{step}[{chapter}]!/4/2/1:0)")


def _link(book):
    return f"Open in Books: ibooks://assetid/{book['asset_id']}"


def test_deep_links(library, epub):
    book = library.add_book("Readable", path=epub, progress=0.5, last_opened=1000.0)
    _position(library, book)
    anno = library.add_annotation(book, "Second chapter", location="epubcfi(/6/6[c2]!/4/2/1:0)")
    for text in (describe_book(book["id"]).text,
                 get_current_reading_position(book["id"]).text,
                 describe_annotation(anno).text,
                 server._currently_reading()):
        assert f"\n{_link(book)}" in text.replace("  Open in Books", "Open in Books")
        # The book's link, outside any envelope, never a CFI fragment.
        assert "#" not in text.split("Open in Books: ")[1].splitlines()[0]
        for part in text.split("<book_text")[1:]:
            assert "ibooks://" not in part.split("</book_text>")[0]


def test_no_position_still_links(library):
    book = library.add_book("Unread")
    assert get_current_reading_position(book["id"]).text == (
        "No reading position and no highlights yet — open the book to a chapter "
        f"and read or highlight something, then try again.\n{_link(book)}")


@pytest.mark.parametrize("asset_id", [
    "-leading-dash", "has space", "slash/../x", "x" * 129, "quote'", "new\nline", "é",
])
def test_odd_asset_ids_get_no_link(library, asset_id):
    book = library.add_book("Odd Asset", asset_id=asset_id)
    assert "Open in Books" not in describe_book(book["id"]).text
    assert "ibooks://" not in get_current_reading_position(book["id"]).text


@pytest.mark.parametrize("asset_id", ["1234567890", "3F2A1B2C9D8E7F60A1B2C3D4E5F60718",
                                      "a.b_c-d", "x" * 128])
def test_plain_asset_ids_get_a_link(asset_id):
    book = SimpleNamespace(asset_id=asset_id, deep_link=f"ibooks://assetid/{asset_id}")
    assert _deep_link(book) == f"ibooks://assetid/{asset_id}"


def test_a_link_comes_from_the_library():
    """Only the library's own ibooks:// link is shown."""
    assert _deep_link(SimpleNamespace(asset_id="ABC", deep_link="https://x/ABC")) is None
    assert _deep_link(SimpleNamespace(asset_id="ABC")) is None
    assert _deep_link(None) is None


def test_removed_books_annotation_has_no_link(library):
    anno = library.add_annotation("3F2A1B2C9D8E7F60A1B2C3D4E5F60718", "gone")
    text = describe_annotation(anno).text
    assert "Open in Books" not in text and "ibooks://" not in text
