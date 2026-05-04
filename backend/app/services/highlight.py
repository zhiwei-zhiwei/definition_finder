import re

from app.services.bm25 import tokenize

# (trigger words in the user's query, regex to bold inside snippets)
INTENT_PATTERNS: list[tuple[set[str], re.Pattern[str]]] = [
    # Dates: "January 17, 2025" / "Jan. 17 2025" / "01/17/2025" / "2025-01-17"
    (
        {"date", "when", "day", "year", "month", "deadline", "expires", "expiration", "term", "start", "end"},
        re.compile(
            r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
            r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b"
            r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
            r"|\b\d{4}-\d{2}-\d{2}\b",
            re.IGNORECASE,
        ),
    ),
    # Currency / amounts
    (
        {"amount", "cost", "price", "fee", "rent", "charge", "pay", "payment",
         "much", "dollar", "dollars", "deposit", "balance"},
        re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\b|\b\d[\d,]*\.\d{2}\s?(?:dollars?)?\b", re.IGNORECASE),
    ),
    # Addresses: street numbers + capitalized words + typical suffixes
    (
        {"address", "where", "located", "location", "place", "street", "city", "live", "premises", "apartment"},
        re.compile(
            r"\b\d{1,5}\s+(?:[A-Z][\w.]*\s+){1,5}"
            r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|"
            r"Ln|Lane|Ct|Court|Way|Pl|Place|Pkwy|Hwy|Highway|Trail|Terrace)\b\.?",
            re.IGNORECASE,
        ),
    ),
    # Phone numbers
    (
        {"phone", "telephone", "contact", "number", "call"},
        re.compile(r"\b(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.\s]\d{4}\b"),
    ),
    # Percentages / rates
    (
        {"percent", "percentage", "rate", "interest"},
        re.compile(r"\b\d+(?:\.\d+)?\s?%"),
    ),
]


def compute_spans(text: str, query: str) -> list[list[int]]:
    """Return non-overlapping [start, end] spans inside `text` to bold,
    based on a token match against the query plus intent-driven regexes.
    """
    spans: list[tuple[int, int]] = []

    # Token pass: case-insensitive whole-word matches of every non-stop query token.
    qtokens = set(tokenize(query))
    for tok in qtokens:
        for m in re.finditer(rf"\b{re.escape(tok)}\b", text, re.IGNORECASE):
            spans.append((m.start(), m.end()))

    # Intent pass: regex categories whose triggers appear in the query.
    for triggers, pat in INTENT_PATTERNS:
        if qtokens & triggers:
            for m in pat.finditer(text):
                spans.append((m.start(), m.end()))

    if not spans:
        return []
    spans.sort()
    merged: list[list[int]] = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged
