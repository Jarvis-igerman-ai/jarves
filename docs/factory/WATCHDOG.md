# Factory watchdog

Step 1 of the software factory: one script, `core/tools/factory_watchdog.py` (standard library + `gh`). One run is one pass; safe every ~20 minutes. Process context: [LEAN_PROCESS_V2](../plans/LEAN_PROCESS_V2_2026-10-27.md).

## Run
```
cd core
python tools/factory_watchdog.py                 # dry-run (default): prints actions, writes nothing
python tools/factory_watchdog.py --apply         # performs the two safe actions, updates STATE.md
python tools/factory_watchdog.py --repo OWNER/NAME --state-file PATH
```
Output is three lines (DONE / NEXT / NEEDS). Exit 0 = fine, 1 = needs attention (any alert, or `gh` unreadable).

## What it does
- Reads open PRs with checks and queue status, the last run on main, open issue counts by P0-P3, the newest beta in `igalgerman/jarvis-releases`.
- Acts (only with `--apply`): re-queues a Ready green PR that dropped from the queue (`gh pr merge N --auto`; skips PRs labelled `no-automerge`); toggles the `no-automerge` label (remove + add) when the required "Auto-label policy" check is missing on a green PR.
- Reports only: main red, a PR red for more than 2 h, a PR stuck BLOCKED, open P0-P2 above 5, no beta newer than the latest green main after 60 min, the same PR re-queued 3 times.
- Writes `C:\Users\97254\.claude\projects\C--work-jarvis\STATE.md` (outside the repo): a "now" block at the top (queue, red PRs, alerts) and one metrics row per day. Re-queue counts live in `STATE.watchdog.json` beside it.

## Schedule
Windows Task Scheduler (every 20 minutes):
```
schtasks /Create /SC MINUTE /MO 20 /TN "JarvisFactoryWatchdog" /TR "cmd /c cd /d C:\work\jarvis\core && set PYTHONPATH=src && .venv\Scripts\python.exe tools\factory_watchdog.py --apply"
```
Claude alternative: a cron routine (the `schedule` skill, `*/20 * * * *`) or `/loop 20m` running the same command, then reading the 3-line result.
Use a checkout of `main` for the script, and run the first passes without `--apply`.

## What it never does
Merge, close, reopen, push, rerun CI, edit issues, touch tags, releases, settings or secrets. Every `gh` call passes an allow-list (read-only list calls plus the two actions above); anything else raises before it runs. Tests: `core/tests/test_factory_watchdog.py`.
