# Handoff implementation and due diligence

Request: [`handoff.md`](../handoff.md), dated 2026-11-15. The source handoff is
preserved. This report records the implemented behavior, corrections to its
claims and the limits that a downstream research agent must retain.

## Implementation map

| Request | Result | Main verification surface |
|---|---|---|
| B1: outstanding-share extraction | Excludes locally identified derivative-underlying figures; retains pre-offering baselines and separate classes; selects a dated baseline and warns about large unexplained XBRL/text divergence. | CODX, SBET, JAGX excerpts; existing OMH fixtures; `test_dilution_handoff.py`, `test_share_selection.py`. |
| B2: baby-shelf text | Recognizes S-3 I.B.6 and F-3 I.B.5; separates non-affiliate value, prior sales, remaining capacity and base shelf totals; rejects filer-status boilerplate. | CODX prospectus excerpts; `test_dilution_handoff.py`, `test_forensic_calculators.py`. |
| B3: document cache | Text and byte consumers share raw cached bytes and encoding; normalized text is cached by exact source content and parser version; 30-day artifact TTL, 4 MiB storage cap, LRU limit, conditional revalidation, no-cache bypass and per-lookup telemetry. | `test_document_cache.py`, `test_cache.py`; composite cold/warm verification. |
| B4: domestic events | Sentence-based 8-K evidence with disclaimer exclusions, expanded item mapping, listing-event distinctions and an EX-99 lead fallback. Metadata-only evidence is identified separately. | Corrected CODX report, SBET closing/release; `test_domestic_events.py`. |
| B5: IFRS aliases | Appends verified IFRS candidates after existing candidates; preserves currency; uses share-count and debt-component tags with the correct meanings. | `test_ifrs.py`; official IFRS taxonomy references below. |
| F1: availability cutoffs | Date and timezone-aware intraday cutoffs propagate through filing selection, XBRL availability, explicit accession reads and composite commands. Historical chunks are searched; responses echo normalized cutoffs and coverage warnings. | `test_point_in_time.py`, adversarial cutoff and composite regressions. |
| F2: richer dilution evidence | Named agents/roles/commissions; sentence-scoped offering status and dates; registration/withdrawal/rescission histories; optional periodic and registration-cover reads. | `test_dilution_features.py`, `test_registrations.py`, integration regressions. |
| F3: calculations | Pure baby-shelf and cash-runway calculators expose formulas, provenance, units, missing inputs and rejected-input caveats. Source disagreements remain visible. | `test_forensic_calculators.py`. |
| F4: share supply | `float` keeps XBRL/text counts, non-affiliate shares, EntityPublicFloat dollars, post-baseline issuance evidence, pending/ambiguous statements, 13D/G owners and insider holdings separate. | `test_float.py`, `test_ownership.py`. |
| F5: composite and feed | `forensics` combines the research sections using four workers with shared rate limiting and request/cache reuse. `feed` polls the bounded Atom source with form-scoped, output-acknowledged cursors. Schema version is 1.3.0. | `test_forensics_integration.py`, `test_feed.py`, state and CLI tests. |

The scan defaults are explicit. `dilution` reads up to 25 current reports and
three supplements within its selected window. The `float` and `forensics` CLI
commands default to 10 current reports, three supplements and five ownership
filings; all expose the same opt-in periodic/shelf flags. `--include-periodic`
selects financing, equity, warrant and liquidity sentences from the latest
periodic report. `--include-shelf` examines the first 60 sentences of eligible
registrations without supplements. These are bounded scans, not exhaustive
legal or transaction reconciliation.

## Usage

```bash
edgar dilution CODX --include-periodic --include-shelf
edgar float CODX --as-of 2026-11-15 --price 2.10
edgar forensics CODX --price 2.10 --high-60d 3.25 --include-periodic
edgar forensics SBET --as-of 2025-05-30T21:00:00Z
edgar feed --forms 8-K,6-K,424B5 --since-minutes 15 --use-state --ndjson
edgar schema float
edgar schema forensics
edgar schema feed
```

```python
from edgar.api import get_client
from edgar.feed import ack_feed

client = get_client()
result = client.forensics("CODX", as_of="2026-11-15", price=2.10,
                          max_filings=10, max_supplements=3,
                          max_ownership_filings=5, include_periodic=True)

poll = client.feed(forms="8-K,6-K,424B5", since_minutes=15, use_state=True)
# Deliver poll successfully before acknowledging it.
# ack_feed(poll)
```

A date includes its full UTC day. An intraday cutoff must include `Z` or an
explicit UTC offset. Filing acceptance time controls availability; when it is
unknown, the filing date must be earlier than the previous UTC day for
intraday replay. This extra day avoids treating after-midnight UTC acceptances
as already available merely because their filing date was the previous day. XBRL facts use
their accession's acceptance time when available. A failed submissions lookup
fails closed for facts. When historical chunks are unavailable, intraday replay
excludes facts whose accessions lack known acceptance metadata and reports the
coverage gap. A release's dateline does
not override its SEC parent filing's availability. Current entity metadata,
listing/ticker mappings and unfiled issuer news are not historical snapshots.

`feed` uses Atom `updated` as its explicitly labeled timestamp source. It does
not guarantee instantaneous publication or complete historical coverage. Its
`coverage` field reports page limits, source exhaustion, parse/order problems
and retention gaps. Incomplete polls never advance the cursor. Complete polls
record their poll-start watermark even when no selected filing is returned. Successful CLI
output is flushed before acknowledgement; Python callers acknowledge explicitly.
Form-scoped cursors keep timestamp ties and an accession/CIK overlap set, so
slightly late entries can be delivered without replaying acknowledged entries.
The full overlap is scanned behind the previous watermark even when polls are
farther apart than `--since-minutes`. Arrivals older than that overlap can still
be missed. An empty Atom response cannot advance an older cursor across an
uncovered gap.

`--reset-state` requires `--use-state` and explicitly removes only the current
form filter's feed cursor before polling. It reports that continuity is being
abandoned; older undelivered filings are not recovered. This is the recovery
path after investigating an unrecoverable Atom retention gap.

Pre-commit opposing-brand review also drove regressions for empty/quiet feed
polls, source-linked closing dates, identical baby-shelf scenarios in both
composite sections, and missing-acceptance UTC boundaries. Real Schedule 13G
cover XML uses different fields from 13D; the parser now supports both and rejects
legacy cover row numbers as missing share amounts. The added SBET fixture retains
the filing's stated 0.004% without silently correcting it.
[SBET Schedule 13G/A XML](https://www.sec.gov/Archives/edgar/data/1981535/000121390025049583/primary_doc.xml).

## Corrections to the handoff

### CODX's financing accession

The October 13, 2026 CODX 8-K is **0001493152-26-046676**, accepted at
`2026-10-13T10:10:19Z`. It contains Items 1.01, 2.03 and 3.02 and substantive
convertible-note financing. The handoff's accession **0001493152-26-047120**
belongs to a GraniteShares ETF Trust Form 485BXT filed October 15. The domestic
classification regression uses the corrected CODX filing.
[CODX report](https://www.sec.gov/Archives/edgar/data/1692415/000149315226046676/form8-k.htm),
[other accession's SEC index](https://www.sec.gov/Archives/edgar/data/1689873/000149315226047120/0001493152-26-047120-index.htm).

### CODX's prospectus contains inconsistent arithmetic

The July 27 prospectus's numbers were quoted accurately in the handoff, but
those disclosures disagree with each other:

| Basis | Amount |
|---|---:|
| Filing-stated MVPHS | $49,425,570 |
| 5,153,941 disclosed shares × $11.19 disclosed reference price | $57,672,599.79 |
| Stated prior 12-month sales | $11,322,816 |
| Stated MVPHS / 3 − prior sales | $5,152,374 |
| Shares × price / 3 − prior sales | $7,901,383.93 |
| Filing-stated remaining capacity | $7,901,383 |

The calculator preserves reported and derived values independently and warns
about the discrepancy. It uses stated MVPHS by default; explicit caller price
or share inputs select a separate arithmetic scenario. It does not silently
change the source to produce the handoff's expected answer.
[CODX prospectus](https://www.sec.gov/Archives/edgar/data/1692415/000149315226034835/form424b5.htm).

### $75 million is a scenario threshold, not a permanent exemption

`escape_price_75m = 75_000_000 / non_affiliate_shares` is useful arithmetic,
but does not establish permanent shelf eligibility or verified market prices.
Eligibility can be reassessed at Section 10(a)(3) updates and the limitation
can apply again; other conditions also matter. The 2026 Registered Offering
Reform is listed as a proposal and is not treated as an adopted repeal.
At/above the threshold the calculator leaves one-third capacity null with an
explanation, rather than reporting unlimited capacity.
[Form S-3](https://www.sec.gov/files/forms-3.pdf),
[Form F-3](https://www.sec.gov/files/formf-3.pdf),
[SEC Forms interpretations](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/securities-act-forms),
[proposal status](https://www.sec.gov/rules-regulations/2026/05/s7-2026-17).

### SBET's May 27 release was filed with the SEC on May 30

The cited 8-K was accepted at `2025-05-30T20:58:07Z`. Its attached May 27
release expected a May 29 closing; the report body says the closing occurred
May 30. A May 27 SEC-only replay must exclude both documents. A separate
issuer-news ingestion system would need independent publication timestamps to
reconstruct the earlier announcement. Closing, registration and eligibility
for resale are separate facts; the output preserves their evidence without
turning them into a legal tradeability conclusion.
[SEC index](https://www.sec.gov/Archives/edgar/data/1981535/000164117225013081/0001641172-25-013081-index.htm),
[closing report](https://www.sec.gov/Archives/edgar/data/1981535/000164117225013081/form8-k.htm),
[attached release](https://www.sec.gov/Archives/edgar/data/1981535/000164117225013081/ex99-1.htm),
[SEC resale overview](https://www.sec.gov/reports/rule-144-selling-restricted-control-securities).

### Runway is historical, and stale debt is unknown

CODX's cited 10-Q was filed November 10; November 6 is the cover-share
measurement date. Its $714,803 cash is September 30 cash. Using the requested
272-day convention gives monthly operating burn of **$2,314,514.61**, rather
than $2,313,568, and runway of **0.308835 months / 9.400164 days**. The report
also discloses subsequent secured financing, so that extrapolation is not a
current cash balance. Stale or missing debt cannot establish zero debt: the
October report describes a $2.835 million principal note. The calculator returns
null with rejected/missing inputs when current compatible debt facts are absent.
[CODX 10-Q](https://www.sec.gov/Archives/edgar/data/1692415/000149315226050963/form10-q.htm),
[financing 8-K](https://www.sec.gov/Archives/edgar/data/1692415/000149315226046676/form8-k.htm).

### Withdrawal forms do not establish one universal status

RW requests registration withdrawal; AW concerns an amendment; RW WD and
AW WD rescind requests; POS AM is a post-effective amendment. The scope and
effect must be established from evidence. Rule 477 has different conditions
for pre-effective and effective registrations, so matching file numbers alone
cannot prove an entire shelf withdrawn. The implementation preserves request,
rescission and confirmation evidence and uses `withdrawal_requested` or
`withdrawal_status_unknown` where needed. It can conservatively leave a legally
completed withdrawal unresolved if sufficient evidence is outside the scan.
Missing EFFECT likewise does not prove ineffectiveness; ASR forms may be
automatically effective. Effectiveness here is historical filing evidence;
expiration, current offering eligibility and remaining registered capacity
are not assessed from those records alone.
[SEC Filer Manual](https://www.sec.gov/files/edgar/filermanual/efmvol2-v76-d.pdf),
[Rule 477](https://www.govinfo.gov/content/pkg/CFR-2025-title17-vol3/pdf/CFR-2025-title17-vol3-sec230-477.pdf),
[amendment-only request](https://www.sec.gov/Archives/edgar/data/1178970/000119312512051327/d298961drw.htm),
[rescinded mistaken RW](https://www.sec.gov/Archives/edgar/data/846615/000119312504197137/drwwd.htm).

### IFRS shares, debt and currency require distinct tags

The official taxonomy defines `IssuedCapital` as monetary; the share-count
fallback uses `NumberOfSharesOutstanding`. Total `Borrowings` and
`NoncurrentLiabilities` are not noncurrent debt components to which current
debt can safely be added. The aliases use `LongtermBorrowings` and
`CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings` and retain the
reported currency. The IFRS 2025 taxonomy remains the stated version for 2026
reporting.
[IFRS core XSD](https://xbrl.ifrs.org/taxonomy/2025-03-27/full_ifrs/full_ifrs-cor_2025-03-27.xsd),
[IFRS Foundation guidance](https://www.ifrs.org/news-and-events/news/2026/02/ifrs-accounting-taxonomy-2025-to-remain-current-for-2026/).

### Ownership and warrant evidence are not active tradable totals

Beneficial-owner records can overlap, include acquisition rights and measure
different dates or classes. They are not summed or automatically deducted from
outstanding shares. Likewise, successive warrant disclosures can supersede
one another; `warrant_overhang` returns dated statements and parsed features,
not an inferred active outstanding balance. Establishing current totals would
require transaction-level reconciliation beyond these bounded text scans.
[SEC beneficial ownership interpretations](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/exchange-act-sections-13d-13g-regulation-13d-g-beneficial-ownership-reporting).

### Cache and performance guarantees need conditions

SEC can correct or remove accepted documents. The 30-day TTL is an explicit
cache policy with revalidation/invalidation, not permanent immutability.
Fresh document hits skip network and rate-limit waits; oversized or expired
entries may still require requests. Telemetry counts logical lookups, including
text/byte reads, derived-text lookups and failures. Normalized text is cached
by source-content hash and parser version after checking source bytes. Parser
behavior changes must bump the `filing-text-v1` representation identifier. Retries, conditional 304s and bundled companyfacts
reads mean those counters are not literal HTTP-attempt counts.
[SEC access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

At five requests/second, 18 cold requests require at least 3.4 seconds between
the first and last request starts, before transfer or parsing. Four workers
cannot make a universal 1.8-second cold guarantee compatible with that limit.
The implementation reports measured API duration and cache telemetry; CLI
startup adds further time. Performance must be reported with scan bounds,
cache state, request counts and environment.

## Fixture provenance and verification

`tests/fixtures/handoff/` contains selected source excerpts for CODX, SBET and
JAGX, alongside selected original SEC submissions rows. Each source fixture
records its URL, accession/CIK, retrieval date, source SHA-256 and transformation.
They are excerpts, not complete filing fixtures. Synthetic envelopes and edge
cases in tests are labeled separately. Existing OMH fixtures remain in use.
The test suite blocks live requests so regressions do not depend on SEC uptime.

The final ordinary offline run completed with **572 passed and 2 skipped in
5.37 seconds** using `.venv/bin/python -E -m pytest tests/ -q`. The skips are
optional local mission fixtures that are not distributed with the repository.
The orchestrator also checked SHA-256 values for 77 existing protected user
artifacts and found them unchanged. UAF completion requires the configured
`pytest`, `uaf-check` and `sync-check` bundle. Canonical verifier/closure evidence
is recorded in `.uaf/ledger.jsonl` for implementation task
`task_8160a6c67019477781867a5c480130be` and mission-root task
`task_f4470ec6ea4c4fd980128b5fc501783f`; inspect with `uaf trace --task ID --verify-chain`.

### Measured performance (2026-11-15)

Measurements used Python 3.10 on WSL with the repository on `/mnt/c`, the
configured five-request/second limiter, and separate initially cold caches.
Durations cover the API call and exclude CLI startup. Each warm run used a
fresh client with persistent cached data. Final warm trials prohibited network
access and each made **zero HTTP requests**.

| API call and scan bounds | Cold time | Cold HTTP requests | Warm median (3 trials) | Warm HTTP requests |
|---|---:|---:|---:|---:|
| `forensics("CODX", price=2.10)`; defaults 10 current reports / 3 supplements / 5 ownership filings | 6,011.476 ms | 30 | 256.844 ms | 0 |
| `dilution("SBET", max_filings=15, max_supplements=4)` | 3,471.217 ms | 17 | 217.868 ms | 0 |

Final CODX warm trials were 256.560 / 256.844 / 262.017 ms; SBET trials were
217.868 / 219.328 / 213.338 ms. CODX returned 12 dilution `filings_read` entries
and no document or section failures; the request total also includes ownership,
exhibits and API metadata. SBET read 14 filing documents with no failures.
Warm telemetry showed all 91 CODX and 32 SBET logical lookups as cache hits.
Both cold and warm measurements above use the parser optimization and
content-keyed normalized-text cache. They preceded the pre-commit semantic
corrections documented above; those corrections were verified with offline regressions.

The zero-network warm-cache requirement and SBET's <250 ms warm target are met
in these API measurements. Default forensics' <200 ms warm and <1.8 s cold
targets are **not met**. Thirty cold HTTP requests require at least 5.8 seconds
between request starts under this client's limiter. That cold target requires
prewarming or a smaller scan, not additional workers. A prior smaller CODX
2-report / 1-supplement / 1-ownership scan took 2,372.734 ms cold (8 requests)
and 190.342 ms warm; it is a different scan, not a substitute for the default
requirement. All measurements are observations, not universal latency guarantees.

Implementation formulas and selection assumptions are also documented in
[definitions.md](definitions.md); command examples and output behavior are in
[README.md](../README.md).
