import os
import shutil
import sqlite3
from unittest.mock import patch

import pytest
from sudachipy import dictionary, tokenizer


# Session-scoped tokenizer to avoid reloading dictionary
@pytest.fixture(scope="session")
def tokenizer_obj():
    return dictionary.Dictionary().create()


@pytest.fixture(scope="session")
def split_mode():
    return tokenizer.Tokenizer.SplitMode.A


@pytest.fixture(autouse=True)
def mock_cwd(tmp_path):
    """
    Mock os.getcwd() to return the temporary directory for each test.
    This ensures that security checks dependent on CWD pass for temp files.
    """
    with patch("os.getcwd", return_value=str(tmp_path)):
        yield


@pytest.fixture
def temp_db(tmp_path):
    """
    Creates a temporary SQLite DB with FTS5 table setup.
    Returns the path to the DB file.
    """
    db_file = tmp_path / "test_documents.db"

    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE VIRTUAL TABLE documents_fts USING fts5(
            path,
            content,
            tokens,
            tokenize='unicode61'
        );
    """)
    conn.execute("""
        CREATE TABLE documents_meta (
            path TEXT PRIMARY KEY,
            mtime REAL,
            scanned_at REAL,
            token_locations BLOB
        );
    """)
    conn.close()
    return str(db_file)


@pytest.fixture
def resource_dir(tmp_path):
    """
    Copies test resources to a temp directory to avoid modifying source.
    """
    src = os.path.join(os.path.dirname(__file__), "resources")
    dst = tmp_path / "resources"
    shutil.copytree(src, dst)
    return str(dst)
