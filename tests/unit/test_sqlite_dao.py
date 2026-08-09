import os
from datetime import date
from src.backend.database.sqlite import (
    init_db,
    create_transaction,
    get_transactions,
    get_total_amount_by_entity,
)
from src.backend.models.pydantic_schemas import FinancialTransactionCreate

TEST_DB = "test_sqlite_dao.db"


def setup_module():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db(TEST_DB)


def teardown_module():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_create_and_get_transaction():
    tx = FinancialTransactionCreate(
        entity_person="Alex",
        amount=50.0,
        currency="USD",
        transaction_date=date(2026, 8, 8),
        notes="dinner",
    )
    created = create_transaction(tx, db_path=TEST_DB)
    assert created.id is not None
    assert created.entity_person == "Alex"
    assert created.amount == 50.0

    all_txs = get_transactions(entity_person="Alex", db_path=TEST_DB)
    assert len(all_txs) >= 1
    assert all_txs[0].amount == 50.0

    total_info = get_total_amount_by_entity("Alex", db_path=TEST_DB)
    assert total_info["total_amount"] >= 50.0
