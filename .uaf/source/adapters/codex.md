# Codex Adapter

Codex-specific guidance:

- Use fast local search such as `rg` when available.
- If `rg` is unavailable, use the next best local search tool.
- Preserve unrelated user changes.
- Before committing non-trivial UAF work, run `uaf adversarial-review --from codex --brand opposite --prompt "<review prompt>" --scope "<scope>"`, address blockers, and record the review decision.
- When using detached workers, keep working only on disjoint scope and rejoin with `uaf runtime wait` before closeout.
- Distinguish verified work from unverified work in final responses.
