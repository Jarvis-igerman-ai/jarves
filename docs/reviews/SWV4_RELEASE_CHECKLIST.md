# SW-V4 release checklist status (release candidate preparation)

Source: [JARVES_SW_V4_WORK_PACKAGE.md section 10](../JARVES_SW_V4_WORK_PACKAGE.md#10-release-checklist). Branch `release/v4-prep` = `main` (V4 integrated, PR #11) + `v4/integrated-h-l` (dialing, installer; PR #26) + `test/v4-campaign-c2` (voice fixes; PR #28) + `v4/pkg-n-docs` (docs). Date: 21 Oct 2026. Nothing was published, tagged or merged; `igalgerman/jarvis-releases` was not touched.

Legend: **done** = finished in code/docs here, evidence given. **needs-owner** = cannot be finished by code; the owner action is one line.

| # | Item | Status | Evidence / exact owner action |
|---|---|---|---|
| 1 | `jarves-checks` green on the exact SHA | needs-owner | Owner: after the draft PR is ready and merged, open the `jarves-checks` run of the final main SHA, confirm all jobs green and paste the run URL here. |
| 2 | Nightly green 5 nights | needs-owner | Owner: wait for 5 green `nightly` runs on main after the merge, then record the URLs here. |
| 3 | Offline suite 3x identical | needs-owner | Run 1 of 3 done here: 2402 passed, 6 failed, 7 deselected (52 min; see "Gates run here"), 0 skipped. Owner: run `pytest -q` in `core` two more times on the final SHA (same numbers, 0 skipped/failed). |
| 4 | `suite_system.py` all PASS | needs-owner | Owner: `uv run --no-sync python tools/suite_system.py --skip-pytest --soak-min 10` in `core` on the final SHA (needs a real Core, Godot and 10 min of soak); report goes to `docs/reviews/PRE_USE_TEST_REPORT_SYSTEM.md`. |
| 5 | Voice suites 1-96 PASS, cost report | needs-owner | Owner: top up the OpenAI balance (it ran out 20 Oct) and say OD-05 yes/no (scenario 62), then run `python tools/suite_v3_voice.py --only 1-96` and `--cost-report`. Last campaign: 89 PASS, 2 FAIL (22, 23 wake near-misses, P3), 1 BLOCKED ([SWV4_CAMPAIGN_VOICE](SWV4_CAMPAIGN_VOICE.md)). |
| 6 | Dashboard sweep x3, axe 0 serious, owner review | needs-owner | Owner: run `python tools/all_pages_check.py` three times (headless browser) and do the hands-on dashboard review (OL-01..OL-08). K report: [SWV4_K_DASHBOARD](SWV4_K_DASHBOARD.md). |
| 7 | Redaction audit 0 hits (both modes); repo and snapshot secret scan 0 hits | needs-owner | Redaction audit exists (`core/tools/redaction_audit.py`, last result 0 leaks, [SWV4_B_PRIVACY](SWV4_B_PRIVACY.md)). Owner: re-run it on the final SHA and run the secret scan on the dry-run snapshot (item 16). |
| 8 | Performance gate on CI and on the owner PC | needs-owner | CI-class gate PASS here (`perf_gate.py --class ci`, all metrics ok, idle disk write 4.8 KB/min). Owner: run `python tools/perf_gate.py --class owner` on the owner PC. |
| 9 | Installer: clean machine, upgrade from 0.1.0.x and 3.0.0.x, rollback, signing (OD-08) | needs-owner | Done in code: version 4.0.0, bridge pin (item 17 evidence), payload check with the pin PASS, build scripts parse-clean, unsigned decision recorded. Owner: on a second PC run INSTALL_TEST rows, pin real 0.1.0.x and 3.0.0.x installers (URL + SHA256) in `packaging/upgrade_fixtures.json`, run `packaging/upgrade_matrix.ps1`, and confirm OD-08 (stay unsigned). |
| 10 | Failure-injection suite PASS | needs-owner | Owner: `python tools/failure_injection.py --all` in `core` (the tool is not in this tree yet, so it is first built by package M/P; sleep/resume needs the owner PC). |
| 11 | Owner passes (section 7 rows) | needs-owner | Owner: work through the section 7 owner rows (OA, OL, OD-T lists) in the two planned sittings and record PASS or accepted BLOCKED. |
| 12 | V4 bug table: no open P0-P2 | needs-owner | No P0. Open P2: V4-SC-1 (crash self-check stops the Core), V4-RP-1 and V4-RP-2 (repo names, owner decision); V4-VC-2 and V4-VC-3 are fixed, awaiting a live retest (OpenAI credit). Owner: decide RP-1/RP-2, fund the retest. See [SWV4_CAMPAIGN_BUG_TABLE](SWV4_CAMPAIGN_BUG_TABLE.md), [voice table](SWV4_CAMPAIGN_BUG_TABLE_VOICE.md). |
| 13 | Capability matrix updated | done | `core/README.md` capability table now has the assisted dialing row and the installer/rollback pointer; Windows status per feature as in release notes. Pi rows stay `planned` (unchanged). |
| 14 | Docs/guides refreshed, release notes, HANDOFF, decision queue | done (decision queue: needs-owner) | `docs/RELEASE_NOTES_4.0.md`, `CHANGELOG.md` (4.0.0 candidate), user guide en/he (stale "not in every build" text removed), reviews tables (this file). Owner: review the open decisions listed in the release notes (OD-12, OD-20..37). |
| 15 | Issues #14 #15 #16 #24 #25 #28 #49 and #17 closed with evidence | needs-owner | Owner: after items 1-11 pass, close each issue with its evidence link (the issue tracker is outside this branch). |
| 16 | Public snapshot dry-run reviewed, then pushed | needs-owner | Owner: `python scripts/make_public_snapshot.py --dry-run` (script lives in the private repo), review the file list, then push. Not run here. |
| 17 | Tag `v4.0.0`, installer published with SHA-256, manifest signed | needs-owner | Dry run done (no publish): see "Dry runs" below. Owner: set secrets `RELEASES_TOKEN` and `JARVES_UPDATE_SIGNING_KEY`, then tag `v4.0.0` after items 1-16. |

## What changed in code for the release

* Version 4.0.0: `core/pyproject.toml`, `core/src/jarves/__init__.py`, `core/uv.lock`. Installer builds append the run number (4.0.0.N), which sorts above 3.0.0.x and 0.1.0.x.
* `packaging/bridge_pin.json` pins the WhatsApp bridge: `bridge/whatsapp_bridge.exe`, SHA-256 `15706e167c489f3b98dd7d09d06a3c9aabe3e61deb5e771c15b37d9af0f1de73` (Go 1.27.0, windows/amd64, `-Version 0.1.0`, `-trimpath`). Rebuilt twice with `scripts/build_whatsapp_bridge.ps1 -Verify`: REPRODUCIBLE.
* Because the bridge version is baked into the binary, CI now builds with the fixed `-Version 0.1.0` (it used the run number, which would change the hash every run) and compares the rebuilt hash with the pin (new step in `jarves-checks.yml`). To change the bridge: rebuild, update `sha256` in the pin.
* `packaging/build_windows.ps1`: with a pin, stages `JARVES_BRIDGE_EXE` (or the local `dist` build) plus its `.sha256` sidecar, and skips the unpinned fallback. `packaging/payload_check.py`: allows the pinned file's `.sha256` sidecar.

## Dry runs (nothing published)

* Payload check on a stage holding the pinned bridge: `BRIDGE: bridge/whatsapp_bridge.exe sha256 verified`, `PAYLOAD CHECK: PASS (0 finding(s))`.
* Update manifest: `release.py verify` on a fake dist (4.0.0.1) OK; manifest built with `build_manifest`, signed with a throwaway Ed25519 key, `latest.json` format `{"format":1,"manifest":{product,platform,channel,version,commit,built,installer,sha256,size,url,python,godot,published[,previous]},"signature":{alg,key_id,sig}}`; the app's `updates.verify_manifest` accepted it and rejected a tampered copy; release notes text rendered. The throwaway key and files were kept outside the repo.
* `build_windows.ps1`, `smoke_windows.ps1`, `upgrade_matrix.ps1`, `build_whatsapp_bridge.ps1`: PowerShell parse, 0 errors. The full installer build (600 MB payload, Inno Setup, Godot export) was not run here.

## Gates run here

* `core/tools/log_lint.py`: 594 call sites, 0 violations.
* `core/tools/perf_gate.py --class ci`: PERF GATE OK.
* Full core suite (`-o pythonpath=src`, 21 Oct): 2402 passed, 6 failed, 7 deselected. One failure was real and is fixed in this branch: `test_update_flow.py::test_in_app_updater_runs_setup_silently_and_restarts_jarves` assumed a source checkout is older than 3.0.0.1; with version 4.0.0 it now pins the running version to the test's OLD value. The other 5 failed only under load (a held `memory.db` file handle on teardown, idle-write churn sample, dashboard load budget 9.7 s, two real-browser tests) and all PASS when re-run (68 passed for the 5 files). The earlier first run (`-x`) also hit `test_privacy_state.py::test_core_preview_with_fake_camera_reaches_the_face` (frame wait timeout under load); it passes alone. Because of these load-sensitive tests the 3x identical run (item 3) must be done on a quiet machine.

## Quick gates on the merged head (22 Oct, after merging main with certifi 2026.10.19 and version 4.0.0)

Run on the agent machine in a temp home; CI on the exact head is the suite evidence (the full offline suite was not re-run locally).

* Redaction audit (`redaction_audit.py`): 0 violations.
* Repo secret scan (regex over tracked files): hits only in test fixtures and the audit tool itself (planted fixtures; the 2 GitHub secret alerts are the same, already resolved).
* Failure injection (`failure_injection.py --all`): 8/8 PASS.
* Performance gate `--class owner`: PASS when run alone. A first run in parallel with four other gates failed `spawn_to_ready_s` 5.57 > 5 from CPU contention only.
* `all_pages_check.py`: 200 loads, 0 failed, 0 rule hits (one pass, not three; the x3 and axe/owner review stay with the owner).
* `suite_system.py --skip-pytest` (short soak, run in parallel with the others, hit the 900 s timeout): 16 PASS, 3 FAIL (S12 needs a real ChatGPT plan; V04 virtual camera; V05 calendar CLI output). Not accepted as a PASS; items 4 and 11 remain needs-owner. These three are environment-dependent on this machine and are listed for the owner to re-run on the owner PC.
