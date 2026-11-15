"""Sentence-backed classification of domestic Form 8-K reports.

Item numbers describe disclosure categories, not necessarily completed actions.
For example, Item 3.01 includes listing deficiencies as well as delisting notices.
Keep that evidence separate from text snippets and from any specific outcome.
This module is pure text processing; callers fetch the main report/exhibits.
"""

from __future__ import annotations

import re

from edgar import sixk
from edgar.filing_text import clip_sentence, paragraphs, split_sentences


# Preserve the public event names already returned for domestic item metadata.
ITEM_EVENTS = {
    "1.01": "material_agreement",
    "1.02": "agreement_termination",
    "1.03": "bankruptcy",
    "2.01": "merger",
    "2.02": "earnings",
    "2.03": "debt",
    "2.04": "debt",
    "3.01": "delisting",
    "3.02": "financing",
    "3.03": "capitalization",
    "4.01": "auditor_change",
    "4.02": "non_reliance_restatement",
    "5.01": "merger",
    "5.02": "leadership",
    "5.03": "capitalization",
    "5.07": "shareholder_vote",
}

_ITEM_TITLES = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement of a Registrant",
    "2.04": "Triggering Events That Accelerate or Increase a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement",
    "3.01": "Notice of Delisting or Failure to Satisfy a Continued Listing Rule or Standard; Transfer of Listing",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements or a Related Audit Report or Completed Interim Review",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers; Compensatory Arrangements of Certain Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

_I = re.IGNORECASE
_ITEM_RE = re.compile(r"\b([1-9]\.\d{2})\b")
_ITEM_HEADER_RE = re.compile(r"^item\s+([1-9]\.\d{2})\b[.\s:]*", _I)
_COVER_RE = re.compile(
    r"^form 8-k(?:/a)?$|^current report$|^united states$|"
    r"^securities and exchange commission$|^washington,? d\.?c\.? 20549$|"
    r"^pursuant to section 13 or 15\(d\)|^date of report|"
    r"^check the appropriate box|^indicate by check mark|^emerging growth company|"
    r"^securities registered pursuant to section 12\(b\)|"
    r"exact name of registrant|commission file number|address of principal executive|"
    r"^written communications pursuant to rule 425|^soliciting material pursuant to rule 14a|"
    r"^pre-commencement communications pursuant to rule", _I,
)
_SIGNATURE_RE = re.compile(r"^signatures?$|^pursuant to the requirements of the securities exchange act", _I)
_EXHIBIT_INDEX_RE = re.compile(r"^(?:exhibit(?:s| index)?|index to exhibits|exhibit (?:no\.?|number)\s+description)$", _I)
_DISCLAIMER_RE = re.compile(
    r"forward[- ]looking statements?|safe[- ]harbor|private securities litigation reform act|"
    r"risks? and uncertainties|no assurance|undertakes? no obligation|"
    r"actual results (?:may|could|will) differ|not (?:a )?guarantee(?:s)? of future|"
    r"words? such as [^.]*(?:expect|believe|anticipate|forecast)", _I,
)
_DISCLAIMER_HEADING_RE = re.compile(r"^(?:cautionary (?:note|statement) (?:regarding|concerning|about) )?(?:forward[- ]looking statements?|safe harbor)(?:\s+statements?)?[.:]?$", _I)
_REFERRAL_RE = re.compile(
    r"(?:attached|furnished|filed)[^.]{0,100}\bexhibit\s+99|"
    r"\bexhibit\s+99[^.]{0,160}(?:incorporated|attached|furnished|filed)|"
    r"incorporated (?:herein|in this report) by reference", _I,
)
_BACKGROUND_RE = re.compile(r"^as (?:previously|earlier) (?:disclosed|announced|reported)", _I)
_ACTION = r"(?:entered into|signed|announc\w+|complet\w+|consummat\w+|approv\w+|executed)"

_RULES = [
    ("financing", rf"{_ACTION}[^.]{{0,250}}warrant (?:inducement|exercise) agreement", None),
    ("merger", rf"{_ACTION}[^.]{{0,250}}(?:merger agreement|acquisition agreement|definitive agreement to acquire|business combination)|(?:merger|acquisition|business combination)[^.]{{0,100}}(?:has closed|was completed|was consummated)", r"terminat\w+|abandon\w+"),
    ("agreement_termination", r"(?:terminated|termination of)[^.]{0,100}(?:agreement|merger)|(?:agreement|merger)[^.]{0,100}(?:was terminated|has been terminated)", r"may terminate|right to terminate"),
    ("bankruptcy", r"(?:filed|commenced)[^.]{0,100}(?:chapter (?:7|11)|bankruptcy|voluntary petition)|(?:appointed|appointment of)[^.]{0,70}receiver", r"may file|risk of bankruptcy"),
    ("debt", rf"{_ACTION}[^.]{{0,180}}(?:credit (?:agreement|facility)|loan agreement|indenture|(?:senior |convertible |promissory )+notes?)|(?:issued|sold)[^.]{{0,80}}(?:senior |convertible |promissory )+notes?", None),
    ("guidance", r"(?:provides?|provided|issues?|issued|updates?|updated|raises?|raised|lowers?|lowered|reaffirms?|reaffirmed|withdraws?|withdrew|suspends?|suspended|announces?|announced|maintains?|maintained)[^.]{0,80}(?:guidance|outlook|forecast)|(?:expects?|projects?|forecasts?)[^.]{0,80}(?:revenue|net income|earnings|sales|ebitda|eps)[^.]{0,100}(?:\$|\d)", r"does not (?:provide|issue)|not providing|no (?:guidance|forecast)"),
    ("delisting", r"(?:nasdaq|nyse|exchange)[^.]{0,140}(?:has delisted|will delist|will suspend trading|suspended trading)|(?:was|has been) delisted|filed (?:a )?form 25", r"may (?:delist|suspend)|could (?:delist|suspend)|if the company"),
    ("delisting_determination", r"(?:received|issued|notified|notice of)[^.]{0,150}(?:delisting determination|determination to delist)|(?:nasdaq|nyse|exchange)[^.]{0,150}(?:determined to delist|will delist|will suspend trading)", r"may (?:delist|issue)|could (?:delist|result)"),
    ("hearing_panel_appeal", r"(?:requested|requests|filed|appealed|appeals|will request|intends to request|plans to request)[^.]{0,140}(?:hearing|appeal)[^.]{0,160}(?:nasdaq|nyse|panel|delisting)|(?:nasdaq|nyse|panel|delisting)[^.]{0,140}(?:requested a hearing|filed an appeal|appealed|intends to appeal)", r"may request|right to (?:request|appeal)"),
    ("minimum_bid_deficiency", r"(?:received|notified|notice|deficien\w+|not in compliance|failed to)[^.]{0,250}(?:minimum bid|bid price requirement)|(?:minimum bid|bid price requirement)[^.]{0,150}(?:not in compliance|failed|below|deficien\w+)", r"regained compliance"),
    ("listing_compliance", r"regained compliance|(?:nasdaq|nyse|exchange)[^.]{0,100}confirmed[^.]{0,100}(?:in compliance|compliance with)", None),
    ("non_reliance_restatement", r"(?:concluded|determined|advised)[^.]{0,180}(?:should|can|may) no longer be relied upon|(?:will|must|plans to)[^.]{0,40}restate[^.]{0,100}financial statements", None),
    ("ceo_change", r"(?:appointed|named|resigned as|will serve as|to serve as)[^.]{0,100}(?:chief executive officer|\bceo\b)|(?:chief executive officer|\bceo\b)[^.]{0,100}(?:resigned|stepped down|was appointed)", r"re-?appointed|re-?elected"),
    ("cfo_change", r"(?:appointed|named|resigned as|will serve as|to serve as)[^.]{0,100}(?:chief financial officer|\bcfo\b)|(?:chief financial officer|\bcfo\b)[^.]{0,100}(?:resigned|stepped down|was appointed)", r"re-?appointed|re-?elected"),
]
_RULES = [(kind, re.compile(pattern, _I), re.compile(exclude, _I) if exclude else None)
          for kind, pattern, exclude in _RULES]
_DOMESTIC_NAMES = {"management_change": "leadership", "financial_results": "earnings",
                   "charter_amendment": "capitalization"}


def item_numbers(items: str) -> set[str]:
    """Parse a metadata item list without treating item titles as event text."""
    return set(_ITEM_RE.findall(items or ""))


def event_types_from_items(items: str) -> set[str]:
    """Disclosure categories only; specific event outcomes require sentence evidence."""
    return {ITEM_EVENTS[item] for item in item_numbers(items) if item in ITEM_EVENTS}


def _body(text: str) -> dict:
    body, index, detected_items = [], [], set()
    in_index = False
    in_disclaimer = False
    for block in paragraphs(text):
        if _SIGNATURE_RE.search(block):
            break
        header = _ITEM_HEADER_RE.match(block)
        if header:
            in_disclaimer = False
            item = header.group(1)
            detected_items.add(item)
            block = block[header.end():].strip()
            title = _ITEM_TITLES.get(item, "")
            if title and block.lower().startswith(title.lower()):
                block = block[len(title):].lstrip(" .:;-\t")
            if item == "9.01":
                in_index = True
        if _EXHIBIT_INDEX_RE.match(block):
            in_index = True
            continue
        if in_index:
            if block:
                index.append(block)
            continue
        if not block or _COVER_RE.search(block):
            continue
        if _DISCLAIMER_HEADING_RE.match(block):
            in_disclaimer = True
            continue
        if in_disclaimer or _DISCLAIMER_RE.search(block):
            continue
        body.append(block)
    return {"body": body, "exhibit_index": index, "detected_items": detected_items}


def classify_8k(text: str, items: str = "") -> dict:
    """Classify cleaned filing text; `needs_exhibit` marks a referral-only body.

    `snippets` contains only text evidence. `item_evidence` separately lists SEC
    item numbers that support broad disclosure categories. A legacy `delisting`
    category based solely on 3.01 is not a prediction of an actual delisting.
    """
    parts = _body(text)
    sentences = split_sentences("\n\n".join(parts["body"]))
    # Incorporation sentences can repeat another item's title; they cannot
    # substantiate an event in the current filing on their own.
    substantive = [s for s in sentences if not _REFERRAL_RE.search(s)]
    found = {}
    for kind, evidence in sixk._classify_sentences(substantive).items():
        found[_DOMESTIC_NAMES.get(kind, kind)] = evidence
    for sentence in substantive:
        for kind, pattern, exclude in _RULES:
            match = pattern.search(sentence)
            if not match or (exclude and exclude.search(sentence)):
                continue
            background = bool(_BACKGROUND_RE.match(sentence))
            rank = (not background, len(sentence) >= 80)
            if kind not in found or rank > found[kind]["_rank"]:
                found[kind] = {"sentence": clip_sentence(sentence, 900, match.span()),
                               "background": background, "_rank": rank}

    fresh = {kind for kind, row in found.items() if not row["background"]}
    if fresh:
        found = {kind: row for kind, row in found.items() if kind in fresh}
    generic = found.get("material_agreement")
    if generic and any(kind in found and found[kind]["sentence"] == generic["sentence"]
                       for kind in ("financing", "merger", "divestiture")):
        del found["material_agreement"]

    numbers = item_numbers(items) | parts["detected_items"]
    item_evidence: dict[str, list[str]] = {}
    for number in sorted(numbers):
        kind = ITEM_EVENTS.get(number)
        if kind:
            item_evidence.setdefault(kind, []).append(number)
    # Item 1.01 has a broad name; preserve a distinct generic agreement only
    # when the text actually supplies one alongside a more specific deal.
    if ("material_agreement" in item_evidence and "material_agreement" not in found
            and any(kind in found for kind in ("financing", "merger", "divestiture"))):
        del item_evidence["material_agreement"]
    kinds = sorted(set(item_evidence) | set(found))
    body_evidence = bool(found)
    lead = next((clip_sentence(s, 400) for s in substantive if len(s) >= 40), "")
    return {
        "event_types": kinds,
        "snippets": {kind: found[kind]["sentence"] for kind in kinds if kind in found},
        "background_types": [kind for kind in kinds if kind in found and found[kind]["background"]],
        "item_evidence": item_evidence,
        "item_evidence_caveat": "Item numbers identify disclosure categories, not completed actions or severity.",
        "heading": "",
        "lead": lead,
        "exhibit_index": parts["exhibit_index"],
        "body_chars": sum(len(s) for s in substantive),
        "classified_from": "body" if body_evidence else ("items" if item_evidence else None),
        "needs_exhibit": not body_evidence and (not substantive or any(_REFERRAL_RE.search(s) for s in sentences)),
    }
