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

    def submissions(self, identifier: str | int, limit: int = 20, form: Optional[str] = None) -> dict:
        """Return company submission metadata and recent filings."""
        company = self.resolve_company(identifier)
        if "error" in company:
            return company

        cik = company["cik"]
        result = self._get(f"/submissions/CIK{cik}.json")
        if "error" in result:
            return result

        filings = self._recent_filings(result, limit=limit, form=form)
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
            "files": result.get("filings", {}).get("files", []),
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

        path = f"/api/xbrl/companyconcept/CIK{company['cik']}/{quote(taxonomy)}/{quote(tag)}.json"
        result = self._get(path)
        if "error" in result:
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

    def frame(self, taxonomy: str, tag: str, unit: str, frame: str,
              limit: int = 25, sort_by: str = "value") -> dict:
        """Return a cross-company XBRL frame."""
        path = (
            f"/api/xbrl/frames/{quote(taxonomy)}/{quote(tag)}/"
            f"{quote(unit, safe='-')}/{quote(frame)}.json"
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

    @staticmethod
    def _recent_filings(data: dict, limit: int = 20, form: Optional[str] = None) -> list[dict]:
        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        cik = normalize_cik(data.get("cik", "0"))
        rows = []
        wanted_form = form.upper() if form else None

        for index, accession in enumerate(accessions):
            row = {key: values[index] for key, values in recent.items() if index < len(values)}
            if wanted_form and row.get("form", "").upper() != wanted_form:
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
                        concept.get("label", ""),
                        concept.get("description", ""),
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
