"""Heuristic DEF 14A (proxy statement) extraction.

Proxy statements are large unstructured HTML documents. Each filer uses its
own structure for executive comp tables, board composition, audit-fee
disclosures, and shareholder proposals. This module ships a small set of
pattern-based extractors that work on the *plain-text* body and surface
candidate values + the surrounding sentence so agents can verify.

Confidence is intentionally conservative: any extraction returns the matched
text alongside the value so downstream callers can audit.
"""

from __future__ import annotations

import re
from typing import Optional


# Audit fees — pattern: "audit fees" (header) / "$X,XXX,XXX" within ~1000 chars,
# or table-style "Audit Fees ... 12,345,678" rows.
_AUDIT_FEE_PATTERNS = [
    re.compile(
        r"(audit\s+fees(?:\s*\(1\))?)\s*[\.\-—–:\s]*\$?\s*([\d,]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(audit-related\s+fees)\s*[\.\-—–:\s]*\$?\s*([\d,]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(tax\s+fees)\s*[\.\-—–:\s]*\$?\s*([\d,]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(all\s+other\s+fees)\s*[\.\-—–:\s]*\$?\s*([\d,]+)",
        re.IGNORECASE,
    ),
]


# Board composition — count of directors. Pattern: "X directors" or "Board of
# Directors consists of X members".
_BOARD_SIZE_PATTERNS = [
    re.compile(
        r"\bBoard\s+of\s+Directors\s+(?:currently\s+)?consists\s+of\s+(\w+)"
        r"(?:\s+\([\d]+\))?\s+(?:directors?|members?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcurrently\s+have\s+(\w+)\s+directors\b",
        re.IGNORECASE,
    ),
]


# Shareholder proposals — count occurrences of "Proposal X" or "Item X" sections
_PROPOSAL_PATTERN = re.compile(
    r"\b(?:Proposal|Item|PROPOSAL)\s+(?:No\.?\s+)?(\d+)[\s—\-:.]*([^\n]{1,80}?)(?=[\.\?\n])",
    re.IGNORECASE,
)


# Executive comp — match common SCT (Summary Compensation Table) row labels.
# Looks for "Total Compensation" within ~5000 chars of "Summary Compensation
# Table" or "named executive officer".
_NEO_PATTERNS = [
    re.compile(
        r"\b(?:Chief\s+Executive\s+Officer|Chief\s+Financial\s+Officer|"
        r"Chief\s+Operating\s+Officer|President\s+and\s+Chief\s+Executive)",
        re.IGNORECASE,
    ),
]


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15,
}


def _maybe_int(token: str) -> Optional[int]:
    token = token.strip().lower().replace(",", "")
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def extract_audit_fees(text: str) -> list[dict]:
    """Find audit-fee disclosures with their surrounding sentence."""
    out = []
    seen_offsets: set[int] = set()
    for pattern in _AUDIT_FEE_PATTERNS:
        for m in pattern.finditer(text):
            if m.start() in seen_offsets:
                continue
            seen_offsets.add(m.start())
            label = re.sub(r"\s+", " ", m.group(1)).strip()
            value_str = m.group(2).replace(",", "")
            try:
                value = int(value_str)
            except ValueError:
                continue
            # Plausibility filter: audit fees are typically $50K–$50M.
            if value < 10_000 or value > 200_000_000:
                continue
            ctx_start = max(0, m.start() - 80)
            ctx_end = min(len(text), m.end() + 80)
            out.append({
                "label": label,
                "value_usd": value,
                "context": text[ctx_start:ctx_end].strip(),
                "offset": m.start(),
            })
    return out


def extract_board_size(text: str) -> Optional[dict]:
    """Find a stated board size (e.g. 'Board consists of nine directors')."""
    for pattern in _BOARD_SIZE_PATTERNS:
        m = pattern.search(text)
        if m:
            count = _maybe_int(m.group(1))
            if count and 3 <= count <= 30:
                ctx_start = max(0, m.start() - 60)
                ctx_end = min(len(text), m.end() + 60)
                return {
                    "count": count,
                    "context": text[ctx_start:ctx_end].strip(),
                    "offset": m.start(),
                }
    return None


def extract_proposals(text: str, max_proposals: int = 12) -> list[dict]:
    """Detect numbered proposal/agenda items in the proxy."""
    seen: dict[int, dict] = {}
    for m in _PROPOSAL_PATTERN.finditer(text):
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        if num < 1 or num > 20:
            continue
        title = m.group(2).strip().rstrip(":.,—–-")
        if not title or len(title) < 4:
            continue
        # Lowercase mid-sentence references look like noise.
        if title[0].islower() and " " in title and len(title.split()) <= 3:
            continue
        if num not in seen:
            seen[num] = {"number": num, "title": title, "offset": m.start()}
    proposals = sorted(seen.values(), key=lambda p: p["number"])
    return proposals[:max_proposals]


def extract_neo_titles(text: str) -> list[str]:
    """Surface mentioned executive officer titles for sanity-checking."""
    found = set()
    for pattern in _NEO_PATTERNS:
        for m in pattern.finditer(text):
            found.add(re.sub(r"\s+", " ", m.group(0)).strip().title())
    return sorted(found)


def summarize(text: str) -> dict:
    """One-call governance summary. Each field carries either a value or
    `None` plus the matched context so the agent can verify."""
    return {
        "audit_fees": extract_audit_fees(text),
        "board_size": extract_board_size(text),
        "proposals": extract_proposals(text),
        "neo_titles_mentioned": extract_neo_titles(text),
        "caveat": ("DEF 14A extraction is heuristic. Each field returns its "
                    "matched context so agents can verify before relying on it. "
                    "Total executive compensation tables vary too much across "
                    "filers for a robust default extractor; pull the proxy "
                    "body and run targeted regex if needed."),
    }
