"""Offline arithmetic/provenance regressions; synthetic cases are labeled.

The CODX disclosure is extracted from the sourced fixture without correcting
its arithmetic. Cash-flow wrappers below are synthetic XBRL envelopes around
amounts independently verified in the accompanying 10-Q excerpt.
"""

import copy
import json
import re
from pathlib import Path

import pytest

from edgar.dilution import extract_statements
from edgar.filing_text import split_sentences
from edgar.forensics import compute_baby_shelf, compute_cash_runway


FIXTURES = Path(__file__).parent / "fixtures" / "handoff"


def _codx_shelf():
    source = json.loads((FIXTURES / "codx-20260727.json").read_text())
    rows = extract_statements(split_sentences("\n\n".join(source["paragraphs"])), prospectus=True)
    capacity = [{**r, "accession": source["accession"], "source_url": source["source_url"],
                 "form": source["form"], "filed": "2026-07-27"}
                for r in rows if r["topic"] == "shelf_capacity"]
    # The excerpt does not supply the file number; do not invent one.
    return {"shelf_registrations": [{"capacity": capacity}]}


def _synthetic_shelf(mvphs=30_000_000, shares=3_000_000, price=10, sales=1_000_000):
    """Deliberately synthetic evidence, not an attributed SEC filing."""
    def row(topic, figures):
        return {"subtopic": topic, "topic": "shelf_capacity", "figures": figures,
                "sentence": f"Synthetic {topic} disclosure.", "file_number": "synthetic-1",
                "accession": "synthetic-accession-1", "filed": "2026-09-01",
                "source_url": "https://example.invalid/synthetic"}
    return {"shelf_capacity": [
        row("mvphs_float", [{"type": "dollars", "value": mvphs, "text": f"${mvphs}"},
                             {"type": "shares", "value": shares, "text": f"{shares} shares"},
                             {"type": "price_per_share", "value": price, "text": f"${price} per share"}]),
        row("prior_12m_sales", [{"type": "dollars", "value": sales, "text": f"${sales}"}]),
    ]}


def _codes(result):
    return {w["code"] for w in result["warnings"]}


def _envelopes(result):
    return [v for v in result.values() if isinstance(v, dict) and "metric" in v]


def _assert_contract(result):
    for metric in _envelopes(result):
        assert {"metric", "value", "formula", "inputs", "caveats"} <= metric.keys()
        assert metric["formula"]
        assert isinstance(metric["inputs"], list)
        assert isinstance(metric["caveats"], list)
        if "missing_inputs" in metric:
            assert metric["value"] is None
            assert metric["missing_inputs"]
        for source in metric["inputs"]:
            assert {"label", "val", "unit", "source_url", "as_of", "origin"} <= source.keys()
    # Nonfinite Python floats are not interoperable JSON numeric values.
    json.dumps(result, allow_nan=False)


def test_codx_preserves_inconsistent_disclosures_and_exposes_both_calculations():
    data = _codx_shelf()
    before = copy.deepcopy(data)
    result = compute_baby_shelf(data)
    assert data == before
    assert result["stated_MVPHS"]["value"] == 49_425_570
    assert result["calculated_MVPHS"]["value"] == pytest.approx(57_672_599.79)
    assert result["MVPHS"]["value"] == 49_425_570
    assert result["gross_12m_cap"]["value"] == 16_475_190
    assert result["prior_12m_ib6_sales_usd"]["value"] == 11_322_816
    assert result["usable_capacity_usd"]["value"] == 5_152_374
    assert result["stated_remaining_capacity_usd"]["value"] == 7_901_383
    assert result["escape_price_75m"]["value"] == pytest.approx(75_000_000 / 5_153_941)
    assert {"float_arithmetic_disagreement", "capacity_arithmetic_disagreement"} <= _codes(result)
    assert result["selected_filing"]["accession"] == "0001493152-26-034835"
    product_inputs = result["calculated_MVPHS"]["inputs"]
    assert [i["as_of"] for i in product_inputs] == ["2026-07-27", "2026-05-28"]
    assert all(i["source_url"].startswith("https://www.sec.gov/Archives/") for i in product_inputs)
    assert any("reassessed" in c for c in result["escape_price_75m"]["caveats"])
    _assert_contract(result)


def test_explicit_price_uses_scenario_but_retains_filing_disclosures():
    result = compute_baby_shelf(_codx_shelf(), price=11.19)
    assert result["MVPHS"]["value"] == pytest.approx(57_672_599.79)
    assert result["usable_capacity_usd"]["value"] == pytest.approx(7_901_383.93)
    assert result["stated_MVPHS"]["value"] == 49_425_570
    assert result["stated_remaining_capacity_usd"]["value"] == 7_901_383
    assert result["reference_price"]["inputs"][0]["origin"] == "caller"
    assert result["reference_price"]["inputs"][0]["source_url"] == ""
    assert "manual_scenario" in _codes(result)
    _assert_contract(result)


def test_high_60d_argument_precedence_and_manual_share_provenance():
    result = compute_baby_shelf(_synthetic_shelf(), public_float_shares=2_000_000,
                                price=2, high_60d_close=4)
    assert result["MVPHS"]["value"] == 8_000_000
    assert result["reference_price"]["inputs"][0]["argument"] == "high_60d_close"
    assert result["non_affiliate_float_shares"]["inputs"][0]["origin"] == "caller"
    assert "price_precedence" in _codes(result)
    assert any("not verified market data" in c for c in result["MVPHS"]["caveats"])


@pytest.mark.parametrize("argument", ["price", "high_60d_close", "public_float_shares"])
@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "invalid"])
def test_invalid_manual_values_never_fall_back_to_disclosed_amount(argument, value):
    result = compute_baby_shelf(_synthetic_shelf(), **{argument: value})
    assert result["MVPHS"]["value"] is None
    assert result["usable_capacity_usd"]["value"] is None
    assert result["stated_MVPHS"]["value"] == 30_000_000
    _assert_contract(result)


@pytest.mark.parametrize("mvphs", [75_000_000, 90_000_000])
def test_threshold_does_not_claim_full_capacity_or_permanent_eligibility(mvphs):
    result = compute_baby_shelf(_synthetic_shelf(mvphs=mvphs))
    assert result["gross_12m_cap"]["value"] is None
    assert result["usable_capacity_usd"]["value"] is None
    assert any("does not establish" in c for c in result["gross_12m_cap"]["caveats"])


def test_absent_sales_remain_unknown_and_reported_zero_is_not_missing():
    data = _synthetic_shelf(sales=0)
    assert compute_baby_shelf(data)["usable_capacity_usd"]["value"] == 10_000_000
    data["shelf_capacity"].pop()
    result = compute_baby_shelf(data)
    assert result["gross_12m_cap"]["value"] == 10_000_000
    assert result["usable_capacity_usd"]["value"] is None
    assert result["usable_capacity_usd"]["missing_inputs"] == ["prior_12m_ib6_sales_usd"]


def test_capacity_floors_at_zero_after_excess_prior_sales():
    result = compute_baby_shelf(_synthetic_shelf(sales=12_000_000))
    assert result["usable_capacity_usd"]["value"] == 0


def test_newest_filing_does_not_backfill_prior_sales_from_an_older_series():
    data = _synthetic_shelf()
    float_row = data["shelf_capacity"][0]
    data["shelf_capacity"].append({**copy.deepcopy(float_row), "filed": "2026-10-01",
                                   "accession": "synthetic-accession-2"})
    result = compute_baby_shelf(data)
    assert result["selected_filing"]["accession"] == "synthetic-accession-2"
    assert result["usable_capacity_usd"]["value"] is None
    assert "single_filing_scope" in _codes(result)


def test_same_time_different_shelves_are_ambiguous_not_combined():
    data = _synthetic_shelf()
    data["shelf_capacity"][1]["file_number"] = "synthetic-2"
    result = compute_baby_shelf(data)
    assert result["MVPHS"]["value"] is None
    assert "ambiguous_latest_shelf" in _codes(result)


def test_multiple_float_dates_in_one_filing_are_not_spliced_together():
    data = _synthetic_shelf()
    row = copy.deepcopy(data["shelf_capacity"][0])
    row["sentence"] = "Synthetic float measured on an earlier date."
    data["shelf_capacity"].append(row)
    result = compute_baby_shelf(data)
    assert result["MVPHS"]["value"] is None
    assert result["escape_price_75m"]["value"] is None
    assert "ambiguous_disclosure" in _codes(result)


def test_shelf_provenance_absent_does_not_combine_unidentified_evidence():
    data = _synthetic_shelf()
    for row in data["shelf_capacity"]:
        row.pop("accession")
        row.pop("source_url")
    result = compute_baby_shelf(data)
    assert result["MVPHS"]["value"] is None
    assert "missing_filing_identity" in _codes(result)


def test_base_prospectus_float_is_not_used_as_a_current_supplement_measurement():
    data = _synthetic_shelf()
    data["shelf_capacity"][0]["part"] = "base_prospectus"
    result = compute_baby_shelf(data)
    assert result["MVPHS"]["value"] is None


@pytest.mark.parametrize("currency", ["SGD", "CAD", "EUR"])
def test_shelf_currency_is_never_silently_converted_to_usd(currency):
    data = _synthetic_shelf()
    data["shelf_capacity"][1]["figures"][0]["currency"] = currency
    result = compute_baby_shelf(data)
    assert result["usable_capacity_usd"]["value"] is None
    assert any("No currency conversion" in c for c in result["usable_capacity_usd"]["caveats"])


def test_unmarked_singapore_dollar_prefix_is_preserved_by_currency_check():
    data = _synthetic_shelf()
    data["shelf_capacity"][1]["figures"][0]["text"] = "S$1,000,000"
    result = compute_baby_shelf(data)
    assert result["usable_capacity_usd"]["value"] is None


def test_manual_scenario_without_filing_cannot_invent_prior_sales():
    result = compute_baby_shelf({}, public_float_shares=1_000_000, price=6)
    assert result["MVPHS"]["value"] == 6_000_000
    assert result["gross_12m_cap"]["value"] == 2_000_000
    assert result["usable_capacity_usd"]["value"] is None


def test_overflowing_float_scenario_is_null_and_json_serializable():
    result = compute_baby_shelf({}, public_float_shares=1e308, price=1e308)
    assert result["MVPHS"]["value"] is None
    assert result["gross_12m_cap"]["value"] is None
    _assert_contract(result)


def _metric(alias, val, *, end="2026-09-30", unit="USD", stale=False, tag=None, **fact):
    """Synthetic company-metrics envelope, with explicit dates/currency."""
    return {"metric": alias, "unit": unit, "stale": stale,
            "tag": tag or alias, "taxonomy": "us-gaap",
            "fact": {"val": val, "unit": unit, "end": end, "filed": "2026-11-10",
                     "accn": "synthetic-periodic-accession",
                     "source_url": "https://example.invalid/synthetic-report", **fact}}


def _synthetic_metrics(ocf=-2720, cash=1000):
    return {"metrics": [
        _metric("cash", cash, tag="CashAndCashEquivalentsAtCarryingValue"),
        _metric("operating_cash_flow", ocf, start="2026-01-01", period_length_days=272),
        _metric("assets_current", 5000), _metric("liabilities_current", 6000),
        _metric("debt", 300, tag="LongTermDebtNoncurrent"),
        _metric("short_term_debt", 100, tag="DebtCurrent"),
    ]}


def test_cash_runway_uses_verified_codx_amounts_and_corrects_handoff_monthly_math():
    fixture = json.loads((FIXTURES / "codx-20261110-periodic.json").read_text())
    cash_sentence = next(p for p in fixture["paragraphs"] if p.startswith("Cash and cash equivalents $"))
    ocf_sentence = next(p for p in fixture["paragraphs"] if p.startswith("Net cash used in operating activities"))
    cash = int(re.search(r"\$ ([\d,]+)", cash_sentence).group(1).replace(",", ""))
    ocf = -int(re.search(r"\(([\d,]+)", ocf_sentence).group(1).replace(",", ""))
    data = {"metrics": [_metric("cash", cash),
                        _metric("operating_cash_flow", ocf, start="2026-01-01", period_length_days=272)]}
    for row in data["metrics"]:
        row["fact"].update(source_url=fixture["source_url"], accn=fixture["accession"])
    result = compute_cash_runway(data)
    assert result["cash_usd"]["value"] == 714_803
    assert result["monthly_operating_burn_usd"]["value"] == pytest.approx(2_314_514.610983456)
    assert result["runway_months"]["value"] == pytest.approx(0.308834948203794)
    assert result["runway_days"]["value"] == pytest.approx(9.40016373595298)
    assert result["total_debt_usd"]["value"] is None
    assert result["cash_usd"]["inputs"][0]["accession"] == fixture["accession"]
    assert "Subsequent financing" in result["cash_usd"]["caveats"][0]
    _assert_contract(result)


def test_aligned_cash_snapshot_is_pure_and_all_metrics_have_envelopes():
    data = _synthetic_metrics()
    before = copy.deepcopy(data)
    result = compute_cash_runway(data)
    assert data == before
    assert result["monthly_operating_burn_usd"]["value"] == 304.375
    assert result["runway_days"]["value"] == pytest.approx(100)
    assert result["net_working_capital_usd"]["value"] == -1000
    assert result["total_debt_usd"]["value"] == 400
    _assert_contract(result)


@pytest.mark.parametrize("debt_case", ["missing", "stale", "older_snapshot", "no_date"])
def test_missing_or_stale_debt_never_becomes_zero(debt_case):
    data = _synthetic_metrics()
    if debt_case == "missing":
        data["metrics"] = [m for m in data["metrics"] if m["metric"] != "debt"]
    else:
        row = next(m for m in data["metrics"] if m["metric"] == "debt")
        if debt_case == "stale":
            row["stale"] = True
            row["stale_reason"] = "Superseded by current periodic report"
        elif debt_case == "older_snapshot":
            row["fact"]["end"] = "2018-12-31"
        else:
            row["fact"].pop("end")
    result = compute_cash_runway(data)
    assert result["total_debt_usd"]["value"] is None
    assert "debt" in result["total_debt_usd"]["missing_inputs"]
    assert result["cash_usd"]["value"] == 1000


def test_explicit_zero_debt_components_are_a_reported_zero():
    data = _synthetic_metrics()
    for row in data["metrics"]:
        if row["metric"] in {"debt", "short_term_debt"}:
            row["fact"]["val"] = 0
    result = compute_cash_runway(data)
    assert result["total_debt_usd"]["value"] == 0
    assert "missing_inputs" not in result["total_debt_usd"]


@pytest.mark.parametrize("alias", ["cash", "operating_cash_flow", "assets_current", "short_term_debt"])
def test_mismatched_periods_are_revealed_and_not_combined(alias):
    data = _synthetic_metrics()
    row = next(m for m in data["metrics"] if m["metric"] == alias)
    row["fact"]["end"] = "2026-06-30"
    result = compute_cash_runway(data)
    metric = {"cash": "runway_days", "operating_cash_flow": "runway_days",
              "assets_current": "net_working_capital_usd", "short_term_debt": "total_debt_usd"}[alias]
    assert result[metric]["value"] is None
    assert "period_mismatch" in _codes(result)


def test_latest_report_period_prevents_use_of_older_snapshot():
    data = _synthetic_metrics()
    data["latest_periodic_report"] = {"report_date": "2026-12-31"}
    result = compute_cash_runway(data)
    assert all(m["value"] is None for m in _envelopes(result))


@pytest.mark.parametrize("currency", ["EUR", "CAD", "", "USD/shares"])
def test_cash_currency_mismatch_does_not_produce_usd_runway(currency):
    data = _synthetic_metrics()
    data["metrics"][1]["fact"]["unit"] = currency
    data["metrics"][1]["unit"] = currency
    result = compute_cash_runway(data)
    assert result["monthly_operating_burn_usd"]["value"] is None
    assert result["runway_days"]["value"] is None
    assert any("No currency conversion" in c for c in result["runway_days"]["caveats"])


def test_matching_nonusd_facts_still_do_not_masquerade_as_usd_outputs():
    data = _synthetic_metrics()
    for row in data["metrics"]:
        row["unit"] = row["fact"]["unit"] = "EUR"
    result = compute_cash_runway(data)
    assert all(m["value"] is None for m in _envelopes(result))


@pytest.mark.parametrize("ocf", [0, 1000])
def test_nonnegative_operating_cash_flow_has_no_finite_depletion_runway(ocf):
    result = compute_cash_runway(_synthetic_metrics(ocf=ocf))
    assert result["monthly_operating_burn_usd"]["value"] is None
    assert result["runway_months"]["value"] is None
    assert any("nonnegative" in c for c in result["runway_months"]["caveats"])


def test_zero_cash_produces_zero_runway_with_valid_burn():
    result = compute_cash_runway(_synthetic_metrics(cash=0))
    assert result["runway_days"]["value"] == 0


@pytest.mark.parametrize("days", [0, -1, 273, float("nan")])
def test_invalid_or_inconsistent_duration_does_not_create_a_burn_rate(days):
    data = _synthetic_metrics()
    data["metrics"][1]["fact"]["period_length_days"] = days
    result = compute_cash_runway(data)
    assert result["monthly_operating_burn_usd"]["value"] is None
    assert result["runway_days"]["value"] is None
    _assert_contract(result)


def test_duration_can_be_derived_from_the_reported_interval():
    data = _synthetic_metrics()
    data["metrics"][1]["fact"].pop("period_length_days")
    assert compute_cash_runway(data)["monthly_operating_burn_usd"]["value"] == 304.375


@pytest.mark.parametrize("tag", ["Borrowings", "ifrs-full:Borrowings", "Liabilities", "LongTermDebt"])
def test_total_borrowings_or_liabilities_are_not_added_to_current_debt(tag):
    data = _synthetic_metrics()
    data["metrics"][4]["tag"] = tag
    result = compute_cash_runway(data)
    assert result["total_debt_usd"]["value"] is None


def test_restricted_cash_tag_surfaces_availability_limit():
    data = _synthetic_metrics()
    data["metrics"][0]["tag"] = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    result = compute_cash_runway(data)
    assert any("includes restricted cash" in c for c in result["runway_months"]["caveats"])


def test_current_liabilities_are_not_treated_as_current_debt():
    data = _synthetic_metrics()
    data["metrics"][5]["tag"] = "ifrs-full:CurrentLiabilities"
    result = compute_cash_runway(data)
    assert result["total_debt_usd"]["value"] is None


def test_shelf_currency_metadata_is_retained_on_its_capacity_statements():
    rows = _synthetic_shelf()["shelf_capacity"]
    result = compute_baby_shelf({"shelf_registrations": [{"capacity": rows, "currency": "EUR"}]})
    assert result["MVPHS"]["value"] is None
    assert result["usable_capacity_usd"]["value"] is None


def test_conflicting_latest_values_are_unknown_instead_of_input_order_dependent():
    data = _synthetic_metrics()
    data["metrics"].append(_metric("cash", 12345))
    result = compute_cash_runway(data)
    assert result["cash_usd"]["value"] is None
    assert any("conflicting values" in c for c in result["cash_usd"]["caveats"])


def test_newest_metric_is_selected_without_mutating_older_evidence():
    data = _synthetic_metrics()
    data["metrics"].append(_metric("cash", 12, end="2026-06-30"))
    assert compute_cash_runway(data)["cash_usd"]["value"] == 1000


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1])
def test_invalid_cash_does_not_create_runway(value):
    result = compute_cash_runway(_synthetic_metrics(cash=value))
    assert result["runway_days"]["value"] is None
    _assert_contract(result)


def test_overflowing_burn_does_not_turn_into_zero_runway():
    data = _synthetic_metrics(ocf=-1e308)
    data["metrics"][1]["fact"].update(start="2026-09-29", period_length_days=1)
    result = compute_cash_runway(data)
    assert result["monthly_operating_burn_usd"]["value"] is None
    assert result["runway_days"]["value"] is None
    _assert_contract(result)


def test_empty_inputs_keep_unknowns_and_source_warnings_are_preserved():
    warning = {"code": "xbrl_filing_missing_from_sec_api", "message": "New filing not yet in XBRL."}
    result = compute_cash_runway({"warnings": [warning]})
    assert all(m["value"] is None for m in _envelopes(result))
    assert result["source_warnings"] == [warning]
    _assert_contract(result)
