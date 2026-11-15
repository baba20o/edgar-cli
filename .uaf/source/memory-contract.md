# Memory Contract

Memory stores durable project truth.

Files:

```text
.uaf/memory.md
.uaf/memory.jsonl
```

Rules:

- Use `memory.md` as the short generated orientation map.
- Use `memory.jsonl` as the canonical structured memory store.
- Do not hand-edit `memory.md`; run `uaf memory render` after migration or repair.
- Use `uaf memory render --check` to detect map drift.
- A mutating render backs up divergent existing map contents under `.uaf/cache/memory-map-backups/` before replacement. Generated maps superseded by a normal `uaf promote` update do not create redundant backups.
- Use `uaf promote` to append structured memory records when possible.
- Add memory only when the information is durable, reusable, and likely to reduce future rediscovery.
- Do not use memory for temporary progress notes.
- Treat provider-specific or private model memory as personal cache only. Durable repo, team, or cross-agent knowledge must be promoted into UAF memory or project documentation so Codex, Claude, and future agents share the same truth.
- Runtime worker packets fail closed on memory portability: they include only active records explicitly marked `portable`. Local-only and legacy records with unspecified portability are not sent to provider runtimes.
- Do not silently delete or rewrite memory records in v0.
- Deprecated or superseded memory must be marked with metadata or a new record.
- `uaf promote --supersedes <ids>` appends replacement guidance. Recall and
  generated maps derive effective state; replaced records remain historical even
  if their successor is later retired. Missing references and cycles are errors.
- `uaf memory retire --id <id> --reason "<why>" --source <event-id>` appends a
  local-only retirement record. `--dry-run` previews without writing.
- `uaf memory show <id>` exposes the original and effective state; `--all` on
  list/recall includes historical records.
- Optional `--applied-memory <ids>` / `--contradicted-memory <ids>` on claims,
  worker reports, or `uaf runtime report` bind citations to original record hashes.
  Do not edit or remove those originals. A retirement preserves receipt integrity.
- `uaf memory usage` reports citations, not benefits. No citation means unknown
  use. Citation counts never change confidence, ranking, portability or authority.
- Reflection of a contradiction proposes inspection before operator correction.
  Do not require citation quotas or automatically retire a record from a report.

Good memory candidates:

- verified setup commands
- verifier definitions
- stable architecture facts
- accepted design decisions
- project conventions
- recurring mistakes converted into guardrails

Common command:

```text
uaf promote --type decision --scope <scope> --summary "<durable fact>" --details "<supporting detail>" --source <ledger-event-id>
```

Keep `--summary` compact. Put longer explanation in `--details` or split the lesson into several focused memory records.

Use `uaf reflect --task <task-id>` after meaningful work to inspect
evidence-linked promotion candidates. Reflection is read-only and candidate-only;
the operator must explicitly run `uaf promote` for a durable lesson.
