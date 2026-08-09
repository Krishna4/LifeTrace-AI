from unittest.mock import MagicMock, patch
from src.backend.ingestion.florence_ocr import process_image_florence


def test_process_image_florence_mocked():
    with patch("src.backend.ingestion.florence_ocr.extract_ocr_and_caption") as mock_extract:
        mock_extract.return_value = {
            "status": "COMPLETED",
            "ocr_text": "Invoice #1024 Paid Alex $50.00",
            "caption": "A receipt document on a desk",
        }
        res = process_image_florence("dummy_image.png")
        assert res["status"] == "COMPLETED"
        assert "Invoice" in res["ocr_text"]
        assert "receipt" in res["caption"]
