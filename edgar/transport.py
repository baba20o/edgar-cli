"""Cached filing document transport shared by HTML, XML and byte consumers."""
from __future__ import annotations

import base64
import binascii
import hashlib
import time

import requests
from research_cli_base.http_client import retry_wait_seconds

from edgar.cache import ttl_for_url
from edgar.documents import validate_document_content

DOCUMENT_PARAMS = {"representation": "document-v1"}
MAX_DOCUMENT_CACHE_BYTES = 4 * 1024 * 1024


def _decode(payload):
    if not isinstance(payload, dict) or payload.get("kind") != "document-v1":
        return None
    try:
        return base64.b64decode(payload["body"], validate=True), payload.get("encoding") or "utf-8"
    except (KeyError, TypeError, ValueError, binascii.Error):
        return None


def _fetch_document(client, url: str) -> tuple[bytes, str]:
    """Return exact bytes and decoding, counting each logical lookup once.

    Failed, empty and viewer-shell responses are never cached. Expired documents
    are revalidated; the TTL is a policy, not permanent-immutability assurance.
    """
    cache = client.edgar_cache if client.use_cache else None
    meta = {"key": url, "hit": False}
    if cache is not None:
        payload, meta = cache.get_with_meta(url, DOCUMENT_PARAMS)
        decoded = _decode(payload)
        if meta.get("hit") and decoded is not None:
            client._record_cache(meta)
            return decoded
        meta["hit"] = False
    stale = _decode(meta.get("stale_payload"))
    headers = {}
    if stale:
        if meta.get("stale_etag"):
            headers["If-None-Match"] = meta["stale_etag"]
        if meta.get("stale_last_modified"):
            headers["If-Modified-Since"] = meta["stale_last_modified"]
    try:
        for attempt in range(client.max_retries + 1):
            response = None
            if client.rate_limiter:
                client.rate_limiter.acquire()
            try:
                response = client.session.get(url, headers=headers or None,
                                              timeout=client.request_timeout)
                if response.status_code == 304 and stale and cache is not None:
                    cache.refresh_timestamp(url, DOCUMENT_PARAMS)
                    meta.update(hit=True, conditional_304=True, age_seconds=0,
                                ttl_remaining=ttl_for_url(url))
                    return stale
                if response.status_code == 429:
                    if attempt < client.max_retries:
                        time.sleep(retry_wait_seconds(attempt, response))
                        continue
                    raise requests.HTTPError(f"429 rate limited: {url}", response=response)
                response.raise_for_status()
            except requests.RequestException:
                if attempt < client.max_retries and (response is None or response.status_code >= 500):
                    time.sleep(retry_wait_seconds(attempt))
                    continue
                raise
            content = response.content
            validate_document_content(content)
            encoding = (getattr(response, "encoding", None)
                        or getattr(response, "apparent_encoding", None) or "utf-8")
            response_headers = getattr(response, "headers", {})
            meta.update(etag=response_headers.get("ETag"),
                        last_modified=response_headers.get("Last-Modified"))
            if cache is not None and len(content) <= MAX_DOCUMENT_CACHE_BYTES:
                cache.set(url, DOCUMENT_PARAMS,
                          {"kind": "document-v1", "body": base64.b64encode(content).decode("ascii"),
                           "encoding": encoding},
                          etag=meta["etag"], last_modified=meta["last_modified"])
            return content, encoding
        raise requests.RequestException(f"Unable to retrieve document: {url}")
    finally:
        client._record_cache({k: v for k, v in meta.items() if not k.startswith("stale_")})


def fetch_document(client, url: str) -> tuple[bytes, str]:
    # Prevent concurrent branches from downloading the same cold document twice.
    with client._key_lock(("document", url)):
        return _fetch_document(client, url)


def rendered_document(client, url: str, content: str) -> str:
    """Reuse normalized text only for the exact source and parser version.

    The caller retrieves/revalidates source bytes first. This derived entry
    cannot conceal an updated document, and no-cache bypasses both layers.
    """
    from edgar.filing_text import document_to_text
    raw = content.encode("utf-8")
    if not client.use_cache or len(raw) > MAX_DOCUMENT_CACHE_BYTES:
        return document_to_text(content)
    # Increment the representation version when document_to_text semantics change.
    params = {"representation": "filing-text-v1",
              "content_sha256": hashlib.sha256(raw).hexdigest()}
    with client._key_lock(("rendered_document", url)):
        payload, meta = client.edgar_cache.get_with_meta(url, params)
        if meta.get("hit") and isinstance(payload, dict) and isinstance(payload.get("text"), str):
            client._record_cache({k: v for k, v in meta.items() if not k.startswith("stale_")})
            return payload["text"]
        meta["hit"] = False
        client._record_cache({k: v for k, v in meta.items() if not k.startswith("stale_")})
        rendered = document_to_text(content)
        if len(rendered.encode("utf-8")) <= MAX_DOCUMENT_CACHE_BYTES:
            client.edgar_cache.set(url, params, {"text": rendered})
        return rendered
