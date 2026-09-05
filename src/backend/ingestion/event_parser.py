import json
import re
import os
import logging
from datetime import date
from typing import List, Optional, Dict, Any
from src.backend.models.pydantic_schemas import PersonalEventCreate
from src.backend.llm.llm_client import get_llm_client

logger = logging.getLogger(__name__)

# Known merchants/places that small LLMs may be unsure about.
KNOWN_MERCHANTS: Dict[str, tuple] = {
    "zomato":   ("DINING",     "Indian food delivery app"),
    "swiggy":   ("DINING",     "Indian food delivery app"),
    "uber eats":("DINING",     "Food delivery app"),
    "blinkit":  ("DINING",     "Grocery/quick delivery app"),
    "zepto":    ("DINING",     "Grocery/quick delivery app"),
    "tirumala": ("TRAVEL",     "Hindu pilgrimage temple in Andhra Pradesh"),
    "tirupati": ("TRAVEL",     "Hindu pilgrimage city / Tirumala temple"),
    "vaishnodevi":("TRAVEL",   "Hindu pilgrimage shrine in Jammu"),
    "uber":     ("TRAVEL",     "Ride-hailing service"),
    "ola":      ("TRAVEL",     "Indian ride-hailing service"),
    "rapido":   ("TRAVEL",     "Bike taxi service"),
    "starbucks":("DINING",     "Coffee shop chain"),
    "cafe coffee day":("DINING","Indian coffee chain"),
    "costa coffee":("DINING",  "Coffee shop chain"),
    "amazon":   ("DAILY_EVENT","Online shopping"),
    "flipkart": ("DAILY_EVENT","Indian online shopping"),
    "bigbasket": ("DAILY_EVENT","Indian online grocery store"),
    "practo":   ("HEALTH",     "Online doctor consultation platform"),
    "apollo":   ("HEALTH",     "Hospital / pharmacy chain"),
}

EVENT_KEYWORDS = [
    r"\b(?:meeting|appointment|sync|discussion|standup|call)\b",
    r"\b(?:flight|train|hotel|trip|travel|visited|stayed|arrived|departed|booked)\b",
    r"\b(?:doctor|hospital|clinic|workout|gym|run|walk|medicine|health|checkup)\b",
    r"\b(?:birthday|anniversary|joined|graduated|started|completed|passed|celebrated|won)\b",
    r"\b(?:remember|reminder|todo|note|journal|log)\b",
]


def extract_events_with_slm(
    text: str,
    source_document_id: Optional[int] = None,
    username: str = "default_user",
    is_secure: bool = False,
) -> List[PersonalEventCreate]:
    """
    Uses active LLM (OpenRouter free model / local Ollama) to extract structured daily events and milestones.
    """
    client = get_llm_client()
    if not client.is_available() or not text:
        return []

    system_prompt = (
        "You extract personal life events, meetings, travel, health logs, and milestones.\n"
        "Return a JSON array of events."
    )
    user_prompt = f"""Analyze the text snippet and extract structured events matching this schema:
[
  {{
    "title": "Short event title",
    "category": "DAILY_EVENT" | "MEETING" | "TRAVEL" | "HEALTH" | "MILESTONE" | "REMINDER" | "DINING",
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
        raw = client.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            json_mode=True,
            temperature=0.1,
            timeout=5.0,
        )
        if raw:
            clean_json = raw.strip()
            if clean_json.startswith("```"):
                clean_json = re.sub(r"^```(?:json)?", "", clean_json)
                clean_json = re.sub(r"```$", "", clean_json).strip()

            data = json.loads(clean_json)
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
                    if cat not in ["DAILY_EVENT", "MEETING", "TRAVEL", "HEALTH", "MILESTONE", "REMINDER", "DINING"]:
                        cat = "DAILY_EVENT"

                    ev = PersonalEventCreate(
                        title=str(item["title"]).strip(),
                        category=cat,
                        event_date=ev_date,
                        location=str(item.get("location")) if item.get("location") and str(item.get("location")).lower() != "null" else None,
                        entity_person=str(item.get("entity_person")) if item.get("entity_person") and str(item.get("entity_person")).lower() != "null" else None,
                        details=str(item.get("details")) if item.get("details") and str(item.get("details")).lower() != "null" else None,
                        source_document_id=source_document_id,
                        username=username,
                        is_secure=is_secure,
                    )
                    events.append(ev)
    except Exception as e:
        logger.debug(f"LLM event extraction warning: {e}")

    return events


def extract_personal_events(
    text: str,
    source_document_id: Optional[int] = None,
    username: str = "default_user",
    is_secure: bool = False,
) -> List[PersonalEventCreate]:
    """
    Extracts personal events, meetings, travel logs, and health entries from text input.
    """
    if not text:
        return []

    # 1. Try LLM extraction first
    events = extract_events_with_slm(text, source_document_id, username=username, is_secure=is_secure)
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
                    username=username,
                    is_secure=is_secure,
                )
                results.append(ev)
                break

    return results


def process_journal_entry(
    raw_text: str,
    entry_date: Optional[date] = None,
    username: str = "default_user",
    is_secure: bool = False,
) -> Dict[str, Any]:
    """
    Uses a unified LLM call (OpenRouter or Ollama) to analyze a free-text daily journal entry.
    Simultaneously extracts: summary, mood, insights, personal life events AND financial transactions.
    """
    from src.backend.ingestion.financial_parser import extract_financial_transactions
    from src.backend.models.pydantic_schemas import FinancialTransactionCreate

    journal_dt = entry_date or date.today()
    today_str = journal_dt.isoformat()

    summary = raw_text[:200].strip()
    mood = "REFLECTIVE"
    insights: List[Any] = []
    events: List[PersonalEventCreate] = []
    transactions: List[Any] = []

    client = get_llm_client()
    if client.is_available() and raw_text.strip():
        text_lower = raw_text.lower()
        merchant_hints = [
            f"- '{name}' is a {desc} (event category: {cat})"
            for name, (cat, desc) in KNOWN_MERCHANTS.items()
            if name in text_lower
        ]
        merchant_hint_str = (
            "\n\nMerchant/Place hints (use these for accurate categorisation):\n"
            + "\n".join(merchant_hints)
            if merchant_hints else ""
        )

        system_prompt = "You are a personal life and finance assistant analyzing a daily journal entry. Return clean structured JSON."
        user_prompt = f"""From the text below, extract ALL of the following simultaneously in a single JSON response:

1. A clean 2-sentence summary
2. Overall mood (POSITIVE | REFLECTIVE | TIRED | EXCITING | ANXIOUS)
3. 2 key insights or takeaways
4. All personal life events (meetings, travel, dining, health, social activities, reminders)
5. All financial transactions (money spent, paid, given, borrowed){merchant_hint_str}

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

Journal Entry: "{raw_text[:2000].strip()}"
JSON Output:"""

        try:
            raw_resp = client.generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                json_mode=True,
                temperature=0.2,
                timeout=8.0,
            )
            if raw_resp:
                clean_json = raw_resp.strip()
                if clean_json.startswith("```"):
                    clean_json = re.sub(r"^```(?:json)?", "", clean_json)
                    clean_json = re.sub(r"```$", "", clean_json).strip()

                data = json.loads(clean_json)
                if isinstance(data, dict):
                    summary = data.get("summary") or summary
                    mood = str(data.get("mood", mood)).upper()
                    insights = data.get("key_insights") or []

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
                            username=username,
                            is_secure=is_secure,
                        ))

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
                            username=username,
                            is_secure=is_secure,
                        ))

        except Exception as e:
            logger.debug(f"LLM unified journal analysis warning: {e}")

    # Offline fallback
    if not events and not transactions:
        events = extract_personal_events(raw_text, username=username, is_secure=is_secure)
        transactions = extract_financial_transactions(raw_text, username=username, is_secure=is_secure)

    return {
        "raw_text": raw_text,
        "entry_date": journal_dt,
        "summary": summary,
        "mood": mood,
        "key_insights": insights,
        "extracted_events": events,
        "extracted_transactions": transactions,
    }
