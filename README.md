# EDGAR CLI

A SEC EDGAR public-data CLI built for agents. Returns JSON by default when
stdout is not a TTY, attaches provenance to every fact, and ships derived
metrics with formula + source facts so an agent can defend every number.

Human-readable rich-table output is also available; agent paths are the
primary surface and the docs lead with them.

## Install

```bash
pip install -e .
```

## Setup

The public `data.sec.gov` APIs are keyless. SEC asks automated tools to
declare a User-Agent with organization/contact info:

```bash
cp .env.example .env
# Edit SEC_USER_AGENT to your organization/contact string
```

## Agent quickstart

```bash
# JSON is the default when stdout is not a TTY — no flag needed in pipelines.
edgar concept NVDA revenue --limit 1 | jq '.facts[0].val'

# Every JSON envelope carries `schema_version`, `cli_version`, and a `cache`
# summary so agents can branch on freshness.
edgar metrics AAPL --bundle revenue,net_income | jq '{schema_version, cache}'

# Citations on demand — one per row, agent-quotable.
edgar concept NVDA revenue --limit 1 --cite | jq '.facts[].citation'
# "NVIDIA CORP FY2026 10-K · 0001045810-26-000021 · filed 2026-02-25"

# Point-in-time queries. Eliminates look-ahead bias for backtests.
edgar concept NVDA revenue --as-of 2024-01-01 | jq '.facts[0]'

# Batch fan-out across one CLI invocation.
edgar metrics --tickers AAPL,MSFT,GOOGL --bundle revenue
edgar metrics --tickers @dow30 --bundle revenue,net_income --ndjson

# Subscribe + drain: a delta-driven workflow without a separate daemon.
edgar subscribe add NVDA --form 8-K
edgar pending --ndjson    # streams new filings since last drain
```

### Stable exit codes

| Code | Meaning |
|------|---------|
| 0    | Success |
| 2    | No data found (well-formed query, empty result, 404) |
| 3    | Rate-limited |
| 4    | SEC outage / 403 / unavailable service |
| 5    | Validation error (malformed input) |

### Envelope on every JSON response

```json
{
  "schema_version": "1.1.0",
  "cli_version": "0.1.0",
  "cache": {"calls": 10, "hits": 7, "misses": 3, "ttl_min_remaining": 52867,
            "last_key": "...", "last_hit": false, "last_etag": null},
  "facts": [...]
}
```

`edgar schema [COMMAND]` returns the JSON Schema for any command output, so
agents can validate without sampling live SEC data. Every schema names its
`primary_row_key` (the main list-of-rows field), and `edgar schema` with no
argument returns the full command → primary-row-key map.

Filing rows carry both SEC's raw camelCase (`accessionNumber`, `filingDate`)
and snake_case aliases (`accession`, `filed`, `report_date`,
`primary_document`) as of schema_version 1.1.0.

### Per-fact provenance + period metadata

Every concept/frame fact ships with:

```json
{
  "val": 215938000000,
  "start": "2025-01-27", "end": "2026-01-25",
  "period_type": "annual",
  "period_length_days": 363,
  "fiscal_period": "FY2026",
  "calendar_period": "CY2025",
  "source_url": "https://www.sec.gov/Archives/edgar/data/...",
  "as_of": "2026-01-25",
  "accession": "0001045810-26-000021",
  "is_restated": false, "is_cumulative": false, "superseded_by": null
}
```

### Universe groups

`--tickers` accepts plain tickers and `@group` expressions:

- `@dow30` — current Dow Jones 30 (static list)
- `@sic:NNNN` — every filer in a SIC code (dynamic from the ticker map)

`edgar resolve @dow30` expands a group to its concrete CIKs.

## Commands

### Discovery

```bash
edgar search-companies apple                        # ticker/CIK/name search
edgar tags "deferred revenue"                       # XBRL tag search
edgar frames --since 2024 --until 2025              # enumerate frame strings
edgar concept-info revenue                          # candidates + freshness
edgar dei NVDA                                      # filer entity metadata
edgar peers MRK --candidates @dow30                 # SIC-matched peers
edgar resolve AAPL @dow30 GOOGL                     # batch ticker→CIK
```

### Filings

```bash
edgar company NVDA --form 10-K --all
edgar filings AAPL --major --limit 5                # 10-K/Q/8-K/S-1/proxy
edgar filings AAPL --insider                        # Forms 3/4/5
edgar filings AAPL --institutional                  # 13D/F/G
edgar filings AAPL --start-date 2024-01-01 --end-date 2024-12-31
edgar filings AAPL --form 8-K --since-last-fetch    # incremental mode
edgar open AAPL --form 10-K --print-only            # print latest filing URL
edgar exhibits 0000320193-25-000079 --cik 320193 --type-filter EX-99
edgar earnings AAPL                                 # latest Item 2.02 8-K
edgar events TSLA --limit 10                        # 8-K event detection
```

### Concepts

```bash
edgar concept NVDA revenue                          # alias resolution
edgar concept NVDA us-gaap Revenues --unit USD      # explicit tag
edgar concept NVDA revenue --annual --as-of 2024-01-01
edgar concept NVDA revenue --canonical --quarterly  # union across alias tags
edgar concept NVDA revenue --explain                # resolution trace
edgar concept --tickers AAPL,MSFT,GOOGL revenue --quarterly --ndjson
edgar facts AAPL --tag-filter revenue
edgar frame us-gaap Assets USD CY2024Q4I --limit 25
edgar compare AAPL MSFT GOOGL --concept revenue --periods 4
edgar diff AAPL MSFT --concept revenue --periods 3  # period-aligned
```

`compare` and `diff` align on `frame`/`calendar_period` so filers with
different fiscal calendars (AAPL FY ends Sep, MSFT FY ends Jun) still pair on
the same row.

### Computed metrics — formula + provenance

Every derived number returns `{value, formula, inputs, caveats, missing_inputs}`.
See [docs/definitions.md](docs/definitions.md) for canonical formulas
(EBITDA SBC treatment, FCF capex scope, etc.).

```bash
edgar metrics NVDA --bundle revenue,net_income,cash,debt
edgar metrics --tickers @dow30 --bundle income-statement --ndjson
edgar ttm NVDA --bundle revenue                     # trailing 12 months
edgar ratios NVDA --period-type annual              # 14 canonical ratios
edgar trend NVDA --metric revenue --periods 8       # slope + label
edgar growth NVDA --metric revenue --basis yoy,qoq,cagr3
edgar reconstruct NVDA --metric ebitda              # ebitda|fcf|net_debt|nwc|tangible_book
edgar brief AAPL                                    # profile + metrics + events
```

`metrics --bundle` accepts named groups: `income-statement`,
`balance-sheet`, `cash-flow`, `liquidity`, `capital-structure`, `quality`.

`ttm` falls back to stub-period reconstruction (`AnnualFY + CurrentYTD −
PriorYTD`) for filers that only tag Q4 inside the annual 10-K (Apple,
Microsoft, NVIDIA). Each TTM envelope tags the formula it used.

`ratios` anchors balance-sheet (instant) facts to the income/cash-flow period
end so ROA/ROE/turnover do not silently mix FY revenue with a later
quarter's balance sheet. Drift over 14 days surfaces in
`alignment_warnings`.

### Audit / point-in-time

```bash
edgar audit-trail NVDA --concept revenue --period CY2024  # restatement detection
edgar amendments AAPL --since 2025-01-01                  # primary↔/A pairs
edgar delta NVDA --use-state                              # new + restated since last fetch
```

Add `--as-of YYYY-MM-DD` to `concept`, `compare`, `diff`, `ttm`, `ratios`,
`trend`, `growth`, `reconstruct`, or `delta` to filter to facts filed on or
before that date — eliminates look-ahead bias.

### Search / mirror

```bash
edgar search "supply chain constraints" --form 10-K   # live EFTS full-text
edgar mirror NVDA --to ./edgar.sqlite --with-bodies-for 10-K --bodies-limit 5
edgar search "supply chain" --db ./edgar.sqlite       # local FTS5 over bodies
edgar item NVDA --form 10-K --section "Risk Factors"  # one Item's body text
edgar insiders NVDA --since 2026-01-01                # Form 4 aggregation
edgar holdings BRK-A --top 10                         # 13F portfolio
edgar governance AAPL                                 # DEF 14A extraction
```

Live EFTS searches default to the last 5 years (disclosed via
`applied_default_since`; override with `--since 2001-01-01`) and return
filing metadata only — EFTS provides no text snippets. Mirror searches use
FTS5 over ingested filing bodies, falling back to metadata with a `hint`
when no bodies exist.

### Subscribe / drain

```bash
edgar subscribe add AAPL --form 8-K
edgar subscribe list
edgar pending --ndjson                              # streams new filings, advances state
edgar mark-seen AAPL 0000320193-26-000011 --form 8-K
edgar subscribe remove AAPL --form 8-K
```

State lives in `~/.edgar/state.json` keyed by `(cik, form)`.

### Cache

```bash
edgar --cache-max-mb 100 cache stats                # bound cache to 100 MB
edgar cache invalidate '*CIK0000320193*'            # surgical glob invalidation
edgar cache warm --tickers @dow30                   # pre-fetch submissions+facts
edgar clear-cache
```

The cache is endpoint-aware (ticker map 7d, companyfacts/concept 1d, frames
90d, recent submissions 1h, 404s 1h). Conditional `If-None-Match` /
`If-Modified-Since` requests refresh entries without re-downloading.

### Other

```bash
edgar bulk-urls                                     # nightly bulk archive URLs
edgar schema concept                                # JSON Schema for an output
edgar --webhook https://hooks.example.com/edgar metrics AAPL  # POST result
edgar concept NVDA revenue --export-csv rev.csv     # CSV alongside JSON
```

`--export-csv` works both before and after the subcommand.

## Output formats

| Flag | Format | Best for |
|------|--------|----------|
| default on TTY | Rich tables/panels | Humans |
| default when piped | Pretty JSON envelope | Agents |
| `--markdown` / `-m` | Markdown tables | Reports, agent prompts |
| `--json-output` / `-j` | Pretty JSON envelope (force) | Forced JSON |
| `--ndjson` | Newline-delimited rows | Streaming, `jq`, `head` |

NDJSON rows are flat and self-describing — each line carries enough metadata
to be processed independently.

## API scope

Public, keyless `data.sec.gov` endpoints:

- `/submissions/CIK##########.json`
- `/api/xbrl/companyfacts/CIK##########.json`
- `/api/xbrl/companyconcept/CIK##########/{taxonomy}/{tag}.json`
- `/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{frame}.json`
- `/files/company_tickers_exchange.json`

The authenticated EDGAR Next filer APIs are out of scope. Market-data ratios
(P/E, EV/EBITDA, dividend yield) appear in the `ratios` envelope under
`not_applicable` so agents do not retry them.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
python -m edgar --help
```

## Notes

- SEC's scripted access ceiling is 10 req/s; this CLI uses a shared local
  limiter at 5 req/s.
- Cache lives at `~/.edgar_cache`. Use `--no-cache` to bypass per call,
  `cache invalidate` for surgical busting, `--cache-max-mb` for LRU bound.
- Filing commands search SEC's recent filing set by default; pass `--all`
  to fetch historical chunks from `filings.files[]`.
- `concept` suggests similar issuer-specific tags when SEC returns 404.
- `concept --deltas` skips adjacent rows with mismatched period lengths.
- Exhibit downloads use the same User-Agent and shared rate limiter.
- Shipped-feature history and open ideas live in
  [docs/BACKLOG.md](docs/BACKLOG.md); live test-drive findings and their
  fixes in [docs/FINDINGS.md](docs/FINDINGS.md).

## License

MIT
