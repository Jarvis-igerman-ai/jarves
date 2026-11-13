# Disk footprint per install: growth sources and proposed bounds (JAR-158)

Status: PROPOSAL, report only. No product code changed. Measured with `core/tests/test_disk_footprint.py`
(fake home, injected clock, incompressible data = worst case for zip). Real-install sizes are UNVERIFIED.

## Measured (fake home with 2 MB of user data: 1 MB memory.db + 1 MB workspace file)
40 simulated days: daily auto backup, manual every 10 d, pre-migration (update) every 7 d, pre-restore every 15 d.
Earlier 60-day run: **25 archives, 50.1 MB, 2.00 MB each** (auto=10, manual=6, pre-migration=5, pre-restore=4).
=> backups folder = **~25x the live data size**, steady state reached after ~5 weeks.

## Growth sources (code-read, bound = what stops it growing)
| Source | Where | Bound today | Worst case |
|---|---|---|---|
| Backups `backups/*.zip` | `backup.py` `prune()` | count only: 7 daily + 4 weekly (overlap) + 10 manual + 5 + 5 checkpoints, min 1 per kind | ~31 x data size; **no byte cap** |
| Pilot recordings | `browser_recording.py` | 7 days and 500 MB total | 500 MB (the largest single bound; never in backups) |
| Logs `logs/jarves.log*` | `logs.py` `setup()` | 1 MB x (1 + 5 rotated); `crash.txt` + 1 older at 1 MB | ~8 MB |
| Updates `updates/` | `updates.py` `keep_previous()` | new + one previous installer | 2 x installer size (installer size UNVERIFIED) |
| Hotfix wheels | `hotfix_slice2.py` | per-wheel 600 MB, unpacked 3 GB caps per operation | no retention of old hotfix backups found: UNVERIFIED |
| WhatsApp archive | `whatsapp_archive.py` | opt-in, keep_days setting | depends on setting |
| Chat history/traces | `store.py`, `context_trace.py` | `history.keep_days` | DB size, never shrinks without VACUUM: UNVERIFIED |

## Top findings
1. **Backups are the only unbounded-by-size item that scales with user data.** A 2 GB profile can hold ~60 GB of archives.
2. Every update adds a pre-migration checkpoint (full archive); five are kept regardless of age.
3. `.partial` leftovers are removed only after 1 h and only when a later backup runs.

## Proposed bounds (for owner decision; each is a separate small PR in `backup.py`, not done here to avoid #1016 overlap)
- A: add `backup.max_total_mb` (default: max(2 GB, 5 x newest archive)); prune oldest auto first, then weekly, never the newest of a kind.
- B: before `create()`, check free disk >= 2 x estimated size and skip with a truthful alert otherwise.
- C: lower default checkpoints 5 -> 3 (the previous version is already kept once in `updates/previous`).
- D: dashboard storage card showing per-folder MB (the numbers above), so the owner sees growth.
- E: measure real installs via the diagnostic export (sizes only, no names) before choosing defaults.

Evidence: `PYTHONPATH=core/src python -m pytest core/tests/test_disk_footprint.py -s`.
