# Apple Books MCP

<!-- mcp-name: io.github.vgnshiyer/apple-books-mcp -->

Model Context Protocol (MCP) server for Apple Books.

[![Website](https://img.shields.io/badge/website-vgnshiyer.me-CC785C)](https://vgnshiyer.me/AppleBooksMcp)
![](https://badge.mcpx.dev?type=server 'MCP Server')
[![PyPI](https://img.shields.io/pypi/v/apple-books-mcp.svg)](https://pypi.org/project/apple-books-mcp/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![](https://img.shields.io/badge/Follow-vgnshiyer-0A66C2?logo=linkedin)](https://www.linkedin.com/comm/mynetwork/discovery-see-all?usecase=PEOPLE_FOLLOWS&followMember=vgnshiyer)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Donate-yellow.svg?logo=buymeacoffee)](https://www.buymeacoffee.com/vgnshiyer)

## At a glance

* **Pick up where you left off** — Claude sees the book you're reading, your progress and the chapter you're on, and pulls that chapter's text and your highlights when it needs them.
* **Expand on any highlight** — get the surrounding paragraph explained in context, with the exact anchor you marked shown in `«...»`.
* **Revisit a book** — pull your highlights, cluster them by theme, and quote you back to yourself.
* **Reflect on your reading** — patterns across books, recurring ideas in your highlights, what you're actually drawn to.

https://github.com/user-attachments/assets/77a5a29b-bfd7-4275-a4af-8d6c51a4527e

And much more!

## Available Tools

### Collections

| Tool | Description | Parameters |
|------|-------------|------------|
| list_all_collections | List all collections | limit?: int |
| get_collection_books | Get all books in a collection | collection_id: str |
| describe_collection | Get details of a collection | collection_id: str |
| search_collections_by_title | Search for collections by title | title: str |

### Editing collections (opt-in)

Off by default. Enable by adding `--enable-writes` to the server args:

```json
"args": ["apple-books-mcp", "--enable-writes"]
```

Apple provides no automation API for collections, so these write directly to the library database — behind guard rails: every write **refuses while Books is open**, takes an automatic WAL-safe backup first (`~/.py_apple_books/backups/`), validates the schema and aborts on drift, and only touches user-created collections (plus "Want to Read" membership). Deleting a collection never deletes the books in it.

> ⚠️ If iCloud sync for collections is enabled, direct edits may not propagate to other devices and can be reverted by a cloud re-sync.

| Tool | Description | Parameters |
|------|-------------|------------|
| create_collection | Create a new collection | title: str, details?: str |
| rename_collection | Rename a user-created collection | collection_id: int, new_title: str |
| delete_collection | Delete a user-created collection (books untouched) | collection_id: int |
| add_book_to_collection | Add a book to a collection (idempotent) | collection_id: int, book_id: int |
| remove_book_from_collection | Remove a book from a collection (idempotent) | collection_id: int, book_id: int |

### Books

| Tool | Description | Parameters |
|------|-------------|------------|
| list_all_books | List all books | limit?: int |
| describe_book | Get details of a particular book (metadata, progress, annotation count, description) | book_id: str |
| list_annotations | Get all annotations for a book (id + text + chapter per row, chapter-ordered) | book_id: int, limit?: int |
| search_books_by_title | Search for books by title | title: str |
| get_books_by_genre | Get books by genre (substring match) | genre: str, limit?: int |

### Reading Status

| Tool | Description | Parameters |
|------|-------------|------------|
| get_books_in_progress | Get books currently being read | limit?: int |
| get_finished_books | Get books that have been finished | limit?: int |
| get_unstarted_books | Get books not yet started | limit?: int |
| get_recently_read_books | Get most recently opened books | limit?: int (default: 10) |

### Annotations

| Tool | Description | Parameters |
|------|-------------|------------|
| list_all_annotations | Browse every annotation grouped by book, newest first | limit?: int |
| recent_annotations | Get most recent annotations (flat, with date + book per row) | limit?: int (default: 10) |
| describe_annotation | Get full details of a single annotation | annotation_id: str |
| get_annotation_context | Text window around a highlight (the paragraph it's in), with the highlight marked `«...»` | annotation_id: int, chars_before?: int (default: 500), chars_after?: int (default: 500) |
| get_highlights_by_color | Highlights of a particular color, grouped by book | color: str, limit?: int |
| search_notes | Search user notes (shows highlight + note inline) | note: str, limit?: int |
| search_annotations | Search across highlights + notes + surrounding text | text: str, limit?: int |
| get_annotations_by_date_range | Annotations within a date range (flat, with date + book per row) | after?: YYYY-MM-DD, before?: YYYY-MM-DD, limit?: int |

### Library Stats

| Tool | Description | Parameters |
|------|-------------|------------|
| get_library_stats | Get library summary with reading stats | None |

### Book Content

Only works for non-DRM EPUBs (imported books, Project Gutenberg, Standard Ebooks, etc.). Apple Books Store purchases are FairPlay-protected and return a clear error. iCloud-only books return a "not downloaded" hint.

| Tool | Description | Parameters |
|------|-------------|------------|
| list_book_chapters | Table of contents for a book (chapter titles, order, nesting) | book_id: int |
| get_chapter_content | Plain-text content of a chapter, with optional `offset` + `max_chars` slicing | book_id: int, chapter_id: str, offset?: int, max_chars?: int |
| get_current_reading_position | The chapter the user last left off reading (via Apple Books' auto-bookmark CFI) | book_id: int |

## Available Resources

Attachable data objects accessible from Claude Desktop's resource picker.

| Resource | URI | Description |
|----------|-----|-------------|
| Currently Reading | `apple-books://currently-reading` | A short pointer to the book you're reading right now (the most recently opened in-progress book): title, author, book id, progress, the chapter you left off on with its chapter id (for non-DRM EPUBs), and how many highlights you have in it. It carries no chapter text or highlights; Claude fetches those on demand with `get_chapter_content` and `list_annotations`. Attach to any conversation to focus Claude on your current read. |

## Available Prompts

One-click workflows, accessible from Claude Desktop's prompt picker.

| Prompt | Description | Arguments |
|--------|-------------|-----------|
| weekly_digest | Summarize what I've read and highlighted in the past week | days?: int (default: 7) |
| library_snapshot | A reflection on my whole reading life | None |
| revisit_book | Revisit your notes and highlights from a specific book | book_title: str |

## Installation

### Using uv (recommended)

[uvx](https://docs.astral.sh/uv/guides/tools/) runs apple-books-mcp without a separate install step.

```bash
brew install uv  # for macos
uvx apple-books-mcp --version
```

The first run downloads about 14 MB of dependencies and can take up to a minute, which is close to how long Claude waits for a server to start. Running the command above once in Terminal warms uv's cache before you add the server to Claude. `--version` prints the apple-books-mcp, mcp and py-apple-books versions uvx resolved.

### Using pip

Needs Python 3.10 or newer (the `python3` that ships with macOS is 3.9). Install into a virtual environment:

```bash
python3 -m venv ~/.venvs/apple-books-mcp
~/.venvs/apple-books-mcp/bin/pip install apple-books-mcp
~/.venvs/apple-books-mcp/bin/apple-books-mcp --version
```

Upgrade later with `~/.venvs/apple-books-mcp/bin/pip install -U apple-books-mcp`.

### Using Docker (deprecated)

> ⚠️ The Docker image is deprecated and only partly works: a Linux container can't be granted the macOS permission, can't reach book files stored in iCloud Drive (so the tools that read book text fail), and can't tell whether Books is running (so collection writes are unsupported). Use uvx instead.

It can still serve library metadata and annotations. Mount the Apple Books container read-only and keep stdin open with `-i`:

```bash
docker run -i --rm -v ~/Library/Containers/com.apple.iBooksX/Data/Documents:/root/Library/Containers/com.apple.iBooksX/Data/Documents:ro ghcr.io/vgnshiyer/apple-books-mcp:latest
```

In a Claude config, spell out your home directory: Claude doesn't expand `~`.

```json
{
    "mcpServers": {
        "apple-books-mcp": {
            "command": "docker",
            "args": [
                "run", "-i", "--rm",
                "-v", "/Users/YOU/Library/Containers/com.apple.iBooksX/Data/Documents:/root/Library/Containers/com.apple.iBooksX/Data/Documents:ro",
                "ghcr.io/vgnshiyer/apple-books-mcp:latest"
            ]
        }
    }
}
```

## First-run permission prompt (macOS)

The first time the server reads your library, macOS asks whether the program that started it may "access data from other apps", because Apple Books keeps its library in its own app container (`~/Library/Containers/com.apple.iBooksX/`). Click **Allow**.

![macOS permission prompt: uvx would like to access data from other apps. Don't Allow / Allow.](./docs/permission-prompt.png)

macOS asks about the program Claude launches, not about apple-books-mcp itself:

* `uvx` with the recommended Claude Desktop config (the prompt above);
* the Python interpreter with the pip config;
* `claude` when the server is configured in Claude Code;
* your terminal app when you run the server by hand.

The permission belongs to that program, so anything else it launches can use it too. apple-books-mcp only reads the library, plus the book files for chapter text; it changes nothing unless you pass `--enable-writes`.

**Asked again after an update?** Homebrew's `uv`/`uvx` and Homebrew or uv-managed Python builds aren't signed by a registered developer, so after `brew upgrade uv` (or a Python upgrade) macOS treats them as a new program and asks again. Click **Allow**.

**Clicked "Don't Allow"?** Tools then fail with "macOS denied access to the Apple Books library". (Older releases failed at startup instead, with "No sqlite files found in … Please open Apple Books at least once".) This permission has no switch in System Settings. To be asked again, run:

```bash
tccutil reset SystemPolicyAppData
```

This clears the "data from other apps" decisions for all apps. Then quit and reopen Claude, and click **Allow**. Adding the program (e.g. `/opt/homebrew/bin/uvx`) to **Full Disk Access** in System Settings > Privacy & Security also works, but grants far more access.

## Configuration

### Claude Desktop Setup

Quit Claude Desktop (Cmd+Q) before editing `~/Library/Application Support/Claude/claude_desktop_config.json`: Desktop rewrites the file while it runs, so edits made while it's open can be lost.

#### Using uvx (recommended)

```json
{
    "mcpServers": {
        "apple-books-mcp": {
            "command": "uvx",
            "args": [ "apple-books-mcp" ]
        }
    }
}
```

Plain `apple-books-mcp` still picks up new releases: uv checks PyPI for a newer version whenever its cached copy of the package index is more than 10 minutes old, which is usually the case when Claude starts. `apple-books-mcp@latest` gains nothing over that and makes uv build a fresh environment on every launch (3–12 s instead of about 1 s).

#### Faster or offline startup

Because uvx checks PyPI at launch, the server won't start while PyPI is unreachable. To avoid that:

* Run `uv tool install apple-books-mcp` once. The same uvx config then uses the installed copy and starts in about a second without contacting PyPI. Installed copies don't update themselves: run `uv tool upgrade apple-books-mcp` to get new releases.
* Or use `"args": ["--offline", "apple-books-mcp"]` to run whatever version uv has already cached (after at least one online run), without any network access.

#### Using pip

Point Claude at the script inside the virtual environment from [Using pip](#using-pip), with your home directory spelled out:

```json
{
    "mcpServers": {
        "apple-books-mcp": {
            "command": "/Users/YOU/.venvs/apple-books-mcp/bin/apple-books-mcp"
        }
    }
}
```

### Claude Code Setup

Claude Code doesn't read the Claude Desktop config. Add the server with:

```bash
claude mcp add --scope user apple-books-mcp -- uvx apple-books-mcp
```

and check it with `/mcp`.

## Troubleshooting

### Where to look

* `~/Library/Logs/Claude/mcp-server-apple-books-mcp.log` (named after your `mcpServers` key) has the server's own output, including Python tracebacks. For more detail, add `"-v"` to the args: the server then logs its version, the mcp and py-apple-books versions it runs with, and each request.
* `~/Library/Logs/Claude/mcp.log` lists failures. Its "Server started and connected successfully" line only means the process was launched, not that it works.
* `~/Library/Logs/Claude/main.log` records "Connected to apple-books-mcp (N tools)" when a Claude Code or Cowork session in Desktop loaded the tools.
* `uvx apple-books-mcp --version` in Terminal shows which versions uvx resolves.

### Common problems

* **"Connection closed", "Server disconnected" or "Server transport closed unexpectedly"**: the server exited at startup. Read the last error in the traceback in `mcp-server-apple-books-mcp.log`.
  * `No module named 'mcp.server.fastmcp'`: uv picked an apple-books-mcp release older than 0.8.1 together with mcp 2.x, which removed the API those releases were built on (on Python 3.12 and older, uvx could even fall back to 0.1.2). 0.8.1 and newer work with Python 3.10+ and stay on mcp 1.x. Run `uv cache clean apple-books-mcp`, check that `uvx apple-books-mcp --version` reports 0.8.1 or newer, then quit and reopen Claude.
  * A `No module named 'server'` error in front of the real one is noise from releases before 0.8.4.
* **"Not ready after 60 seconds" or "Request timed out" at startup**: the first launch was still downloading and installing dependencies. Run `uvx apple-books-mcp --version` once in Terminal, then reopen Claude.
* **"macOS denied access to the Apple Books library"** (older releases: "No sqlite files found in …"): see [First-run permission prompt](#first-run-permission-prompt-macos).
* **"No Apple Books library store found"**: Apple Books hasn't created its library for this macOS user yet. Open Books once, then try again.
* **The server stopped answering after you stopped a response**: before 0.8.4, cancelling a tool call that was waiting behind a slow one could shut the server down. Update, then restart Claude.
* **"Extension apple-books-mcp not found in installed extensions"** in `main.log` is harmless.

### Getting Claude to retry

* **Claude Desktop chats**: quit Claude completely (Cmd+Q) and reopen it. A server that failed to start isn't retried otherwise.
* **Claude Code and Cowork sessions in Desktop**: start a new session; each new session retries the server.
* **Claude Code**: run `/mcp` and reconnect apple-books-mcp, or restart Claude Code.

## Upcoming Features

- [ ] PDF content access (currently EPUB-only)
- [ ] fuller annotation context via CFI → paragraph resolution

## Contribution

Thank you for considering contributing to this project!

### Development

Clone the repository and let uv create the virtual environment and install the package with its dependencies:

```bash
uv sync
uv run apple-books-mcp --version
uv run pytest
```

#### Debugging

Logs go to stderr, which Claude Desktop writes to `~/Library/Logs/Claude/mcp-server-<name>.log`. Without flags the server logs only warnings and errors; `-v` adds startup details (versions, whether writes are enabled) and a line per request; `-vv` adds debug output from the MCP SDK.

**With Claude Desktop**

```json
{
    "mcpServers": {
        "apple-books-mcp": {
            "command": "uv",
            "args": [
                "--directory",
                "/path/to/apple-books-mcp/",
                "run",
                "apple-books-mcp",
                "-v"
            ]
        }
    }
}
```

**With inspector**

```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/apple-books-mcp run apple-books-mcp
```

### Opening Issues
If you encounter a bug, have a feature request, or want to discuss something related to the project, please open an issue on the GitHub repository. When opening an issue, please provide:

**Bug Reports**: Describe the issue in detail. Include steps to reproduce the bug if possible, along with any error messages or screenshots.

**Feature Requests**: Clearly explain the new feature you'd like to see added to the project. Provide context on why this feature would be beneficial.

**General Discussions**: Feel free to start discussions on broader topics related to the project.

### Contributing

1️⃣ Fork the GitHub repository https://github.com/vgnshiyer/apple-books-mcp \
2️⃣ Create a new branch for your changes (git checkout -b feature/my-new-feature). \
3️⃣ Make your changes and test them thoroughly. \
4️⃣ Push your changes and open a Pull Request to `main`.

*Please provide a clear title and description of your changes.*

## License

Apple Books MCP is licensed under the Apache 2.0 license. See the LICENSE file for details.
