# EDGAR CLI Backlog

This tracks test-drive feedback and the next command ideas. Priorities favor
misleading-output fixes before convenience features.

## Correctness

- [x] Encode taxonomy/tag/frame path segments so `/` and `..` cannot change the requested SEC URL.
- [x] Reject blank company identifiers and blank company search queries.
- [x] Avoid silent empty filing tables when a form/date filter has no matches.
- [ ] Support full filing history by fetching historical chunks from `filings.files[]` with an explicit `--all` flag and progress warning.
- [ ] Suggest similar company XBRL tags when `concept` returns 404.

## Output UX

- [x] Format numeric values for human output while keeping JSON raw.
- [x] Show `start`, `end`, and `frame` in concept tables.
- [x] Keep XBRL concept tags untruncated in rich tables.
- [x] Hide filing URLs in default tables; keep them in JSON and expose with `--show-urls`.
- [ ] Add width-aware rich table layouts or make markdown the recommended agent mode in help text.
- [ ] Add YoY/QoQ deltas in concept output.

## Filing Workflow

- [x] Add `--start-date` and `--end-date` filters to `filings` and `company`.
- [ ] Add `edgar open IDENTIFIER --form 10-K` to open the latest filing index.
- [ ] Add `edgar exhibits ACCESSION_OR_URL` to list/download filing exhibits.

## Research Commands

- [ ] Add `edgar brief TICKER` for profile, latest filings, key facts, and notable events.
- [ ] Add `edgar earnings TICKER` to parse latest Item 2.02 8-K / Exhibit 99.1.
- [ ] Add `edgar events TICKER` for reverse splits, CEO/CFO changes, delisting notices, acquisitions, debt events, and guidance updates.
- [ ] Add `edgar compare AAPL MSFT GOOGL --concept Revenues --periods 4`.
