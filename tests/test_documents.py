"""SEC index-to-download regressions; every HTTP request uses a fake session."""

from html import escape
from pathlib import Path

import pytest
import requests
from click.testing import CliRunner

from edgar.api import EdgarClient, FilingIndexParser
from edgar.cli import _download_documents, main
from edgar.documents import normalize_sec_document_url


ARCHIVE = "/Archives/edgar/data/789019/000119312526323660/"
DOCUMENT = ARCHIVE + "msft-20260630.htm"
ORIGIN = "https://www.sec.gov"
INDEX_URL = ORIGIN + ARCHIVE + "0001193125-26-323660-index.htm"
FILING = b"<html><title>Microsoft 10-K</title><p>Actual filing narrative.</p></html>"
SHELL = b"<html><title>XBRL Viewer</title><script>loadViewer();</script></html>"


def index_html(href):
    return (
        "<table><tr><td>1</td><td>10-K</td><td>"
        f'<a href="{escape(href, quote=True)}">msft-20260630.htm</a> '
        '<span class="ixbrl">iXBRL</span></td><td>10-K</td>'
        f"<td>{len(FILING)}</td></tr></table>"
    )


class FakeResponse:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status
        self.text = content.decode()

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.HTTPError(f"{self.status_code} fake SEC response")


def fake_client(monkeypatch, href, document_response=None):
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    calls = []
    responses = {
        INDEX_URL: FakeResponse(index_html(href).encode()),
        ORIGIN + DOCUMENT: document_response or FakeResponse(FILING),
        ORIGIN + "/ix?doc=" + DOCUMENT: FakeResponse(SHELL),
    }

    def get(url, **kwargs):
        calls.append(url)
        assert url in responses, f"Unexpected request: {url}"
        return responses[url]

    monkeypatch.setattr(client.session, "get", get)
    return client, calls


@pytest.mark.parametrize("href", [
    "/ix?doc=" + DOCUMENT,
    "/ix.xhtml?doc=" + DOCUMENT,
    "/ixviewer/ix.html?doc=" + DOCUMENT,
    "/ixviewer/doc/action?doc=" + DOCUMENT,
    ORIGIN + "/ix?doc=" + DOCUMENT,
    "/ix?doc=%2FArchives%2Fedgar%2Fdata%2F789019%2F000119312526323660%2Fmsft-20260630.htm",
    "/ix?doc=https%3A%2F%2Fwww.sec.gov" + DOCUMENT,
])
def test_index_and_downloader_retrieve_actual_filing(monkeypatch, tmp_path, href):
    client, calls = fake_client(monkeypatch, href)
    result = client.filing_documents("789019", "0001193125-26-323660")
    doc = result["documents"][0]
    assert doc["document"] == "msft-20260630.htm"
    assert doc["url"] == ORIGIN + DOCUMENT
    assert doc["href"] == href
    result = _download_documents(client, result, tmp_path)
    assert "error" not in result
    assert Path(doc["downloaded_to"]).read_bytes() == FILING
    assert calls == [INDEX_URL, ORIGIN + DOCUMENT]


@pytest.mark.parametrize("href", [
    "msft-20260630.htm", DOCUMENT, DOCUMENT.lstrip("/"), ORIGIN + DOCUMENT,
])
def test_ordinary_document_links_still_work(href):
    assert normalize_sec_document_url(href, INDEX_URL) == ORIGIN + DOCUMENT


@pytest.mark.parametrize("href", [
    "/ix?doc=https://evil.example" + DOCUMENT,
    "/ix?doc=//evil.example" + DOCUMENT,
    "https://evil.example/ix?doc=" + DOCUMENT,
    "https://www.sec.gov.evil.example/ix?doc=" + DOCUMENT,
    "https://www.sec.gov@evil.example/ix?doc=" + DOCUMENT,
    "/ix?doc=https://www.sec.gov:8443" + DOCUMENT,
    "/ix?doc=/files/not-a-filing.htm",
    "/ix?doc=/Archives/edgar/data/789019/000119312526323660/../../evil.htm",
    "/ix?doc=/Archives/edgar/data/789019/000119312526323660/%252e%252e/evil.htm",
    "/ix?doc=file:///etc/passwd",
    "/ix?doc=",
    "/ix?doc=" + DOCUMENT + "&doc=" + DOCUMENT,
    "/ix?doc=/ix?doc=" + DOCUMENT,
    "/ix?doc=%5C%5Cevil.example" + DOCUMENT,
    "/ix?doc=%0Ahttps://evil.example" + DOCUMENT,
])
def test_untrusted_links_rejected_before_document_request(monkeypatch, tmp_path, href):
    client, calls = fake_client(monkeypatch, href)
    result = client.filing_documents("789019", "0001193125-26-323660")
    assert "error" in result
    assert calls == [INDEX_URL]
    result = _download_documents(client, {"documents": [{"url": href}]}, tmp_path)
    assert "error" in result
    assert calls == [INDEX_URL]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("response", [
    FakeResponse(SHELL), FakeResponse(b""), FakeResponse(b"Not found", 404),
])
def test_failed_document_retrieval_never_marks_download_complete(monkeypatch, tmp_path, response):
    client, calls = fake_client(monkeypatch, "/ix?doc=" + DOCUMENT, response)
    result = _download_documents(client, {"documents": [{
        "url": ORIGIN + "/ix?doc=" + DOCUMENT,
        "document": "msft-20260630.htm iXBRL", "downloaded_to": "stale",
    }]}, tmp_path)
    assert "error" in result
    assert "downloaded_to" not in result["documents"][0]
    assert not list(tmp_path.iterdir())
    assert calls == [ORIGIN + DOCUMENT]


def test_ordinary_exhibit_download(monkeypatch, tmp_path):
    client = EdgarClient(cache=None, rate_limiter=None, use_cache=False)
    url = ORIGIN + ARCHIVE + "ex99-1.htm"
    calls = []

    def get(requested_url, **kwargs):
        calls.append(requested_url)
        return FakeResponse(b"<html>Press release</html>")

    monkeypatch.setattr(client.session, "get", get)
    result = _download_documents(client, {"documents": [{
        "url": url, "document": "ex99-1.htm", "type": "EX-99.1",
    }]}, tmp_path)
    assert "error" not in result
    assert (tmp_path / "ex99-1.htm").read_bytes() == b"<html>Press release</html>"
    assert calls == [url]


def test_index_parser_ignores_badge_link():
    parser = FilingIndexParser()
    parser.feed(index_html("/ix?doc=" + DOCUMENT).replace(
        '<span class="ixbrl">iXBRL</span>', '<a href="/help/ixbrl">iXBRL</a>',
    ))
    assert parser.rows[0]["document"] == "msft-20260630.htm"
    assert parser.rows[0]["href"] == "/ix?doc=" + DOCUMENT


def test_exhibits_cli_downloads_the_filing(monkeypatch, tmp_path):
    client, calls = fake_client(monkeypatch, "/ix?doc=" + DOCUMENT)
    monkeypatch.setattr("edgar.cli.get_client", lambda **kwargs: client)
    result = CliRunner().invoke(main, [
        "--no-cache", "exhibits", "0001193125-26-323660", "--cik", "789019",
        "--type-filter", "10-K", "--download", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "msft-20260630.htm").read_bytes() == FILING
    assert calls == [INDEX_URL, ORIGIN + DOCUMENT]
