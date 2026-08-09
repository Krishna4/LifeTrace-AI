# Data Model Specification: Multimodal Personal RAG

## 1. SQLite Relational Schema

### `transactions` Table
Stores structured financial transactions extracted from personal notes, documents, audio transcripts, or direct user input.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | Unique transaction ID |
| `entity_person` | `TEXT` | `NOT NULL` | Counterparty / person paid or received from |
| `amount` | `REAL` | `NOT NULL` | Monetary amount (decimal precision) |
| `currency` | `TEXT` | `NOT NULL DEFAULT 'USD'` | Currency code (e.g. USD, EUR, INR) |
| `transaction_date` | `TEXT` | `NOT NULL` | Date in ISO-8601 format (`YYYY-MM-DD`) |
| `notes` | `TEXT` | `NULLABLE` | Context, itemization, or category notes |
| `source_document_id` | `INTEGER` | `FOREIGN KEY -> documents(id)` | Associated source document ID if extracted |
| `created_at` | `TEXT` | `NOT NULL DEFAULT CURRENT_TIMESTAMP` | System creation timestamp |

```sql
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_person TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    transaction_date TEXT NOT NULL,
    notes TEXT,
    source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_transactions_entity ON transactions(entity_person);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
```

### `documents` Table
Tracks ingested documents, URLs, audio/video files, and media processing status.

```sql
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL, -- 'text', 'audio', 'video', 'image', 'web'
    file_size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'PROCESSING', 'COMPLETED', 'PARTIAL_SUCCESS', 'PARTIAL_TRANSCRIPT', 'FAILED'
    vector_sync_status TEXT NOT NULL DEFAULT 'UNINDEXED', -- 'UNINDEXED', 'INDEXED', 'SYNC_FAILED'
    error_log TEXT,
    resume_offset_seconds REAL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_sync ON documents(vector_sync_status);
```

### `vector_sync_logs` Table
Logs dual-store reconciliation attempts and vector index sync events.

```sql
CREATE TABLE IF NOT EXISTS vector_sync_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    sync_status TEXT NOT NULL, -- 'SUCCESS', 'FAILED'
    error_message TEXT,
    attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## 2. LanceDB Vector Schema (Apache Arrow Table)

Table Name: `text_chunks`

| Field Name | Arrow / LanceDB Type | Description |
| :--- | :--- | :--- |
| `vector` | `FixedSizeList[Float32, 384]` | Dense vector embedding from `bge-small-en-v1.5` |
| `id` | `Utf8` | UUID string chunk identifier |
| `document_id` | `Int64` | Foreign key referencing SQLite `documents.id` |
| `chunk_index` | `Int32` | Sequential index of chunk within source document |
| `text_content` | `Utf8` | Raw text payload of chunk |
| `bm25_tokens` | `Utf8` | Preprocessed tokenized text for BM25 keyword matching |
| `source_type` | `Utf8` | Source media type (`text`, `audio`, `video`, `image`, `web`) |
| `timestamp_start` | `Float32` | Start timestamp in seconds (for audio/video chunks) |
| `timestamp_end` | `Float32` | End timestamp in seconds (for audio/video chunks) |
| `created_at` | `Utf8` | ISO-8601 creation timestamp |

---

## 3. Pydantic v2 Core Data Models

```python
from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

class FinancialTransactionBase(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    entity_person: str = Field(..., description="Counterparty or person involved in transaction")
    amount: float = Field(..., gt=0, description="Positive numeric amount")
    currency: str = Field(default="USD", max_length=3, description="ISO currency code")
    transaction_date: date = Field(..., description="Date of transaction")
    notes: Optional[str] = Field(default=None, description="Optional notes or context")

class FinancialTransactionCreate(FinancialTransactionBase):
    source_document_id: Optional[int] = None

class FinancialTransactionResponse(FinancialTransactionBase):
    id: int
    source_document_id: Optional[int] = None
    created_at: datetime

class QueryRouteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    query: str = Field(..., min_length=1, description="Natural language question")

class QueryRouteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    target_engine: Literal["SQL", "VECTOR", "HYBRID"]
    sql_query: Optional[str] = None
    vector_terms: Optional[str] = None
    rationale: str
```
