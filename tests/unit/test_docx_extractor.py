import os
import zipfile
import pytest
from src.backend.ingestion.docx_extractor import extract_docx_text

TEST_DOCX = "test_sample.docx"


def setup_module():
    # Create a minimal valid .docx zip file containing word/document.xml
    xml_content = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        '<w:p><w:r><w:t>Hello World from DOCX Parser</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Paid Alex $150 for consulting invoice</w:t></w:r></w:p>'
        '</w:body>'
        '</w:document>'
    )
    with zipfile.ZipFile(TEST_DOCX, "w") as z:
        z.writestr("word/document.xml", xml_content)


def teardown_module():
    if os.path.exists(TEST_DOCX):
        os.remove(TEST_DOCX)


def test_extract_docx_text():
    res = extract_docx_text(TEST_DOCX)
    assert res["status"] == "COMPLETED"
    assert "Hello World from DOCX Parser" in res["text"]
    assert "Paid Alex $150 for consulting invoice" in res["text"]
    assert "word/theme/theme1.xml" not in res["text"]
    assert "PK" not in res["text"]
