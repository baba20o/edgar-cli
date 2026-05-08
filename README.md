# EDGAR CLI

A command-line tool for the SEC EDGAR public data APIs.

Built for research agents and human analysts with rich terminal output, markdown
tables, and raw JSON.

## Install

```bash
pip install -e .
```

## Setup

The public `data.sec.gov` APIs do not require an API key. SEC does ask automated
tools to declare a user agent with organization/contact information:

```bash
cp .env.example .env
# Edit SEC_USER_AGENT to your organization/contact string
```

## Commands

### `search-companies` - Find ticker/CIK mappings

```bash
edgar search-companies apple
edgar search-companies NVDA --json-output
```

### `company` - Company profile plus recent filings

```bash
edgar company AAPL
edgar company 0000320193 --limit 5
edgar company MSFT --form 10-K --markdown
edgar company AAPL --form 10-K --start-date 2024-01-01 --end-date 2024-12-31
edgar company NVDA --form S-1 --all
```

### `filings` - Recent filings

```bash
edgar filings TSLA --limit 20
edgar filings GOOGL --form 8-K
edgar filings NVDA --form S-1 --start-date 2020-01-01 --show-urls
edgar filings NVDA --form S-1 --all --markdown
```

### `facts` - Available XBRL concepts for one company

```bash
edgar facts AAPL --taxonomy us-gaap --tag-filter revenue
edgar facts MSFT --limit 100 --json-output
```

### `concept` - One company's facts for a single concept

```bash
edgar concept AAPL revenue
edgar concept AAPL us-gaap Assets --unit USD
edgar concept MSFT us-gaap Revenues --limit 12 --markdown
edgar concept AAPL us-gaap RevenueFromContractWithCustomerExcludingAssessedTax --deltas
```

### `frame` - Cross-company XBRL frame

```bash
edgar frame us-gaap Assets USD CY2024Q4I --limit 25
edgar frame us-gaap Revenues USD CY2024 --sort name --markdown
```

### `open` - Open or print the latest filing index URL

```bash
edgar open AAPL --form 10-K
edgar open AGL --form 8-K --print-only
```

### `exhibits` - List or download documents from a filing

```bash
edgar exhibits 0001628280-26-031254 --cik 1831097
edgar exhibits https://www.sec.gov/Archives/edgar/data/1831097/000162828026031254/0001628280-26-031254-index.htm --type-filter EX-99 --markdown
edgar exhibits 0001628280-26-031254 --cik 1831097 --download ./downloads/agiliti
```

### `earnings` - Latest Item 2.02 earnings 8-K summary

```bash
edgar earnings AAPL
edgar earnings AGL --markdown
```

### `events` - Recent notable 8-K events

```bash
edgar events AGL --limit 10
edgar events TSLA --markdown
```

### `compare` - Compare a concept across companies

Common aliases include `revenue`, `net_income`, `operating_income`, `cash`,
`assets`, `liabilities`, `debt`, `eps`, and `shares`.

```bash
edgar compare AAPL MSFT GOOGL --concept revenue --periods 4
edgar compare AAPL MSFT --concept Assets --unit USD --markdown
```

Friendly aliases try issuer-specific fallback tags and align on shared period
frames, so companies that migrated XBRL tags can still be compared without
mixing years or period types.

### `brief` - Compact company brief

```bash
edgar brief AAPL
edgar brief AGL --markdown
```

Brief metrics use fallback tags for common concepts and include a freshness
column so stale facts are visible instead of silently looking current.

### `bulk-urls` - Official nightly bulk archive URLs

```bash
edgar bulk-urls
```

### `clear-cache` - Remove cached API responses

```bash
edgar clear-cache
```

## Output Formats

Most data commands support three output modes:

| Flag | Format | Use case |
|------|--------|----------|
| default | Rich terminal tables/panels | Human terminal use |
| `--markdown` / `-m` | Markdown output | Agent parsing and reports |
| `--json-output` / `-j` | Raw JSON | Programmatic pipelines |

Human table output abbreviates numeric values, hides long filing URLs by
default, and keeps raw values in `--json-output`. Use `--show-urls` on filing
commands when you want filing index URLs in the table. For agent-to-agent work
or narrow terminals, prefer `--markdown`; it avoids rich-table truncation.

## API Scope

This first version targets the keyless public-data endpoints:

- Submissions history by filer: `https://data.sec.gov/submissions/CIK##########.json`
- Company facts: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- Company concept: `https://data.sec.gov/api/xbrl/companyconcept/CIK##########/{taxonomy}/{tag}.json`
- Frames: `https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{frame}.json`
- Ticker/CIK map: `https://www.sec.gov/files/company_tickers_exchange.json`

The EDGAR Next filer APIs described in `api-overview.pdf` are a different,
authenticated surface for submissions, submission status, operational status,
and filer management. They are intentionally out of scope for this public
research CLI skeleton.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
python -m edgar --help
```

## Notes

- SEC's published maximum scripted access rate is 10 requests per second. This
  CLI uses a shared local limiter of 5 requests per second.
- Responses are cached in `~/.edgar_cache` for 15 minutes. Use `--no-cache`
  when freshness matters.
- Filing commands search SEC's recent filing set by default. The CLI warns when
  a form/date query is likely to be limited by that recent set, such as a
  filtered result hitting `--limit` or a date filter reaching the oldest recent
  filing. Use `--all` to fetch historical chunks from `filings.files[]` when
  researching older IPO-era forms.
- `concept` suggests similar company-specific XBRL tags when SEC returns a 404,
  which helps with issuer-specific tags like revenue concepts.
- `concept --deltas` only compares adjacent rows with matching period lengths;
  mixed quarterly/annual rows are skipped to avoid misleading math.
- Exhibit downloads use the same SEC user agent and shared rate limiter as API
  requests.

## License

MIT
