# Apple Books MCP

Model Context Protocol (MCP) server for Apple Books.

[![PyPI](https://img.shields.io/pypi/v/apple-books-mcp.svg)](https://pypi.org/project/apple-books-mcp/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![](https://img.shields.io/badge/Follow-vgnshiyer-0A66C2?logo=linkedin)](https://www.linkedin.com/comm/mynetwork/discovery-see-all?usecase=PEOPLE_FOLLOWS&followMember=vgnshiyer)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Donate-yellow.svg?logo=buymeacoffee)](https://www.buymeacoffee.com/vgnshiyer)

## At a glance

* Ask Claude to summarize your recent highlights
* Ask Claude to search your highlights by color
* Ask Claude to organize books in your library by genre
* Ask Claude to recommend similar books based on your reading history
* Ask Claude to compare notes from different books read on the same subject

<!-- ## Feature Demo -->

And much more!

## Available Tools

| Tool | Description | Parameters |
|----------|-------------|------------|
| list_collections() | List all collections | None |
| get_collection_books(collection_id) | Get all books in a collection | collection_id: str |
| describe_collection(collection_id) | Get details of a collection | collection_id: str |
| list_all_books() | List all books | None |
| get_book_annotations(book_id) | Get all annotations for a book | book_id: str |
| describe_book(book_id) | Get details of a particular book | book_id: str |
| list_all_annotations() | List all annotations | None |
| get_highlights_by_color(color) | Get all highlights by color | color: str |
| search_highlighted_text(text) | Search for highlights by highlighted text | text: str |
| search_notes(note) | Search for notes | note: str |
| full_text_search(text) | Search for annotations containing the given text | text: str |
| recent_annotations() | Get 10 most recent annotations | None |
| describe_annotation(annotation_id) | Get details of an annotation | annotation_id: str |

## Installation

### Using uv (recommended)

[uvx](https://docs.astral.sh/uv/guides/tools/) can be used to directly run apple-books-mcp (without installing it).

```bash
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
            "args": [ "apple-books-mcp" ]
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