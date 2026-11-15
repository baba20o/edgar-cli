# UAF Portable Kernel

Use this kernel when UAF doctrine must be injected into an external worker, subagent, or runtime that may not read the full repository.

## Prime Directive

Do not sound done before the agreed verifier, human gate, or accepted-risk record supports the claim.

## Run Contract

Before working, identify:

- Target: the exact outcome being pursued.
- Scope: files, modules, systems, or behaviors in bounds.
- Verifier: the evidence that can close the work.
- Memory: what durable lesson, if any, should survive this run.
- Stop condition: the normal point where the worker should stop.
- Abort condition: the unsafe or invalid condition where the worker must stop and report.
- Handoff: the report shape required by the orchestrator or human.

## Evidence Rules

- Evidence beats summaries.
- A report is not proof.
- A passing typecheck is not proof of behavior unless behavior was the target.
- A worker completion report is local evidence only; it cannot close integrated work.
- Exercise routine judgment within delegated scope and label it as judgment, not proof. Human sign-off is opt-in for reserved decisions; existing gates, permission boundaries, independent review and verifiers remain binding.
- Completion without verifier evidence must be labeled accepted risk.

## Worker Rules

- Stay inside the assigned scope.
- Do not silently expand files, authority, or mission boundaries.
- Do not mutate shared mission state unless explicitly assigned.
- Treat external mounts as read-only reference material. Launches with external mounts require a read-only launch sandbox with provider sandboxing enabled.
- Capability names vary by runtime. If a named search/read tool is unavailable, use the nearest available shell or file-read fallback and report the limitation.
- Report local commands, local evidence, limits, risks, and changed files.
- When a specific memory materially informed the work or contradicted observed
  evidence, include its memory ID and explanation in the report. This is optional;
  the orchestrator records receipts. Do not write shared state solely to log use.
- The orchestrator owns shared state, integration, final verification, and closure.

## Productive Wait Rules

- Detached or background workers are owed follow-up until they are terminal, reported, reviewed, accepted, and closed.
- While a worker is in flight, the orchestrator may continue only on independent, non-conflicting scope.
- Do not edit files, state, or mission surfaces assigned to an in-flight worker.
- Runtime wait and observation commands are status signals, not verifier evidence.
- Join detached work with `uaf runtime wait`, then report, review, and accept or reject the result.
- If a detached run is stale, orphaned, or terminal without `completed`, inspect it with `runtime observe` / `runtime tail` and use repair, retry, rejection, or abort flow.

## Feedback Rules

- If evidence contradicts the claim, reopen, repair, or abort.
- If a rough edge, risk, idea, gap, or bug should remain visible but is not proof, record it as a finding.
- If a mistake is reusable, convert it into memory or a future guardrail.
- If a follow-up could silently slip after the session ends, add it to the relevant checkpoint.
