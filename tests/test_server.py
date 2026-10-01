import pytest
from datetime import datetime
from unittest.mock import patch
from mcp.server.fastmcp.exceptions import ToolError
from py_apple_books import LibraryStats
from py_apple_books.exceptions import (
    AnnotationNotFoundError,
    BookNotFoundError,
)
from py_apple_books.models.location import Location
from apple_books_mcp.server import (
    list_all_collections, get_collection_books, describe_collection,
    list_all_books, describe_book,
    search_books_by_title, search_collections_by_title, get_books_by_genre,
    get_books_in_progress, get_finished_books, get_unstarted_books,
    get_recently_read_books,
    list_all_annotations, list_annotations,
    get_highlights_by_color,
    search_notes, search_annotations, recent_annotations,
    describe_annotation, get_annotation_context,
    get_annotations_by_date_range,
    get_library_stats,
    get_current_reading_position,
    create_collection, rename_collection, delete_collection,
    add_book_to_collection, remove_book_from_collection,
)


class MockResults(list):
    """A list with the ``count()`` of py-apple-books 1.10's
    ``ModelIterable`` (a ``COUNT(*)`` there). Slicing returns a list,
    as a ``ModelIterable`` slice does."""

    def count(self):
        return len(self)


# Ids are integers, as in the library (Z_PK).
BOOK_ID = 1
ANNO_ID = 11
COLLECTION_ID = 21


class MockBook:
    def __init__(self):
        self.id = BOOK_ID
        self.title = "Book 1"
        self.author = "Author 1"
        self.annotations = MockResults()
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
        return {"id": BOOK_ID, "title": "Book 1"}


class MockLocation:
    """Mimic py_apple_books.models.location.Location's surface for tests."""
    def __init__(self, cfi: str = "", chapter_id: str = None, char_range=None):
        self.cfi = cfi
        self.chapter_id = chapter_id
        self.char_range = char_range
        # Document-order fields (py-apple-books 1.10), parsed as the
        # library does.
        parsed = Location(cfi)
        self.sort_key = parsed.sort_key
        self.spine_index = parsed.spine_index

    def __bool__(self):
        return bool(self.cfi)

    def __str__(self):
        return self.cfi


class MockAnnotation:
    def __init__(self):
        self.id = ANNO_ID
        self.selected_text = "Test text"
        self.representative_text = "Test text"

        self.book = MockBook()
        self.chapter = "Chapter 1"
        # Location is now a value object in py-apple-books v1.8.0+; mock
        # that shape so MCP tools that use ``annotation.location.chapter_id``
        # behave correctly.
        self.location = MockLocation(
            cfi="epubcfi(/6/8[chap1]!/4/2/1:0)", chapter_id="chap1"
        )
        self.note = None
        self.creation_date = datetime(2026, 4, 16, 14, 23, 45)
        self.modification_date = datetime(2026, 4, 16, 14, 24, 0)

    def __str__(self):
        return "Highlight: Test text"

    @property
    def __dict__(self):
        return {"id": ANNO_ID, "text": "Test text"}


class MockCollection:
    def __init__(self):
        self.id = COLLECTION_ID
        self.title = "Collection 1"
        self.details = ""
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
        book.annotations = MockResults([anno])

        # The list and search methods return ModelIterables (count() and
        # slicing); search_annotation_by_text returns a plain list.
        mock.list_collections.return_value = MockResults([MockCollection()])
        mock.get_collection_by_id.return_value = MockCollection()
        mock.list_books.return_value = MockResults([book])
        mock.get_book_by_id.return_value = book
        mock.list_annotations.return_value = MockResults([anno])
        mock.get_annotations_by_color.return_value = MockResults([anno])
        mock.search_annotation_by_highlighted_text.return_value = MockResults([anno])
        mock.search_annotation_by_note.return_value = MockResults([anno])
        mock.search_annotation_by_text.return_value = [anno]
        mock.get_annotation_by_id.return_value = anno
        mock.get_annotation_surrounding_text.return_value = (
            "Some preceding text. Test text. Some following text."
        )
        mock.get_book_by_title.return_value = MockResults([book])
        mock.get_collection_by_title.return_value = MockResults([MockCollection()])
        mock.get_books_in_progress.return_value = MockResults([book])
        mock.get_finished_books.return_value = MockResults([book])
        mock.get_unstarted_books.return_value = MockResults([book])
        mock.get_recently_read_books.return_value = MockResults([book])
        mock.get_annotations_by_date_range.return_value = MockResults([anno])
        mock.get_books_by_genre.return_value = MockResults([book])
        mock.get_library_stats.return_value = LibraryStats(
            total_books=1, finished_books=0, in_progress_books=1,
            unstarted_books=0, total_annotations=1, orphan_annotations=0,
            annotations_per_book=((BOOK_ID, "Book 1", 1),),
        )

        # Collection write methods (v0.8.0)
        mock.create_collection.return_value = MockCollection()
        mock.rename_collection.return_value = MockCollection()
        mock.delete_collection.return_value = None
        mock.add_book_to_collection.return_value = True
        mock.remove_book_from_collection.return_value = True

        yield mock


@pytest.fixture
def writes_enabled(monkeypatch):
    monkeypatch.setenv("APPLE_BOOKS_MCP_ENABLE_WRITES", "1")


def test_list_all_collections(mock_apple_books):
    result = list_all_collections()
    assert "Collection 1" in result.text
    # The page is sliced from the query (count() + LIMIT/OFFSET).
    mock_apple_books.list_collections.assert_called_once_with()


def test_get_collection_books(mock_apple_books):
    result = get_collection_books(COLLECTION_ID)
    assert "Book 1" in result.text
    mock_apple_books.get_collection_by_id.assert_called_once_with(COLLECTION_ID)


def test_describe_collection(mock_apple_books):
    # 0.8 typed this id as a string; a numeric string still works and
    # reaches the library as an int.
    result = describe_collection(str(COLLECTION_ID))
    assert isinstance(result.text, str)
    mock_apple_books.get_collection_by_id.assert_called_once_with(COLLECTION_ID)


def test_list_all_books(mock_apple_books):
    result = list_all_books()
    assert "Book 1" in result.text
    mock_apple_books.list_books.assert_called_once_with()


def test_describe_book(mock_apple_books):
    result = describe_book(str(BOOK_ID))
    assert f"Book id: {BOOK_ID}" in result.text
    mock_apple_books.get_book_by_id.assert_called_once_with(BOOK_ID)


@pytest.mark.parametrize("bad", ["book1", "", "1.5", None])
def test_non_numeric_id_is_a_clear_error(mock_apple_books, bad):
    with pytest.raises(ToolError, match=r"book_id must be a numeric id"):
        describe_book(bad)
    mock_apple_books.get_book_by_id.assert_not_called()


def test_list_all_annotations(mock_apple_books):
    result = list_all_annotations()
    # New in v0.7.0: lean grouped-by-book output — id + chapter,
    # no selected_text body. Surrounding text is a follow-up call.
    assert f"[{ANNO_ID}]" in result.text
    assert "Book 1" in result.text
    # Ordering defaults to recent-first so heavily-deleted old books
    # (orphan asset_ids) don't dominate the top of the listing.
    mock_apple_books.list_annotations.assert_called_once_with(
        order_by="-creation_date"
    )


def test_list_annotations_by_book(mock_apple_books):
    result = list_annotations(BOOK_ID)
    # Book-scoped listing: no book name per row (caller passed book_id),
    # just the annotation id and chapter.
    assert f"[{ANNO_ID}]" in result.text
    # The book name should NOT repeat in each row — that's the whole
    # point of taking book_id as an argument.
    assert "Book 1" not in result.text
    mock_apple_books.get_book_by_id.assert_called_with(BOOK_ID)


def test_list_annotations_empty_for_book_with_none(mock_apple_books):
    # Book exists but has no annotations — should return a friendly
    # message instead of empty output.
    book = mock_apple_books.get_book_by_id.return_value
    book.annotations = MockResults()
    result = list_annotations(BOOK_ID)
    # An empty result is a normal answer, not an error.
    assert "No annotations" in result.text


def test_list_annotations_unknown_book(mock_apple_books):
    mock_apple_books.get_book_by_id.side_effect = BookNotFoundError(
        "No book with id 99999."
    )
    with pytest.raises(ToolError) as raised:
        list_annotations("99999")
    assert str(raised.value) == (
        "No book with id 99999. Use search_books_by_title or list_all_books "
        "to find book ids."
    )
    mock_apple_books.get_book_by_id.assert_called_once_with(99999)


def test_get_highlights_by_color(mock_apple_books):
    result = get_highlights_by_color("yellow")
    assert "Test text" in result.text
    mock_apple_books.get_annotations_by_color.assert_called_once_with(
        "yellow", order_by="-creation_date"
    )


def test_search_notes(mock_apple_books):
    result = search_notes("note")
    assert "Test text" in result.text
    mock_apple_books.search_annotation_by_note.assert_called_once_with(
        "note", order_by="-creation_date"
    )


def test_search_annotations(mock_apple_books):
    result = search_annotations("test")
    assert "Test text" in result.text
    # One row past the page says whether there is a next page.
    mock_apple_books.search_annotation_by_text.assert_called_once_with(
        "test", limit=51, offset=0, order_by="-creation_date"
    )


def test_recent_annotations(mock_apple_books):
    result = recent_annotations()
    assert "Test text" in result.text
    mock_apple_books.list_annotations.assert_called_once_with(order_by="-creation_date")


def test_recent_annotations_handles_missing_book(mock_apple_books):
    orphaned_annotation = MockAnnotation()
    orphaned_annotation.book = None
    mock_apple_books.list_annotations.return_value = MockResults([orphaned_annotation])

    result = recent_annotations()

    # Orphaned annotations (asset_id no longer maps to a library book) get
    # an explicit "no longer in library" suffix instead of silently
    # disappearing into an "Unknown Book" bucket.
    assert "no longer in library" in result.text
    mock_apple_books.list_annotations.assert_called_once_with(order_by="-creation_date")


def test_search_books_by_title(mock_apple_books):
    result = search_books_by_title("Book")
    assert "Book 1" in result.text
    mock_apple_books.get_book_by_title.assert_called_once_with("Book")


def test_get_books_by_genre(mock_apple_books):
    result = get_books_by_genre("Romance")
    assert "Book 1" in result.text
    assert "Romance" in result.text
    mock_apple_books.get_books_by_genre.assert_called_once_with("Romance")


def test_search_collections_by_title(mock_apple_books):
    result = search_collections_by_title("Collection")
    assert "Collection 1" in result.text
    mock_apple_books.get_collection_by_title.assert_called_once_with("Collection")


def test_get_books_in_progress(mock_apple_books):
    result = get_books_in_progress()
    assert "Book 1" in result.text
    assert "In Progress" in result.text
    mock_apple_books.get_books_in_progress.assert_called_once_with()


def test_get_finished_books(mock_apple_books):
    result = get_finished_books()
    assert "Book 1" in result.text
    assert "Author 1" in result.text
    mock_apple_books.get_finished_books.assert_called_once_with()


def test_get_unstarted_books(mock_apple_books):
    result = get_unstarted_books()
    assert "Book 1" in result.text
    mock_apple_books.get_unstarted_books.assert_called_once_with()


def test_get_recently_read_books(mock_apple_books):
    result = get_recently_read_books()
    assert "Book 1" in result.text
    # Sorted in Python by the library; the page is sliced from all rows.
    mock_apple_books.get_recently_read_books.assert_called_once_with(limit=None)


def test_limit_parameter(mock_apple_books):
    books = []
    for i in range(8):
        book = MockBook()
        book.id = 100 + i
        books.append(book)
    mock_apple_books.list_books.return_value = MockResults(books)
    mock_apple_books.get_books_in_progress.return_value = MockResults(books)

    result = list_all_books(limit=5)
    assert "[104]" in result.text and "[105]" not in result.text
    assert "Showing 1–5 of 8 books. Next page: offset=5." in result.text

    result = get_books_in_progress(limit=2, offset=6)
    assert "[106]" in result.text and "[107]" in result.text
    assert "[105]" not in result.text
    assert "Showing 7–8 of 8 books (end)." in result.text

    recent_annotations(limit=3)
    mock_apple_books.list_annotations.assert_called_with(order_by="-creation_date")


def test_annotation_lean_row_prefers_selected_text(mock_apple_books):
    """v0.7.0 list-style tools render lean rows: ``[id] selected — chapter``.
    The fuller ``representative_text`` stays out of these — callers who want
    context follow up with ``describe_annotation`` or
    ``get_annotation_context``."""
    anno = MockAnnotation()
    anno.selected_text = "minus one"
    anno.representative_text = "A caret acts like a minus one in git revision syntax."
    mock_apple_books.list_annotations.return_value = MockResults([anno])

    result = recent_annotations()
    # Lean output shows the highlight the user actually selected.
    assert "minus one" in result.text
    # Representative text is NOT shown inline — that's the whole point of
    # the lean format.
    assert "A caret acts like" not in result.text


def test_annotation_output_includes_date(mock_apple_books):
    """The flat-with-timestamp format leads each row with YYYY-MM-DD HH:MM
    (local time) so Claude can cluster annotations into reading
    sessions."""
    result = recent_annotations()
    assert result.text.startswith(f"2026-04-16 14:23 [{ANNO_ID}]")


def test_get_annotations_by_date_range(mock_apple_books):
    result = get_annotations_by_date_range(after="2025-01-01", before="2025-12-31")
    assert "Test text" in result.text
    # A date-only ``before`` covers that whole day; newest first.
    mock_apple_books.get_annotations_by_date_range.assert_called_once_with(
        after=datetime(2025, 1, 1),
        before=datetime(2025, 12, 31, 23, 59, 59, 999999),
        order_by="-creation_date",
    )


def test_get_annotations_by_date_range_after_only(mock_apple_books):
    result = get_annotations_by_date_range(after="2025-06-01")
    assert "Test text" in result.text
    mock_apple_books.get_annotations_by_date_range.assert_called_once_with(
        after=datetime(2025, 6, 1), before=None, order_by="-creation_date"
    )


def test_describe_annotation(mock_apple_books):
    result = describe_annotation(str(ANNO_ID))
    assert f"Annotation {ANNO_ID}" in result.text
    mock_apple_books.get_annotation_by_id.assert_called_once_with(ANNO_ID)


def test_get_annotation_context_marks_highlight(mock_apple_books):
    """The tool wraps the selected_text with guillemets inside the
    returned window so Claude can see the exact anchor."""
    result = get_annotation_context(1)
    assert "«Test text»" in result.text
    assert "Some preceding text" in result.text
    assert "Some following text" in result.text
    # Book text arrives in one untrusted-content envelope.
    assert result.text.startswith(f'<book_text book_id="{BOOK_ID}" annotation_id="1">\n')
    assert result.text.endswith("\n</book_text>")
    mock_apple_books.get_annotation_surrounding_text.assert_called_once_with(
        1, chars_before=500, chars_after=500
    )


def test_get_annotation_context_empty_degrades(mock_apple_books):
    """When the backend returns '' (no CFI hint, DRM, etc.) the tool
    returns an explanatory message instead of an empty response."""
    mock_apple_books.get_annotation_surrounding_text.return_value = ""
    # Drop the location so the reason is the missing-chapter-hint path.
    anno = mock_apple_books.get_annotation_by_id.return_value
    anno.location = None
    with pytest.raises(ToolError, match="No surrounding context available") as raised:
        get_annotation_context(1)
    assert "CFI" in str(raised.value)


def test_get_annotation_context_unknown_id(mock_apple_books):
    mock_apple_books.get_annotation_by_id.side_effect = AnnotationNotFoundError(
        "No annotation with id 99999."
    )
    with pytest.raises(ToolError, match="No annotation with id 99999. Annotation ids"):
        get_annotation_context(99999)


def test_currently_reading_resource_registered():
    """The currently-reading resource is available for attachment."""
    import asyncio
    from apple_books_mcp.server import mcp

    resources = asyncio.run(mcp.list_resources())
    uris = {str(r.uri) for r in resources}
    assert "apple-books://currently-reading" in uris


def test_currently_reading_resource_content(mock_apple_books):
    """The resource is a lean pointer — metadata + ids only, no chapter
    text, no annotations list. Claude fetches richer context on demand
    via list_annotations / get_chapter_content / get_annotation_context.
    """
    import asyncio
    from apple_books_mcp.server import mcp

    result = asyncio.run(mcp.read_resource("apple-books://currently-reading"))
    content = result[0].content if hasattr(result[0], "content") else str(result[0])

    # Metadata + ids — still present.
    assert "Currently Reading: Book 1 by Author 1" in content
    assert "In Progress" in content
    assert f"Book id: {BOOK_ID}" in content
    # Annotation count is shown, but the highlight bodies are NOT.
    assert "Highlights in this book: 1" in content
    assert "Test text" not in content  # the annotation body stays out
    # Pointer to list_annotations for richer browsing.
    assert "list_annotations" in content

    mock_apple_books.get_books_in_progress.assert_called_with(limit=1, order_by="-last_opened_date")


def test_prompts_registered():
    """Verify the 3 curated prompts are exposed via MCP."""
    import asyncio
    from apple_books_mcp.server import mcp

    prompts = asyncio.run(mcp.list_prompts())
    names = {p.name for p in prompts}
    assert names == {
        "weekly_digest",
        "library_snapshot",
        "revisit_book",
    }


def test_get_library_stats(mock_apple_books):
    result = get_library_stats()
    assert "Total books: 1" in result.text
    assert "Total annotations: 1" in result.text
    assert "Most annotated books:" in result.text
    assert f"[{BOOK_ID}] Book 1: 1" in result.text
    # Counted by the library in SQL; no book or annotation is loaded.
    mock_apple_books.get_library_stats.assert_called_once_with()
    mock_apple_books.list_books.assert_not_called()
    mock_apple_books.list_annotations.assert_not_called()


# ---------------------------------------------------------------------------
# v0.7.1 regression fixes
# ---------------------------------------------------------------------------


def test_list_all_books_uses_bracketed_id_format(mock_apple_books):
    """v0.7.1: list_all_books emits ``[id] title by author`` per row so
    Claude can hand off to describe_book / list_annotations without a
    second lookup. The old raw ``ID:\\nTitle:\\n...`` block is gone."""
    result = list_all_books()
    assert f"[{BOOK_ID}] Book 1 by Author 1" in result.text
    assert "Description: None" not in result.text  # old format gone


def test_search_books_by_title_uses_bracketed_id_format(mock_apple_books):
    result = search_books_by_title("Book")
    assert f"[{BOOK_ID}] Book 1 by Author 1" in result.text


def test_get_books_by_genre_includes_book_id(mock_apple_books):
    """v0.7.1: genre output must include [id] for hand-off."""
    result = get_books_by_genre("Romance")
    assert f"[{BOOK_ID}]" in result.text
    assert "Romance" in result.text


def test_list_all_collections_uses_bracketed_id_format(mock_apple_books):
    result = list_all_collections()
    assert f"[{COLLECTION_ID}] Collection 1" in result.text
    assert "Details: None" not in result.text


def test_search_collections_by_title_uses_bracketed_id_format(mock_apple_books):
    result = search_collections_by_title("Collection")
    assert f"[{COLLECTION_ID}] Collection 1" in result.text


def test_get_collection_books_omits_description(mock_apple_books):
    """v0.7.1: get_collection_books returns lean ``[id] title by
    author`` rows. Book descriptions (which can be 2000+ chars of
    marketing blurb per book) are intentionally suppressed — a 72-book
    collection would otherwise emit tens of thousands of chars."""
    book = mock_apple_books.get_collection_by_id.return_value._books[0]
    book.description = (
        "A VERY LONG MARKETING BLURB that should not appear in the output. "
        * 50
    )
    result = get_collection_books(COLLECTION_ID)
    assert f"[{BOOK_ID}] Book 1 by Author 1" in result.text
    assert "MARKETING BLURB" not in result.text


def test_reading_status_rows_include_book_id(mock_apple_books):
    """v0.7.1: every reading-status row must carry the book_id as the
    leading [N] — otherwise Claude can't hand off to per-book tools."""
    for fn in (get_books_in_progress, get_finished_books,
               get_unstarted_books, get_recently_read_books):
        result = fn()
        assert f"[{BOOK_ID}]" in result.text, (
            f"{fn.__name__} missing [{BOOK_ID}] id in row"
        )
        assert "Book 1 by Author 1" in result.text


def test_get_current_reading_position_tier2_fallback(mock_apple_books):
    """v0.7.1: when get_current_reading_chapter returns None (CFI
    points to a sub-section not in the ToC), fall back to emitting
    the raw chapter_id from get_current_reading_location so it's still
    actionable. Previously this returned a false 'No reading position
    recorded' message when a position clearly existed."""
    from unittest.mock import MagicMock
    # Tier-1 lookup yields nothing (sub-section case)
    mock_apple_books.get_current_reading_chapter.return_value = None
    # Tier-2 lookup yields a bookmark whose CFI has a chapter_id
    bookmark = MagicMock()
    bookmark.location = MockLocation(
        cfi="epubcfi(/6/14[chapter001]!/4/10)", chapter_id="chapter001"
    )
    mock_apple_books.get_current_reading_location.return_value = bookmark

    result = get_current_reading_position(175)
    assert "Current chapter id: chapter001" in result.text
    # The hint must be the literal Claude-callable form, not prose.
    assert 'get_chapter_content(175, "chapter001")' in result.text


def test_get_current_reading_position_tier3_most_recent_highlight(mock_apple_books):
    """v0.7.2: when the reading-position bookmark exists but has no
    CFI (Apple Books sometimes writes empty tombstone bookmarks), OR
    when no bookmark exists at all, fall back to the chapter of the
    user's most recent annotation. Clearly labeled as a proxy."""
    # Tier-1 and tier-2 both empty
    mock_apple_books.get_current_reading_chapter.return_value = None
    mock_apple_books.get_current_reading_location.return_value = None
    # Book has annotations (the mock MockBook has [MockAnnotation()]
    # set up in the fixture, whose location.chapter_id = "chap1")
    result = get_current_reading_position(999)
    # Tier-3 output:
    assert "chap1" in result.text
    assert 'get_chapter_content(999, "chap1")' in result.text
    # Must be clearly labeled as inferred, not claimed as authoritative
    assert "inferred from your most recent highlight" in result.text


def test_get_current_reading_position_truly_truly_absent(mock_apple_books):
    """All three tiers empty: no bookmark, no CFI, no annotations.
    Message matches reality and tells the user how to fix it."""
    mock_apple_books.get_current_reading_chapter.return_value = None
    mock_apple_books.get_current_reading_location.return_value = None
    book = mock_apple_books.get_book_by_id.return_value
    book.annotations = MockResults()
    result = get_current_reading_position(999)
    assert "No reading position and no highlights yet" in result.text


def test_library_stats_separates_orphan_annotations(mock_apple_books):
    """v0.7.1: orphan annotations (book no longer in library) are
    counted separately instead of clustering under 'Unknown Book' in
    the 'most annotated' list."""
    mock_apple_books.get_library_stats.return_value = LibraryStats(
        total_books=1, finished_books=0, in_progress_books=1,
        unstarted_books=0, total_annotations=2, orphan_annotations=1,
        annotations_per_book=((BOOK_ID, "Book 1", 1),),
    )

    result = get_library_stats()
    # 'Unknown Book' should NOT appear in the top list anymore.
    assert "Unknown Book:" not in result.text
    # But the orphan count is surfaced separately.
    assert "from books no longer in the library" in result.text
    # The valid annotation still shows up under its real book.
    assert "Book 1" in result.text


# ---------------------------------------------------------------------------
# v0.8.0 collection write tools
# ---------------------------------------------------------------------------


def test_write_tools_disabled_by_default(mock_apple_books, monkeypatch):
    """Without --enable-writes, every write tool refuses (an error)
    with enable instructions and never calls the backend."""
    monkeypatch.delenv("APPLE_BOOKS_MCP_ENABLE_WRITES", raising=False)
    for fn, args in [
        (create_collection, ("X",)),
        (rename_collection, (9, "Y")),
        (delete_collection, (9,)),
        (add_book_to_collection, (9, 1)),
        (remove_book_from_collection, (9, 1)),
    ]:
        with pytest.raises(ToolError, match="--enable-writes"):
            fn(*args)
    mock_apple_books.create_collection.assert_not_called()
    mock_apple_books.delete_collection.assert_not_called()


def test_create_collection(mock_apple_books, writes_enabled):
    result = create_collection("Philosophy")
    assert "Created collection" in result.text
    assert "Collection 1" in result.text
    mock_apple_books.create_collection.assert_called_once_with("Philosophy", None)


def test_rename_collection(mock_apple_books, writes_enabled):
    result = rename_collection(9, "New Name")
    assert "Renamed collection" in result.text
    mock_apple_books.rename_collection.assert_called_once_with(9, "New Name")


def test_delete_collection(mock_apple_books, writes_enabled):
    result = delete_collection(9)
    assert "Deleted collection" in result.text
    assert "untouched" in result.text
    mock_apple_books.delete_collection.assert_called_once_with(9)


def test_add_book_to_collection(mock_apple_books, writes_enabled):
    result = add_book_to_collection(9, 1)
    assert "Added" in result.text
    assert "Book 1" in result.text
    mock_apple_books.add_book_to_collection.assert_called_once_with(9, 1)


def test_add_book_already_present(mock_apple_books, writes_enabled):
    mock_apple_books.add_book_to_collection.return_value = False
    result = add_book_to_collection(9, 1)
    assert "already in" in result.text
    assert "nothing changed" in result.text.lower()


def test_remove_book_from_collection(mock_apple_books, writes_enabled):
    result = remove_book_from_collection(9, 1)
    assert "Removed" in result.text
    assert "still in the library" in result.text


def test_remove_book_not_present(mock_apple_books, writes_enabled):
    mock_apple_books.remove_book_from_collection.return_value = False
    result = remove_book_from_collection(9, 1)
    assert "wasn't in" in result.text


def test_write_blocked_while_books_running(mock_apple_books, writes_enabled):
    from py_apple_books.exceptions import BooksAppRunningError
    mock_apple_books.create_collection.side_effect = BooksAppRunningError("Books is running")
    with pytest.raises(ToolError, match="quit Books"):
        create_collection("X")


def test_write_system_collection_refused(mock_apple_books, writes_enabled):
    from py_apple_books.exceptions import SystemCollectionError
    mock_apple_books.rename_collection.side_effect = SystemCollectionError(
        "'Books' is not a user-created collection"
    )
    with pytest.raises(ToolError, match="not a user-created collection"):
        rename_collection(3, "Nope")


def test_write_schema_drift_aborts_cleanly(mock_apple_books, writes_enabled):
    from py_apple_books.exceptions import SchemaValidationError
    mock_apple_books.create_collection.side_effect = SchemaValidationError(
        "Table ZBKCOLLECTION is missing expected column(s)."
    )
    with pytest.raises(ToolError) as raised:
        create_collection("X")
    assert "aborted for safety" in str(raised.value)
    assert "No changes were made" in str(raised.value)


def test_write_collection_not_found(mock_apple_books, writes_enabled):
    from py_apple_books.exceptions import CollectionNotFoundError
    mock_apple_books.delete_collection.side_effect = CollectionNotFoundError(
        "No collection with id 99."
    )
    # delete fetches the collection first; make that succeed so the
    # writer error is what surfaces
    with pytest.raises(ToolError, match="No collection with id 99"):
        delete_collection(99)
