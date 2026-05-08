"""Derived-metric primitives for edgar-cli.

Every output is shaped as:

    {
      "metric": "...",
      "value": float | None,
      "formula": "human-readable expression",
      "inputs": [{"label": ..., "tag": ..., "val": ..., "source_url": ..., "as_of": ...}, ...],
      "caveats": [...],
      "missing_inputs": [...],   # only when value is None
    }

Without formula + provenance the CLI would hide the math from agents.
"""

from __future__ import annotations

import math
from typing import Any, Optional


def _value_or_none(fact: Optional[dict]) -> Optional[float]:
    if not fact:
        return None
    val = fact.get("val")
    try:
        return float(val) if val not in ("", None) else None
    except (TypeError, ValueError):
        return None


def _input_record(label: str, fact: Optional[dict], optional: bool = False) -> dict:
    """Return a provenance record.

    When `fact` is None and `optional=False`, mark `missing=True` so the
    envelope nulls the metric. When `optional=True`, the input is recorded as
    missing-but-okay; the formula treats it as zero (with a caveat). This
    matches metrics like `quick_ratio` where inventory is reasonably zero
    when a filer omits it.
    """
    if not fact:
        return {"label": label, "tag": "", "val": None, "unit": "",
                "source_url": "", "as_of": "", "fiscal_period": "",
                "calendar_period": "", "accession": "",
                "missing": True, "optional": optional}
    return {
        "label": label,
        "tag": fact.get("tag") or fact.get("source_tag") or "",
        "val": fact.get("val"),
        "unit": fact.get("unit", ""),
        "source_url": fact.get("source_url") or fact.get("filing_url", ""),
        "as_of": fact.get("as_of") or fact.get("end", ""),
        "fiscal_period": fact.get("fiscal_period", ""),
        "calendar_period": fact.get("calendar_period", ""),
        "accession": fact.get("accession") or fact.get("accn", ""),
    }


def _envelope(metric: str, value: Optional[float], formula: str,
              inputs: list[dict], caveats: Optional[list] = None) -> dict:
    """Build the canonical metric envelope.

    A required input that is missing nulls `value` and adds the input's label
    to `missing_inputs`. An optional input that is missing keeps `value` and is
    recorded in `optional_missing_inputs` (with a caveat already supplied by
    the caller).
    """
    required_missing = [i["label"] for i in inputs
                        if i.get("missing") and not i.get("optional")]
    optional_missing = [i["label"] for i in inputs
                        if i.get("missing") and i.get("optional")]
    present = [i for i in inputs if not i.get("missing")]
    out = {
        "metric": metric,
        "value": value if not required_missing else None,
        "formula": formula,
        "inputs": present,
        "caveats": caveats or [],
    }
    if required_missing:
        out["missing_inputs"] = required_missing
    if optional_missing:
        out["optional_missing_inputs"] = optional_missing
    return out


def _safe_divide(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


# --- TTM reconstruction ---


def ttm_from_quarters(quarter_facts: list[dict]) -> dict:
    """Sum 4 contiguous quarterly facts into a TTM value.

    Quarters are deemed contiguous when consecutive `end` dates are 80–110 days
    apart (allowing for 13-week fiscal calendars). Non-contiguous sets return
    a null value with a caveat naming the gap.
    """
    from datetime import datetime, timedelta

    def parse_end(f):
        try:
            return datetime.strptime(f.get("end", ""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    quarters = [f for f in quarter_facts if f.get("period_type") == "quarterly"]
    # Dedupe by end date — restated facts duplicate each period.
    by_end: dict = {}
    for f in quarters:
        end = parse_end(f)
        if end is None:
            continue
        prev = by_end.get(end)
        if prev is None or (f.get("filed", "") > prev.get("filed", "")):
            by_end[end] = f
    ordered = sorted(by_end.items(), key=lambda kv: kv[0], reverse=True)
    if len(ordered) < 4:
        return _envelope(
            metric="ttm",
            value=None,
            formula="sum of 4 contiguous quarterly facts",
            inputs=[_input_record(f"Q{i+1}", None) for i in range(4)],
            caveats=[f"Only {len(ordered)} quarterly facts available; need 4."],
        )

    # Find the most recent 4-period contiguous window.
    first_gap = None
    for start in range(len(ordered) - 3):
        window = ordered[start:start + 4]
        contiguous = True
        for (end_a, _), (end_b, _) in zip(window[:-1], window[1:]):
            delta = (end_a - end_b).days
            if not (80 <= delta <= 110):
                contiguous = False
                if first_gap is None:
                    first_gap = (end_b.isoformat(), end_a.isoformat(), delta)
                break
        if contiguous:
            chosen_facts = [f for _, f in window]
            total = sum(_value_or_none(f) or 0 for f in chosen_facts)
            return {
                "metric": "ttm",
                "value": total,
                "formula": "Q1 + Q2 + Q3 + Q4 (4 contiguous quarterly facts)",
                "inputs": [_input_record(f"Q{i+1}", f) for i, f in enumerate(chosen_facts)],
                "caveats": [],
                "period_end": chosen_facts[0].get("end", ""),
            }

    # No contiguous 4-period window — return null with a gap caveat that names
    # the actual mismatched dates (not just the most recent two). This is the
    # common Apple/Microsoft case where Q4 is only reported in the annual 10-K
    # and never as a standalone quarterly fact.
    if first_gap is None and len(ordered) >= 2:
        end_a, _ = ordered[0]
        end_b, _ = ordered[1]
        first_gap = (end_b.isoformat(), end_a.isoformat(), (end_a - end_b).days)
    msg = (
        f"Quarterly facts are not contiguous; gap of {first_gap[2]} days between "
        f"{first_gap[0]} and {first_gap[1]}. TTM value suppressed to avoid silent "
        "miscount. Filers that report Q4 only in the annual 10-K (e.g. Apple, "
        "Microsoft) need a stub-period reconstruction not yet implemented."
        if first_gap else "Insufficient quarterly facts for TTM."
    )
    return _envelope(
        metric="ttm",
        value=None,
        formula="sum of 4 contiguous quarterly facts",
        inputs=[_input_record(f"Q{i+1}", f) for i, (_, f) in enumerate(ordered[:4])],
        caveats=[msg],
    )


def ttm_from_stub_period(annual: Optional[dict], current_ytd: Optional[dict],
                         prior_ytd: Optional[dict]) -> dict:
    """Reconstruct TTM using FY + current_YTD - prior_year_same_YTD.

    Works for filers that report Q4 only inside the annual 10-K (Apple,
    Microsoft, NVIDIA). Inputs:
    - `annual`: most recent FY fact (period_type "annual").
    - `current_ytd`: most recent YTD fact whose `start` matches the FY's start.
    - `prior_ytd`: same YTD window from the prior fiscal year (matching length).
    """
    fy = _value_or_none(annual)
    cur = _value_or_none(current_ytd)
    prior = _value_or_none(prior_ytd)
    inputs = [_input_record("AnnualFY", annual),
              _input_record("CurrentYTD", current_ytd),
              _input_record("PriorYTD", prior_ytd)]
    if fy is None or cur is None or prior is None:
        return _envelope("ttm", None,
                         "AnnualFY + CurrentYTD - PriorYTD",
                         inputs)
    return {
        "metric": "ttm",
        "value": fy + cur - prior,
        "formula": "AnnualFY + CurrentYTD - PriorYTD",
        "inputs": [i for i in inputs if not i.get("missing")],
        "caveats": ["Stub-period reconstruction; assumes the YTD window is "
                    "comparable across fiscal years."],
        "period_end": (current_ytd or {}).get("end", ""),
    }


# --- Margins ---


def gross_margin(revenue: Optional[dict], cogs: Optional[dict],
                 gross_profit: Optional[dict] = None) -> dict:
    if gross_profit is not None:
        gp = _value_or_none(gross_profit)
        rev = _value_or_none(revenue)
        return _envelope(
            metric="gross_margin",
            value=_safe_divide(gp, rev),
            formula="GrossProfit / Revenue",
            inputs=[_input_record("Revenue", revenue), _input_record("GrossProfit", gross_profit)],
        )
    rev = _value_or_none(revenue)
    cg = _value_or_none(cogs)
    if rev is None or cg is None:
        return _envelope(
            metric="gross_margin",
            value=None,
            formula="(Revenue - CostOfGoodsAndServicesSold) / Revenue",
            inputs=[_input_record("Revenue", revenue), _input_record("CostOfGoodsAndServicesSold", cogs)],
        )
    return _envelope(
        metric="gross_margin",
        value=(rev - cg) / rev if rev else None,
        formula="(Revenue - CostOfGoodsAndServicesSold) / Revenue",
        inputs=[_input_record("Revenue", revenue), _input_record("CostOfGoodsAndServicesSold", cogs)],
    )


def operating_margin(revenue: Optional[dict], operating_income: Optional[dict]) -> dict:
    return _envelope(
        metric="operating_margin",
        value=_safe_divide(_value_or_none(operating_income), _value_or_none(revenue)),
        formula="OperatingIncomeLoss / Revenue",
        inputs=[_input_record("Revenue", revenue), _input_record("OperatingIncomeLoss", operating_income)],
    )


def net_margin(revenue: Optional[dict], net_income: Optional[dict]) -> dict:
    return _envelope(
        metric="net_margin",
        value=_safe_divide(_value_or_none(net_income), _value_or_none(revenue)),
        formula="NetIncomeLoss / Revenue",
        inputs=[_input_record("Revenue", revenue), _input_record("NetIncomeLoss", net_income)],
    )


def ebitda_margin(revenue: Optional[dict], operating_income: Optional[dict],
                  dna: Optional[dict]) -> dict:
    rev = _value_or_none(revenue)
    op = _value_or_none(operating_income)
    da = _value_or_none(dna)
    if any(v is None for v in (rev, op, da)):
        return _envelope(
            metric="ebitda_margin",
            value=None,
            formula="(OperatingIncomeLoss + DepreciationDepletionAndAmortization) / Revenue",
            inputs=[_input_record("Revenue", revenue), _input_record("OperatingIncomeLoss", operating_income),
                    _input_record("DepreciationDepletionAndAmortization", dna)],
            caveats=["EBITDA reconstructed as OperatingIncome + D&A; SBC treatment varies by analyst."],
        )
    return _envelope(
        metric="ebitda_margin",
        value=(op + da) / rev if rev else None,
        formula="(OperatingIncomeLoss + DepreciationDepletionAndAmortization) / Revenue",
        inputs=[_input_record("Revenue", revenue), _input_record("OperatingIncomeLoss", operating_income),
                _input_record("DepreciationDepletionAndAmortization", dna)],
        caveats=["EBITDA reconstructed as OperatingIncome + D&A; SBC treatment varies by analyst."],
    )


# --- Returns ---


def roe(net_income: Optional[dict], equity: Optional[dict]) -> dict:
    return _envelope(
        metric="roe",
        value=_safe_divide(_value_or_none(net_income), _value_or_none(equity)),
        formula="NetIncomeLoss / StockholdersEquity",
        inputs=[_input_record("NetIncomeLoss", net_income), _input_record("StockholdersEquity", equity)],
        caveats=["Uses period-end equity; some sources use average equity."],
    )


def roa(net_income: Optional[dict], assets: Optional[dict]) -> dict:
    return _envelope(
        metric="roa",
        value=_safe_divide(_value_or_none(net_income), _value_or_none(assets)),
        formula="NetIncomeLoss / Assets",
        inputs=[_input_record("NetIncomeLoss", net_income), _input_record("Assets", assets)],
        caveats=["Uses period-end assets; some sources use average assets."],
    )


def asset_turnover(revenue: Optional[dict], assets: Optional[dict]) -> dict:
    return _envelope(
        metric="asset_turnover",
        value=_safe_divide(_value_or_none(revenue), _value_or_none(assets)),
        formula="Revenue / Assets",
        inputs=[_input_record("Revenue", revenue), _input_record("Assets", assets)],
    )


# --- Liquidity / leverage ---


def current_ratio(assets_current: Optional[dict], liabilities_current: Optional[dict]) -> dict:
    return _envelope(
        metric="current_ratio",
        value=_safe_divide(_value_or_none(assets_current), _value_or_none(liabilities_current)),
        formula="AssetsCurrent / LiabilitiesCurrent",
        inputs=[_input_record("AssetsCurrent", assets_current),
                _input_record("LiabilitiesCurrent", liabilities_current)],
    )


def quick_ratio(assets_current: Optional[dict], inventory: Optional[dict],
                liabilities_current: Optional[dict]) -> dict:
    ac = _value_or_none(assets_current)
    inv = _value_or_none(inventory) or 0
    lc = _value_or_none(liabilities_current)
    inputs = [_input_record("AssetsCurrent", assets_current),
              _input_record("InventoryNet", inventory, optional=True),
              _input_record("LiabilitiesCurrent", liabilities_current)]
    if ac is None or lc is None:
        return _envelope("quick_ratio", None,
                         "(AssetsCurrent - InventoryNet) / LiabilitiesCurrent", inputs)
    return _envelope(
        metric="quick_ratio",
        value=(ac - inv) / lc if lc else None,
        formula="(AssetsCurrent - InventoryNet) / LiabilitiesCurrent",
        inputs=inputs,
        caveats=[] if inventory else ["InventoryNet missing; treated as zero."],
    )


def debt_to_equity(debt: Optional[dict], short_term_debt: Optional[dict],
                   equity: Optional[dict]) -> dict:
    d = _value_or_none(debt) or 0
    sd = _value_or_none(short_term_debt) or 0
    eq = _value_or_none(equity)
    return _envelope(
        metric="debt_to_equity",
        value=_safe_divide(d + sd, eq),
        formula="(LongTermDebtNoncurrent + ShortTermDebt) / StockholdersEquity",
        inputs=[_input_record("LongTermDebtNoncurrent", debt),
                _input_record("ShortTermDebt", short_term_debt),
                _input_record("StockholdersEquity", equity)],
    )


def debt_to_ebitda(debt: Optional[dict], short_term_debt: Optional[dict],
                   operating_income: Optional[dict], dna: Optional[dict]) -> dict:
    d = _value_or_none(debt) or 0
    sd = _value_or_none(short_term_debt) or 0
    op = _value_or_none(operating_income)
    da = _value_or_none(dna)
    if op is None or da is None:
        return _envelope(
            "debt_to_ebitda", None,
            "(LongTermDebtNoncurrent + ShortTermDebt) / (OperatingIncomeLoss + D&A)",
            [_input_record("LongTermDebtNoncurrent", debt),
             _input_record("ShortTermDebt", short_term_debt),
             _input_record("OperatingIncomeLoss", operating_income),
             _input_record("DepreciationDepletionAndAmortization", dna)],
            caveats=["EBITDA reconstructed; definition varies."])
    return _envelope(
        "debt_to_ebitda",
        _safe_divide(d + sd, op + da),
        "(LongTermDebtNoncurrent + ShortTermDebt) / (OperatingIncomeLoss + D&A)",
        [_input_record("LongTermDebtNoncurrent", debt),
         _input_record("ShortTermDebt", short_term_debt),
         _input_record("OperatingIncomeLoss", operating_income),
         _input_record("DepreciationDepletionAndAmortization", dna)],
        caveats=["EBITDA reconstructed as OperatingIncome + D&A."])


def net_debt(debt: Optional[dict], short_term_debt: Optional[dict],
             cash: Optional[dict]) -> dict:
    d = _value_or_none(debt) or 0
    sd = _value_or_none(short_term_debt) or 0
    c = _value_or_none(cash)
    if c is None:
        return _envelope(
            "net_debt", None,
            "LongTermDebtNoncurrent + ShortTermDebt - Cash",
            [_input_record("LongTermDebtNoncurrent", debt),
             _input_record("ShortTermDebt", short_term_debt),
             _input_record("Cash", cash)])
    return _envelope(
        "net_debt", d + sd - c,
        "LongTermDebtNoncurrent + ShortTermDebt - Cash",
        [_input_record("LongTermDebtNoncurrent", debt),
         _input_record("ShortTermDebt", short_term_debt),
         _input_record("Cash", cash)])


# --- Free cash flow + reconstructions ---


def free_cash_flow(operating_cash_flow: Optional[dict], capex: Optional[dict]) -> dict:
    ocf = _value_or_none(operating_cash_flow)
    cx = _value_or_none(capex)
    if ocf is None or cx is None:
        return _envelope(
            "fcf", None,
            "OperatingCashFlow - CapEx",
            [_input_record("OperatingCashFlow", operating_cash_flow),
             _input_record("CapEx", capex)],
            caveats=["FCF capex scope: PP&E only; excludes acquisitions and capitalized software."])
    return _envelope(
        "fcf", ocf - cx,
        "OperatingCashFlow - CapEx",
        [_input_record("OperatingCashFlow", operating_cash_flow),
         _input_record("CapEx", capex)],
        caveats=["FCF capex scope: PP&E only; excludes acquisitions and capitalized software."])


def fcf_margin(revenue: Optional[dict], operating_cash_flow: Optional[dict],
               capex: Optional[dict]) -> dict:
    rev = _value_or_none(revenue)
    fcf = free_cash_flow(operating_cash_flow, capex)
    if rev is None or fcf.get("value") is None:
        return _envelope(
            "fcf_margin", None,
            "(OperatingCashFlow - CapEx) / Revenue",
            [_input_record("Revenue", revenue),
             _input_record("OperatingCashFlow", operating_cash_flow),
             _input_record("CapEx", capex)])
    return _envelope(
        "fcf_margin", fcf["value"] / rev if rev else None,
        "(OperatingCashFlow - CapEx) / Revenue",
        [_input_record("Revenue", revenue),
         _input_record("OperatingCashFlow", operating_cash_flow),
         _input_record("CapEx", capex)])


def ebitda(operating_income: Optional[dict], dna: Optional[dict]) -> dict:
    op = _value_or_none(operating_income)
    da = _value_or_none(dna)
    if op is None or da is None:
        return _envelope("ebitda", None,
                         "OperatingIncomeLoss + DepreciationDepletionAndAmortization",
                         [_input_record("OperatingIncomeLoss", operating_income),
                          _input_record("DepreciationDepletionAndAmortization", dna)],
                         caveats=["SBC treatment varies; this definition does not add back SBC."])
    return _envelope("ebitda", op + da,
                     "OperatingIncomeLoss + DepreciationDepletionAndAmortization",
                     [_input_record("OperatingIncomeLoss", operating_income),
                      _input_record("DepreciationDepletionAndAmortization", dna)],
                     caveats=["SBC treatment varies; this definition does not add back SBC."])


def tangible_book_value(equity: Optional[dict], goodwill: Optional[dict] = None,
                        intangibles: Optional[dict] = None) -> dict:
    eq = _value_or_none(equity)
    gw = _value_or_none(goodwill) or 0
    intang = _value_or_none(intangibles) or 0
    if eq is None:
        return _envelope("tangible_book_value", None,
                         "StockholdersEquity - Goodwill - IntangibleAssetsNetExcludingGoodwill",
                         [_input_record("StockholdersEquity", equity),
                          _input_record("Goodwill", goodwill, optional=True),
                          _input_record("Intangibles", intangibles, optional=True)],
                         caveats=["Goodwill / Intangibles treated as zero when missing."])
    return _envelope("tangible_book_value", eq - gw - intang,
                     "StockholdersEquity - Goodwill - IntangibleAssetsNetExcludingGoodwill",
                     [_input_record("StockholdersEquity", equity),
                      _input_record("Goodwill", goodwill, optional=True),
                      _input_record("Intangibles", intangibles, optional=True)],
                     caveats=["Goodwill / Intangibles treated as zero when missing."])


def working_capital(assets_current: Optional[dict], liabilities_current: Optional[dict]) -> dict:
    ac = _value_or_none(assets_current)
    lc = _value_or_none(liabilities_current)
    if ac is None or lc is None:
        return _envelope("working_capital", None,
                         "AssetsCurrent - LiabilitiesCurrent",
                         [_input_record("AssetsCurrent", assets_current),
                          _input_record("LiabilitiesCurrent", liabilities_current)])
    return _envelope("working_capital", ac - lc,
                     "AssetsCurrent - LiabilitiesCurrent",
                     [_input_record("AssetsCurrent", assets_current),
                      _input_record("LiabilitiesCurrent", liabilities_current)])


# --- Trend / growth ---


def linear_slope(values: list[float]) -> Optional[float]:
    """Least-squares slope of a series; returns None if too few points."""
    n = len(values)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else None


def trend_label(slope: Optional[float], values: list[float]) -> str:
    """Categorize a series: expanding / contracting / stable / inflecting."""
    if slope is None or not values:
        return "unknown"
    avg_abs = sum(abs(v) for v in values) / max(1, len(values))
    rel = abs(slope) / max(avg_abs, 1e-12)
    if rel < 0.005:
        return "stable"
    if len(values) >= 4:
        # Check sign change: compare slope of first half vs second half
        mid = len(values) // 2
        s_first = linear_slope(values[:mid])
        s_second = linear_slope(values[mid:])
        if s_first is not None and s_second is not None and (s_first > 0) != (s_second > 0):
            return "inflecting"
    return "expanding" if slope > 0 else "contracting"


def trend_summary(facts: list[dict]) -> dict:
    """Summarize a list of facts (chronologically newest first) with slope and label."""
    sorted_facts = sorted(facts, key=lambda f: f.get("end", ""))
    values = [_value_or_none(f) for f in sorted_facts]
    values = [v for v in values if v is not None]
    if not values:
        return {"slope": None, "label": "unknown", "n": 0}
    slope = linear_slope(values)
    label = trend_label(slope, values)
    direction = None
    if len(values) >= 2:
        direction = "up" if values[-1] > values[0] else ("flat" if values[-1] == values[0] else "down")
    return {
        "slope": slope,
        "label": label,
        "n": len(values),
        "first_value": values[0] if values else None,
        "last_value": values[-1] if values else None,
        "direction": direction,
    }


def growth_rates(facts: list[dict]) -> list[dict]:
    """Per-period YoY-style change between adjacent facts in chronological order."""
    sorted_facts = sorted(facts, key=lambda f: f.get("end", ""))
    out = []
    for i in range(1, len(sorted_facts)):
        prev = _value_or_none(sorted_facts[i - 1])
        cur = _value_or_none(sorted_facts[i])
        if prev in (None, 0) or cur is None:
            continue
        out.append({
            "period_end": sorted_facts[i].get("end", ""),
            "value": cur,
            "prior_value": prev,
            "growth": (cur - prev) / abs(prev),
        })
    return out


def cagr(values: list[float], periods_per_year: int = 1) -> Optional[float]:
    """Compound annual growth rate. `periods_per_year` is 1 for annual, 4 for quarterly."""
    if not values or values[0] <= 0 or values[-1] <= 0 or len(values) < 2:
        return None
    n_periods = len(values) - 1
    years = n_periods / periods_per_year
    if years <= 0:
        return None
    return (values[-1] / values[0]) ** (1 / years) - 1
