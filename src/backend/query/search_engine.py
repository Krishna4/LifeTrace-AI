import re
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from src.backend.models.pydantic_schemas import QueryRouteResponse
from src.backend.database.sqlite import (
    get_transactions, 
    get_total_amount_by_entity, 
    get_personal_events,
)
from src.backend.database.lancedb_store import LanceDBStore
from src.backend.llm.llm_client import get_llm_client

logger = logging.getLogger(__name__)

NON_ENTITY_WORDS = {
    "how", "what", "when", "where", "who", "why", "did", "can", "is", "are", 
    "was", "were", "give", "show", "find", "tell", "summarize", "much", "many", 
    "owe", "owes", "paid", "spent", "total", "amount", "took", "take", "the", 
    "money", "person", "me", "my", "your", "his", "her", "their", "our", "all", 
    "any", "some", "details", "detail", "info", "information", "note", "notes", 
    "document", "documents", "file", "files", "roadmap", "plan", "planning", 
    "meeting", "meetings", "project", "summary", "topic", "topics", "today", 
    "yesterday", "recent", "expense", "expenses", "transaction", "transactions", 
    "balance", "cost", "price", "event", "events", "log", "logs", "entry"
}

FINANCIAL_INTENT_WORDS = {
    "pay", "paid", "amount", "cost", "spend", "spent", "money", "$", "dollar", 
    "dollars", "rupee", "rupees", "inr", "eur", "gbp", "total", "invoice", 
    "receipt", "expense", "expenses", "how much", "salary", "owed", "owe", 
    "owes", "balance", "income", "price", "transfer", "transferred", "payment", 
    "payments", "debt", "debts", "borrow", "borrowed", "lent", "lend"
}


def _sanitize_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts PyArrow/NumPy scalars, arrays, and custom LanceDB metadata types
    into standard JSON-serializable Python primitives.
    Excludes heavy binary vector arrays to minimize payload size.
    """
    clean_chunk: Dict[str, Any] = {}
    for key, val in chunk.items():
        if key in ("vector", "_rowid"):
            continue
        if val is None:
            clean_chunk[key] = None
        elif hasattr(val, "item") and callable(getattr(val, "item")):
            try:
                clean_chunk[key] = val.item()
            except Exception:
                clean_chunk[key] = str(val)
        elif hasattr(val, "tolist") and callable(getattr(val, "tolist")):
            clean_chunk[key] = val.tolist()
        elif isinstance(val, (int, float, str, bool)):
            clean_chunk[key] = val
        elif isinstance(val, list):
            clean_chunk[key] = [
                x.item() if hasattr(x, "item") and callable(getattr(x, "item")) else x
                for x in val
            ]
        else:
            clean_chunk[key] = str(val)
    return clean_chunk


def extract_entity_from_query(
    query: str,
    username: Optional[str] = None,
    include_secure: bool = True,
) -> Optional[str]:
    """
    Intelligently extracts potential person/counterparty name from query,
    prioritizing known entities stored in the user's database.
    """
    try:
        all_txs = get_transactions(username=username, include_secure=include_secure, limit=200)
        known_entities = {t.entity_person.strip() for t in all_txs if t.entity_person}
        for entity in known_entities:
            if re.search(r'\b' + re.escape(entity) + r'\b', query, re.IGNORECASE):
                return entity

        all_evs = get_personal_events(username=username, include_secure=include_secure, limit=200)
        known_ev_entities = {e.entity_person.strip() for e in all_evs if e.entity_person}
        for entity in known_ev_entities:
            if re.search(r'\b' + re.escape(entity) + r'\b', query, re.IGNORECASE):
                return entity
    except Exception:
        pass

    # Extract capitalized candidates that are not stop words
    words = re.findall(r"\b[A-Za-z]{3,}\b", query)
    candidates = [w for w in words if w.lower() not in NON_ENTITY_WORDS and w[0].isupper()]
    return candidates[0] if candidates else None


def has_financial_intent(query: str) -> bool:
    """Checks if the query specifically asks about financial spending, debts, or payments."""
    q_lower = query.lower()
    return any(k in q_lower for k in FINANCIAL_INTENT_WORDS)


EVENT_INTENT_WORDS = {
    "event", "events", "meeting", "meetings", "schedule", "scheduled", "appointment", 
    "appointments", "calendar", "travel", "trip", "trips", "flight", "flights", 
    "doctor", "dentist", "hospital", "clinic", "checkup", "gym", "workout", 
    "run", "walk", "dining", "dinner", "lunch", "breakfast", "restaurant", 
    "visit", "visited", "log", "logs", "diary", "journal", "today", "yesterday", 
    "tomorrow", "recent", "happen", "happened", "activity", "activities", "plan", "plans"
}


def has_event_intent(query: str) -> bool:
    """Checks if the query asks about life events, meetings, travel, health, or daily activities."""
    q_lower = query.lower()
    return any(k in q_lower for k in EVENT_INTENT_WORDS)


def extract_date_filter_from_query(query: str) -> Tuple[Optional[date], Optional[date], Optional[date]]:
    """
    Extracts explicit or relative dates (today, yesterday, tomorrow) for filtering SQLite records.
    Returns (exact_date, date_from, date_to).
    """
    q_lower = query.lower()
    today = date.today()

    if re.search(r"\btoday\b", q_lower):
        return today, None, None
    elif re.search(r"\byesterday\b", q_lower):
        return today - timedelta(days=1), None, None
    elif re.search(r"\btomorrow\b", q_lower):
        return today + timedelta(days=1), None, None
    elif re.search(r"\bthis week\b", q_lower):
        return None, today - timedelta(days=7), today + timedelta(days=1)
    elif re.search(r"\blast week\b", q_lower):
        return None, today - timedelta(days=14), today - timedelta(days=7)
    
    # Check for YYYY-MM-DD
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", query)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1)), None, None
        except Exception:
            pass

    return None, None, None


def extract_category_from_query(query: str) -> Optional[str]:
    """Infers event category from query keywords."""
    q_lower = query.lower()
    if re.search(r"\b(?:meeting|meetings|sync|standup|call)\b", q_lower):
        return "MEETING"
    elif re.search(r"\b(?:travel|trip|flight|hotel|airport)\b", q_lower):
        return "TRAVEL"
    elif re.search(r"\b(?:doctor|dentist|health|clinic|hospital|workout|gym|medicine|checkup)\b", q_lower):
        return "HEALTH"
    elif re.search(r"\b(?:dinner|lunch|breakfast|food|restaurant|dining|biryani|coffee)\b", q_lower):
        return "DINING"
    elif re.search(r"\b(?:birthday|anniversary|milestone|celebration|graduated)\b", q_lower):
        return "MILESTONE"
    elif re.search(r"\b(?:reminder|reminders|todo|remember)\b", q_lower):
        return "REMINDER"
    return None



def synthesize_answer_slm(
    query: str, 
    context_chunks: List[str], 
    transactions: Optional[List[Dict[str, Any]]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    target_entity: Optional[str] = None,
    provider_override: Optional[str] = None,
) -> Optional[str]:
    """
    Uses active LLM (OpenRouter free model / local Ollama) to generate a grounded, accurate answer
    using ONLY the retrieved document chunks and relevant matching records.
    """
    client = get_llm_client()
    if not client.is_available(provider_override):
        return None

    ctx_parts = []
    if transactions:
        tx_strs = [
            f"- {t.get('entity_person')}: {t.get('amount')} {t.get('currency')} on {t.get('transaction_date')} ({t.get('notes') or 'N/A'})"
            for t in transactions
        ]
        ctx_parts.append("Financial Transactions:\n" + "\n".join(tx_strs))
    if events:
        ev_strs = [
            f"- [{e.get('category')}] {e.get('title')} on {e.get('event_date')} | Person: {e.get('entity_person') or 'N/A'} | Location: {e.get('location') or 'N/A'} | {e.get('details') or ''}"
            for e in events
        ]
        ctx_parts.append("Personal Life Events:\n" + "\n".join(ev_strs))
    if context_chunks:
        ctx_parts.append("Retrieved Document Context:\n" + "\n\n---\n\n".join(context_chunks[:4]))

    if not ctx_parts:
        return None

    context_str = "\n\n".join(ctx_parts)
    system_prompt = (
        "You are an expert, highly accurate AI assistant answering questions from the user's private personal knowledge base.\n\n"
        "CORE GROUNDING & EXTRACTION RULES:\n"
        "1. FACTUAL STRICTNESS: Answer ONLY using explicit facts directly stated in the Context Information. Never invent, extrapolate, or guess.\n"
        "2. EXACT ENTITY ISOLATION: When asked about a specific person, ID, organization, or item (e.g. 'Murali Krishna Dhoopati'), locate that EXACT entry in the context. Extract ONLY the fields belonging to that specific person. Never blend or substitute details from neighboring or adjacent records.\n"
        "3. ATTRIBUTE ACCURACY: Carefully distinguish between different numerical and textual fields:\n"
        "   - Do NOT confuse serial numbers with house addresses or door numbers.\n"
        "   - Do NOT confuse father's names with husband's or guardian's names.\n"
        "   - Do NOT confuse dates with ID numbers.\n"
        "4. AGGREGATES & COUNTS: If asked to count or list items, state clearly that your answer is based on the retrieved context passages. If a formal summary table exists in the context, quote the exact summary totals.\n"
        "5. UNKNOWN / MISSING DATA: If the context does not contain the answer, reply: \"I could not find that information in your personal records.\"\n"
        "6. PRESENTATION & FORMATTING:\n"
        "   - Use clean Markdown with bold field labels (e.g., - **Name:** ..., - **Father's Name:** ..., - **House No:** ..., - **Amount:** ...).\n"
        "   - Be direct, concise, and structured. Avoid conversational filler or meta-explanations."
    )
    
    entity_hint = f"\nTARGET FOCUS: Locate and extract information specifically for '{target_entity}'.\n" if target_entity else ""
    user_prompt = f"Context Information:\n{context_str}\n{entity_hint}\nUser Question: {query}\n\nAnswer:"

    try:
        ans = client.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            provider_override=provider_override,
            temperature=0.1,
            max_tokens=600,
            timeout=25.0,
        )
        if ans:
            ans_clean = ans.strip()
            if len(ans_clean) > 0 and ans_clean.lower() != query.lower():
                if "could not find that information" in ans_clean.lower() and (transactions or events):
                    logger.info("LLM responded with 'could not find' despite matching records; falling back to structured records display.")
                    return None
                return ans_clean

    except Exception as e:
        logger.debug(f"LLM answer synthesis error: {e}")
    return None


def extract_context_answer(query: str, context_chunks: List[str]) -> Optional[str]:
    """
    Extractive QA fallback when LLMs are offline.
    Extracts relevant sentences matching query terms.
    """
    if not context_chunks:
        return None

    full_context = "\n".join(context_chunks)
    keywords = [w for w in re.findall(r'\b\w{3,}\b', query) if w.lower() not in NON_ENTITY_WORDS]
    if keywords:
        lines = full_context.splitlines()
        scored_lines = []
        for line in lines:
            l_str = line.strip()
            if len(l_str) > 15 and not l_str.startswith("["):
                match_count = sum(1 for k in keywords if k.lower() in l_str.lower())
                if match_count > 0:
                    scored_lines.append((match_count, l_str))
        if scored_lines:
            scored_lines.sort(key=lambda x: x[0], reverse=True)
            top_unique = []
            for _, s in scored_lines:
                if s not in top_unique:
                    top_unique.append(s)
            return "💡 **Extracted Relevant Passages:**\n" + "\n".join([f"- {m}" for m in top_unique[:4]])

    # If keywords didn't filter lines, return first clean informative sentences
    clean_lines = [l.strip() for l in full_context.splitlines() if len(l.strip()) > 30 and not l.strip().startswith("[") and not l.strip().startswith("|")]
    if clean_lines:
        return "💡 **Relevant Document Excerpt:**\n" + "\n".join([f"- {c}" for c in clean_lines[:3]])

    return None


def execute_unified_search(
    query: str, 
    route: QueryRouteResponse,
    username: str = "default_user",
    include_secure: bool = True,
    provider_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes search according to the route decision (SQL, VECTOR, or HYBRID)
    with strict tenant isolation, retrieving both financial records and personal life events.
    """
    transactions_found: List[Dict[str, Any]] = []
    events_found: List[Dict[str, Any]] = []
    vector_chunks_found: List[Dict[str, Any]] = []
    answer_parts: List[str] = []

    entity_name = extract_entity_from_query(query, username=username, include_secure=include_secure)
    is_financial = has_financial_intent(query) or (route.target_engine == "SQL" and not has_event_intent(query))
    is_event = has_event_intent(query) or (route.target_engine == "SQL" and not has_financial_intent(query))
    target_date, date_from, date_to = extract_date_filter_from_query(query)
    category_hint = extract_category_from_query(query)

    logger.info(
        f"🎯 [Execution Router] Query: '{query}' | User: '{username}' | Engine: {route.target_engine} | "
        f"Entity: '{entity_name}' | Financial: {is_financial} | Event: {is_event} | Date: {target_date}"
    )

    # 1. SQL Execution Path (Transactions & Personal Life Events)
    if route.target_engine in ["SQL", "HYBRID"] or entity_name or is_financial or is_event:
        # A. Financial Transactions
        if entity_name and is_financial:
            total_info = get_total_amount_by_entity(entity_name, username=username, include_secure=include_secure)
            txs = get_transactions(entity_person=entity_name, username=username, include_secure=include_secure, limit=10)
            for t in txs:
                transactions_found.append(t.model_dump())
        elif is_financial:
            txs = get_transactions(username=username, include_secure=include_secure, limit=10)
            for t in txs:
                transactions_found.append(t.model_dump())

        # B. Personal Life Events
        seen_event_ids = set()

        # 1. Match by entity person if detected
        if entity_name:
            evs_entity = get_personal_events(entity_person=entity_name, username=username, include_secure=include_secure, limit=10)
            for e in evs_entity:
                if e.id not in seen_event_ids:
                    seen_event_ids.add(e.id)
                    events_found.append(e.model_dump())

        # 2. Match by date, keywords, or category if event intent or structured route
        if is_event or route.target_engine in ["SQL", "HYBRID"]:
            # Search by date filter if requested
            if target_date or date_from or date_to:
                evs_date = get_personal_events(
                    event_date=target_date,
                    date_from=date_from,
                    date_to=date_to,
                    category=category_hint,
                    username=username,
                    include_secure=include_secure,
                    limit=10,
                )
                for e in evs_date:
                    if e.id not in seen_event_ids:
                        seen_event_ids.add(e.id)
                        events_found.append(e.model_dump())

            # Search by query keywords across title, details, location
            meaningful_words = [w for w in re.findall(r"\b[A-Za-z0-9]{3,}\b", query) if w.lower() not in NON_ENTITY_WORDS]
            for word in meaningful_words:
                evs_kw = get_personal_events(
                    query_text=word,
                    username=username,
                    include_secure=include_secure,
                    limit=10,
                )
                for e in evs_kw:
                    if e.id not in seen_event_ids:
                        seen_event_ids.add(e.id)
                        events_found.append(e.model_dump())

            # Search by inferred category if still not found
            if category_hint and not events_found:
                evs_cat = get_personal_events(
                    category=category_hint,
                    username=username,
                    include_secure=include_secure,
                    limit=10,
                )
                for e in evs_cat:
                    if e.id not in seen_event_ids:
                        seen_event_ids.add(e.id)
                        events_found.append(e.model_dump())

            # If general event question ("what events do I have?", "recent events") and nothing matched yet
            if is_event and not events_found and not meaningful_words:
                evs_recent = get_personal_events(
                    username=username,
                    include_secure=include_secure,
                    limit=10,
                )
                for e in evs_recent:
                    if e.id not in seen_event_ids:
                        seen_event_ids.add(e.id)
                        events_found.append(e.model_dump())

    # 2. Vector / LanceDB Execution Path (For document / conceptual context)
    if route.target_engine in ["VECTOR", "HYBRID"] or not (transactions_found or events_found):
        lancedb_store = LanceDBStore()
        search_terms = route.vector_terms or query
        chunks = lancedb_store.hybrid_search(
            search_terms, 
            limit=5, 
            username=username, 
            include_secure=include_secure,
        )
        vector_chunks_found = [_sanitize_chunk(c) for c in chunks]

    # 3. Answer Synthesis
    top_texts = [c["text_content"] for c in vector_chunks_found[:4]] if vector_chunks_found else []
    
    if transactions_found or events_found or top_texts:
        slm_ans = synthesize_answer_slm(
            query, 
            context_chunks=top_texts, 
            transactions=transactions_found, 
            events=events_found,
            target_entity=entity_name,
            provider_override=provider_override,
        )
        if slm_ans:
            final_answer = slm_ans
        else:
            # Fallback structured synthesis
            if transactions_found and entity_name:
                total_info = get_total_amount_by_entity(entity_name, username=username, include_secure=include_secure)
                tx_lines = [f"- **${t['amount']:.2f} {t['currency']}** on **{t['transaction_date']}** ({t.get('notes') or 'N/A'})" for t in transactions_found]
                answer_parts.append(
                    f"💰 **Financial Records for {entity_name}:**\n"
                    f"Total: **${total_info['total_amount']:.2f} {total_info['currency']}** across {total_info['tx_count']} transaction(s):\n"
                    + "\n".join(tx_lines)
                )
            elif transactions_found:
                tx_lines = [f"- **{t['entity_person']}**: ${t['amount']:.2f} {t['currency']} on {t['transaction_date']}" for t in transactions_found[:5]]
                answer_parts.append(f"💰 **Recent Transactions:**\n" + "\n".join(tx_lines))

            if events_found:
                ev_lines = []
                for e in events_found[:5]:
                    loc = f" at {e['location']}" if e.get("location") else ""
                    person = f" with {e['entity_person']}" if e.get("entity_person") else ""
                    det = f" - {e['details']}" if e.get("details") else ""
                    ev_lines.append(f"- **[{e['category']}] {e['title']}** on **{e['event_date']}**{loc}{person}{det}")
                answer_parts.append(f"📅 **Personal Life Events:**\n" + "\n".join(ev_lines))

            ext_ans = extract_context_answer(query, top_texts)
            if ext_ans:
                answer_parts.append(ext_ans)

            final_answer = "\n\n".join(answer_parts) if answer_parts else f"No direct match found for query: '{query}'."
    else:
        if is_financial:
            final_answer = f"💰 **No financial records found for '{query}' under user '{username}'.**"
        elif is_event:
            final_answer = f"📅 **No personal life events or schedules found for '{query}' under user '{username}'.**"
        else:
            final_answer = f"No documents or records found matching '{query}' in user '{username}' vault."

    return {
        "query": query,
        "username": username,
        "target_engine": route.target_engine,
        "rationale": route.rationale,
        "answer": final_answer,
        "transactions": transactions_found,
        "events": events_found,
        "retrieved_chunks": vector_chunks_found,
        "active_provider": route.active_provider or get_llm_client().get_effective_provider(),
    }
