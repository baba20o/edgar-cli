"""Metric freshness, the `stale` rule, and SEC XBRL API coverage warnings.

OMH (fixtures) is the regression case: `metrics --bundle revenue` returned the
half-year 2025 figure under `Revenues` and called it fresh, while full-year
2025 revenue sat under `RevenueFromContractWithCustomerIncludingAssessedTax`
in the 20-F; and the half-year 2026 6-K, though filed with XBRL financial
statements, is absent from SEC's companyfacts API.
"""

import json

from click.testing import CliRunner

from edgar.api import (
    EdgarClient,
    concept_alias_candidates,
    freshest_fact,
    latest_periodic_report,
    metric_staleness,
    newest_xbrl_filing,
)
from edgar.cli import main

from omh import CIK, offline_client

H1_2026_6K = "0001213900-26-098819"
FY2025_20F = "0001213900-26-048586"


def test_revenue_alias_covers_the_including_assessed_tax_element():
    tags = [tag for _, tag, _ in concept_alias_candidates("revenue")]

    assert "RevenueFromContractWithCustomerIncludingAssessedTax" in tags


def test_freshest_fact_takes_the_latest_period_even_when_unframed():
    facts = [
        {"val": 1, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-04-29", "frame": "CY2024"},
        {"val": 2, "start": "2025-01-01", "end": "2025-06-30", "filed": "2025-12-02"},
    ]

    assert freshest_fact(facts)["val"] == 2


def test_freshest_fact_prefers_framed_then_longer_then_latest_filed_for_one_end_date():
    quarter = {"val": 1, "start": "2025-04-01", "end": "2025-06-30", "filed": "2025-08-01",
               "frame": "CY2025Q2"}
    ytd = {"val": 2, "start": "2025-01-01", "end": "2025-06-30", "filed": "2025-08-01"}
    assert freshest_fact([ytd, quarter])["val"] == 1

    original = {"val": 3, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-02-01"}
    restated = {"val": 4, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-08-01"}
    assert freshest_fact([original, restated])["val"] == 4

    assert freshest_fact([]) is None


def test_freshest_fact_ignores_a_period_that_ended_after_it_was_filed():
    # OMH's 6-K filed 2025-12-02 tags a "2025-07-01..2025-12-31" context with
    # the first-half value: a period cannot be reported before it ends.
    misdated = {"val": 5216078, "start": "2025-07-01", "end": "2025-12-31", "filed": "2025-12-02"}
    real = {"val": 5216078, "start": "2025-01-01", "end": "2025-06-30", "filed": "2025-12-02"}

    assert freshest_fact([misdated, real]) is real
    assert freshest_fact([misdated]) is misdated


def test_metrics_returns_fy2025_revenue_for_omh():
    client = offline_client()

    result = client.metrics(CIK, ["revenue"])

    metric = result["metrics"][0]
    assert metric["tag"] == "RevenueFromContractWithCustomerIncludingAssessedTax"
    assert metric["fact"]["val"] == 9519777
    assert (metric["fact"]["start"], metric["fact"]["end"]) == ("2025-01-01", "2025-12-31")
    assert metric["fact"]["accession"] == FY2025_20F
    assert metric["unit"] == "USD"


def test_metrics_marks_fy2025_stale_because_a_later_report_exists():
    client = offline_client()

    result = client.metrics(CIK, ["revenue"])

    assert result["latest_periodic_report"] == {
        "form": "6-K", "accession": H1_2026_6K, "filed": "2026-09-10", "report_date": "2026-06-30",
    }
    metric = result["metrics"][0]
    assert metric["stale"] is True
    assert metric["age_days"] == 313          # 2025-12-31 -> 2026-11-09, well under 548
    assert metric["stale_reason"].startswith(f"Superseded: 6-K {H1_2026_6K} (filed 2026-09-10)")
    assert "period ending 2026-06-30" in metric["stale_reason"]


def test_metric_staleness_rule():
    report = {"form": "10-Q", "accession": "a-1", "filed": "2026-08-01", "report_date": "2026-06-28"}

    # Same period as the latest report (52/53-week drift tolerated): fresh.
    assert metric_staleness({"end": "2026-06-30"}, "2026-08-01", report) == (False, "")
    assert metric_staleness({"end": "2026-06-22"}, "2026-08-01", report) == (False, "")
    # Cover-page facts dated after the period end: fresh.
    assert metric_staleness({"end": "2026-07-25"}, "2026-08-01", report)[0] is False

    stale, reason = metric_staleness({"end": "2026-03-29"}, "2026-08-01", report)
    assert stale is True and reason.startswith("Superseded: 10-Q a-1")

    # No periodic report known: fall back to the age limit.
    assert metric_staleness({"end": "2025-12-31"}, "2026-08-01", None) == (False, "")
    stale, reason = metric_staleness({"end": "2021-12-31"}, "2026-02-15", None)
    assert stale is True and reason.startswith("Old: the period ended 1507 days")


def test_latest_periodic_report_and_newest_xbrl_filing_from_filing_rows():
    rows = [
        {"form": "6-K", "accessionNumber": "c", "filingDate": "2026-10-01", "reportDate": "2026-10-01",
         "isXBRLNumeric": 0, "isXBRL": 0},
        {"form": "8-K", "accessionNumber": "x", "filingDate": "2026-09-20", "reportDate": "2026-09-19",
         "isXBRLNumeric": 0, "isXBRL": 1, "isInlineXBRL": 1},
        {"form": "6-K", "accessionNumber": "b", "filingDate": "2026-09-10", "reportDate": "2026-06-30",
         "isXBRLNumeric": 1, "isXBRL": 1},
        {"form": "20-F", "accessionNumber": "a", "filingDate": "2026-04-28", "reportDate": "2025-12-31",
         "isXBRLNumeric": 1, "isXBRL": 1},
        {"form": "6-K", "accessionNumber": "old", "filingDate": "2025-12-02", "reportDate": "2025-06-30",
         "isXBRLNumeric": None, "isXBRL": 1},
    ]

    # A plain 6-K (no XBRL financials) and an 8-K cover page are not reports.
    assert latest_periodic_report(rows)["accession"] == "b"
    assert newest_xbrl_filing(rows)["accession"] == "b"
    assert latest_periodic_report(rows[3:])["accession"] == "a"
    # Rows older than the isXBRLNumeric flag fall back to isXBRL.
    assert newest_xbrl_filing(rows[4:])["accession"] == "old"
    assert latest_periodic_report([]) is None and newest_xbrl_filing([]) is None


def test_submissions_reports_where_the_filer_stands():
    profile = offline_client().submissions(CIK, limit=1)

    assert profile["latest_periodic_report"]["accession"] == H1_2026_6K
    assert profile["newest_xbrl_filing"]["accession"] == H1_2026_6K


def test_metrics_warns_that_sec_api_lacks_the_newest_xbrl_filing():
    client = offline_client()

    result = client.metrics(CIK, ["revenue"])

    warning = result["warnings"][0]
    assert warning["code"] == "xbrl_filing_missing_from_sec_api"
    assert warning["filing"]["accession"] == H1_2026_6K
    assert warning["newest_fact_filed"] == "2026-04-28"
    assert f"no facts from 6-K {H1_2026_6K}" in warning["message"]
    assert f"edgar text {H1_2026_6K} --cik 1944902" in warning["message"]


def test_facts_warns_that_sec_api_lacks_the_newest_xbrl_filing():
    result = offline_client().company_facts(CIK)

    assert result["warnings"][0]["filing"]["accession"] == H1_2026_6K


def test_no_coverage_warning_when_sec_api_has_the_newest_filing():
    client = offline_client()
    facts = [{"accn": H1_2026_6K, "filed": "2026-09-10"}]
    assert client.xbrl_coverage_warning(CIK, facts) is None

    # The cheap check trips (this tag was not in the newest report) but
    # companyfacts does contain the filing: no warning.
    payload = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
        {"accn": H1_2026_6K, "filed": "2026-09-10", "end": "2026-06-30", "val": 1}]}}}}}
    stale_tag = [{"accn": FY2025_20F, "filed": "2026-04-28"}]
    assert client.xbrl_coverage_warning(CIK, stale_tag, company_facts=payload) is None


def test_coverage_check_makes_no_request_without_a_filing_list():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    def no_network(*args, **kwargs):
        raise AssertionError("coverage check must not fetch when told not to")

    client._get = no_network

    assert client.xbrl_coverage_warning(CIK, [], None, lookup_filings=False) is None


def test_concept_canonical_puts_fy2025_first_and_warns(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())

    result = CliRunner().invoke(main, ["concept", CIK, "revenue", "--canonical", "-j"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    first = payload["facts"][0]
    assert first["val"] == 9519777 and first["end"] == "2025-12-31"
    assert first["source_tag"] == "RevenueFromContractWithCustomerIncludingAssessedTax"
    assert payload["warnings"][0]["code"] == "xbrl_filing_missing_from_sec_api"


def test_concept_alias_picks_the_tag_with_the_freshest_period(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())

    result = CliRunner().invoke(main, ["concept", CIK, "revenue", "-j"])

    payload = json.loads(result.stdout)
    assert payload["tag"] == "RevenueFromContractWithCustomerIncludingAssessedTax"


def test_concept_as_of_does_not_warn_about_later_filings(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())

    result = CliRunner().invoke(main, ["concept", CIK, "revenue", "--as-of", "2026-01-31", "-j"])

    assert result.exit_code == 0
    assert "warnings" not in json.loads(result.stdout)


def test_metrics_cli_prints_warning_on_stderr_for_table_output(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())

    result = CliRunner().invoke(main, ["metrics", CIK, "--bundle", "revenue", "--markdown"])

    assert result.exit_code == 0
    assert "| revenue | RevenueFromContractWithCustomerIncludingAssessedTax |" in result.stdout
    assert f"Warning: SEC's XBRL API has no facts from 6-K {H1_2026_6K}" in result.stderr
