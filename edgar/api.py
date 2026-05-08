"""Client for SEC EDGAR public data APIs.

Public endpoints covered:
  - /submissions/CIK##########.json
  - /api/xbrl/companyfacts/CIK##########.json
  - /api/xbrl/companyconcept/CIK##########/{taxonomy}/{tag}.json
  - /api/xbrl/frames/{taxonomy}/{tag}/{unit}/{frame}.json
  - /files/company_tickers_exchange.json

These APIs are keyless, but SEC requires automated tools to declare a user agent.
Set SEC_USER_AGENT to an organization/contact string.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from difflib import get_close_matches
from html import unescape
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import quote

from dotenv import load_dotenv

import requests

from research_cli_base import BaseAPIClient, FileCache, SharedRateLimiter
from research_cli_base.http_client import retry_wait_seconds

from edgar import __version__ as CLI_VERSION
from edgar import compute
from edgar import governance as governance_mod
from edgar import holders as holders_mod
from edgar import insiders as insiders_mod
from edgar import items as items_mod
from edgar.cache import EdgarCache, ttl_for_url
from edgar.state import StateStore
from edgar import mirror as mirror_mod

log = logging.getLogger(__name__)

DATA_BASE_URL = "https://data.sec.gov"
SEC_BASE_URL = "https://www.sec.gov"
COMPANY_TICKERS_URL = f"{SEC_BASE_URL}/files/company_tickers_exchange.json"

SCHEMA_VERSION = "1.0.0"

DEFAULT_CACHE_TTL = 900
DEFAULT_RATE_LIMIT_INTERVAL = 0.2
DEFAULT_USER_AGENT = "edgar-cli/0.1.0 baba200@greenmountaincomputing.com"

DOW30_TICKERS = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
    "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
]


FORM_CLASSES: dict[str, set[str]] = {
    "major": {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "S-1", "S-1/A",
              "DEF 14A", "20-F", "20-F/A", "6-K", "40-F"},
    "insider": {"3", "3/A", "4", "4/A", "5", "5/A", "144"},
    "institutional": {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "SCHEDULE 13D",
                      "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
                      "13F-HR", "13F-HR/A", "13F-NT"},
}


BULK_ARCHIVES = [
    {
        "name": "companyfacts",
        "description": "All XBRL company facts and frame data, rebuilt nightly",
        "url": "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
    },
    {
        "name": "submissions",
        "description": "Public EDGAR filing history for all filers, rebuilt nightly",
        "url": "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip",
    },
]

COMMON_CONCEPT_CANDIDATES = {
    "assets": [("us-gaap", "Assets", "USD")],
    "assets_current": [("us-gaap", "AssetsCurrent", "USD")],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets", "USD"),
    ],
    "cogs": [
        ("us-gaap", "CostOfGoodsAndServicesSold", "USD"),
        ("us-gaap", "CostOfRevenue", "USD"),
        ("us-gaap", "CostOfGoodsSold", "USD"),
    ],
    "dna": [
        ("us-gaap", "DepreciationDepletionAndAmortization", "USD"),
        ("us-gaap", "DepreciationAndAmortization", "USD"),
        ("us-gaap", "Depreciation", "USD"),
    ],
    "equity": [
        ("us-gaap", "StockholdersEquity", "USD"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "USD"),
    ],
    "gross_profit": [("us-gaap", "GrossProfit", "USD")],
    "inventory": [("us-gaap", "InventoryNet", "USD")],
    "liabilities_current": [("us-gaap", "LiabilitiesCurrent", "USD")],
    "short_term_debt": [
        ("us-gaap", "LongTermDebtCurrent", "USD"),
        ("us-gaap", "DebtCurrent", "USD"),
        ("us-gaap", "ShortTermBorrowings", "USD"),
    ],
    "cash": [
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "USD"),
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD"),
    ],
    "debt": [
        ("us-gaap", "LongTermDebtNoncurrent", "USD"),
        ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsNoncurrent", "USD"),
    ],
    "diluted_eps": [("us-gaap", "EarningsPerShareDiluted", "USD/shares")],
    "eps": [("us-gaap", "EarningsPerShareDiluted", "USD/shares")],
    "liabilities": [("us-gaap", "Liabilities", "USD")],
    "net_income": [("us-gaap", "NetIncomeLoss", "USD")],
    "operating_cash_flow": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss", "USD")],
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
        ("us-gaap", "Revenues", "USD"),
        ("us-gaap", "SalesRevenueNet", "USD"),
        ("us-gaap", "SalesRevenueGoodsNet", "USD"),
        ("us-gaap", "SalesRevenueServicesNet", "USD"),
    ],
    "shares": [("dei", "EntityCommonStockSharesOutstanding", "shares")],
    # Working-capital line items
    "accounts_receivable": [
        ("us-gaap", "AccountsReceivableNetCurrent", "USD"),
        ("us-gaap", "ReceivablesNetCurrent", "USD"),
    ],
    "accounts_payable": [
        ("us-gaap", "AccountsPayableCurrent", "USD"),
    ],
    "deferred_revenue": [
        ("us-gaap", "ContractWithCustomerLiability", "USD"),
        ("us-gaap", "DeferredRevenue", "USD"),
        ("us-gaap", "DeferredRevenueCurrent", "USD"),
    ],
    "accrued_liabilities": [
        ("us-gaap", "AccruedLiabilitiesCurrent", "USD"),
    ],
    # Tax
    "income_tax_expense": [
        ("us-gaap", "IncomeTaxExpenseBenefit", "USD"),
    ],
    "deferred_tax_assets": [
        ("us-gaap", "DeferredTaxAssetsNet", "USD"),
        ("us-gaap", "DeferredTaxAssetsLiabilitiesNet", "USD"),
    ],
    "deferred_tax_liabilities": [
        ("us-gaap", "DeferredIncomeTaxLiabilitiesNet", "USD"),
    ],
    # Lease accounting (ASC 842)
    "operating_lease_liabilities": [
        ("us-gaap", "OperatingLeaseLiability", "USD"),
        ("us-gaap", "OperatingLeaseLiabilityNoncurrent", "USD"),
    ],
    "operating_lease_rou_assets": [
        ("us-gaap", "OperatingLeaseRightOfUseAsset", "USD"),
    ],
    # Cash flow components
    "depreciation": [
        ("us-gaap", "Depreciation", "USD"),
        ("us-gaap", "DepreciationAndAmortization", "USD"),
    ],
    "amortization_intangibles": [
        ("us-gaap", "AmortizationOfIntangibleAssets", "USD"),
    ],
    "stock_compensation": [
        ("us-gaap", "ShareBasedCompensation", "USD"),
        ("us-gaap", "StockBasedCompensation", "USD"),
    ],
    "investing_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInInvestingActivities", "USD"),
    ],
    "financing_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInFinancingActivities", "USD"),
    ],
    "dividends_paid": [
        ("us-gaap", "PaymentsOfDividends", "USD"),
        ("us-gaap", "PaymentsOfDividendsCommonStock", "USD"),
    ],
    "share_repurchases": [
        ("us-gaap", "PaymentsForRepurchaseOfCommonStock", "USD"),
    ],
    "interest_expense": [
        ("us-gaap", "InterestExpense", "USD"),
    ],
    # Intangibles
    "goodwill": [
        ("us-gaap", "Goodwill", "USD"),
    ],
    "intangibles": [
        ("us-gaap", "IntangibleAssetsNetExcludingGoodwill", "USD"),
        ("us-gaap", "FiniteLivedIntangibleAssetsNet", "USD"),
    ],
    # Retained earnings
    "retained_earnings": [
        ("us-gaap", "RetainedEarningsAccumulatedDeficit", "USD"),
    ],
    # Common shares outstanding (period-end count, not split-adjusted)
    "shares_outstanding": [
        ("dei", "EntityCommonStockSharesOutstanding", "shares"),
        ("us-gaap", "CommonStockSharesOutstanding", "shares"),
    ],
}

COMMON_CONCEPTS = {
    alias: candidates[0] for alias, candidates in COMMON_CONCEPT_CANDIDATES.items()
}

BRIEF_METRICS = ["assets", "cash", "debt", "net_income", "operating_income", "revenue"]
METRIC_BUNDLE_GROUPS: dict[str, list[str]] = {
    "income-statement": ["revenue", "cogs", "gross_profit", "operating_income",
                         "net_income", "eps", "diluted_eps"],
    "balance-sheet": ["assets", "liabilities", "equity", "cash", "debt",
                      "short_term_debt", "assets_current", "liabilities_current"],
    "cash-flow": ["operating_cash_flow", "capex"],
    "liquidity": ["assets_current", "liabilities_current", "cash"],
    "capital-structure": ["debt", "short_term_debt", "equity", "cash", "shares"],
    "quality": ["net_income", "operating_cash_flow", "assets_current", "liabilities_current"],
}

DEFAULT_METRIC_BUNDLE = [
    "revenue",
    "net_income",
    "operating_income",
    "operating_cash_flow",
    "cash",
    "debt",
    "shares",
]
STALE_METRIC_DAYS = 548

EVENT_KEYWORDS = {
    "reverse_split": ["reverse stock split"],
    "ceo_change": [
        "appointed chief executive officer",
        "appointed as chief executive officer",
        "named chief executive officer",
        "serve as the company's chief executive officer",
        "serve as the company’s chief executive officer",
        "chief executive officer and president",
        "resigned as chief executive officer",
    ],
    "cfo_change": [
        "appointed chief financial officer",
        "appointed as chief financial officer",
        "named chief financial officer",
        "serve as the company's chief financial officer",
        "serve as the company’s chief financial officer",
        "resigned as chief financial officer",
        "principal financial officer",
    ],
    "delisting": ["delisting", "form 25", "suspend trading"],
    "merger": [
        "merger agreement",
        "acquisition agreement",
        "definitive agreement to acquire",
        "business combination",
    ],
    "debt": ["credit agreement", "loan agreement", "notes", "indenture"],
    "guidance": ["guidance", "outlook", "forecast"],
    "earnings": ["results of operations", "financial results"],
}


def _next_day(yyyy_mm_dd: str) -> str:
    """Return the day after a YYYY-MM-DD string, or empty string on parse failure."""
    try:
        d = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").date()
        return (d + timedelta(days=1)).isoformat()
    except (ValueError, TypeError):
        return ""


def normalize_cik(value: str | int) -> str:
    """Return a 10-digit CIK string from a CIK-like value."""
    text = str(value).strip()
    text = re.sub(r"^CIK", "", text, flags=re.IGNORECASE)
    if not re.fullmatch(r"\d{1,10}", text):
        raise ValueError(f"Not a CIK: {value}")
    return text.zfill(10)


def is_cik_like(value: str | int) -> bool:
    """Return whether a value can be interpreted as a CIK."""
    try:
        normalize_cik(value)
        return True
    except ValueError:
        return False


def quote_path_segment(value: str | int) -> str:
    """Quote one URL path segment without allowing slashes through."""
    return quote(str(value), safe="")


def filing_urls(cik: str | int, accession_number: str, primary_document: str = "") -> dict:
    """Build SEC filing detail and primary document URLs."""
    cik10 = normalize_cik(cik)
    cik_int = str(int(cik10))
    accession_no_dashes = accession_number.replace("-", "")
    base = f"{SEC_BASE_URL}/Archives/edgar/data/{cik_int}/{accession_no_dashes}"
    return {
        "filing_url": f"{base}/{accession_number}-index.htm",
        "primary_doc_url": f"{base}/{primary_document}" if primary_document else "",
    }


def resolve_concept_alias(concept: str, taxonomy: Optional[str] = None,
                          unit: Optional[str] = None) -> tuple[str, str, Optional[str]]:
    """Resolve a friendly concept alias to taxonomy/tag/unit."""
    return concept_alias_candidates(concept, taxonomy, unit)[0]


def concept_alias_key(concept: str) -> str:
    return concept.strip().lower().replace("-", "_").replace(" ", "_")


def concept_alias_candidates(concept: str, taxonomy: Optional[str] = None,
                             unit: Optional[str] = None) -> list[tuple[str, str, Optional[str]]]:
    """Return candidate taxonomy/tag/unit triples for a concept alias or exact tag."""
    key = concept_alias_key(concept)
    candidates = COMMON_CONCEPT_CANDIDATES.get(key)
    if not candidates:
        return [(taxonomy or "us-gaap", concept, unit)]
    return [
        (taxonomy or candidate_taxonomy, tag, unit or candidate_unit)
        for candidate_taxonomy, tag, candidate_unit in candidates
    ]


def get_client(use_cache: bool = True, cache_max_mb: Optional[int] = None) -> "EdgarClient":
    """Create an EDGAR client with endpoint-aware cache and rate limiter."""
    max_bytes = int(cache_max_mb) * 1024 * 1024 if cache_max_mb else None
    cache = EdgarCache(cache_dir="~/.edgar_cache", default_ttl=DEFAULT_CACHE_TTL,
                       max_bytes=max_bytes)
    rate_limiter = SharedRateLimiter(
        db_path="~/.edgar/rate_limit.db",
        min_interval=DEFAULT_RATE_LIMIT_INTERVAL,
    )
    return EdgarClient(edgar_cache=cache, rate_limiter=rate_limiter, use_cache=use_cache)


class EdgarClient(BaseAPIClient):
    """SEC EDGAR public data client."""

    BASE_URL = DATA_BASE_URL

    def __init__(self, *args, user_agent: Optional[str] = None,
                 edgar_cache: Optional[EdgarCache] = None, **kwargs):
        load_dotenv()
        self.user_agent = user_agent or os.getenv("SEC_USER_AGENT") or DEFAULT_USER_AGENT
        use_cache_flag = kwargs.pop("use_cache", True)
        kwargs.setdefault("cache", None)
        kwargs["use_cache"] = False
        super().__init__(*args, **kwargs)
        self.edgar_cache = edgar_cache
        self.use_cache = bool(use_cache_flag and edgar_cache is not None)
        self._cache_calls: list[dict] = []

    def _build_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }

    def _format_error(self, msg: str) -> dict:
        return {"error": msg}

    def _default_error(self) -> dict:
        return {"error": "Request failed"}

    def _reset_cache_meta(self) -> None:
        self._cache_calls = []

    def _record_cache(self, meta: dict) -> None:
        self._cache_calls.append(meta)

    def _summarize_cache(self) -> dict:
        calls = list(self._cache_calls)
        hits = [c for c in calls if c.get("hit")]
        ages = [c["age_seconds"] for c in hits if c.get("age_seconds") is not None]
        ttls = [c["ttl_remaining"] for c in hits if c.get("ttl_remaining") is not None]
        last = calls[-1] if calls else None
        summary = {
            "calls": len(calls),
            "hits": len(hits),
            "misses": len(calls) - len(hits),
        }
        if ages:
            summary["age_max_seconds"] = max(ages)
            summary["age_min_seconds"] = min(ages)
        if ttls:
            summary["ttl_min_remaining"] = min(ttls)
        if last:
            summary["last_key"] = last.get("key")
            summary["last_hit"] = bool(last.get("hit"))
            summary["last_etag"] = last.get("etag")
        return summary

    def _envelope(self, data: dict, **extras) -> dict:
        """Add schema_version, cli_version, and cache summary to a result dict."""
        if not isinstance(data, dict):
            return data
        wrapped = dict(data)
        wrapped.setdefault("schema_version", SCHEMA_VERSION)
        wrapped.setdefault("cli_version", CLI_VERSION)
        wrapped["cache"] = self._summarize_cache()
        for key, value in extras.items():
            wrapped[key] = value
        return wrapped

    def _get(self, path: str, params: Optional[dict] = None,
             skip_cache: bool = False) -> dict:
        """GET with EdgarCache, conditional headers, negative caching, and meta tracking."""
        url = path if path.startswith("http") else f"{self.BASE_URL}{path}"
        cache = self.edgar_cache if (self.use_cache and not skip_cache) else None

        if cache is not None:
            payload, meta = cache.get_with_meta(url, params)
            if meta.get("hit"):
                self._record_cache(meta)
                if meta.get("negative") and isinstance(payload, dict) and "error" in payload:
                    return payload
                return payload

        headers: dict[str, str] = {}
        if cache is not None:
            stale_etag = (meta or {}).get("stale_etag")
            stale_lm = (meta or {}).get("stale_last_modified")
            if stale_etag:
                headers["If-None-Match"] = stale_etag
            if stale_lm:
                headers["If-Modified-Since"] = stale_lm

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                if self.rate_limiter:
                    self.rate_limiter.acquire()
                resp = self.session.get(
                    url, params=params, headers=headers or None,
                    timeout=self.request_timeout,
                )

                if resp.status_code == 304 and cache is not None and meta.get("stale_payload") is not None:
                    cache.refresh_timestamp(url, params)
                    refreshed_meta = dict(meta)
                    refreshed_meta.update({
                        "hit": True,
                        "age_seconds": 0,
                        "ttl_remaining": ttl_for_url(url),
                        "etag": meta.get("stale_etag"),
                        "last_modified": meta.get("stale_last_modified"),
                        "stale_payload": None,
                        "stale_etag": None,
                        "stale_last_modified": None,
                        "conditional_304": True,
                    })
                    self._record_cache(refreshed_meta)
                    return meta["stale_payload"]

                if resp.status_code == 429:
                    wait = retry_wait_seconds(attempt, resp)
                    log.warning("429 rate limited, waiting %.1fs (attempt %d)", wait, attempt)
                    time.sleep(wait)
                    continue

                if resp.status_code in (403, 404):
                    label = "Forbidden" if resp.status_code == 403 else "Not Found"
                    err = self._format_error(f"{resp.status_code} {label}: {url}")
                    if cache is not None:
                        cache.set(url, params, err, negative=True)
                        miss_meta = dict(meta) if meta else {"key": cache._key(url, params)}
                        miss_meta.update({"hit": False, "negative": True})
                        self._record_cache(miss_meta)
                    return err

                resp.raise_for_status()
                data = resp.json()
                etag = resp.headers.get("ETag")
                last_modified = resp.headers.get("Last-Modified")
                if cache is not None:
                    cache.set(url, params, data, etag=etag, last_modified=last_modified)
                    fresh_meta = {
                        "key": cache._key(url, params),
                        "hit": False,
                        "etag": etag,
                        "last_modified": last_modified,
                    }
                    self._record_cache(fresh_meta)
                return data

            except requests.exceptions.HTTPError as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(retry_wait_seconds(attempt))
                    continue
            except requests.exceptions.RequestException as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(retry_wait_seconds(attempt))
                    continue

        error = self._default_error()
        error["error"] = f"Failed after {self.max_retries + 1} attempts: {last_error}"
        return error

    def _get_text(self, url: str) -> str:
        """GET text/HTML with the same session, headers, limiter, and timeout."""
        if self.rate_limiter:
            self.rate_limiter.acquire()
        response = self.session.get(url, timeout=self.request_timeout)
        response.raise_for_status()
        return response.text

    def _get_bytes(self, url: str) -> bytes:
        """GET bytes with the same session, headers, limiter, and timeout."""
        if self.rate_limiter:
            self.rate_limiter.acquire()
        response = self.session.get(url, timeout=self.request_timeout)
        response.raise_for_status()
        return response.content

    def companies(self) -> dict:
        """Return ticker/CIK/company/exchange mappings."""
        result = self._get(COMPANY_TICKERS_URL)
        if "error" in result:
            return result

        fields = result.get("fields", [])
        companies = []
        for row in result.get("data", []):
            item = dict(zip(fields, row))
            item["cik"] = normalize_cik(item["cik"])
            item["ticker"] = str(item.get("ticker", "")).upper()
            companies.append(item)

        return {"companies": companies, "total": len(companies)}

    def search_companies(self, query: str, limit: int = 20) -> dict:
        """Search SEC's ticker/CIK/company-name map."""
        if not str(query).strip():
            return {"error": "Company search query cannot be blank", "companies": [], "total": 0}

        result = self.companies()
        if "error" in result:
            return result

        needle = query.strip().upper()
        scored = []
        for index, company in enumerate(result["companies"]):
            ticker = company.get("ticker", "")
            name = company.get("name", "").upper()
            if ticker == needle:
                score = 0
            elif name == needle:
                score = 1
            elif name.startswith(needle):
                score = 2
            elif ticker.startswith(needle):
                score = 3
            elif needle in name:
                score = 4
            elif needle in ticker:
                score = 5
            else:
                continue
            scored.append((score, index, company))

        companies = [company for _, _, company in sorted(scored, key=lambda x: (x[0], x[1]))[:limit]]
        return {"query": query, "companies": companies, "total": len(companies)}

    def resolve_company(self, identifier: str | int) -> dict:
        """Resolve a CIK, ticker, or unambiguous company-name search to a CIK."""
        if not str(identifier).strip():
            return {"error": "Company identifier cannot be blank"}

        if is_cik_like(identifier):
            cik = normalize_cik(identifier)
            return {"cik": cik, "ticker": "", "name": "", "exchange": ""}

        result = self.search_companies(str(identifier), limit=10)
        if "error" in result:
            return result

        query = str(identifier).strip().upper()
        companies = result.get("companies", [])
        exact = [c for c in companies if c.get("ticker", "").upper() == query]
        if exact:
            return exact[0]
        if len(companies) == 1:
            return companies[0]
        if not companies:
            return {"error": f"No company found for {identifier}"}

        choices = ", ".join(f"{c['ticker']} ({c['cik']})" for c in companies[:5])
        return {"error": f"Ambiguous company identifier {identifier}; matches: {choices}"}

    def submissions(self, identifier: str | int, limit: int = 20, form: Optional[str] = None,
                    start_date: Optional[str] = None, end_date: Optional[str] = None,
                    all_history: bool = False, since_last_fetch: bool = False,
                    state_store: Optional["StateStore"] = None,
                    form_class: Optional[str] = None) -> dict:
        """Return company submission metadata and recent filings."""
        company = self.resolve_company(identifier)
        if "error" in company:
            return company

        cik = company["cik"]
        result = self._get(f"/submissions/CIK{cik}.json")
        if "error" in result:
            return result

        if since_last_fetch and state_store is not None:
            high_water = state_store.get_high_water(cik, form)
            if high_water and high_water.get("filed"):
                hw_date = high_water["filed"]
                if not start_date or start_date <= hw_date:
                    start_date = _next_day(hw_date)

        files = result.get("filings", {}).get("files", [])
        recent_matches = self._recent_filings(
            result,
            limit=limit + 1,
            form=form,
            start_date=start_date,
            end_date=end_date,
            form_class=form_class,
        )
        recent_truncated = len(recent_matches) > limit
        filings = recent_matches[:limit]
        files_checked = 0
        if all_history and not recent_truncated and len(filings) < limit:
            seen = {f.get("accessionNumber") for f in filings}
            for file_info in files:
                chunk_name = file_info.get("name")
                if not chunk_name:
                    continue
                chunk = self._get(f"/submissions/{quote_path_segment(chunk_name)}")
                files_checked += 1
                if "error" in chunk:
                    continue
                for filing in self._recent_filings(
                    {"cik": cik, "filings": {"recent": chunk}},
                    limit=limit,
                    form=form,
                    start_date=start_date,
                    end_date=end_date,
                    form_class=form_class,
                ):
                    accession = filing.get("accessionNumber")
                    if accession in seen:
                        continue
                    filings.append(filing)
                    seen.add(accession)
                    if len(filings) >= limit:
                        break
                if len(filings) >= limit:
                    break

        oldest_recent_date = self._oldest_recent_filing_date(result)
        date_reaches_recent_boundary = bool(
            oldest_recent_date
            and (
                (start_date and start_date <= oldest_recent_date)
                or (end_date and end_date <= oldest_recent_date)
            )
        )
        filtered_recent_truncated = recent_truncated and bool(form or start_date or end_date)
        warning = ""
        if not filings and (form or start_date or end_date):
            if files_checked:
                warning = f"No matching filings found after searching recent filings plus {files_checked} historical chunk(s)."
            else:
                warning = "No matching filings found in the recent filing set."
                if files and not all_history:
                    warning += " Older filings may exist in historical chunks; rerun with --all to search them."
        elif files and not all_history and filtered_recent_truncated:
            warning = f"Showing the first {limit} matching recent filings; older historical chunks may also match. Rerun with --all to search them."
        elif files and not all_history and date_reaches_recent_boundary:
            warning = f"Date filters reach the oldest SEC recent filing ({oldest_recent_date}); rerun with --all to search historical chunks."
        elif files_checked:
            warning = f"Searched recent filings plus {files_checked} historical chunk(s)."

        if since_last_fetch and state_store is not None and filings:
            top = max(filings, key=lambda f: f.get("filingDate", ""))
            state_store.update_high_water(
                cik, form, top.get("accessionNumber", ""), top.get("filingDate", ""),
            )

        return {
            "cik": cik,
            "ticker": company.get("ticker", ""),
            "name": result.get("name") or company.get("name", ""),
            "entityType": result.get("entityType", ""),
            "sic": result.get("sic", ""),
            "sicDescription": result.get("sicDescription", ""),
            "exchanges": result.get("exchanges", []),
            "tickers": result.get("tickers", []),
            "fiscalYearEnd": result.get("fiscalYearEnd", ""),
            "filings": filings,
            "total_recent": len(filings),
            "files": files,
            "history_limited": bool(files),
            "all_history": all_history,
            "history_files_checked": files_checked,
            "since_last_fetch": since_last_fetch,
            "warning": warning,
        }

    def company_facts(self, identifier: str | int, taxonomy: Optional[str] = None,
                      tag_filter: Optional[str] = None, limit: int = 50) -> dict:
        """Return summarized XBRL concepts available for one company."""
        company = self.resolve_company(identifier)
        if "error" in company:
            return company

        result = self._get(f"/api/xbrl/companyfacts/CIK{company['cik']}.json")
        if "error" in result:
            return result

        concepts = self._summarize_concepts(result.get("facts", {}), taxonomy, tag_filter)
        return {
            "cik": normalize_cik(result.get("cik", company["cik"])),
            "name": result.get("entityName", company.get("name", "")),
            "concepts": concepts[:limit],
            "total": len(concepts),
        }

    @staticmethod
    def _populate_restatement_state(facts: list[dict]) -> list[dict]:
        """Walk facts and back-fill `is_restated`, `superseded_by`,
        `latest_known_value`, and `prior_values` based on same-period siblings.

        Two facts are considered to describe the same period when their
        `(start, end)` (or `end` alone for instants) match. The fact with the
        latest `filed` date is the latest known value; earlier facts whose
        `val` differs are flagged `is_restated=True` with a `superseded_by`
        pointer to the latest accession.
        """
        groups: dict[tuple, list[dict]] = {}
        for fact in facts:
            key = (fact.get("start", ""), fact.get("end", ""), fact.get("unit", ""))
            groups.setdefault(key, []).append(fact)

        for siblings in groups.values():
            if len(siblings) <= 1:
                # Single observation — explicitly mark it as not restated.
                siblings[0].setdefault("is_restated", False)
                siblings[0].setdefault("superseded_by", None)
                siblings[0].setdefault("prior_values", [])
                siblings[0].setdefault("latest_known_value", siblings[0].get("val"))
                continue
            ordered = sorted(siblings, key=lambda f: f.get("filed", ""))
            latest = ordered[-1]
            latest_val = latest.get("val")
            latest_accn = latest.get("accn") or latest.get("accession", "")
            distinct_vals = []
            for prior in ordered[:-1]:
                pv = prior.get("val")
                prior["is_restated"] = pv != latest_val
                prior["superseded_by"] = latest_accn if prior["is_restated"] else None
                prior["latest_known_value"] = latest_val
                if pv != latest_val and pv not in [d["val"] for d in distinct_vals]:
                    distinct_vals.append({
                        "val": pv,
                        "filed": prior.get("filed", ""),
                        "accession": prior.get("accn") or prior.get("accession", ""),
                    })
            latest["is_restated"] = False
            latest["superseded_by"] = None
            latest["latest_known_value"] = latest_val
            latest["prior_values"] = distinct_vals
        return facts

    def company_concept(self, identifier: str | int, taxonomy: str, tag: str,
                        unit: Optional[str] = None, limit: int = 20,
                        suggest_on_404: bool = True,
                        period_type: Optional[str] = None,
                        as_of: Optional[str] = None,
                        since: Optional[str] = None) -> dict:
        """Return all facts for a single company concept.

        `as_of` filters to facts whose `filed` date is on or before that date,
        eliminating look-ahead bias for backtest-style queries.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company

        path = (
            f"/api/xbrl/companyconcept/CIK{company['cik']}/"
            f"{quote_path_segment(taxonomy)}/{quote_path_segment(tag)}.json"
        )
        result = self._get(path)
        if "error" in result:
            if suggest_on_404 and "404" in result.get("error", ""):
                result["suggestions"] = self.suggest_concepts(company["cik"], taxonomy, tag)
            return result

        facts = []
        for unit_name, rows in result.get("units", {}).items():
            if unit and unit_name != unit:
                continue
            for row in rows:
                item = dict(row)
                item["unit"] = unit_name
                item.update(filing_urls(company["cik"], item.get("accn", ""), ""))
                enrich_fact_metadata(item, company["cik"])
                if period_type and item.get("period_type") != period_type:
                    continue
                if as_of and item.get("filed", "") > as_of:
                    continue
                if since and item.get("filed", "") < since:
                    continue
                facts.append(item)

        # Back-fill restatement state across all same-period siblings before
        # truncating to `limit`, so even truncated views carry accurate flags.
        self._populate_restatement_state(facts)

        facts.sort(key=lambda x: (x.get("filed", ""), x.get("end", "")), reverse=True)
        restated_count = sum(1 for f in facts if f.get("is_restated"))
        return {
            "cik": normalize_cik(result.get("cik", company["cik"])),
            "name": result.get("entityName", company.get("name", "")),
            "taxonomy": result.get("taxonomy", taxonomy),
            "tag": result.get("tag", tag),
            "label": result.get("label", ""),
            "description": result.get("description", ""),
            "as_of": as_of,
            "facts": facts[:limit],
            "total": len(facts),
            "restated_facts_in_window": restated_count,
        }

    def company_concept_alias(self, identifier: str | int, concept: str,
                              unit: Optional[str] = None, limit: int = 20,
                              period_type: Optional[str] = None,
                              as_of: Optional[str] = None,
                              since: Optional[str] = None,
                              canonical_union: bool = False) -> dict:
        """Return facts for a friendly concept alias, choosing the freshest candidate tag.

        If `canonical_union` is True, merge facts across all candidate tags
        (deduped by `(start, end, accn)`) instead of picking just the freshest.
        """
        key = concept_alias_key(concept)
        candidates = concept_alias_candidates(concept, unit=unit)
        if key not in COMMON_CONCEPT_CANDIDATES:
            taxonomy, tag, resolved_unit = candidates[0]
            return self.company_concept(
                identifier, taxonomy, tag, unit=resolved_unit, limit=limit,
                period_type=period_type, as_of=as_of, since=since,
            )

        errors = []
        results = []
        for taxonomy, tag, resolved_unit in candidates:
            result = self.company_concept(
                identifier, taxonomy, tag, unit=resolved_unit, limit=limit,
                suggest_on_404=False, period_type=period_type, as_of=as_of,
                since=since,
            )
            if "error" in result:
                errors.append(result["error"])
                continue
            if result.get("facts"):
                results.append(result)

        if not results:
            return {"error": errors[0] if errors else f"No facts found for {concept}", "facts": []}

        if canonical_union:
            seen = set()
            merged = []
            sources = []
            for r in results:
                sources.append({"taxonomy": r.get("taxonomy"), "tag": r.get("tag"),
                                "label": r.get("label", ""), "fact_count": len(r.get("facts", []))})
                for fact in r.get("facts", []):
                    key3 = (fact.get("start", ""), fact.get("end", ""), fact.get("accn", ""))
                    if key3 in seen:
                        continue
                    item = dict(fact)
                    item["source_tag"] = r.get("tag")
                    merged.append(item)
                    seen.add(key3)
            merged.sort(key=lambda x: (x.get("filed", ""), x.get("end", "")), reverse=True)
            return {
                "cik": results[0].get("cik", ""),
                "name": results[0].get("name", ""),
                "alias": concept,
                "canonical_union": True,
                "as_of": as_of,
                "candidate_tags": [tag for _, tag, _ in candidates],
                "tag_sources": sources,
                "facts": merged[:limit],
                "total": len(merged),
            }

        best = max(results, key=lambda result: fact_sort_key(latest_distinct_fact(result.get("facts", [])) or {}))
        best["alias"] = concept
        best["candidate_tags"] = [tag for _, tag, _ in candidates]
        return best

    def concept_info(self, alias_or_tag: str, taxonomy: str = "us-gaap",
                     filer: str = "AAPL") -> dict:
        """Return metadata about an alias or tag: candidates, label, units, sample fact count.

        For aliases, lists every candidate tag the CLI tries plus their freshness
        as reported by the reference filer's companyfacts. For raw tags, fetches
        the reference filer's facts to surface label/description/units.
        """
        key = concept_alias_key(alias_or_tag)
        if key in COMMON_CONCEPT_CANDIDATES:
            candidates = concept_alias_candidates(alias_or_tag)
            facts = self.company_facts(filer, taxonomy=taxonomy, limit=10000)
            tag_index = {c.get("tag"): c for c in facts.get("concepts", [])} if "error" not in facts else {}
            candidate_info = []
            for tax, tag, unit in candidates:
                info = tag_index.get(tag, {})
                candidate_info.append({
                    "taxonomy": tax,
                    "tag": tag,
                    "default_unit": unit,
                    "label": info.get("label", ""),
                    "fact_count_in_filer": info.get("fact_count", 0),
                    "latest_filed_in_filer": info.get("latest_filed", ""),
                    "units_in_filer": info.get("units", ""),
                })
            return {
                "alias": alias_or_tag,
                "is_alias": True,
                "reference_filer": filer,
                "candidates": candidate_info,
            }
        # Treat as a literal tag — look it up in the reference filer's facts.
        facts = self.company_facts(filer, taxonomy=taxonomy, limit=10000)
        if "error" in facts:
            return facts
        for concept in facts.get("concepts", []):
            if concept.get("tag", "").lower() == alias_or_tag.lower():
                return {
                    "alias": None,
                    "is_alias": False,
                    "reference_filer": filer,
                    "tag": concept.get("tag"),
                    "taxonomy": concept.get("taxonomy", taxonomy),
                    "label": concept.get("label", ""),
                    "description": concept.get("description", ""),
                    "units": concept.get("units", ""),
                    "fact_count_in_filer": concept.get("fact_count", 0),
                    "latest_filed_in_filer": concept.get("latest_filed", ""),
                }
        return {"error": f"Tag {alias_or_tag} not found in reference filer {filer} facts",
                "alias": None, "is_alias": False}

    def expand_group(self, expression: str) -> dict:
        """Expand a `@group` expression into a list of identifiers.

        Supported groups:
        - `@dow30` — Dow Jones Industrial Average (static, current as of CLI version).
        - `@sic:NNNN` — all filers with that SIC code, from the ticker/CIK map.
        - `@cik` for a literal — pass-through.
        Unknown groups return an error.
        """
        text = expression.strip()
        if not text.startswith("@"):
            return {"error": f"Not a group expression: {expression}"}
        body = text[1:]
        if body.lower() == "dow30":
            return {"group": "dow30", "identifiers": list(DOW30_TICKERS),
                    "source": "static", "as_of": "2026-05-08"}
        if body.lower().startswith("sic:"):
            try:
                sic = int(body.split(":", 1)[1])
            except ValueError:
                return {"error": f"Invalid SIC: {body}"}
            companies = self.companies()
            if "error" in companies:
                return companies
            matches = [c for c in companies.get("companies", [])
                       if str(c.get("sic", "")) == str(sic)]
            return {"group": f"sic:{sic}", "identifiers": [c["ticker"] for c in matches if c.get("ticker")],
                    "ciks": [c["cik"] for c in matches],
                    "source": "ticker_map"}
        return {"error": f"Unknown group: {expression}"}

    def dei(self, identifier: str | int) -> dict:
        """Surface entity-level (DEI) metadata for a filer.

        Pulls from the submissions JSON which already includes filer status,
        SIC, fiscal year end, exchanges, addresses, and former names.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]
        result = self._get(f"/submissions/CIK{cik}.json")
        if "error" in result:
            return result
        return {
            "cik": cik,
            "ticker": company.get("ticker", ""),
            "name": result.get("name", company.get("name", "")),
            "entityType": result.get("entityType", ""),
            "sic": result.get("sic", ""),
            "sicDescription": result.get("sicDescription", ""),
            "category": result.get("category", ""),
            "fiscalYearEnd": result.get("fiscalYearEnd", ""),
            "stateOfIncorporation": result.get("stateOfIncorporation", ""),
            "stateOfIncorporationDescription": result.get("stateOfIncorporationDescription", ""),
            "tickers": result.get("tickers", []),
            "exchanges": result.get("exchanges", []),
            "ein": result.get("ein", ""),
            "addresses": result.get("addresses", {}),
            "phone": result.get("phone", ""),
            "website": result.get("website", ""),
            "formerNames": result.get("formerNames", []),
            "investorWebsite": result.get("investorWebsite", ""),
            "description": result.get("description", ""),
        }

    def peers(self, identifier: str | int, candidates: Optional[list[str]] = None,
              by: str = "sic", limit: int = 10) -> dict:
        """Return peer filers from a candidate list, grouped by SIC code.

        The SEC ticker/CIK/exchange map does not carry SIC codes, so peer
        discovery requires a candidate set. Pass `candidates` (e.g. tickers from
        `expand_group("@dow30")` or any custom list) and this method fetches
        each candidate's filer metadata and keeps those matching the target's
        SIC.
        """
        target = self.dei(identifier)
        if "error" in target:
            return target
        sic = str(target.get("sic", ""))
        if not sic:
            return {"error": f"No SIC code for {identifier}", "peers": []}
        if by != "sic":
            return {"error": f"Unsupported peer ranking: {by}", "peers": []}

        if candidates is None:
            candidates = list(DOW30_TICKERS)
        peers = []
        for cand in candidates:
            if str(cand).strip().upper() == str(target.get("ticker", "")).upper():
                continue
            cand_dei = self.dei(cand)
            if "error" in cand_dei:
                continue
            if str(cand_dei.get("sic", "")) != sic:
                continue
            peers.append({
                "cik": cand_dei["cik"],
                "ticker": cand_dei.get("ticker", ""),
                "name": cand_dei.get("name", ""),
                "exchanges": cand_dei.get("exchanges", []),
                "sic": cand_dei.get("sic", ""),
                "sicDescription": cand_dei.get("sicDescription", ""),
            })
        peers.sort(key=lambda p: p.get("name", ""))
        return {
            "target": {"cik": target["cik"], "ticker": target.get("ticker", ""),
                       "name": target.get("name", ""), "sic": sic,
                       "sicDescription": target.get("sicDescription", "")},
            "by": by,
            "candidate_set_size": len(candidates),
            "peers": peers[:limit],
            "total": len(peers),
        }

    def tag_search(self, query: str, filer: str = "AAPL", taxonomy: str = "us-gaap",
                   limit: int = 25) -> dict:
        """Search XBRL tag labels and descriptions in a reference filer's facts.

        Returns matches by substring or fuzzy match. Default reference filer is
        Apple, which reports a broad set of common tags. Override `--filer` for
        domain-specific tags (e.g. financial-services concepts).
        """
        if not query.strip():
            return {"error": "Tag search query cannot be blank", "matches": []}
        facts = self.company_facts(filer, taxonomy=taxonomy, limit=10000)
        if "error" in facts:
            return {"error": facts["error"], "matches": []}
        needle = query.strip().lower()
        scored = []
        for index, concept in enumerate(facts.get("concepts", [])):
            tag = concept.get("tag", "")
            label = str(concept.get("label") or "")
            description = str(concept.get("description") or "")
            haystack = f"{tag} {label} {description}".lower()
            score = None
            if needle == tag.lower():
                score = 0
            elif needle in tag.lower():
                score = 1
            elif needle in label.lower():
                score = 2
            elif needle in haystack:
                score = 3
            if score is None:
                continue
            scored.append((score, index, {
                "taxonomy": concept.get("taxonomy", taxonomy),
                "tag": tag,
                "label": label,
                "description": description[:200],
                "units": concept.get("units", ""),
                "fact_count": concept.get("fact_count", 0),
                "latest_filed": concept.get("latest_filed", ""),
            }))
        scored.sort(key=lambda x: (x[0], x[1]))
        return {
            "query": query,
            "reference_filer": filer,
            "taxonomy": taxonomy,
            "matches": [item for _, _, item in scored[:limit]],
            "total": len(scored),
        }

    @staticmethod
    def list_frames(taxonomy: str = "us-gaap", since_year: Optional[int] = None,
                    until_year: Optional[int] = None, kinds: Optional[set] = None) -> dict:
        """Enumerate plausible frame strings deterministically.

        SEC does not expose a frames-listing endpoint; valid frame strings follow
        a known pattern (`CY{YYYY}`, `CY{YYYY}Q{1..4}`, `CY{YYYY}Q{1..4}I` for
        instants). This generator emits all such strings for a year range so
        agents do not have to guess.
        """
        if not since_year:
            since_year = 2009
        if not until_year:
            until_year = date.today().year
        kinds = kinds or {"annual", "quarterly", "instant"}
        frames = []
        for year in range(int(since_year), int(until_year) + 1):
            if "annual" in kinds:
                frames.append({"frame": f"CY{year}", "kind": "annual",
                               "year": year, "quarter": None})
            for q in (1, 2, 3, 4):
                if "quarterly" in kinds:
                    frames.append({"frame": f"CY{year}Q{q}", "kind": "quarterly",
                                   "year": year, "quarter": q})
                if "instant" in kinds:
                    frames.append({"frame": f"CY{year}Q{q}I", "kind": "instant",
                                   "year": year, "quarter": q})
        return {
            "taxonomy": taxonomy,
            "since_year": since_year,
            "until_year": until_year,
            "kinds": sorted(kinds),
            "frames": frames,
            "total": len(frames),
        }

    def suggest_concepts(self, identifier: str | int, taxonomy: str, tag: str,
                         limit: int = 8) -> list[dict]:
        """Suggest similar concept tags from companyfacts."""
        facts = self.company_facts(identifier, taxonomy=taxonomy, limit=10000)
        if "error" in facts:
            return []

        concepts = facts.get("concepts", [])
        tags = [c.get("tag", "") for c in concepts]
        wanted = tag.lower()
        wanted_stem = wanted[:-1] if wanted.endswith("s") else wanted
        scored = []
        for concept in concepts:
            concept_tag = concept.get("tag", "")
            label = str(concept.get("label") or "")
            description = str(concept.get("description") or "")
            haystack = " ".join([concept_tag, label, description]).lower()
            label_haystack = " ".join([label, description]).lower()
            if wanted in haystack or (len(wanted_stem) >= 4 and wanted_stem in label_haystack):
                scored.append((0, concept))

        seen = {c["tag"] for _, c in scored}
        for match in get_close_matches(tag, tags, n=limit * 2, cutoff=0.6):
            if match in seen:
                continue
            concept = next(c for c in concepts if c.get("tag") == match)
            scored.append((1, concept))
            seen.add(match)

        return [concept for _, concept in scored[:limit]]

    def frame(self, taxonomy: str, tag: str, unit: str, frame: str,
              limit: int = 25, sort_by: str = "value") -> dict:
        """Return a cross-company XBRL frame."""
        path = (
            f"/api/xbrl/frames/{quote_path_segment(taxonomy)}/{quote_path_segment(tag)}/"
            f"{quote_path_segment(unit)}/{quote_path_segment(frame)}.json"
        )
        result = self._get(path)
        if "error" in result:
            return result

        rows = []
        for row in result.get("data", []):
            item = dict(row)
            if "cik" in item:
                item["cik"] = normalize_cik(item["cik"])
            item.setdefault("frame", result.get("ccp", frame))
            enrich_fact_metadata(item, item.get("cik", "0"))
            rows.append(item)

        if sort_by == "name":
            rows.sort(key=lambda x: x.get("entityName", ""))
        elif sort_by == "value":
            rows.sort(key=lambda x: _number_or_zero(x.get("val")), reverse=True)

        return {
            "taxonomy": result.get("taxonomy", taxonomy),
            "tag": result.get("tag", tag),
            "unit": result.get("uom", unit),
            "frame": result.get("ccp", frame),
            "label": result.get("label", ""),
            "description": result.get("description", ""),
            "facts": rows[:limit],
            "total": len(rows),
        }

    def latest_filing(self, identifier: str | int, form: Optional[str] = None,
                      all_history: bool = False) -> dict:
        """Return the latest matching filing."""
        result = self.submissions(identifier, limit=1, form=form, all_history=all_history)
        if "error" in result:
            return result
        filings = result.get("filings", [])
        if not filings:
            label = f" form {form}" if form else ""
            return {"error": f"No matching{label} filing found"}
        filing = dict(filings[0])
        filing["cik"] = result.get("cik", "")
        filing["name"] = result.get("name", "")
        return filing

    def filing_documents(self, cik: str | int, accession_number: str) -> dict:
        """Parse the SEC filing index and return document/exhibit rows."""
        try:
            urls = filing_urls(cik, accession_number)
        except ValueError as exc:
            return {"error": str(exc), "documents": []}
        try:
            html = self._get_text(urls["filing_url"])
        except Exception as exc:
            return {"error": str(exc), "documents": []}

        parser = FilingIndexParser()
        parser.feed(html)
        documents = []
        base = urls["filing_url"].rsplit("/", 1)[0]
        for row in parser.rows:
            if not row.get("document"):
                continue
            href = row.get("href", "") or row.get("document", "")
            if href.startswith("http"):
                row["url"] = href
            elif href.startswith("/"):
                row["url"] = f"{SEC_BASE_URL}{href}"
            elif href.startswith("Archives/"):
                row["url"] = f"{SEC_BASE_URL}/{href}"
            else:
                row["url"] = f"{base}/{href}"
            documents.append(row)
        return {
            "cik": normalize_cik(cik),
            "accessionNumber": accession_number,
            "filing_url": urls["filing_url"],
            "documents": documents,
        }

    def filing_documents_for_accession(self, accession_or_url: str, cik: Optional[str] = None) -> dict:
        """Return filing documents for an accession, filing index URL, or primary document URL."""
        accession = extract_accession(accession_or_url)
        try:
            resolved_cik = normalize_cik(cik) if cik else extract_cik_from_url(accession_or_url)
        except ValueError as exc:
            return {"error": str(exc), "documents": []}
        if not accession:
            return {"error": f"Could not find accession number in {accession_or_url}", "documents": []}
        if not resolved_cik:
            return {"error": "CIK is required when passing only an accession number", "documents": []}
        return self.filing_documents(resolved_cik, accession)

    def latest_earnings(self, identifier: str | int, limit: int = 12) -> dict:
        """Find latest earnings 8-K and summarize likely earnings-release exhibits."""
        result = self.submissions(identifier, form="8-K", limit=limit)
        if "error" in result:
            return result
        for filing in result.get("filings", []):
            if "2.02" not in filing.get("items", ""):
                continue
            docs = self.filing_documents(result["cik"], filing["accessionNumber"])
            if "error" in docs:
                continue
            exhibits = [
                doc for doc in docs.get("documents", [])
                if doc.get("type", "").upper().startswith("EX-99")
            ]
            exhibit = exhibits[0] if exhibits else None
            text = ""
            if exhibit:
                try:
                    text = html_to_text(self._get_text(exhibit["url"]))
                except Exception:
                    text = ""
            return {
                "cik": result["cik"],
                "name": result["name"],
                "filing": filing,
                "exhibit": exhibit,
                "highlights": extract_earnings_highlights(text),
            }
        return {"error": "No recent Item 2.02 earnings 8-K found", "filings": result.get("filings", [])}

    def events(self, identifier: str | int, limit: int = 20,
               since_last_fetch: bool = False,
               state_store: Optional["StateStore"] = None) -> dict:
        """Detect notable recent filing events from 8-K metadata and document text."""
        result = self.submissions(
            identifier, form="8-K", limit=limit,
            since_last_fetch=since_last_fetch, state_store=state_store,
        )
        if "error" in result:
            return result
        events = []
        for filing in result.get("filings", []):
            event_types = event_types_from_items(filing.get("items", ""))
            snippets = {}
            try:
                text = html_to_text(self._get_text(filing.get("primary_doc_url", "")))
            except Exception:
                text = ""
            for event_type, keywords in EVENT_KEYWORDS.items():
                snippet = first_snippet(text, keywords)
                if snippet:
                    event_types.add(event_type)
                    snippets[event_type] = snippet
            if event_types:
                events.append({
                    "filingDate": filing.get("filingDate", ""),
                    "form": filing.get("form", ""),
                    "items": filing.get("items", ""),
                    "accessionNumber": filing.get("accessionNumber", ""),
                    "filing_url": filing.get("filing_url", ""),
                    "event_types": sorted(event_types),
                    "snippets": snippets,
                })
        return {"cik": result["cik"], "name": result["name"], "events": events, "total": len(events)}

    def dashboard(self, identifier: str | int) -> dict:
        """One-call composite: profile + key metrics + recent events + earnings + quality.

        Composes the existing primitives so an agent can get the full state of
        a filer in a single CLI invocation.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]
        out = {
            "cik": cik, "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
        }

        profile = self.submissions(identifier, limit=10)
        if "error" not in profile:
            out["profile"] = {
                "sic": profile.get("sic", ""),
                "sicDescription": profile.get("sicDescription", ""),
                "fiscalYearEnd": profile.get("fiscalYearEnd", ""),
                "exchanges": profile.get("exchanges", []),
                "tickers": profile.get("tickers", []),
                "latest_filings": profile.get("filings", [])[:5],
            }

        bundle = ["revenue", "net_income", "operating_income",
                  "operating_cash_flow", "cash", "debt", "equity"]
        metrics_result = self.metrics(identifier, bundle)
        if "error" not in metrics_result:
            out["metrics"] = metrics_result.get("metrics", [])
            out["reference_date"] = metrics_result.get("reference_date", "")

        ratios_result = self.ratios(identifier, period_type="annual")
        if "error" not in ratios_result:
            out["ratios"] = [
                {"metric": r["metric"], "value": r.get("value"),
                 "formula": r.get("formula", "")}
                for r in ratios_result.get("ratios", [])
                if r.get("metric") in {"gross_margin", "operating_margin",
                                        "net_margin", "fcf_margin", "roe", "roa",
                                        "debt_to_equity", "current_ratio"}
            ]

        events_result = self.events(identifier, limit=8)
        if "error" not in events_result:
            out["events"] = events_result.get("events", [])[:5]

        try:
            earnings_result = self.latest_earnings(identifier, limit=8)
            if "error" not in earnings_result:
                out["latest_earnings"] = {
                    "filing": earnings_result.get("filing"),
                    "exhibit": earnings_result.get("exhibit"),
                    "highlights": earnings_result.get("highlights", [])[:5],
                }
        except Exception:
            pass

        quality_result = self.quality(identifier)
        if "error" not in quality_result:
            out["quality"] = {
                "flagged_count": quality_result.get("flagged_count", 0),
                "flags": [{"flag": f["flag"], "value": f.get("value"),
                           "flagged": f.get("flagged", False)}
                          for f in quality_result.get("flags", [])],
            }

        return out

    def governance(self, identifier: str | int, year: Optional[int] = None,
                   db_path: Optional[str] = None) -> dict:
        """Heuristic DEF 14A extraction: audit fees, board size, proposals.

        Picks the latest DEF 14A (filtered by `year` if given), strips the
        primary doc to text, and runs targeted regex extractors. Each field
        is returned alongside its matched context so agents can verify
        before relying on it.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]
        result = self.submissions(identifier, form="DEF 14A", limit=10)
        if "error" in result:
            return result
        filings = result.get("filings", [])
        if not filings:
            return {"error": f"No DEF 14A filings for {identifier}"}
        target = filings[0]
        if year:
            for f in filings:
                if str(year) in (f.get("filingDate", "") or ""):
                    target = f
                    break

        body_text = ""
        body_source = "live"
        if db_path:
            with mirror_mod.open_db(db_path) as conn:
                row = conn.execute(
                    "SELECT body FROM filing_bodies_fts WHERE cik = ? AND accession = ?",
                    (cik, target["accessionNumber"]),
                ).fetchone()
                if row and row[0]:
                    body_text = row[0]
                    body_source = "mirror"
        if not body_text:
            url = target.get("primary_doc_url", "")
            if not url:
                return {"error": "No primary document URL for proxy"}
            try:
                html = self._get_text(url)
            except Exception as exc:
                return {"error": f"Could not fetch proxy body: {exc}"}
            body_text = html_to_text(html)

        summary = governance_mod.summarize(body_text)
        return {
            "cik": cik,
            "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "filing": {
                "accession": target.get("accessionNumber", ""),
                "filed": target.get("filingDate", ""),
                "form": target.get("form", ""),
                "filing_url": target.get("filing_url", ""),
            },
            "body_source": body_source,
            "body_length": len(body_text),
            **summary,
        }

    def filing_section(self, identifier: str | int, form: str = "10-K",
                       section: str = "Risk Factors",
                       as_of: Optional[str] = None,
                       db_path: Optional[str] = None,
                       max_chars: int = 50000) -> dict:
        """Heuristically extract one Item-level section from a filing.

        `form` is one of `10-K`/`10-Q`. `section` accepts either the item code
        (`1A`) or the title (`Risk Factors`). Strategy:
        1. If `db_path` is set and the filing's body has been mirrored, read
           from the mirror (no SEC round-trip).
        2. Otherwise fetch the primary doc live and strip HTML to text.
        3. Slice between Item-header regex matches.
        Returns the section text (truncated to `max_chars`) plus a
        `confidence` field — `"high"` when bounded by the next Item header,
        `"low"` when only the target was found.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]
        # Pick the latest filing of the requested form.
        result = self.submissions(identifier, form=form, limit=10,
                                   end_date=as_of)
        if "error" in result:
            return result
        filings = result.get("filings", [])
        if not filings:
            return {"error": f"No {form} filings for {identifier}"}
        target = filings[0]

        body_text = ""
        body_source = "live"
        # Try the mirror first if a DB path was provided and the body is there.
        if db_path:
            with mirror_mod.open_db(db_path) as conn:
                row = conn.execute(
                    "SELECT body FROM filing_bodies_fts WHERE cik = ? AND accession = ?",
                    (cik, target["accessionNumber"]),
                ).fetchone()
                if row and row[0]:
                    body_text = row[0]
                    body_source = "mirror"

        if not body_text:
            url = target.get("primary_doc_url", "")
            if not url:
                return {"error": "No primary document URL for filing"}
            try:
                html = self._get_text(url)
            except Exception as exc:
                return {"error": f"Could not fetch filing body: {exc}"}
            body_text = html_to_text(html)

        schema = form.upper()
        out = items_mod.extract_section(body_text, section, schema=schema)
        if "error" in out:
            return {**out, "filing": {
                "accession": target.get("accessionNumber", ""),
                "filed": target.get("filingDate", ""),
                "form": target.get("form", ""),
                "filing_url": target.get("filing_url", ""),
            }}
        truncated_text = out["text"][:max_chars]
        truncated = len(truncated_text) < len(out["text"])
        return {
            "cik": cik,
            "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "filing": {
                "accession": target.get("accessionNumber", ""),
                "filed": target.get("filingDate", ""),
                "form": target.get("form", ""),
                "filing_url": target.get("filing_url", ""),
            },
            "body_source": body_source,
            "body_length": len(body_text),
            "section": {
                "item": out["item"],
                "title": out["title"],
                "text": truncated_text,
                "length": out["length"],
                "truncated_to_max_chars": truncated,
                "confidence": out["confidence"],
                "items_found_in_document": out["items_in_document"],
            },
            "caveat": ("Item-level extraction is heuristic. Confidence "
                       "`high` means the section is bounded by the next Item "
                       "header; `medium` runs to end-of-document; `low` means "
                       "only one Item header matched (likely a parsing miss)."),
        }

    def _fetch_13f_holdings(self, cik: str, accession: str) -> list[dict]:
        """Fetch and parse the infoTable XML for one 13F-HR accession.

        13F filings ship two related XMLs: `primary_doc.xml` (the cover) and
        an `INFORMATION TABLE` document with line-item holdings. The
        information-table filename is filer-specific (often `*infotable.xml`,
        but sometimes `<accn-suffix>.xml`). Match by document type first,
        then by filename heuristic, and only consider .xml files.
        """
        docs = self.filing_documents(cik, accession)
        if "error" in docs:
            return []
        for doc in docs.get("documents", []):
            doc_type = (doc.get("type") or "").upper()
            name = (doc.get("document") or "").lower()
            url = doc.get("url", "")
            if not url or not name.endswith(".xml"):
                continue
            is_info_table = (
                "INFORMATION TABLE" in doc_type
                or name.endswith("infotable.xml")
                or name.endswith("info_table.xml")
            )
            if not is_info_table:
                continue
            try:
                xml = self._get_text(url)
            except Exception:
                continue
            return holders_mod.parse_infotable_xml(xml)
        return []

    def holdings(self, identifier: str | int, quarter: Optional[str] = None,
                 top_n: int = 50) -> dict:
        """Single 13F filer's holdings (most recent quarter unless `quarter` given).

        `quarter` accepts forms like `2025Q4`, `CY2025Q4`, or a date string —
        the CLI matches against `periodOfReport` / filing date heuristically.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]
        result = self.submissions(identifier, form="13F-HR", limit=20)
        if "error" in result:
            return result
        filings = result.get("filings", [])
        if not filings:
            return {"error": f"No 13F-HR filings for {identifier}", "rows": []}

        chosen = filings[0]
        if quarter:
            qmatch = re.match(r"(?:CY)?(\d{4})Q([1-4])", str(quarter).upper())
            if qmatch:
                year, q = int(qmatch.group(1)), int(qmatch.group(2))
                # Quarter end month (Mar/Jun/Sep/Dec)
                end_month = {1: "03", 2: "06", 3: "09", 4: "12"}[q]
                wanted = f"{year}-{end_month}"
                for f in filings:
                    if (f.get("primaryDocument", "")
                            and (f.get("filingDate", "").startswith(f"{year}")
                                 or wanted in f.get("primaryDocument", ""))):
                        chosen = f
                        break

        rows = self._fetch_13f_holdings(cik, chosen.get("accessionNumber", ""))
        agg = holders_mod.aggregate_filer_holdings(rows, top_n=top_n)
        return {
            "cik": cik,
            "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "filing": {
                "accession": chosen.get("accessionNumber", ""),
                "form": chosen.get("form", ""),
                "filed": chosen.get("filingDate", ""),
                "filing_url": chosen.get("filing_url", ""),
            },
            "rows": rows,
            **agg,
            "caveat": ("13F-HR reports holdings 45 days after quarter-end and "
                       "covers only equity securities listed in 13F-HR Section "
                       "13(f) tables. Short positions are not disclosed."),
        }

    def holders(self, identifier: str | int, candidates: list[str],
                quarter: Optional[str] = None, top_n: int = 25,
                cusip: Optional[str] = None,
                max_filers: int = 30) -> dict:
        """Find which institutional filers in a candidate list hold an issuer.

        SEC does not publish a ticker→CUSIP map, so the search matches on
        `nameOfIssuer` substrings (case-insensitive) and on `cusip` if the
        caller supplies one. For comprehensive coverage, pass an explicit
        `cusip`.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        target_name = (company.get("name") or str(identifier)).strip().upper()
        # Drop common suffixes for fuzzier substring match.
        needle = re.sub(r"\b(INC|CORP|CORPORATION|CO|LTD|LLC|PLC|HOLDINGS|GROUP)\b",
                        "", target_name).strip(", .").strip()

        per_filer_rows = []
        scanned = 0
        skipped = 0
        for cand in candidates[:max_filers]:
            cand_company = self.resolve_company(cand)
            if "error" in cand_company:
                skipped += 1
                continue
            cand_cik = cand_company["cik"]
            # `resolve_company` leaves `name` empty for direct-CIK lookups;
            # fall back to the submissions JSON so cross-filer rollups carry
            # readable names.
            if not cand_company.get("name"):
                dei = self.dei(cand)
                if "error" not in dei:
                    cand_company["name"] = dei.get("name", "")
                    cand_company["ticker"] = cand_company.get("ticker") or dei.get("ticker", "")
            holdings_result = self.submissions(cand, form="13F-HR", limit=4)
            if "error" in holdings_result:
                skipped += 1
                continue
            f13s = holdings_result.get("filings", [])
            if not f13s:
                skipped += 1
                continue
            chosen = f13s[0]
            if quarter:
                qmatch = re.match(r"(?:CY)?(\d{4})Q([1-4])", str(quarter).upper())
                if qmatch:
                    year = int(qmatch.group(1))
                    for f in f13s:
                        if f.get("filingDate", "").startswith(str(year)):
                            chosen = f
                            break
            scanned += 1
            rows = self._fetch_13f_holdings(cand_cik, chosen.get("accessionNumber", ""))
            for r in rows:
                issuer = (r.get("name_of_issuer") or "").upper()
                cusip_match = cusip and r.get("cusip", "") == cusip
                name_match = needle and needle in issuer
                if cusip_match or name_match:
                    r2 = dict(r)
                    r2["filer_cik"] = cand_cik
                    r2["filer_name"] = cand_company.get("name", "")
                    r2["filer_ticker"] = cand_company.get("ticker", "")
                    r2["filing_filed"] = chosen.get("filingDate", "")
                    r2["filing_accession"] = chosen.get("accessionNumber", "")
                    per_filer_rows.append(r2)

        agg = holders_mod.aggregate_holders(per_filer_rows)
        return {
            "issuer_cik": company["cik"],
            "issuer_name": company.get("name", ""),
            "match_strategy": ("cusip" if cusip else "issuer_name_substring"),
            "needle": cusip or needle,
            "candidates_total": len(candidates),
            "candidates_scanned": scanned,
            "candidates_skipped": skipped,
            "rows": per_filer_rows[:top_n],
            **agg,
            "caveat": ("Without an explicit --cusip, matching is by issuer-name "
                       "substring against 13F filers' nameOfIssuer field. Some "
                       "filers report shorter or longer names; cross-check "
                       "share counts before relying on cross-filer totals."),
        }

    def insiders(self, identifier: str | int, since: Optional[str] = None,
                 limit: int = 50, max_form4_fetches: int = 50) -> dict:
        """Aggregate Form 4 transactions for a filer.

        Walks recent Form 4 filings, fetches the primary XML for each, and
        aggregates by reporting owner + transaction code. `since` filters to
        filings on or after that date; `limit` caps how many Form 4s are
        fetched (default 50, hard ceiling 200).
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]
        max_form4_fetches = min(max_form4_fetches, 200)
        result = self.submissions(identifier, form="4", limit=max_form4_fetches,
                                  start_date=since)
        if "error" in result:
            return result

        transactions: list[dict] = []
        fetched = 0
        failed = 0
        for filing in result.get("filings", []):
            url = filing.get("primary_doc_url") or ""
            if not url:
                continue
            # SEC's `primaryDocument` for Form 4 points at the styled XML
            # (e.g. `xslF345X06/wk-form4_*.xml`). The raw schema-conformant XML
            # lives at the same filename without the stylesheet directory.
            raw_url = re.sub(r"/xslF345X[0-9]+/", "/", url)
            try:
                xml = self._get_text(raw_url)
            except Exception:
                failed += 1
                continue
            parsed = insiders_mod.parse_form4_xml(xml)
            if "error" in parsed:
                failed += 1
                continue
            fetched += 1
            owner = parsed.get("reporting_owner", {})
            for tx in (parsed.get("non_derivative_transactions", [])
                       + parsed.get("derivative_transactions", [])):
                tx2 = dict(tx)
                tx2["owner_name"] = owner.get("name", "")
                tx2["owner_cik"] = owner.get("cik", "")
                tx2["officer_title"] = owner.get("officer_title", "")
                tx2["is_director"] = owner.get("is_director", False)
                tx2["is_officer"] = owner.get("is_officer", False)
                tx2["filed"] = filing.get("filingDate", "")
                tx2["accession"] = filing.get("accessionNumber", "")
                transactions.append(tx2)

        agg = insiders_mod.aggregate(transactions)
        return {
            "cik": cik, "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "since": since,
            "form4s_fetched": fetched, "form4s_failed": failed,
            "transactions": transactions[:limit],
            "transactions_total": len(transactions),
            **agg,
        }

    def quality(self, identifier: str | int, period_type: str = "annual",
                as_of: Optional[str] = None) -> dict:
        """Earnings-quality and balance-sheet-quality flags.

        Returns a list of named flags, each with a value and a `flagged: bool`
        indicator. Threshold values are documented in `docs/definitions.md`.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]

        ni = self._latest_fact_for_alias(cik, "net_income", period_type=period_type, as_of=as_of)
        ocf = self._latest_fact_for_alias(cik, "operating_cash_flow", period_type=period_type, as_of=as_of)
        revenue = self._latest_fact_for_alias(cik, "revenue", period_type=period_type, as_of=as_of)
        sbc = self._latest_fact_for_alias(cik, "stock_compensation", period_type=period_type, as_of=as_of)
        anchor = (ni or revenue or {}).get("end", "")

        assets = self._instant_fact_at_period_end(cik, "assets", target_end=anchor, as_of=as_of)
        ar = self._instant_fact_at_period_end(cik, "accounts_receivable", target_end=anchor, as_of=as_of)
        inv = self._instant_fact_at_period_end(cik, "inventory", target_end=anchor, as_of=as_of)

        flags: list[dict] = []

        # Accruals ratio = (NI - OpCF) / total Assets. >0.10 is an aggressive-accruals flag.
        ni_v = compute._value_or_none(ni)
        ocf_v = compute._value_or_none(ocf)
        assets_v = compute._value_or_none(assets)
        if ni_v is not None and ocf_v is not None and assets_v not in (None, 0):
            value = (ni_v - ocf_v) / assets_v
            flags.append({
                "flag": "accruals_ratio",
                "value": value,
                "formula": "(NetIncome - OperatingCashFlow) / Assets",
                "threshold": "abs > 0.10",
                "flagged": abs(value) > 0.10,
                "inputs": [
                    compute._input_record("NetIncomeLoss", ni),
                    compute._input_record("OperatingCashFlow", ocf),
                    compute._input_record("Assets", assets),
                ],
            })
        else:
            flags.append({"flag": "accruals_ratio", "value": None,
                          "formula": "(NetIncome - OperatingCashFlow) / Assets",
                          "missing_inputs": [k for k, v in
                                              [("NI", ni_v), ("OCF", ocf_v), ("Assets", assets_v)]
                                              if v is None]})

        # OpCF / NI divergence. <0.8 means OCF is materially below earnings.
        if ni_v not in (None, 0) and ocf_v is not None:
            ratio = ocf_v / ni_v
            flags.append({
                "flag": "ocf_to_ni",
                "value": ratio,
                "formula": "OperatingCashFlow / NetIncome",
                "threshold": "ratio < 0.80",
                "flagged": ratio < 0.80,
                "inputs": [compute._input_record("OperatingCashFlow", ocf),
                           compute._input_record("NetIncomeLoss", ni)],
            })

        # AR/Revenue creep — receivables growing faster than revenue suggests
        # channel stuffing or worsening collections.
        ar_v = compute._value_or_none(ar)
        rev_v = compute._value_or_none(revenue)
        if ar_v is not None and rev_v not in (None, 0):
            ratio = ar_v / rev_v
            flags.append({
                "flag": "ar_to_revenue",
                "value": ratio,
                "formula": "AccountsReceivable / Revenue",
                "threshold": "(no static threshold; track delta vs peers/history)",
                "flagged": False,
                "inputs": [compute._input_record("AccountsReceivable", ar),
                           compute._input_record("Revenue", revenue)],
            })

        # Stock-based compensation as % of revenue. >15% is high.
        sbc_v = compute._value_or_none(sbc)
        if sbc_v is not None and rev_v not in (None, 0):
            ratio = sbc_v / rev_v
            flags.append({
                "flag": "sbc_to_revenue",
                "value": ratio,
                "formula": "StockBasedCompensation / Revenue",
                "threshold": "ratio > 0.15",
                "flagged": ratio > 0.15,
                "inputs": [compute._input_record("StockBasedCompensation", sbc),
                           compute._input_record("Revenue", revenue)],
            })

        # Inventory days = Inventory / (COGS / 365). High vs peers signals build-up.
        # We surface raw inventory + days only when COGS is available.
        cogs = self._latest_fact_for_alias(cik, "cogs", period_type=period_type, as_of=as_of)
        cogs_v = compute._value_or_none(cogs)
        inv_v = compute._value_or_none(inv)
        if inv_v is not None and cogs_v not in (None, 0):
            days = inv_v / (cogs_v / 365)
            flags.append({
                "flag": "inventory_days",
                "value": days,
                "formula": "Inventory / (COGS / 365)",
                "threshold": "(no static threshold; compare to sector)",
                "flagged": False,
                "inputs": [compute._input_record("InventoryNet", inv),
                           compute._input_record("CostOfGoodsAndServicesSold", cogs)],
            })

        # Restatement frequency over last 5 years on key metrics.
        recent_restatements = 0
        for probe in ("revenue", "net_income", "assets"):
            try:
                trail = self.audit_trail(identifier, probe)
            except Exception:
                continue
            if "error" in trail:
                continue
            recent_restatements += len(trail.get("restated_periods", []))
        flags.append({
            "flag": "restatement_count_recent",
            "value": recent_restatements,
            "formula": "count of restated (start, end) pairs in audit_trail across "
                       "{revenue, net_income, assets}",
            "threshold": "count > 0",
            "flagged": recent_restatements > 0,
        })

        flagged_count = sum(1 for f in flags if f.get("flagged"))
        return {
            "cik": cik, "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "period_type": period_type, "period_end": anchor,
            "as_of": as_of,
            "flags": flags,
            "flagged_count": flagged_count,
        }

    def verify(self, identifier: str | int, period_type: str = "annual",
               as_of: Optional[str] = None) -> dict:
        """Cross-statement consistency checks.

        Each check returns `{check, expected, actual, delta, tolerance, passed}`.
        Tolerance is 1% of the larger absolute value, accounting for rounding.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]

        ni = self._latest_fact_for_alias(cik, "net_income", period_type=period_type, as_of=as_of)
        eps = self._latest_fact_for_alias(cik, "diluted_eps", period_type=period_type, as_of=as_of)
        shares = self._latest_fact_for_alias(cik, "shares_outstanding", period_type="instant", as_of=as_of)

        checks: list[dict] = []

        ni_v = compute._value_or_none(ni)
        eps_v = compute._value_or_none(eps)
        sh_v = compute._value_or_none(shares)
        if ni_v is not None and eps_v not in (None, 0) and sh_v not in (None, 0):
            implied_shares = ni_v / eps_v
            delta = implied_shares - sh_v
            tolerance = max(abs(implied_shares), abs(sh_v)) * 0.05
            checks.append({
                "check": "eps_ties_to_ni_over_shares",
                "expected": sh_v,
                "actual": implied_shares,
                "delta": delta,
                "tolerance": tolerance,
                "passed": abs(delta) <= tolerance,
                "formula": "NetIncome / DilutedEPS ~= SharesOutstanding (within 5%)",
                "inputs": [compute._input_record("NetIncomeLoss", ni),
                           compute._input_record("EarningsPerShareDiluted", eps),
                           compute._input_record("SharesOutstanding", shares)],
                "caveats": ["Diluted EPS uses weighted-average diluted shares; the shares "
                            "outstanding fact is period-end. A 5% tolerance accommodates "
                            "the gap. Larger deltas suggest dilution or buybacks within "
                            "the period."],
            })

        # Gross profit ties to revenue - cogs.
        rev = self._latest_fact_for_alias(cik, "revenue", period_type=period_type, as_of=as_of)
        cogs = self._latest_fact_for_alias(cik, "cogs", period_type=period_type, as_of=as_of)
        gp = self._latest_fact_for_alias(cik, "gross_profit", period_type=period_type, as_of=as_of)
        rev_v = compute._value_or_none(rev)
        cogs_v = compute._value_or_none(cogs)
        gp_v = compute._value_or_none(gp)
        if rev_v is not None and cogs_v is not None and gp_v is not None:
            implied = rev_v - cogs_v
            delta = implied - gp_v
            tolerance = max(abs(implied), abs(gp_v)) * 0.01
            checks.append({
                "check": "gross_profit_ties_to_revenue_minus_cogs",
                "expected": gp_v,
                "actual": implied,
                "delta": delta,
                "tolerance": tolerance,
                "passed": abs(delta) <= tolerance,
                "formula": "Revenue - CostOfGoodsAndServicesSold ~= GrossProfit (within 1%)",
                "inputs": [compute._input_record("Revenue", rev),
                           compute._input_record("CostOfGoodsAndServicesSold", cogs),
                           compute._input_record("GrossProfit", gp)],
            })

        passed_count = sum(1 for c in checks if c.get("passed"))
        return {
            "cik": cik, "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "period_type": period_type, "as_of": as_of,
            "checks": checks,
            "passed": passed_count,
            "total": len(checks),
        }

    def statement(self, identifier: str | int, statement: str = "income",
                  period_type: str = "annual", as_of: Optional[str] = None) -> dict:
        """Compose a normalized financial statement (income/balance/cash flow).

        Each line item is a metric envelope: `{tag, val, source_url, as_of, ...}`.
        Coverage is bounded by the canonical alias map; unknown line items
        appear with `value: null` and `missing_inputs` so agents can detect
        gaps rather than silently get a zero.
        """
        statement = statement.lower()
        # Income statement uses flow facts; balance sheet uses instants;
        # cash flow uses flow facts.
        layouts = {
            "income": {
                "kind": "flow",
                "lines": [
                    "revenue", "cogs", "gross_profit",
                    "operating_income", "interest_expense", "income_tax_expense",
                    "net_income", "eps", "diluted_eps",
                ],
            },
            "balance": {
                "kind": "instant",
                "lines": [
                    "assets_current", "inventory", "accounts_receivable", "cash",
                    "goodwill", "intangibles", "assets",
                    "accounts_payable", "deferred_revenue", "accrued_liabilities",
                    "short_term_debt", "liabilities_current",
                    "debt", "operating_lease_liabilities",
                    "deferred_tax_liabilities", "liabilities",
                    "retained_earnings", "equity",
                    "shares_outstanding",
                ],
            },
            "cash": {
                "kind": "flow",
                "lines": [
                    "net_income", "depreciation", "amortization_intangibles",
                    "stock_compensation", "operating_cash_flow",
                    "capex", "investing_cash_flow",
                    "dividends_paid", "share_repurchases", "financing_cash_flow",
                ],
            },
        }
        if statement not in layouts:
            return {"error": f"Unknown statement: {statement}; "
                             f"choose income, balance, or cash"}

        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]

        layout = layouts[statement]
        out_lines = []
        period_anchor = None
        for alias in layout["lines"]:
            if alias not in COMMON_CONCEPT_CANDIDATES:
                out_lines.append({"line": alias,
                                  "error": f"Unknown alias '{alias}'"})
                continue
            if layout["kind"] == "instant":
                fact = self._instant_fact_at_period_end(
                    cik, alias, target_end=period_anchor or "", as_of=as_of,
                )
            else:
                fact = self._latest_fact_for_alias(
                    cik, alias, period_type=period_type, as_of=as_of,
                )
                if fact and not period_anchor:
                    period_anchor = fact.get("end", "")
            if fact is None:
                out_lines.append({
                    "line": alias, "value": None, "tag": None,
                    "missing": True,
                })
                continue
            out_lines.append({
                "line": alias,
                "value": fact.get("val"),
                "tag": fact.get("tag", ""),
                "unit": fact.get("unit", ""),
                "fiscal_period": fact.get("fiscal_period", ""),
                "calendar_period": fact.get("calendar_period", ""),
                "start": fact.get("start", ""),
                "end": fact.get("end", ""),
                "source_url": fact.get("source_url", ""),
                "accession": fact.get("accession") or fact.get("accn", ""),
                "is_restated": fact.get("is_restated", False),
            })
        coverage = sum(1 for l in out_lines if l.get("value") is not None) / max(1, len(out_lines))
        return {
            "cik": cik, "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "statement": statement, "period_type": period_type,
            "period_end": period_anchor,
            "as_of": as_of,
            "lines": out_lines,
            "coverage": round(coverage, 2),
        }

    def mirror_filer(self, identifier: str | int, db_path: str,
                     include_facts: bool = True,
                     include_documents_for_form: Optional[str] = None,
                     with_bodies_for_form: Optional[str] = None,
                     bodies_limit: int = 20) -> dict:
        """Mirror one filer's submissions + companyfacts (and optionally
        per-filing document indices) into a local SQLite database.

        Subsequent runs are incremental: only new accessions are inserted.
        Returns a counts dict for what was added on this pass.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]

        submissions = self._get(f"/submissions/CIK{cik}.json")
        if "error" in submissions:
            return {"error": submissions["error"], "identifier": identifier}

        with mirror_mod.open_db(db_path) as conn:
            sub_counts = mirror_mod.ingest_submissions(conn, cik, submissions, client=self)

            facts_counts = {"facts_inserted": 0, "facts_seen": 0}
            if include_facts:
                facts_doc = self._get(f"/api/xbrl/companyfacts/CIK{cik}.json")
                if "error" not in facts_doc:
                    facts_counts = mirror_mod.ingest_companyfacts(conn, cik, facts_doc)

            body_counts = {"bodies_inserted": 0, "bodies_truncated": 0,
                            "bodies_failed": 0, "bodies_total_bytes": 0}
            if with_bodies_for_form:
                pending = mirror_mod.filings_needing_bodies(
                    conn, cik, form=with_bodies_for_form, limit=bodies_limit,
                )
                for row in pending:
                    url = row.get("primary_doc_url") or ""
                    if not url:
                        continue
                    try:
                        html = self._get_text(url)
                    except Exception:
                        body_counts["bodies_failed"] += 1
                        continue
                    text = html_to_text(html)
                    out = mirror_mod.ingest_filing_body(
                        conn, row["cik"], row["accession"], row["form"],
                        row["filed"], row["primary_doc_url"], text,
                    )
                    if out.get("inserted"):
                        body_counts["bodies_inserted"] += 1
                        body_counts["bodies_total_bytes"] += out.get("body_length", 0)
                        if out.get("truncated"):
                            body_counts["bodies_truncated"] += 1

            doc_counts = {"docs_inserted": 0}
            if include_documents_for_form:
                target = include_documents_for_form.upper()
                cur = conn.execute(
                    "SELECT accession FROM filings WHERE cik = ? AND form = ? "
                    "ORDER BY filed DESC LIMIT 5", (cik, target),
                )
                for (accn,) in cur.fetchall():
                    docs = self.filing_documents(cik, accn)
                    if "error" in docs:
                        continue
                    for doc in docs.get("documents", []):
                        try:
                            conn.execute("""
                                INSERT INTO documents(cik, accession, sequence, doc_type,
                                                      document, description, url)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (
                                cik, accn, doc.get("sequence", ""),
                                doc.get("type", ""), doc.get("document", ""),
                                doc.get("description", ""), doc.get("url", ""),
                            ))
                            doc_counts["docs_inserted"] += 1
                        except sqlite3.IntegrityError:
                            pass
                conn.commit()

            return {
                "cik": cik, "ticker": company.get("ticker", ""),
                "name": company.get("name", ""),
                "db_path": str(db_path),
                **sub_counts, **facts_counts, **doc_counts, **body_counts,
            }

    def search_mirror(self, db_path: str, query: str, form: Optional[str] = None,
                      since: Optional[str] = None,
                      ciks: Optional[list[str]] = None,
                      limit: int = 50,
                      mode: str = "auto") -> dict:
        """Full-text search a mirror SQLite database.

        `mode` selects the FTS table:
        - `"bodies"`: search ingested filing-body text (richer, depends on
          `mirror --with-bodies` having run).
        - `"metadata"`: search filing form/items/description (lightweight).
        - `"auto"` (default): bodies if any exist, otherwise metadata.
        """
        with mirror_mod.open_db(db_path) as conn:
            try:
                if mode == "auto":
                    body_count = conn.execute(
                        "SELECT COUNT(*) FROM filing_bodies"
                    ).fetchone()[0]
                    mode = "bodies" if body_count else "metadata"
                if mode == "bodies":
                    rows = mirror_mod.search_bodies(conn, query, form=form,
                                                    since=since, ciks=ciks, limit=limit)
                else:
                    rows = mirror_mod.search_filings(conn, query, form=form,
                                                     since=since, ciks=ciks, limit=limit)
            except sqlite3.OperationalError as exc:
                return {"error": f"FTS query failed: {exc}", "matches": []}
            return {"query": query, "mode": mode, "matches": rows,
                    "total": len(rows), "db_path": str(db_path)}

    def search_efts(self, query: str, form: Optional[str] = None,
                    since: Optional[str] = None,
                    until: Optional[str] = None,
                    cik: Optional[str] = None,
                    limit: int = 25) -> dict:
        """Live SEC EDGAR full-text search (efts.sec.gov/LATEST/search-index).

        Lighter than mirroring + FTS5 but capped at SEC's own response size and
        rate. Use the local mirror path for serious corpus work.
        """
        params: dict[str, Any] = {"q": query, "hits": min(limit, 100)}
        if form:
            params["forms"] = form
        if since:
            params["dateRange"] = "custom"
            params["startdt"] = since
            params["enddt"] = until or time.strftime("%Y-%m-%d")
        elif until:
            params["dateRange"] = "custom"
            params["startdt"] = "2001-01-01"
            params["enddt"] = until
        if cik:
            params["ciks"] = cik
        url = "https://efts.sec.gov/LATEST/search-index"
        result = self._get(url, params=params)
        if "error" in result:
            return result
        hits = result.get("hits", {}).get("hits", [])
        matches = []
        for hit in hits[:limit]:
            src = hit.get("_source", {}) or {}
            display_names = src.get("display_names") or []
            adsh = src.get("adsh", "")
            ciks = src.get("ciks") or []
            primary_cik = ciks[0] if ciks else ""
            matches.append({
                "accession": adsh,
                "filed": src.get("file_date", ""),
                "form": src.get("form", ""),
                "ciks": ciks,
                "primary_cik": primary_cik,
                "display_names": display_names,
                "highlight": (hit.get("highlight") or {}).get("text") or [],
                "score": hit.get("_score"),
            })
        return {"query": query, "matches": matches,
                "total_available": result.get("hits", {}).get("total", {}).get("value"),
                "returned": len(matches)}

    def resolve(self, identifiers: list[str]) -> dict:
        """Batch resolve identifiers to CIKs, with ambiguity metadata per row."""
        out = []
        for ident in identifiers:
            if str(ident).startswith("@"):
                expanded = self.expand_group(ident)
                if "error" in expanded:
                    out.append({"identifier": ident, "error": expanded["error"]})
                    continue
                for sub in expanded.get("identifiers", []):
                    out.append(self._resolve_one(sub))
                continue
            out.append(self._resolve_one(ident))
        return {"results": out, "total": len(out)}

    def _resolve_one(self, identifier: str) -> dict:
        company = self.resolve_company(identifier)
        if "error" in company:
            row = {"identifier": identifier, "error": company["error"]}
            # Surface ambiguity options when the resolver hit multiple matches.
            if "matches" in company.get("error", "").lower() or "ambiguous" in company.get("error", "").lower():
                search = self.search_companies(str(identifier), limit=10)
                if "error" not in search:
                    row["candidates"] = [
                        {"ticker": c.get("ticker", ""), "cik": c.get("cik", ""),
                         "name": c.get("name", ""), "exchange": c.get("exchange", "")}
                        for c in search.get("companies", [])
                    ]
            return row
        return {
            "identifier": identifier,
            "cik": company.get("cik", ""),
            "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "exchange": company.get("exchange", ""),
        }

    def explain_concept(self, identifier: str | int, concept: str,
                        unit: Optional[str] = None,
                        period_type: Optional[str] = None) -> dict:
        """Trace concept-alias resolution: candidates tried, freshness scores, winner."""
        candidates = concept_alias_candidates(concept, unit=unit)
        trials = []
        for taxonomy, tag, resolved_unit in candidates:
            r = self.company_concept(
                identifier, taxonomy, tag, unit=resolved_unit, limit=4,
                suggest_on_404=False, period_type=period_type,
            )
            if "error" in r:
                trials.append({"taxonomy": taxonomy, "tag": tag, "unit": resolved_unit,
                               "error": r["error"], "fact_count": 0})
                continue
            facts = r.get("facts", [])
            latest = latest_distinct_fact(facts) or {}
            trials.append({
                "taxonomy": taxonomy, "tag": tag, "unit": resolved_unit,
                "fact_count": len(facts),
                "latest_filed": latest.get("filed", ""),
                "latest_end": latest.get("end", ""),
                "latest_val": latest.get("val", ""),
                "freshness_key": str(fact_sort_key(latest)),
            })
        valid = [t for t in trials if t.get("fact_count", 0) > 0]
        winner = max(valid, key=lambda t: t.get("freshness_key", "")) if valid else None
        return {
            "alias": concept,
            "is_alias": concept_alias_key(concept) in COMMON_CONCEPT_CANDIDATES,
            "candidates": trials,
            "winner": winner,
        }

    def diff_concept(self, identifier_a: str, identifier_b: str, concept: str,
                     period_type: str = "annual", periods: int = 4,
                     as_of: Optional[str] = None) -> dict:
        """Side-by-side diff of one concept across two filers."""
        a = self.compare_concept([identifier_a, identifier_b], concept,
                                 periods=periods, period_type=period_type, as_of=as_of)
        if "error" in a:
            return a
        # Align on `frame` / `calendar_period` first so different fiscal calendars
        # still pair up (AAPL FY ends Sep, MSFT FY ends Jun, both map to CY2025).
        # Fall back to `(start, end)` only when neither side has a frame.
        grid: dict[str, dict] = {}
        for i, side in enumerate(("a", "b")):
            company = (a.get("companies") or [None, None])[i] or {}
            for fact in company.get("facts", []):
                frame = fact.get("frame") or fact.get("calendar_period") or ""
                key = frame or f"se:{fact.get('start','')}|{fact.get('end','')}"
                entry = grid.setdefault(key, {"a": None, "b": None, "frame": frame,
                                               "a_period": None, "b_period": None})
                entry[side] = fact
                entry[f"{side}_period"] = (fact.get("start", ""), fact.get("end", ""))
                if not entry["frame"] and frame:
                    entry["frame"] = frame
        rows = []
        for key, entry in sorted(
            grid.items(),
            key=lambda kv: (kv[1].get("a_period") or kv[1].get("b_period") or ("", ""))[1] or "",
            reverse=True,
        ):
            va = compute._value_or_none(entry.get("a")) if entry.get("a") else None
            vb = compute._value_or_none(entry.get("b")) if entry.get("b") else None
            row = {
                "frame": entry.get("frame", ""),
                "a_period": entry.get("a_period"),
                "b_period": entry.get("b_period"),
                "a_value": va,
                "b_value": vb,
                "delta": (va - vb) if (va is not None and vb is not None) else None,
                "ratio": (va / vb) if (va is not None and vb not in (None, 0)) else None,
            }
            # Keep legacy `start`/`end` keys for callers that rely on them — pick
            # whichever side actually had a fact.
            period = entry.get("a_period") or entry.get("b_period") or ("", "")
            row["start"] = period[0]
            row["end"] = period[1]
            rows.append(row)
        return {
            "concept": concept,
            "period_type": period_type,
            "as_of": as_of,
            "a": {"identifier": identifier_a, "name": (a.get("companies") or [{}])[0].get("name", "")},
            "b": {"identifier": identifier_b, "name": (a.get("companies") or [{}, {}])[1].get("name", "") if len(a.get("companies", [])) > 1 else ""},
            "rows": rows,
        }

    def audit_trail(self, identifier: str | int, concept: str, period: Optional[str] = None,
                    period_start: Optional[str] = None, period_end: Optional[str] = None) -> dict:
        """Return every filing that reported a given concept value, in chrono order.

        Surfaces restatements: if a fact's `(start, end)` matches across filings
        but `val` differs, the value was restated.
        """
        result = self.company_concept_alias(identifier, concept, limit=10000,
                                            canonical_union=True)
        if "error" in result:
            return result
        facts = result.get("facts", [])
        if period:
            facts = [f for f in facts if (f.get("frame") or f.get("calendar_period")) == period]
        if period_start:
            facts = [f for f in facts if f.get("start", "") == period_start]
        if period_end:
            facts = [f for f in facts if f.get("end", "") == period_end]
        ordered = sorted(facts, key=lambda f: (f.get("filed", ""), f.get("end", "")))
        # Detect restatements: same (start, end) reported with different val.
        seen_periods: dict[tuple, list] = {}
        for fact in ordered:
            key = (fact.get("start", ""), fact.get("end", ""))
            seen_periods.setdefault(key, []).append(fact)
        restated_periods = []
        for key, ents in seen_periods.items():
            vals = {f.get("val") for f in ents}
            if len(vals) > 1:
                restated_periods.append({
                    "start": key[0],
                    "end": key[1],
                    "values_seen": sorted(vals, key=lambda v: str(v)),
                    "filings": [{"filed": f.get("filed"), "val": f.get("val"),
                                 "form": f.get("form"), "accn": f.get("accn"),
                                 "tag": f.get("source_tag") or f.get("tag", "")}
                                for f in ents],
                })
        return {
            "cik": result.get("cik", ""),
            "name": result.get("name", ""),
            "alias": concept,
            "period_filter": {"frame": period, "start": period_start, "end": period_end},
            "facts": ordered,
            "total": len(ordered),
            "restated_periods": restated_periods,
        }

    def amendments(self, identifier: str | int, since: Optional[str] = None,
                   limit: int = 50) -> dict:
        """Pair primary filings with their `/A` amendments.

        For each `FORM/A` in the recent set, find the most recent prior `FORM`
        from the same filer. The amendment's primary is whichever original
        filing it amends (matched by form + form-type heuristic).
        """
        result = self.submissions(identifier, limit=400, start_date=since)
        if "error" in result:
            return result
        filings = result.get("filings", [])
        primaries: dict[str, list] = {}
        amendments: list[dict] = []
        for f in filings:
            form = f.get("form", "").upper()
            if form.endswith("/A"):
                amendments.append(f)
            else:
                primaries.setdefault(form, []).append(f)

        chains = []
        for amd in amendments:
            base_form = amd.get("form", "").upper().rstrip("/A").rstrip()
            primary_pool = primaries.get(base_form, [])
            best = None
            for cand in primary_pool:
                if cand.get("filingDate", "") < amd.get("filingDate", ""):
                    if not best or cand.get("filingDate", "") > best.get("filingDate", ""):
                        best = cand
            chains.append({
                "amendment": {
                    "filingDate": amd.get("filingDate"),
                    "form": amd.get("form"),
                    "accessionNumber": amd.get("accessionNumber"),
                    "filing_url": amd.get("filing_url"),
                },
                "primary": {
                    "filingDate": best.get("filingDate"),
                    "form": best.get("form"),
                    "accessionNumber": best.get("accessionNumber"),
                    "filing_url": best.get("filing_url"),
                } if best else None,
            })
        chains = chains[:limit]
        return {
            "cik": result.get("cik", ""),
            "ticker": result.get("ticker", ""),
            "name": result.get("name", ""),
            "since": since,
            "chains": chains,
            "total": len(chains),
        }

    def delta(self, identifier: str | int, since: Optional[str] = None,
              state_store: Optional["StateStore"] = None) -> dict:
        """Return new filings and (where detectable) restated facts since a date.

        `since` controls the starting date. If `state_store` is provided and
        `since` is empty, the state store's high-water mark is used and updated.
        """
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]
        new_filings_result = self.submissions(
            identifier, limit=200, start_date=since,
            since_last_fetch=bool(state_store and not since),
            state_store=state_store,
        )
        new_filings = new_filings_result.get("filings", [])
        # Restatements: recompute audit-trail for revenue and assets as a probe set.
        restated_facts = []
        for probe in ("revenue", "net_income", "assets"):
            trail = self.audit_trail(identifier, probe)
            if "error" in trail:
                continue
            for entry in trail.get("restated_periods", []):
                restated_facts.append({"metric": probe, **entry})
        return {
            "cik": cik,
            "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "since": since,
            "new_filings": new_filings,
            "restated_facts": restated_facts,
            "summary": {
                "new_filings": len(new_filings),
                "restated_periods": len(restated_facts),
            },
        }

    def _latest_fact_for_alias(self, identifier: str | int, alias: str,
                               period_type: Optional[str] = None,
                               as_of: Optional[str] = None) -> Optional[dict]:
        """Find the freshest fact across all candidate tags for an alias."""
        result = self.company_concept_alias(
            identifier, alias, limit=12, period_type=period_type, as_of=as_of,
        )
        if "error" in result:
            return None
        facts = result.get("facts", [])
        if not facts:
            return None
        fact = latest_distinct_fact(facts)
        if fact is not None:
            fact["tag"] = result.get("tag", "")
        return fact

    def _facts_for_alias(self, identifier: str | int, alias: str,
                         period_type: Optional[str] = None,
                         as_of: Optional[str] = None,
                         limit: int = 24) -> list[dict]:
        result = self.company_concept_alias(
            identifier, alias, limit=limit, period_type=period_type, as_of=as_of,
        )
        if "error" in result:
            return []
        facts = result.get("facts", [])
        for f in facts:
            f.setdefault("tag", result.get("tag", ""))
        return facts

    def _instant_fact_at_period_end(self, identifier: str | int, alias: str,
                                    target_end: str,
                                    as_of: Optional[str] = None,
                                    tolerance_days: int = 14) -> Optional[dict]:
        """Pick the instant fact whose `end` is closest to `target_end`.

        Used by ratios to keep balance-sheet snapshots aligned with the period
        end of the flow (income/cash-flow) facts. Without alignment, ROA/ROE
        can mix FY revenue with a later quarter's assets.
        """
        if not target_end:
            return self._latest_fact_for_alias(
                identifier, alias, period_type="instant", as_of=as_of,
            )
        from datetime import datetime
        try:
            target = datetime.strptime(target_end, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return self._latest_fact_for_alias(
                identifier, alias, period_type="instant", as_of=as_of,
            )
        result = self.company_concept_alias(
            identifier, alias, limit=40, period_type="instant", as_of=as_of,
        )
        if "error" in result:
            return None
        best = None
        best_delta = None
        for fact in result.get("facts", []):
            try:
                end = datetime.strptime(fact.get("end", ""), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            delta = abs((end - target).days)
            # Strongly prefer facts at or before the target; same-day is ideal.
            penalty = 0 if end <= target else delta * 2
            score = delta + penalty
            if best is None or score < best_delta:
                best = fact
                best_delta = score
        if best is not None:
            best.setdefault("tag", result.get("tag", ""))
        # Don't return a wildly mismatched fact (>1 quarter off) without flagging.
        if best is not None and best_delta is not None and best_delta > tolerance_days:
            best = dict(best)
            best["_period_alignment_warning"] = (
                f"Closest instant fact ends {best.get('end')}, "
                f"flow period ends {target_end} (off by {best_delta} days)."
            )
        return best

    def ttm(self, identifier: str | int, bundle: Optional[list[str]] = None,
            as_of: Optional[str] = None) -> dict:
        """Compute trailing-twelve-months for a bundle of canonical metrics.

        Strategy:
        1. If 4 contiguous quarterly facts exist, sum them.
        2. Otherwise, reconstruct via stub period: AnnualFY + CurrentYTD - PriorYearYTD.
        Filers that only tag Q4 inside the annual 10-K (Apple, Microsoft, NVIDIA)
        require path 2.
        """
        bundle = bundle or ["revenue", "net_income", "operating_income", "operating_cash_flow"]
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        out = {"cik": company["cik"], "ticker": company.get("ticker", ""),
               "name": company.get("name", ""), "as_of": as_of, "metrics": []}
        for label in bundle:
            if concept_alias_key(label) not in COMMON_CONCEPT_CANDIDATES:
                out["metrics"].append({"metric": label, "error": f"Unknown alias '{label}'"})
                continue
            quarter_facts = self._facts_for_alias(
                company["cik"], label, period_type="quarterly", as_of=as_of, limit=12,
            )
            envelope = compute.ttm_from_quarters(quarter_facts)
            if envelope.get("value") is None:
                # Try stub-period reconstruction.
                stub = self._ttm_stub_period(company["cik"], label, as_of=as_of)
                if stub is not None:
                    envelope = stub
            envelope["metric"] = label
            out["metrics"].append(envelope)
        return out

    def _ttm_stub_period(self, cik: str, alias: str, as_of: Optional[str]) -> Optional[dict]:
        """Build TTM from FY + current_YTD - prior_year_same_YTD."""
        annual_facts = self._facts_for_alias(cik, alias, period_type="annual",
                                             as_of=as_of, limit=4)
        ytd_facts = self._facts_for_alias(cik, alias, period_type="ytd",
                                          as_of=as_of, limit=20)
        if not annual_facts or not ytd_facts:
            return None
        # Latest annual fact (highest end).
        annual = max(annual_facts, key=lambda f: f.get("end", ""))
        annual_end = annual.get("end", "")
        if not annual_end:
            return None
        # Current YTD: most recent YTD whose end is AFTER the latest FY end.
        future_ytd = [f for f in ytd_facts if f.get("end", "") > annual_end]
        if not future_ytd:
            # No interim filings since FY end — TTM equals the most recent FY.
            return {
                "metric": "ttm",
                "value": compute._value_or_none(annual),
                "formula": "AnnualFY (no interim filings since FY end; TTM = latest FY)",
                "inputs": [compute._input_record("AnnualFY", annual)],
                "caveats": ["TTM resolves to the latest annual fact because no interim YTD filings have been made since."],
                "period_end": annual_end,
            }
        current_ytd = max(future_ytd, key=lambda f: f.get("end", ""))
        # Period length of the current YTD (e.g. 3, 6, 9 months).
        try:
            from datetime import datetime
            cur_start = datetime.strptime(current_ytd.get("start", ""), "%Y-%m-%d").date()
            cur_end = datetime.strptime(current_ytd.get("end", ""), "%Y-%m-%d").date()
            cur_length = (cur_end - cur_start).days
        except (ValueError, TypeError):
            return None
        # Prior-year matching YTD: same approximate length, end date roughly 365 days earlier.
        prior_ytd = None
        for f in ytd_facts:
            try:
                f_start = datetime.strptime(f.get("start", ""), "%Y-%m-%d").date()
                f_end = datetime.strptime(f.get("end", ""), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            length = (f_end - f_start).days
            year_offset = (cur_end - f_end).days
            if abs(length - cur_length) <= 7 and 350 <= year_offset <= 380:
                prior_ytd = f
                break
        if prior_ytd is None:
            return None
        return compute.ttm_from_stub_period(annual, current_ytd, prior_ytd)

    def ratios(self, identifier: str | int, period_type: str = "annual",
               as_of: Optional[str] = None) -> dict:
        """Compute the canonical ratio set for one company at one period kind."""
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]

        flow_pt = period_type if period_type in ("annual", "quarterly", "ytd") else "annual"
        instant_pt = "instant"

        revenue = self._latest_fact_for_alias(cik, "revenue", period_type=flow_pt, as_of=as_of)
        net_income = self._latest_fact_for_alias(cik, "net_income", period_type=flow_pt, as_of=as_of)
        operating_income = self._latest_fact_for_alias(cik, "operating_income", period_type=flow_pt, as_of=as_of)
        cogs = self._latest_fact_for_alias(cik, "cogs", period_type=flow_pt, as_of=as_of)
        gross_profit = self._latest_fact_for_alias(cik, "gross_profit", period_type=flow_pt, as_of=as_of)
        dna = self._latest_fact_for_alias(cik, "dna", period_type=flow_pt, as_of=as_of)
        ocf = self._latest_fact_for_alias(cik, "operating_cash_flow", period_type=flow_pt, as_of=as_of)
        capex = self._latest_fact_for_alias(cik, "capex", period_type=flow_pt, as_of=as_of)

        # Align balance-sheet (instant) facts to the period end of the flow
        # facts so ROE/ROA/turnover do not silently mix FY revenue with a later
        # quarter's balance sheet.
        flow_anchor = (revenue or net_income or operating_income or {}).get("end", "")
        anchor = lambda alias_name: self._instant_fact_at_period_end(
            cik, alias_name, target_end=flow_anchor, as_of=as_of,
        )
        assets = anchor("assets")
        equity = anchor("equity")
        cash = anchor("cash")
        debt = anchor("debt")
        std = anchor("short_term_debt")
        ac = anchor("assets_current")
        lc = anchor("liabilities_current")
        inv = anchor("inventory")

        shares_outstanding = anchor("shares_outstanding")
        ratios = [
            compute.gross_margin(revenue, cogs, gross_profit),
            compute.operating_margin(revenue, operating_income),
            compute.net_margin(revenue, net_income),
            compute.ebitda_margin(revenue, operating_income, dna),
            compute.roe(net_income, equity),
            compute.roa(net_income, assets),
            compute.asset_turnover(revenue, assets),
            compute.current_ratio(ac, lc),
            compute.quick_ratio(ac, inv, lc),
            compute.debt_to_equity(debt, std, equity),
            compute.debt_to_ebitda(debt, std, operating_income, dna),
            compute.net_debt(debt, std, cash),
            compute.free_cash_flow(ocf, capex),
            compute.fcf_margin(revenue, ocf, capex),
            compute.book_value_per_share(equity, shares_outstanding),
            compute.fcf_per_share(ocf, capex, shares_outstanding),
            compute.sales_per_share(revenue, shares_outstanding),
        ]
        not_applicable = [
            {"metric": "pe_ratio", "reason": "requires market price (out of scope without price feed)"},
            {"metric": "ev_ebitda", "reason": "requires market price (out of scope without price feed)"},
            {"metric": "dividend_yield", "reason": "requires market price (out of scope without price feed)"},
        ]
        # Surface any per-instant alignment warnings so agents can detect when
        # the balance sheet was not perfectly aligned with the flow period end.
        alignment_warnings = []
        for label, fact in [("assets", assets), ("equity", equity), ("cash", cash),
                            ("debt", debt), ("short_term_debt", std),
                            ("assets_current", ac), ("liabilities_current", lc),
                            ("inventory", inv)]:
            if fact and fact.get("_period_alignment_warning"):
                alignment_warnings.append({"input": label,
                                            "warning": fact["_period_alignment_warning"]})
        return {
            "cik": cik,
            "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "period_type": flow_pt,
            "flow_period_end": flow_anchor or None,
            "as_of": as_of,
            "ratios": ratios,
            "not_applicable": not_applicable,
            "alignment_warnings": alignment_warnings,
        }

    def trend(self, identifier: str | int, metric: str, periods: int = 8,
              period_type: str = "quarterly", as_of: Optional[str] = None) -> dict:
        """Multi-period trend on a metric: facts list + slope + categorical label."""
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        if concept_alias_key(metric) not in COMMON_CONCEPT_CANDIDATES:
            return {"error": f"Unknown metric alias '{metric}'"}
        facts = self._facts_for_alias(
            company["cik"], metric, period_type=period_type, as_of=as_of, limit=periods,
        )
        facts = facts[:periods]
        summary = compute.trend_summary(facts)
        return {
            "cik": company["cik"],
            "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "metric": metric,
            "period_type": period_type,
            "as_of": as_of,
            "facts": facts,
            "summary": summary,
        }

    def growth(self, identifier: str | int, metric: str,
               basis: Optional[list[str]] = None, period_type: str = "annual",
               periods: int = 8, as_of: Optional[str] = None) -> dict:
        """Compute multi-basis growth: yoy, qoq, cagr3, cagr5."""
        basis = basis or ["yoy"]
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        if concept_alias_key(metric) not in COMMON_CONCEPT_CANDIDATES:
            return {"error": f"Unknown metric alias '{metric}'"}
        facts = self._facts_for_alias(
            company["cik"], metric, period_type=period_type, as_of=as_of, limit=periods,
        )
        sorted_facts = sorted(facts, key=lambda f: f.get("end", ""))
        values = [v for v in (compute._value_or_none(f) for f in sorted_facts) if v is not None]

        out = {"cik": company["cik"], "ticker": company.get("ticker", ""),
               "name": company.get("name", ""), "metric": metric, "period_type": period_type,
               "as_of": as_of, "facts": sorted_facts, "growth": []}

        per_period = compute.growth_rates(sorted_facts)
        for entry in basis:
            entry = entry.strip().lower()
            if entry == "yoy" or entry == "qoq":
                out["growth"].append({
                    "basis": entry,
                    "rates": per_period,
                    "latest": per_period[-1]["growth"] if per_period else None,
                })
            elif entry.startswith("cagr"):
                try:
                    yrs = int(entry[4:])
                except ValueError:
                    continue
                ppy = 4 if period_type == "quarterly" else 1
                window = values[-(yrs * ppy + 1):] if len(values) >= yrs * ppy + 1 else values
                out["growth"].append({
                    "basis": entry,
                    "value": compute.cagr(window, periods_per_year=ppy),
                    "window_size": len(window),
                })
        return out

    def reconstruct(self, identifier: str | int, target: str, period_type: str = "annual",
                    as_of: Optional[str] = None) -> dict:
        """Reconstruct a derived line item not directly tagged by SEC."""
        company = self.resolve_company(identifier)
        if "error" in company:
            return company
        cik = company["cik"]
        target = target.lower()
        flow_pt = period_type if period_type in ("annual", "quarterly", "ytd") else "annual"
        instant_pt = "instant"

        if target == "ebitda":
            envelope = compute.ebitda(
                self._latest_fact_for_alias(cik, "operating_income", period_type=flow_pt, as_of=as_of),
                self._latest_fact_for_alias(cik, "dna", period_type=flow_pt, as_of=as_of),
            )
        elif target == "fcf":
            envelope = compute.free_cash_flow(
                self._latest_fact_for_alias(cik, "operating_cash_flow", period_type=flow_pt, as_of=as_of),
                self._latest_fact_for_alias(cik, "capex", period_type=flow_pt, as_of=as_of),
            )
        elif target == "net_debt":
            envelope = compute.net_debt(
                self._latest_fact_for_alias(cik, "debt", period_type=instant_pt, as_of=as_of),
                self._latest_fact_for_alias(cik, "short_term_debt", period_type=instant_pt, as_of=as_of),
                self._latest_fact_for_alias(cik, "cash", period_type=instant_pt, as_of=as_of),
            )
        elif target in ("nwc", "working_capital"):
            envelope = compute.working_capital(
                self._latest_fact_for_alias(cik, "assets_current", period_type=instant_pt, as_of=as_of),
                self._latest_fact_for_alias(cik, "liabilities_current", period_type=instant_pt, as_of=as_of),
            )
        elif target in ("tangible_book", "tangible_book_value"):
            envelope = compute.tangible_book_value(
                self._latest_fact_for_alias(cik, "equity", period_type=instant_pt, as_of=as_of),
                None,  # goodwill alias not in COMMON_CONCEPT_CANDIDATES yet
                None,
            )
        else:
            return {"error": f"Unknown reconstruct target: {target}"}

        return {
            "cik": cik,
            "ticker": company.get("ticker", ""),
            "name": company.get("name", ""),
            "target": target,
            "period_type": flow_pt,
            "as_of": as_of,
            **envelope,
        }

    def compare_concept(self, identifiers: list[str], concept: str, taxonomy: Optional[str] = None,
                        unit: Optional[str] = None, periods: int = 4,
                        period_type: Optional[str] = None,
                        as_of: Optional[str] = None) -> dict:
        """Compare a concept across companies."""
        candidates = concept_alias_candidates(concept, taxonomy, unit)
        companies = []
        for identifier in identifiers:
            company = self._company_candidate_facts(
                identifier, candidates, periods=max(periods * 12, 36),
                period_type=period_type, as_of=as_of,
            )
            if company.get("error"):
                companies.append(company)
                continue

            frame_facts = [fact for fact in company.get("candidate_facts", []) if fact.get("frame")]
            company["candidate_facts"] = frame_facts or company.get("candidate_facts", [])
            companies.append(company)

        comparable = [company for company in companies if not company.get("error")]
        warnings = []
        shared_frames = []
        if comparable:
            frame_sets = [
                {fact.get("frame") for fact in company.get("candidate_facts", []) if fact.get("frame")}
                for company in comparable
            ]
            shared = set.intersection(*frame_sets) if frame_sets and all(frame_sets) else set()
            sorted_shared = sorted(shared, key=lambda frame: _frame_sort_key(comparable, frame), reverse=True)
            preferred_kind = _frame_period_kind(comparable, sorted_shared[0]) if sorted_shared else ""
            shared_frames = [
                frame for frame in sorted_shared
                if not preferred_kind or _frame_period_kind(comparable, frame) == preferred_kind
            ][:periods]
            if preferred_kind:
                warnings.append(f"Aligned on {preferred_kind} frames.")

        if shared_frames:
            for company in comparable:
                company["facts"] = [
                    _best_fact_for_frame(company.get("candidate_facts", []), frame)
                    for frame in shared_frames
                ]
                company["facts"] = [fact for fact in company["facts"] if fact]
            if len(shared_frames) < periods:
                warnings.append(f"Only {len(shared_frames)} shared frame(s) were available across all companies.")
        else:
            if len(comparable) > 1:
                warnings.append("No shared frames were available across all companies; periods may not align.")
            for company in comparable:
                company["facts"] = pick_comparable_facts(company.get("candidate_facts", []), periods)

        for company in companies:
            company.pop("candidate_facts", None)

        first = next((company for company in comparable if company.get("facts")), {})
        return {
            "concept": concept,
            "taxonomy": first.get("taxonomy", candidates[0][0]),
            "tag": first.get("tag", candidates[0][1]),
            "unit": first.get("unit", candidates[0][2]),
            "frames": shared_frames,
            "warnings": warnings,
            "period_alignment_warning": " ".join(warnings),
            "companies": companies,
        }

    def metrics(self, identifier: str | int, bundle: Optional[list[str]] = None) -> dict:
        """Return a bundle of canonical metrics for one company."""
        profile = self.submissions(identifier, limit=5)
        if "error" in profile:
            return profile

        reference_date = latest_filing_date(profile)
        metrics = []
        for label in bundle or DEFAULT_METRIC_BUNDLE:
            if concept_alias_key(label) not in COMMON_CONCEPT_CANDIDATES:
                metrics.append({
                    "metric": label,
                    "error": f"Unknown metric alias '{label}'",
                    "known_aliases": sorted(COMMON_CONCEPT_CANDIDATES),
                })
                continue
            metric = self._best_metric(label, profile["cik"], reference_date)
            if metric:
                metrics.append(metric)
            else:
                metrics.append({"metric": label, "error": "No facts found"})

        return {
            "cik": profile.get("cik", ""),
            "ticker": profile.get("ticker", ""),
            "name": profile.get("name", ""),
            "reference_date": reference_date,
            "metrics": metrics,
        }

    def brief(self, identifier: str | int) -> dict:
        """Build a compact company brief."""
        profile = self.submissions(identifier, limit=5)
        if "error" in profile:
            return profile

        reference_date = latest_filing_date(profile)
        metrics = []
        for label in BRIEF_METRICS:
            metric = self._best_metric(label, profile["cik"], reference_date)
            if not metric:
                continue
            metrics.append(metric)

        earnings = self.latest_earnings(profile["cik"], limit=8)
        events = self.events(profile["cik"], limit=8)
        return {
            "profile": profile,
            "metrics": metrics,
            "earnings": earnings if "error" not in earnings else None,
            "events": events.get("events", [])[:5] if "error" not in events else [],
        }

    def _company_candidate_facts(self, identifier: str | int, candidates: list[tuple[str, str, Optional[str]]],
                                 periods: int, period_type: Optional[str] = None,
                                 as_of: Optional[str] = None) -> dict:
        errors = []
        candidate_facts = []
        metadata = {}
        for taxonomy, tag, unit in candidates:
            result = self.company_concept(
                identifier, taxonomy, tag, unit=unit, limit=periods,
                suggest_on_404=False, period_type=period_type, as_of=as_of,
            )
            if "error" in result:
                errors.append(result["error"])
                continue
            metadata = {
                "identifier": identifier,
                "cik": result.get("cik", ""),
                "name": result.get("name", identifier),
            }
            for fact in result.get("facts", []):
                item = dict(fact)
                item["_taxonomy"] = result.get("taxonomy", taxonomy)
                item["_tag"] = result.get("tag", tag)
                item["_unit"] = unit or item.get("unit", "")
                candidate_facts.append(item)

        if not candidate_facts:
            return {
                "identifier": identifier,
                "error": errors[0] if errors else "No facts found",
                "facts": [],
            }

        candidate_facts.sort(key=lambda fact: (fact.get("end", ""), fact.get("filed", "")), reverse=True)
        best = candidate_facts[0]
        return {
            **metadata,
            "taxonomy": best.get("_taxonomy", candidates[0][0]),
            "tag": best.get("_tag", candidates[0][1]),
            "unit": best.get("_unit", candidates[0][2]),
            "candidate_facts": candidate_facts,
            "facts": [],
        }

    def _best_metric(self, label: str, cik: str, reference_date: str) -> Optional[dict]:
        best = None
        for taxonomy, tag, unit in concept_alias_candidates(label):
            result = self.company_concept(cik, taxonomy, tag, unit=unit, limit=24, suggest_on_404=False)
            if "error" in result:
                continue
            fact = latest_distinct_fact(result.get("facts", []))
            if not fact:
                continue
            candidate = {
                "metric": label,
                "taxonomy": result.get("taxonomy", taxonomy),
                "tag": result.get("tag", tag),
                "unit": unit or fact.get("unit", ""),
                "fact": fact,
            }
            if not best or fact_sort_key(candidate["fact"]) > fact_sort_key(best["fact"]):
                best = candidate

        if best:
            age_days = fact_age_days(best["fact"], reference_date)
            best["age_days"] = age_days
            best["stale"] = bool(age_days is not None and age_days > STALE_METRIC_DAYS)
        return best

    @staticmethod
    def _recent_filings(data: dict, limit: int = 20, form: Optional[str] = None,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None,
                        form_class: Optional[str] = None) -> list[dict]:
        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        cik = normalize_cik(data.get("cik", "0"))
        rows = []
        wanted_form = form.upper() if form else None
        wanted_class = FORM_CLASSES.get(form_class) if form_class else None

        for index, accession in enumerate(accessions):
            row = {key: values[index] for key, values in recent.items() if index < len(values)}
            row_form = row.get("form", "").upper()
            if wanted_form and row_form != wanted_form:
                continue
            if wanted_class and row_form not in wanted_class:
                continue
            filing_date = row.get("filingDate", "")
            if start_date and filing_date < start_date:
                continue
            if end_date and filing_date > end_date:
                continue
            row.update(filing_urls(cik, accession, row.get("primaryDocument", "")))
            rows.append(row)
            if len(rows) >= limit:
                break

        return rows

    @staticmethod
    def _oldest_recent_filing_date(data: dict) -> str:
        dates = [
            filing_date
            for filing_date in data.get("filings", {}).get("recent", {}).get("filingDate", [])
            if filing_date
        ]
        return min(dates) if dates else ""

    @staticmethod
    def _summarize_concepts(facts: dict, taxonomy: Optional[str],
                            tag_filter: Optional[str]) -> list[dict]:
        concepts = []
        tag_needle = tag_filter.lower() if tag_filter else None

        for taxonomy_name, tags in facts.items():
            if taxonomy and taxonomy_name != taxonomy:
                continue
            for tag, concept in tags.items():
                if tag_needle:
                    haystack = " ".join([
                        tag,
                        str(concept.get("label") or ""),
                        str(concept.get("description") or ""),
                    ]).lower()
                    if tag_needle not in haystack:
                        continue
                units = concept.get("units", {})
                fact_count = sum(len(rows) for rows in units.values())
                latest_filed = ""
                for rows in units.values():
                    for row in rows:
                        latest_filed = max(latest_filed, row.get("filed", ""))
                concepts.append({
                    "taxonomy": taxonomy_name,
                    "tag": tag,
                    "label": concept.get("label", ""),
                    "description": concept.get("description", ""),
                    "units": ", ".join(units.keys()),
                    "fact_count": fact_count,
                    "latest_filed": latest_filed,
                })

        concepts.sort(key=lambda x: (x["latest_filed"], x["fact_count"]), reverse=True)
        return concepts


def _number_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def enrich_fact_metadata(fact: dict, cik: str | int) -> dict:
    """Add provenance and normalized period metadata to a fact row."""
    accession = fact.get("accn") or fact.get("accession") or ""
    if accession:
        fact["accession"] = accession
        if not fact.get("source_url"):
            try:
                fact["source_url"] = filing_urls(cik, accession, "").get("filing_url", "")
            except ValueError:
                fact["source_url"] = ""
    else:
        fact.setdefault("source_url", "")
    fact["as_of"] = fact.get("end") or fact.get("filed") or ""

    start = parse_date(fact.get("start", ""))
    end = parse_date(fact.get("end", ""))
    if end and not start:
        period_length_days = 0
        period_type = "instant"
    elif start and end:
        period_length_days = (end - start).days
        period_type = period_type_from_days(period_length_days)
    else:
        period_length_days = None
        period_type = ""

    fact["period_type"] = period_type
    fact["period_length_days"] = period_length_days
    fact["fiscal_period"] = fiscal_period_label(fact)
    fact["calendar_period"] = fact.get("frame") or calendar_period_label(fact)
    fact["is_restated"] = False
    fact["is_cumulative"] = is_cumulative_fact(fact)
    fact["superseded_by"] = None
    return fact


def period_type_from_days(days: int) -> str:
    if 300 <= days <= 380:
        return "annual"
    if 70 <= days <= 110:
        return "quarterly"
    if 110 < days < 300:
        return "ytd"
    return "other"


def fiscal_period_label(fact: dict) -> str:
    fy = fact.get("fy")
    fp = str(fact.get("fp") or "").upper()
    if not fy:
        return ""
    if fp.startswith("Q"):
        return f"{fp}-FY{fy}"
    if fp == "FY":
        return f"FY{fy}"
    return f"{fp}-FY{fy}" if fp else f"FY{fy}"


def calendar_period_label(fact: dict) -> str:
    end = parse_date(fact.get("end", ""))
    if not end:
        return ""
    period_type = fact.get("period_type")
    if period_type == "annual":
        return f"CY{end.year}"
    if period_type == "quarterly":
        quarter = (end.month - 1) // 3 + 1
        return f"CY{end.year}Q{quarter}"
    if period_type == "instant":
        quarter = (end.month - 1) // 3 + 1
        return f"CY{end.year}Q{quarter}I"
    return ""


def is_cumulative_fact(fact: dict) -> bool:
    fp = str(fact.get("fp") or "").upper()
    days = fact.get("period_length_days")
    return bool(fp in {"Q2", "Q3"} and days and days > 110)


def parse_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def latest_filing_date(profile: dict) -> str:
    dates = [
        filing.get("filingDate", "")
        for filing in profile.get("filings", [])
        if filing.get("filingDate")
    ]
    return max(dates) if dates else ""


def fact_sort_key(fact: dict) -> tuple[str, str]:
    return fact.get("end", ""), fact.get("filed", "")


def fact_age_days(fact: dict, reference_date: str) -> Optional[int]:
    end = parse_date(fact.get("end", ""))
    reference = parse_date(reference_date)
    if not end or not reference:
        return None
    return (reference - end).days


def _frame_sort_key(companies: list[dict], frame: str) -> tuple[str, str]:
    facts = [
        fact
        for company in companies
        for fact in company.get("candidate_facts", [])
        if fact.get("frame") == frame
    ]
    if not facts:
        return "", frame
    return max((fact.get("end", ""), frame) for fact in facts)


def _best_fact_for_frame(facts: list[dict], frame: str) -> Optional[dict]:
    matches = [fact for fact in facts if fact.get("frame") == frame]
    if not matches:
        return None
    return max(matches, key=fact_sort_key)


def _frame_period_kind(companies: list[dict], frame: str) -> str:
    kinds = [
        _fact_period_kind(fact)
        for company in companies
        for fact in company.get("candidate_facts", [])
        if fact.get("frame") == frame
    ]
    kinds = [kind for kind in kinds if kind]
    if not kinds:
        return ""
    return max(set(kinds), key=kinds.count)


def _fact_period_kind(fact: dict) -> str:
    start = parse_date(fact.get("start", ""))
    end = parse_date(fact.get("end", ""))
    if end and not start:
        return "instant"
    if not start or not end:
        return ""
    days = (end - start).days
    if 300 <= days <= 380:
        return "annual"
    if 70 <= days <= 110:
        return "quarterly"
    return "other"


class FilingIndexParser(HTMLParser):
    """Small parser for SEC filing-index document tables."""

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self._in_tr = False
        self._in_td = False
        self._cells: list[dict] = []
        self._current_text: list[str] = []
        self._current_href = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "tr":
            self._in_tr = True
            self._cells = []
        elif self._in_tr and tag == "td":
            self._in_td = True
            self._current_text = []
            self._current_href = ""
        elif self._in_td and tag == "a":
            self._current_href = attrs_dict.get("href", "")

    def handle_data(self, data):
        if self._in_td:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._in_td:
            text = clean_text(" ".join(self._current_text))
            self._cells.append({"text": text, "href": self._current_href})
            self._in_td = False
        elif tag == "tr" and self._in_tr:
            if len(self._cells) >= 4:
                self.rows.append({
                    "sequence": self._cells[0]["text"],
                    "description": self._cells[1]["text"],
                    "document": self._cells[2]["text"],
                    "href": self._cells[2]["href"],
                    "type": self._cells[3]["text"],
                    "size": self._cells[4]["text"] if len(self._cells) > 4 else "",
                })
            self._in_tr = False


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def html_to_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<table.*?</table>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(unescape(text))


def extract_accession(value: str) -> str:
    match = re.search(r"\d{10}-\d{2}-\d{6}", value or "")
    return match.group(0) if match else ""


def extract_cik_from_url(value: str) -> str:
    match = re.search(r"/data/(\d+)/", value or "")
    return normalize_cik(match.group(1)) if match else ""


def first_snippet(text: str, keywords: list[str], width: int = 260) -> str:
    lower = text.lower()
    for keyword in keywords:
        index = lower.find(keyword.lower())
        if index != -1:
            start = max(0, index - width // 2)
            end = min(len(text), index + width)
            return clean_text(text[start:end])
    return ""


def event_types_from_items(items: str) -> set[str]:
    event_types = set()
    item_set = {item.strip() for item in (items or "").split(",") if item.strip()}
    if "2.02" in item_set:
        event_types.add("earnings")
    if "2.01" in item_set or "5.01" in item_set:
        event_types.add("merger")
    if "2.03" in item_set:
        event_types.add("debt")
    if "3.01" in item_set:
        event_types.add("delisting")
    if "5.02" in item_set:
        event_types.add("leadership")
    if "5.03" in item_set or "3.03" in item_set:
        event_types.add("capitalization")
    return event_types


def extract_earnings_highlights(text: str) -> list[dict]:
    """Extract narrative sentences from an earnings release, skipping table dumps.

    A "table dump" is detected by counting numeric tokens, columnar structure
    (multiple consecutive Q/period labels), or excessive length. These get
    dropped before the keyword-based narrative match runs.
    """
    if not text:
        return []
    patterns = [
        "revenue", "net income", "net loss", "adjusted ebitda", "ebitda",
        "guidance", "outlook", "cash flow", "margin", "earnings per share",
    ]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    highlights = []
    seen = set()
    for sentence in sentences:
        cleaned = clean_text(sentence)
        if not cleaned or len(cleaned) > 600:
            continue
        if "document exhibit" in cleaned.lower():
            continue
        # Skip table dumps: many numbers OR columnar period-label patterns.
        numeric_tokens = re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", cleaned)
        if len(numeric_tokens) > 6:
            continue
        if len(re.findall(r"\bQ[1-4]\b", cleaned)) >= 2:
            continue
        if re.search(r"\bThree Months Ended\b.+\bThree Months Ended\b", cleaned, re.I):
            continue
        # Heuristic: ratio of digits to letters > 0.4 means table-like.
        digits = sum(c.isdigit() for c in cleaned)
        letters = sum(c.isalpha() for c in cleaned)
        if letters and digits / max(1, letters) > 0.4:
            continue
        lower = cleaned.lower()
        if not (any(pattern in lower for pattern in patterns) and re.search(r"\$?\d", cleaned)):
            continue
        if cleaned in seen:
            continue
        highlights.append({"text": cleaned[:420]})
        seen.add(cleaned)
        if len(highlights) >= 12:
            break
    return highlights


def pick_comparable_facts(facts: list[dict], periods: int) -> list[dict]:
    """Pick most useful facts, preferring framed annual/quarter facts."""
    filtered = [f for f in facts if f.get("frame")]
    if not filtered:
        filtered = facts
    filtered.sort(key=lambda x: (x.get("end", ""), x.get("filed", "")), reverse=True)
    out = []
    seen = set()
    for fact in filtered:
        key = (fact.get("start", ""), fact.get("end", ""), fact.get("frame", ""))
        if key in seen:
            continue
        out.append(fact)
        seen.add(key)
        if len(out) >= periods:
            break
    return out


def latest_distinct_fact(facts: list[dict]) -> Optional[dict]:
    picked = pick_comparable_facts(facts, 1)
    return picked[0] if picked else None
