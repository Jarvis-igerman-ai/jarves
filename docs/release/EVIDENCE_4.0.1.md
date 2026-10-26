# Evidence for stable 4.0.1 (agent-collected, DRAFT)

Collected 26 Oct 2026 (Israel evening) by an agent on the owner's PC. Installed build: `JARVES 4.0.1-beta.158+hotfix.2` (commit dd3e24eda9f7, channel main). Nothing here is a signature. The owner signs; agents never do. Refs #505.
All runs were silent: no greeting, no sound, no popup, no focus change. A backup was taken first: `C:\Users\97254\AppData\Local\jarves\backups\jarves-backup-20261026-204516-manual.zip` (verified).

Result key: `pass` (evidence shown), `fail`, `owner-needed` (needs his eyes/ears or his phone, exact step in OWNER_REVIEW_4.0.1.md), `ci-proven` (evidence is a green CI run).

## Smoke rows (design S1-S5)

| Row | What | Result | Evidence |
|---|---|---|---|
| S1 | update + `jarvis doctor` no red line | pass (doctor) / WARN (update) | `jarvis doctor`: "All checks passed", one WARN: conversation closed after silence 180 s x3 (voice.idle_disconnect_s). `jarvis update status`: up to date, but the LAST install attempt shows `[5/7] Failed at: Installing the new version (UPDATE_NOT_APPLIED), previous version still running, nothing lost`. The owner runs `jarvis update --channel beta` once himself (S1). |
| S2 | wake + Hebrew time answer | owner-needed | needs ears. Headless parts: wake model `hey_jarvis` loaded (doctor), microphone hears sound (peak 0.0871). Core was not running during doctor. |
| S3 | interrupt stops fast | owner-needed | needs ears. CI barge-in tests are in the Python Core run below. |
| S4 | medium-risk popup + "yes" approves once | owner-needed (logic ci-proven) | approval popup tests (`test_approval_one_popup`, `test_approval_popup_ux`, `test_helper_approvals`) are in the green Python Core run 40366591081. |
| S5 | update + rollback keep the data | ci-proven (not run live) | CI job "Installer upgrade and rollback" success in run 40366591081. Live rollback not run by the agent: it restarts the owner's JARVES; the owner does it (1 step). |

## Other rows an agent could exercise

| Check | Result | Evidence |
|---|---|---|
| `jarvis selftest` (quick, 7.3 s) | pass, 1 warning | 10 checks: config, DB integrity, permissions, 28 feature probes ok, "101 features, 96 guides, parity tables in step", dashboard EN 2244 / HE 2244 complete, log privacy clean, disk 12 GB. WARN: daily limits line shows the 3 idle-silence disconnects. |
| `jarvis speedtest --class owner --no-store` | WARN (not fail) | spawn_to_ready 9.45 s, core_start 9734 ms, wake_to_first_audio 10.29 s, dead_air_p95 5.89 s, openai roundtrip 809 ms, installer 186.3 MB. WARNs vs only 1-2 earlier runs: live_started_s 148.7, tool_p95 5531 ms, dashboard_first_load 144 ms (few samples, not a regression proof). No budget exceeded. |
| installer upgrade/rollback, installer smoke (ci-smoke + release, functional + autostart-purge), Windows installer build + silent install | ci-proven | merge-queue run https://github.com/Jarvis-igerman-ai/jarves/actions/runs/40366591081 (the tree that became main dd3e24ed): all success |
| setup wizard in a real browser | ci-proven | "Real-browser tests" (windows-2025 and ubuntu-24.04) success in the same run; tests `test_setup_wizard`, `test_browser`, `test_feature_setup` |
| approvals logic | ci-proven | same run, Python Core tests windows shards 1-4 and ubuntu shards 1-3, all success |
| guides EN/HE | ci-proven + live | `test_guides`, `test_cli_parity` in the same run; live selftest: 96 guides, parity in step, dashboard EN/HE 2244/2244 |
| main green | pass | `jarves-checks` on dd3e24ed: https://github.com/Jarvis-igerman-ai/jarves/actions/runs/40369315011 (push to main; heavy jobs skipped as docs-only reuse of 40366591081); package + main-health success: runs 40369506127, 40369505787 |
| features list sanity | pass | `jarvis features list`: 101 features, each with state/risk/stage label (stable/beta); `features beta status`: beta features ON (60/79 on). |
| hotfix | pass, with a bug | `hotfix status`: hotfix.2 applied (id 20261026-174409), 2 build-only files listed as skipped. `hotfix --dry-run ... apply` lists 20 files and changes nothing. `hotfix revert` REALLY reverted in both `revert --dry-run` and `--dry-run revert` forms (flag ignored, issue #515); the agent re-applied hotfix.2 right away (id 20261026-184854, `--no-restart`) and confirmed `--version` = 4.0.1-beta.158+hotfix.2 and doctor green. So revert and re-apply both work; the dry-run flag for revert is the bug. |
| phone listener + pairing | pass | `jarvis phone status`: phone access on, 1 paired phone, phone approvals off, listening on the home network, Tailscale not installed. |
| test phone 87a316d4 via adb | blocked (agent) | the default adb server hung (`adb devices` printed nothing within 40 s, three adb.exe processes already running; none killed by the agent). A second server on port 5099 saw no device. No screenshots taken. Phone rows stay owner-needed (issues #509, #510). |
| `jarvis release status --version 4.0.1` (branch simple-rel-code) | run | PASS main green. TODO zero open P0-P2: #503 P1, #515 P2, #510 P2, #509 P2, #179 P2. TODO owner sheet. (No nightly check by design.) |

## Latest nightly

26 Oct 07:39 UTC run 40287134158: failure (flake #503, test_phone_location clock, fix PR #507 open). Previous nights 25 Oct (40172952844) and 24 Oct (40072825349): success. PR #506 drops the nightly rule from the gate; it is not in main yet.

## Known issues for the release notes

- #179 (P2) voice start: greeting audio sometimes never arrives (1 of 5 live starts); Core continues without a greeting.
- #441 (P2) flaky tests: owner_answers dashboard wait (Windows shard 3/4) and whatsapp_lid_alias just after 00:00 UTC.
- #261 (P2) installer: no recovery when the new program is dead after a real schema bump.
- CodeQL alerts #46 and #47: `py/clear-text-storage-sensitive-data`, both open.
- Phone web app: #509 UI looks bad on the paired phone, #510 cannot be installed as PWA (both P2, label phone).
- #503 (P1 showstopper) nightly flake test_phone_location (fix PR #507); #515 hotfix revert ignores --dry-run (P2).
- Voice/wake: no open issue carries the label `voice` at this time; the voice items are #179 and the doctor WARN about 180 s silence disconnects (x3 today).
- #505 tracks the 4.5.1 follow-ups.

## Gate state (honest)

Open P0-P2 today: #503, #515, #510, #509, #179 (plus #441 and #261, labelled P2). The gate demands zero, so stable cannot be tagged until those are fixed or the owner decides otherwise. This file does not change that.
