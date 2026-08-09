from datetime import date
from src.backend.ingestion.financial_parser import extract_financial_transactions


def test_extract_financial_transactions_regex():
    text = "Yesterday I paid Alex $50 for dinner and later transferred $12.50 to Sarah."
    txs = extract_financial_transactions(text, source_document_id=1)

    assert len(txs) >= 1
    alex_tx = next((t for t in txs if t.entity_person == "Alex"), None)
    assert alex_tx is not None
    assert alex_tx.amount == 50.0
    assert alex_tx.currency == "USD"
    assert alex_tx.source_document_id == 1
