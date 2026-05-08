"""13F-HR (institutional holdings) parsing and aggregation.

13F filings have two related XML documents:
- A primary cover doc (form13fInfoTable submission summary).
- An `infotable.xml` (or `*infotable.xml`) holding the actual line items, one
  row per security.

Each holding row carries: nameOfIssuer, titleOfClass, cusip, sshPrnamt
(share count), value (in thousands of USD per SEC schema), putCall,
investmentDiscretion, sole/shared/none voting authorities.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional


def _strip_ns(tag: str) -> str:
    """Drop the XML namespace prefix from a tag name."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text(el, name: str) -> str:
    """Find a child element by local name (namespace-agnostic) and return its text."""
    if el is None:
        return ""
    for child in el:
        if _strip_ns(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _find(el, name: str):
    if el is None:
        return None
    for child in el:
        if _strip_ns(child.tag) == name:
            return child
    return None


def parse_infotable_xml(xml_bytes: bytes | str) -> list[dict]:
    """Parse a 13F infoTable XML into a list of holding rows.

    Returns one row per security held. `value_usd` converts SEC's reported
    thousands-of-dollars convention to absolute dollars for downstream math.
    """
    if isinstance(xml_bytes, bytes):
        try:
            text = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = xml_bytes.decode("utf-8", errors="ignore")
    else:
        text = xml_bytes
    text = re.sub(r"<\?xml[^>]*\?>", "", text, count=1).strip()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    rows: list[dict] = []
    # Newer schemas use <informationTable><infoTable>...; older variants vary.
    iter_targets = []
    if _strip_ns(root.tag) == "infoTable":
        iter_targets = [root]
    else:
        # Walk children for any infoTable elements.
        iter_targets = [el for el in root.iter() if _strip_ns(el.tag) == "infoTable"]

    for el in iter_targets:
        shrs_or_prn = _find(el, "shrsOrPrnAmt")
        voting = _find(el, "votingAuthority")
        try:
            raw_value = int(_text(el, "value") or 0)
        except ValueError:
            raw_value = 0
        try:
            shares = int(_text(shrs_or_prn, "sshPrnamt") or 0) if shrs_or_prn is not None else 0
        except ValueError:
            shares = 0
        # SEC's 2022 13F amendment (effective Jan 2023) changed `value` reporting
        # from thousands of USD to absolute USD. There is no schema-level version
        # marker we can use, so rely on a magnitude heuristic: real holdings under
        # $1M total are vanishingly rare for 13F filers, and any filing whose
        # combined value is below $1B was almost certainly reporting in
        # thousands. We apply the multiplier per-fact only when the implied
        # market value of the share count would otherwise be implausibly low.
        rows.append({
            "name_of_issuer": _text(el, "nameOfIssuer"),
            "title_of_class": _text(el, "titleOfClass"),
            "cusip": _text(el, "cusip"),
            "value_raw": raw_value,
            "shares": shares,
            "shares_kind": (_text(shrs_or_prn, "sshPrnamtType") if shrs_or_prn is not None else "SH"),
            "put_call": _text(el, "putCall"),
            "investment_discretion": _text(el, "investmentDiscretion"),
            "sole_voting": int(_text(voting, "Sole") or 0) if voting is not None else 0,
            "shared_voting": int(_text(voting, "Shared") or 0) if voting is not None else 0,
            "no_voting": int(_text(voting, "None") or 0) if voting is not None else 0,
        })

    # Decide value units per filing using the cumulative magnitude. Post-2023
    # filings report in absolute USD; pre-2023 in thousands. A filer whose
    # entire reported "value" sums to <$10B is almost certainly using the old
    # thousands convention (nearly all 13F filers exceed $100M AUM and most
    # exceed $1B).
    total_raw = sum(r["value_raw"] for r in rows)
    in_thousands = total_raw < 10_000_000_000  # < $10B implies thousands convention
    multiplier = 1000 if in_thousands else 1
    for r in rows:
        r["value_usd"] = r.pop("value_raw") * multiplier
        r["value_unit_convention"] = "thousands" if in_thousands else "absolute"
    return rows


def aggregate_filer_holdings(rows: list[dict], top_n: int = 50) -> dict:
    """Summarize a single filer's holdings: total value, top-N positions, concentration."""
    if not rows:
        return {"total_value_usd": 0, "position_count": 0, "top_positions": [],
                "top_concentration": 0.0}
    total = sum(r.get("value_usd", 0) for r in rows)
    rows_sorted = sorted(rows, key=lambda r: r.get("value_usd", 0), reverse=True)
    top = rows_sorted[:top_n]
    top_value = sum(r.get("value_usd", 0) for r in top)
    return {
        "total_value_usd": total,
        "position_count": len(rows),
        "top_positions": [
            {
                "name_of_issuer": r["name_of_issuer"],
                "title_of_class": r.get("title_of_class", ""),
                "cusip": r.get("cusip", ""),
                "value_usd": r.get("value_usd", 0),
                "shares": r.get("shares", 0),
                "weight": (r.get("value_usd", 0) / total) if total else 0,
                "put_call": r.get("put_call", ""),
            }
            for r in top
        ],
        "top_concentration": (top_value / total) if total else 0.0,
    }


def aggregate_holders(per_filer_rows: list[dict]) -> dict:
    """Aggregate holdings of a single security across many filers.

    Each row in `per_filer_rows` is a single holding entry tagged with the
    filer's CIK and name. Returns total-shares + total-value across the
    candidate filer set, plus per-filer top-list sorted by value.
    """
    if not per_filer_rows:
        return {"total_shares": 0, "total_value_usd": 0, "filer_count": 0,
                "filers": []}
    total_shares = sum(r.get("shares", 0) for r in per_filer_rows)
    total_value = sum(r.get("value_usd", 0) for r in per_filer_rows)
    by_filer: dict[str, dict] = {}
    for r in per_filer_rows:
        cik = r.get("filer_cik", "")
        bucket = by_filer.setdefault(cik, {
            "filer_cik": cik,
            "filer_name": r.get("filer_name", ""),
            "shares": 0,
            "value_usd": 0,
            "positions": 0,
            "put_call_split": {"shares": 0, "calls": 0, "puts": 0},
        })
        bucket["shares"] += r.get("shares", 0)
        bucket["value_usd"] += r.get("value_usd", 0)
        bucket["positions"] += 1
        pc = (r.get("put_call") or "").lower()
        if pc == "call":
            bucket["put_call_split"]["calls"] += r.get("shares", 0)
        elif pc == "put":
            bucket["put_call_split"]["puts"] += r.get("shares", 0)
        else:
            bucket["put_call_split"]["shares"] += r.get("shares", 0)
    filers = sorted(by_filer.values(), key=lambda b: b["value_usd"], reverse=True)
    return {
        "total_shares": total_shares,
        "total_value_usd": total_value,
        "filer_count": len(filers),
        "filers": filers,
    }
