import pytest
from src.backend.ingestion.financial_parser import (
    extract_financial_transactions,
    is_transaction_verified_by_slm,
)


def test_slm_financial_classification_valid():
    text = "I paid Venu $500 for dinner yesterday and transferred $250 to Alex."
    res = extract_financial_transactions(text)
    assert len(res) >= 2
    entities = [tx.entity_person for tx in res]
    amounts = [tx.amount for tx in res]
    assert "Venu" in entities
    assert "Alex" in entities
    assert 500.0 in amounts
    assert 250.0 in amounts


def test_slm_financial_classification_false_positives():
    tax_text = "Under section 194 and sec 115 of the Income Tax Act, paid 15 taxes."
    manual_text = "Set aircraft speed to Taxiway 4 according to AMM 32 manual."

    res_tax = extract_financial_transactions(tax_text)
    res_manual = extract_financial_transactions(manual_text)

    assert len(res_tax) == 0
    assert len(res_manual) == 0


def test_is_transaction_verified_by_slm_binary():
    assert is_transaction_verified_by_slm("paid Venu $500 for dinner") is True
    assert is_transaction_verified_by_slm("under section 194 of the tax act") is False
