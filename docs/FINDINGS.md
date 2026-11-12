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

# Foreign-Filer Test-Drive Findings — 2026-11-11

First use of the CLI on a foreign private issuer: OMH (Ohmyhome Ltd, since
renamed Ohmyswift Limited; CIK 0001944902), a Singapore company that files
6-K, 20-F, F-3 and 424B5 rather than 8-K, 10-K and S-3. `filings`,
`company`, `resolve`, `exhibits --download`, `search` and `concept` with an
explicit tag worked. The items below did not. Same conventions as above:
checked off when fixed, evidence kept so regressions are reproducible. The
regression fixtures are under `tests/fixtures/omh/`; no test calls SEC.

## Bugs (misleading output)

- [x] **`metrics` returned a superseded figure and called it fresh**
  - *Fixed:* the `revenue` alias gained
    `RevenueFromContractWithCustomerIncludingAssessedTax`; `freshest_fact`
    picks the latest period end across all alias tags (then a framed fact,
    then the longer period, then the latest filing) and ignores a fact
    "filed" before its own period ended; `metric_staleness` makes `stale`
    true when the filer's latest periodic report covers a later period than
    the fact, or the fact ended more than 548 days before the reference
    date, with a `stale_reason`. Rule written down in
    [definitions.md](definitions.md). Live: `edgar metrics OMH --bundle
    revenue` → FY2025, 9,519,777 USD, 20-F `0001213900-26-048586`,
    `stale: true` ("Superseded: 6-K 0001213900-26-098819 ... period ending
    2026-06-30").
  - Evidence: `edgar metrics OMH --bundle revenue` → tag `Revenues`,
    2025-01-01..2025-06-30, 5,216,078 USD, from a 6-K filed 2025-12-02, with
    `age_days: 497` and `stale: false`. Full-year 2025 revenue, 9,519,777
    USD, was in the 20-F filed 2026-04-28 under
    `RevenueFromContractWithCustomerIncludingAssessedTax`. `edgar concept
    OMH revenue --canonical` also returned only the half-year fact.
  - Root cause: (1) that tag was not among the alias candidates; (2)
    `latest_distinct_fact` → `pick_comparable_facts` discarded every
    unframed fact whenever any framed fact existed, so "freshest" meant
    "freshest calendar-aligned"; (3) `stale` was only `age_days > 548`
    against the filer's latest filing date, so a half-year figure stayed
    "fresh" for 18 months regardless of what was filed after it.
  - Also found: the same 6-K tags a `2025-07-01..2025-12-31` context with
    the first-half value (5,216,078), filed 2025-12-02, before that period
    ended. It is a filer tagging error that SEC serves as-is; without the
    guard it ties with the real FY2025 fact on period end.

- [x] **`events` returned nothing for a 6-K filer, with exit 0**
  - *Fixed:* `events` reads 6-K as well as 8-K. A 6-K has no item numbers,
    so `edgar/sixk.py` classifies it from its own text (cover page, exhibit
    index and signature block removed) into 15 event types, each returned
    with the sentence it was classified from, plus accession and acceptance
    time. Every 6-K examined is returned (`event_types: ["other"]` and a
    `lead` sentence when nothing matches). When the 6-K itself says nothing
    its first EX-99 exhibit is read. An empty result now carries `hint` and
    `forms_filed` and exits 2. Live: `edgar events OMH` returns the 20 most
    recent 6-Ks, each classified; all 23 of the filer's 2026 6-Ks classify
    from saved copies.
  - Evidence: `edgar events OMH` → `events: []`, exit 0. The filer had 23
    6-Ks in 2026: a standby equity purchase agreement, two private
    placements, a registered direct offering, a 50-for-1 share
    consolidation, a Nasdaq bid-price deficiency and its cure, a divestiture,
    a name change and redomiciliation.
  - Root cause: `EdgarClient.events` asked `submissions` for `form="8-K"`
    only and derived event types from 8-K item numbers.
  - Classifier rules worth knowing: a sentence opening "As previously
    disclosed" is a recap — its type is dropped when the report also makes a
    fresh statement, and flagged in `background_types` when the recap is all
    there is; a reverse split mentioned as a possible cure ("including by
    effecting a reverse stock split, if necessary") is not an event;
    "entered into an agreement" is not reported separately when the same
    sentence is already a financing or divestiture.

- [x] **`insiders` returned zero for a filer with Form 3s, with exit 0**
  - *Fixed:* `insiders` reads Forms 4, 5 and 3. Form 3 initial statements
    come back in `initial_statements` with holdings or `no_securities_owned`;
    `forms_found` counts the insider forms in the window; with no
    transactions a `hint` says what was found; with no insider forms at all
    the command exits 2 and names the forms the filer does file.
  - Evidence: `edgar insiders OMH --since 2026-01-01` → zero transactions,
    exit 0, although five Form 3s were filed in 2026. (They declare no
    securities owned, so zero transactions is the right number — but the
    answer did not say that the forms exist.)
  - Root cause: only `form="4"` was fetched and an empty list was returned
    without explanation.

## Gaps (missing capability)

- [x] **Does not start on Windows; does not install on a clean machine**
  - *Fixed:* `edgar/cache.py` locks with `flock` on POSIX and
    `msvcrt.locking` on Windows (no-op when neither exists) and writes
    entries with `os.replace`; `pyproject.toml` depends on
    `research-cli-base @ git+https://github.com/baba20o/research-cli-base`.
    Verified: fresh venv, `pip install -e ".[dev]"`, `edgar --help` and the
    full suite on Windows (Python 3.12) and WSL Ubuntu (Python 3.10).
  - Evidence: `import fcntl` at module import → `ModuleNotFoundError` on
    Windows. `pip install -e .` → "No matching distribution found for
    research-cli-base" (the package is not on PyPI).
  - Second Windows defect behind the first: `tmp.rename(path)` raises
    `FileExistsError` on Windows when the cache entry already exists, so
    every cache refresh would have failed.
  - `research-cli-base` was checked for the same problems: it has no
    `fcntl`, but `FileCache.set` uses the same `Path.rename`. `edgar` never
    calls it (it writes through `EdgarCache`), so nothing was changed there;
    other CLIs built on the base package will hit it on Windows.

- [x] **Nothing returned a filing's text except `item` (10-K/10-Q only)**
  - *Fixed:* `edgar text` returns the main document of any filing, and
    exhibits by type on request, as clean text with a size cap
    (`--max-chars`, `--offset` / `next_offset`) and `--grep` to pull only
    the sentences matching a pattern (`--context N` for neighbours).
    `edgar/filing_text.py` does the conversion and sentence splitting and is
    shared with the 6-K classifier and the dilution view.
  - Evidence: reading a 6-K meant `exhibits --download` and stripping HTML
    by hand.

- [x] **No dilution view**
  - *Fixed:* `edgar dilution` gathers, for one filer, the latest stated
    shares outstanding, shelf registrations with effectiveness and takedowns,
    prospectus supplements, offerings, equity lines and at-the-market
    programs, warrants (count, exercise price, price floor, multiple-share
    exchange features), reverse splits and going-concern language. Each
    statement is a sentence of a named filing; each figure carries the text
    it was parsed from; nothing is combined, converted across a split, or
    inferred.
  - Traps found in the source documents and handled: a 424B5 bundles the
    base prospectus after a bare `PROSPECTUS` heading, with its own, older
    "shares issued and outstanding" sentence (only the shelf size is read
    from that part); a public-float paragraph can quote a pre-split share
    count with its own "as of" date (statements are ordered by that date,
    else the filing date); a par value is not a price; the XBRL cover count
    can predate a reverse split (flagged in `xbrl_shares_outstanding.note`).

## SEC-side behaviour (documented, warned about)

- [x] **SEC's XBRL API omits a filing that carries XBRL financial
  statements**
  - Finding: it is SEC's side, not the CLI's. The 6-K filed 2026-09-10
    (accession `0001213900-26-098819`, period 2026-06-30) is flagged
    `isXBRL: 1`, `isInlineXBRL: 1`, `isXBRLNumeric: 1` in the filing list,
    and its extracted instance document (`ea0304832-6k_ohmyhome_htm.xml`)
    holds 608 numeric facts, 294 of them dimensionless in `us-gaap/2026` —
    for example first-half 2026 revenue of 1,915,166 USD
    (`RevenueFromContractWithCustomerIncludingAssessedTax`, 2026-01-01 to
    2026-06-30) and total assets of 3,191,309 USD at 2026-06-30. Yet the raw
    `companyfacts` payload for the CIK, fetched cold on 2026-11-11, contains
    no fact filed after 2026-04-28. The CLI passes through everything SEC
    serves. The year-earlier 6-K (`0001213900-25-117227`, `us-gaap/2025`)
    is in the API, so this is not a blanket 6-K exclusion; the cause on
    SEC's side was not established.
  - *Fixed (warning):* `metrics`, `concept` and `facts` add a `warnings`
    entry, code `xbrl_filing_missing_from_sec_api`, when the newest report
    with XBRL financial data in the filing list is newer than every fact in
    hand and its accession is absent from `companyfacts` (fetched only when
    the cheap check trips). `metrics` also marks the affected facts `stale`,
    because that 6-K counts as the latest periodic report. `--as-of` queries
    do not warn: they exclude later filings on purpose.
  - Not done: reading the figures from the filing's own instance document.
    See the follow-up in [BACKLOG.md](BACKLOG.md).

## UX friction

- [x] **`company` and `brief` carry every filing field twice**
  - *Fixed:* `--compact` on `company`, `filings` and `brief` drops each
    camelCase key that has a snake_case alias on the row, and empty values,
    from JSON and NDJSON output (`company OMH`: 12.8 KB → 8.9 KB). Opt-in;
    the default keeps the shape published in schema_version 1.1.0.

- [x] **`acceptanceDateTime` has no stated time zone**
  - Checked: the filing list's value is UTC (`2026-11-09T22:00:10.000Z`
    for a filing the index page shows as accepted `2026-11-09 17:00:10`
    Eastern). `events` passes it through as `acceptance_datetime`.

Status: 9 items closed, 78 tests added (260 passing on Windows and WSL),
each fix verified against live SEC data on 2026-11-11.
