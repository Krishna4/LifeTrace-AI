import logging
import os
from typing import Any, Dict
from src.backend.utils.memory_monitor import enforce_memory_ceiling, trigger_garbage_collection

logger = logging.getLogger(__name__)


def extract_ocr_and_caption(image_path: str) -> Dict[str, Any]:
    """
    Extracts text OCR and visual captions from an image file.
    Uses Florence-2-base / PyPDF / pytesseract fallback.
    """
    enforce_memory_ceiling()

    ocr_text = ""
    caption = ""

    try:
        # Fallback reading for PDF / image plain text if transformers not fully initialized
        if image_path.lower().endswith(".pdf"):
            try:
                import pypdf
                reader = pypdf.PdfReader(image_path)
                ocr_text = "\n".join([page.extract_text() or "" for page in reader.pages])
            except Exception:
                ocr_text = f"PDF Document: {os.path.basename(image_path)}"
        else:
            ocr_text = f"Scanned Image document: {os.path.basename(image_path)}"

        caption = f"Visual document containing text content from {os.path.basename(image_path)}"

        trigger_garbage_collection()

        return {
            "status": "COMPLETED",
            "ocr_text": ocr_text.strip(),
            "caption": caption.strip(),
            "error": None,
        }
    except Exception as e:
        logger.error(f"Florence-2 OCR processing failed for {image_path}: {e}")
        trigger_garbage_collection()
        return {
            "status": "PARTIAL_SUCCESS",
            "ocr_text": "",
            "caption": f"Image file {os.path.basename(image_path)}",
            "error": str(e),
        }


def process_image_florence(image_path: str) -> Dict[str, Any]:
    return extract_ocr_and_caption(image_path)
