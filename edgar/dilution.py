"""What bears on a filer's share supply, read from its own filings.

`extract_statements` scans the sentences of one filing document (a 6-K or
8-K, or a 424B prospectus supplement) for statements about shares
outstanding, offerings, equity lines and at-the-market programs, shelf
capacity, warrants, reverse splits and going-concern doubt.

These are text heuristics, built so that nothing is guessed:

- a statement is one sentence of the filing, returned verbatim (clipped
  around the match when very long, with `...` marking the cut);
- every figure is parsed from that sentence and carries the exact text it
  was read from (`{"type": "shares", "value": 3000000, "text": "3,000,000
  Class A ordinary shares"}`);
- the module never combines figures, converts share counts across a reverse
  split, or infers a number the filing does not state.

Pure text processing: no network. `EdgarClient.dilution` fetches documents
and assembles the sections.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from edgar.filing_text import clip_sentence

_I = re.IGNORECASE
SENTENCE_MAX_CHARS = 900

TOPICS = [
    "shares_outstanding", "offering", "equity_line_terms", "shelf_capacity",
    "warrants", "reverse_split", "going_concern",
]
# Per document and topic: prospectuses repeat themselves.
MAX_STATEMENTS_PER_TOPIC = 8
# The cover page of a prospectus supplement states the deal; later mentions
# of "offering" are boilerplate, risk factors and history.
PROSPECTUS_COVER_SENTENCES = 60

_SHARE_NOUN = (r"(?:class [a-z] )?(?:ordinary shares|common shares|"
               r"shares of (?:our |the company's |its )?(?:class [a-z] )?common stock|"
               r"american depositary shares|adss?)")
_COUNT = r"\d{1,3}(?:,\d{3})+|\d{4,}"

_MONEY = r"(\d(?:[\d,]*\d)?(?:\.\d+)?)"   # digits with inner commas, optional decimals
_FIGURE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("exercise_price", re.compile(
        rf"exercise price (?:of|is|equal to|per share of)[^.$]{{0,60}}?(?:US)?\$\s?{_MONEY}", _I)),
    ("price_per_share", re.compile(
        rf"(?:US)?\$\s?{_MONEY} per (?:class [a-z] )?(?:ordinary |common )?"
        r"(?:share|unit|ads|pre-funded warrant|warrant)", _I)),
    ("dollars", re.compile(
        rf"(?:US|SGD |S)?\$\s?{_MONEY}(?:\s?(million|billion))?(?! per\b)(?!\d)", _I)),
    ("warrants", re.compile(
        rf"({_COUNT}) (?:pre-funded |prefunded |common |ordinary )?warrants\b|"
        rf"warrants\"?\)? to purchase (?:up to )?(?:an aggregate of )?({_COUNT})", _I)),
    ("shares", re.compile(rf"({_COUNT}) {_SHARE_NOUN}", _I)),
    ("percent", re.compile(r"(\d+(?:\.\d+)?)\s?%")),
]
_SCALE = {"million": 1_000_000, "billion": 1_000_000_000}


def _number(text: str) -> float | int:
    value = float(text.replace(",", ""))
    return int(value) if value.is_integer() else value


def parse_figures(sentence: str) -> list[dict]:
    """Every figure stated in a sentence, each with the text it was read from."""
    figures: list[dict] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in _FIGURE_PATTERNS:
        for match in pattern.finditer(sentence):
            span = match.span()
            money = kind in ("exercise_price", "price_per_share", "dollars")
            # "exercise price of $1.30 per share" is one figure, not three.
            if money and any(lo < span[1] and span[0] < hi for lo, hi in taken):
                continue
            # A par value is not a price.
            if money and _PAR_VALUE_RE.search(sentence[max(0, span[0] - 40):span[0]]):
                continue
            raw = next(group for group in match.groups() if group and group[0].isdigit())
            value = _number(raw)
            if kind == "dollars" and match.group(2):
                value = _number(str(float(raw.replace(",", "")) * _SCALE[match.group(2).lower()]))
            figure = {"type": kind, "value": value, "text": match.group(0).strip()}
            if kind == "shares":
                share_class = re.search(r"class ([a-z])\b", match.group(0), _I)
                if share_class:
                    figure["share_class"] = share_class.group(1).upper()
            figures.append(figure)
            if money:
                taken.append(span)
    figures.sort(key=lambda f: sentence.find(f["text"]))
    return figures


_PAR_VALUE_RE = re.compile(r"(?:par|nominal)(?: or par)? value(?: of| per share of)?[^$]{0,12}$", _I)
_AS_OF_RE = re.compile(r"\b(?i:as of) ([A-Z][a-z]+ \d{1,2}, \d{4})")


def _iso_date(text: str) -> str:
    try:
        return datetime.strptime(text, "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


# --- topic rules -----------------------------------------------------------

_OUTSTANDING_RE = re.compile(
    rf"(?:{_COUNT}) {_SHARE_NOUN}[^.]{{0,80}}?(?:issued and )?outstanding|"
    rf"outstanding (?:before|prior to|after|immediately after)[^.]{{0,40}}?(?:{_COUNT})|"
    rf"(?:there (?:were|are)|we had|had|has) (?:{_COUNT}) [^.]{{0,80}}?(?:issued and )?outstanding|"
    rf"(?:consisting of|comprised of|comprising) (?:{_COUNT}) {_SHARE_NOUN}", _I)
_OUTSTANDING_EXCLUDE_RE = re.compile(
    r"\bauthori[sz]ed\b|beneficial ownership limitation|weighted average|"
    r"net tangible book value|would have been", _I)

_KIND_RULES = [
    ("equity_line", re.compile(
        r"standby equity purchase agreement|equity line|committed equity|"
        r"\bequity purchase agreement|purchase agreement[^.]{0,200}from time to time", _I)),
    ("atm", re.compile(
        r"at[- ]the[- ]market|\bATM\b|equity distribution agreement|open market sale agreement|"
        r"(?<!purchase )\bsales agreement", _I)),
    ("registered_direct", re.compile(r"registered direct", _I)),
    ("private_placement", re.compile(r"private placement|\bPIPE\b", _I)),
    ("convertible", re.compile(r"convertible (?:promissory |senior )?(?:notes?|debentures?|preferred)", _I)),
    ("public_offering", re.compile(r"underwritten|underwriting agreement|public offering|"
                                   r"follow-on offering|best[- ]efforts", _I)),
]
_OFFERING_ACTION_RE = re.compile(
    r"entered into|enters into|we are offering|is offering|relates to an offering|"
    r"closed|closing of|consummated|priced|pricing of|agreed to (?:sell|issue|purchase)|"
    r"offer and s(?:ale|ell)|gross proceeds", _I)

_PROCEEDS_RE = re.compile(r"(?:gross|net|aggregate) proceeds (?:from|of|to)", _I)

_EQUITY_LINE_TERMS_RE = re.compile(
    r"lower of|% of the (?:lowest|average|volume|closing|vwap)|floor price|"
    r"commitment (?:shares|fee|amount)|pre-?paid|advance notice|"
    r"direct the investor to purchase|right, but not the obligation, to sell", _I)

_SHELF_CAPACITY_RE = re.compile(
    r"general instruction i\.b\.5|baby shelf|shelf registration[^.]{0,200}up to (?:US)?\$|"
    r"offer and sell up to (?:US)?\$|public float|held by non-affiliates", _I)

_WARRANT_RE = re.compile(r"\bwarrants?\b", _I)
_WARRANT_SUBSTANCE_RE = re.compile(
    rf"(?:{_COUNT}) (?:pre-funded |prefunded |common |ordinary )?warrants|"
    rf"warrants\"?\)? to purchase (?:up to )?(?:an aggregate of )?(?:{_COUNT})|"
    r"exercise price|exercisable|exchange (?:all|any)|cashless|expire", _I)
_WARRANT_EXCLUDE_RE = re.compile(r"we may offer|may be offered|description of warrants|"
                                 r"debt securities, warrants", _I)
_EXCHANGE_MULTIPLE_RE = re.compile(
    r"exchange[^.]{0,400}?product of[^.]{0,30}?\((\d+(?:\.\d+)?)\)|"
    r"exchange(?:d|able)? (?:for|into) (\d+(?:\.\d+)?) (?:class [a-z] )?(?:ordinary|common) shares", _I)
_EXERCISE_FLOOR_RE = re.compile(
    r"in no event[^.]{0,80}exercise price[^.]{0,40}(?:lower|less) than (?:US)?\$\s?(\d[\d,]*(?:\.\d+)?)", _I)
_OWNERSHIP_CAP_RE = re.compile(r"(\d+(?:\.\d+)?)% beneficial ownership limitation|"
                               r"in excess of (\d+(?:\.\d+)?)% of the number of", _I)

_SPLIT_RE = re.compile(
    r"reverse (?:stock|share) split|share consolidation\b|"
    r"consolidation of (?:the |its |all (?:of )?(?:the )?)?[^.]{0,120}?(?:shares|share capital)", _I)
_SPLIT_ACTION_RE = re.compile(
    r"approv\w+|effect\w+|implement\w+|announc\w+|resolved|consolidated into|"
    r"be and is hereby|began trading|begin trading|commence\w* trading|completed", _I)
_SPLIT_EXCLUDE_RE = re.compile(
    r"if necessary|including by effecting|subject to adjustment|adjust(?:ed|ment)s? "
    r"(?:for|upon|in the event)|such as (?:share|stock)|may (?:need|be required|elect) to|"
    r"we may (?:effect|implement)|could (?:effect|implement)|if approved|"
    # Ancillary resolutions about a consolidation are not the consolidation.
    r"\bno fractional|authorized for and on behalf", _I)
_RATIO_RES = [
    (re.compile(r"\b(\d{1,5})[- ]for[- ](?:1|one)\b", _I), "{0}-for-1"),
    (re.compile(r"\b(?:1|one)[- ]for[- ](\d{1,5})\b", _I), "1-for-{0}"),
    (re.compile(r"\((\d[\d,]*):1\)"), "{0}:1"),
]
_EFFECTIVE_RE = re.compile(
    r"(?:became|become|becomes|was|is|be) effective (?:on|as of|from) ([A-Z][a-z]+ \d{1,2}, \d{4})|"
    r"effected (?:on|as of) ([A-Z][a-z]+ \d{1,2}, \d{4})|"
    r"[Oo]n ([A-Z][a-z]+ \d{1,2}, \d{4}),? (?:the Company|the Registrant|we)[^.]{0,40}?effected")
_SPLIT_DONE_RE = re.compile(
    r"became effective|(?:was|were|has|have|had|company|we) effected|began trading|"
    r"completed (?:the|a|its)", _I)
# In a 424B document the accompanying base prospectus follows the supplement
# under a bare "PROSPECTUS" heading. It is older text: only its statement of
# what the shelf registers is read.
_BASE_PROSPECTUS_HEADING = "PROSPECTUS"
_PROSPECTUS_OFFERING_RE = re.compile(
    r"we are (?:also )?offering|relates to an offering|offering price (?:for|of)|"
    r"we are (?:also )?registering", _I)

_GOING_CONCERN_RE = re.compile(
    r"substantial doubt[^.]{0,160}going concern|going concern[^.]{0,160}substantial doubt", _I)


def offering_kind(sentence: str) -> str:
    for kind, pattern in _KIND_RULES:
        if pattern.search(sentence):
            return kind
    return ""


def _statement(topic: str, sentence: str, focus: Optional[tuple[int, int]] = None, **extra) -> dict:
    row = {
        "topic": topic,
        "sentence": clip_sentence(sentence, SENTENCE_MAX_CHARS, focus),
        "figures": parse_figures(sentence),
    }
    row.update({key: value for key, value in extra.items() if value not in ("", None, [])})
    return row


def _shares_outstanding(sentence: str) -> Optional[dict]:
    match = _OUTSTANDING_RE.search(sentence)
    if not match or _OUTSTANDING_EXCLUDE_RE.search(sentence):
        return None
    lowered = sentence.lower()
    if re.search(r"outstanding (?:immediately )?after|after giving effect|assuming", lowered):
        basis = "after_offering"
    elif re.search(r"outstanding (?:before|prior to)", lowered):
        basis = "before_offering"
    else:
        basis = "stated"
    as_of = _AS_OF_RE.search(sentence)
    row = _statement("shares_outstanding", sentence, match.span(), basis=basis,
                     as_of=_iso_date(as_of.group(1)) if as_of else "")
    row["figures"] = [f for f in row["figures"] if f["type"] == "shares"]
    return row if row["figures"] else None


def _reverse_split(sentence: str) -> Optional[dict]:
    match = _SPLIT_RE.search(sentence)
    if not match or not _SPLIT_ACTION_RE.search(sentence) or _SPLIT_EXCLUDE_RE.search(sentence):
        return None
    ratios = []
    for pattern, template in _RATIO_RES:
        for found in pattern.finditer(sentence):
            ratio = template.format(found.group(1))
            if ratio not in ratios:
                ratios.append(ratio)
    effective = _EFFECTIVE_RE.search(sentence)
    effective_date = _iso_date(next(g for g in effective.groups() if g)) if effective else ""
    status = "effective" if effective_date or _SPLIT_DONE_RE.search(sentence) else "approved"
    return _statement("reverse_split", sentence, match.span(), ratios=ratios, status=status,
                      effective_date=effective_date)


def _warrants(sentence: str) -> Optional[dict]:
    match = _WARRANT_RE.search(sentence)
    if (not match or not _WARRANT_SUBSTANCE_RE.search(sentence)
            or _WARRANT_EXCLUDE_RE.search(sentence)):
        return None
    features: dict = {}
    multiple = _EXCHANGE_MULTIPLE_RE.search(sentence)
    if multiple:
        features["exchange_multiple"] = _number(next(g for g in multiple.groups() if g))
    floor = _EXERCISE_FLOOR_RE.search(sentence)
    if floor:
        features["exercise_price_floor"] = _number(floor.group(1))
    cap = _OWNERSHIP_CAP_RE.search(sentence)
    if cap:
        features["beneficial_ownership_cap_percent"] = _number(next(g for g in cap.groups() if g))
    if re.search(r"pre-?funded", sentence, _I):
        features["pre_funded"] = True
    if re.search(r"cashless", sentence, _I):
        features["cashless_exercise"] = True
    row = _statement("warrants", sentence, (multiple or match).span(), features=features)
    return row if row["figures"] or features else None


def extract_statements(sentences: list[str], prospectus: bool = False) -> list[dict]:
    """Dilution statements in one document's sentences, in document order.

    `prospectus` marks a 424B supplement: its offering statement is read from
    the cover page only (`PROSPECTUS_COVER_SENTENCES`), since the rest of a
    prospectus restates history and boilerplate.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}
    equity_line_document = any(
        offering_kind(s) in ("equity_line", "atm") for s in sentences[:PROSPECTUS_COVER_SENTENCES * 2])

    def add(row: Optional[dict]) -> None:
        if not row:
            return
        key = (row["topic"], row["sentence"])
        if key in seen or counts.get(row["topic"], 0) >= MAX_STATEMENTS_PER_TOPIC:
            return
        seen.add(key)
        counts[row["topic"]] = counts.get(row["topic"], 0) + 1
        out.append(row)

    in_base_prospectus = False
    for index, sentence in enumerate(sentences):
        if (prospectus and index > PROSPECTUS_COVER_SENTENCES
                and sentence.strip() == _BASE_PROSPECTUS_HEADING):
            in_base_prospectus = True
        if len(sentence) < 25:
            continue

        shelf = _SHELF_CAPACITY_RE.search(sentence)
        if shelf:
            row = _statement("shelf_capacity", sentence, shelf.span(),
                             part="base_prospectus" if in_base_prospectus else "")
            if any(f["type"] == "dollars" for f in row["figures"]) or "i.b.5" in sentence.lower():
                add(row)
        if in_base_prospectus:
            continue

        add(_shares_outstanding(sentence))
        add(_reverse_split(sentence))

        going = _GOING_CONCERN_RE.search(sentence)
        if going:
            add(_statement("going_concern", sentence, going.span()))

        kind = offering_kind(sentence)
        on_cover = prospectus and index < PROSPECTUS_COVER_SENTENCES
        terms = _EQUITY_LINE_TERMS_RE.search(sentence) if equity_line_document else None
        if kind and _OFFERING_ACTION_RE.search(sentence) and (on_cover or not prospectus):
            row = _statement("offering", sentence, kind=kind)
            if any(f["type"] in ("shares", "dollars", "warrants") for f in row["figures"]):
                add(row)
        elif terms:
            row = _statement("equity_line_terms", sentence, terms.span())
            if row["figures"]:
                add(row)
        elif on_cover and _PROSPECTUS_OFFERING_RE.search(sentence):
            row = _statement("offering", sentence, kind="prospectus_supplement")
            if any(f["type"] in ("shares", "dollars", "warrants") for f in row["figures"]):
                add(row)
        elif not prospectus and _PROCEEDS_RE.search(sentence):
            # "The gross proceeds from this Offering were $3.9 million." names
            # no deal type of its own: reported as a proceeds statement.
            row = _statement("offering", sentence, _PROCEEDS_RE.search(sentence).span(),
                             kind="proceeds")
            if any(f["type"] == "dollars" for f in row["figures"]):
                add(row)

        if not prospectus or index < PROSPECTUS_COVER_SENTENCES * 5:
            add(_warrants(sentence))
    return out
