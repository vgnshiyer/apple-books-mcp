"""``apple-books-mcp --doctor`` against synthetic libraries
(py_apple_books.testing.FixtureLibrary) in a throwaway HOME."""
import hashlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
import py_apple_books.write_safety as write_safety
from mcp.shared.session import RequestResponder
from py_apple_books import PyAppleBooks
from py_apple_books.db.client import _reset_default_library
from py_apple_books.exceptions import WriteError
from py_apple_books.testing import FixtureLibrary, seed_demo

from apple_books_mcp import _runtime, doctor

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTAINER = "~/Library/Containers/com.apple.iBooksX/Data/Documents"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty HOME, read through a fresh default library."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in doctor._LIBRARY_ENV + (
            doctor.ENV_ENABLE_WRITES, doctor._TIMEOUT_ENV, _runtime.ENV_THREADS):
        monkeypatch.delenv(name, raising=False)
    # The doctor installs the cancel guard to report the threads in use.
    monkeypatch.setattr(RequestResponder, "__exit__", RequestResponder.__exit__)
    monkeypatch.setattr(write_safety, "books_is_running", lambda: False)
    monkeypatch.setattr(doctor, "_macos_version", lambda: "15.6")  # also on CI's Linux
    # Set from HOME when py-apple-books is imported.
    monkeypatch.setattr(write_safety, "BACKUP_DIR", home / ".py_apple_books" / "backups")
    _reset_default_library()
    yield home
    _reset_default_library()


@pytest.fixture
def demo(home, tmp_path):
    lib = FixtureLibrary.create(home)
    seed_demo(lib, tmp_path / "work")
    return lib


def _doctor():
    out = io.StringIO()
    status = doctor.run(out)
    return status, out.getvalue()


def _snapshot(root: Path) -> dict:
    """Every file under ``root`` and the hash of its content."""
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_empty_home(home):
    status, out = _doctor()
    assert status == 1
    assert f"FAIL  no Apple Books library store found in {CONTAINER}/BKLibrary" in out
    assert "Open Apple Books once on this Mac" in out
    assert out.rstrip().endswith("1 problem found.")
    assert str(home) not in out
    assert not any(home.iterdir())


def test_healthy_library(demo, home):
    before = _snapshot(home)
    status, out = _doctor()
    assert status == 0, out
    assert _snapshot(home) == before  # nothing written or created

    assert f"ok    library store: {CONTAINER}/BKLibrary/{demo.library_path.name}" in out
    assert f"ok    annotation store: {CONTAINER}/AEAnnotation/{demo.annotation_path.name}" in out
    api = PyAppleBooks(data_dir=demo.data_dir)
    try:
        books = sum(api.count_books_by_status().values())
        annotations = api.count_annotations()
        collections = api.list_collections().count()
    finally:
        api.close()
    assert (f"ok    read {books} books, {annotations} highlights and notes, "
            f"{collections} collections in ") in out
    assert "ok    macOS 15.6" in out
    assert "the server loads (" in out
    assert "note  tool calls run in worker threads, up to 8 at a time (APPLE_BOOKS_MCP_THREADS)" in out
    assert "collection writes are off (add --enable-writes" in out
    assert "queries stop after 30 s (APPLE_BOOKS_QUERY_TIMEOUT)" in out
    assert "Apple Books is not running" in out
    assert out.rstrip().endswith("No problems found.")
    assert str(home) not in out

    # Counts only: no title, author or highlight text.
    words = demo.execute("library", "SELECT ZTITLE, ZAUTHOR FROM ZBKLIBRARYASSET")
    words += demo.execute(
        "annotations",
        "SELECT ZANNOTATIONSELECTEDTEXT, ZANNOTATIONNOTE FROM ZAEANNOTATION",
    )
    for text in {w for row in words for w in row if w and len(w) > 3}:
        assert text not in out


def test_missing_annotation_store(home):
    lib = FixtureLibrary.create(home)
    lib.populate(books=3, annotations_per_book=1)
    lib.annotation_path.unlink()

    status, out = _doctor()
    assert status == 0, out
    assert "warn  no annotation store: books and collections work" in out
    assert "Annotation." not in out
    assert "ok    read 3 books, 0 collections in " in out
    assert out.rstrip().endswith("No problems found (1 warning).")


def test_store_folder_without_a_store(home):
    (home / "Library/Containers/com.apple.iBooksX/Data/Documents/BKLibrary").mkdir(parents=True)
    status, out = _doctor()
    assert status == 1
    assert f"FAIL  no Apple Books library store found in {CONTAINER}/BKLibrary" in out


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_access_denied(home):
    lib = FixtureLibrary.create(home)
    folder = lib.library_path.parent
    folder.chmod(0)
    try:
        status, out = _doctor()
    finally:
        folder.chmod(0o755)
    assert status == 1
    assert f"FAIL  macOS denied access to the Apple Books library ({CONTAINER}/BKLibrary)" in out
    assert '"access data from other apps"' in out
    assert "tccutil reset SystemPolicyAppData" in out
    assert "Full Disk Access" in out
    assert str(home) not in out


def test_writes_enabled_while_books_runs(demo, home, monkeypatch):
    monkeypatch.setenv(doctor.ENV_ENABLE_WRITES, "1")
    monkeypatch.setattr(write_safety, "books_is_running", lambda: True)
    before = _snapshot(home)
    status, out = _doctor()
    assert status == 0, out
    assert _snapshot(home) == before
    assert "collection writes are on" in out
    assert "ok    backups before each write go to ~/.py_apple_books/backups" in out
    assert "warn  Apple Books is running: collection writes are refused until you quit it" in out


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_writes_enabled_without_room_for_backups(demo, home, monkeypatch):
    monkeypatch.setenv(doctor.ENV_ENABLE_WRITES, "1")
    home.chmod(0o555)
    try:
        status, out = _doctor()
    finally:
        home.chmod(0o755)
    assert status == 0, out
    assert "warn  can't create backups in ~/.py_apple_books/backups: collection writes will fail" in out
    assert not (home / ".py_apple_books").exists()


def test_tools_on_the_event_loop(demo, monkeypatch):
    monkeypatch.setenv(_runtime.ENV_THREADS, "0")
    status, out = _doctor()
    assert status == 0, out
    assert "note  tool calls run one at a time on the event loop" in out


def test_not_macos(demo, monkeypatch):
    monkeypatch.setattr(doctor, "_macos_version", lambda: None)
    status, out = _doctor()
    assert status == 0, out
    assert "warn  not macOS (" in out
    assert out.rstrip().endswith("No problems found (1 warning).")


def test_books_state_unknown(demo, monkeypatch):
    def unknown():
        raise WriteError("pgrep is missing")

    monkeypatch.setattr(write_safety, "books_is_running", unknown)
    status, out = _doctor()
    assert status == 0, out
    assert "note  can't tell whether Apple Books is running: pgrep is missing" in out


def test_library_location_override(home, tmp_path, monkeypatch):
    elsewhere = FixtureLibrary.create(home / "copy")
    monkeypatch.setenv("APPLE_BOOKS_DATA_DIR", str(elsewhere.data_dir))
    _reset_default_library()
    status, out = _doctor()
    assert status == 0, out
    assert f"note  APPLE_BOOKS_DATA_DIR=~/copy/{CONTAINER[2:]}: read instead" in out
    assert f"library store: ~/copy/{CONTAINER[2:]}/BKLibrary/" in out


def test_short_path(home):
    assert doctor.short_path(home) == "~"
    assert doctor.short_path(home / "a" / "b") == "~/a/b"
    assert doctor.short_path(f"{home}x/a") == f"{home}x/a"
    assert doctor.short_path("/elsewhere/a") == "/elsewhere/a"


@pytest.mark.parametrize("populated, status", [(False, 1), (True, 0)])
def test_cli_exit_status(tmp_path, populated, status):
    if populated:
        FixtureLibrary.create(tmp_path).populate(books=2, annotations_per_book=1)
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=str(REPO_ROOT))
    for name in doctor._LIBRARY_ENV + (doctor.ENV_ENABLE_WRITES,):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-m", "apple_books_mcp", "--doctor"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == status, result.stdout + result.stderr
    assert result.stdout.startswith("apple-books-mcp ")
    assert ("No problems found" in result.stdout) == populated
