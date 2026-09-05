import pytest
from unittest.mock import patch, MagicMock
from datetime import date
from src.backend.llm.llm_client import LLMClient, configure_llm_client
from src.backend.database.lancedb_store import LanceDBStore
from src.backend.database.sqlite import (
    init_db,
    create_transaction,
    get_transactions,
    get_total_amount_by_entity,
    create_personal_event,
    get_personal_events,
    create_document,
    get_all_documents,
)
from src.backend.models.pydantic_schemas import (
    FinancialTransactionCreate,
    PersonalEventCreate,
    DocumentCreate,
    QueryRouteResponse,
)
from src.backend.query.search_engine import execute_unified_search


def test_llm_client_openrouter_generation():
    """Test LLMClient calling OpenRouter API with mock."""
    client = LLMClient(
        provider="OPENROUTER",
        openrouter_api_key="test-key",
        openrouter_model="meta-llama/llama-3.3-70b-instruct:free",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Hello from Llama 3.3 70B on OpenRouter!"}}]
    }

    with patch("requests.post", return_value=mock_resp):
        res = client.generate(prompt="Hello")
        assert res == "Hello from Llama 3.3 70B on OpenRouter!"
        assert client.get_effective_provider() == "OPENROUTER"


def test_llm_client_fallback_to_ollama_on_openrouter_failure():
    """Test LLMClient falls back to Ollama if OpenRouter fails."""
    client = LLMClient(
        provider="OPENROUTER",
        openrouter_api_key="test-key",
        ollama_model="qwen2.5:1.5b",
    )

    def side_effect(url, **kwargs):
        if "openrouter.ai" in url:
            bad = MagicMock()
            bad.status_code = 429
            bad.text = "Rate Limit"
            return bad
        else:
            good = MagicMock()
            good.status_code = 200
            good.json.return_value = {"response": "Fallback from local Ollama"}
            return good

    with patch("requests.post", side_effect=side_effect), \
         patch("src.backend.llm.llm_client.is_ollama_online", return_value=True):
        res = client.generate(prompt="Test fallback")
        assert res == "Fallback from local Ollama"


def test_multi_user_sqlite_isolation(tmp_path):
    """Test that SQLite queries for Alice never return Bob's transactions or events."""
    db_path = str(tmp_path / "test_rag.db")
    init_db(db_path)

    # 1. Create records for Alice
    create_transaction(
        FinancialTransactionCreate(
            entity_person="Venu",
            amount=500.0,
            currency="USD",
            transaction_date=date.today(),
            username="Alice",
            is_secure=False,
        ),
        db_path=db_path,
    )
    create_personal_event(
        PersonalEventCreate(
            title="Meeting with Bob",
            category="MEETING",
            event_date=date.today(),
            username="Alice",
            is_secure=False,
        ),
        db_path=db_path,
    )

    # 2. Create records for Bob
    create_transaction(
        FinancialTransactionCreate(
            entity_person="Venu",
            amount=2000.0,
            currency="USD",
            transaction_date=date.today(),
            username="Bob",
            is_secure=True,
        ),
        db_path=db_path,
    )
    create_personal_event(
        PersonalEventCreate(
            title="Secret Project Launch",
            category="MILESTONE",
            event_date=date.today(),
            username="Bob",
            is_secure=True,
        ),
        db_path=db_path,
    )

    # 3. Query as Alice
    alice_txs = get_transactions(username="Alice", db_path=db_path)
    assert len(alice_txs) == 1
    assert alice_txs[0].amount == 500.0
    assert alice_txs[0].username == "Alice"

    alice_total = get_total_amount_by_entity("Venu", username="Alice", db_path=db_path)
    assert alice_total["total_amount"] == 500.0

    alice_events = get_personal_events(username="Alice", db_path=db_path)
    assert len(alice_events) == 1
    assert alice_events[0].title == "Meeting with Bob"

    # 4. Query as Bob
    bob_txs = get_transactions(username="Bob", db_path=db_path)
    assert len(bob_txs) == 1
    assert bob_txs[0].amount == 2000.0

    bob_total = get_total_amount_by_entity("Venu", username="Bob", db_path=db_path)
    assert bob_total["total_amount"] == 2000.0

    # 5. Bob queries excluding secure records
    bob_insecure_txs = get_transactions(username="Bob", include_secure=False, db_path=db_path)
    assert len(bob_insecure_txs) == 0


def test_multi_user_lancedb_vector_isolation(tmp_path):
    """Test that LanceDB vector and BM25 search strictly isolate chunks by username."""
    lance_dir = str(tmp_path / "test_lancedb")
    store = LanceDBStore(db_dir=lance_dir)

    chunks = [
        {
            "id": "c1",
            "document_id": 1,
            "chunk_index": 0,
            "text_content": "Alice's top secret strategy document for Project Alpha",
            "username": "Alice",
            "is_secure": True,
            "source_type": "pdf",
        },
        {
            "id": "c2",
            "document_id": 2,
            "chunk_index": 0,
            "text_content": "Bob's public guide on gardening and herbs",
            "username": "Bob",
            "is_secure": False,
            "source_type": "docx",
        },
    ]
    store.add_chunks(chunks)

    # Bob searches for "Project Alpha" or "strategy"
    bob_results = store.hybrid_search("Project Alpha strategy", username="Bob")
    # Bob must NOT find Alice's chunks
    for r in bob_results:
        assert r["username"] == "Bob"
    assert not any("Project Alpha" in r.get("text_content", "") for r in bob_results)

    # Alice searches for "Project Alpha"
    alice_results = store.hybrid_search("Project Alpha", username="Alice", include_secure=True)
    assert len(alice_results) > 0
    assert alice_results[0]["username"] == "Alice"
    assert "Project Alpha" in alice_results[0]["text_content"]

    # Alice searches with include_secure=False
    alice_no_sec = store.hybrid_search("Project Alpha", username="Alice", include_secure=False)
    assert len(alice_no_sec) == 0
