# Research & Technical Decisions: Multimodal Personal RAG

## 1. Dual-Storage Architecture (SQLite + LanceDB)

- **Decision**: Use SQLite for structured relational data (financial transactions, document metadata, sync logs) and LanceDB for unstructured text chunk vector storage and hybrid search.
- **Rationale**:
  - SQLite provides zero-config, embedded ACID transactions with minimal memory footprint (< 10MB RAM).
  - LanceDB uses disk-backed Apache Arrow columnar storage, avoiding in-memory vector index overhead and keeping RAM usage well under the 4GB system ceiling.
  - LanceDB natively supports hybrid search (combining 384-dimensional `bge-small-en-v1.5` dense vectors with BM25 sparse keyword matching using Reciprocal Rank Fusion).
- **Alternatives Considered**:
  - *PostgreSQL + pgvector*: Rejected due to high background memory usage (> 500MB idle RAM) violating low-resource constitution limits.
  - *ChromaDB / Qdrant*: Rejected due to higher RAM overhead for in-memory HNSW index structures compared to LanceDB's disk-native Arrow format.

## 2. Ingestion Pipeline & File Routing Strategy

- **Decision**: Implement a deterministic file type router with sequential model execution:
  - `.mp3`, `.wav`, `.mp4`, `.m4a` → `faster-whisper` (`tiny` model, CPU/quantized int8).
  - `.png`, `.jpg`, `.jpeg`, `.pdf` → `Florence-2-base` for OCR and image captioning.
  - `.html`, web URLs, `.txt`, `.md` → `Trafilatura` for clean main text extraction.
  - Financial statements in all text output → Extract via Pydantic financial transaction parser into SQLite.
- **Rationale**:
  - Enforces a 500MB maximum file upload ceiling (`FILE_TOO_LARGE` error).
  - Sequential model execution with explicit memory offloading prevents concurrent model spikes from exceeding the 4GB RAM budget.
- **Alternatives Considered**:
  - *Parallel multi-model worker queues*: Rejected due to memory overhead when running Whisper, Florence-2, and Ollama simultaneously.

## 3. Financial Transaction Parsing Engine

- **Decision**: Hybrid extraction approach combining fast regex pattern matching for monetary tokens (e.g., `$50`, `USD 100`, `paid 20 EUR`) with SLM entity extraction, validating output using Pydantic `FinancialTransactionCreate` schemas before writing to SQLite.
- **Rationale**:
  - Guarantees data type safety and prevents malformed currency inserts.
  - Catches both explicit monetary formats and natural language payment mentions (e.g., "paid Alex $50 for dinner").

## 4. Query Router Architecture (Qwen-2.5-1.5B via Ollama)

- **Decision**: Deploy local `Qwen-2.5-1.5B` via Ollama with structured JSON schema output to route queries into `SQL`, `VECTOR`, or `HYBRID` execution paths.
- **Rationale**:
  - Compact 1.5B parameters run comfortably within a ~1.2GB RAM footprint.
  - Structured JSON routing response separates SQL filters (e.g., `SELECT * FROM transactions WHERE entity_person = 'Alex'`) from vector semantic queries.

## 5. Web & API Framework Stack (FastAPI + Streamlit)

- **Decision**: FastAPI async REST backend decoupled from Streamlit chat frontend UI.
- **Rationale**:
  - FastAPI provides high-throughput async REST endpoints, automatic OpenAPI documentation, and Pydantic request/response validation.
  - Streamlit enables rapid development of a rich multimodal chat UI with file drag-and-drop, transaction history tables, and retrieval confidence displays.
