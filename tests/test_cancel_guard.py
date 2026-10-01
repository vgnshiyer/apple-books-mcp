"""Cancelling a request must not shut down the stdio server (mcp 1.x).

The subprocess tests start the real server (plus a deliberately slow
``sleep`` tool) over stdio and cancel requests the way Claude Desktop and
Claude Code do, with tools on the event loop (APPLE_BOOKS_MCP_THREADS=0,
where the crash was found) and in worker threads (the default). To
confirm they still catch the crash, run them with
ABM_TEST_WITHOUT_CANCEL_GUARD=1, which starts the server unpatched.
"""
import json
import os
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
    # Restores the original methods after the test.
    for name in ("__exit__", "respond"):
        monkeypatch.setattr(RequestResponder, name, getattr(RequestResponder, name))


def _responder(on_complete, session=None):
    return RequestResponder(
        request_id=1,
        request_meta=None,
        request=None,
        session=session,
        on_complete=on_complete,
    )


class _Session:
    """Records the responses a RequestResponder sends."""

    def __init__(self):
        self.sent = []

    async def _send_response(self, request_id, response):
        self.sent.append(response)


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


def test_install_drops_the_response_to_a_request_cancel_answered(unpatched_responder):
    # A tool's thread finishes just as the host's cancel arrives: anyio
    # doesn't interrupt the handler, which goes on to respond() after
    # cancel() has sent "Request cancelled". Unpatched, respond()'s
    # assertion fails and takes the server down.
    assert _cancel_guard.install()
    session = _Session()

    async def cancel_then_respond():
        responder = _responder(lambda _: None, session)
        with responder:
            await responder.cancel()
            await responder.respond("the tool's result")
        return "handled"

    assert anyio.run(cancel_then_respond) == "handled"
    assert [r.message for r in session.sent] == ["Request cancelled"]

    async def respond():
        responder = _responder(lambda _: None, session)
        with responder:
            await responder.respond("the tool's result")
        # Responding twice is still the bug it always was.
        with pytest.raises(AssertionError):
            with responder:
                await responder.respond("again")

    anyio.run(respond)
    assert session.sent[1:] == ["the tool's result"]


def test_install_is_idempotent(unpatched_responder):
    assert _cancel_guard.install()
    patched = RequestResponder.__exit__, RequestResponder.respond
    assert _cancel_guard.install()
    assert (RequestResponder.__exit__, RequestResponder.respond) == patched


def test_can_install_patches_nothing(unpatched_responder):
    original = RequestResponder.__exit__, RequestResponder.respond
    assert _cancel_guard.can_install()
    assert (RequestResponder.__exit__, RequestResponder.respond) == original


def test_install_skips_other_mcp_major_versions(unpatched_responder, monkeypatch):
    original = RequestResponder.__exit__, RequestResponder.respond
    monkeypatch.setattr(_cancel_guard, "_mcp_version", lambda: "2.2.0")
    assert not _cancel_guard.can_install()
    assert not _cancel_guard.install()
    assert (RequestResponder.__exit__, RequestResponder.respond) == original


def test_install_skips_changed_internals(unpatched_responder, monkeypatch):
    def upstream_exit(self, exc_type, exc_val, exc_tb):
        return self._scope.__exit__(exc_type, exc_val, exc_tb)

    monkeypatch.setattr(RequestResponder, "__exit__", upstream_exit)
    assert not _cancel_guard.can_install()
    assert not _cancel_guard.install()
    assert RequestResponder.__exit__ is upstream_exit


def test_install_skips_a_changed_respond(unpatched_responder, monkeypatch):
    # The other patch still applies, but install() reports the guard as
    # incomplete, so tools stay on the event loop.
    async def upstream_respond(self, response):
        await self._session._send_response(request_id=self.request_id, response=response)

    monkeypatch.setattr(RequestResponder, "respond", upstream_respond)
    assert not _cancel_guard.can_install()
    assert not _cancel_guard.install()
    assert RequestResponder.respond is upstream_respond
    assert getattr(RequestResponder.__exit__, "_apple_books_mcp_patch", False)


class _StdioServer:
    """The server in a subprocess, driven with raw JSON-RPC over stdio."""

    def __init__(self, env=None):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _LAUNCHER],
            cwd=REPO_ROOT,
            env=dict(os.environ, **(env or {})),
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


@pytest.fixture(params=["event-loop", "threads"])
def server(request):
    threads = "0" if request.param == "event-loop" else ""
    server = _StdioServer(env={"APPLE_BOOKS_MCP_THREADS": threads})
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
    # Interrupt a slow call (tools on the event loop ignore it), ask
    # again, interrupt again. The large first reply keeps stdout busy, so
    # the retry's reply is still waiting to be sent when its cancel is
    # processed.
    first = server.call_sleep(1.0, size=200_000)
    time.sleep(0.3)
    server.cancel(first)
    time.sleep(0.2)
    retry = server.call_sleep(0.5)
    time.sleep(0.2)
    server.cancel(retry)

    _assert_still_serving(server)


@pytest.mark.parametrize("server", ["threads"], indirect=True)
def test_cancel_as_the_thread_finishes(server):
    # The cancel races the end of a short call: sometimes the thread is
    # still running, sometimes its result is already on its way, and
    # sometimes cancel() has answered after the thread finished but
    # before the handler resumed (without the respond() patch, that one
    # took the server down a few times in 30).
    for _ in range(30):
        call = server.call_sleep(0.01, size=200_000)
        time.sleep(0.01)
        server.cancel(call)
        assert server.wait_for(call, timeout=5), server.stderr()
        assert server.wait_for(server.request("ping"), timeout=5), server.stderr()
    _assert_still_serving(server)


@pytest.mark.parametrize("server", ["threads"], indirect=True)
def test_cancel_running_call_answers_at_once(server):
    # A call running in a worker thread is answered as soon as it is
    # cancelled; its thread finishes in the background.
    slow = server.call_sleep(3.0)
    time.sleep(0.3)
    start = time.monotonic()
    server.cancel(slow)

    response = server.wait_for(slow, timeout=1.5)
    assert response, server.stderr()
    assert response["error"]["message"] == "Request cancelled"
    assert time.monotonic() - start < 1.5
    _assert_still_serving(server)
    retry = server.call_sleep(0.1)
    assert server.wait_for(retry, timeout=5)["result"]["content"][0]["text"] == "xxxx"
