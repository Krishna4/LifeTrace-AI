import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def extract_url_content(url: str) -> Dict[str, Any]:
    """
    Fetches web URL and extracts clean article text while stripping ads and navigation.
    """
    try:
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning(f"Could not fetch URL: {url}")
            return {
                "status": "PARTIAL_SUCCESS",
                "text_content": f"Web URL link: {url}",
                "title": url,
                "error": "Failed to fetch remote HTML body",
            }

        extracted = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )

        text = extracted or f"Web page content from {url}"

        return {
            "status": "COMPLETED",
            "text_content": text.strip(),
            "title": url,
            "error": None,
        }
    except Exception as e:
        logger.error(f"Trafilatura URL extraction error for {url}: {e}")
        return {
            "status": "PARTIAL_SUCCESS",
            "text_content": f"Web URL: {url}",
            "title": url,
            "error": str(e),
        }
