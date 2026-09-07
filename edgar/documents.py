"""Validated SEC document links shared by index parsing and downloads."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit


SEC_ORIGIN = "https://www.sec.gov"
VIEWER_PATHS = {"/ix", "/ix.xhtml", "/ixviewer/ix.html", "/ixviewer/doc/action"}


def _sec_url(value: str, base_url: str) -> str:
    if not value or re.search(r"[\x00-\x20\\]", value):
        raise ValueError("Invalid SEC document URL")
    decoded_path = unquote(urlsplit(value).path)
    if "\\" in decoded_path or any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise ValueError("Invalid SEC document path")
    if value.startswith("Archives/"):
        value = "/" + value
    url = urljoin(base_url, value)
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"}
            or parts.hostname not in {"www.sec.gov", "sec.gov"}
            or parts.username is not None or parts.password is not None
            or parts.port not in {None, 443}):
        raise ValueError("Document URL must have a trusted SEC origin")
    return url


def normalize_sec_document_url(href: str, base_url: str = SEC_ORIGIN + "/") -> str:
    """Unwrap supported SEC viewers and require an Archives filing document.

    Never follow a `doc` parameter until both wrapper and destination have
    passed origin/path validation. Canonical URLs always use HTTPS.
    """
    url = _sec_url(href, base_url)
    parts = urlsplit(url)
    if parts.path in VIEWER_PATHS:
        docs = parse_qs(parts.query, keep_blank_values=True).get("doc", [])
        if len(docs) != 1 or not docs[0]:
            raise ValueError("SEC viewer URL requires exactly one document")
        url = _sec_url(docs[0], SEC_ORIGIN + "/")
        parts = urlsplit(url)
    path = unquote(parts.path)
    segments = path.split("/")
    if (parts.query or len(segments) < 7
            or segments[:4] != ["", "Archives", "edgar", "data"]
            or not segments[4].isascii() or not segments[4].isdigit()
            or not segments[5].isascii() or not segments[5].isdigit()
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", segment)
                   or segment in {".", ".."} for segment in segments[6:])):
        raise ValueError("Document URL must identify a SEC Archives filing document")
    return SEC_ORIGIN + path


def sec_document_name(url: str) -> str:
    return urlsplit(url).path.rsplit("/", 1)[-1]


def validate_document_content(content: bytes) -> None:
    if not content:
        raise ValueError("SEC returned an empty document")
    if re.search(rb"<title\b[^>]*>\s*(?:inline\s+)?XBRL\s+Viewer\s*</title\s*>",
                 content, re.I):
        raise ValueError("SEC returned an XBRL viewer shell instead of a filing")
