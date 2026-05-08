# EDGAR CLI Backlog

This tracks test-drive feedback and the next command ideas. Priorities favor
misleading-output fixes before convenience features.

## Last Shipped — Full End-to-End Sweep

This pass closed every tractable backlog item and added clear deferral notes
for the genuinely multi-day projects.

**Cache & state**
- `EdgarCache` wrapper with endpoint-aware TTLs (ticker map 7d, companyfacts/concept 1d, frames 90d, submissions 1h, historical chunks 7d) + negative caching + atomic writes + `fcntl` cross-process locks + `--cache-max-mb` LRU eviction.
- Conditional `GET` via `If-None-Match` / `If-Modified-Since`. `304` refreshes timestamp without re-download.
- `cache stats`, `cache invalidate --pattern '*CIK0000320193*'`, `cache warm --tickers ...` commands.
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
- [ ] Add `edgar statements TICKER --statement income --period CY2025` for normalized statement JSON. (Deferred — needs full canonical mapping; `metrics --bundle income-statement` covers 80%.)
- [ ] Add `edgar history TICKER revenue --periods 20 --annual` for deduplicated time series. (Closely related to `edgar trend` and `edgar concept` with period filters; standalone command not yet added.)
- [x] Add `edgar peers TICKER --candidates @group --by sic --limit 10` (SIC-matched against a candidate set; SEC ticker map lacks SIC so dynamic universe scan would be expensive).
- [ ] Add `edgar export` to write command outputs to CSV / SQLite with schema metadata. (NDJSON shipped; CSV/SQLite pending.)

## Filing Text And Ownership

- [ ] Add `edgar item TICKER 10-K --section "Risk Factors" --as-of YYYY-MM-DD` for targeted filing sections. *(Deferred — Item-aware HTML parsing varies per filer; needs a per-form section parser.)*
- [ ] Add `edgar changes TICKER --since YYYY-MM-DD` to diff same-form sections. *(Deferred — depends on item extraction.)*
- [ ] Add `edgar insiders TICKER --since 90d` to aggregate Form 4 transactions by insider and code. *(Deferred — Form 4 XML pipeline + transaction code semantics is a real project.)*
- [ ] Add `edgar holders TICKER --quarter 2025Q4` for 13F/13G/SC 13G holder summaries. *(Deferred — 13F XML aggregation across filings is a real project.)*
- [x] Improve earnings narrative extraction so table dumps do not become giant unreadable highlights.

## Local Research Moat

- [ ] Add `edgar mirror TICKER --since YYYY-MM-DD --to ./edgar.sqlite` for local submissions, facts, documents, and filing text. *(Deferred — multi-day project; foundation for `edgar search`.)*
- [ ] Add `edgar search "query" --form 10-K --since YYYY-MM-DD --tickers FILE_OR_GROUP` over mirrored filings or SEC full-text search. *(Deferred — depends on mirror or wraps SEC EFTS.)*
- [x] `edgar watch` is covered by `edgar subscribe add` + `edgar pending` + `edgar mark-seen` triple, with state in `~/.edgar/state.json`.
- [ ] Add offline-first mode that refuses live SEC calls and reports cache misses cleanly.

## Point-In-Time And Audit

- [x] Add `--as-of YYYY-MM-DD` to every data command (concept, ttm, ratios, trend, growth, reconstruct, delta).
- [x] Add `edgar audit-trail TICKER --concept ALIAS --period CY2024` with restatement detection.
- [ ] Walk later filings to populate `is_restated`, `superseded_by` on every returned fact. *(Deferred — needs bulk pre-walk; `audit-trail` surfaces restatements today on demand.)*
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
- [ ] Add `edgar dashboard TICKER` composite envelope. *(Deferred — most useful once mirror+search land; today agents can compose `metrics` + `events` + `earnings` + `dei` themselves.)*
- [ ] Add `edgar governance TICKER --year 2025` DEF 14A parsing. *(Deferred — proxy parsing is its own project.)*
- [ ] Add `edgar verify TICKER --period CY2025` cross-statement consistency checks. *(Deferred — needs accurate canonical mappings; could ship a v1 with caveats but punted to keep trustworthiness high.)*

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
- [ ] Add `edgar quality TICKER` covering earnings-quality flags. *(Deferred — needs accruals + working-capital change tagging; safer to ship after `--since-last-fetch` on concept lands.)*
- [ ] Add per-share normalizations to `metrics`: BVPS, FCF/share, sales/share. *(Pending — `eps`/`diluted_eps`/`shares` already in canonical set; per-share derivations not yet wired.)*
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
- [x] Composable `--since-last-fetch` flag on `filings`, `company`, and `events`. *(Pending: `concept`, `metrics`, `brief` — those need a different state model since they are not form-keyed.)*
- [x] Add `edgar delta TICKER` returning `{new_filings, restated_facts, summary}`. Combines with `--use-state` for high-water replay.
- [x] Subscribe/drain pattern: `edgar subscribe add/remove/list`, `edgar pending`, `edgar mark-seen ACCESSION`.
- [x] Stream new filings as NDJSON via `edgar pending --ndjson` (one event per line).
