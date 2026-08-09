import json
import re
import os
import requests
import logging
from datetime import date
from typing import List, Optional, Dict, Any
from src.backend.models.pydantic_schemas import PersonalEventCreate
from src.backend.query.slm_router import is_ollama_online, OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

EVENT_KEYWORDS = [
    r"\b(?:meeting|appointment|sync|discussion|standup|call)\b",
    r"\b(?:flight|train|hotel|trip|travel|visited|stayed|arrived|departed|booked)\b",
    r"\b(?:doctor|hospital|clinic|workout|gym|run|walk|medicine|health|checkup)\b",
    r"\b(?:birthday|anniversary|joined|graduated|started|completed|passed|celebrated|won)\b",
    r"\b(?:remember|reminder|todo|note|journal|log)\b",
]


def extract_events_with_slm(text: str, source_document_id: Optional[int] = None) -> List[PersonalEventCreate]:
    """
    Uses local SLM (Qwen-2.5-1.5B via Ollama) to extract structured daily events and milestones.
    """
    if not is_ollama_online() or not text:
        return []

    prompt = f"""System: You extract personal life events, meetings, travel, health logs, and milestones.
Analyze the text snippet and extract structured events.

Return JSON array of events matching this schema:
[
  {{
    "title": "Short event title",
    "category": "DAILY_EVENT" | "MEETING" | "TRAVEL" | "HEALTH" | "MILESTONE" | "REMINDER",
    "event_date": "YYYY-MM-DD",
    "location": "Optional location or null",
    "entity_person": "Optional person involved or null",
    "details": "Summary of details"
  }}
]

Text: "{text[:1500].strip()}"
JSON Output:"""

    events: List[PersonalEventCreate] = []
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        res = requests.post(OLLAMA_URL, json=payload, timeout=3.5)
        if res.status_code == 200:
            raw = res.json().get("response", "")
            data = json.loads(raw)
            if isinstance(data, dict) and "events" in data:
                items = data["events"]
            elif isinstance(data, list):
                items = data
            elif isinstance(data, dict) and data.get("title"):
                items = [data]
            else:
                items = []

            for item in items:
                if isinstance(item, dict) and item.get("title"):
                    try:
                        d_str = str(item.get("event_date") or date.today().isoformat())
                        ev_date = date.fromisoformat(d_str)
                    except Exception:
                        ev_date = date.today()

                    cat = str(item.get("category", "DAILY_EVENT")).upper()
                    if cat not in ["DAILY_EVENT", "MEETING", "TRAVEL", "HEALTH", "MILESTONE", "REMINDER"]:
                        cat = "DAILY_EVENT"

                    ev = PersonalEventCreate(
                        title=str(item["title"]).strip(),
                        category=cat,
                        event_date=ev_date,
                        location=str(item.get("location")) if item.get("location") and str(item.get("location")).lower() != "null" else None,
                        entity_person=str(item.get("entity_person")) if item.get("entity_person") and str(item.get("entity_person")).lower() != "null" else None,
                        details=str(item.get("details")) if item.get("details") and str(item.get("details")).lower() != "null" else None,
                        source_document_id=source_document_id,
                    )
                    events.append(ev)
    except Exception as e:
        logger.debug(f"SLM event extraction warning: {e}")

    return events


def extract_personal_events(
    text: str, source_document_id: Optional[int] = None
) -> List[PersonalEventCreate]:
    """
    Extracts personal events, meetings, travel logs, and health entries from text input.
    """
    if not text:
        return []

    # 1. Try SLM extraction first
    events = extract_events_with_slm(text, source_document_id)
    if events:
        return events

    # 2. Rule-based candidate pattern matching fallback
    results: List[PersonalEventCreate] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for line in lines[:20]:
        for pat in EVENT_KEYWORDS:
            if re.search(pat, line, re.IGNORECASE) and len(line) >= 10:
                cat = "DAILY_EVENT"
                if re.search(r"meeting|sync|call", line, re.IGNORECASE):
                    cat = "MEETING"
                elif re.search(r"flight|hotel|trip|travel", line, re.IGNORECASE):
                    cat = "TRAVEL"
                elif re.search(r"food|zomato|swiggy|biryani|lunch|dinner|breakfast|restaurant|coffee", line, re.IGNORECASE):
                    cat = "DINING"
                elif re.search(r"doctor|gym|workout|health|clinic", line, re.IGNORECASE):
                    cat = "HEALTH"
                elif re.search(r"birthday|graduated|joined|celebrated", line, re.IGNORECASE):
                    cat = "MILESTONE"

                ev = PersonalEventCreate(
                    title=line[:80],
                    category=cat,
                    event_date=date.today(),
                    details=line,
                    source_document_id=source_document_id,
                )
                results.append(ev)
                break

    return results


def process_journal_entry(raw_text: str, entry_date: Optional[date] = None) -> Dict[str, Any]:
    """
    Uses a SINGLE unified SLM call to analyze a free-text daily journal entry.
    Simultaneously extracts: summary, mood, insights, personal life events AND financial transactions.
    The same sentence (e.g. "Spent 1500 INR at Tirumala") produces BOTH a TRAVEL event and a transaction.
    Falls back to separate rule-based extractors if Ollama is offline.
    """
    from src.backend.ingestion.financial_parser import extract_financial_transactions

    journal_dt = entry_date or date.today()
    today_str = journal_dt.isoformat()

    # Default fallback values
    summary = raw_text[:200].strip()
    mood = "REFLECTIVE"
    insights: List[Any] = []
    events: List[PersonalEventCreate] = []
    transactions: List[Any] = []

    if is_ollama_online() and raw_text.strip():
        prompt = f"""System: You are a personal life and finance assistant analyzing a daily journal entry.
From the text below, extract ALL of the following simultaneously in a single JSON response:

1. A clean 2-sentence summary
2. Overall mood (POSITIVE | REFLECTIVE | TIRED | EXCITING | ANXIOUS)
3. 2 key insights or takeaways
4. All personal life events (meetings, travel, dining, health, social activities, reminders)
5. All financial transactions (money spent, paid, given, borrowed)

IMPORTANT: The same sentence CAN and SHOULD generate BOTH an event AND a transaction entry.
Example: "Spent 1500 INR at Tirumala for Seva" -> 1 TRAVEL event + 1 transaction (Tirumala, 1500 INR)

Return ONLY this JSON schema:
{{
  "summary": "Two sentence summary",
  "mood": "MOOD_NAME",
  "key_insights": ["insight 1", "insight 2"],
  "events": [
    {{
      "title": "Short event title",
      "category": "DAILY_EVENT | MEETING | TRAVEL | HEALTH | MILESTONE | REMINDER | DINING",
      "event_date": "{today_str}",
      "location": "Location or null",
      "entity_person": "Person involved or null",
      "details": "Details of the event"
    }}
  ],
  "transactions": [
    {{
      "entity_person": "Person or merchant name (e.g. Tirumala, Zomato, Venu)",
      "amount": 100.0,
      "currency": "INR | USD | EUR | GBP",
      "notes": "What the payment was for"
    }}
  ]
}}

Do NOT extract tax/legal section references as transactions.

Journal Entry: "{raw_text[:2000].strip()}"
JSON Output:"""

        try:
            payload = {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            }
            res = requests.post(OLLAMA_URL, json=payload, timeout=8.0)
            if res.status_code == 200:
                raw_resp = res.json().get("response", "")
                data = json.loads(raw_resp)
                if isinstance(data, dict):
                    summary = data.get("summary") or summary
                    mood = str(data.get("mood", mood)).upper()
                    insights = data.get("key_insights") or []

                    # Parse events
                    valid_cats = {"DAILY_EVENT", "MEETING", "TRAVEL", "HEALTH", "MILESTONE", "REMINDER", "DINING"}
                    for item in (data.get("events") or []):
                        if not isinstance(item, dict) or not item.get("title"):
                            continue
                        try:
                            ev_date = date.fromisoformat(str(item.get("event_date") or today_str))
                        except Exception:
                            ev_date = journal_dt

                        cat = str(item.get("category", "DAILY_EVENT")).upper()
                        if cat not in valid_cats:
                            cat = "DAILY_EVENT"

                        events.append(PersonalEventCreate(
                            title=str(item["title"]).strip(),
                            category=cat,
                            event_date=ev_date,
                            location=str(item["location"]) if item.get("location") and str(item.get("location")).lower() not in ("null", "none", "") else None,
                            entity_person=str(item["entity_person"]) if item.get("entity_person") and str(item.get("entity_person")).lower() not in ("null", "none", "") else None,
                            details=str(item["details"]) if item.get("details") and str(item.get("details")).lower() not in ("null", "none", "") else None,
                        ))

                    # Parse transactions (SLM-extracted, no regex needed)
                    from src.backend.models.pydantic_schemas import FinancialTransactionCreate
                    for item in (data.get("transactions") or []):
                        if not isinstance(item, dict) or not item.get("entity_person"):
                            continue
                        try:
                            amt = float(item.get("amount") or 0)
                        except Exception:
                            continue
                        if amt <= 0:
                            continue

                        currency_raw = str(item.get("currency") or "USD").upper()
                        if currency_raw not in {"INR", "USD", "EUR", "GBP"}:
                            currency_raw = "USD"

                        transactions.append(FinancialTransactionCreate(
                            entity_person=str(item["entity_person"]).strip().capitalize(),
                            amount=amt,
                            currency=currency_raw,
                            transaction_date=journal_dt,
                            notes=str(item.get("notes") or "From journal entry"),
                        ))

        except Exception as e:
            logger.debug(f"SLM unified journal analysis warning: {e}")

    # Offline fallback: use separate rule-based extractors when Ollama is unavailable
    if not events and not transactions:
        logger.info("SLM unavailable or returned empty — using offline rule-based extractors for journal")
        events = extract_personal_events(raw_text)
        transactions = extract_financial_transactions(raw_text)

    return {
        "raw_text": raw_text,
        "entry_date": journal_dt,
        "summary": summary,
        "mood": mood,
        "key_insights": insights,
        "extracted_events": events,
        "extracted_transactions": transactions,
    }
