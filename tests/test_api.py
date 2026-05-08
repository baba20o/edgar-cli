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
