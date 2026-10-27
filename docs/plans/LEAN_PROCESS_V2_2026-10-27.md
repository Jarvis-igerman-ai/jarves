# Lean process v2 (second pass, 27 Oct 2026)

Plain words. Second pass after [PROCESS_RETRO_AND_AGENT_PLAN_2026-10-27](PROCESS_RETRO_AND_AGENT_PLAN_2026-10-27.md): cut what is still slow or ceremony.
Facts used: `jarves-checks.yml` (PR run, merge_group run, main push reuse via `ci_reuse.py`), ruleset (required: `CI result` + `Auto-label policy`;
queue builds 3, merges up to 4, wait 2 min), CI speed plan timings (`docs/plans/CI_SPEED_2026-10-26.md` in PR #516: 40 PR runs + 30 merge groups), retro numbers, live issue counts (P0 0, P1 16, P2 16, P3 34, 131 open).
Rule changes to CLAUDE.md files are PROPOSALS for the owner (agents never edit them).

## The idea in 5 lines
1. **Test once, at the right place.** PR run = the full test run. The merge group only re-checks what a merge can break (Windows tests), and skips everything when its tree is the tree the PR already proved. Linux, browser, perf and the installer chain move after the merge (main push) and nightly, and they still gate the beta.
2. **Review only what can hurt.** One Opus review, one round, only for the Tier R list. Everything else ships on green.
3. **One PR per agent.** 3 agents, each one open PR, no stacking, small fixes bundled per area.
4. **Issues = real defects.** Nits go to one ledger file. Open P0-P2 never grows.
5. **Scripts act, people do not poll.** A watchdog fixes queue trouble and writes one status board; the owner gets one batch of questions a day.

## Before -> after (minutes, numbers from 24-27 Oct)
| Step | Before | After | How |
|---|---|---|---|
| PR run (code) | 15 median, 24 p90 | 12 median, 15 p90 | browser retry re-runs only failed files (#516 R1); browser job only when web/dashboard/browser files change |
| Merge group | 16 median, 19 p90; ~21 jobs (more than the 20 free runners) | 1 (same tree as the green PR) or 9 (Windows shards only); ~7 jobs | do-now 1 and 2 |
| Wait between groups | 20-40 (groups queue for runners) | 0-10 (2-3 groups fit in 20 runners) | smaller groups |
| Open -> merged | 44 median (65 reviewed) | **<= 25** (<= 50 Tier R) | all of the above + fewer reviews |
| Independent review | 43% of PRs, ~19 Opus runs/day, ~32 fix-recheck loops | ~15% (Tier R only), <= 3/day, 1 round | review rule below |
| Manager polling + status lines | ~60 min/day + hourly lines | 0 (watchdog + one status board) | do-now 4 |
| Beta on the owner PC | 30-45 min by hand, hotfix after every batch | 1 command, ~10 min, once a day + any P0/P1-fix beta | do-now 5 |
| Stable release | 2-3 h agent (evidence pack, freeze, dry-run dispatch, sheet) + owner sheet | ~20 min agent + 15 min owner smoke (5 rows) + 1 chat approval | do-now 6 |
| Issues filed | 272 in 3.5 days (~65 from reviews) | real defects only; nits in the ledger | do-now 7 |
| Agent starts | ~40/day | <= 6/day (3 agents + at most 3 short fixers/reviewers) | 3 agents, STATE.md |

Minimum safe open -> merged: one PR run (~12) + queue wait (2) + Windows re-check (~9) = **~23 min**; ~15 min when main did not move.
Risk of moving Linux/browser/perf/installer after the merge: a break they alone catch shows up on the main push ~15 min later, before
any beta is published (the beta needs a green main). Expected under 1 per week at today's volume. Answer: the author reverts the PR at once (P0, 1 hour).
Why not drop the queue entirely: without it every merge lands untested against the PRs before it; the lean group costs 9 min and keeps main green.
Why keep Linux on the PR: it runs in parallel (Windows sets the wall time), so dropping it saves runners, not minutes; the Pi is a target.

## Review rule (one reviewer, one round)
- **Tier R** (independent Opus review before queueing): Gate and approvals, release sign-off/gate/tag tooling, hotfix and update write paths,
  installer/packaging, browser network egress, wake model apply, workflows/secrets/supply chain. These are exactly the classes where Opus found real
  defects (#291 #292 #465 #493 #523 #541 #560 #588 #602 #614 #615). Budget: one review, the author fixes in ONE push, no second review unless a
  finding was marked blocking. Nits go to the ledger, not to a second round.
- **Everything else** (features behind beta switches, bug fixes, UI, docs, tests, guides, Dependabot patch/minor): author self-check = `prepush.py`
  green + the 5-line PR checklist (what changed, test added, guide/parity/Hebrew if a feature, risk, issue link) + CI green -> `gh pr merge N --auto`.
  No separate scan agent, no reviewer.

## PR flow
- One feature = one branch = one PR, opened READY when `prepush.py` passes (no draft stage). Each agent has at most ONE open PR; the next waits for the merge.
- Small fixes in the same area go into one PR (up to ~300 lines). No stacking: dependent work starts after the merge.
- Never push only to "update" a PR (no merge-main, no close/reopen). Push again only to fix a red run or a Tier R finding, max twice; then split or stop.
- The watchdog (do-now 4) re-queues dropped PRs and toggles the label when `Auto-label policy` did not run. Agents do not babysit.

## Issues
- An issue only for a real defect (P0-P2) or an owner report. Findings from reviews and tests go to ONE ledger: `docs/reviews/FINDINGS_LEDGER.md` (date, area, finding, P3/nit, status).
- Labels: `P0`-`P3`, one area label, `showstopper` for flakes, plus the merge labels. Nothing else.
- Fix times: P0 1 hour, P1 1 day, P2 2 days. Open P0-P2 never grows from one beta to the next. Above 5: the release and issues agents fix only P0-P2 until <= 3.
- Weekly sweep script (agent 1): close an issue whose fix PR merged ("Fixes #N") with that link as evidence; move open P3 older than 14 days to the ledger and close them with a link (owner OK needed once, do-now 8).

## Agents and the manager
- 3 agents: (1) issues + security, (2) releases, (3) features. Prompts: [AGENT_1](AGENT_1_ISSUES_PROMPT_2026-10-27.md), [AGENT_2](AGENT_2_RELEASE_PROMPT_2026-10-27.md), [AGENT_3](AGENT_3_FEATURES_PROMPT_2026-10-27.md).
- Each keeps `C:\Users\97254\.claude\projects\C--work-jarvis\agents\agentN_STATE.md` (<= 30 lines: current item, PR, last result, next 3, lessons) and reads it
  instead of re-reading plans. No per-task handoff files in the repo; one handoff note per released version.
- The manager acts only on agent-finished notifications: restart the agent on its next item. No polling; the watchdog handles the queue.
- Owner present (last message < 10 min or "back"): at most one AskUserQuestion batch (<= 4) a day, plus the stable approval. Away: OWNER_OPENS.md with a safe default, keep going.
- Report per agent: 3 lines (DONE / NEXT / NEEDS). The owner sees one daily status from the board.

## Delete list (proposals; the owner applies them to the CLAUDE.md files)
1. "Keep 5-8 sub-agents busy", "no idle agents", "5-6 agents": replace with "3 agents, one PR each".
2. "At most 4 open PRs, one per lane" and older 8-PR limits: replace with "one open PR per agent".
3. Draft-then-ready dance: open ready when prepush passes.
4. Independent review of every PR touching phone/updates/privacy: only the Tier R list above.
5. "Add every minor finding as a GitHub issue": ledger instead.
6. AUTO HOTFIX after every green batch: one beta install a day (do-now 5); `jarvis hotfix` only for a P0 or when the owner asks.
7. Hourly owner questions, hourly status lines, the NIGHT_LOG: one daily status board.
8. `docs/HANDOFF.md` after every task and per-session handoff docs: STATE.md per agent; a handoff only per version.
9. "Merge one PR per run on main; never merge while a main run is in progress": the merge queue already guarantees this.
10. Stable-release choreography: hand-built evidence pack, freeze window, workflow dry-run dispatch (replaced by `jarvis release run`, do-now 6).

## Keep list
Merge queue + the two required checks; `prepush.py` before every push; no skipped tests; flake = quarantine ledger + fix in 2 days; Tier R Opus review;
backup + silent install + auto-revert, installers launched from PowerShell (#349); beta features OFF by default; guide + CLI parity + features row + Hebrew
for every feature; the owner's chat approval before any stable tag; OWNER_OPENS.md with defaults; the hard stops (spend caps, hardware, external sends,
alert dismissal, force push, bypassing checks, settings).

## Do now (max 8, in this order)
| # | Item | Owner | Effort | Saves |
|---|---|---|---|---|
| 1 | Merge group reuses the green PR proof when the tree is identical; merge group builds the installer only on packaging changes (main push already builds it via `resolve-tests` before the beta). Tier R workflow PR | Agent 2 | M | ~7 min per group, ~10 runners per group |
| 2 | Lean merge group: validate + 4 Windows shards; Linux, browser, perf, e2e, installer chain run on the main push (gate the beta) and nightly; land #516 (browser retry only failed files). Separate Tier R PR after 1 | Agent 2 | M | group 16 -> 9 min; queue wait -20 min/PR |
| 3 | Browser job only on web/dashboard/browser paths; a test that fails then passes goes to the quarantine ledger automatically with a `showstopper` issue | Agent 1 | S | ~4 min per PR, ~half of red PR runs |
| 4 | Watchdog (scheduled workflow, every 30 min): re-queue dropped PRs, toggle the label when `Auto-label policy` is missing, update one pinned "Status board" issue (merged, open PRs, open P0-P2, newest beta, owner items) | Agent 2 | M | ~60 min/day manager time |
| 5 | `jarvis install-latest` (backup, beta install silent, doctor/selftest/speedtest, features diff, auto rollback, one-line report) | Agent 2 | M | ~30 min per PC test |
| 6 | `jarvis release run --version X` (generated evidence page from `release status --report`, sign-off with the chat approval, local gate check, tag after approval, watch the workflow) | Agent 2 | M | ~2 h per stable release |
| 7 | Findings ledger + issue sweep script + labels cut to P-level + area | Agent 1 | S | ~1 h/day of issue triage |
| 8 | One-time: paste the D1 permission list, approve the delete list, OK to move open P3 into the ledger | OWNER | S (10 min) | no classifier waits; ~30 fewer open issues |

## Targets (checked weekly by agent 1 on the status board)
| Metric | Now | Target |
|---|---|---|
| Open -> merged median (non Tier R / Tier R) | 44 / 65 min | <= 25 / <= 50 min |
| Red PR runs / red merge groups | 16% / 8% | <= 8% / <= 3%; main red < 1 h per week |
| Open P0-P2 | 32 | <= 10 by 3 Nov; <= 3 at the 5.0 stable; never grows week to week |
| Agent starts per day | ~40 | <= 6 |
| Owner questions | many single ones | <= 1 batch per day |
