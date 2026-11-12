"""Shared OMH (Ohmyhome Ltd, CIK 0001944902) fixtures for offline tests.

OMH is a Singapore foreign private issuer: it files 6-K, 20-F, F-3 and 424B5
rather than 8-K and 10-K. Under `tests/fixtures/omh/`:

- `<accession>.htm`: 6-K primary documents as SEC serves them;
- `<accession>.excerpt.txt`: verbatim paragraphs of the two 424B5 prospectus
  supplements (the full documents are ~700 KB each);
- `submissions.json`: the filing list trimmed to rows filed on or after
  2025-12-01 plus every row under shelf file number 333-285637;
- `companyfacts.json`: SEC's companyfacts payload trimmed to a few tags;
- `form3_<accession>.xml`: one Form 3.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from edgar.api import EdgarClient

FIXTURES = Path(__file__).parent / "fixtures" / "omh"
CIK = "0001944902"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict:
    return json.loads(fixture_text(name))


def offline_client(extra_documents: dict[str, str] | None = None,
                   json_routes: dict[str, dict] | None = None,
                   default_document: str | None = None) -> EdgarClient:
    """An EdgarClient that answers from fixtures and fails on anything else.

    `_get` serves the submissions fixture (plus `json_routes`, matched by URL
    substring); `_get_text` serves a document by the accession in its URL, or
    by a URL substring given in `extra_documents`. A document with no fixture
    is an error unless `default_document` supplies a stand-in.
    """
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    company_facts = fixture_json("companyfacts.json")
    routes = {
        f"/submissions/CIK{CIK}.json": fixture_json("submissions.json"),
        f"/api/xbrl/companyfacts/CIK{CIK}.json": company_facts,
    }
    routes.update(json_routes or {})
    client.requested: list[str] = []

    def fake_get(path, params=None, skip_cache=False):
        client.requested.append(path)
        for needle, payload in routes.items():
            if needle in path:
                return payload
        # companyconcept is one tag of companyfacts: serve it from that fixture.
        concept = re.search(rf"/companyconcept/CIK{CIK}/([\w-]+)/(\w+)\.json", path)
        if concept:
            taxonomy, tag = concept.groups()
            found = company_facts["facts"].get(taxonomy, {}).get(tag)
            if found:
                return {"cik": int(CIK), "taxonomy": taxonomy, "tag": tag,
                        "entityName": company_facts["entityName"], **found}
        return {"error": f"404 Not Found: {path}"}

    def fake_get_text(url):
        client.requested.append(url)
        for needle, content in (extra_documents or {}).items():
            if needle in url:
                return content
        match = re.search(r"/(\d{10})(\d{2})(\d{6})/", url)
        if match:
            accession = "-".join(match.groups())
            for candidate in (f"{accession}.htm", f"{accession}.excerpt.txt",
                              f"form3_{accession}.xml"):
                if (FIXTURES / candidate).exists():
                    return fixture_text(candidate)
        if default_document is not None:
            return default_document
        raise AssertionError(f"offline test tried to fetch {url}")

    client._get = fake_get
    client._get_text = fake_get_text
    return client
