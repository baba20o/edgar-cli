Wrapping up: I have all the evidence I need from the diff and reads; no further tool calls required. Composing the review now.

# Adversarial Review — Copilot EDGAR repair (working diff vs HEAD 030df3a)

**Result:** NOT approved as-is — one concrete correctness regression (cheap to fix), plus optional follow-ups.

## BLOCKERS: 1

**B1 — Strict title gate makes real Item headings unfindable (regression from HEAD).**
- `edgar/items.py:228` — `if not title_match and (heading_tail or not at_start): continue` discards *any* line-start heading whose caption doesn't prefix-match the catalog title. HEAD only *preferred* title matches; non-matching headings still fell through the fallback predicates (old lines ~163-170 in the diff).
- `edgar/items.py:39` — catalog Item 15 is `"Exhibits, Financial Statement Schedules"`; the current SEC caption is "Exhibit(s) **and** Financial Statement Schedules" (MSFT, AAPL, NVDA all use it). `_title_follows` (`items.py:185-188`) fails → no `(None,"15")` candidate → `extract_section(text,"15")` returns "Item 15 not found" on essentially every modern 10-K, and Item 14 bleeds into the exhibit index (labeled `medium`, so at least honest).
- Same class, not exhaustive: 10-Q Part I Item 1 captioned "Condensed Consolidated Financial Statements (Unaudited)" vs catalog "Financial Statements"; 10-K Item 5 "Market for **the** Registrant's…".
- Fix: reject only when the tail matches a *different* catalog title (true conflict), otherwise keep as a weak `title_matched=False` candidate; also correct the Item 15 caption. `test_conflicting_quarterly_title_is_never_relabelled_mda` still holds under that rule (Part II Item 2's tail matches Part II's own title → conflict for `("I","2")`).
- Evidence is static trace, not execution.

## Optional follow-ups (ordered by severity)

1. **False-`high` risk after removing the gap/"thin" heuristic.** `items.py:214-216` only skips TOC-ish lines ending in `\s\d+`; page ranges ("25-30"), div-based TOCs, or rows without `href="#"` pass. `items.py:244` picks the *first* title-matched candidate, and the next TOC stub is an adjacent title-matched boundary → `high` (`items.py:296`) on a 30-char stub. Bank-style "Cross-Reference Index" tables are realistic triggers. Cheap guard: downgrade to `low` when `end-start` is tiny. Tests only cover `#`-linked TOCs.
2. **`filing_documents` now fails the whole listing on one odd row** (`api.py:1375-1378`). HEAD resolved rows independently. Any non-Archives href (or unlinked document cell with spaces) makes `edgar exhibits` error outright. Prefer per-row `url_error`/skip; keep the hard reject in `_download_documents`.
3. **Parser edge cases** (`_SectionTextParser`): body headings that are themselves `#`-links are dropped as navigation (`items.py:91-94`); `<a href="#"><div>…</div></a>` loses `linked` at the div flush (`items.py:99-101,111-112`) so TOC lines leak outside `<tr>`; only the exact phrase "table of contents" is stripped (`items.py:93,150`) — "Back to Contents" in the same block defeats `at_start` (`items.py:205-207`); `<br>` as a block (`items.py:75`) lets a `<br>`-split cross-reference become a line-start match and win the pick; a body paragraph beginning "Part II, Item 1A…" flips Part context (`items.py:220-221`) and truncates Part I via `next_part` (`items.py:286-290`) — degrades to `medium`, so honest but lossy.
4. **Verification gap:** `tests/test_items.py:188-206` skip because `mission-input/` is not in the repo; every executed fixture is synthetic HTML written alongside the parser. The "actual SEC document" claim rests on the parent's local, non-committed evidence. Commit a trimmed real fixture (TOC + heading region).
5. **CLI text output** (`cli.py:1655-1657`) doesn't show `part` or `confidence_reason`; `items_found_in_document` now prints duplicate codes ("1, 2, 3, 4, 1, 1A, 2…") with no Part labels.
6. **Mirror path is now always `low`** (`api.py:1641-1643`) with no way to force a live fetch when `db_path` is set. Documented, but a UX regression for mirror users.
7. `_download_documents` now hard-errors on docs missing `url` (`cli.py:2876-2878`); HEAD skipped silently. Fine if intentional.
8. `docs/BACKLOG.md:354` still describes the old three-signal heuristic (outside my file scope).

## What holds up
- `edgar/documents.py`: origin/port/userinfo checks, `..` and double-encoded rejection, single-`doc` viewer unwrap, query rejection, https canonicalization — tight; tests assert no request is made before rejection (`test_documents.py:107-115`).
- `_download_documents` pops `downloaded_to` before validation; `requests` exceptions subclass `OSError`, so 404/timeouts are caught by the narrowed clause (from library knowledge, not executed).
- `FilingIndexParser` iXBRL badge stripping; structured-mode inline-reference handling (non-line-start matches skipped) is robust; Part I/II identity resolves via title even with no PART headers.

## Worker report
- **Files changed:** none.
- **Commands run:** `git status`, `git diff HEAD` (items/api/cli/.gitignore), `git log`, `git rev-parse`, `ls`, `sed` (pyproject header), `cat` (conftest), file reads, `rg` searches. No tests, network, installs, agents, UAF or Git mutation.
- **Verifier result:** review report delivered; B1 found.
- **Verifier limits:** static reasoning only; I did not execute any claim. Parent's 176-pass/2-skip run is theirs, not mine.
- **Risks:** B1 regresses `extract_section` for common captions; follow-up 1 can yield false `high`; follow-up 2 can break `edgar exhibits` on unexpected index rows.
- **Orchestrator verification still required:** yes — fix B1, add a regression test for a variant caption (e.g., "Item 15. Exhibits and Financial Statement Schedules"), re-run the suite, then commit.