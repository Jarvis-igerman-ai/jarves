# Suggestion report r2 (JAR-116)

Five more improvement ideas with evidence and cost/benefit. Status: current (main b5dc54f, 16 Nov 2026). Proposals only: nothing here is implemented or authorised. None repeats the eight ideas of [r1 (JAR-67)](2026-11-16-suggestions.md), which covered CI cancellations and wall time, atomic-write and log-once helpers, changelog self-checks, fail-without-change tests, the flake aggregator and unused imports.

Sources, all read on 16 Nov 2026: `git tag`, `git log origin/main -- CHANGELOG.md`, `ls changelog.d`, `git branch -r --merged/--no-merged origin/main` after a full fetch, the GitHub search API (open PRs, open issues, labels) and the pulls API (`mergeable_state`, labels) for nine open PRs. Tier: N = ordinary change, R = Tier R category (release, workflows, Gate) needing the Opus sign-off. Costs are rough agent-hours, not measured.

## 1. Ship a beta from main so merged work reaches an artifact

**Evidence:** the newest tag is `v4.0.1` (target commit dated 27 Oct); the newest GitHub release is `win-v4.0.0.153` (22 Oct). `CHANGELOG.md` was last folded on 2 Nov (#836), and `changelog.d/` now holds 256 unreleased fragments (71 dated 2 Nov alone). The operating model says Done needs an identified beta or stable artifact, so none of that work can be Done yet.
**Cost:** about 2 h of agent time to run `core/tools/release_gate.py` and `changelog_build.py --write` on a beta branch, plus the owner's release approval and installed-product check (owner time, not estimated here).
**Benefit:** turns three weeks of merged fixes into something the owner can install and verify; the fragment pile stops growing between releases.
**Tier:** R (release path; owner gate, never automatic).

## 2. Turn BLOCKED draft PRs into owner questions and close them

**Evidence:** 7 PRs are open drafts and 6 of them are titled `BLOCKED: ...` (#924 since 3 Nov; #1120, #1121, #1122, #1123 since 14 Nov; #1142, #1143 since 16 Nov). They hold a question, not a change, so they inflate the open-PR count (25) and nobody is notified through the question inbox.
**Cost:** about 1 h: the factory copies the BLOCKED question into the owner question inbox (or one ticket comment) and closes the draft with a link; a short rule in the ticket template.
**Benefit:** the open-PR list shows only real changes; each question reaches the owner once, through the channel the operating model names.
**Tier:** N (factory ticket text; no repo code or workflow).

## 3. Delete merged branches and list orphan ones

**Evidence:** the remote has 172 branches: 13 are already merged into main, 159 are not merged, while only 25 PRs are open. So well over 100 unmerged branches have no open PR (exact orphan count UNVERIFIED: one PR per branch assumed). 27 branches start with `f2/`.
**Cost:** about 1 h for a read-only script that lists merged branches and unmerged branches without an open PR; deleting them, or turning on "delete head branch after merge", is an owner decision.
**Benefit:** `git fetch` and branch lists get smaller; abandoned work is visible instead of hidden among live branches.
**Tier:** N (read-only list; deletions only by the owner).

## 4. Give every open issue a priority and triage the stale ones

**Evidence:** 72 issues are open; 24 have no P0 to P3 label although AGENTS.md rule 8 asks for one per defect (P1 3, P2 31, P3 14, P0 0). 30 open issues were not updated since before 2 Nov, and 1 open issue carries `showstopper`.
**Cost:** about 2 h (Haiku) to label the 24 issues and comment on the 30 stale ones with keep / close-with-evidence / needs-owner; no code.
**Benefit:** the backlog can be ordered by priority, and stale issues get a decision instead of drifting.
**Tier:** N.

## 5. Report green PRs that stay unmerged

**Evidence:** of nine open PRs created 11 to 14 Nov, eight report `mergeable_state: clean` (checks green, no conflict). Three carry `no-automerge` (#1080, #1101, #1108: Tier R waiting for the Opus sign-off); five do not (#1073, #1079, #1086, #1091, #1094). Why those five are not queued (Tier R by judgment, missing review, or the steward not run) is UNVERIFIED.
**Cost:** about 1 h for a read-only list in the steward's output: each green open PR older than 24 h with its age, tier and the one thing it waits for.
**Benefit:** finished work stops sitting for days without a named reason; Tier R sign-offs become a visible queue for the owner.
**Tier:** N (read-only report; merging rules unchanged).

## Suggested order

2 and 4 are text and labelling only; 5 and 3 are small read-only tools; 1 needs the owner's release approval and is the only one that moves work to Done. Each needs its own ticket; none is started.
