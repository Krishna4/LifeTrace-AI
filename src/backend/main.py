import os
import shutil
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, File, UploadFile, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from src.backend.models.pydantic_schemas import (
    DocumentCreate,
    DocumentResponse,
    IngestUrlRequest,
    FinancialTransactionCreate,
    FinancialTransactionResponse,
    PersonalEventCreate,
    PersonalEventResponse,
    QueryRouteRequest,
    QueryRouteResponse,
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
from src.backend.ingestion.event_parser import extract_personal_events
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
    version="1.1.0",
    description="Private Multimodal Intelligence, Personal Life Ledger & Expense Tracker System API",
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
    return {
        "status": "healthy",
        "ram_usage_mb": mem_info["process_ram_mb"],
        "max_memory_ceiling_mb": mem_info["max_ceiling_mb"],
        "system_used_ram_mb": mem_info["system_used_ram_mb"],
        "is_under_memory_limit": mem_info["is_under_limit"],
    }


def _process_document_background(doc_id: int, file_path: str, file_type: str, filename: str):
    """Background worker for asynchronous document ingestion and vector indexing."""
    import time
    start_t = time.time()
    logger.info(f"⚙️ [Async Job #{doc_id}] Starting background processing for '{filename}' ({file_type})...")
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

        update_document_status(doc_id, processing_status, error_log=error_log)
        logger.info(f"📝 [Async Job #{doc_id}] Extracted {len(extracted_text)} characters in {time.time() - start_t:.2f}s")

        # Extract monetary transactions & personal events
        if extracted_text:
            txs = extract_financial_transactions(extracted_text, source_document_id=doc_id)
            if txs:
                logger.info(f"💸 [Async Job #{doc_id}] Found {len(txs)} transaction(s)")
                for tx in txs:
                    create_transaction(tx)

            events = extract_personal_events(extracted_text, source_document_id=doc_id)
            if events:
                logger.info(f"📅 [Async Job #{doc_id}] Found {len(events)} personal event(s)")
                for ev in events:
                    create_personal_event(ev)

            # Index into LanceDB
            vec_start = time.time()
            chunks = _chunk_text(extracted_text, doc_id, source_type=file_type)
            logger.info(f"🧩 [Async Job #{doc_id}] Indexing {len(chunks)} chunk(s) into LanceDB...")
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
def ingest_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Uploads file and enqueues an asynchronous background processing job (Returns < 50ms)."""
    enforce_memory_ceiling()

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)

    logger.info(f"📥 Received file upload request: '{file.filename}' ({file_size / (1024*1024):.2f} MB)")

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
    )
    doc_rec = create_document(doc_create)

    # Schedule background task
    background_tasks.add_task(_process_document_background, doc_rec.id, file_path, file_type, file.filename or "file")

    return {
        "document_id": doc_rec.id,
        "filename": file.filename,
        "file_type": file_type,
        "status": "PROCESSING",
        "message": "File uploaded successfully. Async background processing job enqueued.",
    }


@app.post("/api/v1/ingest/files", summary="Ingest Multiple Files (Batch Async Jobs)", status_code=status.HTTP_202_ACCEPTED)
def ingest_multiple_files(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...)):
    """Uploads multiple files in batch and enqueues asynchronous background processing jobs."""
    enforce_memory_ceiling()

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
        )
        doc_rec = create_document(doc_create)

        background_tasks.add_task(_process_document_background, doc_rec.id, file_path, file_type, filename)
        enqueued_jobs.append({
            "document_id": doc_rec.id,
            "filename": filename,
            "file_type": file_type,
            "status": "PROCESSING",
        })

    return {
        "enqueued_count": len(enqueued_jobs),
        "jobs": enqueued_jobs,
        "message": f"Enqueued {len(enqueued_jobs)} file(s) for background ingestion.",
    }


@app.post("/api/v1/ingest/url", summary="Ingest URL (Async Background Job)", status_code=status.HTTP_202_ACCEPTED)
def ingest_url(background_tasks: BackgroundTasks, req: IngestUrlRequest):
    """Enqueues web URL scraping and indexing background job (Returns < 50ms)."""
    enforce_memory_ceiling()
    logger.info(f"🌐 Received URL scraping request: '{req.url}'")

    file_type = "web"
    doc_create = DocumentCreate(
        file_path=req.url,
        file_type=file_type,
        file_size_bytes=0,
    )
    doc_rec = create_document(doc_create)

    background_tasks.add_task(_process_document_background, doc_rec.id, req.url, file_type, req.url)

    return {
        "document_id": doc_rec.id,
        "url": req.url,
        "status": "PROCESSING",
        "message": "Web URL job enqueued for background processing.",
    }


@app.get("/api/v1/documents", summary="List Ingestion Jobs & Documents")
def list_documents(limit: int = 50):
    """Lists document ingestion jobs with real-time status and vector sync metrics."""
    return get_all_documents(limit=limit)


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
def list_transactions(entity_person: Optional[str] = None, limit: int = 50):
    """Lists structured financial transactions, optionally filtered by counterparty name."""
    return get_transactions(entity_person=entity_person, limit=limit)


@app.post("/api/v1/transactions", summary="Create Manual Transaction", status_code=status.HTTP_201_CREATED)
def add_transaction(tx: FinancialTransactionCreate):
    """Manually creates a new financial transaction record."""
    return create_transaction(tx)


@app.get("/api/v1/events", summary="List Personal Events & Daily Life Logs")
def list_events(category: Optional[str] = None, entity_person: Optional[str] = None, limit: int = 50):
    """Retrieves personal events and daily life logs filtered by category or person."""
    return get_personal_events(category=category, entity_person=entity_person, limit=limit)


@app.post("/api/v1/events", summary="Create Personal Event Log", status_code=status.HTTP_201_CREATED)
def add_event(ev: PersonalEventCreate):
    """Manually logs a new personal event or daily life entry."""
    return create_personal_event(ev)


@app.delete("/api/v1/events/{event_id}", summary="Delete Personal Event Log")
def remove_event(event_id: int):
    """Deletes a personal event record by ID."""
    success = delete_personal_event(event_id)
    if not success:
        raise HTTPException(status_code=404, detail="Event record not found")
    return {"message": f"Event #{event_id} deleted successfully."}


@app.post("/api/v1/query", summary="Execute RAG Query via SLM Router")
def query_rag(req: QueryRouteRequest):
    """Routes query via SLM into SQL, Vector, or Hybrid execution path."""
    enforce_memory_ceiling()
    logger.info(f"💬 Received RAG query: '{req.query}'")
    route = route_query_slm(req.query)
    logger.info(f"🔀 SLM Query Router decision: [{route.target_engine}] - {route.rationale}")
    search_result = execute_unified_search(req.query, route)
    logger.info(f"🔍 Search complete. Answer generated.")
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


def _chunk_text(text: str, doc_id: int, source_type: str = "text", chunk_size: int = 400) -> List[Dict[str, Any]]:
    """Splits text into sliding character chunks."""
    if not text:
        return []
    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        segment = text[start:end].strip()
        if segment:
            chunks.append(
                {
                    "document_id": doc_id,
                    "chunk_index": idx,
                    "text_content": segment,
                    "source_type": source_type,
                }
            )
            idx += 1
        start += chunk_size - 50  # 50 char overlap
    return chunks
