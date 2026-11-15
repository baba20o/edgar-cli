"""Bounded, uncached polling of the SEC current-filings Atom feed.

The Atom ``updated`` field supplies the timestamp (its provenance is explicit).
This recent-feed window is not an historical archive or a guaranteed delivery
service. Cursor acknowledgement is separate from retrieval so callers can write
their output successfully before marking rows delivered.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests
from research_cli_base.http_client import retry_wait_seconds

from edgar.filing_text import document_to_text
from edgar.state import StateStore


FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
DEFAULT_FORMS = ("8-K", "6-K", "424B5", "S-3", "F-3", "S-1", "F-1", "EFFECT", "RW")
PAGE_SIZE = 100
_ATOM = "{http://www.w3.org/2005/Atom}"
_ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Feed timestamps require an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _forms(forms) -> tuple[str, ...]:
    values = DEFAULT_FORMS if forms is None else forms
    if isinstance(values, str):
        values = values.split(",")
    normalized = {str(value).strip().upper() for value in values if str(value).strip()}
    if "*" in normalized:
        return ()
    return tuple(sorted(normalized))


def _scope(forms: tuple[str, ...]) -> str:
    return "atom-v1:" + (",".join(forms) if forms else "*")


def _identity(row: dict) -> str:
    return f"{row['cik']}:{row['accession']}"


def _entry(entry: ET.Element) -> dict:
    title = entry.findtext(_ATOM + "title", "")
    identifier = entry.findtext(_ATOM + "id", "")
    summary_element = entry.find(_ATOM + "summary")
    summary = ""
    if summary_element is not None:
        summary = (summary_element.text or "") + "".join(
            ET.tostring(child, encoding="unicode") for child in summary_element)
    # SEC's HTML summaries begin with <b>, which is not a complete filing's
    # markup signature. Supply an explicit block wrapper for the shared parser.
    summary = document_to_text("<div>" + summary + "</div>")
    accession = _ACCESSION.search(identifier) or _ACCESSION.search(summary)
    category = next((c.get("term", "") for c in entry.findall(_ATOM + "category")
                     if c.get("label", "").lower() == "form type"), "")
    if not category and " - " in title:
        category = title.split(" - ", 1)[0]
    title_cik = re.search(r"\((\d{1,10})\)", title)
    link = next((link.get("href", "") for link in entry.findall(_ATOM + "link")
                 if link.get("rel", "alternate") == "alternate"), "")
    link = urljoin(FEED_URL, link)
    parts = urlsplit(link)
    url_cik = re.search(r"^/Archives/edgar/data/(\d{1,10})/", parts.path)
    if (parts.scheme not in {"http", "https"} or parts.hostname not in {"www.sec.gov", "sec.gov"}
            or parts.username or parts.password or parts.port not in {None, 443}
            or not url_cik):
        raise ValueError("Atom entry has no trusted SEC Archives filing link")
    cik_match = title_cik or url_cik
    if not accession or not cik_match or not category:
        raise ValueError("Atom entry is missing its accession, filer CIK, or form")
    accepted = entry.findtext(_ATOM + "updated", "")
    try:
        accepted = _utc(_timestamp(accepted))
    except (ValueError, TypeError):
        accepted = None
    filed = re.search(r"Filed:\s*(\d{4}-\d{2}-\d{2})", summary, re.I)
    item_sections = re.findall(r"\bItems?\s*:?\s*([\d.,;\s]+)", summary, re.I)
    items = sorted({item for section in item_sections
                    for item in re.findall(r"\b[1-9]\.\d{2}\b", section)})
    return {
        "accession": accession.group(),
        "cik": cik_match.group(1).zfill(10),
        "form": category.strip().upper(),
        "filed": filed.group(1) if filed else None,
        "acceptance_datetime": accepted,
        "acceptance_datetime_source": "atom.updated",
        "items": ",".join(items),
        "filing_url": "https://www.sec.gov" + parts.path,
        "name": re.sub(r"\s*\(\d{1,10}\).*", "", title.split(" - ", 1)[-1]).strip(),
    }


def parse_atom(content: bytes | str) -> tuple[list[dict], list[str]]:
    """Parse entries individually; malformed entries cannot silently advance state."""
    root = ET.fromstring(content)
    if root.tag != _ATOM + "feed":
        raise ValueError("SEC response is not an Atom feed")
    rows, errors = [], []
    for entry in root.findall(_ATOM + "entry"):
        try:
            rows.append(_entry(entry))
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
    return rows, errors


def _fetch_atom(client, start: int) -> bytes:
    params = {"action": "getcurrent", "output": "atom", "owner": "include",
              "count": PAGE_SIZE, "start": start}
    url = FEED_URL + "?" + urlencode(params)
    attempts = max(0, getattr(client, "max_retries", 0))
    try:
        for attempt in range(attempts + 1):
            response = None
            limiter = getattr(client, "rate_limiter", None)
            if limiter:
                limiter.acquire()
            try:
                response = client.session.get(
                    url, headers={"Accept": "application/atom+xml", "Cache-Control": "no-cache"},
                    timeout=getattr(client, "request_timeout", 30))
                response.raise_for_status()
                return response.content
            except requests.RequestException:
                retry = response is None or response.status_code == 429 or response.status_code >= 500
                if not retry or attempt == attempts:
                    raise
                time.sleep(retry_wait_seconds(attempt, response))
        raise requests.RequestException("SEC feed request did not return a response")
    finally:
        record = getattr(client, "_record_cache", None)
        if record:
            record({"key": url, "hit": False, "cache_bypass": "live_feed"})


def _enrich(rows: list[dict], companies: list[dict]) -> None:
    listings: dict[str, list[dict]] = {}
    for company in companies:
        cik = str(company.get("cik", "")).zfill(10)
        ticker = company.get("ticker") or ""
        exchange = company.get("exchange") or ""
        listing = {"ticker": ticker, "exchange": exchange}
        if ticker and listing not in listings.setdefault(cik, []):
            listings[cik].append(listing)
    for row in rows:
        row["listings"] = sorted(listings.get(row["cik"], []), key=lambda item: (item["ticker"], item["exchange"]))
        row["tickers"] = sorted({item["ticker"] for item in row["listings"]})
        row["exchanges"] = sorted({item["exchange"] for item in row["listings"] if item["exchange"]})
        # A filer-level entry cannot identify which of multiple share classes
        # the filing concerns. Do not silently select the first ticker.
        row["ticker"] = row["tickers"][0] if len(row["tickers"]) == 1 else None
        row["exchange"] = row["exchanges"][0] if len(row["exchanges"]) == 1 else None


def fetch_feed(client, forms=None, since_minutes: int = 15, use_state: bool = False,
               state_store: StateStore | None = None, now: datetime | str | None = None,
               max_pages: int = 10) -> dict:
    """Read recent filings and propose, but never persist, a delivery cursor.

    With existing state, retain the full requested overlap behind its watermark,
    or the current lookback when that is earlier. Acknowledged IDs deduplicate
    that overlap, allowing newly published
    entries with slightly older timestamps to surface. A strict older boundary
    retains timestamp ties. If the finite feed no longer reaches the boundary,
    coverage is incomplete; arrivals older than the overlap are not guaranteed.
    Every complete poll proposes its start time as the scanned-through watermark,
    including quiet scopes and empty feeds; output delivery still controls ack.
    """
    selected = _forms(forms)
    scope = _scope(selected)
    if (not isinstance(since_minutes, (int, float)) or isinstance(since_minutes, bool)
            or since_minutes <= 0 or not isinstance(max_pages, int)
            or isinstance(max_pages, bool) or max_pages <= 0):
        return {"error": "since_minutes and max_pages must be positive", "filings": [], "total": 0}
    try:
        current = datetime.now(timezone.utc) if now is None else (_timestamp(now) if isinstance(now, str) else now)
        if current.tzinfo is None:
            raise ValueError("now must include a UTC offset")
        current = current.astimezone(timezone.utc)
        store = (state_store or StateStore()) if use_state else None
        cursor = store.get_feed_cursor(scope) if store else None
        lookback = current - timedelta(minutes=since_minutes)
        cursor_time = _timestamp(cursor["timestamp"]) if cursor else None
        cutoff = min(cursor_time - timedelta(minutes=since_minutes), lookback) if cursor_time else lookback
        if cursor_time and cursor_time > current:
            raise ValueError("Feed state timestamp is later than the requested polling time")
    except (ValueError, TypeError, OSError, KeyError) as exc:
        return {"error": f"Invalid feed state or time: {exc}", "filings": [], "total": 0}
    seen_boundary = set(cursor.get("seen", [])) if cursor else set()
    recent_seen = cursor.get("recent_seen", {}) if cursor else {}
    warnings, rows, unresolved = [], {}, {}
    pages = 0
    examined = 0
    oldest = None
    previous = None
    complete = False
    reason = "page_limit"
    failed = False
    exhausted = False
    request_error = None
    for page in range(max_pages):
        try:
            page_rows, errors = parse_atom(_fetch_atom(client, page * PAGE_SIZE))
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            failed = True
            reason = "request_or_parse_failure"
            request_error = str(exc)
            warnings.append({"code": "feed_fetch_failed", "message": str(exc), "start": page * PAGE_SIZE})
            break
        pages += 1
        examined += len(page_rows) + len(errors)
        if errors:
            failed = True
            warnings.append({"code": "feed_invalid_entries", "message": "Some entries could not be identified.", "count": len(errors)})
        for row in page_rows:
            if not row["acceptance_datetime"]:
                if not selected or row["form"] in selected:
                    unresolved[_identity(row)] = row
                    failed = True
                continue
            stamp = _timestamp(row["acceptance_datetime"])
            if previous is not None and stamp > previous:
                failed = True
                warnings.append({"code": "feed_order_changed", "message": "Atom entries were not in descending timestamp order; no cursor will advance."})
            previous = stamp
            oldest = stamp if oldest is None else min(oldest, stamp)
            if selected and row["form"] not in selected:
                continue
            identity = _identity(row)
            if (stamp < cutoff or stamp > current or identity in recent_seen
                    or (stamp == cursor_time and identity in seen_boundary)):
                continue
            rows[identity] = row
        # An older row closes the equal-timestamp boundary as well. Parse or
        # ordering failures still prevent cursor acknowledgement.
        if oldest is not None and oldest < cutoff:
            complete = not failed
            reason = "cutoff_reached"
            break
        exhausted = len(page_rows) + len(errors) < PAGE_SIZE
        if exhausted:
            if oldest is None:
                # An empty source can anchor a new/recent poll, but cannot prove
                # coverage of an older outstanding interval after a long gap.
                empty_covers_cursor = cursor_time is None or cursor_time >= lookback
                complete = not failed and empty_covers_cursor
                reason = "empty_feed" if empty_covers_cursor else "retention_gap"
            else:
                complete = not failed and oldest <= cutoff
                reason = "cutoff_reached" if oldest <= cutoff else "retention_gap"
            break
    output = sorted(rows.values(), key=lambda row: (_timestamp(row["acceptance_datetime"]), row["accession"], row["cik"]), reverse=True)
    unknown = list(unresolved.values())
    if unknown:
        warnings.append({"code": "feed_missing_timestamps", "message": "Entries with missing or ambiguous timestamps are separate from time-filtered filings; state will not advance.", "count": len(unknown)})
    try:
        mapping = client.companies()
        if "error" in mapping:
            raise ValueError(mapping["error"])
        _enrich(output + unknown, mapping.get("companies", []))
    except (requests.RequestException, ValueError, OSError) as exc:
        _enrich(output + unknown, [])
        warnings.append({"code": "feed_ticker_map_unavailable", "message": str(exc)})
    if not complete:
        warnings.append({"code": "feed_incomplete_coverage", "message": "Coverage is incomplete; state will not advance. Retry or increase max_pages, and investigate any retention gap.", "reason": reason})
    result = {
        "filings": output, "total": len(output), "forms": list(selected) or ["*"],
        "since": _utc(cutoff), "polled_at": _utc(current), "source": FEED_URL,
        "state_scope": scope, "state_used": bool(cursor), "overlap_minutes": since_minutes,
        "coverage": {"complete": complete, "reason": reason, "pages_read": pages,
                     "entries_examined": examined, "max_pages": max_pages,
                     "oldest_timestamp": _utc(oldest) if oldest else None,
                     "source_exhausted": exhausted, "next_start": pages * PAGE_SIZE},
        "warnings": warnings,
    }
    if unknown:
        result["unresolved_filings"] = unknown
    if request_error and pages == 0:
        result["error"] = f"SEC feed request failed: {request_error}"
    if use_state and complete:
        # Timestamp means scanned through, not newest matching filing. Persisting
        # quiet polls prevents gaps when the polling interval exceeds lookback.
        # Poll-start time also leaves entries published while paging for next time.
        ids = {_identity(row) for row in output if _timestamp(row["acceptance_datetime"]) == current}
        if cursor_time == current:
            ids.update(seen_boundary)
        # Preserve boundary IDs from older cursor versions when moving beyond
        # their timestamp, otherwise the overlap would replay acknowledged rows.
        prior_boundary = {identity: _utc(cursor_time) for identity in seen_boundary} if cursor_time else {}
        recent = {**prior_boundary, **recent_seen,
                  **{_identity(row): row["acceptance_datetime"] for row in output}}
        recent = {identity: stamp for identity, stamp in recent.items() if _timestamp(stamp) >= lookback}
        result["cursor_candidate"] = {"scope": scope, "timestamp": _utc(current), "seen": sorted(ids),
                                      "recent_seen": recent, "retention_from": _utc(lookback),
                                      "watermark_basis": "poll_started_at"}
    return result


def ack_feed(result: dict, state_store: StateStore | None = None) -> bool:
    """Persist a completed poll's candidate only after successful output delivery."""
    candidate = result.get("cursor_candidate")
    if not candidate or not result.get("coverage", {}).get("complete") or result.get("error"):
        return False
    if candidate.get("scope") != result.get("state_scope"):
        raise ValueError("Feed cursor scope does not match its result")
    store = state_store or StateStore()
    return store.update_feed_cursor(candidate["scope"], candidate["timestamp"], candidate["seen"],
                                    recent_seen=candidate.get("recent_seen"),
                                    retention_from=candidate.get("retention_from"))


def reset_feed_cursor(forms=None, state_store: StateStore | None = None) -> dict:
    """Explicitly discard one form scope's continuity before a fresh lookback.

    Callers must surface the warning: resetting does not recover filings that
    have already fallen outside the requested lookback or SEC's finite feed.
    """
    scope = _scope(_forms(forms))
    store = state_store or StateStore()
    removed = store.reset_feed_cursor(scope)
    return {"state_scope": scope, "cursor_reset": removed,
            "warning": {"code": "feed_cursor_reset", "state_scope": scope,
                        "message": "Feed continuity was explicitly reset for this form scope. The next poll uses its requested lookback; older undelivered filings may be missed."}}
