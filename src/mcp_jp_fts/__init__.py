import contextlib
import os
import sqlite3
from typing import List

from fastmcp import FastMCP
from sudachipy import dictionary, tokenizer

# Initialize FastMCP server
mcp = FastMCP("mcp-jp-fts")

# SudachiPy Initialization
# split_mode="A" for high recall (Shortest unit) as requested
tokenizer_obj = dictionary.Dictionary().create()
mode = tokenizer.Tokenizer.SplitMode.A


def tokenize(text: str) -> str:
    """Tokenize text using SudachiPy and return space-separated string."""
    tokens = tokenizer_obj.tokenize(text, mode)
    return " ".join([m.surface() for m in tokens])


# Database Helper
DB_PATH = "documents.db"


@contextlib.contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the SQLite database with a FTS5 virtual table."""
    with get_db() as conn:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                path,
                content,
                tokens,
                tokenize='unicode61' 
            );
        """)
        # Note: 'unicode61' is the default tokenizer which works well with space-separated tokens


# Initialize DB on startup
init_db()


@mcp.tool()
def index_directory(root_path: str) -> str:
    """
    Recursively indexes all text files in the given root_path.

    WARNING: This will remove all existing index entries that start with this root_path before re-indexing.
    """
    root_path = os.path.abspath(root_path)
    if not os.path.exists(root_path):
        return f"Error: Path {root_path} does not exist."

    count = 0
    with get_db() as conn:
        # Atomic Transaction
        with conn:  # Context manager handles transaction
            # 1. Clear stale data
            conn.execute(
                "DELETE FROM documents_fts WHERE path LIKE ? || '%'", (root_path,)
            )

            # 2. Walk and Index
            for dirpath, _, filenames in os.walk(root_path):
                for filename in filenames:
                    # Skip hidden files
                    if filename.startswith("."):
                        continue

                    file_path = os.path.join(dirpath, filename)

                    try:
                        # Simple text check: try reading snippets to detect binary
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()  # Read full content for indexing

                        # Tokenize
                        tokens = tokenize(content)

                        # Insert
                        conn.execute(
                            "INSERT INTO documents_fts (path, content, tokens) VALUES (?, ?, ?)",
                            (file_path, content, tokens),
                        )
                        count += 1

                    except UnicodeDecodeError:
                        # Skip binary/non-utf8 files
                        continue
                    except Exception as e:
                        print(f"Failed to index {file_path}: {e}")

    return f"Indexed {count} files in {root_path} (Previous entries cleared)."


@mcp.tool()
def search_documents(query: str, limit: int = 5) -> List[str]:
    """
    Search for documents matching the Japanese query string.
    Returns a list of matching file paths and snippets.
    """
    # Tokenize the query to match the indexed format
    query_tokens = tokenize(query)

    # Simple formatting for FTS5 match: quote phrases or simple AND logic
    # Here we just use the space-separated tokens which implies implicit AND/phrase depending on query syntax
    # For robustness, we can wrap in quotes "..." to treat as phrase or leave as is.
    # Let's treat it as a standard full-text query.
    fts_query = f'"{query_tokens}"'  # Phrase search for exact sequence of tokens might be too strict?
    # Let's try simple space separation which means AND in FTS5 usually (or OR check docs, standard is implicit AND)
    # Actually FTS5 standard syntax: space is implicit AND.
    # But since we space-separated the content ourselves, "A B C" in content matches "A AND B AND C".
    # If user query creates "A B", we want to find docs with A and B.

    fts_query = query_tokens

    results = []
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT path, snippet(documents_fts, 1, '<b>', '</b>', '...', 64) 
            FROM documents_fts 
            WHERE tokens MATCH ? 
            ORDER BY rank 
            LIMIT ?
        """,
            (fts_query, limit),
        )

        for row in cursor:
            results.append(f"File: {row[0]}\nSnippet: {row[1]}\n")

    if not results:
        return ["No matches found."]

    return results


if __name__ == "__main__":
    mcp.run()
