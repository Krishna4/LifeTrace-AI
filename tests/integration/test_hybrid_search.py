import os
import pytest
from src.backend.database.lancedb_store import LanceDBStore

TEST_LANCE_DIR = "test_lancedb_hybrid_data"


def setup_module():
    if os.path.exists(TEST_LANCE_DIR):
        import shutil
        shutil.rmtree(TEST_LANCE_DIR)


def teardown_module():
    if os.path.exists(TEST_LANCE_DIR):
        import shutil
        shutil.rmtree(TEST_LANCE_DIR)


def test_lancedb_hybrid_search():
    store = LanceDBStore(db_dir=TEST_LANCE_DIR)

    chunks = [
        {"document_id": 1, "chunk_index": 0, "text_content": "Project roadmap for Q3 planning meeting", "source_type": "text"},
        {"document_id": 1, "chunk_index": 1, "text_content": "Invoice payment of $50 to Alex for dinner", "source_type": "text"},
        {"document_id": 2, "chunk_index": 0, "text_content": "Speech audio transcript discussing budget updates", "source_type": "audio"},
    ]

    added = store.add_chunks(chunks)
    assert added == 3

    results = store.hybrid_search("roadmap Q3", limit=2)
    assert len(results) >= 1
    assert "roadmap" in results[0]["text_content"].lower()
