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
import zipfile
from pathlib import Path

import pytest
from py_apple_books.db import client as library_client
from py_apple_books.testing import FixtureLibrary

import apple_books_mcp
from apple_books_mcp.server import mcp

ROOT = Path(__file__).resolve().parent.parent

# The sdist ships tests/ but not what these tests check (scripts/,
# mcpb/, server.json, the Dockerfile, the workflows).
if (ROOT / "PKG-INFO").is_file():
    pytest.skip("release packaging files aren't in the sdist", allow_module_level=True)


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
    assert set(fields) == set(build_mcpb.VERSION_FIELDS)
    assert len(set(fields.values())) == 1, fields
    assert apple_books_mcp.__version__ in fields.values()


def _check_tag(tag):
    return subprocess.run(
        [sys.executable, "scripts/build_mcpb.py", "--check-tag", tag],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )


def test_release_tag_guard():
    version = build_mcpb.project_version()
    assert _check_tag("v" + version).returncode == 0
    wrong = _check_tag("v0.0.0")
    assert wrong.returncode == 1
    assert "pyproject.toml: " + version in wrong.stderr
    assert "server.json version: " + version in wrong.stderr
    # The bundle's release URL is .../download/v<version>/..., so the
    # tag must be exactly that (this repo has tags without the "v").
    for tag in (version, "V" + version, "v" + version + "-rc1"):
        assert _check_tag(tag).returncode == 1, tag


def test_release_tag_guard_missing_field(tmp_path):
    """A version field the guard can't find fails it, rather than
    quietly dropping out of the comparison."""
    for name in ("pyproject.toml", "server.json", "uv.lock", "apple_books_mcp/__init__.py"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    version = build_mcpb.project_version()
    assert build_mcpb.tag_problems("v" + version, tmp_path) == {}
    init = tmp_path / "apple_books_mcp" / "__init__.py"
    init.write_text(re.sub(r'^__version__ = .*$', '__version__ = metadata_version()',
                           init.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8")
    assert build_mcpb.tag_problems("v" + version, tmp_path) == \
        {"apple_books_mcp/__init__.py": "missing"}
    with pytest.raises(SystemExit):
        build_mcpb.project_version(tmp_path)


def test_release_tag_guard_every_package(tmp_path):
    """Every server.json package's version is checked, two of one type
    included."""
    for name in ("pyproject.toml", "server.json", "uv.lock", "apple_books_mcp/__init__.py"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    version = build_mcpb.project_version()
    server = json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))
    server["packages"].insert(0, {**server["packages"][0], "version": "0.0.1"})
    (tmp_path / "server.json").write_text(json.dumps(server), encoding="utf-8")
    assert build_mcpb.tag_problems("v" + version, tmp_path) == \
        {"server.json packages[0] (pypi)": "0.0.1"}


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
               for p in (ROOT / "apple_books_mcp").rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    assert files == {"manifest.json", "pyproject.toml", "uv.lock", "README.md", "LICENSE"} | package
    staged_manifest = json.loads((staged / "manifest.json").read_text(encoding="utf-8"))
    assert staged_manifest == _rendered()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (staged / "pyproject.toml").read_text(encoding="utf-8") == \
        build_mcpb.bundle_pyproject(pyproject)
    for name in ("uv.lock", "README.md", "LICENSE"):
        assert (staged / name).read_bytes() == (ROOT / name).read_bytes(), name


def test_bundle_pyproject_no_dev_group():
    """Claude Desktop installs the bundle with a plain `uv sync`, which
    installs the default groups too: the bundled pyproject.toml has none,
    and otherwise says what the repo's does (so uv.lock still matches)."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    bundled = build_mcpb.bundle_pyproject(pyproject)
    assert re.search(r"^\[tool\.uv\]\ndefault-groups = \[\]$", bundled, re.M)
    assert bundled.replace("\ndefault-groups = []", "", 1) == pyproject
    if sys.version_info >= (3, 11):
        import tomllib

        expected = tomllib.loads(pyproject)
        expected["tool"]["uv"]["default-groups"] = []
        assert tomllib.loads(bundled) == expected
    # Without a [tool.uv] table, one is added.
    assert build_mcpb.bundle_pyproject('[project]\nname = "x"\n') == \
        '[project]\nname = "x"\n\n[tool.uv]\ndefault-groups = []\n'
    with pytest.raises(SystemExit):
        build_mcpb.bundle_pyproject(bundled)


# Desktop's `uv sync` builds the project (an editable install) with the
# [build-system] backend, which uv resolves fresh from PyPI on every
# install unless uv.lock records build constraints for it: outside the
# lock and the audit, and a future hatchling could break bundles already
# published. pyproject.toml's [tool.uv] build-constraint-dependencies
# pins it.
def test_bundle_pins_build_backend(tmp_path):
    staged = build_mcpb.stage(tmp_path / "bundle", _rendered())
    pyproject = (staged / "pyproject.toml").read_text(encoding="utf-8")
    requirement = re.search(r'^requires\s*=\s*\[\s*"([^"]+)"', pyproject, re.M).group(1)
    backend = re.match(r"[\w.-]+", requirement).group(0)
    lock = (staged / "uv.lock").read_text(encoding="utf-8")
    manifest = re.search(r"^\[manifest\]$(.*?)(?=^\[)", lock, re.M | re.S)
    pinned = dict(re.findall(r'\{ name = "([^"]+)", specifier = "==([^"]+)" \}',
                             manifest.group(1) if manifest else ""))
    assert backend in pinned or "==" in requirement, (requirement, pinned)


def _zip(staged, bundle, extra=None):
    with zipfile.ZipFile(bundle, "w") as archive:
        for p in sorted(staged.rglob("*")):
            if p.is_file():
                data = p.read_bytes()
                if p.name == "manifest.json" and extra == "changed":
                    data += b" "
                archive.writestr(p.relative_to(staged).as_posix(), data)
        if extra == "added":
            archive.writestr("node_modules/x.js", b"")


def test_packed_bundle_checked(tmp_path):
    """F39: the packer is third-party code; a bundle that isn't byte
    for byte the staged files fails the build."""
    staged = build_mcpb.stage(tmp_path / "bundle", _rendered())
    _zip(staged, tmp_path / "ok.mcpb")
    build_mcpb.check_packed(tmp_path / "ok.mcpb", staged)
    for extra in ("added", "changed"):
        _zip(staged, tmp_path / f"{extra}.mcpb", extra)
        with pytest.raises(SystemExit):
            build_mcpb.check_packed(tmp_path / f"{extra}.mcpb", staged)


def test_packer_locked():
    """F39: the packer and its whole dependency tree come from
    mcpb/package-lock.json by hash (npm ci, no install scripts), not
    whatever npx resolves on the day."""
    package = json.loads((ROOT / "mcpb" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "mcpb" / "package-lock.json").read_text(encoding="utf-8"))
    ((name, version),) = package["devDependencies"].items()
    assert name == "@anthropic-ai/mcpb" and re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert lock["packages"][""]["devDependencies"] == package["devDependencies"]
    assert lock["packages"][f"node_modules/{name}"]["version"] == version
    for path, entry in lock["packages"].items():
        if path:
            assert entry["integrity"].startswith("sha512-"), path
            assert not entry.get("hasInstallScript"), path
    assert build_mcpb.PACKER.relative_to(ROOT).as_posix() == "mcpb/node_modules/.bin/mcpb"
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        assert "npx" not in text, workflow.name
        for npm in re.findall(r"^\s*(?:-\s*)?(?:run:\s*)?(npm\s.*)$", text, re.M):
            assert npm.startswith("npm ci --ignore-scripts"), (workflow.name, npm)


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


_WRITES_DISABLED = re.compile(r"editing is disabled", re.I)


def _writes_text(home, enable_writes):
    """A write tool's text with the "Allow editing collections" box
    ticked or not, passed the way Claude Desktop passes it. Only a
    missing collection is touched, in a synthetic library."""
    FixtureLibrary.create(home)
    _, env = build_mcpb.desktop_command(TEMPLATE, "/ext", {"enable_writes": enable_writes})
    return _call_write_tool(home, env[WRITES_ENV])


def test_extension_writes_off(tmp_path):
    """F20: unticked ("false") keeps the write tools refusing."""
    assert _WRITES_DISABLED.search(_writes_text(tmp_path, False))


def test_extension_writes_on(tmp_path):
    """F20: ticked ("true") enables the write tools."""
    assert not _WRITES_DISABLED.search(_writes_text(tmp_path, True))


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


def _step_inputs(text, action):
    """The `with:` lines of every step that uses ``action``."""
    lines = text.splitlines()
    steps = []
    for n, line in enumerate(lines):
        if re.match(rf"\s*(?:-\s*)?uses:\s*{re.escape(action)}@", line):
            indent = len(line) - len(line.lstrip(" -"))
            body = []
            for following in lines[n + 1:]:
                if following.strip() and len(following) - len(following.lstrip()) < indent:
                    break
                body.append(following)
            steps.append("\n".join(body))
    return steps


def test_uv_pinned():
    """F39: CI, the release build and the audit all use one uv version,
    not whatever uv is newest on the day."""
    versions = set()
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for inputs in _step_inputs(workflow.read_text(encoding="utf-8"), "astral-sh/setup-uv"):
            match = re.search(r'^\s+version: "(\d+\.\d+\.\d+)"$', inputs, re.M)
            assert match, (workflow.name, inputs)
            versions.add(match.group(1))
    assert len(versions) == 1, versions


@pytest.mark.parametrize("workflow", sorted((ROOT / ".github" / "workflows").glob("*.yml")),
                         ids=lambda p: p.name)
def test_actions_pinned_by_sha(workflow):
    """F39: a tag can be moved to other code; a commit can't."""
    uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", workflow.read_text(encoding="utf-8"), re.M)
    assert uses
    for action, comment in uses:
        if action.startswith("./"):  # a workflow in this repo, at this commit
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action
        assert re.match(r"\s*# v\d", comment), f"{action}: add a '# vX.Y.Z' comment"
