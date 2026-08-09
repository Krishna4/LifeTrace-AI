import re
import logging
import requests
from typing import Any, Dict, List, Optional
from src.backend.models.pydantic_schemas import QueryRouteResponse
from src.backend.database.sqlite import (
    get_transactions, 
    get_total_amount_by_entity, 
    get_personal_events
)
from src.backend.database.lancedb_store import LanceDBStore
from src.backend.query.slm_router import is_ollama_online, OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)


def _sanitize_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts PyArrow/NumPy scalars, arrays, and custom LanceDB metadata types
    into standard JSON-serializable Python primitives (int, float, str, bool, list).
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


def extract_entity_from_query(query: str) -> Optional[str]:
    """
    Intelligently extracts potential person/counterparty name from query.
    1. Checks known entity_person names stored in SQLite.
    2. If no DB match, extracts titlecased words excluding question/stop words.
    """
    try:
        all_txs = get_transactions(limit=200)
        known_entities = {t.entity_person.strip() for t in all_txs if t.entity_person}
        for entity in known_entities:
            if re.search(r'\b' + re.escape(entity) + r'\b', query, re.IGNORECASE):
                return entity
    except Exception:
        pass

    stop_words = {
        "how", "what", "when", "where", "who", "why", "did", "can", "is", "are", 
        "was", "were", "give", "show", "find", "tell", "summarize", "much", "many", 
        "owe", "owes", "paid", "spent", "total", "amount", "took", "take", "the", "money", "person"
    }
    words = re.findall(r"\b[A-Za-z]{2,}\b", query)
    candidates = [w for w in words if w.lower() not in stop_words and (w[0].isupper() or len(words) <= 5)]
    return candidates[0] if candidates else None


def synthesize_answer_slm(
    query: str, 
    context_chunks: List[str], 
    transactions: Optional[List[Dict[str, Any]]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """
    Uses local Ollama SLM (Qwen-2.5-1.5B) to generate a direct, concise, natural language answer
    from retrieved document context chunks, financial transactions, and personal life event logs.
    """
    if not is_ollama_online():
        return None

    ctx_parts = []
    if transactions:
        tx_strs = [f"Person: {t.get('entity_person')}, Amount: ${t.get('amount')} {t.get('currency')}, Date: {t.get('transaction_date')}, Notes: {t.get('notes')}" for t in transactions]
        ctx_parts.append("Financial Transaction Records:\n" + "\n".join(tx_strs))
    if events:
        ev_strs = [f"Title: {e.get('title')}, Category: {e.get('category')}, Date: {e.get('event_date')}, Person: {e.get('entity_person') or 'N/A'}, Location: {e.get('location') or 'N/A'}, Details: {e.get('details') or 'N/A'}" for e in events]
        ctx_parts.append("Personal Life Event Logs:\n" + "\n".join(ev_strs))
    if context_chunks:
        ctx_parts.append("Document Context:\n" + "\n\n---\n\n".join(context_chunks[:3]))

    if not ctx_parts:
        return None

    context_str = "\n\n".join(ctx_parts)
    prompt = (
        "You are an intelligent personal assistant. Answer the user's question directly and concisely "
        "using ONLY the provided context information. Include exact amounts, dates, locations, and person names when available.\n\n"
        f"Context Information:\n{context_str}\n\n"
        f"User Question: {query}\n\n"
        "Concise Answer:"
    )

    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
        }
        res = requests.post(OLLAMA_URL, json=payload, timeout=6.0)
        if res.status_code == 200:
            ans = res.json().get("response", "").strip()
            if ans:
                return ans
    except Exception as e:
        logger.debug(f"Ollama SLM answer synthesis error: {e}")
    return None


def extract_context_answer(query: str, context_chunks: List[str]) -> Optional[str]:
    """
    Extractive QA engine: extracts targeted key-value pairs, address lines, contact details,
    or key sentence matches from document context when local Ollama SLM is offline.
    """
    if not context_chunks:
        return None

    full_context = "\n".join(context_chunks)
    q_lower = query.lower()

    # A. Address & Location extraction
    if any(k in q_lower for k in ["address", "location", "residence", "where", "living"]):
        lines = full_context.splitlines()
        addr_lines = []
        for line in lines:
            if any(k in line for k in ["Door No", "Street", "City", "Pincode", "Dammaiguda", "Gayatri", "Address", "Village", "Dist"]):
                addr_lines.append(line.strip())
        if addr_lines:
            return "📍 **Extracted Address Details:**\n" + "\n".join([f"- {l}" for l in addr_lines[:5]])

    # B. Email / Phone / Contact extraction
    if any(k in q_lower for k in ["email", "mail", "phone", "mobile", "contact", "number", "dob", "birth"]):
        lines = full_context.splitlines()
        contact_lines = []
        for line in lines:
            if any(k in line.lower() for k in ["email", "mobile", "phone", "aadhar", "dob", "birth", "pooja"]):
                contact_lines.append(line.strip())
        if contact_lines:
            return "📞 **Extracted Contact / Personal Details:**\n" + "\n".join([f"- {l}" for l in contact_lines[:5]])

    # C. Target term sentence extraction
    keywords = [w for w in re.findall(r'\b\w{3,}\b', query) if w.lower() not in ["give", "what", "where", "show", "tell", "from", "with", "this", "that"]]
    if keywords:
        lines = full_context.splitlines()
        matched_lines = []
        for line in lines:
            l_str = line.strip()
            if len(l_str) > 10 and any(k.lower() in l_str.lower() for k in keywords):
                if l_str not in matched_lines:
                    matched_lines.append(l_str)
        if matched_lines:
            return "💡 **Extracted Relevant Passages:**\n" + "\n".join([f"- {m}" for m in matched_lines[:4]])

    return None


def execute_unified_search(query: str, route: QueryRouteResponse) -> Dict[str, Any]:
    """
    Executes search according to the SLM route decision (SQL, VECTOR, or HYBRID)
    and synthesizes a natural language answer with source context.
    """
    transactions_found: List[Dict[str, Any]] = []
    events_found: List[Dict[str, Any]] = []
    vector_chunks_found: List[Dict[str, Any]] = []
    answer_parts: List[str] = []

    # 1. SQL Execution Path (Transactions & Personal Events)
    if route.target_engine in ["SQL", "HYBRID"]:
        entity_name = extract_entity_from_query(query)

        if entity_name:
            total_info = get_total_amount_by_entity(entity_name)
            txs = get_transactions(entity_person=entity_name, limit=10)
            for t in txs:
                transactions_found.append(t.model_dump())

            evs = get_personal_events(entity_person=entity_name, limit=10)
            for e in evs:
                events_found.append(e.model_dump())

            if txs:
                tx_lines = [
                    f"- **${t.amount:.2f} {t.currency}** on **{t.transaction_date}** (Notes: {t.notes or 'N/A'})"
                    for t in txs
                ]
                answer_parts.append(
                    f"💰 **Financial Records for {total_info['entity_person']}:**\n"
                    f"Total: **${total_info['total_amount']:.2f} {total_info['currency']}** across {total_info['tx_count']} transaction(s):\n"
                    + "\n".join(tx_lines)
                )
            if evs:
                ev_lines = [
                    f"- **[{e.category}] {e.title}** on **{e.event_date}** ({e.location or 'Location N/A'})"
                    for e in evs
                ]
                answer_parts.append(f"📅 **Personal Life Log Entries for {entity_name}:**\n" + "\n".join(ev_lines))
        else:
            txs = get_transactions(limit=10)
            for t in txs:
                transactions_found.append(t.model_dump())

            evs = get_personal_events(limit=10)
            for e in evs:
                events_found.append(e.model_dump())

            if txs:
                tx_lines = [
                    f"- **{t.entity_person}**: ${t.amount:.2f} {t.currency} on {t.transaction_date} ({t.notes or 'N/A'})"
                    for t in txs[:5]
                ]
                answer_parts.append(f"💰 **Recent Financial Transactions ({len(txs)} found):**\n" + "\n".join(tx_lines))
            if evs:
                ev_lines = [
                    f"- **[{e.category}] {e.title}** on {e.event_date} ({e.details or 'N/A'})"
                    for e in evs[:5]
                ]
                answer_parts.append(f"📅 **Recent Personal Life Event Logs ({len(evs)} found):**\n" + "\n".join(ev_lines))

    # 2. Vector / LanceDB Execution Path
    if route.target_engine in ["VECTOR", "HYBRID"]:
        lancedb_store = LanceDBStore()
        search_terms = route.vector_terms or query
        chunks = lancedb_store.hybrid_search(search_terms, limit=5)
        vector_chunks_found = [_sanitize_chunk(c) for c in chunks]

        if chunks:
            top_texts = [c["text_content"] for c in chunks[:3]]
            slm_ans = synthesize_answer_slm(query, top_texts, transactions=transactions_found, events=events_found)
            if slm_ans:
                answer_parts.insert(0, slm_ans)
            else:
                extracted_ans = extract_context_answer(query, top_texts)
                if extracted_ans and not answer_parts:
                    answer_parts.append(extracted_ans)

    # Fallback answer synthesis
    if not answer_parts:
        final_answer = f"No direct match found for query: '{query}'. Storage contains empty or un-indexed records."
    else:
        final_answer = "\n\n".join(answer_parts)

    return {
        "query": query,
        "target_engine": route.target_engine,
        "rationale": route.rationale,
        "answer": final_answer,
        "transactions": transactions_found,
        "events": events_found,
        "retrieved_chunks": vector_chunks_found,
    }
