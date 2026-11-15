# EDGAR CLI Engineering Handoff: Low-Float Dilution & Catalyst Forensics

**Baseline Audited**: `fa3fcff` (`schema_version: "1.2.0"`, `cli_version: "0.1.0"`, `260 passed, 2 skipped` on Windows Python 3.12)  
**Date**: 2026-11-15  
**Target Consumer**: Autonomous `<2-second` SEC EDGAR Forensics Engine (`Process A`) for US Low-Float Equity Trading & Point-in-Time Historical Simulation

---

## 0. Context & Non-Negotiable Repository Invariants

Live evaluation against six representative micro-cap/low-float issuers—**`SBET`**, **`CODX`**, **`JAGX`**, **`UONE`** (domestic `8-K` / `10-Q` / `S-3` / `424B*` filers) and **`OMH`**, **`HOLO`** (foreign `6-K` / `20-F` / `F-3` / `424B5` filers)—confirmed that `edgar-cli` already provides ~80–85% of the exact forensic primitives needed by an autonomous trading agent.

All bug fixes and new features in this handoff **must** preserve `edgar-cli`'s core engineering contracts:

1. **Zero Hallucination / Verbatim Provenance**: Text extractors (`edgar/dilution.py`, `edgar/sixk.py`, `edgar/filing_text.py`) never guess or silently combine figures across sentences. Every extracted figure carries `{type, value, text}` where `text` is a literal substring of `sentence`, alongside `{accession, form, filed, acceptance_datetime, source_url}`. Every derived calculation (`compute.py` / `forensics.py`) uses the `{value, formula, inputs, caveats, missing_inputs}` envelope so downstream agents can audit every input.
2. **Additive Schema Evolution**: Bump `SCHEMA_VERSION` minor (`1.2.0` -> `1.3.0` for additive fields/commands). Never rename or drop existing snake_case keys or change stable exit codes (`0` ok, `2` no data + `hint`, `3` rate limit, `4` SEC outage, `5` validation error).
3. **100% Offline Test Suite**: `pytest tests/` must make **zero network calls** to `sec.gov`. Trim real filing HTML/JSON into small deterministic fixtures (following `a950eca` and `tests/fixtures/omh/`) covering domestic `8-K`, `424B5`, `S-3`, and `10-Q` cases (`CODX`, `SBET`, `JAGX`).
4. **Preserve Working Tree**: Do not touch or overwrite untracked local files (`CLI-ROUGH-EDGES.md`, `Insights/`, `findings/`).

---

## Part I — Bugs & Correctness Defects (Fix First)

Following `docs/BACKLOG.md` policy (*"misleading-output fixes before convenience features"*), land **`B1`–`B5`** before building Part II capabilities.

---

### B1 (Critical — Misleading Output): `dilution` `latest_shares_outstanding` False-Positives on Derivative Bullets & Drops Pre-Offering Baselines

#### Live Reproduction
```bash
edgar dilution CODX --max-filings 15 --max-supplements 4
edgar dilution SBET --max-filings 15 --max-supplements 4
edgar dilution JAGX --max-filings 15 --max-supplements 4
```

#### Observed Behavior
1. **`CODX`** (`CIK 0001692415`, `424B5` `0001493152-26-034835`, filed `2026-07-27`):
   - `xbrl_shares_outstanding.value` = **`10,512,190`** (`10-Q` `0001493152-26-050963`, as of `2026-11-06`).
   - `latest_shares_outstanding` returns **`81,565` shares** (`basis: "stated"`) parsed from bullet:
     > `"● 81,565 shares of Common Stock issuable upon the settlement of outstanding restricted stock units;"`
   - Also captures `"● 15,500 shares of Common Stock issuable upon the exercise of outstanding warrants..."` as `shares_outstanding`.
2. **`SBET`** (`CIK 0001981535`, `424B5` `0001493152-26-029734`, filed `2026-06-23`):
   - `xbrl_shares_outstanding.value` = **`217,346,200`** (`10-Q` `0001493152-26-050483`, as of `2026-11-03`).
   - `latest_shares_outstanding` returns **`11,762,236` shares** (`basis: "stated"`) parsed from bullet:
     > `"● 11,762,236 shares of Common Stock underlying outstanding warrants (including 5,542,952 pre-funded warrants)."`
   - Meanwhile, the *actual* pre-offering baseline sentence in the same `424B5`:
     > `"Unless otherwise indicated, the number of shares of Common Stock to be outstanding after this offering is based on 202,382,019 shares of Common Stock outstanding as of June 18, 2026, and assumes no exercise of the Warrants..."`
     is labeled `basis: "after_offering"` and skipped by `latest_shares_outstanding`!
3. **`JAGX`** (`CIK 0001585608`, `424B5` `0001193125-26-452741`, filed `2026-11-09`):
   - `latest_shares_outstanding` returns **`142,535` shares** from:
     > `"142,535 shares of common stock with dividend rights (the 'Eligible Warrants') outstanding, at the close of business on October 13, 2026..."`
     skipping the real post-conversion count (`13,345,986 shares of common stock issued and outstanding`) in the very same filing.

#### Impact
An autonomous low-float agent reading `latest_shares_outstanding` mistakes a `10.5M` or `217M`-share company for an `81K` or `142K`-share nano-float squeeze candidate.

#### Root Cause
- **`edgar/dilution.py` (`L114–L121`)**: `_OUTSTANDING_RE` matches `({_COUNT}) {_SHARE_NOUN}[^.]{0,80}?(?:issued and )?outstanding`. In `424B` "The Offering" summary bullets (`"X shares of Common Stock issuable upon exercise of outstanding warrants"`, `"underlying outstanding options"`, `"reserved for issuance"`), `"outstanding"` modifies the *derivative* (`warrants`/`RSUs`/`options`), not `Common Stock`. `_OUTSTANDING_EXCLUDE_RE` lacks exclusions for underlying/issuable derivative clauses.
- **`edgar/dilution.py` (`L220–L235`)**: `basis = "after_offering"` is tested against the entire `sentence.lower()` (`r"outstanding (?:immediately )?after|after giving effect|assuming"`). Standard prospectus boilerplate (`"The number of shares to be outstanding after this offering is based on 202,382,019 shares of Common Stock outstanding as of [Date]"`) contains `"outstanding after"` in the preamble even though the numeric figure in the `"based on ... outstanding as of [Date]"` clause is the **`before_offering` / `stated`** count!
- **`edgar/api.py` (`L2068–L2071`)**: `shares` sorts by `(s.get("as_of") or s["filed"], s["acceptance_datetime"])` and picks the first non-`after_offering` entry in document order without preferring `"issued and outstanding"` / `as_of`-dated baselines or flagging extreme (`>5x`) divergence against `xbrl_shares_outstanding`.

#### Required Fix & Acceptance Criteria
1. Expand `_OUTSTANDING_EXCLUDE_RE` (or inspect the text between `{_SHARE_NOUN}` and `outstanding` plus trailing noun) to reject counts describing shares `issuable upon`, `underlying`, `reserved for`, `subject to`, `upon (?:the )?(?:exercise|settlement|conversion|vesting) of`, or parenthetically defined as warrants/options/RSUs/preferreds (`\(the "[^"]*Warrants"`).
2. In `_shares_outstanding(sentence)`, recognize `"based on <N> <share_noun> (?:issued and )?outstanding as of <Date>"` specifically as `basis = "before_offering"` (extracting ONLY the `"based on"` figure and its `as_of` date, not hypothetical post-offering figures earlier in the sentence).
3. When sorting candidate `shares_outstanding` rows from the same filing, rank explicit `as_of`-dated and `"issued and outstanding"` / `before_offering` cover statements ahead of undated incidental mentions.
4. Add a `warnings` entry on the `dilution` envelope if `latest_shares_outstanding` and `xbrl_shares_outstanding` (when no effective reverse split occurred between their dates) diverge by `> 3.0x` or `< 0.33x`.
5. **Acceptance**: Saved fixtures from `CODX` (`0001493152-26-034835`), `SBET` (`0001493152-26-029734`), and `JAGX` (`0001193125-26-452741`) must reject the `81,565`, `11,762,236`, and `142,535` derivative bullets, extract `SBET`'s `202,382,019` (`as_of: "2026-06-18"`, `basis: "before_offering"`) and `JAGX`'s `13,345,986`, and keep all existing `OMH` dual-class (`Class A` + `Class B`) tests green.

---

### B2 (High — Domestic Recall + Boilerplate Noise): Form `S-3` Baby-Shelf (`General Instruction I.B.6`) Blind Spot

#### Live Reproduction
```bash
edgar dilution CODX --max-filings 15 --max-supplements 4
edgar text CODX --form 424B5 --grep "I\.B\.6"
```

#### Observed Behavior
In `CODX` `424B5` (`0001493152-26-034835`, filed under `S-3` `333-295803`), the cover page explicitly states all four Baby-Shelf numbers:
1. `"We are subject to General Instruction I.B.6 of Form S-3, which limits the amounts that we may sell..."`
2. `"The aggregate market value of our common stock held by non-affiliates pursuant to General Instruction I.B.6 of Form S-3 is approximately $49,425,570, which was calculated based on approximately 5,153,941 shares of our outstanding common stock held by non-affiliates on July 27, 2026 at a price of $11.19 per share..."`
3. `"During the 12 calendar months prior to, and including, the date of this prospectus supplement, we have sold approximately $11,322,816 of securities pursuant to General Instruction I.B.6 of Form S-3."`
4. `"As a result, we are currently eligible to offer and sell up to an aggregate of approximately $7,901,383 of our securities pursuant to General Instruction I.B.6 of Form S-3."`

`edgar dilution CODX` **missed sentences (1), (3), and (4) completely** (losing both the `$11.32M` 12-month prior sales and the `$7.90M` remaining ATM capacity!), while **falsely capturing** `"smaller reporting company"` definition boilerplate (`$250.0 million`, `$100.0 million`, `$700.0 million`) as `shelf_capacity`!

#### Root Cause
- **`edgar/dilution.py` (`L148–L150` & `L309–L315`)**:
  - Foreign Form `F-3` uses `General Instruction I.B.5`, whereas US domestic Form `S-3` (`17 CFR § 239.13`) uses **`General Instruction I.B.6`**.
  - `_SHELF_CAPACITY_RE` only matches `general instruction i\.b\.5`, and `L313` checks `"i.b.5" in sentence.lower()`.
  - Meanwhile, `public float|held by non-affiliates` matches generic SEC filer-category definitions (`"smaller reporting company"`, `"non-accelerated filer"`, `"accelerated filer"`).

#### Required Fix & Acceptance Criteria
1. Update `_SHELF_CAPACITY_RE` and `L313` to match both **`General Instruction I.B.5`** (Form `F-3`) and **`General Instruction I.B.6`** (Form `S-3`).
2. Add `_SHELF_CAPACITY_EXCLUDE_RE` to reject filer-status boilerplate (`smaller reporting company|emerging growth company|accelerated filer|large accelerated filer|minimum listing maintenance`).
3. Tag each `shelf_capacity` statement with an optional `subtopic` (`"baby_shelf_restriction"`, `"mvphs_float"`, `"prior_12m_sales"`, `"remaining_eligible_capacity"`, `"base_shelf_total"`) when matched by explicit phrases (`sold .* during the 12 calendar months`, `eligible to offer and sell`, `aggregate market value .* held by non-affiliates`), without inferring unstated math.
4. **Acceptance**: On the `CODX` `424B5` fixture, `shelf_registrations` for `333-295803` captures all four `I.B.6` sentences (including `5,153,941` shares, `$49,425,570`, `$11,322,816` sold, and `$7,901,383` eligible) and zero `"smaller reporting company"` `$250M/$700M` false positives.

---

### B3 (High — Latency & Telemetry): `_get_text` and `_get_bytes` Bypass `EdgarCache` (`3.10s` Warm vs. `<150ms`)

#### Live Reproduction
```bash
# Run twice back-to-back:
edgar dilution SBET --max-filings 15 --max-supplements 4
edgar dilution SBET --max-filings 15 --max-supplements 4
```

#### Observed Behavior
- Cold run: `5.36s`.
- Immediate warm re-run: **`3.10s`**, while response envelope claims:
  `"cache": {"calls": 4, "hits": 4, "misses": 0, "last_hit": true}` despite `filings_read` listing **14 HTML filings**.

#### Root Cause
- **`edgar/api.py` (`L596–L610`)**: `_get_text(url)` and `_get_bytes(url)` call `self.rate_limiter.acquire()` (`0.20s` floor per file -> `14 * 0.20s = 2.80s` minimum) and `self.session.get(url)` directly without reading or populating `self.edgar_cache`, and without calling `self._record_cache(...)`.
- Every filing document under `https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}` is **immutable forever once accepted by EDGAR**.

#### Required Fix & Acceptance Criteria
1. Route `_get_text` (and XML/text exhibit fetches in `events`, `dilution`, `text`, `item`, `insiders`, `holdings`) through `self.edgar_cache` when `self.use_cache` is true (respecting `--no-cache` and `--cache-max-mb` LRU eviction).
2. Add an `ENDPOINT_TTLS` entry in `edgar/cache.py` (`L39–L46`) for `/Archives/edgar/data/` immutable filing artifacts with a `30 * 86400` (`30d`) TTL.
3. Optional fast-path: cache or memoize cleaned `document_to_text` output keyed by `(url, parser_version)` (or store raw text for documents `<= 4 MB` so `--cache-max-mb` stays bounded and giant `50 MB` exhibits don't thrash disk).
4. Record every `_get_text` / `_get_bytes` cache hit/miss in `self._record_cache(...)` so the envelope's `cache.calls / hits / misses` accurately reflects all SEC requests made by the command.
5. **Acceptance**: Second consecutive `edgar dilution SBET` call with warm cache makes `0` network requests, reports `hits == calls` across all `4 + 14 = 18` lookups, and finishes in **`< 250ms`**.

---

### B4 (High — Domestic Catalyst Accuracy): `events` on `8-K`s Lacks `Item 1.01/3.02` Financing Mapping, Ignores `EX-99.1`, and False-Matches Boilerplate

#### Live Reproduction
```bash
edgar events CODX --limit 10
edgar filings SBET --start-date 2025-05-20 --end-date 2025-06-05 --compact
```

#### Observed Behavior
1. **`CODX` `8-K` `0001493152-26-047120`** (filed `2026-10-13`, `items="1.01,2.03,3.02,9.01"`): `Item 3.02` is *Unregistered Sales of Equity Securities*, yet `events` classified it as `["debt", "guidance"]` because `EVENT_KEYWORDS` (`edgar/api.py` `L344–L345`) matched generic words `"notes"` and `"outlook"`/`"forecast"` inside forward-looking-statement boilerplate.
2. **`SBET` `8-K` `0001641172-25-013081`** (filed `2025-05-30`, `items="1.01,3.02,5.02,8.01,9.01"` — the transformational `$425,000,000` Ethereum Treasury PIPE): `event_types_from_items` (`edgar/api.py` `L4605–L4620`) only maps `2.02`, `2.01/5.01`, `2.03`, `3.01`, `5.02`, and `5.03/3.03`—completely ignoring **`1.01`**, **`3.02`**, **`1.02`**, **`1.03`**, **`4.01`**, **`4.02`**, **`5.07`**, and **`8.01`**, and `events` never reads `EX-99.1` on `8-K`s (`L1524–L1545`) even when `Item 7.01`/`8.01` puts the entire catalyst headline inside `EX-99.1`.

#### Required Fix & Acceptance Criteria
1. Complete `event_types_from_items` (`edgar/api.py` `L4605`):
   - `1.01` -> inspect text via `sixk.py` financing/agreement rules to distinguish `"financing"` (SPA, ATM, underwriting, equity line, warrant inducement) vs. `"merger"` vs. `"material_agreement"`
   - `1.02` -> `"agreement_termination"`
   - `1.03` -> `"bankruptcy"`
   - `3.01` -> `"delisting"` (and sub-classify `"delisting_determination"` / `"hearing_panel_appeal"` vs. `"minimum_bid_deficiency"` vs. `"listing_compliance"` from sentence text—critical for OTC suspension risk!)
   - `3.02` -> `"financing"` (unregistered equity sale / PIPE / warrant exchange)
   - `4.01` -> `"auditor_change"`
   - `4.02` -> `"non_reliance_restatement"`
   - `5.07` -> `"shareholder_vote"`
2. Replace `8-K` naive substring `EVENT_KEYWORDS` (`"notes"`, `"guidance"`, `"outlook"`) with sentence-level rules (reusing `sixk.py` + `filing_text.py` with forward-looking disclaimer exclusions so PSLRA safe-harbor paragraphs never trigger `"guidance"`).
3. Enable `EX-99.1` lead-text fallback (`read_exhibits=True`, first `6,000` chars) for `8-K`s whose body merely incorporates Exhibit `99.1` by reference (especially `Item 7.01` / `8.01` press-release `8-K`s).

---

### B5 (Medium — Foreign Filer XBRL Coverage): `COMMON_CONCEPT_CANDIDATES` Has Zero `ifrs-full` Tags

#### Root Cause & Fix
- **`edgar/api.py` (`L127–L261`)**: Every alias in `COMMON_CONCEPT_CANDIDATES` only searches `"us-gaap"` and `"dei"`. Foreign private issuers (`20-F` / `6-K` filers reporting under IFRS rather than US-GAAP) tag facts under the `"ifrs-full"` taxonomy (`Revenue`, `CashAndCashEquivalents`, `Assets`, `CurrentAssets`, `Liabilities`, `CurrentLiabilities`, `Equity`, `ProfitLoss`, `CashFlowsFromUsedInOperatingActivities`, `NoncurrentLiabilities` / `Borrowings`, `IssuedCapital` / `NumberOfSharesOutstanding`).
- **Acceptance**: Append `("ifrs-full", <Tag>, <Unit>)` candidates to the core income-statement, balance-sheet, cash-flow, liquidity, and share-count aliases so `edgar metrics` and `edgar concept --canonical` work out-of-the-box on IFRS `20-F`/`6-K` micro-caps without dropping `us-gaap` precedence.

---

## Part II — New Features Requested for the Low-Float Forensics Engine

Once `B1`–`B5` are green, implement **`F1`–`F5`** to turn `edgar-cli` into a complete, look-ahead-free `<2s` forensics backend.

---

### F1: Intraday Point-in-Time Isolation (`--as-of YYYY-MM-DD[THH:MM:SSZ]`) Everywhere

#### Why Needed
In low-float day trading, a company frequently gaps `+150%` at `07:30 ET` (`11:30:00Z`) and files a `424B5` ATM or registered-direct offering at `16:05 ET` (`20:05:00Z`) on the **same calendar date**.
1. Currently, `edgar dilution`, `edgar events`, `edgar metrics`, and `edgar insiders` **reject `--as-of` (`rc=5`)**.
2. Even on commands that accept `--as-of YYYY-MM-DD`, filtering by `filingDate <= YYYY-MM-DD` leaks a `16:05 ET` after-hours filing into a `07:30 ET` pre-market backtest on the same day.

#### Specification
- Accept `--as-of` in both date format (`YYYY-MM-DD`, inclusive through `23:59:59.999Z`) and ISO-8601 UTC/offset timestamp format (`2025-05-27T11:30:00Z` or `2025-05-27T07:30:00-04:00`) across:
  - `dilution`, `events`, `metrics`, `brief`, `dashboard`, `insiders`, `filings`, `company`, `text`, and the new `float` / `forensics` commands.
- When a timestamp with time-of-day is supplied, filter SEC submission rows using `acceptanceDateTime` (normalized to UTC) `<= cutoff_utc` (falling back to `filingDate < cutoff_date` only on legacy pre-EDGAR rows lacking `acceptanceDateTime`).
- Echo `"as_of": <normalized_cutoff>` on the response envelope whenever `--as-of` is passed so backtest harnesses can verify look-ahead isolation.

---

### F2: `dilution` Forensics Upgrades — Placement Agents, Unclosed PIPE Status, Shelf Withdrawals (`RW`), & `--include-periodic` / `--include-shelf`

#### Why Needed
Our practitioner research across 102 low-float transcripts identified four recurring SEC filing traps/edges that decide whether a micro-cap squeezes `+500%` or rugs `-60%`:
1. **Named Serial-Dilution Underwriters / Sales Agents**: Knowing *who* is running the ATM/offering (`Maxim Group LLC` on `CODX` `424B5` `0001493152-26-034835`; `H.C. Wainwright`; `Univest Securities`; `Dawson James`; `Boustead Securities`; `EF Hutton`; `Spartan Capital`; `Aegis Capital`; `Joseph Gunnar`; `Roth`; `Ladenburg`; `Cantor`; `Jefferies`; `B. Riley`).
2. **Unclosed Private Placements (`$SBET` Booster)**: On May 27, 2025, `$SBET` announced `69M` private-placement shares *"expected to close on or about May 29, 2025"*. Short sellers shorted assuming `69M` dilution, not realizing unclosed/unregistered PIPE shares **cannot legally hit the tape today**—squeezing `$SBET` from `$3` to `$100+`.
3. **Withdrawn Registrations (`Form RW` / `POS AM`)**: Filers sometimes withdraw an `S-1`/`S-3` via `Form RW` just before a catalyst run; if `_shelf_registrations` ignores `RW`, an agent falsely thinks an offering is pending.
4. **`10-Q`/`10-K` Footnote Warrant Resets & Zero-Supplement Shelves**: Warrant strike reductions (`$SONN` slashing `3.3M` warrants from `$5.40` to `$3.19` inside a `10-Q` Subsequent Events footnote) and newly effective `S-1`/`S-3` registrations with `0` `424B` supplements yet are invisible when `dilution` only reads `8-K`/`6-K`/`424B*`.

#### Specification
1. **Placement Agents / Underwriters (`agents` section in `dilution`)**:
   - Extract verbatim agent/underwriter names, roles (`"sales_agent"`, `"placement_agent"`, `"underwriter"`, `"equity_line_investor"`), and stated commission `%` from sentences matching `Equity Distribution Agreement with <Entity>`, `At-the-Market Offering Agreement with <Entity>`, `acting as (?:sole |lead |co-)?(?:placement agent|book-running manager|sales agent|underwriter)`, etc. Keep output factual (no hardcoded "toxic" opinion in `edgar-cli`; downstream strategy rules score the names).
2. **Offering Closing Status (`closing_status` & `expected_closing_date` on `offering` rows)**:
   - Distinguish `"closed"` (`has closed`, `consummated`, `completed the offering`) vs. `"announced_pending"` (`entered into`, `expects the offering to close on or about <Date>`, `subject to customary closing conditions`) and extract any stated closing date.
   - Tag `"registration_status"` when stated (`"unregistered_pipe"`, `"registered_direct"`, `"public_shelf_takedown"`).
3. **`Form RW` & `POS AM` in `_shelf_registrations`**:
   - Include `POS AM` in `REGISTRATION_FORMS` amendments, and match `RW` (Registration Withdrawal) by `fileNumber` to populate `"withdrawn": {"date": ..., "accession": ...}` and `"status": "withdrawn" | "effective" | "pending_effectiveness"`.
4. **Opt-In `--include-periodic` and `--include-shelf` Flags on `dilution`**:
   - `--include-periodic`: Also scans the latest periodic report (`10-Q`, `10-K`, `20-F`) specifically targeting its *Subsequent Events*, *Stockholders' Equity / Warrants*, and *Liquidity / Going Concern* sections for warrant repricings, ATM sales-to-date, and post-period share issuances.
   - `--include-shelf`: When a registration in `shelf_registrations` has `supplements == []` and `status != "withdrawn"`, fetches the cover page (`first 60 sentences`) of the latest `S-3`/`F-3`/`S-1`/`F-1` (or `/A`) to extract headline registered dollar/share capacity and resale vs. primary offering type.

---

### F3: Deterministic Baby-Shelf (`I.B.6` / `I.B.5`) & Cash-Runway Provenance Calculators

#### Specification (`edgar/compute.py` or `edgar/forensics.py`)
Build two pure, provenance-backed calculators using `compute.py`'s `{value, formula, inputs, caveats, missing_inputs}` contract:

1. **`compute_baby_shelf(dilution_data, public_float_shares=None, price=None, high_60d_close=None)`**:
   - Implements SEC General Instruction `I.B.6` (`S-3`) / `I.B.5` (`F-3`) (`17 CFR § 239.13`):
     - `MVPHS = NonAffiliateFloatShares * ReferencePrice` (using filing-stated `MVPHS` / `non_affiliate_shares` when present, or caller-supplied `--price` / `--high-60d-close` with explicit input provenance).
     - If `MVPHS < $75,000,000`: `gross_12m_cap = MVPHS / 3`, `usable_capacity_usd = max(0, gross_12m_cap - prior_12m_ib6_sales_usd)`.
     - Computes `escape_price_75m = 75_000_000 / NonAffiliateFloatShares` (the exact closing price needed to permanently unlock the full shelf above `$75M` MVPHS!).
2. **`compute_cash_runway(metrics_data)`**:
   - Uses latest non-stale `cash`, `operating_cash_flow` (normalized by `period_length_days / 30.4375` to `monthly_operating_burn_usd` when `OCF < 0`), `assets_current - liabilities_current` (`net_working_capital_usd`), and non-stale `debt + short_term_debt` (`total_debt_usd`).
   - Example verified on live `CODX` Q3 2026 `10-Q`: `cash = $714,803`, `OCF = -$20,683,301` over `272d` (`$2,313,568/mo` burn) -> `runway_months = 0.31` (`9.4 days`), `net_working_capital_usd = -$573,722`, `total_debt_usd = 0` (since 2018 debt is flagged `stale: true`).

---

### F4: Point-in-Time Share Supply & Non-Affiliate Float Command (`edgar float <TICKER>`)

#### Why Needed
Commercial portals (`Yahoo`, `Finviz`) lag real micro-cap floats by weeks to months (`$UONE` showed `1.5M` for 2 months after ATM sales expanded it to `4M+`; `$WNW` expanded `2M -> 9M` overnight).

#### Specification
Add `edgar float IDENTIFIER [--as-of ...] [--price FLOAT]`:
1. **Reconciled `shares_outstanding`**:
   - Presents both `xbrl_cover_shares` (`dei:EntityCommonStockSharesOutstanding` with `as_of` and post-split adjustment warning) and `latest_filing_stated_shares` (from `dilution` after `B1` fix, per share class).
   - Lists dated post-`as_of` share issuances (`closed` offerings/conversions) separately from `pending_unclosed_shares` (`announced_pending` PIPEs like `$SBET`) so nothing is double-counted or silently added without provenance.
2. **Affiliate / Insider / `10%` Holder Deductions**:
   - Surfaces `stated_non_affiliate_shares` when disclosed in `I.B.6`/`I.B.5` text (e.g., `CODX`'s exact `5,153,941` shares vs. `10,512,190` OS!).
   - Surfaces `dei:EntityPublicFloat` (`USD` value + `end` date from `10-K`/`20-F` cover page).
   - Parses recent `SC 13D` / `SC 13D/A` / `SC 13G` / `SC 13G/A` cover tables (`Aggregate Amount Beneficially Owned` + `Percent of Class` per reporting person, deduped by reporting-person CIK/name) alongside `Form 3/4` latest `shares_after` holdings per officer/director.

---

### F5: One-Call `<2s` `edgar forensics` Command + Global Real-Time Filing Poller (`edgar feed`)

#### 1. `edgar forensics IDENTIFIER [--price FLOAT] [--high-60d FLOAT] [--as-of TS]`
- Single composite CLI command and `EdgarClient.forensics(...)` Python API returning one unified JSON envelope in **`< 200ms` warm / `< 1.8s` cold** (using `concurrent.futures.ThreadPoolExecutor(max_workers=4)` gated by the existing thread-safe `SharedRateLimiter` at `5 req/s`):
  - `entity`: CIK, ticker, exchanges (`Nasdaq` vs. `NYSE American`), incorporation jurisdiction (`DE`, `NV`, `Cayman Islands`, `BVI`, `Singapore`), SIC, filer category.
  - `share_supply`: output of `F4` (`shares_outstanding`, `stated_non_affiliate_shares`, `pending_unclosed_shares`, `reverse_splits`).
  - `shelves_and_capacity`: output of `B2` + `F2` + `F3` (`shelf_registrations` with `EFFECT` & `RW` status, `baby_shelf` `MVPHS / 3` math & `escape_price_75m`, `equity_lines_and_atm`, `agents`).
  - `warrant_overhang`: structured active warrant blocks (`count`, `exercise_price`, `floor`, `exchange_multiple`, `cashless`, `repricing_events`).
  - `financial_distress`: output of `F3` (`cash_usd`, `monthly_operating_burn_usd`, `runway_months`, `net_working_capital_usd`, `total_debt_usd`, `going_concern` quotes).
  - `recent_catalysts_and_risks`: top `events` (`financing`, `merger`, `listing_deficiency` / `delisting_determination` / `hearing_panel_appeal`, `reverse_split`, `non_reliance_restatement`).

#### 2. `edgar feed [--forms 8-K,6-K,424B5,S-3,F-3,S-1,F-1,EFFECT,RW] [--since-minutes 15] [--use-state] [--ndjson]`
- **Why Needed**: `edgar subscribe` + `edgar pending` works per pre-subscribed `(cik, form)`, which is great for a static watchlist, **but low-float momentum gappers emerge out of the entire ~8,000-ticker universe unannounced at `04:00–09:30 ET` and `16:00–20:00 ET`**.
- **Specification**: Poll SEC's public EDGAR EFTS recent-filings query (`https://efts.sec.gov/LATEST/search-index` with date/form filter, sorted newest-first) or SEC's current-events Atom feed (`cgi-bin/browse-edgar?action=getcurrent&output=atom`), join each hit against the cached `company_tickers_exchange.json` map (`cik -> ticker, exchange`), advance a global feed high-water mark in `~/.edgar/state.json` when `--use-state` is passed, and emit one self-describing NDJSON row per new filing (`{accession, cik, ticker, exchange, form, items, acceptance_datetime, filing_url}`).

---

## Suggested Implementation Order (4 Clean PR/Commit Slices)

| Slice | Scope | Primary Files Touched | Key Verification Gate |
| :--- | :--- | :--- | :--- |
| **Slice 1: Dilution & Cache Bugfixes (`B1`, `B2`, `B3`)** | Fix `shares_outstanding` derivative false positives + `"based on"` baseline; add `I.B.6` + SRC exclusion; wire `_get_text`/`_get_bytes` into `EdgarCache` + `cache` telemetry. | `edgar/dilution.py`, `edgar/api.py`, `edgar/cache.py`, `tests/test_dilution.py`, `tests/test_cache.py` | Offline fixtures for `CODX`, `SBET`, `JAGX` + `OMH` green; warm `edgar dilution SBET` `< 250ms` with `0` network calls. |
| **Slice 2: Domestic `8-K` Classifier, IFRS Aliases & Intraday `--as-of` (`B4`, `B5`, `F1`)** | Upgrade `8-K` item + sentence + `EX-99.1` classification; add `ifrs-full` concept fallbacks; thread `--as-of YYYY-MM-DD[THH:MM:SSZ]` (`acceptanceDateTime`) across `dilution`, `events`, `metrics`, `insiders`, `filings`. | `edgar/api.py`, `edgar/sixk.py` (or shared `edgar/events_classifier.py`), `edgar/cli.py`, `tests/test_events.py`, `tests/test_freshness.py` | Replay `SBET` `--as-of 2025-05-27T13:00:00Z` vs. `2025-05-30T21:00:00Z` with zero look-ahead leakage; `CODX` `Item 3.02` classifies as `financing`. |
| **Slice 3: Agents, Unclosed PIPEs, `RW` Withdrawals, `--include-periodic/shelf` & `float` (`F2`, `F3`, `F4`)** | Add underwriter/agent extraction, `closed` vs. `announced_pending` offering status, `RW`/`POS AM` tracking, periodic footnote scan, `compute_baby_shelf` + `compute_cash_runway`, and `edgar float`. | `edgar/dilution.py`, `edgar/compute.py`, `edgar/api.py`, `edgar/cli.py`, `tests/test_dilution.py`, `tests/test_float.py` | `CODX` extracts `Maxim Group LLC` (`3%`), `5,153,941` non-affiliate float, `$7.90M` `I.B.6` capacity, and `0.31` months cash runway; `SBET` flags May 2025 PIPE `announced_pending`. |
| **Slice 4: Unified `<2s` `edgar forensics` & Universe `edgar feed` (`F5`)** | Parallelized `EdgarClient.forensics` + `edgar forensics` CLI + JSON schema registration in `edgar schema`; global `edgar feed --ndjson` poller. | `edgar/api.py`, `edgar/cli.py`, `edgar/state.py`, `tests/test_forensics.py`, `tests/test_feed.py`, `README.md` | `edgar schema forensics` & `edgar schema feed` valid; cold `edgar forensics CODX --price 2.10` `< 2.0s`, warm `< 200ms`; full `pytest` suite `100%` offline & green. |
