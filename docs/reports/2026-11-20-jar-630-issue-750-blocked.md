# JAR-630: issue #750 needs an owner decision (no code change)

Date: 20 Nov 2026. Base: origin/main 9b6b427.

## What the issue reports

An agent that can read `access.token` in the workflow home could post to the
dashboard owner-answer route over HTTP.

## Status on main

- That vector is closed (671595bd, PR #779). A session opened with
  `access.token` alone is read-only. Every owner route returns 403 until the owner
  types an unlock code shown only in their own terminal: answer, confirm, defer,
  reopen, discuss, ask-more, task transition, followups, owner, tasks, dispatch
  and trace/settings (`core/src/jarves/workflow/server.py`, the `_unlock_left()` gate).
- Check: `cd core && uv run --frozen python -m pytest -q tests/test_workflow_unlock.py`
  gave 20 passed on 9b6b427.

## Why there is no fix in this PR

The gap that is still open is already stated in `workflow/unlock.py`: an agent
that runs code as the same OS user can call `unlock.issue(home)` itself, or crack
the 4-digit hash in `unlock.json` offline. Removing the offline check alone
(the approach in closed PRs #1148, #1166, #1210 and #1234) does not stop an
agent that can mint a code. Any agent able to crack the hash offline can also
mint a code. Files and localhost HTTP cannot keep out a code-running agent
that runs as the same user. That attempt also grew past 500 lines and was
closed after review rounds.

Closing the gap needs an owner-presence check through the OS, for example
Windows Hello consent at unlock. That is a Tier R design change and an exception
to the no-popups rule. The dashboard is also deprecated (owner, 8 Nov). The
JAR-475 and JAR-552 triage comments on #750 reached the same conclusion.

## Owner decision needed

1. Close #750 as fixed and accept the same-user code-execution limit as known.
2. Keep #750 open for an OS-consent redesign if the dashboard comes back.
