"""Offline extraction regressions from independently fetched SEC excerpts.

The JSON fixtures retain accession, URL and document hash. Additional examples
below are synthetic clause-boundary tests, rather than attributed filing text.
"""

import json
from pathlib import Path

import pytest

from edgar import dilution
from edgar.filing_text import split_sentences


FIXTURES = Path(__file__).parent / "fixtures" / "handoff"


def _fixture_rows(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return dilution.extract_statements(
        split_sentences("\n\n".join(data["paragraphs"])), prospectus=True)


def _shares(rows):
    return [row for row in rows if row["topic"] == "shares_outstanding"]


@pytest.mark.parametrize("name,rejected", [
    ("codx-20260727", {81565, 15500, 31595, 114024, 247024}),
    ("sbet-20260623", {11762236}),
    ("jagx-20261109", {142535}),
])
def test_sec_derivative_counts_are_not_shares_outstanding(name, rejected):
    rows = _shares(_fixture_rows(name))
    assert rows
    assert not rejected.intersection(f["value"] for row in rows for f in row["figures"])


def test_sbet_based_on_count_is_the_pre_offering_baseline():
    rows = _shares(_fixture_rows("sbet-20260623"))
    assert len(rows) == 1
    assert rows[0]["basis"] == "before_offering"
    assert rows[0]["as_of"] == "2026-06-18"
    assert [f["value"] for f in rows[0]["figures"]] == [202382019]


def test_jagx_issued_count_and_dated_voting_baseline_survive():
    rows = _shares(_fixture_rows("jagx-20261109"))
    assert any("issued and outstanding" in row["sentence"]
               and [f["value"] for f in row["figures"]] == [13345986] for row in rows)
    assert any(row.get("as_of") == "2026-11-04"
               and row["basis"] == "before_offering" for row in rows)


@pytest.mark.parametrize("qualifier", [
    "issuable upon the settlement of outstanding restricted stock units",
    "underlying outstanding options",
    "reserved for issuance under outstanding warrants",
    "subject to outstanding preferreds",
    "upon conversion of outstanding preferred stock",
    'with dividend rights (the "Eligible Warrants") outstanding',
])
def test_derivative_qualifiers_never_create_a_stock_baseline(qualifier):
    sentence = f"100,000 shares of common stock {qualifier}."
    assert _shares(dilution.extract_statements([sentence])) == []


def test_actual_baseline_is_not_contaminated_by_other_counts_in_sentence():
    sentence = (
        "The 12,000,000 shares of common stock outstanding after this offering "
        "are based on 10,000,000 shares of common stock outstanding as of July 1, 2026, "
        "and assume exercise of 2,000,000 shares of common stock underlying warrants."
    )
    row = _shares(dilution.extract_statements([sentence]))[0]
    assert row["basis"] == "before_offering"
    assert row["as_of"] == "2026-07-01"
    assert [figure["value"] for figure in row["figures"]] == [10000000]


def test_valid_issued_count_survives_a_separate_derivative_clause():
    sentence = (
        "We have 10,000,000 shares of common stock issued and outstanding; "
        "2,000,000 shares of common stock are issuable upon exercise of outstanding warrants."
    )
    row = _shares(dilution.extract_statements([sentence]))[0]
    assert [figure["value"] for figure in row["figures"]] == [10000000]


def test_ordinary_dual_class_table_row_keeps_both_classes():
    sentence = (
        "Ordinary Shares outstanding before this offering "
        "1,911,893 Class A Ordinary Shares and 16,831 Class B Ordinary Shares."
    )
    row = _shares(dilution.extract_statements([sentence]))[0]
    assert row["basis"] == "before_offering"
    assert [(f["value"], f["share_class"]) for f in row["figures"]] == [
        (1911893, "A"), (16831, "B")]


def test_dual_class_counts_share_a_trailing_outstanding_qualifier():
    sentence = ("As of November 1, 2026, we had 10,000,000 Class A ordinary shares "
                "and 20,000 Class B ordinary shares outstanding.")
    row = _shares(dilution.extract_statements([sentence]))[0]
    assert [(f["value"], f["share_class"]) for f in row["figures"]] == [
        (10000000, "A"), (20000, "B")]
    assert row["basis"] == "stated"


def test_no_exercise_caveat_does_not_make_actual_count_hypothetical():
    sentence = ("As of November 1, 2026, we had 10,000,000 shares of common stock "
                "issued and outstanding, assuming no exercise of outstanding warrants.")
    row = _shares(dilution.extract_statements([sentence]))[0]
    assert row["basis"] == "stated"


def test_codx_baby_shelf_keeps_all_four_disclosures_without_recalculating_them():
    rows = [row for row in _fixture_rows("codx-20260727")
            if row["topic"] == "shelf_capacity"]
    assert len(rows) == 4
    assert [row["subtopic"] for row in rows] == [
        "baby_shelf_restriction", "mvphs_float", "prior_12m_sales",
        "remaining_eligible_capacity"]
    assert [f["value"] for f in rows[1]["figures"] if f["type"] == "shares"] == [5153941]
    assert [f["value"] for f in rows[1]["figures"] if f["type"] == "dollars"] == [49425570]
    assert [f["value"] for f in rows[2]["figures"] if f["type"] == "dollars"] == [11322816]
    assert [f["value"] for f in rows[3]["figures"] if f["type"] == "dollars"] == [7901383]
    assert all("smaller reporting company" not in row["sentence"] for row in rows)


@pytest.mark.parametrize("instruction", ["I.B.5", "I.B.6", "I. B. 6"])
def test_baby_shelf_restriction_without_a_dollar_figure_is_retained(instruction):
    sentence = f"We are subject to General Instruction {instruction} of our registration form."
    row = dilution.extract_statements([sentence])[0]
    assert row["topic"] == "shelf_capacity"
    assert row["subtopic"] == "baby_shelf_restriction"


@pytest.mark.parametrize("category", [
    "smaller reporting company", "emerging growth company", "accelerated filer",
    "large accelerated filer", "non-accelerated filer", "minimum listing maintenance",
])
def test_filer_category_thresholds_do_not_become_shelf_capacity(category):
    sentence = f"The {category} definition requires public float below $250 million."
    assert not [r for r in dilution.extract_statements([sentence])
                if r["topic"] == "shelf_capacity"]


def test_all_figures_are_contained_in_displayed_sentence_even_when_clipped():
    sentence = (
        "We entered into a public offering for $2 million and 1,000,000 common shares "
        + "with documented conditions " * 60
        + "and a separate 9,000,000 common shares."
    )
    rows = dilution.extract_statements([sentence])
    assert rows
    for row in rows:
        assert all(figure["text"] in row["sentence"] for figure in row["figures"])
        assert 9000000 not in [figure["value"] for figure in row["figures"]]


def test_clipping_a_large_dollar_amount_cannot_create_a_smaller_figure():
    prefix = "We entered into a public offering for $2 million with "
    sentence = prefix + "x" * (dilution.SENTENCE_MAX_CHARS - len(prefix) - 4) + "$100000000."
    rows = dilution.extract_statements([sentence])
    assert rows
    assert [f["value"] for f in rows[0]["figures"]] == [2000000]
