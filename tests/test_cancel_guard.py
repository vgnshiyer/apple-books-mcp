"""Cancelling a request must not shut down the stdio server (mcp 1.x).

The subprocess tests start the real server (plus a deliberately slow
``sleep`` tool) over stdio and cancel requests the way Claude Desktop and
Claude Code do. To confirm they still catch the crash, run them with
ABM_TEST_WITHOUT_CANCEL_GUARD=1, which starts the server unpatched.
"""
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
from mcp.shared.session import RequestResponder

from apple_books_mcp import _cancel_guard

REPO_ROOT = Path(__file__).resolve().parent.parent

_LAUNCHER = """
import os, time
import apple_books_mcp
from apple_books_mcp import _cancel_guard
from apple_books_mcp.server import mcp

@mcp.tool()
def sleep(seconds: float, size: int = 4) -> str:
    time.sleep(seconds)
    return "x" * size

if os.environ.get("ABM_TEST_WITHOUT_CANCEL_GUARD"):
    _cancel_guard.install = lambda: False
apple_books_mcp.main(args=[])
"""


@pytest.fixture
def unpatched_responder(monkeypatch):
    # Restores the original __exit__ after the test.
    monkeypatch.setattr(RequestResponder, "__exit__", RequestResponder.__exit__)


def _responder(on_complete):
    return RequestResponder(
        request_id=1,
        request_meta=None,
        request=None,
        session=None,
        on_complete=on_complete,
    )


def test_install_keeps_a_cancelled_response_inside_the_request(unpatched_responder):
    assert _cancel_guard.install()
    completed = []

    async def respond_then_get_cancelled():
        responder = _responder(completed.append)
        with responder:
            # respond() has started sending when the host's cancel arrives.
            responder._completed = True
            responder._cancel_scope.cancel()
            await anyio.sleep(1)
        return "handled"

    assert anyio.run(respond_then_get_cancelled) == "handled"
    assert len(completed) == 1


def test_install_is_idempotent(unpatched_responder):
    assert _cancel_guard.install()
    patched = RequestResponder.__exit__
    assert _cancel_guard.install()
    assert RequestResponder.__exit__ is patched


def test_install_skips_other_mcp_major_versions(unpatched_responder, monkeypatch):
    original = RequestResponder.__exit__
    monkeypatch.setattr(_cancel_guard, "_mcp_version", lambda: "2.2.0")
    assert not _cancel_guard.install()
    assert RequestResponder.__exit__ is original


def test_install_skips_changed_internals(unpatched_responder, monkeypatch):
    def upstream_exit(self, exc_type, exc_val, exc_tb):
        return self._scope.__exit__(exc_type, exc_val, exc_tb)

    monkeypatch.setattr(RequestResponder, "__exit__", upstream_exit)
    assert not _cancel_guard.install()
    assert RequestResponder.__exit__ is upstream_exit


class _StdioServer:
    """The server in a subprocess, driven with raw JSON-RPC over stdio."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _LAUNCHER],
            cwd=REPO_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.messages = queue.Queue()
        self.responses = {}
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.messages.put(json.loads(line))
        self.messages.put(None)

    def _send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        return self.next_id

    def call_sleep(self, seconds, size=4):
        return self.request(
            "tools/call",
            {"name": "sleep", "arguments": {"seconds": seconds, "size": size}},
        )

    def cancel(self, request_id):
        self._send({
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": request_id, "reason": "user interrupt"},
        })

    def wait_for(self, request_id, timeout):
        deadline = time.monotonic() + timeout
        while request_id not in self.responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                return None
            if message is None:  # stdout closed: the server is gone
                self.messages.put(None)
                return None
            if "id" in message:
                self.responses.setdefault(message["id"], message)
        return self.responses[request_id]

    def initialize(self):
        request_id = self.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "cancel-test", "version": "0"},
        })
        assert self.wait_for(request_id, timeout=60), self.stderr()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def stderr(self):
        if self.proc.poll() is None:
            return "(server still running)"
        return self.proc.stderr.read()[-3000:]

    def close(self):
        self.proc.kill()
        self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()


@pytest.fixture
def server():
    server = _StdioServer()
    try:
        server.initialize()
        yield server
    finally:
        server.close()


def _assert_still_serving(server):
    assert server.wait_for(server.request("ping"), timeout=5), server.stderr()
    tools = server.wait_for(server.request("tools/list"), timeout=5)
    assert tools and tools["result"]["tools"], server.stderr()
    assert server.proc.poll() is None


def test_cancel_call_queued_behind_slow_call(server):
    slow = server.call_sleep(1.0)
    time.sleep(0.3)
    queued = server.call_sleep(0.2)
    time.sleep(0.05)
    server.cancel(queued)

    assert server.wait_for(slow, timeout=10), server.stderr()
    _assert_still_serving(server)


def test_cancel_two_parallel_calls(server):
    first = server.call_sleep(1.0)
    second = server.call_sleep(0.2)
    time.sleep(0.5)
    server.cancel(first)
    server.cancel(second)

    _assert_still_serving(server)


def test_cancel_twice_in_a_row(server):
    # Interrupt a slow call (sync tools ignore it), ask again, interrupt
    # again. The large first reply keeps stdout busy, so the retry's reply
    # is still waiting to be sent when its cancel is processed.
    first = server.call_sleep(1.0, size=200_000)
    time.sleep(0.3)
    server.cancel(first)
    time.sleep(0.2)
    retry = server.call_sleep(0.5)
    time.sleep(0.2)
    server.cancel(retry)

    _assert_still_serving(server)
