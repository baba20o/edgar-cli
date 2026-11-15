---

<!-- UAF-ASTRA-ADDENDUM: 2026-10-23/edgar-cli db31f3b61a377577335d760ced74db6942da440fd5f80a36e867e272714ef6ef -->

# Astra Follow-up Addendum - 2026-10-23

Requested model: gpt-6-astra, effort high; provider-resolved identity unconfirmed.
Advisory static review, not verified defects or approval. No tests were authorized.
Original Fable report SHA-256: `e248e4dc7adb1b293e8919d91129578368368fa80ee771ab09297ece51e44f68`.
Captured source SHA-256: `923d42f0ba24a273c58a7d675ad5954513ea14035b6d1c22bc0f3022e18b8f01`.
Repository-relative citations refer to the captured source, not this findings directory.

## Scope and provenance

Repository: **edgar-cli**. Identities below are copied from `AUDIT-SCOPE.json:2` and `AUDIT-SCOPE.json:592`; they were not independently rehashed.

| Identity | Current | Previous |
|---|---|---|
| HEAD | `e2aa67eb9ba83c8f517b7a52a6e67eb5dacc2c7d` | `e2aa67eb9ba83c8f517b7a52a6e67eb5dacc2c7d` |
| Snapshot SHA-256 | `923d42f0ba24a273c58a7d675ad5954513ea14035b6d1c22bc0f3022e18b8f01` | `923d42f0ba24a273c58a7d675ad5954513ea14035b6d1c22bc0f3022e18b8f01` |

- Current capture: `2026-10-23T16:17:53.823Z`; dirty working tree: **true**.
- Original report SHA-256: `e248e4dc7adb1b293e8919d91129578368368fa80ee771ab09297ece51e44f68`.
- Inspected areas: TTM selection, ratio inputs and alignment, search scoping, filing high-water marks, 13F aggregation, delta/restatement filtering, and relevant documentation and tests as text.
- Citations include the captured `source/` prefix. The manifest describes a non-atomic working-file capture and says old source is unavailable (`AUDIT-SCOPE.json:597`).

**Recommendation:** retain Fable F02 as a priority, but correct its proposed truncation remedy. Next, address the period-selection and search-scoping defects below. These are static findings, not reproduced results.

## New findings

### ASTRA-F01 — An older quarterly window can suppress a newer TTM reconstruction

**Severity:** Medium. **Confidence:** High in the control flow; prevalence unmeasured.

- **Trigger:** The newest quarterly facts contain a gap, an older contiguous four-quarter window exists, and annual/YTD facts could reconstruct a more recent TTM.
- **Evidence:** `source/edgar/compute.py:134` scans progressively older windows until one is contiguous, then returns a value with empty caveats at line 149. `source/edgar/api.py:2922` attempts the annual/YTD reconstruction only when that value is `None`. Consequently, a valid older sum prevents consideration of a newer reconstruction.
- **Impact:** A command requesting trailing twelve months can return a historical twelve-month window despite having sufficient data for a newer one. The table/Markdown renderer omits `period_end` (`source/edgar/cli.py:960`), making the stale window harder to notice.
- **Safeguards/counter-evidence:** JSON retains `period_end` and input provenance. Quarterly continuity is checked; the problem is freshness and fallback selection, not summing across a gap. Existing tests cover a contiguous newest window and a four-row gap, but not an older valid window competing with a newer stub reconstruction (`source/tests/test_api.py:881`, `:1018`, `:1600`).
- **Smallest correction:** Require the quarterly window to end at the latest eligible quarter before treating it as the current TTM. Otherwise attempt the stub path. Alternatively, compare both candidates’ period ends and explicitly label any historical fallback.
- **Proposed regression test — NOT RUN:** Supply Q1 2026 = 150; Q3/Q2/Q1 2025 = 90/80/70; Q4 2024 = 60; and FY2025 = 400. Current selection can return 300 ending September 2025. Assert that `ttm()` instead reconstructs **480**, ending March 2026, using FY2025 + Q1 2026 − Q1 2025.

### ASTRA-F02 — Ratios combine flows from different periods without an alignment warning

**Severity:** Medium. **Confidence:** High for the conditional defect.

- **Trigger:** One flow alias has current facts while another has only older facts of the same period kind—for example, FY2025 revenue and FY2024 net income.
- **Evidence:** `source/edgar/api.py:3024` independently selects each flow’s latest fact. Line 3036 anchors balance-sheet facts primarily to revenue’s end date. Arithmetic then consumes the independently selected flows (`source/edgar/compute.py:253`, `:289`, `:415`). The warning collector checks only instant facts (`source/edgar/api.py:3074`).
- **Impact:** Margins, ROA/ROE, FCF and related ratios can combine different years while reporting one `flow_period_end` and an empty `alignment_warnings` list. This extends beyond the prior report’s discussion of mixed periods in the separate `metrics` command.
- **Safeguards/counter-evidence:** `period_type` and `as_of` are forwarded, and input provenance exposes dates. Those checks do not establish a common reporting interval. The separate statement builder already rejects mismatched period ends (`source/edgar/api.py:2293`), demonstrating that this safeguard exists elsewhere. Arithmetic tests exercise values and missing inputs, not cross-period orchestration (`source/tests/test_api.py:854`, `:873`).
- **Smallest correction:** Validate flow start/end compatibility before computing each affected ratio; suppress incompatible results with a specific mismatch explanation. An alternative is to select a common historical interval and label it explicitly.
- **Proposed regression test — NOT RUN:** Provide FY2025 revenue of 1,000, FY2024 net income of 100, and assets/equity aligned exactly to FY2025. Assert that net margin and ROA/ROE do not silently produce mixed-year values with empty alignment warnings. Include a same-period control.

### ASTRA-F03 — Failed ticker resolution silently removes search restrictions

**Severity:** Medium. **Confidence:** High in the request construction.

- **Trigger:** `search --tickers …` receives an unknown or ambiguous identifier, a resolver error, or a group that successfully expands to no identifiers.
- **Evidence:** `source/edgar/cli.py:1500` builds a CIK list but discards company-resolution errors. An empty list becomes `cik=None` for live search at line 1511; `source/edgar/api.py:2500` then omits the CIK restriction. Mirror searches likewise add a CIK predicate only for a truthy list (`source/edgar/mirror.py:363`, `:406`).
- **Impact:** An explicitly scoped search can search across all filers in the backend’s other limits and appear successful. Mixed valid/invalid selections silently lose unresolved members.
- **Safeguards/counter-evidence:** Explicit group-expansion errors already stop execution (`source/edgar/cli.py:346`); successful empty expansions and company-resolution errors bypass that protection. Returned matches contain company identifiers, but the command does not explain that its requested scope changed. Existing search tests exercise backend behavior, not this CLI resolution path (`source/tests/test_api.py:1747`, `:1781`).
- **Smallest correction:** Distinguish an omitted scope from an explicitly empty or failed scope. Reject failed resolution and empty explicit selections before invoking either backend. If partial results are intentional, require explicit partial-result metadata and never broaden an empty selection.
- **Proposed regression test — NOT RUN:** Stub resolution to return an error, then invoke search with and without `--db`. Assert a nonzero result and no backend search call. Repeat for an empty group and a mixed valid/invalid selection.

## Additions/corrections to Fable

### EDGAR-CLI-F01 — Challenge the claimed effect on weights and concentration

The unit heuristic remains present at `source/edgar/holders.py:109`. However, the claim that within-filer `weight` and `top_concentration` become 1,000× too large is not supported by the current arithmetic.

Every row in a filing receives the same multiplier. Weights divide each scaled value by the scaled total, and concentration divides the scaled top-position total by that same total (`source/edgar/holders.py:123`, `:137`, `:142`). The multiplier cancels.

**Status:** Narrow the impact statement; retain the absolute-dollar defect. Cross-filer dollar totals and rankings can still be distorted when filers receive different multipliers. This does not establish that the original source differed or that F01 as a whole was false.

**Proposed test — NOT RUN:** Scale every position in the existing concentration fixture by 1,000; assert unchanged weights/concentration and a 1,000× dollar total (`source/tests/test_api.py:1435`).

### EDGAR-CLI-F02 — Challenge the “oldest returned filing” remedy; strengthen the warning gap

Moving the mark to the **oldest returned** filing does not safely drain a newest-first truncated batch. If 60 unseen filings exist and only the newest 50 are returned, the oldest returned filing is still newer than the ten omitted filings. Advancing to that date excludes those ten on the next request.

The relevant selection, date exclusion and advancement remain at `source/edgar/api.py:668`, `:676`, `:739`; the state store refuses older or equal dates at `source/edgar/state.py:66`.

The warning safeguard is also narrower than Fable described: the truncation warning requires historical chunk metadata, `not all_history`, and a form/date filter (`source/edgar/api.py:723`, `:732`). Truncation can therefore advance state without even producing that warning.

**Status:** Strengthens the data-loss concern and challenges one proposed repair. Preserve the existing mark on incomplete retrieval; complete pagination or use a cursor representing a fully consumed interval before advancing. Inclusive boundary dates plus accession tracking remain necessary for same-day arrivals. Merely preserving the mark prevents loss but will replay the same newest batch until pagination is addressed.

**Proposed test — NOT RUN:** Drain 60 distinct-date filings with a batch size of 50 and assert eventual delivery of all 60. Add a later-arriving same-day accession. Test truncation with an empty historical-chunk list.

### EDGAR-CLI-F09 — Separate missing `delta --as-of` support from ignored `--since`

In the current source, `delta` has **no `as_of` parameter or CLI option** (`source/edgar/api.py:2764`; `source/edgar/cli.py:2011`). Thus its correction requires adding an interface or correcting the documentation; it cannot simply forward an existing argument.

There is a further lower-bound defect: `since` filters new filings at `source/edgar/api.py:2775`, but all restated periods from unbounded audit trails are appended at line 2783. Restatements entirely before `--since` can therefore recur in later deltas. State-based deltas likewise do not constrain that probe.

**Status:** Refines and strengthens F09. The `quality --as-of` concern remains supported by its unbounded call at `source/edgar/api.py:2066`.

**Correction/test proposal — NOT RUN:** Retain historical facts needed as comparison baselines, but filter emitted changes by the filing date when a changed value appeared. Test that a wholly pre-boundary restatement is excluded while an original before the boundary and changed filing after it remains detectable. Decide separately whether to implement the documented upper bound.

### Fable §6, “`search --tickers A,B` live path” — Stronger static support

This does not require live SEC behavior to establish the lost request scope: the CLI passes only `cik_list[0]` (`source/edgar/cli.py:1511`), and `search_efts` forwards that single value (`source/edgar/api.py:2500`). Both identifiers can resolve successfully and the second still never reaches the request.

**Status:** Upgrade from a runtime-dependent hypothesis to a statically supported request-construction defect; user-visible results remain untested.

The smallest containment is to reject multiple live identifiers explicitly. Full support would require a supported multi-CIK request or merged per-CIK requests with clear limit/deduplication semantics. A proposed CLI test should capture backend arguments for two valid identifiers; **NOT RUN**.

## Documentation deltas

| Documented assertion | Contrary or qualifying current source |
|---|---|
| README says TTM falls back to annual/YTD reconstruction for missing standalone Q4 (`source/README.md:179`). | An older successful quarterly sum blocks that fallback (`source/edgar/api.py:2922`; ASTRA-F01). This adds a behavioral qualification beyond Fable’s existing README/definitions disagreement. |
| Search help promises comma-separated identifiers “to scope the search” (`source/edgar/cli.py:1479`). | Live search retains only the first resolved CIK; failed/empty resolution can remove the restriction altogether (`source/edgar/cli.py:1500`; ASTRA-F03). |
| README lists `delta` among commands accepting `--as-of` (`source/README.md:196`). | Neither the command nor API method accepts it (`source/edgar/cli.py:2011`; `source/edgar/api.py:2764`). |
| Delta help promises filings and restatements “since a date” (`source/edgar/cli.py:2020`). | The date constrains filings only; historical restatement groups are appended without that filter (`source/edgar/api.py:2775`, `:2783`). |

## Coverage and limits

- Read `AUDIT-SCOPE.json` provenance/exclusions and the prior findings, hypotheses and documentation inventory. Inspected targeted slices of `source/edgar/{api,compute,cli,holders,mirror,state}.py`, relevant tests, README, definitions and rough-edge notes.
- Preserved the manifest’s exclusions: private/data-format content, oversized content, non-source formats, private/generated/vendored directories and omitted symlinks. Did not inspect `.uaf`, credentials, provider homes, external repositories or links. Saved `Insights/**` artifacts were not reviewed.
- No old source was available. Matching manifest identities are provenance, not an independent content verification. No claim is made that other Fable findings are fixed or fully revalidated.
- External `research_cli_base` behavior and live SEC data were unavailable. Financial examples above are proposed synthetic cases; actual filer prevalence and runtime outcomes remain unmeasured.
- Triage questions that could change the remedy: Should TTM permit a clearly labeled older window? Should search allow explicit partial scope resolution? Which filing-date boundary defines a restatement event for `delta`?

**No tests ran, no project code executed, and no files changed. No network access, state operations or delegated agents were used. Findings remain advisory pending triage; this addendum is neither verification nor human approval.**
