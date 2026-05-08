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

COMMON_CONCEPTS = {
    "assets": ("us-gaap", "Assets", "USD"),
    "cash": ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "USD"),
    "debt": ("us-gaap", "LongTermDebtNoncurrent", "USD"),
    "diluted_eps": ("us-gaap", "EarningsPerShareDiluted", "USD/shares"),
    "eps": ("us-gaap", "EarningsPerShareDiluted", "USD/shares"),
    "liabilities": ("us-gaap", "Liabilities", "USD"),
    "net_income": ("us-gaap", "NetIncomeLoss", "USD"),
    "operating_cash_flow": ("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD"),
    "operating_income": ("us-gaap", "OperatingIncomeLoss", "USD"),
    "revenue": ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
    "shares": ("dei", "EntityCommonStockSharesOutstanding", "shares"),
}

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
    key = concept.strip().lower().replace("-", "_").replace(" ", "_")
    if key in COMMON_CONCEPTS:
        alias_taxonomy, tag, alias_unit = COMMON_CONCEPTS[key]
        return taxonomy or alias_taxonomy, tag, unit or alias_unit
    return taxonomy or "us-gaap", concept, unit


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
        filings = self._recent_filings(
            result,
            limit=limit,
            form=form,
            start_date=start_date,
            end_date=end_date,
        )
        files_checked = 0
        if all_history and len(filings) < limit:
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

        warning = ""
        if not filings and (form or start_date or end_date):
            if files_checked:
                warning = f"No matching filings found after searching recent filings plus {files_checked} historical chunk(s)."
            else:
                warning = "No matching filings found in the recent filing set."
                if files and not all_history:
                    warning += " Older filings may exist in historical chunks; rerun with --all to search them."
        elif files and not all_history:
            warning = "Only the SEC recent filing set is searched; older historical chunks exist."
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
                        unit: Optional[str] = None, limit: int = 20) -> dict:
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
            if "404" in result.get("error", ""):
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
            haystack = " ".join([
                concept_tag,
                str(concept.get("label") or ""),
                str(concept.get("description") or ""),
            ]).lower()
            if wanted in haystack or wanted_stem in haystack or haystack in wanted:
                scored.append((0, concept))

        seen = {c["tag"] for _, c in scored}
        for match in get_close_matches(tag, tags, n=limit * 2, cutoff=0.35):
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
        urls = filing_urls(cik, accession_number)
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
        resolved_cik = cik or extract_cik_from_url(accession_or_url)
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
                        unit: Optional[str] = None, periods: int = 4) -> dict:
        """Compare a concept across companies."""
        resolved_taxonomy, tag, resolved_unit = resolve_concept_alias(concept, taxonomy, unit)
        companies = []
        for identifier in identifiers:
            result = self.company_concept(
                identifier,
                resolved_taxonomy,
                tag,
                unit=resolved_unit,
                limit=max(periods * 4, periods),
            )
            if "error" in result:
                companies.append({"identifier": identifier, "error": result["error"], "facts": []})
                continue
            facts = pick_comparable_facts(result.get("facts", []), periods)
            companies.append({
                "identifier": identifier,
                "cik": result.get("cik", ""),
                "name": result.get("name", identifier),
                "taxonomy": resolved_taxonomy,
                "tag": tag,
                "unit": resolved_unit,
                "facts": facts,
            })
        return {"concept": concept, "taxonomy": resolved_taxonomy, "tag": tag, "unit": resolved_unit, "companies": companies}

    def brief(self, identifier: str | int) -> dict:
        """Build a compact company brief."""
        profile = self.submissions(identifier, limit=5)
        if "error" in profile:
            return profile

        metrics = []
        for label, (taxonomy, tag, unit) in COMMON_CONCEPTS.items():
            if label not in {"revenue", "net_income", "operating_income", "cash", "assets", "debt"}:
                continue
            result = self.company_concept(profile["cik"], taxonomy, tag, unit=unit, limit=3)
            if "error" in result:
                continue
            fact = latest_distinct_fact(result.get("facts", []))
            if fact:
                metrics.append({"metric": label, "taxonomy": taxonomy, "tag": tag, "unit": unit, "fact": fact})

        earnings = self.latest_earnings(profile["cik"], limit=8)
        events = self.events(profile["cik"], limit=8)
        return {
            "profile": profile,
            "metrics": metrics,
            "earnings": earnings if "error" not in earnings else None,
            "events": events.get("events", [])[:5] if "error" not in events else [],
        }

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
