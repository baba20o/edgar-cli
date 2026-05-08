from edgar.api import EdgarClient, filing_urls, normalize_cik


def test_normalize_cik():
    assert normalize_cik("320193") == "0000320193"
    assert normalize_cik("CIK0000320193") == "0000320193"


def test_filing_urls():
    urls = filing_urls("0000320193", "0000320193-25-000079", "aapl-20250628.htm")
    assert urls["filing_url"].endswith(
        "/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm"
    )
    assert urls["primary_doc_url"].endswith(
        "/Archives/edgar/data/320193/000032019325000079/aapl-20250628.htm"
    )


def test_recent_filings_filters_and_adds_urls():
    data = {
        "cik": "0000320193",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000079", "0000320193-25-000057"],
                "filingDate": ["2025-08-01", "2025-05-02"],
                "form": ["10-Q", "8-K"],
                "primaryDocument": ["aapl-20250628.htm", "aapl-20250502.htm"],
                "primaryDocDescription": ["10-Q", "8-K"],
            }
        },
    }

    rows = EdgarClient._recent_filings(data, limit=10, form="10-Q")

    assert len(rows) == 1
    assert rows[0]["form"] == "10-Q"
    assert rows[0]["filing_url"].endswith("0000320193-25-000079-index.htm")


def test_recent_filings_filters_dates():
    data = {
        "cik": "0000320193",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000079", "0000320193-25-000057"],
                "filingDate": ["2025-08-01", "2025-05-02"],
                "form": ["10-Q", "8-K"],
                "primaryDocument": ["aapl-20250628.htm", "aapl-20250502.htm"],
            }
        },
    }

    rows = EdgarClient._recent_filings(
        data,
        limit=10,
        start_date="2025-06-01",
        end_date="2025-08-31",
    )

    assert len(rows) == 1
    assert rows[0]["filingDate"] == "2025-08-01"


def test_summarize_concepts_filters():
    facts = {
        "us-gaap": {
            "Assets": {
                "label": "Assets",
                "description": "Total assets",
                "units": {"USD": [{"filed": "2025-01-01"}, {"filed": "2025-02-01"}]},
            },
            "Revenues": {
                "label": "Revenue",
                "description": "Sales",
                "units": {"USD": [{"filed": "2024-01-01"}]},
            },
        }
    }

    rows = EdgarClient._summarize_concepts(facts, "us-gaap", "asset")

    assert len(rows) == 1
    assert rows[0]["tag"] == "Assets"
    assert rows[0]["fact_count"] == 2


def test_summarize_concepts_handles_null_metadata():
    facts = {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "label": None,
                "description": None,
                "units": {"USD": [{"filed": "2022-01-01"}]},
            },
        }
    }

    rows = EdgarClient._summarize_concepts(facts, "us-gaap", "revenue")

    assert len(rows) == 1
    assert rows[0]["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_resolve_company_rejects_blank_identifier():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    result = client.resolve_company("   ")

    assert result["error"] == "Company identifier cannot be blank"


def test_company_concept_quotes_path_segments():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    paths = []
    client.resolve_company = lambda identifier: {"cik": "0000320193", "name": "Apple Inc."}

    def fake_get(path, params=None, skip_cache=False):
        paths.append(path)
        return {"cik": 320193, "taxonomy": "us-gaap", "tag": "Assets/../Liabilities", "units": {}}

    client._get = fake_get
    client.company_concept("AAPL", "us-gaap", "Assets/../Liabilities")

    assert paths == ["/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets%2F..%2FLiabilities.json"]


def test_frame_quotes_path_segments():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    paths = []

    def fake_get(path, params=None, skip_cache=False):
        paths.append(path)
        return {"data": []}

    client._get = fake_get
    client.frame("us-gaap", "Assets/../Liabilities", "USD/shares", "CY2024Q4I/../CY2023")

    assert paths == ["/api/xbrl/frames/us-gaap/Assets%2F..%2FLiabilities/USD%2Fshares/CY2024Q4I%2F..%2FCY2023.json"]
