# NOW: the single active brief (27 Oct 2026)

Plain words. This is the current truth; if another document disagrees, this one wins. Repo: `Jarvis-igerman-ai/jarves` (remote `jarves`) is the only writable repo.

## Where we are
- Released: JARVES 4.5 = tag `v4.0.1` (installer 4.0.1.1214) on 27 Oct 2026. Beta channel follows green `main`. Details: [handoff after the release](handoff/2026-10-27-session-45-release.md), [release notes](RELEASE_NOTES_4.5.md).
- Next: 4.5.1 (installer recovery, known issues, held wake PR) -> 5.0 (phone, stays beta until the owner's phone sheet) -> 6.0 (drive / hands-free, until his car test) -> 7.0 ([work package](plans/V7_WORK_PACKAGE_2026-10-26.md)). Plans: [ROADMAP](ROADMAP.md).
- Stable tag only with the owner's chat approval and a green gate ([RELEASING](RELEASING.md)). Unverified features ship OFF as beta features.

## The 3 agents (one lane each, Sonnet; Opus only for Tier R reviews)
| Agent | Lane | Prompt |
|---|---|---|
| 1 | issues + security (open P0-P2 only goes down) | [AGENT_1](plans/AGENT_1_ISSUES_PROMPT_2026-10-27.md) |
| 2 | releases, CI speed, betas, install tests; builds the unbuilt tools FIRST (`jarvis install-latest`, merge-group proof reuse, `jarvis release run` is in, watchdog is in; the docs-only fast path already exists: docs PRs merge in about 3 min) | [AGENT_2](plans/AGENT_2_RELEASE_PROMPT_2026-10-27.md) |
| 3 | features V5-V7, OFF by default | [AGENT_3](plans/AGENT_3_FEATURES_PROMPT_2026-10-27.md) |

Each keeps `C:\Users\97254\.claude\projects\C--work-jarvis\agents\agentN_STATE.md` (<= 30 lines).

## The process
[LEAN_PROCESS_V2](plans/LEAN_PROCESS_V2_2026-10-27.md): test once, review only Tier R, one open PR per agent, issues only for real defects (nits go to `docs/reviews/FINDINGS_LEDGER.md`), scripts act instead of people polling.
- BATCHING (owner 27 Oct): one local batch branch per agent, one commit per item, `prepush.py` + targeted tests after each commit; ONE ready PR per batch (3+ items of one lane or end of day; ~8 commits / ~800 non-test lines). P0/P1 and any Tier R change go ALONE and at once. Never mix lanes. A red batch is fixed in place. Keep committing to the NEXT batch branch while one PR is queued.
- 3-day TRIAL: daily metrics in each STATE.md: open P0-P2, PRs merged, CI re-runs, hours merge -> installed beta.

## Owner decisions
`C:\Users\97254\.claude\projects\C--work-jarvis\OWNER_OPENS.md` (read it fresh before asking). Owner present = last message < 10 min or "back": at most one AskUserQuestion batch (<= 4) a day. Away: write the item with a safe default there and go on.

## Hard stops
Paid API beyond caps; hardware or physical tests; external sends or posts; dismissing alerts without evidence and his OK; force pushes; bypassing checks; stable tag without his approval; editing `~/.claude` settings or CLAUDE.md files without him; installers launched from Git Bash (use PowerShell).

## First 10 minutes of a fresh manager
1. Read `CLAUDE.md`, `AGENTS.md`, this file; open [CONTEXT_MAP](CONTEXT_MAP.md) only on demand; skim [HANDOFF](HANDOFF.md). Read OWNER_OPENS.md and the three STATE files.
2. Run the watchdog / status board if it exists (lean do-now 4); else check `gh run list -R Jarvis-igerman-ai/jarves --branch main --limit 3` (is main green?).
3. Counts: `gh pr list -R Jarvis-igerman-ai/jarves` (open PRs, queue), `gh issue list -R Jarvis-igerman-ai/jarves --label P0` and P1, P2 (open P0-P2 now).
4. Start the 3 agents with their prompt files (copy the part below the line); one per lane; no extra agents.
5. If the owner is present: ONE AskUserQuestion batch (<= 4, recommended first, safe default stated). Then manage only: restart an agent when it finishes; no polling.
