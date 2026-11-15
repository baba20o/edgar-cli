"""Pure, conservative financing scenarios with inspectable input provenance.

These functions consume ``EdgarClient.dilution`` / ``metrics`` dictionaries;
they never fetch prices, reconcile share classes, or determine legal eligibility.
Each numeric output uses compute.py's metric envelope. Missing or rejected
inputs remain distinguishable from reported zeroes.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any


_DAYS_PER_MONTH = 30.4375
_FLOAT_THRESHOLD = 75_000_000
_BABY_TOPICS = {"baby_shelf_restriction", "mvphs_float", "prior_12m_sales",
                "remaining_eligible_capacity"}
_MONTH_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December) \d{1,2}, \d{4}\b", re.I)
_SCENARIO_CAVEAT = (
    "Arithmetic scenario for the one-third limitation only. Other eligibility "
    "requirements, unused registered capacity, intervening sales, and concurrent "
    "offerings can further limit sales; this is not a legal eligibility determination.")
_MANUAL_CAVEAT = (
    "Caller-supplied inputs are an explicit scenario, not verified market data or "
    "a qualifying 60-day measurement. Filing share counts are not updated or "
    "adjusted for subsequent issuances, affiliate changes, or splits.")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _dates(text: str) -> list[str]:
    found = []
    for match in _MONTH_DATE.finditer(text):
        try:
            found.append(datetime.strptime(match.group(), "%B %d, %Y").date().isoformat())
        except ValueError:
            continue
    return found


def _missing(label: str) -> dict:
    return {"label": label, "val": None, "missing": True}


def _record(label: str, value: Any, source: dict, unit: str,
            *, as_of: str | None = None, origin: str = "filing") -> dict:
    record = {
        "label": label, "val": _number(value), "unit": unit,
        "tag": source.get("tag") or source.get("source_tag", ""),
        "source_url": source.get("source_url") or source.get("filing_url", ""),
        "as_of": (source.get("as_of") or source.get("end", "")) if as_of is None else as_of,
        "start": source.get("start", ""), "end": source.get("end", ""),
        "accession": source.get("accession") or source.get("accn", ""),
        "filed": source.get("filed", ""),
        "file_number": source.get("file_number", ""), "origin": origin,
    }
    for key in ("sentence", "text", "acceptance_datetime", "fiscal_period", "calendar_period"):
        if source.get(key):
            record[key] = source[key]
    if record["val"] is None:
        record["rejected_reason"] = f"{label} is not a finite numeric value."
    return record


def _valid(record: dict) -> bool:
    return record.get("val") is not None and not record.get("rejected_reason")


def _nonnegative(record: dict, positive: bool = False) -> dict:
    value = record.get("val")
    if value is not None and (value < 0 or (positive and value == 0)):
        record["rejected_reason"] = (
            f"{record['label']} must be {'positive' if positive else 'nonnegative'}.")
    return record


def _usd(record: dict, expected: str = "USD") -> dict:
    if record.get("val") is not None and record.get("unit") != expected:
        record["rejected_reason"] = (
            f"{record['label']} has unit {record.get('unit') or 'unknown'}; "
            f"{expected} is required. No currency conversion is inferred.")
    return record


def _envelope(metric: str, value: float | None, formula: str,
              inputs: list[dict], unit: str, caveats: list[str] | None = None,
              missing: list[str] | None = None) -> dict:
    absent = list(dict.fromkeys((missing or []) + [i["label"] for i in inputs if not _valid(i)]))
    notes = list(caveats or [])
    notes.extend(i["rejected_reason"] for i in inputs if i.get("rejected_reason"))
    if value is not None and _number(value) is None:
        value = None
        notes.append("The calculation exceeds the finite numeric range.")
    result = {"metric": metric, "value": None if absent else value,
              "unit": unit, "formula": formula,
              "inputs": [dict(i) for i in inputs if not i.get("missing")],
              "caveats": list(dict.fromkeys(notes))}
    if absent:
        result["missing_inputs"] = absent
    return result


def _warn(warnings: list[dict], code: str, message: str) -> None:
    warning = {"code": code, "message": message}
    if warning not in warnings:
        warnings.append(warning)


def _filing_key(row: dict) -> tuple[str, str]:
    return row.get("file_number", ""), row.get("accession") or row.get("source_url", "")


def _latest_baby_series(data: dict, warnings: list[dict]) -> tuple[list[dict], dict]:
    """Select one filing and one SEC file number; never fill from older series."""
    candidates = list(data.get("shelf_capacity", []))
    for shelf in data.get("shelf_registrations", []):
        for statement in shelf.get("capacity", []):
            candidates.append({"file_number": shelf.get("file_number", ""),
                               "currency": shelf.get("currency", ""), **statement})
    groups: dict[tuple[str, str], list[dict]] = {}
    seen = set()
    for row in candidates:
        if row.get("subtopic") not in _BABY_TOPICS or row.get("part") == "base_prospectus":
            continue
        key = _filing_key(row)
        if not key[1]:
            _warn(warnings, "missing_filing_identity",
                  "A capacity statement has no accession or source URL; it cannot be combined with other evidence.")
            continue
        dedupe = key + (row.get("subtopic"), row.get("sentence"), repr(row.get("figures")))
        if dedupe not in seen:
            seen.add(dedupe)
            groups.setdefault(key, []).append(row)
    if not groups:
        return [], {}

    def filing_time(rows: list[dict]) -> str:
        return max((r.get("acceptance_datetime") or r.get("filed", "") for r in rows), default="")

    newest = max(filing_time(rows) for rows in groups.values())
    finalists = [(key, rows) for key, rows in groups.items() if filing_time(rows) == newest]
    if len(finalists) != 1:
        _warn(warnings, "ambiguous_latest_shelf",
              "Multiple shelf series share the latest available filing time; no series was selected.")
        return [], {}
    key, rows = finalists[0]
    first = max(rows, key=lambda r: r.get("acceptance_datetime") or r.get("filed", ""))
    selected = {field: first.get(field, "") for field in (
        "accession", "filed", "acceptance_datetime", "source_url", "form")}
    selected["file_number"] = key[0]
    if not key[0]:
        _warn(warnings, "missing_file_number",
              "The selected disclosure lacks an SEC file number; evidence is limited to this single filing.")
    if len(groups) > 1:
        _warn(warnings, "single_filing_scope",
              "Only the latest baby-shelf disclosure series is used. Other filings and registration file numbers are not combined.")
    return rows, selected


def _currency(figure: dict, row: dict) -> str:
    explicit = figure.get("unit") or figure.get("currency") or row.get("currency")
    if explicit:
        return explicit
    text = figure.get("text", "")
    sentence = row.get("sentence", "")
    offset = sentence.find(text) if text else -1
    if offset >= 0:
        text = sentence[max(0, offset - 5):offset] + text
    if re.search(r"\bSGD\s*\$|\bS\$", text, re.I):
        return "SGD"
    if re.search(r"\b(?:C|CAD)\s*\$", text, re.I):
        return "CAD"
    if re.search(r"\b(?:A|AUD)\s*\$", text, re.I):
        return "AUD"
    return "USD"


def _disclosed(rows: list[dict], subtopic: str, kind: str, label: str,
               warnings: list[dict]) -> dict:
    selected = [r for r in rows if r.get("subtopic") == subtopic]
    # Multiple float statements may have different dates or share classes even
    # when they report the same value. Do not splice their components together.
    if len(selected) > 1:
        _warn(warnings, "ambiguous_disclosure",
              f"Multiple {subtopic} statements occur in the selected filing; {label} is not selected.")
        return _missing(label)
    if not selected:
        return _missing(label)
    row = selected[0]
    figures = [f for f in row.get("figures", []) if f.get("type") == kind]
    if len(figures) != 1:
        if figures:
            _warn(warnings, "ambiguous_figure",
                  f"The selected {subtopic} statement has multiple {kind} figures; {label} is not selected.")
        return _missing(label)
    figure = figures[0]
    unit = "shares" if kind == "shares" else _currency(figure, row)
    if kind == "price_per_share" and not unit.endswith("/shares"):
        unit += "/shares"
    sentence = row.get("sentence", "")
    after_figure = sentence.split(figure.get("text", ""), 1)[-1] if figure.get("text") else ""
    if kind == "shares":
        # The reference price date can differ legitimately from the share date.
        after_figure = re.split(r"\bat a price\b|\bat \$", after_figure, maxsplit=1, flags=re.I)[0]
    dates = _dates(after_figure) if kind in ("shares", "price_per_share") else []
    as_of = (dates[0] if dates else row.get("as_of", ""))
    if subtopic != "mvphs_float":
        as_of = as_of or row.get("filed", "")
    result = _record(label, figure.get("value"), {**row, "text": figure.get("text", "")},
                     unit, as_of=as_of)
    if kind != "shares":
        _usd(result, "USD/shares" if kind == "price_per_share" else "USD")
    return _nonnegative(result, positive=kind in ("shares", "price_per_share"))


def compute_baby_shelf(dilution_data: dict, public_float_shares=None,
                       price=None, high_60d_close=None) -> dict:
    """Return disclosed values and one-third-limit arithmetic, never eligibility.

    Inputs come from one latest filing/file-number series of shelf ``capacity``
    statements. An explicit override selects the shares-times-price scenario;
    ``high_60d_close`` takes precedence when both price arguments are supplied.
    Without overrides, the filing-stated MVPHS takes precedence, with the
    independent product and any disagreement preserved separately.
    """
    warnings: list[dict] = []
    rows, selected = _latest_baby_series(dilution_data, warnings)
    stated = _disclosed(rows, "mvphs_float", "dollars", "stated_MVPHS", warnings)
    shares = _disclosed(rows, "mvphs_float", "shares", "non_affiliate_float_shares", warnings)
    reference = _disclosed(rows, "mvphs_float", "price_per_share", "reference_price", warnings)
    sales = _disclosed(rows, "prior_12m_sales", "dollars", "prior_12m_ib6_sales_usd", warnings)
    remaining = _disclosed(rows, "remaining_eligible_capacity", "dollars",
                            "stated_remaining_capacity_usd", warnings)
    disclosed_product = _number(shares["val"] * reference["val"]) if _valid(shares) and _valid(reference) else None
    if (_valid(stated) and disclosed_product is not None
            and not math.isclose(stated["val"], disclosed_product, rel_tol=1e-6, abs_tol=1)):
        _warn(warnings, "float_arithmetic_disagreement",
              f"Filing-stated MVPHS ({stated['val']:,.2f}) differs from its stated shares times price "
              f"({disclosed_product:,.2f}). Neither disclosure has been repaired.")

    scenario = any(v is not None for v in (public_float_shares, price, high_60d_close))
    if public_float_shares is not None:
        shares = _nonnegative(_record("non_affiliate_float_shares", public_float_shares,
                                     {}, "shares", origin="caller"), positive=True)
    if high_60d_close is not None or price is not None:
        chosen = high_60d_close if high_60d_close is not None else price
        reference = _nonnegative(_record("reference_price", chosen, {}, "USD/shares", origin="caller"), positive=True)
        reference["argument"] = "high_60d_close" if high_60d_close is not None else "price"
        if high_60d_close is not None and price is not None:
            _warn(warnings, "price_precedence", "Both price inputs were supplied; high_60d_close is used for the scenario.")
    if scenario:
        _warn(warnings, "manual_scenario", _MANUAL_CAVEAT)
    notes = [_SCENARIO_CAVEAT] + ([_MANUAL_CAVEAT] if scenario else [])
    if (shares.get("as_of") and reference.get("as_of")
            and shares["as_of"] != reference["as_of"]):
        notes.append(f"Float shares are dated {shares['as_of']}; the reference price is dated {reference['as_of']}. "
                     "The filing's measurement dates are preserved without a current-price inference.")
    if any(w["code"] == "float_arithmetic_disagreement" for w in warnings):
        notes.append("The filing's stated MVPHS disagrees with its shares-times-price arithmetic; inspect both values.")
    product = _number(shares["val"] * reference["val"]) if _valid(shares) and _valid(reference) else None
    if product is None and _valid(shares) and _valid(reference):
        notes.append("Shares times reference price exceeds the finite numeric range.")
    use_stated = not scenario and _valid(stated)
    mvphs = stated["val"] if use_stated else product
    mvphs_inputs = [stated] if use_stated else [shares, reference]
    formula = "filing-stated MVPHS" if use_stated else "NonAffiliateFloatShares * ReferencePrice"
    cap = mvphs / 3 if mvphs is not None and mvphs < _FLOAT_THRESHOLD else None
    cap_notes = list(notes)
    if mvphs is not None and mvphs >= _FLOAT_THRESHOLD:
        cap_notes.append("MVPHS is at least $75 million in this scenario; the one-third formula is not applied. "
                         "This does not establish available shelf capacity or permanent eligibility.")
    usable = max(0, cap - sales["val"]) if cap is not None and _valid(sales) else None
    if (_valid(stated) and stated["val"] < _FLOAT_THRESHOLD and _valid(sales) and _valid(remaining)):
        stated_basis = max(0, stated["val"] / 3 - sales["val"])
        if not math.isclose(stated_basis, remaining["val"], rel_tol=1e-6, abs_tol=1):
            _warn(warnings, "capacity_arithmetic_disagreement",
                  f"Stated remaining capacity ({remaining['val']:,.2f}) differs from stated MVPHS / 3 "
                  f"minus stated prior sales ({stated_basis:,.2f}).")
            cap_notes.append("Stated remaining capacity disagrees with arithmetic based on stated MVPHS; "
                             "the stated amount is preserved separately.")
    escape = _FLOAT_THRESHOLD / shares["val"] if _valid(shares) else None
    return {
        "selected_filing": selected,
        "basis": "caller_scenario" if scenario else ("filing_stated" if use_stated else "filing_arithmetic"),
        "non_affiliate_float_shares": _envelope("non_affiliate_float_shares", shares["val"],
            "explicit non-affiliate share count", [shares], "shares", notes),
        "reference_price": _envelope("reference_price", reference["val"],
            "explicit reference price", [reference], "USD/shares", notes),
        "stated_MVPHS": _envelope("stated_MVPHS", stated["val"], "filing-stated MVPHS", [stated], "USD"),
        "calculated_MVPHS": _envelope("calculated_MVPHS", product,
            "NonAffiliateFloatShares * ReferencePrice", [shares, reference], "USD", notes),
        "MVPHS": _envelope("MVPHS", mvphs, formula, mvphs_inputs, "USD", notes),
        "prior_12m_ib6_sales_usd": _envelope("prior_12m_ib6_sales_usd", sales["val"],
            "filing-stated prior 12-month I.B.6 / I.B.5 sales", [sales], "USD"),
        "gross_12m_cap": _envelope("gross_12m_cap", cap,
            "MVPHS / 3 when MVPHS < 75,000,000", mvphs_inputs, "USD", cap_notes),
        "usable_capacity_usd": _envelope("usable_capacity_usd", usable,
            "max(0, gross_12m_cap - prior_12m_ib6_sales_usd)", mvphs_inputs + [sales], "USD", cap_notes),
        "stated_remaining_capacity_usd": _envelope("stated_remaining_capacity_usd", remaining["val"],
            "filing-stated remaining eligible capacity", [remaining], "USD", cap_notes),
        "escape_price_75m": _envelope("escape_price_75m", escape,
            "75,000,000 / NonAffiliateFloatShares", [shares], "USD/shares", notes + [
                "A price scenario for reaching $75 million, not a permanent unlock. Eligibility is "
                "reassessed and also depends on other applicable conditions."]),
        "warnings": warnings,
    }


def _cash_inputs(data: dict, warnings: list[dict]) -> dict[str, dict]:
    aliases = ("cash", "operating_cash_flow", "assets_current", "liabilities_current",
               "debt", "short_term_debt")
    chosen = {}
    for alias in aliases:
        candidates = [m for m in data.get("metrics", [])
                      if m.get("metric") == alias and isinstance(m.get("fact"), dict)]
        if not candidates:
            chosen[alias] = _missing(alias)
            continue
        latest = max(candidates, key=lambda m: (m["fact"].get("end", ""), m["fact"].get("filed", "")))
        fact = latest["fact"]
        record = _record(alias, fact.get("val"), {**latest, **fact},
                         fact.get("unit") or latest.get("unit", ""), origin="xbrl")
        if "period_length_days" in fact:
            record["period_length_days"] = _number(fact["period_length_days"])
        if latest.get("stale") or fact.get("stale"):
            record["rejected_reason"] = f"{alias} is stale: {latest.get('stale_reason') or fact.get('stale_reason') or 'flagged by source'}."
        same_period = [m["fact"] for m in candidates
                       if (m["fact"].get("end", ""), m["fact"].get("filed", ""))
                       == (fact.get("end", ""), fact.get("filed", ""))]
        if len({_number(f.get("val")) for f in same_period}) > 1:
            record["rejected_reason"] = f"{alias} has conflicting values for the latest period and filing date."
        if not _date(record["as_of"]):
            record["rejected_reason"] = f"{alias} lacks a valid period-end date."
        _usd(record)
        if alias != "operating_cash_flow":
            _nonnegative(record)
        chosen[alias] = record
    report = data.get("latest_periodic_report") or {}
    ends = [i["as_of"] for i in chosen.values() if _date(i.get("as_of")) and not i.get("rejected_reason")]
    if _date(report.get("report_date")):
        ends.append(report["report_date"])
    anchor = max(ends, default="")
    for record in chosen.values():
        if _valid(record) and record.get("as_of", "") < anchor:
            record["rejected_reason"] = (
                f"{record['label']} ends {record['as_of']}, before the snapshot period {anchor}; "
                "mismatched periods are not combined.")
            _warn(warnings, "period_mismatch", record["rejected_reason"])
    return chosen


def compute_cash_runway(metrics_data: dict) -> dict:
    """Return a historical cash/burn scenario from aligned, non-stale USD facts.

    No financing after the balance date is added, no missing debt is zeroed,
    and cash-generating periods produce null runway rather than infinity.
    Duration uses the API's elapsed-day convention (end minus start).
    """
    warnings: list[dict] = []
    records = _cash_inputs(metrics_data, warnings)
    cash, ocf, assets, liabilities, debt, short = (records[k] for k in (
        "cash", "operating_cash_flow", "assets_current", "liabilities_current", "debt", "short_term_debt"))
    notes = ["Historical scenario at the reported balance date, assuming the observed operating cash outflow "
             "continues. Subsequent financing, restricted-cash availability, and future spending are not estimated."]
    cash_notes = list(notes)
    if "RestrictedCash" in cash.get("tag", ""):
        cash_notes.append("The selected cash tag includes restricted cash; the full reported amount may not be available for operations.")
    start, end = _date(ocf.get("start")), _date(ocf.get("end"))
    actual_days = (end - start).days if start and end else None
    provided_days = ocf.get("period_length_days")
    days = provided_days if "period_length_days" in ocf else actual_days
    duration = _record("period_length_days", days, ocf, "days", origin="xbrl_period")
    _nonnegative(duration, positive=True)
    if actual_days is not None and provided_days is not None and actual_days != provided_days:
        duration["rejected_reason"] = (
            f"Reported duration {provided_days:g} days disagrees with end minus start ({actual_days} days).")
    if actual_days is not None and actual_days <= 0:
        duration["rejected_reason"] = "Operating cash flow requires a positive duration with start before end."
    if actual_days is None and days is not None:
        notes.append("Operating-cash-flow duration is supplied by the source; a complete start/end interval was not available to cross-check it.")
    burn = _number((-ocf["val"] / days) * _DAYS_PER_MONTH) if _valid(ocf) and _valid(duration) and ocf["val"] < 0 else None
    burn_notes = list(notes)
    if burn is None and _valid(ocf) and _valid(duration) and ocf["val"] < 0:
        burn_notes.append("The normalized operating burn exceeds the finite numeric range.")
    if _valid(ocf) and ocf["val"] >= 0:
        burn_notes.append("Operating cash flow is nonnegative; no operating cash burn or finite depletion runway is inferred.")
    months = cash["val"] / burn if _valid(cash) and burn is not None and burn > 0 else None
    runway_days = months * _DAYS_PER_MONTH if months is not None else None
    nwc = assets["val"] - liabilities["val"] if _valid(assets) and _valid(liabilities) else None
    debt_notes = notes + ["The sum covers the selected noncurrent and current debt aliases; "
                          "other borrowing categories may be absent from these tags."]
    tag = debt.get("tag", "").split(":")[-1]
    if tag in {"Borrowings", "LongTermDebt", "LongTermDebtCurrent", "DebtCurrent", "Liabilities"}:
        debt["rejected_reason"] = (
            f"{tag} is not an unambiguous noncurrent debt component; adding current debt could double-count or misclassify liabilities.")
    short_tag = short.get("tag", "").split(":")[-1]
    if short_tag in {"Borrowings", "Liabilities", "CurrentLiabilities", "LiabilitiesCurrent"}:
        short["rejected_reason"] = f"{short_tag} is not a current debt component; liabilities are not equated with debt."
    total_debt = debt["val"] + short["val"] if _valid(debt) and _valid(short) else None
    if not _valid(debt) or not _valid(short):
        debt_notes.append("Missing, stale, or incompatible debt components are unknown, not zero. Even valid alias components may omit other borrowing categories.")
    for record in [*records.values(), duration]:
        if record.get("rejected_reason"):
            _warn(warnings, "unusable_input", record["rejected_reason"])
    result = {
        "cash_usd": _envelope("cash_usd", cash["val"], "reported cash balance", [cash], "USD", cash_notes),
        "monthly_operating_burn_usd": _envelope("monthly_operating_burn_usd", burn,
            "-operating_cash_flow / (period_length_days / 30.4375), when operating_cash_flow < 0",
            [ocf, duration], "USD/month", burn_notes),
        "runway_months": _envelope("runway_months", months, "cash_usd / monthly_operating_burn_usd",
            [cash, ocf, duration], "months", cash_notes + burn_notes),
        "runway_days": _envelope("runway_days", runway_days, "runway_months * 30.4375",
            [cash, ocf, duration], "days", cash_notes + burn_notes),
        "net_working_capital_usd": _envelope("net_working_capital_usd", nwc,
            "assets_current - liabilities_current", [assets, liabilities], "USD", notes),
        "total_debt_usd": _envelope("total_debt_usd", total_debt,
            "noncurrent_debt + current_debt", [debt, short], "USD", debt_notes),
        "warnings": warnings,
    }
    if metrics_data.get("warnings"):
        result["source_warnings"] = list(metrics_data["warnings"])
    return result
