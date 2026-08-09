# 🏗️ System Architecture & Design Documentation

**Project**: Personal RAG — Low-Resource Multimodal Intelligence System & Expense Tracker  
**Target RAM Constraint**: Max 4GB Total RAM Ceiling  
**Frameworks**: FastAPI, Streamlit, LanceDB (Apache Arrow), SQLite, Pydantic v2, PyTorch/MPS  

---

## 1. High-Level Architecture Overview

The Personal RAG system employs a dual-storage architecture that decouples relational metadata & structured financial transactions from high-dimensional dense vector embeddings and BM25 sparse keyword indices.

```mermaid
flowchart TB
    subgraph Frontend["🎨 Presentation Layer (Streamlit Dashboard)"]
        UI["src/frontend/app.py"]
        UI_Chat["Conversational Chat Tab"]
        UI_Ingest["File & URL Ingestion Tab"]
        UI_Expenses["Financial History Tab"]
    end

    subgraph Backend["⚡ API Gateway Layer (FastAPI Server)"]
        API["src/backend/main.py"]
        Health["/api/v1/health"]
        IngestAPI["/api/v1/ingest/file & /url"]
        QueryAPI["/api/v1/query"]
        TxAPI["/api/v1/transactions"]
        SyncAPI["/api/v1/sync"]
        MemMon["src/backend/utils/memory_monitor.py<br/>(4GB RAM Ceiling Enforcer)"]
    end

    subgraph IngestionEngine["📥 Ingestion & Extraction Engine"]
        Router["router.py"]
        Whisper["whisper_transcriber.py<br/>(faster-whisper-tiny)"]
        Florence["florence_ocr.py<br/>(Florence-2-base OCR)"]
        Trafilatura["trafilatura_extractor.py<br/>(Web Article Cleaner)"]
        DOCX["docx_extractor.py<br/>(Word XML Parser)"]
        PDF["pdf_extractor.py<br/>(PyPDF Text Extractor)"]
        DynamicAgent["dynamic_agent.py<br/>(Guardrail & Self-Learning Agent)"]
        FinParser["financial_parser.py<br/>(Monetary Entity Extractor)"]
    end

    subgraph StorageLayer["💾 Dual-Storage Engine"]
        subgraph RelationalDB["SQLite Database (personal_rag.db)"]
            T_Docs["documents"]
            T_Txs["transactions"]
            T_Logs["vector_sync_logs"]
            T_Rules["format_rules"]
        end

        subgraph VectorDB["LanceDB Columnar Vector Store (lancedb_data/)"]
            LanceTable["text_chunks Table<br/>(384-d bge-small-en-v1.5 + BM25)"]
        end

        SyncWorker["src/backend/services/sync_worker.py<br/>(Async Sync Reconciliation)"]
    end

    subgraph QueryEngine["🧠 Query & Reasoning Engine"]
        SLMRouter["slm_router.py<br/>(Qwen-2.5-1.5B / Semantic Prototype)"]
        SearchEng["search_engine.py<br/>(Hybrid RRF Search & Extractive QA)"]
        SLMSynth["synthesize_answer_slm()<br/>(Generative Context Synthesis)"]
    end

    UI --> API
    API --> MemMon
    IngestAPI --> Router
    Router -->|Audio/Video| Whisper
    Router -->|Images| Florence
    Router -->|Web URLs| Trafilatura
    Router -->|DOCX| DOCX
    Router -->|PDF| PDF
    Router -->|Unknown / Fallback| DynamicAgent
    
    Whisper & Florence & Trafilatura & DOCX & PDF & DynamicAgent --> FinParser
    FinParser -->|Structured Records| T_Txs
    FinParser -->|Text Payloads| LanceTable
    FinParser -->|Document Status| T_Docs

    QueryAPI --> SLMRouter
    SLMRouter -->|SQL Target| T_Txs
    SLMRouter -->|Vector Target| LanceTable
    SLMRouter -->|Hybrid Target| SearchEng

    T_Txs --> SearchEng
    LanceTable --> SearchEng
    SearchEng --> SLMSynth
    SLMSynth --> API

    T_Docs -.-> SyncWorker
    LanceTable -.-> SyncWorker
```

---

## 2. Ingestion Pipeline Sequence Flow

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Streamlit App
    participant API as FastAPI Server
    participant DB as SQLite DB
    participant Engine as Ingestion Engine
    participant Lance as LanceDB Store

    User->>UI: Upload File (e.g., DOCX / Audio / Image / PDF)
    UI->>API: POST /api/v1/ingest/file (multipart/form-data)
    API->>API: Validate File Size (<= 500MB)
    API->>DB: INSERT document record (status='PROCESSING', sync='UNINDEXED')
    API-->>UI: HTTP 202 Accepted (document_id, < 50ms response)

    par Background Task
        API->>Engine: _process_document_background(doc_id)
        alt Audio / Video
            Engine->>Engine: Run faster-whisper-tiny speech transcription
        else Image
            Engine->>Engine: Run Florence-2 OCR & Scene Captioning
        else Word (.docx)
            Engine->>Engine: Parse word/document.xml clean paragraphs
        else Unknown / Unrecognized Format
            Engine->>Engine: Run Dynamic Guardrail & Self-Learning Agent
        end

        Engine->>Engine: Parse monetary statements ("paid Alex $50")
        opt Monetary Statements Found
            Engine->>DB: INSERT INTO transactions
        end

        Engine->>Lance: Split into semantic chunks & generate 384-d embeddings (bge-small-en-v1.5)
        Lance->>Lance: Add chunks to Apache Arrow table
        Engine->>DB: UPDATE documents (status='COMPLETED', sync='INDEXED')
    end
```

---

## 3. Query & RAG Reasoning Sequence Flow

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Streamlit Chat
    participant API as FastAPI Server
    participant Router as SLM Query Router
    participant SQL as SQLite DB
    participant Lance as LanceDB Store
    participant Synth as SLM Synthesis Engine

    User->>UI: Submit Question ("How much Venu Owe and when he took the money?")
    UI->>API: POST /api/v1/query {query: "..."}
    API->>Router: route_query_slm(query)
    
    alt Ollama Online
        Router->>Router: Execute Qwen-2.5-1.5B JSON classification
    else Ollama Offline
        Router->>Router: Fast < 2ms semantic prototype similarity check
    end
    Router-->>API: QueryRouteResponse (target_engine="HYBRID", entity="Venu")

    par Parallel Search Execution
        opt SQL / HYBRID Route
            API->>SQL: Case-insensitive match on entity_person ("Venu")
            SQL-->>API: Financial Transactions ($200.00 USD on 2026-08-01)
        end
        opt VECTOR / HYBRID Route
            API->>Lance: Dense Vector Search + Sparse BM25 RRF Fusion
            Lance-->>API: Top 5 Relevant Text Chunks
        end
    end

    API->>Synth: synthesize_answer_slm(query, chunks, transactions)
    
    alt Ollama Available
        Synth->>Synth: Generate concise natural language answer via Qwen 1.5B
    else Ollama Offline
        Synth->>Synth: Run Extractive QA Engine (Address / Contact / Financial extraction)
    end
    
    Synth-->>API: Final Answer String
    API-->>UI: HTTP 200 OK {answer, target_engine, transactions, retrieved_chunks}
    UI-->>User: Render Answer, Route Badge, and Inspection Accordion Drawer
```

---

## 4. Storage & Schema Specifications

### 4.1 SQLite Relational Schema (`personal_rag.db`)

```mermaid
erDiagram
    documents ||--o{ transactions : "source_document_id"
    documents ||--o{ vector_sync_logs : "document_id"

    documents {
        int id PK
        string file_path
        string file_type
        int file_size_bytes
        string status
        string vector_sync_status
        string error_log
        float resume_offset_seconds
        datetime created_at
        datetime updated_at
    }

    transactions {
        int id PK
        string entity_person
        float amount
        string currency
        string transaction_date
        string notes
        int source_document_id FK
        datetime created_at
    }

    vector_sync_logs {
        int id PK
        int document_id FK
        int chunk_count
        string sync_status
        string error_message
        datetime attempted_at
    }

    format_rules {
        int id PK
        string extension UK
        string strategy_name
        string sample_filename
        datetime created_at
        datetime updated_at
    }
```

### 4.2 LanceDB Columnar Arrow Table Schema (`text_chunks`)

| Field Name | Type | Description |
| :--- | :--- | :--- |
| `vector` | `FixedSizeList[Float32, 384]` | 384-dimensional dense vector (`BAAI/bge-small-en-v1.5`) |
| `id` | `String` | Unique chunk UUID |
| `document_id` | `Int64` | Foreign key referencing `documents.id` |
| `chunk_index` | `Int32` | Sequential index of chunk within document |
| `text_content` | `String` | Raw text payload of the chunk |
| `bm25_tokens` | `String` | Lowercase normalized space-separated tokens for BM25 matching |
| `source_type` | `String` | Document type category (`text`, `pdf`, `docx`, `audio`, `image`, `web`) |
| `timestamp_start` | `Float32` | Audio/video start timestamp offset (in seconds) |
| `timestamp_end` | `Float32` | Audio/video end timestamp offset (in seconds) |
| `created_at` | `String` | ISO timestamp |

---

## 5. Architectural Design Patterns Summary

| Pattern Name | Location | Description |
| :--- | :--- | :--- |
| **Multimodal Router** | [router.py](file:///Users/muralidupati/PersonalRag/src/backend/ingestion/router.py) | Directs incoming files to specialized extractors (Whisper, Florence-2, Trafilatura, DOCX parser). |
| **Dynamic Guardrail Agent** | [dynamic_agent.py](file:///Users/muralidupati/PersonalRag/src/backend/ingestion/dynamic_agent.py) | Inspects magic bytes, prevents raw binary garbled dumps, and self-learns strategies for unknown formats in SQLite. |
| **Dual-Store Reconciliation** | [sync_worker.py](file:///Users/muralidupati/PersonalRag/src/backend/services/sync_worker.py) | Asynchronously reconciles `UNINDEXED` or `SYNC_FAILED` documents into LanceDB without blocking users. |
| **Dynamic SLM Query Router** | [slm_router.py](file:///Users/muralidupati/PersonalRag/src/backend/query/slm_router.py) | Classifies user queries into `SQL`, `VECTOR`, or `HYBRID` routes via Qwen 1.5B or < 2ms semantic prototypes. |
| **Reciprocal Rank Fusion (RRF)** | [lancedb_store.py](file:///Users/muralidupati/PersonalRag/src/backend/database/lancedb_store.py) | Combines dense vector semantic similarity with sparse BM25 keyword matching ($k=60$). |
| **Extractive & Generative QA** | [search_engine.py](file:///Users/muralidupati/PersonalRag/src/backend/query/search_engine.py) | Generates natural language answers via local SLM or extracts targeted addresses/contacts when SLM is offline. |
| **4GB Memory Ceiling Enforcer** | [memory_monitor.py](file:///Users/muralidupati/PersonalRag/src/backend/utils/memory_monitor.py) | Enforces memory ceilings, size caps (500MB), and triggers proactive garbage collection sweeps. |
