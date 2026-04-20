"""Formatting + lookup helpers shared across the MCP tool definitions.

These are package-internal (``_``-prefixed) — not part of the MCP
surface. Tools in :mod:`server` call them for consistent output
formatting and for the lookups needed to attach chapter context to
annotations.

Anything that needs a live :class:`~py_apple_books.PyAppleBooks` instance
takes it as the first argument (``api``) — keeps this module free of
singletons and decoupled from ``server`` import order.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from py_apple_books.exceptions import (
    AppleBooksError,
    BookNotDownloadedError,
    DRMProtectedError,
)

if TYPE_CHECKING:
    from py_apple_books import PyAppleBooks

logger = logging.getLogger("apple-books-mcp")


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# Max characters shown for a single annotation in list-style outputs.
# Typical highlights are 1–3 sentences (50–200 chars); this cap keeps
# each row a single readable line without truncating most.
_LEAN_TEXT_CAP = 180


# --------------------------------------------------------------------------
# Book / annotation formatting (pure — no apple_books dependency)
# --------------------------------------------------------------------------


def _format_book_with_progress(book) -> str:
    """Two-line book summary used by reading-status tools. The first
    line carries the ``[id]`` so Claude can hand off to describe_book,
    list_annotations, or get_current_reading_position without a second
    lookup.
    """
    author = getattr(book, "author", None) or "Unknown Author"
    title = getattr(book, "title", None) or "Unknown Title"
    progress = book.format_progress_summary()
    return f"[{book.id}] {title} by {author}\n  {progress}"


def _format_book_row(book) -> str:
    """Single-line ``[id] title by author`` format for browse-style
    listings (list_all_books, search_books_by_title). Keeps the row
    compact; the ``[id]`` is the hand-off for follow-up tools.
    """
    author = getattr(book, "author", None) or "Unknown Author"
    title = getattr(book, "title", None) or "Unknown Title"
    return f"[{book.id}] {title} by {author}"


def _format_collection_row(collection) -> str:
    """Single-line ``[id] title`` for browse-style collection listings.
    Collections don't have authors; we add the book count in parens
    when it's available without triggering a relation fetch.
    """
    title = getattr(collection, "title", None) or "Untitled Collection"
    return f"[{collection.id}] {title}"


def _get_book_title(annotation) -> str:
    book = getattr(annotation, "book", None)
    return getattr(book, "title", None) or "Unknown Book"


# ``_format_annotation_with_book`` (legacy) was dropped in v0.7.0 — it
# used Apple's ``ZFUTUREPROOFING5`` chapter field, which is NULL for
# most annotations. The grouped/flat formatters below replace it with
# CFI-based chapter resolution via :func:`_chapter_title_map`.


# --------------------------------------------------------------------------
# Lean annotation rows (id + text + chapter) — for list_annotations tools
# --------------------------------------------------------------------------


def _annotation_chapter_title(annotation, chapter_map: dict) -> str:
    """Look up the annotation's chapter title from a prefetched map.
    Returns an empty string when the annotation has no CFI chapter
    hint or the hinted id isn't in the book's ToC (sub-section case).
    """
    if not annotation.location or not annotation.location.chapter_id:
        return ""
    return chapter_map.get(annotation.location.chapter_id, "")


def _lean_annotation_text(annotation) -> str:
    """Pick a short, single-line representation of an annotation's
    highlight text for list-style outputs. Prefers ``selected_text``;
    falls back to ``representative_text``. Collapses interior whitespace
    and truncates at :data:`_LEAN_TEXT_CAP`.
    """
    sel = (getattr(annotation, "selected_text", None) or "").strip()
    rep = (getattr(annotation, "representative_text", None) or "").strip()
    text = sel or rep
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) > _LEAN_TEXT_CAP:
        text = text[: _LEAN_TEXT_CAP].rsplit(" ", 1)[0] + "…"
    return text


def _format_lean_row(annotation, chapter_map: dict) -> str:
    """Render one annotation as ``[id] text — chapter (ch=chapter_id)``.

    Pieces are dropped in order when unavailable:

    * no text → row shows only id + chapter
    * no chapter title but a ``chapter_id`` bracket hint → still
      emits ``(ch=...)`` so Claude can hand it straight to
      :mcp:`get_chapter_content` without round-tripping through
      ``list_book_chapters``
    * neither title nor id (bookmark, DRM, orphan) → bare ``[id]``
    """
    text = _lean_annotation_text(annotation)
    chapter_id = (
        annotation.location.chapter_id
        if annotation.location and annotation.location.chapter_id
        else None
    )
    chapter_title = chapter_map.get(chapter_id, "") if chapter_id else ""
    aid = annotation.id

    parts = [f"[{aid}]"]
    if text:
        parts.append(text)
    if chapter_title:
        parts.append(f"— {chapter_title}")
    if chapter_id:
        parts.append(f"(ch={chapter_id})")

    return " ".join(parts)


def _format_note_row(annotation, chapter_map: dict) -> str:
    """Render a user-note annotation — same shape as ``_format_lean_row``
    but the note body appears on a second line, prefixed with ``↳``.

    Notes are rarer than highlights and carry the user's own thinking
    rather than book text, so showing both the highlighted passage and
    the note keeps the output interpretable without forcing callers
    into a separate follow-up lookup.
    """
    primary = _format_lean_row(annotation, chapter_map)
    note = (getattr(annotation, "note", None) or "").strip()
    if not note:
        return primary
    note = " ".join(note.split())
    if len(note) > _LEAN_TEXT_CAP:
        note = note[: _LEAN_TEXT_CAP].rsplit(" ", 1)[0] + "…"
    return f"{primary}\n    ↳ note: {note}"


def _iso_date(annotation) -> str:
    """YYYY-MM-DD rendering of the annotation's creation date, or the
    literal string ``"?"`` when missing."""
    created = getattr(annotation, "creation_date", None)
    return created.strftime("%Y-%m-%d") if created else "?"


# --------------------------------------------------------------------------
# Group-by-book formatter — used by the grouped-output annotation tools
# --------------------------------------------------------------------------


def _format_grouped_by_book(
    api: "PyAppleBooks",
    annotations,
    *,
    empty_message: str = "No annotations.",
    row_formatter=None,
    book_header: Optional[callable] = None,
) -> str:
    """Render a list of annotations grouped by their originating book.

    Each book gets a header line (``Book Title (Author):`` by default)
    followed by one :func:`_format_lean_row` per annotation. Orphan
    annotations (whose ``asset_id`` no longer maps to a book in the
    library) accumulate into a trailing "Unassigned" section rather
    than being dropped silently.

    :param api: Facade instance; needed to look up each book's chapter
        titles for CFI resolution.
    :param annotations: Iterable of annotations. The caller controls
        ordering; we preserve it within each book's group.
    :param empty_message: Returned verbatim when ``annotations`` is
        empty.
    :param row_formatter: Callable ``(annotation, chapter_map) -> str``
        used to render each row. Defaults to
        :func:`_format_lean_row`; pass :func:`_format_note_row` when
        annotations carry user notes you want surfaced inline.
    :param book_header: Optional callable ``(book, annotations) ->
        str`` that builds the per-book header. Defaults to
        ``"{title} ({author}):"``. Useful for tools that want to
        include a count or filter label in the header (e.g.
        ``{count} yellow highlights``).
    """
    annotations = list(annotations)
    if not annotations:
        return empty_message

    row_formatter = row_formatter or _format_lean_row

    from collections import defaultdict

    by_book: dict = defaultdict(list)
    orphans: list = []
    for anno in annotations:
        book = getattr(anno, "book", None)
        if book is None:
            orphans.append(anno)
        else:
            by_book[book.id].append((anno, book))

    chapter_maps = {book_id: _chapter_title_map(api, book_id) for book_id in by_book}

    lines: list = []
    for book_id, pairs in by_book.items():
        book = pairs[0][1]
        annos = [a for a, _ in pairs]
        if book_header is not None:
            header = book_header(book, annos)
        else:
            author = getattr(book, "author", None) or "Unknown Author"
            header = f"{book.title} ({author}):"
        lines.append(f"\n{header}")
        ch_map = chapter_maps[book_id]
        for anno in annos:
            lines.append(f"  {row_formatter(anno, ch_map)}")

    if orphans:
        lines.append("\nUnassigned (book no longer in library):")
        for anno in orphans:
            lines.append(f"  {row_formatter(anno, {})}")

    return "\n".join(lines).lstrip()


# --------------------------------------------------------------------------
# Flat-with-timestamp formatter — for time-oriented tools
# --------------------------------------------------------------------------


def _format_flat_with_timestamp(
    api: "PyAppleBooks",
    annotations,
    *,
    empty_message: str = "No annotations.",
) -> str:
    """Render annotations as a flat, chronologically-oriented list.

    Format per row::

        YYYY-MM-DD [id] text — chapter · Book Title

    The book title appears per-row (not as a group header) because
    time-oriented tools tend to jump between books, and Claude needs
    the book context inline. Chapter resolution still uses
    :func:`_chapter_title_map` — cached across rows so we only parse
    each EPUB once per call.
    """
    annotations = list(annotations)
    if not annotations:
        return empty_message

    chapter_map_cache: dict = {}

    def ch_map_for(book_id):
        if book_id not in chapter_map_cache:
            chapter_map_cache[book_id] = _chapter_title_map(api, book_id)
        return chapter_map_cache[book_id]

    lines: list = []
    for anno in annotations:
        book = getattr(anno, "book", None)
        ch_map = ch_map_for(book.id) if book else {}
        row = _format_lean_row(anno, ch_map)
        book_suffix = f" · {book.title}" if book else " · (book no longer in library)"
        lines.append(f"{_iso_date(anno)} {row}{book_suffix}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Lookups that need the apple_books instance
# --------------------------------------------------------------------------


def _chapter_title_map(api: "PyAppleBooks", book_id: int) -> dict:
    """Return ``{chapter_id: chapter_title}`` for a book, or ``{}`` when
    the book isn't readable (DRM, not downloaded, not an EPUB).
    """
    try:
        content = api.get_book_content(book_id)
    except (BookNotDownloadedError, DRMProtectedError, AppleBooksError):
        return {}
    try:
        return {c.id: c.title for c in content.list_chapters()}
    except AppleBooksError:
        return {}


def _build_current_reading_section(api: "PyAppleBooks", book) -> str:
    """Return a compact 'you left off on…' metadata block, or '' if we
    can't resolve the user's reading position (DRM, not downloaded, no
    bookmark).

    Lean-by-design: emits only the chapter's title, order, and id —
    never the chapter text. If Claude wants the text, it calls
    ``get_chapter_content(book_id, chapter_id)`` on demand. This keeps
    the attached resource small so it doesn't dominate the context
    window.

    Two-tier resolution:

    1. Preferred: :meth:`PyAppleBooks.get_current_reading_chapter`
       gives a full :class:`Chapter` entry when the CFI's chapter_id
       matches a ToC item.
    2. Fallback: many EPUBs store the reading-position CFI against a
       manifest item id that the ToC doesn't list (sub-section, or a
       re-numbered spine entry). The chapter is still fetchable via
       :meth:`get_chapter_content` — we just don't have a human
       title. Peek at the raw Location and emit the id alone so
       Claude can still hand it off.
    """
    # Tier 1: try the clean ToC-resolved chapter first.
    try:
        chapter = api.get_current_reading_chapter(book.id)
    except BookNotDownloadedError:
        return (
            "\nCurrent chapter: not available — this book hasn't been "
            "downloaded to this Mac. Open it in Apple Books to sync."
        )
    except DRMProtectedError:
        return (
            "\nCurrent chapter: not readable — this is a DRM-protected "
            "Apple Books Store purchase; only imported EPUBs expose their "
            "chapter metadata."
        )
    except AppleBooksError as e:
        logger.warning("current reading chapter unavailable: %s", e)
        return ""

    if chapter is not None:
        try:
            content = api.get_book_content(book.id)
            total_chapters = len(content.list_chapters())
        except AppleBooksError as e:
            logger.warning("chapter count unavailable: %s", e)
            total_chapters = None

        position = (
            f"[{chapter.order}/{total_chapters}]" if total_chapters else f"[{chapter.order}]"
        )
        return (
            f"\nCurrent chapter: {position} {chapter.title}  "
            f'(use get_chapter_content({book.id}, "{chapter.id}") for the text)'
        )

    # Tier 2: ToC lookup yielded nothing. Peek at the raw position CFI —
    # sub-section ids still work with get_chapter_content even when the
    # ToC doesn't carry them.
    try:
        bookmark = api.get_current_reading_location(book.id)
    except AppleBooksError as e:
        logger.warning("current reading location unavailable: %s", e)
        return ""

    if (
        bookmark is not None
        and getattr(bookmark, "location", None)
        and bookmark.location.chapter_id
    ):
        cid = bookmark.location.chapter_id
        return (
            f"\nCurrent chapter id: {cid}  "
            f'(use get_chapter_content({book.id}, "{cid}") for the text)'
        )

    return ""
