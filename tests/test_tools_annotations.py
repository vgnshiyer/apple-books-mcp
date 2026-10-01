"""Per-book annotation listing and library counts against a synthetic
library: reading order from the CFI (F53), notes in the listings
(F18-0) and counts done by the library in SQL (F02).
"""
import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.models import Annotation
from py_apple_books.testing import FixtureLibrary, write_epub

from apple_books_mcp import server
from apple_books_mcp.server import (
    describe_book,
    get_library_stats,
    list_all_annotations,
    list_annotations,
    revisit_book,
    search_annotations,
)

CHAPTERS = [
    ("c1", "One", ["First chapter text."]),
    ("c2", "Two", ["Second chapter text."]),
    ("c3", "Three", ["Third chapter text."]),
]


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = FixtureLibrary.create(tmp_path / "home")
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield lib
    api.close()


@pytest.fixture
def no_annotation_models(monkeypatch):
    """Fail the test if any annotation row is turned into a model."""
    def refuse(cls, row, db=None):
        raise AssertionError("an annotation was loaded")
    monkeypatch.setattr(Annotation, "from_db", classmethod(refuse))


def _seed_positions(library, book):
    """Highlights whose creation order runs against their position, one
    in a spine item the ToC doesn't list, and one without a CFI.
    Returns their ids in reading order."""
    add = library.add_annotation
    third = add(book, "in chapter three", created=1000.0,
                location="epubcfi(/6/8[c3]!/4/2/1:0)")
    untitled = add(book, "in a file the ToC skips", created=2000.0,
                   location="epubcfi(/6/6[c2-part]!/4/2/1:0)")
    late_c1 = add(book, "late in chapter one", created=3000.0,
                  location="epubcfi(/6/4[c1]!/4/6/1:10)")
    mid_c1 = add(book, "middle of chapter one", created=4000.0,
                 location="epubcfi(/6/4[c1]!/4/4,/1:0,/1:21)")
    early_c1 = add(book, "start of chapter one", created=5000.0,
                   location="epubcfi(/6/4[c1]!/4/2/1:0)")
    no_cfi = add(book, "no position recorded", created=6000.0)
    return [early_c1, mid_c1, late_c1, untitled, third, no_cfi]


def _row_ids(text):
    return [int(line.split("]")[0].lstrip("[")) for line in text.splitlines()
            if line.startswith("[")]


def test_list_annotations_is_in_reading_order(library, tmp_path):
    """F53: position order from the CFI — not ToC order plus creation
    time, which put later-created highlights first within a chapter and
    sank ones outside the ToC to the end."""
    epub = write_epub(tmp_path / "Ordered.epub", "Ordered", CHAPTERS)
    book = library.add_book("Ordered", path=epub)
    expected = _seed_positions(library, book)

    text = list_annotations(book["id"]).text
    assert _row_ids(text) == expected
    lines = text.splitlines()
    assert lines[0] == f"[{expected[0]}] start of chapter one — One (ch=c1)"
    # Untitled spine item: still in place, with its chapter id.
    assert lines[3] == f"[{expected[3]}] in a file the ToC skips (ch=c2-part)"
    assert lines[4] == f"[{expected[4]}] in chapter three — Three (ch=c3)"
    assert lines[5] == f"[{expected[5]}] no position recorded"


def test_reading_order_needs_no_book_file(library):
    """A book that can't be opened (not downloaded, DRM) is still in
    reading order; only the chapter titles are missing."""
    book = library.add_book("No File")
    expected = _seed_positions(library, book)
    text = list_annotations(book["id"]).text
    assert _row_ids(text) == expected
    assert "— One" not in text


def test_notes_are_shown(library, tmp_path):
    """F18-0: a highlight's note appears under it in list_annotations
    and list_all_annotations."""
    epub = write_epub(tmp_path / "Noted.epub", "Noted", CHAPTERS)
    book = library.add_book("Noted", path=epub)
    plain = library.add_annotation(
        book, "plain highlight", location="epubcfi(/6/4[c1]!/4/2/1:0)")
    noted = library.add_annotation(
        book, "noted highlight", kind="note", note="my own  thought\non this",
        location="epubcfi(/6/4[c1]!/4/4/1:0)")

    text = list_annotations(book["id"]).text
    assert text == (
        f"[{plain}] plain highlight — One (ch=c1)\n"
        f"[{noted}] noted highlight — One (ch=c1)\n"
        "    ↳ note: my own thought on this"
    )
    text = list_all_annotations().text
    assert f"  [{noted}] noted highlight — One (ch=c1)\n    ↳ note: my own thought on this" in text


def test_revisit_book_prompt_mentions_notes_and_paging():
    text = revisit_book("Some Book")
    # search_books also finds the book by its author.
    assert '`search_books` with "Some Book"' in text
    assert "`list_annotations`" in text and "limit=200" in text
    assert "↳ note:" in text
    assert "Next page: offset=N" in text


def _seed_counts(library):
    finished = library.add_book("Finished", finished=True, progress=1.0)
    reading = library.add_book("Reading", progress=0.3)
    library.add_book("Unstarted")
    for text in ("a", "b", "c"):
        library.add_annotation(reading, f"reading {text}")
    library.add_annotation(reading, "deleted", deleted=True)
    library.add_annotation(reading, None, kind="reading_position")
    library.add_annotation(finished, "finished a")
    library.add_annotation("ORPHANASSET0000000000000000000001", "orphan a")
    library.add_annotation("ORPHANASSET0000000000000000000002", "orphan b")
    return finished, reading


def test_library_stats_output(library, no_annotation_models):
    """F02: the same report as before, from the library's SQL counts —
    no annotation is loaded."""
    finished, reading = _seed_counts(library)
    assert get_library_stats().text == (
        "Total books: 3\n"
        "  Finished: 1\n"
        "  In progress: 1\n"
        "  Unstarted: 1\n"
        "Total annotations: 6\n"
        "  (2 highlights from 2 removed books, no longer in the library)\n"
        "Most annotated books:\n"
        f"  [{reading['id']}] Reading: 3\n"
        f"  [{finished['id']}] Finished: 1"
    )


def test_library_stats_empty(library):
    library.add_book("Lonely")
    text = get_library_stats().text
    assert "Total annotations: 0" in text
    assert text.endswith("Most annotated books:\n  (none)")
    assert "no longer in the library" not in text


def test_describe_book_counts_without_loading(library, no_annotation_models):
    _, reading = _seed_counts(library)
    text = describe_book(str(reading["id"])).text
    # Live highlights only: the deleted one and the reading position
    # aren't counted.
    assert "  Annotations: 3" in text.splitlines()


def test_search_annotations_shows_the_note(library):
    """F13: a note that matches is shown, as search_notes shows it."""
    book = library.add_book("Noted")
    noted = library.add_annotation(book, "a highlight", kind="note", note="my thought")
    text = search_annotations("thought").text
    assert text == f"Noted (Test Author):\n  [{noted}] a highlight\n    ↳ note: my thought"
