No further reads needed — the diff regions and new tests are sufficient for a final verdict.

# Final verdict on B1 repair

**BLOCKERS: none.** The scoped repair addresses B1.

**Static trace of `edgar/items.py` (new gate, `find_items`):**
- Catalog Item 15 is now "Exhibits and Financial Statement Schedules"; the old comma caption is retained via `_TITLE_ALIASES`, consulted by both `_title_follows` (heading recognition) and `_resolve_item_key` (request alias). Both captions → `title_match=True` → strong candidate.
- Unknown line-start captions ("Market for **the** Registrant's…", "Condensed Consolidated Financial Statements (Unaudited)") now pass as weak candidates (`title_matched=False`) because `conflicting_title` is false and `at_start` holds; they yield `low` confidence when targeted and still serve as boundaries for the preceding Item (14 → 15 test).
- The Part I/II conflict property is preserved: for bare "ITEM 2. UNREGISTERED SALES…" with no PART header, `("I","2")` sees `("II","2")`'s title match → conflict → rejected; `("II","2")` accepted; request "2" → error, as tested. The subsequent `identity[0] not in (None, part or "I")` filter still keeps bare weak candidates out of Part II when Part is unknown.
- No within-catalog title is a prefix of another (10-K and 10-Q catalogs are never mixed), so the broadened conflict scan cannot self-reject a correct heading.

**Tests (`tests/test_items.py:101-130`):** the six new cases assert exactly the intended semantics — two Item 15 captions → `high`, two variants → `low` but extractable with a correct end boundary, Item 14 bounded by the variant Item 15, and the old-title request alias. Expected confidences agree with my trace.

**Residual (unchanged from first report, non-blocking):** weak line-start cross-references (e.g., "Item 1A of our Form 10-K…") are now candidates, but the pick prefers any later title-matched heading and otherwise labels `low`, so this is honest rather than false-rigor. Optional follow-ups 1–8 stand.

**Worker report:** files changed — none; commands — `git diff --stat`, `sed`/`grep` reads of the two files only; no tests, network, agents, UAF or Git mutation. Verifier limit: static reasoning; the 182-pass/2-skip run is the parent's evidence, not mine. Orchestrator verification still required: record the run, then commit.