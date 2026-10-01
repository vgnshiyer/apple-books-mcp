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

import html
import logging
import os
import re
import unicodedata
from datetime import date, datetime, time
from typing import Callable, NamedTuple, Optional, TYPE_CHECKING

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

# Rows per call for the list and search tools. Annotation rows are
# ~150 chars, so the default page is ~8k chars; book and collection
# rows are short enough to list a typical library in one page. No
# tool returns more than _MAX_PAGE rows per call.
_ANNOTATION_PAGE = 50
_BOOK_PAGE = 200
_MAX_PAGE = 500

# Hard cap on the characters one list or search call returns (~10k
# tokens). A page that would exceed it is cut on a row boundary, and
# the footer names the offset to resume from.
_OUTPUT_BUDGET = 40_000

# ``order_by`` values the annotation search tools accept, mapped to
# the library's ordering.
_ORDERS = {"newest": "-creation_date", "oldest": "creation_date"}


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


# --------------------------------------------------------------------------
# Book search (title or author)
# --------------------------------------------------------------------------

# A copy of py-apple-books 1.10's text fold (py_apple_books.text
# .fold_for_match, which its ``__search`` lookups apply to both sides in
# SQL), so a match in Python agrees with search_books_by_title's in the
# library. That module isn't public API, and 1.10 has no author search;
# tests/test_search_books.py checks the copy against the library's.
_FOLD_WHITESPACE = re.compile(r"\s+")
_FOLD_MAP: dict = {}
for _ch in "’‘‚‛′‵‹›ʼ＇":
    _FOLD_MAP[ord(_ch)] = "'"
for _ch in "“”„‟″‶«»＂":
    _FOLD_MAP[ord(_ch)] = '"'
for _ch in "‐‑‒–—―−﹘﹣－":
    _FOLD_MAP[ord(_ch)] = "-"
# Soft hyphen, zero-width space / non-joiner / joiner, word joiner, BOM.
for _cp in (0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
    _FOLD_MAP[_cp] = None
# Combining accents, split off by NFKD (Gödel -> godel).
for _cp in range(0x0300, 0x0370):
    _FOLD_MAP[_cp] = None
del _ch, _cp


def _fold(text) -> Optional[str]:
    """``text`` folded for matching: lower case (casefold), no accents,
    one kind of quote and dash, whitespace runs as one space. None stays
    None. Same rules as the library's fold, in the same order."""
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if text.isascii():
        return _FOLD_WHITESPACE.sub(" ", text).lower()
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
    text = unicodedata.normalize("NFKD", text.casefold()).casefold().translate(_FOLD_MAP)
    return _FOLD_WHITESPACE.sub(" ", unicodedata.normalize("NFC", text))


def _search_needle(query) -> Optional[str]:
    """``query`` folded, or None when it can match nothing: as in the
    library, a query whose visible characters all fold away (accents
    alone, zero-width characters) finds nothing rather than everything.
    An empty query matches every non-empty value."""
    needle = _fold(query)
    if needle is None or (str(query).strip() and not needle.strip()):
        return None
    return needle


def _book_matches(book, needle: str) -> bool:
    """Whether ``needle`` (from :func:`_search_needle`) is in the book's
    title or author, folded. A missing title or author matches nothing."""
    for value in (getattr(book, "title", None), getattr(book, "author", None)):
        folded = _fold(value)
        if folded is not None and needle in folded:
            return True
    return False


# --------------------------------------------------------------------------
# Apple Books deep links and removed books
# --------------------------------------------------------------------------

# An asset id as Books makes them (32 hex digits for an imported book,
# digits for a Store title). A link or a label is shown only for an id
# like that, so nothing odd from the database ends up in a URL.
_ASSET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LINK_PREFIX = "ibooks://assetid/"


def _deep_link(book) -> Optional[str]:
    """``ibooks://assetid/<asset id>`` (py-apple-books' ``Book.deep_link``),
    which opens the book in Books.app; None for no book, or an asset id
    that isn't plain."""
    asset_id = getattr(book, "asset_id", None)
    link = getattr(book, "deep_link", None)
    if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
        return None
    if link != f"{_LINK_PREFIX}{asset_id}":
        return None
    return link


def _removed_book(asset_id, *, count: Optional[int] = None, short: bool = False) -> str:
    """How output names a book that is no longer in the library (its
    annotations stay in Apple Books): by the first 8 characters of its
    asset id, the same in every tool.

    ``Removed book (asset 3F2A1B2C…)``; with ``count``, ``Removed book
    (asset 3F2A1B2C…, 3 highlights on this page)``; ``short``: ``removed
    book 3F2A1B2C…``, for the end of a row.
    """
    if isinstance(asset_id, str) and _ASSET_ID.fullmatch(asset_id):
        tag = asset_id if len(asset_id) <= 8 else f"{asset_id[:8]}…"
    else:
        tag = None
    if short:
        return f"removed book {tag}" if tag else "removed book"
    if tag:
        label = f"asset {tag}"
    else:
        # An id that isn't plain is not shown, as in links.
        label = "asset id not shown" if isinstance(asset_id, str) and asset_id else "no asset id"
    if count is not None:
        label += f", {count} highlight{'' if count == 1 else 's'} on this page"
    return f"Removed book ({label})"


# --------------------------------------------------------------------------
# Whether a book's text can be read (describe_book's "Readable text")
# --------------------------------------------------------------------------

# Worded like the errors the chapter tools give for each case.
_READABLE = "yes (EPUB)"
_PDF = "no (PDF; the chapter tools read EPUB books only)"
_OTHER_FORMAT = "no (not an EPUB book; the chapter tools read EPUB books only)"
_DRM = "no (DRM-protected; readable only in Apple Books)"
_IN_ICLOUD = "not downloaded (in iCloud only; open it in Books to download it)"
_STORE_TITLE = (
    "not downloaded (Apple Books Store title; Store purchases are usually "
    "DRM-protected)"
)
_NO_FILE = "not downloaded (open it in Books to download it)"
_MISSING = "no (the book's file is missing)"
_SERIES_ITEM = (
    "no (an Apple Books Store series item that isn't in your library; there "
    "is no book file)"
)

# st_flags bit macOS sets on a file or folder whose data is in iCloud
# only (SF_DATALESS).
_SF_DATALESS = 0x40000000


def _readable_text(api: "PyAppleBooks", book) -> Optional[str]:
    """Whether the chapter tools can read ``book``, and if not why, as
    describe_book's ``Readable text:`` value; None if that can't be told
    (the line is left out then: this never raises).

    Reads no book content and no byte of a file that may be in iCloud
    only, since reading an evicted file makes macOS download it: it
    goes by the library's records first (Books' iCloud-only state, the
    file path, the Store id), then file metadata (``lstat``), and only
    for a book on disk asks the library to open it, which checks the
    download state from metadata and only then looks for DRM, as the
    chapter tools do. Never claims a book is an audiobook: py-apple-books
    1.10 has no reliable signal for one.
    """
    try:
        return _readability(api, book)
    except Exception as e:
        logger.debug("readability unavailable: %s", e)
        return None


def _readability(api: "PyAppleBooks", book) -> str:
    # Books' own record first, before the file system is touched.
    if getattr(book, "is_cloud_only", False) is True:
        return _IN_ICLOUD
    series_item = getattr(book, "is_store_series_item", False) is True
    store = bool(getattr(book, "store_id", None))
    path = getattr(book, "path", None)
    if not path:
        if series_item:
            return _SERIES_ITEM
        return _STORE_TITLE if store else _NO_FILE
    path = os.fspath(path)
    try:
        flags = getattr(os.lstat(path), "st_flags", 0)
    except FileNotFoundError:
        # Older iCloud Drive leaves a ".<name>.icloud" stub in place of
        # an evicted file.
        folder, name = os.path.split(path.rstrip("/"))
        if os.path.lexists(os.path.join(folder, f".{name}.icloud")):
            return _IN_ICLOUD
        if series_item:
            return _SERIES_ITEM
        return _STORE_TITLE if store else _MISSING
    if flags & _SF_DATALESS:
        return _IN_ICLOUD
    if not os.path.exists(path):  # a link to nothing
        return _MISSING
    # The library's DRM check reads META-INF/encryption.xml inside an
    # EPUB bundle; a bundle iCloud evicted in part can have that file
    # in iCloud only while the folder itself is not.
    try:
        inner = os.lstat(os.path.join(path, "META-INF", "encryption.xml"))
    except OSError:
        pass
    else:
        if getattr(inner, "st_flags", 0) & _SF_DATALESS:
            return _IN_ICLOUD
    try:
        content = api.get_book_content(book.id)
    except DRMProtectedError:
        return _DRM
    except BookNotDownloadedError:
        return _IN_ICLOUD
    if content.is_epub:
        return _READABLE
    if content.is_pdf:
        return _PDF
    return _OTHER_FORMAT


# --------------------------------------------------------------------------
# Untrusted book text
# --------------------------------------------------------------------------

# Code points looked at for hidden characters and look-alikes: the
# basic and supplementary multilingual planes, and the tags and
# variation selectors. The scan takes a few milliseconds at import.
_CODE_POINTS = (*range(0x20000), *range(0xE0000, 0xE1000))

# Characters a crafted book could hide inside a ``</book_text>`` tag:
# whitespace, controls, format characters (zero-width spaces and
# joiners, soft hyphen, direction marks, Unicode tags), combining marks
# (variation selectors included), the blank fillers (Hangul, the empty
# Braille pattern), and the unassigned code points beside the
# specials and in the tags block. The translated copy has a zero-width
# space for each.
_HIDDEN_CATEGORIES = {"Cc", "Cf", "Mn", "Me", "Zs", "Zl", "Zp"}
_BLANKS = {
    0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0, *range(0xFFF0, 0xFFF9),
    *range(0xE0000, 0xE1000),
}
_HIDDEN = "\u200b"

# The tag's characters, and what can stand between ``book`` and ``text``.
_TAG_CHARS = "</booktext_.-"


def _tag_lookalikes() -> dict:
    """A ``str.translate`` table that maps each hidden character to
    :data:`_HIDDEN`, and each look-alike of the tag's characters to the
    character: capitals, every character whose compatibility form
    (NFKC) is one (fullwidth, small, mathematical, circled, superscript
    forms), and the angle brackets, slashes, dashes, low lines, small
    capitals and Cyrillic or Greek letters that pass for one. One
    character maps to one, so a match in the translated copy is at the
    same index in the text."""
    table = {}
    for c in _CODE_POINTS:
        char = chr(c)
        if c in _BLANKS or unicodedata.category(char) in _HIDDEN_CATEGORIES:
            table[c] = _HIDDEN
            continue
        folded = unicodedata.normalize("NFKC", char).lower()
        if folded != char and len(folded) == 1 and folded in _TAG_CHARS:
            table[c] = folded
    for lookalikes, char in (
        ("\u2039\u2329\u3008\u27e8\u1438\u276c\u276e\u2770\u02c2", "<"),
        ("\u2215\u2044\u29f8\u2571\u27cb", "/"),
        ("\u2010\u2011\u2012\u2013\u2014\u2212", "-"),
        ("\u2017\u02cd", "_"),
        ("\u0412\u0392\u042c\u044c\u0299", "b"),
        ("\u043e\u041e\u03bf\u039f\u1d0f", "o"),
        ("\u043a\u041a\u03ba\u039a\u1d0b", "k"),
        ("\u0442\u0422\u03c4\u03a4\u1d1b", "t"),
        ("\u0435\u0415\u0395\u1d07", "e"),
        ("\u0445\u0425\u03c7\u03a7\u00d7", "x"),
    ):
        table.update(dict.fromkeys(map(ord, lookalikes), char))
    return table


_TAG_LOOKALIKES = _tag_lookalikes()

# The ``<`` of a ``<book_text`` or ``</book_text`` tag in the translated
# copy (so in any case, with look-alikes), with hidden characters
# anywhere in it and any run of ``_``, ``.`` or ``-`` (or none) between
# the words. Each run of hidden characters is followed by a character
# that can't be one, so the match is linear in the length of the text.
_GAP = f"{_HIDDEN}*"
_BOOK_TEXT_TAG = re.compile(
    f"<(?={_GAP}(?:/{_GAP})?{_GAP.join('book')}{_GAP}"
    f"(?:[_.-]{_GAP})*{_GAP.join('text')})"
)

# Characters that would break an attribute value's line.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _attribute(value) -> str:
    """``value`` for a quoted attribute: HTML-escaped, with control
    characters and line breaks as character references."""
    escaped = html.escape(str(value), quote=True)
    return _CONTROL.sub(lambda m: f"&#{ord(m[0])};", escaped)


def _book_text(text: str, **attrs) -> str:
    """Wrap text taken from a book (chapter text, a passage, the ToC,
    the store description) in one ``<book_text ...>`` envelope. The
    server instructions tell the model that what's inside is untrusted
    content, never instructions.

    A ``book_text`` tag inside ``text``, or a look-alike of one, gets
    its ``<`` escaped, so a crafted book can't close the envelope
    early. ``attrs`` (None values skipped) become quoted attributes
    that say where the text came from.
    """
    attributes = "".join(
        f' {name}="{_attribute(value)}"'
        for name, value in attrs.items()
        if value is not None
    )
    pieces, end = [], 0
    for tag in _BOOK_TEXT_TAG.finditer(text.translate(_TAG_LOOKALIKES)):
        pieces += [text[end:tag.start()], "&lt;"]
        end = tag.start() + 1
    body = "".join(pieces) + text[end:]
    return f"<book_text{attributes}>\n{body}\n</book_text>"


# A chapter id comes from the book (its manifest or ToC ids, which
# Apple Books copies into each CFI), and a crafted book can put quotes,
# spaces and whole sentences in one. Outside an envelope, an id or a
# CFI is shown only when it is a plain token like these.
_PLAIN_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_PLAIN_CFI = re.compile(r"epubcfi\([A-Za-z0-9_.:/!,;=~^@\[\]-]{1,512}\)")


def _plain_id(value) -> Optional[str]:
    """``value`` if it is a plain id, safe to show and quote as is;
    else None."""
    if isinstance(value, str) and _PLAIN_ID.fullmatch(value):
        return value
    return None


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

    An id that isn't plain (:func:`_plain_id`) is left out.
    """
    text = _lean_annotation_text(annotation)
    chapter_id = (
        annotation.location.chapter_id
        if annotation.location and annotation.location.chapter_id
        else None
    )
    chapter_title = chapter_map.get(chapter_id, "") if chapter_id else ""
    shown_id = _plain_id(chapter_id)
    aid = annotation.id

    parts = [f"[{aid}]"]
    if text:
        parts.append(text)
    if chapter_title:
        parts.append(f"— {chapter_title}")
    if shown_id:
        parts.append(f"(ch={shown_id})")

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


def _created_at(annotation) -> str:
    """``YYYY-MM-DD HH:MM`` rendering of the annotation's creation time
    (the server's local time zone), or the literal string ``"?"`` when
    missing. The minutes let Claude cluster rows into reading sessions.
    """
    created = getattr(annotation, "creation_date", None)
    return created.strftime("%Y-%m-%d %H:%M") if created else "?"


def _reading_order_key(annotation) -> tuple:
    """Sort key that puts a book's annotations in reading order.

    Uses the CFI's document order (:attr:`Location.sort_key`: the spine
    position, then the steps and character offset within the chapter),
    so it needs no ToC and works for books that can't be opened (DRM,
    iCloud-only). Creation time breaks ties; annotations without a CFI
    go last.
    """
    location = getattr(annotation, "location", None)
    key = location.sort_key if location else None
    created = getattr(annotation, "creation_date", None) or datetime.min
    return (key is None, key or (), created)


def _local_zone_label() -> str:
    """The server's local time zone as ``PDT, UTC-07:00`` — the zone
    every date and time in the tool output is in."""
    local = datetime.now().astimezone()
    offset = local.strftime("%z") or "+0000"
    return f"{local.tzname()}, UTC{offset[:3]}:{offset[3:5]}"


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
    chapter_maps: Optional[dict] = None,
) -> str:
    """Render a list of annotations grouped by their originating book.

    Each book gets a header line (``Book Title (Author):`` by default)
    followed by one :func:`_format_lean_row` per annotation. Annotations
    whose book is no longer in the library (no book has their
    ``asset_id``) are grouped per asset id after the library's books, in
    the order they first appear, each under a :func:`_removed_book`
    header that counts its rows on this page.

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
    :param chapter_maps: Optional ``{book_id: chapter_map}`` cache,
        filled as books are seen. Pass the same dict when rendering the
        same annotations more than once (see :func:`_fit_budget`) so
        each EPUB is parsed once per call.
    """
    annotations = list(annotations)
    if not annotations:
        return empty_message

    row_formatter = row_formatter or _format_lean_row
    if chapter_maps is None:
        chapter_maps = {}

    from collections import defaultdict

    by_book: dict = defaultdict(list)
    # Annotations of removed books, by asset id ("" and None as one).
    removed: dict = defaultdict(list)
    for anno in annotations:
        book = getattr(anno, "book", None)
        if book is None:
            removed[getattr(anno, "asset_id", None) or None].append(anno)
        else:
            by_book[book.id].append((anno, book))

    for book_id in by_book:
        if book_id not in chapter_maps:
            chapter_maps[book_id] = _chapter_title_map(api, book_id)

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

    for asset_id, annos in removed.items():
        lines.append(f"\n{_removed_book(asset_id, count=len(annos))}:")
        for anno in annos:
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
    chapter_maps: Optional[dict] = None,
) -> str:
    """Render annotations as a flat, chronologically-oriented list.

    Format per row::

        YYYY-MM-DD HH:MM [id] text — chapter · Book Title

    Times are in the server's local time zone. The book title appears
    per-row (not as a group header) because time-oriented tools tend to
    jump between books, and Claude needs the book context inline.
    Chapter resolution still uses :func:`_chapter_title_map` — cached
    across rows (and across calls sharing ``chapter_maps``) so we only
    parse each EPUB once per call.
    """
    annotations = list(annotations)
    if not annotations:
        return empty_message

    chapter_map_cache: dict = {} if chapter_maps is None else chapter_maps

    def ch_map_for(book_id):
        if book_id not in chapter_map_cache:
            chapter_map_cache[book_id] = _chapter_title_map(api, book_id)
        return chapter_map_cache[book_id]

    lines: list = []
    for anno in annotations:
        book = getattr(anno, "book", None)
        ch_map = ch_map_for(book.id) if book else {}
        row = _format_lean_row(anno, ch_map)
        if book:
            book_suffix = f" · {book.title}"
        else:
            book_suffix = f" · {_removed_book(getattr(anno, 'asset_id', None), short=True)}"
        lines.append(f"{_created_at(anno)} {row}{book_suffix}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Paging — shared by the list and search tools
# --------------------------------------------------------------------------


class _PageArgs(NamedTuple):
    """``limit`` and ``offset`` after clamping, plus one note per value
    that had to be changed (shown under the output)."""

    limit: int
    offset: int
    notes: list


class _Page(NamedTuple):
    """One page of results. ``total`` is None when counting every match
    would cost a second full query (text search); ``more`` says whether
    rows exist past this page either way."""

    items: list
    offset: int
    total: Optional[int]
    more: bool


def _page_args(limit, offset, *, default: int) -> _PageArgs:
    """Clamp ``limit`` to 1..:data:`_MAX_PAGE` (None means ``default``)
    and ``offset`` to >= 0. Out-of-range values are clamped rather than
    rejected, with a note, so a call never silently returns everything.
    """
    notes: list = []
    if limit is None:
        limit = default
    elif limit < 1:
        notes.append(f"(limit={limit} is below the minimum; used limit=1.)")
        limit = 1
    elif limit > _MAX_PAGE:
        notes.append(f"(limit={limit} is above the maximum; used limit={_MAX_PAGE}.)")
        limit = _MAX_PAGE
    if offset is None:
        offset = 0
    elif offset < 0:
        notes.append(f"(offset={offset} is negative; used offset=0.)")
        offset = 0
    return _PageArgs(limit, offset, notes)


def _query_page(results, limit: int, offset: int) -> _Page:
    """Rows ``[offset, offset + limit)`` of a library query
    (a ``ModelIterable``): the total comes from ``count()`` (a
    ``COUNT(*)``) and the rows from a slice (a ``LIMIT``/``OFFSET``
    query), so nothing past the page is loaded.
    """
    total = results.count()
    items = list(results[offset:offset + limit]) if offset < total else []
    return _Page(items, offset, total, offset + len(items) < total)


def _list_page(rows: list, limit: int, offset: int) -> _Page:
    """Rows ``[offset, offset + limit)`` of an already-loaded list."""
    items = rows[offset:offset + limit]
    return _Page(items, offset, len(rows), offset + len(items) < len(rows))


def _probe_page(rows: list, limit: int, offset: int) -> _Page:
    """A page from a query run with ``limit + 1`` rows at ``offset``:
    the extra row, if any, only says there are more. Used where the
    library has no count (text search returns a list)."""
    return _Page(list(rows[:limit]), offset, None, len(rows) > limit)


def _order_by(order: str) -> Optional[str]:
    """Map a tool's ``order_by`` (``newest``/``oldest``) to the
    library's; None for anything else."""
    return _ORDERS.get((order or "").strip().lower())


def _fit_budget(
    render: Callable[[list], str], items: list, budget: int = _OUTPUT_BUDGET
) -> tuple:
    """Render the longest leading run of ``items`` whose output fits in
    ``budget`` characters — always at least one row. Returns
    ``(text, rows_rendered)``; the caller resumes at the row after.

    ``render`` is called O(log n) times when the page doesn't fit, so
    it should reuse anything expensive (chapter maps) across calls.
    """
    text = render(items)
    if len(text) <= budget or len(items) <= 1:
        return text, len(items)
    # Binary search for the largest prefix that fits (output grows
    # with every row added).
    best = None
    low, high = 1, len(items) - 1
    while low <= high:
        mid = (low + high) // 2
        candidate = render(items[:mid])
        if len(candidate) <= budget:
            best = (candidate, mid)
            low = mid + 1
        else:
            high = mid - 1
    return best or (render(items[:1]), 1)


def _page_footer(
    noun: str, offset: int, shown: int, total: Optional[int], more: bool, capped: bool
) -> str:
    """``Showing 51–100 of 1,117 annotations. Next page: offset=100.``

    Empty when the first page holds everything, so short results read
    exactly as before.
    """
    if not more and not offset:
        return ""
    end = offset + shown
    if total is not None:
        span = f"Showing {offset + 1:,}–{end:,} of {total:,} {noun}"
    elif more:
        span = f"Showing {offset + 1:,}–{end:,} of more than {end:,} {noun}"
    else:
        span = f"Showing {offset + 1:,}–{end:,} of {end:,} {noun}"
    if capped:
        span += f" (output capped at {_OUTPUT_BUDGET:,} chars)"
    if more:
        return f"{span}. Next page: offset={end}."
    return f"{span} (end)."


def _render_page(
    page: _Page,
    render: Callable[[list], str],
    *,
    noun: str,
    empty_message: str,
    notes: list = (),
    header: str = "",
) -> str:
    """Render one page of a list or search tool: optional header line,
    the rows (cut to :data:`_OUTPUT_BUDGET`), then any clamp notes and
    the paging footer.
    """
    if page.items:
        body, shown = _fit_budget(render, page.items)
        capped = shown < len(page.items)
        footer = _page_footer(
            noun, page.offset, shown, page.total, page.more or capped, capped
        )
        parts = [header, body]
    elif page.offset and page.total != 0:
        past = f" (there are {page.total:,})" if page.total is not None else ""
        footer = ""
        parts = [f"No {noun} at offset {page.offset}{past}. Pass a smaller offset."]
    else:
        footer = ""
        parts = [empty_message]
    tail = "\n".join(line for line in (*notes, footer) if line)
    text = "\n".join(part for part in parts if part)
    return f"{text}\n\n{tail}" if tail else text


# --------------------------------------------------------------------------
# Date-range arguments
# --------------------------------------------------------------------------

_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_date_arg(name: str, value, *, end_of_day: bool = False) -> Optional[datetime]:
    """Parse a date-range bound given as ``YYYY-MM-DD`` or an ISO
    datetime into a naive datetime in the server's local time zone —
    the zone the library's annotation dates are in.

    A date-only value means the start of that day, or with
    ``end_of_day`` (the ``before`` bound) its last instant, so that
    "on or before" covers the whole day. A datetime with a zone is
    converted to local time. Raises ValueError with a message meant
    for the caller on anything else.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max if end_of_day else time.min)
    else:
        text = str(value).strip()
        try:
            if _DATE_ONLY.fullmatch(text):
                day = date.fromisoformat(text)
                parsed = datetime.combine(day, time.max if end_of_day else time.min)
            else:
                # Python 3.10's fromisoformat doesn't accept a "Z" suffix.
                parsed = datetime.fromisoformat(re.sub(r"[zZ]$", "+00:00", text))
        except ValueError:
            raise ValueError(
                f"{name}={text!r} is not a date. Use YYYY-MM-DD, or "
                f"YYYY-MM-DDTHH:MM for a time of day."
            ) from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _date_range_label(after: Optional[datetime], before: Optional[datetime]) -> str:
    """``from 2025-01-01 00:00 through 2025-01-31 23:59`` (either end
    may be open)."""
    fmt = "%Y-%m-%d %H:%M"
    if after and before:
        return f"from {after.strftime(fmt)} through {before.strftime(fmt)}"
    if after:
        return f"from {after.strftime(fmt)} on"
    if before:
        return f"through {before.strftime(fmt)}"
    return "at any time"


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

    When the book's chapters can't be read, it says why in
    describe_book's words (:func:`_readable_text`).
    """
    try:
        resolution = _resolve_current_chapter(api, book)
    except AppleBooksError as e:
        readable = _readable_text(api, book)
        if readable is None or readable == _READABLE:
            logger.warning("current reading chapter unavailable: %s", e)
            return ""
        return f"\nCurrent chapter: not available\n  Readable text: {readable}"

    if resolution is None:
        return ""

    call_hint = _chapter_call_hint(book.id, resolution)

    if resolution.source == "toc":
        position = (
            f"[{resolution.order}/{resolution.total_chapters}]"
            if resolution.total_chapters
            else f"[{resolution.order}]"
        )
        return f"\nCurrent chapter: {position} {resolution.title}  {call_hint}"

    if resolution.source == "cfi":
        return f"\n{_untitled_chapter_line(book.id, resolution)}"

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
    return f"\n{_untitled_chapter_line(book.id, resolution)}\n  {label}"


def _chapter_call_hint(book_id, resolution: _ChapterResolution) -> str:
    """Where to get the resolved chapter's text: get_chapter_content
    with its id, or with its order when the id isn't plain
    (:func:`_plain_id`); without either, the chapter list."""
    target = _plain_id(resolution.chapter_id)
    if target is None and resolution.order is not None:
        target = str(resolution.order)
    if target is None:
        return f"(list_book_chapters({book_id}) lists the chapters)"
    return f'(use get_chapter_content({book_id}, "{target}") for the text)'


def _untitled_chapter_line(book_id, resolution: _ChapterResolution) -> str:
    """The current chapter by its id, when there is no title to show."""
    call_hint = _chapter_call_hint(book_id, resolution)
    chapter_id = _plain_id(resolution.chapter_id)
    if chapter_id is None:
        return (
            "Current chapter: untitled, and its id isn't a plain name, so it "
            f"isn't shown  {call_hint}"
        )
    return f"Current chapter id: {chapter_id}  {call_hint}"
