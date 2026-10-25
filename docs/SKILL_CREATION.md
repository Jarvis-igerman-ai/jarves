# JARVES-generated Skills with approval (SW-V2 M2, D15)

Code: `core/src/jarves/skills/generated.py` (manager, validator, registry sync, dashboard routes),
`core/src/jarves/skills/sandbox_child.py` (per-call child). It extends the ONE `SkillRegistry`; no second loader.

## Flow
1. **Propose** — JARVES calls `skill_propose` (or the owner writes files). A draft lands in
   `<state_dir>/skills/drafts/<id>/` (`SKILL.md` + optional `skill.py`). Nothing is registered or run.
2. **Validate** (static only, never executes): manifest, capabilities, tool permissions, AST rules, sizes, banned
   intents (calendar deletion, sending mail), prompt-injection wording (warning). Report + Hebrew/English summary.
3. **Approve** — owner only (dashboard **New Skills** page; there is no model/voice approve tool, and the manager
   refuses any `by` other than dashboard/cli/user), bound to the sha256 of exactly what was reviewed. Installs `installed/<id>/versions/<N>/` **disabled, no grants**.
4. **Grant** — a subset of the requested capabilities (`storage`, `network:<public host>`) and, separately,
   `actions` for its action tools. **Enable** — owner only. Disable works from any route.
5. **Rollback** to the previous version (disabled, grants cleared), **uninstall** (moved to `removed/`).

## SKILL.md
```
---
name: shopping            # id: 3-32 chars a-z0-9_
title: Shopping list
description: Keeps a small shopping list
capabilities: [storage]   # storage | network:<host>
---
Markdown instructions (served by the read tool shopping_guide).
```
A Markdown instruction alone never grants anything: tools it mentions keep their own permissions.

## skill.py (optional)
`TOOLS = [{"name": "<id>_<word>", "description", "permission": "read"|"action", "args": {"x": "string"|"integer"|"number"|"boolean"}}]`
and `def <tool>(args, ctx)`. Stdlib allow-list only (json, math, re, datetime, statistics, random, string, textwrap,
itertools, functools, collections, decimal, fractions, unicodedata, calendar, zoneinfo). No top-level code, classes,
async, global, eval/exec/open/getattr/type/print, private or dunder attributes, `__` strings, `.format`.
`ctx.storage_get/set` (needs storage), `ctx.http_get(url)` (https, granted host, public address only, 200 KB),
`ctx.now()`, `ctx.log(text)`. Destructive tools are not allowed in generated Skills.

## Enforcement (all routes: voice, model tools, scheduler, offline commands, dashboard test)
- Registry: disabled Skills are hidden and refused (`SKILL_DISABLED`); action tools run only with the `actions` grant.
- `GeneratedSkills.run` re-reads the state on every call (stale registry / dashboard process) and checks grants.
- Child process per call: `python -I -S`, empty environment (no API keys), throw-away cwd, restricted builtins and
  imports, POSIX rlimits (memory, CPU, no file writes, no fork), 10 s timeout, max 50 ctx calls.
- Network results are untrusted output: no model-origin side effects in the same turn.

Residual risk (truthful): AST + restricted builtins are not a complete sandbox; on Windows there are no rlimits.
The approval gate, process isolation and key-less environment are the main controls. OS-level sandboxing on Linux
(bubblewrap/seccomp) is tracked in #21. Tests: `core/tests/test_skill_creation.py` (29 malicious drafts, runtime
containment, tampered install, network grants, rollback, every route refused while disabled).

## Manifest v2, update and rollback (V6 #220, switch `skills_manifest2`, OFF by default)
With the switch on, `skills/platform2.py` adds manifest v2 checks to the same flow (nothing else changes):
- propose / approve: the trust state (`unsigned`, `owner_signed`, `tampered`) and the semver are computed and shown in
  the summary. A tampered signature or an invalid manifest 2 (undeclared action tools, unknown permission, too-new
  `min_core`, a missing dependency) fails validation, so it can never be approved, installed or updated. Unsigned and
  owner-signed Skills need the same sha256 approval and install DISABLED with no grants.
- load / run: a stored version whose files no longer match the sha256 the owner approved (or whose signature is
  tampered) is not registered and does not run (`SKILL_TAMPERED`).
- `jarvis skills update <id>` shows the plan for the waiting draft (added / removed permissions, trust, sha256);
  `--approve <sha256>` installs it through `approve(by="cli")`. `jarvis skills rollback <id> [--yes]` goes back one
  version after checking the stored copy against its approved sha256; grants are cleared and the Skill is off.
- The optional owner key is a file the owner creates at `<home>/skills/owner.key` (16+ bytes); no code creates it.
- There is no model tool for update, rollback, approve, grant or enable. Logs carry ids and versions, never file text.
- A v2 Skill may list `storage` / `network:<host>` under `permissions`; they are requested exactly like `capabilities`.
Tests: `core/tests/test_v6_skills_platform2.py`.
