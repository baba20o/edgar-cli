"""Classify a Form 6-K from its own text.

An 8-K names its events with item numbers (2.02, 5.02, ...). A 6-K, the
current report of a foreign private issuer, has no item numbers: the only
signal is what the report says. This module reads the body of a 6-K (cover
page, exhibit index and signature block removed) and assigns event types by
matching sentences against explicit patterns. Every type is returned with the
sentence it was classified from, so nothing is asserted without its source.

Pure text processing: no network. `EdgarClient.events` fetches the documents.
"""

from __future__ import annotations

import re
from typing import Optional

from edgar.filing_text import _split_paragraph, clip_sentence, paragraphs

# Order is the order types are reported in.
EVENT_TYPES = [
    "financing",
    "material_agreement",
    "divestiture",
    "reverse_split",
    "name_change",
    "redomicile",
    "listing_deficiency",
    "listing_compliance",
    "shareholder_vote",
    "shareholder_meeting_notice",
    "authorized_capital_change",
    "charter_amendment",
    "management_change",
    "auditor_change",
    "financial_results",
]

EVENT_TYPE_DESCRIPTIONS = {
    "financing": "Equity or debt capital raise: purchase agreement, private placement, "
                 "registered direct or public offering, equity line, ATM, convertible notes",
    "material_agreement": "Entry into another agreement (waiver, purchase, licence, ...)",
    "divestiture": "Sale or disposal of a subsidiary, business or assets",
    "reverse_split": "Reverse split / share consolidation approved, announced or effected",
    "name_change": "Change of the registrant's name",
    "redomicile": "Change of jurisdiction of incorporation (continuation, redomiciliation)",
    "listing_deficiency": "Exchange notice of non-compliance with a listing rule",
    "listing_compliance": "Exchange confirmation that compliance was regained",
    "shareholder_vote": "Results of a shareholder meeting",
    "shareholder_meeting_notice": "Notice of an upcoming shareholder meeting / proxy materials",
    "authorized_capital_change": "Change in authorized share capital",
    "charter_amendment": "New or amended memorandum / articles of association",
    "management_change": "Director or officer resignation, appointment or removal",
    "auditor_change": "Change of independent registered public accounting firm",
    "financial_results": "Interim or annual financial statements or a results release",
}

_I = re.IGNORECASE

_ACTION = (r"(?:entered into|enters into|has entered|closing of|closed|consummated|"
           r"completed|priced|pricing of|announc\w+|agreed to (?:sell|issue|purchase))")
_FINANCING_NOUN = (
    r"(?:securities purchase agreement|subscription agreement|"
    r"(?:standby )?equity purchase agreement|equity line|"
    r"at[- ]the[- ]market|(?:equity distribution|sales) agreement|underwriting agreement|"
    r"placement agen(?:cy|t) agreement|registered direct offering|private placement|"
    r"(?:public|follow-on|rights) offering|convertible (?:promissory |senior )?(?:notes?|debentures?)|"
    r"pre-funded warrants?|loan agreement|credit (?:agreement|facility))"
)

# (type, must match, must not match). All patterns are case-insensitive.
_RULES: list[tuple[str, re.Pattern, Optional[re.Pattern]]] = [
    ("financing",
     re.compile(rf"{_ACTION}[^.]{{0,400}}{_FINANCING_NOUN}|{_FINANCING_NOUN}[^.]{{0,200}}{_ACTION}", _I),
     None),
    ("material_agreement",
     re.compile(r"entered into (?:a|an|the|that certain)\b[^.]{0,160}?\bagreement\b", _I),
     re.compile(_FINANCING_NOUN, _I)),
    ("divestiture",
     re.compile(
         r"(?:\bsold\b|\bsell\b|\bsale of\b|dispos(?:e|ed|al) of|divest\w*)[^.]{0,200}"
         r"(?:subsidiar\w+|all of (?:its|the) (?:shares|equity|interests?|assets)|"
         r"(?:its|the) (?:entire )?(?:equity )?(?:interests?|stake) in|business unit|"
         r"substantially all)|\(the \"(?:divestiture|disposal|disposition)\"\)", _I),
     None),
    ("reverse_split",
     re.compile(
         r"(?:reverse (?:stock|share) split|share consolidation\b|"
         r"consolidation of (?:the |its |all (?:of )?(?:the )?)?[^.]{0,120}?(?:shares|share capital)|"
         r"\b\d{1,4}[- ]for[- ](?:1|one)\b|\b(?:1|one)[- ]for[- ]\d{1,4}\b)"
         r"[^.]{0,400}?(?:approv\w+|effect\w+|implement\w+|announc\w+|resolved|consolidated into|"
         r"begin trading|commence trading|on a \d{1,4} for 1 basis)|"
         r"(?:approv\w+|effect\w+|implement\w+|announc\w+|resolved)[^.]{0,300}?"
         r"(?:reverse (?:stock|share) split|share consolidation\b)", _I),
     re.compile(r"if necessary|including by effecting|subject to adjustment|adjust(?:ed|ment)s? "
                r"(?:for|upon|in the event)|such as (?:share|stock)|may (?:need|be required|elect) to", _I)),
    ("name_change",
     re.compile(
         r"chang(?:e|ed|ing) (?:of )?(?:its|the company's|the registrant's|the corporate|the) ?"
         r"(?:corporate |legal |company )?name\b|\bname change\b|change of (?:the )?(?:company's |"
         r"registrant's |corporate )?name\b|\brenamed\b|\(now known as\b", _I),
     None),
    ("redomicile",
     re.compile(
         r"continuation (?:of the company )?(?:from|to|into|as a)\b|by way of continuation|"
         r"re-?domicil\w+|reincorporat\w+|change (?:of|in) (?:its )?(?:jurisdiction|domicile|place) of "
         r"incorporation|\bdomesticat\w+", _I),
     None),
    ("listing_deficiency",
     re.compile(
         r"(?:nasdaq|nyse|new york stock exchange|listing)[^.]{0,400}?"
         r"(?:not in compliance|no longer (?:in compliance|compl\w+)|did not meet|does not (?:meet|satisfy|"
         r"comply)|was below|deficiency|delisting determination|notice of delisting|failed to (?:meet|"
         r"satisfy|comply|maintain))|"
         r"(?:not in compliance|did not meet|deficiency (?:letter|notice|notification))[^.]{0,300}?"
         r"(?:nasdaq|nyse|listing rule|continued listing|minimum bid)", _I),
     None),
    ("listing_compliance",
     re.compile(r"(?:has|have|had) regained compliance|\bregained compliance\b|"
                r"confirm\w+ (?:that )?(?:the company|it) (?:has|is)[^.]{0,80}compliance", _I),
     None),
    ("shareholder_vote",
     re.compile(
         r"voting results|(?:items?|matters?) of business (?:was|were) acted upon|"
         r"submission of matters to a vote|shareholders (?:approved|voted|passed|adopted)\b|"
         r"(?:was|were) (?:duly )?(?:approved|passed|adopted) (?:by|at) the (?:company's )?"
         r"(?:shareholders|meeting|annual|extraordinary)|"
         r"resolutions? (?:was|were) (?:duly )?(?:passed|approved|adopted)|"
         r"results of (?:the|its) (?:annual|extraordinary|special) (?:general )?meeting", _I),
     None),
    ("shareholder_meeting_notice",
     re.compile(
         r"(?:announc\w+|will hold|to hold|will be held|notice of|convene\w*)[^.]{0,160}?"
         r"(?:general meeting|annual meeting|special meeting|meeting of (?:the )?shareholders)|"
         r"notice and proxy statement|form of proxy card", _I),
     re.compile(r"voting results|was held|\bheld the\b|were acted upon", _I)),
    ("authorized_capital_change",
     re.compile(r"authori[sz]ed share capital[^.]{0,300}?(?:increas\w+|decreas\w+|reduc\w+|"
                r"chang\w+|divided into)|(?:increas\w+|chang\w+) (?:of|in|to) (?:the |its )?"
                r"authori[sz]ed (?:share capital|shares)", _I),
     None),
    ("charter_amendment",
     re.compile(
         r"(?:\badopt(?:s|ed|ion)?\b|\bfiled\b|\bapproved\b|\breplaced\b|amendments? (?:to|of))"
         r"[^.]{0,160}?(?:memorandum and articles of association|memorandum of association|"
         r"articles of (?:association|incorporation)|\bbylaws\b|\bby-laws\b)|"
         r"(?:memorandum and articles of association|articles of (?:association|incorporation))"
         r"[^.]{0,200}?(?:be and (?:is|are) hereby|(?:was|were) (?:approved|adopted))", _I),
     None),
    ("management_change",
     re.compile(
         r"(?:resign\w+|tendered (?:his|her|their) resignation|appoint\w+|was (?:elected|named)|"
         r"step(?:ped|s)? down|will step down|removed as|retire[ds]?\b|retirement|"
         r"terminat\w+ (?:of )?(?:his|her|the)? ?employment|ceased to (?:be|serve))[^.]{0,260}?"
         r"(?:director|officer|chief \w+|chairman|chairwoman|chairperson|\bchair\b|president|"
         r"\bceo\b|\bcfo\b|\bcoo\b|\bcto\b|company secretary)|"
         r"(?:director|officer|chairman|president)[^.]{0,120}?(?:resign\w+|tendered (?:his|her) resignation|"
         r"was appointed|has been appointed|stepped down)", _I),
     re.compile(r"independent registered public accounting firm|transfer agent|placement agent|"
                r"\bauditors?\b|\bre-?appoint\w+|\bre-?elect\w+", _I)),
    ("auditor_change",
     re.compile(
         r"(?:dismiss\w+|resign\w+|engag\w+|appoint\w+|replac\w+)[^.]{0,200}?"
         r"(?:independent registered public accounting firm|independent auditors?)", _I),
     None),
    ("financial_results",
     re.compile(
         r"(?:interim|unaudited|half[- ]year(?:ly)?|first half|second half|six[- ]months?|"
         r"three[- ]months?|nine[- ]months?|quarter(?:ly)?|fiscal year|full[- ]year|annual)[^.]{0,160}?"
         r"(?:financial statements|financial results|results of operations|earnings release|"
         r"operating results)|financial results for the|"
         r"announc\w+ (?:its |the )?[^.]{0,80}?(?:financial|operating|unaudited) results", _I),
     re.compile(r"pro forma", _I)),
]

_COVER_RE = re.compile(
    r"form 20-f\b.*form 40-f|indicate by check mark|address of principal executive|"
    r"commission file number|exact name of registrant|translation of registrant|"
    r"^for the month of|report of foreign private issuer|pursuant to rule 13a-16|"
    r"securities exchange act of 1934$|if \"yes\" is marked|regulation s-t rule 101|"
    r"^yes\s*[☐☒x ]|^form 6-k$|^united states$|^securities and exchange commission$|"
    r"^washington, d\.?c\.? 20549$", _I)
_SIGNATURE_RE = re.compile(
    r"^signatures?$|pursuant to the requirements of the securities exchange act of 1934, the "
    r"registrant has duly caused", _I)
_EXHIBIT_INDEX_RE = re.compile(
    r"^(?:exhibits?(?: index)?|index (?:to|of) exhibits|exhibit(?: no\.?| number)?\s+description)$", _I)
_GENERIC_HEADING_RE = re.compile(r"^information contained in th(?:is|e) ", _I)
_BACKGROUND_RE = re.compile(r"^as (?:previously|earlier) (?:disclosed|announced|reported)", _I)
# A heading is a title, not a statement: no finite verb.
_FINITE_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|will|today|announced|announces|entered|held|"
    r"received|completed|approved|issued|furnish\w+)\b", _I)
_COVER_SCAN_PARAGRAPHS = 45
_MIN_INFORMATIVE_CHARS = 80
# Deal sentences run long and put the price last; longer ones are clipped
# around the matched phrase and end in " ...".
SNIPPET_MAX_CHARS = 900


def split_6k(text: str) -> dict:
    """Split 6-K text into `heading`, `body` paragraphs and `exhibit_index` lines.

    The cover page (up to the Form 20-F / 40-F check boxes), the exhibit index
    and the signature block are boilerplate and are not classified.
    """
    blocks = paragraphs(text)
    end = len(blocks)
    for index, block in enumerate(blocks):
        if _SIGNATURE_RE.search(block):
            end = index
            break
    start = 0
    for index, block in enumerate(blocks[:min(end, _COVER_SCAN_PARAGRAPHS)]):
        if len(block) < 320 and _COVER_RE.search(block):
            start = index + 1
    body = blocks[start:end]

    exhibit_index: list[str] = []
    for index, block in enumerate(body):
        if _EXHIBIT_INDEX_RE.match(block):
            exhibit_index = [b for b in body[index + 1:] if not _EXHIBIT_INDEX_RE.match(b)]
            body = body[:index]
            break

    heading = ""
    for block in body[:3]:
        if _GENERIC_HEADING_RE.match(block):
            continue
        if (len(block) < 160 and not block.endswith(":")
                and len(_split_paragraph(block)) == 1
                and not _FINITE_VERB_RE.search(block)):
            heading = block
        break
    return {"heading": heading, "body": body, "exhibit_index": exhibit_index}


def _classify_sentences(sentences: list[str]) -> dict[str, dict]:
    """Best evidence sentence per event type: informative before terse,
    fresh statements before "As previously disclosed" recaps."""
    best: dict[str, dict] = {}
    for sentence in sentences:
        background = bool(_BACKGROUND_RE.match(sentence))
        for event_type, pattern, exclude in _RULES:
            match = pattern.search(sentence)
            if not match or (exclude and exclude.search(sentence)):
                continue
            rank = (not background, len(sentence) >= _MIN_INFORMATIVE_CHARS)
            current = best.get(event_type)
            if current is None or rank > current["_rank"]:
                best[event_type] = {
                    "sentence": clip_sentence(sentence, SNIPPET_MAX_CHARS, match.span()),
                    "background": background,
                    "_rank": rank,
                }
    return best


def classify_6k(text: str) -> dict:
    """Classify one 6-K document's text.

    Returns `event_types` (possibly empty), `snippets` (type -> the sentence it
    was classified from), `background_types` (types supported only by an "As
    previously disclosed" recap), the report's `heading` and `lead` sentence,
    `exhibit_index` lines, and `classified_from` (`body`, `exhibit_index` or
    None). A type backed only by a recap is dropped when the report also makes
    a fresh statement of another type; when a recap is all there is, the type
    is kept and listed in `background_types`.
    """
    parts = split_6k(text)
    sentences: list[str] = []
    for block in parts["body"]:
        sentences.extend(_split_paragraph(block))

    found = _classify_sentences(sentences)
    classified_from = "body" if found else None
    if not found and parts["exhibit_index"]:
        found = _classify_sentences(parts["exhibit_index"])
        classified_from = "exhibit_index" if found else None

    fresh = {t for t, row in found.items() if not row["background"]}
    if fresh:
        found = {t: row for t, row in found.items() if t in fresh}
    # "Entered into an agreement" adds nothing when the same sentence already
    # carries a more specific type (a divestiture, say).
    generic = found.get("material_agreement")
    if generic and any(row["sentence"] == generic["sentence"]
                       for t, row in found.items() if t != "material_agreement"):
        del found["material_agreement"]
    event_types = [t for t in EVENT_TYPES if t in found]

    lead = ""
    for sentence in sentences:
        if sentence != parts["heading"] and len(sentence) >= 40 and not _GENERIC_HEADING_RE.match(sentence):
            lead = clip_sentence(sentence, 400)
            break
    return {
        "event_types": event_types,
        "snippets": {t: found[t]["sentence"] for t in event_types},
        "background_types": [t for t in event_types if found[t]["background"]],
        "heading": parts["heading"],
        "lead": lead,
        "exhibit_index": parts["exhibit_index"],
        "body_chars": sum(len(block) for block in parts["body"]),
        "classified_from": classified_from,
    }
