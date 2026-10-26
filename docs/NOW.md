# NOW — V4.5 bounded implementation authorization (owner approval, 24 October 2026, Israel time)

The owner approved V4.5. This is the only active brief; scope and acceptance are defined in the [V4.5 plan](plans/V4_5_STABILIZATION_SECURITY_QA.md), status in [ROADMAP](ROADMAP.md). The previous NOW text (SW-V1 era and the 20-24 Oct notes) is kept unchanged as evidence in [reviews/NOW_SWV1_18OCT2026](reviews/NOW_SWV1_18OCT2026.md). `Jarvis-igerman-ai/jarves` is the only writable repository (remote `jarves`); `igalgerman/JARVIS` is read-only history.

**Owner authorization, 24 Oct — Performance 4.5 (active bounded slice):** Build a coherent measurement-and-advice package on existing speedtest/selfspeed, performance budgets, Dashboard and GitHub issue_registry; repeated comparable readings must drive local evidence-based recommendations and optional owner-enabled automatic sanitized GitHub performance issues with deduplication/cooldown. Preserve the existing manual approval requirement for all OTHER diagnostics. Never self-modify, self-merge, start paid/live cloud sessions or send private text. Implement in protected reviewed PRs with regression tests. Actual Windows microphone/paid voice/network performance and end-to-end improvement claims remain pending real-device acceptance; no fabricated measurements or release approval. This explicit narrow authorization supersedes only the earlier docs-only V4.5 performance restriction, not other stabilization streams.

## Authorized scope (owner-approved)

| Row | Work | Priority | Needs from the owner |
| --- | --- | --- | --- |
| 0 | #121 performance advisor PR (in flight) | - | nothing |
| A | Issue audit and evidence-based closure; table first; #89 and #77 first | P0 | nothing to start |
| B | CodeQL/security backlog #6-#37; Sonnet per bucket, Opus reviews security | P0 | security-events read access |
| E0-E3 | Faster patch flow: baseline, conflict hotspots, scoped worktrees, merge queue | P0, early | nothing |
| C | Recurring light (daily) and full (weekly) security scans, `auto_fix` OFF | P0/P1 | scan cadence choice |
| F | Interactive QA workspace + voice skill + GitHub issue reporting (#60 canonical) | P1 | sign-offs, owner PC |
| D | Camera UX, on demand only | P1 | real webcam on the owner PC |
| #122 | Real voice latency and device acceptance | P1, last | owner PC and OpenAI credit |

Recommended order: #121, then A and E0, B in parallel, then C, F, D, and #122 last.

Out of scope: V5 phone acceptance, V6, V7, new V4 features, physical hardware.

## Authorized now vs needs the owner

Authorized now: code, tests, docs, guides and PRs for the rows above; read-only GitHub inventories with the existing token; evidence comments on issues and closing only issues whose evidence is complete; scoped CI/merge-flow changes that keep every required check.

Still needs the owner (do not do without it):
- Private PC tests: real microphone, webcam, installed-build round trip and rollback.
- OpenAI credit; no paid API call without explicit permission (the existing V4 budget is not extended).
- Security-alert read access (security-events scope); without it mark coverage incomplete, never claim zero.
- Scan cadence choice (days, time, report destination); scans stay off until chosen.
- Sign-offs: #60 human rows, accepted-risk decisions, P3 decisions.
- No external sends, no new account linking, no alert dismissal without evidence (precise, justified, one by one), no stable release without owner sign-off on the installed build.

## Operating rules

- Follow [CLAUDE.md](../CLAUDE.md) and [AGENTS](../AGENTS.md): every new feature ships with its guide (EN and HE), CLI parity row and capability row.
- One task, one branch, created from fresh `jarves/main` in `C:/work/wt/<branch>`. Small PRs, run `python core/tools/prepush.py` first.
- Merge each PR alone as soon as its checks are green, never while a run on main is in progress; low-risk ones (docs, tests, guides) by label. No forced pushes, no `git add -A`, no large integration batches.
- Sub-agents execute (Haiku for status/CI/audits, Sonnet for code, Opus only for plans and security reviews); the coordinator manages, reviews diffs and merges.
- Remove the worktree and local branch after the merge.
- Never close an issue, dismiss an alert or mark a test PASS without linked evidence. Passing CI is not installed-app acceptance.
- Record results in `docs/handoff/` (one file per task) and the questions in the same note under `; reference the GitHub issue in every commit.

## Open questions for the owner

1. Scan cadence: daily light and weekly full as proposed, or other days/times, and where reports go?
2. May the security-events read scope be granted to the runner token (or will you export the alert list)?
3. May scans draft repair PRs when you turn that on (default stays OFF, never auto-merge)?
4. Camera: which device is authoritative, and may an explicit still be sent to a provider?
5. #60 baseline: full first run or smoke only? When can you test on the PC (webcam, voice, OpenAI credit for #122)?

## V7 work package (owner approved 25 Oct 2026; plan: [V7 work package](plans/V7_WORK_PACKAGE_2026-10-26.md))

Execution authority for V7 slices. Priority: V4.5, V5 and V6 gate items keep the PR slots; V7 runs in free slots and on free agents, one small PR (about 400 changed lines) per slice, from fresh `jarves/main`, issue per slice (label `v7`, umbrella #50). Tracks: G GitHub (full read+write, every write approval-gated with the exact action, merges only through the merge queue), M Model Lab and spend policy, D OpenCode third harness, A Android daily companion (PC-hosted Core first, then offline basics), B Browser Studio (read-only live view, then takeover, then opt-in recording), R research (OpenClaw and alternatives, Buzz), X extras backlog.

First three slices: G1 (read-only GitHub client), D1 (OpenCode read-only adapter), M1 (ModelRegistry and SpendPolicy). Docs filler: A0, R1, R2. Fakes first; no live login, no paid call beyond the caps in the plan (D4 1 USD, M5 2 USD, only with the owner's go), no private code to another provider, no new device permission, no recording of secrets, no direct merge/force push/bypass, no stable release. Opus-only review for approvals, write framework, token/key storage, spend policy, mic permission, live-view and takeover surfaces, orchestrator gate and installer; Sonnet otherwise. Owner needs and safe defaults: plan section 8.
