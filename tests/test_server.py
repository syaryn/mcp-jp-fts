import os
import sqlite3
import time
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
    # sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from mcp_jp_fts import server


def test_tokenize(tokenizer_obj, split_mode):
    text = "吾輩は猫である"
    tokens = server.tokenize(text)
    assert "吾輩" in tokens
    assert "猫" in tokens


def test_index_directory_clears_stale_data(temp_db, resource_dir):
    # Patch server.DB_PATH to use temp_db
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        # 1. Initial Index
        result = server.index_directory(resource_dir)  # type: ignore
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
                (stale_path, "stale content", "stale tokens"),
            )
            # Also insert into meta with old timestamp
            conn.execute(
                "INSERT INTO documents_meta (path, mtime, scanned_at) VALUES (?, ?, ?)",
                (stale_path, 1000.0, 1000.0)
            )
            count = conn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
            assert count >= 3

        # 3. Re-index
        result = server.index_directory(resource_dir)  # type: ignore

            # 4. Verify stale data is gone
        with sqlite3.connect(temp_db) as conn:
            count = conn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
            assert count == 4

            rows = conn.execute("SELECT path FROM documents_fts").fetchall()
            paths = [r[0] for r in rows]
            assert not any("stale_file.txt" in p for p in paths)


def test_search_documents(temp_db, resource_dir):
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        server.index_directory(resource_dir)  # type: ignore

        results = server.search_documents("猫")  # type: ignore
        assert len(results) > 0
        assert any("wagahai.txt" in r for r in results)

        results = server.search_documents("雪国")  # type: ignore
        assert len(results) > 0
        assert any("yukiguni.txt" in r for r in results)

        results = server.search_documents("存在しない言葉")  # type: ignore
        assert results == ["No matches found."]


def test_search_tokenization(temp_db, resource_dir):
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        server.index_directory(resource_dir)  # type: ignore

        results = server.search_documents("トンネル")  # type: ignore
        assert len(results) > 0
        assert any("yukiguni.txt" in r for r in results)


def test_delete_index(temp_db, resource_dir):
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        # Index everything
        server.index_directory(resource_dir) # type: ignore
        
        # Verify indexed
        with sqlite3.connect(temp_db) as conn:
             count_before = conn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
             assert count_before >= 2

        # Delete from a specific subdirectory (if we had one) or the whole thing
        # Let's delete the whole resource_dir
        result = server.delete_index(resource_dir) # type: ignore
        assert "Deleted" in result

        # Verify empty
        with sqlite3.connect(temp_db) as conn:
             count_after = conn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
             assert count_after == 0


def test_search_documents_with_filter(temp_db, resource_dir):
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        server.index_directory(resource_dir) # type: ignore
        
        # Test query "猫" (exists in root wagahai.txt)
        query = "猫"
        
        # 1. Filter with root path should return results (recursive)
        results = server.search_documents(query, path_filter=resource_dir) # type: ignore
        assert len(results) > 0
        assert any("wagahai.txt" in r for r in results)
        
        # 2. Filter with non-matching path should return no results
        dummy_path = os.path.join(os.path.dirname(resource_dir), "non_existent_dir")
        results = server.search_documents(query, path_filter=dummy_path) # type: ignore
        assert results == ["No matches found."]
        
        # 3. Test subdirectory filtering
        # "カムパネルラ" is in subdir1/ginga.txt
        # "先生" is in subdir2/kokoro.txt
        
        # Search for "カムパネルラ" with filter=subdir1 -> should find
        subdir1 = os.path.join(resource_dir, "subdir1")
        results = server.search_documents("カムパネルラ", path_filter=subdir1) # type: ignore
        assert len(results) > 0
        assert any("ginga.txt" in r for r in results)
        
        # Search for "カムパネルラ" with filter=subdir2 -> should NOT find
        subdir2 = os.path.join(resource_dir, "subdir2")
        results = server.search_documents("カムパネルラ", path_filter=subdir2) # type: ignore
        assert results == ["No matches found."]

def test_delete_index_subdirectory(temp_db, resource_dir):
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        server.index_directory(resource_dir) # type: ignore
        
        subdir1 = os.path.join(resource_dir, "subdir1")
        
        # Verify subdir1 content is indexed
        results = server.search_documents("カムパネルラ") # type: ignore
        assert any("ginga.txt" in r for r in results)
        
        # Delete only subdir1 index
        server.delete_index(subdir1) # type: ignore
        
        # Verify subdir1 content is gone
        results = server.search_documents("カムパネルラ") # type: ignore
        assert results == ["No matches found."]
        
        # Verify other content still exists (e.g. root files or subdir2)
        results = server.search_documents("先生") # type: ignore (in subdir2)
        assert any("kokoro.txt" in r for r in results)


def test_list_indexed_files(temp_db, resource_dir):
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        server.index_directory(resource_dir) # type: ignore
        
        files = server.list_indexed_files() # type: ignore
        assert len(files) >= 4 # wagahai, yukiguni, ginga, kokoro
        
        # Check presence of all files including subdirs
        basenames = [os.path.basename(f) for f in files]
        assert "wagahai.txt" in basenames
        assert "yukiguni.txt" in basenames
        assert "ginga.txt" in basenames
        assert "kokoro.txt" in basenames
        
        # Pagination check
        files_limited = server.list_indexed_files(limit=1) # type: ignore
        assert len(files_limited) == 1


def test_index_respects_gitignore(temp_db, resource_dir):
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        # Create .gitignore
        gitignore_path = os.path.join(resource_dir, ".gitignore")
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write("*.tmp\nignore_me.txt\n")
            
        # Create ignored files
        with open(os.path.join(resource_dir, "test.tmp"), "w") as f:
            f.write("ignored content")
        with open(os.path.join(resource_dir, "ignore_me.txt"), "w") as f:
            f.write("ignored content")
            
        # Create normal file
        with open(os.path.join(resource_dir, "normal.txt"), "w") as f:
            f.write("normal content")
            
        # Index
        server.index_directory(resource_dir) # type: ignore
        
        # Verify
        files = server.list_indexed_files() # type: ignore
        basenames = [os.path.basename(f) for f in files]
        
        assert "normal.txt" in basenames
        assert "test.tmp" not in basenames
        assert "ignore_me.txt" not in basenames
        assert "wagahai.txt" in basenames # existing content


def test_search_extension_filtering(temp_db, resource_dir):
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        # Create dummy files with different extensions
        with open(os.path.join(resource_dir, "test.py"), "w") as f:
            f.write("def func(): pass\n# 猫がいる")
        with open(os.path.join(resource_dir, "test.md"), "w") as f:
            f.write("# 猫について")
        with open(os.path.join(resource_dir, "test.txt"), "w") as f:
            f.write("猫のメモ")
            
        # Index
        server.index_directory(resource_dir) # type: ignore
        
        query = "猫"
        
        # 1. Filter by .py
        results_py = server.search_documents(query, extensions=[".py"]) # type: ignore
        assert len(results_py) > 0
        assert all(".py" in r for r in results_py)
        assert not any(".md" in r for r in results_py)
        assert not any(".txt" in r for r in results_py)
        
        # 2. Filter by .md
        results_md = server.search_documents(query, extensions=["md"]) # type: ignore # test normalization
        assert len(results_md) > 0
        assert all(".md" in r for r in results_md)
        
        # 3. Filter by .py and .md
        results_multi = server.search_documents(query, extensions=[".py", ".md"]) # type: ignore
        assert len(results_multi) > 0
        assert any(".py" in r for r in results_multi)
        assert any(".md" in r for r in results_multi)
        assert not any(".txt" in r for r in results_multi)

        assert any(".md" in r for r in results_multi)
        assert not any(".txt" in r for r in results_multi)

def test_incremental_indexing(temp_db, tmp_path):
    # Use a clean directory for this test
    clean_dir = str(tmp_path / "clean_resources")
    os.makedirs(clean_dir)
    
    with patch("mcp_jp_fts.server.DB_PATH", temp_db):
        file_a = os.path.join(clean_dir, "a.txt")
        file_b = os.path.join(clean_dir, "b.txt")
        
        # 1. Initial State: a.txt, b.txt
        with open(file_a, "w") as f: f.write("content A")
        with open(file_b, "w") as f: f.write("content B")
        
        # Initial Index
        res1 = server.index_directory(clean_dir) # type: ignore
        assert "Indexed 2 files" in res1
        
        # 2. Modify State: 
        # - a.txt: Modified
        # - b.txt: Deleted
        # - c.txt: Added
        
        time.sleep(1.1) # Ensure mtime changes significantly
        with open(file_a, "w") as f: f.write("content A modified")
        os.remove(file_b)
        file_c = os.path.join(clean_dir, "c.txt")
        with open(file_c, "w") as f: f.write("content C")
        
        # Incremental Index
        res2 = server.index_directory(clean_dir) # type: ignore
        
        # Verify Output String
        # Should be: Indexed 2 files (a and c), Skipped 0 files, Deleted 1 files (b)
        # Wait... "Indexed" count includes updated(a) and new(c).
        # "Skipped" should be 0 (no other files).
        # "Deleted" should be 1 (b).
        assert "Indexed 2" in res2
        assert "Deleted 1" in res2
        
        # Verify DB Content
        files = server.list_indexed_files() # type: ignore
        basenames = [os.path.basename(f) for f in files]
        
        assert "a.txt" in basenames
        assert "c.txt" in basenames
        assert "b.txt" not in basenames
        
        # Verify Content Update
        results = server.search_documents("modified") # type: ignore
        assert any("a.txt" in r for r in results)
        
        # 3. No Change Scan
        res3 = server.index_directory(clean_dir) # type: ignore
        assert "Indexed 0" in res3
        assert "Skipped 2" in res3 # a and c
        assert "Deleted 0" in res3
