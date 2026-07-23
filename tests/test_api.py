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

    # Linear growth — no change point, expanding label.
    facts = [{"val": v, "end": f"2025-{m:02d}-01"} for m, v in [(1, 100), (3, 110), (6, 120), (9, 130)]]
    out = trend_summary(facts)
    assert out["label"] == "expanding"
    assert out["direction"] == "up"

    flat_facts = [{"val": 100, "end": f"2025-{m:02d}-01"} for m in (1, 4, 7, 10)]
    assert trend_summary(flat_facts)["label"] == "stable"


def test_compute_trend_detects_acceleration():
    """Slow growth then fast growth -> 'accelerating' with change_point set."""
    from edgar.compute import trend_summary

    # 8 points: first 4 grow at +1/period, last 4 grow at +10/period.
    facts = [
        {"val": 100, "end": "2024-01-01"}, {"val": 101, "end": "2024-04-01"},
        {"val": 102, "end": "2024-07-01"}, {"val": 103, "end": "2024-10-01"},
        {"val": 113, "end": "2025-01-01"}, {"val": 123, "end": "2025-04-01"},
        {"val": 133, "end": "2025-07-01"}, {"val": 143, "end": "2025-10-01"},
    ]
    out = trend_summary(facts)
    assert out["label"] == "accelerating"
    assert out["change_point"] is not None
    assert out["segment_slopes"]["after"] > out["segment_slopes"]["before"]


def test_compute_trend_detects_inflection():
    """Up then down -> 'inflecting'."""
    from edgar.compute import trend_summary

    facts = [
        {"val": 100, "end": "2024-01-01"}, {"val": 110, "end": "2024-04-01"},
        {"val": 120, "end": "2024-07-01"}, {"val": 130, "end": "2024-10-01"},
        {"val": 125, "end": "2025-01-01"}, {"val": 115, "end": "2025-04-01"},
        {"val": 105, "end": "2025-07-01"}, {"val": 95, "end": "2025-10-01"},
    ]
    out = trend_summary(facts)
    assert out["label"] == "inflecting"


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


# --- Phase 1-2: mirror + search ---


def test_mirror_open_db_creates_schema(tmp_path):
    from edgar.mirror import open_db

    db_path = tmp_path / "mirror.sqlite"
    with open_db(db_path) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )}
    assert {"filers", "filings", "facts", "documents", "filings_fts",
            "schema_meta"}.issubset(tables)


def test_mirror_ingest_submissions_inserts_filings(tmp_path):
    from edgar.mirror import open_db, ingest_submissions

    submissions = {
        "name": "Apple Inc.", "tickers": ["AAPL"], "sic": "3571",
        "sicDescription": "Electronic Computers", "fiscalYearEnd": "0926",
        "filings": {"recent": {
            "accessionNumber": ["0000320193-25-000079", "0000320193-26-000013"],
            "filingDate": ["2025-10-31", "2026-05-01"],
            "form": ["10-K", "10-Q"],
            "primaryDocument": ["aapl-20250927.htm", "aapl-20260328.htm"],
            "primaryDocDescription": ["10-K", "10-Q"],
            "items": ["", ""],
            "reportDate": ["2025-09-27", "2026-03-28"],
        }},
    }
    with open_db(tmp_path / "m.sqlite") as conn:
        out = ingest_submissions(conn, "0000320193", submissions)
        assert out["filings_inserted"] == 2
        # Idempotent — second pass inserts zero.
        out2 = ingest_submissions(conn, "0000320193", submissions)
        assert out2["filings_inserted"] == 0
        rows = list(conn.execute(
            "SELECT form, filed FROM filings WHERE cik = ? ORDER BY filed",
            ("0000320193",),
        ))
        assert [r[0] for r in rows] == ["10-K", "10-Q"]


def test_mirror_search_fts_finds_form_metadata(tmp_path):
    from edgar.mirror import open_db, ingest_submissions, search_filings

    submissions = {
        "name": "Test Inc.", "filings": {"recent": {
            "accessionNumber": ["x-1", "x-2"],
            "filingDate": ["2025-01-01", "2025-02-01"],
            "form": ["10-K", "8-K"],
            "primaryDocument": ["10k.htm", "8k.htm"],
            "primaryDocDescription": ["10-K", "8-K"],
            "items": ["", "2.02,9.01"],
            "reportDate": ["", ""],
        }},
    }
    with open_db(tmp_path / "m.sqlite") as conn:
        ingest_submissions(conn, "0000000001", submissions)
        results = search_filings(conn, "10-K")
        assert any(r["form"] == "10-K" for r in results)
        # Item-code search.
        results = search_filings(conn, "2.02")
        assert any("2.02" in (r.get("items") or "") for r in results)


# --- Phase 3: restatement back-fill ---


def test_populate_restatement_state_marks_priors():
    from edgar.api import EdgarClient

    facts = [
        {"start": "2024-01-01", "end": "2024-12-31", "unit": "USD",
         "val": 100, "filed": "2025-02-01", "accn": "x-1"},
        {"start": "2024-01-01", "end": "2024-12-31", "unit": "USD",
         "val": 110, "filed": "2026-02-01", "accn": "x-2"},  # restated up
        {"start": "2023-01-01", "end": "2023-12-31", "unit": "USD",
         "val": 80, "filed": "2024-02-01", "accn": "y-1"},
    ]
    EdgarClient._populate_restatement_state(facts)
    by_accn = {f["accn"]: f for f in facts}
    assert by_accn["x-1"]["is_restated"] is True
    assert by_accn["x-1"]["superseded_by"] == "x-2"
    assert by_accn["x-1"]["latest_known_value"] == 110
    assert by_accn["x-2"]["is_restated"] is False
    assert by_accn["x-2"]["prior_values"] == [
        {"val": 100, "filed": "2025-02-01", "accession": "x-1"}
    ]
    # Unrestated period stays clean.
    assert by_accn["y-1"]["is_restated"] is False


# --- Phase 7-9: quality / verify / dashboard ---


def test_mirror_filing_bodies_round_trip(tmp_path):
    from edgar.mirror import open_db, ingest_filing_body, search_bodies

    with open_db(tmp_path / "m.sqlite") as conn:
        # First filer (gives the FTS table real names to join against).
        conn.execute("INSERT INTO filers(cik, name) VALUES (?, ?)", ("0001", "Acme Inc."))
        conn.commit()
        out = ingest_filing_body(
            conn, "0001", "x-1", "10-K", "2025-01-01",
            "acme.htm", "Our supply chain is concentrated in Asia.",
        )
        assert out["inserted"] is True
        # Second insertion of same accession is a no-op.
        out2 = ingest_filing_body(
            conn, "0001", "x-1", "10-K", "2025-01-01",
            "acme.htm", "Anything else",
        )
        assert out2.get("already_present") is True

        # Body search finds the inserted text and emits a snippet.
        rows = search_bodies(conn, "supply chain")
        assert len(rows) == 1
        assert rows[0]["accession"] == "x-1"
        assert "supply chain" in rows[0]["snippet"].lower()


def test_mirror_filing_bodies_truncate_at_byte_cap(tmp_path):
    from edgar.mirror import open_db, ingest_filing_body

    big = "x" * (5 * 1024 * 1024)  # 5 MB > 4 MB cap
    with open_db(tmp_path / "m.sqlite") as conn:
        out = ingest_filing_body(conn, "0002", "x-2", "10-K", "2025-01-01",
                                  "big.htm", big, max_bytes=1024)
        assert out["truncated"] is True
        assert out["body_length"] <= 1024


# --- Phase 2: insiders ---


def test_form4_xml_parses_transactions():
    from edgar.insiders import parse_form4_xml

    xml = """<?xml version="1.0"?>
    <ownershipDocument>
      <schemaVersion>X0508</schemaVersion>
      <documentType>4</documentType>
      <periodOfReport>2026-03-20</periodOfReport>
      <issuer>
        <issuerCik>0001045810</issuerCik>
        <issuerName>NVIDIA CORP</issuerName>
        <issuerTradingSymbol>NVDA</issuerTradingSymbol>
      </issuer>
      <reportingOwner>
        <reportingOwnerId>
          <rptOwnerCik>0001000001</rptOwnerCik>
          <rptOwnerName>Test Insider</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
          <isDirector>1</isDirector>
          <isOfficer>1</isOfficer>
          <officerTitle>CFO</officerTitle>
        </reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2026-03-20</value></transactionDate>
          <transactionCoding>
            <transactionCode>S</transactionCode>
          </transactionCoding>
          <transactionAmounts>
            <transactionShares><value>1000</value></transactionShares>
            <transactionPricePerShare><value>120.50</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
          <postTransactionAmounts>
            <sharesOwnedFollowingTransaction><value>9000</value></sharesOwnedFollowingTransaction>
          </postTransactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>
    """
    out = parse_form4_xml(xml)
    assert out["issuer"]["ticker"] == "NVDA"
    assert out["reporting_owner"]["name"] == "Test Insider"
    assert out["reporting_owner"]["officer_title"] == "CFO"
    txs = out["non_derivative_transactions"]
    assert len(txs) == 1
    tx = txs[0]
    assert tx["code"] == "S"
    assert tx["direction"] == "dispose"
    assert tx["shares"] == 1000
    assert tx["price_per_share"] == 120.50
    assert tx["transaction_value"] == 1000 * 120.50


def test_form4_aggregate_groups_by_owner_and_code():
    from edgar.insiders import aggregate

    txs = [
        {"owner_name": "Tim Cook", "code": "S", "direction": "dispose",
         "shares": 1000, "transaction_value": 100_000, "code_meaning": "Sale",
         "is_director": False, "is_officer": True, "officer_title": "CEO"},
        {"owner_name": "Tim Cook", "code": "S", "direction": "dispose",
         "shares": 500, "transaction_value": 50_000, "code_meaning": "Sale",
         "is_director": False, "is_officer": True, "officer_title": "CEO"},
        {"owner_name": "Luca Maestri", "code": "P", "direction": "acquire",
         "shares": 100, "transaction_value": 10_000, "code_meaning": "Purchase",
         "is_director": False, "is_officer": True, "officer_title": "CFO"},
    ]
    out = aggregate(txs)
    by_name = {i["name"]: i for i in out["insiders"]}
    assert by_name["Tim Cook"]["disposed_shares"] == 1500
    assert by_name["Tim Cook"]["disposed_value"] == 150_000
    assert by_name["Luca Maestri"]["acquired_value"] == 10_000
    assert out["summary"]["net_value"] == 10_000 - 150_000


# --- Phase 3: per-share metrics ---


def test_expand_group_13f_top_returns_curated_filers():
    from edgar.api import EdgarClient

    client = EdgarClient.__new__(EdgarClient)
    out = client.expand_group("@13f-top")
    assert out["group"] == "13f-top"
    # Berkshire and BlackRock are anchor entries; must be present.
    assert "0001067983" in out["identifiers"]
    assert "0001364742" in out["identifiers"]
    # Dedup is preserved.
    assert len(out["identifiers"]) == len(set(out["identifiers"]))


def test_holders_parse_infotable_xml_post_2023():
    """Recent 13F filings report value in absolute USD."""
    from edgar.holders import parse_infotable_xml

    xml = """<?xml version="1.0"?>
    <informationTable>
      <infoTable>
        <nameOfIssuer>APPLE INC</nameOfIssuer>
        <titleOfClass>COM</titleOfClass>
        <cusip>037833100</cusip>
        <value>21929537965</value>
        <shrsOrPrnAmt>
          <sshPrnamt>123456789</sshPrnamt>
          <sshPrnamtType>SH</sshPrnamtType>
        </shrsOrPrnAmt>
        <investmentDiscretion>SOLE</investmentDiscretion>
        <votingAuthority>
          <Sole>123456789</Sole><Shared>0</Shared><None>0</None>
        </votingAuthority>
      </infoTable>
      <infoTable>
        <nameOfIssuer>COCA COLA CO</nameOfIssuer>
        <titleOfClass>COM</titleOfClass>
        <cusip>191216100</cusip>
        <value>19765145984</value>
        <shrsOrPrnAmt>
          <sshPrnamt>400000000</sshPrnamt>
          <sshPrnamtType>SH</sshPrnamtType>
        </shrsOrPrnAmt>
      </infoTable>
    </informationTable>
    """
    rows = parse_infotable_xml(xml)
    assert len(rows) == 2
    aapl = next(r for r in rows if r["cusip"] == "037833100")
    # Total > $10B threshold, so values stay as absolute USD.
    assert aapl["value_usd"] == 21929537965
    assert aapl["value_unit_convention"] == "absolute"
    assert aapl["shares"] == 123456789


def test_holders_parse_infotable_xml_pre_2023_thousands():
    """Pre-2023 13F filings reported value in thousands of USD."""
    from edgar.holders import parse_infotable_xml

    xml = """<?xml version="1.0"?>
    <informationTable>
      <infoTable>
        <nameOfIssuer>SMALL CO</nameOfIssuer>
        <titleOfClass>COM</titleOfClass>
        <cusip>000000000</cusip>
        <value>50000</value>
        <shrsOrPrnAmt><sshPrnamt>1000</sshPrnamt></shrsOrPrnAmt>
      </infoTable>
    </informationTable>
    """
    rows = parse_infotable_xml(xml)
    # Total raw value $50K < $10B threshold -> treated as thousands.
    assert rows[0]["value_unit_convention"] == "thousands"
    assert rows[0]["value_usd"] == 50_000_000  # 50,000 thousands


def test_holdings_quarter_matches_report_date_not_filing_date():
    """13F-HR is filed ~45 days after quarter-end; matching must use reportDate."""
    from edgar.api import EdgarClient

    client = EdgarClient.__new__(EdgarClient)

    # Stub: the Q4-2024 filing is filed in Feb 2025 (reportDate 2024-12-31),
    # the Q3-2024 filing is filed in Nov 2024 (reportDate 2024-09-30).
    # The buggy implementation matched filingDate.startswith('2024') which
    # picked the November (Q3) filing for `--quarter 2024Q4`.
    captured = {}

    def fake_resolve(identifier):
        return {"cik": "0001067983", "ticker": "BRK", "name": "BERKSHIRE"}

    def fake_submissions(identifier, form=None, limit=None):
        return {
            "filings": [
                {"accessionNumber": "x-2026Q4", "form": "13F-HR",
                 "filingDate": "2026-02-17", "reportDate": "2025-12-31",
                 "primaryDocument": "primary_doc.html", "primary_doc_url": "u1"},
                {"accessionNumber": "x-2025Q3", "form": "13F-HR",
                 "filingDate": "2025-11-14", "reportDate": "2025-09-30",
                 "primaryDocument": "primary_doc.html", "primary_doc_url": "u2"},
                {"accessionNumber": "x-2024Q4", "form": "13F-HR",
                 "filingDate": "2025-02-14", "reportDate": "2024-12-31",
                 "primaryDocument": "primary_doc.html", "primary_doc_url": "u3"},
                {"accessionNumber": "x-2024Q3", "form": "13F-HR",
                 "filingDate": "2024-11-14", "reportDate": "2024-09-30",
                 "primaryDocument": "primary_doc.html", "primary_doc_url": "u4"},
            ]
        }

    def fake_fetch_holdings(cik, accession):
        captured["accession"] = accession
        return [{"name_of_issuer": "AMERICAN EXPRESS CO", "value_usd": 1000,
                 "shares": 1, "value_unit_convention": "absolute"}]

    client.resolve_company = fake_resolve
    client.submissions = fake_submissions
    client._fetch_13f_holdings = fake_fetch_holdings

    out = client.holdings("BRK", quarter="2024Q4")
    assert captured["accession"] == "x-2024Q4"
    assert out["filing"]["filed"] == "2025-02-14"

    out_q3 = client.holdings("BRK", quarter="2024Q3")
    assert captured["accession"] == "x-2024Q3"


def test_holders_aggregate_filer_holdings_concentration():
    from edgar.holders import aggregate_filer_holdings

    rows = [
        {"name_of_issuer": "A", "value_usd": 100, "shares": 10},
        {"name_of_issuer": "B", "value_usd": 50, "shares": 5},
        {"name_of_issuer": "C", "value_usd": 25, "shares": 2},
        {"name_of_issuer": "D", "value_usd": 25, "shares": 1},
    ]
    out = aggregate_filer_holdings(rows, top_n=2)
    assert out["total_value_usd"] == 200
    assert out["position_count"] == 4
    # Top-2 concentration: 150/200 = 0.75
    assert abs(out["top_concentration"] - 0.75) < 1e-9
    assert [p["name_of_issuer"] for p in out["top_positions"]] == ["A", "B"]


# --- Phase 2: Item extraction ---


def test_items_find_canonical_order_in_simple_doc():
    from edgar.items import find_items

    body = (
        "Item 1. Business\n\n"
        "We make chips. " * 100 +
        "\n\nItem 1A. Risk Factors\n\n"
        "Risks include " + ("market volatility, " * 50) + "\n\n"
        "Item 2. Properties\n\n"
        "We own facilities. " * 50
    )
    items = find_items(body, schema="10-K")
    assert [i["item"] for i in items] == ["1", "1A", "2"]


def test_items_skip_inline_back_references():
    """`see Item 1A — Risk Factors` should NOT win over the real header."""
    from edgar.items import find_items, extract_section

    body = (
        "Item 1. Business\n\n"
        "Our business has many risks; see Item 1A. Risk Factors of this "
        "Form 10-K for details. " + ("more business prose. " * 100) +
        "\n\nItem 1A. Risk Factors\n\n"
        "Investing in our common stock involves risks. " +
        ("risk discussion. " * 100) +
        "\n\nItem 1B. Unresolved Staff Comments\n\nNone."
    )
    items = find_items(body, schema="10-K")
    item_1a = next(i for i in items if i["item"] == "1A")
    # The chosen 1A offset should be AFTER the back-reference.
    back_ref_offset = body.index("see Item 1A")
    assert item_1a["start"] > back_ref_offset

    section = extract_section(body, "1A")
    assert section["text"].startswith("Item 1A. Risk Factors")
    assert "Investing in our common stock" in section["text"]


def test_items_resolve_by_title():
    from edgar.items import extract_section

    body = (
        "Item 1. Business\n\n" + ("biz prose. " * 50) +
        "\n\nItem 1A. Risk Factors\n\n" + ("risk prose. " * 50) +
        "\n\nItem 2. Properties\n\nNone."
    )
    out = extract_section(body, "Risk Factors")
    assert out["item"] == "1A"


# --- Phase 3: governance ---


def test_governance_extract_audit_fees():
    from edgar.governance import extract_audit_fees

    text = (
        "The aggregate fees billed to the Company by PwC are summarized below.\n"
        "Audit Fees      $12,500,000\n"
        "Audit-Related Fees   $250,000\n"
        "Tax Fees             $80,000\n"
        "All Other Fees       $5,000\n"
    )
    fees = extract_audit_fees(text)
    by_label = {f["label"].lower(): f for f in fees}
    assert by_label["audit fees"]["value_usd"] == 12_500_000
    assert by_label["tax fees"]["value_usd"] == 80_000


def test_governance_extract_board_size_word_form():
    from edgar.governance import extract_board_size

    text = "Our Board of Directors currently consists of nine directors elected annually."
    out = extract_board_size(text)
    assert out["count"] == 9


def test_compute_book_value_per_share():
    from edgar.compute import book_value_per_share

    out = book_value_per_share({"val": 1000}, {"val": 100})
    assert out["value"] == 10.0
    assert "StockholdersEquity / SharesOutstanding" in out["formula"]


def test_compute_fcf_per_share_propagates_caveats():
    from edgar.compute import fcf_per_share

    out = fcf_per_share({"val": 100}, {"val": 30}, {"val": 70})
    assert out["value"] == 1.0
    assert any("FCF capex scope" in c for c in out["caveats"])


def test_quality_flags_have_provenance(tmp_path):
    from edgar.compute import _input_record

    # Just verify the input-record shape we depend on for quality flags.
    rec = _input_record("Test", {"val": 100, "tag": "X", "filing_url": "http://e",
                                 "end": "2025-01-01"})
    assert rec["val"] == 100
    assert rec["tag"] == "X"
    assert rec["source_url"] == "http://e"


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


# --- regressions for live test-drive findings (docs/FINDINGS.md) ---


def test_facts_for_alias_dedupes_reported_periods():
    """Comparative re-reports of the same period must collapse to latest-filed."""
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)

    def fake_alias(identifier, alias, **kwargs):
        return {
            "tag": "Revenues",
            "facts": [
                {"start": "2023-01-30", "end": "2024-01-28", "val": 60922, "filed": "2024-02-21"},
                {"start": "2023-01-30", "end": "2024-01-28", "val": 60922, "filed": "2025-02-26"},
                {"start": "2023-01-30", "end": "2024-01-28", "val": 60922, "filed": "2026-02-25"},
                {"start": "2024-01-29", "end": "2025-01-26", "val": 130497, "filed": "2025-02-26"},
            ],
        }

    client.company_concept_alias = fake_alias
    facts = client._facts_for_alias("NVDA", "revenue", period_type="annual", limit=8)
    assert [f["end"] for f in facts] == ["2025-01-26", "2024-01-28"]
    assert facts[1]["filed"] == "2026-02-25"


def test_ttm_stub_accepts_q1_quarter_as_current_ytd():
    """A Q1 10-Q fact (classified quarterly, not ytd) must trigger stub TTM."""
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    annual = {"val": 215938, "start": "2025-01-27", "end": "2026-01-25", "filed": "2026-02-25"}
    q1_cur = {"val": 81615, "start": "2026-01-26", "end": "2026-04-26", "filed": "2026-05-20"}
    q1_prior = {"val": 44062, "start": "2025-01-27", "end": "2025-04-27", "filed": "2026-05-20"}

    def fake_facts(cik, alias, period_type=None, as_of=None, limit=24):
        return {"annual": [annual], "ytd": [], "quarterly": [q1_cur, q1_prior]}.get(period_type, [])

    client._facts_for_alias = fake_facts
    out = client._ttm_stub_period("0001045810", "revenue", as_of=None)
    assert out["value"] == 215938 + 81615 - 44062
    assert "no interim filings" not in out["formula"]


def test_ttm_stub_fy_only_message_requires_truly_no_interim():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    annual = {"val": 215938, "start": "2025-01-27", "end": "2026-01-25", "filed": "2026-02-25"}

    def fake_facts(cik, alias, period_type=None, as_of=None, limit=24):
        return [annual] if period_type == "annual" else []

    client._facts_for_alias = fake_facts
    out = client._ttm_stub_period("0001045810", "revenue", as_of=None)
    assert out["value"] == 215938
    assert "no interim filings" in out["formula"]


def test_build_fiscal_grid_prefers_earliest_filed_fy_row():
    from edgar.api import build_fiscal_grid

    rows = [
        # FY2025 primary in its own 10-K...
        {"fp": "FY", "fy": 2025, "start": "2024-01-29", "end": "2025-01-26", "filed": "2025-02-26"},
        # ...re-reported as a comparative in the FY2026 10-K with the filing's fy.
        {"fp": "FY", "fy": 2026, "start": "2024-01-29", "end": "2025-01-26", "filed": "2026-02-25"},
        {"fp": "FY", "fy": 2026, "start": "2025-01-27", "end": "2026-01-25", "filed": "2026-02-25"},
        # Quarterly rows never enter the grid.
        {"fp": "Q1", "fy": 2026, "start": "2025-01-27", "end": "2025-04-27", "filed": "2025-05-28"},
    ]
    grid = build_fiscal_grid(rows)
    assert [(str(s), str(e), fy) for s, e, fy in grid] == [
        ("2024-01-29", "2025-01-26", 2025),
        ("2025-01-27", "2026-01-25", 2026),
    ]


def test_fiscal_period_from_dates_labels_comparatives_and_extrapolates():
    from edgar.api import build_fiscal_grid, fiscal_period_from_dates

    grid = build_fiscal_grid([
        {"fp": "FY", "fy": 2025, "start": "2024-01-29", "end": "2025-01-26", "filed": "2025-02-26"},
        {"fp": "FY", "fy": 2026, "start": "2025-01-27", "end": "2026-01-25", "filed": "2026-02-25"},
    ])
    # Comparative quarter inside a known span: labeled by its own dates,
    # not by the Q1-FY2027 filing it appeared in.
    comparative = {"start": "2025-01-27", "end": "2025-04-27", "fy": 2027, "fp": "Q1",
                   "period_type": "quarterly", "period_length_days": 90}
    assert fiscal_period_from_dates(comparative, grid) == "Q1-FY2026"
    # Primary quarter beyond the newest span extrapolates one quarter slot.
    primary = {"start": "2026-01-26", "end": "2026-04-26", "fy": 2027, "fp": "Q1",
               "period_type": "quarterly", "period_length_days": 90}
    assert fiscal_period_from_dates(primary, grid) == "Q1-FY2027"
    # Annual comparative keeps its own year.
    annual_cmp = {"start": "2024-01-29", "end": "2025-01-26", "fy": 2026, "fp": "FY",
                  "period_type": "annual", "period_length_days": 363}
    assert fiscal_period_from_dates(annual_cmp, grid) == "FY2025"
    # FY-end balance instant re-reported in a later 10-Q stays the FY balance.
    instant = {"end": "2026-01-25", "fy": 2027, "fp": "Q1", "period_type": "instant",
               "period_length_days": 0}
    assert fiscal_period_from_dates(instant, grid) == "FY2026"


def test_paired_growth_rates_does_not_bridge_missing_quarters():
    from edgar.compute import paired_growth_rates

    quarters = [
        {"val": 46743, "end": "2025-07-27"},
        {"val": 57006, "end": "2025-10-26"},
        # Q4 FY2026 never tagged standalone — 182-day hole before Q1 FY2027.
        {"val": 81615, "end": "2026-04-26"},
    ]
    rates = paired_growth_rates(quarters, 80, 110)
    assert [r["period_end"] for r in rates] == ["2025-10-26"]
    assert rates[0]["gap_days"] == 91
    # YoY pairing over the same series finds only the ~365-day partner.
    yoy = paired_growth_rates(
        quarters + [{"val": 44062, "end": "2025-04-27"}], 350, 380)
    assert [r["period_end"] for r in yoy] == ["2026-04-26"]
    assert yoy[0]["prior_period_end"] == "2025-04-27"


def test_growth_qoq_uses_quarterly_facts_even_for_annual_period_type():
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    client.resolve_company = lambda identifier: {"cik": "0001045810", "name": "NVIDIA CORP",
                                                 "ticker": "NVDA"}
    annuals = [
        {"val": 130497, "start": "2024-01-29", "end": "2025-01-26"},
        {"val": 215938, "start": "2025-01-27", "end": "2026-01-25"},
    ]
    quarters = [
        {"val": 46743, "start": "2025-04-28", "end": "2025-07-27"},
        {"val": 57006, "start": "2025-07-28", "end": "2025-10-26"},
    ]

    def fake_facts(cik, alias, period_type=None, as_of=None, limit=24):
        return quarters if period_type == "quarterly" else annuals

    client._facts_for_alias = fake_facts
    out = client.growth("NVDA", "revenue", basis=["yoy", "qoq"], period_type="annual")
    blocks = {b["basis"]: b for b in out["growth"]}
    assert blocks["qoq"]["period_basis"] == "quarterly"
    assert blocks["qoq"]["rates"][0]["period_end"] == "2025-10-26"
    assert "note" in blocks["qoq"]
    assert blocks["yoy"]["rates"][0]["period_end"] == "2026-01-25"
