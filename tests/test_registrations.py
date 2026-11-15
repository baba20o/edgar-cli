"""Registration status follows explicit evidence, never form-code shortcuts."""

import pytest

from edgar import registrations


def _row(form, sequence, **extra):
    return {"form": form, "fileNumber": "333-123456", "filingDate": f"2026-01-{sequence:02}",
            "acceptanceDateTime": f"2026-01-{sequence:02}T12:00:00Z",
            "accessionNumber": f"0000000001-26-{sequence:06}",
            "primary_doc_url": f"https://www.sec.gov/Archives/edgar/data/1/{sequence}/filing.htm",
            **extra}


def _evidence(row, sentence):
    return [{**statement, "file_number": row["fileNumber"],
             "accession": row["accessionNumber"], "filed": row["filingDate"],
             "acceptance_datetime": row["acceptanceDateTime"], "source_url": row["primary_doc_url"]}
            for statement in registrations.withdrawal_statements([sentence], row["form"])]


def test_original_shelf_shape_is_preserved_with_provenance_and_pos_am():
    rows = [_row("S-3", 1), _row("S-3/A", 2), _row("EFFECT", 3),
            _row("POS AM", 4), _row("424B5", 5)]
    shelf = registrations.aggregate(rows, [
        {"topic": "shelf_capacity", "file_number": "333-123456", "sentence": "A cover quote.",
         "offering_type": "resale", "figures": []}])[0]
    assert shelf["form"] == "S-3" and shelf["shelf"] is True
    assert [r["form"] for r in shelf["amendments"]] == ["S-3/A", "POS AM"]
    assert shelf["effective"]["date"] == "2026-01-03"
    assert [r["form"] for r in shelf["supplements"]] == ["424B5"]
    assert shelf["status"] == "effective" and shelf["withdrawn"] is None
    assert shelf["offering_type"] == "resale"
    assert all(r["source_url"] for r in shelf["amendments"] + shelf["supplements"])


def test_absent_effect_notice_is_unknown_not_proof_of_ineffectiveness():
    shelf = registrations.aggregate([_row("S-3", 1)], [])[0]
    assert shelf["status"] == "effectiveness_unknown"
    assert shelf["effective"] is None and shelf["caveats"]


def test_automatic_effectiveness_form_is_identified_as_the_evidence_basis():
    shelf = registrations.aggregate([_row("S-3ASR", 1)], [])[0]
    assert shelf["status"] == "effective"
    assert shelf["effective"]["basis"] == "automatic_effectiveness_form"


def test_rw_metadata_alone_does_not_prove_shelf_withdrawal():
    shelf = registrations.aggregate([_row("S-3", 1), _row("EFFECT", 2), _row("RW", 3)], [])[0]
    assert shelf["status"] == "withdrawal_status_unknown"
    assert shelf["withdrawn"] is None
    assert shelf["withdrawal_requests"][0]["scope"] == "unknown"
    assert shelf["effective"]["date"] == "2026-01-02"


def test_explicit_entire_registration_request_remains_a_request():
    request = _row("RW", 3)
    evidence = _evidence(request, "We request withdrawal of the entire registration statement.")
    shelf = registrations.aggregate([_row("S-3", 1), _row("EFFECT", 2), request], evidence)[0]
    assert shelf["status"] == "withdrawal_requested"
    assert shelf["withdrawn"] is None


def test_amendment_withdrawal_does_not_kill_parent_even_if_mislabeled_rw():
    for form in ("RW", "AW"):
        request = _row(form, 4)
        evidence = _evidence(request, "We request withdrawal of only the amendment filed yesterday.")
        shelf = registrations.aggregate([_row("S-3", 1), _row("EFFECT", 2), request], evidence)[0]
        assert shelf["status"] == "effective" and shelf["withdrawn"] is None
        assert shelf["withdrawal_requests"][0]["scope"] == "amendment"


def test_explicit_rescission_links_exact_request_without_clearing_another_request():
    first, second, rescission = _row("RW", 3), _row("RW", 4), _row("RW WD", 5)
    statements = _evidence(first, "We request withdrawal of the entire registration statement.")
    statements += _evidence(second, "We request withdrawal of the entire registration statement.")
    statements += _evidence(rescission, "We rescind our withdrawal request 0000000001-26-000003.")
    shelf = registrations.aggregate([_row("S-3", 1), _row("EFFECT", 2), first, second, rescission], statements)[0]
    assert shelf["withdrawal_requests"][0]["rescinded_by"] == rescission["accessionNumber"]
    assert "rescinded_by" not in shelf["withdrawal_requests"][1]
    assert shelf["status"] == "withdrawal_requested"


def test_rescinding_the_only_exact_request_returns_to_documented_effectiveness():
    request, rescission = _row("RW", 3), _row("RW WD", 4)
    statements = _evidence(request, "We request withdrawal of the entire registration statement.")
    statements += _evidence(rescission, "We rescind our withdrawal request 0000000001-26-000003.")
    shelf = registrations.aggregate([_row("S-3", 1), _row("EFFECT", 2), request, rescission], statements)[0]
    assert shelf["status"] == "effective"
    assert shelf["withdrawal_rescissions"][0]["resolved_request_accessions"] == [request["accessionNumber"]]


def test_unlinked_rescission_is_preserved_without_guessing_its_target():
    shelf = registrations.aggregate([_row("S-3", 1), _row("RW", 3), _row("RW WD", 4)], [])[0]
    assert shelf["status"] == "withdrawal_status_unknown"
    assert shelf["withdrawal_rescissions"][0]["resolved_request_accessions"] == []


def test_only_explicit_commission_grant_and_entire_scope_support_withdrawn():
    request = _row("RW", 3)
    statements = _evidence(request, "The Commission has granted withdrawal of the entire registration statement.")
    shelf = registrations.aggregate([_row("S-3", 1), _row("EFFECT", 2), request], statements)[0]
    assert shelf["status"] == "withdrawn"
    assert shelf["withdrawn"]["basis"] == "explicit_commission_confirmation"
    assert shelf["withdrawn"]["sentence"] == statements[0]["sentence"]


def test_negated_or_conditional_commission_grant_does_not_establish_withdrawal():
    for sentence in (
        "The Commission has not granted withdrawal of the entire registration statement.",
        "Once the SEC has granted withdrawal of the entire registration statement, we will notify investors.",
    ):
        request = _row("RW", 3)
        statements = _evidence(request, sentence)
        shelf = registrations.aggregate([_row("S-3", 1), _row("EFFECT", 2), request], statements)[0]
        assert shelf["status"] != "withdrawn" and shelf["withdrawn"] is None


def test_post_effective_deregistration_does_not_imply_entire_withdrawal():
    amendment = _row("POS AM", 3)
    statements = _evidence(amendment, "This amendment deregisters all unsold securities.")
    shelf = registrations.aggregate([_row("S-3", 1), _row("EFFECT", 2), amendment], statements)[0]
    assert shelf["status"] == "effective" and shelf["withdrawn"] is None


def test_different_file_numbers_do_not_cross_contaminate():
    shelf = registrations.aggregate([_row("S-3", 1), _row("RW", 2, fileNumber="333-999999")], [])[0]
    assert shelf["withdrawal_requests"] == []


@pytest.mark.parametrize("form", ["S-3", "S-3ASR"])
def test_old_effectiveness_evidence_does_not_claim_current_shelf_availability(form):
    old = {"filingDate":"2021-01-01", "acceptanceDateTime":"2021-01-01T12:00:00Z"}
    rows = [_row(form, 1, **old)]
    if form == "S-3":
        rows.append(_row("EFFECT", 2, filingDate="2021-01-02",
                         acceptanceDateTime="2021-01-02T12:00:00Z"))
    shelf = registrations.aggregate(rows, [])[0]
    assert shelf["status"] == "effective"  # Historical evidence remains available.
    assert shelf["effectiveness_scope"] == "historical_filing_evidence"
    assert shelf["current_offering_availability"] == "not_assessed"
    assert any("expiration" in caveat and "unused capacity" in caveat for caveat in shelf["caveats"])
    assert not any("expired" == value for value in shelf.values())
