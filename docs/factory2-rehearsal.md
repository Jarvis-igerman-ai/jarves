# Factory 2 rehearsal

Ticket: JAR-206 (rehearsal, **not counted** in Factory 2 throughput or quality metrics).

## Purpose

A harmless, docs-only task that exercises the Factory 2 pipeline end to end:
ticket pickup, isolated worktree, branch `factory/jar-206`, targeted test, commit,
push, PR, CI and merge steward. It changes no product behaviour.

## What it proves

- A worker can take a ticket, work in its own worktree and open one PR.
- The docs index rule holds: every new file under `docs/` gets a row in
  [DOC_MAP](DOC_MAP.md) (`core/tests/test_docs_links.py`).
- CI and the merge path run on a Tier L (docs only) change.

## What it does not prove

- Nothing about the installed product, devices or real user scenarios (UNVERIFIED).
- Not a measure of Factory 2 speed or quality; exclude it from counts.

## Removal

Safe to delete together with its DOC_MAP row once the rehearsal is no longer needed.
