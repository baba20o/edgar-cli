"""`events` and `insiders` for foreign private issuers (6-K filers)."""

import json

import pytest
from click.testing import CliRunner

from edgar import sixk
from edgar.cli import main
from edgar.filing_text import document_to_text

from omh import CIK, fixture_text, offline_client

# accession -> (event types that must be present, {type: substrings of its sentence})
KNOWN_6K = {
    "0001213900-26-117590": (
        {"name_change", "redomicile"},
        {"name_change": ['changed its name from "Ohmyhome Limited" to "Ohmyswift Limited',
                         "November 3, 2026"],
         "redomicile": ["from the Cayman Islands to the British Virgin Islands"]},
    ),
    "0001213900-26-110319": (
        {"financing"},
        {"financing": ["private placement", "3,000,000 Class A ordinary shares",
                       "3,000,000 warrants", "$1.30"]},
    ),
    "0001213900-26-107603": (
        {"financing"},
        {"financing": ["standby equity purchase agreement", "$40,000,000"]},
    ),
    "0001213900-26-100756": (
        {"listing_compliance"},
        {"listing_compliance": ["regained compliance with Listing Rule 5550(a)(2)"]},
    ),
    "0001213900-26-091913": (
        {"reverse_split", "shareholder_vote"},
        {"reverse_split": ["50 for 1 basis"],
         "shareholder_vote": ["each of which was approved by the shareholders"]},
    ),
    "0001213900-26-082334": (
        {"financing"},
        {"financing": ["registered direct offering", "8,000,000 Class A ordinary shares",
                       "12,000,000"]},
    ),
    "0001213900-26-078921": (
        {"financing"},
        {"financing": ["private placement", "5,333,331 Class A ordinary shares", "$0.30"]},
    ),
    "0001213900-26-078738": (
        {"listing_deficiency"},
        {"listing_deficiency": ["notice dated July 13, 2026", "minimum bid price"]},
    ),
    "0001213900-26-070191": (
        {"material_agreement", "divestiture"},
        {"material_agreement": ["debt waiver and release agreement"],
         "divestiture": ["Sterling Oat Ltd.", "purchase price of $1"]},
    ),
    "0001213900-26-072273": (
        {"divestiture"},
        {"divestiture": ["sold all of its shares in its wholly-owned subsidiary, Ohmyhome (BVI) Limited",
                         "Sterling Oat Ltd."]},
    ),
}


@pytest.mark.parametrize("accession", sorted(KNOWN_6K))
def test_classify_6k_known_omh_filings(accession):
    expected_types, expected_text = KNOWN_6K[accession]

    result = sixk.classify_6k(document_to_text(fixture_text(f"{accession}.htm")))

    assert expected_types <= set(result["event_types"])
    assert result["classified_from"] == "body"
    for event_type, needles in expected_text.items():
        for needle in needles:
            assert needle in result["snippets"][event_type]


def test_classify_6k_ignores_cover_page_signature_and_hypotheticals():
    # The Nasdaq deficiency 6-K mentions a reverse split only as a possible
    # future cure ("including by effecting a reverse stock split, if
    # necessary"): that must not be reported as a reverse split.
    result = sixk.classify_6k(document_to_text(fixture_text("0001213900-26-078738.htm")))

    assert result["event_types"] == ["listing_deficiency"]
    assert "reverse_split" not in result["snippets"]
    for sentence in result["snippets"].values():
        assert "Indicate by check mark" not in sentence
        assert "duly caused this report" not in sentence


def test_classify_6k_marks_recap_only_types_as_background():
    # 0001213900-26-072273 only restates the divestiture ("As previously
    # disclosed, on June 17, 2026 ..."): keep it, flagged as a recap.
    recap = sixk.classify_6k(document_to_text(fixture_text("0001213900-26-072273.htm")))
    assert recap["background_types"] == ["divestiture"]

    # 0001213900-26-100756 recaps the deficiency notice but its news is the
    # regained compliance: the recap-only type is dropped.
    fresh = sixk.classify_6k(document_to_text(fixture_text("0001213900-26-100756.htm")))
    assert fresh["event_types"] == ["listing_compliance"]
    assert fresh["background_types"] == []


def test_classify_6k_interim_financial_statements():
    # Body of OMH's 6-K 0001213900-26-098819 (the document itself is 115 KB of
    # inline XBRL cover page, so only its text is kept here).
    result = sixk.classify_6k(
        "FORM 6-K\n\nForm 20-F ☒ Form 40-F ☐\n\n"
        "INFORMATION CONTAINED IN THIS REPORT ON FORM 6-K\n\n"
        'Ohmyhome Limited (the "Company") is furnishing this Form 6-K to provide six-month '
        "interim financial statements.\n\n"
        "EXHIBIT INDEX\n\nExhibit No. Description\n\n"
        "99.1 Unaudited Interim Condensed Consolidated Financial Statements for the Six Months "
        "Ended June 30, 2026 and 2025\n\nSIGNATURES"
    )

    assert result["event_types"] == ["financial_results"]
    assert result["snippets"]["financial_results"].endswith("six-month interim financial statements.")
    assert result["heading"] == ""
    assert result["exhibit_index"][-1].startswith("99.1 Unaudited Interim Condensed")


def test_classify_6k_pro_forma_statements_are_not_results():
    result = sixk.classify_6k(document_to_text(fixture_text("0001213900-26-072273.htm")))

    assert "financial_results" not in result["event_types"]


def test_classify_6k_heading_and_unclassified_lead():
    named = sixk.classify_6k(document_to_text(fixture_text("0001213900-26-117590.htm")))
    assert named["heading"] == "Name Change and Continuation to the British Virgin Islands"

    plain = sixk.classify_6k(
        "FORM 6-K\n\nForm 20-F ☒ Form 40-F ☐\n\n"
        "The registrant today published its sustainability report for the year on its website.\n\n"
        "SIGNATURE\n\nPursuant to the requirements of the Securities Exchange Act of 1934, "
        "the registrant has duly caused this report to be signed."
    )
    assert plain["event_types"] == []
    assert plain["classified_from"] is None
    assert plain["lead"].startswith("The registrant today published its sustainability report")


def test_events_reads_6k_filings_for_a_foreign_filer():
    client = offline_client()

    result = client.events(CIK, limit=5)

    assert result["forms_examined"] == {"6-K": 5}
    # Newest first, by acceptance time. Every 6-K examined is returned.
    assert [e["accession"] for e in result["events"]] == [
        "0001213900-26-117590", "0001213900-26-112280", "0001213900-26-110319",
        "0001213900-26-108641", "0001213900-26-107603",
    ]
    # 0001213900-26-112280 has no fixture: a document that cannot be fetched
    # is reported as such, never silently dropped or guessed at.
    unread = result["events"][1]
    assert unread["event_types"] == ["other"] and "fetch_error" in unread
    first = result["events"][0]
    assert first["form"] == "6-K"
    assert first["acceptance_datetime"] == "2026-11-09T22:00:10.000Z"
    assert {"name_change", "redomicile"} <= set(first["event_types"])
    assert "Ohmyswift Limited" in first["snippets"]["name_change"]
    assert first["evidence_document"] == "ea0311444-6k_ohmyswift.htm"
    assert first["classified_from"] == "body"
    assert "hint" not in result


def test_events_falls_back_to_first_ex99_exhibit_when_the_6k_says_nothing():
    cover = ("<html><body><p>FORM 6-K</p><p>Form 20-F ☒ Form 40-F ☐</p>"
             "<p>Exhibit Index</p><p>99.1 Press release</p><p>SIGNATURE</p></body></html>")
    index = """<table>
      <tr><td>1</td><td>6-K</td><td><a href="/Archives/edgar/data/1944902/000121390026117590/cover.htm">cover.htm</a></td><td>6-K</td><td>900</td></tr>
      <tr><td>2</td><td>Press release</td><td><a href="/Archives/edgar/data/1944902/000121390026117590/ex99-1.htm">ex99-1.htm</a></td><td>EX-99.1</td><td>1200</td></tr>
    </table>"""
    release = ("<html><body><p>Acme Ltd today announced that it has regained compliance with "
               "Nasdaq Listing Rule 5550(a)(2).</p></body></html>")
    client = offline_client(extra_documents={
        "-index.htm": index, "ex99-1.htm": release, "ea0311444-6k_ohmyswift.htm": cover,
    })

    with_exhibits = client.events(CIK, limit=1)["events"][0]
    assert with_exhibits["event_types"] == ["listing_compliance"]
    assert with_exhibits["classified_from"] == "exhibit"
    assert with_exhibits["evidence_document"] == "ex99-1.htm"
    assert with_exhibits["evidence_document_type"] == "EX-99.1"

    without = client.events(CIK, limit=1, read_exhibits=False)["events"][0]
    assert without["event_types"] == ["other"]
    assert without["classified_from"] is None


def test_events_still_classifies_8k_items():
    client = offline_client(
        json_routes={"/submissions/CIK0000000001.json": {
            "cik": "1", "name": "Domestic Co",
            "filings": {"recent": {
                "accessionNumber": ["0000000001-26-000002"],
                "filingDate": ["2026-05-01"],
                "acceptanceDateTime": ["2026-05-01T20:05:00.000Z"],
                "form": ["8-K"], "items": ["5.02"],
                "primaryDocument": ["a8k.htm"],
            }},
        }},
        extra_documents={"a8k.htm": "<p>The board accepted a resignation.</p>"},
    )

    result = client.events("1")

    assert result["forms_examined"] == {"8-K": 1}
    assert result["events"][0]["event_types"] == ["leadership"]
    assert result["events"][0]["acceptance_datetime"] == "2026-05-01T20:05:00.000Z"


def test_events_empty_result_names_the_forms_the_filer_files():
    client = offline_client(json_routes={"/submissions/CIK0000000002.json": {
        "cik": "2", "name": "Fund Trust",
        "filings": {"recent": {
            "accessionNumber": ["0000000002-26-000001", "0000000002-26-000002"],
            "filingDate": ["2026-05-01", "2026-02-01"],
            "form": ["N-CSR", "N-CSR"], "primaryDocument": ["a.htm", "b.htm"],
        }},
    }})

    result = client.events("2")

    assert result["events"] == []
    assert result["forms_filed"] == {"N-CSR": 2}
    assert "N-CSR (2)" in result["hint"]
    assert "8-K" in result["hint"] and "6-K" in result["hint"]


def test_events_cli_exits_2_with_hint_when_empty(monkeypatch):
    class FakeClient:
        def events(self, identifier, **kwargs):
            return {"cik": "0000000002", "name": "Fund Trust", "events": [], "total": 0,
                    "hint": "It files: N-CSR (2).", "forms_filed": {"N-CSR": 2}}

    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: FakeClient())

    result = CliRunner().invoke(main, ["events", "2", "--json-output"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["events"] == []
    assert payload["hint"] == "It files: N-CSR (2)."
    assert "No data: It files: N-CSR (2)." in result.stderr


def test_events_cli_exits_0_when_events_exist(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())

    result = CliRunner().invoke(main, ["events", CIK, "--limit", "2", "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["events"][0]["accession"] == "0001213900-26-117590"
    assert payload["schema_version"]


def test_insiders_reports_form3_statements_when_there_are_no_transactions():
    client = offline_client()

    result = client.insiders(CIK, since="2026-06-01")

    assert result["transactions"] == []
    assert result["forms_found"] == {"3": 1}
    statement = result["initial_statements"][0]
    assert statement["owner_name"] == "Wong Tai Kok"
    assert statement["is_director"] is True
    assert statement["no_securities_owned"] is True
    assert statement["holdings"] == []
    assert statement["accession"] == "0001213900-26-076944"
    assert "No Form 4 or Form 5 transactions on or after 2026-06-01" in result["hint"]
    assert "1 Form 3 initial ownership statement(s) (1 declare no securities owned)" in result["hint"]


def test_insiders_parses_form3_holdings():
    from edgar.insiders import parse_ownership_xml

    parsed = parse_ownership_xml("""<?xml version="1.0"?>
    <ownershipDocument><documentType>3</documentType>
      <reportingOwner><reportingOwnerId><rptOwnerCik>1</rptOwnerCik>
        <rptOwnerName>Jane Roe</rptOwnerName></reportingOwnerId></reportingOwner>
      <nonDerivativeTable><nonDerivativeHolding>
        <securityTitle><value>Class A Ordinary Shares</value></securityTitle>
        <postTransactionAmounts><sharesOwnedFollowingTransaction><value>1200</value>
        </sharesOwnedFollowingTransaction></postTransactionAmounts>
        <ownershipNature><directOrIndirectOwnership><value>D</value>
        </directOrIndirectOwnership></ownershipNature>
      </nonDerivativeHolding></nonDerivativeTable>
      <derivativeTable><derivativeHolding>
        <securityTitle><value>Warrant</value></securityTitle>
        <conversionOrExercisePrice><value>1.30</value></conversionOrExercisePrice>
        <underlyingSecurity><underlyingSecurityTitle><value>Class A Ordinary Shares</value>
        </underlyingSecurityTitle><underlyingSecurityShares><value>500</value>
        </underlyingSecurityShares></underlyingSecurity>
      </derivativeHolding></derivativeTable>
    </ownershipDocument>""")

    assert parsed["no_securities_owned"] is False
    assert parsed["non_derivative_holdings"] == [{
        "security_title": "Class A Ordinary Shares", "shares": 1200.0, "ownership": "D",
        "nature_of_ownership": None, "is_derivative": False,
    }]
    warrant = parsed["derivative_holdings"][0]
    assert warrant["conversion_or_exercise_price"] == 1.30
    assert warrant["underlying_shares"] == 500.0


def test_insiders_cli_exits_2_when_no_insider_forms(monkeypatch):
    client = offline_client(json_routes={"/submissions/CIK0000000002.json": {
        "cik": "2", "name": "Fund Trust",
        "filings": {"recent": {"accessionNumber": ["0000000002-26-000001"],
                               "filingDate": ["2026-05-01"], "form": ["N-CSR"],
                               "primaryDocument": ["a.htm"]}},
    }})
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: client)

    result = CliRunner().invoke(main, ["insiders", "2", "--json-output"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["transactions"] == [] and payload["initial_statements"] == []
    assert "No insider ownership forms (3, 4, 5, 144)" in payload["hint"]
    assert "N-CSR (1)" in payload["hint"]


def test_insiders_cli_exits_0_with_form3_statements(monkeypatch):
    monkeypatch.setattr("edgar.cli.get_client", lambda use_cache=True, cache_max_mb=None: offline_client())

    result = CliRunner().invoke(main, ["insiders", CIK, "--since", "2026-06-01", "--json-output"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["initial_statements"][0]["owner_name"] == "Wong Tai Kok"
