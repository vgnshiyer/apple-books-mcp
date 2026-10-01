"""describe_book's "Readable text" line (F44, F63): whether the
chapter tools can read a book, told from the library's records and file
metadata without reading a byte of a file that may be in iCloud only;
and the currently-reading resource saying why not in the same words.
"""
import asyncio
import os
from types import SimpleNamespace

import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary, write_epub
from py_apple_books.testing.fixture import STORE_SERIES

from apple_books_mcp import server, utils
from apple_books_mcp.server import describe_book

CHAPTERS = [
    ("c1", "One", ["First chapter text."]),
    ("c2", "Two", ["Second chapter text. " * 20]),
]


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = FixtureLibrary.create(tmp_path / "home")
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield lib
    api.close()


@pytest.fixture
def epub(tmp_path):
    return write_epub(tmp_path / "Readable.epub", "Readable", CHAPTERS)


def _position(library, book, chapter="c2", step=6):
    """Apple Books' reading-position row, in ``chapter``."""
    library.add_annotation(book, None, kind="reading_position",
                           location=f"epubcfi(/6/{step}[{chapter}]!/4/2/1:0)")


def _readable(book_id):
    lines = [line for line in describe_book(book_id).text.splitlines()
             if line.startswith("  Readable text: ")]
    return lines[0][len("  Readable text: "):] if lines else None


def _no_file_access(monkeypatch, book_path):
    """Fail the test if anything looks at ``book_path`` or a file in it
    (stat, exists, open), or the library opens a book."""
    folder = str(book_path)

    def guard(call):
        def guarded(path, *args, **kwargs):
            if str(os.fspath(path)).startswith(folder):
                raise AssertionError(f"touched {path}")
            return call(path, *args, **kwargs)
        return guarded

    for module, name in ((os, "lstat"), (os, "stat"), (os, "open"),
                         (os.path, "exists"), (os.path, "lexists")):
        monkeypatch.setattr(module, name, guard(getattr(module, name)))

    def refuse(*args, **kwargs):
        raise AssertionError("opened the book")

    monkeypatch.setattr(server.apple_books, "get_book_content", refuse)


def test_readable_epub(library, epub):
    book = library.add_book("Readable", path=epub)
    assert _readable(book["id"]) == "yes (EPUB)"


def test_pdf(library, tmp_path):
    pdf = tmp_path / "Paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 5000)
    book = library.add_book("Paper", path=pdf, content_type=3)
    assert _readable(book["id"]) == "no (PDF; the chapter tools read EPUB books only)"


def test_other_format(library, tmp_path):
    zipped = tmp_path / "Zipped.epub"
    zipped.write_bytes(b"PK" + b"x" * 5000)
    book = library.add_book("Zipped", path=zipped)
    assert _readable(book["id"]) == (
        "no (not an EPUB book; the chapter tools read EPUB books only)")


def test_drm(library, tmp_path):
    epub = write_epub(tmp_path / "Locked.epub", "Locked", CHAPTERS)
    (epub / "META-INF" / "sinf.xml").write_text("<sinf/>")
    book = library.add_book("Locked", path=epub, raw={"ZSTOREID": "1234567890"})
    assert _readable(book["id"]) == "no (DRM-protected; readable only in Apple Books)"


def test_icloud_only_is_told_without_the_file_system(library, epub, monkeypatch):
    """Books' iCloud-only state (ZSTATE 3) is checked first, before any
    file is looked at: reading an evicted file downloads it."""
    book = library.add_book("Evicted", path=epub, state=3)
    _no_file_access(monkeypatch, epub)
    assert _readable(book["id"]) == (
        "not downloaded (in iCloud only; open it in Books to download it)")


def test_no_file(library, monkeypatch):
    book = library.add_book("Never Downloaded")
    _no_file_access(monkeypatch, "/nonexistent")
    assert _readable(book["id"]) == "not downloaded (open it in Books to download it)"


def test_store_title_without_a_file(library, tmp_path):
    no_path = library.add_book("Store Book", raw={"ZSTOREID": "1234567890"})
    missing = library.add_book("Store Book 2", path=tmp_path / "gone.epub",
                               raw={"ZSTOREID": "1234567891"})
    for book in (no_path, missing):
        assert _readable(book["id"]) == (
            "not downloaded (Apple Books Store title; Store purchases are usually "
            "DRM-protected)")


def test_missing_file(library, tmp_path):
    book = library.add_book("Gone", path=tmp_path / "Gone.epub")
    assert _readable(book["id"]) == "no (the book's file is missing)"
    os.symlink(tmp_path / "nowhere.epub", tmp_path / "Dangling.epub")
    book = library.add_book("Dangling", path=tmp_path / "Dangling.epub")
    assert _readable(book["id"]) == "no (the book's file is missing)"


def test_icloud_placeholder_stub(library, tmp_path):
    """Older iCloud Drive keeps a ".<name>.icloud" stub for an evicted
    file: in iCloud, not missing."""
    (tmp_path / ".Stub.epub.icloud").write_bytes(b"")
    book = library.add_book("Stub", path=tmp_path / "Stub.epub")
    assert _readable(book["id"]) == (
        "not downloaded (in iCloud only; open it in Books to download it)")


def test_dataless_file_is_not_opened(library, tmp_path, monkeypatch):
    """A file macOS marks dataless (in iCloud only) is never opened: not
    even the library's download check runs."""
    pdf = tmp_path / "Evicted.pdf"
    pdf.write_bytes(b"%PDF")
    book = library.add_book("Evicted", path=pdf)
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if os.fspath(path) == str(pdf):
            return SimpleNamespace(st_flags=utils._SF_DATALESS, st_mode=st.st_mode)
        return st

    def refuse(*args, **kwargs):
        raise AssertionError("opened a dataless book")

    monkeypatch.setattr(utils.os, "lstat", lstat)
    monkeypatch.setattr(server.apple_books, "get_book_content", refuse)
    assert _readable(book["id"]) == (
        "not downloaded (in iCloud only; open it in Books to download it)")


def test_dataless_encryption_xml_is_not_read(library, tmp_path, monkeypatch):
    """A bundle evicted in part: the folder is local, but the
    encryption.xml the library's DRM check would read is in iCloud only."""
    epub = write_epub(tmp_path / "Partial.epub", "Partial", CHAPTERS)
    encryption = epub / "META-INF" / "encryption.xml"
    encryption.write_text("<encryption/>")
    book = library.add_book("Partial", path=epub)
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if os.fspath(path) == str(encryption):
            return SimpleNamespace(st_flags=utils._SF_DATALESS, st_mode=st.st_mode)
        return st

    def refuse(*args, **kwargs):
        raise AssertionError("opened a book with a dataless encryption.xml")

    monkeypatch.setattr(utils.os, "lstat", lstat)
    monkeypatch.setattr(server.apple_books, "get_book_content", refuse)
    assert _readable(book["id"]) == (
        "not downloaded (in iCloud only; open it in Books to download it)")


def test_store_series_item(library):
    book = library.add_book("Saga", content_type=5, data_source=STORE_SERIES, state=5)
    assert _readable(book["id"]) == (
        "no (an Apple Books Store series item that isn't in your library; there "
        "is no book file)")


def test_never_raises(library, epub, monkeypatch):
    book = library.add_book("Readable", path=epub)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(server.apple_books, "get_book_content", broken)
    text = describe_book(book["id"]).text
    assert "Readable text" not in text and f"Book id: {book['id']}" in text


def test_no_paths_in_output(library, tmp_path):
    book = library.add_book("Gone", path=tmp_path / "Gone.epub")
    text = describe_book(book["id"]).text
    assert str(tmp_path) not in text and "Gone.epub" not in text


def test_currently_reading_says_why_not(library, tmp_path):
    """The currently-reading resource explains an unreadable book in
    describe_book's words."""
    epub = write_epub(tmp_path / "Locked.epub", "Locked", CHAPTERS)
    (epub / "META-INF" / "sinf.xml").write_text("<sinf/>")
    book = library.add_book("Locked", path=epub, progress=0.4, last_opened=1000.0)
    _position(library, book)
    content = asyncio.run(server.mcp.read_resource("apple-books://currently-reading"))[0].content
    assert (
        "\nCurrent chapter: not available\n"
        "  Readable text: no (DRM-protected; readable only in Apple Books)\n"
    ) in content


def test_currently_reading_in_icloud(library, tmp_path):
    # Books records it as in iCloud only, and there is no local file.
    book = library.add_book("Evicted", path=tmp_path / "Evicted.epub", progress=0.4,
                            last_opened=1000.0, state=3)
    _position(library, book)
    assert (
        "\nCurrent chapter: not available\n"
        "  Readable text: not downloaded (in iCloud only; open it in Books to download it)\n"
    ) in server._currently_reading()
