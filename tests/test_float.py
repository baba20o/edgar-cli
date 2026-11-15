"""Share-supply reconciliation preserves uncertainty and avoids double counts."""
import json
from pathlib import Path

from edgar.dilution import extract_statements
from edgar.filing_text import split_sentences
from edgar.forensics_api import _share_supply
from edgar.ownership import deduplicate_owners, insider_holdings


FIXTURES = Path(__file__).parent / "fixtures" / "handoff"


def statements(name):
    source = json.loads((FIXTURES / name).read_text())
    rows = extract_statements(split_sentences("\n\n".join(source["paragraphs"])), prospectus=source["form"].startswith("424B"))
    return [{**row, "accession": source["accession"], "form": source["form"],
             "filed": source.get("filed", "2025-05-30"), "source_url": source["source_url"],
             "acceptance_datetime": "2025-05-30T20:58:07Z"} for row in rows]


def test_sbet_later_closed_evidence_does_not_remain_active_pending():
    body = statements("sbet-20250530.json")
    release = statements("sbet-20250527-release-filed-20250530.json")
    offerings = [row for row in body + release if row["topic"] == "offering"]
    assert any(row.get("closing_status") == "announced_pending" for row in offerings)
    assert any(row.get("closing_status") == "closed" for row in offerings)
    result = _share_supply({"offerings": offerings}, {}, None)
    assert result["pending_unclosed_shares"] == []
    assert result["offering_status_unresolved"]
    for row in result["offering_status_unresolved"]:
        assert row["current_closing_status"] == "unresolved"
        assert row["potential_closing_evidence"]
        assert row["status_caveat"]
    assert "tradable_float" not in result


def test_post_baseline_requires_explicit_closing_date_and_keeps_pending_separate():
    base = {"accession": "one", "filed": "2026-11-10", "form": "8-K", "kind": "private_placement"}
    rows = [{**base, "sentence": "Closed November 9", "closing_status": "closed", "closing_date": "2026-11-09"},
            {**base, "sentence": "Closed November 1", "closing_status": "closed", "closing_date": "2026-11-01"},
            {**base, "sentence": "Closed with no date", "closing_status": "closed"},
            {**base, "accession": "two", "filed": "2026-11-11", "sentence": "New pending transaction", "closing_status": "announced_pending"}]
    dilution = {"latest_shares_outstanding": {"as_of": "2026-11-06", "figures": [{"type": "shares", "value": 1000}]},
                "offerings": rows, "prospectus_supplements": [{"offering": [rows[0]]}]}
    result = _share_supply(dilution, {}, None)
    assert [row["sentence"] for row in result["post_baseline_issuances"]] == ["Closed November 9"]
    assert [row["sentence"] for row in result["closed_issuances_date_unknown"]] == ["Closed with no date"]
    assert [row["sentence"] for row in result["pending_unclosed_shares"]] == ["New pending transaction"]
    assert result["shares_outstanding"]["latest_filing_stated_shares"] == dilution["latest_shares_outstanding"]


def test_overlapping_owners_and_derivative_holdings_are_evidence_not_float_deductions():
    owners = deduplicate_owners([
        {"owner_name": "Fund", "owner_cik": "1", "security_title": "Common", "shares": 400, "filed": "2026-10-01"},
        {"owner_name": "Fund", "owner_cik": "1", "security_title": "Common", "shares": 500, "filed": "2026-11-01", "shared_voting_power": 500},
        {"owner_name": "Manager", "owner_cik": "2", "security_title": "Common", "shares": 500, "filed": "2026-11-01", "shared_voting_power": 500},
        {"owner_name": "Fund", "owner_cik": "1", "security_title": "Preferred", "shares": 10, "filed": "2026-11-01"}])
    base = {"owner_name": "Manager", "is_officer": True, "security_title": "Common", "filed": "2026-11-01"}
    holdings = insider_holdings({"transactions": [
        {**base, "shares_after": 50, "is_derivative": False, "ownership": "D"},
        {**base, "shares_after": 500, "is_derivative": False, "ownership": "I", "nature_of_ownership": "Fund"},
        {**base, "shares_after": 900, "is_derivative": True, "ownership": "D"}]})
    dilution = {"xbrl_shares_outstanding": {"val": 1000, "end": "2026-11-01"}}
    result = _share_supply(dilution, {"beneficial_owners": owners, "insider_holdings": holdings}, {"val": 2000, "unit": "USD"})
    assert len(result["beneficial_owners"]) == len(result["insider_holdings"]) == 3
    assert result["shares_outstanding"]["xbrl_cover_shares"]["val"] == 1000
    assert result["stated_non_affiliate_shares"]["value"] is None
    assert any("overlap" in caveat for caveat in result["caveats"])
    assert not any(key in result for key in ("affiliate_deductions", "tradable_float", "estimated_float"))


def test_multiple_share_classes_do_not_turn_into_one_total():
    class_a = {"sentence": "Class A shares outstanding 100", "as_of": "2026-11-01", "share_class": "Class A",
               "figures": [{"type": "shares", "value": 100, "text": "100"}]}
    class_b = {"sentence": "Class B shares outstanding 20", "as_of": "2026-11-01", "share_class": "Class B",
               "figures": [{"type": "shares", "value": 20, "text": "20"}]}
    result = _share_supply({"shares_outstanding": [class_a, class_b]}, {}, None)
    rows = result["shares_outstanding"]["filing_stated_shares_by_class"]
    assert [(row["share_class"], row["figure"]["value"]) for row in rows] == [("Class A", 100), ("Class B", 20)]
    assert result["shares_outstanding"]["latest_filing_stated_shares"] is None


def test_latest_per_class_retains_historical_evidence_without_old_counts_in_latest():
    def row(share_class, number, day, accession):
        return {"share_class": share_class, "as_of": day, "filed": day,
                "accession": accession, "basis": "issued_and_outstanding",
                "sentence": f"{number} {share_class} shares outstanding as of {day}",
                "figures": [{"type": "shares", "value": number, "text": str(number)}]}
    old_a = row("Class A", 100, "2026-09-01", "old")
    new_a = row("Class A", 200, "2026-11-01", "new")
    class_b = row("Class B", 20, "2026-09-01", "old")
    result = _share_supply({"shares_outstanding": [old_a, new_a, class_b]}, {}, None)
    supply = result["shares_outstanding"]
    assert {(r["share_class"], r["figure"]["value"]) for r in supply["filing_stated_shares_by_class"]} == {("Class A", 200), ("Class B", 20)}
    assert len(supply["filing_stated_share_history"]) == 3
