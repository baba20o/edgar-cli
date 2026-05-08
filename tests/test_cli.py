from click.testing import CliRunner

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
