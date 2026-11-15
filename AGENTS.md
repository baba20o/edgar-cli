# Agent Instructions

<!-- UAF-GENERATED-START -->
<!-- UAF-RENDERER-VERSION: uaf-js-0.2.1 -->
<!-- UAF-SOURCE-FILES: agent-kernel.md,adapters/codex.md -->
<!-- UAF-SOURCE-HASH: fb81183de21586fb5384beab869b894f91803aa3c3eeb6e65ba702d766e55fca -->
<!-- UAF-RENDERED-HASH: bab022009b37aa00ff549a8df02750322e3316682688b92a4416b75b2d937f00 -->

This repository uses Universal Agent Framework.

The following instructions are generated from `.uaf/source/**`.

<!-- UAF-SOURCE-FILE: agent-kernel.md -->

# UAF Agent Kernel

This repository uses Universal Agent Framework. Keep startup context small and
load detailed contracts only when the current operation needs them.

## Prime Directive

Do not describe work as done until the agreed verifier, human gate, or explicit
accepted-risk record supports the claim. Reports, reviews, logs, and confidence
are not verifier evidence.

## Startup

1. Run `uaf begin --query "<current topic>"`; add `--task <task-id> --for continue`
   when resuming known work. Without `--task`, startup is a repository overview.
2. Resolve shared integrity failures and selected-task blockers before mutation.
   Inspect ownership/scope with `uaf ops` before acting on unrelated queues;
   do not report, approve, or stop another session's work to clear startup.
   Task selection does not prove that file scopes are disjoint.
3. Read the relevant root or project `checkpoint.md` when one exists.
4. Use focused retrieval instead of loading whole stores:
   - `uaf show task <task-id>`
   - `uaf trace --task <task-id> --verify-chain`
   - `uaf memory recall --query "<topic>"`
   - `uaf debt list`

Use the smallest safe loop. A narrow, low-risk edit can remain lightweight.

For repeated task inspection, optionally use `uaf work open <session-name>`, then
`uaf work bind <name> --task <task-id> --work-session <session-name>`.
`uaf work show <name> --work-session <session-name>` gives compact readiness.
Pass the namespace explicitly across fresh shell calls; no global current task
is selected. `task --name` creates/binds recoverably; `note --work` supports
claims, decisions and handoffs. `closeout --work --prepare <action-name>` stores
a frozen plan for `action show|apply`; it never refreshes stale intent silently.
Ordinary assign/verify/review and fresh runtime launch also accept `--work`.
Review/runtime targets require explicit `current` or an exact report/assignment
ID; `current` is late-bound, not a saved approval. Ambiguity refuses; existing
runs require their original recovery path. Names grant no additional authority.
Run `uaf help work` for selectors, restore, and concurrent rebinding rules.

Open a UAF task when work is resumable, risky, subjective, delegated,
long-running, or explicitly mission-managed.

For an unfamiliar orchestration decision, `uaf guidance --query "<work idea>"`
optionally consults Terra using a bounded UAF reference packet. Use `--task` for a
small task snapshot and `--session` for follow-ups. Advice is not execution or
evidence; inspect command templates before acting. Do not send private data.
Skip this consultation when the appropriate loop is already clear.

For an optional repository-aware second opinion, use `uaf consult --contact
<name> --model <catalog-id> --query "<question>"`; subsequent turns need only
`--contact` and `--query`. Run `uaf help consult` for disclosure and reset rules.
Consultants are read-only and advisory, not independent reviewers or evidence.
Their broad repository reads may disclose ignored content to the provider.
Do not use this surface for repositories whose sensitive content cannot be shared.

## Core Loop

```text
uaf task --summary "<work>" --target "<outcome>" --scope "<scope>" --verifier-ids <ids>
uaf note --type claim --task <task-id> --summary "<claim>"
uaf verify bundle --ids <ids> --claim latest --task <task-id>
uaf converge --task <task-id> --preview
uaf converge --task <task-id> --apply --plan-hash <hash> --subgraph-revision <hash>
```

Use `uaf close` when convergence is not appropriate. A task with bound verifier
IDs needs current passing evidence from every bound verifier for clean
completion. Clean completion cannot cross a current blocker, and mission roots
cannot close while child tasks remain open or accept new child task or mission
roots after becoming terminal. A task ID has exactly one task-or-mission root
declaration. Routine decisions within delegated scope do not require human
sign-off. Use `--human-gate-required` or `uaf gate` for a decision reserved to the
human or outside the granted authority. Existing explicit gates remain binding;
never fabricate approval. Independent review and verifier requirements still apply.

Convergence uses a repository-wide ledger revision as a deliberate safety
fence. Any intervening ledger append invalidates a preview, including an append
to unrelated work. The stale-plan response reports whether the selected task's
relevant subgraph remained unchanged; that diagnostic never permits bypassing
a fresh preview and review.

Record non-proof operational signal with:

```text
uaf finding --type rough_edge|risk|idea|gap|bug --summary "<signal>" --scope "<scope>"
```

## Delegation And Review

- Use `uaf fork` only for explicitly approved owner-history reuse by non-review
  workers. Inspect `uaf fork capabilities` first. Pin a baseline, submit an
  occurrence key, and recover the recorded child instead of blindly repeating
  allocation. Forks inherit context, not authority; independent reviewers stay
  fresh. See `uaf help fork` for disclosure, workspace, and retention limits.
- The orchestrator owns shared UAF state, integration, final verification, and
  closure. Workers stay inside assigned scope and return reports.
- Treat detached work as owed follow-up until terminal, reported, reviewed, and
  accepted. Use `uaf runtime wait --all --guide` and only work on disjoint scope
  while waiting.
- Use `uaf runtime observe`, `tail`, and observer snapshots for status, never as
  completion evidence.
- After requested changes, use
  `uaf review iterate --report <report-id> --lane implement` for a repair worker,
  or omit `--lane` to preserve the prior assignment lane in the same review
  thread.
- Review iteration preserves same-role/lane custom briefs. Changing role/lane
  requires explicit `--scope` and `--output-contract`, plus `--details` or
  `--clear-details` when custom instructions exist. Inspect the resulting
  runtime prompt for conflicting instructions before launch.
- Review verdicts and orchestrator decisions route queues but never change task
  lifecycle alone. A confirmed rejected review appends an explicit `abort`
  event atomically; use changes requested when the task should continue.
- Before committing non-trivial work, run an opposite-brand review with
  `uaf adversarial-review`, address blockers, and record the reviewer verdict
  plus orchestrator decision.
- Classify reviewer divergence before resolving it. Settle factual disputes by
  testing; use `uaf combo start divergence-debate` for bounded judgment disputes
  and escalate unconverged judgment to a human gate. Its three semantic rounds
  are explicit phases; do not use generic workflow retries to add more turns.
- Use `uaf models catalog` when a specific model must be selected or provider
  availability is uncertain. Prefer provider-reported descriptions and
  capabilities over assumptions from model names; treat learned strengths and
  weaknesses as dated, source-linked evidence, not permanent facts. Explain a
  model choice from current catalog facts and recent task evidence; do not turn
  an alias or anecdote into a leaderboard. Catalog discovery is live
  provider access and must pass an audited profile gate or carry explicit
  `--accept-profile-risk`. Bind `--effort` only to an explicit model; UAF must
  check the live model capability before launch. A known unsupported level is
  a hard failure. Use `--accept-unverified-effort` only when the catalog omits
  the model or capability, and preserve that uncertainty in the run/report.
  Antigravity effort is encoded in its exact model ID, not a separate flag.
  Use `uaf review campaign` only when independent
  multi-model or role-specific review justifies the extra cost and latency.
  Campaigns own a dedicated mission with an immutable reviewer/fallback plan;
  after a settled provider failure, `uaf review iterate` can create only the
  exact next declared fallback. Inspect and decide on every final report; model
  agreement is never proof. Campaign allocation seals model, effort, role,
  output contract, profile
  fingerprint, launch limits, risk acknowledgement, prompt, and report
  contract; do not override those values, provider paths, mounts, safety
  policy, or side-turn mode at runtime. Profile effort defaults become
  assignment intent, and cached preflight remains valid only for the exact
  launch identity it audited. Cross-provider fallbacks must declare effort
  unless a campaign-wide value applies. Close only with
  `uaf review campaign close`; generic task closure has no campaign authority.
- Declare nested-agent authority explicitly for delegated work with
  `--subagents disabled|bounded|allowed`. Review campaigns and adversarial
  reviews default to `disabled` so each reviewer slot represents one root
  model. Bounded mode requires an explicit concurrency, total, or depth cap.
  Check the recorded enforcement: `prompt_only` is an instruction and
  `provider_controlled` is a requested provider mechanism, not an absolute
  ceiling. Nested workers are not extra independent reviewers.

## Memory And Reflection

- `.uaf/memory.jsonl` is canonical durable memory.
- `.uaf/memory.md` is generated with `uaf memory render`; do not hand-edit it.
- Correct memory with `uaf promote --supersedes <id>` or retire it with
  `uaf memory retire --id <id> --reason "<why>" --source <event-id>`.
  Inspect originals with `uaf memory show <id>`. Never rewrite cited records.
- Optionally cite materially used or contradicted memory on claims/reports with
  `--applied-memory <ids>` or `--contradicted-memory <ids>`. Receipts are reported
  observations, not proof; missing citations mean unknown use. Use
  `uaf memory usage` to inspect them and `uaf models evidence --lane <lane>`
  for descriptive delegation history. Neither changes completion authority.
- `uaf reflect --task <task-id>` proposes evidence-linked candidates but never
  promotes automatically.
- Use `uaf promote` only for durable, reusable truth. Keep temporary progress in
  the ledger and easy-to-lose owed follow-ups in checkpoint.

## Safety

- Prefer UAF commands over hand-editing JSONL.
- Preserve unrelated user changes.
- Do not store credentials, private user data, secrets, or large raw logs in UAF
  artifacts.
- Inspect unfamiliar runtime profiles with `uaf runtime profile audit` and
  `uaf runtime doctor` before launch.
- A named provider profile owns its launch safety fields; CLI flags cannot
  weaken them. Select or define another profile when different policy is
  required. Profile names still do not prove OS-level isolation.
- Treat `copilot-scan` as experimental mixed-safety: SDK empty mode and UAF
  permissions reject mutation, but provider transport uses network and there is
  no OS sandbox. Copilot launches require an explicit named profile. Run
  `uaf runtime conformance --profile copilot-scan` first.
- Treat `cursor-scan` as experimental mixed-safety: only read/search tools are
  enabled, but broad reads can disclose ignored or out-of-repository content.
  Use an explicit named profile and model, disabled subagents, and the profile
  risk acknowledgement. This is not OS-enforced read-only isolation. Consult
  `uaf help runtime` and `docs/CURSOR_PROVIDER.md` before first use.
- `.uaf/source/manifest.json` seals every configured source contract and
  adapter. After source edits, run `uaf sync`. Source-template upgrades refuse
  customized files unless the operator explicitly uses `--force-source`.

## Detailed Contracts

Load these only when relevant:

- `.uaf/source/doctrine.md`: full operating doctrine and command paths
- `.uaf/source/ledger-contract.md`: event authority and relationship rules
- `.uaf/source/verifier-contract.md`: verifier configuration and evidence rules
- `.uaf/source/memory-contract.md`: durable-memory lifecycle
- `.uaf/source/checkpoint-contract.md`: owed-follow-up queue
- `.uaf/source/kernel.md`: portable external-worker contract
- `.uaf/source/sync-rules.md`: generated instruction ownership

Run `uaf help <command>` for command-specific syntax.

<!-- UAF-SOURCE-FILE: adapters/codex.md -->

# Codex Adapter

Codex-specific guidance:

- Use fast local search such as `rg` when available.
- If `rg` is unavailable, use the next best local search tool.
- Preserve unrelated user changes.
- Before committing non-trivial UAF work, run `uaf adversarial-review --from codex --brand opposite --prompt "<review prompt>" --scope "<scope>"`, address blockers, and record the review decision.
- When using detached workers, keep working only on disjoint scope and rejoin with `uaf runtime wait` before closeout.
- Distinguish verified work from unverified work in final responses.

<!-- UAF-GENERATED-END -->
