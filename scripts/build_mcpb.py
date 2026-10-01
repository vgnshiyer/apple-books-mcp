"""Build the Claude Desktop extension (.mcpb) and the release's server.json.

Usage:
  uv run --locked python scripts/build_mcpb.py [--out DIR]
      Build DIR/apple-books-mcp-<version>.mcpb (default DIR: dist) with
      the official packer, and write DIR/server.json: ./server.json
      plus the bundle as an "mcpb" package (release URL and sha256),
      ready for `mcp-publisher publish DIR/server.json`.
  python scripts/build_mcpb.py --smoke BUNDLE
      Install BUNDLE the way Claude Desktop does (`uv sync` in the
      unpacked bundle) and check the server it starts answers
      initialize + tools/list (scripts/smoke_test.py).
  python scripts/build_mcpb.py --check-version VERSION
      Fail unless every version field (pyproject.toml, __init__.py,
      server.json, uv.lock) equals VERSION. The release workflow passes
      the tag without its "v".

mcpb/manifest.json is the bundle's manifest minus "version" and
"tools", which the build fills in from pyproject.toml and the server's
registered tools. The bundle uses the manifest's "uv" server type:
Claude Desktop installs uv, runs `uv sync` against the bundled
pyproject.toml and uv.lock (the tested dependency set), and starts the
server from mcp_config. No Python interpreter or packages are bundled.

Building needs the project's dependencies importable (to list the
tools) and Node.js (for npx); --check-version needs only Python.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "mcpb" / "manifest.json"
SERVER_JSON = ROOT / "server.json"

# The official packer (https://github.com/modelcontextprotocol/mcpb),
# pinned so a new release can't change what gets shipped.
MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"

# What goes into the bundle: enough for `uv sync` to install the
# project from source with the locked dependencies. Not .python-version
# (it would make uv download that exact Python), tests, CI or docs.
BUNDLE_FILES = ("pyproject.toml", "uv.lock", "README.md", "LICENSE")
BUNDLE_PACKAGE = "apple_books_mcp"


def bundle_name(version: str) -> str:
    return f"apple-books-mcp-{version}.mcpb"


# -- versions ---------------------------------------------------------------

def _pyproject_version(text: str):
    """[project] version, or None if it is dynamic or missing. A small
    parser rather than tomllib, which Python 3.10 lacks."""
    section = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if section:
        match = re.search(r'^version\s*=\s*"([^"]+)"', section.group(1), re.M)
        if match:
            return match.group(1)
    return None


def version_fields(root: Path = ROOT) -> dict:
    """Every place the release version is written: {place: version}."""
    fields = {}
    pyproject = _pyproject_version((root / "pyproject.toml").read_text(encoding="utf-8"))
    if pyproject:
        fields["pyproject.toml"] = pyproject
    init = re.search(r'^__version__\s*=\s*"([^"]+)"',
                     (root / BUNDLE_PACKAGE / "__init__.py").read_text(encoding="utf-8"), re.M)
    if init:
        fields["apple_books_mcp/__init__.py"] = init.group(1)
    server = json.loads((root / "server.json").read_text(encoding="utf-8"))
    fields["server.json version"] = server.get("version")
    for i, package in enumerate(server.get("packages", [])):
        if "version" in package:
            fields[f"server.json packages[{i}] ({package.get('registryType')})"] = package["version"]
    lock = re.search(r'^name = "apple-books-mcp"\nversion = "([^"]+)"',
                     (root / "uv.lock").read_text(encoding="utf-8"), re.M)
    if lock:
        fields["uv.lock"] = lock.group(1)
    return fields


def project_version(root: Path = ROOT) -> str:
    fields = version_fields(root)
    versions = set(fields.values())
    if len(versions) != 1:
        raise SystemExit("Version fields disagree:\n" + _describe(fields))
    return versions.pop()


def version_mismatches(expected: str, root: Path = ROOT) -> dict:
    """The version fields that aren't ``expected``."""
    return {place: v for place, v in version_fields(root).items() if v != expected}


def _describe(fields: dict) -> str:
    return "\n".join(f"  {place}: {version}" for place, version in fields.items())


# -- manifest ---------------------------------------------------------------

def _summary(description: str) -> str:
    """The first sentence of a tool description, on one line."""
    paragraph = re.split(r"\n\s*\n", description.strip(), maxsplit=1)[0]
    text = " ".join(paragraph.split()).replace("``", "`")
    match = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return match.group(1) if match else text


def server_tools() -> list:
    """[{name, description}] for every tool the server registers."""
    from apple_books_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    return [{"name": t.name, "description": _summary(t.description or t.name)} for t in tools]


def render_manifest(version: str, tools: list) -> dict:
    """The template with "version" after "name" and "tools" after
    "server" (both required keys)."""
    manifest = {}
    for key, value in json.loads(TEMPLATE.read_text(encoding="utf-8")).items():
        manifest[key] = value
        if key == "name":
            manifest["version"] = version
        elif key == "server":
            manifest["tools"] = tools
    return manifest


def stage(dest: Path, manifest: dict, root: Path = ROOT) -> Path:
    """Lay out the bundle's files in ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in BUNDLE_FILES:
        shutil.copy2(root / name, dest / name)
    shutil.copytree(root / BUNDLE_PACKAGE, dest / BUNDLE_PACKAGE,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dest


def release_server_json(version: str, bundle: Path) -> dict:
    """server.json with the bundle added as an "mcpb" package: a GitHub
    release asset URL (the registry checks it exists) and its sha256
    (clients check it before installing)."""
    server = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    repo = server["repository"]["url"].rstrip("/")
    server["packages"] = [p for p in server["packages"] if p.get("registryType") != "mcpb"]
    server["packages"].append({
        "registryType": "mcpb",
        "identifier": f"{repo}/releases/download/v{version}/{bundle.name}",
        "version": version,
        "fileSha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "transport": {"type": "stdio"},
    })
    return server


def _npx(*args: str) -> None:
    npx = shutil.which("npx")
    if npx is None:
        raise SystemExit("npx not found: building the bundle needs Node.js.")
    subprocess.run([npx, "--yes", MCPB_CLI, *args], check=True)


def build(out: Path) -> Path:
    version = project_version()
    manifest = render_manifest(version, server_tools())
    out.mkdir(parents=True, exist_ok=True)
    bundle = out / bundle_name(version)
    with tempfile.TemporaryDirectory() as tmp:
        staged = stage(Path(tmp) / "apple-books-mcp", manifest)
        # pack validates the manifest against the schema first.
        _npx("pack", str(staged), str(bundle))
    server_json = out / "server.json"
    server_json.write_text(json.dumps(release_server_json(version, bundle), indent=2) + "\n",
                           encoding="utf-8")
    print(f"Built {bundle} ({bundle.stat().st_size:,} bytes, "
          f"{len(manifest['tools'])} tools) and {server_json}")
    return bundle


# -- smoke test ---------------------------------------------------------------

def desktop_value(value) -> str:
    """A user_config value as Claude Desktop substitutes it into
    mcp_config: booleans become "true"/"false"."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def desktop_command(manifest: dict, dirname: str, user_config=None):
    """(argv, env) Claude Desktop starts for ``manifest`` installed in
    ``dirname``, with each user_config option at its default unless
    ``user_config`` sets it."""
    values = {key: option.get("default", "") for key, option in manifest.get("user_config", {}).items()}
    values.update(user_config or {})

    def substitute(text: str) -> str:
        text = text.replace("${__dirname}", dirname)
        return re.sub(r"\$\{user_config\.(\w+)\}", lambda m: desktop_value(values[m.group(1)]), text)

    config = manifest["server"]["mcp_config"]
    argv = [substitute(config["command"]), *(substitute(a) for a in config.get("args", []))]
    env = {key: substitute(value) for key, value in config.get("env", {}).items()}
    return argv, env


def smoke(bundle: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        install = Path(tmp) / "extension"
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(install)
        manifest = json.loads((install / "manifest.json").read_text(encoding="utf-8"))
        # The bundle's own environment, as under Claude Desktop, not one
        # this script happens to run in.
        base_env = {k: v for k, v in os.environ.items()
                    if k not in ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")}
        # What Claude Desktop runs when it installs a "uv" extension.
        subprocess.run(["uv", "sync", "--quiet"], cwd=install, env=base_env, check=True)
        argv, env = desktop_command(manifest, str(install))
        print(f"Starting: {' '.join(argv)} with {env}", flush=True)
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "smoke_test.py"), *argv],
            env={**base_env, **env},
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--out", type=Path, default=ROOT / "dist",
                       help="directory for the bundle and server.json (default: dist)")
    group.add_argument("--smoke", type=Path, metavar="BUNDLE",
                       help="install BUNDLE like Claude Desktop and smoke-test it")
    group.add_argument("--check-version", metavar="VERSION",
                       help="fail unless every version field equals VERSION")
    args = parser.parse_args()

    if args.check_version is not None:
        wrong = version_mismatches(args.check_version)
        if wrong:
            print(f"Release {args.check_version} doesn't match:\n{_describe(wrong)}", file=sys.stderr)
            return 1
        print(f"All version fields are {args.check_version}:\n{_describe(version_fields())}")
        return 0
    if args.smoke is not None:
        return smoke(args.smoke)
    build(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
