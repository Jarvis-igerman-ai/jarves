# Software factory: flexible stages for the project manager — 23 Oct 2026 (follow-up to #94)

## Asked
Owner, after #94: every task should go through `plan -> work -> test -> review -> merge -> release` (bug-fix:
`reproduce -> diagnose -> work -> verify -> review -> merge`), but the order can change, any step can be skipped, and
each step can be done by JARVES, the owner, one agent, a list of agents (or sub-agents), with cloud agents, a debate
between two agents with a judge, and GitHub optional. "In general it must be full sw factory."

## What changed
Design: [SW_FACTORY](../plans/SW_FACTORY.md). New modules `core/src/jarves/projectmgr/`: `pipeline.py` (stages, actor
specs, templates, validation), `engine.py` (`StageEngine`, the stage machine mixed into `Manager`), `debate.py`
(round-based debate with anti-injection quoting and a judge who never takes part), `forge.py` (GitHub optional
off/read/write via `gh`, no token stored). New service ops: `flow`, `answer`, `bug`, `debate`, `decisions`, `github`,
all with CLI, voice-tool (`project_answer`, `project_bug`, `project_debate`, `project_decisions`) and dashboard
surfaces. `docs.store.py` gained a `decisions` table and task columns (`pipeline`, `stage`, `rounds`, `job`,
`waiting`, `kind`), migrated in place.

Two independent Opus design/code reviews ran before and after the first implementation; their findings (retry vs.
back-to-stage semantics, parked-task scope holding, restart recovery, review abstention never approving, best-of-N
determinism, debate anti-injection and judge exclusion, owner-approval bound to an exact tree, release safety, GitHub
push restricted to `jarves/...` branches) are implemented — see "Review fixes" in SW_FACTORY.md.

**Found and fixed during testing (not from either review):** a real race between the dispatcher starting a stage
and `stop_agent`/`cancel_task` stopping the same task — the dispatcher's own status write could silently overwrite an
owner's stop between two separate DB calls, leaving a stopped task showing `running` forever. Fixed with one atomic
guarded write (`ProjectDB.claim_stage`, `UPDATE ... WHERE status IN ('running','review')`); covered by a deterministic
unit test plus the original (now non-flaky) end-to-end one.

**Deferred to a later PR, per review:** actually starting/polling cloud agents (Codex cloud, Copilot, Jules) — their
start/poll/result interfaces are each individually documented but none has been live-verified, and GitHub push (needed
for any cloud stage) is brand new in this PR. `forge.py` (GitHub status/config) ships now; the `cloud:<provider>` actor
is not yet accepted by `pipeline.py`.

## Verification
`core/tests/test_projectmgr.py`: 55 tests total (new: flow parsing/validation, feature/bugfix/release/decide
templates end to end, owner-performed stages with tree-bound approval, best-of-N, debate (judged and owner-decided),
GitHub config/status against a fake `gh`, the race regression). Ran the full file 5× after the race fix with no
flake. `python core/tools/prepush.py`: see the commit this note ships with.

NOT verified: real coding-agent behaviour for every stage kind, real `gh` CLI, cloud agents (not wired yet), a debate
judged by a model that returns an unexpected format on live traffic.

## Decisions needed
- **USER DECISION REQUIRED:** should `cloud:codex` / `cloud:copilot` / `cloud:jules` land in the next PR once GitHub
  write mode is exercised for real, or wait for the owner's own first live run of this PR's local-agent flows first?
- Release command safety: `projects.release_env` lets the owner name env variables a release command may read (e.g. a
  publish token). None are allow-listed by default; is that the right default, or should common ones (`GITHUB_TOKEN`)
  be suggested in the guide?
