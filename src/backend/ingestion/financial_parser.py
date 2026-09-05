import re
import os
import logging
from datetime import date
from typing import List, Optional
from src.backend.models.pydantic_schemas import FinancialTransactionCreate
from src.backend.llm.llm_client import get_llm_client

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
    # "spent/paid 1500 rupees/INR/$ at/for/to Tirumala/Zomato/Venu"
    r"(?:spent|paid|bought|cost|charged|sent|transferred|gave|lent|borrowed)\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(USD|EUR|INR|GBP|rupees|rs)?\s*(?:at|for|to|on|in|from)?\s*([A-Za-z]{3,})?",
    # "paid/sent $500 to Venu"
    r"(?:paid|sent|gave|transferred|lent|borrowed|spent)\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(?:USD|EUR|INR|GBP|rupees|rs)?\s+(?:to|for|at|from)\s+([A-Za-z]{3,})",
    # "paid Venu $500" or "transferred Venu $500"
    r"(?:spent|paid|sent|gave|transferred)\s+([A-Za-z]{3,})\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(USD|EUR|INR|GBP|rupees|rs)?",
    # "Venu paid $500"
    r"([A-Za-z]{3,})\s+(?:paid|sent|gave|transferred|borrowed|lent|spent)\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(USD|EUR|INR|GBP|rupees|rs)?",
    # "$500 paid to Venu"
    r"[\$€£]\s*(\d+(?:\.\d{1,2})?)\s+(?:paid to|transferred to|sent to|given to)\s+([A-Za-z]{3,})",
]


def is_transaction_verified_by_slm(match_text: str) -> bool:
    """
    Uses active LLM to perform zero-shot binary validation
    on candidate transaction statements. Returns True if verified as a real transaction, False if tax/manual section.
    """
    text_lower = match_text.lower()
    for invalid in ["section", "sec ", "tax act", "tax law", "chapter", "paragraph", "subsection"]:
        if invalid in text_lower:
            return False

    client = get_llm_client()
    if not client.is_available():
        return True  # Fallback to strict regex blacklist

    prompt = (
        f"Question: Does this statement describe a real monetary transaction or payment to/from a person or merchant?\n"
        f"Statement: \"{match_text.strip()}\"\n"
        f"Answer strictly with YES or NO:"
    )

    try:
        ans = client.generate(
            prompt=prompt,
            system_prompt="You are a financial transaction classifier. Answer strictly YES or NO.",
            temperature=0.1,
            max_tokens=10,
            timeout=3.0,
        )
        if ans:
            ans_upper = ans.strip().upper()
            if "NO" in ans_upper or "NOT" in ans_upper:
                return False
            if "YES" in ans_upper:
                return True
    except Exception as e:
        logger.debug(f"LLM transaction verification bypassed: {e}")

    return True


def extract_financial_transactions(
    text: str,
    source_document_id: Optional[int] = None,
    username: str = "default_user",
    is_secure: bool = False,
) -> List[FinancialTransactionCreate]:
    """
    Extracts explicit monetary transaction statements from text input into Pydantic models with user metadata.
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

                    # Verify snippet using LLM
                    if not is_transaction_verified_by_slm(snippet):
                        logger.info(f"🚫 LLM rejected false positive candidate: '{snippet}'")
                        continue

                    currency = "USD"
                    snippet_lower = snippet.lower()
                    if "inr" in snippet_lower or "rupees" in snippet_lower or "rs" in snippet_lower:
                        currency = "INR"
                    elif "eur" in snippet_lower or "€" in snippet:
                        currency = "EUR"
                    elif "gbp" in snippet_lower or "£" in snippet:
                        currency = "GBP"
                    elif len(groups) >= 3 and groups[2]:
                        c_str = groups[2].upper()
                        if c_str in ["INR", "USD", "EUR", "GBP"]:
                            currency = c_str
                        elif c_str in ["RUPEES", "RS"]:
                            currency = "INR"

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
                            notes=f"Extracted from statement: '{snippet}' (LLM Verified)",
                            source_document_id=source_document_id,
                            username=username,
                            is_secure=is_secure,
                        )
                        results.append(tx)
            except Exception as e:
                logger.warning(f"Error parsing transaction pattern match '{match.group(0)}': {e}")

    return results
