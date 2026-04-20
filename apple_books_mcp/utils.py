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
from datetime import datetime
from typing import NamedTuple, Optional, TYPE_CHECKING

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


class _ChapterResolution(NamedTuple):
    """Result of resolving a book's 'current chapter' with its
    provenance tier. Consumers render the same data differently
    depending on ``source`` so we don't claim the bookmark told us
    something the bookmark didn't actually contain.

    ``chapter_id`` is always usable with ``get_chapter_content``;
    ``title`` and ``order`` may be None when the CFI points at a
    manifest item the ToC doesn't carry.

    ``source`` values:

    * ``"toc"`` — Apple Books' reading-position bookmark CFI resolves
      cleanly to a ToC entry. Highest-fidelity answer.
    * ``"cfi"`` — The bookmark has a CFI but its ``chapter_id`` isn't
      in the ToC (sub-section or re-numbered spine entry). Still
      actionable with ``get_chapter_content``.
    * ``"recent_highlight"`` — Bookmark exists but has no CFI (Apple
      Books sometimes writes empty tombstones). We proxy with the
      user's most recent highlight in this book. Clearly labeled
      downstream so the caller doesn't mistake it for an authoritative
      position.
    """

    chapter_id: str
    title: Optional[str]
    order: Optional[int]
    total_chapters: Optional[int]
    source: str


def _most_recent_highlight_chapter(book) -> Optional[tuple[str, object]]:
    """Return (chapter_id, annotation) for the most recent annotation
    in this book whose CFI carries a chapter_id. None if the book has
    no annotations, or no annotations with a chapter_id.
    """
    try:
        annos = [a for a in book.annotations if a.location and a.location.chapter_id]
    except Exception as e:
        logger.warning("book.annotations unavailable: %s", e)
        return None
    if not annos:
        return None
    annos.sort(
        key=lambda a: getattr(a, "creation_date", None) or datetime.min,
        reverse=True,
    )
    top = annos[0]
    return top.location.chapter_id, top


def _resolve_current_chapter(
    api: "PyAppleBooks", book
) -> Optional[_ChapterResolution]:
    """Three-tier resolution of 'where is the user in this book?'.

    Raises the book-wide errors (BookNotDownloadedError,
    DRMProtectedError) — callers render those as the user-facing
    "not available" / "DRM-protected" messages.

    Returns None if no tier succeeds.
    """
    # Tier 1: ToC-resolved chapter.
    chapter = api.get_current_reading_chapter(book.id)
    if chapter is not None:
        try:
            content = api.get_book_content(book.id)
            total = len(content.list_chapters())
        except AppleBooksError as e:
            logger.warning("chapter count unavailable: %s", e)
            total = None
        return _ChapterResolution(
            chapter_id=chapter.id,
            title=chapter.title,
            order=chapter.order,
            total_chapters=total,
            source="toc",
        )

    # Tier 2: raw CFI from reading-position bookmark.
    try:
        bookmark = api.get_current_reading_location(book.id)
    except AppleBooksError as e:
        logger.warning("current reading location unavailable: %s", e)
        bookmark = None

    if (
        bookmark is not None
        and getattr(bookmark, "location", None)
        and bookmark.location.chapter_id
    ):
        return _ChapterResolution(
            chapter_id=bookmark.location.chapter_id,
            title=None,
            order=None,
            total_chapters=None,
            source="cfi",
        )

    # Tier 3: most-recent-highlight proxy. Only fires when Apple Books
    # wrote a tombstone bookmark (no CFI) OR wrote no bookmark at all.
    # Honest labeling downstream makes clear this is a proxy, not the
    # bookmark itself.
    proxy = _most_recent_highlight_chapter(book)
    if proxy is not None:
        cid, anno = proxy
        # Enrich with a ToC title if the highlight's CFI happens to
        # point at a ToC-known chapter (common case).
        title = None
        try:
            content = api.get_book_content(book.id)
            for c in content.list_chapters():
                if c.id == cid:
                    title = c.title
                    break
        except AppleBooksError:
            pass
        return _ChapterResolution(
            chapter_id=cid,
            title=title,
            order=None,
            total_chapters=None,
            source="recent_highlight",
        )

    return None


def _build_current_reading_section(api: "PyAppleBooks", book) -> str:
    """Return a compact 'you left off on…' metadata block, or '' if no
    tier yields an answer (DRM, not downloaded, no bookmark and no
    highlights).

    Lean-by-design: emits only the chapter's title and id — never the
    chapter text. If Claude wants the text, it calls
    ``get_chapter_content(book_id, chapter_id)`` on demand. This keeps
    the attached resource small so it doesn't dominate the context
    window.
    """
    try:
        resolution = _resolve_current_chapter(api, book)
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

    if resolution is None:
        return ""

    call_hint = f'(use get_chapter_content({book.id}, "{resolution.chapter_id}") for the text)'

    if resolution.source == "toc":
        position = (
            f"[{resolution.order}/{resolution.total_chapters}]"
            if resolution.total_chapters
            else f"[{resolution.order}]"
        )
        return f"\nCurrent chapter: {position} {resolution.title}  {call_hint}"

    if resolution.source == "cfi":
        return f"\nCurrent chapter id: {resolution.chapter_id}  {call_hint}"

    # source == "recent_highlight" — proxy, not the bookmark itself.
    # Label clearly so the caller knows the provenance.
    label = (
        f"(inferred from your most recent highlight — Apple Books hasn't "
        f"recorded a CFI on the reading bookmark yet)"
    )
    if resolution.title:
        return (
            f"\nCurrent chapter: {resolution.title}  {call_hint}"
            f"\n  {label}"
        )
    return (
        f"\nCurrent chapter id: {resolution.chapter_id}  {call_hint}"
        f"\n  {label}"
    )
