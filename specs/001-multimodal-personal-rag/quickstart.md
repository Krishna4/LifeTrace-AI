# Quickstart & Validation Guide: Multimodal Personal RAG

This guide documents runnable validation scenarios to verify end-to-end functionality of the PersonalRag dual-storage architecture, ingestion models, vector indexing, SLM routing, and Streamlit frontend.

---

## 1. Prerequisites

- **Python**: Python >= 3.11
- **System Packages**: `ffmpeg` (required for `faster-whisper` audio decoding)
- **Ollama Engine**: Local Ollama server running `Qwen-2.5-1.5B` model (`ollama run qwen2.5:1.5b`)
- **System Memory**: Minimum 4GB available RAM

---

## 2. Setup & Environment Initialisation

```bash
# 1. Create and activate virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install fastapi uvicorn pydantic lancedb pyarrow sqlite3-api faster-whisper trafilatura sentence-transformers streamlit requests
```

---

## 3. Server Launch

```bash
# Launch FastAPI backend server on port 8000
uvicorn src.backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 4. End-to-End Validation Scenarios

### Scenario 1: System Health & Memory Footprint Verification

Verify that the system is running healthy and total RAM footprint remains under 4GB.

```bash
curl -s http://localhost:8000/api/v1/health | jq .
```
**Expected Response**:
```json
{
  "status": "healthy",
  "ram_usage_mb": 420.5,
  "max_memory_ceiling_mb": 4096.0,
  "ollama_status": "connected"
}
```

---

### Scenario 2: Web Ingestion & Monetary Extraction

Ingest a web article containing monetary mentions and verify structured SQLite insertion and LanceDB vector indexing.

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest/url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/receipt-note"}' | jq .
```

---

### Scenario 3: Audio Transcription & Transaction Parsing

Upload an audio note ("paid Alex $50 for dinner yesterday") and verify speech transcription and SQLite transaction record generation.

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest/file \
  -F "file=@tests/fixtures/sample_payment_note.mp3" | jq .
```

---

### Scenario 4: Natural Language Query via SLM Router

Submit a financial spending question and verify that the SLM router directs execution to the `SQL` engine.

```bash
curl -s -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How much money did I pay Alex?"}' | jq .
```
**Expected Response**:
```json
{
  "query": "How much money did I pay Alex?",
  "target_engine": "SQL",
  "answer": "You paid Alex a total of $50.00 USD on 2026-08-08.",
  "transactions": [
    {
      "id": 1,
      "entity_person": "Alex",
      "amount": 50.0,
      "currency": "USD",
      "transaction_date": "2026-08-08",
      "notes": "dinner"
    }
  ]
}
```

---

### Scenario 5: Streamlit Frontend Chat UI Launch

```bash
# Launch Streamlit frontend application
streamlit run src/frontend/app.py
```
Open browser at `http://localhost:8501` to test interactive file upload, query input, and response visualization.
