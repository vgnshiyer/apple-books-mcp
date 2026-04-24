import pytest
from datetime import datetime
from unittest.mock import patch
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


class MockLocation:
    """Mimic py_apple_books.models.location.Location's surface for tests."""
    def __init__(self, cfi: str = "", chapter_id: str = None, char_range=None):
        self.cfi = cfi
        self.chapter_id = chapter_id
        self.char_range = char_range

    def __bool__(self):
        return bool(self.cfi)

    def __str__(self):
        return self.cfi


class MockAnnotation:
    def __init__(self):
        self.id = "anno1"
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
        self.creation_date = datetime(2026, 4, 16, 14, 23, 45)
        self.modification_date = datetime(2026, 4, 16, 14, 24, 0)

    def __str__(self):
        return "Highlight: Test text"

    @property
    def __dict__(self):
        return {"id": "anno1", "text": "Test text"}


class MockCollection:
    def __init__(self):
        self.id = "col1"
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
        mock.get_annotation_surrounding_text.return_value = (
            "Some preceding text. Test text. Some following text."
        )
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


def test_describe_book(mock_apple_books):
    result = describe_book("book1")
    assert "book1" in result.text
    mock_apple_books.get_book_by_id.assert_called_once_with("book1")


def test_list_all_annotations(mock_apple_books):
    result = list_all_annotations()
    # New in v0.7.0: lean grouped-by-book output — id + chapter,
    # no selected_text body. Surrounding text is a follow-up call.
    assert "[anno1]" in result.text
    assert "Book 1" in result.text
    # Ordering defaults to recent-first so heavily-deleted old books
    # (orphan asset_ids) don't dominate the top of the listing.
    mock_apple_books.list_annotations.assert_called_once_with(
        limit=None, order_by="-creation_date"
    )


def test_list_annotations_by_book(mock_apple_books):
    result = list_annotations("book1")
    # Book-scoped listing: no book name per row (caller passed book_id),
    # just the annotation id and chapter.
    assert "[anno1]" in result.text
    # The book name should NOT repeat in each row — that's the whole
    # point of taking book_id as an argument.
    assert "Book 1" not in result.text
    mock_apple_books.get_book_by_id.assert_called_with("book1")


def test_list_annotations_empty_for_book_with_none(mock_apple_books):
    # Book exists but has no annotations — should return a friendly
    # message instead of empty output.
    book = mock_apple_books.get_book_by_id.return_value
    book.annotations = []
    result = list_annotations("book1")
    assert "No annotations" in result.text


def test_list_annotations_unknown_book(mock_apple_books):
    mock_apple_books.get_book_by_id.side_effect = IndexError()
    result = list_annotations("99999")
    assert "No book found" in result.text


def test_get_highlights_by_color(mock_apple_books):
    result = get_highlights_by_color("yellow")
    assert "Test text" in result.text
    mock_apple_books.get_annotations_by_color.assert_called_once_with("yellow", limit=None)


def test_search_notes(mock_apple_books):
    result = search_notes("note")
    assert "Test text" in result.text
    mock_apple_books.search_annotation_by_note.assert_called_once_with("note", limit=None)


def test_search_annotations(mock_apple_books):
    result = search_annotations("test")
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

    # Orphaned annotations (asset_id no longer maps to a library book) get
    # an explicit "no longer in library" suffix instead of silently
    # disappearing into an "Unknown Book" bucket.
    assert "no longer in library" in result.text
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


def test_annotation_lean_row_prefers_selected_text(mock_apple_books):
    """v0.7.0 list-style tools render lean rows: ``[id] selected — chapter``.
    The fuller ``representative_text`` stays out of these — callers who want
    context follow up with ``describe_annotation`` or
    ``get_annotation_context``."""
    anno = MockAnnotation()
    anno.selected_text = "minus one"
    anno.representative_text = "A caret acts like a minus one in git revision syntax."
    mock_apple_books.list_annotations.return_value = [anno]

    result = recent_annotations()
    # Lean output shows the highlight the user actually selected.
    assert "minus one" in result.text
    # Representative text is NOT shown inline — that's the whole point of
    # the lean format.
    assert "A caret acts like" not in result.text


def test_annotation_output_includes_date(mock_apple_books):
    """The flat-with-timestamp format leads each row with YYYY-MM-DD so
    Claude can cluster annotations into reading sessions."""
    result = recent_annotations()
    assert "2026-04-16" in result.text


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


def test_get_annotation_context_marks_highlight(mock_apple_books):
    """The tool wraps the selected_text with guillemets inside the
    returned window so Claude can see the exact anchor."""
    result = get_annotation_context(1)
    assert "«Test text»" in result.text
    assert "Some preceding text" in result.text
    assert "Some following text" in result.text
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
    result = get_annotation_context(1)
    assert "No surrounding context available" in result.text
    assert "CFI" in result.text


def test_get_annotation_context_unknown_id(mock_apple_books):
    mock_apple_books.get_annotation_by_id.side_effect = IndexError()
    result = get_annotation_context(99999)
    assert "No annotation found with id 99999" in result.text


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
    assert "Book id: book1" in content
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
    assert "Book 1" in result.text
    mock_apple_books.list_books.assert_called_once()
    mock_apple_books.list_annotations.assert_called_once()


# ---------------------------------------------------------------------------
# v0.7.1 regression fixes
# ---------------------------------------------------------------------------


def test_list_all_books_uses_bracketed_id_format(mock_apple_books):
    """v0.7.1: list_all_books emits ``[id] title by author`` per row so
    Claude can hand off to describe_book / list_annotations without a
    second lookup. The old raw ``ID:\\nTitle:\\n...`` block is gone."""
    result = list_all_books()
    assert "[book1] Book 1 by Author 1" in result.text
    assert "Description: None" not in result.text  # old format gone


def test_search_books_by_title_uses_bracketed_id_format(mock_apple_books):
    result = search_books_by_title("Book")
    assert "[book1] Book 1 by Author 1" in result.text


def test_get_books_by_genre_includes_book_id(mock_apple_books):
    """v0.7.1: genre output must include [id] for hand-off."""
    result = get_books_by_genre("Romance")
    assert "[book1]" in result.text
    assert "Romance" in result.text


def test_list_all_collections_uses_bracketed_id_format(mock_apple_books):
    result = list_all_collections()
    assert "[col1] Collection 1" in result.text
    assert "Details: None" not in result.text


def test_search_collections_by_title_uses_bracketed_id_format(mock_apple_books):
    result = search_collections_by_title("Collection")
    assert "[col1] Collection 1" in result.text


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
    result = get_collection_books("col1")
    assert "[book1] Book 1 by Author 1" in result.text
    assert "MARKETING BLURB" not in result.text


def test_reading_status_rows_include_book_id(mock_apple_books):
    """v0.7.1: every reading-status row must carry the book_id as the
    leading [N] — otherwise Claude can't hand off to per-book tools."""
    for fn in (get_books_in_progress, get_finished_books,
               get_unstarted_books, get_recently_read_books):
        result = fn()
        assert "[book1]" in result.text, (
            f"{fn.__name__} missing [book1] id in row"
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
    book.annotations = []
    result = get_current_reading_position(999)
    assert "No reading position and no highlights yet" in result.text


def test_library_stats_separates_orphan_annotations(mock_apple_books):
    """v0.7.1: orphan annotations (book no longer in library) are
    counted separately instead of clustering under 'Unknown Book' in
    the 'most annotated' list."""
    orphan = MockAnnotation()
    orphan.book = None
    valid = MockAnnotation()
    mock_apple_books.list_annotations.return_value = [orphan, valid]

    result = get_library_stats()
    # 'Unknown Book' should NOT appear in the top list anymore.
    assert "Unknown Book:" not in result.text
    # But the orphan count is surfaced separately.
    assert "from books no longer in the library" in result.text
    # The valid annotation still shows up under its real book.
    assert "Book 1" in result.text
