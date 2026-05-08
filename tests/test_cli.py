from click.testing import CliRunner

from edgar.cli import main


def test_help_lists_core_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "search-companies" in result.output
    assert "filings" in result.output
    assert "concept" in result.output
    assert "frame" in result.output
