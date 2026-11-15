# EDGAR CLI rough edges — September 6, 2026

Observed during the AI-infrastructure exploration of Microsoft, NVIDIA, and Vertiv. **Update September 7: F01/F02 published to origin/main in `e2aa67e` after Copilot implementation, parent verification, and Claude review. F03–F06 are historical observations that need revalidation against the newly fetched upstream fixes.** See [mission validation](/mnt/c/projects/edgar-cli/Insights/copilot-e1-2026-09-07/REVIEW-AND-VALIDATION.md). All six findings below have live CLI evidence. Source permalinks describe the original audited revision; failure artifacts remain historical records. An independent source review checked the causes, including reproducing the Vertiv section-selection failure from saved HTML.

[Exploration report](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/FINDINGS.md) · [Command manifest](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/MANIFEST.json) · [Baseline](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/BASELINE.json)

## F01 — Published September 7 (original priority: High): section extraction can return the wrong Part/Item with high confidence

Reproduction:

```bash
edgar --no-cache item VRT --form 10-Q --section 2 --as-of 2026-09-06 --max-chars 150000
```

[Observed response](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/14-vrt-mda.json): title `Management's Discussion and Analysis`, confidence `high`, but the 864-character text begins `ITEM 2. UNREGISTERED SALES OF EQUITY SECURITIES AND USE OF PROCEEDS` and discusses repurchases. It is Part II Item 2. Correct MD&A is Part I Item 2. Exit code 0.

Source review reproduced this from [original HTML](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/documents/vrt-direct.html): the correct heading is marked inline after a page's `Table of contents` link. Its curly apostrophe also fails the straight-apostrophe title match. A fallback then picks the later non-inline Item 2, supplies the hardcoded MD&A title, and treats a following Item as sufficient for high confidence. See [candidate selection](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/items.py#L105), [fallback](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/items.py#L136), and [confidence](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/items.py#L179).

A related boundary case appears in [Vertiv 10-K Business](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/17-vrt-business.json): its 5,739-character result stops inside a sentence introducing an Item 1A cross-reference. It too says high confidence and not truncated.

**Impact:** an agent can summarize the wrong section or mistake a partial section for the whole. Increasing `--max-chars` does not repair identification.

**Acceptance:** preserve structural heading boundaries; distinguish `(Part, Item)` in 10-Qs; normalize typography for title matching; reject conflicting headings; exclude inline cross-references from boundaries. The saved VRT 10-Q must yield actual Part I MD&A, and its 10-K Business must continue past the quoted cross-reference. Confidence must reflect identity/boundary evidence.

## F02 — Published September 7 (original priority: High): exhibit downloader can save an XBRL viewer shell as the filing

Reproduction:

```bash
edgar --no-cache exhibits 0001193125-26-323660 --cik 789019 --type-filter 10-K --download /tmp/edgar-msft-example
```

[Observed result](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/15-msft-original.json): document name `msft-20260630.htm iXBRL`; URL is SEC `/ix?doc=/Archives/.../msft-20260630.htm`; `downloaded_to` is populated. The artifact is **6,356 bytes**, title `XBRL Viewer`, containing JavaScript and no filing narrative. The index lists **8,585,501 bytes**. The [direct original](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/documents/msft-direct.html) has that expected size. Exit code 0.

The [index parser](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/api.py#L3532) collects the display badge as filename text; [URL construction](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/api.py#L1313) retains the viewer link; [downloading](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/cli.py#L2775) trusts those fields. Vertiv's ordinary EX-99.1 download succeeded, so this is a specific inline-viewer path failure.

**Acceptance:** resolve supported SEC viewer links to their underlying SEC Archives document, validate origin/path, derive a clean filename, and retain the viewer URL separately if useful. This command must save the actual `msft-20260630.htm` filing; a wrapper or unsuccessful document retrieval must not be reported as a completed filing download.

## F03 — High: fiscal labels describe the reporting filing rather than comparative fact periods

Reproduction:

```bash
edgar --no-cache concept MSFT capex --annual --limit 3 --as-of 2026-09-06
```

[Response](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/04-msft-capex.json) contains annual facts ending June 2026, June 2025, and June 2024, **all labeled `FY2026`**. The SEC `fy/fp` values belong to the 2026 reporting filing, which repeats comparative years. [Label construction](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/api.py#L3393) copies those fields directly. Vertiv comparative revenue and NVIDIA comparative YTD facts exhibit the same issue.

**Impact:** summaries, citations, tables, and period selection based on the fiscal label can misdate correct numbers. The actual `start/end` fields remain available and were used for this report.

**Acceptance:** distinguish the reporting filing's fiscal year/period from the fact's economic period. Derive a fiscal label only with sufficient issuer-calendar evidence; otherwise return explicit dates and an unknown label. Do not infer every issuer's fiscal year from calendar year. Cover a June year-end issuer and a 52/53-week issuer such as NVIDIA, including comparative facts from later filings.

## F04 — High: default metric bundle silently mixes current revenue and older cash-flow periods

Reproduction:

```bash
edgar --no-cache metrics NVDA --bundle revenue,cash-flow
edgar --no-cache concept NVDA capex --ytd --limit 2 --as-of 2026-09-06
edgar --no-cache concept NVDA operating_cash_flow --ytd --limit 1 --as-of 2026-09-06
```

[Metrics](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/10-nvda-metrics.json) returns revenue for the quarter ended July 26, 2026, but OCF and capex for Q1 ended April 26. Every row says `stale: false`. The more recent six-month facts are present: [capex $4.434B](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/11-nvda-capex-ytd.json) and [OCF $74.421B](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/20-nvda-ocf-ytd.json), both through July 26.

[Comparable-fact selection](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/api.py#L3645) discards all unframed facts whenever any framed facts exist. Because cumulative cash flows often lack frames, the old Q1 wins. The age-based stale flag does not signal that newer-period information exists. `metrics` also has no explicit period selector.

**Acceptance:** make period policy explicit. Return period/duration alignment metadata and warn when a bundle mixes periods. Do not silently discard newer YTD facts under a generic latest policy. If producing standalone quarters, derive them only from compatible cumulative inputs with provenance; never relabel a six-month number as a quarter. Cover the saved NVIDIA case and a filer that directly reports standalone cash-flow quarters.

## F05 — Medium: Markdown item output silently clips at 2,000 characters

Reproduction: run F01-style `item` retrieval for MSFT's 10-K Item 7 with `--max-chars 150000`, once as JSON and once with `--markdown`.

The [JSON result](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/06-msft-mda.json) contains **41,906 characters**, not truncated. The [Markdown result](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/12-msft-mda-markdown.txt) uses a Rich panel and includes only the first 2,000 section characters, without an ellipsis or a rendered-truncation warning. [Renderer](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/cli.py#L1563) bases its ellipsis on API truncation, not its own slice.

**Acceptance:** honor requested output limits consistently; either emit the requested section in actual Markdown or make a preview limit explicit with returned/full lengths and a recovery command. JSON and Markdown should identify the same section and expose any content omitted by their own renderer.

## F06 — Medium: FCF's scope caveat contradicts the selected capex tag

Reproduction:

```bash
edgar --no-cache reconstruct NVDA --metric fcf --period-type annual --as-of 2026-09-06
```

[Result](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/19-nvda-fcf.json) uses `PaymentsToAcquireProductiveAssets` but says its capex scope is PP&E only and excludes capitalized software. The actual [returned concept description](/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06/11-nvda-capex-ytd.json) includes software and other intangible assets.

The [alias map](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/api.py#L114) permits both PP&E-only and broader productive assets; [FCF computation](https://github.com/baba20o/edgar-cli/blob/0d01c1cb72252f04095608dd7027aa358b105633/edgar/compute.py#L415) applies a fixed caveat. The arithmetic is not the defect. The definition attached to the result is.

**Acceptance:** generate scope metadata from the resolved tag, including broader or unknown scope as appropriate. Documentation must not promise PP&E-only calculations while accepting broader tags. Neither tag should be presented as an AI-only investment measure or automatically lease-inclusive.

## Next slice and operational notes

Next slice after the verified F01/F02 repairs: **F03/F04**, then F05/F06. These protect the meaning of evidence before adding features. Use the saved real filings as regression cases; network access should not be required for those tests. Exercise both structured and human-facing output.

Environment friction: this WSL interpreter lacked Rich and the shared `research-cli-base` package. The run used temporary dependencies and a clone of the user's exact shared repository. This is recorded as setup friction, not proof that normal installation is broken. A clearly documented bootstrap path for the shared package would help a fresh agent.

Telemetry observation: every `--no-cache` response carrying cache metadata reported zero cache calls/hits/misses, although network retrieval succeeded. These fields are cache counters, so they cannot be used to infer request counts or network latency. Optional separate request telemetry would improve evaluation.

Documentation drift: `docs/definitions.md` says TTM has no annual-plus-YTD fallback, while README describes one and current source contains that machinery. This run did not exercise TTM; reconcile the documentation during a later maintenance pass.

The exploration preserved all 21 pre-existing modified files. New evidence and documentation are uncommitted. Do not treat this report as approval to overwrite existing work or as a request for a broader refactor.
## Pre-commit review follow-ups — September 7, 2026

Claude's review on current GitHub main found one blocking regression in the
F01 implementation: strict catalog-title matching rejected legitimate caption
variants. The release repair retains unfamiliar structural captions at low
confidence, rejects known conflicting titles, and recognizes both Item 15
caption spellings. Six regression cases were added; the release suite passed
182 tests, with two optional saved-original tests skipped.

The review also identified follow-ups to investigate separately: unlinked TOC
rows and page ranges can still produce overconfident short sections; unusual
index rows currently fail the document listing rather than producing per-row
errors; some linked/fragmented heading and Part-transition layouts remain
heuristic; CLI text output should expose Part identity and confidence reasons;
and representative trimmed original fixtures should become distributable tests.
Mirror-only extraction remains low confidence, with no explicit live override.
These are retained observations, not claims that those additional cases were
reproduced or repaired here. See
[the full review](Insights/copilot-e1-2026-09-07/PRE-COMMIT-CLAUDE-REVIEW.md).
