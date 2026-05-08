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
```

### `filings` - Recent filings

```bash
edgar filings TSLA --limit 20
edgar filings GOOGL --form 8-K
```

### `facts` - Available XBRL concepts for one company

```bash
edgar facts AAPL --taxonomy us-gaap --tag-filter revenue
edgar facts MSFT --limit 100 --json-output
```

### `concept` - One company's facts for a single concept

```bash
edgar concept AAPL us-gaap Assets --unit USD
edgar concept MSFT us-gaap Revenues --limit 12 --markdown
```

### `frame` - Cross-company XBRL frame

```bash
edgar frame us-gaap Assets USD CY2024Q4I --limit 25
edgar frame us-gaap Revenues USD CY2024 --sort name --markdown
```

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

## License

MIT
