# EDGAR CLI Backlog

This tracks test-drive feedback and the next command ideas. Priorities favor
misleading-output fixes before convenience features.

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
- [ ] Add JSON schemas and `edgar schema COMMAND` so agents can validate outputs without sampling live SEC data.
- [ ] Add `--explain` traces for concept resolution: candidate tags tried, freshness score, period filters, and why the winning fact was chosen.
- [ ] Add `edgar resolve` for batch ticker/CIK/name resolution with ambiguity metadata.
- [ ] Add cache observability: `edgar cache stats`, `edgar cache warm`, and cache-key display for agent debugging.

## Normalization Layer

- [ ] Add `edgar concept --canonical ALIAS TICKER` as an explicit canonical-union mode across all known fallback tags.
- [ ] Expand canonical concept maps for income statement, balance sheet, cash flow, per-share, share-count, and segment metrics.
- [ ] Deduplicate restated facts and expose `is_restated`, `superseded_by`, and restatement lineage where SEC metadata allows it.
- [ ] Add stricter period filters and aliases: `--annual`, `--quarterly`, `--ytd`, `--instant`, `--duration-days`, and `--latest-per-period`.
- [ ] Add `concept-info ALIAS_OR_TAG` for related tags, common units, migration history, and known issuer-specific variants.

## Batch And Statements

- [ ] Add predefined bundles: `income-statement`, `balance-sheet`, `cash-flow`, `quality`, `liquidity`, and `capital-structure`.
- [ ] Add `edgar statements TICKER --statement income --period CY2025` for normalized statement JSON.
- [ ] Add `edgar history TICKER revenue --periods 20 --annual` for deduplicated time series.
- [ ] Add `edgar peers TICKER --by sic --limit 10` ranked by recent revenue/assets when market cap is unavailable.
- [ ] Add `edgar export` to write command outputs to JSON, NDJSON, CSV, or SQLite with schema metadata.

## Filing Text And Ownership

- [ ] Add `edgar item TICKER 10-K --section "Risk Factors" --as-of YYYY-MM-DD` for targeted filing sections.
- [ ] Add `edgar changes TICKER --since YYYY-MM-DD` to diff same-form sections and summarize material changes.
- [ ] Add `edgar insiders TICKER --since 90d` to aggregate Form 4 transactions by insider and code.
- [ ] Add `edgar holders TICKER --quarter 2025Q4` for 13F/13G/SC 13G holder summaries.
- [ ] Improve earnings narrative extraction so table dumps do not become giant unreadable highlights.

## Local Research Moat

- [ ] Add `edgar mirror TICKER --since YYYY-MM-DD --to ./edgar.sqlite` for local submissions, facts, documents, and filing text.
- [ ] Add `edgar search "query" --form 10-K --since YYYY-MM-DD --tickers FILE_OR_GROUP` over mirrored filings or SEC full-text search.
- [ ] Add `edgar watch TICKER --form 8-K` with state in `~/.edgar/watch.json` for delta-driven long-running agents.
- [ ] Add offline-first mode that refuses live SEC calls and reports cache misses cleanly.
