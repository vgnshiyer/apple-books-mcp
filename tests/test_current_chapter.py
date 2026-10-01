"""get_chapter_content's chapter_id "current" (F13 stage 1): the
chapter of the user's reading position, against a synthetic library
read through py-apple-books.
"""
import re

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary, write_epub

from apple_books_mcp import server
from apple_books_mcp.server import get_chapter_content

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


@pytest.mark.parametrize("chapter_id", [None, "current", " CURRENT ", "Current"])
def test_current_is_the_reading_position(library, epub, chapter_id):
    book = library.add_book("Readable", path=epub, progress=0.5)
    _position(library, book)
    args = {} if chapter_id is None else {"chapter_id": chapter_id}
    text = get_chapter_content(book["id"], **args).text
    assert text.startswith(f'<book_text book_id="{book["id"]}" chapter_id="c2" offset="0">\n')
    assert "Second chapter text." in text and "First chapter" not in text
    assert text.endswith(
        '\n(chapter_id "current" read chapter c2, from your reading position.)')


def test_current_names_the_chapter_to_page_on(library, epub):
    book = library.add_book("Readable", path=epub, progress=0.5)
    _position(library, book)
    text = get_chapter_content(book["id"], max_chars=100).text
    assert "Call again with offset=100 to continue" in text
    assert text.endswith(
        '\n(chapter_id "current" read chapter c2, from your reading position. '
        'Pass chapter_id="c2" with the next offset.)')
    assert "Second chapter" in get_chapter_content(book["id"], "c2", offset=100).text


def test_a_chapter_named_current_wins(library, tmp_path):
    epub = write_epub(tmp_path / "Odd.epub", "Odd", [
        ("c1", "One", ["Where the reader is."]),
        ("current", "Named Current", ["The chapter called current."]),
    ])
    book = library.add_book("Odd", path=epub)
    _position(library, book, chapter="c1", step=4)
    text = get_chapter_content(book["id"], "current").text
    assert "The chapter called current." in text
    assert 'read chapter' not in text
    # Any other spelling asks for the reading position.
    assert "Where the reader is." in get_chapter_content(book["id"], "Current").text


def test_current_without_a_reading_position(library, epub):
    book = library.add_book("Readable", path=epub)
    # A highlight is not a reading position.
    library.add_annotation(book, "First chapter", location="epubcfi(/6/4[c1]!/4/2/1:0)")
    with pytest.raises(ToolError) as raised:
        get_chapter_content(book["id"])
    assert str(raised.value) == (
        "Apple Books has no reading position for this book yet, so there is no "
        f"current chapter. list_book_chapters({book['id']}) lists its chapters; "
        "pass one's id as chapter_id.")


def test_current_in_a_file_the_toc_skips(library, tmp_path):
    """A reading position in a spine item the ToC doesn't list is still
    the current chapter (get_current_reading_position shows it too)."""
    epub = write_epub(tmp_path / "Parts.epub", "Parts", CHAPTERS)
    # Take c2 out of both tables of contents; it stays in the spine.
    nav, ncx = epub / "OEBPS" / "nav.xhtml", epub / "OEBPS" / "toc.ncx"
    nav.write_text(re.sub(r"\s*<li><a href=\"c2.xhtml\">.*?</li>", "", nav.read_text()))
    ncx.write_text(re.sub(r"\s*<navPoint id=\"np2\".*?</navPoint>", "", ncx.read_text(),
                          flags=re.S))
    book = library.add_book("Parts", path=epub)
    _position(library, book, chapter="c2")
    assert [c.id for c in server.apple_books.get_book_content(book["id"]).list_chapters()] == ["c1"]
    text = get_chapter_content(book["id"]).text
    assert "Second chapter text." in text
    assert text.endswith('(chapter_id "current" read chapter c2, from your reading position.)')


def test_current_on_an_unreadable_book(library):
    """The book's own error comes first."""
    book = library.add_book("No File")
    _position(library, book)
    with pytest.raises(ToolError, match="has not been downloaded"):
        get_chapter_content(book["id"])


def test_a_chapter_that_is_not_there_is_still_an_error(library, epub):
    book = library.add_book("Readable", path=epub)
    _position(library, book)
    with pytest.raises(ToolError, match=rf"list_book_chapters\({book['id']}\) lists"):
        get_chapter_content(book["id"], "currently")
