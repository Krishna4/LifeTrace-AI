import re
from typing import List, Dict, Any, Optional

def semantic_chunk_text(
    text: str,
    doc_id: int,
    source_type: str = "text",
    username: str = "default_user",
    is_secure: bool = False,
    source_file: str = "",
    target_chunk_size: int = 700,
    overlap_sentences: int = 1,
) -> List[Dict[str, Any]]:
    """
    Splits text into semantically cohesive chunks by respecting paragraph
    and sentence boundaries rather than arbitrary character cuts.
    Prepends document metadata header for superior embedding & retrieval accuracy.
    """
    if not text or not text.strip():
        return []

    # Clean whitespace and normalize line breaks
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    # Break into sentences
    sentences: List[str] = []
    for p in paragraphs:
        # Split on sentence boundaries: periods, question marks, exclamation marks, or newlines
        p_sentences = re.split(r'(?<=[.!?])\s+|\n+', p)
        for s in p_sentences:
            s_clean = s.strip()
            if s_clean:
                sentences.append(s_clean)

    if not sentences:
        return []

    chunks: List[Dict[str, Any]] = []
    current_chunk_sentences: List[str] = []
    current_length = 0
    chunk_index = 0

    header = f"[{source_file or 'Document'} ({source_type})]" if source_file else ""

    for s in sentences:
        s_len = len(s) + 1
        if current_length + s_len > target_chunk_size and current_chunk_sentences:
            # Finalize current chunk
            body_text = " ".join(current_chunk_sentences)
            full_content = f"{header}\n{body_text}".strip() if header else body_text

            chunks.append({
                "document_id": doc_id,
                "chunk_index": chunk_index,
                "text_content": full_content,
                "source_type": source_type,
                "username": username,
                "is_secure": is_secure,
                "source_file": source_file,
                "doc_type": source_type,
            })
            chunk_index += 1

            # Keep overlap sentences for continuity
            overlap = current_chunk_sentences[-overlap_sentences:] if overlap_sentences > 0 else []
            current_chunk_sentences = list(overlap)
            current_length = sum(len(x) + 1 for x in current_chunk_sentences)

        current_chunk_sentences.append(s)
        current_length += s_len

    # Final residual chunk
    if current_chunk_sentences:
        body_text = " ".join(current_chunk_sentences)
        full_content = f"{header}\n{body_text}".strip() if header else body_text
        chunks.append({
            "document_id": doc_id,
            "chunk_index": chunk_index,
            "text_content": full_content,
            "source_type": source_type,
            "username": username,
            "is_secure": is_secure,
            "source_file": source_file,
            "doc_type": source_type,
        })

    return chunks
