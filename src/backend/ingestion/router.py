import os
import logging
from typing import Literal

logger = logging.getLogger(__name__)

# Strict 500MB upload size ceiling from Constitution / FR-014
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024


def validate_file_size(size_bytes: int) -> bool:
    """Validates that file size does not exceed the 500MB ceiling."""
    if size_bytes > MAX_FILE_SIZE_BYTES:
        err = f"FILE_TOO_LARGE: File size ({size_bytes / (1024*1024):.2f}MB) exceeds maximum limit of 500MB."
        logger.error(err)
        raise ValueError(err)
    return True


def get_file_type(filename: str) -> Literal["text", "pdf", "docx", "audio", "video", "image", "web"]:
    """Determines file type category from filename extension or URI format."""
    if filename.startswith("http://") or filename.startswith("https://"):
        return "web"

    ext = os.path.splitext(filename)[1].lower()

    if ext in [".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"]:
        return "audio"
    elif ext in [".mp4", ".mkv", ".avi", ".mov", ".webm"]:
        return "video"
    elif ext in [".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"]:
        return "image"
    elif ext == ".pdf":
        return "pdf"
    elif ext in [".docx", ".doc"]:
        return "docx"
    else:
        # Default all text, markdown, code, log, csv, json, and document extensions to text
        return "text"
