"""Form 4 (insider transaction) parsing and aggregation.

Form 4 primary documents are XML with a stable schema. Each transaction
carries a code (`P`/`S`/`A`/`M`/`G`/`D`/`F`/`C`/`I`/`J`/`X`), share count,
price per share, and an indicator of whether the security is derivative.

Aggregation groups by (filer CIK, reporting-owner CIK, transaction code) and
sums shares + dollar value over a time window.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

# https://www.sec.gov/about/forms/form4data.pdf
TRANSACTION_CODES: dict[str, dict] = {
    "P": {"name": "Open-market or private purchase", "direction": "acquire", "category": "purchase"},
    "S": {"name": "Open-market or private sale", "direction": "dispose", "category": "sale"},
    "A": {"name": "Grant, award, or other acquisition", "direction": "acquire", "category": "award"},
    "D": {"name": "Sale or transfer to issuer", "direction": "dispose", "category": "transfer"},
    "F": {"name": "Payment of exercise price or tax via shares", "direction": "dispose", "category": "tax"},
    "M": {"name": "Exercise/conversion of derivative", "direction": "acquire", "category": "exercise"},
    "G": {"name": "Bona fide gift", "direction": "transfer", "category": "gift"},
    "C": {"name": "Conversion of derivative", "direction": "acquire", "category": "exercise"},
    "I": {"name": "Discretionary transaction", "direction": "either", "category": "other"},
    "J": {"name": "Other acquisition or disposition", "direction": "either", "category": "other"},
    "X": {"name": "Exercise of in-the-money or at-the-money derivative", "direction": "acquire", "category": "exercise"},
}


def parse_form4_xml(xml_bytes: bytes | str) -> dict:
    """Parse a Form 4 XML primary document into a structured dict.

    Returns the issuer, reporting owner identity/role, and a list of
    non-derivative + derivative transactions.
    """
    if isinstance(xml_bytes, bytes):
        try:
            text = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = xml_bytes.decode("utf-8", errors="ignore")
    else:
        text = xml_bytes
    # Strip xml declaration if it has odd encoding to keep ElementTree happy.
    text = re.sub(r"<\?xml[^>]*\?>", "", text, count=1).strip()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {"error": f"Form 4 XML parse error: {exc}"}

    def _text(el, path):
        node = el.find(path) if el is not None else None
        if node is None:
            return ""
        # Form 4 wraps values in <value>...</value> children.
        v = node.find("value")
        if v is not None and v.text:
            return v.text.strip()
        return (node.text or "").strip()

    issuer = root.find("issuer")
    owner = root.find("reportingOwner")
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None

    out = {
        "schemaVersion": _text(root, "schemaVersion") or root.get("schemaVersion", ""),
        "documentType": _text(root, "documentType"),
        "periodOfReport": _text(root, "periodOfReport"),
        "issuer": {
            "cik": _text(issuer, "issuerCik").lstrip("0").zfill(10) if issuer is not None else "",
            "name": _text(issuer, "issuerName") if issuer is not None else "",
            "ticker": _text(issuer, "issuerTradingSymbol") if issuer is not None else "",
        },
        "reporting_owner": {
            "cik": _text(owner.find("reportingOwnerId"), "rptOwnerCik").lstrip("0").zfill(10)
                   if owner is not None and owner.find("reportingOwnerId") is not None else "",
            "name": _text(owner.find("reportingOwnerId"), "rptOwnerName")
                    if owner is not None and owner.find("reportingOwnerId") is not None else "",
            "is_director": _text(rel, "isDirector") in ("1", "true") if rel is not None else False,
            "is_officer": _text(rel, "isOfficer") in ("1", "true") if rel is not None else False,
            "is_ten_percent_owner": _text(rel, "isTenPercentOwner") in ("1", "true") if rel is not None else False,
            "officer_title": _text(rel, "officerTitle") if rel is not None else "",
        },
        "non_derivative_transactions": [],
        "derivative_transactions": [],
    }

    table = root.find("nonDerivativeTable")
    if table is not None:
        for tx in table.findall("nonDerivativeTransaction"):
            out["non_derivative_transactions"].append(_parse_transaction(tx, derivative=False))

    dtable = root.find("derivativeTable")
    if dtable is not None:
        for tx in dtable.findall("derivativeTransaction"):
            out["derivative_transactions"].append(_parse_transaction(tx, derivative=True))

    return out


def _parse_transaction(tx, derivative: bool = False) -> dict:
    def _v(el, path):
        node = el.find(path)
        if node is None:
            return None
        v = node.find("value")
        if v is not None and v.text is not None:
            return v.text.strip()
        return (node.text or "").strip() if node.text else None

    coding = tx.find("transactionCoding")
    amounts = tx.find("transactionAmounts")
    post = tx.find("postTransactionAmounts")

    code = _v(coding, "transactionCode") if coding is not None else ""
    shares_raw = _v(amounts, "transactionShares") if amounts is not None else None
    price_raw = _v(amounts, "transactionPricePerShare") if amounts is not None else None
    acq_disp = _v(amounts, "transactionAcquiredDisposedCode") if amounts is not None else ""
    shares_after_raw = _v(post, "sharesOwnedFollowingTransaction") if post is not None else None

    def _f(s):
        try:
            return float(s) if s not in (None, "") else None
        except (ValueError, TypeError):
            return None

    shares = _f(shares_raw)
    price = _f(price_raw)
    value = (shares * price) if (shares is not None and price is not None) else None

    return {
        "security_title": _v(tx, "securityTitle"),
        "transaction_date": _v(tx, "transactionDate"),
        "code": code,
        "code_meaning": TRANSACTION_CODES.get(code, {}).get("name", "Unknown"),
        "category": TRANSACTION_CODES.get(code, {}).get("category", "other"),
        "direction": "acquire" if acq_disp == "A" else ("dispose" if acq_disp == "D" else "either"),
        "shares": shares,
        "price_per_share": price,
        "transaction_value": value,
        "shares_after": _f(shares_after_raw),
        "is_derivative": derivative,
    }


def aggregate(transactions: list[dict]) -> dict:
    """Aggregate transactions by (insider name, code).

    Returns per-insider rollups plus an overall summary.
    """
    by_owner: dict[str, dict] = {}
    overall = {"acquired_shares": 0.0, "disposed_shares": 0.0,
               "acquired_value": 0.0, "disposed_value": 0.0,
               "transaction_count": 0}
    for tx in transactions:
        owner = tx.get("owner_name") or tx.get("reporting_owner", {}).get("name", "Unknown")
        bucket = by_owner.setdefault(owner, {
            "name": owner,
            "title": tx.get("officer_title", ""),
            "is_director": tx.get("is_director", False),
            "is_officer": tx.get("is_officer", False),
            "transactions": 0,
            "acquired_shares": 0.0,
            "disposed_shares": 0.0,
            "acquired_value": 0.0,
            "disposed_value": 0.0,
            "by_code": {},
        })
        bucket["transactions"] += 1
        overall["transaction_count"] += 1
        shares = tx.get("shares") or 0
        value = tx.get("transaction_value") or 0
        code = tx.get("code", "")
        code_bucket = bucket["by_code"].setdefault(code, {
            "code": code, "meaning": tx.get("code_meaning", ""),
            "shares": 0.0, "value": 0.0, "transactions": 0,
        })
        code_bucket["shares"] += shares
        code_bucket["value"] += value
        code_bucket["transactions"] += 1
        if tx.get("direction") == "acquire":
            bucket["acquired_shares"] += shares
            bucket["acquired_value"] += value
            overall["acquired_shares"] += shares
            overall["acquired_value"] += value
        elif tx.get("direction") == "dispose":
            bucket["disposed_shares"] += shares
            bucket["disposed_value"] += value
            overall["disposed_shares"] += shares
            overall["disposed_value"] += value

    insiders = list(by_owner.values())
    insiders.sort(key=lambda i: abs(i["acquired_value"] - i["disposed_value"]), reverse=True)
    overall["net_shares"] = overall["acquired_shares"] - overall["disposed_shares"]
    overall["net_value"] = overall["acquired_value"] - overall["disposed_value"]
    return {"insiders": insiders, "summary": overall}
