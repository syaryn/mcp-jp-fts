import contextlib
import html
import os
import sqlite3
import time
from typing import List

from fastmcp import FastMCP
import pathspec
from sudachipy import dictionary, tokenizer
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

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


def validate_path(path: str) -> str:
    """
    Validate and resolve path to absolute path.
    Prevents basics of path traversal by ensuring we work with resolved paths.
    """
    # Simply resolving to absolute path is the main requirement for this local tool.
    # Snyk might still flag this as "Path Traversal" because we allow arbitrary file reads,
    # but that is the intended feature of this tool.
    return os.path.abspath(path)


# Database Helper
DB_PATH = "documents.db"


@contextlib.contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
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
        
        # Meta table for incremental indexing
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents_meta (
                path TEXT PRIMARY KEY,
                mtime REAL,
                scanned_at REAL
            );
        """)
        # Enable Write-Ahead Logging (WAL) for better concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        # Note: 'unicode61' is the default tokenizer which works well with space-separated tokens


# Initialize DB on startup
# Initialize DB on startup
init_db()

# Global Observer for Watch Mode
observer = Observer()
WATCHED_PATHS = set()

def _update_or_remove_file(file_path: str) -> str:
    """Internal helper to update or remove a file from index."""
    file_path = validate_path(file_path)
    current_time = time.time()
    
    with get_db() as conn:
        with conn:
            if not os.path.exists(file_path):
                # File deleted
                conn.execute("DELETE FROM documents_fts WHERE path = ?", (file_path,))
                conn.execute("DELETE FROM documents_meta WHERE path = ?", (file_path,))
                return f"Removed {file_path} from index."
            
            try:
                # 1. Read and Tokenize
                # deepcode ignore PathTraversal: This is a local file indexing tool that must access user-specified files.
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                tokens = tokenize(content)
                
                # 2. Update FTS
                conn.execute("DELETE FROM documents_fts WHERE path = ?", (file_path,))
                conn.execute(
                    "INSERT INTO documents_fts (path, content, tokens) VALUES (?, ?, ?)",
                    (file_path, content, tokens),
                )
                
                # 3. Update Metadata
                file_mtime = os.path.getmtime(file_path)
                conn.execute(
                    """
                    INSERT INTO documents_meta (path, mtime, scanned_at) 
                    VALUES (?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime = excluded.mtime,
                        scanned_at = excluded.scanned_at
                    """,
                    (file_path, file_mtime, current_time)
                )
                return f"Updated {file_path} in index."

            except UnicodeDecodeError:
                return f"Skipped binary/non-utf8 file: {file_path}"
            except Exception as e:
                return f"Failed to update {file_path}: {e}"


class FTSHandler(FileSystemEventHandler):
    def __init__(self, root_path, ignore_spec=None):
        self.root_path = validate_path(root_path)
        self.ignore_spec = ignore_spec

    def _should_ignore(self, path):
        if self.ignore_spec:
            rel_path = os.path.relpath(path, self.root_path)
            return self.ignore_spec.match_file(rel_path)
        return False

    def on_moved(self, event):
        if not event.is_directory:
            if not self._should_ignore(event.src_path):
                _update_or_remove_file(event.src_path) # Will delete because it's gone
            if not self._should_ignore(event.dest_path):
                _update_or_remove_file(event.dest_path) # Will add

    def on_created(self, event):
        if not event.is_directory:
            if not self._should_ignore(event.src_path):
                _update_or_remove_file(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            if not self._should_ignore(event.src_path):
                _update_or_remove_file(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            if not self._should_ignore(event.src_path):
                _update_or_remove_file(event.src_path)



@mcp.tool()
def index_directory(root_path: str) -> str:
    """
    Recursively indexes all text files in the given root_path.

    WARNING: This will remove all existing index entries that start with this root_path before re-indexing.
    """
    root_path = validate_path(root_path)

    current_time = time.time()
    updated_count = 0
    skipped_count = 0
    
    with get_db() as conn:
        with conn:
            # Load .gitignore if exists
            gitignore_path = os.path.join(root_path, ".gitignore")
            ignore_spec = None
            if os.path.exists(gitignore_path):
                try:
                    with open(gitignore_path, "r", encoding="utf-8") as f:
                        ignore_spec = pathspec.PathSpec.from_lines("gitignore", f)
                except Exception as e:
                    print(f"Failed to load .gitignore: {e}")

            # deepcode ignore PathTraversal: This is a local file indexing tool that must walk user-specified trees.
            for dirpath, _, filenames in os.walk(root_path):
                for filename in filenames:
                    if filename.startswith("."):
                        continue
                    
                    file_path = os.path.join(dirpath, filename)
                    
                    if ignore_spec:
                        rel_path = os.path.relpath(file_path, root_path)
                        if ignore_spec.match_file(rel_path):
                            continue

                    try:
                        # Get file mtime
                        file_mtime = os.path.getmtime(file_path)
                        
                        # Check if update needed
                        row = conn.execute(
                            "SELECT mtime FROM documents_meta WHERE path = ?", 
                            (file_path,)
                        ).fetchone()
                        
                        needs_update = True
                        if row:
                            db_mtime = row[0]
                            if file_mtime <= db_mtime:
                                needs_update = False
                        
                        if needs_update:
                            # 1. Read and Tokenize
                            # deepcode ignore PathTraversal: This is a local file indexing tool that must access user-specified files.
                            with open(file_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            
                            tokens = tokenize(content)
                            
                            # 2. Update FTS (Delete old entry if exists, then Insert)
                            conn.execute("DELETE FROM documents_fts WHERE path = ?", (file_path,))
                            conn.execute(
                                "INSERT INTO documents_fts (path, content, tokens) VALUES (?, ?, ?)",
                                (file_path, content, tokens),
                            )
                            updated_count += 1
                        else:
                            skipped_count += 1

                        # 3. Update Metadata (mtime and scanned_at)
                        conn.execute(
                            """
                            INSERT INTO documents_meta (path, mtime, scanned_at) 
                            VALUES (?, ?, ?)
                            ON CONFLICT(path) DO UPDATE SET
                                mtime = excluded.mtime,
                                scanned_at = excluded.scanned_at
                            """,
                            (file_path, file_mtime, current_time)
                        )

                    except UnicodeDecodeError:
                        continue
                    except Exception as e:
                        print(f"Failed to process {file_path}: {e}")

            # 4. Cleanup Stale Entries
            # Delete files under root_path that were NOT scanned in this pass
            # (scanned_at < current_time)
            
            # Prepare LIKE pattern for root_path
            search_pattern = root_path if root_path.endswith(os.sep) else root_path + os.sep
            search_pattern = search_pattern + "%"
            
            # Find stale paths
            cursor = conn.execute(
                """
                SELECT path FROM documents_meta 
                WHERE (path = ? OR path LIKE ?) AND scanned_at < ?
                """,
                (root_path, search_pattern, current_time)
            )
            stale_paths = [r[0] for r in cursor]
            
            for path in stale_paths:
                conn.execute("DELETE FROM documents_fts WHERE path = ?", (path,))
                conn.execute("DELETE FROM documents_meta WHERE path = ?", (path,))
            
            deleted_count = len(stale_paths)

    return f"Indexed {updated_count} files, Skipped {skipped_count} unchanged, Deleted {deleted_count} stale in {root_path}."


@mcp.tool()
def delete_index(root_path: str) -> str:
    """
    Delete all indexed documents under the specified root path.
    """
    root_path = validate_path(root_path)

    context_manager = get_db()
    with context_manager as conn:
        with conn:
            # Using LIKE 'root_path%' to match all subpaths
            # Ensure directory separator is included to avoid partial matches on directory names
            # e.g. /tmp/test matching /tmp/testing
            search_pattern = root_path if root_path.endswith(os.sep) else root_path + os.sep
            search_pattern = search_pattern + "%"
            
            # Also match the exact root path if it's a file
            cursor = conn.execute(
                "DELETE FROM documents_fts WHERE path = ? OR path LIKE ?", 
                (root_path, search_pattern)
            )
            deleted_fts = cursor.rowcount

            cursor = conn.execute(
                "DELETE FROM documents_meta WHERE path = ? OR path LIKE ?", 
                (root_path, search_pattern)
            )
            
            return f"Deleted {deleted_fts} documents under {root_path}"


@mcp.tool()
def search_documents(
    query: str, 
    limit: int = 5, 
    path_filter: str = None, 
    extensions: List[str] = None
) -> List[str]:
    """
    Search for documents matching the Japanese query string.
    Optionally filter by a root path and/or file extensions.
    
    Args:
        query: Japanese search query
        limit: Max results to return
        path_filter: Only return results under this path
        extensions: List of file extensions to include (e.g., [".py", ".md"])
    """
    # Tokenize the query to match the indexed format
    query_tokens = tokenize(query)
    fts_query = query_tokens

    results = []
    with get_db() as conn:
        # XSS Remediation: Use safe placeholders for highlighting, then escape and replace in Python
        sql = """
            SELECT path, snippet(documents_fts, 1, '{{{MATCH}}}', '{{{/MATCH}}}', '...', 64) 
            FROM documents_fts 
            WHERE tokens MATCH ? 
        """
        params = [fts_query]

        if path_filter:
            path_filter = validate_path(path_filter)
            # Ensure proper separator for directory matching
            filter_pattern = path_filter if path_filter.endswith(os.sep) else path_filter + os.sep
            filter_pattern = filter_pattern + "%"
            
            sql += " AND (path = ? OR path LIKE ?)"
            params.extend([path_filter, filter_pattern])

        if extensions:
            # Construct OR clauses for extensions
            # e.g. AND (path LIKE '%.py' OR path LIKE '%.md')
            ext_clauses = []
            for ext in extensions:
                if not ext.startswith("."):
                    ext = "." + ext
                ext_clauses.append("path LIKE ?")
                params.append(f"%{ext}")
            
            if ext_clauses:
                sql += " AND (" + " OR ".join(ext_clauses) + ")"

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        cursor = conn.execute(sql, params)

        for row in cursor:
            path = row[0]
            raw_snippet = row[1]
            
            # Escape the entire string first (sanitizing malicious scripts)
            safe_snippet = html.escape(raw_snippet)
            
            # Restore the highlighting tags
            final_snippet = safe_snippet.replace("{{{MATCH}}}", "<b>").replace("{{{/MATCH}}}", "</b>")
            
            results.append(f"File: {path}\nSnippet: {final_snippet}\n")

    if not results:
        return ["No matches found."]

    return results


@mcp.tool()
def list_indexed_files(limit: int = 100, offset: int = 0) -> List[str]:
    """
    List paths of all indexed files with pagination.
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT path FROM documents_fts ORDER BY path LIMIT ? OFFSET ?",
            (limit, offset)
        )
        files = [row[0] for row in cursor]
    
    return files


@mcp.tool()
def update_file(file_path: str) -> str:
    """
    Update the index for a single file.
    If the file exists, it is re-indexed.
    If the file does not exist, it is removed from the index.
    """
    file_path = validate_path(file_path)
    return _update_or_remove_file(file_path)


@mcp.tool()
def watch_directory(root_path: str) -> str:
    """
    Start watching a directory for changes and automatically update the index.
    """
    root_path = validate_path(root_path)
    if not os.path.exists(root_path):
        return f"Error: Path {root_path} does not exist."
    
    # Load .gitignore
    gitignore_path = os.path.join(root_path, ".gitignore")
    ignore_spec = None
    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8") as f:
                ignore_spec = pathspec.PathSpec.from_lines("gitignore", f)
        except Exception as e:
            print(f"Failed to load .gitignore: {e}")

    handler = FTSHandler(root_path, ignore_spec)
    
    # Check if already watching to avoid duplicates
    if root_path in WATCHED_PATHS:
        return f"Already watching {root_path}"

    WATCHED_PATHS.add(root_path)
    
    global observer
    if observer is None:
        observer = Observer()
        
    try:
        # If observer was stopped (e.g. in tests), we need a new instance
        # Thread/Observer cannot be restarted once stopped.
        # There is no public API to check if it's "stopped" vs "never started" easily without accessing internals
        # or tracking state ourselves. 
        # But commonly if is_alive() is False but we want to schedule, we might need to check.
        if not observer.is_alive():
            try:
                observer.start()
            except RuntimeError:
                # Threads can only be started once. If it was stopped, create new.
                observer = Observer()
                observer.start()

        observer.schedule(handler, root_path, recursive=True)

    except Exception as e:
        # Fallback if something goes wrong
        return f"Failed to start watcher: {e}"
        
    return f"Started watching {root_path} for changes."


def main():
    mcp.run()


if __name__ == "__main__":
    main()
