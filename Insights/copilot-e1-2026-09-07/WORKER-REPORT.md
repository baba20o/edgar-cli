# E1 worker handoff

**Local result: implementation prepared, verification blocked.** No tests
executed, no passing result claimed, and no mission closure or accepted-risk
decision made. The orchestrator owns review, integration, and final verification.

## Changes

- `edgar/items.py`: structure-preserving HTML conversion, linked/nested TOC
  filtering, typography-normalized titles, quarterly `(Part, Item)` identities,
  MD&A/title/code aliases, inline-reference rejection, and confidence requiring
  target identity plus an immediate successor boundary.
- `edgar/api.py`: wire structural extraction into `filing_section`, expose Part
  and confidence evidence, update its caveat, clean index link text, and normalize
  document URLs while preserving the original `href`.
- `edgar/documents.py`: shared SEC viewer normalization with origin, Archives
  path, traversal, duplicate-parameter, and filename validation; reject empty
  downloads and recognized XBRL viewer shells.
- `edgar/cli.py`: normalize again at the download boundary, use the actual
  document filename, and populate `downloaded_to` only after retrieval/content
  validation and writing.
- `tests/test_items.py`: small deterministic quarterly/annual HTML regressions,
  Part conflicts, TOC/navigation layouts, aliases, confidence, API truncation,
  NVIDIA/Microsoft-style layouts, and optional local VRT/MSFT original checks.
- `tests/test_documents.py`: fake HTTP index-to-downloader and CLI coverage for
  viewer variants, ordinary exhibits, hostile links, shells, empty responses,
  and HTTP failure. No live requests.
- `COPILOT-E1-REPORT.md`: this handoff.

## Evidence and commands

Read the F01/F02 report, saved failure JSONs, relevant source/tests, and the
small viewer-shell excerpt through file tools. The saved JSONs report the
864-character wrong quarterly section, the 5,739-character clipped Business
section, and a badge-contaminated viewer download. The saved shell has
`<title>XBRL Viewer</title>`. These are baseline evidence, not proof of the fix.

Two shell attempts were rejected before execution with:
`Shell is denied unless the profile explicitly allows a provider-classified read-only command.`

1. A `.venv/bin/python` heredoc intended to extract bounded original-HTML heading
   excerpts and failure metadata. It did not execute; large originals were not
   dumped or copied.
2. The exact focused command:

```sh
.venv/bin/python -m pytest -q tests/test_items.py tests/test_documents.py tests/test_api.py tests/test_cli.py -k 'items or section or document or index or exhibits' --basetemp=tests/.pytest-e1-tmp -p no:cacheprovider
```

No shell workaround, installs, network research, subagents, commits, Git metadata
changes, or shared UAF/checkpoint/memory writes were attempted. Source review
continued through permitted read/edit tools. The full suite was not attempted
after the test-tool denial.

## Required handoff and limits

Run the focused command above, inspect the optional original-filing results,
then run:

```sh
.venv/bin/python -m pytest -q tests --basetemp=tests/.pytest-e1-tmp -p no:cacheprovider
```

Review any failures before integration. The provided originals include VRT
10-Q and MSFT 10-K, but not the complete VRT 10-K or NVIDIA original; those
preservation claims still need orchestrator evidence. Minimal fixtures cover
the reported quoted Business cross-reference and representative NVIDIA layout.

Extraction remains heuristic. Flattened mirror text cannot recover lost
structure and is deliberately not high confidence. The URL normalizer fails
closed for unsupported viewer routes, non-SEC origins, and nonstandard Archives
paths/filename characters. No live download was performed.

No portable memory informed this run. A candidate lesson for the orchestrator:
retain structural/Part identity evidence separately from requested labels, and
validate destinations rather than trusting viewer query parameters.
