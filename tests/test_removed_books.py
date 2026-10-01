"""Annotations of books no longer in the library (F54 step 1): one
group per removed book, named by its asset id the same way in every
tool, after the library's books.
"""
import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary

from apple_books_mcp import server
from apple_books_mcp.server import (
    describe_annotation,
    get_annotations_by_date_range,
    get_highlights_by_color,
    list_all_annotations,
    recent_annotations,
    search_annotations,
    search_notes,
)
from apple_books_mcp.utils import _removed_book

@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = FixtureLibrary.create(tmp_path / "home")
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield lib
    api.close()


GONE_A = "3F2A1B2C9D8E7F60A1B2C3D4E5F60718"
GONE_B = "77AA88BB99CC00DD11EE22FF33AA44BB"


@pytest.fixture
def removed(library):
    """A library book, and highlights of two removed books, newest
    first: B, the book, A, B, A."""
    book = library.add_book("Kept", "Kept Author")
    ids = {
        "b_new": library.add_annotation(GONE_B, "b newest", created=5000.0),
        "kept": library.add_annotation(book, "kept highlight", created=4000.0),
        "a_new": library.add_annotation(GONE_A, "a newer", created=3000.0, note="a note"),
        "b_old": library.add_annotation(GONE_B, "b older", created=2000.0),
        "a_old": library.add_annotation(GONE_A, "a oldest", created=1000.0),
    }
    return book, ids


def test_grouped_per_removed_book(removed):
    _, ids = removed
    text = list_all_annotations().text
    assert text == (
        "Kept (Kept Author):\n"
        f"  [{ids['kept']}] kept highlight\n"
        "\n"
        "Removed book (asset 77AA88BB…, 2 highlights on this page):\n"
        f"  [{ids['b_new']}] b newest\n"
        f"  [{ids['b_old']}] b older\n"
        "\n"
        "Removed book (asset 3F2A1B2C…, 2 highlights on this page):\n"
        f"  [{ids['a_new']}] a newer\n"
        "    ↳ note: a note\n"
        f"  [{ids['a_old']}] a oldest"
    )
    # Counted per page.
    text = list_all_annotations(limit=2).text
    assert "Removed book (asset 77AA88BB…, 1 highlight on this page):" in text


def test_every_grouped_listing(removed):
    for text in (search_annotations("o").text, search_notes("note").text,
                 get_highlights_by_color("yellow").text):
        assert "Unassigned" not in text and "no longer in library" not in text
        assert "Removed book (asset 3F2A1B2C…, " in text


def test_flat_rows_name_the_removed_book(removed):
    _, ids = removed
    for text in (recent_annotations().text, get_annotations_by_date_range().text):
        lines = text.splitlines()
        assert any(line.endswith(f"[{ids['b_new']}] b newest · removed book 77AA88BB…")
                   for line in lines), text
        assert any(line.endswith("kept highlight · Kept") for line in lines)


def test_describe_annotation_names_the_removed_book(removed):
    _, ids = removed
    text = describe_annotation(ids["a_old"]).text
    assert "\n  Book:     Removed book (asset 3F2A1B2C…), no longer in the library\n" in text


def test_library_stats_counts_removed_books(removed):
    text = server.get_library_stats().text
    assert "\n  (4 highlights from 2 removed books, no longer in the library)\n" in text


@pytest.mark.parametrize("asset_id, label", [
    (GONE_A, "Removed book (asset 3F2A1B2C…)"),
    ("12345", "Removed book (asset 12345)"),
    (None, "Removed book (no asset id)"),
    ("", "Removed book (no asset id)"),
    ("odd id", "Removed book (no asset id)"),
])
def test_removed_book_label(asset_id, label):
    assert _removed_book(asset_id) == label


def test_removed_book_label_forms():
    assert _removed_book(GONE_A, count=1) == (
        "Removed book (asset 3F2A1B2C…, 1 highlight on this page)")
    assert _removed_book(GONE_A, short=True) == "removed book 3F2A1B2C…"
    assert _removed_book(None, short=True) == "removed book"
