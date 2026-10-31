# NOW — active scope and live-state pointers

Owner decision, 30 October 2026: establish [the new workflow](workflow/OPERATING_MODEL.md).
Claude Code is the usual manager with native subagents; Codex can take assigned
work. The live view and external observer support that manager, not replace it.

## Authorised work

- Build/test the private ledger, live view, question inbox, native Claude
  lifecycle observer and independent watchdog.
- Reconcile shared Markdown; prepare local Markdown/settings migration with
  backups. Do not claim files on the owner's PC changed without actual access.
- Map V4.5.1/V5/V6/V7 scope and GitHub state; retain unknown verification and
  missing device evidence. Exercise queue, recovery and browser user scenarios.
- Owner additions, 31 October: audit simplicity; reuse existing agents interfaces
  with explicit provider/model choice; add local action drill-down and manual
  retro handoff. Prepare (do not activate) Orchestrator adoption task #715.

## Current truth

The baseline contains 94 scope requirements including optional items; 18 lack
a granular issue mapping. This is not a completion report or blanket permission
to start every version. See [VERSION_MAPPING](workflow/VERSION_MAPPING_2026-10-30.md).

Live state: `python core/tools/workflow_console.py status` and `open`. Both read
the same private ledger; no handwritten completion counters in NOW/HANDOFF are
authoritative. [HANDOFF](HANDOFF.md) is historical release context. The previous
brief is retained in Git history; no device verification is created by migration.

## Presence and safe continuation

Present: ask compact questions interactively. Away: retain unanswered questions
and checkpoint only dependent work. Continue other authorised work. If all work
really needs the owner, report it; do not invent work, assume approval or mark
waiting tasks done.

## Hard stops retained

No paid usage beyond approved caps; unapproved hardware/device/account or
external-send actions; security-alert dismissal without evidence/authority;
force pushes, bypassed checks or stable releases without owner approval.
Permissions, privacy, budget and release gates remain unchanged.

## First local run

Owner decision, 31 October: native PC acceptance runs after the code merges.
This does not waive the existing pre-merge review/CI/protected-queue requirements.

Follow [SETUP](workflow/SETUP.md). Verify installed Claude compatibility, local
instruction imports, one native worker, an owner-wait/answer/resume scenario
and OS observer restart before calling the owner's environment operational.
