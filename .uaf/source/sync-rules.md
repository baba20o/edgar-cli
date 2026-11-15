# Sync Rules

`AGENTS.md` and `CLAUDE.md` are tracked generated outputs.
In the UAF package repo, `templates/installed/AGENTS.md` and `templates/installed/CLAUDE.md` are also tracked generated outputs.

Canonical source:

```text
.uaf/source/**
```

Generated agent files contain only the files listed in
`source.agent_shared` plus their runtime adapter. The broader `source.shared`
set remains canonical, installed, validated, and available for on-demand reads.

Rules:

- Do not edit generated sections of `AGENTS.md`, `CLAUDE.md`, or installed template agent files directly.
- Edit `.uaf/source/**` and run `uaf sync`.
- `.uaf/source/manifest.json` schema v2 records normalized content hashes for every configured shared source file and runtime adapter. `uaf check` and `uaf sync --check` detect drift in any of them, including contracts not rendered into the startup agent files.
- The manifest keeps current `content_hashes` separate from trusted installed-template `template_hashes`. Normal `uaf sync` updates current hashes without blessing a local customization as the template baseline.
- Generated blocks must include renderer version, source files, source hash, and rendered hash.
- Manual user sections may exist only outside generated blocks.
- Sync drift is blocking only when the task touches UAF source, generated agent files, sync behavior, or verifier policy.
- In the UAF package repo, installed-template agent files are rendered from canonical `.uaf/source/**` in the same sync pass that mirrors source files. `uaf sync` also reconciles the installed template config's source/adapter fields and removes stale orphan files under `templates/installed/.uaf/source/**`.
- In an installed repo, `uaf install upgrade` updates safe config defaults without changing any `source.*` settings or contract contents; sync may regenerate manifest metadata and agent outputs. `--source` is explicit and refuses source files that differ from their trusted template baseline. Without a trusted baseline, exact path-specific hashes from packaged historical templates can recognize unchanged files; audit and upgrade report the matching version and Git revision. Historical matches never override an existing trusted baseline. `--force-source` is required to replace customized or unrecognized source and writes backups under `.uaf/cache/install-upgrade/`.
