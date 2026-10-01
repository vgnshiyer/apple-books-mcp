import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import apple_books_mcp

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(args, cwd=REPO_ROOT, env=None, **kwargs):
    env = env or dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, *args], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=60, **kwargs,
    )


def _handshake(flags):
    """Start `python -m apple_books_mcp`, return (serverInfo, tools, stderr)."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "apple_books_mcp", *flags],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    watchdog = threading.Timer(60, proc.kill)
    watchdog.start()
    try:
        for message in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "cli-test", "version": "0"},
            }},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ):
            proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        responses = [json.loads(proc.stdout.readline()) for _ in range(2)]
    finally:
        proc.kill()
        _, stderr = proc.communicate()
        watchdog.cancel()
    return (
        responses[0]["result"]["serverInfo"],
        responses[1]["result"]["tools"],
        stderr,
    )


@pytest.mark.skipif(not (REPO_ROOT / "server.json").exists(), reason="not in the sdist")
def test_version_matches_pyproject_and_server_json():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    assert re.search(r'^version = "(.+)"$', pyproject, re.M)[1] == apple_books_mcp.__version__
    server_json = json.loads((REPO_ROOT / "server.json").read_text())
    assert server_json["version"] == apple_books_mcp.__version__
    assert server_json["packages"][0]["version"] == apple_books_mcp.__version__


@pytest.mark.parametrize("value, enabled", [
    ("1", True), ("true", True), (" TRUE ", True), ("yes", True),
    ("", False), ("0", False), ("false", False), ("no", False), ("on", False),
])
def test_writes_env_values(monkeypatch, value, enabled):
    """--enable-writes sets "1"; the Desktop extension's checkbox sends
    "true" or "false"."""
    monkeypatch.setenv(apple_books_mcp.ENV_ENABLE_WRITES, value)
    assert apple_books_mcp._writes_from_env() is enabled


def test_version_flag():
    result = _run(["-m", "apple_books_mcp", "--version"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"apple-books-mcp {apple_books_mcp.__version__} (mcp ")
    assert "py-apple-books " in result.stdout


def test_version_and_help_work_when_the_server_cannot_import():
    # e.g. an incompatible mcp release: --version should still report it.
    code = (
        "import sys; sys.modules['mcp.server.fastmcp'] = None; "
        "from apple_books_mcp import main; main([sys.argv[1]])"
    )
    result = _run(["-c", code, "--version"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"apple-books-mcp {apple_books_mcp.__version__} ")
    result = _run(["-c", code, "--help"])
    assert result.returncode == 0, result.stderr
    assert "--enable-writes" in result.stdout
    assert "--doctor" in result.stdout


def test_doctor_reports_a_server_that_cannot_import(tmp_path):
    code = (
        "import sys; sys.modules['mcp.server.fastmcp'] = None; "
        "from apple_books_mcp import main; main([sys.argv[1]])"
    )
    result = _run(["-c", code, "--doctor"], env=dict(
        os.environ, HOME=str(tmp_path), PYTHONPATH=str(REPO_ROOT)))
    assert result.returncode == 1, result.stderr
    assert result.stdout.startswith(f"apple-books-mcp {apple_books_mcp.__version__} ")
    assert "FAIL  the server can't load: ModuleNotFoundError" in result.stdout
    assert "mcp.server.fastmcp" in result.stdout


def test_server_py_in_cwd_is_not_imported(tmp_path):
    (tmp_path / "server.py").write_text("raise SystemExit('wrong server.py imported')\n")
    result = _run(["-m", "apple_books_mcp", "--version"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("apple-books-mcp ")


@pytest.mark.parametrize("flags, shown, hidden", [
    ([], set(), {"INFO", "DEBUG"}),
    (["-v"], {"INFO"}, {"DEBUG"}),
    (["-vv"], {"INFO", "DEBUG"}, set()),
])
def test_server_reports_version_and_honours_verbosity(flags, shown, hidden):
    server_info, tools, stderr = _handshake(flags)
    assert server_info == {"name": "apple-books", "version": apple_books_mcp.__version__}
    assert tools

    logged = set(re.findall(r"^\S+ \S+ ([A-Z]+) \S+: ", stderr, re.M))
    assert shown <= logged, stderr
    assert not hidden & logged, stderr
    if shown:
        assert f"apple-books-mcp {apple_books_mcp.__version__} (mcp " in stderr
        assert re.search(r"\d+ tools run in worker threads, up to \d+ at a time", stderr)
    else:
        # FastMCP logs every request at INFO unless its setup is overridden.
        assert "Processing request" not in stderr, stderr
