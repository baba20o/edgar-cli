"""Availability cutoffs: SEC acceptance time, never a document's dateline."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from functools import wraps
import inspect
import re


@dataclass(frozen=True)
class Cutoff:
    instant: datetime
    normalized: str
    date_only: bool

    @property
    def day(self):
        return self.instant.date().isoformat()


def parse_cutoff(value: str) -> Cutoff:
    if not isinstance(value, str):
        raise ValueError("as-of must be a calendar date or timezone-aware ISO timestamp")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        day = datetime.strptime(value, "%Y-%m-%d").date()
        return Cutoff(datetime.combine(day, time.max, timezone.utc), value, True)
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("as-of must be YYYY-MM-DD or a timezone-aware ISO timestamp") from exc
    if "T" not in value or instant.tzinfo is None:
        raise ValueError("Intraday as-of requires a timezone offset or Z")
    instant = instant.astimezone(timezone.utc)
    return Cutoff(instant, instant.isoformat().replace("+00:00", "Z"), False)


def acceptance_time(value):
    if not value:
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", str(value)):
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # SEC submissions' older unqualified acceptance strings are UTC.
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except ValueError:
        return None


def available(row, cutoff: Cutoff, acceptance=None):
    stamp = acceptance_time(acceptance or row.get("acceptanceDateTime") or row.get("acceptance_datetime"))
    if stamp is not None:
        return stamp <= cutoff.instant
    filed = row.get("filingDate") or row.get("filed") or ""
    if not isinstance(filed, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", filed):
        return False
    try:
        datetime.strptime(filed, "%Y-%m-%d")
    except ValueError:
        return False
    # A filing date may precede SEC acceptance after midnight UTC. Without a
    # timestamp, wait one full UTC calendar day beyond the filing date.
    if cutoff.date_only:
        return filed <= cutoff.day
    previous_day = (cutoff.instant.date() - timedelta(days=1)).isoformat()
    return filed < previous_day


def column_rows(columns):
    count = len(columns.get("accessionNumber", []))
    return [{key: values[i] if i < len(values) else ""
             for key, values in columns.items() if isinstance(values, list)} for i in range(count)]


def rows_columns(rows):
    keys = dict.fromkeys(key for row in rows for key in row)
    return {key: [row.get(key, "") for row in rows] for key in keys}


def raw_submissions(client, cik, cutoff):
    """Load history explicitly for replay; do not silently clip to recent filings."""
    key = (cik, cutoff.normalized)
    if key in client._pit_submissions:
        return client._pit_submissions[key]
    raw = client._get_uncut(f"/submissions/CIK{cik}.json")
    if "error" in raw:
        return raw
    result = deepcopy(raw)
    rows = column_rows(raw.get("filings", {}).get("recent", {}))
    seen = {r.get("accessionNumber") for r in rows}
    for info in raw.get("filings", {}).get("files", []):
        # Filing dates may follow acceptance dates, including after weekends.
        # Chunk metadata cannot safely bound intraday availability.
        if not info.get("name"):
            continue
        chunk = client._get_uncut("/submissions/" + info["name"])
        if "error" in chunk:
            result["_historical_coverage_incomplete"] = True
            client._pit_warnings.get().append({"code": "historical_chunk_unavailable",
                "message": "Historical replay coverage is incomplete.", "file": info["name"]})
            continue
        for row in column_rows(chunk):
            if row.get("accessionNumber") not in seen:
                seen.add(row.get("accessionNumber")); rows.append(row)
    rows.sort(key=lambda r: (r.get("acceptanceDateTime") or r.get("filingDate", ""),
                             r.get("accessionNumber", "")), reverse=True)
    result.setdefault("filings", {})["recent"] = rows_columns(rows)
    result["filings"]["files"] = []
    client._pit_submissions[key] = result
    return result


def filter_payload(client, path, payload, cutoff):
    if not isinstance(payload, dict) or "error" in payload:
        return payload
    cik_match = re.search(r"CIK(\d{10})", path)
    cik = cik_match.group(1) if cik_match else None
    if "/submissions/" in path:
        if "filings" in payload and cik:
            result = deepcopy(raw_submissions(client, cik, cutoff))
            if "error" in result:
                return result
            columns = result.get("filings", {}).get("recent", {})
            result["filings"]["recent"] = rows_columns([r for r in column_rows(columns) if available(r, cutoff)])
            return result
        if "accessionNumber" in payload:
            return rows_columns([r for r in column_rows(payload) if available(r, cutoff)])
    if "/api/xbrl/" not in path or not cik:
        return payload
    raw = raw_submissions(client, cik, cutoff)
    if "error" in raw:
        return {"error": "Cannot establish filing availability: " + str(raw["error"]),
                "code": "acceptance_metadata_unavailable"}
    times = {r.get("accessionNumber"): r.get("acceptanceDateTime")
             for r in column_rows(raw.get("filings", {}).get("recent", {}))}
    result = deepcopy(payload)
    concepts = [result] if "units" in result else [concept for tags in result.get("facts", {}).values() for concept in tags.values()]
    for concept in concepts:
        for unit, rows in concept.get("units", {}).items():
            kept = []
            for row in rows:
                accepted = times.get(row.get("accn"))
                if (not cutoff.date_only and raw.get("_historical_coverage_incomplete")
                        and row.get("accn") not in times):
                    continue
                if available(row, cutoff, accepted):
                    item = dict(row)
                    stamp = acceptance_time(accepted)
                    if stamp is not None:
                        item["acceptance_datetime"] = stamp.isoformat().replace("+00:00", "Z")
                    kept.append(item)
            concept["units"][unit] = kept
    return result


def point_in_time(fn):
    """Scope nested reads to a cutoff; cache entries always retain raw SEC data."""
    signature = inspect.signature(fn)
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        requested = bound.arguments.get("as_of")
        cutoff_var = getattr(self, "_pit_cutoff", None)
        inherited = cutoff_var.get() if cutoff_var is not None else None
        cutoff = inherited or (parse_cutoff(requested) if requested else None)
        if cutoff is None:
            return fn(self, *args, **kwargs)
        if requested and inherited and parse_cutoff(requested).instant < inherited.instant:
            cutoff = parse_cutoff(requested)
        if "as_of" in signature.parameters:
            bound.arguments["as_of"] = cutoff.day
        if cutoff_var is None:
            from contextvars import ContextVar
            self._pit_cutoff = ContextVar("edgar_cutoff", default=None)
            self._pit_warnings = ContextVar("edgar_cutoff_warnings", default=None)
            self._pit_submissions = {}
        token = self._pit_cutoff.set(cutoff)
        warning_token = self._pit_warnings.set([]) if inherited is None else None
        old_grid = getattr(self, "_fiscal_grids", {})
        if inherited is None:
            self._pit_submissions = {}
            self._fiscal_grids = {}
        try:
            result = fn(*bound.args, **bound.kwargs)
            if isinstance(result, dict):
                result = dict(result)
                result["as_of"] = cutoff.normalized
                result["point_in_time"] = {
                    "cutoff_utc": cutoff.instant.isoformat().replace("+00:00", "Z"),
                    "filing_availability": "acceptance_datetime; missing intraday acceptance requires a filing date earlier than the previous UTC day",
                    "unknown_acceptance_caveat": "Calendar-date fallback is conservative availability evidence, not a reconstructed acceptance timestamp. Date-only replay uses the filing date when acceptance is unknown.",
                    "entity_metadata": "Current SEC entity/ticker metadata; historical entity attributes are not reconstructed.",
                }
                notes = self._pit_warnings.get()
                if notes:
                    result["warnings"] = result.get("warnings", []) + notes
            return result
        finally:
            self._pit_cutoff.reset(token)
            if warning_token is not None:
                self._pit_warnings.reset(warning_token)
                self._fiscal_grids = old_grid
    return wrapped
