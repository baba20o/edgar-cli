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
    "warrants", "reverse_split", "going_concern", "agent",
]
# Per document and topic: prospectuses repeat themselves.
MAX_STATEMENTS_PER_TOPIC = 8
# The cover page of a prospectus supplement states the deal; later mentions
# of "offering" are boilerplate, risk factors and history.
PROSPECTUS_COVER_SENTENCES = 60

_SHARE_NOUN = (r"(?:class [a-z] )?(?:ordinary shares|common shares|"
               r"shares of (?:our |the company's |its )?(?:outstanding )?(?:class [a-z] )?"
               r"(?:voting |non-voting )?common stock|"
               r"american depositary shares|adss?)")
_COUNT = r"\d{1,3}(?:,\d{3})+|\d{4,}"
_SHARE_FIGURE_RE = re.compile(rf"({_COUNT}) {_SHARE_NOUN}", _I)

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
    ("shares", _SHARE_FIGURE_RE),
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
_DERIVATIVE_SHARES_RE = re.compile(
    r"\b(?:issuable|underlying|reserved for|subject to|"
    r"warrants?|options?|restricted stock units?|rsus?|preferreds?)\b|"
    r"\bupon (?:the )?(?:exercise|settlement|conversion|vesting)\b", _I)
_BASED_ON_OUTSTANDING_RE = re.compile(
    rf"\bbased on (?:approximately )?(?P<shares>(?:{_COUNT}) {_SHARE_NOUN}) "
    r"(?P<qualifier>[^.;]{0,160}?)outstanding"
    r"(?: as of (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4}))?", _I)
_OUTSTANDING_TABLE_RE = re.compile(
    r"outstanding (?P<basis>before|prior to|after|immediately after)"
    r"[^.;]{0,80}?(?=\d)", _I)

_KIND_RULES = [
    ("equity_line", re.compile(
        r"standby equity purchase agreement|equity line|committed equity|"
        r"\bequity purchase agreement|purchase agreement[^.]{0,200}from time to time", _I)),
    ("atm", re.compile(
        r"at[- ]the[- ]market|\bATM\b|equity distribution agreement|open market sale agreement|"
        r"(?<!purchase )\bsales agreement", _I)),
    ("registered_direct", re.compile(r"registered direct", _I)),
    ("private_placement", re.compile(r"private placement|private investment in public equity|\bPIPE\b", _I)),
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
    r"general instruction i\s*\.\s*b\s*\.\s*[56]\b|baby shelf|"
    r"shelf registration[^.]{0,200}up to (?:US)?\$|"
    r"offer and sell up to (?:US)?\$|public float|held by non-affiliates", _I)
_BABY_SHELF_RE = re.compile(r"\bi\s*\.\s*b\s*\.\s*[56]\b", _I)
_SHELF_CAPACITY_EXCLUDE_RE = re.compile(
    r"smaller reporting company|emerging growth company|(?:large |non-)?accelerated filer|"
    r"minimum listing maintenance", _I)

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

_DATE_TEXT = r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}"
_CLOSED_RE = re.compile(r"\b(?:closed|consummated|completed (?:the |a |its )?(?:offering|placement|transaction))\b", _I)
_PENDING_RE = re.compile(
    r"expect(?:ed|s)?[^.;]{0,70}clos(?:e|ing)|clos(?:e|ing)[^.;]{0,70}expected|"
    r"subject to[^.;]{0,70}closing conditions", _I)
_COMMISSION_RE = re.compile(
    r"(?:commission(?: rate)?(?: of| equal to)?|cash fee (?:of|equal to))\s*(\d+(?:\.\d+)?)%", _I)
_AGENT_ROLES = {
    "sales agent": "sales_agent", "placement agent": "placement_agent",
    "book-running manager": "underwriter", "book running manager": "underwriter",
    "underwriter": "underwriter",
}
_ENTITY_NAME = r"[A-Z][A-Za-z0-9&.,/' -]{1,120}?"
_AGENT_ACTOR_RE = re.compile(
    rf"(?P<name>{_ENTITY_NAME})(?:\s*\(\"(?P<alias>[^\"]+)\"\))?\s+"
    r"(?:(?:is|was|will be) )?(?:acting|acted|acts|serves?|served) as "
    r"(?:the |our )?(?:sole |lead |co-|exclusive )?"
    r"(?P<role>placement agent|book[- ]running manager|sales agent|underwriter)", _I)
_AGENT_AGREEMENT_RE = re.compile(
    r"(?P<agreement>equity distribution|at[- ]the[- ]market offering|open market sale|"
    r"placement agency|underwriting|standby equity purchase|equity purchase) agreement"
    r"(?:\s*\([^)]{0,120}\))?\s+with\s+"
    rf"(?P<name>{_ENTITY_NAME})(?=\s*\(|,?\s+dated\b|,\s+(?:a|an|pursuant)\b|;|$)", _I)
_REPRICING_RE = re.compile(
    r"(?:exercise price|repric\w*|reduc\w*)[^;]{0,120}?from\s+(?:US)?\$\s?"
    r"(?P<old>\d[\d,]*(?:\.\d+)?)\s+(?:per share\s+)?to\s+(?:US)?\$\s?"
    r"(?P<new>\d[\d,]*(?:\.\d+)?)", _I)


def offering_kind(sentence: str) -> str:
    for kind, pattern in _KIND_RULES:
        if pattern.search(sentence):
            return kind
    return ""


def _statement(topic: str, sentence: str, focus: Optional[tuple[int, int]] = None, **extra) -> dict:
    displayed = clip_sentence(sentence, SENTENCE_MAX_CHARS, focus)
    row = {
        "topic": topic,
        "sentence": displayed,
        # Filter complete source matches after clipping. Parsing the clipped
        # text itself could turn a partially cut dollar value into a new one.
        "figures": [f for f in parse_figures(sentence) if f["text"] in displayed],
    }
    row.update({key: value for key, value in extra.items() if value not in ("", None, [])})
    return row


def _closing_date_text(sentence: str, closed: re.Match) -> Optional[str]:
    """Bind a date to the closing verb, never to a nearby dated agreement."""
    # Recognize a small closing-object grammar rather than searching arbitrary
    # following prose. This still covers "completed the offering of N common
    # shares in a registered direct offering on DATE".
    trailing = re.match(
        r"(?:\s+(?:(?:the|a|its|our)\s+)?"
        r"(?:private placement|registered direct offering|public offering|offering|placement|transaction))?"
        r"(?:\s+of\s+\d[\d,]*(?:\.\d+)?\s+"
        r"(?:(?:Class\s+[A-Z]\s+)?(?:common|ordinary|preferred)\s+)?shares)?"
        r"(?:\s+in\s+(?:a|the)\s+(?:registered direct|public|private placement)\s+offering)?"
        rf"\s+(?:on|as of)\s+({_DATE_TEXT})\b",
        sentence[closed.end():], _I)
    if trailing:
        return trailing.group(1)
    leading = re.fullmatch(
        rf"\s*On\s+({_DATE_TEXT}),?\s+"
        r"(?:(?:the\s+)?(?:Company|Registrant)|we|it)\s+"
        r"(?:(?:has|had|successfully)\s+)*",
        sentence[:closed.start()], _I)
    return leading.group(1) if leading else None


def _offering_details(sentence: str) -> dict:
    """Only the closing/registration assertions made in this sentence.

    A later sentence about another transaction must not close an earlier
    offering. Status-only sentences remain separate evidence rows.
    """
    details: dict = {}
    closed = _CLOSED_RE.search(sentence)
    if closed and re.search(r"\b(?:if|may|would|will|expect\w*)\b[^,;]{0,45}$",
                            sentence[max(0, closed.start() - 55):closed.start()], _I):
        closed = None
    pending = _PENDING_RE.search(sentence)
    if closed and pending:
        details.update(closing_status="unknown",
                       closing_caveat="This sentence contains both completed and pending closing language; no transaction linkage is inferred.")
    elif closed:
        details["closing_status"] = "closed"
        details["closing_evidence"] = closed.group(0)
        date_text = _closing_date_text(sentence, closed)
        if date_text:
            details["closing_date"] = _iso_date(date_text)
            details["closing_date_text"] = date_text
    elif pending or re.search(r"\bentered into\b|\bagreed to (?:sell|issue)\b", sentence, _I):
        details["closing_status"] = "announced_pending"
        cue = pending or re.search(r"\bentered into\b|\bagreed to (?:sell|issue)\b", sentence, _I)
        details["closing_evidence"] = cue.group(0)
        details["closing_caveat"] = "No closing confirmation is stated in this sentence; later filing evidence may supersede it."
        if pending:
            date = re.search(_DATE_TEXT, sentence[pending.start():pending.end() + 100], _I)
            if date:
                details["expected_closing_date"] = _iso_date(date.group(0))
                details["expected_closing_date_text"] = date.group(0)
    if "closing_status" in details:
        details["closing_status_scope"] = "statement"
    if re.search(r"registered direct", sentence, _I):
        details["registration_status"] = "registered_direct"
        details["registration_evidence"] = re.search(r"registered direct", sentence, _I).group(0)
    elif (re.search(r"private placement|\bPIPE\b|private investment in public equity", sentence, _I)
          and re.search(r"unregistered|not (?:been )?registered|exempt(?:ion)? from registration", sentence, _I)):
        details["registration_status"] = "unregistered_pipe"
        details["registration_evidence"] = re.search(
            r"unregistered|not (?:been )?registered|exempt(?:ion)? from registration", sentence, _I).group(0)
    elif re.search(r"(?:under|pursuant to)[^.;]{0,80}shelf registration", sentence, _I):
        details["registration_status"] = "public_shelf_takedown"
        details["registration_evidence"] = re.search(
            r"(?:under|pursuant to)[^.;]{0,80}shelf registration", sentence, _I).group(0)
    return details


def _offering_statement(sentence: str, kind: str,
                        focus: Optional[tuple[int, int]] = None) -> dict:
    row = _statement("offering", sentence, focus, kind=kind)
    row.update(_offering_details(row["sentence"]))
    return row


def _clean_agent_name(name: str) -> str:
    name = re.split(r"\b(?:through|with)\s+", name)[-1].strip(" ,")
    if name.lower().startswith(("the company", "the lender", "we ", "our ", "in connection")):
        return ""
    return name if name and name[0].isupper() and len(name) <= 100 else ""


def agent_statements(sentences: list[str]) -> list[dict]:
    """Named parties and commissions; alias links retain their declaration.

    No fuzzy name matching is used. A commission sentence can use an alias
    only when the same document explicitly declared that alias for a party.
    """
    out: list[dict] = []
    declared: dict[str, list[dict]] = {}
    for sentence in sentences:
        entries = []
        lower = sentence.lower()
        for match in _AGENT_AGREEMENT_RE.finditer(sentence):
            agreement = match.group("agreement").lower()
            role = ("placement_agent" if agreement == "placement agency" else
                    "underwriter" if agreement == "underwriting" else
                    "equity_line_investor" if "equity purchase" in agreement else "sales_agent")
            entries.append((match, role, match.group("name"), None))
        # The actor pattern starts with a broad entity-name match. Skip it
        # when none of its mandatory roles occur, avoiding quadratic scanning
        # of unrelated prospectus prose.
        actor_text = sentence if any(role in lower for role in ("agent", "manager", "underwriter")) else ""
        for match in _AGENT_ACTOR_RE.finditer(actor_text):
            entries.append((match, _AGENT_ROLES[match.group("role").lower()],
                            match.group("name"), match.group("alias")))
        for match, role, raw_name, actor_alias in entries:
            name = _clean_agent_name(raw_name)
            if not name:
                continue
            # Agreement patterns stop before the explicit alias declaration.
            suffix = sentence[match.end():match.end() + 80]
            alias_match = re.match(r'\s*\("([^\"]+)"\)', suffix)
            alias = actor_alias or (alias_match.group(1) if alias_match else "")
            row = _statement("agent", sentence, match.span(), name=name, role=role)
            if name not in row["sentence"]:
                continue
            if alias:
                row["alias"] = alias
            out.append(row)
            declaration = {"name": name, "role": role, "sentence": row["sentence"]}
            for key in (name, alias):
                if key:
                    declared.setdefault(key, []).append(declaration)

        commission = _COMMISSION_RE.search(sentence)
        if not commission:
            continue
        matches = []
        for key, declarations in declared.items():
            named = re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", sentence)
            if named and named.end() <= commission.start():
                identities = {(d["name"], d["role"]) for d in declarations}
                if len(identities) == 1:
                    matches.append((named, key, declarations[-1]))
        # If more than one named party precedes a fee, attribution is not clear.
        identities = {(d["name"], d["role"]) for _, _, d in matches}
        if len(identities) != 1:
            continue
        named, key, declaration = max(matches, key=lambda entry: entry[0].end())
        row = _statement("agent", sentence, (named.start(), commission.end()),
                         name=declaration["name"], name_text=key, role=declaration["role"],
                         commission_percent=_number(commission.group(1)),
                         commission_text=commission.group(0))
        if commission.group(0) not in row["sentence"] or key not in row["sentence"]:
            continue
        if declaration["sentence"] != sentence:
            row["name_evidence"] = declaration["sentence"]
        out.append(row)
    return out


def _shelf_cover_statement(sentence: str) -> Optional[dict]:
    resale = re.search(r"offer and resale|resale by|proceeds from (?:the )?resale|register(?:ing|ed)?[^;]{0,100}for resale", sentence, _I)
    primary = re.search(r"we (?:may |are (?:also )?)?(?:offer(?:ing)?|sell)|public primary offering", sentence, _I)
    if not resale and not primary:
        return None
    row = _statement("shelf_capacity", sentence, (resale or primary).span(),
                     subtopic="base_shelf_total", offering_type="resale" if resale else "primary")
    proceeds = re.search(r"(?:will|do) not receive any proceeds", sentence, _I)
    if proceeds and proceeds.group(0) in row["sentence"]:
        row["issuer_receives_resale_proceeds"] = False
        row["proceeds_evidence"] = proceeds.group(0)
    # Some cover paragraphs interpose a defined term between "shares" and
    # "of our common stock". Retain the exact count, without guessing a class.
    if re.search(r"common stock|ordinary shares", row["sentence"], _I):
        for match in re.finditer(rf"({_COUNT}) shares\b", row["sentence"], _I):
            if not any(f["type"] == "shares" and f["text"].startswith(match.group(0))
                       for f in row["figures"]):
                row["figures"].append({"type": "shares", "value": _number(match.group(1)),
                                       "text": match.group(0)})
    return row


def _shares_outstanding(sentence: str) -> Optional[dict]:
    match = _OUTSTANDING_RE.search(sentence)
    if not match or _OUTSTANDING_EXCLUDE_RE.search(sentence):
        return None
    # A prospectus often describes its hypothetical post-offering total and
    # then states the actual baseline in the same sentence. The qualifiers
    # belong to their own counts, not to every number in the sentence.
    baseline = _BASED_ON_OUTSTANDING_RE.search(sentence)
    if baseline and not (_DERIVATIVE_SHARES_RE.search(baseline.group("qualifier"))
                         or _SHARE_FIGURE_RE.search(baseline.group("qualifier"))):
        date_text = baseline.group("date")
        as_of = _AS_OF_RE.search(sentence[:baseline.start()]) if not date_text else None
        date_text = date_text or (as_of.group(1) if as_of else "")
        row = _statement("shares_outstanding", sentence, baseline.span(),
                         basis="before_offering" if "offering" in sentence.lower() else "stated",
                         as_of=_iso_date(date_text))
        row["figures"] = [f for f in parse_figures(baseline.group("shares"))
                          if f["type"] == "shares" and f["text"] in row["sentence"]]
        return row if row["figures"] else None

    table = _OUTSTANDING_TABLE_RE.search(sentence)
    candidates = list(_SHARE_FIGURE_RE.finditer(sentence))
    selected = []
    for index, candidate in enumerate(candidates):
        end = candidates[index + 1].start() if index + 1 < len(candidates) else len(sentence)
        tail = sentence[candidate.end():end]
        # In a mixed sentence, the next semicolon begins a different clause.
        tail = tail.split(";", 1)[0]
        outstanding = re.search(r"\boutstanding\b", tail[:180], _I)
        if outstanding:
            bridge = tail[:outstanding.end()]
            following = tail[outstanding.end():outstanding.end() + 35]
            if (_DERIVATIVE_SHARES_RE.search(bridge)
                    or re.match(r"\s+(?:warrants?|options?|restricted stock units?|rsus?)\b",
                                following, _I)):
                continue
        elif table and candidate.start() >= table.end():
            if _DERIVATIVE_SHARES_RE.search(tail[:120]):
                continue
        else:
            continue
        selected.append(candidate)
    # "10,000 Class A shares and 5,000 Class B shares outstanding" applies
    # the shared trailing qualifier to both counts, while preserving classes.
    for index in range(len(candidates) - 2, -1, -1):
        candidate, following = candidates[index:index + 2]
        between = sentence[candidate.end():following.start()]
        if (following in selected and candidate not in selected
                and re.fullmatch(r"\s*,?\s*(?:and\s+)?", between, _I)):
            selected.insert(0, candidate)
    if not selected:
        return None

    lowered = sentence.lower()
    if table:
        basis = "after_offering" if "after" in table.group("basis").lower() else "before_offering"
    elif re.search(r"outstanding (?:immediately )?after|after giving effect|assuming (?!no\b)", lowered):
        basis = "after_offering"
    else:
        basis = "stated"
    as_of = _AS_OF_RE.search(sentence)
    row = _statement("shares_outstanding", sentence,
                     (selected[0].start(), selected[-1].end()), basis=basis,
                     as_of=_iso_date(as_of.group(1)) if as_of else "")
    selected_texts = {candidate.group(0) for candidate in selected}
    row["figures"] = [f for f in row["figures"]
                      if f["type"] == "shares" and f["text"] in selected_texts]
    return row if row["figures"] else None


def _shelf_subtopic(sentence: str) -> str:
    """Label the statement's explicit subject without inferring a capacity."""
    if re.search(r"eligible to (?:offer and )?sell|(?:may|can) (?:offer and )?sell up to|"
                 r"remaining (?:eligible |available )?(?:offering |shelf )?capacity", sentence, _I):
        return "remaining_eligible_capacity"
    if (re.search(r"\bsold\b", sentence, _I)
            and re.search(r"(?:12|twelve)[- ](?:calendar[- ]?)?months?", sentence, _I)):
        return "prior_12m_sales"
    if (re.search(r"aggregate market value|public float", sentence, _I)
            and re.search(r"(?:was|is|of|at) (?:approximately )?(?:US)?\$", sentence, _I)
            and not re.search(r"remains below|less than|exceed(?:ing|ed|s)?", sentence, _I)):
        return "mvphs_float"
    if _BABY_SHELF_RE.search(sentence):
        return "baby_shelf_restriction"
    if re.search(r"shelf registration|offer and sell up to", sentence, _I):
        return "base_shelf_total"
    return ""


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
    evidence: dict = {}
    multiple = _EXCHANGE_MULTIPLE_RE.search(sentence)
    if multiple:
        features["exchange_multiple"] = _number(next(g for g in multiple.groups() if g))
        evidence["exchange_multiple"] = multiple.group(0)
    floor = _EXERCISE_FLOOR_RE.search(sentence)
    if floor:
        features["exercise_price_floor"] = _number(floor.group(1))
        evidence["exercise_price_floor"] = floor.group(0)
    cap = _OWNERSHIP_CAP_RE.search(sentence)
    if cap:
        features["beneficial_ownership_cap_percent"] = _number(next(g for g in cap.groups() if g))
        evidence["beneficial_ownership_cap_percent"] = cap.group(0)
    prefunded = re.search(r"pre-?funded", sentence, _I)
    if prefunded:
        features["pre_funded"] = True
        evidence["pre_funded"] = prefunded.group(0)
    cashless = re.search(r"cashless", sentence, _I)
    if cashless:
        denial = re.search(r"(?:cannot|may not|shall not|not permitted to|not entitled to|not exercisable)[^.;]{0,60}cashless|"
                           r"no (?:right to |provision for )cashless|cashless[^.;]{0,40}(?:not permitted|prohibited|unavailable)", sentence, _I)
        features["cashless_exercise"] = not bool(denial)
        evidence["cashless_exercise"] = (denial or cashless).group(0)
    repricing = _REPRICING_RE.search(sentence)
    if repricing and not re.search(r"reduc\w*|repric\w*|amend\w*|lower\w*|reset\w*|chang\w*", sentence, _I):
        repricing = None
    row = _statement("warrants", sentence, (repricing or multiple or match).span())
    row["features"] = {key: value for key, value in features.items()
                       if evidence[key] in row["sentence"]}
    row["feature_evidence"] = {key: evidence[key] for key in row["features"]}
    if repricing and repricing.group(0) in row["sentence"]:
        conditional = re.search(r"\b(?:may|would|could|if|propos\w*)\b[^.;]{0,100}", sentence[:repricing.start() + 40], _I)
        row["repricing_events"] = [{
            "old_exercise_price": _number(repricing.group("old")),
            "new_exercise_price": _number(repricing.group("new")),
            "text": repricing.group(0),
            "status": "proposed_or_conditional" if conditional else "stated",
        }]
    return row if row["figures"] or features else None


def extract_statements(sentences: list[str], prospectus: bool = False,
                       shelf: bool = False) -> list[dict]:
    """Dilution statements in one document's sentences, in document order.

    `prospectus` marks a 424B supplement: its offering statement is read from
    the cover page only (`PROSPECTUS_COVER_SENTENCES`), since the rest of a
    prospectus restates history and boilerplate. `shelf` additionally reads
    primary/resale registration capacity from the first cover-page sentences.
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    counts: dict[str, int] = {}
    equity_line_document = any(
        offering_kind(s) in ("equity_line", "atm") for s in sentences[:PROSPECTUS_COVER_SENTENCES * 2])

    def add(row: Optional[dict]) -> None:
        if not row:
            return
        key = (row["topic"], row["sentence"], row.get("name"), row.get("role"),
               row.get("commission_percent"))
        if key in seen or counts.get(row["topic"], 0) >= MAX_STATEMENTS_PER_TOPIC:
            return
        seen.add(key)
        counts[row["topic"]] = counts.get(row["topic"], 0) + 1
        out.append(row)

    in_base_prospectus = False
    for index, sentence in enumerate(sentences):
        if shelf and index >= PROSPECTUS_COVER_SENTENCES:
            break
        if (prospectus and index > PROSPECTUS_COVER_SENTENCES
                and sentence.strip() == _BASE_PROSPECTUS_HEADING):
            in_base_prospectus = True
        if len(sentence) < 25:
            continue

        shelf_match = _SHELF_CAPACITY_RE.search(sentence)
        cover_row = _shelf_cover_statement(sentence) if shelf else None
        if cover_row:
            add(cover_row)
        elif shelf_match and not (_SHELF_CAPACITY_EXCLUDE_RE.search(sentence)
                          and not _BABY_SHELF_RE.search(sentence)):
            row = _statement("shelf_capacity", sentence, shelf_match.span(),
                             part="base_prospectus" if in_base_prospectus else "",
                             subtopic=_shelf_subtopic(sentence))
            if any(f["type"] == "dollars" for f in row["figures"]) or _BABY_SHELF_RE.search(sentence):
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
            row = _offering_statement(sentence, kind)
            if any(f["type"] in ("shares", "dollars", "warrants") for f in row["figures"]):
                add(row)
        elif terms:
            row = _statement("equity_line_terms", sentence, terms.span())
            if row["figures"]:
                add(row)
        elif on_cover and _PROSPECTUS_OFFERING_RE.search(sentence):
            row = _offering_statement(sentence, "prospectus_supplement")
            if any(f["type"] in ("shares", "dollars", "warrants") for f in row["figures"]):
                add(row)
        elif not prospectus and _PROCEEDS_RE.search(sentence):
            # "The gross proceeds from this Offering were $3.9 million." names
            # no deal type of its own: reported as a proceeds statement.
            row = _offering_statement(sentence, "proceeds", _PROCEEDS_RE.search(sentence).span())
            if any(f["type"] == "dollars" for f in row["figures"]):
                add(row)

        # The closing may be stated in its own sentence without repeating
        # the transaction size. Keep it separate rather than assigning it to
        # every offering or share count found elsewhere in the document.
        if (re.search(r"\boffering\b|\bprivate placement\b", sentence, _I)
                and (_PENDING_RE.search(sentence) or _CLOSED_RE.search(sentence))
                and (on_cover or not prospectus)):
            status_row = _offering_statement(sentence, kind or "closing_status")
            if status_row.get("closing_status"):
                add(status_row)

        if not prospectus or index < PROSPECTUS_COVER_SENTENCES * 5:
            add(_warrants(sentence))
    for row in agent_statements(sentences[:PROSPECTUS_COVER_SENTENCES] if prospectus or shelf else sentences):
        add(row)
    return out
