"""Synthetic IFRS-only issuer verifies alias and currency behavior offline."""
from edgar.api import EdgarClient, concept_alias_candidates


def ifrs_client():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda _: {"cik": "0000000001", "name": "IFRS fixture"}
    client.submissions = lambda *a, **k: {"cik": "0000000001", "filings": [{"filingDate": "2026-04-01"}]}
    def get(path, **kwargs):
        if '/ifrs-full/Revenue.json' in path:
            return {"taxonomy": "ifrs-full", "tag": "Revenue", "units": {"EUR": [
                {"val": 123, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-04-01", "form": "20-F", "accn": "0000000001-26-000001"}]}}
        return {"error": "404 unavailable in fixture"}
    client._get = get
    return client


def test_ifrs_metrics_keep_reported_currency():
    metric = ifrs_client().metrics("1", ["revenue"])["metrics"][0]
    assert metric["taxonomy"] == "ifrs-full"
    assert metric["unit"] == "EUR"
    assert metric["fact"]["val"] == 123


def test_canonical_ifrs_concept_and_explicit_currency():
    client = ifrs_client()
    facts = client.company_concept_alias("1", "revenue", canonical_union=True)["facts"]
    assert facts[0]["source_tag"] == "Revenue"
    assert facts[0]["unit"] == "EUR"
    assert client.company_concept_alias("1", "revenue", unit="USD")["facts"] == []


def test_us_gaap_precedence_and_no_monetary_share_alias():
    assert concept_alias_candidates("revenue")[0][0] == "us-gaap"
    shares = concept_alias_candidates("shares_outstanding")
    assert ("ifrs-full", "NumberOfSharesOutstanding", "shares") in shares
    assert not any(tag == "IssuedCapital" for _, tag, _ in shares)
    assert not any(tag in {"Borrowings", "NoncurrentLiabilities"}
                   for _, tag, _ in concept_alias_candidates("debt"))
