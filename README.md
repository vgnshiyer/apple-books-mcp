# Apple Books MCP

Model Context Protocol (MCP) server for Apple Books.

![](https://badge.mcpx.dev?type=server 'MCP Server')
[![PyPI](https://img.shields.io/pypi/v/apple-books-mcp.svg)](https://pypi.org/project/apple-books-mcp/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![](https://img.shields.io/badge/Follow-vgnshiyer-0A66C2?logo=linkedin)](https://www.linkedin.com/comm/mynetwork/discovery-see-all?usecase=PEOPLE_FOLLOWS&followMember=vgnshiyer)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Donate-yellow.svg?logo=buymeacoffee)](https://www.buymeacoffee.com/vgnshiyer)

## At a glance

* Ask Claude to summarize your recent highlights
* Ask Claude what books you're currently reading and your progress
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
| describe_book | Get details of a particular book | book_id: str |
| get_book_annotations | Get all annotations for a book | book_id: str |
| search_books_by_title | Search for books by title | title: str |

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
| list_all_annotations | List all annotations | limit?: int |
| recent_annotations | Get most recent annotations | limit?: int (default: 10) |
| describe_annotation | Get details of an annotation | annotation_id: str |
| get_highlights_by_color | Get all highlights by color | color: str, limit?: int |
| search_highlighted_text | Search highlights by text | text: str, limit?: int |
| search_notes | Search annotations by note | note: str, limit?: int |
| full_text_search | Search annotations by any text | text: str, limit?: int |

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

## Upcoming Features

- [ ] add docker support
- [ ] add resources support
- [ ] edit collections support
- [ ] edit highlights support

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
