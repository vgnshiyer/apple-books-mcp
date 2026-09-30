"""Library counts against a synthetic library: counts done by the
library in SQL (F02).
"""
import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.models import Annotation
from py_apple_books.testing import FixtureLibrary

from apple_books_mcp import server
from apple_books_mcp.server import describe_book, get_library_stats

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
        "  (2 from books no longer in the library)\n"
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
