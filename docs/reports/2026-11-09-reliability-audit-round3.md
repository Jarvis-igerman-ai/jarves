# Reliability audit round 3 (JAR-103)

Scope: `mobile.py`, `health.py`, `mobile_drive.py`, skills package, `continuity.py`. Follows #990 (voice) and #1004 (auto_update).

## Fixed (this PR)
| Where | Was | Now |
|---|---|---|
| `health.Health.save` | `except OSError: pass` - a failed write left a stale file that readers call "off", no reason anywhere | `HEALTH_SNAPSHOT_WRITE_FAILED` warn, once per outage |
| `mobile.MobileAccess.replay` | `except Exception: return None` - a failed lookup silently let a phone message run twice | `MOBILE_REPLAY_LOOKUP_FAILED` |
| `mobile` desktop_present | `except Exception: return False` | `MOBILE_PRESENCE_UNKNOWN` |
| `mobile.health_view` tests | `except Exception: pass` hid every fault (and one bad entry hid all) | reads `History.load()["latest"]` only; a missing/unparseable file is already an empty history there, so "no tests yet" never raises; a wrong-shaped file or entry is `MOBILE_TEST_HISTORY_FAILED` once per outage and hides only itself; perf_advisor faults no longer count |

Logs carry only `error_type` (no message text, no secrets). Test: `core/tests/test_reliability_r3_logs.py`.

## Left alone (deliberate)
- `mobile.write_private` chmod `OSError: pass` (Windows has no POSIX modes; file is already created 0600).
- `mobile.py` fail-closed switch, cert SAN check, `mobile_drive._on/passenger` default to safe values (fail closed/"ask"); a log there would repeat per request - candidate for a rate-limited helper.
- `selfspeed.History.load` treats an unparseable file as empty (selfspeed's own contract, not changed here).
- Reads that return `[]`/`None` on `OSError` for missing files (normal first-run).

## Not touched (open PRs)
`continuity.py` (#981), `skills/generated.py` (#982), `mobile_prefs.py` (#1005). No separate "skills runner" module exists; `skills/*` not audited beyond those.

## Health truthfulness
`components_view`/`snapshot` report component states as set; no state is inferred from a failed write. UNVERIFIED: real-device phone behaviour.
