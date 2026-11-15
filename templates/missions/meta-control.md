# Meta-Control Mission Template

Summary: {{summary}}

## Purpose

Choose the next safest loop, mission, or escalation path.

## Target

{{target}}

## Current State

{{scope}}

## Decision Inputs

- current ledger state
- checkpoint follow-ups
- verifier coverage
- human gates
- mission risk and blast radius

## Verifier

{{verifier}}

## Stop

{{stop}}

## Abort

{{abort}}

## UAF Handling

- Do not make irreversible architecture, product, trust, or migration choices outside explicit delegated authority; request a human gate when authority is missing.
- Prefer Atlas before transformation when the project is not mapped.
- Prefer verifier foundation before high-risk execution.
