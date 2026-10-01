"""Default limits, clamps, offsets and paging footers (F03), and the
``order_by`` option of the search tools (F28), against a synthetic
5,000-annotation library read through py-apple-books.
"""
import re

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary, write_epub

from apple_books_mcp import server
from apple_books_mcp.server import (
    get_annotation_context,
    get_annotations_by_date_range,
    get_books_in_progress,
    get_chapter_content,
    get_highlights_by_color,
    get_recently_read_books,
    list_all_annotations,
    list_all_books,
    list_all_collections,
    list_annotations,
    recent_annotations,
    search_annotations,
    search_notes,
)
from apple_books_mcp.utils import _OUTPUT_BUDGET

BOOKS = 50
PER_BOOK = 100
TOTAL = BOOKS * PER_BOOK
# Room for the footer and clamp notes under a page cut to the budget.
FOOTER_ROOM = 300


@pytest.fixture(scope="module")
def big_library(tmp_path_factory):
    """50 books with 100 highlights each, long enough (~180 chars a
    row) that a 500-row page overflows the output budget; every 10th
    carries a note."""
    lib = FixtureLibrary.create(tmp_path_factory.mktemp("big"))
    made = lib.populate(books=BOOKS, annotations_per_book=PER_BOOK)
    lib.execute(
        "annotations",
        "UPDATE ZAEANNOTATION SET ZANNOTATIONSELECTEDTEXT = "
        "ZANNOTATIONSELECTEDTEXT || replace(printf('%.12c', 'x'), 'x', ' lorem ipsum')",
    )
    lib.execute(
        "annotations",
        "UPDATE ZAEANNOTATION SET ZANNOTATIONNOTE = 'synthetic note ' || Z_PK "
        "WHERE Z_PK % 10 = 0",
    )
    return lib, made


@pytest.fixture
def big(big_library, monkeypatch):
    lib, made = big_library
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield made
    api.close()


def _text(result):
    return result.text


def _ids(text):
    return [int(i) for i in re.findall(r"^\s*(?:\S+ \S+ )?\[(\d+)\]", text, re.M)]


def _next_offset(text):
    m = re.search(r"Next page: offset=(\d+)\.$", text)
    return int(m.group(1)) if m else None


def test_default_calls_are_bounded(big):
    """No default call returns the whole library: each stays well under
    the output budget and says how to get the next page."""
    book_id = big["books"][0]["id"]
    calls = [
        list_all_annotations(),
        list_annotations(book_id),
        get_highlights_by_color("yellow"),
        search_notes("synthetic"),
        search_annotations("synthetic"),
        recent_annotations(),
        get_annotations_by_date_range(),
    ]
    for result in calls:
        text = _text(result)
        assert len(text) < _OUTPUT_BUDGET
        assert _next_offset(text) is not None, text[-200:]
    text = _text(list_all_annotations())
    assert len(_ids(text)) == 50
    assert text.endswith(f"Showing 1–50 of {TOTAL:,} annotations. Next page: offset=50.")


@pytest.mark.parametrize("limit, used, note", [
    (0, 1, "(limit=0 is below the minimum; used limit=1.)"),
    (-1, 1, "(limit=-1 is below the minimum; used limit=1.)"),
])
def test_zero_or_negative_limit_is_not_unlimited(big, limit, used, note):
    for tool in (list_all_annotations, recent_annotations):
        text = _text(tool(limit=limit))
        assert len(_ids(text)) == used
        assert note in text
        assert f"Next page: offset={used}." in text


def test_huge_limit_is_capped_and_cut_to_the_budget(big):
    text = _text(list_all_annotations(limit=10**9))
    assert "(limit=1000000000 is above the maximum; used limit=500.)" in text
    assert len(text) <= _OUTPUT_BUDGET + FOOTER_ROOM
    shown = len(_ids(text))
    assert 0 < shown <= 500
    assert f"(output capped at {_OUTPUT_BUDGET:,} chars). Next page: offset={shown}." in text


def test_negative_offset_is_clamped(big):
    text = _text(list_all_annotations(limit=3, offset=-5))
    assert "(offset=-5 is negative; used offset=0.)" in text
    assert "Showing 1–3 of" in text


def test_offset_past_the_end(big):
    text = _text(list_all_annotations(offset=TOTAL + 10))
    assert text == (
        f"No annotations at offset {TOTAL + 10} (there are {TOTAL:,}). "
        "Pass a smaller offset."
    )
    text = _text(search_annotations("synthetic", offset=TOTAL + 10))
    assert text == f"No matches at offset {TOTAL + 10}. Pass a smaller offset."


def _follow(tool, *args, **kwargs):
    """Every id a tool returns, following its footer page by page."""
    ids, offset, pages = [], 0, 0
    while offset is not None:
        text = _text(tool(*args, offset=offset, **kwargs))
        ids += _ids(text)
        offset = _next_offset(text)
        pages += 1
        assert pages < 100
    return ids, text, pages


def test_pages_tile_the_whole_result(big):
    """Following the footer visits every annotation exactly once, even
    when pages are cut to the output budget."""
    ids, last, pages = _follow(list_all_annotations, limit=500)
    assert sorted(ids) == list(range(1, TOTAL + 1))
    assert pages > TOTAL // 500  # the budget cut pages short
    assert last.endswith(f"of {TOTAL:,} annotations (end).")

    ids, last, _ = _follow(search_annotations, "synthetic", limit=500)
    assert sorted(ids) == list(range(1, TOTAL + 1))
    assert last.endswith("matches (end).")


def test_list_annotations_pages_after_sorting(big):
    book_id = big["books"][0]["id"]
    ids, last, pages = _follow(list_annotations, book_id, limit=30)
    assert pages == 4
    assert len(ids) == PER_BOOK and len(set(ids)) == PER_BOOK
    # populate() records no CFI, so reading order falls back to creation
    # order, which is id order here.
    assert ids == sorted(ids)
    assert last.endswith(f"Showing 91–100 of {PER_BOOK} annotations (end).")


def test_short_results_have_no_footer(big):
    """A first page that holds everything reads as before: no footer."""
    text = _text(list_all_books())
    assert len(_ids(text)) == BOOKS
    assert "Showing" not in text
    text = _text(list_all_collections())
    assert text == "No collections in library."


def test_book_lists_page(big):
    text = _text(list_all_books(limit=20, offset=40))
    assert len(_ids(text)) == 10
    assert text.endswith(f"Showing 41–50 of {BOOKS} books (end).")
    text = _text(get_books_in_progress(limit=5))
    assert text.endswith("Next page: offset=5.")
    text = _text(get_recently_read_books())
    assert "Showing 1–10 of" in text


@pytest.mark.parametrize("tool, args", [
    (get_highlights_by_color, ("yellow",)),
    (search_notes, ("synthetic",)),
    (search_annotations, ("synthetic",)),
    (get_annotations_by_date_range, ()),
])
def test_order_by_newest_or_oldest(big, tool, args):
    """F28: newest first by default (populate() creates annotations in
    id order); ``order_by="oldest"`` reverses it."""
    newest = _ids(_text(tool(*args, limit=5)))
    oldest = _ids(_text(tool(*args, limit=5, order_by="oldest")))
    assert newest == sorted(newest, reverse=True)
    assert oldest == sorted(oldest)
    assert min(newest) > max(oldest)
    with pytest.raises(ToolError) as raised:
        tool(*args, order_by="sideways")
    assert str(raised.value) == "order_by must be 'newest' or 'oldest', not 'sideways'."


def test_color_page_counts(big):
    text = _text(get_highlights_by_color("yellow", limit=5))
    assert f"Showing 1–5 of {TOTAL // 5:,} yellow highlights. Next page: offset=5." in text


@pytest.fixture
def long_chapter(tmp_path, monkeypatch):
    """One readable book with a 60,000-char chapter and a highlight
    in its middle."""
    lib = FixtureLibrary.create(tmp_path / "home")
    words = " ".join(f"word{i}" for i in range(6000))
    epub = write_epub(tmp_path / "Long.epub", "Long", [
        ("c1", "Only Chapter", [words, "the marked sentence", words]),
    ])
    book = lib.add_book("Long", path=epub)
    anno = lib.add_annotation(
        book, "the marked sentence", location="epubcfi(/6/4[c1]!/4/4,/1:0,/1:19)")
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield book["id"], anno
    api.close()


def test_chapter_content_is_capped(long_chapter):
    book_id, _ = long_chapter
    text = _text(get_chapter_content(book_id, "c1", max_chars=10**9))
    assert "(max_chars=1000000000 is above the maximum; used 50000.)" in text
    assert "returned chars 0–50000 of" in text
    assert "Call again with offset=50000" in text
    assert len(text) < 50_000 + FOOTER_ROOM


def test_annotation_context_is_clamped(long_chapter):
    _, anno = long_chapter
    text = _text(get_annotation_context(anno, chars_before=-5, chars_after=10**6))
    # The passage is inside the <book_text> envelope; no context before
    # the highlight (the library marks the cut with "…").
    passage = text.split("\n", 1)[1]
    assert passage.lstrip("…").startswith("«the marked sentence»")
    assert "(chars_before=-5 is out of range; used 0.)" in text
    assert "(chars_after=1000000 is out of range; used 5000.)" in text
    assert len(text) < 5_000 + FOOTER_ROOM
