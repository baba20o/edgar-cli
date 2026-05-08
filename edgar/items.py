"""Heuristic Item-level extraction for 10-K, 10-Q, and 8-K filings.

10-K Item structure is conventional but not standardized in HTML. Filers use
varying markup for headers — `<h1>Item 1A. Risk Factors</h1>`, `<b>ITEM 1A.</b>`,
or just inline bold spans. This module operates on the *plain-text* body
(post HTML strip) and slices between `Item N` headers.

This is heuristic. Expect ~80% precision on common forms. We tag every
extraction with a `confidence` field so agents can detect borderline cases.
"""

from __future__ import annotations

import re
from typing import Optional


# Canonical 10-K Items (per SEC Form 10-K instructions)
TEN_K_ITEMS = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Reserved",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements With Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits, Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

TEN_Q_ITEMS = {
    "1": "Financial Statements",
    "2": "Management's Discussion and Analysis",
    "3": "Quantitative and Qualitative Disclosures About Market Risk",
    "4": "Controls and Procedures",
}


# Pattern matches: "Item 1A.", "ITEM 1A.", "Item 1A — Risk Factors", etc.
# We require `Item` followed by a number (1-16) optionally followed by a
# letter (A/B/C). Non-greedy and tolerant of whitespace and unicode dashes.
_ITEM_HEADER_RE = re.compile(
    r"\b(?:item|ITEM)[\s\xa0]+(?P<num>1[0-6]|[1-9])(?P<sub>[A-C])?[\s\.\-—–:]*",
    re.IGNORECASE,
)


def find_items(text: str, schema: str = "10-K") -> list[dict]:
    """Return a list of detected Item headers in `text` with start offsets.

    Each entry: `{item: "1A", title: "Risk Factors", start: int, header: str}`.

    Heuristic for picking the right occurrence per item code:
    - 1 match: that's it.
    - 2 matches: second is the body header (first is the TOC).
    - 3+ matches: pick the occurrence whose distance to neighbouring items
      most closely matches the canonical Item ordering. In practice, the
      second occurrence usually wins, but back-references late in a 10-K
      can produce a fourth or fifth match that we want to ignore.

    The selection then enforces canonical-order monotonicity: items must
    appear in `[1, 1A, 1B, 1C, 2, 3, ...]` order in the result. Any item
    whose pick would violate that is dropped (treated as a missed parse).
    """
    catalog = TEN_K_ITEMS if schema.upper().startswith("10-K") else TEN_Q_ITEMS
    canonical_order = list(catalog.keys())
    canonical_index = {k: i for i, k in enumerate(canonical_order)}

    matches = list(_ITEM_HEADER_RE.finditer(text))
    match_offsets = [m.start() for m in matches]
    occurrences: dict[str, list[dict]] = {}
    for i, m in enumerate(matches):
        num = m.group("num")
        sub = (m.group("sub") or "").upper()
        item_key = num + sub if sub else num
        if item_key not in catalog:
            continue
        next_match_start = match_offsets[i + 1] if i + 1 < len(match_offsets) else len(text)
        gap = next_match_start - m.start()
        # "Thin" matches sit too close to the next Item marker — typically a
        # TOC row.
        is_thin = gap < 1200
        # "Inline" matches are cross-references inside body prose like
        # "see Item 1A — Risk Factors of this Form 10-K". Real section
        # headers begin a paragraph: they're preceded by `.\s+` (sentence
        # end) or two consecutive newlines, NOT by a lower-case letter,
        # comma, opening quote, or "see"/"under"/"in" word.
        prefix = text[max(0, m.start() - 60):m.start()]
        is_inline = (
            bool(re.search(r"[a-z,\"“”‘’]\s*$", prefix))
            or bool(re.search(r"\b(see|under|in|to|of)\s+$", prefix, re.I))
        )
        # "Title-following" matches have the canonical title text in the
        # immediate vicinity — strong positive signal that this is a real
        # section header.
        title = catalog[item_key]
        following = text[m.end():m.end() + len(title) + 8]
        title_follows = title.lower() in following.lower()
        occurrences.setdefault(item_key, []).append({
            "offset": m.start(),
            "thin": is_thin,
            "inline": is_inline,
            "title_follows": title_follows,
            "gap": gap,
        })

    # Walk items in canonical order. For each, prefer a candidate that:
    # 1. Has the canonical title following it (strongest signal), AND
    # 2. Is not inline (mid-sentence cross-reference), AND
    # 3. Is not thin (close to another Item marker), AND
    # 4. Sits after the previous picked offset.
    # Fall back through weaker preferences.
    picks: list[dict] = []
    last_offset = -1
    for item_key in canonical_order:
        candidates = occurrences.get(item_key) or []
        if not candidates:
            continue
        valid = [c for c in candidates if c["offset"] > last_offset]
        if not valid:
            continue
        chosen = None
        for predicate in (
            lambda c: c["title_follows"] and not c["inline"] and not c["thin"],
            lambda c: c["title_follows"] and not c["inline"],
            lambda c: not c["inline"] and not c["thin"],
            lambda c: not c["inline"],
            lambda c: True,
        ):
            chosen = next((c["offset"] for c in valid if predicate(c)), None)
            if chosen is not None:
                break
        if chosen is None:
            continue
        picks.append({
            "item": item_key,
            "title": catalog[item_key],
            "start": chosen,
            "header": text[chosen:chosen + 32].strip(),
        })
        last_offset = chosen
    return picks


def extract_section(text: str, item: str, schema: str = "10-K") -> dict:
    """Slice the body text for one Item, returning raw section text.

    `item` accepts either the canonical code (`1A`) or the title (`Risk Factors`).
    Returns `{item, title, text, start, end, length, confidence}`. Confidence is
    `"high"` when the next item is found cleanly downstream, `"medium"` when the
    section runs to end-of-document, `"low"` when only one match was found.
    """
    catalog = TEN_K_ITEMS if schema.upper().startswith("10-K") else TEN_Q_ITEMS
    item_key = _resolve_item_key(item, catalog)
    if not item_key:
        return {"error": f"Unknown item '{item}' for schema {schema}",
                "available_items": [{"item": k, "title": v} for k, v in catalog.items()]}

    items = find_items(text, schema=schema)
    target_idx = next((i for i, e in enumerate(items) if e["item"] == item_key), None)
    if target_idx is None:
        return {"error": f"Item {item_key} not found in document",
                "items_found": [e["item"] for e in items]}

    start = items[target_idx]["start"]
    if target_idx + 1 < len(items):
        end = items[target_idx + 1]["start"]
        confidence = "high"
    else:
        end = len(text)
        confidence = "medium"
    if len(items) == 1:
        confidence = "low"

    section_text = text[start:end].strip()
    return {
        "item": item_key,
        "title": catalog[item_key],
        "text": section_text,
        "start": start,
        "end": end,
        "length": len(section_text),
        "confidence": confidence,
        "items_in_document": [e["item"] for e in items],
    }


def _resolve_item_key(item_or_title: str, catalog: dict[str, str]) -> Optional[str]:
    """Map a user-supplied item code or title to a canonical key."""
    needle = (item_or_title or "").strip()
    if not needle:
        return None
    upper = needle.upper().replace("ITEM ", "").rstrip(".").strip()
    if upper in catalog:
        return upper
    # Try title-substring match.
    needle_lower = needle.lower()
    for key, title in catalog.items():
        if needle_lower == title.lower() or needle_lower in title.lower():
            return key
    return None
