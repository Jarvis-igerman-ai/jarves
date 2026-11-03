# Tier R flow and merge backlog (proposal)

PROPOSED, NOT ACTIVE: owner approval UNVERIFIED (no ledger question/answer id is recorded).
Until the owner approves it and changes his own global instructions himself, the steward
never merges Tier R / `no-automerge` PRs and `tier_r_flow.py` is advisory only (`review`/`wait`/`owner`
advice; ignore `queue`). If approved it would narrow the rule only for PRs outside the excluded set below.

`core/tools/tier_r_flow.py plan` prints, per open PR, the next action
`review|fix|queue|wait|owner` with reasons (read-only; it never merges).
1. Green on the CURRENT head SHA and no verdict for that SHA: one Opus review
   task per PR + SHA (never per push). The reviewer must be Opus and declare a
   task/agent id different from the PR's declared `Author-Agent:` id(s).
2. Verdict format (machine-readable, first line): `SIGN-OFF <40-char head sha>` or
   `CHANGES REQUESTED <40-char head sha>`, then marker lines `Review-Head: <same sha>`,
   `Review-Task: <ledger task id>`, `Reviewer-Agent: <agent id>`, `Reviewer-Model: opus-5-5`. The manager also requires the review task to be in
   review/verified/done with passing SIGN-OFF evidence at the full head sha, and the reviewer to be a registered
   `claude-native` session different from the author's. Honest limit: all agents share one OS user and one GitHub
   account, so this is tamper-evidence, not authentication.
   Only comments whose GitHub association is OWNER, MEMBER or COLLABORATOR count;
   quoted or buried text, short SHAs and incomplete markers do not. The LATEST verdict
   for the current head wins, so a later CHANGES REQUESTED cancels an earlier SIGN-OFF.
   Honest limit: every agent posts through one GitHub account, so account != author
   proves nothing about independence. The check is only that the declared reviewer id
   differs from the declared author id, and `tier_r_flow.py` itself checks the ledger
   (fail closed to owner): the `Review-Task` is a `tier_r_review` task for this PR number
   and head (id ends with the 8-char sha), requested Opus (a requested, not a verified
   effective, model), belongs to the declared reviewer, who holds no claim on `pr:N` or
   the branch; and the declared `Author-Agent:` must be corroborated by a ledger claim
   on the PR or branch. A missing `Author-Agent:` or marker means owner.
   `OPUS REVIEW: SIGN-OFF` / `OPUS REVIEW: CHANGES REQUESTED` is also read as the first
   line; the sha then comes from `Review-Head:`. Hotfix/update and installer/packaging
   paths are never automatic (owner).
3. CHANGES REQUESTED: a Sonnet fixer pushes to the PR's own branch only
   (`check-touch pr:N` first, `--force-with-lease` only on that branch); the new
   head is reviewed again. At most 2 review rounds (two heads with CHANGES REQUESTED);
   after that every verdict, even a SIGN-OFF, means a ledger blocker, alert and owner.
4. SIGN-OFF on the current head, required checks green, main not red: the steward
   would (only if the owner approves) remove `no-automerge`, run `gh pr merge N --auto` and confirm the PR is in
   the merge queue. A push after sign-off invalidates it (back to step 1).
5. Never automatic (owner): any path containing release, approval or signoff
   (release_cmd, release_flow, release_run, release_signoff, release_gate, approval_keys,
   approval_popup ...), the Gate (drive_mode.py, skills/__init__.py), secrets,
   `.github/workflows`, permission/settings/CLAUDE.md/AGENTS.md files, this flow's own
   files, release/tag/stable wording in the title, empty or truncated file lists, and
   anything unknown or unsure (fail closed).

Merge backlog mode: `tier_r_flow.py focus` sets `backlog` when 8 or more
non-draft PRs are open, or 3 or more green PRs are not yet queued. Then the
status/verdict line says `merge backlog: N PRs` and the manager, within the
policy worker limit (never above it), starts in this order: P0/P1 bug fixes,
already running reviews and fixers, steward queue, Tier R Opus reviews, fixers
for red or conflicted PRs. No new feature or bug-implementation task starts until
the counts drop below both thresholds.
