"""get_annotations_by_date_range: inclusive ``before``, ordering, input
checks and the time-zone label (F05, G4.3), and the weekly_digest
prompt that drives it.
"""
import re
import time
from datetime import date, datetime, timedelta

import pytest
from py_apple_books import PyAppleBooks
from py_apple_books.testing import FixtureLibrary

from apple_books_mcp import server
from apple_books_mcp.server import get_annotations_by_date_range, weekly_digest


def _local(*args) -> datetime:
    """An aware datetime at this local wall-clock time (the fixture
    takes naive datetimes as UTC)."""
    return datetime(*args).astimezone()


@pytest.fixture
def local_zone(monkeypatch):
    """Switch the process time zone; restored afterwards."""
    def switch(zone):
        monkeypatch.setenv("TZ", zone)
        time.tzset()
    yield switch
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = FixtureLibrary.create(tmp_path)
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    yield lib
    api.close()


def _ids(text):
    return [int(i) for i in re.findall(r"^\S+ \S+ \[(\d+)\]", text, re.M)]


def _seed_january(library):
    """Highlights on Jan 30 (09:00), Jan 31 (08:15 and 23:30, local)
    and Feb 1 (00:30), created out of order."""
    book = library.add_book("Dated Book")
    return {
        "jan31_late": library.add_annotation(book, "late", created=_local(2025, 1, 31, 23, 30)),
        "jan30": library.add_annotation(book, "early", created=_local(2025, 1, 30, 9, 0)),
        "feb1": library.add_annotation(book, "next", created=_local(2025, 2, 1, 0, 30)),
        "jan31_morning": library.add_annotation(book, "morning", created=_local(2025, 1, 31, 8, 15)),
    }


def test_before_includes_the_whole_day(library):
    """after == before returns that day (it returned nothing)."""
    ids = _seed_january(library)
    text = get_annotations_by_date_range(after="2025-01-31", before="2025-01-31").text
    assert _ids(text) == [ids["jan31_late"], ids["jan31_morning"]]
    assert "2025-01-31 23:30 [" in text


def test_rows_are_newest_first_unless_asked(library):
    ids = _seed_january(library)
    text = get_annotations_by_date_range(after="2025-01-01").text
    assert _ids(text) == [ids["feb1"], ids["jan31_late"], ids["jan31_morning"], ids["jan30"]]
    text = get_annotations_by_date_range(after="2025-01-01", order_by="oldest").text
    assert _ids(text) == [ids["jan30"], ids["jan31_morning"], ids["jan31_late"], ids["feb1"]]
    assert "oldest first:" in text.splitlines()[0]


def test_limit_keeps_the_newest_in_range(library):
    ids = _seed_january(library)
    text = get_annotations_by_date_range(before="2025-01-31", limit=1).text
    assert _ids(text) == [ids["jan31_late"]]
    assert text.endswith("Showing 1–1 of 3 annotations. Next page: offset=1.")


def test_iso_datetimes_are_accepted(library):
    ids = _seed_january(library)
    text = get_annotations_by_date_range(
        after="2025-01-31T08:00", before="2025-01-31T09:00:00").text
    assert _ids(text) == [ids["jan31_morning"]]


@pytest.mark.parametrize("after", ["2025/01/01", "last week", "2025-13-01"])
def test_bad_dates_get_a_clear_message(library, after):
    text = get_annotations_by_date_range(after=after).text
    assert text == (
        f"after={after!r} is not a date. Use YYYY-MM-DD, or "
        "YYYY-MM-DDTHH:MM for a time of day."
    )


def test_inverted_range_says_so(library):
    _seed_january(library)
    text = get_annotations_by_date_range(after="2025-02-01", before="2025-01-01").text
    assert text == (
        "after (2025-02-01 00:00) is later than before (2025-01-01 23:59), "
        "so no annotation can match. Swap them."
    )


def test_header_names_the_range_and_zone(library, local_zone):
    local_zone("America/Los_Angeles")
    _seed_january(library)
    text = get_annotations_by_date_range(after="2025-01-31", before="2025-01-31").text
    header = text.splitlines()[0]
    assert header.startswith(
        "Annotations created from 2025-01-31 00:00 through 2025-01-31 23:59, local time (P")
    assert re.search(r"\(P[DS]T, UTC-0[78]:00\), newest first:$", header)
    text = get_annotations_by_date_range(after="2030-01-01").text
    assert re.fullmatch(
        r"No annotations created from 2030-01-01 00:00 on, local time \(P[DS]T, UTC-0[78]:00\)\.",
        text)


@pytest.mark.parametrize("zone", ["America/Los_Angeles", "UTC", "Asia/Kolkata"])
def test_days_follow_the_local_zone(tmp_path, monkeypatch, local_zone, zone):
    """The same local day returns the same highlights in any zone: an
    annotation at 23:30 local stays on its day."""
    local_zone(zone)
    lib = FixtureLibrary.create(tmp_path)
    api = PyAppleBooks(data_dir=lib.data_dir)
    monkeypatch.setattr(server, "apple_books", api)
    try:
        ids = _seed_january(lib)
        text = get_annotations_by_date_range(after="2025-01-31", before="2025-01-31").text
        assert _ids(text) == [ids["jan31_late"], ids["jan31_morning"]]
    finally:
        api.close()


def test_weekly_digest_names_the_date_and_paging():
    text = weekly_digest(days=7)
    since = (date.today() - timedelta(days=7)).isoformat()
    assert f'get_annotations_by_date_range(after="{since}", limit=200)' in text
    assert "Next page: offset=N" in text
    assert "30 minutes" in text
