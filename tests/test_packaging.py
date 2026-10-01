"""Release packaging (F20, F39, F51): one version everywhere, the Claude
Desktop extension (mcpb/manifest.json, scripts/build_mcpb.py), the
registry's server.json, the Dockerfile and the workflows' pinning.

Offline: nothing here runs npx or uv. CI's mcpb job builds the bundle
with the official packer (which validates the manifest) and installs it
the way Claude Desktop does.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from py_apple_books.db import client as library_client
from py_apple_books.testing import FixtureLibrary

import apple_books_mcp
from apple_books_mcp.server import mcp

ROOT = Path(__file__).resolve().parent.parent


def _load_build_mcpb():
    spec = importlib.util.spec_from_file_location("build_mcpb", ROOT / "scripts" / "build_mcpb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_mcpb = _load_build_mcpb()
TEMPLATE = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
SERVER_JSON = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

# The keys MCPB manifest 0.4 allows (its schema is strict).
MANIFEST_KEYS = {
    "$schema", "manifest_version", "name", "display_name", "version", "description",
    "long_description", "author", "repository", "homepage", "documentation", "support",
    "icon", "icons", "screenshots", "localization", "server", "tools", "tools_generated",
    "prompts", "prompts_generated", "keywords", "license", "privacy_policies",
    "compatibility", "user_config", "_meta",
}
USER_CONFIG_KEYS = {"type", "title", "description", "required", "default", "multiple",
                    "sensitive", "min", "max"}
WRITES_ENV = "APPLE_BOOKS_MCP_ENABLE_WRITES"


# -- versions ---------------------------------------------------------------

def test_one_version_everywhere():
    """F51: tag v0.3.2 shipped 0.3.1 metadata. Every version field
    agrees, and the release workflow checks them against the tag."""
    fields = build_mcpb.version_fields()
    assert {"pyproject.toml", "server.json version", "uv.lock"} <= set(fields)
    assert len(set(fields.values())) == 1, fields
    assert apple_books_mcp.__version__ in fields.values()


def _check_version(version):
    return subprocess.run(
        [sys.executable, "scripts/build_mcpb.py", "--check-version", version],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )


def test_release_tag_guard():
    version = build_mcpb.project_version()
    assert _check_version(version).returncode == 0
    wrong = _check_version("0.0.0")
    assert wrong.returncode == 1
    assert "pyproject.toml: " + version in wrong.stderr
    assert "server.json version: " + version in wrong.stderr


# -- Claude Desktop extension -------------------------------------------------

def _rendered():
    return build_mcpb.render_manifest(build_mcpb.project_version(), build_mcpb.server_tools())


def test_manifest_template():
    """F20: the uv server type, run as a module from the unpacked
    bundle with the locked dependencies; version and tools come from
    the build."""
    assert "version" not in TEMPLATE and "tools" not in TEMPLATE
    assert set(TEMPLATE) <= MANIFEST_KEYS
    assert TEMPLATE["manifest_version"] == "0.4"
    assert TEMPLATE["name"] == "apple-books-mcp"
    server = TEMPLATE["server"]
    assert server["type"] == "uv"
    assert (ROOT / server["entry_point"]).is_file()
    config = server["mcp_config"]
    assert config["command"] == "uv"
    args = config["args"]
    assert args[:3] == ["run", "--directory", "${__dirname}"]
    assert "--frozen" in args
    assert args[-3:] == ["python", "-m", "apple_books_mcp"]
    assert TEMPLATE["compatibility"]["platforms"] == ["darwin"]
    requires = re.search(r'^requires-python\s*=\s*"([^"]+)"',
                         (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    assert TEMPLATE["compatibility"]["runtimes"]["python"] == requires


def test_manifest_user_config():
    """Writes stay off unless the user ticks the box, and every option
    reaches the server."""
    options = TEMPLATE["user_config"]
    for option in options.values():
        assert set(option) <= USER_CONFIG_KEYS
        assert option["type"] in {"string", "number", "boolean", "directory", "file"}
        assert option["title"] and option["description"]
    writes = options["enable_writes"]
    assert writes["type"] == "boolean" and writes["default"] is False
    assert TEMPLATE["server"]["mcp_config"]["env"] == {WRITES_ENV: "${user_config.enable_writes}"}
    used = set(re.findall(r"\$\{user_config\.(\w+)\}", json.dumps(TEMPLATE["server"])))
    assert used == set(options)


def test_manifest_lists_every_tool():
    manifest = _rendered()
    assert set(manifest) <= MANIFEST_KEYS
    assert manifest["version"] == build_mcpb.project_version()
    registered = [t.name for t in asyncio.run(mcp.list_tools())]
    assert [t["name"] for t in manifest["tools"]] == registered
    for tool in manifest["tools"]:
        assert set(tool) == {"name", "description"}
        assert tool["description"] and "\n" not in tool["description"], tool
        assert len(tool["description"]) <= 200, tool


def test_bundle_contents(tmp_path):
    """The bundle holds what `uv sync` needs and nothing else: no
    .python-version (it would pin the Python uv downloads), tests, CI
    or caches."""
    staged = build_mcpb.stage(tmp_path / "bundle", _rendered())
    files = {p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()}
    package = {p.relative_to(ROOT).as_posix()
               for p in (ROOT / "apple_books_mcp").rglob("*.py")}
    assert files == {"manifest.json", "pyproject.toml", "uv.lock", "README.md", "LICENSE"} | package
    staged_manifest = json.loads((staged / "manifest.json").read_text(encoding="utf-8"))
    assert staged_manifest == _rendered()


def test_desktop_command():
    """What Claude Desktop starts: booleans arrive as "true"/"false"."""
    argv, env = build_mcpb.desktop_command(TEMPLATE, "/ext")
    assert argv == ["uv", "run", "--directory", "/ext", "--frozen", "--no-dev",
                    "python", "-m", "apple_books_mcp"]
    assert env == {WRITES_ENV: "false"}
    _, env = build_mcpb.desktop_command(TEMPLATE, "/ext", {"enable_writes": True})
    assert env == {WRITES_ENV: "true"}


def _call_write_tool(home, env_value):
    """Start the server with the extension's environment and call a
    write tool on a collection that doesn't exist; its text."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("APPLE_BOOKS_")}
    env.update(HOME=str(home), PYTHONPATH=str(ROOT), **{WRITES_ENV: env_value})
    proc = subprocess.Popen(
        [sys.executable, "-m", "apple_books_mcp"], cwd=ROOT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    watchdog = threading.Timer(60, proc.kill)
    watchdog.start()
    try:
        responses = []
        for message in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "packaging-test", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "delete_collection", "arguments": {"collection_id": 999999}}},
        ):
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            if "id" in message:
                responses.append(json.loads(proc.stdout.readline()))
    finally:
        watchdog.cancel()
        proc.kill()
        proc.communicate()
    result = responses[-1]["result"]
    return "".join(block.get("text", "") for block in result["content"])


def test_extension_writes_toggle(tmp_path):
    """F20: Claude Desktop passes the "Allow editing collections" box
    as "true"/"false"; ticked must enable the write tools, unticked
    must not. Only a missing collection is touched, in a synthetic
    library."""
    FixtureLibrary.create(tmp_path)
    disabled = re.compile(r"editing is disabled", re.I)
    _, off = build_mcpb.desktop_command(TEMPLATE, "/ext", {"enable_writes": False})
    assert disabled.search(_call_write_tool(tmp_path, off[WRITES_ENV]))
    _, on = build_mcpb.desktop_command(TEMPLATE, "/ext", {"enable_writes": True})
    assert not disabled.search(_call_write_tool(tmp_path, on[WRITES_ENV]))


# -- registry -------------------------------------------------------------------

# The registry's rule for GitHub-hosted MCPB packages.
GITHUB_RELEASE_ASSET = re.compile(
    r"^https://github\.com/[a-zA-Z0-9]([a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?/[a-zA-Z0-9._-]+"
    r"/releases/download/[^/]+/[^/]+$")


def test_server_json_metadata():
    """F20: what the registry and its clients need to install and
    configure the server without the README."""
    assert SERVER_JSON["$schema"].endswith("/2025-12-11/server.schema.json")
    assert 0 < len(SERVER_JSON["description"]) <= 100
    assert SERVER_JSON["title"] and SERVER_JSON["websiteUrl"].startswith("https://")
    assert SERVER_JSON["repository"]["url"] == "https://github.com/vgnshiyer/apple-books-mcp"
    (pypi,) = SERVER_JSON["packages"]  # the mcpb package is added at release
    assert pypi["registryType"] == "pypi" and pypi["identifier"] == "apple-books-mcp"
    assert pypi["runtimeHint"] == "uvx"
    assert pypi["transport"] == {"type": "stdio"}
    # --enable-writes is a bare flag, which the schema's named arguments
    # (always "--name value") can't express; the variable does the same.
    assert "packageArguments" not in pypi


def test_server_json_environment_variables_exist():
    """Every documented variable is optional and one the server or the
    library reads; writes are a boolean that defaults to off."""
    variables = {v["name"]: v for v in SERVER_JSON["packages"][0]["environmentVariables"]}
    source = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "apple_books_mcp").glob("*.py"))
    library = {library_client.ENV_DATA_DIR, library_client.ENV_QUERY_TIMEOUT}
    for name, variable in variables.items():
        assert name in source or name in library, name
        assert not variable.get("isRequired") and not variable.get("isSecret")
        assert variable["description"]
    assert variables[WRITES_ENV]["format"] == "boolean"
    assert variables[WRITES_ENV]["default"] == "false"


def test_release_server_json(tmp_path):
    version = build_mcpb.project_version()
    bundle = tmp_path / build_mcpb.bundle_name(version)
    bundle.write_bytes(b"not really a zip")
    released = build_mcpb.release_server_json(version, bundle)
    assert released["packages"][0] == SERVER_JSON["packages"][0]
    mcpb = released["packages"][1]
    assert mcpb["registryType"] == "mcpb" and "registryBaseUrl" not in mcpb
    assert GITHUB_RELEASE_ASSET.match(mcpb["identifier"])
    assert mcpb["identifier"].endswith(f"/releases/download/v{version}/apple-books-mcp-{version}.mcpb")
    assert mcpb["fileSha256"] == hashlib.sha256(b"not really a zip").hexdigest()
    assert {k: v for k, v in released.items() if k != "packages"} == \
        {k: v for k, v in SERVER_JSON.items() if k != "packages"}


# -- Docker and workflows ---------------------------------------------------------

def test_dockerfile_pinned_and_unprivileged():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    users = re.findall(r"^USER (\S+)$", dockerfile, re.M)
    assert users and users[-1] not in {"root", "0"}


@pytest.mark.parametrize("workflow", sorted((ROOT / ".github" / "workflows").glob("*.yml")),
                         ids=lambda p: p.name)
def test_actions_pinned_by_sha(workflow):
    """F39: a tag can be moved to other code; a commit can't."""
    uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", workflow.read_text(encoding="utf-8"), re.M)
    assert uses
    for action, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action
        assert re.match(r"\s*# v\d", comment), f"{action}: add a '# vX.Y.Z' comment"
