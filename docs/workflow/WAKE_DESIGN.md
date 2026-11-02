# Quiet manager wake (issue #762)

Problem: a timed restart nudge types into a session that may be mid-turn, holding a draft or showing a
permission prompt. Replacement: a coalesced wake request in the ledger plus clean-prompt delivery.

- Request: `wake.request_wake(store, target, reason)`. One pending record per target (`workflow_meta`
  key `wake:<target>`); repeats raise `count`, keep at most 5 reasons. Many events, one wake.
- Delivery: `wake.try_deliver(store, target, channel)`. Injects the single line `WAKE_LINE` only if the
  channel state is known and idle, with no draft, no permission prompt and not mid-turn. Unknown state
  counts as not clean. Success clears the pending record.
- Every outcome is an event: `wake_requested`, `wake_deferred`, `wake_delivered`, `wake_failed`.
- No timer, no retry loop. A deferred wake stays pending and is attempted again only on a new event
  (a new request or the manager's idle hook). Silence is never approval; a wake grants no permission.
- Channel: an object with `state()` and `inject(line)`. tmux-style panes do not exist on Windows; a Windows
  console/UI-automation channel is a separate opt-in follow-up and is UNVERIFIED (no real keystroke
  injection ships here, so no focus or sound is produced). Wiring into the supervisor/hooks is also a
  follow-up; this change is the queue, the delivery rule and the simulation.
- Simulation: `core/tests/test_workflow_wake.py` drives the scenarios with a fake channel and clock.
