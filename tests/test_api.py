from edgar.api import (
    EdgarClient,
    FilingIndexParser,
    concept_alias_candidates,
    event_types_from_items,
    extract_accession,
    extract_cik_from_url,
    filing_urls,
    normalize_cik,
    resolve_concept_alias,
)


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


def test_submissions_all_history_fetches_historical_chunks():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda identifier: {
        "cik": "0001045810",
        "ticker": "NVDA",
        "name": "NVIDIA CORP",
        "exchange": "Nasdaq",
    }
    paths = []

    def fake_get(path, params=None, skip_cache=False):
        paths.append(path)
        if path == "/submissions/CIK0001045810.json":
            return {
                "cik": "0001045810",
                "name": "NVIDIA CORP",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0001045810-26-000001"],
                        "filingDate": ["2026-01-01"],
                        "form": ["10-K"],
                        "primaryDocument": ["nvda-20260101.htm"],
                    },
                    "files": [{"name": "CIK0001045810-submissions-001.json"}],
                },
            }
        if path == "/submissions/CIK0001045810-submissions-001.json":
            return {
                "accessionNumber": ["0001045810-99-000001"],
                "filingDate": ["1999-01-22"],
                "form": ["S-1"],
                "primaryDocument": ["nvda-s1.htm"],
            }
        return {"error": f"Unexpected path {path}"}

    client._get = fake_get

    result = client.submissions("NVDA", form="S-1", limit=1, all_history=True)

    assert result["filings"][0]["form"] == "S-1"
    assert result["history_files_checked"] == 1
    assert "Searched recent filings plus 1 historical chunk" in result["warning"]
    assert paths == [
        "/submissions/CIK0001045810.json",
        "/submissions/CIK0001045810-submissions-001.json",
    ]


def test_submissions_does_not_warn_only_because_history_chunks_exist():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda identifier: {"cik": "0000320193", "name": "Apple Inc."}

    def fake_get(path, params=None, skip_cache=False):
        return {
            "cik": "0000320193",
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001"],
                    "filingDate": ["2026-01-01"],
                    "form": ["10-K"],
                    "primaryDocument": ["aapl-20260101.htm"],
                },
                "files": [{"name": "CIK0000320193-submissions-001.json"}],
            },
        }

    client._get = fake_get

    result = client.submissions("AAPL", limit=10)

    assert result["warning"] == ""


def test_submissions_warns_when_filtered_recent_matches_hit_limit():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda identifier: {"cik": "0000320193", "name": "Apple Inc."}

    def fake_get(path, params=None, skip_cache=False):
        return {
            "cik": "0000320193",
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000002", "0000320193-26-000001"],
                    "filingDate": ["2026-02-01", "2026-01-01"],
                    "form": ["8-K", "8-K"],
                    "primaryDocument": ["aapl-20260201.htm", "aapl-20260101.htm"],
                },
                "files": [{"name": "CIK0000320193-submissions-001.json"}],
            },
        }

    client._get = fake_get

    result = client.submissions("AAPL", form="8-K", limit=1)

    assert len(result["filings"]) == 1
    assert result["warning"].startswith("Showing the first 1 matching recent filings")


def test_submissions_warns_when_date_filter_reaches_recent_boundary():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda identifier: {"cik": "0000320193", "name": "Apple Inc."}

    def fake_get(path, params=None, skip_cache=False):
        return {
            "cik": "0000320193",
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001", "0000320193-25-000001"],
                    "filingDate": ["2026-01-01", "2025-01-01"],
                    "form": ["10-K", "10-K"],
                    "primaryDocument": ["aapl-20260101.htm", "aapl-20250101.htm"],
                },
                "files": [{"name": "CIK0000320193-submissions-001.json"}],
            },
        }

    client._get = fake_get

    result = client.submissions("AAPL", start_date="2025-01-01", limit=10)

    assert "oldest SEC recent filing (2025-01-01)" in result["warning"]


def test_company_concept_404_includes_similar_tag_suggestions():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda identifier: {"cik": "0000320193", "name": "Apple Inc."}

    def fake_get(path, params=None, skip_cache=False):
        if path.startswith("/api/xbrl/companyconcept/"):
            return {"error": "404 Not Found: missing concept"}
        if path == "/api/xbrl/companyfacts/CIK0000320193.json":
            return {
                "cik": 320193,
                "entityName": "Apple Inc.",
                "facts": {
                    "us-gaap": {
                        "RevenueFromContractWithCustomerExcludingAssessedTax": {
                            "label": "Revenue",
                            "description": "Revenue from contracts with customers",
                            "units": {"USD": [{"filed": "2026-01-01"}]},
                        }
                    }
                },
            }
        return {"error": f"Unexpected path {path}"}

    client._get = fake_get

    result = client.company_concept("AAPL", "us-gaap", "Revenues")

    assert result["error"].startswith("404")
    assert result["suggestions"][0]["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_resolve_concept_aliases_common_metrics():
    assert resolve_concept_alias("revenue") == (
        "us-gaap",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "USD",
    )
    assert resolve_concept_alias("Revenues") == ("us-gaap", "Revenues", None)
    assert resolve_concept_alias("Assets", unit="EUR") == ("us-gaap", "Assets", "EUR")
    assert resolve_concept_alias("CustomTag") == ("us-gaap", "CustomTag", None)


def test_filing_index_parser_and_url_extractors():
    html = """
    <table>
      <tr>
        <td>1</td><td>Press release</td>
        <td><a href="press.htm">press.htm</a></td><td>EX-99.1</td><td>1234</td>
      </tr>
    </table>
    """
    parser = FilingIndexParser()
    parser.feed(html)

    assert parser.rows[0]["document"] == "press.htm"
    assert parser.rows[0]["type"] == "EX-99.1"
    url = "https://www.sec.gov/Archives/edgar/data/1831097/000162828026031254/0001628280-26-031254-index.htm"
    assert extract_accession(url) == "0001628280-26-031254"
    assert extract_cik_from_url(url) == "0001831097"


def test_filing_documents_resolves_sec_root_relative_links():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client._get_text = lambda url: """
    <table>
      <tr>
        <td>2</td><td>EX-99.1</td>
        <td><a href="/Archives/edgar/data/1831097/000162828026031254/agl-ex991.htm">agl-ex991.htm</a></td>
        <td>EX-99.1</td><td>1234</td>
      </tr>
    </table>
    """

    result = client.filing_documents("1831097", "0001628280-26-031254")

    assert result["documents"][0]["url"] == (
        "https://www.sec.gov/Archives/edgar/data/1831097/000162828026031254/agl-ex991.htm"
    )


def test_filing_documents_rejects_invalid_cik_without_traceback():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    result = client.filing_documents_for_accession("0001045810-26-000019", cik="abc; rm -rf /tmp/x")

    assert result["error"] == "Not a CIK: abc; rm -rf /tmp/x"
    assert result["documents"] == []


def test_concept_alias_candidates_include_revenue_fallbacks():
    assert concept_alias_candidates("revenue")[:2] == [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
        ("us-gaap", "Revenues", "USD"),
    ]


def test_best_metric_prefers_fresher_revenue_fallback_tag():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    def fake_company_concept(identifier, taxonomy, tag, unit=None, limit=20, **kwargs):
        if tag == "RevenueFromContractWithCustomerExcludingAssessedTax":
            return {
                "taxonomy": taxonomy,
                "tag": tag,
                "facts": [{
                    "val": 100,
                    "unit": unit,
                    "start": "2021-01-01",
                    "end": "2021-12-31",
                    "filed": "2022-01-31",
                    "frame": "CY2021",
                }],
            }
        if tag == "Revenues":
            return {
                "taxonomy": taxonomy,
                "tag": tag,
                "facts": [{
                    "val": 800,
                    "unit": unit,
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "filed": "2026-01-31",
                    "frame": "CY2025",
                }],
            }
        return {"error": "404 Not Found"}

    client.company_concept = fake_company_concept

    metric = client._best_metric("revenue", "0001045810", "2026-02-15")

    assert metric["tag"] == "Revenues"
    assert metric["fact"]["val"] == 800
    assert metric["stale"] is False


def test_company_concept_alias_prefers_fresher_candidate_tag():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    def fake_company_concept(identifier, taxonomy, tag, unit=None, limit=20, **kwargs):
        if tag == "RevenueFromContractWithCustomerExcludingAssessedTax":
            return {
                "taxonomy": taxonomy,
                "tag": tag,
                "facts": [{"val": 100, "end": "2021-12-31", "filed": "2022-01-31"}],
            }
        if tag == "Revenues":
            return {
                "taxonomy": taxonomy,
                "tag": tag,
                "facts": [{"val": 800, "end": "2025-12-31", "filed": "2026-01-31"}],
            }
        return {"error": "404 Not Found"}

    client.company_concept = fake_company_concept

    result = client.company_concept_alias("NVDA", "revenue")

    assert result["tag"] == "Revenues"
    assert result["alias"] == "revenue"


def test_best_metric_flags_stale_metric():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    def fake_company_concept(identifier, taxonomy, tag, unit=None, limit=20, **kwargs):
        if tag == "Assets":
            return {
                "taxonomy": taxonomy,
                "tag": tag,
                "facts": [{
                    "val": 100,
                    "unit": unit,
                    "end": "2021-12-31",
                    "filed": "2022-01-31",
                    "frame": "CY2021Q4I",
                }],
            }
        return {"error": "404 Not Found"}

    client.company_concept = fake_company_concept

    metric = client._best_metric("assets", "0001045810", "2026-02-15")

    assert metric["stale"] is True
    assert metric["age_days"] > 548


def test_compare_alias_aligns_on_shared_frames_across_fallback_tags():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    def fake_company_concept(identifier, taxonomy, tag, unit=None, limit=20, **kwargs):
        if identifier == "AAPL" and tag == "RevenueFromContractWithCustomerExcludingAssessedTax":
            return {
                "cik": "0000320193",
                "name": "Apple Inc.",
                "taxonomy": taxonomy,
                "tag": tag,
                "facts": [
                    {"val": 10, "unit": unit, "start": "2026-01-01", "end": "2026-03-31", "filed": "2026-04-01", "frame": "CY2026Q1"},
                    {"val": 40, "unit": unit, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-01-31", "frame": "CY2025"},
                    {"val": 8, "unit": unit, "start": "2025-01-01", "end": "2025-03-31", "filed": "2025-04-01", "frame": "CY2025Q1"},
                ],
            }
        if identifier == "GOOGL" and tag == "Revenues":
            return {
                "cik": "0001652044",
                "name": "Alphabet Inc.",
                "taxonomy": taxonomy,
                "tag": tag,
                "facts": [
                    {"val": 9, "unit": unit, "start": "2026-01-01", "end": "2026-03-31", "filed": "2026-04-01", "frame": "CY2026Q1"},
                    {"val": 35, "unit": unit, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-01-31", "frame": "CY2025"},
                    {"val": 7, "unit": unit, "start": "2025-01-01", "end": "2025-03-31", "filed": "2025-04-01", "frame": "CY2025Q1"},
                ],
            }
        return {"error": "404 Not Found"}

    client.company_concept = fake_company_concept

    result = client.compare_concept(["AAPL", "GOOGL"], "revenue", periods=2)

    assert result["frames"] == ["CY2026Q1", "CY2025Q1"]
    assert [company["facts"][0]["frame"] for company in result["companies"]] == ["CY2026Q1", "CY2026Q1"]
    assert result["companies"][1]["facts"][0]["_tag"] == "Revenues"
    assert result["warnings"] == ["Aligned on quarterly frames."]


def test_suggest_concepts_avoids_weak_false_positive():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.company_facts = lambda identifier, taxonomy=None, limit=50: {
        "concepts": [{
            "taxonomy": "us-gaap",
            "tag": "NotesReceivableNet",
            "label": "Notes Receivable, Net",
            "description": "Notes receivable after allowances",
        }]
    }

    assert client.suggest_concepts("TSLA", "us-gaap", "NotARealConcept") == []


def test_event_types_from_8k_items():
    assert event_types_from_items("2.02, 5.02, 3.01") == {"earnings", "leadership", "delisting"}
