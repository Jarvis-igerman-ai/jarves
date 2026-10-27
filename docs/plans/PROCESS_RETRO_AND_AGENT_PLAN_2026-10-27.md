# Process retro (24-27 Oct) and the 3-agent plan

Plain words. Written 27 Oct 2026 after the 4.5 (v4.0.1) release. Plan only: nothing here is applied until the owner says so.
Sources: night log, owner-opens file, memory notes, `gh pr list` / `gh issue list` on Jarvis-igerman-ai/jarves, handoff `docs/handoff/2026-10-27-session-45-release.md`.

## Summary (what the owner reads)
1. In 3.5 days we merged 229 PRs and shipped stable 4.5. The output was good; the process cost too much.
2. Half the effort went to process, not product: 43% of PRs held for an independent review, ~67 Opus reviews, ~49 fixer runs, ~140 agent starts.
3. The biggest time sinks: the free CI queue, too many open PRs and stacked PRs, Opus review on almost everything, review-fix-recheck loops, flakes.
4. About 40 red CI runs had the SAME 5 causes (Hebrew in two places, wizard lists, DOC_MAP, a missing label check, conflicts). One pre-push script catches them.
5. New rule: only risky code (Gate, approvals, release, hotfix, installer, browser-network, wake apply) gets an independent Opus review. Everything else: checklist + CI + one cheap Sonnet scan.
6. From now on 3 agents only: (1) issues + security, (2) next release (4.5.1, then 5.0), (3) features (V5-V7). Max 2 open PRs each.
7. The manager only starts/restarts these 3, reads their 3-line reports, and asks you at most one batch of questions per day.
8. More automatic: one permission list you paste once, `jarvis install-latest`, `jarvis release run`, a self-acting CI watchdog, auto-merge for docs/tests, a docs-only CI fast path.
9. You get ONE status a day: shipped, next, needs you.
10. Your decisions needed: the permission list (section D1) and the risk tiers (A3). Safe default if silent: the 3-agent plan runs, no permissions change.

## A. Retro

### A1. Numbers (24-27 Oct, Israel days)
| Metric | Value |
|---|---|
| PRs merged | 229 (24 Oct 45, 25 Oct 86, 26 Oct 59, 27 Oct morning 39); median size 124 lines |
| Open to merge | median 44 min (reviewed `no-automerge` PRs 65 min, others 33 min); slowest 10% over 2.6 h; queue drops added 40-90 min |
| Held for independent review | 98 of 229 (43%); ~67 Opus review runs; ~32 reviews sent a PR back (FIX-FIRST, then re-check) |
| Stacked / combined PRs | ~10 stacked chains (#268/#269, #377, #465, #483, #493, #496, #571/#573, #590); 4 combined PRs (#514 #517 #522 #523) |
| Issues | 272 filed, 152 closed; 133 open now (P1 16, P2 16, P3 36, many unlabelled); ~65 from reviews, ~30 flake/showstopper (15 by the scan bot), ~20 from the owner |
| Agents | ~140 starts; 3 stopped for looping/duplicate reports; one usage-limit stop (all agents down ~1 h); CI watcher killed 3 times by low memory |
| Classifier | 3 blocks: direct merge denied (#306), outage stopped a review (#455), owner install of 4.5 denied (owner ran `jarvis update` himself) |

### A2. Red CI: causes (from the night log)
| Cause | Count | Fix |
|---|---|---|
| Flakes (timing, clock, browser leak) | ~25 showstoppers, 5 of 9 in one scan false | keep quarantine; clock-free tests; scan ignores old failures |
| Conflicts / DIRTY branches | ~15 | fewer open PRs, no stacking |
| Hebrew missing in one of two places | ~8 (#310 #320 #351 #482 #483 #489 #579 x2) | pre-push check |
| Auto-label policy missing after retarget | ~7 (#377 #483 #496 #541 #570 #571 #590) | no stacking; watchdog toggles the label |
| Wizard GROUPS/BUNDLE/SAFE_OFF missing | 4 (#310 #320 #484 #512) | pre-push check |
| DOC_MAP row missing | 4 (#333 #338 #438 #536) | pre-push check |
| Stale tests / prompt budget / DST | 3 / 2 / 1 (main red ~1 h) | targets() in pre-push; lazy tools |

### A3. Keep
Merge queue + `gh pr merge --auto`; small focused PRs; flake = showstopper with quarantine; `jarvis hotfix` with backup and auto-revert; Opus review on Gate/installer/release (it found real holes: #291 #292 #465 #493 #523 #615); owner-opens file with safe defaults; one approval question per release.

### A4. Overkill or slow (biggest 5 time sinks)
1. **CI queue on the free plan**: one full run per push; every review fix, merge-main and close/reopen restarted it. Merge groups 17-25 min each.
2. **PR pile and stacking**: up to 17 open PRs; stacked PRs went DIRTY or BLOCKED after every retarget.
3. **Opus review on almost everything**: docs, tests and UI-only changes waited for an independent review.
4. **Review -> fix -> re-verify loops**: ~32; each cost a push, a full CI run and a second reviewer.
5. **Flakes + owner questions + agent stalls**: flake scans, many single questions, looping agents, classifier blocks on safe actions.
Root causes: no risk tiers; checks the machine can do were left to CI; too many agents making PRs at once; the manager polled instead of a script acting.

### A5. Process changes
- **Risk-tiered review.** Tier R (independent Opus): Gate, approvals, release/sign-off, hotfix, installer, browser-network, wake apply, secrets/supply chain. Tier N (normal code): author checklist + CI + one Sonnet scan by the same agent, then auto-merge. Tier L (docs, tests, guides, Dependabot patch/minor): CI only, auto-merge.
- **Fewer, bigger PRs per lane; no stacking.** Finish a feature on one branch, one PR. Dependent work waits for the merge.
- **One pre-push checklist script** (`core/tools/prepush.py --checklist`): Hebrew in both places, wizard GROUPS/BUNDLE/SAFE_OFF, `targets()`, DOC_MAP row, prompt budget, lazy tools.
- **Loop rule**: an agent that fixes the same PR twice, or sends the same report twice, stops and reports; the manager does not restart it on the same task.
- **Freeze only between sign and tag**; one approval question per release.
- **STOP doing**: Opus on Tier N/L; plain merge-main pushes; close/reopen PRs; stacked PRs; separate reviewer agents for docs; filing every P3 nit as its own issue (one list issue per review instead); hourly status lines in the log.

## B. The three agents

| | 1. Issues + security | 2. Next release | 3. General features |
|---|---|---|---|
| Mission | Open bug and security count goes down every day | Ship 4.5.1, then 5.0 (beta until the phone sheet) | Build the V5/V6/V7 backlog behind beta switches |
| Scope | bugs P0-P3, flakes, review follow-ups, security issues, closing stale issues with evidence | 4.5.1 items, release tooling, CI speed, held release PRs, automation D2-D6 | smart chat, self-diagnosis, setup cards, V7 tracks A/B/D/G/M |
| Not scope | new features, release docs | new features, bug burn-down outside the release list | bug sweeps, release work, CI changes |
| WIP | max 2 open PRs | max 2 open PRs (+1 release docs PR) | max 2 open PRs |
| Review tier | R for Gate/approvals/browser; else N | R for release, installer, hotfix, workflows; else N/L | N; R only when it touches the Tier R list |
| Done | merged, issue closed with evidence | tag + release notes + owner PC on the release | merged, guide + parity + features row + Hebrew, OFF/beta by default |
| Model | Sonnet; Opus reviews for Tier R | Sonnet; Opus for release/installer reviews | Sonnet; Opus only for hard design the owner names |
| Stop when | same fix fails twice; owner-gated; classifier denies twice | gate refuses for an owner item; CI red on main | design needs an owner decision; budget cap |

**Status format** (each agent, after each merge and once a day, 3 lines): `DONE: #PRs merged, issues closed` / `NEXT: one item` / `NEEDS: owner item or blocker (or none)`.
**Cadence**: daily one status; weekly (Monday) agent 1 recounts issues, agent 2 refreshes the release list, agent 3 picks the next 5.

**First 5 tasks** (issue numbers checked open on 27 Oct):
- **Agent 1**: (1) close already-done issues with evidence (#396 B1 merged, #357 vs #360, #343/#349 vs #383, #166); (2) flake candidates #367 #422 #481 #488; (3) skills lock + redaction #584 #585 #586; (4) bridge exe path #466; (5) escaping and plans #603 #385.
- **Agent 2**: (1) land held PRs #609 #614 #611 #615 #616 #617 and the handoff #626; (2) 4.5.1 list #505: installer recovery #261, greeting re-measure #179, flake #441; (3) docs-only CI fast path + #516 / #160; (4) `jarvis release run` and simple releases #504 (OD-S1/S2); (5) 5.0 prep: #509 #510 #513, beta numbering #621, phone sheet #193.
- **Agent 3**: (1) setup cards on Home #457; (2) smart chat S4 #447; (3) self-diagnosis next slice #336; (4) browser live view B2 #397; (5) merge-queue writes G5 #412.

## C. Coordinator rules
- Spawns and restarts only these 3 agents (plus one short Sonnet fixer when a PR is red and its agent is busy). No other agents.
- Reads 3-line reports; does not read diffs or logs itself.
- Asks the owner at most ONE batch (up to 4 questions) per day; everything else goes to the owner-opens file with a safe default.
- Keeps the watchdog (D4) running; checks it once a day.
- The owner sees ONE status a day: **Shipped** (PRs, release, PC version), **Next** (one line per agent), **Needs you** (top 3 owner items with time each).

## D. AUTOMATION: remove the manual steps (ranked by minutes saved per week)
| # | Measure | Saves | Effort | Built by | Owner decision |
|---|---|---|---|---|---|
| D1 | Standing permission list for safe installed-CLI actions | ~60 min + no waits | S | owner pastes | yes/no on the list below |
| D2 | `jarvis release run` | ~90 min per release | M | Agent 2 | OK to tag after his one chat approval |
| D3 | `jarvis install-latest` | ~30 min per install | M | Agent 2 | default channel (beta) |
| D4 | Self-acting CI watchdog | ~60 min/day of manager polling | M | Agent 2 | none (only safe gh actions) |
| D5 | Auto-merge Tier L + pre-push checklist | ~40 red runs per 3 days | S | Agent 2 (script), Agent 1 (rules) | accept the tiers (A5) |
| D6 | Docs-only CI fast path | ~15 min per docs PR | S | Agent 2 (Opus review, workflow) | already approved 27 Oct |

**D1. Permission list (the OWNER pastes it into `~/.claude/settings.json`; agents never edit settings).** Narrowest patterns:
```json
"permissions": { "allow": [
  "Bash(jarvis backup now)", "Bash(jarvis backup list)",
  "Bash(jarvis update check)", "Bash(jarvis update status)", "Bash(jarvis update install --yes --silent)",
  "Bash(jarvis update install --channel beta --yes --silent)",
  "Bash(jarvis hotfix status)", "Bash(jarvis hotfix apply --dry-run:*)", "Bash(jarvis hotfix apply --from:*)", "Bash(jarvis hotfix revert)",
  "Bash(jarvis features on:*)", "Bash(jarvis features off:*)",
  "Bash(jarvis wake test:*)", "Bash(jarvis wake train:*)", "Bash(jarvis wake reset-tune)",
  "Bash(jarvis doctor)", "Bash(jarvis selftest)", "Bash(jarvis speedtest)",
  "Bash(git push jarves refs/tags/v*)" ] }
```
Auto-mode allow wording (same list, in words): "Allowed on this PC for the JARVES project: jarvis backup, jarvis update check/install (silent, after a backup), jarvis hotfix status/dry-run/apply/revert, jarvis features on/off, jarvis wake test/train, doctor/selftest/speedtest, and pushing an annotated `v*` tag ONLY after the owner approved that release in chat." Flags checked against the CLI source on 27 Oct (`hotfix` has no `--yes`; it always backs up and dry-runs first). Installers stay launched from PowerShell, never Git Bash (#349).
**Standing authorization (answer 'yes' once):** "Agents may run the listed jarvis commands on my PC (backup always first, silent, auto-revert on failure) and push the release tag after my one chat approval for that release. No other settings, accounts, spend or force pushes."

**D2. `jarvis release run --version X`**: one command (or `core/tools/release_run.py`) that records the rows with the owner's approval note, `--finish`, opens the docs PR, queues it, runs the dry run from the branch, tags the signed commit, watches the workflow and checks assets and `latest.json`. "Do stable release" = evidence summary, one approval, one command.
**D3. `jarvis install-latest`**: backup, update (silent, `/RESTARTJARVES=1` only if Core ran), doctor + selftest, features diff before/after, auto-rollback on failure, one-line report with revert id. No agent needed.
**D4. Watchdog** (scheduled GitHub workflow or local cron, every 30 min): re-queue dropped PRs (`gh pr merge N --auto`), toggle `no-automerge` when "Auto-label policy" is missing after a retarget, `update-branch` only when DIRTY without conflicts, label green Tier L PRs, post one line per action. Never pushes code, never closes/reopens, never merges by hand.
**D5. Auto-merge Tier L** (docs, tests, guides, Dependabot patch/minor) on green CI without a reviewer; the pre-push checklist (A5) runs before every push.
**D6. Docs-only fast path**: docs-only merge groups skip the test matrix but keep `CI result` + `Auto-label policy`; one workflow PR, alone, with an Opus review.
