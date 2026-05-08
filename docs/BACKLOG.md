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

## Filing Workflow

- [x] Add `--start-date` and `--end-date` filters to `filings` and `company`.
- [x] Add `edgar open IDENTIFIER --form 10-K` to open the latest filing index.
- [x] Add `edgar exhibits ACCESSION_OR_URL` to list/download filing exhibits.

## Research Commands

- [x] Add `edgar brief TICKER` for profile, latest filings, key facts, and notable events.
- [x] Add `edgar earnings TICKER` to parse latest Item 2.02 8-K / Exhibit 99.1.
- [x] Add `edgar events TICKER` for reverse splits, CEO/CFO changes, delisting notices, acquisitions, debt events, and guidance updates.
- [x] Add `edgar compare AAPL MSFT GOOGL --concept Revenues --periods 4`.
