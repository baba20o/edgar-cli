# Verifier Contract

Verification is the authority for verified claims.

Rules:

- Only verifier evidence can justify verified completion claims.
- Unverified completion requires human-gate approval or accepted-risk approval.
- Explicit human gates remain binding until resolved. Delegated subjective judgment alone does not require a new gate and is never verifier evidence.
- A passing typecheck is not proof of behavior unless behavior was the agreed target.
- Weak evidence may support investigation, but it cannot close work as verified by itself.
- New verification events record a SHA-256 fingerprint of the exact configured
  verifier definition that ran.
- If that definition changes before closeout, the evidence is stale and the
  current verifier must run again. Historical completed work remains immutable.
- New verification events compare pre-run and post-run workspace fingerprints
  and record the post-run value. A mismatch records failed evidence; a covered
  workspace-state change before clean completion makes passing evidence stale
  and requires a rerun. Git-mode authority is an allowlist: the enclosing Git
  `HEAD`, the bytes and modes of tracked changes and non-ignored untracked
  paths reported by Git, content fingerprints for surfaced embedded Git trees
  and every tracked submodule working tree, the nested UAF root scope, and an explicit digest of
  the portable UAF control plane (`.uaf/uaf.config.json`, `.uaf/source/**`,
  generated agent files, and the complete installed/template surface). That
  explicit digest keeps local-only UAF policy covered even though Git ignores
  it.
- Nested repositories are traversed as raw content, without executing their
  Git configuration, monitors or filters. Nested `.git` metadata is excluded;
  nested ignore rules are not consulted. Root Git status ignores submodules,
  while their bytes are fingerprinted separately. The `dirty` status diagnostic
  is root-only, not proof that submodules are clean. Root Git configuration and
  the selected Git executable remain trusted operator inputs, not sandboxed
  content. Rerun verification after an upgrade changes fingerprint policy.
- Git-mode capture fails closed when status output contains a path that cannot
  be decoded safely as UTF-8 or when a non-deleted status path cannot be read.
  UAF does not silently replace such a path with content-blind authority.
- Snapshot comparison cannot detect a transient covered edit that is fully
  restored before the post-run capture. Cooperative workers must not mutate
  verifier-covered scope while a verifier is running.
- Outside raw nested-tree capture, other files ignored by repository `.gitignore`, `.git/info/exclude`, or the
  user's global Git excludes are outside Git-mode workspace authority. This can
  include build output, local environment files, and application-specific
  generated artifacts. A claim that depends on ignored content needs a verifier
  that checks that content; workspace fingerprinting alone is not proof.
- The ignored `.uaf/uaf.local.json` overlay is deliberately outside workspace
  authority. It can change provider launch policy and external mount roots,
  but not verifier definitions or completion authority. Inspect effective
  launch policy with `uaf runtime profile audit`; provider-profile review is an
  operational safety gate, not verifier evidence.
- The following coordination-path suffixes are excluded at the active UAF root
  and under any directory named `.uaf` at any repository depth. `checkpoint.md`
  is excluded at any repository depth. The list is machine-pinned to the
  runtime policy:

<!-- UAF-OPERATIONAL-PATHS-START -->
```json
[
  ".uaf/cache",
  ".uaf/contacts",
  ".uaf/context",
  ".uaf/conversations",
  ".uaf/exports",
  ".uaf/inbox",
  ".uaf/observers",
  ".uaf/quarantine",
  ".uaf/runtime",
  ".uaf/workflows",
  ".uaf/.tmp",
  ".uaf/.lock",
  ".uaf/.lock.reclaim",
  ".uaf/ledger.jsonl",
  ".uaf/memory.jsonl",
  ".uaf/memory.md",
  ".uaf/uaf.local.json",
  "checkpoint.md"
]
```
<!-- UAF-OPERATIONAL-PATHS-END -->

  Canonical UAF source, portable config, generated instructions, and the full
  installed/template payload remain fingerprinted even in local-only mode.
  Git-visible application content is represented by `HEAD` and current-content
  descriptors. The raw Git status hash is diagnostic only, so staging or
  unstaging byte-identical content does not invalidate evidence. Excluded coordination paths remain governed by the
  controls applicable to them; `checkpoint.md` is mutable coordination state
  and is never evidence.
- Low-level ledger append callers must supply a trusted current workspace
  snapshot for new verification and evidence-backed completion events. Event
  fields cannot authenticate themselves. Structured excluded stores remain
  subject to their schema, reference, health, and locking rules where defined.

Verifier definitions in `.uaf/uaf.config.json` are security-sensitive.

Verifier commands must be structured argv records, not shell strings:

```json
{
  "id": "sync",
  "cmd": "node",
  "args": ["{uafCli}", "sync", "--check"],
  "cwd": "{repoRoot}",
  "shell": false,
  "timeout_ms": 120000,
  "network": false
}
```

The verifier runner resolves `{uafCli}` in verifier `cmd` and `args` at execution time. Prefer `cmd: "node"` with `args: ["{uafCli}", ...]` for UAF self-checks so installed repos do not depend on platform-specific package-manager shims. Ledger verification details keep the configured command form so machine-local package paths do not become committed history.

Verifier `env` overrides are allowlisted. Use only `CI`, `NODE_ENV`, `NO_COLOR`, `FORCE_COLOR`, or `UAF_*` keys, plus `PYTHONPATH` limited to repository-relative entries such as `src` for source-layout Python projects. Relative entries resolve against the verifier's `cwd`; prefix an entry with `{repoRoot}/` to pin it to the checkout regardless of `cwd`. Separate entries with `:` or `;` in committed config; the runner rewrites them with the host's own delimiter. Do not inject `PATH`, `NODE_OPTIONS`, preload hooks, credentials, or provider auth through verifier config.

`network:false` is required policy metadata for verifier definitions. The default Node verifier runner does not create an OS-level offline sandbox by itself; use an external sandboxed verifier command when network isolation must be mechanically enforced.

Prefer direct `node` verifier commands over package-manager shims when the verifier can be expressed that way. Package-manager binaries such as `npm` are shell shims on some platforms and can fail under `shell:false`; direct `node` argv keeps verifier execution portable and deterministic.

Do not run newly changed verifier definitions blindly.

Verification events must reference verifier IDs configured in `.uaf/uaf.config.json`. Legacy verifier waivers are only for migration of historical ledgers; new verification support should come from `uaf verify`.

Use `uaf verify` to run a configured verifier and record evidence:

```text
uaf verify --id <verifier-id> --claim <claim-event-id>
uaf verify --id <verifier-id> --claim latest
uaf verify --id <verifier-id> --claim <claim-event-id> --complete
uaf verify bundle --ids <verifier-id[,verifier-id]> --claim <claim-event-id>
uaf verify bundle --ids <verifier-id[,verifier-id]> --claim latest
uaf verify bundle --ids <verifier-id[,verifier-id]> --claim <claim-event-id> --complete
```

`--claim latest` resolves to the latest claim for the selected task scope and prints the resolved event id. Prefer it in workflow templates and closeout commands where hand-copying event ids creates risk. If multiple tasks are open, pass `--task <task-id>`. If no task is open, also pass `--task <task-id>`. Ambiguous or closed-task latest-claim resolution must fail closed.

With `--complete`, the CLI appends a completion event only when the verifier passes.
Bundle execution does not fail fast: it runs every selected verifier and
publishes each verifier event against that verifier's immediate post-run
workspace snapshot. This preserves the complete evidence trail when a later
verifier fails or detects a workspace change. With `uaf verify bundle --complete`, the
CLI appends a separate completion event only when every verifier in the bundle
passes against one current workspace state, and the completion must reference
every passing verification event produced by the bundle.
Verifier `--complete` modes must refuse already terminal tasks, including `completed`, `completed_accepted_risk`, and `aborted`.

Tasks may bind a mechanical verifier bar with:

```text
uaf task --summary "<task>" --verifier-ids <verifier-id[,verifier-id]>
```

When `bound_verifier_ids` are present, `uaf close`, `uaf complete`, and `uaf verify --complete` refuse a clean `completed` closure until every bound verifier has passing evidence for the latest claim. The prose `--verifier` task field is descriptive only.

Use `uaf verify --inventory` to list configured verifier coverage. The inventory is a planning aid, not proof that any verifier has passed.

Failed verifier events include machine-readable `failure_class` when possible:

- `test_failure`
- `timeout`
- `output_limit` (captured output exceeded the configured byte bound)
- `spawn_error`
- `signal`
- `nonzero_exit`
- `workspace_changed`
- `unknown`

UAF captures the workspace immediately before and after every verifier command.
When those snapshots differ, UAF records failed evidence with `failure_class:
workspace_changed`, even when the command exits zero. In a bundle, UAF also
compares the prior verifier's post-run snapshot with the next verifier's pre-run
snapshot. A mismatch fails the next verifier and records
`bundle_previous_workspace` in its details, even when that verifier's own
pre/post snapshots match. Snapshot comparison cannot detect a transient edit
that occurs and is fully restored between captures. Cooperative workers must
not mutate verifier-covered scope while a verifier runs or between bundle
verifiers. Verifiers must inspect state without mutating covered workspace
content; move formatting, code generation, and other mutations into a separate
work step before verify.

Failure class helps triage evidence. It does not weaken the rule that failed verification cannot close verified work.
