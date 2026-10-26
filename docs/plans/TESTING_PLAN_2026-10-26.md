# Testing plan (26 Oct 2026)

Owner request: "testing". One page: what is tested automatically, what only the owner can test, flake policy, release gates, and the gaps that still need tests. Facts come from `.github/workflows/`, `core/tools/` and [CI_DETERMINISM](../CI_DETERMINISM.md); this plan adds no new rule. Agents run targeted tests locally; CI gives the full evidence.

## 1. What is tested automatically today

Local setup used below: `cd core`, Python `.venv\Scripts\python.exe`, `PYTHONPATH=src`. "Hermetic" means no real network (`core/tools/netguard.py`), `JARVES_TEST_OFFLINE=1`, event-driven waits. PR #381 (hermetic backend-prompt budget, same number locally and in CI) is still **open**: until it merges, the prompt-size check can differ between a local run and CI. Re-check after it merges.

| Area | What it covers | Local command | CI job / workflow |
|---|---|---|---|
| Fast gate | log lint, guides + CLI parity, Hebrew/locales, docs links + DOC_MAP, prompt-size budget, workflow contracts, network guard, dashboard endpoint timing | `python core/tools/prepush.py` (`--fast`, `--changed-only`, `--only dash`) | `validate` in `jarves-checks.yml` |
| Unit / integration (pytest) | `core/tests`, offline, any skip fails | targeted: `python -m pytest tests/test_x.py -q`; failure retried once: `python tools/pytest_ci.py` | `core` (Linux + Windows, planned shards, xdist, slowest first); `durations` feeds the next shard plan |
| Real-browser a11y / e2e | Playwright: `test_dashboard_a11y`, `test_wizard_browser`, `test_browser`, `test_chat_markdown_browser`, `test_dashboard_mic_stop_browser`, `test_dashboard_phone*`, `test_mobile_phone_e2e` | `python -m pytest tests/test_dashboard_a11y.py -q` (needs Chromium or Edge) | `browser` (Chromium on Linux, preinstalled Edge on Windows; one retry; skips fail) |
| Core-Godot E2E | real Core process + real Godot 4.7.2 face over the link | `python -m pytest tests/test_face_protocol_godot.py tests/test_v6_scenario_e2e.py -q` | `e2e` (Linux, real processes), `godot` |
| Installer smoke | silent install, run, upgrade, uninstall, autostart, default tasks, size/time budget | `packaging\smoke_windows.ps1 -Installer dist\JARVES-Setup-<ver>.exe -Version <ver> -RequireFace -CheckAutostart -DefaultTasks -Scenario <s>` (Windows) | `installer`, `installer-smoke`, `installer-upgrade` (upgrade matrix, `upgrade_fixtures.json`) |
| Security scans | CodeQL, dependency/secret/audit scans, Dependabot gate, redaction audit | `python core/tools/security_scans.py`, `python core/tools/redaction_audit.py`, `tests/test_security*.py` | `codeql.yml` (push, PR, schedule), `security-scans.yml` (schedule), nightly `audits` |
| Phone e2e (no phone) | headless browser as the phone, HTTP harness, resume, switch | `python -m pytest tests/test_mobile_phone_e2e.py tests/test_dashboard_phone_switch.py tests/test_v5_phone_resume.py -q` | `browser` job; fake bridge `tools/fake_whatsapp_bridge.py`; `whatsapp-bridge` job |
| Voice latency without a mic | `silent_live.py`: synthesised questions injected through a virtual mic into the real GPT-Live, virtual speaker, dead air measured. **Costs money** (about $0.05 per voice minute + tokens) | `python tools/silent_live.py --turns 1,2,3` (approved caps only) | not in CI. Free siblings: `silent_help.py`, `silent_memory.py`, `silent_tasks.py`, `say_roundtrip.py` |
| selftest / speedtest / doctor | self-diagnosis, timing, microphone/wake/keys check | `jarvis selftest`, `jarvis speedtest`, `jarvis doctor` | findings triaged per release; `perf` job and nightly `perf` (3 runs, Windows, `perf_gate.py`) |
| Synthetic canaries | voice stack, music fake, calendar fake, dashboard, installer, scheduler journeys | `python core/tools/canary.py` (`test_canary.py`) | nightly `audits` |
| Nightly health | full suite on both OS, 3x flake detector, perf gate, audits and canaries, flake scan | n/a | `nightly.yml`; `main-health.yml` reruns a red main once and alerts; `package.yml` builds candidate and beta |
| Merge gate | all required jobs summarised; red or pending never merges | n/a | `ci-ok` and the `failure-summary` comment |

## 2. What only the owner can test

| # | Test | Why agents cannot | Agent approximation (and how) |
|---|---|---|---|
| 1 | Real voice in the room: wake word, barge-in, accents, noise | needs a real mic, speakers, the owner's voice | `silent_live.py` (dead air, in-answer pauses, tools; no speakers) and `say_roundtrip.py`; owner approves spend |
| 2 | Voice quality, naturalness, Hebrew pronunciation | subjective, by ear | none; only latency and text can be checked |
| 3 | Camera: face detect, privacy LED, camera UX | physical camera, lighting | fake camera frames in tests; screenshots via the `*_screens.py` tools |
| 4 | Real Android phone: pairing, Chrome UI, Hebrew layout, background/resume | owner device, owner PIN | adb sheet as in [PHONE_SHEET_RESULT_2026-10-25](../reviews/PHONE_SHEET_RESULT_2026-10-25.md) (adb screenshots and taps only, no PIN or settings, `mobile_access` turned off afterwards); emulated phone e2e in CI for logic |
| 5 | Phone in the car, Android Auto, Bluetooth, mic echo | car, hardware | none yet ([Android Auto feasibility](V6_ANDROID_AUTO_FEASIBILITY_2026-10-25.md)); protocol-level fakes only |
| 6 | Robot hardware: face display, servos, print and assembly | physical parts | Godot face E2E and CAD/Blender checks in CI; hardware stays a hard stop |
| 7 | Real WhatsApp / Google / Telegram accounts | owner accounts; external sends are a hard stop | fake bridge, fake calendar, canaries |
| 8 | Install the beta on the owner PC (backup first) and sign the stable sheet | owner PC, signature | agents prepare the sheet (`manual_test_sheet.py`) and smoke the beta; owner signs |

Everything owner-only goes to `OWNER_OPENS.md` with a safe default; the pipeline never waits for it.

## 3. Flake policy

1. **Rerun-once rule.** CI reruns only the failed files once (`ci_flake.py`; browser job one retry). Pass on retry is green but recorded in `flake-report.json`. Fail twice is real: read the log, do not rerun again.
2. **Quarantine.** A test that fails and then passes on the same commit is paused at once with one entry in `core/tests/quarantine.toml` (`id`, `issue`, `since`, `reason`; an entry without an issue fails collection). It is deselected (never skipped) in blocking jobs and still runs in the non-blocking "Quarantined tests" step.
3. **Showstopper rule.** Each entry has an open issue labelled `showstopper` + `P1`; it is fixed before new feature work in that area; the fix PR deletes the entry and closes the issue. `flake_scan.py` (nightly) opens issues from run history.
4. Never merge with pending, failed or missing checks; never bypass.
5. **Known flake candidates now:** Windows real-browser job, issue #367 (different tests fail: `test_mobile_phone_e2e` "Plan paused", `test_canary` dashboard_load 50 s over 30 s); `test_say_engine::test_default_output_is_one_line_without_espeak_on_windows`, issue #422 (extra "unhandled exception" line, Windows shard 3/4). Both are P2 candidates: if either reproduces on the same commit, quarantine it with a showstopper issue.

## 4. Release gates per version

| Version | Gate | Evidence |
|---|---|---|
| 4.0.1 stable | all rows of `RELEASE_CHECKLIST_4.0.1.md` done/n/a/accepted; no open P0-P2, P3 listed with reason; **latest nightly run on `main` green (no five-nights rule, owner 26 Oct)**; owner-**signed** `MANUAL_TEST_4.0.1.md` (`jarvis release signoff ... --finish`); `release_gate.py` dry run clean | [RELEASING](../RELEASING.md) "Stable release, step by step" |
| 5.0 | **stays beta** until the owner's real-phone sheet ([SWV5_ACCEPTANCE](../reviews/SWV5_ACCEPTANCE.md)) is signed; all CI gates green, installer upgrade/rollback smoke, bug table triaged; then the same stable steps as 4.0.1 | `RELEASE_CHECKLIST_5.0.0.md`, signed sheet |
| 6.0 | **stays beta** until the owner's phone and car sheets are signed (voice in the car, Android Auto path); V5 must already be stable | V6 sheets, [V6 plan](V6_INVENTORY_AND_PLAN_2026-10-25.md) |
| V7 | tracks only (proposal, issue #50): no gate until authorized; each track writes its test list before code | [V7 proposal](V7_PROPOSAL_2026-10-21.md) |

Every version: the beta channel stays current from green main; stable needs the signed sheet; hard stops stay hard (paid API beyond caps, hardware, external sends, no stable without sign-off).

## 5. Gaps found this week that need tests

Done this week: https POST origin check (test added), WhatsApp display (test added). Still missing, each opened as a GitHub issue labelled `testing`, P3, agent-doable:

| # | Proposed issue title | Test shape |
|---|---|---|
| 1 | tests: real-browser test for the bug-reports popover (open, submit, close; Hebrew and English) | Playwright in the `browser` job |
| 2 | tests: Windows toast fallback when the tray/popup cannot show (Tcl init error, no desktop) | unit test with a fake Tk/toast backend; Windows shard |
| 3 | tests: installer restart smoke, silent install restores the pre-install state without a flag (#383) | new scenario in `smoke_windows.ps1` / `installer-smoke` |
| 4 | tests: hotfix apply on a real source tree (copy, apply, verify, rollback) | extends `test_hotfix_install.py` with a temp tree |
| 5 | tests: phone pairing switch end-to-end (pair phone A, switch to B, A rejected) | extends `test_dashboard_phone_switch.py` in the browser job |

Order of value: 3, 1, 5, 4, 2. Each new test follows the CI determinism rules and ships without skips.
