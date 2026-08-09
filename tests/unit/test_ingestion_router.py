import pytest
from src.backend.ingestion.router import validate_file_size, get_file_type, MAX_FILE_SIZE_BYTES


def test_validate_file_size_valid():
    # 10MB file should pass
    assert validate_file_size(10 * 1024 * 1024) is True


def test_validate_file_size_exceeded():
    # 501MB file should raise ValueError / HTTP 413
    oversized = MAX_FILE_SIZE_BYTES + 1
    with pytest.raises(ValueError, match="FILE_TOO_LARGE"):
        validate_file_size(oversized)


def test_get_file_type_routing():
    assert get_file_type("note.txt") == "text"
    assert get_file_type("audio.mp3") == "audio"
    assert get_file_type("video.mp4") == "video"
    assert get_file_type("receipt.png") == "image"
    assert get_file_type("https://example.com/article") == "web"
    assert get_file_type("unknown.xyz") == "text"  # fallback
