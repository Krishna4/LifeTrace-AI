from unittest.mock import patch
from src.backend.ingestion.trafilatura_extractor import extract_url_content


def test_extract_url_content_mocked():
    with patch("trafilatura.fetch_url") as mock_fetch, patch("trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html><body><p>Article body content paid Alex $50</p></body></html>"
        mock_extract.return_value = "Article body content paid Alex $50"

        res = extract_url_content("https://example.com/test-article")
        assert res["status"] == "COMPLETED"
        assert "Article body content" in res["text_content"]
