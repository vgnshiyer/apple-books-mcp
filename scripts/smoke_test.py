"""Start the server over stdio and check it answers initialize + tools/list.

Usage: python scripts/smoke_test.py <command> [args...]
  e.g. python scripts/smoke_test.py uvx --from dist/apple_books_mcp-0.8.1-py3-none-any.whl apple-books-mcp

Catches packaging breaks that unit tests can't see, because unit tests
run against uv.lock while users get whatever the published metadata
resolves to (e.g. mcp 2.x removing mcp.server.fastmcp).
"""
import json
import subprocess
import sys
import threading

TIMEOUT_SECONDS = 120


def main() -> int:
    command = sys.argv[1:]
    if not command:
        print(__doc__, file=sys.stderr)
        return 2

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test", "version": "0"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    proc = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    # readline() below blocks, so a hung server is killed by a watchdog.
    watchdog = threading.Timer(TIMEOUT_SECONDS, proc.kill)
    watchdog.start()
    responses = {}
    try:
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            if "id" not in message:
                continue
            line = proc.stdout.readline()
            if not line:
                break
            response = json.loads(line)
            responses[response.get("id")] = response
    finally:
        proc.stdin.close()
        proc.wait()
        watchdog.cancel()

    server_info = responses.get(1, {}).get("result", {}).get("serverInfo")
    tools = responses.get(2, {}).get("result", {}).get("tools", [])
    if not server_info or not tools:
        print("Smoke test FAILED: server did not answer initialize/tools/list.", file=sys.stderr)
        print(proc.stderr.read()[-4000:], file=sys.stderr)
        return 1

    print(f"Smoke test passed: {server_info} exposed {len(tools)} tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
