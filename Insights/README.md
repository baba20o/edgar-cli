# Historical research and review archive

These records describe their dated source revisions and working copies. They
are retained for provenance and future investigation, not as verification of
the current implementation. Current handoff behavior and known limits are
documented in [the implementation report](../docs/handoff-implementation.md).

| Record | Scope | Supporting material |
|---|---|---|
| [September 6 exploration](ai-infrastructure-2026-09-06/FINDINGS.md) | Microsoft, NVIDIA and Vertiv CLI exploration | [Command manifest](ai-infrastructure-2026-09-06/MANIFEST.json), [baseline](ai-infrastructure-2026-09-06/BASELINE.json), [recorded validation](ai-infrastructure-2026-09-06/VALIDATION.json) |
| [CLI rough edges](../CLI-ROUGH-EDGES.md) | September 6 observations and September 7 follow-ups | [Captured CLI responses](ai-infrastructure-2026-09-06/) |
| [September 7 implementation review](copilot-e1-2026-09-07/REVIEW-AND-VALIDATION.md) | Historical F01/F02 repair and release | [Independent review](copilot-e1-2026-09-07/PRE-COMMIT-CLAUDE-REVIEW.md), [recheck](copilot-e1-2026-09-07/PRE-COMMIT-CLAUDE-RECHECK.md), [release receipt](copilot-e1-2026-09-07/RELEASE.json) |
| [October advisory audits](../findings/README.md) | Static findings requiring current reproduction | Original Fable report and Astra addendum |

## What is distributed

The compact CLI responses, reports, capture scripts, manifests, empty stderr
files, and review/test receipts are tracked. Empty stderr files are intentional:
the command manifest and hash inventory reference them.

The eight files under `ai-infrastructure-2026-09-06/documents/` are local-only
downloads and extracted text (13,012,960 bytes). Git ignores that exact directory;
the files are preserved locally but are absent from a fresh clone. Source URLs,
capture timestamps and hashes for the direct filings remain in
[DOCUMENTS.json](ai-infrastructure-2026-09-06/DOCUMENTS.json); the complete
historical file inventory is in
[ARTIFACT-HASHES.json](ai-infrastructure-2026-09-06/ARTIFACT-HASHES.json).
The viewer shell and exhibit download are recorded separately in the
[Microsoft download response](ai-infrastructure-2026-09-06/15-msft-original.json)
and [Vertiv download response](ai-infrastructure-2026-09-06/16-vrt-original-release.json).
The existing optional original-filing tests use a separate `mission-input/`
directory, which is also not supplied by this archive.

## Reading and reusing the records

The September 6 archive is byte-preserved, including its original hash manifest.
All 55 file entries matched their local bytes when archived on November 15,
2026; eight of those entries describe the omitted downloads. The separate
`rough_edges_sha256` in `ARTIFACT-HASHES.json` does **not** match the current
`CLI-ROUGH-EDGES.md`, which already contained September 7 updates before this
archiving pass. That historical checksum was not rewritten.

Some original Markdown links and recorded command paths begin with
`/mnt/c/projects/edgar-cli/`. Interpret that prefix as this checkout's root;
use the relative links above to navigate on GitHub. Links into the omitted
`documents/` directory require the local downloads. External worktree and UAF
handoff paths in historical receipts refer to artifacts outside this repository.
The September 7 summary's two workstation links have been made portable or
explicitly labeled unavailable; original JSON receipts remain unchanged.

The two `capture_*.py` files are historical scripts, not portable replay tools.
They hardcode the original checkout and temporary dependency paths, make live
requests, and overwrite capture files and manifests. Do not run them over this
archive. Any new investigation should use a separate output directory and record
its own source revision, commands, environment and hashes.

Historical test counts, review verdicts and release receipts apply only to their
recorded revisions. The November archive checks establish file preservation and
JSON readability; they do not rerun the historical research or validate its
financial conclusions.
