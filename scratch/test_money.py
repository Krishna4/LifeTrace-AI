import re

patterns = [
    # "spent/paid 1500 rupees/INR/$ at/for/to Tirumala/Zomato/Venu"
    r"(?:spent|paid|bought|cost|charged|sent|transferred|gave|lent|borrowed)\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(USD|EUR|INR|GBP|rupees|rs)?\s*(?:at|for|to|on|in|from)?\s*([A-Za-z]{3,})?",
    # "paid/sent $500 to Venu"
    r"(?:paid|sent|gave|transferred|lent|borrowed|spent)\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(?:USD|EUR|INR|GBP|rupees|rs)?\s+(?:to|for|at|from)\s+([A-Za-z]{3,})",
    # "paid Venu $500"
    r"(?:spent|paid|sent|gave|transferred)\s+([A-Za-z]{3,})\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(USD|EUR|INR|GBP|rupees|rs)?",
    # "Venu paid $500"
    r"([A-Za-z]{3,})\s+(?:paid|sent|gave|transferred|borrowed|lent|spent)\s+[\$€£]?\s*(\d+(?:\.\d{1,2})?)\s*(USD|EUR|INR|GBP|rupees|rs)?",
    # "$500 paid to Venu"
    r"[\$€£]\s*(\d+(?:\.\d{1,2})?)\s+(?:paid to|transferred to|sent to|given to)\s+([A-Za-z]{3,})",
]

texts = [
    "Spent 1500 rupees at Tirumala for Kalyanotsavam Seva with Poojitha.",
    "paid $50 to Venu for dinner.",
    "spent 200 INR for lunch on Zomato.",
    "Gave Alex 100 USD for movie tickets.",
    "Paid 1000 INR to Tirumala Devastanam",
]

for t in texts:
    found = False
    for p in patterns:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            print(f"Matched: '{t}' -> {m.groups()}")
            found = True
            break
    if not found:
        print(f"MISSED: '{t}'")
