import os
import zipfile
import pytest
from src.backend.ingestion.dynamic_agent import inspect_and_extract_adaptive, extract_printable_strings_guardrail
from src.backend.database.sqlite import get_format_rule, record_format_rule

TEST_XLSX = "sample_data.xlsx"
TEST_BIN = "sample_data.unknown"


def setup_module():
    # 1. Create mock Excel .xlsx zip containing xl/sharedStrings.xml
    xml_content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<si><t>Q3 Budget Statement</t></si>'
        '<si><t>Paid Alex $250 for software design</t></si>'
        '</sst>'
    )
    with zipfile.ZipFile(TEST_XLSX, "w") as z:
        z.writestr("xl/sharedStrings.xml", xml_content)

    # 2. Create mock binary file
    with open(TEST_BIN, "wb") as f:
        f.write(b"\x7fELF\x01\x01\x00Header garbage bytes\x00\x00Project Roadmap Q4 Delivery\x00\x00")


def teardown_module():
    for f in [TEST_XLSX, TEST_BIN]:
        if os.path.exists(f):
            os.remove(f)


def test_dynamic_agent_xlsx_ingestion():
    res = inspect_and_extract_adaptive(TEST_XLSX, "sample_data.xlsx")
    assert res["status"] == "COMPLETED"
    assert "Q3 Budget Statement" in res["text"]
    assert "Paid Alex $250 for software design" in res["text"]
    assert res["strategy_used"] == "zip_xml_extractor"

    # Verify self-learning persistence in SQLite registry
    learned = get_format_rule(".xlsx")
    assert learned == "zip_xml_extractor"


def test_dynamic_agent_binary_guardrail():
    res = inspect_and_extract_adaptive(TEST_BIN, "sample_data.unknown")
    assert "Project Roadmap Q4 Delivery" in res["text"]
    assert "PK" not in res["text"]

    learned = get_format_rule(".unknown")
    assert learned in ["text_cleaner", "binary_strings_guardrail"]
