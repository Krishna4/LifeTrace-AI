import pytest
from datetime import date
from unittest.mock import patch, MagicMock
from src.backend.services.telegram_service import (
    format_event_for_telegram,
    publish_event_to_telegram,
    publish_daily_digest,
    handle_telegram_update,
    send_telegram_message,
)
from src.backend.database.sqlite import init_db, create_personal_event
from src.backend.models.pydantic_schemas import PersonalEventCreate


def test_format_event_for_telegram():
    event = {
        "title": "Dentist Checkup",
        "category": "HEALTH",
        "event_date": "2026-09-05",
        "location": "Apollo Clinic",
        "entity_person": "Dr. Rao",
        "details": "Routine scaling and cleaning.",
    }
    msg = format_event_for_telegram(event)
    assert "[HEALTH]" in msg
    assert "Dentist Checkup" in msg
    assert "Apollo Clinic" in msg
    assert "Dr. Rao" in msg
    assert "Routine scaling" in msg


@patch("src.backend.services.telegram_service.requests.post")
def test_send_telegram_message(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 12345}}

    res = send_telegram_message("Hello Test", bot_token="fake-token", chat_id="123456")
    assert res["success"] is True
    assert res["message_id"] == 12345


@patch("src.backend.services.telegram_service.send_telegram_message")
def test_publish_daily_digest(mock_send, tmp_path):
    test_db = str(tmp_path / "test_tg.db")
    init_db(test_db)

    ev = PersonalEventCreate(
        title="Sprint Sync Meeting",
        category="MEETING",
        event_date=date.today(),
        location="Google Meet",
        entity_person="Team",
        details="Quarterly planning",
        username="test_user",
    )
    create_personal_event(ev, db_path=test_db)

    mock_send.return_value = {"success": True, "message_id": 999}

    res = publish_daily_digest(username="test_user", bot_token="mock-token", chat_id="123", db_path=test_db)
    assert res["events_count"] >= 1
    assert res["delivery"]["success"] is True



@patch("src.backend.services.telegram_service.send_telegram_message")
def test_handle_telegram_webhook_commands(mock_send):
    mock_send.return_value = {"success": True}

    # /today command
    update_today = {
        "message": {
            "chat": {"id": 12345},
            "text": "/today",
            "from": {"first_name": "Alice"},
        }
    }
    res = handle_telegram_update(update_today, default_user="test_user")
    assert res is not None
    assert res["action"] == "today"

    # /help command
    update_help = {
        "message": {
            "chat": {"id": 12345},
            "text": "/help",
            "from": {"first_name": "Alice"},
        }
    }
    res_help = handle_telegram_update(update_help, default_user="test_user")
    assert res_help is not None
    assert res_help["action"] == "help"

    # Channel post
    update_channel = {
        "channel_post": {
            "chat": {"id": -100123456789},
            "text": "/today",
        }
    }
    res_chan = handle_telegram_update(update_channel, default_user="test_user")
    assert res_chan is not None
    assert res_chan["action"] == "today"
