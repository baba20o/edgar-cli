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


def test_company_concept_adds_provenance_and_period_metadata():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda identifier: {"cik": "0000320193", "name": "Apple Inc."}

    def fake_get(path, params=None, skip_cache=False):
        return {
            "cik": 320193,
            "entityName": "Apple Inc.",
            "taxonomy": "us-gaap",
            "tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "units": {
                "USD": [{
                    "val": 100,
                    "accn": "0000320193-26-000013",
                    "start": "2025-12-28",
                    "end": "2026-03-28",
                    "filed": "2026-05-01",
                    "fy": 2026,
                    "fp": "Q2",
                    "frame": "CY2026Q1",
                }]
            },
        }

    client._get = fake_get

    result = client.company_concept("AAPL", "us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax")
    fact = result["facts"][0]

    assert fact["source_url"].endswith("0000320193-26-000013-index.htm")
    assert fact["accession"] == "0000320193-26-000013"
    assert fact["as_of"] == "2026-03-28"
    assert fact["period_type"] == "quarterly"
    assert fact["period_length_days"] == 90
    assert fact["fiscal_period"] == "Q2-FY2026"
    assert fact["calendar_period"] == "CY2026Q1"
    assert fact["is_cumulative"] is False
    assert fact["is_restated"] is False
    assert fact["superseded_by"] is None


def test_company_concept_filters_period_type():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda identifier: {"cik": "0000320193", "name": "Apple Inc."}

    def fake_get(path, params=None, skip_cache=False):
        return {
            "cik": 320193,
            "units": {
                "USD": [
                    {"val": 1, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-01-01"},
                    {"val": 2, "start": "2026-01-01", "end": "2026-03-31", "filed": "2026-04-01"},
                ]
            },
        }

    client._get = fake_get

    result = client.company_concept("AAPL", "us-gaap", "Revenues", period_type="quarterly")

    assert [fact["val"] for fact in result["facts"]] == [2]


def test_frame_quotes_path_segments():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    paths = []

    def fake_get(path, params=None, skip_cache=False):
        paths.append(path)
        return {"data": []}

    client._get = fake_get
    client.frame("us-gaap", "Assets/../Liabilities", "USD/shares", "CY2024Q4I/../CY2023")

    assert paths == ["/api/xbrl/frames/us-gaap/Assets%2F..%2FLiabilities/USD%2Fshares/CY2024Q4I%2F..%2FCY2023.json"]


def test_frame_rows_inherit_frame_for_period_metadata():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    def fake_get(path, params=None, skip_cache=False):
        return {
            "taxonomy": "us-gaap",
            "tag": "Revenues",
            "uom": "USD",
            "ccp": "CY2025Q1",
            "data": [
                {
                    "cik": 104169,
                    "entityName": "Walmart Inc.",
                    "val": 165609000000,
                    "accn": "0000104169-25-000090",
                    "start": "2025-02-01",
                    "end": "2025-04-30",
                    "filed": "2025-06-06",
                }
            ],
        }

    client._get = fake_get

    result = client.frame("us-gaap", "Revenues", "USD", "CY2025Q1")
    fact = result["facts"][0]

    assert fact["frame"] == "CY2025Q1"
    assert fact["calendar_period"] == "CY2025Q1"
    assert fact["period_type"] == "quarterly"


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


def test_metrics_reports_unknown_alias_without_sec_lookup():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.submissions = lambda identifier, **kwargs: {
        "cik": "0001045810",
        "ticker": "NVDA",
        "name": "NVIDIA CORP",
        "filings": [{"filingDate": "2026-02-25"}],
    }

    def fail_company_concept(*args, **kwargs):
        raise AssertionError("unknown metric aliases should not hit SEC concept endpoints")

    client.company_concept = fail_company_concept

    result = client.metrics("NVDA", ["fakemetric"])

    metric = result["metrics"][0]
    assert metric["metric"] == "fakemetric"
    assert metric["error"] == "Unknown metric alias 'fakemetric'"
    assert "revenue" in metric["known_aliases"]


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


# --- foundation tranche: cache, state, envelope, citation, _next_day ---


def test_ttl_for_url_picks_endpoint_specific_value():
    from edgar.cache import ttl_for_url

    assert ttl_for_url("https://www.sec.gov/files/company_tickers_exchange.json") == 7 * 86400
    assert ttl_for_url("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json") == 86400
    assert ttl_for_url("https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets.json") == 86400
    assert ttl_for_url("https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2024Q4I.json") == 90 * 86400
    assert ttl_for_url("https://data.sec.gov/submissions/CIK0000320193.json") == 3600
    assert ttl_for_url("https://example.com/unknown") == 900


def test_edgar_cache_miss_then_hit_with_meta(tmp_path):
    from edgar.cache import EdgarCache

    cache = EdgarCache(cache_dir=str(tmp_path))
    payload, meta = cache.get_with_meta("https://data.sec.gov/x.json")
    assert payload is None
    assert meta["hit"] is False

    cache.set("https://data.sec.gov/x.json", None, {"ok": 1}, etag='W/"abc"', last_modified="Tue")
    payload, meta = cache.get_with_meta("https://data.sec.gov/x.json")
    assert payload == {"ok": 1}
    assert meta["hit"] is True
    assert meta["etag"] == 'W/"abc"'
    assert meta["last_modified"] == "Tue"
    assert meta["age_seconds"] is not None
    assert meta["ttl_remaining"] is not None


def test_edgar_cache_negative_entry_round_trips(tmp_path):
    from edgar.cache import EdgarCache

    cache = EdgarCache(cache_dir=str(tmp_path))
    cache.set("https://data.sec.gov/missing.json", None, {"error": "404"}, negative=True)
    payload, meta = cache.get_with_meta("https://data.sec.gov/missing.json")
    assert payload == {"error": "404"}
    assert meta["hit"] is True
    assert meta["negative"] is True


def test_edgar_cache_refresh_timestamp_after_304(tmp_path):
    from edgar.cache import EdgarCache

    cache = EdgarCache(cache_dir=str(tmp_path), default_ttl=1)
    cache.set("https://data.sec.gov/x.json", None, {"ok": 1}, etag='"v1"')
    # Force the entry to be stale by rewriting its timestamp to long ago.
    path = cache._path(cache._key("https://data.sec.gov/x.json"))
    import json as _json
    data = _json.loads(path.read_text())
    data["_ts"] = 0
    path.write_text(_json.dumps(data))

    payload, meta = cache.get_with_meta("https://data.sec.gov/x.json")
    assert payload is None
    assert meta["stale_payload"] == {"ok": 1}
    assert meta["stale_etag"] == '"v1"'

    refreshed = cache.refresh_timestamp("https://data.sec.gov/x.json")
    assert refreshed is True
    payload, meta = cache.get_with_meta("https://data.sec.gov/x.json")
    assert payload == {"ok": 1}
    assert meta["hit"] is True


def test_state_store_high_water_round_trip(tmp_path):
    from edgar.state import StateStore

    store = StateStore(path=str(tmp_path / "state.json"))
    assert store.get_high_water("0000320193", "10-K") is None

    store.update_high_water("0000320193", "10-K", "0000320193-25-000079", "2025-10-31")
    hw = store.get_high_water("0000320193", "10-K")
    assert hw["accession"] == "0000320193-25-000079"
    assert hw["filed"] == "2025-10-31"

    # Older entries do not regress the high-water mark.
    store.update_high_water("0000320193", "10-K", "0000320193-23-000106", "2023-11-03")
    hw = store.get_high_water("0000320193", "10-K")
    assert hw["filed"] == "2025-10-31"

    # Fresh StateStore re-reads the file from disk.
    store2 = StateStore(path=str(tmp_path / "state.json"))
    assert store2.get_high_water("0000320193", "10-K")["accession"] == "0000320193-25-000079"


def test_state_store_reset_scopes(tmp_path):
    from edgar.state import StateStore

    store = StateStore(path=str(tmp_path / "state.json"))
    store.update_high_water("0000320193", "10-K", "a", "2025-10-31")
    store.update_high_water("0000320193", "10-Q", "b", "2025-08-01")
    store.update_high_water("0001045810", "10-K", "c", "2026-02-25")

    assert store.reset("0000320193", "10-K") == 1
    assert store.get_high_water("0000320193", "10-K") is None
    assert store.get_high_water("0000320193", "10-Q") is not None

    assert store.reset("0000320193") == 1  # remaining 10-Q for AAPL
    assert store.get_high_water("0000320193", "10-Q") is None

    assert store.reset() == 1  # NVDA 10-K only one left
    assert store.get_high_water("0001045810", "10-K") is None


def test_envelope_adds_schema_cli_and_cache_summary():
    from edgar.api import EdgarClient, SCHEMA_VERSION, CLI_VERSION

    client = EdgarClient.__new__(EdgarClient)
    client._cache_calls = [
        {"hit": True, "age_seconds": 60, "ttl_remaining": 540, "key": "k1", "etag": '"a"'},
        {"hit": False, "age_seconds": None, "ttl_remaining": None, "key": "k2", "etag": None},
    ]
    wrapped = client._envelope({"facts": [], "name": "Test"})
    assert wrapped["schema_version"] == SCHEMA_VERSION
    assert wrapped["cli_version"] == CLI_VERSION
    cache = wrapped["cache"]
    assert cache["calls"] == 2
    assert cache["hits"] == 1
    assert cache["misses"] == 1
    assert cache["age_max_seconds"] == 60
    assert cache["ttl_min_remaining"] == 540
    assert cache["last_key"] == "k2"
    assert cache["last_hit"] is False


def test_envelope_pass_through_for_non_dict():
    from edgar.api import EdgarClient

    client = EdgarClient.__new__(EdgarClient)
    client._cache_calls = []
    assert client._envelope("not a dict") == "not a dict"


def test_next_day_handles_year_rollover():
    from edgar.api import _next_day

    assert _next_day("2025-12-31") == "2026-01-01"
    assert _next_day("2025-02-28") == "2025-03-01"
    assert _next_day("not-a-date") == ""


# --- Phase 1: cache management ---


def test_edgar_cache_invalidate_pattern(tmp_path):
    from edgar.cache import EdgarCache

    cache = EdgarCache(cache_dir=str(tmp_path))
    cache.set("https://data.sec.gov/submissions/CIK0000320193.json", None, {"a": 1})
    cache.set("https://data.sec.gov/submissions/CIK0000789019.json", None, {"b": 2})
    cache.set("https://data.sec.gov/api/xbrl/frames/x.json", None, {"c": 3})
    removed = cache.invalidate("*CIK0000320193*")
    assert removed == 1
    payload, meta = cache.get_with_meta("https://data.sec.gov/submissions/CIK0000789019.json")
    assert payload == {"b": 2}


def test_edgar_cache_max_bytes_evicts_oldest(tmp_path):
    from edgar.cache import EdgarCache
    import time as _time

    cache = EdgarCache(cache_dir=str(tmp_path), max_bytes=200)
    cache.set("u1", None, {"x": "a" * 80})
    _time.sleep(0.01)
    cache.set("u2", None, {"x": "b" * 80})
    _time.sleep(0.01)
    cache.set("u3", None, {"x": "c" * 80})
    stats = cache.stats()
    assert stats["total"] <= 3
    assert stats["size_bytes"] <= 800  # eviction kept things bounded


# --- Phase 2 / 3: discovery + groups ---


def test_list_frames_enumerates_period_kinds():
    from edgar.api import EdgarClient

    out = EdgarClient.list_frames(since_year=2024, until_year=2024)
    frames = [f["frame"] for f in out["frames"]]
    assert "CY2024" in frames
    assert "CY2024Q1" in frames
    assert "CY2024Q1I" in frames
    assert out["total"] == 9  # 1 annual + 4 quarterly + 4 instant


def test_form_classes_filter():
    from edgar.api import EdgarClient

    data = {
        "cik": "0000320193",
        "filings": {"recent": {
            "accessionNumber": ["a", "b", "c", "d"],
            "filingDate": ["2025-01-01"] * 4,
            "form": ["10-K", "4", "SC 13G", "8-K"],
            "primaryDocument": [""] * 4,
        }},
    }
    insiders = EdgarClient._recent_filings(data, limit=10, form_class="insider")
    assert len(insiders) == 1 and insiders[0]["form"] == "4"
    inst = EdgarClient._recent_filings(data, limit=10, form_class="institutional")
    assert len(inst) == 1 and inst[0]["form"] == "SC 13G"
    major = EdgarClient._recent_filings(data, limit=10, form_class="major")
    assert {row["form"] for row in major} == {"10-K", "8-K"}


# --- Phase 5: computed metrics primitives ---


def test_compute_gross_margin_uses_gross_profit_when_available():
    from edgar.compute import gross_margin

    rev = {"val": 100, "tag": "Revenues"}
    gp = {"val": 40, "tag": "GrossProfit"}
    out = gross_margin(rev, None, gp)
    assert abs(out["value"] - 0.4) < 1e-9
    assert "GrossProfit / Revenue" in out["formula"]


def test_compute_gross_margin_falls_back_to_revenue_minus_cogs():
    from edgar.compute import gross_margin

    rev = {"val": 100, "tag": "Revenues"}
    cogs = {"val": 60, "tag": "CostOfGoodsAndServicesSold"}
    out = gross_margin(rev, cogs)
    assert abs(out["value"] - 0.4) < 1e-9


def test_compute_returns_missing_inputs_when_facts_absent():
    from edgar.compute import operating_margin

    out = operating_margin(None, None)
    assert out["value"] is None
    assert "Revenue" in out["missing_inputs"]


def test_compute_ttm_sums_four_quarters():
    from edgar.compute import ttm_from_quarters

    quarters = [
        {"val": 100, "end": "2026-03-31", "period_type": "quarterly"},
        {"val": 90, "end": "2025-12-31", "period_type": "quarterly"},
        {"val": 80, "end": "2025-09-30", "period_type": "quarterly"},
        {"val": 70, "end": "2025-06-30", "period_type": "quarterly"},
        {"val": 60, "end": "2025-03-31", "period_type": "quarterly"},
    ]
    out = ttm_from_quarters(quarters)
    assert out["value"] == 340
    assert len(out["inputs"]) == 4


def test_compute_trend_label_categorizes():
    from edgar.compute import trend_summary

    facts = [{"val": v, "end": f"2025-{m:02d}-01"} for m, v in [(1, 100), (3, 110), (6, 120), (9, 130)]]
    out = trend_summary(facts)
    assert out["label"] == "expanding"
    assert out["direction"] == "up"

    flat_facts = [{"val": 100, "end": f"2025-{m:02d}-01"} for m in (1, 4, 7, 10)]
    assert trend_summary(flat_facts)["label"] == "stable"


def test_compute_cagr_handles_two_years():
    from edgar.compute import cagr

    # 100 -> 144 over 2 years = 20% CAGR
    assert abs(cagr([100, 120, 144], periods_per_year=1) - 0.2) < 1e-6
    assert cagr([100], periods_per_year=1) is None
    assert cagr([0, 100], periods_per_year=1) is None


# --- Phase 6: audit / delta ---


def test_audit_trail_detects_restatement_in_synthesized_facts():
    """Verify the restatement detection logic operates on synthesized inputs."""
    from edgar.api import EdgarClient

    client = EdgarClient.__new__(EdgarClient)

    # Stub company_concept_alias to inject two facts for the same period with
    # different values — that's exactly what a restatement looks like.
    def fake_alias(identifier, concept, **kwargs):
        return {
            "cik": "0000320193", "name": "Test Inc.",
            "facts": [
                {"start": "2024-01-01", "end": "2024-12-31", "val": 100,
                 "filed": "2025-02-01", "form": "10-K", "accn": "x-1", "source_tag": "Revenues"},
                {"start": "2024-01-01", "end": "2024-12-31", "val": 110,
                 "filed": "2026-02-01", "form": "10-K", "accn": "x-2", "source_tag": "Revenues"},
            ],
        }

    client.company_concept_alias = fake_alias
    out = client.audit_trail("X", "revenue")
    assert len(out["restated_periods"]) == 1
    assert sorted(out["restated_periods"][0]["values_seen"]) == [100, 110]


# --- Phase 7: composability ---


def test_resolve_company_returns_ambiguity_metadata():
    from edgar.api import EdgarClient

    client = EdgarClient.__new__(EdgarClient)
    captured = {}

    def fake_resolve(identifier):
        return {"error": f"Ambiguous company identifier {identifier}; matches: A, B"}

    def fake_search(query, limit=10):
        captured["query"] = query
        return {"companies": [
            {"ticker": "AAA", "cik": "1", "name": "Alpha", "exchange": "X"},
            {"ticker": "BBB", "cik": "2", "name": "Beta", "exchange": "X"},
        ]}

    client.resolve_company = fake_resolve
    client.search_companies = fake_search
    out = client._resolve_one("FOO")
    assert "candidates" in out
    assert {c["ticker"] for c in out["candidates"]} == {"AAA", "BBB"}
    assert captured["query"] == "FOO"


# --- Phase 8: bundles + earnings narrative ---


def test_metric_bundle_groups_define_canonical_groups():
    from edgar.api import METRIC_BUNDLE_GROUPS

    assert "income-statement" in METRIC_BUNDLE_GROUPS
    assert "revenue" in METRIC_BUNDLE_GROUPS["income-statement"]
    assert "assets" in METRIC_BUNDLE_GROUPS["balance-sheet"]


# --- regressions for reviewer's 8 findings ---


def test_ttm_suppresses_value_when_quarters_have_gap():
    from edgar.compute import ttm_from_quarters

    quarters = [
        {"val": 100, "end": "2026-03-28", "period_type": "quarterly"},
        {"val": 90, "end": "2025-12-27", "period_type": "quarterly"},
        # Q4 missing — gap from Sep to Dec
        {"val": 70, "end": "2025-06-28", "period_type": "quarterly"},
        {"val": 60, "end": "2025-03-29", "period_type": "quarterly"},
    ]
    out = ttm_from_quarters(quarters)
    assert out["value"] is None
    assert any("not contiguous" in c for c in out["caveats"])


def test_ttm_from_stub_period_reconstructs_apple_style():
    from edgar.compute import ttm_from_stub_period

    annual = {"val": 416161, "end": "2025-09-27", "start": "2024-09-29"}
    current_ytd = {"val": 254940, "end": "2026-03-28", "start": "2025-09-28"}
    prior_ytd = {"val": 219659, "end": "2025-03-29", "start": "2024-09-30"}
    out = ttm_from_stub_period(annual, current_ytd, prior_ytd)
    assert out["value"] == 416161 + 254940 - 219659
    assert "AnnualFY + CurrentYTD - PriorYTD" in out["formula"]


def test_diff_aligns_on_frame_when_fiscal_calendars_differ():
    """Filers with different FY ends must still pair up by calendar frame."""
    from edgar.api import EdgarClient

    client = EdgarClient.__new__(EdgarClient)

    def fake_compare(identifiers, concept, **kwargs):
        return {
            "concept": concept,
            "companies": [
                {
                    "identifier": "AAPL", "name": "Apple Inc.",
                    "facts": [
                        {"frame": "CY2025", "start": "2024-09-29", "end": "2025-09-27", "val": 416161},
                        {"frame": "CY2024", "start": "2023-09-30", "end": "2024-09-28", "val": 391035},
                    ],
                },
                {
                    "identifier": "MSFT", "name": "Microsoft Corp",
                    "facts": [
                        {"frame": "CY2025", "start": "2024-07-01", "end": "2025-06-30", "val": 281724},
                        {"frame": "CY2024", "start": "2023-07-01", "end": "2024-06-30", "val": 245122},
                    ],
                },
            ],
        }

    client.compare_concept = fake_compare
    out = client.diff_concept("AAPL", "MSFT", "revenue", periods=2)
    by_frame = {r["frame"]: r for r in out["rows"]}
    assert by_frame["CY2025"]["a_value"] == 416161
    assert by_frame["CY2025"]["b_value"] == 281724
    assert by_frame["CY2025"]["delta"] == 416161 - 281724
    # Periods are preserved per-side so callers can still see the calendar mismatch.
    assert by_frame["CY2025"]["a_period"] == ("2024-09-29", "2025-09-27")
    assert by_frame["CY2025"]["b_period"] == ("2024-07-01", "2025-06-30")


def test_compute_envelope_keeps_value_when_only_optional_input_missing():
    from edgar.compute import quick_ratio

    out = quick_ratio({"val": 100}, None, {"val": 50})
    assert out["value"] == 2.0
    assert out.get("missing_inputs") is None
    assert out.get("optional_missing_inputs") == ["InventoryNet"]


def test_extract_earnings_highlights_skips_table_dumps():
    from edgar.api import extract_earnings_highlights

    table_dump = ("Three Months Ended Six Months Ended March 28, 2026 March 29, 2025 "
                  "March 28, 2026 March 29, 2025 Net sales: Products $ 80,208 $ 68,714 "
                  "$ 193,951 $ 166,674 Services 30,976 26,645 60,989 52,985 Total net "
                  "sales 111,184 95,359 254,940 219,659.")
    narrative = ("The Company posted quarterly revenue of $111.2 billion, up "
                 "17 percent year over year.")
    text = table_dump + " " + narrative
    out = extract_earnings_highlights(text)
    texts = [h["text"] for h in out]
    assert any("posted quarterly revenue" in t for t in texts)
    assert not any("Three Months Ended Six Months" in t for t in texts)
