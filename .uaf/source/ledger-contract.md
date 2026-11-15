# Ledger Contract

Claims and worker reports may contain up to 16 `memory_receipts`, each binding
`memory_id`, `relation` (`applied` or `contradicted`), and `record_sha256`.
Shared append validation rejects missing memory or mismatched content hashes.
`uaf check` validates historical links too. Receipts are optional reported
observations; they are never completion support and cannot appear on authority
events. Retirement/supersession preserves original memory bytes and receipt links.

The ledger records work events and state transitions.

File:

```text
.uaf/ledger.jsonl
```

Rules:

- The ledger is append-only in v0 except for the explicit, backed-up
  `uaf migrate normalize-lists` compatibility rewrite described below.
- Each nonblank line is one JSON object.
- Use `uaf task` to start scoped work when the task is resumable, risky, subjective, or explicitly UAF-managed.
- Use `uaf finding` for rough edges, risks, ideas, gaps, and bugs that should be visible but are not proof.
- Use `uaf finding resolve` when a finding has been addressed or superseded by later support.
- Use `uaf debt list` to surface unresolved findings, deferred human gates, undecided worker reports, and accepted-risk closeouts.
- Use `uaf show`, `uaf trace`, and `uaf ledger query` for focused read-only ledger inspection instead of hand-grepping JSONL.
- Optional `trace --format markdown --out <relative-file>` creates a new report,
  never overwrites an existing file, and refuses linked or UAF/Git control paths.
  Prefer `.uaf/exports/<unique-name>.md`; this does not modify ledger authority.
- Use `uaf note` to append structured ledger events when possible.
- Do not use `uaf note` to append abort, verification, human-gate approval, accepted-risk, completion, or finding-resolution authority events; use the purpose-built command so provenance and support checks are enforced.
- Use `uaf verify` to append verifier evidence when possible.
- Use `uaf review` to list pending human gates and produce a concrete prompt for the human.
- Use `uaf gate` for subjective human review requests, approvals, requested changes, rejections, and deferrals.
- Use `uaf abort` to append an explicit aborted task state for unsafe or invalid paths.
- Use `uaf close` for the common completion path backed by verifier evidence, human approval, or accepted risk.
- Use `uaf complete` as the manual exact-event escape hatch when support inference is not appropriate.
- Use `uaf lifecycle audit` before batch-closing stale task or mission state.
- Use `uaf lifecycle close-stale --dry-run` before any stale lifecycle cleanup write.
- Completion must reference supporting evidence.
- Matching by task alone is not enough.

Important relationship fields:

- `event_id`
- `task_id`
- `related_event_ids`
- `verifies_event_ids`
- `evidence_event_ids`
- `human_gate_event_id`
- `risk_acceptance_event_id`
- `bound_verifier_ids`
- `review_campaign_id`
- `reviewer_id`
- `requested_provider` / `requested_model` / `requested_effort`
- `resolved_model` / `model_resolution`
- `resolved_effort` / `effort_resolution`
- `unverified_effort_accepted`
- `prompt_sha256`
- `attempt_index` / `fallback_for_assignment_id`
- `record_origin` for structured automation provenance

A completion claim without evidence is not complete.

If a task declares `bound_verifier_ids`, a clean `completed` closeout through `uaf close`, `uaf complete`, or `uaf verify --complete` requires passing verification evidence for every bound verifier against the latest claim. Missing bound verifier evidence must be repaired or closed as accepted risk. Human approval can document accepted risk, but it cannot replace missing mechanical verifier evidence for a clean `completed` closeout.

Verification events must use verifier IDs configured in `.uaf/uaf.config.json`. Legacy verifier waivers are migration-only records for older ledgers and must not be used to bless new forged evidence.

Review campaign provenance:

- A campaign owns a dedicated red-team mission whose `review_campaign_slots`
  declare the immutable reviewer and fallback-attempt plan. Each attempt binds
  provider, model, optional reasoning effort, profile or adapter, scope, prompt hash, report contract, and
  assignment-contract hash.
- A campaign assignment and its report must carry the same campaign, reviewer,
  requested provider/model/effort, effort-risk acknowledgement, prompt hash,
  and attempt index.
- Requested effort is launch intent, not proof. `resolved_effort` is legal only
  with `effort_resolution: provider_reported`; otherwise the report must keep
  requested effort explicitly unconfirmed. Missing catalog capability accepted
  with `--accept-unverified-effort` remains visible and cannot override known
  incompatibility.
- Reviewer verdicts and orchestrator decisions must carry the same campaign and
  reviewer identity as their target.
- Public `uaf assign` cannot set campaign-routing fields. Campaign assignments
  are created only through campaign start or `uaf review iterate`.
- A fallback assignment must be the exact next attempt declared for the same
  task, mission, campaign, and reviewer slot. It must point to the preceding
  assignment and preserve the declared contract hash; attempts cannot be
  skipped, reordered, duplicated, or mutated.
- Failed attempts remain explicit reports and routing decisions. They cannot be
  erased by a successful fallback. Router-created records use
  `record_origin: campaign_router` so provider failures cannot be confused with
  a human-inspected review or a reviewer-authored blocked report. Human-facing
  campaign, review, show, and trace surfaces must render that origin explicitly.
- Only a broker-authored runtime failure or a launch/preflight failure observed
  directly by the foreground router may advance a declared fallback. Manual
  cancel, kill, and stale repair require operator intervention and cannot create
  a router failure record.
- Each campaign assignment may allocate exactly one runtime run. Allocation is
  checked while holding the repository lock, and broker start atomically claims
  a detached run from `pending` before writing artifacts or invoking a provider.
  Re-entry cannot replay a completed attempt or replace its captured response;
  completed runs cannot be canceled.
- A fallback cannot be created until the preceding router report is followed by
  a blocked router verdict and a `split_follow_up` router decision. These records
  are appended atomically.
- Campaign identity and model agreement are orchestration metadata, not
  verifier evidence or completion authority.

Record ledger state only when the work is resumable, risky, long-running, multi-agent, or explicitly UAF-managed.

Common commands:

```text
uaf task --summary "<task>" --target "<outcome>" --scope "<scope>" --verifier "<verifier>" --verifier-ids <verifier-id[,verifier-id]>
uaf task --from-finding <finding-event-id> --summary "<fix>"
uaf finding --type rough_edge --scope "<scope>" --summary "<operator friction>"
uaf finding --type risk --severity medium --task <task-id> --summary "<risk>"
uaf finding resolve --id <finding-event-id> --by <support-event-id> --summary "<resolved>"
uaf debt list
uaf show task <task-id>
uaf trace --task <task-id> --verify-chain
uaf ledger query --type finding --text "<text>"
uaf note --type claim --summary "<claim>"
uaf note --type blocker --task <task-id> --summary "<blocker>"
uaf abort --task <task-id> --summary "<abort reason>"
uaf gate --task <task-id> --related <claim-event-id> --summary "<human review needed>"
uaf review --latest --prompt
uaf review --apply <pending-gate-event-id> --approve --summary "<approved>"
uaf review --apply <pending-gate-event-id> --changes --summary "<requested changes>"
uaf review --apply <pending-gate-event-id> --reject --summary "<rejected>"
uaf review --apply <pending-gate-event-id> --defer --summary "<defer reason>"
uaf close --task <task-id> --summary "<done>"
uaf close --task <task-id> --claim-set --related <event-id> --summary "<accepted investigation output>"
uaf lifecycle audit
uaf lifecycle close-stale --dry-run
uaf lifecycle close-stale --accept-risk --plan-hash <dry-run-plan-hash>
uaf migrate normalize-lists --dry-run
uaf migrate ledger-roots --dry-run
uaf migrate review-rejections --dry-run
uaf migrate strict-waivers --dry-run
```

For a task-level judgment gate that has no narrower claim event, `--related task-root` resolves to the unique task or mission root and persists its concrete event ID. Resolution fails closed when the task has zero or multiple roots.

Finding records:

- `uaf finding` appends a `finding` event with `finding_type` and `severity`.
- `uaf finding resolve` appends a `finding_resolution` event linked to the original finding and evidence-bearing support.
- Allowed finding types are `rough_edge`, `risk`, `idea`, `gap`, and `bug`.
- If `--task` is omitted, `uaf finding` uses the latest open task; if no task exists, pass `--task`.
- `uaf finding list` shows open findings by default; use `--resolved` or `--all` for history.
- `uaf task --from-finding <finding-event-id>` creates a linked follow-up task without marking the finding resolved.
- Findings are operational signal, not evidence of completion or verification.
- Finding resolutions are lifecycle signal, not verifier evidence by themselves.
- Finding resolutions must have support beyond the original finding. A claim, finding, worker report, reviewer verdict, orchestrator decision, or unsupported note cannot resolve a finding.
- Normal support is a passing verification, terminal completion, approved human gate, or completed risk acceptance.
- If review work resolves a finding, record the verifier, human gate, accepted-risk, or completion support behind the review decision.
- Promote a finding into memory only when it becomes durable guidance or a recurring guardrail.

Debt records:

- `uaf debt list` is read-only and surfaces unresolved trust debt.
- Debt rows include unresolved findings, deferred gates, accepted-risk completions, and worker reports still needing review or decision.
- `uaf debt list --strict` exits nonzero when debt exists; use it as a policy tripwire only when that friction is intended.

Lifecycle cleanup records:

- `uaf lifecycle audit` classifies open task and mission state as closeable, blocked, or manual.
- `uaf lifecycle close-stale --dry-run` previews the exact closure event count without writing and prints a `plan_hash`.
- `uaf lifecycle close-stale --accept-risk --plan-hash <dry-run-plan-hash>` may close stale historical work without objective verifier evidence only when the reviewed plan hash still matches; it appends explicit `risk_acceptance` and `completion` events.
- Manual `uaf lifecycle close-task` and `uaf lifecycle close-mission` are idempotent and must not append duplicate completions once a task already has a completion or final task state.
- Manual lifecycle closeout must not close active or pending-verification work unless the operator explicitly passes `--allow-fresh`; prefer `uaf close --task <task-id>` for normal current work so latest-claim and bound-verifier guards run.
- A `task_id` has exactly one active root declaration: either `task` or `mission`. A second root declaration is invalid regardless of declaration order.
- Clean completion cannot close a task whose latest projected state is `blocked`. A blocker is a current operational state marker, not a durable debt record: recording a later claim attests that the blocker was resolved and reopens verification. Use a finding when an obligation must remain open independently of later claims; otherwise abort or use explicit accepted risk.
- Requesting or responding to a human gate cannot erase an existing blocker.
  Clean completion also refuses a changes-requested response on the current
  claim, even with passing mechanical evidence or a different approved gate.
  A corrected new claim reopens verification; old deferred/unanswered gates
  remain owed until explicitly resolved. Required gates must approve the new
  claim rather than reuse approval of the old one.
- New clean ledger completions recheck assignment/report/decision/human-gate
  obligations under the ledger lock. Public completion commands also check
  task-owned active runs/workflows there (invalid workflow ownership fences
  globally). Historical events are not rewritten. Accepted-risk collection
  retains its separate explicit authority; it is not verified completion.
- A mission root cannot complete while any task already associated with that mission remains nonterminal. Close or abort child tasks first.
- A terminal mission cannot accept a new child task or nested mission root. Reopen work under a new mission instead of attaching it after completion or abort.
- Once a task is terminal, the append boundary rejects new claims, verification, assignments, reports, review verdicts, human-gate activity, and other work-producing records for that task. Start a new task for additional work. Postmortem findings, finding resolutions, migration or administrative decisions, compaction records, supported terminal mission state, and the matching second-step orchestrator rejection after an explicit review abort remain legal.
- Reviewer verdicts and orchestrator decisions route review queues; they do not change task lifecycle. Only task-state events such as a claim, blocker, completion, or explicit abort do that. Confirmed rejected review commands append the rejection and an explicit `abort` event atomically.

Migration records:

- `uaf migrate normalize-lists` removes only exact duplicate values from arrays
  that current ledger and memory schemas require to be unique. It preserves
  record IDs, first occurrence order, and untouched JSONL lines; validates the
  resulting records before writing; refreshes the generated memory map; and
  stores original files plus a receipt under
  `.uaf/quarantine/migrations/normalize-lists/`. This narrow compatibility
  repair is the sole exception to append-only storage and does not fix or waive
  unrelated reference, lifecycle, or authority errors.
- `uaf migrate ledger-roots` appends missing historical task roots.
- `uaf migrate review-rejections` appends explicit aborts for rejected reviewer or orchestrator routes that predate the review-rejection boundary and have no later terminal task record.
- `uaf migrate strict-waivers` appends explicit compatibility records for historical verifier-id, task-link, terminal-mission, claim-binding, completion-lifecycle, pre-projection authority, or implicit rejected-review behavior accepted by older UAF versions, including mission-root task-link mismatches, pre-strict bound-verifier completions, blocked clean completions, mission roots closed with then-open child tasks, and old human-gate or accepted-risk shapes.
- Running claim-binding or strict migration against an empty ledger creates a synthetic migration task, establishes the required append-order boundaries, and immediately aborts the synthetic task. A successful empty migration therefore never leaves its compatibility window open.
- Projection-authority waivers are bounded by a one-time append-order migration marker. They preserve historical interpretation only and cannot authorize a completion appended after the marker.
- Legacy planless review-campaign waivers preserve only historical readability. `uaf migrate strict-waivers` establishes a second append-order activity boundary; later assignments and completion claims for those campaigns are invalid. Abort the legacy task or start a new plan-sealed campaign.
- A plan-sealed review campaign can close only through `uaf review campaign close`. Its risk acceptance and completion carry `campaign_closeout: true`, and reference validation requires both markers plus every final accepted reviewer lineage record.
- Campaign attempts seal an external-mount digest so runtime launch cannot gain
  a new external read surface after allocation. Exact machine-local mount paths
  remain in ignored runtime state rather than the tracked ledger. Selected
  mounts must exist as directories; direct symlink paths are rejected and
  ancestor links are resolved before the canonical target digest is sealed.
- A campaign worker report requires a non-whitespace runtime `result.txt`
  referenced by matching broker-authored candidate and completion events for
  the sealed run. Generic runtime-event injection, incomplete response paths,
  and arbitrary response-artifact bridges cannot manufacture that provenance.
- Worker assignments may declare `subagent_policy` as `disabled`, `bounded`, or
  `allowed`. A bounded policy requires at least one positive concurrency, total,
  or depth limit. Every declared policy also records `subagent_enforcement`;
  prompt-only enforcement is audit context, not proof that the provider obeyed
  a hard ceiling. Campaign plans seal these fields per attempt.
- Completion-lifecycle waivers are independently bounded by a one-time append-order marker and cannot authorize a blocked-task or open-child mission completion appended after that marker.
- Migration waivers are compatibility records only. They do not prove work, verify claims, approve gates, or justify new weak records. Each waiver applies only to its named rule class; claim-binding or projection-authority waivers cannot erase blocked-task or open-mission-child lifecycle reasons.
- Migration boundaries rely on append order under UAF's cooperative local-writer trust model. They are not cryptographic tamper evidence and cannot defend against a deliberate rewrite of the JSONL store.
- Mission lifecycle cleanup records task completion before the mission completed state.
- Lifecycle cleanup must not close active tasks, open human gates, pending worker reports, undecided reports, or other live queues.

Human feedback records:

- `uaf gate --approve` appends a completed `human_gate` event that can support completion.
- `uaf gate --changes` appends a `human_response` event with `response: changes_requested` and returns an unblocked task to `active`; an existing blocker remains blocked.
- `uaf gate --reject` appends a `human_response` event with `response: rejected` and marks the task `aborted`.
- `uaf gate --defer` appends a `human_response` event with `response: deferred`; the gate remains visible in `uaf review`.
- The first related event identifies the exact pending gate on the same task.
  Remaining references are context, not authority to answer other gates.

Claim-set closeout records:

- `uaf close --claim-set --related <event-id>` appends a `risk_acceptance` event and a `completed_accepted_risk` completion for accepted investigation output.
- Claim-set closeout is for useful but unverified findings, reports, or analysis. It must not be described as verified completion.
- Claim-set closeout is mutually exclusive with `--accept-risk`, `--evidence`, `--gate`, and `--risk`.
- Boolean closeout consent requires true (bare flag or explicit `true`).
  Explicit `false` never enables `--claim-set` or `--accept-risk`.
