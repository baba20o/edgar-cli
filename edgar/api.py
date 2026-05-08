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
from datetime import date, datetime
from difflib import get_close_matches
from html import unescape
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import quote

from dotenv import load_dotenv

from research_cli_base import BaseAPIClient, FileCache, SharedRateLimiter

log = logging.getLogger(__name__)

DATA_BASE_URL = "https://data.sec.gov"
SEC_BASE_URL = "https://www.sec.gov"
COMPANY_TICKERS_URL = f"{SEC_BASE_URL}/files/company_tickers_exchange.json"

DEFAULT_CACHE_TTL = 900
DEFAULT_RATE_LIMIT_INTERVAL = 0.2
DEFAULT_USER_AGENT = "edgar-cli/0.1.0 baba200@greenmountaincomputing.com"

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
}

COMMON_CONCEPTS = {
    alias: candidates[0] for alias, candidates in COMMON_CONCEPT_CANDIDATES.items()
}

BRIEF_METRICS = ["assets", "cash", "debt", "net_income", "operating_income", "revenue"]
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


def get_client(use_cache: bool = True) -> "EdgarClient":
    """Create an EDGAR client with shared cache and rate limiter."""
    cache = FileCache(cache_dir="~/.edgar_cache", ttl=DEFAULT_CACHE_TTL)
    rate_limiter = SharedRateLimiter(
        db_path="~/.edgar/rate_limit.db",
        min_interval=DEFAULT_RATE_LIMIT_INTERVAL,
    )
    return EdgarClient(cache=cache, rate_limiter=rate_limiter, use_cache=use_cache)


class EdgarClient(BaseAPIClient):
    """SEC EDGAR public data client."""

    BASE_URL = DATA_BASE_URL

    def __init__(self, *args, user_agent: Optional[str] = None, **kwargs):
        load_dotenv()
        self.user_agent = user_agent or os.getenv("SEC_USER_AGENT") or DEFAULT_USER_AGENT
        super().__init__(*args, **kwargs)

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
                    all_history: bool = False) -> dict:
        """Return company submission metadata and recent filings."""
        company = self.resolve_company(identifier)
        if "error" in company:
            return company

        cik = company["cik"]
        result = self._get(f"/submissions/CIK{cik}.json")
        if "error" in result:
            return result

        files = result.get("filings", {}).get("files", [])
        recent_matches = self._recent_filings(
            result,
            limit=limit + 1,
            form=form,
            start_date=start_date,
            end_date=end_date,
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

    def company_concept(self, identifier: str | int, taxonomy: str, tag: str,
                        unit: Optional[str] = None, limit: int = 20,
                        suggest_on_404: bool = True,
                        period_type: Optional[str] = None) -> dict:
        """Return all facts for a single company concept."""
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
                facts.append(item)

        facts.sort(key=lambda x: (x.get("filed", ""), x.get("end", "")), reverse=True)
        return {
            "cik": normalize_cik(result.get("cik", company["cik"])),
            "name": result.get("entityName", company.get("name", "")),
            "taxonomy": result.get("taxonomy", taxonomy),
            "tag": result.get("tag", tag),
            "label": result.get("label", ""),
            "description": result.get("description", ""),
            "facts": facts[:limit],
            "total": len(facts),
        }

    def company_concept_alias(self, identifier: str | int, concept: str,
                              unit: Optional[str] = None, limit: int = 20,
                              period_type: Optional[str] = None) -> dict:
        """Return facts for a friendly concept alias, choosing the freshest candidate tag."""
        key = concept_alias_key(concept)
        candidates = concept_alias_candidates(concept, unit=unit)
        if key not in COMMON_CONCEPT_CANDIDATES:
            taxonomy, tag, resolved_unit = candidates[0]
            return self.company_concept(
                identifier, taxonomy, tag, unit=resolved_unit, limit=limit, period_type=period_type,
            )

        errors = []
        results = []
        for taxonomy, tag, resolved_unit in candidates:
            result = self.company_concept(
                identifier, taxonomy, tag, unit=resolved_unit, limit=limit,
                suggest_on_404=False, period_type=period_type,
            )
            if "error" in result:
                errors.append(result["error"])
                continue
            if result.get("facts"):
                results.append(result)

        if not results:
            return {"error": errors[0] if errors else f"No facts found for {concept}", "facts": []}

        best = max(results, key=lambda result: fact_sort_key(latest_distinct_fact(result.get("facts", [])) or {}))
        best["alias"] = concept
        best["candidate_tags"] = [tag for _, tag, _ in candidates]
        return best

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

    def events(self, identifier: str | int, limit: int = 20) -> dict:
        """Detect notable recent filing events from 8-K metadata and document text."""
        result = self.submissions(identifier, form="8-K", limit=limit)
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

    def compare_concept(self, identifiers: list[str], concept: str, taxonomy: Optional[str] = None,
                        unit: Optional[str] = None, periods: int = 4,
                        period_type: Optional[str] = None) -> dict:
        """Compare a concept across companies."""
        candidates = concept_alias_candidates(concept, taxonomy, unit)
        companies = []
        for identifier in identifiers:
            company = self._company_candidate_facts(
                identifier, candidates, periods=max(periods * 12, 36), period_type=period_type,
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
                                 periods: int, period_type: Optional[str] = None) -> dict:
        errors = []
        candidate_facts = []
        metadata = {}
        for taxonomy, tag, unit in candidates:
            result = self.company_concept(
                identifier, taxonomy, tag, unit=unit, limit=periods,
                suggest_on_404=False, period_type=period_type,
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
                        end_date: Optional[str] = None) -> list[dict]:
        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        cik = normalize_cik(data.get("cik", "0"))
        rows = []
        wanted_form = form.upper() if form else None

        for index, accession in enumerate(accessions):
            row = {key: values[index] for key, values in recent.items() if index < len(values)}
            if wanted_form and row.get("form", "").upper() != wanted_form:
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
    """Extract simple key-value-ish sentences from an earnings release."""
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
        lower = sentence.lower()
        if any(pattern in lower for pattern in patterns) and re.search(r"\$?\d", sentence):
            cleaned = clean_text(sentence)
            if "document exhibit" in cleaned.lower():
                continue
            if len(re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", cleaned)) > 8:
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
