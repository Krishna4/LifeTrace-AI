import os
import io
import logging
import tempfile
import subprocess
from typing import Any, Dict
from src.backend.utils.memory_monitor import enforce_memory_ceiling

logger = logging.getLogger(__name__)


def _extract_text_from_image_bytes(img_bytes: bytes) -> str:
    """Extracts text using pytesseract Python module or CLI tesseract fallback."""
    # Method 1: Try pytesseract module
    try:
        import pytesseract
        from PIL import Image

        for t_cmd in ["/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract", "tesseract"]:
            if os.path.exists(t_cmd) or t_cmd == "tesseract":
                pytesseract.pytesseract.tesseract_cmd = t_cmd
                break

        img = Image.open(io.BytesIO(img_bytes))
        txt = pytesseract.image_to_string(img)
        if txt.strip():
            return txt.strip()
    except Exception as e:
        logger.debug(f"pytesseract module OCR warning: {e}")

    # Method 2: Fallback to system tesseract CLI binary via subprocess
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(img_bytes)
            temp_path = tmp.name

        tess_bin = "/usr/local/bin/tesseract" if os.path.exists("/usr/local/bin/tesseract") else "tesseract"
        cmd = [tess_bin, temp_path, "stdout"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception as se:
        logger.error(f"CLI tesseract fallback error: {se}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    return ""


def run_ocr_on_pdf_images(reader: Any) -> str:
    """Fallback OCR extractor for scanned image-based PDFs."""
    ocr_texts = []
    try:
        for i, page in enumerate(reader.pages):
            page_ocr_lines = []
            if hasattr(page, "images") and page.images:
                for img_obj in page.images:
                    txt = _extract_text_from_image_bytes(img_obj.data)
                    if txt:
                        page_ocr_lines.append(txt)
            if page_ocr_lines:
                ocr_texts.append(f"[Page {i+1} OCR]\n" + "\n".join(page_ocr_lines))
    except Exception as e:
        logger.error(f"PDF Tesseract OCR fallback error: {e}")

    return "\n\n".join(ocr_texts)


def extract_pdf_text(pdf_path: str) -> Dict[str, Any]:
    """
    Extracts text from PDF documents using pypdf.
    Falls back to Tesseract OCR for scanned / image-based PDFs.
    """
    enforce_memory_ceiling()

    if not os.path.exists(pdf_path):
        return {
            "status": "PARTIAL_SUCCESS",
            "text": f"PDF File: {os.path.basename(pdf_path)}",
            "error": f"File does not exist: {pdf_path}",
        }

    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        extracted_pages = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            if page_text.strip():
                extracted_pages.append(f"[Page {i+1}]\n{page_text.strip()}")

        text = "\n\n".join(extracted_pages)

        # Scanned PDF Fallback: if pypdf extracted no text, run image OCR
        if not text.strip() or len(text.strip()) < 30:
            logger.info(f"📸 Detected scanned/image PDF for {pdf_path}. Running Tesseract OCR fallback...")
            ocr_text = run_ocr_on_pdf_images(reader)
            if ocr_text.strip():
                text = ocr_text

        if not text.strip():
            text = f"Scanned PDF Document: {os.path.basename(pdf_path)}"

        return {
            "status": "COMPLETED",
            "text": text.strip(),
            "error": None,
        }
    except Exception as e:
        logger.error(f"PyPDF extraction error for {pdf_path}: {e}")
        return {
            "status": "PARTIAL_SUCCESS",
            "text": f"PDF document content from {os.path.basename(pdf_path)}",
            "error": str(e),
        }
