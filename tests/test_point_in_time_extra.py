"""Independent replay regressions.

Synthetic boundary metadata is explicitly separated from the saved SEC SBET
submissions and document excerpts used by the historical replay test.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from edgar.api import EdgarClient
from edgar.cli import main
from edgar.point_in_time import acceptance_time, available, parse_cutoff, rows_columns


CIK = "0000000001"
CUTOFF = "2026-07-01T11:30:00Z"
FIXTURES = Path(__file__).parent / "fixtures" / "handoff"


def _row(number, *, accepted, filed="2026-07-01", form="8-K", document=None):
    return {"accessionNumber": f"0000000001-26-{number:06d}", "form": form,
            "filingDate": filed, "acceptanceDateTime": accepted,
            "primaryDocument": document or f"document-{number}.htm",
            "items": "1.01,3.02" if form == "8-K" else "",
            "reportDate": "2025-12-31" if form == "10-K" else "",
            "isXBRL": int(form == "10-K")}


EARLY = _row(1, accepted="2026-07-01T11:00:00Z")
LATE = _row(2, accepted="2026-07-01T20:05:00Z")
EARLY_ANNUAL = _row(3, accepted="2026-07-01T11:00:00Z", form="10-K")
LATE_ANNUAL = _row(4, accepted="2026-07-01T20:05:00Z", form="10-K")


def _make_client(rows=None, *, cik=CIK, history=None, facts=None, documents=None):
    """Fixture API boundary; every unmapped document request is recorded."""
    rows = deepcopy(rows if rows is not None else [LATE, LATE_ANNUAL, EARLY, EARLY_ANNUAL])
    c = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    c.resolve_company = lambda _: {"cik": cik, "name": "Replay fixture", "ticker": "FIX"}
    history = history or {}
    payload = {"cik": cik, "name": "Replay fixture", "fiscalYearEnd": "1231",
               "filings": {"recent": rows_columns(rows), "files": [entry for entry, _ in history.values()]}}
    if facts is None:
        facts = [{"val": 100 if row["accessionNumber"] == EARLY_ANNUAL["accessionNumber"] else 999,
                  "start": "2025-01-01", "end": "2025-12-31", "filed": row["filingDate"],
                  "accn": row["accessionNumber"], "form": "10-K", "fy": 2025, "fp": "FY"}
                 for row in rows if row["form"] == "10-K"]
    concept = {"label": "Revenue", "units": {"USD": deepcopy(facts)}}
    calls, reads = [], []

    def raw(path, **kwargs):
        calls.append(path)
        if path == f"/submissions/CIK{cik}.json":
            return deepcopy(payload)
        if path in history:
            return deepcopy(history[path][1])
        if path.endswith("/Revenues.json"):
            return deepcopy(concept)
        if "/companyfacts/" in path:
            return {"cik": cik, "entityName": "Replay fixture", "facts": {"us-gaap": {"Revenues": deepcopy(concept)}}}
        return {"error": "404: no synthetic fixture for " + path}

    def text(url):
        reads.append(url)
        if documents is not None:
            assert url in documents, f"Document not in saved fixture mapping: {url}"
            return documents[url]
        return "<p>The Company entered into a securities purchase agreement for a private placement of 1,000,000 shares of common stock.</p>"

    c._get_uncut, c._get_text = raw, text
    return c, payload, calls, reads


def _accessions(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"accession", "accessionNumber", "accn", "superseded_by"} and isinstance(item, str):
                yield item
            yield from _accessions(item)
    elif isinstance(value, list):
        for item in value:
            yield from _accessions(item)


@pytest.mark.parametrize("value", ["2026-07-01", "bad timestamp", "2026-02-30T12:00:00Z"])
def test_non_timestamp_acceptance_never_becomes_midnight(value):
    assert acceptance_time(value) is None
    assert not available({"filingDate": "2026-07-01", "acceptanceDateTime": value}, parse_cutoff(CUTOFF))


@pytest.mark.parametrize("value", ["2026-02-30", "2026-13-01", "0000-01-01"])
def test_impossible_filing_dates_cannot_supply_availability(value):
    assert not available({"filed": value}, parse_cutoff("2027-01-01"))


def test_malformed_acceptance_for_older_fact_uses_fallback_without_crashing():
    row = _row(10, accepted="malformed", filed="2026-06-29", form="10-K")
    c, _, _, _ = _make_client([row])
    result = c.company_concept("FIX", "us-gaap", "Revenues", as_of=CUTOFF)
    assert len(result["facts"]) == 1
    assert not result["facts"][0].get("acceptance_datetime")
    assert result["facts"][0]["filed"] == "2026-06-29"


def test_acceptance_is_authoritative_when_filing_date_is_next_day():
    row = _row(10, accepted="2026-07-01T11:00:00Z", filed="2026-07-02", form="10-K")
    c, _, _, reads = _make_client([row])
    assert c.submissions("FIX", as_of=CUTOFF)["filings"][0]["accession"] == row["accessionNumber"]
    assert c.company_concept("FIX", "us-gaap", "Revenues", as_of=CUTOFF)["facts"]
    result = c.filing_text("FIX", form="10-K", as_of=CUTOFF)
    assert "error" not in result and result["filing"]["accession"] == row["accessionNumber"]
    assert len(reads) == 1


def test_future_dated_chunk_is_searched_for_earlier_acceptance():
    row = _row(10, accepted="2026-07-01T11:00:00Z", filed="2026-07-06", form="10-K")
    path = "/submissions/CIK0000000001-submissions-001.json"
    history = {path: ({"name": path.rsplit("/", 1)[1], "filingFrom": "2026-07-06", "filingTo": "2026-07-06"}, rows_columns([row]))}
    c, _, calls, _ = _make_client([], history=history)
    result = c.submissions("FIX", as_of=CUTOFF)
    assert path in calls
    assert [r["accession"] for r in result["filings"]] == [row["accessionNumber"]]


def test_chunk_failure_is_disclosed_and_cannot_enable_direct_accession_fallback():
    path = "/submissions/CIK0000000001-submissions-001.json"
    history = {path: ({"name": path.rsplit("/", 1)[1], "filingFrom": "2025-01-01"}, {"error": "fixture unavailable"})}
    c, _, _, reads = _make_client([EARLY], history=history)
    result = c.submissions("FIX", as_of=CUTOFF)
    assert any(w["code"] == "historical_chunk_unavailable" for w in result["warnings"])
    denied = c.filing_text("FIX", accession_or_url=LATE["accessionNumber"], as_of=CUTOFF)
    assert "error" in denied and denied["documents"] == []
    assert any(w["code"] == "historical_chunk_unavailable" for w in denied["warnings"])
    assert reads == []


@pytest.mark.parametrize("command,args", [
    ("company", []), ("filings", []), ("events", []),
    ("metrics", ["--bundle", "revenue"]), ("brief", []), ("dashboard", []),
    ("dilution", ["--max-supplements", "0"]), ("text", ["--form", "8-K"]),
    ("float", ["--max-ownership-filings", "0", "--max-supplements", "0"]),
    ("forensics", ["--max-ownership-filings", "0", "--max-supplements", "0"]),
])
def test_requested_cli_views_filter_underlying_reads_and_echo_cutoff(monkeypatch, command, args):
    c, _, _, reads = _make_client()
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: c)
    result = CliRunner().invoke(main, [command, "FIX", *args, "--as-of", "2026-07-01T07:30:00-04:00", "-j"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["as_of"] == CUTOFF
    assert "error" not in payload
    assert set(_accessions(payload)).isdisjoint({LATE["accessionNumber"], LATE_ANNUAL["accessionNumber"]})
    assert all(LATE["accessionNumber"].replace("-", "") not in url for url in reads)
    assert all(LATE_ANNUAL["accessionNumber"].replace("-", "") not in url for url in reads)
    assert c._pit_cutoff.get() is None


def test_insider_selection_excludes_later_owner_report(monkeypatch):
    before = _row(20, accepted="2026-07-01T11:00:00Z", form="3", document="early.xml")
    after = _row(21, accepted="2026-07-01T20:05:00Z", form="3", document="late.xml")
    c, _, _, reads = _make_client([after, before])
    xml = """<ownershipDocument><documentType>3</documentType><periodOfReport>2026-06-30</periodOfReport>
      <reportingOwner><reportingOwnerId><rptOwnerCik>5</rptOwnerCik><rptOwnerName>Officer</rptOwnerName></reportingOwnerId>
      <reportingOwnerRelationship><isOfficer>1</isOfficer></reportingOwnerRelationship></reportingOwner>
      <nonDerivativeTable><nonDerivativeHolding><securityTitle><value>Common Stock</value></securityTitle>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>100</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
      </nonDerivativeHolding></nonDerivativeTable></ownershipDocument>"""
    def text(url):
        reads.append(url)
        return xml
    c._get_text = text
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: c)
    result = CliRunner().invoke(main, ["insiders", "FIX", "--as-of", CUTOFF, "-j"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["as_of"] == CUTOFF
    assert len(payload["initial_statements"]) == 1
    assert payload["initial_statements"][0]["accession"] == before["accessionNumber"]
    assert len(reads) == 1 and reads[0].endswith("early.xml")


@pytest.mark.parametrize("command,args", [("metrics", ["--bundle", "revenue"]), ("brief", []), ("concept", ["revenue"])])
def test_batch_envelope_echoes_normalized_cutoff(monkeypatch, command, args):
    c, _, _, _ = _make_client()
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: c)
    result = CliRunner().invoke(main, [command, *args, "--tickers", "FIX,FIX2", "--as-of", "2026-07-01T07:30:00-04:00", "-j"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["as_of"] == CUTOFF
    assert len(payload["results"]) == 2
    assert all(r["as_of"] == CUTOFF for r in payload["results"])


def test_concept_explanation_cannot_leak_future_value(monkeypatch):
    c, _, _, _ = _make_client()
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: c)
    result = CliRunner().invoke(main, ["concept", "FIX", "revenue", "--as-of", CUTOFF, "--explain", "-j"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    winner = payload["_explain"]["winner"]
    assert winner["fact_count"] == 1 and winner["latest_val"] == 100
    assert {r["val"] for r in payload["facts"]} == {100}


def test_reused_client_replay_restores_unfiltered_facts_and_grid():
    c, raw, _, _ = _make_client()
    original = deepcopy(raw)
    sentinel = [("existing", "grid", 2026)]
    c._fiscal_grids[CIK] = sentinel
    replay = c.company_concept("FIX", "us-gaap", "Revenues", as_of=CUTOFF)
    assert [r["val"] for r in replay["facts"]] == [100]
    assert c._fiscal_grids[CIK] is sentinel
    assert c._pit_cutoff.get() is None
    current = c.company_concept("FIX", "us-gaap", "Revenues")
    assert {r["val"] for r in current["facts"]} == {100, 999}
    assert raw == original


def test_actual_sbet_release_dateline_does_not_backdate_sec_availability():
    submissions = json.loads((FIXTURES / "sbet-may2025-submissions.json").read_text())
    body = json.loads((FIXTURES / "sbet-20250530.json").read_text())
    release = json.loads((FIXTURES / "sbet-20250527-release-filed-20250530.json").read_text())
    assert "May 27, 2025" in release["paragraphs"][0]
    row = next(r for r in submissions["rows"] if r["accessionNumber"] == body["accession"])
    assert row["acceptanceDateTime"] == "2025-05-30T20:58:07.000Z"
    documents = {body["source_url"]: "\n\n".join(body["paragraphs"]),
                 release["source_url"]: "\n\n".join(release["paragraphs"])}
    c, _, _, reads = _make_client([row], cik=submissions["cik"].zfill(10), documents=documents)
    c.filing_documents = lambda cik, accession: {"documents": [
        {"sequence": "1", "type": "8-K", "document": "form8-k.htm", "url": body["source_url"]},
        {"sequence": "2", "type": "EX-99.1", "document": "ex99-1.htm", "url": release["source_url"]}]}
    for cutoff in ["2025-05-27T13:00:00Z", "2025-05-30T20:58:06Z"]:
        assert c.events("FIX", as_of=cutoff)["events"] == []
        denied = c.filing_text("FIX", accession_or_url=row["accessionNumber"], exhibits=["EX-99"], as_of=cutoff)
        assert "error" in denied and denied["documents"] == []
    assert reads == []
    after = c.filing_text("FIX", accession_or_url=row["accessionNumber"], exhibits=["EX-99"], as_of="2025-05-30T21:00:00Z")
    assert "error" not in after
    assert len(after["documents"]) == 2
    assert release["source_url"] in reads and body["source_url"] in reads
    assert "May 27, 2025" in json.dumps(after)


def test_intraday_fact_does_not_silently_fallback_when_submissions_lookup_failed():
    # Synthetic fact filed the prior day; its unknown acceptance could be after
    # midnight UTC (the genuine saved SBET EFFECT row demonstrates this pattern).
    fact = {"val": 5, "filed": "2026-06-30", "accn": "0000000001-26-000050",
            "end": "2026-03-31", "form": "10-Q"}
    c, _, _, _ = _make_client([], facts=[fact])
    raw = c._get_uncut
    def unavailable(path, **kwargs):
        if "/submissions/" in path:
            return {"error": "503 fixture unavailable"}
        return raw(path, **kwargs)
    c._get_uncut = unavailable
    result = c.company_concept("FIX", "us-gaap", "Revenues", as_of="2026-07-01T00:01:00Z")
    assert not result.get("facts"), "Unproven acceptance during a metadata outage must not become an available fact"
    assert result.get("error") or result.get("warnings")


def test_incomplete_history_retains_proven_facts_but_excludes_unknown_accessions():
    known = _row(60, accepted="2026-06-30T20:00:00Z", filed="2026-06-30", form="10-K")
    unknown = _row(61, accepted="2026-07-01T04:00:00Z", filed="2026-06-30", form="10-K")
    facts = [{"val": value, "filed": row["filingDate"], "accn": row["accessionNumber"],
              "start": "2025-01-01", "end": "2025-12-31", "form": "10-K"}
             for value, row in [(100, known), (999, unknown)]]
    path = "/submissions/CIK0000000001-submissions-001.json"
    history = {path: ({"name": path.rsplit("/", 1)[1], "filingFrom": "2026-06-30"}, {"error": "503 fixture unavailable"})}
    c, _, _, _ = _make_client([known], history=history, facts=facts)
    result = c.company_concept("FIX", "us-gaap", "Revenues", as_of="2026-07-01T00:01:00Z")
    assert "error" not in result
    assert {row["accn"] for row in result["facts"]} == {known["accessionNumber"]}
    assert [row["val"] for row in result["facts"]] == [100]
    assert any(w["code"] == "historical_chunk_unavailable" for w in result["warnings"])


def test_missing_acceptance_does_not_leak_previous_day_effect_into_early_utc_replay():
    submissions = json.loads((FIXTURES / "sbet-may2025-submissions.json").read_text())
    row = next(row for row in submissions["rows"] if row["form"] == "EFFECT" and row["filingDate"] == "2025-05-20")
    early = parse_cutoff("2025-05-21T01:00:00Z")
    assert not available(row, early)
    without_time = {**row, "acceptanceDateTime": ""}
    assert not available(without_time, early)
    assert available(without_time, parse_cutoff("2025-05-22T00:00:00Z"))
    assert available(row, parse_cutoff("2025-05-21T05:00:00Z"))
