# Worker Assignment Template

## Role

{{role}}

## Scope

{{scope}}

## Files Allowed

List exact files, directories, or modules in bounds.

## Files Forbidden

List shared state, generated files, or sensitive files the worker must not edit.

## Verifier

{{verifier}}

## Stop

{{stop}}

## Abort

{{abort}}

## Output Contract

{{output_contract}}

Worker report must include:

- result
- files changed
- commands run
- verifier result
- verifier limits
- risks
- follow-ups
- whether orchestrator verification is still required

Worker report must not include:

- secrets or credentials
- bearer tokens or request headers
- database URLs with embedded credentials
- raw stdout/stderr logs
- environment dumps
- large pasted traces

Summarize command results as metadata and reference only safe artifacts when needed.

The worker cannot claim integrated completion. The orchestrator owns shared state, integration, and final verification.
