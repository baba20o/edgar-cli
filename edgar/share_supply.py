"""Conservative share evidence selection; no inferred totals or split adjustment."""
from __future__ import annotations

import re


def select_latest_shares(rows):
    """Prefer explicit dated baselines within a filing, then compare their dates."""
    per_filing = {}
    for row in rows:
        if row.get("basis") == "after_offering":
            continue
        key = row.get("accession", "")
        rank = (row.get("basis") == "before_offering", bool(row.get("as_of")),
                bool(re.search(r"issued and outstanding", row.get("sentence", ""), re.I)),
                row.get("as_of", ""))
        if key not in per_filing or rank > per_filing[key][0]:
            per_filing[key] = rank, row
    return max((item[1] for item in per_filing.values()),
               key=lambda s: (s.get("as_of") or s.get("filed", ""),
                              s.get("acceptance_datetime", "")), default=None)


def share_count_warnings(stated, xbrl, splits):
    if not stated or not xbrl:
        return []
    figures = [f for f in stated.get("figures", []) if f["type"] == "shares"]
    # Do not compare an identified class or an unsummed dual-class disclosure
    # with a cover-page count whose class basis cannot be established.
    if len(figures) != 1 or figures[0].get("share_class"):
        return []
    value, cover = figures[0]["value"], xbrl.get("value")
    if not isinstance(cover, (int, float)) or cover <= 0 or value <= 0:
        return []
    left, right = sorted([stated.get("as_of") or stated.get("filed", ""), xbrl.get("as_of", "")])
    if any(s.get("status") == "effective" and left < (s.get("effective_date") or s.get("filed", "")) <= right
           for s in splits):
        return []
    ratio = value / cover
    if ratio > 3 or ratio < 0.33:
        return [{"code": "share_count_divergence", "ratio": ratio,
                 "stated_accession": stated.get("accession"), "xbrl_accession": xbrl.get("accession"),
                 "message": "Filing-stated and XBRL cover shares differ by more than the comparison threshold. Dates and class coverage may differ; no total or float is inferred."}]
    return []
