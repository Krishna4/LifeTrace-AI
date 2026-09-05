import os
test_db = "scratch/test_retrieval.db"
os.makedirs("scratch", exist_ok=True)
if os.path.exists(test_db):
    os.remove(test_db)
os.environ["PERSONAL_RAG_DB_PATH"] = test_db

from datetime import date
from src.backend.database.sqlite import init_db, get_personal_events
from src.backend.models.pydantic_schemas import PersonalEventCreate, QueryRouteResponse
from src.backend.query.search_engine import execute_unified_search
from src.backend.main import add_event

init_db(test_db)


# 1. Feed some sample personal life events
events = [
    PersonalEventCreate(
        title="Dentist checkup and cleaning",
        category="HEALTH",
        event_date=date.today(),
        location="Apollo Dental Clinic",
        entity_person="Dr. Rao",
        details="Routine dental checkup, cavity inspection, and scaling.",
        username="Alice"
    ),
    PersonalEventCreate(
        title="Project sync with team",
        category="MEETING",
        event_date=date.today(),
        location="Google Meet",
        entity_person="Bob and Charlie",
        details="Discussed LifeTrace AI sprint backlog and deployment targets.",
        username="Alice"
    ),
    PersonalEventCreate(
        title="Flight to Hyderabad",
        category="TRAVEL",
        event_date=date.today(),
        location="Rajiv Gandhi International Airport",
        entity_person=None,
        details="Flight 6E-204 departed at 10:30 AM, landed at 12:45 PM.",
        username="Alice"
    ),
    PersonalEventCreate(
        title="Dinner at Paradise Biryani with Poojitha",
        category="DINING",
        event_date=date.today(),
        location="Paradise Secunderabad",
        entity_person="Poojitha",
        details="Had special mutton biryani and mirchi ka salan.",
        username="Alice"
    )
]

for ev in events:
    add_event(ev)

print(f"Total events in SQLite: {len(get_personal_events(username='Alice'))}")


# Test queries:
queries = [
    "What events do I have today?",
    "What meetings are scheduled?",
    "Did I visit the dentist?",
    "Show my travel details",
    "Where did I have dinner?"
]

from src.backend.query.slm_router import route_query_slm

for q in queries:
    route = route_query_slm(q)
    res = execute_unified_search(q, route, username="Alice")
    print(f"\n==========================================", flush=True)
    print(f"User Query: '{q}'", flush=True)
    print(f"Router Engine: [{route.target_engine}] - {route.rationale}", flush=True)
    print(f"Events Found in DB: {len(res.get('events', []))}", flush=True)
    for ev in res.get('events', []):
        print(f"  -> [{ev['category']}] {ev['title']} ({ev['event_date']})", flush=True)
    print(f"Final Answer:\n{res.get('answer')}", flush=True)


