# Copilot EDGAR E1: review and verification

September 7, 2026. **F01/F02 published to origin/main as `e2aa67e`; local main is synchronized.** F03–F06 and Atlas implementation remain outside this mission.

Copilot through UAF authored the changes. UAF confirmed `gpt-6-astra` at high effort. The runtime ran approximately ten minutes, with nested agents disabled. Codex reviewed the code, ran the independent checks, and integrated the six source/test files while preserving the original files' newline styles and verifying their pre-integration hashes.

## Initial integration results

- Original baseline: **110 tests passed**.
- Worker worktree: **165 tests passed**, including two optional cases against saved original filings.
- Integrated checkout: **163 passed, 2 skipped**. The skipped tests look for the optional `mission-input` directory, which was not copied into the main checkout; both passed in the worktree.
- Saved original MD&A checks: VRT Part I Item 2 now returns **37,121 characters**, MSFT Item 7 returns **51,880**, and NVIDIA Part I Item 2 returns **37,740**. Each begins with the actual management-discussion heading and is bounded by the succeeding section.
- Live VRT 10-K Business extraction: **39,355 characters**, retaining the customer discussion beyond the inline Item 1A reference that previously stopped extraction at 5,739 characters.
- Live Microsoft download: clean `msft-20260630.htm`, **8,585,501 bytes**, SHA-256 matching the previously saved direct original. The earlier command had downloaded a 6,356-byte viewer shell.

The new tests cover structural headings, typography, Part I/II identity, inline references, navigation/TOC layouts, confidence metadata, SEC viewer normalization, ordinary exhibit downloads, and unsuccessful downloads. HTTP fixture tests do not use live endpoints.

## Review assessment

The initial parent review found no code blockers within the E1 scope. The later independent pre-commit review below found and resolved a caption regression. Existing tests passed on the first orchestrator run. The worker improved the underlying section identification and document-link normalization rather than special-casing the observed ticker or filing.

The worker accurately marked verification as blocked: UAF denied both its HTML-inspection shell command and pytest command before execution. It continued through allowed read/edit tools and did not claim passing tests or try to evade the denial. Its [original handoff](WORKER-REPORT.md) preserves that distinction.

Section extraction remains heuristic, and flattened mirror text receives lower confidence because lost structure cannot be reconstructed reliably. Document validation deliberately rejects unsupported viewer/path forms. Successful checks on these filings do not establish universal coverage of all SEC layouts.

## Evidence

- `RUN-SUMMARY.json`: resolved model/effort and task/run identifiers; provider cost was not reported.
- `LIVE-CHECKS.json`, `PRESERVATION-CHECKS.json`: command outcomes and original-filing comparisons.
- `WORKTREE-TESTS.txt`, `INTEGRATED-TESTS.txt`: actual test outputs.
- `UAF-VERIFICATION.json`, `UAF-COMPLETION.json`: verifier-backed closure of the isolated worktree task.
- `INTEGRATION.json`: per-file pre/post hashes and preserved newline styles.

The six integrated files are `edgar/items.py`, `edgar/api.py`, `edgar/cli.py`, new `edgar/documents.py`, and new `tests/test_items.py` / `tests/test_documents.py`. UAF setup files, the worker's large input bundle, and its virtual environment were retained only in the separate worktree.

## Published release

The six-file fix was applied without conflicts on upstream `030df3a`, retaining three newer upstream commits. Claude Fable 5.1 identified B1: unfamiliar legitimate captions could be rejected. The parent repaired that regression, retained old Item 15 caption aliases, and added six regression cases. The [same-session recheck](PRE-COMMIT-CLAUDE-RECHECK.md) reports **BLOCKERS: none**; optional findings remain in [the initial review](PRE-COMMIT-CLAUDE-REVIEW.md) and the rough-edge record.

Both release and synchronized-main suites passed **182 tests, with 2 optional saved-filing skips**. The latter cases passed in the original worker worktree. [Commit e2aa67e](https://github.com/baba20o/edgar-cli/commit/e2aa67eb9ba83c8f517b7a52a6e67eb5dacc2c7d) contains only the six source/test files. No UAF setup, private runtime state, or research bundles were committed. [Release receipt](RELEASE.json) records remote verification and review references.

The UAF repair packet was handed off as `context/COPILOT-IMPLEMENTATION-WORKFLOW-HANDOFF.md` in the separate UAF checkout. That historical external artifact is not included in this repository; UAF source changes were not part of this release.
