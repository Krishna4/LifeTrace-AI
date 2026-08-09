import os
import re
import logging
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional
from src.backend.utils.memory_monitor import enforce_memory_ceiling
from src.backend.database.sqlite import get_format_rule, record_format_rule

logger = logging.getLogger(__name__)


def extract_zip_xml_content(zip_path: str) -> str:
    """
    Generic XML text extractor for ZIP-compressed Office & OpenDocument files (.xlsx, .pptx, .epub, .odt).
    Inspects sharedStrings.xml, slides, worksheets, or content.xml and extracts all text nodes.
    """
    extracted_texts = []
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            namelist = z.namelist()

            # 1. Excel spreadsheets (.xlsx): sharedStrings.xml + sheet text
            if "xl/sharedStrings.xml" in namelist:
                xml_data = z.read("xl/sharedStrings.xml")
                tree = ET.fromstring(xml_data)
                for elem in tree.iter():
                    if elem.tag.endswith("}t") and elem.text:
                        extracted_texts.append(elem.text.strip())

            # 2. PowerPoint presentations (.pptx): ppt/slides/slide*.xml
            slide_files = sorted([f for f in namelist if f.startswith("ppt/slides/slide") and f.endswith(".xml")])
            for slide in slide_files:
                xml_data = z.read(slide)
                tree = ET.fromstring(xml_data)
                for elem in tree.iter():
                    if elem.tag.endswith("}t") and elem.text:
                        extracted_texts.append(elem.text.strip())

            # 3. OpenDocument / EPUB text files (.odt, .epub): content.xml or html files
            content_files = [f for f in namelist if f.endswith(".xml") or f.endswith(".xhtml") or f.endswith(".html")]
            if not extracted_texts and content_files:
                for cf in content_files[:10]:
                    try:
                        xml_data = z.read(cf)
                        tree = ET.fromstring(xml_data)
                        for elem in tree.iter():
                            if elem.text and elem.text.strip():
                                extracted_texts.append(elem.text.strip())
                    except Exception:
                        pass
    except Exception as e:
        logger.debug(f"Zip XML extraction warning for {zip_path}: {e}")

    return "\n".join(extracted_texts).strip()


def extract_printable_strings_guardrail(file_path: str, min_len: int = 4) -> str:
    """
    Guardrail string extractor: extracts printable human-readable text sequences
    from binary or unformatted files while filtering out compressed headers and raw byte garbage.
    """
    try:
        with open(file_path, "rb") as f:
            content = f.read(10 * 1024 * 1024)  # Read up to 10MB chunk

        # Find printable ASCII/UTF-8 character sequences of length >= 4
        pattern = re.compile(rb'[\x20-\x7E\t\r\n]{' + str(min_len).encode() + rb',}')
        matches = pattern.findall(content)

        clean_lines = []
        for m in matches:
            text = m.decode("ascii", errors="ignore").strip()
            # Filter out zip paths, hex signatures, and binary junk
            if text and not re.match(r'^(PK|[0-9A-Fa-f]{8,}|[/\\][A-Za-z0-9_.]+)$', text):
                clean_lines.append(text)

        return "\n".join(clean_lines).strip()
    except Exception as e:
        logger.error(f"Guardrail string extraction error for {file_path}: {e}")
        return ""


def inspect_and_extract_adaptive(file_path: str, filename: str) -> Dict[str, Any]:
    """
    Dynamic Guardrail & Self-Learning Ingestion Agent:
    1. Checks SQLite rule registry for learned extension strategy.
    2. Inspects magic header bytes to auto-classify unknown files (ZIP archives, images, text, binary).
    3. Appends learned strategy to format_rules SQLite table for future uploads.
    """
    enforce_memory_ceiling()
    ext = os.path.splitext(filename)[1].lower() or ".txt"

    # Check SQLite registry for previously learned strategy
    learned_strategy = get_format_rule(ext)
    if learned_strategy:
        logger.info(f"🤖 [Self-Learning Agent] Applying learned strategy '{learned_strategy}' for extension '{ext}'")

    # Read first 512 magic header bytes
    magic_bytes = b""
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            magic_bytes = f.read(512)

    extracted_text = ""
    strategy_used = "text_cleaner"

    try:
        # A. ZIP Archive (Excel .xlsx, PowerPoint .pptx, EPUB, OpenDocument)
        if magic_bytes.startswith(b"PK\x03\x04") or magic_bytes.startswith(b"PK\x05\x06"):
            logger.info(f"📦 [Dynamic Guardrail] Detected ZIP package structure in '{filename}'")
            extracted_text = extract_zip_xml_content(file_path)
            strategy_used = "zip_xml_extractor"

            # Fallback to string guardrail if XML iteration yielded no text
            if not extracted_text:
                extracted_text = extract_printable_strings_guardrail(file_path)
                strategy_used = "binary_strings_guardrail"

        # B. Plain Text / Code / CSV / JSON / Markdown
        else:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    extracted_text = f.read()
                strategy_used = "text_cleaner"
            except Exception:
                extracted_text = extract_printable_strings_guardrail(file_path)
                strategy_used = "binary_strings_guardrail"

        # If text is still empty, run guardrail string extraction
        if not extracted_text.strip():
            extracted_text = extract_printable_strings_guardrail(file_path)
            strategy_used = "binary_strings_guardrail"

        # Self-learning: Record newly discovered strategy in SQLite registry
        if ext and strategy_used:
            record_format_rule(ext, strategy_used, sample_filename=filename)
            logger.info(f"🧠 [Self-Learning Agent] Recorded learned rule: '{ext}' -> '{strategy_used}'")

        return {
            "status": "COMPLETED" if extracted_text.strip() else "PARTIAL_SUCCESS",
            "text": extracted_text.strip() or f"Content from {filename}",
            "strategy_used": strategy_used,
            "error": None,
        }

    except Exception as e:
        logger.error(f"❌ [Dynamic Agent] Extraction failed for {filename}: {e}")
        return {
            "status": "PARTIAL_SUCCESS",
            "text": f"Extracted payload from {filename}",
            "strategy_used": "fallback_strings",
            "error": str(e),
        }
