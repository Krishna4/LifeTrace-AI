import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, date
from typing import Any, Dict, List, Optional
from src.backend.models.pydantic_schemas import (
    FinancialTransactionCreate,
    FinancialTransactionResponse,
    PersonalEventCreate,
    PersonalEventResponse,
    DocumentCreate,
    DocumentResponse,
)

DEFAULT_DB_PATH = os.environ.get("PERSONAL_RAG_DB_PATH", "personal_rag.db")


@contextmanager
def get_db_connection(db_path: str = DEFAULT_DB_PATH):
    """Context manager for SQLite connections with row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize SQLite database tables and indices."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Documents Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                vector_sync_status TEXT NOT NULL DEFAULT 'UNINDEXED',
                error_log TEXT,
                resume_offset_seconds REAL DEFAULT 0.0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_sync ON documents(vector_sync_status);")

        # Transactions Table
        cursor.execute("""
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
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_entity ON transactions(entity_person);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);")

        # Personal Events / Daily Life Ledger Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS personal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'DAILY_EVENT',
                event_date TEXT NOT NULL,
                location TEXT,
                entity_person TEXT,
                details TEXT,
                source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_personal_events_category ON personal_events(category);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_personal_events_date ON personal_events(event_date);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_personal_events_entity ON personal_events(entity_person);")

        # Vector Sync Logs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vector_sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                sync_status TEXT NOT NULL,
                error_message TEXT,
                attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Format Rules Table (Self-Learning Ingestion Agent Registry)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS format_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                extension TEXT UNIQUE NOT NULL,
                strategy_name TEXT NOT NULL,
                sample_filename TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)


# --- Self-Learning Format Rule DAO Functions ---

def record_format_rule(extension: str, strategy_name: str, sample_filename: str = "", db_path: str = DEFAULT_DB_PATH) -> None:
    """Records or updates a learned ingestion strategy for a file extension in SQLite."""
    now_str = datetime.utcnow().isoformat()
    ext_clean = extension.lower().strip()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO format_rules (extension, strategy_name, sample_filename, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(extension) DO UPDATE SET
                strategy_name=excluded.strategy_name,
                sample_filename=excluded.sample_filename,
                updated_at=excluded.updated_at
            """,
            (ext_clean, strategy_name, sample_filename, now_str, now_str),
        )


def get_format_rule(extension: str, db_path: str = DEFAULT_DB_PATH) -> Optional[str]:
    """Retrieves a previously learned ingestion strategy for a file extension."""
    ext_clean = extension.lower().strip()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT strategy_name FROM format_rules WHERE extension = ?", (ext_clean,))
        row = cursor.fetchone()
        return row["strategy_name"] if row else None

def create_document(doc: DocumentCreate, db_path: str = DEFAULT_DB_PATH) -> DocumentResponse:
    now_str = datetime.utcnow().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO documents (file_path, file_type, file_size_bytes, status, vector_sync_status, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', 'UNINDEXED', ?, ?)
            """,
            (doc.file_path, doc.file_type, doc.file_size_bytes, now_str, now_str),
        )
        doc_id = cursor.lastrowid
        cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        return _row_to_document_response(row)


def update_document_status(
    doc_id: int,
    status: str,
    error_log: Optional[str] = None,
    resume_offset: float = 0.0,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    now_str = datetime.utcnow().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE documents
            SET status = ?, error_log = ?, resume_offset_seconds = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_log, resume_offset, now_str, doc_id),
        )


def update_document_vector_sync(
    doc_id: int,
    sync_status: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    now_str = datetime.utcnow().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE documents
            SET vector_sync_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (sync_status, now_str, doc_id),
        )


def get_document_by_id(doc_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[DocumentResponse]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        if row:
            return _row_to_document_response(row)
        return None


def get_unindexed_documents(db_path: str = DEFAULT_DB_PATH) -> List[DocumentResponse]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM documents WHERE vector_sync_status IN ('UNINDEXED', 'SYNC_FAILED')"
        )
        rows = cursor.fetchall()
        return [_row_to_document_response(r) for r in rows]


def get_all_documents(limit: int = 50, db_path: str = DEFAULT_DB_PATH) -> List[DocumentResponse]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM documents ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return [_row_to_document_response(r) for r in rows]


# --- Transactions DAO Functions ---

def create_transaction(
    tx: FinancialTransactionCreate, db_path: str = DEFAULT_DB_PATH
) -> FinancialTransactionResponse:
    now_str = datetime.utcnow().isoformat()
    date_str = tx.transaction_date.isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO transactions (entity_person, amount, currency, transaction_date, notes, source_document_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.entity_person,
                tx.amount,
                tx.currency.upper(),
                date_str,
                tx.notes,
                tx.source_document_id,
                now_str,
            ),
        )
        tx_id = cursor.lastrowid
        cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,))
        row = cursor.fetchone()
        return _row_to_transaction_response(row)


def get_transactions(
    entity_person: Optional[str] = None,
    limit: int = 50,
    db_path: str = DEFAULT_DB_PATH,
) -> List[FinancialTransactionResponse]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if entity_person:
            cursor.execute(
                "SELECT * FROM transactions WHERE LOWER(entity_person) LIKE LOWER(?) ORDER BY transaction_date DESC LIMIT ?",
                (f"%{entity_person}%", limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM transactions ORDER BY transaction_date DESC LIMIT ?", (limit,)
            )
        rows = cursor.fetchall()
        return [_row_to_transaction_response(r) for r in rows]


def get_total_amount_by_entity(
    entity_person: str, db_path: str = DEFAULT_DB_PATH
) -> Dict[str, Any]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT entity_person, SUM(amount) as total_amount, currency, COUNT(*) as tx_count
            FROM transactions
            WHERE LOWER(entity_person) LIKE LOWER(?)
            GROUP BY currency
            """,
            (f"%{entity_person}%",),
        )
        rows = cursor.fetchall()
        if not rows:
            return {"entity_person": entity_person, "total_amount": 0.0, "currency": "USD", "tx_count": 0}
        first = rows[0]
        return {
            "entity_person": first["entity_person"],
            "total_amount": float(first["total_amount"]),
            "currency": first["currency"],
            "tx_count": int(first["tx_count"]),
        }


def log_vector_sync_event(
    doc_id: int, chunk_count: int, sync_status: str, error_message: Optional[str] = None, db_path: str = DEFAULT_DB_PATH
) -> None:
    now_str = datetime.utcnow().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO vector_sync_logs (document_id, chunk_count, sync_status, error_message, attempted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (doc_id, chunk_count, sync_status, error_message, now_str),
        )


def delete_document(doc_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[str]:
    """Deletes document record and associated transactions from SQLite, returning file_path."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        file_path = row["file_path"] if row else None

        cursor.execute("DELETE FROM transactions WHERE source_document_id = ?", (doc_id,))
        cursor.execute("DELETE FROM vector_sync_logs WHERE document_id = ?", (doc_id,))
        cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return file_path


# --- Helper mapping functions ---

def _row_to_document_response(row: sqlite3.Row) -> DocumentResponse:
    created = datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"]
    updated = datetime.fromisoformat(row["updated_at"]) if isinstance(row["updated_at"], str) else row["updated_at"]
    return DocumentResponse(
        id=row["id"],
        file_path=row["file_path"],
        file_type=row["file_type"],
        file_size_bytes=row["file_size_bytes"],
        status=row["status"],
        vector_sync_status=row["vector_sync_status"],
        error_log=row["error_log"],
        resume_offset_seconds=float(row["resume_offset_seconds"] or 0.0),
        created_at=created,
        updated_at=updated,
    )


def _row_to_transaction_response(row: sqlite3.Row) -> FinancialTransactionResponse:
    created = datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"]
    tx_date = date.fromisoformat(row["transaction_date"]) if isinstance(row["transaction_date"], str) else row["transaction_date"]
    return FinancialTransactionResponse(
        id=row["id"],
        entity_person=row["entity_person"],
        amount=float(row["amount"]),
        currency=row["currency"],
        transaction_date=tx_date,
        notes=row["notes"],
        source_document_id=row["source_document_id"],
        created_at=created,
    )


def _row_to_event_response(row: sqlite3.Row) -> PersonalEventResponse:
    created = datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"]
    ev_date = date.fromisoformat(row["event_date"]) if isinstance(row["event_date"], str) else row["event_date"]
    return PersonalEventResponse(
        id=row["id"],
        title=row["title"],
        category=row["category"],
        event_date=ev_date,
        location=row["location"],
        entity_person=row["entity_person"],
        details=row["details"],
        source_document_id=row["source_document_id"],
        created_at=created,
    )


# --- Personal Events DAO Functions ---

def create_personal_event(
    ev: PersonalEventCreate, db_path: str = DEFAULT_DB_PATH
) -> PersonalEventResponse:
    now_str = datetime.utcnow().isoformat()
    date_str = ev.event_date.isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO personal_events (title, category, event_date, location, entity_person, details, source_document_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.title,
                ev.category.upper(),
                date_str,
                ev.location,
                ev.entity_person,
                ev.details,
                ev.source_document_id,
                now_str,
            ),
        )
        ev_id = cursor.lastrowid
        cursor.execute("SELECT * FROM personal_events WHERE id = ?", (ev_id,))
        row = cursor.fetchone()
        return _row_to_event_response(row)


def get_personal_events(
    category: Optional[str] = None,
    entity_person: Optional[str] = None,
    limit: int = 50,
    db_path: str = DEFAULT_DB_PATH,
) -> List[PersonalEventResponse]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM personal_events"
        params = []
        conditions = []

        if category:
            conditions.append("category = ?")
            params.append(category.upper())
        if entity_person:
            conditions.append("LOWER(entity_person) LIKE ?")
            params.append(f"%{entity_person.lower()}%")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY event_date DESC, id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [_row_to_event_response(r) for r in rows]


def delete_personal_event(event_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Deletes a personal event record by ID."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM personal_events WHERE id = ?", (event_id,))
        return cursor.rowcount > 0
