# EDGAR exploration: the physical infrastructure behind AI

Research date: September 6, 2026, America/New_York. Captures occurred September 7 UTC. Scope: Microsoft, NVIDIA, and Vertiv, using the local EDGAR CLI and official filings available by the research date.

## What made this dive worthwhile

The most interesting connection is that AI deployment depends on several different things happening together: customers allocating money, suppliers reserving manufacturing capacity, projects obtaining financing and power, and equipment reaching completed sites. These disclosures make that chain concrete. They do not establish that every commitment will become an operating data center or that every company will earn attractive returns.

This was an actual CLI run: **20 captured commands**, covering filing discovery, XBRL concepts, cash-flow statements, earnings exhibits, filing sections, document downloads, and reconstructed free cash flow. Median command duration was **0.84 seconds**; all returned exit code 0. Successful execution did not always mean correct content, as the examples below demonstrate.

## 1. Microsoft: measure the spending before interpreting it

The annual `capex` concept resolved to cash purchases of property, plant, and equipment:

| Fiscal year ended June 30 | Cash PP&E purchases, USD billions |
|---|---:|
| 2024 | 44.477 |
| 2025 | 64.551 |
| 2026 | 115.948 |

That is approximately **79.6% growth in FY2026**. These are company-wide cash purchases, not an AI-only spending category. The normalized cash statement returned FY2026 operating cash flow of $182.935 billion; the CLI's explicit subtraction produced $66.987 billion of free cash flow. The inputs and arithmetic match the original cash-flow statement. [Microsoft FY2026 10-K](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm)

The July earnings call supplies a crucial qualification: Microsoft said a change in estimated building lives would cause more future data-center leases to be classified as operating leases. Those are excluded from its capex metric. Calendar-2026 capex guidance therefore became approximately $175 billion, while investment expectations outside that accounting effect remained unchanged. **A lower capex headline can reflect classification rather than less infrastructure.** [Microsoft FY26 Q4 call](https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q4)

Evidence: [annual facts](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/04-msft-capex.json), [cash statement](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/05-msft-cash-statement.json), [FCF calculation](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/18-msft-fcf.json). Fiscal years above follow actual start/end dates; the CLI's comparative fiscal labels are defective.

## 2. NVIDIA: financing the places that will use the chips

NVIDIA's July 26, 2026 quarter-end filing reports **$279 billion of supply/capacity commitments**, up from $119 billion the preceding quarter. Some agreements can be changed before firm orders, potentially at additional cost. It also discloses an August subsequent event: **up to $105 billion of conditional guarantees** supporting approximately **4.25 GW** of SB Energy infrastructure for an OpenAI affiliate. The first phase is expected in NVIDIA FY2029. This is contingent credit support, not current cash expenditure. [NVIDIA FY27 Q2 10-Q, Note 10 and MD&A](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda-20260726.htm)

My interpretation: the chip supplier is helping address customers' construction and financing constraints because hardware sales depend on places being ready to use the equipment. The numbers describe different obligations and must not be added into a single spending total.

Both the [management discussion](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/07-nvda-mda.json) and [financial notes](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/13-nvda-financial-notes.json) were retrieved through the CLI, then checked against the original filing.

## 3. Vertiv: demand and delivery run on different schedules

Vertiv's Q2 2026 net sales were approximately **$3.274 billion**, up **24% reported and 18% organically**. Management attributed some revenue timing shifts to supply-chain congestion and phased project execution. These are company-wide infrastructure results, not a standalone liquid-cooling revenue series. [Q2 2026 earnings release](https://www.sec.gov/Archives/edgar/data/1674101/000162828026050323/q22026exhibit991vrt07292026.htm)

Its earlier year-end backlog shows the timing issue: **$15 billion at December 31, 2025**, versus $7.2 billion a year earlier. Most was expected to ship within 12–18 months, and orders can be cancelled or rescheduled. That is a dated backlog disclosure, not the current backlog or guaranteed future revenue. [2025 10-K](https://www.sec.gov/Archives/edgar/data/1674101/000167410126000008/vrt-20251231.htm)

A concrete capacity example: Vertiv announced roughly **$50 million** of Ohio expansion investment in March 2026. Its Ironton expansion is expected to operate in **Q2 2027**, increasing liquid-cooling and chilled-water production capacity. This illustrates the lead time between responding to demand and supplying additional equipment. [Manufacturing announcement](https://www.vertiv.com/en-us/about/news-and-events/corporate-news/2026/vertiv-to-expand-ohio-manufacturing-to-boost-u.s.-production-of-critical-thermal-management-technologies-for-ai-data-centers/)

The CLI found the correct [earnings exhibit](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/08-vrt-earnings.json), downloaded that release successfully, and returned the [quarterly revenue facts](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/09-vrt-revenue.json). Its quarterly MD&A extraction failed, so that output was excluded from the substantive analysis.

## What using the CLI felt like

The strongest design choices are practical:

- JSON is the natural output for an agent. Facts carry dates, XBRL tags, accessions, source URLs, and period types.
- `filings → item` provides a short path from a company to its narrative. `earnings → exhibits` provides a similar path to the actual release.
- Computed FCF includes a formula and source-backed inputs. That lets the reader inspect the calculation.
- Explicit period filters and `--as-of` make careful research possible. A bounded annual cash statement was particularly useful.

The source has more capabilities than this run exercised. I did not test governance, institutional holdings, insiders, full-text search, local mirrors, subscriptions, or the full metric set. This is a bounded experience report, not a product-wide certification.

The biggest weaknesses concern **whether the returned content means what its label says**:

1. Vertiv's quarterly MD&A request returned Part II's share-repurchase section, labeled MD&A with high confidence.
2. The Microsoft filing download saved a 6 KB JavaScript viewer instead of the 8.6 MB filing.
3. Comparative Microsoft years all displayed FY2026, despite different date ranges.
4. NVIDIA's metric bundle mixed current-quarter revenue with older Q1 cash-flow facts and marked every metric non-stale.
5. Markdown section output silently retained only 2,000 characters of a 41,906-character section.
6. The FCF caveat claimed PP&E-only scope even when its selected tag included software and other intangible assets.

These are documented with evidence and acceptance criteria in [CLI rough edges](/mnt/c/projects/edgar-cli/CLI-ROUGH-EDGES.md). My next-slice vote is to fix section identity, document identity, and period selection before expanding the command surface.

## My assessment

I would use this again. Its strength is reducing the effort needed to move between a financial number and the filing that explains it. I would currently verify section identity and date ranges before relying on a synthesis.

Compared with our other explorations, Filmot helped investigate what people are saying; USPTO helped inspect technical approaches being disclosed; EDGAR helped examine resources and obligations behind execution. Together they support a useful research workflow: discover an idea, inspect its technical detail, then examine who is committing resources to it. That connection is more interesting to me than treating the CLIs as isolated search tools.

## Reproducibility and boundaries

- [Manifest](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/MANIFEST.json): exact canonical commands, UTC timestamps, timings, exit codes, raw stdout/stderr filenames. All 20 calls used `--no-cache`; 19 outputs parsed as JSON, and one intentionally requested Markdown.
- [Baseline](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/BASELINE.json): repository HEAD, hashes of 21 pre-existing modified files, and the exact shared dependency revision. Those files were preserved.
- The WSL interpreter lacked Rich and `research-cli-base`. Rich came from the existing temporary exploration dependencies; the user's exact `baba20o/research-cli-base` repository was cloned into `/tmp`. No production source was changed. The CLI used its existing repository-default SEC User-Agent.
- [Supplemental document manifest](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/DOCUMENTS.json): original SEC HTML retrieved separately from the CLI-returned `primary_doc_url`, capped at 12 MiB each, with hashes and extracted text. These direct downloads are explicitly not credited to the CLI's exhibit downloader.
- Microsoft call commentary and Vertiv's manufacturing announcement came from supplemental official web research. All other financial discussion above was cross-checked against filing/release evidence found through the CLI.
- [Validation record](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/VALIDATION.json) records arithmetic and observed discrepancies. [Artifact hashes](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/ARTIFACT-HASHES.json) cover the saved evidence and report, excluding the hash file itself.

No fixes, commits, or pushes were made during this exploration.
