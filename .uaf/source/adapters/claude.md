# Claude Adapter

Claude-specific guidance:

- Prefer concise startup orientation over broad context loading.
- Use UAF memory and ledger slices only when they are relevant to the task.
- Preserve unrelated user changes.
- Before committing non-trivial UAF work, run `uaf adversarial-review --from claude --brand opposite --prompt "<review prompt>" --scope "<scope>"`, address blockers, and record the review decision.
- When using detached workers, keep working only on disjoint scope and rejoin with `uaf runtime wait` before closeout.
- Distinguish verified work from unverified work in final responses.
