"""Offline end-to-end checks for the bounded research commands.

Filing prose comes from the provenance-bearing handoff fixtures. The acceptance
times, ownership XML, and companyfacts below are intentionally synthetic test
data; they establish boundary behavior, not historical issuer measurements.
"""
from collections import Counter
from html import escape
import json
from pathlib import Path
import threading
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
import requests
from click.testing import CliRunner

from edgar.api import EdgarClient
from edgar.cache import EdgarCache
from edgar.cli import main
from edgar.point_in_time import rows_columns


FIXTURES = Path(__file__).parent / "fixtures" / "handoff"
CIK = "0001692415"
MORNING = "2026-11-10T11:30:00Z"


def source_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def html_paragraphs(paragraphs):
    return "<html><body>" + "".join(f"<p>{escape(p)}</p>" for p in paragraphs) + "</body></html>"


def filing_row(accession, form, filed, document, *, accepted=None, file_number="001-TEST"):
    return {"accessionNumber": accession, "form": form, "filingDate": filed,
            "acceptanceDateTime": accepted or filed + "T12:00:00Z",
            "primaryDocument": document, "fileNumber": file_number,
            "items": "1.01,3.02" if form == "8-K" else "", "isXBRL": int(form == "10-Q"),
            "reportDate": "2026-09-30" if form == "10-Q" else ""}


def doc_url(row, cik=CIK):
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{row['accessionNumber'].replace('-', '')}/{row['primaryDocument']}")


class FakeSEC:
    """An HTTP boundary fake shared by copied sessions in composite workers."""
    def __init__(self, rows, documents, facts=None, cik=CIK, ticker="CODX"):
        self.rows, self.documents, self.cik = rows, documents, cik
        self.facts = facts or {"cik": int(cik), "facts": {}}
        self.ticker = ticker
        self.calls = []
        self.lock = threading.Lock()
        self.statuses = {}

    def get(self, url, **kwargs):
        with self.lock:
            self.calls.append(url)
        path = urlsplit(url).path
        status = self.statuses.get(url, 200)
        if path.endswith("company_tickers_exchange.json"):
            data = {"fields": ["cik", "name", "ticker", "exchange"],
                    "data": [[int(self.cik), "Fixture Issuer", self.ticker, "Nasdaq"]]}
        elif path == f"/submissions/CIK{self.cik}.json":
            data = {"cik": self.cik, "name": "Fixture Issuer", "tickers": [self.ticker],
                    "exchanges": ["Nasdaq"], "stateOfIncorporation": "UT",
                    "stateOfIncorporationDescription": "UTAH", "sic": "3826",
                    "sicDescription": "Laboratory instruments", "fiscalYearEnd": "1231",
                    "filings": {"recent": rows_columns(self.rows), "files": []}}
        elif path == f"/api/xbrl/companyfacts/CIK{self.cik}.json":
            data = self.facts
        elif "/companyconcept/" in path:
            taxonomy, filename = path.rsplit("/", 2)[-2:]
            data = self.facts["facts"].get(taxonomy, {}).get(filename[:-5])
            if data is None:
                status, data = 404, {"error": "No synthetic concept fixture"}
        elif url in self.documents:
            data = self.documents[url]
        else:
            raise AssertionError(f"Unexpected SEC request: {url}")
        response = requests.Response()
        response.status_code, response.url, response.encoding = status, url, "utf-8"
        response._content = (data if isinstance(data, str) else json.dumps(data)).encode()
        return response


@pytest.fixture
def sec_client(tmp_path, monkeypatch):
    fixtures = [("codx-20260727.json", "2026-07-27", "333-OLD"),
                ("codx-20261013.json", "2026-10-13", "001-TEST"),
                ("codx-20261104-shelf.json", "2026-11-04", "333-NEW"),
                ("codx-20261110-periodic.json", "2026-11-10", "001-TEST")]
    rows, documents, sources = [], {}, {}
    for name, filed, number in fixtures:
        source = source_fixture(name)
        row = filing_row(source["accession"], source["form"], filed,
                         source["source_url"].rsplit("/", 1)[1], file_number=number)
        if source["form"] == "10-Q":
            row["acceptanceDateTime"] = "2026-11-10T20:05:00Z"
        rows.append(row)
        documents[doc_url(row)] = html_paragraphs(source["paragraphs"])
        sources[source["form"]] = row
    old = filing_row("0000000001-26-000010", "10-Q", "2026-08-10", "older.htm")
    old["reportDate"] = "2026-06-30"
    rows.append(old)
    documents[doc_url(old)] = html_paragraphs([
        "The exercise price of outstanding warrants was reduced from $5.40 to $3.19."])
    # Same economic positions may appear in more than one beneficial-owner row.
    owner = filing_row("0000000001-26-000011", "SCHEDULE 13D", "2026-11-09",
                       "xslSCHEDULE_13D_X01/primary_doc.xml")
    rows.append(owner)
    raw_owner_url = doc_url(owner).replace("/xslSCHEDULE_13D_X01/", "/")
    documents[raw_owner_url] = """<edgarSubmission><securitiesClassTitle>Common stock</securitiesClassTitle>
      <dateOfEvent>11/06/2026</dateOfEvent><reportingPersons>
      <reportingPersonInfo><reportingPersonName>Fund A</reportingPersonName><reportingPersonCIK>101</reportingPersonCIK>
      <aggregateAmountOwned>200</aggregateAmountOwned><percentOfClass>20</percentOfClass><sharedVotingPower>200</sharedVotingPower></reportingPersonInfo>
      <reportingPersonInfo><reportingPersonName>Manager A</reportingPersonName><reportingPersonCIK>102</reportingPersonCIK>
      <aggregateAmountOwned>200</aggregateAmountOwned><percentOfClass>20</percentOfClass><sharedVotingPower>200</sharedVotingPower></reportingPersonInfo>
      </reportingPersons></edgarSubmission>"""
    rows.sort(key=lambda row: row["acceptanceDateTime"], reverse=True)
    facts = {"cik": int(CIK), "entityName": "Fixture Issuer", "facts": {"us-gaap": {}, "dei": {}}}
    latest = sources["10-Q"]
    def fact(value, row, end, start=None):
        result = {"val": value, "filed": row["filingDate"], "accn": row["accessionNumber"],
                  "form": "10-Q", "end": end, "fy": 2026, "fp": "Q3"}
        if start:
            result["start"] = start
        return result
    for tag, old_val, new_val in [("CashAndCashEquivalentsAtCarryingValue", 1_000_000, 714_803),
                                  ("AssetsCurrent", 2_000_000, 1_000_000),
                                  ("LiabilitiesCurrent", 1_000_000, 1_573_722),
                                  ("LongTermDebtNoncurrent", 0, 0),
                                  ("LongTermDebtCurrent", 0, 0),
                                  ("NetCashProvidedByUsedInOperatingActivities", -1_000_000, -20_683_301)]:
        start = "2026-01-01" if "OperatingActivities" in tag else None
        facts["facts"]["us-gaap"][tag] = {"label": tag, "units": {"USD": [
            fact(old_val, old, "2026-06-30", start), fact(new_val, latest, "2026-09-30", start)]}}
    for tag, unit, old_val, new_val in [("EntityCommonStockSharesOutstanding", "shares", 2_000_000, 10_512_190),
                                       ("EntityPublicFloat", "USD", 10_000_000, 49_425_570)]:
        facts["facts"]["dei"][tag] = {"label": tag, "units": {unit: [
            fact(old_val, old, "2026-06-30"), fact(new_val, latest, "2026-11-06")]}}
    fake = FakeSEC(rows, documents, facts)
    client = EdgarClient(edgar_cache=EdgarCache(cache_dir=str(tmp_path)), rate_limiter=None)
    limiter = Mock()
    client.rate_limiter, client.max_retries = limiter, 0
    monkeypatch.setattr(requests.sessions.Session, "get", lambda session, url, **kwargs: fake.get(url, **kwargs))
    return client, fake, sources, limiter


def test_forensics_cold_shares_fetches_and_warm_needs_no_network(sec_client):
    client, fake, sources, limiter = sec_client
    result = client.forensics("CODX", include_periodic=True, include_shelf=True)
    assert result["coverage"]["section_errors"] == {}
    counts = Counter(fake.calls)
    assert counts[doc_url(sources["8-K"])] == 1  # Events and dilution share this document.
    assert counts[f"https://data.sec.gov/submissions/CIK{CIK}.json"] == 1
    assert counts[f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"] == 1
    assert not any("companyconcept" in url for url in counts)
    assert max(counts.values()) == 1
    assert limiter.acquire.call_count == len(fake.calls)
    assert result["financial_distress"]["cash_usd"]["value"] == 714_803
    assert result["performance"]["elapsed_ms"] >= 0
    before = len(fake.calls)
    client._cache_calls.clear()
    second = client.forensics("CODX", include_periodic=True, include_shelf=True)
    assert len(fake.calls) == before
    assert second["performance"]["cache"]["misses"] == 0
    assert second["performance"]["cache"]["hits"] > 0
    assert second["financial_distress"] == result["financial_distress"]
    assert client.rate_limiter is limiter
    assert client._request_memo is client._facts_snapshot is None


def test_forensics_cutoff_reaches_workers_facts_and_documents(sec_client):
    client, fake, sources, _ = sec_client
    morning = client.forensics("CODX", as_of=MORNING, include_periodic=True, include_shelf=True)
    assert morning["as_of"] == MORNING
    assert morning["coverage"]["section_errors"] == {}
    assert morning["financial_distress"]["cash_usd"]["value"] == 1_000_000
    assert morning["share_supply"]["entity_public_float"]["val"] == 10_000_000
    assert doc_url(sources["10-Q"]) not in fake.calls
    assert sources["10-Q"]["accessionNumber"] not in json.dumps(morning)
    # A later call must see raw cached facts, never a poisoned filtered snapshot.
    evening = client.forensics("CODX", as_of="2026-11-10", include_periodic=True)
    assert evening["financial_distress"]["cash_usd"]["value"] == 714_803
    assert evening["share_supply"]["entity_public_float"]["val"] == 49_425_570
    assert doc_url(sources["10-Q"]) in fake.calls
    assert client._pit_cutoff.get() is None


def test_periodic_and_shelf_reads_are_opt_in_and_return_provenance(sec_client):
    client, fake, sources, _ = sec_client
    default = client.dilution("CODX")
    assert not {"10-Q", "S-3"} & {row["form"] for row in default["filings_read"]}
    assert doc_url(sources["10-Q"]) not in fake.calls
    assert doc_url(sources["S-3"]) not in fake.calls
    expanded = client.dilution("CODX", include_periodic=True, include_shelf=True)
    read = {row["form"]: row for row in expanded["filings_read"]}
    assert read["10-Q"]["scan_scope"].startswith("periodic")
    assert read["S-3"]["scan_scope"] == "registration cover: first 60 sentences"
    shelf = [row for row in expanded["shelf_capacity"] if row["accession"] == sources["S-3"]["accessionNumber"]]
    assert shelf and any("39,594,495" in row["sentence"] for row in shelf)
    for row in shelf:
        assert row["source_url"] == doc_url(sources["S-3"])
        assert row["acceptance_datetime"] == sources["S-3"]["acceptanceDateTime"]
        assert all(figure["text"] in row["sentence"] for figure in row["figures"])
    old = client.dilution("CODX", include_periodic=True, as_of=MORNING)
    assert any("$3.19" in row["sentence"] for row in old["warrants"])


def test_forensics_branch_exception_is_visible_and_state_restored(sec_client, monkeypatch):
    client, _, _, limiter = sec_client
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic metrics failure")
    monkeypatch.setattr(client, "metrics", broken)
    result = client.forensics("CODX")
    assert result["coverage"]["section_errors"] == {"metrics": "synthetic metrics failure"}
    assert any(row["code"] == "section_unavailable" and row["section"] == "metrics" for row in result["warnings"])
    assert result["financial_distress"]["cash_usd"]["value"] is None
    assert result["financial_distress"]["cash_usd"]["missing_inputs"]
    assert client.rate_limiter is limiter
    assert client._request_memo is client._facts_snapshot is None


def test_document_failure_remains_visible_in_composite_coverage(sec_client):
    client, fake, sources, _ = sec_client
    fake.statuses[doc_url(sources["8-K"])] = 403
    result = client.forensics("CODX")
    failed = [row for row in result["coverage"]["filings_read"] if "error" in row]
    assert any(row["accession"] == sources["8-K"]["accessionNumber"] for row in failed)
    events = result["recent_catalysts_and_risks"]
    assert any(row.get("fetch_error") for row in events)
    assert result["coverage"]["document_failures"]
    assert result["coverage"]["event_document_failures"]
    assert any(row["code"] == "document_coverage_incomplete" for row in result["warnings"])


@pytest.mark.parametrize("command", ["float", "forensics"])
@pytest.mark.parametrize("option", ["-j", "--ndjson", "--markdown"])
def test_research_cli_full_output_and_registered_schema(sec_client, monkeypatch, command, option):
    client, _, _, _ = sec_client
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: client)
    runner = CliRunner()
    result = runner.invoke(main, [command, "CODX", "--as-of", MORNING, "--include-periodic", "--include-shelf", option])
    assert result.exit_code == 0, result.output
    rendered = result.output
    if option == "--markdown":
        assert rendered.startswith("```json\n") and rendered.rstrip().endswith("```")
        rendered = rendered.split("\n", 1)[1].rsplit("```", 1)[0]
    if option == "--ndjson":
        assert len(rendered.strip().splitlines()) == 1
    payload = json.loads(rendered)
    schema_result = runner.invoke(main, ["schema", command, "-j"])
    assert schema_result.exit_code == 0
    schema = json.loads(schema_result.output)
    assert not schema.get("coarse", False)
    assert set(schema["required"]) <= payload.keys()
    assert payload["schema_version"] == "1.3.0"
    assert payload["as_of"] == MORNING
    assert payload["cache"]["calls"] > 0
    share_supply = payload if command == "float" else payload["share_supply"]
    assert len(share_supply["beneficial_owners"]) == 2
    assert share_supply["ownership_coverage"]["filings_read"]
    assert all(row["reported_date"] == "2026-11-06" and row["reported_date_text"] == "11/06/2026"
               for row in share_supply["beneficial_owners"])


@pytest.mark.parametrize("command", ["float", "forensics"])
def test_research_cli_validation_and_missing_company_are_explicit(sec_client, monkeypatch, command):
    client, _, _, _ = sec_client
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: client)
    runner = CliRunner()
    bad_date = runner.invoke(main, [command, "CODX", "--as-of", "2026-11-10T11:30:00"])
    assert bad_date.exit_code == 5
    bad_price = runner.invoke(main, [command, "CODX", "--price", "-1"])
    assert bad_price.exit_code == 5
    missing = runner.invoke(main, [command, "NO_SUCH_FIXTURE", "-j"])
    assert missing.exit_code == 2 and "No company found" in missing.output


def test_new_client_can_reuse_all_warm_composite_documents(sec_client):
    client, fake, _, _ = sec_client
    first = client.forensics("CODX", include_periodic=True, include_shelf=True)
    before = len(fake.calls)
    fresh = EdgarClient(edgar_cache=EdgarCache(cache_dir=str(client.edgar_cache._dir)), rate_limiter=None)
    fresh.max_retries = 0
    second = fresh.forensics("CODX", include_periodic=True, include_shelf=True)
    assert len(fake.calls) == before
    assert second["performance"]["cache"]["misses"] == 0
    assert first["financial_distress"] == second["financial_distress"]
    assert first["share_supply"] == second["share_supply"]


def test_forensics_performance_cache_is_per_call_without_manual_reset(sec_client):
    client, _, _, _ = sec_client
    first = client.forensics("CODX")
    second = client.forensics("CODX")
    assert first["performance"]["cache"]["misses"] > 0
    assert second["performance"]["cache"]["misses"] == 0


def test_no_cache_composite_bypasses_existing_persistent_entries(sec_client):
    client, fake, sources, _ = sec_client
    client.forensics("CODX", include_periodic=True, include_shelf=True)
    before = len(fake.calls)
    uncached = EdgarClient(edgar_cache=client.edgar_cache, use_cache=False, rate_limiter=None)
    uncached.max_retries = 0
    result = uncached.forensics("CODX", include_periodic=True, include_shelf=True)
    assert result["coverage"]["section_errors"] == {}
    assert len(fake.calls) > before
    assert doc_url(sources["8-K"]) in fake.calls[before:]
    assert result["performance"]["cache"]["misses"] > 0


def test_forensics_runs_four_independent_branches_concurrently(sec_client, monkeypatch):
    client, _, _, _ = sec_client
    entered = []
    barrier = threading.Barrier(4, timeout=10)
    def branch(name, result):
        def run(self, *args, **kwargs):
            entered.append((name, threading.get_ident(), id(self.session)))
            barrier.wait()
            return result
        return run
    for method, result in [("dilution", {}), ("metrics", {"metrics": []}),
                           ("events", {"events": []}), ("_ownership_snapshot", {})]:
        monkeypatch.setattr(EdgarClient, method, branch(method, result))
    result = client.forensics("CODX")
    assert result["coverage"]["section_errors"] == {}
    assert {name for name, _, _ in entered} == {"dilution", "metrics", "events", "_ownership_snapshot"}
    assert len({thread for _, thread, _ in entered}) == 4
    assert len({session for _, _, session in entered}) == 4
    assert result["coverage"]["max_workers"] == 4


def test_ownership_coverage_exposes_failed_insider_document(sec_client):
    client, fake, _, _ = sec_client
    row = filing_row("0000000001-26-000012", "4", "2026-11-10", "xslF345X05/form4.xml")
    fake.rows.insert(0, row)
    fake.documents[doc_url(row).replace("/xslF345X05/", "/")] = "<html><body>Not an ownership document</body></html>"
    result = client.float("CODX")
    assert result["ownership_coverage"]["insider_filings_fetched"] == 0
    assert result["ownership_coverage"]["insider_filings_failed"] == 1
    assert any("insider" in row.get("code", "") for row in result["warnings"])


@pytest.mark.parametrize("command, option", [("float", "--price"), ("forensics", "--price"), ("forensics", "--high-60d")])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_research_cli_rejects_nonfinite_prices(sec_client, monkeypatch, command, option, value):
    client, fake, _, _ = sec_client
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: client)
    result = CliRunner().invoke(main, [command, "CODX", option, value])
    assert result.exit_code == 5
    assert not fake.calls


def install_feed(sec_client, monkeypatch, tmp_path):
    """Use the actual Atom parser, CLI, persistent cursor and acknowledgement."""
    from edgar.feed import fetch_feed
    from edgar.state import StateStore
    client, fake, _, _ = sec_client
    template = '''<entry><title>8-K - Fixture Issuer (0001692415) (Filer)</title>
      <id>urn:tag:sec.gov,2008:accession-number=0000000001-26-{number}</id>
      <updated>2026-11-15T{stamp}Z</updated><category label="form type" term="8-K"/>
      <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1692415/000000000126{number}/0000000001-26-{number}-index.htm"/>
      <summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-11-15 Item 1.01: Agreement</summary></entry>'''
    xml = '<feed xmlns="http://www.w3.org/2005/Atom">' + template.format(number="000022", stamp="11:59:00") + template.format(number="000021", stamp="11:40:00") + '</feed>'
    def get(session, url, **kwargs):
        if urlsplit(url).path != "/cgi-bin/browse-edgar":
            return fake.get(url, **kwargs)
        fake.calls.append(url)
        response = requests.Response()
        response.status_code, response.url, response.encoding = 200, url, "utf-8"
        response._content = xml.encode()
        return response
    monkeypatch.setattr(requests.sessions.Session, "get", get)
    monkeypatch.setattr(client, "feed", lambda **kwargs: fetch_feed(client, now="2026-11-15T12:00:00Z", **kwargs))
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: client)
    store = StateStore(str(tmp_path / "feed-state.json"))
    monkeypatch.setattr("edgar.cli.StateStore", lambda: store)
    return store


@pytest.mark.parametrize("output", ["-j", "--ndjson", "--markdown"])
def test_feed_cli_acknowledges_only_delivered_rows_and_registers_schema(sec_client, monkeypatch, tmp_path, output):
    store = install_feed(sec_client, monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["feed", "--forms", "8-K", "--use-state", output])
    assert result.exit_code == 0, result.output
    cursor = store.get_feed_cursor("atom-v1:8-K")
    assert cursor["timestamp"] == "2026-11-15T12:00:00Z"
    assert cursor["seen"] == []
    assert "0001692415:0000000001-26-000022" in cursor["recent_seen"]
    schema = json.loads(runner.invoke(main, ["schema", "feed", "-j"]).output)
    assert not schema.get("coarse", False)
    if output == "-j":
        payload = json.loads(result.output)
        assert set(schema["required"]) <= payload.keys()
        assert payload["coverage"]["complete"]
        assert payload["schema_version"] == "1.3.0"
        row = payload["filings"][0]
    elif output == "--ndjson":
        assert len(result.output.strip().splitlines()) == 1
        row = json.loads(result.output)
    else:
        assert "CODX" in result.output
        row = None
    if row:
        assert {"accession", "cik", "ticker", "exchange", "form", "items", "acceptance_datetime", "filing_url"} <= row.keys()
        assert row["ticker"] == "CODX" and row["items"] == "1.01"
    second = runner.invoke(main, ["feed", "--forms", "8-K", "--use-state", "-j"])
    assert second.exit_code == 2
    assert json.loads(second.stdout)["filings"] == []


def test_feed_cli_does_not_advance_state_after_output_failure(sec_client, monkeypatch, tmp_path):
    store = install_feed(sec_client, monkeypatch, tmp_path)
    def fail_output(payload):
        raise OSError("synthetic output failure")
    monkeypatch.setattr("edgar.cli._json", fail_output)
    result = CliRunner().invoke(main, ["feed", "--forms", "8-K", "--use-state", "-j"])
    assert result.exit_code != 0
    assert isinstance(result.exception, OSError)
    assert store.get_feed_cursor("atom-v1:8-K") is None


def test_high_60d_scenario_is_identical_across_composite_sections(sec_client):
    client, _, _, _ = sec_client
    result = client.forensics("CODX", price=2.10, high_60d=3.25)
    baby = result["shelves_and_capacity"]["baby_shelf"]
    assert baby == result["share_supply"]["baby_shelf"]
    assert baby["reference_price"]["value"] == 3.25
    for warning in baby["warnings"]:
        assert result["warnings"].count(warning) == 1


def test_feed_reset_requires_state_without_fetching(sec_client, monkeypatch):
    client, fake, _, _ = sec_client
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: client)
    result = CliRunner().invoke(main, ["feed", "--reset-state", "-j"])
    assert result.exit_code == 5
    assert "requires --use-state" in result.output
    assert not fake.calls


def test_feed_explicit_reset_recovers_selected_scope_without_erasing_other_state(sec_client, monkeypatch, tmp_path):
    store = install_feed(sec_client, monkeypatch, tmp_path)
    store.update_feed_cursor("atom-v1:8-K", "2026-11-14T12:00:00Z", [])
    store.update_feed_cursor("atom-v1:RW", "2026-11-14T12:00:00Z", [])
    runner = CliRunner()
    incomplete = runner.invoke(main, ["feed", "--forms", "8-K", "--use-state", "-j"])
    assert not json.loads(incomplete.stdout)["coverage"]["complete"]
    result = runner.invoke(main, ["feed", "--forms", "8-K", "--use-state", "--reset-state", "-j"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["coverage"]["complete"]
    assert data["state_reset"]["cursor_reset"]
    assert any(w["code"] == "feed_cursor_reset" for w in data["warnings"])
    assert store.get_feed_cursor("atom-v1:8-K")["timestamp"] == "2026-11-15T12:00:00Z"
    assert store.get_feed_cursor("atom-v1:RW")["timestamp"] == "2026-11-14T12:00:00Z"
