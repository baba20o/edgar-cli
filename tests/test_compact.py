"""`--compact`: filing rows without duplicate camelCase fields or empty values."""

import json

from click.testing import CliRunner

from edgar.api import compact_filing_row, compact_result
from edgar.cli import main

from omh import CIK, offline_client


def test_compact_filing_row_drops_duplicates_and_empties_only():
    row = {
        "accessionNumber": "0001213900-26-117590", "accession": "0001213900-26-117590",
        "filingDate": "2026-11-09", "filed": "2026-11-09",
        "form": "6-K", "items": "", "act": None, "size": 210765, "isXBRL": 0,
        "primaryDocument": "a.htm", "primary_document": "a.htm",
        "core_type": "6-K",
    }

    assert compact_filing_row(row) == {
        "accession": "0001213900-26-117590", "filed": "2026-11-09", "form": "6-K",
        "size": 210765, "isXBRL": 0, "primary_document": "a.htm", "core_type": "6-K",
    }


def test_compact_filing_row_keeps_a_camelcase_field_that_has_no_alias_on_the_row():
    assert compact_filing_row({"accessionNumber": "x", "form": "8-K"}) == {
        "accessionNumber": "x", "form": "8-K",
    }


def test_compact_result_covers_brief_and_batch_shapes():
    row = {"accessionNumber": "a", "accession": "a", "items": ""}
    brief = {"profile": {"cik": "1", "filings": [dict(row)], "files": []},
             "events": [dict(row)], "earnings": {"filing": dict(row)}, "metrics": []}

    out = compact_result({"results": [brief]})

    item = out["results"][0]
    assert out["compact"] is True and "compact" not in item
    assert item["profile"] == {"cik": "1", "filings": [{"accession": "a"}]}
    assert item["events"] == [{"accession": "a"}]
    assert item["earnings"]["filing"] == {"accession": "a"}
    assert compact_result({"error": "No company found"}) == {"error": "No company found"}


def test_company_cli_default_keeps_both_spellings_and_compact_is_smaller(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())
    runner = CliRunner()

    full = runner.invoke(main, ["company", CIK, "--limit", "5", "-j"])
    compact = runner.invoke(main, ["company", CIK, "--limit", "5", "--compact", "-j"])

    assert full.exit_code == 0 and compact.exit_code == 0
    full_row = json.loads(full.stdout)["filings"][0]
    assert full_row["accessionNumber"] == full_row["accession"]          # published 1.1.0 shape
    payload = json.loads(compact.stdout)
    row = payload["filings"][0]
    assert payload["compact"] is True and payload["schema_version"]
    assert row["accession"] == full_row["accession"] and row["filed"] == full_row["filed"]
    assert "accessionNumber" not in row and "filingDate" not in row and "items" not in row
    assert row["primary_doc_url"] == full_row["primary_doc_url"]
    assert len(compact.stdout) < 0.75 * len(full.stdout)


def test_compact_does_not_blank_the_markdown_table(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())

    result = CliRunner().invoke(main, ["filings", CIK, "--limit", "1", "--compact", "-m"])

    assert result.exit_code == 0
    assert "| 2026-11-09 | 6-K | 0001213900-26-117590 |" in result.stdout
