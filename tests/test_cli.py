import json

from click.testing import CliRunner

from edgar.api import BULK_ARCHIVES
from edgar.cli import _add_deltas, _download_documents, _format_delta, _format_value, main


def test_help_lists_core_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "search-companies" in result.output
    assert "filings" in result.output
    assert "concept" in result.output
    assert "frame" in result.output
    assert "open" in result.output
    assert "exhibits" in result.output
    assert "compare" in result.output
    assert "brief" in result.output
    assert "earnings" in result.output
    assert "events" in result.output
    assert "metrics" in result.output


def test_bulk_urls_defaults_to_json_when_stdout_is_not_tty():
    result = CliRunner().invoke(main, ["bulk-urls"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["archives"][0]["name"] == BULK_ARCHIVES[0]["name"]


def test_bulk_urls_ndjson_streams_rows():
    result = CliRunner().invoke(main, ["bulk-urls", "--ndjson"])

    assert result.exit_code == 0
    rows = [json.loads(line) for line in result.output.splitlines()]
    assert len(rows) == len(BULK_ARCHIVES)
    assert rows[0]["url"].startswith("https://www.sec.gov/")


def test_validation_errors_use_stable_exit_code():
    result = CliRunner().invoke(
        main,
        ["exhibits", "0001045810-26-000019", "--cik", "abc; rm -rf /tmp/x"],
    )

    assert result.exit_code == 5
    assert "Not a CIK" in result.output


def test_unknown_company_uses_no_data_exit_code(monkeypatch):
    class FakeClient:
        def submissions(self, *args, **kwargs):
            return {"error": "No company found for FAKETICKER"}

    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: FakeClient())

    result = CliRunner().invoke(main, ["company", "FAKETICKER"])

    assert result.exit_code == 2
    assert "No company found" in result.output


def test_click_usage_errors_use_stable_validation_exit_code():
    result = CliRunner().invoke(main, ["concept", "AAPL", "revenue", "--annual", "--quarterly"])

    assert result.exit_code == 5
    assert "Choose only one period filter" in result.output


def test_metrics_accepts_ticker_batch(monkeypatch):
    class FakeClient:
        def metrics(self, identifier, labels):
            return {
                "identifier": identifier,
                "cik": identifier,
                "ticker": identifier,
                "name": f"{identifier} Inc.",
                "metrics": [{"metric": labels[0], "tag": "Revenues", "fact": {"val": 123}, "unit": "USD"}],
            }

    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: FakeClient())

    result = CliRunner().invoke(
        main,
        ["metrics", "--tickers", "AAPL,MSFT", "--bundle", "revenue", "--json-output"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [item["identifier"] for item in payload["results"]] == ["AAPL", "MSFT"]


def test_concept_accepts_input_file_batch(monkeypatch, tmp_path):
    input_file = tmp_path / "tickers.txt"
    input_file.write_text("AAPL\nMSFT\n")

    class FakeClient:
        def company_concept_alias(self, identifier, concept, unit=None, limit=20,
                                  period_type=None, as_of=None, since=None,
                                  canonical_union=False):
            return {
                "identifier": identifier,
                "cik": identifier,
                "name": f"{identifier} Inc.",
                "taxonomy": "us-gaap",
                "tag": concept,
                "facts": [{"val": 123, "unit": "USD", "filed": "2026-01-01"}],
            }

    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: FakeClient())

    result = CliRunner().invoke(main, ["concept", "--input", str(input_file), "revenue", "--ndjson"])

    assert result.exit_code == 0
    rows = [json.loads(line) for line in result.output.splitlines()]
    assert [row["identifier"] for row in rows] == ["AAPL", "MSFT"]
    assert all(row["tag"] == "revenue" for row in rows)


def test_format_value_abbreviates_money_and_shares():
    assert _format_value(215938000000, "USD") == "$215.94B"
    assert _format_value(2.944, "USD/shares") == "$2.94/share"
    assert _format_value(16683786, "shares") == "16.68M"


def test_add_deltas_compares_displayed_rows():
    rows = _add_deltas([
        {"val": 150, "start": "2026-01-01", "end": "2026-03-31"},
        {"val": 100, "start": "2025-01-01", "end": "2025-03-31"},
        {"val": 0, "start": "2024-01-01", "end": "2024-03-31"},
    ])

    assert rows[0]["_delta_pct"] == 50
    assert "_delta_pct" not in rows[1]
    assert _format_delta(rows[0]["_delta_pct"]) == "+50.0%"


def test_add_deltas_skips_mismatched_period_lengths():
    rows = _add_deltas([
        {"val": 100, "start": "2026-01-01", "end": "2026-03-31"},
        {"val": 400, "start": "2025-01-01", "end": "2025-12-31"},
    ])

    assert "_delta_pct" not in rows[0]
    assert rows[0]["_delta_note"] == "period mismatch"


def test_download_documents_returns_error_on_directory_failure(monkeypatch, tmp_path):
    target = tmp_path / "blocked"

    def fail_mkdir(*args, **kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr(type(target), "mkdir", fail_mkdir)

    result = _download_documents(object(), {"documents": []}, target)

    assert result["error"].startswith("Could not create download directory")


# --- foundation tranche: citations and envelope wiring at the CLI seam ---


def test_format_citation_builds_agent_quotable_string():
    from edgar.cli import _format_citation

    cite = _format_citation(
        {"accessionNumber": "0000320193-25-000079", "filingDate": "2025-10-31",
         "form": "10-K", "fiscal_period": "FY2025"},
        ticker="AAPL",
    )
    assert "AAPL" in cite
    assert "FY2025" in cite
    assert "10-K" in cite
    assert "0000320193-25-000079" in cite
    assert "filed 2025-10-31" in cite


def test_add_citations_walks_facts_and_filings():
    from edgar.cli import _add_citations

    result = _add_citations({
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "facts": [
            {"accn": "0000320193-25-000079", "filed": "2025-10-31", "form": "10-K",
             "fiscal_period": "FY2025"},
            {"accn": "0000320193-26-000013", "filed": "2026-05-01", "form": "10-Q",
             "fiscal_period": "Q2-FY2026"},
        ],
        "filings": [
            {"accessionNumber": "0000320193-26-000011", "filingDate": "2026-04-30",
             "form": "8-K"},
        ],
    })

    assert "AAPL" in result["facts"][0]["citation"]
    assert "FY2025" in result["facts"][0]["citation"]
    assert "Q2-FY2026" in result["facts"][1]["citation"]
    assert "8-K" in result["filings"][0]["citation"]


def test_add_citations_handles_compare_and_metrics_shapes():
    from edgar.cli import _add_citations

    result = _add_citations({
        "companies": [{"ticker": "AAPL", "name": "Apple", "facts": [
            {"accn": "x-1", "filed": "2026-05-01", "form": "10-Q", "fiscal_period": "Q1"},
        ]}],
        "metrics": [
            {"metric": "revenue", "fact": {"accn": "x-2", "filed": "2026-02-25",
                                           "form": "10-K", "fiscal_period": "FY2026"}},
        ],
        "ticker": "NVDA",
        "name": "NVIDIA",
    })

    assert "AAPL" in result["companies"][0]["facts"][0]["citation"]
    assert "NVDA" in result["metrics"][0]["fact"]["citation"]
    assert "FY2026" in result["metrics"][0]["fact"]["citation"]


def test_finalize_passes_through_when_client_lacks_envelope():
    from edgar.cli import _finalize

    class Stub:
        pass

    out = _finalize(Stub(), {"x": 1}, cite=False)
    assert out == {"x": 1}


def test_finalize_calls_envelope_when_available():
    from edgar.cli import _finalize

    captured = {}

    class Client:
        def _envelope(self, data):
            captured["called"] = True
            return {**data, "schema_version": "1.0.0"}

    out = _finalize(Client(), {"x": 1})
    assert captured["called"] is True
    assert out["schema_version"] == "1.0.0"


# --- new commands wired into CLI surface ---


def test_help_includes_new_commands():
    result = CliRunner().invoke(main, ["--help"])
    for cmd in ("ttm", "ratios", "trend", "growth", "reconstruct", "audit-trail",
                "amendments", "delta", "subscribe", "pending", "mark-seen",
                "tags", "frames", "dei", "peers", "concept-info", "resolve",
                "diff", "schema", "cache"):
        assert cmd in result.output, f"missing {cmd}"


def test_schema_command_lists_known_commands():
    result = CliRunner().invoke(main, ["schema"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "concept" in payload["available"]
    assert "ratios" in payload["available"]


def test_schema_command_returns_concept_schema():
    result = CliRunner().invoke(main, ["schema", "concept"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["type"] == "object"
    assert "facts" in payload["properties"]


def test_schema_command_unknown_returns_exit_2():
    result = CliRunner().invoke(main, ["schema", "no-such-command"])
    assert result.exit_code == 2


def test_invalid_as_of_date_rejected_with_validation_exit_code():
    result = CliRunner().invoke(main, ["concept", "AAPL", "revenue",
                                        "--as-of", "not-a-date", "--limit", "1"])
    assert result.exit_code == 5
    assert "not a valid YYYY-MM-DD" in result.output


def test_invalid_start_date_rejected():
    result = CliRunner().invoke(main, ["filings", "AAPL", "--start-date", "garbage"])
    assert result.exit_code == 5


def test_negative_limit_rejected():
    result = CliRunner().invoke(main, ["search-companies", "AAPL", "--limit", "-1"])
    assert result.exit_code == 5


def test_negative_periods_rejected():
    result = CliRunner().invoke(main, ["trend", "AAPL", "-c", "revenue", "--periods", "-3"])
    assert result.exit_code == 5


def test_filings_json_keeps_envelope(monkeypatch):
    class FakeClient:
        edgar_cache = None
        _cache_calls: list = []
        def submissions(self, *args, **kwargs):
            return {"cik": "0000320193", "name": "Apple Inc.",
                    "filings": [{"filingDate": "2025-10-31", "form": "10-K"}],
                    "warning": ""}
        def _envelope(self, data):
            return {**data, "schema_version": "1.0.0", "cli_version": "0.1.0",
                    "cache": {"calls": 1, "hits": 0, "misses": 1}}

    monkeypatch.setattr("edgar.cli.get_client",
                        lambda use_cache=True, cache_max_mb=None: FakeClient())
    result = CliRunner().invoke(main, ["filings", "AAPL", "--limit", "1", "--json-output"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload.get("schema_version") == "1.0.0"
    assert "cache" in payload
    assert payload.get("filings")


def test_help_includes_new_v2_commands():
    result = CliRunner().invoke(main, ["--help"])
    for cmd in ("mirror", "search", "statements", "quality", "verify", "dashboard",
                "insiders"):
        assert cmd in result.output, f"missing {cmd}"


def test_export_csv_writes_facts(tmp_path, monkeypatch):
    """End-to-end test that --export-csv flushes the primary tabular slice."""
    csv_path = tmp_path / "out.csv"

    class FakeClient:
        edgar_cache = None
        _cache_calls: list = []
        def company_concept_alias(self, identifier, concept, **kwargs):
            return {
                "cik": "1", "name": "Test", "tag": "Revenues",
                "facts": [
                    {"end": "2025-12-31", "val": 100, "filed": "2026-01-01"},
                    {"end": "2024-12-31", "val": 90, "filed": "2025-01-01"},
                ],
            }
        def _envelope(self, d):
            return d

    monkeypatch.setattr("edgar.cli.get_client",
                        lambda use_cache=True, cache_max_mb=None: FakeClient())
    result = CliRunner().invoke(
        main,
        ["--export-csv", str(csv_path), "concept", "TEST", "revenue", "--json-output"],
    )
    assert result.exit_code == 0
    text = csv_path.read_text(encoding="utf-8")
    assert "end,val,filed" in text or "end" in text.split("\n")[0]
    assert "100" in text and "90" in text


def test_cache_stats_honors_global_cache_max_mb():
    result = CliRunner().invoke(main, ["--cache-max-mb", "5", "cache", "stats", "--json-output"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["max_bytes"] == 5 * 1024 * 1024


def test_schema_accepts_output_options():
    result = CliRunner().invoke(main, ["schema", "concept", "--json-output"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "properties" in payload


def test_metrics_bundle_groups_expand(monkeypatch):
    captured = {}

    class FakeClient:
        edgar_cache = None
        _cache_calls: list = []
        def metrics(self, ident, labels):
            captured["labels"] = labels
            return {"identifier": ident, "cik": "1", "name": "x", "metrics": []}
        def _envelope(self, d):
            return d

    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: FakeClient())
    runner = CliRunner()
    result = runner.invoke(main, ["metrics", "AAPL", "--bundle", "income-statement"])
    assert result.exit_code == 0
    assert "revenue" in captured["labels"]
    assert "operating_income" in captured["labels"]
