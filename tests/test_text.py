"""`edgar text`: any filing as clean text, with a size cap and sentence grep."""

import json

from click.testing import CliRunner

from edgar import filing_text
from edgar.cli import main

from omh import CIK, fixture_text, offline_client

NAME_CHANGE = "0001213900-26-117590"
SEPA = "0001213900-26-107603"


def test_document_to_text_drops_wrapper_markup_and_hidden_blocks():
    html = """<DOCUMENT>
<TYPE>6-K
<SEQUENCE>1
<FILENAME>a.htm
<DESCRIPTION>REPORT
<TEXT>
<html><head><title>ignored</title><style>p{color:red}</style></head><body>
<ix:header><ix:hidden>hidden xbrl</ix:hidden></ix:header>
<p>The&nbsp;Company&#8217;s  &#8220;Shares&#8221;
continue to trade.</p>
<script>var x = 1;</script>
<table><tr><td>Class A</td><td>1,911,893</td></tr><tr><td>Class B</td><td>16,831</td></tr></table>
</body></html>
</TEXT>
</DOCUMENT>"""

    text = filing_text.document_to_text(html)

    assert text == 'The Company\'s "Shares" continue to trade.\n\nClass A 1,911,893\n\nClass B 16,831'


def test_document_to_text_keeps_plain_text_paragraphs():
    assert filing_text.document_to_text("First line\nwraps here.\n\nSecond  paragraph.") == (
        "First line wraps here.\n\nSecond paragraph."
    )


def test_split_sentences_respects_abbreviations_decimals_and_page_breaks():
    text = "\n\n".join([
        'Mr. Tan joined Ohmyhome Pte. Ltd. in 2019. The price was $1.30 per share under '
        'Listing Rule No. 5550(a)(2). It was filed with the SEC. Thereafter sales may begin.',
        "The Warrants are exercisable upon issuance and will expire five years from the",
        "12",
        "issuance date of the Warrants.",
        "Table of Contents",
    ])

    assert filing_text.split_sentences(text) == [
        "Mr. Tan joined Ohmyhome Pte. Ltd. in 2019.",
        "The price was $1.30 per share under Listing Rule No. 5550(a)(2).",
        "It was filed with the SEC.",
        "Thereafter sales may begin.",
        "The Warrants are exercisable upon issuance and will expire five years from the "
        "issuance date of the Warrants.",
    ]


def test_match_sentences_returns_context_and_marks_clipping():
    sentences = ["Alpha one.", "Beta " + "x " * 400 + "going concern doubt exists.", "Gamma three."]

    rows = filing_text.match_sentences(sentences, filing_text.compile_pattern("going concern"),
                                       context=1, max_sentence_chars=120)

    assert len(rows) == 1
    row = rows[0]
    assert row["index"] == 1 and row["match"] == "going concern"
    assert row["clipped"] is True and row["sentence"].startswith("... ")
    assert "going concern doubt exists." in row["sentence"] and len(row["sentence"]) < 140
    assert row["before"] == ["Alpha one."] and row["after"] == ["Gamma three."]


def test_filing_text_returns_main_document_by_accession_without_reading_the_index():
    client = offline_client()

    result = client.filing_text(identifier=CIK, accession_or_url=NAME_CHANGE)

    assert result["filing"] == {
        "accession": NAME_CHANGE, "form": "6-K", "filed": "2026-11-09",
        "acceptance_datetime": "2026-11-09T22:00:10.000Z", "report_date": "2026-11-09",
        "filing_url": "https://www.sec.gov/Archives/edgar/data/1944902/"
                      "000121390026117590/0001213900-26-117590-index.htm",
    }
    document = result["documents"][0]
    assert document["role"] == "main" and document["type"] == "6-K"
    assert document["text"].startswith("UNITED STATES")
    assert 'changed its name from "Ohmyhome Limited" to "Ohmyswift Limited."' in document["text"]
    assert document["truncated"] is False and document["next_offset"] is None
    assert not any("-index.htm" in url for url in client.requested)


def test_filing_text_caps_size_and_pages_with_offset():
    client = offline_client()

    first = client.filing_text(identifier=CIK, accession_or_url=SEPA, max_chars=500)["documents"][0]
    assert len(first["text"]) == 500 and first["truncated"] is True
    assert first["next_offset"] == 500 and first["length"] > 5000

    second = client.filing_text(identifier=CIK, accession_or_url=SEPA, max_chars=500,
                                offset=first["next_offset"])["documents"][0]
    full = filing_text.document_to_text(fixture_text(f"{SEPA}.htm"))
    assert first["text"] + second["text"] == full[:1000]


def test_filing_text_grep_returns_only_matching_sentences():
    client = offline_client()

    document = client.filing_text(
        identifier=CIK, accession_or_url=SEPA, pattern=r"\$40,000,000|commitment shares|lower of",
    )["documents"][0]

    assert "text" not in document
    matched = " ".join(row["sentence"] for row in document["matches"])
    assert "up to an aggregate of $40,000,000" in matched
    assert "the lower of (i) $1.26" in matched
    assert all(row["match"] for row in document["matches"])


def test_filing_text_picks_latest_filing_of_a_form():
    client = offline_client()

    latest = client.filing_text(identifier=CIK, form="6-K", pattern="Ohmyswift")
    assert latest["filing"]["accession"] == NAME_CHANGE

    earlier = client.filing_text(identifier=CIK, form="6-K", as_of="2026-10-20", max_chars=10)
    assert earlier["filing"]["accession"] == "0001213900-26-110319"

    missing = client.filing_text(identifier=CIK, form="10-K")
    assert "No matching 10-K filing found" in missing["error"]
    assert "6-K (" in missing["error"]


def test_filing_text_reads_requested_exhibits_from_the_index():
    index = """<table>
      <tr><td>1</td><td>6-K</td><td><a href="/Archives/edgar/data/1944902/000121390026107603/ea0307807-6k_ohmyhome.htm">ea0307807-6k_ohmyhome.htm</a></td><td>6-K</td><td>33005</td></tr>
      <tr><td>2</td><td>Opinion</td><td><a href="/Archives/edgar/data/1944902/000121390026107603/ex5-1.htm">ex5-1.htm</a></td><td>EX-5.1</td><td>46854</td></tr>
      <tr><td>3</td><td>Purchase agreement</td><td><a href="/Archives/edgar/data/1944902/000121390026107603/ex10-1.htm">ex10-1.htm</a></td><td>EX-10.1</td><td>160122</td></tr>
      <tr><td>4</td><td>Logo</td><td><a href="/Archives/edgar/data/1944902/000121390026107603/img1.jpg">img1.jpg</a></td><td>GRAPHIC</td><td>5288</td></tr>
      <tr><td>5</td><td>Chart</td><td><a href="/Archives/edgar/data/1944902/000121390026107603/ex99-2.pdf">ex99-2.pdf</a></td><td>EX-99.2</td><td>5288</td></tr>
      <tr><td></td><td>Complete submission text file</td><td><a href="/Archives/edgar/data/1944902/000121390026107603/0001213900-26-107603.txt">0001213900-26-107603.txt</a></td><td></td><td>427248</td></tr>
    </table>"""
    client = offline_client(extra_documents={
        "-index.htm": index,
        "ex10-1.htm": "<p>STANDBY EQUITY PURCHASE AGREEMENT dated October 7, 2026.</p>",
    })

    result = client.filing_text(identifier=CIK, accession_or_url=SEPA,
                                exhibits=["EX-10", "EX-99"], exhibits_only=True)

    assert [(d["type"], d["role"]) for d in result["documents"]] == [
        ("EX-10.1", "exhibit"), ("EX-99.2", "exhibit"),
    ]
    assert result["documents"][0]["text"] == "STANDBY EQUITY PURCHASE AGREEMENT dated October 7, 2026."
    assert result["documents"][1]["skipped"] == "not a text document"

    with_main = client.filing_text(identifier=CIK, accession_or_url=SEPA, exhibits=["all"],
                                   max_chars=20)
    assert [d["type"] for d in with_main["documents"]] == ["6-K", "EX-5.1", "EX-10.1", "EX-99.2"]


def test_filing_text_needs_a_filer_for_a_bare_accession():
    client = offline_client()

    result = client.filing_text(accession_or_url=NAME_CHANGE)

    assert "accession number alone does not name the filer" in result["error"]
    assert client.filing_text(accession_or_url=NAME_CHANGE, cik="1944902")["filing"]["form"] == "6-K"


def test_text_cli_json_ndjson_and_exit_codes(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())
    runner = CliRunner()

    as_json = runner.invoke(main, ["text", CIK, NAME_CHANGE, "--grep", "continuation", "-j"])
    assert as_json.exit_code == 0
    payload = json.loads(as_json.stdout)
    assert payload["schema_version"] and payload["pattern"] == "continuation"
    sentences = [row["sentence"] for row in payload["documents"][0]["matches"]]
    assert sentences[0] == "Name Change and Continuation to the British Virgin Islands"
    assert any(s.startswith("On November 3, 2026, pursuant to such approvals") for s in sentences)

    as_rows = runner.invoke(main, ["text", NAME_CHANGE, "--cik", "1944902",
                                   "--grep", "British Virgin Islands", "--ndjson"])
    assert as_rows.exit_code == 0
    rows = [json.loads(line) for line in as_rows.stdout.splitlines()]
    assert rows and all(row["accession"] == NAME_CHANGE and "sentence" in row for row in rows)

    bad_pattern = runner.invoke(main, ["text", CIK, NAME_CHANGE, "--grep", "(", "-j"])
    assert bad_pattern.exit_code == 5
    assert "Invalid pattern" in bad_pattern.output

    nothing = runner.invoke(main, ["text", CIK, NAME_CHANGE, "--exhibits-only", "-j"])
    assert nothing.exit_code == 2
    assert json.loads(nothing.stdout)["documents"] == []

    usage = runner.invoke(main, ["text", "OMH", "MSFT"])
    assert usage.exit_code == 5


def test_text_cli_markdown_shows_truncation_and_resume_offset(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())

    result = CliRunner().invoke(main, ["text", CIK, SEPA, "--max-chars", "300", "-m"])

    assert result.exit_code == 0
    assert "# Ohmyhome Ltd · 6-K · 0001213900-26-107603 · filed 2026-10-07" in result.output
    assert "continue with --offset 300" in result.output


def test_schema_lists_text_command():
    result = CliRunner().invoke(main, ["schema", "text"])

    assert result.exit_code == 0
    assert json.loads(result.output)["primary_row_key"] == "documents"
