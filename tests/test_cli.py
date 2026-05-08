from click.testing import CliRunner

from edgar.cli import _format_value, main


def test_help_lists_core_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "search-companies" in result.output
    assert "filings" in result.output
    assert "concept" in result.output
    assert "frame" in result.output


def test_format_value_abbreviates_money_and_shares():
    assert _format_value(215938000000, "USD") == "$215.94B"
    assert _format_value(2.944, "USD/shares") == "$2.94/share"
    assert _format_value(16683786, "shares") == "16.68M"
