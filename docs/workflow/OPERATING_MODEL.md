# One manager, transparent progress

Owner direction, 30 October 2026. This replaces task/presence/handoff rules in
dated agent prompts and Lean Process v2. Permission, security, cost, independent
risky-change review and protected release gates remain. No automatic stable tag.

## Native manager and workers

Claude Code is normally the manager. Use native subagents for bugs, the current
version and product verification; add specialists for independent ready work.
Definitions live under `.claude/agents/`. Codex uses the same ledger/evidence
contract when assigned. Do not enable experimental teams or another LLM scheduler
just to use the view. Verify actual worker tools and worktree base SHA; neither
is guaranteed by a role name or isolation flag.

The local workflow service stores tasks, questions, evidence and resource leases
outside the Core. It cannot approve runtime tools, merge code, publish or pay.
Optional delivery to already attached orchestrator sessions uses its existing
bridge/gates, requires explicit opt-in and is not the default native-Claude path.

## Keep work and evidence current

Start with one worker and add only independent work/test capacity. Roles are not
fixed staffing. The implementer performs relevant QA; independent review follows
the existing risk rule. Do not add another LLM scheduler. There is no general provider launcher: the autopilot (below) launches
workers only under the owner's time-limited start grant.
Use [the owner journey](USER_JOURNEY.he.md) for diagrams and the simplicity audit.

1. Select a bounded authorised task with an ID, acceptance, dependencies and
   named exclusive resources such as `windows-gui`, `installer` or a phone ID.
   Imported scope needs reconciliation before execution.
2. Delegate the exact task ID, required base and context. Hooks inject the native
   worker ID/home. The worker claims that task with `next --task`; rejected
   claims are blockers, not permission to implement competing work.
3. Record implementation separately from verification and delivered artifact.
   Set code revision before recording tests; identify target and any fakes.
4. Before returning, record evidence and move to review, or save an owner
   question and safe checkpoint. A native stop without handoff is quarantined.
5. After merge, inspect the combined build and relevant installed-product
   scenario. Close defects only with required evidence. Maintain guides/parity.
6. Continue independent authorised work. If none remains, state whether the
   cause is owner wait, dependencies, reserved resources or missing scope.

Batch related defects of similar risk; no fixed minimum delays shipping. Urgent
or sensitive fixes remain separate when required. Use targeted local checks and
existing broad CI; do not rerun every suite at every step. Each task has one owner.

## Present, away and answers

The owner explicitly selects Present/Away. Present lasts ten minutes and renews
only through actual UI activity, not background polling. Ask concise planning
questions with choices/free text; there is no old once-a-day cap while present.
Ask with options (`--choices`, each with what changes), one recommended option
and reason, `--why`, `--minutes` and the `--safe-default`. Owner "ask more"
follow-ups reach the manager once at its next native Stop; answer each with
`workflow reply --id ID --answer TEXT`. A reply never answers the question.
A 'Get more info' click sends "Explain this question: ...": reply in the owner's
language with a plain explanation, numbered steps, each option and the recommendation.

Away: retain the question and usable checkpoint, pause only dependent work and
release only safely paused resources. Continue other ready tasks. A deferred
question remains unanswered; silence is never approval. Answers/checkpoints
travel with the resumed task after other prerequisites hold.

Runtime permissions remain on existing Claude/JARVES approval surfaces. Planning
answers cannot approve tools. Do not park the whole manager in native
AskUserQuestion while the owner is away; use the durable inbox and switch work.

## One source per fact

Cross-provider delegation is owner-invoked: preserve the exact task, provider and
model requested. Use the existing `jarvis agents start ... --model MODEL` and
`ask/tell/status` interfaces. Keep approvals, allowed projects, isolated writers
and cost policy; do not silently substitute models, recursively delegate or
interpret an agent's text as an owner request. Use a compact task-ID/ledger-read
prompt for OpenCode (its full approval prompt is bounded). `requested_model` is
not a verified effective model. Full automatic Orchestrator task/lifecycle
integration is the prepared follow-up [#715](https://github.com/Jarvis-igerman-ai/jarves/issues/715).

Claude models (verified available 31 Oct 2026): Opus 5.5 `claude-opus-5-5` for reviews,
Tier R and design; Sonnet 5.5 `claude-sonnet-5-5` for code, fixes and docs (the
workflow-bugs/current/qa default); Haiku 5.5 `claude-haiku-5-5` for status, the
merge steward and cleanup; Fable 5.1 `claude-fable-5-1` only when the owner names it.

`version_plan.json` defines required/optional/deferred scope and source links.
Read-only GitHub sync adds issue/PR status, without inferring verification.
Closed issue ≠ merged code ≠ tested user scenario ≠ shipped artifact.

Verified needs acceptance evidence bound to the explicit current revision at
the required automated/app/device level. Done also needs an identified beta or
stable artifact. New revisions invalidate prior passing evidence and delivery.
Ear/device/car checks stay unknown without real evidence. Optional scope does
not belong in a mandatory-completion denominator.

NOW contains scope and pointers; HANDOFF is history. The live view and CLI read
the same private database. Hooks report minimal lifecycle IDs/timestamps, never
complete transcripts or proof that the product works. Keep sensitive logs/screens
out of the public repo. Existing capability mechanisms remain authoritative for
runtime support; the console is not another assistant-feature registry.

## Merge steward

Nobody waits on a timer. The manager reacts to events (native hooks, the
supervisor alerts, or the Monitor tool on `gh` output) and hands PR events to
the `workflow-merger` subagent (Haiku). No cron, sleep or polling loop.
- Worker finishes or blocks on CI or an owner question: the manager assigns the
  next independent ready task from the ledger at once; the question persists.
- PR green, main not red, not Tier R: `gh pr merge N
  --auto`. Tier R = the `no-automerge` label, OR a high-risk result from running
  `core/tools/risk_class.py --files-from FILE` (`C:\work\jarvis\core\.venv\Scripts\python.exe`,
  not bare `python`) on the PR's changed files (HIGH_RISK is the path list, but it is not exhaustive), OR the PR
  touches any Tier R category by judgment; when unsure, do not queue, report. The tool exits 1 for ordinary
  Tier N code, so match the words "high-risk path" in its output, not the exit code.
  Missing "Auto-label policy" check on a non-Tier-R PR: add then remove
  `no-automerge`, so it ends absent. Queued PRs are
  merge-queue entries, not autoMergeRequest: count `mergeQueue` entries.
- PR red: failing log tail only, then a Sonnet fixer; conflict: the fixer
  rebases that PR's own branch (`--force-with-lease` on it only).
- Tier R green (Gate/approvals, release/sign-off/tag, hotfix/update write paths,
  installer/packaging, browser network, wake apply, workflows/secrets): request
  the Opus sign-off; never merged by the steward.
- Queue entry merged: check main CI and free the next batch.
- Per cycle the steward decides individual vs integration with
  `core/tools/integration_branch.py plan --auto` (JSON: tiers, file-overlap and
  `git merge-tree` forecast, recommendation). `individual` when fewer than 3
  eligible PRs, any overlap, or a lone Tier R; `integration` for 3 or more
  independent green PRs: `build ... --open-pr` makes ONE branch
  `integration-green-<MMDD>` (`--no-ff` merges, conflicting PRs skipped with the
  reason, changelog fragments checked, prepush run) and one PR with
  `Supersedes: #a #b`. Any Tier R inside: label `no-automerge` and ONE Opus review
  of the combined diff; the steward never merges it. After it merges,
  `close-superseded` comments, closes and deletes only fully merged branches.
No close/reopen, merge-main push, force on main, check bypass or blind rerun;
a test that fails then passes is quarantined with a `showstopper` issue.

## External observer

Action history uses the same local database, not a separate telemetry platform.
Default capture is metadata; detailed tool data is explicitly opt-in and
best-effort redacted. Native hooks and existing normalized provider events have
different coverage; never fabricate missing history or precise ancestry.
`workflow retro` only prepares selected local evidence and an explicit `/retro`
invocation. It never installs a skill, runs a model or changes instructions.
The owner selects any proposed permanent change separately.

The supervisor is separate from Claude and the Core. It checks freshness,
stalls, ready work and dependency/resource waits, and can restart only its owned
web child with a bounded restart budget. It cannot kill or blindly restart
workers or repeat ambiguous actions. Lost workers retain locks until explicit
checkpoint/recovery. Slow GitHub reads run separately from local monitoring.
Only the actual supervisor writes its heartbeat. Native Stop can request at most two
continuations per hour when independent authorised work is ready; it respects
stop_hook_active and never loops on pending owner questions or imported scope.

Windows Task Scheduler is the further layer that restarts the supervisor after
failure/login. Until installation and restart tests run on the owner's PC,
background recovery there is unverified. Do not assume a saved inbox can wake a
dead native session; ordinary async hooks do not reliably do that. See
[industry research](INDUSTRY_RESEARCH_2026-10-30.md) for verified interfaces/version caveats.

First reconcile local instructions and run a harmless native-worker test,
including owner wait/answer/resume and a stopped manager. Measure time to an
installed working change, escaped/reopened bugs, CI retries and owner effort.
PR count and zero open Issues are not substitutes for product quality.

## Autopilot (beta, off by default)

Deterministic code inside the existing supervisor, not an LLM scheduler (umbrella
[#715](https://github.com/Jarvis-igerman-ai/jarves/issues/715)). Policy: `<home>/autopilot.json`,
`enabled=false` and `kill` switch. It launches workers only under the owner's grant
(exact project, models, expiry, concurrency); worker tool approvals are unchanged.
Watchdog actions (`workflow/autopilot_watch.py`):
- Lane idle with ready work for 300 s: start a worker; versions in policy order.
- Stalled or offline autopilot-owned worker: stop its session, re-queue once; the
  second stall blocks the task with an alert and the lane moves to the next task.
- Foreign or native session, or a permission wait: alert only, never resurrect or
  approve. A permission wait over 10 minutes sends one deduplicated Core notify.
- Steward: a new PR head gets a Haiku steward task, but Tier R (risk_class
  high-risk, `no-automerge`, autopilot code, or unsure) gets an Opus review task and is
  never queued for merge. One beta-verify task per day (plus any beta with a P0/P1 fix).
  `device` tasks become an owner test item; only that task waits.
- Never merges, tags, promotes stable, edits settings/CLAUDE.md or authorises imported scope.
