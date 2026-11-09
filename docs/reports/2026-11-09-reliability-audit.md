# Reliability audit: wake greeting, phone sync, update paths (JAR-60, 2026-11-09)

Scope: `core/src/jarves/voice/`, `mobile.py`, `continuity.py`, `auto_update.py` (read only). Method: grep for
`except ...: pass/continue` and bare returns, then read each site. Open PRs #977 #978 #980 #981 #982 #985 not examined.
Update path (`auto_update.py`) is Tier R: proposals only.

## Ranked findings

| # | file:line | Risk | Evidence | Status |
|---|-----------|------|----------|--------|
| 1 | auto_update.py:525-553 (busy checks) | HIGH: a failing `approvals.has_pending()`, `chat.turns`, orchestrator or quiet-hours probe is treated as "not busy", so an update restart may run during an approval or coding session | four `except Exception: pass` blocks | PROPOSAL (Tier R): on probe error add a `probe_error` busy reason (fail closed) plus log |
| 2 | voice/delegation_client.py:~279 (`save`) | MED: state file write failure silently lost; `jarvis budget status` and dashboard show stale ladder state | `except OSError: pass` | FIXED, DELEG_STATE_SAVE_FAILED |
| 3 | voice/delegation_client.py:~242 (`_health`) | MED: a raising health callback hides the degraded/ok state change | `except Exception: pass` | FIXED, DELEG_HEALTH_CB_FAILED |
| 4 | voice/wake_confirm.py:246 (`_warm`) | MED: warm-up failure invisible; first wake confirmation is slow (1.5-5 s per code comment) with no trace | `except Exception: pass` | FIXED, WAKE_CONFIRM_WARM_FAILED |
| 5 | voice/live.py:~680 (`audio.close`) | LOW: audio device close errors invisible | `except Exception: pass` | FIXED (debug level, DEBUG log) |
| 6 | auto_update.py:215, 258 | LOW: feature-state read failure silently picks the default channel / re-prompts | `except Exception: pass` | PROPOSAL (Tier R) |
| 7 | voice/audio.py:303,324; wake_record.py:192 | LOW: cleanup of a vanished device, intentionally masked (commented) | `__exit__`/release in cleanup | OK, no change |
| 8 | mobile.py:112, 419, 1199 | LOW: chmod best effort, malformed audit line skipped, dashboard_seen marker write | OSError / ValueError only | OK; no change |
| 9 | continuity.py | none found: no swallowed exceptions; parse errors surface (`ValueError, TypeError` handled at 130, 388) | grep | OK |

## Logs
- `mobile_audit.jsonl` is bounded (`AUDIT_MAX_BYTES` 512 000, one rotation) and unauthenticated events are rate limited (`audit_noisy`). OK.
- No unbounded log writers found in the audited files.

## Health states
- Delegation exposes ok/degraded through `_health`; findings 2 and 3 close the gaps where that signal could be lost.
- Wake confirmer warm-up has no health state (UNVERIFIED whether the dashboard needs one); logged only.

## Not verified
Real microphone, phone and installed-update behaviour were not tested (hermetic tests only).

## Tests
`core/tests/test_reliability_swallowed_errors.py` (3 hermetic tests for findings 2, 3, 4). Finding 5 is a debug-log-only change, untested.
