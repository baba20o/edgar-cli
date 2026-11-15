"""Offline offering-party, closing-state and registration-cover regressions."""

import json
from pathlib import Path

import pytest

from edgar import dilution
from edgar.filing_text import split_sentences


FIXTURES = Path(__file__).parent / "fixtures" / "handoff"


def _sentences(name):
    fixture = json.loads((FIXTURES / f"{name}.json").read_text())
    return split_sentences("\n\n".join(fixture["paragraphs"]))


def test_codx_sales_agent_and_explicit_alias_commission_have_separate_evidence():
    agents = [row for row in dilution.extract_statements(_sentences("codx-20260727"), prospectus=True)
              if row["topic"] == "agent"]
    declaration = next(row for row in agents if row.get("alias") == "Maxim")
    assert declaration["name"] == "Maxim Group LLC"
    assert declaration["role"] == "sales_agent"
    commission = next(row for row in agents if row.get("commission_percent") == 3)
    assert commission["name"] == "Maxim Group LLC"
    assert commission["name_text"] == "Maxim"
    assert commission["name_evidence"] == declaration["sentence"]
    assert commission["commission_text"] in commission["sentence"]
    assert all(f["text"] in row["sentence"] for row in agents for f in row["figures"])


def test_sbet_named_placement_agent_is_preserved_verbatim():
    agents = dilution.agent_statements(_sentences("sbet-20250530"))
    assert any(row["name"] == "A.G.P./Alliance Global Partners"
               and row["role"] == "placement_agent" for row in agents)


def test_agent_name_keeps_corporate_comma_and_initials():
    sentence = 'We entered into an underwriting agreement with H.C. Wainwright & Co., LLC ("HCW").'
    row = dilution.agent_statements([sentence])[0]
    assert row["name"] == "H.C. Wainwright & Co., LLC"
    assert row["role"] == "underwriter" and row["alias"] == "HCW"


def test_ambiguous_named_commission_is_not_attributed_to_either_agent():
    rows = dilution.agent_statements([
        'We entered into an Equity Distribution Agreement with Alpha LLC ("Alpha").',
        'We entered into an Equity Distribution Agreement with Beta LLC ("Beta").',
        "Alpha and Beta will share a commission of 3% of gross proceeds.",
    ])
    assert not [row for row in rows if "commission_percent" in row]


def test_sbet_pending_and_actual_closings_are_evidence_not_global_transaction_state():
    pending = dilution.extract_statements(_sentences("sbet-20250527-release-filed-20250530"))
    assert any(row.get("closing_status") == "announced_pending"
               and row.get("expected_closing_date") == "2025-05-29" for row in pending)
    body = dilution.extract_statements(_sentences("sbet-20250530"))
    closed = next(row for row in body if row.get("closing_date") == "2025-05-30")
    assert closed["closing_status"] == "closed"
    assert closed["figures"] == []
    # This factual sentence has no count to assign to another transaction.
    deal = next(row for row in body if row.get("kind") == "private_placement")
    assert deal["closing_status"] == "announced_pending"
    assert "closing_date" not in deal


def test_offering_registration_and_closing_states_use_the_same_sentence():
    sentence = ("The Company completed the offering of 2,000,000 common shares in a "
                "registered direct offering on November 3, 2026 for $4 million.")
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "offering")
    assert row["closing_status"] == "closed"
    assert row["closing_date"] == "2026-11-03"
    assert row["registration_status"] == "registered_direct"


def test_private_placement_label_alone_does_not_claim_resale_eligibility():
    sentence = "We entered into a private placement to issue 2,000,000 common shares for $4 million."
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "offering")
    assert row["closing_status"] == "announced_pending"
    assert "registration_status" not in row
    explicit = sentence[:-1] + " in an unregistered offering."
    row = next(row for row in dilution.extract_statements([explicit]) if row["topic"] == "offering")
    assert row["registration_status"] == "unregistered_pipe"


def test_different_closing_states_in_one_sentence_are_not_resolved_by_guessing():
    sentence = ("The first offering closed on November 1, 2026, while the second offering "
                "is expected to close on November 4, 2026.")
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "offering")
    assert row["closing_status"] == "unknown"
    assert "closing_date" not in row and "expected_closing_date" not in row


def test_warrant_repricing_and_cashless_language_keep_exact_evidence():
    sentence = ("The Company reduced the exercise price of 3,300,000 warrants from $5.40 to $3.19 "
                "and the warrants permit cashless exercise.")
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "warrants")
    assert row["features"]["cashless_exercise"] is True
    event = row["repricing_events"][0]
    assert (event["old_exercise_price"], event["new_exercise_price"]) == (5.4, 3.19)
    assert event["text"] in row["sentence"] and event["status"] == "stated"
    assert all(text in row["sentence"] for text in row["feature_evidence"].values())


def test_hypothetical_repricing_and_prohibited_cashless_exercise_are_distinguished():
    sentence = ("The Company may reduce the exercise price of its warrants from $5.40 to $3.19; "
                "cashless exercise is not permitted.")
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "warrants")
    assert row["features"]["cashless_exercise"] is False
    assert row["repricing_events"][0]["status"] == "proposed_or_conditional"


def test_exercise_price_range_is_not_a_repricing_event():
    sentence = "The warrants have exercise prices ranging from $3.19 to $5.40 per share."
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "warrants")
    assert "repricing_events" not in row


def test_cashless_condition_is_not_mistaken_for_a_prohibition():
    sentence = "If no registration statement is effective, the warrants may be exercised on a cashless basis."
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "warrants")
    assert row["features"]["cashless_exercise"] is True


def test_registration_cover_labels_actual_resale_capacity_without_issuer_proceeds():
    rows = dilution.extract_statements(_sentences("codx-20261104-shelf"), prospectus=True, shelf=True)
    covers = [row for row in rows if row.get("offering_type") == "resale"]
    assert any(any(f["type"] == "shares" and f["value"] == 39594495 for f in row["figures"])
               for row in covers)
    assert all(row["subtopic"] == "base_shelf_total" for row in covers)
    assert any(row.get("issuer_receives_resale_proceeds") is False for row in covers)


def test_shelf_cover_scan_stops_before_later_historical_offering_language():
    filler = ["This is an ordinary introductory statement."] * 60
    later = "We are offering 100,000,000 common shares under a public primary offering."
    assert dilution.extract_statements(filler + [later], prospectus=True, shelf=True) == []


@pytest.mark.parametrize("sentence", [
    "The Company closed the private placement contemplated by the Securities Purchase Agreement dated May 26, 2025.",
    "The Company closed the private placement under the agreement executed on May 26, 2025.",
    "On May 26, 2025, the Company entered into an agreement and closed the private placement.",
])
def test_closing_date_does_not_borrow_agreement_or_unlinked_dates(sentence):
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "offering")
    assert row["closing_status"] == "closed"
    assert "closing_date" not in row
    assert "closing_date_text" not in row


@pytest.mark.parametrize("sentence", [
    "On May 30, 2025, the Company closed the private placement.",
    "On May 30, 2025, we successfully completed the offering.",
    "The Company closed the private placement on May 30, 2025.",
    "The private placement closed as of May 30, 2025.",
])
def test_closing_date_requires_explicit_temporal_link_to_the_closing(sentence):
    row = next(row for row in dilution.extract_statements([sentence]) if row["topic"] == "offering")
    assert row["closing_status"] == "closed"
    assert row["closing_date"] == "2025-05-30"
    assert row["closing_date_text"] == "May 30, 2025"
    assert row["closing_date_text"] in row["sentence"]
