"""Deterministic regressions for Part/Item selection and HTML boundaries."""

from pathlib import Path

import pytest

from edgar.api import EdgarClient
from edgar.items import extract_section, find_items, html_to_section_text


VRT_QUARTERLY = """
<html><head><title>Vertiv 10-Q</title></head><body>
<table>
  <tr><td><a href="#part1">PART I</a></td></tr>
  <tr><td>Item 2.</td><td><a href="#mda">Management&#8217;s Discussion and Analysis</a></td><td>24</td></tr>
  <tr><td><a href="#part2">PART II</a></td></tr>
  <tr><td><a href="#sales">Item 2. Unregistered Sales of Equity Securities and Use of Proceeds</a></td><td>38</td></tr>
</table>
<div>PART I. FINANCIAL INFORMATION</div>
<div>ITEM 1. FINANCIAL STATEMENTS</div>
<p>Quarterly financial statements.</p>
<div><a href="#toc">Table of contents</a></div>
<div><span>ITEM 2. </span><span>MANAGEMENT&#8217;S DISCUSSION AND ANALYSIS
OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS</span></div>
<p>Quarterly operating performance and liquidity discussion.</p>
<p>See <span>Item 3. Quantitative and Qualitative Disclosures About Market Risk</span>
for further information. This sentence is still MD&amp;A.</p>
<table><tr><td>Revenue</td><td>123</td></tr></table>
<p>The final MD&amp;A paragraph.</p>
<div>ITEM 3. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK</div>
<p>Market risk discussion.</p>
<div>ITEM 4. CONTROLS AND PROCEDURES</div><p>Controls discussion.</p>
<div>PART II. OTHER INFORMATION</div>
<div>ITEM 1. LEGAL PROCEEDINGS</div><p>Legal discussion.</p>
<div>ITEM 1A. RISK FACTORS</div><p>Risk discussion.</p>
<div>ITEM 2. UNREGISTERED SALES OF EQUITY SECURITIES AND USE OF PROCEEDS</div>
<p>Repurchases and unregistered sales only.</p>
<div>ITEM 3. DEFAULTS UPON SENIOR SECURITIES</div><p>None.</p>
</body></html>
"""

VRT_BUSINESS = """
<div>Item 1. Business</div><p>Business overview and backlog.</p>
<p>Our backlog estimates are subject to risks, as further detailed in
&#8220;<span>Item 1A. Risk Factors</span>&#8221; of this Form 10-K.</p>
<p>Our products, customers and competition remain part of Business.</p>
<div><a href="#toc">Table of contents</a></div>
<div>Item 1A. Risk Factors</div><p>Actual risk factors begin here.</p>
<div>Item 1B. Unresolved Staff Comments</div><p>None.</p>
"""


@pytest.mark.parametrize("section", [
    "2", "Item 2.", "MD&A", "mda", "Management's Discussion",
    "Management\u2019s Discussion and Analysis", "Part I Item 2",
])
def test_vrt_quarterly_selects_part_i_mda(section):
    text = html_to_section_text(VRT_QUARTERLY)
    result = extract_section(text, section, "10-Q")
    assert result["part"] == "I"
    assert result["item"] == "2"
    assert result["text"].startswith("ITEM 2. MANAGEMENT\u2019S DISCUSSION")
    assert "Quarterly operating performance" in result["text"]
    assert "This sentence is still MD&A." in result["text"]
    assert "Revenue 123" in result["text"]
    assert result["text"].endswith("The final MD&A paragraph.")
    assert "Repurchases" not in result["text"]
    assert result["confidence"] == "high"


def test_quarterly_explicit_part_and_part_title():
    text = html_to_section_text(VRT_QUARTERLY)
    for section in ("Part II, Item 2", "Unregistered Sales"):
        result = extract_section(text, section, "10-Q")
        assert result["part"] == "II"
        assert "Repurchases" in result["text"]
        assert "Quarterly operating performance" not in result["text"]
    identities = [(entry["part"], entry["item"]) for entry in find_items(text, "10-Q")]
    assert ("I", "2") in identities and ("II", "2") in identities


def test_business_continues_past_quoted_inline_reference():
    text = html_to_section_text(VRT_BUSINESS)
    result = extract_section(text, "Business")
    assert "Item 1A. Risk Factors" in result["text"]
    assert result["text"].endswith("Our products, customers and competition remain part of Business.")
    assert "Actual risk factors" not in result["text"]
    assert result["confidence"] == "high"


@pytest.mark.parametrize("prefix", ["", "PART II. OTHER INFORMATION\n\n"])
def test_conflicting_quarterly_title_is_never_relabelled_mda(prefix):
    text = prefix + (
        "ITEM 2. UNREGISTERED SALES OF EQUITY SECURITIES AND USE OF PROCEEDS\n\n"
        "Repurchases.\n\nITEM 3. DEFAULTS UPON SENIOR SECURITIES\n\nNone."
    )
    result = extract_section(text, "2", "10-Q")
    assert "error" in result


@pytest.mark.parametrize("schema,item,caption,next_heading,confidence", [
    ("10-K", "15", "Exhibits and Financial Statement Schedules",
     "Item 16. Form 10-K Summary", "high"),
    ("10-K", "15", "Exhibits, Financial Statement Schedules",
     "Item 16. Form 10-K Summary", "high"),
    ("10-K", "5", "Market for the Registrant's Common Equity",
     "Item 6. Reserved", "low"),
    ("10-Q", "1", "Condensed Consolidated Financial Statements (Unaudited)",
     "Item 2. Management's Discussion and Analysis", "low"),
])
def test_variant_heading_captions_remain_extractable(schema, item, caption, next_heading, confidence):
    text = f"Item {item}. {caption}\n\nActual section body.\n\n{next_heading}\n\nNext body."
    result = extract_section(text, item, schema)
    assert "error" not in result
    assert result["text"].endswith("Actual section body.")
    assert "Next body" not in result["text"]
    assert result["confidence"] == confidence


def test_variant_heading_bounds_preceding_section():
    text = (
        "Item 14. Principal Accountant Fees and Services\n\nAudit fees.\n\n"
        "Item 15. Exhibits and Financial Statement Schedules\n\nExhibit index."
    )
    assert extract_section(text, "14")["text"].endswith("Audit fees.")


def test_old_exhibits_title_remains_a_request_alias():
    text = "Item 15. Exhibits and Financial Statement Schedules\n\nExhibit index."
    assert extract_section(text, "Exhibits, Financial Statement Schedules")["item"] == "15"


def test_part_transition_bounds_final_part_i_item():
    text = (
        "PART I\n\nItem 4. Controls and Procedures\n\nEffective controls.\n\n"
        "PART II. OTHER INFORMATION\n\nOther material without a recognized item."
    )
    result = extract_section(text, "4", "10-Q")
    assert result["text"].endswith("Effective controls.")
    assert result["confidence"] == "medium"


@pytest.mark.parametrize("text,confidence", [
    ("Item 1. Business\n\nBusiness prose.\n\nItem 2. Properties\n\nOffices.", "medium"),
    ("Item 1. Business\n\nBusiness prose.", "medium"),
    ("Item 1.\n\nOperational prose.\n\nItem 1A.\n\nHazards prose.", "low"),
    ("Item 1. Business Business prose. Item 1A. Risk Factors Risk prose.", "low"),
])
def test_confidence_requires_identity_and_immediate_boundary(text, confidence):
    assert extract_section(text, "1")["confidence"] == confidence


def test_nested_toc_cells_and_same_block_navigation():
    html = """
    <table><tr><td><p>Item 1.</p></td>
    <td><p><a href="#business">Business</a></p></td><td><p>6</p></td></tr>
    <tr><td><p>Item 1A.</p></td>
    <td><p><a href="#risks">Risk Factors</a></p></td><td><p>9</p></td></tr></table>
    <div><a href="#toc">Table of contents</a><span>Item 1. Business</span></div>
    <p>Actual business narrative.</p>
    <div>Item 1A. Risk Factors</div><p>Actual risks.</p>
    """
    result = extract_section(html_to_section_text(html), "Business")
    assert result["text"].startswith("Item 1. Business")
    assert result["text"].endswith("Actual business narrative.")
    assert result["confidence"] == "high"


def test_unlinked_part_label_in_toc_does_not_set_body_identity():
    html = """
    <table><tr><td>PART II. OTHER INFORMATION</td></tr>
    <tr><td><a href="#sales">Item 2. Unregistered Sales of Equity Securities and Use of Proceeds</a></td></tr>
    </table>
    <div>Item 2. Management's Discussion and Analysis</div><p>Actual MD&amp;A.</p>
    <div>Item 3. Quantitative and Qualitative Disclosures About Market Risk</div>
    """
    result = extract_section(html_to_section_text(html), "2", "10-Q")
    assert result["part"] == "I"
    assert result["text"].endswith("Actual MD&A.")
    assert result["confidence"] == "high"


@pytest.mark.parametrize("html,schema,section,expected", [
    (
        "<h2>Item 2.</h2><h2>Management's Discussion and Analysis of Financial "
        "Condition and Results of Operations</h2><p>NVIDIA operating results.</p>"
        "<h2>Item 3. Quantitative and Qualitative Disclosures About Market Risk</h2>",
        "10-Q", "MD&A", "NVIDIA operating results.",
    ),
    (
        "<table><tr><td>ITEM 7.</td><td>MANAGEMENT'S DISCUSSION AND ANALYSIS OF "
        "FINANCIAL CONDITION AND RESULTS OF OPERATIONS</td></tr></table>"
        "<p>Microsoft operating results.</p><p>ITEM 7A. QUANTITATIVE AND "
        "QUALITATIVE DISCLOSURES ABOUT MARKET RISK</p>",
        "10-K", "7", "Microsoft operating results.",
    ),
])
def test_common_nvidia_and_microsoft_heading_layouts(html, schema, section, expected):
    result = extract_section(html_to_section_text(html), section, schema)
    assert expected in result["text"]
    assert result["confidence"] == "high"


def test_filing_section_api_preserves_structure_and_truncation(monkeypatch):
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    monkeypatch.setattr(client, "resolve_company", lambda identifier: {"cik": "0001674101"})
    monkeypatch.setattr(client, "submissions", lambda *args, **kwargs: {"filings": [{
        "accessionNumber": "0001628280-26-050609", "form": "10-Q",
        "primary_doc_url": "https://www.sec.gov/Archives/edgar/data/1674101/000162828026050609/vrt.htm",
    }]})
    monkeypatch.setattr(client, "_get_text", lambda url: VRT_QUARTERLY)
    result = client.filing_section("VRT", form="10-Q", section="MD&A", max_chars=100)
    assert result["section"]["part"] == "I"
    assert result["section"]["confidence"] == "high"
    assert result["section"]["truncated_to_max_chars"] is True
    assert len(result["section"]["text"]) == 100
    assert result["section"]["length"] > 100


@pytest.mark.parametrize("filename,schema,section", [
    ("vrt-direct.html", "10-Q", "2"),
    ("msft-direct.html", "10-K", "7"),
])
def test_saved_original_mda_when_available(filename, schema, section):
    path = Path(__file__).resolve().parents[1] / "mission-input" / filename
    if not path.exists():
        pytest.skip("Optional local mission evidence is not distributed as a fixture")
    text = html_to_section_text(path.read_text())
    result = extract_section(text, section, schema)
    assert "error" not in result
    assert result["title"] == "Management's Discussion and Analysis"
    assert result["length"] > 10000
    assert result["confidence"] == "high"
    assert "DISCUSSION" in result["text"][:200].upper()
    assert text[result["end"]:].lstrip().upper().startswith("ITEM")
    if schema == "10-Q":
        assert result["part"] == "I"
    assert "unregistered sales" not in result["text"][:150].lower()
