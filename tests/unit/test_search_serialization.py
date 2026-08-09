import pytest
from fastapi.encoders import jsonable_encoder
from src.backend.models.pydantic_schemas import QueryRouteResponse
from src.backend.query.search_engine import execute_unified_search, _sanitize_chunk

def test_sanitize_chunk():
    class DummyScalar:
        def item(self):
            return 42

    raw_chunk = {
        "id": "abc-123",
        "document_id": DummyScalar(),
        "text_content": "Test chunk payload",
        "vector": [0.1, 0.2, 0.3],
        "_rowid": 1,
    }

    clean = _sanitize_chunk(raw_chunk)
    assert clean["document_id"] == 42
    assert "vector" not in clean
    assert "_rowid" not in clean

    # Ensure jsonable_encoder serializes cleanly
    encoded = jsonable_encoder(clean)
    assert encoded["document_id"] == 42


def test_execute_unified_search_json_encodable():
    route = QueryRouteResponse(
        query="Show me roadmap planning",
        target_engine="HYBRID",
        confidence=0.9,
        vector_terms="roadmap planning",
        sql_query=None,
        rationale="Hybrid RAG query",
    )

    res = execute_unified_search("Show me roadmap planning", route)
    # Must not raise TypeError / ValueError in FastAPI serialization
    encoded = jsonable_encoder(res)
    assert encoded["target_engine"] == "HYBRID"
    assert isinstance(encoded["retrieved_chunks"], list)
