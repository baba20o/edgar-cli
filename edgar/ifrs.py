"""IFRS fallbacks validated against the IFRS Accounting Taxonomy 2025.

https://xbrl.ifrs.org/taxonomy/2025-03-27/full_ifrs/full_ifrs-cor_2025-03-27.xsd
Monetary amounts retain the issuer's reported currency; no FX conversion occurs.
IssuedCapital is monetary, not a count of shares. Total Borrowings and total
NoncurrentLiabilities are deliberately not treated as noncurrent debt.
"""

IFRS_ALIASES = {
    "revenue": "Revenue",
    "cogs": "CostOfSales",
    "gross_profit": "GrossProfit",
    "net_income": "ProfitLoss",
    "operating_income": "ProfitLossFromOperatingActivities",
    "cash": "CashAndCashEquivalents",
    "assets": "Assets",
    "assets_current": "CurrentAssets",
    "liabilities": "Liabilities",
    "liabilities_current": "CurrentLiabilities",
    "equity": "Equity",
    "inventory": "Inventories",
    "operating_cash_flow": "CashFlowsFromUsedInOperatingActivities",
    "investing_cash_flow": "CashFlowsFromUsedInInvestingActivities",
    "financing_cash_flow": "CashFlowsFromUsedInFinancingActivities",
    "capex": "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "dna": "AdjustmentsForDepreciationAndAmortisationExpense",
    "goodwill": "Goodwill",
    "intangibles": "IntangibleAssetsOtherThanGoodwill",
    "shares": "NumberOfSharesOutstanding",
    "shares_outstanding": "NumberOfSharesOutstanding",
    "debt": "LongtermBorrowings",
    "diluted_eps": "DilutedEarningsLossPerShare",
    "eps": "DilutedEarningsLossPerShare",
    "short_term_debt": "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
}


def append_ifrs_candidates(aliases):
    for alias, tag in IFRS_ALIASES.items():
        unit = "shares" if alias in {"shares", "shares_outstanding"} else None
        aliases[alias].append(("ifrs-full", tag, unit))
