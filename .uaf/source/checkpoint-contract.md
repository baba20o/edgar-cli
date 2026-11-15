# Checkpoint Contract

Checkpoint stores the live human-facing queue of owed-by-us follow-ups.

Files:

```text
checkpoint.md
projects/<project-id>/checkpoint.md
```

Use the root checkpoint for repo-wide or cross-project follow-ups. Use a project checkpoint for follow-ups local to one project folder.

Rules:

- Read the root `checkpoint.md` at startup when it exists.
- Read the relevant project checkpoint before working inside a project folder.
- Use checkpoint only for staged actions that we or the human must circle back on and that could silently slip if the session ended now.
- Link checkpoint rows to UAF task IDs, event IDs, gates, plans, or files when possible.
- Keep checkpoint mutable and short; it is a live queue, not an audit log.
- Prefer deleting or marking rows done after the follow-up is actually resolved or moved into a better tracking surface.
- Record material state transitions in the ledger. Checkpoint alone is not completion, verification, approval, or durable memory.
- Do not use checkpoint as a second ledger. Do not mirror every task, event, or open ticket.
- Do not store secrets, credentials, private data, raw logs, or large transcripts in checkpoint.

Good checkpoint candidates:

- work waiting on a human decision before the agent can continue
- external access or approval needed before a known next action
- a staged follow-up that should survive context loss
- a deferred human review that needs to be prompted again
- a cross-project loose end that is easy to lose in ledger detail
- a ready-to-use command, close note, or handoff snippet blocked by a known trigger
- a process drift note that prevents re-litigating a known recent change

Do not use checkpoint for pure history, proof, or evidence. Use the ledger and verifier records for that.

Structured checkpoint records such as `checkpoint.jsonl` are not part of v0. If needed later, they should be generated from or linked to the ledger rather than becoming another canonical state store.
