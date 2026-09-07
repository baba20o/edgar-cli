"""Heuristic Item-level extraction for 10-K, 10-Q, and 8-K filings.

10-K Item structure is conventional but not standardized in HTML. Filers use
varying markup for headers — `<h1>Item 1A. Risk Factors</h1>`, `<b>ITEM 1A.</b>`,
or just inline bold spans. Preserve block boundaries with
`html_to_section_text` before extraction; flattened mirror text remains a
low-confidence fallback. Quarterly items are identified by both Part and Item.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
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
    "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

_TITLE_ALIASES = {
    TEN_K_ITEMS["15"]: ("Exhibits, Financial Statement Schedules",),
}

TEN_Q_ITEMS = {
    "1": "Financial Statements",
    "2": "Management's Discussion and Analysis",
    "3": "Quantitative and Qualitative Disclosures About Market Risk",
    "4": "Controls and Procedures",
}


TEN_Q_PART_II_ITEMS = {
    "1": "Legal Proceedings",
    "1A": "Risk Factors",
    "2": "Unregistered Sales of Equity Securities and Use of Proceeds",
    "3": "Defaults Upon Senior Securities",
    "4": "Mine Safety Disclosures",
    "5": "Other Information",
    "6": "Exhibits",
}

_ITEM_HEADER_RE = re.compile(
    r"\bitem[ \t\xa0]+(?P<num>1[0-6]|[1-9])(?P<sub>[A-C])?"
    r"(?![a-z0-9])[ \t\xa0.\-—–:]*",
    re.IGNORECASE,
)
_PART_HEADER_RE = re.compile(
    r"^[ \t]*PART[ \t]+(?P<part>II|I)\b[ \t.\-—–:]*", re.I | re.M
)


class _SectionTextParser(HTMLParser):
    """Keep block/row boundaries without splitting inline heading spans."""

    _blocks = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
               "tr", "li", "section", "article", "blockquote", "hr", "br"}
    _hidden = {"head", "script", "style", "ix:header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.fragments: list[str] = []
        self.hidden: list[str] = []
        self.linked = False
        self.rows: list[dict] = []
        self.tables: list[dict] = []
        self.anchor_start: Optional[tuple[int, bool]] = None

    def _flush(self):
        line = re.sub(r"\s+", " ", "".join(self.fragments)).strip()
        # Fragment-linked Item/Part rows are navigation, not section headings.
        navigation = self.linked and (
            _ITEM_HEADER_RE.match(line) or _PART_HEADER_RE.match(line)
            or line.lower() == "table of contents"
        )
        if line and not navigation:
            self.lines.append(line)
        elif navigation and self.tables:
            self.tables[-1]["toc"] = True
        self.fragments = []
        self.linked = False
        self.anchor_start = None

    def handle_starttag(self, tag, attrs):
        if tag in self._hidden:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag == "table":
            self._flush()
            self.tables.append({"start": len(self.lines), "toc": False})
        elif tag in self._blocks:
            self._flush()
            if tag == "tr":
                self.rows.append({"start": len(self.lines), "linked": False})
        elif tag in {"td", "th"}:
            self.fragments.append(" ")
        elif tag == "a" and (dict(attrs).get("href") or "").startswith("#"):
            self.anchor_start = (len(self.fragments), self.linked)
            self.linked = True
            for row in self.rows:
                row["linked"] = True

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == "table":
            self._flush()
            if self.tables:
                table = self.tables.pop()
                if table["toc"]:
                    start = table["start"]
                    self.lines[start:] = [line for line in self.lines[start:]
                                          if not _PART_HEADER_RE.match(line)]
        elif tag in self._blocks:
            self._flush()
            if tag == "tr" and self.rows:
                row = self.rows.pop()
                row_text = " ".join(self.lines[row["start"]:])
                if row["linked"] and (_ITEM_HEADER_RE.match(row_text) or _PART_HEADER_RE.match(row_text)):
                    del self.lines[row["start"]:]
                    if self.tables:
                        self.tables[-1]["toc"] = True
        elif tag in {"td", "th"}:
            self.fragments.append(" ")
        elif tag == "a" and self.anchor_start is not None:
            start, previous_linked = self.anchor_start
            anchor_text = re.sub(r"\s+", " ", "".join(self.fragments[start:])).strip()
            if anchor_text.lower() == "table of contents":
                del self.fragments[start:]
                self.linked = previous_linked
                self._flush()
            self.anchor_start = None

    def handle_data(self, data):
        if not self.hidden:
            self.fragments.append(data)


def html_to_section_text(html: str) -> str:
    """Convert filing HTML to text while retaining paragraph and table rows."""
    parser = _SectionTextParser()
    parser.feed(html)
    parser.close()
    parser._flush()
    return "\n\n".join(parser.lines)


def _normalize_title(text: str) -> str:
    text = text.casefold().replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("'", "").replace("&", " and ")
    return re.sub(r"[\W_]+", " ", text).strip()


def _catalog(schema: str) -> dict[tuple[Optional[str], str], str]:
    if schema.upper().startswith("10-K"):
        return {(None, key): title for key, title in TEN_K_ITEMS.items()}
    return {
        **{("I", key): title for key, title in TEN_Q_ITEMS.items()},
        **{("II", key): title for key, title in TEN_Q_PART_II_ITEMS.items()},
    }


def _title_follows(following: str, title: str) -> bool:
    normalized = _normalize_title(following)
    for variant in (title, *_TITLE_ALIASES.get(title, ())):
        needle = _normalize_title(variant)
        if normalized == needle or normalized.startswith(needle + " "):
            return True
    return False


def find_items(text: str, schema: str = "10-K") -> list[dict]:
    """Find ordered headings, rejecting inline references and title conflicts.

    Existing Item/title/start/header fields are retained. `part` disambiguates
    10-Q identities; title/structure evidence controls extraction confidence.
    """
    catalog = _catalog(schema)
    structured = "\n" in text
    parts = list(_PART_HEADER_RE.finditer(text))
    occurrences: dict[tuple[Optional[str], str], list[dict]] = {}
    for m in _ITEM_HEADER_RE.finditer(text):
        item_key = m.group("num") + (m.group("sub") or "").upper()
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[line_start if structured else max(0, m.start() - 80):m.start()].strip()
        at_start = not prefix or bool(re.fullmatch(r"PART\s+(?:II|I)[ .:—–-]*", prefix, re.I))
        if structured and not at_start:
            continue
        if not structured and re.search(r"[a-z,\"“”‘’]\s*$", prefix):
            continue
        line_end = text.find("\n", m.end())
        if line_end < 0:
            line_end = len(text)
        heading_tail = text[m.end():line_end].strip()
        # Page-number suffixes distinguish plain-text TOC rows from headings.
        if structured and len(heading_tail) < 180 and re.search(r"\s\d+\s*$", heading_tail):
            continue
        following = text[m.end():m.end() + 220].strip()
        part = None
        if not schema.upper().startswith("10-K"):
            preceding = [p for p in parts if p.start() <= m.start()]
            part = preceding[-1].group("part").upper() if preceding else None
        for identity, title in catalog.items():
            if identity[1] != item_key or (part and identity[0] != part):
                continue
            title_match = _title_follows(following, title)
            # Unfamiliar line-start captions remain weak candidates. A known
            # different title is a conflict, not a variant to relabel.
            if not title_match:
                conflicting_title = any(
                    other != identity and _title_follows(following, other_title)
                    for other, other_title in catalog.items()
                )
                if not at_start or conflicting_title:
                    continue
            if not title_match and identity[0] not in (None, part or "I"):
                continue
            occurrences.setdefault(identity, []).append({
                "item": item_key, "part": identity[0], "title": title,
                "start": m.start(), "header": text[m.start():m.start() + 80].strip(),
                "title_matched": title_match, "structured": structured and at_start,
            })

    picks: list[dict] = []
    last_offset = -1
    for identity in catalog:
        valid = [c for c in occurrences.get(identity, []) if c["start"] > last_offset]
        if not valid:
            continue
        chosen = next((c for c in valid if c["title_matched"]), valid[0])
        picks.append(chosen)
        last_offset = chosen["start"]
    return picks


def extract_section(text: str, item: str, schema: str = "10-K") -> dict:
    """Slice the body text for one Item, returning raw section text.

    Bare 10-Q codes refer to Part I; use `Part II Item 2` or a Part II title
    for other information. High confidence requires title-confirmed structural
    headings for both the target and its immediate canonical successor.
    """
    catalog = _catalog(schema)
    requested_part = re.match(r"part\s+(II|I)\b[\s,.:—–-]*(.*)", (item or "").strip(), re.I)
    needle = requested_part.group(2) if requested_part else item
    part = requested_part.group(1).upper() if requested_part else None
    identity = None
    for candidate_part in (("I", "II") if not schema.upper().startswith("10-K") else (None,)):
        if part and part != candidate_part:
            continue
        local_catalog = {key: title for (p, key), title in catalog.items() if p == candidate_part}
        key = _resolve_item_key(needle, local_catalog)
        if key:
            identity = (candidate_part, key)
            break
    if identity is None:
        return {"error": f"Unknown item '{item}' for schema {schema}",
                "available_items": [{"part": p, "item": k, "title": v}
                                    for (p, k), v in catalog.items()]}

    items = find_items(text, schema=schema)
    target_idx = next((i for i, e in enumerate(items)
                       if (e["part"], e["item"]) == identity), None)
    if target_idx is None:
        return {"error": f"Item {identity[1]} not found in document",
                "items_found": [e["item"] for e in items]}

    target = items[target_idx]
    start = target["start"]
    boundary = items[target_idx + 1] if target_idx + 1 < len(items) else None
    end = boundary["start"] if boundary else len(text)
    if identity[0] == "I":
        next_part = next((p for p in _PART_HEADER_RE.finditer(text)
                          if start < p.start() < end and p.group("part").upper() == "II"), None)
        if next_part:
            end = next_part.start()
    order = list(catalog)
    adjacent = boundary and order.index((boundary["part"], boundary["item"])) == order.index(identity) + 1
    if not target["title_matched"] or not target["structured"]:
        confidence = "low"
        reason = "Target identity or structural heading evidence is weak."
    elif adjacent and boundary["title_matched"] and boundary["structured"]:
        confidence = "high"
        reason = "Title-confirmed structural target and immediate successor."
    else:
        confidence = "medium"
        reason = "Target title confirmed; immediate successor boundary not confirmed."

    section_text = text[start:end].strip()
    return {
        "item": identity[1],
        "part": identity[0],
        "title": catalog[identity],
        "text": section_text,
        "start": start,
        "end": end,
        "length": len(section_text),
        "confidence": confidence,
        "confidence_reason": reason,
        "items_in_document": [e["item"] for e in items],
        "item_identities_in_document": [{"part": e["part"], "item": e["item"]} for e in items],
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
    needle_lower = _normalize_title(needle)
    if needle_lower in {"md and a", "mda"}:
        needle_lower = "managements discussion and analysis"
    for key, title in catalog.items():
        variants = (title, *_TITLE_ALIASES.get(title, ()))
        if needle_lower and any(needle_lower in _normalize_title(v) for v in variants):
            return key
    return None
