# Claude Code — JARVES

@AGENTS.md
@docs/workflow/OPERATING_MODEL.md

Read `docs/NOW.md` for active scope. Claude Code normally manages native
subagents; Codex takes explicitly assigned work under the same contract. Do not
enable experimental agent teams or create another LLM scheduler for this view.
Other documents are indexed in `docs/DOC_MAP.md`; open only relevant sources.

Use the private workflow ledger and linked GitHub/evidence for current state.
Imported version scope is not execution permission. Stop, issue closure, merged
PR and green CI do not prove installed-product acceptance.

First local setup: `python core/tools/workflow_console.py bootstrap`, inspect
the diff, then use `--apply`. This reconciles project-local Markdown and merges
observer hooks with backups, preserving unrelated settings. Inspect Claude
`/memory` for unseen machine-global instructions; the repo cannot claim those
were changed from a remote environment.

The old five-nightly rule, per-task HANDOFF updates, once-a-day question cap,
fixed worker quotas and handwritten version-state copies are retired. Existing
permissions, independent risky-change review and protected release gates remain.
