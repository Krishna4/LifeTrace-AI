import os
import pytest
from fastapi.testclient import TestClient
from src.backend.main import app

client = TestClient(app)


def test_batch_file_ingestion():
    files = [
        ("files", ("batch_doc1.txt", b"First batch text content", "text/plain")),
        ("files", ("batch_doc2.txt", b"Second batch text content", "text/plain")),
    ]
    response = client.post("/api/v1/ingest/files", files=files)
    assert response.status_code == 202
    data = response.json()
    assert data["enqueued_count"] == 2
    assert len(data["jobs"]) == 2
    assert data["jobs"][0]["filename"] == "batch_doc1.txt"
    assert data["jobs"][1]["filename"] == "batch_doc2.txt"
