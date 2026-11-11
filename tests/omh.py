"""Shared OMH (Ohmyhome Ltd, CIK 0001944902) fixtures for offline tests.

OMH is a Singapore foreign private issuer: it files 6-K, 20-F, F-3 and 424B5
rather than 8-K and 10-K. The documents under `tests/fixtures/omh/` are the
filings as SEC serves them; `submissions.json` is the filing list trimmed to
rows filed on or after 2025-12-01.
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
                   json_routes: dict[str, dict] | None = None) -> EdgarClient:
    """An EdgarClient that answers from fixtures and fails on anything else.

    `_get` serves the submissions fixture (plus `json_routes`, matched by URL
    substring); `_get_text` serves a document by the accession in its URL, or
    by a URL substring given in `extra_documents`.
    """
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    routes = {f"/submissions/CIK{CIK}.json": fixture_json("submissions.json")}
    routes.update(json_routes or {})
    client.requested: list[str] = []

    def fake_get(path, params=None, skip_cache=False):
        client.requested.append(path)
        for needle, payload in routes.items():
            if needle in path:
                return payload
        return {"error": f"404 Not Found: {path}"}

    def fake_get_text(url):
        client.requested.append(url)
        for needle, content in (extra_documents or {}).items():
            if needle in url:
                return content
        match = re.search(r"/(\d{10})(\d{2})(\d{6})/", url)
        if match:
            accession = "-".join(match.groups())
            for candidate in (f"{accession}.htm", f"form3_{accession}.xml"):
                if (FIXTURES / candidate).exists():
                    return fixture_text(candidate)
        raise AssertionError(f"offline test tried to fetch {url}")

    client._get = fake_get
    client._get_text = fake_get_text
    return client
