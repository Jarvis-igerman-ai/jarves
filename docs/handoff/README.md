# Handoff notes: one file per task

Every finished task (or PR) adds ONE new file here instead of editing a shared file:

    docs/handoff/<YYYY-MM-DD>-<short-topic>.md        e.g. docs/handoff/2026-10-23-ci-merge-release.md

Why: until 23 Oct 2026 every PR added its note at the top of `docs/HANDOFF.md`, so any two PRs in flight conflicted in
that file and one had to merge `main` and re-run its whole CI (PR #90 conflicted three times that way, #89). Separate
files never conflict.

What goes in a note (short): what was asked, what changed (files, PR, issue), how it was verified (exact commands, CI
run), what is NOT verified, and the open questions for the owner ("Decisions needed"). No secrets, no personal data.

Newest first = sort by file name. `docs/HANDOFF.md` keeps the history up to 23 Oct 2026; the owner-wide decision
queue stays at its top. Notes here are covered by `docs/DOC_MAP.md` through this README (no per-note DOC_MAP row).

**Open questions for the owner** go in the same note under a `## Decisions needed` heading (status ANSWERED / USER DECISION
REQUIRED / DEFERRED / BLOCKED, as in AGENTS). Do not edit `docs/HANDOFF.md` in a task PR: it is frozen history, so
nothing in it can conflict. To list every open question: `git grep -n -A3 "Decisions needed" docs/handoff`.
