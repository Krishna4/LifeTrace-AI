import os
import shutil
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from src.backend.models.pydantic_schemas import (
    DocumentCreate,
    DocumentResponse,
    IngestUrlRequest,
    FinancialTransactionCreate,
    FinancialTransactionResponse,
    PersonalEventCreate,
    PersonalEventResponse,
    JournalEntryRequest,
    JournalEntryResponse,
    QueryRouteRequest,
    QueryRouteResponse,
    LLMConfig,
)
from src.backend.database.sqlite import (
    init_db,
    create_document,
    update_document_status,
    update_document_vector_sync,
    get_all_documents,
    get_document_by_id,
    create_transaction,
    get_transactions,
    get_total_amount_by_entity,
    create_personal_event,
    get_personal_events,
    delete_personal_event,
    log_vector_sync_event,
    delete_document,
)
from src.backend.ingestion.event_parser import extract_personal_events, process_journal_entry
from src.backend.database.lancedb_store import LanceDBStore
from src.backend.utils.memory_monitor import get_memory_usage, enforce_memory_ceiling
from src.backend.ingestion.router import validate_file_size, get_file_type
from src.backend.ingestion.pdf_extractor import extract_pdf_text
from src.backend.ingestion.docx_extractor import extract_docx_text
from src.backend.ingestion.dynamic_agent import inspect_and_extract_adaptive
from src.backend.ingestion.whisper_transcriber import transcribe_audio
from src.backend.ingestion.florence_ocr import process_image_florence
from src.backend.ingestion.trafilatura_extractor import extract_url_content
from src.backend.ingestion.financial_parser import extract_financial_transactions
from src.backend.query.slm_router import route_query_slm
from src.backend.query.search_engine import execute_unified_search
from src.backend.llm.llm_client import get_llm_client, configure_llm_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)
logger = logging.getLogger("backend.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize SQLite and LanceDB
    db_path = os.environ.get("PERSONAL_RAG_DB_PATH", "personal_rag.db")
    logger.info(f"🌱 Starting LifeTrace AI Backend... Initializing SQLite DB at '{db_path}'")
    init_db(db_path)
    logger.info("⚡ Initializing LanceDB Store...")
    LanceDBStore()
    logger.info("✅ Startup complete! LifeTrace AI ready.")
    yield
    # Shutdown logic
    logger.info("🛑 Shutting down LifeTrace AI Backend service...")
    try:
        from src.backend.utils.memory_monitor import trigger_garbage_collection
        trigger_garbage_collection()
    except Exception as e:
        logger.warning(f"Shutdown cleanup warning: {e}")
    logger.info("👋 Backend shutdown cleanly completed.")


app = FastAPI(
    title="LifeTrace AI API",
    version="1.2.0",
    description="Private Multimodal Intelligence, Personal Life Ledger & Multi-User RAG API",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health", summary="System Health & Memory Check")
def health_check():
    """Returns system status, RAM footprint, and database readiness."""
    mem_info = get_memory_usage()
    llm_client = get_llm_client()
    return {
        "status": "healthy",
        "active_llm_provider": llm_client.get_effective_provider(),
        "ram_usage_mb": mem_info["process_ram_mb"],
        "max_memory_ceiling_mb": mem_info["max_ceiling_mb"],
        "system_used_ram_mb": mem_info["system_used_ram_mb"],
        "is_under_memory_limit": mem_info["is_under_limit"],
    }


# --- LLM Configuration Endpoints ---

@app.get("/api/v1/llm/config", summary="Get LLM Provider Configuration")
def get_llm_config():
    """Returns current active LLM provider and model configurations."""
    client = get_llm_client()
    return {
        "provider": client.provider,
        "effective_provider": client.get_effective_provider(),
        "openrouter_configured": bool(client.openrouter_api_key),
        "openrouter_model": client.openrouter_model,
        "ollama_model": client.ollama_model,
        "is_available": client.is_available(),
    }


@app.post("/api/v1/llm/config", summary="Update LLM Provider Configuration")
def set_llm_config(config: LLMConfig):
    """Dynamically updates active LLM provider, API keys, and model choices."""
    client = configure_llm_client(
        provider=config.provider,
        openrouter_api_key=config.openrouter_api_key,
        openrouter_model=config.openrouter_model,
        ollama_model=config.ollama_model,
    )
    return {
        "message": "LLM Configuration updated successfully.",
        "provider": client.provider,
        "effective_provider": client.get_effective_provider(),
        "openrouter_model": client.openrouter_model,
        "ollama_model": client.ollama_model,
    }


def _process_document_background(
    doc_id: int,
    file_path: str,
    file_type: str,
    filename: str,
    username: str = "default_user",
    is_secure: bool = False,
):
    """Background worker for asynchronous document ingestion, metadata tagging, and vector indexing."""
    import time
    start_t = time.time()
    logger.info(f"⚙️ [Async Job #{doc_id}] Starting processing for '{filename}' ({file_type}) for user '{username}' (Secure={is_secure})...")
    update_document_status(doc_id, "PROCESSING")

    extracted_text = ""
    processing_status = "COMPLETED"
    error_log = None

    try:
        if file_type == "pdf":
            logger.info(f"📄 [Async Job #{doc_id}] Extracting PDF text with pypdf...")
            res = extract_pdf_text(file_path)
            processing_status = res["status"]
            extracted_text = res["text"]
            error_log = res.get("error")
        elif file_type in ["audio", "video"]:
            logger.info(f"🎙️ [Async Job #{doc_id}] Running speech transcription...")
            res = transcribe_audio(file_path)
            processing_status = res["status"]
            error_log = res.get("error")
            segments = res.get("segments", [])
            extracted_text = " ".join([s["text"] for s in segments])
        elif file_type == "image":
            logger.info(f"🖼️ [Async Job #{doc_id}] Running OCR captioning...")
            res = process_image_florence(file_path)
            processing_status = res["status"]
            error_log = res.get("error")
            extracted_text = f"{res.get('ocr_text', '')}\n{res.get('caption', '')}".strip()
        elif file_type == "docx":
            logger.info(f"📄 [Async Job #{doc_id}] Extracting DOCX text...")
            res = extract_docx_text(file_path)
            processing_status = res["status"]
            extracted_text = res["text"]
            error_log = res.get("error")
        elif file_type == "web":
            logger.info(f"🌐 [Async Job #{doc_id}] Scraping web URL...")
            res = extract_url_content(file_path)
            processing_status = res["status"]
            error_log = res.get("error")
            extracted_text = res.get("text_content", "")
        else:
            logger.info(f"🤖 [Async Job #{doc_id}] Running Dynamic Guardrail & Self-Learning Agent for '{filename}'...")
            res = inspect_and_extract_adaptive(file_path, filename)
            processing_status = res["status"]
            extracted_text = res["text"]
            error_log = res.get("error")

        extracted_bytes = len(extracted_text.encode('utf-8')) if extracted_text else 0
        update_document_status(doc_id, processing_status, error_log=error_log, file_size_bytes=extracted_bytes)
        logger.info(f"📝 [Async Job #{doc_id}] Extracted {len(extracted_text)} characters ({extracted_bytes} bytes) in {time.time() - start_t:.2f}s")

        # Extract monetary transactions & personal events with tenant isolation metadata
        if extracted_text:
            txs = extract_financial_transactions(
                extracted_text, 
                source_document_id=doc_id, 
                username=username, 
                is_secure=is_secure,
            )
            if txs:
                logger.info(f"💸 [Async Job #{doc_id}] Found {len(txs)} transaction(s) for user '{username}'")
                for tx in txs:
                    create_transaction(tx)

            events = extract_personal_events(
                extracted_text, 
                source_document_id=doc_id, 
                username=username, 
                is_secure=is_secure,
            )
            if events:
                logger.info(f"📅 [Async Job #{doc_id}] Found {len(events)} personal event(s) for user '{username}'")
                for ev in events:
                    create_personal_event(ev)

            # Index into LanceDB with tenant metadata
            vec_start = time.time()
            chunks = _chunk_text(
                extracted_text, 
                doc_id, 
                source_type=file_type,
                username=username,
                is_secure=is_secure,
                source_file=filename,
            )
            logger.info(f"🧩 [Async Job #{doc_id}] Indexing {len(chunks)} chunk(s) into LanceDB for '{username}'...")
            lancedb_store = LanceDBStore()
            count = lancedb_store.add_chunks(chunks)
            update_document_vector_sync(doc_id, "INDEXED")
            log_vector_sync_event(doc_id, count, "SUCCESS")
            logger.info(f"⚡ [Async Job #{doc_id}] Vector indexing completed in {time.time() - vec_start:.2f}s (Total Job Time: {time.time() - start_t:.2f}s)")

    except Exception as e:
        logger.error(f"❌ [Async Job #{doc_id}] Ingestion failed: {e}")
        update_document_status(doc_id, "FAILED", error_log=str(e))
        update_document_vector_sync(doc_id, "SYNC_FAILED")


@app.post("/api/v1/ingest/file", summary="Ingest File (Async Background Job)", status_code=status.HTTP_202_ACCEPTED)
def ingest_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    username: str = Form(default="default_user"),
    is_secure: Any = Form(default=False),
):
    """Uploads file and enqueues an asynchronous background processing job with user metadata."""
    enforce_memory_ceiling()

    is_sec = str(is_secure).lower() in ("true", "1", "t") if isinstance(is_secure, str) else bool(is_secure)
    user_name = username or "default_user"

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)

    logger.info(f"📥 Received file upload: '{file.filename}' ({file_size / (1024*1024):.2f} MB) for user '{user_name}' (Secure={is_sec})")

    try:
        validate_file_size(file_size)
    except ValueError as e:
        logger.error(f"❌ File size check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "FILE_TOO_LARGE", "message": str(e)},
        )

    uploads_dir = "uploads"
    os.makedirs(uploads_dir, exist_ok=True)
    file_path = os.path.join(uploads_dir, file.filename or "upload.tmp")

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_type = get_file_type(file.filename or "file.txt")
    doc_create = DocumentCreate(
        file_path=file_path,
        file_type=file_type,
        file_size_bytes=file_size,
        username=user_name,
        is_secure=is_sec,
        source_name=file.filename,
    )
    doc_rec = create_document(doc_create)

    # Schedule background task
    background_tasks.add_task(
        _process_document_background, 
        doc_rec.id, 
        file_path, 
        file_type, 
        file.filename or "file",
        user_name,
        is_sec,
    )

    return {
        "document_id": doc_rec.id,
        "filename": file.filename,
        "file_type": file_type,
        "username": user_name,
        "is_secure": is_sec,
        "status": "PROCESSING",
        "message": "File uploaded successfully. Async background processing job enqueued.",
    }


@app.post("/api/v1/ingest/files", summary="Ingest Multiple Files (Batch Async Jobs)", status_code=status.HTTP_202_ACCEPTED)
def ingest_multiple_files(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    username: str = Form(default="default_user"),
    is_secure: Any = Form(default=False),
):
    """Uploads multiple files in batch and enqueues asynchronous background processing jobs."""
    enforce_memory_ceiling()

    is_sec = str(is_secure).lower() in ("true", "1", "t") if isinstance(is_secure, str) else bool(is_secure)
    user_name = username or "default_user"

    enqueued_jobs = []
    uploads_dir = "uploads"
    os.makedirs(uploads_dir, exist_ok=True)

    for file in files:
        file.file.seek(0, os.SEEK_END)
        file_size = file.file.tell()
        file.file.seek(0)

        if file_size > 500 * 1024 * 1024:
            logger.warning(f"Skipping '{file.filename}': size exceeds 500MB limit.")
            continue

        filename = file.filename or f"upload_{len(enqueued_jobs)}.tmp"
        file_path = os.path.join(uploads_dir, filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_type = get_file_type(filename)
        doc_create = DocumentCreate(
            file_path=file_path,
            file_type=file_type,
            file_size_bytes=file_size,
            username=user_name,
            is_secure=is_sec,
            source_name=filename,
        )
        doc_rec = create_document(doc_create)

        background_tasks.add_task(
            _process_document_background, 
            doc_rec.id, 
            file_path, 
            file_type, 
            filename,
            user_name,
            is_sec,
        )
        enqueued_jobs.append({
            "document_id": doc_rec.id,
            "filename": filename,
            "file_type": file_type,
            "username": user_name,
            "is_secure": is_sec,
            "status": "PROCESSING",
        })

    return {
        "enqueued_count": len(enqueued_jobs),
        "username": user_name,
        "jobs": enqueued_jobs,
        "message": f"Enqueued {len(enqueued_jobs)} file(s) for background ingestion.",
    }


@app.post("/api/v1/ingest/url", summary="Ingest URL (Async Background Job)", status_code=status.HTTP_202_ACCEPTED)
def ingest_url(background_tasks: BackgroundTasks, req: IngestUrlRequest):
    """Enqueues web URL scraping and indexing background job with tenant isolation."""
    enforce_memory_ceiling()
    logger.info(f"🌐 Received URL scraping request: '{req.url}' for user '{req.username}'")

    file_type = "web"
    doc_create = DocumentCreate(
        file_path=req.url,
        file_type=file_type,
        file_size_bytes=0,
        username=req.username,
        is_secure=req.is_secure,
        source_name=req.url,
    )
    doc_rec = create_document(doc_create)

    background_tasks.add_task(
        _process_document_background, 
        doc_rec.id, 
        req.url, 
        file_type, 
        req.url,
        req.username,
        req.is_secure,
    )

    return {
        "document_id": doc_rec.id,
        "url": req.url,
        "username": req.username,
        "status": "PROCESSING",
        "message": "Web URL job enqueued for background processing.",
    }


@app.get("/api/v1/documents", summary="List Ingestion Jobs & Documents")
def list_documents(
    username: Optional[str] = None,
    include_secure: bool = True,
    limit: int = 50,
):
    """Lists document ingestion jobs with real-time status and user isolation."""
    return get_all_documents(username=username, include_secure=include_secure, limit=limit)


@app.get("/api/v1/documents/{doc_id}", summary="Get Ingestion Job Status")
def get_document_status(doc_id: int):
    """Retrieves real-time status of a specific document ingestion job."""
    doc = get_document_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document job not found")
    return doc


@app.delete("/api/v1/documents/{doc_id}", summary="Delete Uploaded Document & Purge Vectors")
def delete_document_by_id(doc_id: int):
    """Deletes document metadata, vector chunks, transactions, and physical file from disk."""
    doc = get_document_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = delete_document(doc_id)
    try:
        lancedb_store = LanceDBStore()
        lancedb_store.delete_document_chunks(doc_id)
    except Exception as e:
        logger.warning(f"LanceDB chunk delete warning for #{doc_id}: {e}")

    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    return {"message": f"Document #{doc_id} and all associated vector chunks purged successfully."}


@app.get("/api/v1/transactions", summary="List Financial Transactions")
def list_transactions(
    entity_person: Optional[str] = None,
    username: Optional[str] = None,
    include_secure: bool = True,
    limit: int = 50,
):
    """Lists structured financial transactions with tenant isolation."""
    return get_transactions(
        entity_person=entity_person,
        username=username,
        include_secure=include_secure,
        limit=limit,
    )


@app.post("/api/v1/transactions", summary="Create Manual Transaction", status_code=status.HTTP_201_CREATED)
def add_transaction(tx: FinancialTransactionCreate):
    """Manually creates a new financial transaction record."""
    return create_transaction(tx)


@app.get("/api/v1/events", summary="List Personal Events & Daily Life Logs")
def list_events(
    category: Optional[str] = None,
    entity_person: Optional[str] = None,
    query: Optional[str] = None,
    event_date: Optional[str] = None,
    username: Optional[str] = None,
    include_secure: bool = True,
    limit: int = 50,
):
    """Retrieves personal events and daily life logs with tenant isolation and search filtering."""
    return get_personal_events(
        category=category,
        entity_person=entity_person,
        query_text=query,
        event_date=event_date,
        username=username,
        include_secure=include_secure,
        limit=limit,
    )


@app.post("/api/v1/events", summary="Create Personal Event Log", status_code=status.HTTP_201_CREATED)
def add_event(ev: PersonalEventCreate):
    """Manually logs a new personal event or daily life entry and indexes it into the vector store."""
    from datetime import datetime
    rec = create_personal_event(ev)
    try:
        loc_str = f" at {ev.location}" if ev.location else ""
        person_str = f" with {ev.entity_person}" if ev.entity_person else ""
        details_str = f". Details: {ev.details}" if ev.details else ""
        event_text = f"Personal Life Event ({ev.event_date}) [{ev.category}]: {ev.title}{loc_str}{person_str}{details_str}"

        chunks = [{
            "document_id": ev.source_document_id or 0,
            "chunk_index": 0,
            "text_content": event_text,
            "source_type": "event",
            "username": ev.username,
            "is_secure": ev.is_secure,
            "source_file": "event",
            "doc_type": "event",
            "created_at": datetime.utcnow().isoformat(),
        }]
        lancedb_store = LanceDBStore()
        lancedb_store.add_chunks(chunks)
    except Exception as e:
        logger.warning(f"Vector indexing event #{rec.id} warning: {e}")
    return rec



@app.delete("/api/v1/events/{event_id}", summary="Delete Personal Event Log")
def remove_event(event_id: int):
    """Deletes a personal event record by ID."""
    success = delete_personal_event(event_id)
    if not success:
        raise HTTPException(status_code=404, detail="Event record not found")
    return {"message": f"Event #{event_id} deleted successfully."}


@app.post("/api/v1/journal", summary="Log & Analyze Free-Text Daily Journal Entry", status_code=status.HTTP_201_CREATED)
def submit_journal(entry: JournalEntryRequest):
    """
    Analyzes raw free-text daily diary/journal text using LLM, synthesizes a summary with insights & mood,
    extracts structured events/expenses into SQLite, and indexes entry into LanceDB vector store.
    """
    from datetime import datetime
    res = process_journal_entry(
        entry.text, 
        entry_date=entry.entry_date,
        username=entry.username,
        is_secure=entry.is_secure,
    )

    for ev in res["extracted_events"]:
        create_personal_event(ev)

    for tx in res["extracted_transactions"]:
        create_transaction(tx)

    chunks = [{
        "document_id": 0,
        "chunk_index": 0,
        "text_content": f"Journal Entry ({res['entry_date']}) [Mood: {res['mood']}]: {res['raw_text']}",
        "source_type": "journal",
        "username": entry.username,
        "is_secure": entry.is_secure,
        "source_file": "journal",
        "doc_type": "journal",
        "created_at": datetime.utcnow().isoformat(),
    }]
    lancedb_store = LanceDBStore()
    lancedb_store.add_chunks(chunks)

    return {
        "summary": res["summary"],
        "mood": res["mood"],
        "key_insights": res["key_insights"],
        "extracted_events_count": len(res["extracted_events"]),
        "extracted_transactions_count": len(res["extracted_transactions"]),
    }


@app.post("/api/v1/query", summary="Execute RAG Query via LLM Router")
def query_rag(req: QueryRouteRequest):
    """Routes query via LLM into SQL, Vector, or Hybrid execution path with strict user data isolation."""
    enforce_memory_ceiling()
    logger.info(f"💬 Received RAG query for user '{req.username}' (Secure={req.include_secure}): '{req.query}'")
    route = route_query_slm(req.query, provider_override=req.llm_provider)
    logger.info(f"🔀 LLM Query Router decision: [{route.target_engine}] - {route.rationale}")
    search_result = execute_unified_search(
        req.query, 
        route, 
        username=req.username, 
        include_secure=req.include_secure,
        provider_override=req.llm_provider,
    )
    logger.info(f"🔍 Search complete for user '{req.username}'. Answer generated.")
    return search_result


@app.post("/api/v1/sync", summary="Trigger Background Vector Sync Reconciliation", status_code=status.HTTP_202_ACCEPTED)
def trigger_vector_sync(background_tasks: BackgroundTasks):
    """Enqueues background worker to reconcile documents marked UNINDEXED or SYNC_FAILED."""
    logger.info("📡 Received vector sync request via API. Enqueuing background task...")
    from src.backend.services.sync_worker import reconcile_vector_sync
    background_tasks.add_task(reconcile_vector_sync)
    return {"message": "Vector sync reconciliation job enqueued in background."}


@app.post("/api/v1/reset", summary="Reset All Databases & Vector Storage to Fresh Start")
def reset_database():
    """Clears SQLite transactions, documents, LanceDB vector tables, and uploaded files."""
    from src.backend.database.sqlite import get_db_connection, DEFAULT_DB_PATH
    db_path = os.environ.get("PERSONAL_RAG_DB_PATH", DEFAULT_DB_PATH)
    lance_dir = os.environ.get("PERSONAL_RAG_LANCE_DIR", "lancedb_data")

    # 1. Clear SQLite tables
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions")
        cursor.execute("DELETE FROM personal_events")
        cursor.execute("DELETE FROM documents")
        cursor.execute("DELETE FROM vector_sync_logs")
        conn.commit()

    # 2. Clear LanceDB directory
    if os.path.exists(lance_dir):
        try:
            shutil.rmtree(lance_dir)
            os.makedirs(lance_dir, exist_ok=True)
            LanceDBStore()
        except Exception as e:
            logger.warning(f"LanceDB directory reset warning: {e}")

    # 3. Clear uploads directory
    if os.path.exists("uploads"):
        try:
            shutil.rmtree("uploads")
            os.makedirs("uploads", exist_ok=True)
        except Exception as e:
            logger.warning(f"Uploads directory reset warning: {e}")

    logger.info("🧹 System database, vector store, and upload cache reset to fresh start.")
    return {"status": "SUCCESS", "message": "Database and vector store reset to fresh start successfully."}


def _chunk_text(
    text: str, 
    doc_id: int, 
    source_type: str = "text", 
    username: str = "default_user",
    is_secure: bool = False,
    source_file: str = "",
    chunk_size: int = 700,
) -> List[Dict[str, Any]]:
    """Splits text into semantically cohesive chunks by sentence and paragraph boundaries."""
    from src.backend.utils.chunker import semantic_chunk_text
    return semantic_chunk_text(
        text=text,
        doc_id=doc_id,
        source_type=source_type,
        username=username,
        is_secure=is_secure,
        source_file=source_file,
        target_chunk_size=chunk_size,
    )
