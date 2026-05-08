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

## Period assumptions

- Computed metrics consume only one period kind at a time (`--period-type
  annual` is the default for ratios; `--quarterly` is the default for trend).
- `--as-of` filters every computed metric's underlying facts to those filed on
  or before that date, eliminating look-ahead bias.
