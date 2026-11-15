# UAF Doctrine

This repository uses Universal Agent Framework.

Core rules:

- Treat agent conclusions as claims until verified.
- Use the smallest safe loop for the task.
- Prefer evidence over summaries.
- Keep memory for durable truth and ledger for work events.
- Prefer UAF CLI commands over hand-editing JSONL when the CLI can express the record.
- Do not call work verified unless verifier evidence supports it.
- Before committing non-trivial UAF work, run an opposite-brand adversarial review with `uaf adversarial-review`, address blockers, and record the reviewer verdict and orchestrator decision.
- Classify a divergence with an opposite-brand reviewer before resolving it; brand separation is useful but is not proof of independence. A FACTUAL divergence (is there a defect, does the reproduction reproduce, is the claim true) is settled by TESTING, never by argument. A JUDGMENT divergence (scope, trade-off, or design, where no reproduction settles it and neither party is simply correct) is resolved by a bounded divergence-debate: neither silently overrule nor silently defer, argue to convergence on the merits or to a human-gate tiebreaker, and record the decision.
- Routine judgment within explicitly delegated scope does not require human sign-off. New task modes do not imply a human gate. Use `--human-gate-required` or `uaf gate` when the human reserves a decision, scope/permissions would expand, or risk exceeds delegated authority. Existing gates and chosen workflow gates remain binding. Record delegated decisions and their limits; they are not verifier evidence or human approval. Accepted risk still requires its explicit, authorized record.
- Do not leave pending human gates silent; surface them with `uaf review` and prompt the human for approval, changes, rejection, or deferral.
- Do not store secrets, credentials, private user data, or large logs in UAF files.

Memory maintenance uses `uaf memory show <id>` for original/effective state,
`uaf promote --supersedes <id>` for replacement guidance, and
`uaf memory retire --id <id> --reason "<why>" --source <event-id>` for retirement.
Both mutations preserve history. Optional `--applied-memory` and
`--contradicted-memory` flags on claims/reports bind citations to original hashes.
`uaf memory usage` and `uaf models evidence --lane <lane>` expose descriptive
history. Citations and acceptance decisions do not establish correctness; absent
citations mean unknown use. These observations never change completion authority.

Startup sequence:

1. Run `uaf begin --query "<topic>"` to get readiness, open work, human gates, checkpoint excerpts, sync/check status, optional memory recall, and next commands in one read-only startup packet.
2. Add `--task <task-id> --for continue` for known work. Unscoped startup is a repository overview: inspect ownership and scope with `uaf ops`, rather than reporting, approving, or stopping another session's work to clear it. Resolve shared integrity failures and selected-task blockers before mutation. Distinct task IDs do not establish disjoint file scope. `--query` and `--project` do not scope readiness.
3. Use `uaf show`, `uaf trace`, or `uaf ledger query` for focused ledger inspection instead of grepping `.uaf/ledger.jsonl`.

Use `uaf <command>` as the normal CLI form. In the UAF package repo, `node src/cli/main.js <command>` is equivalent when `uaf` is not on PATH.

Allowed closeout states:

- `completed`
- `completed_accepted_risk`
- `blocked`
- `aborted`
- `pending_human_review`
- `pending_verification`

Only verifier evidence can justify describing a `completed` task as verified.

Core CLI path:

```text
uaf status --brief
uaf status --next
uaf begin --query "<topic>"
uaf show task <task-id>
uaf show event <event-id>
uaf trace --task <task-id> --verify-chain
uaf ledger query --type <type> --task <task-id> --text "<text>"
uaf review --latest --prompt
uaf review queue
uaf review show <event-id>
uaf memory recall --query "<topic>"
uaf memory render --check
uaf reflect --task <task-id>
uaf sync --check
uaf ready
uaf closeout
uaf lifecycle audit
uaf task --summary "<task>" --target "<outcome>" --scope "<scope>" --verifier "<verifier>" --verifier-ids <verifier-id[,verifier-id]>
uaf task --from-finding <finding-event-id> --summary "<fix>"
uaf finding --type rough_edge|risk|idea|gap|bug --summary "<finding>" --scope "<scope>"
uaf finding list --type rough_edge --limit 20
uaf finding resolve --id <finding-event-id> --by <support-event-id> --summary "<resolved>"
uaf debt list
uaf note --type claim --task <task-id> --summary "<claim>"
uaf verify --id <verifier-id> --claim <claim-event-id>
uaf verify --id <verifier-id> --claim latest
uaf verify bundle --ids <verifier-id[,verifier-id]> --claim <claim-event-id>
uaf verify bundle --ids <verifier-id[,verifier-id]> --claim latest
uaf abort --task <task-id> --summary "<abort reason>"
uaf gate --task <task-id> --related <claim-event-id> --summary "<human review needed>"
uaf review --apply <pending-gate-event-id> --approve --summary "<approved>"
uaf review --apply <pending-gate-event-id> --changes --summary "<requested changes>"
uaf review --apply <pending-gate-event-id> --reject --summary "<rejected>"
uaf review --apply <pending-gate-event-id> --defer --summary "<defer reason>"
uaf close --task <task-id> --summary "<done>"
uaf close --task <task-id> --claim-set --related <event-id> --summary "<accepted investigation output>"
uaf promote --type <memory-type> --scope <scope> --summary "<durable lesson>" --source <completion-event-id>
uaf check
```

Use `uaf complete` as the exact-event/manual escape hatch when `uaf close` inference is not appropriate.

Use `--verifier-ids` when a task has a mechanical verifier bar. The prose `--verifier` field is orientation text; it is not completion authority by itself.

Clean completion cannot cross a current `blocked` task state. Resolve the
blocker and record a fresh claim before re-verifying, or use explicit abort or
accepted-risk authority. The fresh claim is the explicit assertion that the
current blocker was resolved; use a finding instead when debt must survive later
claims. Mission roots cannot complete while any task already associated with
the mission remains nonterminal.

Do not use `uaf note` to create abort, verification, human-gate approval, accepted-risk, completion, or finding-resolution authority events. Use the purpose-built command so the event provenance and support rules are enforced.

When more than one task is open, pass `--task <task-id>` with `--claim latest`; when no task is open, `--claim latest` also requires explicit `--task`. Latest-claim verification must fail closed instead of falling back to a closed or unrelated task.

Most tasks should use the core path. Do not use the orchestration path for small work unless the task is delegated, risky, subjective, long-running, or explicitly mission-managed.

Orchestration path for missions, workers, and external runtimes:

```text
uaf ops
uaf ready
uaf combo list
uaf combo validate
uaf combo start ship-it --mission <mission-id> --summary "<combo>"
uaf workflow template list
uaf workflow start --template council-investigate --mission <mission-id> --summary "<workflow>"
uaf workflow status --latest
uaf workflow continue --latest
uaf lane list
uaf kernel --format text
uaf mission start --mode <mode> --summary "<mission>"
uaf mission status --mission <mission-id>
uaf mission export --latest --format markdown
uaf mission plan --mode <mode>
uaf task --mode atlas|execution|redteam|meta-control|light|standard|heavy --summary "<task>"
uaf assign --mission <mission-id> --role <role> --scope "<scope>" --template worker
uaf delegate --mission <mission-id> --lane implement|review|redteam|verify|atlas|meta-control --scope "<scope>" --launch --adapter <adapter> --report
uaf delegate --mission <mission-id> --lane review --scope "<scope>" --launch --profile <profile> --report
uaf delegate --mission <mission-id> --lane review --scope "<scope>" --detach --profile <profile> --report
uaf adversarial-review --from <codex|claude> --brand opposite --prompt "<review prompt>" --scope "<scope>" --subagents disabled
uaf adversarial-review --from <codex|claude> --brand opposite --prompt "<review prompt>" --scope "<scope>" --detach
uaf models list --provider codex|claude|copilot|antigravity|all
uaf review campaign --reviewers "<provider:model,...>" --prompt "<review prompt>" --scope "<scope>" --effort <level> --subagents disabled
uaf review campaign --spec <campaign.json>
uaf review campaign status|wait|close --campaign <campaign-id>
uaf runtime list
uaf runtime list --active
uaf runtime show --latest
uaf runtime wait --run <run-id>
uaf runtime wait --all
uaf runtime wait --all --guide
uaf observer snapshot --mission <mission-id>
uaf observer watch --mission <mission-id> --interval-ms 30000 --out .uaf/observers/<mission-id>.md
uaf observer status
uaf runtime observe --latest
uaf runtime tell --latest
uaf runtime message --latest --from <sender> --message "<follow-up>"
uaf runtime message --latest --from <sender> --message "<follow-up>" --detach
uaf runtime tail --latest --last 20
uaf contact message --contact <name> --from <sender> --message "<message>"
uaf runtime profile audit
uaf runtime mount check --profile <profile>
uaf runtime import --source <path> --assignment <assignment-event-id> --report
uaf artifact check --event <worker-report-event-id>
uaf report --assignment <assignment-event-id> --summary "<worker report>"
uaf review close --latest --mission <mission-id> --verdict approved|changes_requested|blocked|rejected --summary "<review and decision>"
uaf review close --response <response-artifact-event-id> --verdict approved|changes_requested|blocked|rejected --summary "<review and decision>"
uaf review iterate --report <worker-report-event-id> --lane implement --summary "<follow-up work>"
uaf review supersede --report <old-report-event-id> --by <new-report-event-id> --summary "<resolved by follow-up>"
uaf review close-mission --mission <mission-id> --accepted-report <worker-report-event-id> --summary "<review mission closeout>"
uaf verify --inventory
uaf lifecycle audit
uaf lifecycle close-stale --dry-run
uaf lifecycle close-stale --accept-risk --plan-hash <dry-run-plan-hash>
uaf migrate normalize-lists --dry-run
uaf migrate ledger-roots --dry-run
uaf migrate review-rejections --dry-run
uaf migrate strict-waivers --dry-run
```

Provider profile safety:

- Use scan profiles such as `codex-scan` and `claude-scan` for read-only review or atlas work. Configure default opposing-brand profiles under `runtime.adversarial_review.profiles` in `.uaf/uaf.config.json`; each value must reference a defined provider profile. Treat `copilot-scan` as experimental mixed-safety because its UAF permission policy is not an OS sandbox and provider transport requires network. Treat experimental `antigravity-scan` as review/atlas-capable but mixed-safety until its `agy --sandbox` read-only behavior is proven in the target repo.
- Treat implementation profiles such as `codex` and `claude-wsl` as write-capable unless the installed repo config says otherwise.
- Run `uaf runtime profile audit` before launching unfamiliar profiles or when a worker needs external mounts.
- Profile audit reports normal and side-turn safety separately, aggregates to the more permissive class, and preserves warnings from both paths. `fixture` and `external` are `unaudited`, not read-only proof: fixture runs no provider, and UAF cannot enforce an external process.
- Inspect `.uaf/uaf.config.json` before launching unfamiliar profiles. Named profiles own their adapter and safety policy: launch-time flags cannot replace the adapter or override sandbox, approval, network, roots, protected paths, command policy, or provider permission-bypass fields. Select or define another profile instead. A profile name is still not proof of OS-level sandbox behavior.
- Treat `antigravity-cli` as experimental. UAF requires AGY's stream-JSON input and output protocol for authoritative session identity, terminal status, usage, and live text/tool events. Authentication home and transcript observation are separate: an explicitly selected AGY application-data directory replaces default-root transcript search. A matching Antigravity `transcript.jsonl` is optional richer history, not the authority for successful completion. Use `uaf runtime doctor --profile antigravity-scan` and `uaf runtime profile audit --profile antigravity-scan` before relying on it in a repo.
- Treat `copilot-sdk` as experimental. Every live Copilot launch, message, or retry requires an explicit named profile. Run `uaf runtime conformance --profile copilot-scan`; empty mode, explicit tools, and permission callbacks are required, and read-only remains policy-enforced rather than OS-isolated. Copilot policy always denies Git metadata, all UAF state, checkpoint files, and common secret-bearing repo files.

`uaf combo` starts reusable Podium-inspired orchestration bundles. Run `uaf combo list` for the authoritative installed set; current built-ins include `council-investigate`, `cross-brand-audit`, `deep-design-review`, `divergence-debate`, `doctrine-compliance`, `implement-review-fix`, and `ship-it`. Combos are workflow templates with role and cross-brand metadata, not evidence. Override roles with `--role role=brand` or with direct template role flags such as `--analyst claude`; unknown direct role flags must be rejected after the template is loaded.

`uaf combo start divergence-debate` is the complement to adversarial review. Adversarial review challenges facts: attack the work and run reproductions; testing settles factual disagreement. A divergence debate resolves judgment about scope, trade-offs, or design when no reproduction can make either party simply correct. Either party may call it. Each states a position, concedes what is right in the other position, and holds what remains unsupported. The template models at most three semantic rounds explicitly. After each response, a clerical alignment check may record `approved` only when both party artifacts explicitly adopt the same outcome; it may not choose a winner, infer tacit agreement, or synthesize a compromise. A remaining split uses `changes_requested`, and the third split activates an executable human-gate phase. The orchestrator is a party to the divergence, not its judge, and must not self-declare victory. Alignment honesty is prompt-only: the workflow prevents a default verdict from silently approving alignment, but it does not compare party artifacts or stop an operator from entering `approved`. Every debate records the positions, who moved and why, and the aligned outcome or human decision in the ledger so the result can be promoted to decision memory instead of re-litigated. Debate is not ceremony: if one side is plainly right, concede in the first round; the bound prevents endless debate, it does not require consuming every round.

Do not use `uaf workflow retry` to manufacture extra divergence-debate rounds. That template sets the generic workflow retry bound to one because its three debate rounds are already explicit. Rerun a still-active command directly, or use `uaf runtime retry` for provider/run recovery. `workflow retry --force` is an administrative round-limit bypass, not debate authority: it cannot jump forward, activate an unsatisfied conditional phase, or revive a skipped conditional phase. Delegated debate turns request `--subagents disabled`; inspect the recorded enforcement because `hard` is mechanical while `provider_controlled` and `prompt_only` are not absolute ceilings. Nested workers are not additional independent debaters.

`uaf workflow` tracks reusable orchestration sequences such as investigation councils and implement-review-fix loops. Every new workflow must own an open task; `--mission` infers the mission root task, while standalone use requires `--task`. It is a resumability surface, not evidence. Workflow phase status cannot close work without ledger records and verifier or human-gate support. `uaf check` fails on corrupt workflow records; inspect with `uaf workflow repair --workflow <id> --dry-run` and quarantine only with explicit `--force`.

For long-running delegated workers, use `uaf runtime observe --run <run-id>` before assuming the worker is stuck. `runtime observe` and `runtime tell` emit observational advice plus copy-pasteable `next_commands`; these commands are operational guidance, not verifier evidence. Use `uaf runtime tail --run <run-id> --follow` when live high-signal provider or runtime events are needed. Use `uaf runtime wait --all --guide` to get safe parallel actions while detached work is in flight. A launch timeout of `--timeout-ms 0` means no hard timeout; stale detection must come from observation, not an automatic kill.

After launching detached worker, review, message, or long verifier work, use `uaf observer snapshot --mission <mission-id>` for a point-in-time cockpit view and bounded `uaf observer watch --mission <mission-id> --interval-ms <ms> --iterations <n>` when the run will take long enough that a rolling summary reduces cognitive load. Observer artifacts are written under `.uaf/observers/*.md` and are situational awareness only: they are not verifier evidence, completion support, permission to mutate worker-owned scope, or authority to retry, repair, cancel, approve, close, verify, or message workers. The observer may suggest next commands; the orchestrator decides and executes them.

Use `uaf runtime message` or `uaf contact message` for agent communication. Use `uaf runtime message --detach` when the follow-up can run in the background and must be rejoined like any other detached runtime run. `uaf runtime tell` is observational advice only and must not be used as a message transport. A message response must produce a response artifact; tail/observe/observer output is not proof of delivery. If a message response should enter reviewer closeout, use `uaf review close --response <response-artifact-event-id>` or `uaf review close --run <runtime-run-id>` to bridge it into an idempotent worker report and append the reviewer verdict plus orchestrator decision. Runtime and contact messages must not use `--session` or saved contact metadata to bypass unresolved questions or missing authorization on delegated worker sessions unless the orchestrator explicitly passes `--force`.

Productive wait is allowed only when the in-flight worker owns a disjoint scope. A detached runtime run is an owed follow-up until it is terminal, reported, reviewed, accepted, and closed. The orchestrator remains the single writer to shared UAF state, must not touch worker-owned scope while waiting, and must not treat elapsed time, `runtime wait`, `runtime observe`, `runtime tell`, or `runtime tail` output as verifier evidence. If `runtime wait` reports a non-completed terminal state, inspect the run and repair, retry, reject, or abort rather than proceeding.

`uaf adversarial-review` is an orchestration shortcut, not a verifier. It creates or reuses a red-team mission, launches the requested brand read-only by default, appends a worker report, and prints the next review closeout command. Use `--detach` when the opposing-brand review may run long so observation and productive-wait rules can govern it. Review intent is persisted as the assignment `lane`; recognized review-role aliases remain a compatibility guard for direct assignments. Every live entry point, including `delegate`, `contact message`, runtime launch, retry, and message, checks the effective side-turn policy before writing launch-related ledger state. A deliberate non-scan launch requires `--accept-profile-risk`; the resulting worker report preserves `launch_profile`, `profile_safety`, and `profile_risk_accepted` so the acknowledgement survives runtime-cache pruning. Review, red-team, and adversarial-review assignments must preserve their selected `--profile` or `--adapter`; `fixture` is valid only when explicitly requested, never as a silent fallback for a real review. `uaf runtime prompt`, `uaf runtime init`, and `uaf runtime launch` inherit assignment runtime intent so the inspected worker packet and the launched worker use the same profile policy. If a runtime command supplies `--profile`, `--provider-profile`, or `--adapter`, that explicit runtime intent replaces assignment intent without mixing profile and adapter metadata; named-profile safety remains immutable, and Copilot cannot launch profileless. Assignment-controlled prompt values are serialized as JSON strings or arrays so embedded newlines remain data instead of forging policy or report-contract sections. The orchestrator must still inspect the report, repair blockers, run objective verifiers, and close with evidence. Use `uaf review close` to append the reviewer verdict and orchestrator decision together when the report has been inspected. Use `--verdict rejected --abort-task` only when rejecting the report should abort the parent task; the command appends the rejection records and an explicit `abort` event atomically. A later orchestrator rejection recognizes that explicit abort instead of attempting a second lifecycle transition. For timed-out or unusable partial reports, prefer `changes_requested`. Direct `uaf review-worker --verdict rejected` and `uaf accept --decision rejected` use the same explicit-abort rule. A blocked verdict leaves the orchestrator decision pending unless `--decision` is supplied.

For opt-in multi-agent research campaigns, use the UAF package's
`docs/AUDIT_WORKFLOW.md` and its `docs/audit-workflow/` briefs. Keep research
claims in the independent findings store and execution authority in UAF.
Challenge and validate before promoting a claim to an authorized repair;
database labels and model agreement never replace verifier evidence.
This playbook does not add campaign stages to ordinary small changes.

Nested-agent authority is part of the assignment contract, not an informal
prompt preference. Use `disabled`, `bounded`, or `allowed`; bounded mode
requires a positive concurrency, total, or depth cap. Review work defaults to
disabled. UAF records whether the full requested policy is `hard`,
`provider_controlled`, `prompt_only`, or `not_applicable`. Never describe a
prompt-only limit as mechanical enforcement. Codex disabled mode is
`provider_controlled`: UAF requests its multi-agent feature gates off without
claiming an absolute provider-side ceiling.

`uaf models catalog` queries account-visible provider catalogs for explicit model selection and exposes provider-reported descriptions, policy, capabilities, profile safety, and catalog completeness when available; catalog visibility does not prove live inference health. Discovery is live provider access and must pass the same audited-profile gate as review work or carry explicit `--accept-profile-risk`; this is an access gate, not proof that discovery executes inside an OS sandbox, and Copilot discovery remains named-profile-only. Select from provider facts rather than guessing from model names. Any learned strengths, weaknesses, cost, or latency assessments must be dated and tied to human feedback or evaluation evidence so stale folklore cannot masquerade as provider fact. Explain a choice from current facts and evidence; do not encode an anecdotal model leaderboard. `--effort` binds a provider-native reasoning level to an explicit model. UAF validates it against the live model catalog before launch; known incompatibility fails, while missing capability metadata requires explicit `--accept-unverified-effort` and remains unconfirmed. Antigravity uses effort-bearing model IDs rather than this separate control. Provider-profile effort defaults become sealed assignment intent. Cached preflight is reusable only for the exact provider/profile/model/effort, provider paths, policy, and mount set that produced it. `uaf review campaign` creates bounded reviewer slots for either one shared prompt or per-reviewer prompts from a JSON spec. Compact campaigns can share a role, output contract, and effort; JSON specs can vary them per reviewer and fallback. Without an explicit campaign default, fallback effort inherits only within the same provider; cross-provider fallbacks must declare their own value. It owns a dedicated red-team mission whose immutable slot plan binds every provider, model, effort, role, profile or adapter, profile-definition hash, launch timeout, output limit, risk acknowledgement, prompt hash, stop and abort conditions, verifier limits, report contract, assignment-contract hash, and fallback attempt. Runtime launch cannot override those sealed values, provider paths, mounts, safety policy, or side-turn mode. Reviewer independence is instruction-enforced rather than filesystem-isolated, so agreement may be correlated and is never authority. Explicit fallback chains retain failed attempts and never silently substitute a model. A router fallback may proceed only to the exact next declared attempt after atomically recorded blocked/split-follow-up routing; `uaf review iterate` resumes that plan after interruption. Campaign-scoped status and wait avoid repo-global latest selection, remain nonzero while fallback action is owed or a slot is exhausted, and fail on unlaunched declared slots. Machine-generated fallback-routing records must remain structurally and visibly labeled `campaign_router`; they are not inspected reviews. Preserve every report and disagreement, inspect and decide on each final report, and never use model agreement or majority voting as verifier authority. Only `uaf review campaign close` may close a campaign mission; generic task closure is rejected and ledger validation requires campaign-closeout markers on its risk and completion records. Use campaigns only when the extra cost and latency are justified.

Campaign reviewers default to `subagents: disabled`, and the immutable attempt
plan seals subagent policy, limits, enforcement, and external mounts alongside
the model and provider profile. Nested reviewer workers may add analysis but do
not become additional independent votes.

Use `uaf closeout` when open queues make the next closeout step unclear. It is read-only guidance that prints exact commands from readiness state; it is not evidence and does not mutate by default. Use `uaf close --claim-set --related <event-id>` only for accepted investigation output that is useful to close but remains a set of unverified claims. Use `uaf review supersede` when a later accepted worker report resolves or replaces an earlier pending report. Use `uaf review close-mission` only after an accepted review report exists and the mission has no open report, gate, inbox, question, workflow, or runtime queues; it closes review-only missions with accepted-risk records because review evidence is not objective verifier evidence.

`uaf lifecycle audit` explains why tasks and missions are still open and which stale records can be safely closed. Use `uaf lifecycle close-stale --dry-run` before batch cleanup and copy the printed `plan_hash` into `uaf lifecycle close-stale --accept-risk --plan-hash <dry-run-plan-hash>` when the reviewed plan is still valid. Actual stale cleanup that lacks objective evidence appends explicit risk acceptance and completion records instead of pretending the historical work was verified. Manual lifecycle closeout is idempotent: repeated `close-task` or `close-mission` commands must not append duplicate completions once the task is already closed. Manual lifecycle closeout must not close active or pending-verification work unless `--allow-fresh` is explicit. Every `task_id` has one active task-or-mission root declaration. A mission cannot close over nonterminal children or accept a new child task or nested mission root after the mission becomes terminal. Review verdicts and orchestrator decisions route queues; an explicit task-state event, including the `abort` event emitted by a confirmed rejected review, changes lifecycle.

Use `uaf migrate normalize-lists --dry-run` first when pre-0.2 ledger or memory records contain exact duplicate values in arrays that current schemas require to be unique. This is the only rewrite migration: it preserves IDs, first occurrence order, and untouched JSONL lines, validates resulting records, and stores recoverable originals plus a receipt under `.uaf/quarantine/migrations/normalize-lists/`. It does not repair unrelated failures. Use `uaf migrate ledger-roots` when old ledgers lack explicit task roots. Use `uaf migrate review-rejections` to append explicit aborts for pre-boundary rejected review routes whose older semantics implicitly terminated their tasks. Use `uaf migrate strict-waivers` when stricter validation exposes historical verifier-id, task-link, terminal-mission, claim-binding, completion-lifecycle, projection-authority, rejected-review, or planless review-campaign records accepted by older UAF versions. Strict migration freezes waived planless campaigns at an append-order activity boundary; it does not authorize later assignments or completion. An empty ledger receives a synthetic, immediately aborted migration anchor so every one-shot boundary is actually established. Other migration modes append explicit bounded compatibility records; they do not turn weak historical records into verifier evidence. Append order is a cooperative local-writer boundary, not cryptographic protection against deliberate ledger rewriting.
