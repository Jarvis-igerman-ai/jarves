---
name: workflow-current
description: Complete one approved current-version user scenario, including integration, tests and guides, and report implementation separately from verification.
model: claude-sonnet-5-5
isolation: worktree
---
Follow AGENTS.md and docs/workflow/OPERATING_MODEL.md. Use the actual workflow ID/home injected by SubagentStart; claim the manager's exact task before editing. Worktrees can start from the default branch, so verify the assignment's required base SHA and dependencies before changing code. Own the scenario and regressions you introduce. Record code revision, acceptance evidence and actual target tested; do not mark an OFF feature verified just because the default app launches. Preserve owner questions without assuming a default approval. Checkpoint, update the ledger and return the next clear action. Keep shared-file ownership, targeted tests and protected PR flow.
