# Live Test-Drive Findings — 2026-07-23

Result of exercising ~25 commands against live SEC data (NVDA, AAPL, MSFT,
TSLA, BRK-A, @dow30). Most surfaces returned correct, well-provenanced data;
the items below are what broke or misled. Ordered worst-first within each
section. Check off as fixed; each item records the evidence so regressions
are reproducible.

## Bugs (misleading output)

- [x] **`ttm` silently misses interim quarters — understates TTM and asserts a false reason**
  - *Fixed:* `_ttm_stub_period` now draws stub candidates from the merged
    `ytd` + `quarterly` pools, anchors the current stub to a start within 7
    days of the FY end, and only claims "no interim filings" when no
    duration fact ends after the FY end. Live: NVDA TTM revenue 253,491M ✓,
    AAPL 451,442M ✓.
  - Evidence: `edgar ttm NVDA --bundle revenue` → `215,938M` with formula
    `"AnnualFY (no interim filings since FY end; TTM = latest FY)"`. But the
    Q1-FY2027 10-Q (period 2026-01-26..2026-04-26, filed 2026-05-20) exists.
    True TTM = FY2026 + Q1FY2027 − Q1FY2026 = 215,938 + 81,615 − 44,062 =
    **253,491M**. ~37B understatement stated with confidence.
  - Root cause: `EdgarClient._ttm_stub_period` (api.py) selects stub
    candidates with `period_type="ytd"` only. `period_type_from_days` classes
    110–300 days as `ytd`, so a 90-day Q1 fact is `quarterly` and never
    considered — yet a Q1 quarter *is* the 3-month YTD. Whenever the only
    interim filing since FY end is Q1, `future_ytd` comes back empty and the
    code emits the false "no interim filings" formula. (The 4-contiguous-
    quarters path can never fire for NVDA/AAPL/MSFT because Q4 is only tagged
    inside the 10-K as FY.)
  - Fix: include quarterly facts as stub candidates — current stub = latest
    duration fact with `end > annual_end` and `start` within ~7 days after
    `annual_end` (i.e. cumulative-since-FY-start), drawn from `ytd` +
    `quarterly` pools; prior stub = same-length fact ending 350–380 days
    earlier, same pools. Only claim "no interim filings" when no duration
    fact of any length ends after `annual_end`. Regression: NVDA-shaped
    fixture (FY + Q1-only interim) must return FY + Q1cur − Q1prior.

- [x] **`fiscal_period` labels comparative facts with the *filing's* period, not the fact's**
  - *Fixed:* `build_fiscal_grid` maps fiscal years to real date spans using
    each annual period's earliest-filed FY row (its own 10-K); facts are
    labeled by placing their `end` on that grid, extrapolating in ~91-day
    slots beyond either edge. Instant-only tags (balance sheet) reuse a
    grid learned earlier in the process or synthesize one from
    `submissions.fiscalYearEnd` with ±6-day 52/53-week tolerance. Live:
    NVDA comparative → `Q1-FY2026` ✓, AAPL 2025-09-27 assets → `FY2025` ✓,
    citations follow.
  - Evidence: in `edgar concept NVDA revenue`, the prior-year comparative
    from the Q1-FY2027 10-Q (2025-01-27..2025-04-27 = Q1 FY2026) is labeled
    `"Q1-FY2027"`; FY2025 revenue (2024-01-29..2025-01-26, 130,497M) is
    labeled `"FY2026"`. `--cite` embeds the wrong label into "agent-quotable"
    citations: `"NVIDIA CORP Q1-FY2027 10-Q"` for a Q1-FY2026 number.
  - Root cause: `fiscal_period_label` (api.py) reads `fact["fy"]`/`fact["fp"]`
    verbatim. In SEC companyfacts/companyconcept, `fy`/`fp` describe the
    **filing context** (fiscal period focus of the report the fact appeared
    in), not the fact's own period. Correct only when the fact is the
    filing's primary period. `calendar_period` is date-derived and already
    correct — proof the date path works.
  - Fix: derive the fiscal label from the fact's own dates + the filer's
    fiscal calendar: fiscal-year-end month from `submissions.fiscalYearEnd`
    (MMDD, already fetched/cached); FY label year = calendar year of the
    fiscal year containing `end` (`end.year + 1 if end.month > fye_month
    else end.year`); quarter = months from fiscal-year start to `end`,
    /3, for quarterly/ytd facts. Keep `fy`/`fp` passthrough fields raw
    (they are SEC truth about the filing) but stop deriving `fiscal_period`
    from them; fall back to fy/fp only when the fiscal calendar is unknown.
    Thread the FYE month through `enrich_fact_metadata`. Citation strings
    (`_format_citation`, cli.py) pick up the fix for free.

- [x] **`growth --basis qoq` never computes a real quarter-over-quarter rate**
  - *Fixed:* `compute.paired_growth_rates` pairs each fact with the nearest
    earlier fact inside a date-gap window (80–110d for qoq, 350–380d for
    yoy); unpaired facts are omitted and counted in `skipped_unpaired`, and
    each row carries `prior_period_end` + `gap_days` for auditability.
    `qoq` always sources quarterly facts (with a `note` when
    `--period-type` differs). Live: NVDA qoq rows all gap 91d; the
    Q4-hole quarter is excluded instead of bridged.
  - Evidence (a): `edgar growth NVDA --metric revenue --basis yoy,qoq`
    (default `--period-type annual`) → yoy and qoq blocks are byte-identical
    adjacent-row rates over annual facts. "QoQ" of annual data is
    meaningless and silently wrong.
  - Evidence (b): with `--period-type quarterly`, the latest "qoq" rate is
    43.2% spanning 2025-10-26 → 2026-04-26 — **two** quarters (Q4 FY2026 is
    never tagged standalone, so the series has a hole and adjacent-row math
    bridges it without complaint). `concept --deltas` already has a
    period-length guard; `growth` has none.
  - Root cause: `EdgarClient.growth` (api.py) computes one
    `compute.growth_rates(sorted_facts)` (adjacent-row) and reuses it for
    both `yoy` and `qoq`, over whatever `period_type` was loaded.
  - Fix: per-basis pairing. `qoq`: quarterly facts only (auto-select
    quarterly regardless of `--period-type`, or error with exit 5 if
    incompatible), pair rows only when the gap between period ends is ~90d
    (skip and annotate gaps: `"gap": true` or omit with a caveat). `yoy`:
    pair each fact with the fact ending 350–380 days earlier (works for
    both annual and quarterly series; for quarterly this is same-quarter
    prior-year, which adjacent-row math also gets wrong today). Depends on
    the dedup fix below landing first.

- [x] **`growth`/computed-metric series contain duplicate periods → spurious 0% rows**
  - *Fixed:* `_facts_for_alias` over-fetches 3× the requested window, keeps
    the latest-filed fact per `(start, end)`, and trims to `limit` distinct
    periods. Live: NVDA annual growth series is 7 distinct period ends.
  - Evidence: `edgar growth NVDA --metric revenue --basis yoy` rates list
    `period_end 2024-01-28` three times — value 60,922M then two
    self-comparisons at growth 0. Same period re-reported as a comparative
    in later 10-Ks is treated as three data points.
  - Root cause: `_facts_for_alias` (api.py) does no dedup by period;
    comparatives from successive filings survive. Compounding: `limit` is
    applied **before** dedup, so `--periods 8` yields ~3 distinct periods.
  - Fix: dedupe on `(start, end)` keeping the latest-filed fact (consistent
    with `latest_known_value` semantics) inside `_facts_for_alias`;
    over-fetch (e.g. 3×limit) then trim to `limit` distinct periods.
    Callers (ttm + its stub, trend, growth) all want distinct periods;
    `audit-trail` intentionally wants every filing and does not use this
    helper — verified it goes through `company_concept` directly.

## UX friction (agent ergonomics)

- [x] **Two field-naming conventions: SEC camelCase vs enriched snake_case**
  - *Fixed:* `alias_filing_row` adds snake_case aliases (`accession`,
    `filed`, `report_date`, `primary_document`, …) at the `_recent_filings`
    zip point — covering `filings`, `company`, `events`, `earnings.filing`,
    `pending`, `amendments`, `delta`, `latest_filing` — plus the exhibits
    envelope. CamelCase originals remain; `schema_version` bumped to 1.1.0.
  - Evidence: filing rows pass through SEC raw (`accessionNumber`,
    `filingDate`) while enriched facts use (`accession`, `filed`). Same
    logical field, two names; every consumer trips once per surface
    (I did, repeatedly, and an agent will too).
  - Fix: additive, non-breaking — enrich filing rows with snake_case
    aliases (`accession`, `filed`, `report_date`, `primary_document` …)
    alongside the raw passthrough, mirroring what fact enrichment already
    does (`accn` → `accession`). Applies to `filings`, `company`,
    `events`, `earnings.filing`, `pending`, `amendments`, `delta`.

- [x] **Primary row-list key varies per command; `edgar schema` covers only 6 commands**
  - *Fixed:* `PRIMARY_ROW_KEYS` maps all 39 data commands to their primary
    list field; `_output_schemas` generates a coarse envelope schema
    (marked `"coarse": true`) for every command without a hand-written one,
    each schema carries `primary_row_key`, and `edgar schema` with no args
    returns the full command → key map. CSV export's field priority list
    was aligned with the same keys.
  - Evidence: `companies` (search-companies), `results` (resolve),
    `facts` (concept), `metrics` (metrics/ttm), `matches` (search),
    `rows`/`top_positions` (holdings), `filings`, `events`, `lines`
    (statements), `transactions`/`insiders` (insiders). Guessing costs a
    round-trip per command; `schema` mitigates but `_output_schemas`
    (cli.py) only defines concept/metrics/ratios/filings/events/delta.
  - Fix: (1) extend `edgar schema` to every command (schema per envelope,
    even if coarse), and have `schema` with no args list command → primary
    row key. (2) Longer term consider a uniform `rows` alias next to the
    domain key (additive), gated on a schema_version minor bump.

- [x] **Same-CIK share classes trigger a false ambiguity error**
  - *Fixed:* `resolve_company` collapses candidates sharing one CIK and
    reports the classes in `share_class_tickers`. Live:
    `edgar holdings "Berkshire Hathaway"` resolves and returns the $263B
    portfolio.
  - Evidence: `edgar holdings "Berkshire Hathaway"` → `Error: Ambiguous
    company identifier; matches: BRK-B (0001067983), BRK-A (0001067983)` —
    both rows are the same filer. Any multi-class company by name hits this.
  - Fix: in `resolve_company` (api.py), collapse candidates when
    `len({c.cik}) == 1` (return the first, note both tickers). Keep the
    error for genuinely different CIKs.

- [x] **`--export-csv` only works before the subcommand**
  - *Fixed:* the flag is also registered on every data command through
    `output_options` (callback writes to `ctx.obj`, `expose_value=False`,
    so no command signature changed). Both positions verified live.
  - Evidence: `edgar concept NVDA revenue --export-csv out.csv` → usage
    error `No such option`; `edgar --export-csv out.csv concept …` works.
    README/backlog call it "global", but Click group options are
    position-sensitive and the failure gives no hint.
  - Fix: register `--export-csv` (and `--webhook`) on subcommands too via
    the shared `output_options` decorator writing into `ctx`, or at minimum
    add the placement hint to the README agent-quickstart and the error
    path. Prefer accepting both positions.

- [x] **Mirror metadata-mode search finds almost nothing and doesn't say why**
  - *Fixed:* `search_mirror` counts ingested bodies up front; when the
    search runs in metadata mode against a bodies-free mirror, the envelope
    carries a `hint` naming the exact remedy
    (`edgar mirror ... --with-bodies-for FORM`).
  - Evidence: after `edgar mirror NVDA --to test.sqlite --no-facts`,
    `edgar search "annual report" --db test.sqlite` → 0 matches, no
    explanation. The FTS metadata doc is only form + items + description +
    primary_document filename — "annual report" appears in none of them.
  - Fix: when `--db` search in metadata mode returns 0 (or always), include
    a `hint` field stating bodies are not ingested and the exact remedy
    (`edgar mirror … --with-bodies-for 10-K`). Consider adding company name
    + form description words to the metadata FTS doc so obvious queries hit.

- [x] **Live EFTS search: relevance-only ranking surfaces 2001 filings; `highlight` always empty**
  - *Fixed:* probed the live endpoint — EFTS returns no `highlight` key for
    any parameter (`hl`, `highlights`, `snippets` all ignored), so the
    always-empty field is dropped and the `_source` metadata that does
    exist (`file_type`, `file_description`, `period_ending`) is surfaced
    instead. With no `--since`/`--until`, a 5-year window is applied and
    disclosed via `applied_default_since` + `note` (override with
    `--since 2001-01-01`). Live: top hits are now 2022+, not 2001.
  - Evidence: `edgar search "supply chain constraints" --form 10-K` → top
    hits are Logility 10-Ks from 2001; every hit has `"highlight": []`.
  - Fix: (1) investigate the EFTS response contract for highlights —
    `search_efts` (api.py) reads `hit["highlight"]["text"]`, which EFTS may
    only populate for certain query forms; if unavailable, drop the field
    or synthesize a snippet from `_source`. (2) Add recency ergonomics:
    a `--sort filed` option if EFTS supports date sort, else default
    `--since` to a recent window with an explicit `applied_default` note
    (agents can override with `--since 1994-01-01`).

- [x] **`cache stats` reports the legacy flat TTL despite endpoint-aware TTLs**
  - *Fixed:* `EdgarCache.stats()` drops the inner flat `ttl`, reports
    `default_ttl` plus an `endpoint_ttls` policy table (pattern →
    ttl_seconds); the human table renders one row per policy.
  - Evidence: `edgar cache stats` → `"ttl": 900` (15 min), but
    `EdgarCache` applies per-endpoint TTLs (ticker map 7d, facts 1d,
    frames 90d, submissions 1h). The one number shown is the one that is
    never used as-is.
  - Fix: report the TTL policy table (url-pattern → ttl_seconds) plus
    `default_ttl`, and per-bucket entry counts if cheap. Rename the flat
    field to `default_ttl` to stop it reading as authoritative.

- [x] **Minor: `governance` proposal titles truncate at the first period**
  - *Fixed:* title capture is tempered so it cannot run into the next
    proposal marker; sentence-end detection tolerates corporate-suffix and
    initial periods; heading→body transitions ("… Plan The Board has…",
    "… Proposal Apple has been advised…") cut the title; lowercase openers
    (mid-sentence references) no longer claim a proposal number. Live: all
    5 AAPL proposal titles extract cleanly, including
    "Approval of the Apple Inc. Non-Employee Director Stock Plan, as
    Amended and Restated".
  - Evidence: AAPL DEF 14A proposal 4 title = `"Approval of the Apple Inc"`
    — sentence-split on the `.` in "Inc." mid-title.
  - Fix: make the title regex tolerant of corporate-suffix periods
    (Inc./Corp./Co./Ltd.) or capture to end-of-line instead of
    end-of-sentence. Context/offset fields already let agents verify.

## Suggested fix order

1. ~~Series dedup (`_facts_for_alias`)~~ — done.
2. ~~`ttm` stub-period quarterly candidates~~ — done.
3. ~~`fiscal_period` date-derived labels~~ — done.
4. ~~`growth` per-basis pairing with adjacency guard~~ — done.
5. ~~UX quick wins: same-CIK collapse, `--export-csv` placement, cache stats
   TTL table, governance title regex~~ — done.
6. ~~Larger UX: filing-row snake_case aliases, `schema` coverage for every
   command, mirror/EFTS search hints + highlight investigation~~ — done,
   shipped with the `schema_version` 1.0.0 → 1.1.0 minor bump.

Status: all 12 items fixed (4 bugs + 8 UX), 13 regression tests added
(123 passing), each fix verified against live SEC data.
