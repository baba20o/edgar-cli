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

    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True: FakeClient())

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

    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True: FakeClient())

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
        def company_concept_alias(self, identifier, concept, unit=None, limit=20, period_type=None):
            return {
                "identifier": identifier,
                "cik": identifier,
                "name": f"{identifier} Inc.",
                "taxonomy": "us-gaap",
                "tag": concept,
                "facts": [{"val": 123, "unit": "USD", "filed": "2026-01-01"}],
            }

    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True: FakeClient())

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
