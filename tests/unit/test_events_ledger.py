import pytest
from datetime import date
from fastapi.testclient import TestClient
from src.backend.main import app
from src.backend.models.pydantic_schemas import PersonalEventCreate
from src.backend.database.sqlite import (
    create_personal_event,
    get_personal_events,
    delete_personal_event,
)
from src.backend.ingestion.event_parser import extract_personal_events

client = TestClient(app)


def test_personal_events_dao():
    ev = PersonalEventCreate(
        title="Architecture Sync Meeting",
        category="MEETING",
        event_date=date(2026, 8, 9),
        location="Office Room 3B",
        entity_person="Alex",
        details="Discussed system memory ceiling and hybrid search design.",
    )
    created = create_personal_event(ev)
    assert created.id is not None
    assert created.title == "Architecture Sync Meeting"
    assert created.category == "MEETING"
    assert created.entity_person == "Alex"

    fetched = get_personal_events(category="MEETING", entity_person="Alex")
    assert len(fetched) >= 1
    assert any(e.id == created.id for e in fetched)

    deleted = delete_personal_event(created.id)
    assert deleted is True


def test_event_parser():
    note_text = """
    Team Standup Meeting
    Attended quarterly review call with Poojitha and Alex.
    Booked flight to Hyderabad for annual conference.
    Doctor appointment at City Hospital for annual health checkup.
    """
    events = extract_personal_events(note_text)
    assert len(events) >= 1
    categories = [e.category for e in events]
    assert any(c in ["MEETING", "TRAVEL", "HEALTH", "DAILY_EVENT"] for c in categories)


def test_events_api_endpoints():
    payload = {
        "title": "Annual Health Checkup",
        "category": "HEALTH",
        "event_date": "2026-08-09",
        "location": "City Clinic",
        "entity_person": "Dr. Smith",
        "details": "Routine blood test and vitals check.",
    }
    post_res = client.post("/api/v1/events", json=payload)
    assert post_res.status_code == 201
    ev_id = post_res.json()["id"]

    get_res = client.get("/api/v1/events", params={"category": "HEALTH"})
    assert get_res.status_code == 200
    events = get_res.json()
    assert any(e["id"] == ev_id for e in events)

    del_res = client.delete(f"/api/v1/events/{ev_id}")
    assert del_res.status_code == 200
