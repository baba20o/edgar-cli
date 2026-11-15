"""Synthetic domestic-report regressions; no live filing or network dependency."""

import json
from pathlib import Path

import pytest

from edgar.events_classifier import classify_8k, event_types_from_items


def test_legacy_item_names_are_preserved():
    assert event_types_from_items("2.02, 5.02, 3.01") == {"earnings", "leadership", "delisting"}
    assert event_types_from_items("2.01,5.01,2.03,5.03,3.03") == {"merger", "debt", "capitalization"}


@pytest.mark.parametrize(("item", "kind"), [
    ("1.01", "material_agreement"), ("1.02", "agreement_termination"),
    ("1.03", "bankruptcy"), ("3.02", "financing"), ("4.01", "auditor_change"),
    ("4.02", "non_reliance_restatement"), ("5.07", "shareholder_vote"),
])
def test_additional_item_categories_have_metadata_evidence(item, kind):
    result = classify_8k("", item)
    assert result["event_types"] == [kind]
    assert result["item_evidence"] == {kind: [item]}
    assert result["snippets"] == {}
    assert result["classified_from"] == "items"


def test_item_301_does_not_invent_a_delisting_determination():
    result = classify_8k("", "3.01")
    assert result["event_types"] == ["delisting"]
    assert "delisting_determination" not in result["event_types"]
    assert result["snippets"] == {}
    assert "not completed actions" in result["item_evidence_caveat"]


def test_financing_item_and_text_do_not_match_safe_harbor_outlook():
    body = (
        "Item 1.01 Entry into a Material Definitive Agreement.\n\n"
        "On October 13, the Company entered into a securities purchase agreement for a private placement.\n\n"
        "Forward-Looking Statements\n\n"
        "This report includes forward-looking statements about the Company's outlook and forecast.\n\n"
        "The Company notes that actual results may differ materially from expectations."
    )
    result = classify_8k(body, "1.01,2.03,3.02,9.01")
    assert set(result["event_types"]) == {"debt", "financing"}
    assert result["snippets"]["financing"] in body
    assert "guidance" not in result["snippets"]
    assert "debt" not in result["snippets"]  # Item 2.03, not the word "notes".


def test_notes_and_outlook_alone_are_not_event_evidence():
    assert classify_8k("Notes to the financial statements. The outlook is discussed on our website.")["event_types"] == []


def test_actual_guidance_update_has_sentence_evidence():
    sentence = "The Company raised its full-year revenue guidance to $100 million."
    result = classify_8k(sentence, "8.01")
    assert result["event_types"] == ["guidance"]
    assert result["snippets"] == {"guidance": sentence}


@pytest.mark.parametrize(("sentence", "kind"), [
    ("The Company entered into a merger agreement with Acme Corporation.", "merger"),
    ("The Company entered into a warrant inducement agreement with the holders.", "financing"),
    ("The Company entered into a license agreement with Acme Corporation.", "material_agreement"),
])
def test_item_101_uses_specific_deal_text(sentence, kind):
    result = classify_8k(sentence, "1.01")
    assert result["event_types"] == [kind]
    assert result["snippets"][kind] == sentence


def test_merger_termination_is_not_a_new_merger():
    result = classify_8k("The Company announced termination of its merger agreement with Acme.", "1.02")
    assert result["event_types"] == ["agreement_termination"]


@pytest.mark.parametrize(("sentence", "kind"), [
    ("The Company received a Nasdaq deficiency notice because its minimum bid price was below $1.00.", "minimum_bid_deficiency"),
    ("The Company received a delisting determination from Nasdaq on November 1.", "delisting_determination"),
    ("The Company requested a hearing before the Nasdaq Hearings Panel to appeal the delisting determination.", "hearing_panel_appeal"),
    ("Nasdaq confirmed that the Company has regained compliance with its minimum bid price requirement.", "listing_compliance"),
])
def test_listing_details_require_specific_text(sentence, kind):
    result = classify_8k(sentence, "3.01")
    assert kind in result["event_types"]
    assert result["snippets"][kind] == sentence
    if kind == "listing_compliance":
        assert "minimum_bid_deficiency" not in result["event_types"]
        assert "delisting_determination" not in result["event_types"]


def test_possible_hearing_does_not_imply_an_appeal_was_filed():
    result = classify_8k("The Company may request a hearing before the Nasdaq Hearings Panel.", "3.01")
    assert "hearing_panel_appeal" not in result["event_types"]


def test_deficiency_notice_does_not_require_an_immediate_trading_effect():
    sentence = "The Company received a minimum bid deficiency notice from Nasdaq, which has no immediate effect on trading."
    result = classify_8k(sentence, "3.01")
    assert "minimum_bid_deficiency" in result["event_types"]
    assert "delisting_determination" not in result["event_types"]


def test_actual_trading_suspension_has_text_evidence():
    sentence = "Nasdaq notified the Company that it will suspend trading in its common stock on Monday."
    result = classify_8k(sentence, "3.01")
    assert result["snippets"]["delisting"] == sentence


def test_cover_and_exhibit_referral_requests_fallback():
    body = (
        "UNITED STATES\n\nSECURITIES AND EXCHANGE COMMISSION\n\nFORM 8-K\n\n"
        "Item 7.01 Regulation FD Disclosure.\n\n"
        "A copy of the press release is attached as Exhibit 99.1 and incorporated herein by reference.\n\n"
        "Item 9.01 Financial Statements and Exhibits.\n\n"
        "99.1 Press Release Announcing a Merger Agreement\n\nSIGNATURES"
    )
    result = classify_8k(body, "7.01,9.01")
    assert result["event_types"] == []
    assert result["needs_exhibit"] is True
    assert result["exhibit_index"] == ["99.1 Press Release Announcing a Merger Agreement"]


def test_body_classification_prevents_unnecessary_exhibit_fallback():
    result = classify_8k("The Company announced the closing of a private placement for $10 million.", "3.02")
    assert result["needs_exhibit"] is False
    assert result["classified_from"] == "body"


def test_item_headers_are_not_promoted_to_sentence_claims():
    result = classify_8k("Item 1.03 Bankruptcy or Receivership.\n\nItem 4.01 Changes in Registrant's Certifying Accountant.")
    assert set(result["event_types"]) == {"bankruptcy", "auditor_change"}
    assert result["snippets"] == {}


def test_fresh_compliance_supersedes_recap_deficiency_text():
    result = classify_8k(
        "As previously disclosed, the Company received a Nasdaq deficiency notice for its minimum bid price.\n\n"
        "Nasdaq confirmed that the Company has regained compliance with its minimum bid price requirement.",
        "3.01",
    )
    assert "listing_compliance" in result["event_types"]
    assert "minimum_bid_deficiency" not in result["event_types"]
    assert "listing_deficiency" not in result["event_types"]
    assert result["background_types"] == []


def test_recap_only_event_is_marked_background():
    result = classify_8k("As previously disclosed, the Company entered into a securities purchase agreement for a private placement.")
    assert result["background_types"] == ["financing"]


def test_safe_harbor_heading_does_not_hide_a_subsequent_item():
    result = classify_8k(
        "Forward-Looking Statements\n\nThe Company expects revenue to grow in the future.\n\n"
        "Item 5.02 Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers; Compensatory Arrangements of Certain Officers.\n\n"
        "The Company appointed Jane Doe as chief financial officer."
    )
    assert set(result["event_types"]) == {"leadership", "cfo_change"}
    assert "guidance" not in result["event_types"]


@pytest.mark.parametrize(("filename", "items", "expected"), [
    ("codx-20261013.json", "1.01,2.03,3.02,9.01", {"debt", "financing"}),
    ("sbet-20250530.json", "1.01,3.02,5.02,8.01,9.01", {"financing", "leadership"}),
])
def test_saved_domestic_filing_excerpt_keeps_financing_evidence(filename, items, expected):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "handoff" / filename).read_text())
    body = "\n\n".join(fixture["paragraphs"])
    result = classify_8k(body, items)
    assert set(result["event_types"]) == expected
    assert result["snippets"]["financing"] in body
    assert "guidance" not in result["event_types"]
