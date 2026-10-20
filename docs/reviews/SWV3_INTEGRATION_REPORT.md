# SW-V3 integration report: `v3-final` (issue #17)

Date: 20 Oct 2026. Integrator branch: `v3-final` (local work branch `v3-final-work`, pushed with
`git push origin HEAD:v3-final`, never forced). GitHub Actions has no minutes left, so everything below ran
**locally** on the owner's Windows 11 PC (Python 3.12.12, the main checkout's `core/.venv`, code from this worktree via
`PYTHONPATH`).

## 1. What is merged (`git merge --no-ff`, both sides kept on every conflict)

Already on `v3-final` from the previous run: v3-p02c-multilang, v3-p1-basics, v3-p1-browser, v3-no-skips,
v3-p03-perf, v3-docs, v3-profiles, v3-selfdiag.

Merged in this run:

| Branch | Conflicts and how they were resolved |
|---|---|
| v3-fix-show-twice | `runtime/service.py` `window_closed`: kept both `viewer.expect_exit()` and the tray notice |
| v3-approvals | `app.py` (profiles.attach + TrayChannel + register_helper, one `_write_tools_md`), `cli.py` (profile + approvals commands), `dashboard.py` PLUGIN_MODULES (profiles, browser, approvals, helper), `index.html` (browser/profiles/selfcheck + approvals scripts; Approvals nav entry in the English-default nav), `setup_wizard.py` (ADDON_MODULES, SAFE_OFF incl. browser + helper, WIZARD_HIDDEN, GROUPS profiles + helper, BUNDLE profiles + helper) |
| v3-ci-yaml-fix | none; plus a new test (below) |
| v3-wake | `strip.js`: the wake-word line now uses `tx()` like the rest of the multi-language strip |
| v3-connect | clean |
| v3-campaign-dash | `index.html` favicon: took the campaign's SVG icon |
| v3-campaign-features | clean |
| v3-campaign-voice (two rounds) | clean |

## 2. Bugs found and fixed during integration

| # | Sev | Found by | Bug | Fix |
|---|---|---|---|---|
| 1 | P1 | coordinator | `.github/workflows/jarves-checks.yml` invalid YAML (unquoted step name with a colon): GitHub rejected the whole workflow | merged v3-ci-yaml-fix; new `core/tests/test_ci_workflows.py` parses every workflow with `yaml.safe_load` and checks jobs/steps shape (pyyaml added to the dev group, uv.lock updated, so it never skips) |
| 2 | P1 | full suite run 1 | after v3-approvals, `test_voice_switch_needs_on_screen_approval` timed out: an identical request denied seconds ago is denied without asking (by design, anti-nagging) until the owner speaks again | test now models the owner's new turn (`approvals.note_user_turn()`); verified the Core calls it on every user turn |
| 3 | P1 | test_runtime after v3-connect | the once-a-day "Google not connected" reminder was raised with channel `speech`: JARVES **spoke out of nowhere** at background start while hidden and idle (contradicted its own docstring) | `maybe_nudge(channel=...)`; operations passes `speech` only while a live conversation is open, else a quiet popup |
| 4 | P2 | headless Edge page sweep | the dashboard HTTP server used socketserver's default listen backlog of 5: page loads in Edge got random `ERR_CONNECTION_REFUSED` (`/api/privacy`, backup) in 2 of 3 sweeps | `_Server.request_queue_size = 128` (+ regression test); 0 of 3 sweeps after |
| 5 | P2 | locale test after merges | `approvals.js` was Hebrew/English only (no Arabic/Russian), the wake strip line was not in the catalogs | `approvals.js` uses `tx()`; 20 keys added to en/he/ar/ru |
| 6 | P2 | test_hebrew_texts | 23 feature texts without Hebrew (instagram, self_check, timers, lists, browser, helper, helper_readonly_auto) | added to `hebrew.py` TEXT |
| 7 | P3 | headless Edge | `favicon.ico` 404 console error | inline icon (the campaign's SVG won the merge) |
| 8 | P3 | `jarvis setup --check-addons` | dashboard bundle check did not cover the shipped ar/ru catalogs | added |
| 9 | P3 | suite_system S09 | the startup bench's dummy key `sk-bench-...` matched the repo secret scan | renamed `sk-test-bench-...` (fakes are skipped by the scan) |
| 10 | P3 | suite_system V06 | lateness of the due job was measured from its due time although the harness keeps the scheduler switched off for 35 s across it (34.0 s vs the 35 s limit) | measured from max(due, scheduler on again) |

Also found and confirmed already fixed by a merged branch: suite_system V06/V07 failed on the first run because
dashboard POSTs were rate-limited (429) by the static-file load of the page; v3-campaign-dash exempts static GETs.

## 3. Offline test suite (from `core/`, one pytest process at a time)

Command: `C:\work\jarvis\core\.venv\Scripts\python.exe -m pytest tests -o pythonpath=src -p no:cacheprovider`
(`PYTHONPATH=<worktree>/core/src`). The conftest no-skips hook fails any skipped/xfailed test; the 2 deselected tests
are `linux_only` (deselected, not skipped, on Windows).

| Run | Code | Result |
|---|---|---|
| exploratory 1 | after the first 4 merges | 1 failed (bug 2), 1399 passed |
| exploratory 2 | after the fix | 1400 passed, 0 skipped |
| A1-A3 | `6ce2984` (all merges incl. campaign-voice round 1) | 1453 passed x3, 0 skipped, 0 failed (564 s, 573 s, 577 s) |
| **final 1-3** | `29b751c` (+ campaign-voice round 2) | FINAL_RESULTS |

## 4. Real-system checks (no accounts, no sound, no paid calls)

| Check | Result |
|---|---|
| `tools/suite_system.py --skip-pytest --no-plan --soak-min 3` | **20/20 PASS** ([report](PRE_USE_TEST_REPORT_SYSTEM.md)): Godot 4.7.2 headless viewer + face self-tests, real face survives malformed protocol, doctor, 12 CLI commands, persistence, bad configs, Core start/stop x5 (ready 1.5-1.8 s, port freed), diag masking, log rotation, 3-min wake-word soak (411 MB flat, peak CPU 52 %), V01-V08 privacy strip / features / camera preview / calendar guardrails / scheduler incl. restart catch-up, secret scan 30 668 files 0 hits. First run was 16/20 (bugs 3, 9, 10 + the 429 above) |
| `silent_live.py` | not run: it needs the owner's OpenAI key and costs money (the brief asked for checks needing no accounts) |
| `jarvis setup --check-addons` | ADDON CHECK: PASS (every feature incl. helper, approvals, profiles, browser, guided_setup) |
| real Core 5x on a **TEST profile** (throw-away base dir, `jarvis profile create test`, `jarvis --profile test start/status/stop`) | 5/5 PASS: start 4.3-7.3 s, CORE_READY 5, CORE_STOPPED 5, port 8767 freed each time, 0 ERROR log lines. `status` exits 3 = "degraded", which is correct for the offline test profile (voice off, speech_out degraded) |
| dashboard, every nav page (32) x en/he/ar in headless Edge (`tools/v3_all_pages_check.py`, new) | 96/96 page loads clean x3 sweeps: no JS errors, no console errors, no HTTP >= 400, no failed requests, correct `lang`/`dir`, no horizontal scroll, non-empty page |

## 5. Open points for the owner (also in HANDOFF)

1. On a fresh TEST profile, Core health lists `helper: ok` while browser/telegram/... show `disabled`; helper is in SAFE_OFF, so the health row probably reflects "module loaded" rather than the switch. Not investigated further (P3, display only).
2. v3-campaign-voice was marked "in progress" by its author; two rounds are merged. Later commits need another merge.
3. Live checks that need accounts (silent_live, Google, Telegram, Instagram, Home Assistant) remain for the owner.
