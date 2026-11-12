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

Trailing twelve months is the sum of the four most recent contiguous quarterly
facts for a metric. The CLI does not currently fall back to `(FY + Q1 + Q2 + Q3 - prior_year_Q1 - prior_year_Q2 - prior_year_Q3)`
when a fresh quarter is unavailable; that variant may land later if needed.

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
- `--as-of` filters every computed metric's underlying facts to those filed on
  or before that date, eliminating look-ahead bias.
