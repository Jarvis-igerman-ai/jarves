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

Away: retain the question and usable checkpoint, pause only dependent work and
release only safely paused resources. Continue other ready tasks. A deferred
question remains unanswered; silence is never approval. Answers/checkpoints
travel with the resumed task after other prerequisites hold.

Runtime permissions remain on existing Claude/JARVES approval surfaces. Planning
answers cannot approve tools. Do not park the whole manager in native
AskUserQuestion while the owner is away; use the durable inbox and switch work.

## One source per fact

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

## External observer

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
