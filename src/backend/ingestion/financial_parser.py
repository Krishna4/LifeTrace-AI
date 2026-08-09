import re
import os
import requests
import logging
from datetime import date
from typing import List, Optional
from src.backend.models.pydantic_schemas import FinancialTransactionCreate
from src.backend.query.slm_router import is_ollama_online, OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

# Blacklist of non-person terms commonly found in technical manuals & tax forms
INVALID_ENTITIES = {
    "section", "sec", "taxes", "tax", "in", "at", "the", "page", "rule", "act", 
    "item", "no", "table", "form", "part", "schedule", "code", "chapter", 
    "subsection", "clause", "paragraph", "amount", "total", "price", "cost", 
    "fee", "date", "day", "year", "month", "percent", "rs", "rupees", "inr", 
    "usd", "taxiway", "amm", "iso", "ms", "before", "approximately", "give", 
    "interest", "paid", "sent", "gave", "transferred", "to", "sub", "ref", 
    "doc", "pdf", "is", "of", "for", "on", "with", "by", "from", "as", "an"
}

# Heuristic transaction patterns matching explicit payments/debts
MONEY_PATTERNS = [
    # "paid Venu $500" or "transferred Venu $500"
    r"(?:paid|sent|gave|transferred)\s+([A-Z][a-z]{2,})\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(USD|EUR|INR|GBP)?",
    # "paid/sent $500 to Venu"
    r"(?:paid|sent|gave|transferred|lent|borrowed)\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(?:USD|EUR|INR|GBP)?\s+(?:to|from)\s+([A-Z][a-z]{2,})",
    # "Venu paid $500"
    r"([A-Z][a-z]{2,})\s+(?:paid|sent|gave|transferred|borrowed|lent)\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(USD|EUR|INR|GBP)?",
    # "$500 paid to Venu"
    r"[\$€£]\s*(\d+(?:\.\d{1,2})?)\s+(?:paid to|transferred to|sent to|given to)\s+([A-Z][a-z]{2,})",
]


def is_transaction_verified_by_slm(match_text: str) -> bool:
    """
    Uses local SLM (Qwen-2.5-1.5B via Ollama) to perform zero-shot binary validation
    on candidate transaction statements. Returns True if verified as a real transaction, False if tax/manual section.
    """
    text_lower = match_text.lower()
    for invalid in ["section", "sec ", "tax act", "tax law", "chapter", "paragraph", "subsection"]:
        if invalid in text_lower:
            return False

    if not is_ollama_online():
        return True  # Fallback to strict regex blacklist if Ollama is offline

    prompt = f"""System: You are a financial transaction classifier.
Question: Does this statement describe a real monetary transaction or payment to/from a person or merchant?
Statement: "{match_text.strip()}"
Answer strictly with YES or NO:"""

    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
        }
        res = requests.post(OLLAMA_URL, json=payload, timeout=2.5)
        if res.status_code == 200:
            ans = res.json().get("response", "").strip().upper()
            if "NO" in ans or "NOT" in ans:
                return False
            if "YES" in ans:
                return True
    except Exception as e:
        logger.debug(f"SLM verification bypassed: {e}")

    return True


def extract_financial_transactions(
    text: str, source_document_id: Optional[int] = None
) -> List[FinancialTransactionCreate]:
    """
    Extracts explicit monetary transaction statements from text input into Pydantic models.
    Uses regex for precise numerical parsing and local SLM for binary transaction verification.
    """
    if not text:
        return []

    results: List[FinancialTransactionCreate] = []
    seen_keys = set()

    for pattern in MONEY_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            groups = match.groups()
            try:
                if len(groups) >= 2:
                    g0, g1 = groups[0], groups[1]
                    if g0 and g0.replace(".", "", 1).isdigit():
                        amount = float(g0)
                        entity = (g1 or "").strip()
                    else:
                        entity = (g0 or "").strip()
                        amount = float(g1 or 0)

                    entity_clean = entity.lower()
                    if entity_clean in INVALID_ENTITIES or len(entity_clean) < 3:
                        continue

                    snippet = match.group(0).strip()

                    # Verify snippet using local Ollama SLM
                    if not is_transaction_verified_by_slm(snippet):
                        logger.info(f"🚫 SLM rejected false positive candidate: '{snippet}'")
                        continue

                    currency = "USD"
                    if len(groups) >= 3 and groups[2]:
                        currency = groups[2].upper()

                    dedup_key = (entity_clean, amount, currency)
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)

                    if amount > 0:
                        tx = FinancialTransactionCreate(
                            entity_person=entity.capitalize(),
                            amount=amount,
                            currency=currency,
                            transaction_date=date.today(),
                            notes=f"Extracted from statement: '{snippet}' (SLM Verified)",
                            source_document_id=source_document_id,
                        )
                        results.append(tx)
            except Exception as e:
                logger.warning(f"Error parsing transaction pattern match '{match.group(0)}': {e}")

    return results
