# Atlas Mission Template

Summary: {{summary}}

## Purpose

Map an unknown or broad project before execution. Atlas work is read-mostly unless the assignment explicitly allows scaffolding.

## Target

{{target}}

## Scope

{{scope}}

## Required Output

- architecture or system map
- risk list
- verifier inventory
- bounded next missions
- open questions requiring human decision

## Verifier

{{verifier}}

Atlas verification proves the map is useful enough for a bounded follow-up. It does not prove implementation behavior.

## Stop

{{stop}}

## Abort

{{abort}}

## UAF Handling

- Record claims in the ledger.
- Record durable architecture facts with `uaf promote` only after evidence exists.
- Put owed follow-ups in `checkpoint.md`.
