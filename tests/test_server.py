import os
import sqlite3
import sys
from unittest.mock import patch


# Helper to mock the decorator to return the original function
def identity_decorator(func):
    return func

# Patch FastMCP to avoid actual server initialization side effects if any,
# AND ensure the tool decorator preserves the function.
with patch("fastmcp.FastMCP") as MockFastMCP:
    # Configure the instance's tool method to return the identity decorator
    MockFastMCP.return_value.tool.return_value = identity_decorator
    
    # Now import server
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    import server

def test_tokenize(tokenizer_obj, split_mode):
    text = "吾輩は猫である"
    tokens = server.tokenize(text)
    assert "吾輩" in tokens
    assert "猫" in tokens

def test_index_directory_clears_stale_data(temp_db, resource_dir):
    # Patch server.DB_PATH to use temp_db
    with patch("server.DB_PATH", temp_db):
        
        # 1. Initial Index
        result = server.index_directory(resource_dir) # type: ignore
        assert "Indexed" in result
        
        # Verify content
        with sqlite3.connect(temp_db) as conn:
            count = conn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
            assert count >= 2  # wagahai.txt and yukiguni.txt
            
            # Check for specific content
            rows = conn.execute("SELECT path, tokens FROM documents_fts").fetchall()
            paths = [r[0] for r in rows]
            assert any("wagahai.txt" in p for p in paths)

        # 2. Simulate existing stale data (a file that no longer exists in resource_dir)
        stale_path = os.path.join(resource_dir, "stale_file.txt")
        with sqlite3.connect(temp_db) as conn:
            conn.execute(
                "INSERT INTO documents_fts (path, content, tokens) VALUES (?, ?, ?)",
                (stale_path, "stale content", "stale tokens")
            )
            count = conn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
            assert count >= 3

        # 3. Re-index
        result = server.index_directory(resource_dir) # type: ignore
        
        # 4. Verify stale data is gone
        with sqlite3.connect(temp_db) as conn:
            count = conn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
            assert count == 2
            
            rows = conn.execute("SELECT path FROM documents_fts").fetchall()
            paths = [r[0] for r in rows]
            assert not any("stale_file.txt" in p for p in paths)

def test_search_documents(temp_db, resource_dir):
    with patch("server.DB_PATH", temp_db):
        server.index_directory(resource_dir) # type: ignore
        
        results = server.search_documents("猫") # type: ignore
        assert len(results) > 0
        assert any("wagahai.txt" in r for r in results)
        
        results = server.search_documents("雪国") # type: ignore
        assert len(results) > 0
        assert any("yukiguni.txt" in r for r in results)
        
        results = server.search_documents("存在しない言葉") # type: ignore
        assert results == ["No matches found."]

def test_search_tokenization(temp_db, resource_dir):
    with patch("server.DB_PATH", temp_db):
        server.index_directory(resource_dir) # type: ignore
        
        results = server.search_documents("トンネル") # type: ignore 
        assert len(results) > 0
        assert any("yukiguni.txt" in r for r in results)
