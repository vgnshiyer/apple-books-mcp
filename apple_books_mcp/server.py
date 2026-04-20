import logging
import re
from datetime import datetime
from mcp.types import (
    TextContent
)
from mcp.server.fastmcp import FastMCP
from py_apple_books import PyAppleBooks
from py_apple_books.exceptions import (
    AppleBooksError,
    BookNotDownloadedError,
    DRMProtectedError,
)

from apple_books_mcp.utils import (
    _build_current_reading_section,
    _chapter_title_map,
    _format_book_with_progress,
    _format_flat_with_timestamp,
    _format_grouped_by_book,
    _format_lean_row,
    _format_note_row,
    _get_book_title,
)

logger = logging.getLogger("apple-books-mcp")

mcp = FastMCP("apple-books")
apple_books = PyAppleBooks()


# -- Collections Tools --
@mcp.tool()
def list_all_collections(limit: int = None) -> TextContent:
    """
    List all collections in my Apple Books library.

    Args:
        limit: Maximum number of collections to return.
    """
    collections = apple_books.list_collections(limit=limit)
    collections_str = "\n".join([f"{str(collection)}\n" for collection in collections])
    return TextContent(
        type="text",
        text=f"Collections:\n{collections_str}"
    )


@mcp.tool()
def get_collection_books(collection_id: str) -> TextContent:
    """
    Get all books in a particular collection.

    Args:
        collection_id: The ID of the collection to get books from.
    """
    collection = apple_books.get_collection_by_id(collection_id)
    books_str = "\n".join([f"{str(book)}\n" for book in collection.books])
    return TextContent(
        type="text",
        text=f"Books:\n{books_str}"
    )


@mcp.tool()
def describe_collection(collection_id: str) -> TextContent:
    """
    Describe a specific collection in detail — title, details text,
    and the books contained in it.

    Args:
        collection_id: The ID of the collection to get details for.
    """
    try:
        collection = apple_books.get_collection_by_id(collection_id)
    except IndexError:
        return TextContent(
            type="text", text=f"No collection found with id {collection_id}."
        )

    title = getattr(collection, "title", None) or "Untitled Collection"
    lines = [title, f"  Collection id: {collection.id}"]

    details = (getattr(collection, "details", None) or "").strip()
    if details:
        lines.append(f"  Details: {details}")

    try:
        books = list(collection.books)
    except Exception:
        books = []

    lines.append(f"  Books: {len(books)}")
    if books:
        lines.append("")
        for book in books:
            btitle = getattr(book, "title", None) or "Unknown Title"
            bauthor = getattr(book, "author", None) or "Unknown Author"
            lines.append(f"  - [{book.id}] {btitle} by {bauthor}")

    return TextContent(type="text", text="\n".join(lines))


@mcp.tool()
def search_collections_by_title(title: str) -> TextContent:
    """
    Search for collections by title.

    Args:
        title: The title to search for.
    """
    collections = apple_books.get_collection_by_title(title)
    collections_str = "\n".join([f"{str(collection)}\n" for collection in collections])
    return TextContent(
        type="text",
        text=f"Collections:\n{collections_str}"
    )


# -- Books Tools --
@mcp.tool()
def list_all_books(limit: int = None) -> TextContent:
    """
    List all books in my Apple Books library.

    Args:
        limit: Maximum number of books to return.
    """
    books = apple_books.list_books(limit=limit)
    books_str = "\n".join([f"{str(book)}\n" for book in books])
    return TextContent(
        type="text",
        text=f"Books:\n{books_str}"
    )


@mcp.tool()
def describe_book(book_id: str) -> TextContent:
    """
    Describe a specific book in detail — metadata (title, author, genre,
    page count), reading status (progress, last opened, finished date),
    and annotation count.

    Args:
        book_id: The ID of the book to get.
    """
    try:
        book = apple_books.get_book_by_id(book_id)
    except IndexError:
        return TextContent(type="text", text=f"No book found with id {book_id}.")

    title = getattr(book, "title", None) or "Unknown Title"
    author = getattr(book, "author", None) or "Unknown Author"

    lines = [f"{title} by {author}", f"  Book id: {book.id}"]

    genre = getattr(book, "genre", None)
    if genre:
        lines.append(f"  Genre:    {genre}")

    page_count = getattr(book, "page_count", None)
    if page_count:
        lines.append(f"  Pages:    {page_count}")

    # Reading status — reuse the same compact summary used elsewhere.
    lines.append(f"  {book.format_progress_summary()}")

    finished_date = getattr(book, "finished_date", None)
    if finished_date:
        lines.append(f"  Finished: {finished_date.strftime('%Y-%m-%d')}")

    purchased_date = getattr(book, "purchased_date", None)
    if purchased_date:
        lines.append(f"  Added:    {purchased_date.strftime('%Y-%m-%d')}")

    rating = getattr(book, "rating", None)
    if rating:
        lines.append(f"  Rating:   {rating}/5")

    # Annotation count — useful signal for "should I bother listing them?"
    try:
        anno_count = len(list(book.annotations))
    except Exception:
        anno_count = 0
    if anno_count:
        lines.append(f"  Annotations: {anno_count}")

    description = (getattr(book, "description", None) or "").strip()
    if description:
        lines.append("")
        lines.append(f"About: {description}")

    return TextContent(type="text", text="\n".join(lines))


@mcp.tool()
def search_books_by_title(title: str) -> TextContent:
    """
    Search for books by title.

    Args:
        title: The title to search for.
    """
    books = apple_books.get_book_by_title(title)
    books_str = "\n".join([f"{str(book)}\n" for book in books])
    return TextContent(
        type="text",
        text=f"Books:\n{books_str}"
    )


@mcp.tool()
def get_books_by_genre(genre: str, limit: int = None) -> TextContent:
    """
    Get books whose genre matches the given string (substring match).

    Args:
        genre: The genre to search for (e.g. "Romance", "Philosophy").
        limit: Maximum number of books to return.
    """
    books = apple_books.get_books_by_genre(genre, limit=limit)
    books_str = "\n".join([
        f"{getattr(book, 'title', 'Unknown')} by {getattr(book, 'author', 'Unknown')} ({getattr(book, 'genre', None)})"
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Books:\n{books_str}"
    )


# -- Reading Status Tools --
@mcp.tool()
def get_books_in_progress(limit: int = None) -> TextContent:
    """
    Get all books currently being read (progress > 0% and < 100%).

    Args:
        limit: Maximum number of books to return.
    """
    books = apple_books.get_books_in_progress(limit=limit)
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Books in progress:\n{books_str}"
    )


@mcp.tool()
def get_finished_books(limit: int = None) -> TextContent:
    """
    Get all books that have been finished.

    Args:
        limit: Maximum number of books to return.
    """
    books = apple_books.get_finished_books(limit=limit)
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Finished books:\n{books_str}"
    )


@mcp.tool()
def get_unstarted_books(limit: int = None) -> TextContent:
    """
    Get all books that haven't been started yet (0% progress).

    Args:
        limit: Maximum number of books to return.
    """
    books = apple_books.get_unstarted_books(limit=limit)
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Unstarted books:\n{books_str}"
    )


@mcp.tool()
def get_recently_read_books(limit: int = 10) -> TextContent:
    """
    Get most recently opened books, ordered by last opened date.

    Args:
        limit: Maximum number of books to return. Defaults to 10.
    """
    books = apple_books.get_recently_read_books(limit=limit)
    books_str = "\n".join([
        _format_book_with_progress(book)
        for book in books
    ])
    return TextContent(
        type="text",
        text=f"Recently read books:\n{books_str}"
    )


# -- Annotations Tools --
@mcp.tool()
def list_all_annotations(limit: int = None) -> TextContent:
    """
    Browse all your annotations grouped by book, most recent first.
    Each row is ``[annotation_id] <text> — <chapter title> (ch=<id>)``.
    For the surrounding passage of a specific highlight, follow up
    with ``get_annotation_context(annotation_id)``. For the full
    chapter, pass the emitted ``ch=<id>`` to ``get_chapter_content``.

    Chapter resolution uses Apple Books' CFI hints; books that can't
    be opened (DRM, iCloud-only) still appear, just without the
    chapter suffix.

    Args:
        limit: Maximum number of annotations to return. Applied before
            grouping; defaults to unlimited, which may produce a very
            long list on heavily-annotated libraries.
    """
    # Default to newest-first — old annotations are often from books
    # the user has since removed from their library (orphan rows), and
    # the library's DB stores in Z_PK order, so the oldest entries
    # surface first. Sorting by creation date descending puts current
    # reading activity at the top of the output.
    annotations = list(
        apple_books.list_annotations(limit=limit, order_by="-creation_date")
    )
    if not annotations:
        return TextContent(type="text", text="No annotations.")

    # Group by book. Orphans (asset_id → no book in the library) get a
    # dedicated tail section.
    from collections import defaultdict
    by_book: dict = defaultdict(list)
    orphans: list = []
    for anno in annotations:
        book = getattr(anno, "book", None)
        if book is None:
            orphans.append(anno)
        else:
            by_book[book.id].append((anno, book))

    # Resolve chapter titles per book exactly once.
    chapter_maps = {book_id: _chapter_title_map(apple_books, book_id) for book_id in by_book}

    lines: list = []
    for book_id, pairs in by_book.items():
        book = pairs[0][1]
        author = getattr(book, "author", None) or "Unknown Author"
        lines.append(f"\n{book.title} ({author}):")
        ch_map = chapter_maps[book_id]
        for anno, _ in pairs:
            lines.append(f"  {_format_lean_row(anno, ch_map)}")

    if orphans:
        lines.append("\nUnassigned (book no longer in library):")
        for anno in orphans:
            lines.append(f"  {_format_lean_row(anno, {})}")

    return TextContent(type="text", text="\n".join(lines).lstrip())


@mcp.tool()
def list_annotations(book_id: int, limit: int = None) -> TextContent:
    """
    List annotations within a specific book. Returns minimal rows —
    ``[annotation_id] <chapter title>`` — since the caller already
    scoped the query by book id.

    Ordered by chapter position in the book (reading order), then by
    creation date for ties. Unresolvable chapters (sub-sections not
    in the ToC, DRM books) show ``—``.

    Args:
        book_id: The book's numeric ID.
        limit: Maximum number of annotations to return.
    """
    try:
        book = apple_books.get_book_by_id(book_id)
    except IndexError:
        return TextContent(type="text", text=f"No book found with id {book_id}.")

    annotations = list(book.annotations)
    if not annotations:
        return TextContent(
            type="text", text=f"No annotations in '{book.title}'."
        )

    ch_map = _chapter_title_map(apple_books, book.id)
    # Also precompute chapter order for a reading-order sort.
    try:
        content = apple_books.get_book_content(book.id)
        chapter_order = {c.id: c.order for c in content.list_chapters()}
    except (BookNotDownloadedError, DRMProtectedError, AppleBooksError):
        chapter_order = {}

    def _sort_key(a):
        cid = a.location.chapter_id if a.location else None
        order = chapter_order.get(cid, float("inf")) if cid else float("inf")
        created = getattr(a, "creation_date", None)
        return (order, created or datetime.min)

    annotations.sort(key=_sort_key)
    if limit:
        annotations = annotations[:limit]

    lines = [_format_lean_row(a, ch_map) for a in annotations]
    return TextContent(type="text", text="\n".join(lines))


@mcp.tool()
def get_highlights_by_color(color: str, limit: int = None) -> TextContent:
    """
    Browse highlights of a particular color, grouped by book.

    Output is one row per highlight: ``[id] text — chapter``. The
    book's name is shown once in the header, with a count of matching
    highlights.

    Args:
        color: ``yellow``, ``green``, ``blue``, ``pink``, or ``purple``.
        limit: Maximum number of annotations to return.
    """
    annotations = apple_books.get_annotations_by_color(color, limit=limit)

    def color_header(book, annos):
        author = getattr(book, "author", None) or "Unknown Author"
        count = len(annos)
        plural = "" if count == 1 else "s"
        return f"{book.title} ({author}) — {count} {color.lower()} highlight{plural}:"

    text = _format_grouped_by_book(
        apple_books,
        annotations,
        empty_message=f"No {color} highlights.",
        book_header=color_header,
    )
    return TextContent(type="text", text=text)


@mcp.tool()
def search_notes(note: str, limit: int = None) -> TextContent:
    """
    Search user notes (not highlights) by substring, grouped by book.
    Output shows the highlighted passage on the primary row and the
    matching note on a second line prefixed with ``↳ note:``.

    Args:
        note: Substring to find inside note bodies.
        limit: Maximum number of annotations to return.
    """
    annotations = apple_books.search_annotation_by_note(note, limit=limit)
    return TextContent(
        type="text",
        text=_format_grouped_by_book(
            apple_books,
            annotations,
            empty_message=f"No notes matched {note!r}.",
            row_formatter=_format_note_row,
        ),
    )


@mcp.tool()
def search_annotations(text: str, limit: int = None) -> TextContent:
    """
    Search across every annotation field — selected (highlighted) text,
    the surrounding paragraph, and the user's note body. Grouped by
    book. Use ``search_notes`` when you only want to find your own
    written notes.

    Args:
        text: Substring to match anywhere in an annotation.
        limit: Maximum number of annotations to return.
    """
    annotations = apple_books.search_annotation_by_text(text, limit=limit)
    return TextContent(
        type="text",
        text=_format_grouped_by_book(
            apple_books, annotations, empty_message=f"No annotations matched {text!r}."
        ),
    )


@mcp.tool()
def recent_annotations(limit: int = 10) -> TextContent:
    """
    Most recent annotations, newest first. Flat rows with the creation
    date and book name inline so Claude can see chronology across
    books at a glance.

    Row format::

        YYYY-MM-DD [id] text — chapter · Book Title

    Args:
        limit: Maximum number of annotations to return. Defaults to 10.
    """
    annotations = apple_books.list_annotations(limit=limit, order_by="-creation_date")
    return TextContent(
        type="text",
        text=_format_flat_with_timestamp(
            apple_books, annotations, empty_message="No annotations."
        ),
    )


@mcp.tool()
def describe_annotation(annotation_id: str) -> TextContent:
    """
    Describe a specific annotation in detail — its text, note, book,
    chapter (with chapter_id for hand-off to ``get_chapter_content``),
    color, creation date, and raw CFI. Use this when you have an
    annotation id from one of the listing tools and want the full
    picture. For just the surrounding paragraph, call
    ``get_annotation_context`` instead.

    Args:
        annotation_id: The annotation's numeric ID.
    """
    try:
        anno = apple_books.get_annotation_by_id(annotation_id)
    except IndexError:
        return TextContent(
            type="text", text=f"No annotation found with id {annotation_id}."
        )

    book = getattr(anno, "book", None)
    book_title = getattr(book, "title", None) or "(book no longer in library)"
    book_author = getattr(book, "author", None) or "?"

    # Resolve chapter title via the CFI, same as the listing tools.
    chapter_title = ""
    chapter_id = None
    if book and anno.location and anno.location.chapter_id:
        chapter_id = anno.location.chapter_id
        ch_map = _chapter_title_map(apple_books, book.id)
        chapter_title = ch_map.get(chapter_id, "")

    created = getattr(anno, "creation_date", None)
    created_str = created.strftime("%Y-%m-%d %H:%M") if created else "unknown"

    lines = [
        f"Annotation {anno.id}",
        f"  Book:     {book_title} ({book_author})",
    ]
    # Show both the human title and the id so Claude can pass chapter_id
    # directly to get_chapter_content without rescanning the ToC.
    if chapter_title and chapter_id:
        lines.append(f"  Chapter:  {chapter_title} (ch={chapter_id})")
    elif chapter_id:
        lines.append(f"  Chapter:  (ch={chapter_id})")
    lines.append(f"  Created:  {created_str}")
    if getattr(anno, "color", None):
        lines.append(f"  Color:    {anno.color}")

    selected = (getattr(anno, "selected_text", None) or "").strip()
    rep = (getattr(anno, "representative_text", None) or "").strip()
    note = (getattr(anno, "note", None) or "").strip()

    if selected:
        lines.append("")
        lines.append(f'  Highlighted: "{selected}"')
    if rep and rep != selected:
        lines.append(f'  In context:  "{rep}"')
    if note:
        lines.append(f"  Note:        {note}")

    cfi = str(anno.location) if anno.location else ""
    if cfi:
        lines.append("")
        lines.append(f"  CFI: {cfi}")

    return TextContent(type="text", text="\n".join(lines))


@mcp.tool()
def get_annotation_context(
    annotation_id: int,
    chars_before: int = 500,
    chars_after: int = 500,
) -> TextContent:
    """
    Return a text window around a single annotation — the passage
    immediately before and after the user's highlight, snapped to word
    boundaries. The highlighted text itself is wrapped in guillemets
    (``«...»``) inside the output so Claude can see the exact anchor.

    Use this to explain or expand on a specific highlight without
    fetching the whole chapter. For a full-chapter view, follow up with
    ``get_chapter_content``.

    Only works for non-DRM EPUBs that have been downloaded to this Mac.
    DRM-protected Apple Books Store purchases, iCloud-only books, and
    annotations whose CFI has no chapter hint all degrade to a clear
    "no context available" message.

    Args:
        annotation_id: The annotation's numeric ID (from any of the
            listing tools).
        chars_before: Characters of context before the highlight.
            Default 500.
        chars_after: Characters of context after the highlight.
            Default 500.
    """
    try:
        anno = apple_books.get_annotation_by_id(annotation_id)
    except IndexError:
        return TextContent(
            type="text",
            text=f"No annotation found with id {annotation_id}.",
        )

    try:
        window = apple_books.get_annotation_surrounding_text(
            annotation_id,
            chars_before=chars_before,
            chars_after=chars_after,
        )
    except (BookNotDownloadedError, DRMProtectedError, AppleBooksError) as e:
        return TextContent(
            type="text", text=f"Could not read annotation context: {e}"
        )

    if not window:
        # Backend returns "" in several degraded cases — surface a
        # reason so Claude (or the user) knows whether to retry with a
        # different annotation or move on.
        if not anno.location or not anno.location.chapter_id:
            reason = (
                "this annotation has no CFI chapter hint "
                "(likely an older or iCloud-only highlight)."
            )
        elif getattr(anno, "book", None) is None:
            reason = "the book is no longer in the library."
        else:
            reason = (
                "the book isn't downloaded locally, is DRM-protected, "
                "or the highlight's anchor text can't be located."
            )
        return TextContent(
            type="text", text=f"No surrounding context available: {reason}"
        )

    # Wrap the highlight with guillemets so Claude can see exactly which
    # span the user marked. Match with flexible whitespace — Apple
    # Books stores ``selected_text`` with the EPUB's original line
    # breaks intact, but our HTML→text extraction normalizes them to
    # spaces, so a literal ``in`` check fails for most multi-line
    # highlights. First occurrence only: if the anchor text repeats in
    # the window, subsequent occurrences stay unmarked to avoid
    # implying they're all highlighted.
    selected = (getattr(anno, "selected_text", None) or "").strip()
    tokens = selected.split() if selected else []
    if tokens:
        pattern = r"\s+".join(re.escape(t) for t in tokens)
        m = re.search(pattern, window)
        if m:
            matched = m.group(0)
            window = window.replace(matched, f"«{matched}»", 1)

    return TextContent(type="text", text=window)


@mcp.tool()
def get_annotations_by_date_range(after: str = None, before: str = None, limit: int = None) -> TextContent:
    """
    Annotations created within a date range. Flat rows with the
    creation date and book name inline.

    Row format::

        YYYY-MM-DD [id] text — chapter · Book Title

    Args:
        after: Only include annotations created on or after this date
            (YYYY-MM-DD).
        before: Only include annotations created on or before this
            date (YYYY-MM-DD).
        limit: Maximum number of annotations to return.
    """
    after_dt = datetime.strptime(after, "%Y-%m-%d") if after else None
    before_dt = datetime.strptime(before, "%Y-%m-%d") if before else None

    annotations = apple_books.get_annotations_by_date_range(
        after=after_dt, before=before_dt, limit=limit
    )
    return TextContent(
        type="text",
        text=_format_flat_with_timestamp(
            apple_books, annotations, empty_message="No annotations in that range."
        ),
    )


# -- Content Tools --
@mcp.tool()
def list_book_chapters(book_id: int) -> TextContent:
    """
    List the table of contents for a book — chapter titles, order, and
    nesting depth. Only works for non-DRM EPUBs that have been downloaded
    to this Mac.

    Args:
        book_id: The book's numeric ID (from ``list_all_books`` or similar).
    """
    try:
        content = apple_books.get_book_content(book_id)
    except BookNotDownloadedError as e:
        return TextContent(type="text", text=f"Book not available: {e}")
    except DRMProtectedError as e:
        return TextContent(type="text", text=f"Book is DRM-protected: {e}")
    except AppleBooksError as e:
        return TextContent(type="text", text=f"Could not open book: {e}")
    except IndexError:
        return TextContent(
            type="text", text=f"No book found with id {book_id}."
        )

    try:
        chapters = content.list_chapters()
    except AppleBooksError as e:
        return TextContent(type="text", text=f"Could not list chapters: {e}")

    if not chapters:
        return TextContent(type="text", text="No chapters found for this book.")

    lines = [f"Chapters ({len(chapters)} total):"]
    for ch in chapters:
        indent = "  " * ch.depth
        lines.append(f"  [{ch.order:>3}] {indent}{ch.title}  (id={ch.id})")
    return TextContent(type="text", text="\n".join(lines))


@mcp.tool()
def get_chapter_content(
    book_id: int,
    chapter_id: str,
    offset: int = 0,
    max_chars: int = None,
) -> TextContent:
    """
    Return the plain-text content of a specific chapter, with optional
    slicing so long chapters don't consume the whole context window.

    ``chapter_id`` should come from a prior ``list_book_chapters`` call
    (or from the ``(ch=...)`` suffix printed on annotation listing rows).
    It accepts either the ``Chapter.id`` value or the 1-based chapter
    order rendered as a string (e.g. ``"5"``). Only works for non-DRM
    EPUBs that have been downloaded to this Mac.

    Pagination is opt-in — the tool never truncates on its own. If you
    don't pass ``max_chars``, you get the whole chapter, however long.
    To sample a long chapter, pass ``max_chars`` (and optionally
    ``offset`` for continuation).

    Every response ends with an explicit footer showing the exact
    ``offset`` / ``end`` / ``total`` character counts, so you can
    decide whether to re-call with a new ``offset`` without guessing.
    Example footers::

        (full chapter returned: 4,872 chars.)
        …(returned chars 0–8000 of 24,311 [8000 chars]. Call again with
          offset=8000 to continue; 16,311 chars remaining.)
        (returned chars 16000–24311 of 24311 [8311 chars]. End of chapter.)

    Args:
        book_id: The book's numeric ID.
        chapter_id: The chapter identifier or 1-based position.
        offset: Character offset to start from (0 = start of chapter).
            Useful for paginating through a long chapter without
            re-reading the part you've already seen.
        max_chars: Maximum characters to return in this call. Omit (or
            pass ``None``) to get the whole chapter from ``offset``
            onward. Set to something like ``8000`` when you want to
            sample a long chapter without blowing up the context.
    """
    try:
        content = apple_books.get_book_content(book_id)
    except BookNotDownloadedError as e:
        return TextContent(type="text", text=f"Book not available: {e}")
    except DRMProtectedError as e:
        return TextContent(type="text", text=f"Book is DRM-protected: {e}")
    except AppleBooksError as e:
        return TextContent(type="text", text=f"Could not open book: {e}")
    except IndexError:
        return TextContent(
            type="text", text=f"No book found with id {book_id}."
        )

    try:
        text = content.get_chapter(chapter_id)
    except AppleBooksError as e:
        return TextContent(type="text", text=f"Could not read chapter: {e}")

    if not text.strip():
        return TextContent(
            type="text",
            text="(This chapter has no extractable text — likely an image-only page.)",
        )

    total_chars = len(text)

    # Clamp offset to the chapter's length — an out-of-range offset
    # should fail soft with a clear message instead of silently
    # returning "".
    if offset < 0:
        offset = 0
    if offset >= total_chars:
        return TextContent(
            type="text",
            text=(
                f"Offset {offset} is past the end of the chapter "
                f"(total {total_chars} chars). Pass a smaller offset."
            ),
        )

    if max_chars is None:
        sliced = text[offset:]
        end = total_chars
    else:
        if max_chars <= 0:
            return TextContent(
                type="text", text="max_chars must be a positive integer."
            )
        end = min(offset + max_chars, total_chars)
        sliced = text[offset:end]

    # Always append a pagination footer so Claude sees exactly what it
    # just received and whether there's more. The tool does not auto-
    # paginate — if Claude wants the rest, it calls again with the
    # suggested offset.
    returned = end - offset
    remaining = total_chars - end
    if remaining > 0:
        footer = (
            f"…(returned chars {offset}–{end} of {total_chars} "
            f"[{returned} chars]. Call again with offset={end} to "
            f"continue; {remaining} chars remaining.)"
        )
    elif offset == 0 and end == total_chars:
        footer = f"(full chapter returned: {total_chars} chars.)"
    else:
        footer = (
            f"(returned chars {offset}–{end} of {total_chars} "
            f"[{returned} chars]. End of chapter.)"
        )

    return TextContent(type="text", text=f"{sliced}\n\n{footer}")


@mcp.tool()
def get_current_reading_position(book_id: int) -> TextContent:
    """
    Return the chapter the user last left off reading, based on Apple Books'
    auto-tracked reading position bookmark. Useful when the caller wants to
    focus on a specific book rather than the global ``currently-reading``
    resource.

    Returns the chapter metadata only (title, order, href). For the chapter
    text, follow up with ``get_chapter_content``. Only works for non-DRM
    EPUBs that have been downloaded to this Mac.

    Args:
        book_id: The book's numeric ID.
    """
    try:
        chapter = apple_books.get_current_reading_chapter(book_id)
    except BookNotDownloadedError as e:
        return TextContent(type="text", text=f"Book not available: {e}")
    except DRMProtectedError as e:
        return TextContent(type="text", text=f"Book is DRM-protected: {e}")
    except AppleBooksError as e:
        return TextContent(type="text", text=f"Could not resolve position: {e}")
    except IndexError:
        return TextContent(
            type="text", text=f"No book found with id {book_id}."
        )

    if chapter is None:
        return TextContent(
            type="text",
            text="No reading position recorded for this book yet.",
        )

    return TextContent(
        type="text",
        text=(
            f"Current chapter: [{chapter.order}] {chapter.title}\n"
            f"Chapter id (for get_chapter_content): {chapter.id}\n"
            f"Depth in ToC: {chapter.depth}\n"
            f"File: {chapter.href}"
        ),
    )


# -- Library Stats Tools --
@mcp.tool()
def get_library_stats() -> TextContent:
    """Get a summary of your Apple Books library with reading stats."""
    books = list(apple_books.list_books())
    annotations = list(apple_books.list_annotations())

    total_books = len(books)
    finished = sum(1 for b in books if getattr(b, "is_finished", False))
    in_progress = sum(
        1 for b in books
        if (getattr(b, "reading_progress", 0) or 0) > 0
        and not getattr(b, "is_finished", False)
    )
    unstarted = total_books - finished - in_progress

    total_annotations = len(annotations)

    # Count annotations per book
    anno_counts = {}
    for anno in annotations:
        title = _get_book_title(anno)
        anno_counts[title] = anno_counts.get(title, 0) + 1

    top_annotated = sorted(anno_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_str = "\n".join([f"  {title}: {count}" for title, count in top_annotated])

    stats = (
        f"Total books: {total_books}\n"
        f"Finished: {finished}\n"
        f"In progress: {in_progress}\n"
        f"Unstarted: {unstarted}\n"
        f"Total annotations: {total_annotations}\n"
        f"Most annotated books:\n{top_str}"
    )

    return TextContent(
        type="text",
        text=stats
    )


# -- Resources --
@mcp.resource(
    "apple-books://currently-reading",
    name="Currently Reading",
    description=(
        "A lightweight pointer to the book you're actively reading right "
        "now — title, author, ids, progress, and the chapter you last "
        "left off on. Attach this to any conversation to give Claude your "
        "reading context without pulling chapter text or annotations; "
        "Claude can fetch those on demand via get_chapter_content, "
        "list_annotations, and get_annotation_context."
    ),
    mime_type="text/plain",
)
def currently_reading_resource() -> str:
    """Lean resource: metadata + ids + chapter pointer.

    By design this does NOT embed chapter text or annotations — pulling
    them eagerly inflated attached context by ~10–15k chars per use.
    The resource now weighs in at ~300 chars and hands Claude the
    book_id + chapter_id it needs to fetch richer content on demand.
    """
    books = list(apple_books.get_books_in_progress(limit=1, order_by="-last_opened_date"))
    if not books:
        return "No book currently in progress."

    book = books[0]
    author = getattr(book, "author", None) or "Unknown Author"
    title = getattr(book, "title", None) or "Unknown Title"

    sections: list[str] = [
        f"Currently Reading: {title} by {author}",
        f"  Book id: {book.id}",
        f"  {book.format_progress_summary()}",
    ]

    # Current chapter pointer — metadata only, no text.
    reading_section = _build_current_reading_section(apple_books, book)
    if reading_section:
        sections.append(reading_section)

    # Annotation count only — keeps the resource cheap. Claude can call
    # list_annotations(book_id) when it actually wants to browse them.
    try:
        anno_count = len(list(book.annotations))
    except Exception:
        anno_count = 0
    if anno_count:
        sections.append(
            f"\nHighlights in this book: {anno_count}  "
            f"(use list_annotations({book.id}) to browse)"
        )
    else:
        sections.append("\nHighlights in this book: 0")

    return "\n".join(sections)


# -- Prompts --
@mcp.prompt()
def weekly_digest(days: int = 7) -> str:
    """Summarize what I've read and highlighted in the past week."""
    return (
        f"Give me a digest of my reading from the past {days} days.\n\n"
        f"Call `get_annotations_by_date_range` with `after` set to {days} days ago. "
        "Group highlights by book, then cluster within each book into reading sessions "
        "(highlights within ~30 minutes of each other belong to the same session). "
        "Identify recurring themes or ideas I seem to be circling. Call out anything "
        "surprising or interesting. Keep it under 400 words."
    )


@mcp.prompt()
def explain_recent_highlight() -> str:
    """Take my most recent highlight and explain what it means."""
    return (
        "Call `recent_annotations` with `limit=1` to get my most recent highlight. "
        "Then explain:\n\n"
        "1. What the passage is saying, in plain language.\n"
        "2. Why it likely caught my attention — what's interesting about it.\n"
        "3. How it fits into the broader argument of the book (if you know the book).\n\n"
        "Quote the passage first, then explain. Keep it tight."
    )


@mcp.prompt()
def what_am_i_reading() -> str:
    """Quick snapshot of books I'm currently in the middle of."""
    return (
        "Call `get_books_in_progress` to list what I'm actively reading. For each book, "
        "give a one-line characterization (title, author, progress, when I last opened "
        "it). Don't just restate the data — add a sentence about what each book is "
        "generally about if you know it. End with: \"What would you like to focus on?\""
    )


@mcp.prompt()
def library_snapshot() -> str:
    """A reflection on my whole reading life — what I've read, what I'm reading, what's stuck."""
    return (
        "Call `get_library_stats` and `get_books_in_progress`. Synthesize a reflection "
        "covering:\n\n"
        "- The overall shape of my library (total, finished, in progress, untouched)\n"
        "- What I'm actively engaged with right now\n"
        "- Themes across my most-annotated books — what do I seem drawn to?\n"
        "- One honest observation about my reading pattern (e.g., lots of unfinished "
        "ambition, or strong completion rate on certain genres, etc.)\n\n"
        "Under 300 words. Warm but honest."
    )


@mcp.prompt()
def revisit_book(book_title: str) -> str:
    """Revisit your notes and highlights from a specific book."""
    return (
        f"I want to revisit my notes on \"{book_title}\".\n\n"
        f"1. Call `search_books_by_title` with \"{book_title}\" to find it.\n"
        "2. Call `list_annotations` with the book's ID to pull every highlight "
        "(returns each as id + text + chapter).\n"
        "3. Group related highlights together by theme or argument.\n"
        "4. Surface the 2-3 most interesting threads — what was I fixated on in this book?\n"
        "5. If I wrote any notes (not just highlights), call those out — they usually "
        "contain my actual thinking.\n\n"
        "Format as a short essay, not a list. Quote me back to myself."
    )


def serve():
    """Serve the Apple Books MCP server."""
    logger.info("--- Started Apple Books MCP server ---")
    mcp.run(transport="stdio")
