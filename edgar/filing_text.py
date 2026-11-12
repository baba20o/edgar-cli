"""Filing documents as clean text, paragraphs and sentences.

Shared by `edgar text` (read any filing), the 6-K event classifier and the
dilution view. Everything here is pure text processing: no network, no
filer-specific rules.

- `document_to_text` turns an SEC document (HTML, inline XBRL, XML, plain
  text) into paragraphs separated by blank lines. Table rows become one line
  each. Hidden inline-XBRL headers, scripts and styles are dropped.
- `split_sentences` splits that text into sentences, rejoining sentences cut
  by a page break and not splitting after common abbreviations.
- `match_sentences` returns the sentences that match a regular expression.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Iterable, Optional

_BLOCK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "dl",
    "dt", "dd", "section", "article", "blockquote", "hr", "br", "table", "tr",
    "pre", "center", "title", "caption", "header", "footer", "body",
}
_HIDDEN_TAGS = {"head", "script", "style", "ix:header", "noscript"}
_CELL_TAGS = {"td", "th"}

# Typographic characters normalized so patterns written in plain ASCII match.
_TRANSLATE = str.maketrans({
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "‌": "", "‍": "", "﻿": "", "­": "",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-",
})

_PAGE_FURNITURE_RE = re.compile(
    r"^(?:(?:[A-Z]{1,3}|[ivxlc]{1,6})?-?\s?\d{1,3}|[ivxlc]{1,6}|table of contents|"
    r"page \d{1,3}(?: of \d{1,3})?)$",
    re.IGNORECASE,
)

# A period after one of these does not end a sentence.
# Kept short on purpose: every entry also stops a split when the word really
# does end a sentence, so words that often close one ("SEC.", "etc.") are out.
_ABBREVIATIONS = {
    "inc", "ltd", "corp", "co", "llc", "lp", "plc", "pte", "pty", "gmbh",
    "no", "nos", "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs",
    "approx", "dept", "fig", "para", "messrs",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "u.s", "u.k", "e.g", "i.e", "a.m", "p.m", "u.s.c", "l.p",
    "n.a", "s.a",
}

_SENTENCE_END_RE = re.compile(r"([.!?])([\"')\]]*)\s+(?=[\"'(\[]*[A-Z0-9$])")


def normalize_text(text: str) -> str:
    """Normalize typographic spaces, quotes and dashes; collapse all whitespace."""
    return re.sub(r"\s+", " ", (text or "").translate(_TRANSLATE)).strip()


# EDGAR serves some documents inside their SGML submission wrapper
# (<DOCUMENT><TYPE>6-K<SEQUENCE>1<FILENAME>...<TEXT>). It is not filing text.
_SGML_WRAPPER_RE = re.compile(
    r"\A\s*<DOCUMENT>\s*(?:<(?:TYPE|SEQUENCE|FILENAME|DESCRIPTION)>[^\n<]*\s*)*<TEXT>",
    re.IGNORECASE,
)


def strip_sgml_wrapper(content: str) -> str:
    """Drop the SGML <DOCUMENT>...<TEXT> header and trailer around a document."""
    stripped = _SGML_WRAPPER_RE.sub("", content, count=1)
    if stripped is not content:
        stripped = re.sub(r"</TEXT>\s*</DOCUMENT>\s*\Z", "", stripped, flags=re.IGNORECASE)
    return stripped


class _PlainTextParser(HTMLParser):
    """Block-aware HTML to text: one line per block element or table row."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._fragments: list[str] = []
        self._hidden: list[str] = []

    def _flush(self) -> None:
        line = normalize_text("".join(self._fragments))
        if line:
            self.lines.append(line)
        self._fragments = []

    def handle_starttag(self, tag, attrs):
        if tag in _HIDDEN_TAGS:
            self._hidden.append(tag)
        if self._hidden:
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        elif tag in _CELL_TAGS:
            self._fragments.append(" ")

    def handle_startendtag(self, tag, attrs):
        if self._hidden:
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if self._hidden:
            if tag == self._hidden[-1]:
                self._hidden.pop()
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        elif tag in _CELL_TAGS:
            self._fragments.append(" ")

    def handle_data(self, data):
        if not self._hidden:
            self._fragments.append(data)


def _looks_like_markup(content: str) -> bool:
    head = content[:4000].lower()
    return bool(re.search(r"<(?:html|body|div|p|table|span|font|\?xml|xbrl|ix:)", head))


def document_to_text(content: str) -> str:
    """Convert one SEC document to clean text, paragraphs split by blank lines.

    Markup (HTML, inline XBRL, XML) is parsed block by block; anything else is
    treated as plain text and only normalized. Runs of blank lines collapse to
    one, so the output is stable to compare and cheap to hand to an agent.
    """
    if not content:
        return ""
    content = strip_sgml_wrapper(content)
    if _looks_like_markup(content):
        parser = _PlainTextParser()
        parser.feed(content)
        parser.close()
        parser._flush()
        lines = parser.lines
    else:
        lines = [normalize_text(line) for line in content.splitlines()]
        # Plain text keeps its own paragraphing: blank lines separate blocks.
        blocks, current = [], []
        for line in lines:
            if line:
                current.append(line)
            elif current:
                blocks.append(" ".join(current))
                current = []
        if current:
            blocks.append(" ".join(current))
        lines = blocks
    return "\n\n".join(line for line in lines if line)


def paragraphs(text: str) -> list[str]:
    """Paragraphs of `document_to_text` output, page furniture removed.

    A paragraph cut in two by a page break (the first half has no closing
    punctuation and the second starts in lower case) is joined back together.
    """
    out: list[str] = []
    for block in (text or "").split("\n\n"):
        block = block.strip()
        if not block or _PAGE_FURNITURE_RE.match(block):
            continue
        if (out and block[:1].islower()
                and not re.search(r"[.!?:;][\"')\]]*$", out[-1])
                and len(out[-1]) > 60):
            out[-1] = f"{out[-1]} {block}"
        else:
            out.append(block)
    return out


def _is_abbreviation(before: str) -> bool:
    """True when the text before a period ends in a known abbreviation or initial."""
    match = re.search(r"([A-Za-z][A-Za-z.]*)$", before)
    if not match:
        return False
    token = match.group(1).lower()
    if token in _ABBREVIATIONS:
        return True
    # Single-letter initials ("John A. Smith") and dotted forms ("U.S.").
    if len(token) == 1 and match.group(1).isupper():
        return True
    return bool(re.fullmatch(r"(?:[a-z]\.)+[a-z]", token))


def _split_paragraph(paragraph: str) -> list[str]:
    sentences = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(paragraph):
        if match.group(1) == "." and _is_abbreviation(paragraph[start:match.start()]):
            continue
        end = match.end(2)
        sentence = paragraph[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = match.end()
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def split_sentences(text: str) -> list[str]:
    """Split `document_to_text` output into sentences, in document order."""
    sentences: list[str] = []
    for paragraph in paragraphs(text):
        sentences.extend(_split_paragraph(paragraph))
    return sentences


def clip_sentence(sentence: str, max_chars: int = 600,
                  focus: Optional[tuple[int, int]] = None) -> str:
    """Shorten a long sentence to `max_chars`, keeping the `focus` span visible.

    Cut ends are marked with `...` so a clipped quote is never mistaken for
    the whole sentence.
    """
    if len(sentence) <= max_chars:
        return sentence
    if not focus:
        return sentence[:max_chars].rstrip() + " ..."
    lo, hi = focus
    centre = (lo + hi) // 2
    begin = max(0, min(centre - max_chars // 2, len(sentence) - max_chars))
    end = min(len(sentence), begin + max_chars)
    # Prefer to cut on word boundaries.
    if begin > 0:
        space = sentence.find(" ", begin)
        if 0 <= space < lo:
            begin = space + 1
    if end < len(sentence):
        space = sentence.rfind(" ", hi, end)
        if space > hi:
            end = space
    prefix = "... " if begin > 0 else ""
    suffix = " ..." if end < len(sentence) else ""
    return f"{prefix}{sentence[begin:end].strip()}{suffix}"


def compile_pattern(pattern: str, ignore_case: bool = True) -> re.Pattern:
    """Compile a user pattern; raises ValueError with a readable message."""
    try:
        return re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ValueError(f"Invalid pattern {pattern!r}: {exc}") from exc


def match_sentences(sentences: Iterable[str], pattern: re.Pattern,
                    context: int = 0, max_sentence_chars: int = 600,
                    limit: Optional[int] = None) -> list[dict]:
    """Return sentences matching `pattern`, each with its index and the match.

    `context` adds that many neighbouring sentences before and after each hit
    (as `before` / `after`), so a figure can be read with its qualifiers.
    """
    items = list(sentences)
    out = []
    for index, sentence in enumerate(items):
        match = pattern.search(sentence)
        if not match:
            continue
        row = {
            "index": index,
            "sentence": clip_sentence(sentence, max_sentence_chars, match.span()),
            "match": match.group(0),
        }
        if len(sentence) > max_sentence_chars:
            row["sentence_length"] = len(sentence)
            row["clipped"] = True
        if context:
            row["before"] = [clip_sentence(s, max_sentence_chars)
                             for s in items[max(0, index - context):index]]
            row["after"] = [clip_sentence(s, max_sentence_chars)
                            for s in items[index + 1:index + 1 + context]]
        out.append(row)
        if limit and len(out) >= limit:
            break
    return out
