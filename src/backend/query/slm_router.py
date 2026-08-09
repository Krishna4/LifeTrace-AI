import json
import logging
import os
import re
import requests
from typing import Literal
from src.backend.models.pydantic_schemas import QueryRouteResponse

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")

SYSTEM_ROUTER_PROMPT = """You are an expert Query Router for a Personal RAG system.
Given a user query, classify the query into exactly one of three target execution engines:
1. "SQL": If the query asks about financial payments, expenses, numerical totals, or transactions (e.g. "How much did I pay Alex?").
2. "VECTOR": If the query asks for conceptual document summaries, meeting notes, or general information (e.g. "What was discussed in the project meeting?").
3. "HYBRID": If the query requires both financial totals AND context details (e.g. "Find receipt for Alex and summarize invoice details").

You MUST respond strictly with a valid JSON object matching this schema:
{
  "target_engine": "SQL" | "VECTOR" | "HYBRID",
  "sql_query": "optional string filter or SQL query if engine is SQL or HYBRID",
  "vector_terms": "optional search string if engine is VECTOR or HYBRID",
  "rationale": "1 sentence explanation"
}
"""


def is_ollama_online(host: str = "127.0.0.1", port: int = 11434) -> bool:
    """Fast < 1ms socket check to determine if local Ollama service is listening."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.05)
        res = sock.connect_ex((host, port))
        sock.close()
        return res == 0
    except Exception:
        return False


def route_query_slm(query: str) -> QueryRouteResponse:
    """
    Uses local Qwen-2.5-1.5B via Ollama API to classify query if Ollama is online.
    Instantly uses zero-shot semantic embedding classification (< 2ms) if Ollama is offline.
    """
    if is_ollama_online():
        try:
            payload = {
                "model": OLLAMA_MODEL,
                "prompt": f"{SYSTEM_ROUTER_PROMPT}\nUser Query: \"{query}\"\nJSON Output:",
                "stream": False,
                "format": "json",
            }
            res = requests.post(OLLAMA_URL, json=payload, timeout=2.0)
            if res.status_code == 200:
                raw = res.json().get("response", "")
                data = json.loads(raw)
                target = data.get("target_engine", "HYBRID").upper()
                if target not in ["SQL", "VECTOR", "HYBRID"]:
                    target = "HYBRID"

                return QueryRouteResponse(
                    query=query,
                    target_engine=target,  # type: ignore
                    sql_query=data.get("sql_query"),
                    vector_terms=data.get("vector_terms", query),
                    rationale=data.get("rationale", "Classified by local Qwen SLM router"),
                )
        except Exception as e:
            logger.debug(f"Ollama SLM router request error ({e}). Using semantic embedding router.")

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
            "financial payment expenses spending money transactions totals paid amount cost price salary balance",
            "document details topic meeting notes article summary text info audio transcript",
            "invoice receipt breakdown paid total and document context summary"
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
                rationale=f"Semantic hybrid similarity (SQL: {sql_sim:.2f}, Vector: {vec_sim:.2f})",
            )
        elif sql_sim > vec_sim and sql_sim > 0.35:
            return QueryRouteResponse(
                query=query,
                target_engine="SQL",
                vector_terms=query,
                rationale=f"Semantic financial intent (Similarity: {sql_sim:.2f})",
            )
        else:
            return QueryRouteResponse(
                query=query,
                target_engine="VECTOR",
                vector_terms=query,
                rationale=f"Semantic document/context intent (Similarity: {vec_sim:.2f})",
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
    semantic_keywords = [
        "summarize", "summary", "topic", "meeting", "note", "explain", "what is", 
        "about", "abut", "details", "detail", "info", "information", "tell me", 
        "project", "audio", "video", "document"
    ]

    has_financial = any(k in q_lower for k in financial_keywords)
    has_semantic = any(k in q_lower for k in semantic_keywords)

    if has_financial and has_semantic:
        target: Literal["SQL", "VECTOR", "HYBRID"] = "HYBRID"
        rationale = "Query requests both financial amounts and document context."
    elif has_financial:
        target = "SQL"
        rationale = "Query requests financial spending or transaction numbers."
    else:
        target = "VECTOR"
        rationale = "Query requests conceptual document or semantic transcript search."

    return QueryRouteResponse(
        query=query,
        target_engine=target,
        vector_terms=query,
        rationale=rationale,
    )
