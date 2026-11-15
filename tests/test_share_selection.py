from edgar.share_supply import select_latest_shares, share_count_warnings


def stated(value, **kwargs):
    return {"accession": "a", "filed": "2026-07-01", "basis": "stated",
            "figures": [{"type": "shares", "value": value}], **kwargs}


def test_dated_baseline_beats_undated_incidental_same_filing():
    dated = stated(202382019, as_of="2026-06-18", basis="before_offering")
    assert select_latest_shares([stated(1000000), dated]) is dated
    assert select_latest_shares([stated(999, basis="after_offering")]) is None


def test_large_disagreement_is_warning_not_silent_replacement():
    count = stated(10000, as_of="2026-07-01")
    cover = {"value": 1000000, "as_of": "2026-06-01", "accession": "b"}
    assert share_count_warnings(count, cover, [])[0]["code"] == "share_count_divergence"
    split = {"status": "effective", "effective_date": "2026-06-20"}
    assert share_count_warnings(count, cover, [split]) == []
    count["figures"][0]["share_class"] = "A"
    assert share_count_warnings(count, cover, []) == []
