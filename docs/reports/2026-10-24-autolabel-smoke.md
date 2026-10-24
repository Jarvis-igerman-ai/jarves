# Auto-label CI incident and live smoke test — 24 October 2026

## Incident

GitHub pull requests #112, #113 and #115 passed the single required `jarves-checks` aggregate gate, while the optional `jarves-autolabel` job failed with `'automerge-auto' not found`. The previous workflow preferred a PAT without confirmed Issues write permission, hid label creation errors, and tried to assign a label which had not been created. Native GitHub auto-merge requires only explicitly configured required checks; the separate red labeling workflow therefore did not block the merge.

## Repair under test

Fix PR #116 switches label actions to the scoped `GITHUB_TOKEN` with explicit Issues write permission and visible, idempotent label initialization; retains the risk classifier; adds an offline regression test and names the `Auto-label policy` check distinctly.

## Purpose of this docs-only PR

This document is intentionally a **docs-only smoke candidate** so the repaired workflow on `main` must actually classify a harmless PR, create/locate both repository labels and apply `automerge` and `automerge-auto` without an error. Verify the *real* `jarves-autolabel` job, the required CI gate, merge outcome, and post-merge `main` checks. An empty/successful CI badge alone is not proof that the labels were applied.

The owner must separately require `Auto-label policy` alongside `CI result (single required check)` in GitHub branch-protection settings to make that additional check a native merge blocker; the GitHub connector has no repo administration permission.

*Evidence and final verdict belong in the corresponding GitHub Actions runs and PR conversation; this document makes no claim before those checks complete.*
