# Apple Books MCP

<!-- mcp-name: io.github.vgnshiyer/apple-books-mcp -->

Model Context Protocol (MCP) server for Apple Books.

![](https://badge.mcpx.dev?type=server 'MCP Server')
[![PyPI](https://img.shields.io/pypi/v/apple-books-mcp.svg)](https://pypi.org/project/apple-books-mcp/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![](https://img.shields.io/badge/Follow-vgnshiyer-0A66C2?logo=linkedin)](https://www.linkedin.com/comm/mynetwork/discovery-see-all?usecase=PEOPLE_FOLLOWS&followMember=vgnshiyer)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Donate-yellow.svg?logo=buymeacoffee)](https://www.buymeacoffee.com/vgnshiyer)

## At a glance

* Ask Claude to summarize your recent highlights
* Ask Claude what books you're currently reading and your progress
* Ask Claude to pick up where you left off — it can see the chapter you're on *and* its text
* Ask Claude to find a book by title
* Ask Claude to organize books in your library by genre
* Ask Claude to recommend similar books based on your reading history
* Ask Claude to compare notes from different books read on the same subject

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
| Currently Reading | `apple-books://currently-reading` | The book you're reading right now — most recently opened in-progress book, with metadata, **the chapter you left off on plus a preview of its text** (for non-DRM EPUBs), and recent annotations. Attach to any conversation to focus Claude on your current read. |

## Available Prompts

One-click workflows, accessible from Claude Desktop's prompt picker.

| Prompt | Description | Arguments |
|--------|-------------|-----------|
| weekly_digest | Summarize what I've read and highlighted in the past week | days?: int (default: 7) |
| explain_recent_highlight | Take my most recent highlight and explain what it means | None |
| what_am_i_reading | Quick snapshot of books I'm currently in the middle of | None |
| library_snapshot | A reflection on my whole reading life | None |
| revisit_book | Revisit your notes and highlights from a specific book | book_title: str |

## Installation

### Using uv (recommended)

[uvx](https://docs.astral.sh/uv/guides/tools/) can be used to directly run apple-books-mcp (without installing it).

```bash
brew install uv  # for macos
uvx apple-books-mcp
```

### Using pip

```bash
pip install apple-books-mcp
```

After installing, you can run the server using:

```bash
python -m apple_books_mcp
```

### Using Docker

```bash
docker run -v ~/Library/Containers/com.apple.iBooksX/Data/Documents:/root/Library/Containers/com.apple.iBooksX/Data/Documents:ro ghcr.io/vgnshiyer/apple-books-mcp:latest
```

## Configuration

### Claude Desktop Setup

#### Using uvx (recommended)

```json
{
    "mcpServers": {
        "apple-books-mcp": {
            "command": "uvx",
            "args": [ "apple-books-mcp@latest" ]
        }
    }
}
```

#### Using python

```json
{
    "mcpServers": {
        "apple-books-mcp": {
            "command": "python",
            "args": ["-m", "apple_books_mcp"]
        }
    }
}
```

#### Using Docker

```json
{
    "mcpServers": {
        "apple-books-mcp": {
            "command": "docker",
            "args": [
                "run", "-i", "--rm",
                "-v", "~/Library/Containers/com.apple.iBooksX/Data/Documents:/root/Library/Containers/com.apple.iBooksX/Data/Documents:ro",
                "ghcr.io/vgnshiyer/apple-books-mcp:latest"
            ]
        }
    }
}
```

## Upcoming Features

- [ ] PDF content access (currently EPUB-only)
- [ ] fuller annotation context via CFI → paragraph resolution

## Contribution

Thank you for considering contributing to this project!

### Development

If you cloned this repository, you can test it using Claude Desktop with below configuration:

Use `uv venv` to create a virtual environment and install the dependencies.

```bash
uv venv
uv sync
```

#### Debugging

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
                "apple_books_mcp",
                "-v"
            ]
        }
    }
}
```

**With inspector**

```bash
npx @modelcontextprotocol/inspector uvx apple-books-mcp
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
