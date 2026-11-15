# edgar-cli: Fable Audit

Status: **audit findings awaiting triage**, not certified bugs or accepted fixes.

- Published: 2026-10-22T16:14:09.514Z
- Audited local HEAD: `e2aa67eb9ba83c8f517b7a52a6e67eb5dacc2c7d`; original worktree dirty: true.
- Sanitized working-file snapshot: `923d42f0ba24a273c58a7d675ad5954513ea14035b6d1c22bc0f3022e18b8f01` (96 files).
- Requested model/effort: `claude-fable-5-1` / `high`. Provider model: `claude-fable-5-1`; effort: `requested_unconfirmed`.
- UAF task: `task_599c859554d542388ef80b4688b75440`; run: `run_dfdb799d5dde42b2a8b29d74d7dba670`; worker report: `evt_b6c4cf64050641d49d85a35bb39c486d`.
- Exact provider report SHA-256: `230f914b5ea4172aeb592443bf25d2c2322a1d7ab1151f86860e3a500fdd059e`.
- Observed tool calls: 44, limited to Read/Grep/Glob; tool-error results: 0. No tests were authorized or run by this audit harness.
- Snapshot files rehashed unchanged before publication. This does not prove exclusive OS-level read containment or that other editors left the original repository unchanged.
- Secret/private/generated-content exclusions apply; the scan is not exhaustive. No repairs or commits performed.

The following is the model's report. Findings and factual claims still need owner triage and appropriate reproduction.

---

# edgar-cli — Static Repository Audit (read-only)

**Status: static audit leads awaiting triage. No tests were run. No source changes were made. Nothing below is reproduced evidence; everything is static reading of the sanitized snapshot.**

## 1. Executive summary

edgar-cli is a Click CLI over SEC's keyless `data.sec.gov` endpoints: `edgar/api.py` (client, caching, fact enrichment, ~40 composite workflows), `edgar/cli.py` (command surface, exit codes, output renderers), `edgar/compute.py` (derived metrics with formula/provenance envelopes), extractors (`items.py`, `insiders.py`, `holders.py`, `governance.py`), a SQLite mirror (`mirror.py`), an endpoint-aware file cache (`cache.py`) and a high-water-mark state store (`state.py`). The codebase is unusually well-instrumented (caveats, `missing_inputs`, period alignment warnings) and the recent F01/F02 repairs (viewer-shell downloads, Part/Item selection) have solid regression coverage.

The ten highest-value leads, ordered by estimated impact:

| ID | Area | One line | Severity | Confidence |
|---|---|---|---|---|
| F01 | `holders.py` | 13F value unit chosen by a $10B magnitude heuristic → 1000× overstatement for most post-2023 filers | High | High (logic) / Medium (prevalence) |
| F02 | `api.submissions` + `pending`/`mark-seen` | High-water drain can permanently skip filings (truncation past `limit`, same-day filings, `mark-seen` defaults to today) | High | High |
| F03 | `expand_group("@sic:")` | Documented `@sic:NNNN` group reads a `sic` field the ticker map does not carry → always empty | Medium | High |
| F04 | `reconstruct tangible_book` | Goodwill/intangibles hard-coded `None` though aliases exist → value == equity with a misleading "missing" caveat | Medium | High |
| F05 | `_get` 429 path | Exhausted rate-limit retries exit 4 ("outage") with message `…: None`, not the documented exit 3 | Medium | High |
| F06 | `_get` 403 path | Transient 403 (SEC throttle/UA rejection) negative-cached for 1 h | Medium | High (logic) |
| F07 | `html_to_text` | Strips all `<table>` content before governance audit-fee extraction and mirror body FTS | Medium | Medium-High |
| F08 | `amendments` | `rstrip("/A")` corrupts base form for `DEF 14A/A`, `N-1A/A`, … → primary never paired | Low-Med | High |
| F09 | `quality`/`delta` | `--as-of` not applied to restatement probes (look-ahead leak; contradicts docs) | Low-Med | High |
| F10 | alias map | `eps` resolves to `EarningsPerShareDiluted`; income statement shows two identical lines | Low-Med | High |

Plus a documentation-drift list (14 pairs), hypotheses that need runtime confirmation, and three previously reported rough edges confirmed still present in source.

## 2. Snapshot and scope

- Repository: `edgar-cli`, HEAD `e2aa67eb9ba83c8f517b7a52a6e67eb5dacc2c7d`, `original_dirty: true`, captured `2026-10-22T15:36:19.866Z`, snapshot sha256 `923d42f0…8f01` (per `AUDIT-SCOPE.json`).
- Scope read: `source/**` and `AUDIT-SCOPE.json` only. Tools: Read/Grep/Glob. No shell, network, tests, installs, writes.
- `AUDIT-SCOPE.json` excluded three files (1 private/data-format, 1 size-limit, 1 non-source). Those were not inspected; conclusions about data fixtures may be incomplete.
- The external dependency `research_cli_base` (`BaseAPIClient`, `FileCache`, `SharedRateLimiter`, `retry_wait_seconds`) is not in the snapshot; behaviour attributed to it (default `max_retries`, `request_timeout`, session headers) is assumed, not read.
- Paths below are repository-relative (no `source/` prefix).

## 3. Reproduced facts

None. No permitted operation in this pass could execute code or hit SEC, so no finding is reproduced. All items are static-supported defects or hypotheses, labeled per finding.

## 4. Prioritized findings (static-supported unless marked hypothesis)

### EDGAR-CLI-F01 — 13F `value` unit decided by a $10B total-magnitude heuristic (1000× error for sub-$10B filers post-2023)

- **Evidence:** `edgar/holders.py:104-115` — `in_thousands = total_raw < 10_000_000_000`, then `value_usd = value_raw * (1000 if in_thousands else 1)`. The comment block at `holders.py:83-89` describes a *per-fact* implausibility check that is not what the code does (it is per-filing total). Module docstring `holders.py:9` still says value is "in thousands of USD per SEC schema". Callers `edgar/api.py:1772` and `:1858` have the chosen filing (`reportDate` is read at `:1758`) but do not pass it to the parser.
- **Trigger:** `edgar holdings <any 13F filer with total reported value < $10B>` for a period ending ≥ 2022-12-31 (SEC switched 13F values from thousands to whole dollars for periods ending on/after that date). The 13F filing threshold is $100M AUM, so most filers fall in this band. `edgar holders … --candidates <mixed list>` sums across filers with potentially different inferred conventions.
- **Impact:** `value_usd`, `total_value_usd`, `weight`, `top_concentration`, cross-filer totals all 1000× too large; `value_unit_convention` confidently says `"thousands"`. Agents are told to trust this field.
- **Severity:** High. **Confidence:** High that the logic behaves as described; Medium on real-world prevalence (depends on filer size mix — no live check performed).
- **Existing safeguards:** `value_unit_convention` is exposed per row; caveat text on `holdings`. Tests `tests/test_api.py:1323-1382` *encode* the heuristic (a $50K total is asserted to be "thousands"), so they will not catch the defect.
- **Smallest correction:** Add `report_date: Optional[str]` to `parse_infotable_xml`; if `report_date >= "2022-12-31"` use multiplier 1, else 1000; fall back to the magnitude heuristic only when `report_date` is missing, and set `value_unit_convention` to `"absolute (by period)"` / `"thousands (by period)"` / `"…(heuristic)"`. Thread `chosen.get("reportDate")` from `api.py:1772` and `:1858`.
- **Proposed regression test:** small infotable XML totaling `2_000_000_000` with `report_date="2025-12-31"` → `value_usd == 2_000_000_000`, convention `absolute`; same XML with `report_date="2021-12-31"` → ×1000; no `report_date` → current heuristic. Update `test_holders_parse_infotable_xml_pre_2023_thousands` to pass a pre-2023 date explicitly.

### EDGAR-CLI-F02 — `--since-last-fetch` / `pending` high-water logic can permanently skip filings

- **Evidence:**
  - `edgar/api.py:668-673` sets `start_date = _next_day(hw_date)` (exclusive of the HWM day).
  - `api.py:676-685` fetches `limit + 1` newest matches and keeps `filings = recent_matches[:limit]` (SEC `recent` is newest-first).
  - `api.py:739-743` advances the HWM to the **newest** returned filing even when `recent_truncated` is true (the truncation is only turned into `warning` text at `:732-733`).
  - `edgar/cli.py:2099-2101` (`pending`) uses `limit=50` and ignores `warning`; `cli.py:2128-2129` (`mark-seen`) defaults `filed` to **today** when `--filed` is omitted (README example at `README.md:224` omits it).
  - `edgar/state.py:66-68` refuses to regress the mark, so once advanced the window is closed for good.
- **Trigger (three paths):** (a) a subscription (especially `form="*"`, or Form-4-heavy filers) accrues > 50 new filings between drains: filings older than the newest 50 but newer than the old mark are dropped and the mark jumps past them; (b) two filings on the same date where only the first was present at drain time (SEC posts filings throughout the day): the later one is excluded forever by `_next_day`; (c) `edgar mark-seen AAPL <accn> --form 8-K` without `--filed` sets the mark to today, hiding every not-yet-drained filing dated ≤ today.
- **Impact:** Silent data loss in the README's flagship "subscribe + drain" workflow (`README.md:47-50, 220-228`) and in `events --since-last-fetch` / `delta --use-state`.
- **Severity:** High. **Confidence:** High (pure control-flow reading).
- **Existing safeguards:** `warning` string in the `filings`/`company` envelope (not surfaced by `pending`); `--all` is not used by `pending`.
- **Smallest correction:** (1) in `submissions`, when `recent_truncated` and `since_last_fetch`, advance the mark only to the **oldest** returned filing's date (or do not advance and emit `truncated: true`); (2) store the set of accessions seen on the HWM date and use `start_date = hw_date` (inclusive) with accession-set exclusion instead of `_next_day`; (3) make `--filed` required for `mark-seen` or default it to the filing's actual date via `latest_filing`.
- **Proposed regression tests:** stub `_get` with 60 same-form filings newer than the mark and `limit=50` → after the call the mark must not exceed the oldest returned date (or truncation flag set); stub two 8-Ks on the same `filingDate`, drain with only the first present, add the second, drain again → the second must appear; `mark-seen` without `--filed` must not set a future-relative mark.

### EDGAR-CLI-F03 — `@sic:NNNN` group expansion cannot return anything

- **Evidence:** `edgar/api.py:1091-1103` filters `self.companies()` rows on `c.get("sic")`, but `companies()` (`api.py:571-585`) zips the fields of `company_tickers_exchange.json` (cik/name/ticker/exchange) and the code's own `peers` docstring states "The SEC ticker/CIK/exchange map does not carry SIC codes" (`api.py:1145-1146`). README advertises the feature (`README.md:107`) and BACKLOG marks it shipped (`docs/BACKLOG.md:232, 383`).
- **Trigger:** `edgar metrics --tickers @sic:3571 …` or `edgar resolve @sic:3571`.
- **Impact:** `_expand_groups` (`cli.py:339-354`) yields an empty list → `_collect_identifiers` raises the generic UsageError "Provide an identifier…" (`cli.py:381-382`, exit 5) — misleading; `resolve` returns `total: 0` with exit 0.
- **Severity:** Medium (documented feature non-functional). **Confidence:** High (internal contradiction; no live check needed).
- **Existing safeguards:** none; no test covers `@sic:` (only `@13f-top`, `tests/test_api.py:1310-1320`).
- **Smallest correction:** either return `{"error": "@sic groups are not supported: the SEC ticker map has no SIC field; use `edgar peers` with a candidate set"}` and remove the README/BACKLOG claims, or implement via the mirror DB (`filers.sic`) when `--db` is given.
- **Proposed regression test:** stub `companies()` with rows lacking `sic` → `expand_group("@sic:3571")` must return an explicit error (or, after implementation, non-empty identifiers from a stub carrying `sic`).

### EDGAR-CLI-F04 — `reconstruct tangible_book` ignores goodwill/intangibles despite available aliases

- **Evidence:** `edgar/api.py:3220-3225` passes `None, None` with comment "goodwill alias not in COMMON_CONCEPT_CANDIDATES yet", but `goodwill` and `intangibles` aliases exist at `api.py:231-237` and are used by `statement("balance")` (`api.py:2197`). `compute.tangible_book_value` (`compute.py:468-485`) treats missing as zero and emits caveat "Goodwill / Intangibles treated as zero when missing" plus `optional_missing_inputs`.
- **Trigger:** `edgar reconstruct <any filer> --metric tangible_book`.
- **Impact:** Returned value equals `StockholdersEquity` for every filer, labeled as tangible book with a caveat implying the data was absent (it was never requested). `docs/definitions.md:65-66` and `README.md:172` document the subtraction.
- **Severity:** Medium (wrong number, misleading provenance). **Confidence:** High.
- **Existing safeguards:** the caveat text and `optional_missing_inputs` do disclose the zero treatment.
- **Smallest correction:** replace the two `None`s with `self._latest_fact_for_alias(cik, "goodwill", period_type=instant_pt, as_of=as_of)` and `…"intangibles"…` (ideally anchored to the equity period end via `_instant_fact_at_period_end`); delete the stale comment.
- **Proposed regression test:** stub `_latest_fact_for_alias` to return equity 1000, goodwill 200, intangibles 100 → `value == 700` and no `optional_missing_inputs`.

### EDGAR-CLI-F05 — Rate-limit exhaustion maps to exit 4 with message "…: None"

- **Evidence:** `edgar/api.py:509-513` handles 429 with `continue` and never sets `last_error`; after the loop `api.py:551-553` returns `"Failed after N attempts: None"`. `cli._exit_code_for_error` (`cli.py:240-264`) checks for "429"/"rate limit" first (→3) but this string has neither, so the "failed after" branch (`cli.py:262`) returns 4. README exit-code table (`README.md:52-60`) and BACKLOG (`docs/BACKLOG.md:294`) promise 3 for rate-limited.
- **Trigger:** SEC returns 429 on every retry attempt.
- **Impact:** Agents branching on exit 3 vs 4 (back off vs. treat as outage) get the wrong signal; the error text is uninformative.
- **Severity:** Medium (stable-exit-code contract). **Confidence:** High.
- **Existing safeguards:** `log.warning` per 429 (stderr via logging lastResort); `_get_text`/`_get_bytes` paths (`api.py:555-569`) have no retry at all but their `HTTPError` text contains "429" and maps correctly when surfaced.
- **Smallest correction:** in the 429 branch set `last_error = f"429 Too Many Requests (rate limited) from {url}"` before `continue`; optionally return immediately after the last attempt with that message.
- **Proposed regression test:** fake `session.get` returning status 429 for all attempts → `_get` error text contains "429" and `_exit_code_for_error(text) == 3`. Add a CLI-level test via `CliRunner` asserting `exit_code == 3`.

### EDGAR-CLI-F06 — 403 responses are negative-cached for one hour

- **Evidence:** `edgar/api.py:515-523` treats 403 like 404: `cache.set(url, params, err, negative=True)`; `cache.py:29` `NEGATIVE_TTL = 3600`, applied at `cache.py:125`. README says only "404s 1h" (`README.md:240`); BACKLOG `docs/BACKLOG.md:423` says 403 caching is intentional "so typos and dead concepts do not burn rate-limit slots".
- **Trigger:** SEC answers 403 for a throttled client or an undeclared/blocked User-Agent (SEC's documented behaviour for automated-access violations), then recovers.
- **Impact:** For the next hour every command touching that URL (e.g., the ticker map, a filer's submissions) returns the cached "403 Forbidden" with exit 4, even after the user fixes `SEC_USER_AGENT` or the throttle lifts. `--no-cache` or `cache invalidate` is the only escape and the message does not say so.
- **Severity:** Medium (availability). **Confidence:** High on logic; the design intent is a documented counter-argument, so this is a risk trade-off to triage rather than an unambiguous bug.
- **Existing safeguards:** `--no-cache`, `cache invalidate '*'`, `cache.negative` surfaced in the envelope.
- **Smallest correction:** negative-cache only 404 (or 403 with a much shorter TTL, e.g., 60 s), and append "cached negative response; use --no-cache or `edgar cache invalidate`" to the error when served from cache (`api.py:467-471`).
- **Proposed regression test:** fake session returns 403 then 200 for the same URL with a fresh `EdgarCache(tmp_path)` → second `_get` must perform a network call and return the payload.

### EDGAR-CLI-F07 — `html_to_text` removes all `<table>` content before governance and mirror-body extraction

- **Evidence:** `edgar/api.py:3848-3852` deletes `<table.*?</table>` (non-greedy, re.S) before stripping tags. It feeds `governance()` (`api.py:1583`) and `mirror_filer --with-bodies-for` (`api.py:2378`). `governance._AUDIT_FEE_PATTERNS` are written for "table-style 'Audit Fees … 12,345,678' rows" (`governance.py:19-38`). A table-preserving converter already exists (`items.html_to_section_text`, `items.py:165-171`) and is used by `filing_section` (`api.py:1653`).
- **Trigger:** `edgar governance <filer>` on a proxy whose principal-accountant-fees disclosure is tabular (the common layout); `edgar search … --db` over bodies ingested with `--with-bodies-for 10-K` for numeric/table content.
- **Impact:** `audit_fees` silently empty or limited to prose mentions; FTS misses every number that lives in a table (financial statements, fee tables). The non-greedy regex also leaves the tail of nested tables as orphan text, so output is inconsistent rather than cleanly "tables removed".
- **Severity:** Medium. **Confidence:** Medium-High (table stripping is certain; that fee disclosures are tabular is domain knowledge, not verified against a fixture here).
- **Existing safeguards:** `caveat` on governance output; `test_governance_extract_audit_fees` (`tests/test_api.py:1509-1522`) exercises plain text only, so it cannot detect this.
- **Smallest correction:** use `items_mod.html_to_section_text` (or a variant that keeps `<tr>` rows as lines) for the governance body and for mirror bodies; keep table-stripping only in `latest_earnings` highlights where it is intentional.
- **Proposed regression test:** HTML fixture with `<table><tr><td>Audit Fees</td><td>$12,500,000</td></tr></table>` → `governance.summarize(converter(html))["audit_fees"]` must contain 12_500_000.

### EDGAR-CLI-F08 — `amendments` derives the base form with `rstrip("/A")`

- **Evidence:** `edgar/api.py:2733` `base_form = amd.get("form","").upper().rstrip("/A").rstrip()`. `str.rstrip` strips a character set, so `"DEF 14A/A"` → `"DEF 14"`, `"N-1A/A"` → `"N-1"`; lookups into `primaries` (keyed `"DEF 14A"`) fail.
- **Trigger:** any filer with an amended proxy (`DEF 14A/A`) or other form ending in `A` before `/A`.
- **Impact:** `primary: null` for those chains; agents conclude the amendment has no original.
- **Severity:** Low-Medium. **Confidence:** High.
- **Existing safeguards:** none; no test covers `amendments` (grep over `tests/` finds only the help-text check at `tests/test_cli.py:257`).
- **Smallest correction:** `base_form = form[:-2] if form.endswith("/A") else form`.
- **Proposed regression test:** filings `[{"form":"DEF 14A/A","filingDate":"2026-05-01"},{"form":"DEF 14A","filingDate":"2026-04-01"}]` → chain primary accession equals the `DEF 14A` row.

### EDGAR-CLI-F09 — `quality --as-of` and `delta` restatement probes ignore `as_of`

- **Evidence:** `quality()` accepts `as_of` (`api.py:1953-1954`) and applies it to every fact lookup, but the restatement probe calls `self.audit_trail(identifier, probe)` with no date (`api.py:2064-2066`); `audit_trail` has no `as_of` parameter (`api.py:2663-2664`) and pulls `limit=10000` across all history. Same in `delta` (`api.py:2783-2784`). Comment at `api.py:2062` says "over last 5 years" but no window exists; the flag is named `restatement_count_recent`. `docs/definitions.md:93-94` claims `--as-of` filters every computed metric's underlying facts.
- **Trigger:** `edgar quality NVDA --as-of 2024-01-01`.
- **Impact:** Look-ahead leak: restatements filed after `as_of` flip `flagged: true` in a backtest context; the "recent" label is inaccurate for all-history counts.
- **Severity:** Low-Medium. **Confidence:** High.
- **Existing safeguards:** `period_end`/`as_of` echoed in the envelope; per-filing `filed` dates are visible in `audit_trail` output.
- **Smallest correction:** add `as_of: Optional[str] = None` to `audit_trail` (pass through to `company_concept_alias`), call it with `as_of` from `quality`/`delta`, and either implement the 5-year window or rename the flag/comment.
- **Proposed regression test:** stub `company_concept_alias` capturing kwargs → `quality("X", as_of="2024-01-01")` must call `audit_trail` with `as_of="2024-01-01"`; facts filed after `as_of` must not produce a restated period.

### EDGAR-CLI-F10 — `eps` alias resolves to diluted EPS; income statement emits two identical lines

- **Evidence:** `edgar/api.py:153-154` map both `diluted_eps` and `eps` to `EarningsPerShareDiluted`; `METRIC_BUNDLE_GROUPS["income-statement"]` (`api.py:255-256`) and `statement("income")` lines (`api.py:2188-2191`) include both.
- **Trigger:** `edgar metrics X --bundle income-statement`, `edgar statements X --statement income`.
- **Impact:** Agents reading `eps` as basic EPS get diluted EPS; the two lines carry the same tag/value so the "basic vs diluted" distinction is silently lost. Not a crash, but a labeling correctness issue in a tool that promises defensible numbers.
- **Severity:** Low-Medium. **Confidence:** High.
- **Existing safeguards:** `tag` is returned on every line so a careful reader can see `EarningsPerShareDiluted` twice.
- **Smallest correction:** `"eps": [("us-gaap","EarningsPerShareBasic","USD/shares")]` (and optionally rename to `basic_eps` with `eps` as an explicit alias of basic).
- **Proposed regression test:** `resolve_concept_alias("eps")[1] == "EarningsPerShareBasic"` and `resolve_concept_alias("diluted_eps")[1] == "EarningsPerShareDiluted"`; `statement("income")` lines for `eps` and `diluted_eps` must have different tags.

### Previously reported rough edges confirmed still present in this source (not counted toward the ten)

- **CLI-ROUGH-EDGES F04** (metrics mixes periods): `pick_comparable_facts` still discards unframed facts whenever any framed fact exists (`api.py:3941-3957`), consumed by `_best_metric` (`api.py:3404-3427`); `metrics` still has no period selector (`cli.py:699-707`).
- **CLI-ROUGH-EDGES F05** (item renderer clip): `cli.py:1660` still slices to 2000 chars with an ellipsis keyed on API truncation; `--markdown` has no branch in `item` (`cli.py:1646-1664`) so it renders a Rich panel.
- **CLI-ROUGH-EDGES F06** (FCF caveat): fixed caveat "PP&E only" at `compute.py:424, 430` while the `capex` alias still admits `PaymentsToAcquireProductiveAssets` (`api.py:119-122`); `docs/definitions.md:54-56` repeats the PP&E-only claim.
- **CLI-ROUGH-EDGES F03** (fiscal labels) appears resolved statically: `build_fiscal_grid`/`fiscal_period_from_dates` (`api.py:3610-3686`) with tests at `tests/test_api.py:1629-1671`.

## 5. Documentation drift (documented claim ↔ contrary implementation)

1. `docs/definitions.md:76-80` says TTM has no `FY + YTD − prior YTD` fallback ↔ `api.py:2922-2927` + `_ttm_stub_period` `api.py:2932-3011` implement it and `README.md:179-181` advertises it. (`CLI-ROUGH-EDGES.md:99` already flagged this; still unreconciled.) Also `compute.py:169-170` emits user-facing text "stub-period reconstruction not yet implemented" whenever `_ttm_stub_period` returns `None`.
2. `docs/definitions.md:84-87` lists trend labels `expanding/contracting/stable/inflecting` with a half-series sign check ↔ `compute.py:566-627` adds `accelerating/decelerating/re-decelerating` via change-point detection.
3. `docs/definitions.md:93-94` "`--as-of` filters every computed metric's underlying facts" ↔ `metrics` (`cli.py:699-707`), `brief`, `dashboard` (`cli.py:1539-1547`) have no `--as-of`; `quality`/`delta` restatement probes ignore it (F09).
4. `README.md:169` "14 canonical ratios" ↔ `api.py:3050-3068` computes 17.
5. `README.md:107` `@sic:NNNN` "dynamic from the ticker map" ↔ `api.py:1145-1146` and `companies()` (F03).
6. `README.md:52-60` exit 3 for rate-limited ↔ `api.py:509-513, 551-553` + `cli.py:262` (F05).
7. `README.md:62-72` "Envelope on every JSON response" ↔ `bulk-urls` (`cli.py:2138-2143`), `pending` (`cli.py:2112`), `subscribe list` (`cli.py:2084`), `cache warm` (`cli.py:2239`), `schema` (`cli.py:1203-1210`) emit raw dicts without `schema_version`/`cli_version`/`cache`.
8. `README.md:240` "404s 1h" ↔ `api.py:515-519` also negative-caches 403 (F06); `docs/BACKLOG.md:423` says so, README does not.
9. `pyproject.toml:21` "Operating System :: OS Independent" ↔ `edgar/cache.py:15` `import fcntl` at module import (POSIX-only); `cli.py → api.py → cache.py` import chain makes the CLI unimportable on Windows.
10. `edgar/mirror.py:14-15` "the submissions ETag is sent on subsequent fetches" ↔ `update_filer_etag`/`get_filer_metadata` (`mirror.py:278-296`) are never called anywhere (grep: definitions only); `mirror_filer` relies on `EdgarCache` instead. `ingest_submissions(client=None)` parameter is unused.
11. `api.py:3223` comment "goodwill alias not in COMMON_CONCEPT_CANDIDATES yet" ↔ `api.py:231-237` (F04).
12. `edgar/holders.py:9` "value (in thousands of USD per SEC schema)" ↔ `holders.py:104-115` heuristic (F01); `docs/BACKLOG.md:62-64` describes the heuristic as "per filing" while the in-code comment (`holders.py:83-89`) describes per-fact.
13. `api.py:2062` "Restatement frequency over last 5 years" ↔ no date window in `audit_trail` (F09).
14. `README.md:277-279` says market-data ratios appear under `not_applicable` — correct — but `README.md:176-177` lists the `income-statement` bundle containing `eps` and `diluted_eps` as distinct items ↔ identical tag (F10).

## 6. Hypotheses (need runtime confirmation; not counted as findings)

- **Cross-tag false restatements:** `company_concept_alias(canonical_union=True)` dedupes by `(start, end, accn)` only (`api.py:977-991`), and `audit_trail` groups by `(start, end)` ignoring tag (`api.py:2683-2699`). A filer that moved from `Revenues` to `RevenueFromContractWithCustomerExcludingAssessedTax` (or reports both with different scope) would show "restated" periods that are tag differences. This feeds `quality.restatement_count_recent` and `delta`. Mitigation: group by `(start, end, source_tag)` or report tag-change separately.
- **`_fy_end` non-termination:** `api.py:852-858` decrements `probe` until `date()` succeeds; a malformed `fiscalYearEnd` with day `00` would loop forever. Unlikely from SEC, cheap to guard (`probe <= 0 → return []`).
- **Silent empty holdings:** `_fetch_13f_holdings` returns `[]` on any index/XML failure (`api.py:1706-1727`), so `holdings` exits 0 with `position_count: 0`. Also, `filing_documents` fails the whole index if one row's href is unparseable (`api.py:1376-1378`) — already noted as a follow-up in `CLI-ROUGH-EDGES.md:112-113`.
- **`search --tickers A,B` live path** sends only `cik_list[0]` to EFTS (`cli.py:1511`); the rest are silently dropped.
- **`pending` swallows per-subscription errors** (`cli.py:2101`), reporting `count: 0` for a filer whose fetch failed.
- **`delta` without `--since`/`--use-state`** labels up to 200 recent filings as "new" (`api.py:2775-2779`).
- **`cache.refresh_timestamp`** writes `tmp → rename` without `_file_lock` (`cache.py:216-233`) unlike `set` (`cache.py:159`); a concurrent `set` on the same key could interleave on the shared `.tmp` name.
- **`holders` issuer-name needle** (`api.py:1805-1808`): `"APPLE"` substring also matches unrelated issuers (e.g., names containing "PINEAPPLE"); caveat present, but a word-boundary match would be cheap.

## 7. Suggested tests (NOT RUN — proposals only)

Consolidated from the findings; all can be offline with stubbed `_get`/`session.get`:

1. `test_infotable_value_units_follow_report_date` (F01) — three cases: post-2022 absolute, pre-2023 thousands, missing date → heuristic.
2. `test_since_last_fetch_does_not_advance_past_truncated_window` and `test_since_last_fetch_includes_later_same_day_filing` (F02); `test_mark_seen_requires_or_derives_filed_date` (F02, CLI).
3. `test_expand_group_sic_errors_without_sic_field` (F03).
4. `test_reconstruct_tangible_book_subtracts_goodwill_and_intangibles` (F04).
5. `test_get_exhausted_429_reports_rate_limit_and_exit_3` (F05) — fake session + `CliRunner`.
6. `test_403_is_not_served_from_negative_cache_after_recovery` (F06).
7. `test_governance_audit_fees_survive_table_markup` (F07).
8. `test_amendments_pairs_def14a_amendment_with_primary` (F08).
9. `test_quality_passes_as_of_to_audit_trail` (F09).
10. `test_eps_alias_is_basic_and_distinct_from_diluted` (F10).
11. Documentation guards: a test asserting `len(ratios)` matches the number documented, or drop the number from README.

## 8. Inspected files and workflows

- Read in full: `README.md`, `pyproject.toml`, `.gitignore`, `CLI-ROUGH-EDGES.md`, `docs/definitions.md`, `docs/FINDINGS.md`, `edgar/__init__.py`, `edgar/__main__.py`, `edgar/api.py` (3963 lines), `edgar/cli.py` (3049 lines), `edgar/compute.py`, `edgar/cache.py`, `edgar/state.py`, `edgar/documents.py`, `edgar/mirror.py`, `edgar/items.py`, `edgar/insiders.py`, `edgar/holders.py`, `edgar/governance.py`, `tests/conftest.py`, `tests/test_api.py`, `tests/test_cli.py`, `tests/test_documents.py`, `tests/test_items.py`.
- Read partially (targeted ranges/greps): `docs/BACKLOG.md`.
- Not read (listed in manifest but out of audit value): `Insights/**` artifacts (prior run outputs, large saved filings). They were not needed to support any finding and were skipped to keep the audit to source/tests/docs.
- Workflows traced end-to-end: HTTP get/cache/retry (`_get`), company resolution, submissions + high-water drain, concept/alias resolution and fact enrichment, TTM/ratios/growth/reconstruct, statements/quality/verify, 13F holdings/holders, Form 4 insiders, DEF 14A governance, Item extraction, mirror ingest + FTS search, EFTS search, exhibits download, CLI exit-code mapping and output renderers.

## 9. Exclusions and limitations

- Static audit only; no execution, no network, no tests run, no source modified. Nothing here is reproduced.
- `research_cli_base` internals unavailable; retry counts, timeouts and session header behaviour are assumed.
- SEC behaviours referenced (403 for throttling, 13F unit change for periods ending ≥ 2022-12-31, multiple same-day filings) are domain knowledge, not verified in this pass.
- Three files excluded by the sanitizer were not inspected; `Insights/**` artifacts were deliberately not reviewed.
- `original_dirty: true` — the snapshot includes uncommitted working-tree changes; findings may not correspond 1:1 to commit `e2aa67e`.

---

## Worker report (for `uaf report --assignment evt_4b4c17158b4948bd99cb51e1c5d11baf`)

- **Local result:** Static read-only audit of `edgar-cli` complete; 10 prioritized leads (2 High, 5 Medium, 3 Low-Medium), 14 documentation-drift pairs, 8 hypotheses, 3 prior rough edges confirmed still present, 1 confirmed resolved. Status: **needs_parent_verification** — static audit leads awaiting triage; no tests run; no source changes.
- **Evidence:** file/line citations above, all within `source/**` of snapshot sha256 `923d42f0…8f01`; counter-evidence checked in `tests/**`, `docs/BACKLOG.md`, `docs/FINDINGS.md`, `CLI-ROUGH-EDGES.md`.
- **Commands run:** none (no shell). Tool calls: Read ×~35 (files/ranges), Grep ×8, Glob ×1.
- **Limits:** no execution/network; external dependency not in snapshot; Insights artifacts and 3 sanitizer-excluded files not inspected.
- **Risks:** F01 and F02 are the ones most likely to be producing wrong or silently-missing data for live users today; F06 is a design trade-off the owner may consciously accept.
- **Follow-ups suggested for orchestrator:** triage F01/F02 first; run the proposed offline regression tests; reconcile `docs/definitions.md` TTM/trend/as-of text with code; decide `@sic` support or removal.
- **Memory note (optional):** none of the portable memory records applied (map was empty).

**Coverage statement:** All Python modules, all four test files, README, pyproject, definitions/FINDINGS/rough-edges docs and the relevant BACKLOG sections were read; `Insights/**` artifacts and sanitizer-excluded files were not. Findings are static leads, not verified defects.


---

<!-- UAF-ASTRA-ADDENDUM: 2026-10-23/edgar-cli db31f3b61a377577335d760ced74db6942da440fd5f80a36e867e272714ef6ef -->

# Astra Follow-up Addendum - 2026-10-23

Requested model: gpt-6-astra, effort high; provider-resolved identity unconfirmed.
Advisory static review, not verified defects or approval. No tests were authorized.
Original Fable report SHA-256: `e248e4dc7adb1b293e8919d91129578368368fa80ee771ab09297ece51e44f68`.
Captured source SHA-256: `923d42f0ba24a273c58a7d675ad5954513ea14035b6d1c22bc0f3022e18b8f01`.
Repository-relative citations refer to the captured source, not this findings directory.

## Scope and provenance

Repository: **edgar-cli**. Identities below are copied from `AUDIT-SCOPE.json:2` and `AUDIT-SCOPE.json:592`; they were not independently rehashed.

| Identity | Current | Previous |
|---|---|---|
| HEAD | `e2aa67eb9ba83c8f517b7a52a6e67eb5dacc2c7d` | `e2aa67eb9ba83c8f517b7a52a6e67eb5dacc2c7d` |
| Snapshot SHA-256 | `923d42f0ba24a273c58a7d675ad5954513ea14035b6d1c22bc0f3022e18b8f01` | `923d42f0ba24a273c58a7d675ad5954513ea14035b6d1c22bc0f3022e18b8f01` |

- Current capture: `2026-10-23T16:17:53.823Z`; dirty working tree: **true**.
- Original report SHA-256: `e248e4dc7adb1b293e8919d91129578368368fa80ee771ab09297ece51e44f68`.
- Inspected areas: TTM selection, ratio inputs and alignment, search scoping, filing high-water marks, 13F aggregation, delta/restatement filtering, and relevant documentation and tests as text.
- Citations include the captured `source/` prefix. The manifest describes a non-atomic working-file capture and says old source is unavailable (`AUDIT-SCOPE.json:597`).

**Recommendation:** retain Fable F02 as a priority, but correct its proposed truncation remedy. Next, address the period-selection and search-scoping defects below. These are static findings, not reproduced results.

## New findings

### ASTRA-F01 — An older quarterly window can suppress a newer TTM reconstruction

**Severity:** Medium. **Confidence:** High in the control flow; prevalence unmeasured.

- **Trigger:** The newest quarterly facts contain a gap, an older contiguous four-quarter window exists, and annual/YTD facts could reconstruct a more recent TTM.
- **Evidence:** `source/edgar/compute.py:134` scans progressively older windows until one is contiguous, then returns a value with empty caveats at line 149. `source/edgar/api.py:2922` attempts the annual/YTD reconstruction only when that value is `None`. Consequently, a valid older sum prevents consideration of a newer reconstruction.
- **Impact:** A command requesting trailing twelve months can return a historical twelve-month window despite having sufficient data for a newer one. The table/Markdown renderer omits `period_end` (`source/edgar/cli.py:960`), making the stale window harder to notice.
- **Safeguards/counter-evidence:** JSON retains `period_end` and input provenance. Quarterly continuity is checked; the problem is freshness and fallback selection, not summing across a gap. Existing tests cover a contiguous newest window and a four-row gap, but not an older valid window competing with a newer stub reconstruction (`source/tests/test_api.py:881`, `:1018`, `:1600`).
- **Smallest correction:** Require the quarterly window to end at the latest eligible quarter before treating it as the current TTM. Otherwise attempt the stub path. Alternatively, compare both candidates’ period ends and explicitly label any historical fallback.
- **Proposed regression test — NOT RUN:** Supply Q1 2026 = 150; Q3/Q2/Q1 2025 = 90/80/70; Q4 2024 = 60; and FY2025 = 400. Current selection can return 300 ending September 2025. Assert that `ttm()` instead reconstructs **480**, ending March 2026, using FY2025 + Q1 2026 − Q1 2025.

### ASTRA-F02 — Ratios combine flows from different periods without an alignment warning

**Severity:** Medium. **Confidence:** High for the conditional defect.

- **Trigger:** One flow alias has current facts while another has only older facts of the same period kind—for example, FY2025 revenue and FY2024 net income.
- **Evidence:** `source/edgar/api.py:3024` independently selects each flow’s latest fact. Line 3036 anchors balance-sheet facts primarily to revenue’s end date. Arithmetic then consumes the independently selected flows (`source/edgar/compute.py:253`, `:289`, `:415`). The warning collector checks only instant facts (`source/edgar/api.py:3074`).
- **Impact:** Margins, ROA/ROE, FCF and related ratios can combine different years while reporting one `flow_period_end` and an empty `alignment_warnings` list. This extends beyond the prior report’s discussion of mixed periods in the separate `metrics` command.
- **Safeguards/counter-evidence:** `period_type` and `as_of` are forwarded, and input provenance exposes dates. Those checks do not establish a common reporting interval. The separate statement builder already rejects mismatched period ends (`source/edgar/api.py:2293`), demonstrating that this safeguard exists elsewhere. Arithmetic tests exercise values and missing inputs, not cross-period orchestration (`source/tests/test_api.py:854`, `:873`).
- **Smallest correction:** Validate flow start/end compatibility before computing each affected ratio; suppress incompatible results with a specific mismatch explanation. An alternative is to select a common historical interval and label it explicitly.
- **Proposed regression test — NOT RUN:** Provide FY2025 revenue of 1,000, FY2024 net income of 100, and assets/equity aligned exactly to FY2025. Assert that net margin and ROA/ROE do not silently produce mixed-year values with empty alignment warnings. Include a same-period control.

### ASTRA-F03 — Failed ticker resolution silently removes search restrictions

**Severity:** Medium. **Confidence:** High in the request construction.

- **Trigger:** `search --tickers …` receives an unknown or ambiguous identifier, a resolver error, or a group that successfully expands to no identifiers.
- **Evidence:** `source/edgar/cli.py:1500` builds a CIK list but discards company-resolution errors. An empty list becomes `cik=None` for live search at line 1511; `source/edgar/api.py:2500` then omits the CIK restriction. Mirror searches likewise add a CIK predicate only for a truthy list (`source/edgar/mirror.py:363`, `:406`).
- **Impact:** An explicitly scoped search can search across all filers in the backend’s other limits and appear successful. Mixed valid/invalid selections silently lose unresolved members.
- **Safeguards/counter-evidence:** Explicit group-expansion errors already stop execution (`source/edgar/cli.py:346`); successful empty expansions and company-resolution errors bypass that protection. Returned matches contain company identifiers, but the command does not explain that its requested scope changed. Existing search tests exercise backend behavior, not this CLI resolution path (`source/tests/test_api.py:1747`, `:1781`).
- **Smallest correction:** Distinguish an omitted scope from an explicitly empty or failed scope. Reject failed resolution and empty explicit selections before invoking either backend. If partial results are intentional, require explicit partial-result metadata and never broaden an empty selection.
- **Proposed regression test — NOT RUN:** Stub resolution to return an error, then invoke search with and without `--db`. Assert a nonzero result and no backend search call. Repeat for an empty group and a mixed valid/invalid selection.

## Additions/corrections to Fable

### EDGAR-CLI-F01 — Challenge the claimed effect on weights and concentration

The unit heuristic remains present at `source/edgar/holders.py:109`. However, the claim that within-filer `weight` and `top_concentration` become 1,000× too large is not supported by the current arithmetic.

Every row in a filing receives the same multiplier. Weights divide each scaled value by the scaled total, and concentration divides the scaled top-position total by that same total (`source/edgar/holders.py:123`, `:137`, `:142`). The multiplier cancels.

**Status:** Narrow the impact statement; retain the absolute-dollar defect. Cross-filer dollar totals and rankings can still be distorted when filers receive different multipliers. This does not establish that the original source differed or that F01 as a whole was false.

**Proposed test — NOT RUN:** Scale every position in the existing concentration fixture by 1,000; assert unchanged weights/concentration and a 1,000× dollar total (`source/tests/test_api.py:1435`).

### EDGAR-CLI-F02 — Challenge the “oldest returned filing” remedy; strengthen the warning gap

Moving the mark to the **oldest returned** filing does not safely drain a newest-first truncated batch. If 60 unseen filings exist and only the newest 50 are returned, the oldest returned filing is still newer than the ten omitted filings. Advancing to that date excludes those ten on the next request.

The relevant selection, date exclusion and advancement remain at `source/edgar/api.py:668`, `:676`, `:739`; the state store refuses older or equal dates at `source/edgar/state.py:66`.

The warning safeguard is also narrower than Fable described: the truncation warning requires historical chunk metadata, `not all_history`, and a form/date filter (`source/edgar/api.py:723`, `:732`). Truncation can therefore advance state without even producing that warning.

**Status:** Strengthens the data-loss concern and challenges one proposed repair. Preserve the existing mark on incomplete retrieval; complete pagination or use a cursor representing a fully consumed interval before advancing. Inclusive boundary dates plus accession tracking remain necessary for same-day arrivals. Merely preserving the mark prevents loss but will replay the same newest batch until pagination is addressed.

**Proposed test — NOT RUN:** Drain 60 distinct-date filings with a batch size of 50 and assert eventual delivery of all 60. Add a later-arriving same-day accession. Test truncation with an empty historical-chunk list.

### EDGAR-CLI-F09 — Separate missing `delta --as-of` support from ignored `--since`

In the current source, `delta` has **no `as_of` parameter or CLI option** (`source/edgar/api.py:2764`; `source/edgar/cli.py:2011`). Thus its correction requires adding an interface or correcting the documentation; it cannot simply forward an existing argument.

There is a further lower-bound defect: `since` filters new filings at `source/edgar/api.py:2775`, but all restated periods from unbounded audit trails are appended at line 2783. Restatements entirely before `--since` can therefore recur in later deltas. State-based deltas likewise do not constrain that probe.

**Status:** Refines and strengthens F09. The `quality --as-of` concern remains supported by its unbounded call at `source/edgar/api.py:2066`.

**Correction/test proposal — NOT RUN:** Retain historical facts needed as comparison baselines, but filter emitted changes by the filing date when a changed value appeared. Test that a wholly pre-boundary restatement is excluded while an original before the boundary and changed filing after it remains detectable. Decide separately whether to implement the documented upper bound.

### Fable §6, “`search --tickers A,B` live path” — Stronger static support

This does not require live SEC behavior to establish the lost request scope: the CLI passes only `cik_list[0]` (`source/edgar/cli.py:1511`), and `search_efts` forwards that single value (`source/edgar/api.py:2500`). Both identifiers can resolve successfully and the second still never reaches the request.

**Status:** Upgrade from a runtime-dependent hypothesis to a statically supported request-construction defect; user-visible results remain untested.

The smallest containment is to reject multiple live identifiers explicitly. Full support would require a supported multi-CIK request or merged per-CIK requests with clear limit/deduplication semantics. A proposed CLI test should capture backend arguments for two valid identifiers; **NOT RUN**.

## Documentation deltas

| Documented assertion | Contrary or qualifying current source |
|---|---|
| README says TTM falls back to annual/YTD reconstruction for missing standalone Q4 (`source/README.md:179`). | An older successful quarterly sum blocks that fallback (`source/edgar/api.py:2922`; ASTRA-F01). This adds a behavioral qualification beyond Fable’s existing README/definitions disagreement. |
| Search help promises comma-separated identifiers “to scope the search” (`source/edgar/cli.py:1479`). | Live search retains only the first resolved CIK; failed/empty resolution can remove the restriction altogether (`source/edgar/cli.py:1500`; ASTRA-F03). |
| README lists `delta` among commands accepting `--as-of` (`source/README.md:196`). | Neither the command nor API method accepts it (`source/edgar/cli.py:2011`; `source/edgar/api.py:2764`). |
| Delta help promises filings and restatements “since a date” (`source/edgar/cli.py:2020`). | The date constrains filings only; historical restatement groups are appended without that filter (`source/edgar/api.py:2775`, `:2783`). |

## Coverage and limits

- Read `AUDIT-SCOPE.json` provenance/exclusions and the prior findings, hypotheses and documentation inventory. Inspected targeted slices of `source/edgar/{api,compute,cli,holders,mirror,state}.py`, relevant tests, README, definitions and rough-edge notes.
- Preserved the manifest’s exclusions: private/data-format content, oversized content, non-source formats, private/generated/vendored directories and omitted symlinks. Did not inspect `.uaf`, credentials, provider homes, external repositories or links. Saved `Insights/**` artifacts were not reviewed.
- No old source was available. Matching manifest identities are provenance, not an independent content verification. No claim is made that other Fable findings are fixed or fully revalidated.
- External `research_cli_base` behavior and live SEC data were unavailable. Financial examples above are proposed synthetic cases; actual filer prevalence and runtime outcomes remain unmeasured.
- Triage questions that could change the remedy: Should TTM permit a clearly labeled older window? Should search allow explicit partial scope resolution? Which filing-date boundary defines a restatement event for `delta`?

**No tests ran, no project code executed, and no files changed. No network access, state operations or delegated agents were used. Findings remain advisory pending triage; this addendum is neither verification nor human approval.**
