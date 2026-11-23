# JAR-553 / issue #1313: blocked on an owner decision (23 Nov 2026)

No code change. This report records why the ticket "Fix security issue #1313" cannot be finished by an agent.

## Problem (#1313, P3, Tier R)
A process that runs as the same OS user and can WRITE the workflow home can write its own record into
`unlock.json` and then unlock the dashboard answer paths over HTTP with a code it chose itself.

## State checked on main @ 18d01f3
- `core/src/jarves/workflow/unlock.py` stores a salted scrypt hash of a 4-digit code in `unlock.json`
  (last changed in f053c92). PR #1304 (the X25519/HMAC follow-up) was closed, not merged. So on main both
  gaps are open: a reader can brute-force the code offline (#750) and a writer can plant a record (#1313).
- No owner decision on the #750 threat model is recorded in the repo or on the issue.

## Why no software fix fits this ticket
- The owner's terminal and the agent run as the same OS user. Any channel the terminal can use to get or
  check the code (file, local IPC, named pipe, credential store) is open to that agent too.
  `owner_only_guard` checks env/TTY only, and a pty can fake both.
- Such an agent can also read the unlocked HttpOnly cookie from the browser profile, or drive the browser.
- The real fix is OS separation: run the workflow server as a separate OS user and set an ACL on the
  workflow home so the agent user cannot write it. That is a machine and permission change on the owner
  PC, which is an owner gate (AGENTS.md, security basics).
- Loop rule: #750 already has 8 closed fix PRs (#1148, #1166, #1210, #1234, #1245, #1261, #1273, #1304).
  The triage comment on #1313 (19 Nov) says no new attempt until the owner chooses.

## Owner decision needed (one)
1. Recommended: accept "token-only and read-only callers" as the #750 threat model, and keep #1313 as a
   documented known limit (it is already described in the `unlock.py` docstring and the workflow_console
   guide). Reason: the dashboard does not gate money, release or security actions.
2. Order OS separation: a separate Windows user for the workflow server plus an ACL on the workflow home.
   This needs owner-run steps on the PC; an agent can then write the setup code and a test for it.

## Verification
UNVERIFIED: nothing was run against the installed product. Repo state was checked with
`git log -- core/src/jarves/workflow/unlock.py` and by reading the file.
