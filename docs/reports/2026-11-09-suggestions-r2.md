Report: improvement suggestions round 2 (JAR-116), status proposal (9 Nov 2026). Docs only; nothing implemented, nothing decided.

# Suggestions r2: 5 ideas with evidence, cost, benefit

Does not repeat the 7 ideas of PR #993 (JAR-67). Evidence read on 9 Nov 2026 from `origin/main` at 89832588 and `gh`.
Tier: R = never auto-merged, N = ordinary. Cost: S = under a day of agent work, M = more. Benefits are estimates unless a number is given.

| # | Idea | Tier | Cost | Benefit |
|---|------|------|------|---------|
| 1 | Treat `changelog.d/` as docs in CI classification | R | S | Docs PRs stop running 9 test jobs |
| 2 | Ratchet on silent `except Exception: pass` | N | S | Ends repeat manual audit rounds |
| 3 | Stop rule for audit tracks with no yield | process | S | Saves agent runs and CI |
| 4 | Make quarantine and showstopper issues agree | N | S | Honest flake debt count |
| 5 | One short owner phone test sheet | process | S | Unblocks issues open since October |

## 1. A changelog fragment turns a docs PR into a full test run (Tier R)
- Evidence: `core/tools/ci_plan.py:34` `DOCS` does not match `changelog.d/`. Run locally, `classify()` gives `code=False, full=False` for a `docs/reports/` file alone and `code=True, full=True` once a `changelog.d/` file is added. The factory rule requires a fragment on every PR.
- Observed: #993, #991, #1012 and #1013 touch only `docs/` and `changelog.d/`, and all four ran the Windows shards. #993 had 15 successful checks, including 7 Core shards and 2 real-browser jobs, over 13.5 min (10:13Z to 10:27Z).
- Cost: S, two regexes (`ci_plan.py:34`, `ci_reuse.py:155`, whose comment says it uses the same patterns) and one test. Tier R by judgment because it decides what CI skips.
- Risk: `test_changelog_parts.py` validates fragments but is not in the `prepush.py` checks; the Linux shards are what run it today. Add it to the fast stage in the same PR or a bad fragment reaches main.
- Benefit: every docs-only PR drops to the fast checks. Different from #993 idea 5, which is about code PRs.

## 2. Ratchet on silent broad excepts (Tier N)
- Evidence: 91 `except Exception: pass` blocks in 54 files of `core/src` (multiline search; includes `auto_update.py` 6, `selfcheck.py` 7, `approval_popup.py` 5). Three separate PRs fix these by hand: #990, #1004, #1010. No `ruff` or similar lint is configured in `core/pyproject.toml`, `core/tools` or `.github`.
- Cost: S, one hermetic AST test with a per-file baseline that may only shrink. No product change.
- Benefit: new silent failures are blocked at PR time, and audits only need to work down the baseline.
- Risk: many of the 91 are legitimate best-effort cleanup, so the baseline must not be read as 91 bugs. An explicit `# best-effort: why` marker keeps those allowed.

## 3. Stop rule for audit tracks (process, no code)
- Evidence: SPEED r3 (#1009) cut one test group from 84.7 s to 40.3 s. SPEED r4 (#1012) and r5 (#1013) both report no safe test-only speedup. Each round costs a model run plus a full CI run (idea 1).
- Cost: S, one line in the planning cycle: after two rounds in a row with no shipped change, a track pauses until a new measurement or an owner request reopens it.
- Benefit: agent budget moves to issues that ship. Saving not measured; at least two runs per stalled track.
- Risk: a real finding is delayed. Acceptable, since reopening needs only a measurement.

## 4. Quarantine and showstopper issues disagree (Tier N)
- Evidence: `core/tests/quarantine.toml` line 4 says every entry has an open `showstopper` issue. Two entries point at #778, which is open with labels `P2, agent-doable, testing, 4.5.1` and no `showstopper`. In the other direction, 13 issues carry `showstopper`, while PR titles #965 and #991 describe #946, #943, #788 and #770 as already fixed or under verification, yet those stay open.
- Cost: S, one hermetic test or `flake_scan.py` report comparing the file with an issue list passed in as data (no network in tests), plus closing issues only with evidence from #991 once it merges.
- Benefit: the count of real flakes becomes trustworthy; today it can be neither 6 nor 13 with confidence.
- Risk: none for product. Closing issues still needs run links per the issue rule.

## 5. One short owner phone test sheet (process, owner time)
- Evidence: #509, #510 (26 Oct) and #344 (25 Oct) carry `needs-phone-retest`; #193 and #194 (24 Oct) are owner-gated phone items. `docs/reviews/ANDROID_COMPANION_ACCEPTANCE.md` has 15 rows, 1 to 11 NOT-RUN and 12 to 15 NOT-BUILT, per the JAR-85 report. The retest issues are not in that sheet, so the owner has several separate lists.
- Cost: S for the agent (one page in Hebrew listing runnable checks in order). Owner time about 20 to 30 min on a real phone (estimate).
- Benefit: can close or re-confirm the 3 retest issues and replace NOT-RUN rows with real results, which no mock can do. PR #1001 (phone polish) should merge first so the owner tests the newer page.
- Risk: results stay UNVERIFIED until the owner runs it; nothing here claims a device pass.

## Suggested order
1 (ask the owner for Tier R review), 4, 2, then 3 at the next planning cycle, and 5 when the owner has a phone at hand.

## Not verified
No idea was implemented. CI time is from a single PR. The 91 count is a text pattern, not a review of each block. Issue age and release state beyond the listed items were not examined.
