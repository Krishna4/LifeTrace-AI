from unittest.mock import MagicMock, patch
from src.backend.ingestion.whisper_transcriber import transcribe_audio


def test_transcribe_audio_mocked():
    mock_segment_1 = MagicMock(start=0.0, end=5.0, text="Hello world, paid Alex $50.")
    mock_segment_2 = MagicMock(start=5.0, end=10.0, text="Thank you very much.")

    with patch("src.backend.ingestion.whisper_transcriber.get_whisper_model") as mock_get_model:
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment_1, mock_segment_2], MagicMock())
        mock_get_model.return_value = mock_model

        result = transcribe_audio("dummy_path.mp3", resume_offset=0.0)

        assert result["status"] == "COMPLETED"
        assert len(result["segments"]) == 2
        assert result["segments"][0]["text"] == "Hello world, paid Alex $50."
        assert result["segments"][0]["start"] == 0.0
