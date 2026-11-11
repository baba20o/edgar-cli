"""`edgar dilution`: what bears on share supply, every figure sourced.

Acceptance facts come from OMH's live filings (fixtures): the October 2026
standby equity purchase agreement and private placement, the July 2026
registered direct offering and private placement, the 50-for-1 share
consolidation, and the going-concern language in the prospectus supplement.
"""

import json

from click.testing import CliRunner

from edgar import dilution, sixk
from edgar.cli import main
from edgar.filing_text import _split_paragraph, document_to_text, split_sentences

from omh import CIK, fixture_text, offline_client

SEPA_6K = "0001213900-26-107603"
SEPA_424B5 = "0001213900-26-107501"
OCT_PLACEMENT = "0001213900-26-110319"
VOTE_6K = "0001213900-26-091913"
RDO_6K = "0001213900-26-082334"
RDO_424B5 = "0001213900-26-082050"
JULY_PLACEMENT = "0001213900-26-078921"

STAND_IN = "<html><body><p>The registrant furnished a routine notice.</p></body></html>"


def _statements(accession: str) -> list[dict]:
    body = sixk.split_6k(document_to_text(fixture_text(f"{accession}.htm")))["body"]
    return dilution.extract_statements([s for block in body for s in _split_paragraph(block)])


def _figures(statement: dict, kind: str) -> list:
    return [f["value"] for f in statement["figures"] if f["type"] == kind]


def _one(statements: list[dict], topic: str, needle: str) -> dict:
    matches = [s for s in statements if s["topic"] == topic and needle in s["sentence"]]
    assert matches, f"no {topic} statement containing {needle!r}"
    return matches[0]


def test_parse_figures_keeps_the_text_each_figure_was_read_from():
    sentence = ('a private placement of (i) 3,000,000 Class A ordinary shares (the "Shares"), par value '
                "of $0.000005 per share and (ii) 3,000,000 warrants, at a combined price of $1.30 per "
                "share; gross proceeds were $3.9 million and the exercise price is $1.30 per share.")

    figures = dilution.parse_figures(sentence)

    assert figures == [
        {"type": "shares", "value": 3000000, "text": "3,000,000 Class A ordinary shares",
         "share_class": "A"},
        {"type": "warrants", "value": 3000000, "text": "3,000,000 warrants"},
        {"type": "price_per_share", "value": 1.3, "text": "$1.30 per share"},
        {"type": "dollars", "value": 3900000, "text": "$3.9 million"},
        {"type": "exercise_price", "value": 1.3, "text": "exercise price is $1.30"},
    ]
    # Every figure's text is a literal substring of the sentence; the par
    # value is not mistaken for a price.
    assert all(f["text"] in sentence for f in figures)


def test_october_private_placement_and_warrant_exchange_feature():
    statements = _statements(OCT_PLACEMENT)

    deal = _one(statements, "offering", "private placement")
    assert deal["kind"] == "private_placement"
    assert _figures(deal, "shares") == [3000000] and _figures(deal, "warrants") == [3000000]
    assert _figures(deal, "price_per_share") == [1.3]

    proceeds = _one(statements, "offering", "gross proceeds")
    assert proceeds["kind"] == "proceeds" and _figures(proceeds, "dollars") == [3900000]

    exercise = _one(statements, "warrants", "initial exercise price")
    assert _figures(exercise, "exercise_price") == [1.3]
    assert exercise["features"]["exercise_price_floor"] == 0.1

    exchange = _one(statements, "warrants", "starting on the 7th calendar day")
    assert exchange["features"]["exchange_multiple"] == 9
    assert "product of (x) nine (9)" in exchange["sentence"]


def test_july_private_placement_has_the_same_exchange_feature():
    statements = _statements(JULY_PLACEMENT)

    deal = _one(statements, "offering", "private placement")
    assert _figures(deal, "shares") == [5333331] and _figures(deal, "warrants") == [5333331]
    assert _figures(deal, "price_per_share") == [0.3]
    assert _one(statements, "warrants", "7th calendar day")["features"]["exchange_multiple"] == 9


def test_registered_direct_offering_shares_and_pre_funded_warrants():
    deal = _one(_statements(RDO_6K), "offering", "registered direct offering")

    assert deal["kind"] == "registered_direct"
    assert _figures(deal, "shares") == [8000000, 12000000]
    assert _figures(deal, "warrants") == [12000000]
    assert "pre-funded warrants" in deal["sentence"]


def test_equity_line_amount_pricing_commitment_shares_and_prepayment():
    statements = _statements(SEPA_6K)

    line = _one(statements, "offering", "standby equity purchase agreement")
    assert line["kind"] == "equity_line" and _figures(line, "dollars") == [40000000]

    pricing = _one(statements, "equity_line_terms", "lower of")
    assert 1.26 in _figures(pricing, "dollars") and _figures(pricing, "percent") == [50, 50]
    assert "$0.10" in pricing["sentence"]

    assert _figures(_one(statements, "equity_line_terms", "agreed to issue"), "shares") == [200000]
    assert _figures(_one(statements, "equity_line_terms", "Pre-Paid"), "dollars") == [5000000]


def test_share_consolidation_ratio_and_status():
    approved = _one(_statements(VOTE_6K), "reverse_split", "50 for 1 basis")
    assert approved["ratios"] == ["50-for-1"] and approved["status"] == "approved"

    supplement = dilution.extract_statements(
        split_sentences(fixture_text(f"{SEPA_424B5}.excerpt.txt")), prospectus=True)
    for needle in ("became effective on August 31, 2026", "effected a 1-for-50 share consolidation"):
        effected = _one(supplement, "reverse_split", needle)
        assert effected["ratios"] == ["1-for-50"] and effected["status"] == "effective"
        assert effected["effective_date"] == "2026-08-31"

    # Ancillary resolutions ("no fractional Shares be issued ...") are not splits.
    assert len([s for s in _statements(VOTE_6K) if s["topic"] == "reverse_split"]) == 1


def test_hypothetical_reverse_split_is_not_reported():
    # Nasdaq deficiency 6-K: "...including by effecting a reverse stock split, if necessary."
    assert not [s for s in _statements("0001213900-26-078738") if s["topic"] == "reverse_split"]


def test_prospectus_supplement_shares_outstanding_going_concern_and_base_prospectus():
    statements = dilution.extract_statements(
        split_sentences(fixture_text(f"{SEPA_424B5}.excerpt.txt")), prospectus=True)

    before = _one(statements, "shares_outstanding", "outstanding before this offering")
    assert before["basis"] == "before_offering"
    assert [(f["value"], f["share_class"]) for f in before["figures"]] == [(1911893, "A"), (16831, "B")]

    dated = _one(statements, "shares_outstanding", "As of July 24, 2026")
    assert dated["as_of"] == "2026-07-24" and _figures(dated, "shares")[0] == 75592901

    assert _one(statements, "going_concern", "substantial doubt")

    shelf = [s for s in statements if s["topic"] == "shelf_capacity"]
    assert any(_figures(s, "dollars") == [300000000] for s in shelf)
    # The accompanying base prospectus (after the bare PROSPECTUS heading) is
    # older text: only its shelf statement is read, and it is labelled.
    base = [s for s in statements if s.get("part") == "base_prospectus"]
    assert base and all(s["topic"] == "shelf_capacity" for s in base)
    assert not [s for s in statements if s["topic"] == "shares_outstanding"
                and "2,394,464" in s["sentence"]]


def test_dilution_view_for_omh():
    client = offline_client(default_document=STAND_IN)

    result = client.dilution(CIK, since="2026-07-01")

    latest = result["latest_shares_outstanding"]
    assert latest["accession"] == SEPA_424B5 and latest["filed"] == "2026-10-07"
    assert _figures(latest, "shares") == [1911893, 16831]
    assert "1,911,893 Class A Ordinary Shares" in latest["sentence"]

    xbrl = result["xbrl_shares_outstanding"]
    assert xbrl["value"] == 22259591 and xbrl["as_of"] == "2025-12-31"
    assert "reverse split took effect on 2026-08-31" in xbrl["note"]

    shelf = result["shelf_registrations"][0]
    assert (shelf["file_number"], shelf["form"], shelf["filed"]) == ("333-285637", "F-3", "2025-03-07")
    assert shelf["shelf"] is True and shelf["effective"]["date"] == "2025-03-26"
    assert [a["form"] for a in shelf["amendments"]] == ["F-3/A", "F-3/A"]
    assert [s["accession"] for s in shelf["supplements"]][-2:] == [RDO_424B5, SEPA_424B5]
    assert any(_figures(s, "dollars") == [300000000] for s in shelf["capacity"])

    supplements = {row["accession"]: row for row in result["prospectus_supplements"]}
    assert set(supplements) == {SEPA_424B5, RDO_424B5}
    assert all(row["read"] for row in supplements.values())
    assert any(40000000 in _figures(s, "dollars") for s in supplements[SEPA_424B5]["offering"])
    assert any(8000000 in _figures(s, "shares") for s in supplements[RDO_424B5]["offering"])

    kinds = {(s["accession"], s["kind"]) for s in result["offerings"]}
    assert {(OCT_PLACEMENT, "private_placement"), (RDO_6K, "registered_direct"),
            (JULY_PLACEMENT, "private_placement")} <= kinds

    programs = result["equity_lines_and_atm"]
    assert any(s["accession"] == SEPA_6K and 40000000 in _figures(s, "dollars") for s in programs)
    assert any(5000000 in _figures(s, "dollars") for s in programs)

    assert {s["features"].get("exchange_multiple") for s in result["warrants"]} >= {9}
    assert {tuple(s["ratios"]) for s in result["reverse_splits"]} >= {("50-for-1",), ("1-for-50",)}
    assert {s["accession"] for s in result["going_concern"]} == {SEPA_424B5, RDO_424B5}

    # Provenance on every statement in every section.
    sections = ["shares_outstanding", "offerings", "equity_lines_and_atm", "warrants",
                "reverse_splits", "going_concern"]
    for section in sections:
        for statement in result[section]:
            assert statement["accession"] and statement["filed"] and statement["sentence"]
            assert all(figure["text"] for figure in statement["figures"])
    assert result["filings_not_read"] == {"current_reports": 0, "prospectus_supplements": 0}


def test_dilution_reports_documents_it_could_not_read():
    client = offline_client()   # no stand-in: 6-Ks without a fixture cannot be fetched

    result = client.dilution(CIK, since="2026-10-20", max_supplements=0)

    by_accession = {entry["accession"]: entry for entry in result["filings_read"]}
    assert by_accession["0001213900-26-117590"]["statements"] == 0
    assert "Could not fetch document" in by_accession["0001213900-26-112280"]["error"]


def test_dilution_cli_outputs_and_exit_codes(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None:
                        offline_client(default_document=STAND_IN))
    runner = CliRunner()

    as_json = runner.invoke(main, ["dilution", CIK, "--since", "2026-10-01", "-j"])
    assert as_json.exit_code == 0
    payload = json.loads(as_json.stdout)
    assert payload["schema_version"] and payload["since"] == "2026-10-01"
    assert payload["latest_shares_outstanding"]["accession"] == SEPA_424B5

    rows = [json.loads(line) for line in
            runner.invoke(main, ["dilution", CIK, "--since", "2026-10-01", "--ndjson"]).stdout.splitlines()]
    assert {"offerings", "equity_lines_and_atm", "warrants", "going_concern",
            "shelf_capacity"} <= {row["section"] for row in rows}
    assert all(row["accession"] and row["sentence"] for row in rows)

    as_markdown = runner.invoke(main, ["dilution", CIK, "--since", "2026-10-01", "-m"])
    assert as_markdown.exit_code == 0
    assert "Latest stated shares outstanding (2026-10-07, 424B5 0001213900-26-107501)" in as_markdown.stdout
    assert "### Shelf registrations" in as_markdown.stdout
    assert "| 333-285637 | F-3 | 2025-03-07 | 2025-03-26 |" in as_markdown.stdout
    assert "### Equity lines and at-the-market programs" in as_markdown.stdout


def test_dilution_cli_exits_2_when_there_is_nothing_to_read(monkeypatch):
    client = offline_client(json_routes={"/submissions/CIK0000000002.json": {
        "cik": "2", "name": "Fund Trust",
        "filings": {"recent": {"accessionNumber": ["0000000002-26-000001"],
                               "filingDate": ["2026-05-01"], "form": ["N-CSR"],
                               "primaryDocument": ["a.htm"]}},
    }})
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: client)

    result = CliRunner().invoke(main, ["dilution", "2", "-j"])

    assert result.exit_code == 2
    assert "N-CSR (1)" in json.loads(result.stdout)["hint"]
