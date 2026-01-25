import contextlib
import html
import os
import sqlite3
import struct
import time
import sys
from typing import List, Tuple, Any

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


def tokenize(text: str) -> List[Tuple[str, int]]:
    """
    Tokenize text using SudachiPy and return list of (surface, byte_offset) tuples.
    """
    tokens = tokenizer_obj.tokenize(text, mode)
    # SudachiPy returns character offsets (m.begin()).
    # We need UTF-8 byte offsets for file seeking and FTS mapping.
    # Recalculating byte offsets based on the utf-8 encoded text.
    
    results = []
    current_byte_offset = 0
    current_char_offset = 0
    
    for m in tokens:
        surface = m.surface()
        # Calculate bytes skipped since last token (e.g. spaces)
        # However, SudachiPy usually returns contiguous tokens unless we skip something.
        # But to be safe, we use substring from last char position to current token start.
        # Wait, m.begin() is absolute char index.
        
        # Optimization: Maintain running char/byte count if tokens are sequential.
        # But simpler: Get substring from 0 to m.begin(), encode, len().
        # For non-sequential access it could be slow O(N^2), but tokens are sequential.
        
        # Efficient approach:
        # We know tokens come in order.
        skipped_text = text[current_char_offset:m.begin()]
        current_byte_offset += len(skipped_text.encode("utf-8"))
        
        results.append((surface, current_byte_offset))
        
        surface_len_bytes = len(surface.encode("utf-8"))
        current_byte_offset += surface_len_bytes
        current_char_offset = m.end()
        
    return results


def validate_path(path: str) -> str:
    """
    Validate and resolve path to absolute path.
    Prevents basics of path traversal by ensuring we work with resolved paths.
    """
    # Simply resolving to absolute path is the main requirement for this local tool.
    # Snyk might still flag this as "Path Traversal" because we allow arbitrary file reads,
    # but that is the intended feature of this tool.
    abs_path = os.path.abspath(path)
    cwd = os.getcwd()
    
    # Robust check using commonpath to avoid prefix tampering (like /foo vs /foobar)
    try:
        if os.path.commonpath([cwd, abs_path]) != cwd:
            raise ValueError(f"Access denied: Path {path} is outside the current working directory.")
    except ValueError:
        # commonpath can raise ValueError on Windows if mixed drives are used
        raise ValueError(f"Access denied: Path {path} is invalid/outside CWD.")

    return abs_path


# Database Helper
DB_PATH = "documents.db"


@contextlib.contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection):
    """Initialize the SQLite database with a FTS5 virtual table."""
    with conn:
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
                scanned_at REAL,
                token_locations BLOB
            );
        """)
        
        # Migration: Add token_locations column if it doesn't exist (for existing DBs)
        try:
            conn.execute("ALTER TABLE documents_meta ADD COLUMN token_locations BLOB")
        except sqlite3.OperationalError:
            # Column likely already exists
            pass
        # Enable Write-Ahead Logging (WAL) for better concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        # Note: 'unicode61' is the default tokenizer which works well with space-separated tokens




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
                
                # Tokenize and get offsets
                token_data = tokenize(content)
                token_surfaces = [t[0] for t in token_data]
                token_offsets = [t[1] for t in token_data]
                
                # Join tokens for FTS
                tokens_str = " ".join(token_surfaces)
                
                # Pack offsets only (unsigned int, 4 bytes)
                # 'I' is unsigned int (4 bytes). We use big-endian or native? Standard 'I' is typically 4 bytes.
                # Use '<' for little-endian or '>' for big-endian to be explicit? '<' is standard for generic data.
                # Actually, simple 'I'*len is fine if consistent.
                packed_offsets = struct.pack(f"<{len(token_offsets)}I", *token_offsets)
                
                # 2. Update FTS
                conn.execute("DELETE FROM documents_fts WHERE path = ?", (file_path,))
                conn.execute(
                    "INSERT INTO documents_fts (path, content, tokens) VALUES (?, ?, ?)",
                    (file_path, content, tokens_str),
                )
                
                # 3. Update Metadata
                file_mtime = os.path.getmtime(file_path)
                conn.execute(
                    """
                    INSERT INTO documents_meta (path, mtime, scanned_at, token_locations) 
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime = excluded.mtime,
                        scanned_at = excluded.scanned_at,
                        token_locations = excluded.token_locations
                    """,
                    (file_path, file_mtime, current_time, packed_offsets)
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
        # We manually manage transactions (commits) inside the loop for concurrency
        # with conn:  <-- Removed monolithic transaction block
            # Load .gitignore if exists
            gitignore_path = os.path.join(root_path, ".gitignore")
            ignore_spec = None
            if os.path.exists(gitignore_path):
                try:
                    with open(gitignore_path, "r", encoding="utf-8") as f:
                        ignore_spec = pathspec.PathSpec.from_lines("gitignore", f)
                except Exception as e:
                    print(f"Failed to load .gitignore: {e}", file=sys.stderr)

            # deepcode ignore PathTraversal: This is a local file indexing tool that must walk user-specified trees.
            current_files = set()
            
            # Batch commit configuration
            BATCH_SIZE = 50
            pending_updates = 0

            for dirpath, _, filenames in os.walk(root_path):
                for filename in filenames:
                    if filename.startswith("."):
                        continue
                    
                    file_path = os.path.join(dirpath, filename)
                    
                    if ignore_spec:
                        rel_path = os.path.relpath(file_path, root_path)
                        if ignore_spec.match_file(rel_path):
                            continue
                            
                    # Add to current_files set
                    abs_path = validate_path(file_path)
                    current_files.add(abs_path)

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
                            # deepcode ignore PathTraversal: Validated path access
                            with open(file_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            
                            # Tokenize and get offsets
                            token_data = tokenize(content)
                            token_surfaces = [t[0] for t in token_data]
                            token_offsets = [t[1] for t in token_data]
                            
                            tokens_str = " ".join(token_surfaces)
                            packed_offsets = struct.pack(f"<{len(token_offsets)}I", *token_offsets)
                            
                            # 2. Update FTS (Delete old entry if exists, then Insert)
                            with conn:
                                conn.execute("DELETE FROM documents_fts WHERE path = ?", (file_path,))
                                conn.execute(
                                    "INSERT INTO documents_fts (path, content, tokens) VALUES (?, ?, ?)",
                                    (file_path, content, tokens_str),
                                )
                                # 3. Update Metadata (mtime and scanned_at)
                                conn.execute(
                                    """
                                    INSERT INTO documents_meta (path, mtime, scanned_at, token_locations) 
                                    VALUES (?, ?, ?, ?)
                                    ON CONFLICT(path) DO UPDATE SET
                                        mtime = excluded.mtime,
                                        scanned_at = excluded.scanned_at,
                                        token_locations = excluded.token_locations
                                    """,
                                    (file_path, file_mtime, current_time, packed_offsets)
                                )
                            updated_count += 1

                        else:
                            skipped_count += 1
                            # Update scanned_at even if skipped, so it's not marked as stale
                            with conn:
                                conn.execute(
                                    "UPDATE documents_meta SET scanned_at = ? WHERE path = ?",
                                    (current_time, file_path)
                                )
                        
                        # Use explicit transaction commit for batches to avoid locking the DB for too long
                        if updated_count > 0 and updated_count % BATCH_SIZE == 0:
                            conn.commit()

                    except UnicodeDecodeError:
                        continue
                    except Exception as e:
                        print(f"Failed to process {file_path}: {e}", file=sys.stderr)
            
            # Commit any remaining updates
            conn.commit()

            # 4. Cleanup Stale Entries
            # Delete files under root_path that were NOT scanned in this pass
            # (scanned_at < current_time)
            
            # Prepare LIKE pattern for root_path
            search_pattern = root_path if root_path.endswith(os.sep) else root_path + os.sep
            search_pattern = search_pattern + "%"
            
            # Cleanup stale entries atomically and efficiently
            with conn:
                conn.execute(
                    """
                    DELETE FROM documents_fts
                    WHERE path IN (SELECT path FROM documents_meta WHERE (path = ? OR path LIKE ?) AND scanned_at < ?)
                    """,
                    (root_path, search_pattern, current_time)
                )
                cursor_meta = conn.execute(
                    "DELETE FROM documents_meta WHERE (path = ? OR path LIKE ?) AND scanned_at < ?",
                    (root_path, search_pattern, current_time)
                )
                conn.commit()
            
            deleted_count = cursor_meta.rowcount

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
    query_token_data = tokenize(query)
    # Extract surfaces for FTS MATCH query
    # Sanitize tokens: Escape double quotes and wrap in double quotes to treat as string literals
    # This prevents FTS5 syntax injection (e.g. *, OR, NEAR, :, etc. inside words)
    safe_surfaces = []
    for t in query_token_data:
        surface = t[0]
        # Escape existing quotes by doubling them (standard SQL/FTS escaping)
        surface_escaped = surface.replace('"', '""')
        safe_surfaces.append(f'"{surface_escaped}"')
    
    if not safe_surfaces:
        return ["No matches found."]

    fts_query = " ".join(safe_surfaces)

    with get_db() as conn:
        # XSS Remediation: Use safe placeholders for highlighting, then escape and replace in Python
        # Use offsets() to find which token matched
        # Note: We fetch tokens to count spaces for term index
        sql = """
            SELECT 
                path, 
                snippet(documents_fts, 1, '{{{MATCH}}}', '{{{/MATCH}}}', '...', 64), 
                highlight(documents_fts, 2, '{{{MATCH}}}', '{{{/MATCH}}}'), 
                tokens
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
        rows = list(cursor)
        
        if not rows:
            return ["No matches found."]
            
        # Fetch Token Maps for the found paths
        # Optimization: Batch fetch
        found_paths = [r[0] for r in rows]
        placeholders = ",".join(["?"] * len(found_paths))
        meta_cursor = conn.execute(
            f"SELECT path, token_locations FROM documents_meta WHERE path IN ({placeholders})",
            found_paths
        )
        token_map_lookup = {r[0]: r[1] for r in meta_cursor}

        results = []
        for row in rows:
            path = row[0]
            raw_snippet = row[1]
            highlighted_tokens = row[2]
            tokens_str = row[3]
            
            # Lookup token locations map
            token_locations_blob = token_map_lookup.get(path)
            
            # Escape the entire string first (sanitizing malicious scripts)
            safe_snippet = html.escape(raw_snippet)
            
            # Restore the highlighting tags
            final_snippet = safe_snippet.replace("{{{MATCH}}}", "<b>").replace("{{{/MATCH}}}", "</b>")
            
            # Calculate Line Number using Token Map Strategy via Highlight
            line_number = 1
            try:
                # highlighted_tokens contains "A B {{{MATCH}}}Target{{{/MATCH}}} C"
                # We find the first {{{MATCH}}} in highlighted_tokens
                # Then count spaces before it.
                
                match_start = highlighted_tokens.find("{{{MATCH}}}")
                if match_start != -1 and token_locations_blob:
                    preceding_text_highlighted = highlighted_tokens[:match_start]
                    
                    # preceding_text might contain match tags?
                    # Since we found FIRST match, no match tags before it.
                    # Just count spaces.
                    token_index = preceding_text_highlighted.count(" ")
                    
                    # Unpack blob
                    count = len(token_locations_blob) // 4
                    if token_index < count:
                        original_byte_offset = struct.unpack_from("<I", token_locations_blob, offset=token_index*4)[0]
                        
                        # deepcode ignore PathTraversal: Validated path access
                        # deepcode ignore PathTraversal: Validated path access
                        try:
                            # Use binary mode to read exact byte offset as determined by token map
                            with open(path, "rb") as f:
                                f.seek(0)
                                prefix_bytes = f.read(original_byte_offset)
                                line_number = prefix_bytes.count(b"\n") + 1
                        except (IOError, OSError, ValueError):
                            # File might have changed or been deleted since search match
                            # Fallback to line 1 or handle gracefully
                            line_number = 1

            except Exception:
                pass
            
            results.append(f"File: {path}:{line_number}\nSnippet: {final_snippet}\n")

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
            print(f"Failed to load .gitignore: {e}", file=sys.stderr)

    handler = FTSHandler(root_path, ignore_spec)
    
    global observer
    
    # Check observer health first
    # If observer is dead, we must restart it AND clear the watched paths,
    # because the new observer has no schedules.
    if observer is None or not observer.is_alive():
        if observer is not None:
             # Observer existed but died. Clear stale state.
             WATCHED_PATHS.clear()
        
        # Create new observer
        observer = Observer()
        
    # Check if already watching to avoid duplicates
    if root_path in WATCHED_PATHS:
        return f"Already watching {root_path}"

    WATCHED_PATHS.add(root_path)
        
    try:
        if not observer.is_alive():
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
