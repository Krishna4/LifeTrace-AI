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
_initialized_dbs = set()


def _ensure_columns_exist(conn: sqlite3.Connection) -> None:
    """Safely adds missing columns to existing database tables if not present."""
    cursor = conn.cursor()

    # Columns for documents
    cursor.execute("PRAGMA table_info(documents)")
    doc_cols = {col["name"] for col in cursor.fetchall()}
    if "username" not in doc_cols:
        cursor.execute("ALTER TABLE documents ADD COLUMN username TEXT NOT NULL DEFAULT 'default_user'")
    if "is_secure" not in doc_cols:
        cursor.execute("ALTER TABLE documents ADD COLUMN is_secure INTEGER NOT NULL DEFAULT 0")
    if "source_name" not in doc_cols:
        cursor.execute("ALTER TABLE documents ADD COLUMN source_name TEXT")
    if "metadata_json" not in doc_cols:
        cursor.execute("ALTER TABLE documents ADD COLUMN metadata_json TEXT")

    # Columns for transactions
    cursor.execute("PRAGMA table_info(transactions)")
    tx_cols = {col["name"] for col in cursor.fetchall()}
    if "username" not in tx_cols:
        cursor.execute("ALTER TABLE transactions ADD COLUMN username TEXT NOT NULL DEFAULT 'default_user'")
    if "is_secure" not in tx_cols:
        cursor.execute("ALTER TABLE transactions ADD COLUMN is_secure INTEGER NOT NULL DEFAULT 0")

    # Columns for personal_events
    cursor.execute("PRAGMA table_info(personal_events)")
    ev_cols = {col["name"] for col in cursor.fetchall()}
    if "username" not in ev_cols:
        cursor.execute("ALTER TABLE personal_events ADD COLUMN username TEXT NOT NULL DEFAULT 'default_user'")
    if "is_secure" not in ev_cols:
        cursor.execute("ALTER TABLE personal_events ADD COLUMN is_secure INTEGER NOT NULL DEFAULT 0")


def _run_init(conn: sqlite3.Connection) -> None:
    """Creates tables and indices if missing and migrates schemas."""
    cursor = conn.cursor()

    # 1. Documents Table
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
            username TEXT NOT NULL DEFAULT 'default_user',
            is_secure INTEGER NOT NULL DEFAULT 0,
            source_name TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. Transactions Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_person TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            transaction_date TEXT NOT NULL,
            notes TEXT,
            username TEXT NOT NULL DEFAULT 'default_user',
            is_secure INTEGER NOT NULL DEFAULT 0,
            source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 3. Personal Events / Daily Life Ledger Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS personal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'DAILY_EVENT',
            event_date TEXT NOT NULL,
            location TEXT,
            entity_person TEXT,
            details TEXT,
            username TEXT NOT NULL DEFAULT 'default_user',
            is_secure INTEGER NOT NULL DEFAULT 0,
            source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 4. Vector Sync Logs Table
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

    # 5. Format Rules Table
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

    # Ensure all columns exist on older existing databases
    _ensure_columns_exist(conn)

    # Indices (Created after ensuring columns exist)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_sync ON documents(vector_sync_status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(username);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_entity ON transactions(entity_person);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(username);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_personal_events_category ON personal_events(category);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_personal_events_date ON personal_events(event_date);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_personal_events_entity ON personal_events(entity_person);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_personal_events_user ON personal_events(username);")


def get_current_db_path(db_path: Optional[str] = None) -> str:
    """Returns provided db_path or dynamically resolves from PERSONAL_RAG_DB_PATH environment variable."""
    return db_path or os.environ.get("PERSONAL_RAG_DB_PATH", DEFAULT_DB_PATH)


@contextmanager
def get_db_connection(db_path: Optional[str] = None):
    """Context manager for SQLite connections with automatic schema migration and row factory."""
    resolved_path = get_current_db_path(db_path)
    conn = sqlite3.connect(resolved_path)
    conn.row_factory = sqlite3.Row
    if resolved_path not in _initialized_dbs:
        _run_init(conn)
        _initialized_dbs.add(resolved_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite database tables and indices with multi-user isolation support."""
    resolved_path = get_current_db_path(db_path)
    with sqlite3.connect(resolved_path) as conn:
        conn.row_factory = sqlite3.Row
        _run_init(conn)
        _initialized_dbs.add(resolved_path)



# --- Self-Learning Format Rule DAO Functions ---

def record_format_rule(extension: str, strategy_name: str, sample_filename: str = "", db_path: str = DEFAULT_DB_PATH) -> None:
    now_str = datetime.now().isoformat()
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
    ext_clean = extension.lower().strip()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT strategy_name FROM format_rules WHERE extension = ?", (ext_clean,))
        row = cursor.fetchone()
        return row["strategy_name"] if row else None


# --- Document DAO Functions ---

def create_document(doc: DocumentCreate, db_path: str = DEFAULT_DB_PATH) -> DocumentResponse:
    now_str = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO documents (file_path, file_type, file_size_bytes, status, vector_sync_status, username, is_secure, source_name, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', 'UNINDEXED', ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.file_path,
                doc.file_type,
                doc.file_size_bytes,
                doc.username or "default_user",
                1 if doc.is_secure else 0,
                doc.source_name or "",
                doc.metadata_json or "{}",
                now_str,
                now_str,
            ),
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
    file_size_bytes: Optional[int] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    now_str = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if file_size_bytes is not None and file_size_bytes > 0:
            cursor.execute(
                """
                UPDATE documents
                SET status = ?, error_log = ?, resume_offset_seconds = ?, file_size_bytes = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error_log, resume_offset, file_size_bytes, now_str, doc_id),
            )
        else:
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
    now_str = datetime.now().isoformat()
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


def get_all_documents(
    username: Optional[str] = None,
    include_secure: bool = True,
    limit: int = 50,
    db_path: str = DEFAULT_DB_PATH,
) -> List[DocumentResponse]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM documents"
        params: List[Any] = []
        conditions: List[str] = []

        if username:
            conditions.append("username = ?")
            params.append(username)
        if not include_secure:
            conditions.append("is_secure = 0")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [_row_to_document_response(r) for r in rows]


# --- Transactions DAO Functions ---

def create_transaction(
    tx: FinancialTransactionCreate, db_path: str = DEFAULT_DB_PATH
) -> FinancialTransactionResponse:
    now_str = datetime.now().isoformat()
    date_str = tx.transaction_date.isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO transactions (entity_person, amount, currency, transaction_date, notes, username, is_secure, source_document_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.entity_person,
                tx.amount,
                tx.currency.upper(),
                date_str,
                tx.notes,
                tx.username or "default_user",
                1 if tx.is_secure else 0,
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
    username: Optional[str] = None,
    include_secure: bool = True,
    limit: int = 50,
    db_path: str = DEFAULT_DB_PATH,
) -> List[FinancialTransactionResponse]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM transactions"
        params: List[Any] = []
        conditions: List[str] = []

        if username:
            conditions.append("username = ?")
            params.append(username)
        if not include_secure:
            conditions.append("is_secure = 0")
        if entity_person:
            conditions.append("LOWER(entity_person) LIKE LOWER(?)")
            params.append(f"%{entity_person}%")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY transaction_date DESC, id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [_row_to_transaction_response(r) for r in rows]


def get_total_amount_by_entity(
    entity_person: str,
    username: Optional[str] = None,
    include_secure: bool = True,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        query = """
            SELECT entity_person, SUM(amount) as total_amount, currency, COUNT(*) as tx_count
            FROM transactions
            WHERE LOWER(entity_person) LIKE LOWER(?)
        """
        params: List[Any] = [f"%{entity_person}%"]

        if username:
            query += " AND username = ?"
            params.append(username)
        if not include_secure:
            query += " AND is_secure = 0"

        query += " GROUP BY currency"

        cursor.execute(query, params)
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
    now_str = datetime.now().isoformat()
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
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        file_path = row["file_path"] if row else None

        cursor.execute("DELETE FROM transactions WHERE source_document_id = ?", (doc_id,))
        cursor.execute("DELETE FROM personal_events WHERE source_document_id = ?", (doc_id,))
        cursor.execute("DELETE FROM vector_sync_logs WHERE document_id = ?", (doc_id,))
        cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return file_path


# --- Personal Events DAO Functions ---

def create_personal_event(
    ev: PersonalEventCreate, db_path: str = DEFAULT_DB_PATH
) -> PersonalEventResponse:
    now_str = datetime.now().isoformat()
    date_str = ev.event_date.isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO personal_events (title, category, event_date, location, entity_person, details, username, is_secure, source_document_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.title,
                ev.category.upper(),
                date_str,
                ev.location,
                ev.entity_person,
                ev.details,
                ev.username or "default_user",
                1 if ev.is_secure else 0,
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
    query_text: Optional[str] = None,
    event_date: Optional[Any] = None,
    date_from: Optional[Any] = None,
    date_to: Optional[Any] = None,
    username: Optional[str] = None,
    include_secure: bool = True,
    limit: int = 50,
    db_path: str = DEFAULT_DB_PATH,
) -> List[PersonalEventResponse]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM personal_events"
        params: List[Any] = []
        conditions: List[str] = []

        if username:
            conditions.append("username = ?")
            params.append(username)
        if not include_secure:
            conditions.append("is_secure = 0")
        if category:
            conditions.append("category = ?")
            params.append(category.upper())
        if entity_person:
            conditions.append("LOWER(entity_person) LIKE ?")
            params.append(f"%{entity_person.lower()}%")
        if event_date:
            d_str = event_date.isoformat() if hasattr(event_date, "isoformat") else str(event_date)
            conditions.append("event_date = ?")
            params.append(d_str)
        if date_from:
            d_from_str = date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from)
            conditions.append("event_date >= ?")
            params.append(d_from_str)
        if date_to:
            d_to_str = date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to)
            conditions.append("event_date <= ?")
            params.append(d_to_str)
        if query_text and query_text.strip():
            qt = query_text.strip().lower()
            conditions.append("(LOWER(title) LIKE ? OR LOWER(details) LIKE ? OR LOWER(location) LIKE ? OR LOWER(entity_person) LIKE ?)")
            params.extend([f"%{qt}%", f"%{qt}%", f"%{qt}%", f"%{qt}%"])

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY event_date DESC, id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [_row_to_event_response(r) for r in rows]



def delete_personal_event(event_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM personal_events WHERE id = ?", (event_id,))
        return cursor.rowcount > 0


# --- Helper mapping functions ---

def _row_to_document_response(row: sqlite3.Row) -> DocumentResponse:
    row_dict = dict(row)
    created = datetime.fromisoformat(row_dict["created_at"]) if isinstance(row_dict["created_at"], str) else row_dict["created_at"]
    updated = datetime.fromisoformat(row_dict["updated_at"]) if isinstance(row_dict["updated_at"], str) else row_dict["updated_at"]
    
    return DocumentResponse(
        id=row_dict["id"],
        file_path=row_dict["file_path"],
        file_type=row_dict["file_type"],
        file_size_bytes=row_dict["file_size_bytes"],
        username=row_dict.get("username", "default_user"),
        is_secure=bool(row_dict.get("is_secure", 0)),
        source_name=row_dict.get("source_name"),
        status=row_dict["status"],
        vector_sync_status=row_dict["vector_sync_status"],
        error_log=row_dict.get("error_log"),
        resume_offset_seconds=float(row_dict.get("resume_offset_seconds") or 0.0),
        created_at=created,
        updated_at=updated,
    )


def _row_to_transaction_response(row: sqlite3.Row) -> FinancialTransactionResponse:
    row_dict = dict(row)
    created = datetime.fromisoformat(row_dict["created_at"]) if isinstance(row_dict["created_at"], str) else row_dict["created_at"]
    tx_date = date.fromisoformat(row_dict["transaction_date"]) if isinstance(row_dict["transaction_date"], str) else row_dict["transaction_date"]

    return FinancialTransactionResponse(
        id=row_dict["id"],
        entity_person=row_dict["entity_person"],
        amount=float(row_dict["amount"]),
        currency=row_dict["currency"],
        transaction_date=tx_date,
        notes=row_dict.get("notes"),
        username=row_dict.get("username", "default_user"),
        is_secure=bool(row_dict.get("is_secure", 0)),
        source_document_id=row_dict.get("source_document_id"),
        created_at=created,
    )


def _row_to_event_response(row: sqlite3.Row) -> PersonalEventResponse:
    row_dict = dict(row)
    created = datetime.fromisoformat(row_dict["created_at"]) if isinstance(row_dict["created_at"], str) else row_dict["created_at"]
    ev_date = date.fromisoformat(row_dict["event_date"]) if isinstance(row_dict["event_date"], str) else row_dict["event_date"]

    return PersonalEventResponse(
        id=row_dict["id"],
        title=row_dict["title"],
        category=row_dict["category"],
        event_date=ev_date,
        location=row_dict.get("location"),
        entity_person=row_dict.get("entity_person"),
        details=row_dict.get("details"),
        username=row_dict.get("username", "default_user"),
        is_secure=bool(row_dict.get("is_secure", 0)),
        source_document_id=row_dict.get("source_document_id"),
        created_at=created,
    )
