import logging
import os
from typing import Any, Dict, List, Optional
from src.backend.utils.memory_monitor import enforce_memory_ceiling, trigger_garbage_collection

logger = logging.getLogger(__name__)

_whisper_model_instance = None


def get_whisper_model():
    """Lazily load faster-whisper tiny model on CPU/int8 to preserve RAM."""
    global _whisper_model_instance
    if _whisper_model_instance is None:
        enforce_memory_ceiling()
        try:
            from faster_whisper import WhisperModel
            # Load tiny model with int8 quantization for minimal memory usage (< 500MB RAM)
            _whisper_model_instance = WhisperModel("tiny", device="cpu", compute_type="int8")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper model: {e}")
            raise e
    return _whisper_model_instance


def transcribe_audio(
    file_path: str, resume_offset: float = 0.0
) -> Dict[str, Any]:
    """
    Transcribes audio/video file starting from resume_offset.
    Returns status dict with segments, timestamps, and resume offset.
    """
    enforce_memory_ceiling()

    if not os.path.exists(file_path) and not file_path.endswith(".mp3"):
        logger.warning(f"File path does not exist locally: {file_path}")

    try:
        model = get_whisper_model()
        segments_raw, info = model.transcribe(file_path, beam_size=1)

        segments: List[Dict[str, Any]] = []
        last_offset = resume_offset

        for segment in segments_raw:
            if segment.start < resume_offset:
                continue
            segments.append(
                {
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": segment.text.strip(),
                }
            )
            last_offset = round(segment.end, 2)

        trigger_garbage_collection()

        return {
            "status": "COMPLETED",
            "segments": segments,
            "resume_offset": last_offset,
            "language": getattr(info, "language", "en"),
            "error": None,
        }
    except Exception as e:
        logger.error(f"Speech transcription failed for {file_path}: {e}")
        trigger_garbage_collection()
        return {
            "status": "PARTIAL_TRANSCRIPT",
            "segments": [],
            "resume_offset": resume_offset,
            "error": str(e),
        }
