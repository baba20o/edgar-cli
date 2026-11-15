"""Offline Atom feed and acknowledgement regressions."""

from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

import pytest
import requests

from edgar import feed
from edgar.feed import ack_feed, fetch_feed, parse_atom, reset_feed_cursor
from edgar.state import StateStore


NOW = "2026-11-15T12:00:00Z"
NS = "{http://www.w3.org/2005/Atom}"


def filing(number, stamp="2026-11-15T11:59:00Z", form="8-K", cik="1"):
    return {"number": number, "stamp": stamp, "form": form, "cik": cik}


def atom(rows):
    root = ET.Element(NS + "feed")
    for row in rows:
        accession = f"0000000001-26-{row['number']:06}"
        entry = ET.SubElement(root, NS + "entry")
        ET.SubElement(entry, NS + "title").text = f"{row['form']} - Example Inc ({int(row['cik']):010}) (Filer)"
        ET.SubElement(entry, NS + "id").text = f"urn:tag:sec.gov,2008:accession-number={accession}"
        ET.SubElement(entry, NS + "link", {"rel": "alternate", "href": f"https://www.sec.gov/Archives/edgar/data/{row['cik']}/{accession.replace('-', '')}/{accession}-index.htm"})
        if row["stamp"] is not None:
            ET.SubElement(entry, NS + "updated").text = row["stamp"]
        ET.SubElement(entry, NS + "category", {"label": "form type", "term": row["form"]})
        ET.SubElement(entry, NS + "summary", {"type": "html"}).text = f"<b>Filed:</b> 2026-11-15 <b>AccNo:</b> {accession} <b>Size:</b> 8 KB<br/>Item 1.01: Agreement<br/>Item 3.02: Unregistered Sales"
    return ET.tostring(root)


class FakeClient:
    max_retries = 0
    request_timeout = 10

    def __init__(self, pages, companies=None):
        self.pages = pages
        self.session = self
        self.rate_limiter = self
        self.calls = []
        self.limits = 0
        self.cache_records = []
        self.mapping = companies if companies is not None else {"companies": [{"cik": "0000000001", "ticker": "ONE", "exchange": "Nasdaq"}]}

    def acquire(self):
        self.limits += 1

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        start = int(parse_qs(urlsplit(url).query)["start"][0])
        data = self.pages.get(start, atom([]))
        if isinstance(data, Exception):
            raise data
        response = requests.Response()
        response.status_code = 200
        response._content = data
        response.url = url
        return response

    def companies(self):
        return self.mapping

    def _record_cache(self, meta):
        self.cache_records.append(meta)


def test_atom_entry_uses_offset_time_and_preserves_filed_date_and_items():
    rows, errors = parse_atom(atom([filing(1, "2026-11-15T06:59:00-05:00")]))
    assert not errors
    assert rows[0]["acceptance_datetime"] == "2026-11-15T11:59:00Z"
    assert rows[0]["acceptance_datetime_source"] == "atom.updated"
    assert rows[0]["filed"] == "2026-11-15"
    assert rows[0]["items"] == "1.01,3.02"


def test_feed_is_uncached_and_fetch_does_not_acknowledge(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(2), filing(1, "2026-11-15T11:40:00Z")])})
    first = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert first["coverage"]["complete"] is True
    assert first["total"] == 1
    assert first["filings"][0]["ticker"] == "ONE"
    assert store.get_feed_cursor(first["state_scope"]) is None
    fetch_feed(client, forms="8-K", now=NOW)
    assert len(client.calls) == client.limits == len(client.cache_records) == 2
    assert client.calls[0][1]["headers"]["Cache-Control"] == "no-cache"
    assert ack_feed(first, store) is True
    assert ack_feed(first, store) is False


def test_unseen_same_timestamp_rows_survive_and_duplicate_entries_are_deduped(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(2), filing(1, "2026-11-15T11:40:00Z")])})
    first = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    ack_feed(first, store)
    client.pages[0] = atom([filing(3), filing(3), filing(2), filing(1, "2026-11-15T11:40:00Z")])
    second = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert [r["accession"] for r in second["filings"]] == ["0000000001-26-000003"]
    ack_feed(second, store)
    assert len(store.get_feed_cursor(first["state_scope"])["recent_seen"]) == 2
    assert fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)["total"] == 0


def test_timestamp_ties_are_drained_across_page_boundaries(monkeypatch, tmp_path):
    monkeypatch.setattr(feed, "PAGE_SIZE", 2)
    store = StateStore(str(tmp_path / "state.json"))
    store.update_feed_cursor("atom-v1:8-K", "2026-11-15T11:59:00Z", ["0000000001:0000000001-26-000003"])
    client = FakeClient({0: atom([filing(4), filing(3)]),
                         2: atom([filing(2), filing(1, "2026-11-15T11:40:00Z")])})
    result = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert result["coverage"]["pages_read"] == 2
    assert {r["accession"] for r in result["filings"]} == {"0000000001-26-000004", "0000000001-26-000002"}
    ack_feed(result, store)
    assert len(store.get_feed_cursor(result["state_scope"])["recent_seen"]) == 3
    assert fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)["total"] == 0


def test_late_entry_older_than_high_water_is_found_inside_overlap(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(3), filing(1, "2026-11-15T11:40:00Z")])})
    initial = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    ack_feed(initial, store)
    client.pages[0] = atom([filing(3), filing(2, "2026-11-15T11:50:00Z"), filing(1, "2026-11-15T11:40:00Z")])
    late = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert [r["accession"] for r in late["filings"]] == ["0000000001-26-000002"]
    ack_feed(late, store)
    cursor = store.get_feed_cursor(late["state_scope"])
    assert cursor["timestamp"] == NOW
    assert len(cursor["recent_seen"]) == 2
    assert fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)["total"] == 0


def test_scope_is_bound_to_exact_forms_and_normalized_order(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(3, form="6-K"), filing(2), filing(1, "2026-11-15T11:40:00Z")])})
    domestic = fetch_feed(client, forms="8-k", use_state=True, state_store=store, now=NOW)
    ack_feed(domestic, store)
    foreign = fetch_feed(client, forms="6-K", use_state=True, state_store=store, now=NOW)
    assert foreign["total"] == 1
    assert foreign["state_used"] is False
    mixed = fetch_feed(client, forms="8-K,6-K", now=NOW)
    reordered = fetch_feed(client, forms="6-k,8-k,8-k", now=NOW)
    assert mixed["state_scope"] == reordered["state_scope"]


def test_multiple_share_classes_are_explicit_not_silently_selected():
    client = FakeClient({0: atom([filing(2), filing(1, "2026-11-15T11:40:00Z")])}, {"companies": [
        {"cik": "1", "ticker": "ONE-A", "exchange": "NYSE"},
        {"cik": "1", "ticker": "ONE-B", "exchange": "NYSE"},
    ]})
    row = fetch_feed(client, forms="8-K", now=NOW)["filings"][0]
    assert row["ticker"] is None
    assert row["tickers"] == ["ONE-A", "ONE-B"]
    assert row["exchange"] == "NYSE"
    assert len(row["listings"]) == 2


def test_same_accession_different_filers_remain_distinct():
    client = FakeClient({0: atom([filing(2, cik="1"), filing(2, cik="2"), filing(1, "2026-11-15T11:40:00Z")])})
    result = fetch_feed(client, forms="8-K", now=NOW)
    assert result["total"] == 2
    assert {row["cik"] for row in result["filings"]} == {"0000000001", "0000000002"}


def test_page_limit_never_advances_past_uncaptured_backlog(monkeypatch, tmp_path):
    monkeypatch.setattr(feed, "PAGE_SIZE", 2)
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(4), filing(3, "2026-11-15T11:58:00Z")]),
                         2: atom([filing(2, "2026-11-15T11:57:00Z"), filing(1, "2026-11-15T11:40:00Z")])})
    partial = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW, max_pages=1)
    assert partial["total"] == 2
    assert partial["coverage"]["complete"] is False
    assert partial["coverage"]["reason"] == "page_limit"
    assert "cursor_candidate" not in partial
    assert not ack_feed(partial, store)
    retry = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW, max_pages=2)
    assert retry["coverage"]["complete"] is True
    assert retry["total"] == 3
    assert ack_feed(retry, store)


def test_existing_cursor_overrides_short_lookback(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    store.update_feed_cursor("atom-v1:8-K", "2026-11-15T10:00:00Z", ["0000000001:0000000001-26-000001"])
    client = FakeClient({0: atom([filing(3), filing(2, "2026-11-15T10:10:00Z"),
                                  filing(1, "2026-11-15T10:00:00Z"), filing(0, "2026-11-15T09:40:00Z")])})
    result = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert result["total"] == 2
    assert result["since"] == "2026-11-15T09:45:00Z"


def test_page_failure_preserves_existing_cursor_for_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(feed, "PAGE_SIZE", 2)
    store = StateStore(str(tmp_path / "state.json"))
    store.update_feed_cursor("atom-v1:8-K", "2026-11-15T11:45:00Z", [])
    before = store.path.read_bytes()
    client = FakeClient({0: atom([filing(3), filing(2)]), 2: requests.ConnectionError("connection lost")})
    result = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert result["total"] == 2
    assert result["coverage"]["complete"] is False
    assert not ack_feed(result, store)
    assert store.path.read_bytes() == before


def test_missing_or_naive_timestamps_are_unresolved_and_block_ack(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(4), filing(3, None), filing(2, "2026-11-15T11:59:00"), filing(1, "2026-11-15T11:40:00Z")])})
    result = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert result["total"] == 1
    assert len(result["unresolved_filings"]) == 2
    assert not result["coverage"]["complete"]
    assert not ack_feed(result, store)


def test_missing_timestamp_on_excluded_form_does_not_lose_selected_scope():
    client = FakeClient({0: atom([filing(3), filing(2, None, form="4"), filing(1, "2026-11-15T11:40:00Z")])})
    result = fetch_feed(client, forms="8-K", now=NOW)
    assert result["coverage"]["complete"]
    assert result["total"] == 1


def test_out_of_order_feed_refuses_cursor_advancement(tmp_path):
    client = FakeClient({0: atom([filing(3, "2026-11-15T11:55:00Z"), filing(2), filing(1, "2026-11-15T11:40:00Z")])})
    result = fetch_feed(client, forms="8-K", use_state=True, state_store=StateStore(str(tmp_path / "state.json")), now=NOW)
    assert not result["coverage"]["complete"]
    assert any(w["code"] == "feed_order_changed" for w in result["warnings"])


def test_date_rollover_compares_utc_instants():
    client = FakeClient({0: atom([filing(2, "2026-11-14T19:01:00-05:00"), filing(1, "2026-11-14T23:00:00Z")])})
    result = fetch_feed(client, forms="8-K", now="2026-11-15T00:05:00Z")
    assert result["total"] == 1
    assert result["filings"][0]["acceptance_datetime"] == "2026-11-15T00:01:00Z"


def test_source_exhausted_before_cursor_is_a_retention_gap(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    store.update_feed_cursor("atom-v1:8-K", "2026-11-14T00:00:00Z", [])
    client = FakeClient({0: atom([filing(2), filing(1, "2026-11-15T11:50:00Z")])})
    result = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert result["coverage"]["reason"] == "retention_gap"
    assert not ack_feed(result, store)


def test_malformed_xml_first_page_is_an_error_with_no_candidate():
    result = fetch_feed(FakeClient({0: b"<html>not Atom</html>"}), now=NOW)
    assert "error" in result
    assert not result["coverage"]["complete"]
    assert "cursor_candidate" not in result


def test_unidentifiable_entry_blocks_advancement(tmp_path):
    broken = atom([filing(3), filing(2), filing(1, "2026-11-15T11:40:00Z")])
    broken = broken.replace(b"accession-number=0000000001-26-000002", b"no-accession")
    broken = broken.replace(b"AccNo:&lt;/b&gt; 0000000001-26-000002", b"AccNo:&lt;/b&gt; missing")
    result = fetch_feed(FakeClient({0: broken}), use_state=True, state_store=StateStore(str(tmp_path / "state.json")), now=NOW)
    assert not result["coverage"]["complete"]
    assert any(w["code"] == "feed_invalid_entries" for w in result["warnings"])


def test_ticker_map_failure_keeps_filing_data_and_warns():
    client = FakeClient({0: atom([filing(2), filing(1, "2026-11-15T11:40:00Z")])}, {"error": "map unavailable"})
    result = fetch_feed(client, forms="8-K", now=NOW)
    assert result["coverage"]["complete"]
    assert result["filings"][0]["ticker"] is None
    assert any(w["code"] == "feed_ticker_map_unavailable" for w in result["warnings"])


def test_stale_ack_does_not_rollback_state_and_equal_time_ids_merge(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    assert store.update_feed_cursor("atom-v1:8-K", "2026-11-15T11:59:00Z", ["a"])
    assert not store.update_feed_cursor("atom-v1:8-K", "2026-11-15T11:58:00Z", ["b"])
    assert store.update_feed_cursor("atom-v1:8-K", "2026-11-15T11:59:00Z", ["c"])
    assert store.get_feed_cursor("atom-v1:8-K")["seen"] == ["a", "c"]


def test_stale_state_instances_preserve_each_others_data(tmp_path):
    path = str(tmp_path / "state.json")
    first, second = StateStore(path), StateStore(path)
    first.update_feed_cursor("atom-v1:8-K", "2026-11-15T11:59:00Z", ["a"])
    second.subscribe("1", "8-K")
    first.update_high_water("1", "8-K", "acc", "2026-11-15")
    second.update_feed_cursor("atom-v1:6-K", "2026-11-15T11:59:00Z", ["b"])
    data = json.loads(first.path.read_text())
    assert len(data["feed_cursors"]) == 2
    assert len(data["subscriptions"]) == 1
    assert data["last_seen"]["1:8-K"]["accession"] == "acc"


def test_concurrent_state_writes_merge_instead_of_overwriting(tmp_path):
    path = str(tmp_path / "state.json")
    def write(number):
        store = StateStore(path)
        store.update_feed_cursor("atom-v1:8-K", "2026-11-15T11:59:00Z", [str(number)])
        store.subscribe(str(number), "8-K")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(16)))
    store = StateStore(path)
    assert len(store.get_feed_cursor("atom-v1:8-K")["seen"]) == 16
    assert len(store.subscriptions()) == 16


def test_cross_process_cursor_acknowledgements_merge(tmp_path):
    path = str(tmp_path / "state.json")
    code = (
        "import sys,time; from edgar.state import StateStore; "
        "s=StateStore(sys.argv[1]); time.sleep(.05); "
        "[s.update_feed_cursor('atom-v1:8-K','2026-11-15T11:59:00Z',"
        "[sys.argv[2]+'-'+str(i)]) for i in range(5)]"
    )
    def write(number):
        return subprocess.run([sys.executable, "-E", "-c", code, path, str(number)],
                              capture_output=True, text=True, timeout=20)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(4)))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert len(StateStore(path).get_feed_cursor("atom-v1:8-K")["seen"]) == 20


def test_corrupt_state_is_not_overwritten_by_feed_or_subscription_writes(tmp_path):
    path = tmp_path / "state.json"
    original = b'{"last_seen":'
    path.write_bytes(original)
    store = StateStore(str(path))
    with pytest.raises(ValueError):
        store.update_feed_cursor("atom-v1:8-K", "2026-11-15T11:59:00Z", [])
    with pytest.raises(ValueError):
        store.subscribe("1", "8-K")
    with pytest.raises(ValueError):
        store.reset_feed_cursor("atom-v1:8-K")
    assert path.read_bytes() == original


@pytest.mark.parametrize("first_rows", [[], [filing(1, "2026-11-15T09:40:00Z")]])
def test_empty_first_poll_saves_watermark_and_catches_between_poll_filings(tmp_path, first_rows):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom(first_rows)})
    first = fetch_feed(client, forms="RW", use_state=True, state_store=store,
                       now="2026-11-15T10:00:00Z")
    assert first["total"] == 0
    assert first["coverage"]["complete"]
    assert first["cursor_candidate"]["timestamp"] == "2026-11-15T10:00:00Z"
    assert first["cursor_candidate"]["watermark_basis"] == "poll_started_at"
    assert store.get_feed_cursor(first["state_scope"]) is None
    assert ack_feed(first, store)
    client.pages[0] = atom([filing(2, "2026-11-15T10:20:00Z", form="RW"),
                            filing(1, "2026-11-15T09:40:00Z")])
    second = fetch_feed(client, forms="RW", use_state=True, state_store=store,
                        now="2026-11-15T11:00:00Z")
    assert second["since"] == "2026-11-15T09:45:00Z"
    assert second["coverage"]["complete"]
    assert [row["accession"] for row in second["filings"]] == ["0000000001-26-000002"]
    assert ack_feed(second, store)
    assert store.get_feed_cursor(second["state_scope"])["timestamp"] == "2026-11-15T11:00:00Z"


def test_quiet_form_scope_advances_and_late_arrival_remains_inside_overlap(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(1, "2026-11-15T11:40:00Z")])})
    for stamp in [NOW, "2026-11-15T12:05:00Z"]:
        result = fetch_feed(client, forms="RW", use_state=True, state_store=store, now=stamp)
        assert result["total"] == 0
        assert ack_feed(result, store)
        assert store.get_feed_cursor(result["state_scope"])["timestamp"] == stamp
    client.pages[0] = atom([filing(2, "2026-11-15T12:01:00Z", form="RW"),
                            filing(1, "2026-11-15T11:40:00Z")])
    late = fetch_feed(client, forms="RW", use_state=True, state_store=store,
                      now="2026-11-15T12:06:00Z")
    assert late["total"] == 1
    assert ack_feed(late, store)
    repeated = fetch_feed(client, forms="RW", use_state=True, state_store=store,
                          now="2026-11-15T12:07:00Z")
    assert repeated["total"] == 0
    assert ack_feed(repeated, store)


def test_empty_incomplete_poll_does_not_overwrite_watermark(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    store.update_feed_cursor("atom-v1:RW", "2026-11-14T00:00:00Z", [])
    before = store.path.read_bytes()
    result = fetch_feed(FakeClient({0: atom([filing(1)])}), forms="RW", use_state=True,
                        state_store=store, now=NOW)
    assert result["total"] == 0
    assert result["coverage"]["reason"] == "retention_gap"
    assert "cursor_candidate" not in result
    assert not ack_feed(result, store)
    assert store.path.read_bytes() == before


def test_explicit_scope_reset_recovers_from_retention_gap_without_clearing_other_state(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    store.update_feed_cursor("atom-v1:8-K", "2026-11-14T00:00:00Z", [])
    store.update_feed_cursor("atom-v1:6-K", "2026-11-15T10:00:00Z", ["foreign"])
    store.subscribe("1", "8-K")
    store.update_high_water("1", "8-K", "existing", "2026-11-14")
    client = FakeClient({0: atom([filing(2), filing(1, "2026-11-15T11:40:00Z")])})
    before = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert before["coverage"]["reason"] == "retention_gap"
    assert not ack_feed(before, store)
    reset = reset_feed_cursor(forms="8-k,8-K", state_store=store)
    assert reset["state_scope"] == "atom-v1:8-K"
    assert reset["cursor_reset"] is True
    assert reset["warning"]["code"] == "feed_cursor_reset"
    assert "older undelivered filings may be missed" in reset["warning"]["message"]
    assert store.get_feed_cursor("atom-v1:8-K") is None
    assert store.get_feed_cursor("atom-v1:6-K")["seen"] == ["foreign"]
    assert len(store.subscriptions()) == 1
    assert store.get_high_water("1", "8-K")["accession"] == "existing"
    assert reset_feed_cursor(forms="8-K", state_store=store)["cursor_reset"] is False
    recovered = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert recovered["coverage"]["complete"]
    assert recovered["since"] == "2026-11-15T11:45:00Z"
    assert recovered["total"] == 1
    assert ack_feed(recovered, store)
    assert store.get_feed_cursor("atom-v1:8-K")["timestamp"] == NOW


def test_new_entries_exactly_at_poll_watermark_preserve_tie_identities(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(2, NOW), filing(1, "2026-11-15T11:40:00Z")])})
    first = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert ack_feed(first, store)
    assert store.get_feed_cursor(first["state_scope"])["seen"] == ["0000000001:0000000001-26-000002"]
    client.pages[0] = atom([filing(3, NOW), filing(2, NOW), filing(1, "2026-11-15T11:40:00Z")])
    second = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert [row["accession"] for row in second["filings"]] == ["0000000001-26-000003"]
    assert ack_feed(second, store)
    assert len(store.get_feed_cursor(second["state_scope"])["seen"]) == 2
    assert fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)["total"] == 0


@pytest.mark.parametrize("next_poll", ["2026-11-15T12:15:00Z", "2026-11-15T13:00:00Z"])
def test_full_overlap_survives_poll_cadence_at_or_beyond_lookback(tmp_path, next_poll):
    store = StateStore(str(tmp_path / "state.json"))
    client = FakeClient({0: atom([filing(3, "2026-11-15T11:59:58Z"),
                                  filing(1, "2026-11-15T11:40:00Z")])})
    first = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=NOW)
    assert ack_feed(first, store)
    # Filing 2 was timestamped five seconds before the first watermark, but
    # became visible ten seconds after that poll. It appears in this next read.
    client.pages[0] = atom([filing(4, "2026-11-15T12:10:00Z"),
                            filing(3, "2026-11-15T11:59:58Z"),
                            filing(2, "2026-11-15T11:59:55Z"),
                            filing(1, "2026-11-15T11:40:00Z")])
    second = fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=next_poll)
    assert second["coverage"]["complete"]
    assert second["since"] == "2026-11-15T11:45:00Z"
    assert [row["accession"] for row in second["filings"]] == [
        "0000000001-26-000004", "0000000001-26-000002"]
    assert ack_feed(second, store)
    # Previously delivered rows stay suppressed even though the overlap repeats.
    assert fetch_feed(client, forms="8-K", use_state=True, state_store=store, now=next_poll)["total"] == 0


@pytest.mark.parametrize("cursor_time", ["2026-11-15T11:44:59Z", "2026-11-14T00:00:00Z"])
def test_empty_atom_cannot_prove_coverage_of_an_old_cursor(tmp_path, cursor_time):
    store = StateStore(str(tmp_path / "state.json"))
    store.update_feed_cursor("atom-v1:8-K", cursor_time, [])
    before = store.path.read_bytes()
    result = fetch_feed(FakeClient({0: atom([])}), forms="8-K", use_state=True,
                        state_store=store, now=NOW)
    assert result["total"] == 0
    assert result["coverage"]["reason"] == "retention_gap"
    assert not result["coverage"]["complete"]
    assert "cursor_candidate" not in result
    assert not ack_feed(result, store)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("cursor_time", ["2026-11-15T11:45:00Z", "2026-11-15T11:59:00Z"])
def test_empty_atom_can_advance_a_recent_cursor(tmp_path, cursor_time):
    store = StateStore(str(tmp_path / "state.json"))
    store.update_feed_cursor("atom-v1:8-K", cursor_time, [])
    result = fetch_feed(FakeClient({0: atom([])}), forms="8-K", use_state=True,
                        state_store=store, now=NOW)
    assert result["coverage"]["complete"]
    assert result["coverage"]["reason"] == "empty_feed"
    assert ack_feed(result, store)
    assert store.get_feed_cursor("atom-v1:8-K")["timestamp"] == NOW
