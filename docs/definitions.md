# Canonical Metric Definitions

Computed metrics in this CLI follow a single canonical definition each. Where
analysts disagree (EBITDA SBC treatment, FCF capex scope, etc.), the choice is
documented below. An `--adjusted` variant may be added later as a separate key
with its own envelope.

Every computed metric envelope returns the formula plus its inputs:

```json
{
  "metric": "gross_margin",
  "value": 0.4624,
  "formula": "(Revenue - CostOfGoodsAndServicesSold) / Revenue",
  "inputs": [{"label": "...", "tag": "...", "val": ..., "source_url": "..."}],
  "caveats": [...],
  "missing_inputs": [...]
}
```

If `value` is `null`, see `missing_inputs` for which alias did not resolve.

## Margins

- **gross_margin** = `(Revenue - CostOfGoodsAndServicesSold) / Revenue`. Falls
  back to `GrossProfit / Revenue` when GP is reported directly.
- **operating_margin** = `OperatingIncomeLoss / Revenue`.
- **net_margin** = `NetIncomeLoss / Revenue`.
- **ebitda_margin** = `(OperatingIncomeLoss + DepreciationDepletionAndAmortization) / Revenue`.
  *Caveat:* SBC is not added back. Some analysts add SBC for an "adjusted EBITDA"; this CLI does not (yet).

## Returns

- **roe** = `NetIncomeLoss / StockholdersEquity` (period-end equity).
- **roa** = `NetIncomeLoss / Assets` (period-end assets).
- **asset_turnover** = `Revenue / Assets`.
- *Not implemented:* ROIC. Requires NOPAT (taxed operating income) plus debt
  components; definitional variance high.

## Liquidity

- **current_ratio** = `AssetsCurrent / LiabilitiesCurrent`.
- **quick_ratio** = `(AssetsCurrent - InventoryNet) / LiabilitiesCurrent`.
  Inventory treated as zero when missing (with a caveat on the envelope).

## Leverage

- **debt_to_equity** = `(LongTermDebtNoncurrent + ShortTermDebt) / StockholdersEquity`.
- **debt_to_ebitda** = `(LongTermDebtNoncurrent + ShortTermDebt) / (OperatingIncomeLoss + D&A)`.
- **net_debt** = `LongTermDebtNoncurrent + ShortTermDebt - Cash`.

## Free cash flow

- **fcf** = `OperatingCashFlow - CapEx`. *Caveat:* CapEx scope is PP&E only
  (`PaymentsToAcquirePropertyPlantAndEquipment`). Acquisitions and capitalized
  software costs are excluded.
- **fcf_margin** = `fcf / Revenue`.

## Reconstructions (`edgar reconstruct`)

- **ebitda** = `OperatingIncomeLoss + D&A` (no SBC adjustment).
- **fcf** = same as above.
- **net_debt** = same as above.
- **working_capital** / **nwc** = `AssetsCurrent - LiabilitiesCurrent`.
- **tangible_book_value** = `StockholdersEquity - Goodwill - IntangibleAssetsNetExcludingGoodwill`.
  Goodwill / intangibles default to zero when missing (with a caveat).

## Out of scope

These are reported in the ratios envelope as `not_applicable` so agents do not
retry them on this CLI:

- **pe_ratio**, **ev_ebitda**, **dividend_yield** — require market price data,
  which SEC does not publish.

## TTM (`edgar ttm`)

Trailing twelve months first uses the four most recent contiguous quarterly
facts. When four quarters cannot be reconstructed, it can use
`AnnualFY + CurrentYTD - PriorYTD` for compatible stub periods. A first-quarter
fact is also a three-month YTD candidate. The result names the formula and
source facts used.

## Trend (`edgar trend`)

- **slope** is least-squares slope over the displayed values (no normalization).
- **label** is `expanding` / `contracting` / `stable` / `inflecting` based on the
  ratio of `|slope|` to the average absolute value (threshold 0.5%) plus a
  sign-change check between the first and second half of the series.

## Latest value and staleness (`edgar metrics`, `edgar brief`)

**Which fact is "latest".** An alias such as `revenue` maps to several tags
(`RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`,
`RevenueFromContractWithCustomerIncludingAssessedTax`, ...). The metric is the
fact covering the freshest period across all of them, chosen in this order:

1. the latest period end;
2. for one end date, a framed fact (SEC's calendar-aligned, comparable one)
   before an unframed one;
3. the longer period;
4. the latest filing (a restatement wins).

A newer unframed fact (a half-year or year-to-date figure) is never passed
over for an older framed one; its `period_type` and `period_length_days` say
what it covers. A fact "filed" before its own period ended is a mis-dated
context and is ignored when any other fact exists.

**`stale`.** True when the figure is not the filer's latest reported period:

1. **Superseded** — the filer's latest periodic report covers a period ending
   more than 7 days after the fact's period end. Periodic reports are 10-K,
   10-Q, 20-F and 40-F (and their amendments and transition forms), plus a
   6-K that carries XBRL financial statements (a foreign private issuer's
   interim accounts). The report is named in `latest_periodic_report` and in
   the metric's `stale_reason`.
2. **Old** — no such report is known, but the fact's period ended more than
   548 days before `reference_date` (the filer's latest filing date).

`age_days` is `reference_date` minus the fact's period end. A fresh fact has
`stale: false` and no `stale_reason`.

**`warnings`.** SEC's XBRL APIs can lag or omit a filing. When the filing list
shows a report with XBRL financial data that is newer than every fact SEC's
companyfacts API serves for the filer, `metrics`, `concept` and `facts` add a
`warnings` entry with code `xbrl_filing_missing_from_sec_api` naming the
filing. The figures returned then predate that report; read it with
`edgar text`.

## Period assumptions

- Computed metrics consume only one period kind at a time (`--period-type
  annual` is the default for ratios; `--quarterly` is the default for trend).
- `--as-of` filters underlying filings and facts by SEC acceptance time.
  A date includes the whole UTC day; a timestamp requires `Z` or a UTC offset.
  Without a valid acceptance timestamp, an intraday query requires a filing
  date earlier than the previous UTC day, providing one full extra day of margin. Entity/ticker metadata remains current; historical trading
  universes and unfiled news are outside this availability filter.


## IFRS aliases and currency

Core aliases retain their existing US-GAAP/DEI precedence and append IFRS
fallbacks. The selected fact retains its reported unit and currency. Canonical
union does not collapse facts with different units, and no FX conversion is
performed. `NumberOfSharesOutstanding` is a count; `IssuedCapital` is a monetary
amount. IFRS `LongtermBorrowings` supplies a noncurrent debt component;
`CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings` supplies the current
component. Total `Borrowings` and total liabilities are not interchangeable
with either component.

## Baby-shelf scenario (`compute_baby_shelf`)

The calculator consumes extracted I.B.6 / I.B.5 statements from one latest
filing and SEC registration file number. It does not fill gaps from an older
filing or another shelf. Multiple conflicting statements remain ambiguous.
Each amount uses `{metric, value, unit, formula, inputs, caveats}`; missing or
rejected inputs add `missing_inputs`. Input records retain source URL,
accession, dates, unit and `origin` (`filing` or `caller`).

| Output | Definition |
|---|---|
| `non_affiliate_float_shares` | Explicitly disclosed non-affiliate share count, or a caller override. |
| `stated_MVPHS` | Market value of shares held by non-affiliates as stated in the selected filing. |
| `calculated_MVPHS` | Non-affiliate shares × reference price. |
| `MVPHS` | Filing-stated value by default when available; shares × price when a caller supplies any scenario override. |
| `gross_12m_cap` | MVPHS / 3 when MVPHS < $75 million. |
| `prior_12m_ib6_sales_usd` | Explicitly disclosed prior 12-month sales under the limitation. Missing sales are unknown, not zero. |
| `usable_capacity_usd` | max(0, gross cap − prior 12-month sales). |
| `stated_remaining_capacity_usd` | Remaining capacity quoted by the filing, preserved independently of calculated capacity. |
| `escape_price_75m` | $75 million / non-affiliate shares; a threshold-price scenario. |

`high_60d_close` (CLI `--high-60d`) takes precedence over `price` when both are
supplied. Neither argument verifies market data or a qualifying 60-day closing
price. Share counts retain their original dates without adjustments for later
splits, issuances or affiliate changes. Non-USD disclosures are not converted.
At or above $75 million, the one-third cap and usable-capacity outputs are null
with a caveat; the calculator does not substitute an unlimited or full-shelf
capacity. The threshold is not a permanent exemption. Other eligibility
conditions, unused registered capacity, intervening sales and concurrent
offerings can constrain the result. See the SEC's [Form S-3 I.B.6](https://www.sec.gov/files/forms-3.pdf),
[Form F-3 I.B.5](https://www.sec.gov/files/formf-3.pdf) and
[Forms interpretations](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/securities-act-forms).

Disagreement is exposed, not repaired. CODX's July 27, 2026 prospectus states
$49,425,570 MVPHS, 5,153,941 shares and an $11.19 reference price, but the product
is $57,672,599.79. Using stated MVPHS and stated sales of $11,322,816 yields
$5,152,374 capacity; using the product yields $7,901,383.93, close to its separately
stated $7,901,383. The calculator retains all four amounts and emits
`float_arithmetic_disagreement` and `capacity_arithmetic_disagreement`.
[Source prospectus](https://www.sec.gov/Archives/edgar/data/1692415/000149315226034835/form424b5.htm).

## Historical cash runway (`compute_cash_runway`)

The calculator requires aligned, non-stale USD facts at the snapshot period.
It rejects stale, conflicting, invalid-date, incompatible-currency or mismatched
period inputs. It does not backfill from a stale fact when a newer input is
missing. Period duration follows the existing elapsed-day convention,
`end - start`, and a supplied duration is checked against those dates.

| Output | Definition |
|---|---|
| `cash_usd` | Reported cash at the balance date. |
| `monthly_operating_burn_usd` | −OCF / (period_length_days / 30.4375), only when OCF < 0. |
| `runway_months` | Cash / monthly operating burn. |
| `runway_days` | Runway months × 30.4375. |
| `net_working_capital_usd` | Current assets − current liabilities at the same period end. |
| `total_debt_usd` | Compatible noncurrent debt + current debt at the same period end. |

Nonnegative operating cash flow produces null burn/runway with a caveat,
not infinite runway. Missing or stale debt remains unknown, not zero. A total
debt tag cannot serve as the noncurrent component before adding current debt.
Even valid component tags may omit other borrowing categories.

This is a historical extrapolation assuming the observed operating outflow
continues. It does not add subsequent financing, estimate spending after the
balance date or establish that restricted cash is available for operations.
Using CODX's September 30, 2026 cash of $714,803 and OCF of −$20,683,301 over
272 days gives $2,314,514.61 monthly burn, 0.308835 months and 9.400164 days.
Those figures do not describe its live cash balance; the report also discloses
subsequent secured financing. [CODX Q3 report](https://www.sec.gov/Archives/edgar/data/1692415/000149315226050963/form10-q.htm).

## Share-supply and registration evidence

`float` and `forensics.share_supply` keep XBRL cover shares, dated filing
statements, explicit non-affiliate shares, EntityPublicFloat dollars,
post-baseline closed-issuance evidence and pending/ambiguous offering evidence
separate. No total is formed by adding these sets. A derivative's underlying
shares are not an outstanding-share baseline. Counts are not automatically
split-adjusted; large unexplained XBRL/text disagreements produce warnings.

13D/G ownership is deduplicated by reporting person and security class using
filing availability, while officer/director holdings retain ownership type,
derivative status and source date. Rights to acquire securities and overlapping
groups make summing owners or subtracting them from outstanding shares unsafe.
This output is evidence for review, not a computed freely tradable float.
[SEC beneficial ownership interpretations](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/exchange-act-sections-13d-13g-regulation-13d-g-beneficial-ownership-reporting).

Offering `closing_status` describes the cited sentence. Registration status,
closing and resale availability are separate questions; an earlier pending
statement can be superseded. Where later closing evidence might refer to an
earlier announcement but linkage is uncertain, the composite exposes unresolved
status instead of declaring that transaction currently unclosed.

Registration histories distinguish `effective`, `effectiveness_unknown`,
`withdrawal_requested`, `withdrawal_status_unknown` and `withdrawn`.
An EFFECT filing or automatically effective form is positive evidence of
historical effectiveness; missing EFFECT is not proof of ineffectiveness.
Current availability, expiration/time limits, eligibility and unused capacity
are explicitly not assessed from an old EFFECT or ASR record. RW requests,
AW amendment requests, withdrawal rescissions and POS AM amendments retain
their evidence and scope. `withdrawn` requires explicit supporting confirmation
for the whole registration; file-number equality alone is insufficient.
This conservative extractor can leave a legally completed withdrawal unresolved
when the required evidence is outside the scanned history. See the
[SEC Filer Manual](https://www.sec.gov/files/edgar/filermanual/efmvol2-v76-d.pdf)
and [Rule 477](https://www.govinfo.gov/content/pkg/CFR-2025-title17-vol3/pdf/CFR-2025-title17-vol3-sec230-477.pdf).
