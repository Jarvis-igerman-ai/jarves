# HANDOFF: latest state plus pointers

> Historical release context. Current work uses the private workflow ledger and
> [operating model](workflow/OPERATING_MODEL.md); do not resume dated worker or
> question policies from these notes. No new device acceptance is implied.

Short on purpose. The 24-27 Oct session log (many entries) is archived: [HANDOFF_2026-10-and-earlier](archive/HANDOFF_2026-10-and-earlier.md). New work records state in each agent's STATE.md; a handoff note is written once per released version.

## Latest entry: after the JARVES 4.5 release, 27 Oct 2026
Full note: [handoff/2026-10-27-session-45-release](handoff/2026-10-27-session-45-release.md).
- JARVES 4.5 shipped as tag `v4.0.1` (installer 4.0.1.1214, tag commit `ca8eb36b`); the owner approved in chat. Known issues accepted for the notes (label `known-issue-4.5`): #179 #261 #441 #509 #510 #543 #544 #605.
- Release tooling: the "five nightly runs" rule is gone; the gate needs main green, dry runs green, zero open P0-P2 (except `known-issue-4.5`) and the owner's signed sheet.
- Open at that time: wake PR #614 (held), browser PR A (#615), phone escaping (#616); docs-only CI fast path approved but not built.
- Owner-only next steps: #179 live re-measure, wake apply after `jarvis wake test`, GitHub settings #543 #544 #605, trusted certificate for a full PWA install.

## Pointers
| Need | Where |
|---|---|
| Current brief | [NOW](NOW.md) |
| Process and agents | [LEAN_PROCESS_V2](plans/LEAN_PROCESS_V2_2026-10-27.md), [retro](plans/PROCESS_RETRO_AND_AGENT_PLAN_2026-10-27.md) |
| Release steps | [RELEASING](RELEASING.md), [release checklist 5.0.0](reviews/RELEASE_CHECKLIST_5.0.0.md) |
| Per-task notes (history) | [handoff/](handoff/README.md), [archived notes](archive/handoff/) |
| Bug tables | `docs/reviews/*BUG_TABLE*.md` |
| Where every doc is | [DOC_MAP](DOC_MAP.md) |
