# EDGAR CLI Backlog

This tracks test-drive feedback and the next command ideas. Priorities favor
misleading-output fixes before convenience features.

## Last Shipped — 13F Holders, Item Extraction, Governance

The remaining "deferred multi-day projects" called out as high-value:

**13F holdings + holders**
- New `edgar/holders.py`: parses 13F infoTable XML (namespace-agnostic).
- `edgar holdings IDENTIFIER --quarter 2025Q4 --top N` — single filer's
  holdings + top concentration. Live: Berkshire Q4 2025 = $274B portfolio,
  AMEX 20.1% / AAPL 8% / KO 7.2%.
- `edgar holders TICKER --candidates @group --cusip CCCCCCCCC` — given a
  candidate institutional list, find which hold the issuer (matches by
  name-substring, exact CUSIP via flag).
- Auto-detects 2023 SEC schema change (value reporting moved from
  thousands→absolute USD) by magnitude heuristic per filing; tags every row
  with `value_unit_convention`.
- Locates the right `INFORMATION TABLE` doc by `doc_type` (filename varies
  per filer, e.g. `infotable.xml` vs `50240.xml`).

**Item-level 10-K/10-Q extraction**
- New `edgar/items.py`: heuristic Item-header detection in plain-text body.
- `edgar item TICKER --form 10-K --section "Risk Factors"` — extracts one
  Item with its full body text.
- Discriminates real headers from TOC entries and inline cross-references
  using three signals: (1) gap to next Item marker (TOC entries cluster
  tightly), (2) inline preceding context (`see ...`, `under ...`, lowercase
  letters), (3) canonical title following the marker. Picks satisfy
  monotonic canonical ordering.
- Live verified: NVDA 10-K Item 1A returns 115K-char Risk Factors section
  starting at the actual header, not a back-reference.
- `--db PATH` reads from a previously-mirrored body if present, avoiding
  the SEC round-trip.

**DEF 14A governance (heuristic, with caveats)**
- New `edgar/governance.py`: pattern-based extractors for audit fees,
  board size, shareholder proposals, NEO-title mentions.
- `edgar governance TICKER --year 2025` returns each field with its
  `context` (matched sentence) so agents can verify before relying on it.
- Honest about limits: total executive compensation tables vary too much
  across filers for a robust default extractor; the `caveat` field says so.
  Audit-fee patterns work for filers that use the common labeled-row
  format; tabular formats may need filer-specific extractors.

Tests: 108 passing (was 100, +8 new). Regressions cover infoTable XML
parsing under both unit conventions, filer aggregation + concentration,
Item canonical-order detection, inline-cross-reference rejection,
title-based item resolution, audit-fee + board-size extraction.

## Previously Shipped — Filing Bodies, Insiders, Per-Share, CSV Export

Builds directly on the previous tranche's mirror foundation.

**Filing-body indexing (the 10x of the 10x)**
- New `filing_bodies` + `filing_bodies_fts` tables in the mirror schema.
- `edgar mirror IDENTIFIERS --to PATH --with-bodies-for FORM --bodies-limit N`
  fetches each filing's primary document, strips HTML to plain text, and
  indexes into FTS5 with a 4 MB-per-document byte cap (truncation tracked).
- `edgar search QUERY --db PATH --mode auto|bodies|metadata` runs FTS5 over
  filing text. Returns SEC-style highlighted snippets (e.g. `« supply chain »`).
- Live verified: `mirror NVDA --with-bodies-for 10-K --bodies-limit 5`
  ingests 5 filings in 2.3s; `search "supply chain" --db ...` returns 3 hits
  with year-over-year language drift visible.

**Form 4 insider aggregation**
- New `edgar/insiders.py` module: parses Form 4 XML (using
  `xml.etree.ElementTree`), maps transaction codes (P/S/A/M/...) to
  semantic categories.
- `edgar insiders TICKER --since YYYY-MM-DD --max-fetch N` aggregates by
  reporting owner + transaction code, returns acquired/disposed shares and
  dollar value totals plus a per-insider rollup.
- Handles SEC's stylesheet-rendered URL pattern (`xslF345X*/wf-form4_*.xml`)
  by stripping the stylesheet directory to find the raw schema-conformant XML.
- Live verified: NVDA last 8 Form 4s show $239M net insider selling, with
  Jensen Huang ($79.7M), Ajay Puri ($67.3M), and Mark Stevens ($38.5M) as
  the top sellers.

**Per-share metrics**
- Three new derived metrics with formula+provenance: `bvps`, `fcf_per_share`,
  `sales_per_share`. Wired into `edgar ratios` automatically.
- NVDA FY26: BVPS $6.47, FCF/share $3.98, Sales/share $8.89.

**Concept incremental queries**
- `edgar concept --since YYYY-MM-DD` filters to facts filed on/after that
  date, paralleling the existing `--as-of`. Threads through
  `company_concept` and `company_concept_alias`.

**CSV export**
- New global `--export-csv PATH` flag writes the primary tabular slice of
  any command's result to CSV. Auto-picks the best list-of-dicts field
  (facts/matches/filings/concepts/events/ratios/metrics/transactions/...).
- Non-scalar cells are flattened to JSON strings so the CSV stays well-formed.
- Lives entirely outside the existing JSON path — agents that don't pass
  `--export-csv` see no behavior change.

Tests: 100 passing (was 93). New regressions cover Form 4 XML parsing,
transaction aggregation, body ingestion + truncation, FTS body search, the
per-share metrics, and the CSV export end-to-end through the CLI.

## Previously Shipped — 10x Features and Fall-Short Closeout

This pass shipped the items I called out as the qualitative gap and the real
limitations (excluding market data, which is being handled separately).

**SQLite mirror + search**
- New `edgar/mirror.py` module: schema for filers/filings/facts/documents
  plus `filings_fts` (FTS5) virtual table.
- `edgar mirror IDENTIFIERS --to ./edgar.sqlite` for incremental ingestion of
  submissions + companyfacts + (optionally) per-filing document indices.
  Re-runs are deduplicated by `(cik, accession)`.
- `edgar search QUERY --db PATH` searches the local FTS5 index over filing
  metadata (form, items, description, primary_document).
- `edgar search QUERY` (no `--db`) wraps SEC's live EFTS service for content
  search across all filers, including `--tickers`/`--form`/`--since`/`--until`.

**Per-fact restatement back-population**
- `EdgarClient._populate_restatement_state` walks same-period siblings on
  every `concept` call; older facts whose value differs from the latest are
  flagged `is_restated=True` with a `superseded_by` accession pointer.
- New fields on every fact: `is_restated`, `superseded_by`,
  `latest_known_value`, `prior_values`. `restated_facts_in_window` summary
  added to the response envelope.

**Trend change-point detection (real signal)**
- `compute.detect_change_point` replaced the naive slope/threshold label
  with a slope-difference binary segmentation. Linear growth no longer
  produces spurious change points; real trend shifts (NVDA revenue
  accelerating in Q1 FY26) get correctly labeled.
- New labels: `accelerating`, `decelerating`, `re-decelerating`,
  `inflecting`, in addition to `expanding`/`contracting`/`stable`.
- Trend summary now includes `change_point_end` and `segment_slopes`.

**Canonical alias expansion**
- 18 new aliases: `accounts_receivable`, `accounts_payable`,
  `deferred_revenue`, `accrued_liabilities`, `income_tax_expense`,
  `deferred_tax_assets`, `deferred_tax_liabilities`,
  `operating_lease_liabilities`, `operating_lease_rou_assets`,
  `depreciation`, `amortization_intangibles`, `stock_compensation`,
  `investing_cash_flow`, `financing_cash_flow`, `dividends_paid`,
  `share_repurchases`, `interest_expense`, `goodwill`, `intangibles`,
  `retained_earnings`, `shares_outstanding`.

**Statements / quality / verify / dashboard**
- `edgar statements TICKER --statement income|balance|cash` composes the
  canonical aliases into a normalized financial statement envelope per
  period, including a `coverage` percentage and per-line provenance.
- `edgar quality TICKER` returns earnings-quality flags: `accruals_ratio`,
  `ocf_to_ni`, `ar_to_revenue`, `sbc_to_revenue`, `inventory_days`,
  `restatement_count_recent`. Each flag has a formula, threshold, and
  `flagged: bool`.
- `edgar verify TICKER` performs cross-statement consistency checks:
  EPS↔NI/shares (5% tolerance), GP↔Revenue−COGS (1% tolerance). Each check
  returns expected/actual/delta/tolerance/passed.
- `edgar dashboard TICKER` is a one-call composite: profile + 7 metrics +
  8 ratios + 5 events + earnings highlights + quality flags. Uses the
  existing primitives so new commands automatically flow through.

Tests: 93 passing (was 85 before this push).

## Previously Shipped — Full End-to-End Sweep

This pass closed every tractable backlog item and added clear deferral notes
for the genuinely multi-day projects.

**Cache & state**
- `EdgarCache` wrapper with endpoint-aware TTLs (ticker map 7d, companyfacts/concept 1d, frames 90d, submissions 1h, historical chunks 7d) + negative caching + atomic writes + `fcntl` cross-process locks + `--cache-max-mb` LRU eviction.
- Conditional `GET` via `If-None-Match` / `If-Modified-Since`. `304` refreshes timestamp without re-download.
- `cache stats`, `cache invalidate '*CIK0000320193*'`, `cache warm --tickers ...` commands.
- `cache` summary in every JSON envelope.
- `StateStore` at `~/.edgar/state.json`: high-water marks per `(cik, form)`, plus `subscribe`/`unsubscribe`/`mark_seen` primitives.

**Envelope & composability**
- `schema_version` + `cli_version` on every JSON envelope.
- `--cite` flag attaches agent-quotable citations to rows.
- `--webhook URL` global flag (POSTs result JSON, fire-and-forget).
- `--explain` flag on `concept` adds resolution trace.
- `edgar schema [COMMAND]` returns JSON Schemas for `concept`/`metrics`/`ratios`/`filings`/`events`/`delta`.
- `edgar resolve` for batch ticker/CIK/name resolution with ambiguity metadata.
- `edgar diff IDENT_A IDENT_B -c CONCEPT` for period-aligned cross-filer diff.

**Discovery & universe**
- `edgar tags PHRASE` (positional) searches a reference filer's reported tags.
- `edgar frames --since YYYY` enumerates plausible frame strings.
- `--major` / `--insider` / `--institutional` form-class filters on `filings` and `company`.
- `@dow30`, `@sic:NNNN` group expansion in `--tickers` everywhere.
- `edgar dei TICKER` for entity metadata; `edgar peers TICKER --candidates @dow30` for SIC-matched peers.
- `edgar concept-info ALIAS_OR_TAG` shows candidate tags + label + freshness.

**Point-in-time & audit**
- `--as-of YYYY-MM-DD` on `concept` (and through `concept_alias`, `ttm`, `ratios`, `trend`, `growth`, `reconstruct`, `delta`) — eliminates look-ahead bias.
- `--canonical` flag on `concept` unions across all candidate tags (deduped on `(start, end, accn)`).
- `edgar audit-trail TICKER -c CONCEPT --period CYxxxx` lists every filing reporting a fact, with restatement detection.
- `edgar amendments TICKER --since DATE` pairs primary filings to their `/A` amendments.
- `edgar delta TICKER [--use-state]` returns `{new_filings, restated_facts, summary}`.
- `edgar subscribe add|remove|list` + `edgar pending` (incl. `--ndjson`) + `edgar mark-seen` standard queue semantics for delta-driven agents.

**Computed metrics (formula + provenance)**
- `edgar ttm TICKER --bundle ...` — sum of last 4 quarterly facts per metric.
- `edgar ratios TICKER --period-type {annual|quarterly|ytd}` — 14 canonical ratios. Each returns `{value, formula, inputs:[{tag,val,source_url,as_of}], caveats, missing_inputs}`. Out-of-scope ratios (P/E, EV/EBITDA, dividend yield) explicitly listed under `not_applicable`.
- `edgar trend TICKER -c METRIC --periods N` — slope + categorical label (`expanding`, `contracting`, `stable`, `inflecting`).
- `edgar growth TICKER -c METRIC --basis yoy,qoq,cagr3,cagr5`.
- `edgar reconstruct TICKER -c {ebitda|fcf|net_debt|nwc|tangible_book}`.
- Predefined `--bundle` group names: `income-statement`, `balance-sheet`, `cash-flow`, `liquidity`, `capital-structure`, `quality`.
- Canonical concept map expanded: cogs, gross_profit, equity, dna, capex, assets_current, liabilities_current, inventory, short_term_debt.
- Canonical definitions documented in [`docs/definitions.md`](definitions.md).

**Quality / narrative**
- `extract_earnings_highlights` now skips table-dump sentences (column-label patterns, high digit/letter ratio, "Three Months Ended" markers).

Tests: 74 passing (was 41 at start of this push). Each phase backed by regression tests.

### Deferred — multi-day projects

The following items were intentionally not shipped in this pass because each
is genuinely a dedicated project, and stub implementations would mislead
agents on edge cases. Each has a brief note on what would be needed.

- **`edgar mirror TICKER --to ./edgar.sqlite`** — local SQLite ingestion + incremental refresh + index strategy. Multi-day; needs schema design and ETag-aware refresh. Foundation for `edgar search`.
- **`edgar search "phrase"`** — depends on mirror or wraps SEC EFTS. EFTS wrapper is the easier path but needs careful URL building + result normalization.
- **`edgar governance TICKER --year 2025`** — DEF 14A proxy parsing. Hundreds of pages of unstructured HTML; board comp, exec comp, audit fees vary wildly per filer. Real ML/parsing project.
- **`edgar item TICKER 10-K --section "Risk Factors"`** — Item-aware 10-K HTML parsing. Item structure is conventional but actual section boundaries differ. Needs a per-form section parser with caveats.
- **`edgar changes TICKER --since DATE`** — section-level diff across consecutive filings. Depends on item extraction.
- **`edgar insiders TICKER --since 90d`** — Form 4 XML pipeline + transaction code semantics + per-insider aggregation.
- **`edgar holders TICKER --quarter 2025Q4`** — 13F XML aggregation joining holdings across filings.
- **`edgar statements TICKER --statement income --period CY2025`** — full canonical income/balance/cash flow with all edge cases. The `metrics --bundle income-statement` group provides 80% of the line items today.
- **`edgar quality TICKER`** and **`edgar verify TICKER`** — earnings-quality flags (accruals, OpCF/NI divergence, working-capital swings) and cross-statement consistency. Need accurate per-filer canonical mappings; a v1 with caveats could ship but punted to keep trustworthiness high.
- **Restatement detection during cache refresh** — diff prior cached `companyfacts` against new value. Hooks into `EdgarCache.set()`. Worth shipping but has subtle correctness implications (must not double-count fact moves).
- **`is_restated` / `superseded_by` walking** — currently surfaced via `audit-trail`'s `restated_periods`, but not back-populated onto every fact returned by `concept`. Needs a bulk pre-walk.
- **`--since-last-fetch` on `concept` / `metrics` / `brief`** — these aren't form-keyed, they're concept-keyed; needs a different state model (per-(cik, alias) high-water by `filed`).
- **`edgar dashboard TICKER`** — composes existing commands but its value depends on mirror+search to do well; deferred until those land.

---

## Correctness

- [x] Encode taxonomy/tag/frame path segments so `/` and `..` cannot change the requested SEC URL.
- [x] Reject blank company identifiers and blank company search queries.
- [x] Avoid silent empty filing tables when a form/date filter has no matches.
- [x] Support full filing history by fetching historical chunks from `filings.files[]` with an explicit `--all` flag and progress warning.
- [x] Suggest similar company XBRL tags when `concept` returns 404.
- [x] Try fallback tags for common concept aliases so `brief` and `compare` do not silently show stale migrated-tag facts.
- [x] Return CLI errors instead of tracebacks for invalid exhibit CIKs and failed download paths.
- [x] Route exhibit downloads through the SEC-aware client user agent and shared rate limiter.
- [x] Align compare output on shared frames and one period kind.
- [x] Add normalized period metadata to concept/frame facts.
- [x] Add provenance to concept/frame facts: `source_url`, `accession`, `filed`, and `as_of`.
- [x] Use stable agent-friendly exit codes for no-data, rate-limit, outage, and validation failures.
- [x] Map unknown ticker/company resolution failures to the no-data exit code.
- [x] Report unknown `metrics --bundle` aliases explicitly without wasting a SEC concept lookup.

## Output UX

- [x] Format numeric values for human output while keeping JSON raw.
- [x] Show `start`, `end`, and `frame` in concept tables.
- [x] Keep XBRL concept tags untruncated in rich tables.
- [x] Hide filing URLs in default tables; keep them in JSON and expose with `--show-urls`.
- [x] Add width-aware rich table layouts or make markdown the recommended agent mode in help text.
- [x] Add YoY/QoQ deltas in concept output.
- [x] Skip delta calculations across mismatched period lengths.
- [x] Show event snippets in rich output.
- [x] Add metric freshness to `brief`.
- [x] Default to JSON when stdout is not a TTY.
- [x] Add `--ndjson` streaming output for primary row sets.
- [x] Add `--tickers`, `--input FILE`, and `--batch` stdin for core research fan-out commands.

## Filing Workflow

- [x] Add `--start-date` and `--end-date` filters to `filings` and `company`.
- [x] Add `edgar open IDENTIFIER --form 10-K` to open the latest filing index.
- [x] Add `edgar exhibits ACCESSION_OR_URL` to list/download filing exhibits.

## Research Commands

- [x] Add `edgar brief TICKER` for profile, latest filings, key facts, and notable events.
- [x] Add `edgar earnings TICKER` to parse latest Item 2.02 8-K / Exhibit 99.1.
- [x] Add `edgar events TICKER` for reverse splits, CEO/CFO changes, delisting notices, acquisitions, debt events, and guidance updates.
- [x] Add `edgar compare AAPL MSFT GOOGL --concept Revenues --periods 4`.
- [x] Add `edgar metrics TICKER --bundle revenue,net_income,cash,debt,shares`.

## Agent-First Next

- [x] Add stdin and `--input FILE` batch support for `concept`, `metrics`, and `brief`.
- [x] Promote multi-ticker concept queries: `edgar concept --tickers AAPL,MSFT,GOOGL revenue --annual`.
- [x] Add JSON schemas and `edgar schema COMMAND` so agents can validate outputs without sampling live SEC data.
- [x] Add `--explain` traces for concept resolution: candidate tags tried, freshness score, period filters, and why the winning fact was chosen.
- [x] Add `edgar resolve` for batch ticker/CIK/name resolution with ambiguity metadata.
- [x] Add cache observability commands: `edgar cache stats`, `edgar cache warm`, `edgar cache invalidate`. Cache-key + summary already exposed in every envelope.

## Normalization Layer

- [x] Add `edgar concept --canonical` as an explicit canonical-union mode across all known fallback tags.
- [x] Expand canonical concept maps for income statement, balance sheet, cash flow, share count. Per-share normalizations + segment metrics still pending.
- [ ] Deduplicate restated facts and expose `is_restated`, `superseded_by`, and restatement lineage at the per-fact level. (Surfaced via `audit-trail` already; not yet back-populated onto every fact.)
- [x] Add `--annual`, `--quarterly`, `--ytd`, `--instant` period filters. `--duration-days` and `--latest-per-period` still pending.
- [x] Add `concept-info ALIAS_OR_TAG` for candidate tags, units, label, freshness in a reference filer.

## Batch And Statements

- [x] Add predefined bundles: `income-statement`, `balance-sheet`, `cash-flow`, `quality`, `liquidity`, `capital-structure` (consumed via `metrics --bundle GROUP`).
- [x] Add `edgar statements TICKER --statement income|balance|cash --period-type annual|quarterly` for normalized statement JSON with provenance + coverage %.
- [ ] Add `edgar history TICKER revenue --periods 20 --annual` for deduplicated time series. (Closely related to `edgar trend` and `edgar concept` with period filters; standalone command not yet added.)
- [x] Add `edgar peers TICKER --candidates @group --by sic --limit 10` (SIC-matched against a candidate set; SEC ticker map lacks SIC so dynamic universe scan would be expensive).
- [x] Add `--export-csv PATH` global flag that writes any command's primary tabular slice to CSV. SQLite export deferred (`mirror` is the natural place for a SQLite write; ad-hoc export less critical).

## Filing Text And Ownership

- [x] Add `edgar item TICKER --form 10-K --section "Risk Factors"` for Item-level extraction. Heuristic — uses three signals (gap to next marker, inline-context check, title-following) plus canonical-order monotonicity to skip TOC entries and back-references. Optionally reads from a mirrored body via `--db PATH`.
- [ ] Add `edgar changes TICKER --since YYYY-MM-DD` to diff same-form sections. *(Deferred — depends on item extraction.)*
- [x] Add `edgar insiders TICKER --since YYYY-MM-DD` to parse Form 4 XML and aggregate by reporting owner + transaction code. Strips SEC's stylesheet wrapper to find raw XML; maps codes to semantic categories.
- [x] Add `edgar holdings IDENTIFIER --quarter 2025Q4` (single 13F filer's holdings + concentration) and `edgar holders TICKER --candidates @group --cusip ...` (cross-filer search for holders of an issuer). Handles SEC's 2023 value-reporting schema change automatically.
- [x] Improve earnings narrative extraction so table dumps do not become giant unreadable highlights.

## Local Research Moat

- [x] Add `edgar mirror IDENTIFIERS --to ./edgar.sqlite` for incremental local submissions+facts ingestion. Filing-body ingestion via `--with-bodies-for FORM` shipped (4 MB/document cap with truncation tracking).
- [x] Add `edgar search QUERY [--db PATH | live EFTS]` for full-text search across filings. Mirror path uses FTS5 over metadata or filing-body text (`--mode auto|bodies|metadata`); default path wraps SEC EFTS.
- [x] `edgar watch` is covered by `edgar subscribe add` + `edgar pending` + `edgar mark-seen` triple, with state in `~/.edgar/state.json`.
- [ ] Add offline-first mode that refuses live SEC calls and reports cache misses cleanly.

## Point-In-Time And Audit

- [x] Add `--as-of YYYY-MM-DD` to every data command (concept, ttm, ratios, trend, growth, reconstruct, delta).
- [x] Add `edgar audit-trail TICKER --concept ALIAS --period CY2024` with restatement detection.
- [x] Walk same-period sibling facts to populate `is_restated`, `superseded_by`, `latest_known_value`, and `prior_values` on every fact returned by `concept`.
- [x] Add `edgar amendments TICKER --since YYYY-MM-DD` to chain primary filings to their `/A` amendments. (Reports the chain; section-level diff between amendment and primary is part of the deferred `edgar changes`.)

## Discovery

- [x] Add `edgar tags "deferred revenue"` against a reference filer's reported tag set (positional query).
- [x] Add `edgar frames --since YYYY` to enumerate plausible XBRL frame strings.
- [x] Add semantic form-class filters: `--major`, `--insider`, `--institutional` on `filings` and `company`.
- [ ] Add `edgar concept --segments` for segment-axis breakdowns. *(Deferred — needs XBRL dimension parsing.)*

## Universe And Groups

- [x] Support `@dow30` and `@sic:NNNN` group expressions in `--tickers` everywhere. *(`@sp500` / `@nasdaq100` deferred — would need a maintained list with caveats about index changes.)*
- [ ] Extend `edgar peers TICKER --by` with `financial-similarity` (k-nearest neighbors). *(Deferred — needs revenue/assets/margin computation across candidate set.)*
- [x] `edgar resolve` accepts `@group` syntax and returns concrete CIK lists.

## Filer Intelligence

- [x] Add `edgar dei TICKER` for entity-level metadata (filer status, fiscal year end, addresses, former names).
- [x] Add `edgar dashboard TICKER` composite envelope (profile + metrics + ratios + events + earnings + quality).
- [x] Add `edgar governance TICKER --year 2025` DEF 14A extraction for audit fees, board size, shareholder proposals, NEO titles. Heuristic — every field returns its `context` (matched sentence) for agent verification. Total executive compensation tables explicitly out of scope (filer markup varies too much for a default extractor).
- [x] Add `edgar verify TICKER --period-type ...` cross-statement consistency checks (EPS↔NI/shares, GP↔Rev−COGS).

## Composability

- [x] Emit a `schema_version` and `cli_version` on every JSON envelope so agents can pin against schema breaks across CLI upgrades.
- [x] Add `edgar diff IDENT_A IDENT_B -c CONCEPT` for period-aligned cross-filer diff. (Two-filers-one-concept case shipped; two-filings-one-form variant deferred until `edgar item` lands.)
- [x] Add a `--cite` flag that prefixes each row with an agent-quotable citation string.
- [x] Add `--webhook URL` to fire-and-forget POST result JSON.

## Computed Metrics

Foundation principle: every derived number returns `{value, formula, inputs:[{tag, val, source_url, as_of}], caveats:[...]}`. Without formula + provenance the CLI hides the math from agents, which is worse than not computing it.

- [x] Add `edgar ttm TICKER --bundle` for trailing-twelve-months reconstruction (4-quarter sum). Composes with `--as-of`.
- [x] Add `edgar ratios TICKER --period-type ...` returning the canonical ratio set with formula + source facts.
- [x] Add `edgar trend TICKER -c METRIC --periods N` with slope and a categorical label.
- [x] Add `edgar growth TICKER -c METRIC --basis yoy,qoq,cagr3,cagr5`.
- [x] Add `edgar reconstruct TICKER -c {ebitda|fcf|net_debt|nwc|tangible_book}`.
- [x] Add `edgar quality TICKER` with accruals_ratio / ocf_to_ni / ar_to_revenue / sbc_to_revenue / inventory_days / restatement_count_recent flags.
- [x] Add per-share normalizations to `ratios`: `bvps`, `fcf_per_share`, `sales_per_share`. Each carries formula + inputs.
- [ ] Honor `is_restated`/`superseded_by` in every computed metric so trend slopes do not mix restated with original. *(Tied to the bulk-walk deferral above.)*
- [x] Document canonical definitions for ambiguous metrics in [`docs/definitions.md`](definitions.md).
- [x] Skip market-data ratios (P/E, EV/EBITDA, dividend yield) — listed as `not_applicable` in the ratios envelope.

## Cache And Delta

Cache today is a flat 15-minute TTL. That is wrong on both sides — over-caches stable endpoints (frames, ticker map) and under-caches volatile ones (recent filings during earnings). It is also opaque: agents cannot tell hit from miss or reason about freshness. Delta detection does not exist as a primitive, so every long-running run is full-refresh.

- [x] Add `cache` metadata to every JSON envelope: `{calls, hits, misses, age_max_seconds, age_min_seconds, ttl_min_remaining, last_key, last_hit, last_etag}`. Lets agents branch on freshness without a separate observability command.
- [x] Endpoint-aware TTL defaults: ticker map 7d, companyfacts 1d, companyconcept 1d, frames 90d, recent submissions 1h, historical chunks 7d.
- [x] Conditional GET via `If-Modified-Since`/`If-None-Match` when SEC sends `Last-Modified`/`ETag`. On 304, extend TTL without re-downloading the body. Cuts SEC traffic 5-10× for typical agent workloads.
- [x] Negative caching for 404s and 403s so typos and dead concepts do not burn rate-limit slots on every retry.
- [x] Bounded cache with LRU eviction via `--cache-max-mb` constructor flag.
- [x] Concurrent-safe cache reads/writes via `fcntl` advisory locks plus the existing tmp+rename atomic write.
- [x] Surgical invalidation: `edgar cache invalidate '*CIK0000320193*'` (positional glob pattern).
- [ ] Restatement detection during cache refresh: diff prior cached `companyfacts` against new value at `EdgarCache.set()` time. *(Deferred — has subtle correctness implications; surfaced today via `edgar audit-trail` and `edgar delta`.)*
- [x] Persistent state at `~/.edgar/state.json` keyed by `(cik, form)` with last-seen accession and filed date.
- [x] Composable `--since-last-fetch` flag on `filings`, `company`, and `events`. `concept` now also accepts `--since YYYY-MM-DD` for filed-date-based incremental queries.
- [x] Add `edgar delta TICKER` returning `{new_filings, restated_facts, summary}`. Combines with `--use-state` for high-water replay.
- [x] Subscribe/drain pattern: `edgar subscribe add/remove/list`, `edgar pending`, `edgar mark-seen ACCESSION`.
- [x] Stream new filings as NDJSON via `edgar pending --ndjson` (one event per line).
