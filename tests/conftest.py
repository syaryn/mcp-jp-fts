import os
import shutil
import sqlite3

import pytest
from sudachipy import dictionary, tokenizer


# Session-scoped tokenizer to avoid reloading dictionary
@pytest.fixture(scope="session")
def tokenizer_obj():
    return dictionary.Dictionary().create()


@pytest.fixture(scope="session")
def split_mode():
    return tokenizer.Tokenizer.SplitMode.A


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
            scanned_at REAL
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
