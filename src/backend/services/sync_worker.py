import os
import logging
from typing import Dict, Any
from src.backend.database.sqlite import (
    get_unindexed_documents,
    update_document_status,
    update_document_vector_sync,
    log_vector_sync_event,
    create_transaction,
)
from src.backend.database.lancedb_store import LanceDBStore

logger = logging.getLogger(__name__)


def reconcile_vector_sync() -> Dict[str, Any]:
    """
    Background worker that fetches ALL documents with UNINDEXED or SYNC_FAILED status,
    extracts their text, parses financial statements, and indexes them into LanceDB with tenant metadata.
    """
    from src.backend.main import _chunk_text
    from src.backend.ingestion.pdf_extractor import extract_pdf_text
    from src.backend.ingestion.docx_extractor import extract_docx_text
    from src.backend.ingestion.florence_ocr import process_image_florence
    from src.backend.ingestion.whisper_transcriber import transcribe_audio
    from src.backend.ingestion.trafilatura_extractor import extract_url_content
    from src.backend.ingestion.dynamic_agent import inspect_and_extract_adaptive
    from src.backend.ingestion.financial_parser import extract_financial_transactions

    logger.info("🔄 Starting background vector sync reconciliation worker...")
    unindexed = get_unindexed_documents()
    logger.info(f"📊 Found {len(unindexed)} unindexed/failed document(s) requiring vector indexing.")

    if not unindexed:
        logger.info("✨ Vector sync status is up to date (0 documents to sync).")
        return {
            "unindexed_found": 0,
            "reconciled_count": 0,
            "failed_count": 0,
        }

    lancedb_store = LanceDBStore()
    reconciled_count = 0
    failed_count = 0

    for doc in unindexed:
        file_path = doc.file_path
        filename = os.path.basename(file_path)
        file_type = doc.file_type
        username = doc.username or "default_user"
        is_secure = doc.is_secure or False
        logger.info(f"⚙️ Reconciling doc #{doc.id}: '{filename}' ({file_type}) for user '{username}'...")

        try:
            update_document_status(doc.id, "PROCESSING")
            extracted_text = ""

            if file_type == "pdf":
                res = extract_pdf_text(file_path)
                extracted_text = res.get("text", "")
            elif file_type == "docx":
                res = extract_docx_text(file_path)
                extracted_text = res.get("text", "")
            elif file_type in ["audio", "video"]:
                res = transcribe_audio(file_path)
                extracted_text = " ".join([s["text"] for s in res.get("segments", [])])
            elif file_type == "image":
                res = process_image_florence(file_path)
                extracted_text = f"{res.get('ocr_text', '')}\n{res.get('caption', '')}".strip()
            elif file_type == "web":
                res = extract_url_content(file_path)
                extracted_text = res.get("text_content", "")
            else:
                res = inspect_and_extract_adaptive(file_path, filename)
                extracted_text = res.get("text", "")

            if extracted_text and extracted_text.strip():
                # Parse financial entries
                txs = extract_financial_transactions(
                    extracted_text, 
                    source_document_id=doc.id,
                    username=username,
                    is_secure=is_secure,
                )
                for tx in txs:
                    try:
                        create_transaction(tx)
                    except Exception:
                        pass

                # Index into LanceDB
                chunks = _chunk_text(
                    extracted_text, 
                    doc.id, 
                    source_type=file_type,
                    username=username,
                    is_secure=is_secure,
                    source_file=filename,
                )
                count = lancedb_store.add_chunks(chunks)
                update_document_status(doc.id, "COMPLETED")
                update_document_vector_sync(doc.id, "INDEXED")
                log_vector_sync_event(doc.id, count, "SUCCESS")
                reconciled_count += 1
                logger.info(f"✅ Successfully indexed {count} chunk(s) into LanceDB for doc #{doc.id} ('{filename}').")
            else:
                update_document_status(doc.id, "COMPLETED")
                update_document_vector_sync(doc.id, "INDEXED")
                reconciled_count += 1
        except Exception as e:
            logger.error(f"❌ Sync failed for doc #{doc.id} ('{filename}'): {e}")
            update_document_status(doc.id, "FAILED", error_log=str(e))
            update_document_vector_sync(doc.id, "SYNC_FAILED")
            log_vector_sync_event(doc.id, 0, "FAILED", error_message=str(e))
            failed_count += 1

    logger.info(f"🎉 Vector sync reconciliation complete. Reconciled: {reconciled_count}, Failed: {failed_count}.")
    return {
        "unindexed_found": len(unindexed),
        "reconciled_count": reconciled_count,
        "failed_count": failed_count,
    }
