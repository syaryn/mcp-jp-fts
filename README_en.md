# Japanese Full-Text Search MCP Server

[日本語](./README.md)

A Model Context Protocol (MCP) server for Japanese full-text search using **FastMCP**, **SQLite FTS5**, and **SudachiPy**.

## Features

- **Japanese Full-Text Search**: Uses SudachiPy (Mode A) to properly tokenize Japanese text for high-precision search
- **Local File Indexing**: Recursively scans directories to index text files
- **Atomic Updates**: Automatically removes entries for deleted files when re-indexing a directory to keep the index clean
- **FastMCP Integration**: Exposes `index_directory` and `search_documents` as MCP tools

## Advantages Over grep for LLM-Based Search

When compared to traditional grep-based file searches performed by LLMs, this full-text search server offers several significant advantages:

### 1. High-Precision Search with Japanese Morphological Analysis

**With grep:**
- Simple substring pattern matching
- Searching for "東京都" (Tokyo) will find "東京都庁" (Tokyo Metropolitan Government) and "東京都民" (Tokyo residents), but doesn't understand context or word boundaries
- Difficulty searching for compound words or conjugated forms (e.g., searching for "走る" (to run) won't find "走った" (ran) or "走っている" (is running))

**With this server:**
- Word-level tokenization using SudachiPy morphological analysis
- Understands Japanese linguistic structure for more accurate searches
- Tokenization provides higher precision search results

### 2. Fast Search Through Indexing

**With grep:**
- Scans all files on every search
- Time-consuming when dealing with large numbers of files
- LLM context window and token constraints may require multiple grep executions

**With this server:**
- Pre-built indexes enable fast searches
- Optimized full-text search using SQLite FTS5
- Retrieve all relevant results in a single query

### 3. Relevance Scoring and Snippet Display

**With grep:**
- Displays matched lines as-is
- Difficult to determine which results are more relevant
- Additional `cat` or `head` commands needed to understand context

**With this server:**
- FTS5 rank function displays results in order of relevance
- Automatically generates snippets with surrounding context for matched locations
- Improved search result quality enables better LLM decision-making

### 4. Flexible Search Patterns

**With grep:**
- Requires knowledge of regular expressions
- Complex search conditions lead to complicated regex patterns prone to errors
- Difficult to handle Japanese language characteristics (mixed hiragana, katakana, and kanji)

**With this server:**
- Natural language queries supported
- Tokenization automatically recognizes word boundaries
- Flexible searches using FTS5 operators (AND, OR, NEAR, etc.)

### Usage Comparison Example

**Search with grep (executed by LLM):**
```bash
# Search for files containing the word "データベース" (database)
$ grep -r "データベース" /path/to/docs/
# → Returns massive results that may overwhelm the LLM
# → Includes low-relevance results
# → Multiple executions needed for refinement
```

**Search with this server:**
```json
{
  "query": "データベース 設計",
  "limit": 5
}
```
→ Returns top 5 relevant results containing both "データベース" (database) and "設計" (design) through morphological analysis, with snippets included



## Requirements

- Python 3.10 or higher
- [uv](https://docs.astral.sh/uv/) (package manager)
- [mise](https://mise.jdx.dev/) (optional, for development tool management)

## Installation and Execution

### Using `uvx` (Recommended)

You can run this server directly from the GitHub repository:

```bash
uvx --from git+https://github.com/syaryn/mcp-jp-fts mcp-jp-fts
```

### Local Development

#### 1. Clone the repository

```bash
git clone https://github.com/syaryn/mcp-jp-fts.git
cd mcp-jp-fts
```

#### 2. Install dependencies

```bash
uv sync
```

## Usage

### Starting the Server

#### Development mode (with hot reload)

```bash
uv run fastmcp dev src/mcp_jp_fts/server.py
# or
mise run dev
```

#### Production mode (if installed locally)

```bash
uv run mcp-jp-fts
# or
mise run start
```

### MCP Tools

#### `index_directory`

Indexes all text files in the specified path.

**Input example:**
```json
{
  "root_path": "/path/to/docs"
}
```

**Output example:**
```
Indexed 42 files in /path/to/docs (Previous entries cleared).
```

**Note:** Clears the existing index for this path before adding new data.

#### `search_documents`

Searches indexed documents using SudachiPy tokenization.

**Input example:**
```json
{
  "query": "猫",
  "limit": 5
}
```

**Output example:**
```
File: /path/to/wagahai.txt
Snippet: 吾輩は<b>猫</b>である...

File: /path/to/other.txt
Snippet: この<b>猫</b>は...
```

## Development

### Setting up the development environment

This project uses `mise` to manage development tools.

```bash
# Install mise (if not already installed)
curl https://mise.run | sh

# Install tools
mise install

# Install Git hooks
mise exec -- lefthook install
```

### Common Commands

#### mise tasks

```bash
mise run dev          # Start development server (with hot reload)
mise run start        # Start production server
mise run test         # Run tests (pytest)
mise run test-all     # Run tests across all Python versions (tox)
mise run lint         # Run linter (ruff)
mise run format       # Format code (ruff)
mise run type         # Run type checker (ty)
mise run check        # Run lint, type, and test together
mise run scan         # Run vulnerability scan (osv-scanner)
mise run scan-license # Run license compliance check
```

#### Manual commands

```bash
# Run tests
uv run pytest tests/

# Test across multiple versions
uv run tox

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run ty check .
```

### Git Hooks

Uses `lefthook` for automated checks:

- **pre-commit**: Runs `ruff check` (lint), `ruff format` (format), and `ty check` (type check)
- **pre-push**: Runs `pytest` (tests) and `osv-scanner` (vulnerability scan)

Run hooks manually:
```bash
lefthook run pre-commit
lefthook run pre-push
```

## Testing

The project includes tests for:

1. **Tokenization tests**: Verify correct splitting of Japanese text
2. **Indexing tests**: Validate file walking, reading, tokenization, and SQLite insertion
3. **Atomic update tests**: Confirm deletion of non-existent files during directory re-indexing
4. **Search tests**: Verify queries return correct documents and handle Japanese tokenization

```bash
# Run all tests
mise run test

# Test across multiple Python versions (3.10, 3.11, 3.12, 3.13)
mise run test-all
```

## Code Quality

This project maintains code quality using the following tools:

- **[ruff](https://github.com/astral-sh/ruff)**: Linting and formatting
- **[ty](https://github.com/google/tyche)**: Type checking
- **[pytest](https://pytest.org/)**: Testing framework
- **[tox](https://tox.wiki/)**: Multi-version test automation
- **[osv-scanner](https://github.com/google/osv-scanner)**: Vulnerability and license scanning
- **[lefthook](https://github.com/evilmartians/lefthook)**: Git hook management

## Project Structure

```
mcp-jp-fts/
├── src/
│   └── mcp_jp_fts/
│       └── server.py      # Main FastMCP server implementation
├── tests/
│   ├── test_server.py     # Server functionality tests
│   └── resources/         # Test resources (sample text files)
├── pyproject.toml         # Project metadata and dependencies
├── tox.ini                # Tox configuration
├── mise.toml              # Mise tools and task configuration
├── lefthook.yml           # Git hooks configuration
├── osv-scanner.toml       # OSV Scanner configuration
└── uv.lock                # Lock file
```

## Technology Stack

- **FastMCP**: MCP server framework
- **SQLite FTS5**: Full-text search engine
- **SudachiPy**: Japanese morphological analyzer library
- **uv**: Package manager
- **mise**: Development tool management

## License

This project is released under the MIT License.

## Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

## Troubleshooting

### Python Version Issues

This project supports Python 3.10 or higher. Use a version with available SudachiPy wheels.

```bash
# Check current Python version
python --version

# Use a specific version with uv
uv python install 3.13
uv sync
```

### SudachiPy Dictionary Errors

SudachiPy automatically downloads the dictionary on first run. If you encounter network errors, you can install it manually:

```bash
uv run python -c "import sudachipy; sudachipy.Dictionary()"
```
