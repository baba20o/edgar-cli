"""Document retrieval must be reusable offline and report every lookup."""

import json
from unittest.mock import Mock

import pytest
import requests

from edgar.api import EdgarClient
from edgar.cache import EdgarCache, ttl_for_url
from edgar.transport import DOCUMENT_PARAMS

URL = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/filing.htm"


def response(content=b"<p>Actual filing text</p>", status=200):
    result = requests.Response()
    result.status_code = status
    result._content = content
    result.encoding = "utf-8"
    result.headers["ETag"] = '"filing"'
    return result


def client_for(tmp_path, monkeypatch, replies):
    cache = EdgarCache(cache_dir=str(tmp_path))
    client = EdgarClient(edgar_cache=cache, rate_limiter=None)
    client.rate_limiter = Mock()
    get = Mock(side_effect=replies)
    monkeypatch.setattr(client.session, "get", get)
    return client, cache, get


def test_text_and_bytes_share_one_exact_cached_representation(tmp_path, monkeypatch):
    body = '<p>Caf\u00e9 \u2014 $2</p>'.encode()
    client, cache, get = client_for(tmp_path, monkeypatch, [response(body)])
    assert client._get_text(URL) == body.decode()
    assert client._get_bytes(URL) == body
    assert client._get_text(URL) == body.decode()
    assert get.call_count == client.rate_limiter.acquire.call_count == 1
    assert client._summarize_cache()["calls"] == 3
    assert client._summarize_cache()["hits"] == 2
    assert ttl_for_url(URL) == 30 * 86400
    # A new process/client uses the disk cache too.
    other = EdgarClient(edgar_cache=cache, rate_limiter=None)
    monkeypatch.setattr(other.session, "get", Mock(side_effect=AssertionError("network")))
    assert other._get_bytes(URL) == body


def test_expired_document_revalidates_without_redownloading(tmp_path, monkeypatch):
    client, cache, get = client_for(tmp_path, monkeypatch, [response(), response(b"", 304)])
    original = client._get_bytes(URL)
    path = cache._path(cache._key(URL, DOCUMENT_PARAMS))
    stored = json.loads(path.read_text()); stored["_ts"] = 0
    path.write_text(json.dumps(stored))
    assert client._get_bytes(URL) == original
    assert get.call_args.kwargs["headers"] == {"If-None-Match": '"filing"'}
    assert client._summarize_cache()["hits"] == 1


@pytest.mark.parametrize("reply", [response(b"", 200), response(b"denied", 403),
    response(b"missing", 404), response(b"<title>XBRL Viewer</title>", 200)])
def test_failed_or_viewer_documents_never_poison_cache(tmp_path, monkeypatch, reply):
    client, cache, get = client_for(tmp_path, monkeypatch, [reply, response()])
    with pytest.raises((requests.HTTPError, ValueError)):
        client._get_bytes(URL)
    assert cache.get_with_meta(URL, DOCUMENT_PARAMS)[0] is None
    assert client._get_bytes(URL) == b"<p>Actual filing text</p>"
    assert client._summarize_cache()["calls"] == 2


def test_no_cache_bypasses_saved_documents_but_keeps_telemetry(tmp_path, monkeypatch):
    client, _, get = client_for(tmp_path, monkeypatch, [response(), response(b"new")])
    client._get_bytes(URL)
    client.use_cache = False
    assert client._get_bytes(URL) == b"new"
    assert get.call_count == 2
    assert client._summarize_cache()["misses"] == 2


def test_document_size_eviction_and_invalidation(tmp_path, monkeypatch):
    client, cache, _ = client_for(tmp_path, monkeypatch, [response(), response()])
    client._get_bytes(URL)
    assert cache.invalidate("*filing.htm") == 1
    cache.max_bytes = 10
    client._get_bytes(URL)
    assert cache.get_with_meta(URL, DOCUMENT_PARAMS)[0] is None


def test_rendered_cache_survives_new_client_and_tracks_source_updates(tmp_path, monkeypatch):
    from edgar import filing_text
    client, cache, get = client_for(tmp_path, monkeypatch, [response()])
    assert client._get_filing_text(URL) == "Actual filing text"
    other = EdgarClient(edgar_cache=cache)
    monkeypatch.setattr(other.session, "get", Mock(side_effect=AssertionError("network")))
    parser = Mock(wraps=filing_text.document_to_text)
    monkeypatch.setattr(filing_text, "document_to_text", parser)
    assert other._get_filing_text(URL) == "Actual filing text"
    parser.assert_not_called()
    # A refreshed source at the same URL cannot reuse stale derived text.
    cache.set(URL, DOCUMENT_PARAMS, {"kind":"document-v1", "body":"PHA+VXBkYXRlZDwvcD4=", "encoding":"utf-8"})
    assert other._get_filing_text(URL) == "Updated"
    parser.assert_called_once()
    assert cache.invalidate("*filing.htm") == 3


def test_no_cache_bypasses_rendered_text(tmp_path, monkeypatch):
    client, cache, get = client_for(tmp_path, monkeypatch, [response(), response(b"<p>Replacement</p>")])
    assert client._get_filing_text(URL) == "Actual filing text"
    client.use_cache = False
    assert client._get_filing_text(URL) == "Replacement"
    assert get.call_count == 2
    client.use_cache = True
    assert client._get_filing_text(URL) == "Actual filing text"
