import json
import logging
import os
import re
from typing import Literal, Optional
from src.backend.models.pydantic_schemas import QueryRouteResponse
from src.backend.llm.llm_client import get_llm_client, is_ollama_online

logger = logging.getLogger(__name__)

SYSTEM_ROUTER_PROMPT = """You are an expert Query Router for a Personal RAG system.
Given a user query, classify the query into exactly one of three target execution engines:
1. "SQL": If the query asks about structured records, financial payments, expenses, totals, personal life events, daily logs, meetings, travel, health checkups, or schedules (e.g. "How much did I pay Alex?", "What events do I have today?", "Did I visit the dentist?").
2. "VECTOR": If the query asks for conceptual document contents, uploaded articles, manuals, policies, or general textual information (e.g. "What does the employee handbook say about leave?").
3. "HYBRID": If the query requires both structured data (financials/events) AND unstructured document context (e.g. "Find receipt for Alex and summarize invoice details", "List my meetings and summarize the project design document").

You MUST respond strictly with a valid JSON object matching this schema:
{
  "target_engine": "SQL" | "VECTOR" | "HYBRID",
  "sql_query": "optional string filter or SQL query if engine is SQL or HYBRID",
  "vector_terms": "optional search string if engine is VECTOR or HYBRID",
  "rationale": "1 sentence explanation"
}
"""


def route_query_slm(query: str, provider_override: Optional[str] = None) -> QueryRouteResponse:
    """
    Uses active LLM (OpenRouter free model / local Ollama) to classify query.
    Instantly uses zero-shot semantic embedding classification (< 2ms) if LLMs are unavailable.
    """
    client = get_llm_client()
    if client.is_available():
        try:
            prompt = f"User Query: \"{query}\"\nJSON Output:"
            raw = client.generate(
                prompt=prompt,
                system_prompt=SYSTEM_ROUTER_PROMPT,
                json_mode=True,
                temperature=0.1,
                timeout=4.0,
            )
            if raw:
                # Clean code fences if present
                clean_json = raw.strip()
                if clean_json.startswith("```"):
                    clean_json = re.sub(r"^```(?:json)?", "", clean_json)
                    clean_json = re.sub(r"```$", "", clean_json).strip()

                data = json.loads(clean_json)
                target = data.get("target_engine", "HYBRID").upper()
                if target not in ["SQL", "VECTOR", "HYBRID"]:
                    target = "HYBRID"

                active_provider = client.get_effective_provider()
                return QueryRouteResponse(
                    query=query,
                    target_engine=target,  # type: ignore
                    sql_query=data.get("sql_query"),
                    vector_terms=data.get("vector_terms", query),
                    rationale=data.get("rationale", f"Classified by {active_provider} LLM router"),
                    active_provider=active_provider,
                )
        except Exception as e:
            logger.debug(f"LLM router request error ({e}). Using semantic embedding router.")

    return classify_query_semantic(query)


def classify_query_semantic(query: str) -> QueryRouteResponse:
    """
    Zero-shot semantic embedding classifier using in-memory bge-small vectors.
    Executes in < 2ms on CPU/MPS, handling typos, synonyms, and natural phrasing automatically.
    """
    try:
        from src.backend.database.lancedb_store import LanceDBStore
        store = LanceDBStore()

        prototypes = [
            "financial payment expenses spending money transactions totals paid amount cost price salary balance personal life events meetings schedule daily log travel appointment doctor dentist activities",
            "document details topic manual guide article handbook policy specification text info audio transcript",
            "invoice receipt breakdown paid total and document context summary project plan and meetings"
        ]

        vecs = store.generate_embeddings([query] + prototypes)
        q_vec = vecs[0]
        sql_vec, vec_vec, hyb_vec = vecs[1], vecs[2], vecs[3]

        def cosine_sim(v1, v2):
            dot = sum(a * b for a, b in zip(v1, v2))
            norm1 = (sum(a * a for a in v1) ** 0.5) or 1.0
            norm2 = (sum(b * b for b in v2) ** 0.5) or 1.0
            return dot / (norm1 * norm2)

        sql_sim = cosine_sim(q_vec, sql_vec)
        vec_sim = cosine_sim(q_vec, vec_vec)

        if sql_sim > 0.45 and vec_sim > 0.45:
            return QueryRouteResponse(
                query=query,
                target_engine="HYBRID",
                vector_terms=query,
                rationale=f"Semantic hybrid similarity (SQL/Events: {sql_sim:.2f}, Vector: {vec_sim:.2f})",
                active_provider="SEMANTIC_EMBEDDING",
            )
        elif sql_sim > vec_sim and sql_sim > 0.35:
            return QueryRouteResponse(
                query=query,
                target_engine="SQL",
                vector_terms=query,
                rationale=f"Semantic financial/event intent (Similarity: {sql_sim:.2f})",
                active_provider="SEMANTIC_EMBEDDING",
            )
        else:
            return QueryRouteResponse(
                query=query,
                target_engine="VECTOR",
                vector_terms=query,
                rationale=f"Semantic document/context intent (Similarity: {vec_sim:.2f})",
                active_provider="SEMANTIC_EMBEDDING",
            )
    except Exception as e:
        logger.warning(f"Semantic classifier fallback to rule matcher: {e}")
        return classify_query_rule_based(query)


def classify_query_rule_based(query: str) -> QueryRouteResponse:
    """Rule-based keyword query classifier fallback."""
    q_lower = query.lower()
    financial_keywords = [
        "pay", "paid", "amount", "cost", "spend", "spent", "money", "$", "dollar", 
        "total", "invoice", "receipt", "expense", "how much", "salary", "owed", 
        "balance", "income", "price", "transfer", "transferred", "payment", "transaction"
    ]
    event_keywords = [
        "event", "events", "meeting", "meetings", "appointment", "schedule", "scheduled", 
        "travel", "trip", "flight", "doctor", "dentist", "hospital", "clinic", "gym", 
        "workout", "dining", "dinner", "lunch", "breakfast", "daily log", "today", 
        "yesterday", "tomorrow", "calendar", "milestone"
    ]
    semantic_keywords = [
        "summarize", "summary", "topic", "note", "notes", "explain", "what is", 
        "about", "abut", "details", "detail", "info", "information", "tell me", 
        "manual", "handbook", "policy", "audio", "video", "document"
    ]

    has_financial = any(k in q_lower for k in financial_keywords)
    has_event = any(k in q_lower for k in event_keywords)
    has_semantic = any(k in q_lower for k in semantic_keywords)

    if (has_financial or has_event) and has_semantic:
        target: Literal["SQL", "VECTOR", "HYBRID"] = "HYBRID"
        rationale = "Contains both structured (financial/event) and unstructured semantic keywords"
    elif has_financial or has_event:
        target = "SQL"
        rationale = "Contains structured financial or life event keywords"
    else:
        target = "VECTOR"
        rationale = "Defaulting to unstructured document search"

    return QueryRouteResponse(
        query=query,
        target_engine=target,
        vector_terms=query,
        rationale=rationale,
        active_provider="RULE_BASED",
    )
