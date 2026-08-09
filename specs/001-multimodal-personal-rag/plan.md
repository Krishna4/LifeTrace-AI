# Implementation Plan: Multimodal Personal RAG System

**Branch**: `001-multimodal-personal-rag` | **Date**: 2026-08-09 | **Spec**: [spec.md](file:///Users/muralidupati/PersonalRag/specs/001-multimodal-personal-rag/spec.md)

**Input**: Architect a dual-storage RAG system with SQLite transaction schema (`id`, `entity_person`, `amount`, `currency`, `transaction_date`, `notes`), LanceDB disk-backed Arrow tables (`bge-small-en-v1.5`), ingestion routing pipeline (`faster-whisper`, `Florence-2`, `Trafilatura`), SLM query router, FastAPI backend, and Streamlit frontend.

---

## Summary

Build a low-resource multimodal Personal RAG system operating strictly within a **max 4GB RAM total memory ceiling**. The architecture pairs an embedded SQLite database (for structured financial transactions and metadata) with LanceDB columnar disk-backed Arrow tables (for 384-dimensional `bge-small-en-v1.5` dense vectors + BM25 sparse hybrid search). The system ingests files up to 500MB (audio/video via `faster-whisper`, images via `Florence-2-base`, web pages via `Trafilatura`), parses monetary statements into SQLite, and routes questions using local `Qwen-2.5-1.5B` via Ollama into SQL, Vector, or Hybrid execution paths.

---

## Technical Context

- **Language/Version**: Python 3.11+
- **Primary Dependencies**: FastAPI, Pydantic v2, LanceDB, PyArrow, faster-whisper, Trafilatura, transformers/Florence-2, Streamlit, Ollama client
- **Storage**: SQLite (`transactions`, `documents`, `vector_sync_logs`) + LanceDB (`text_chunks` Arrow table)
- **Testing**: `pytest`, `pytest-asyncio`, `mypy` (strict mode), `ruff`
- **Target Platform**: macOS / Linux local system
- **Project Type**: Web Application (FastAPI REST backend + Streamlit frontend)
- **Performance Goals**: Sub-3-second search query response time; 90%+ router accuracy
- **Constraints**: **Max 4GB RAM ceiling** (Constitution Principle I); **500MB maximum single file upload** (`FR-014`)

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle / Rule | Compliance Status | Rationale |
| :--- | :---: | :--- |
| **I. Memory Limit (<= 4GB RAM)** | **PASS** | Disk-backed LanceDB Arrow tables, quantized `faster-whisper` (tiny), and sequential model execution with garbage collection keep total RAM < 2.5GB. |
| **II. Python 3.11+ Baseline** | **PASS** | Source code enforces Python 3.11 syntax, modern type hints (`type`, `Self`), and async execution. |
| **III. Pydantic v2 Schemas** | **PASS** | All API contracts, SQLite inserts, and vector payloads use frozen Pydantic v2 schemas (`FinancialTransactionCreate`, `QueryRouteResponse`). |
| **IV. Dynamic Query Routing** | **PASS** | Local SLM router (`Qwen-2.5-1.5B`) dynamically classifies incoming queries to SQL, Vector, or Hybrid routes. |
| **V. Test-Driven Development (TDD)** | **PASS** | Test suites in `tests/` cover unit, integration, and routing paths prior to implementation phase. |

---

## Project Structure

```text
/Users/muralidupati/PersonalRag/
├── specs/001-multimodal-personal-rag/
│   ├── spec.md                  # Feature specification
│   ├── plan.md                  # Implementation plan (this file)
│   ├── research.md              # Phase 0 technical decisions
│   ├── data-model.md            # Phase 1 SQLite, LanceDB, & Pydantic models
│   ├── quickstart.md            # Phase 1 validation guide
│   └── contracts/
│       └── api-spec.json        # FastAPI OpenAPI endpoint schema
├── src/
│   ├── backend/
│   │   ├── database/
│   │   │   ├── sqlite.py        # SQLite schema initialization & transactions DAO
│   │   │   └── lancedb_store.py # LanceDB Arrow table vector store & BM25 hybrid search
│   │   ├── models/
│   │   │   └── pydantic_schemas.py # Pydantic v2 data models
│   │   ├── ingestion/
│   │   │   ├── router.py        # Ingestion routing engine & file size validator
│   │   │   ├── whisper_transcriber.py # faster-whisper speech-to-text integration
│   │   │   ├── florence_ocr.py  # Florence-2 image captioning & OCR
│   │   │   ├── trafilatura_extractor.py # Web URL text cleaner
│   │   │   └── financial_parser.py # Monetary transaction extractor into SQLite
│   │   ├── query/
│   │   │   ├── slm_router.py    # Qwen-2.5-1.5B Ollama query classifier
│   │   │   └── search_engine.py # Unified SQL / Vector / Hybrid retriever
│   │   └── main.py              # FastAPI server entry point
│   └── frontend/
│       └── app.py               # Streamlit chat interface
└── tests/
    ├── unit/
    └── integration/
```

---

## Proposed Changes & Components

### Component 1: Core Database Layer (`src/backend/database/`)

#### [NEW] `sqlite.py`
- Initialize SQLite schema (`transactions`, `documents`, `vector_sync_logs`).
- Provide CRUD DAO for transactions (`id`, `entity_person`, `amount`, `currency`, `transaction_date`, `notes`) and document sync states.

#### [NEW] `lancedb_store.py`
- Initialize LanceDB database and `text_chunks` Arrow table.
- Implement `bge-small-en-v1.5` 384-d dense embedding generation and hybrid vector + BM25 reciprocal rank fusion search.

---

### Component 2: Ingestion & Extraction Engine (`src/backend/ingestion/`)

#### [NEW] `router.py`
- Validate file size ceiling (<= 500MB, raising `FILE_TOO_LARGE` error if exceeded).
- Route `.mp3`/`.mp4` to Whisper, images to Florence-2, URLs to Trafilatura.

#### [NEW] `whisper_transcriber.py`
- Integrate `faster-whisper-tiny` with timestamp logging and resumable partial transcript offset handling.

#### [NEW] `florence_ocr.py`
- Integrate `Florence-2-base` for OCR text extraction and image captioning.

#### [NEW] `trafilatura_extractor.py`
- Extract main article text from URLs using `trafilatura`.

#### [NEW] `financial_parser.py`
- Extract monetary statements from text/transcripts into Pydantic-validated SQLite transaction records.

---

### Component 3: Query & Retrieval Engine (`src/backend/query/`)

#### [NEW] `slm_router.py`
- Invoke Ollama `Qwen-2.5-1.5B` to output JSON routing decision (`SQL`, `VECTOR`, or `HYBRID`).

#### [NEW] `search_engine.py`
- Execute unified retrieval based on SLM route decision and synthesize final answer.

---

### Component 4: REST API Backend & UI (`src/backend/`, `src/frontend/`)

#### [NEW] `main.py`
- FastAPI REST application exposing `/api/v1/ingest/file`, `/api/v1/ingest/url`, `/api/v1/query`, `/api/v1/transactions`, `/api/v1/health`, and `/api/v1/sync`.

#### [NEW] `app.py`
- Streamlit interactive chat UI with file drag-and-drop, monetary transaction table view, and RAG answer visualization.

---

## Verification Plan

### Automated Tests
- `pytest tests/unit/test_sqlite_schema.py`: Validate SQLite table creation and transactions CRUD.
- `pytest tests/unit/test_financial_parser.py`: Verify parsing of text strings ("paid Alex $50") into `FinancialTransactionCreate`.
- `pytest tests/unit/test_slm_router.py`: Test query classification into `SQL`, `VECTOR`, and `HYBRID` routes.
- `pytest tests/integration/test_memory_ceiling.py`: Verify peak RAM usage stays < 4GB during ingestion and retrieval runs.

### Manual Verification
- Execute end-to-end scenarios documented in `specs/001-multimodal-personal-rag/quickstart.md`.
