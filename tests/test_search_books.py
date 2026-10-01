"""search_books: title or author, matched like the library's title
search (F13 stage 1), and search_books_by_title's paging, against a
synthetic library read through py-apple-books.
"""
import unicodedata

import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary
from py_apple_books.testing.fixture import STORE_SERIES

from apple_books_mcp import server
from apple_books_mcp.server import search_books, search_books_by_title
from apple_books_mcp.utils import _fold


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = FixtureLibrary.create(tmp_path / "home")
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield lib
    api.close()


def _ids(text):
    return [int(line.split("]")[0].lstrip("[")) for line in text.splitlines()
            if line.startswith("[")]


SAMPLES = [
    "Plain ASCII  Title\twith\nwhitespace", "Gödel Numbers for Cats", "Don’t Forget the Towel",
    "“Quoted” — dashed – text", "Straße", "ﬁnal ﬂight", "naïve café", " nbsp em",
    "soft­hyphen zero​width", "が and ガ", "℃ 𝐀", "İstanbul", "\ud800 lone surrogate",
    "ＦＵＬＬＷＩＤＴＨ", "", "   ",
]


def test_fold_is_the_librarys():
    """The copy in utils folds exactly as py-apple-books does, so author
    matches agree with its title search. (py_apple_books.text is not
    public API; the copy exists so the server doesn't import it.)"""
    from py_apple_books.text import fold_for_match

    for sample in SAMPLES:
        assert _fold(sample) == fold_for_match(sample), sample
    assert _fold(None) is None
    # Every character of the basic multilingual plane, one at a time.
    for code in range(0x10000):
        char = chr(code)
        if unicodedata.category(char) == "Cs":
            continue
        assert _fold(f"a{char}b") == fold_for_match(f"a{char}b"), hex(code)


def test_title_or_author(library):
    gödel = library.add_book("Gödel Numbers for Cats", "Ada Brightwater")
    loop = library.add_book("Strange Loops at Breakfast", "Ada Brightwater")
    guide = library.add_book("Don’t Forget the Towel", "Rowan Tamsin")
    both = library.add_book("Tamsin on Tamsin", "Rowan Tamsin")
    library.add_book("Unrelated", "Someone Else")
    library.add_book("No Author Here", None)

    assert _ids(search_books("brightwater").text) == [gödel["id"], loop["id"]]
    assert _ids(search_books("GODEL").text) == [gödel["id"]]
    assert _ids(search_books("don't").text) == [guide["id"]]
    # Title, author or both: one row per book, by id.
    assert _ids(search_books("tamsin").text) == [guide["id"], both["id"]]
    assert search_books("brightwater").text == (
        f"[{gödel['id']}] Gödel Numbers for Cats by Ada Brightwater\n"
        f"[{loop['id']}] Strange Loops at Breakfast by Ada Brightwater"
    )


def test_rows_are_in_id_order(library, monkeypatch):
    """By book id, whatever order the library returns the books in."""
    books = [library.add_book(f"Ordered {n}", "Orderly") for n in range(4)]
    real = server.apple_books.list_books
    monkeypatch.setattr(server.apple_books, "list_books",
                        lambda *a, **k: list(reversed(list(real(*a, **k)))))
    assert _ids(search_books("orderly").text) == [b["id"] for b in books]


def test_matches_the_title_search(library):
    """On titles, search_books finds what search_books_by_title finds."""
    for title in ("Gödel Numbers for Cats", "Don’t Forget the Towel", "STRASSE", "Straße", "100% _odd_"):
        library.add_book(title, "Qq")
    for query in ("godel", "DON'T", "strasse", "%", "_", "e", "Ö", "́", "", " "):
        by_title = _ids(search_books_by_title(query).text)
        assert _ids(search_books(query).text) == by_title, query


def test_unknown_author_placeholder_is_no_author(library):
    """Apple's "Unknown Author" placeholder isn't text to match."""
    library.add_book("Anonymous", "UnknownAuthor")
    assert "No books matched 'unknownauthor' in title or author." == (
        search_books("unknownauthor").text)


def test_store_series_items_you_dont_own_stay_out(library):
    owned = library.add_book("Saga Volume 1", "Series Author")
    library.add_book("Saga", "Series Author", content_type=5, data_source=STORE_SERIES,
                     state=5)
    library.add_book("Saga Volume 2", "Series Author", data_source=STORE_SERIES)
    assert _ids(search_books("saga").text) == [owned["id"]]
    assert _ids(search_books_by_title("saga").text) == [owned["id"]]


def test_paging(library):
    books = [library.add_book(f"Paged {n}", "Pager") for n in range(5)]
    text = search_books("pager", limit=2, offset=2).text
    assert _ids(text) == [books[2]["id"], books[3]["id"]]
    assert text.endswith("Showing 3–4 of 5 books. Next page: offset=4.")
    assert search_books("pager", offset=9).text == (
        "No books at offset 9 (there are 5). Pass a smaller offset.")
    assert search_books("pager", limit=0).text.endswith(
        "(limit=0 is below the minimum; used limit=1.)\n"
        "Showing 1–1 of 5 books. Next page: offset=1.")


def test_search_books_by_title_pages(library):
    books = [library.add_book(f"Paged {n}", "Pager") for n in range(5)]
    text = search_books_by_title("paged", limit=2, offset=2).text
    assert _ids(text) == [books[2]["id"], books[3]["id"]]
    assert text.endswith("Showing 3–4 of 5 books. Next page: offset=4.")
    # A call without paging arguments reads as before.
    assert search_books_by_title(title="Paged 1").text == f"[{books[1]['id']}] Paged 1 by Pager"


def test_nothing_found_is_not_an_error(library):
    library.add_book("Present", "Someone")
    assert search_books("absent").text == "No books matched 'absent' in title or author."
    # Accents alone fold away: nothing to find, rather than everything.
    assert search_books("́").text == "No books matched '́' in title or author."
