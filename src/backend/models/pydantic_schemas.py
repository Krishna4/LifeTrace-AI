from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FinancialTransactionBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_person: str = Field(..., description="Counterparty or person involved in transaction")
    amount: float = Field(..., gt=0, description="Positive numeric amount")
    currency: str = Field(default="USD", max_length=3, description="ISO currency code")
    transaction_date: date = Field(..., description="Date of transaction")
    notes: Optional[str] = Field(default=None, description="Optional notes or context")


class FinancialTransactionCreate(FinancialTransactionBase):
    source_document_id: Optional[int] = Field(default=None, description="Associated source document ID")


class FinancialTransactionResponse(FinancialTransactionBase):
    id: int
    source_document_id: Optional[int] = None
    created_at: datetime


class PersonalEventBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = Field(..., min_length=1, description="Event title or activity description")
    category: str = Field(default="DAILY_EVENT", description="Category e.g. DAILY_EVENT, MEETING, TRAVEL, HEALTH, MILESTONE")
    event_date: date = Field(..., description="Date of the event")
    location: Optional[str] = Field(default=None, description="Location or venue")
    entity_person: Optional[str] = Field(default=None, description="People involved")
    details: Optional[str] = Field(default=None, description="Detailed notes or description")


class PersonalEventCreate(PersonalEventBase):
    source_document_id: Optional[int] = Field(default=None, description="Associated document ID")


class PersonalEventResponse(PersonalEventBase):
    id: int
    source_document_id: Optional[int] = None
    created_at: datetime


class DocumentCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_path: str = Field(..., min_length=1)
    file_type: str = Field(..., min_length=1)
    file_size_bytes: int = Field(..., ge=0)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    file_path: str
    file_type: str
    file_size_bytes: int
    status: Literal["PENDING", "PROCESSING", "COMPLETED", "PARTIAL_SUCCESS", "PARTIAL_TRANSCRIPT", "FAILED"]
    vector_sync_status: Literal["UNINDEXED", "INDEXED", "SYNC_FAILED"]
    error_log: Optional[str] = None
    resume_offset_seconds: float = 0.0
    created_at: datetime
    updated_at: datetime


class TextChunkModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="UUID string identifier")
    document_id: int = Field(..., description="Foreign key referencing SQLite documents.id")
    chunk_index: int = Field(..., ge=0)
    text_content: str = Field(..., min_length=1)
    bm25_tokens: str = Field(default="")
    source_type: str = Field(default="text")
    timestamp_start: Optional[float] = None
    timestamp_end: Optional[float] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class QueryRouteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(..., min_length=1, description="Natural language question")


class QueryRouteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    target_engine: Literal["SQL", "VECTOR", "HYBRID"]
    sql_query: Optional[str] = None
    vector_terms: Optional[str] = None
    rationale: str = Field(default="")
    answer: Optional[str] = None
    transactions: List[Dict[str, Any]] = Field(default_factory=list)
    retrieved_chunks: List[Dict[str, Any]] = Field(default_factory=list)


class IngestUrlRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str = Field(..., min_length=5, description="Web page URL to ingest")
