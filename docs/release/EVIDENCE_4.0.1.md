# Release evidence for JARVES 4.0.1

Refreshed 27 Oct 2026 on main 313425ae (green) by an agent (Refs #505). Nothing here is a signature: the owner signs, agents never do.

## Phase 1 for the stable (27 Oct 2026, 06:30 UTC, main 86a93667)

- Tested beta: 4.0.1-beta.195, installer 4.0.1.1214 (sha256 a1ec102b3c67a407e912274828a5029fdbf7b5b93200e7347e9721cd4a4fd2c1), commit 313425aeee1f70f11740d8bfd23fa4f5653f9183. main 86a93667 is a descendant with 6 docs-only files changed (#610, #612, #613); beta.196 in `latest-beta.json` is the same installer bytes re-stamped on 86a93667.
- CI for 313425ae: merge-queue run 40445814680, every job green (Python Core Windows 4 shards and Linux 3 shards, real-browser Windows and Linux incl. the setup wizard, Windows installer build + silent install, installer smoke release/ci-smoke x functional/autostart-purge, installer upgrade and rollback, Core-Godot E2E, performance gate). Push run 40447597708 green (docs reuse). Beta publish: jarves-package 40448426810 and 40448529833 (candidate + beta). main 86a93667: jarves-checks 40451587348 green, package 40451679093.
- Owner PC (codes only): installer 4.0.1.1040 + hotfix.4 carrying the Python code of 313425ae (hotfix 20261027-041717, previous 20261026-223500, backup taken first). `jarvis selftest` quick: PASS 10/10 (re-run 06:3x UTC); `jarvis doctor`: All checks passed; CORE_READY 9281 ms; WAKE_MIC_OPEN 920 ms; WAKE_LISTENING about 9 s after ready. The PC did not run the beta installer itself (hotfix path); S1/S5 live update and rollback were not run on it.
- Row verdicts: S1 and S5 proven by automation (CI install, upgrade and rollback on the tested installer tree). S2, S3, S4: logic covered by CI; the human ear/voice check in the room was not run. All five are recorded as accepted by the owner's approval, never as a hand pass.
- Gate dry run (`release_gate.py --tag v4.0.1 --sha 313425ae...`): 13 problems before signing: no verified SHA (given at tag time), open #543 #544 #605, S1-S5 pending, sheet unsigned, Tested beta/Commit/Date missing.
- Workflow dry run 40461847679 (dispatch on main, dry_run=true): checkout and setup-uv pass; "Find the tested installer" refuses with "no signed-off commit in docs/reviews/MANUAL_TEST_4.0.1.md"; fails closed as designed.
- New minor findings: #620 (hotfix restart seen as a crash by the self-check), #621 (beta build numbers out of order).

| Check | State | Evidence |
|---|---|---|
| main green | PASS | jarves-checks 313425ae: success (push run 40447597708) |
| zero open P0-P2 | TODO | `jarvis release status --version 4.0.1 --report`: open #605 (P2), #544 (P2), #543 (P2); the flake showstoppers #546, #547, #555 were closed with run evidence (below). Accepted (known-issue-4.5, not counted by the gate): #510, #509, #441, #261, #179. |
| owner sheet | TODO | rows left for you: S1-S5, then `--finish` (sheet regenerated 27 Oct with the Owner and Rows hash lines; all rows pending) |

## Gate dry run (main 313425ae, 27 Oct)

`python core/tools/release_gate.py --tag v4.0.1 --sha 313425ae...` FAILED with 16 problems, all expected: no verified SHA (given at tag time), open #555, #547, #546 (closed since, see below), #605, #544, #543, S1-S5 pending, sheet not signed, Tested beta, Commit and Date missing. `docs/release/*` legacy bug tables and the checklist are printed as information only (#537). #160 (P1, label v5) and other v5-v9 / enhancement items are skipped by the gate (planned work).

## Release workflow dry run (27 Oct)

`gh workflow run release.yml --ref main -f tag=v4.0.1 -f dry_run=true` (run 40448791298, DRY_RUN=true): checkout and setup-uv succeed (#592/#597 fixed); the step "Find the tested installer" refuses with "no signed-off commit in docs/reviews/MANUAL_TEST_4.0.1.md"; the gate, verify, plan and publish steps are skipped. It fails closed. Note: a dry run with an unsigned sheet therefore stops before the gate step and does not print the full gate list; run `release_gate.py` locally for that.

## Flake showstoppers #546, #547, #555

Un-quarantined by #599 and closed with evidence: the three tests ran in the real-browser jobs and passed on the first try (no retry warning, 87 passed, 0 skipped) on Windows and Linux, in the PR run 40438514266 and the merge-queue run 40443178814. One run per path is thin evidence: reopen on any recurrence.

## Sign-off notes

- `jarvis release signoff --version 4.0.1 --finish --beta <4.0.1-beta.N> --commit <full SHA>` also records the owner (this account, or `--owner NAME`) and prints a hash of every row (and of the sign-off fields). The gate refuses a sheet edited after signing (#508, #602). Write the printed hash down out of band: the hash alone is not authentic (#605).

## PRs merged on 27 Oct that are in this release (main 313425ae)

#531 jarvis wake record; #537 gate slice 3 (legacy tables retired); #541 tag commit may be a docs-only descendant of the signed commit; #560 hotfix safety; #570 skills revoke race; #571, #573, #590 browser pilot hardening; #577, #579, #587 GitHub writes class 2 (off by default); #583 skills state lock; #588 hotfix recover fixes; #591 wake train hard negatives; #593 owner review refresh; #597 release.yml dry-run dispatch (#592); #599 un-quarantine #546 #547 #555; #601 bidi escaping in popups; #602 sign-off owner name and rows hash (#508); #604 V7-M4 Model Lab page. Earlier on 26 Oct: see git log.

## Known issues for the release notes

- #179 (P2) voice start: the greeting audio sometimes never arrives (fix in 4.5.1).
- #441 (P2) flaky owner_answers wait: de-flaked, kept under watch.
- #261 (P2) installer: no recovery when the new program is dead after a real schema bump.
- #509 and #510 (P2) phone web app: UI looks bad, cannot be installed as a PWA (redesign not in this stable).
- #543, #544, #605, #606 release sign-off and secrets hardening (P2/P3, follow-ups of the gate review).
- #598 release.yml: on a tag-ref dispatch the input tag may differ from the ref released.
- #594, #595, #596 wake train: embeddings count, relative gate ceiling, phonetic twins in the held-out set.
- #603, #607 popup/phone plans escaping follow-ups (bidi and zero-width characters).
- CodeQL alerts #46 and #47 (`skills/generated.py`) open; voice idle-silence disconnect (doctor WARN) and wake items.
- #505 tracks the 4.5.1 follow-ups.

## Live checks on the owner PC (earlier draft, installed 4.0.1-beta.158+hotfix.2)

Backup taken first: `C:\Users\97254\AppData\Local\jarves\backups\jarves-backup-20261026-204516-manual.zip` (verified). All runs silent.

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
| hotfix | pass (bug #515 since fixed by PR #521) | `hotfix status`: hotfix.2 applied (id 20261026-174409), 2 build-only files listed as skipped. `hotfix --dry-run ... apply` lists 20 files and changes nothing. `hotfix revert` REALLY reverted in both `revert --dry-run` and `--dry-run revert` forms (flag ignored, issue #515); the agent re-applied hotfix.2 right away (id 20261026-184854, `--no-restart`) and confirmed `--version` = 4.0.1-beta.158+hotfix.2 and doctor green. So revert and re-apply both work; the dry-run flag for revert is the bug. |
| phone listener + pairing | pass | `jarvis phone status`: phone access on, 1 paired phone, phone approvals off, listening on the home network, Tailscale not installed. |
| test phone 87a316d4 via adb | blocked (agent) | the default adb server hung (`adb devices` printed nothing within 40 s, three adb.exe processes already running; none killed by the agent). A second server on port 5099 saw no device. No screenshots taken. Phone rows stay owner-needed (issues #509, #510). |

