# SW-V4 package L: installer, updates, rollback (20 Oct 2026)

Branch `v4/l-installer` (public repo `igalgerman/jarves`, built on the integrated V4 draft PR #11). Issue #17 / the V4 package L.
Evidence classes: **fake** = unit test with temp dirs and fakes; **script** = written and parse-checked, runs in CI only; **not run** = needs a real installer, a second PC or the owner.

## 1. Status

| Item | Result | Evidence |
|---|---|---|
| Clean-machine install test (no VM/Sandbox) | scripted + residue audit | `packaging/smoke_windows.ps1 -DefaultTasks`, `packaging/install_audit.py`; audit logic **fake**-tested (`test_installer_clean.py`); the script itself **not run** (needs the CI installer) |
| Autostart OFF by default | installer task now `unchecked`, `UsePreviousTasks=no`; wizard defaults leave the Run key empty | **fake** (fake Run key) + script check; **owner decision flag below** |
| Uninstall keeps data unless asked | existing `purge_guard.iss` (compiled test) + policy test of the silent/interactive branches | **fake** + compiled Inno harness |
| Silent upgrade 0.1.0.x to 3.x to 4.x | version compare, unchanged AppId, data migration with backup first, settings kept, `/VERYSILENT /SUPPRESSMSGBOXES` args | **fake** (`test_update_flow.py`, `test_migrations.py`); real old installers **not run** (fixtures not pinned yet) |
| Update flow vs local release server | real loopback HTTP server, signed `latest.json` | **fake** (35 tests) |
| Rollback | `updates.rollback`, `jarvis update --rollback`, dashboard Updates page button (asks approval), failed-first-start offer | **fake**; real installer round trip is the CI scenario (**script**) |
| Unsigned decision | recorded (section 6) | docs |
| Size/time budget hooks for M | `[installer.*]` in `core/perf_budgets.toml`, `packaging/installer_metrics.py`, `perf_gate.py --installer-json` | **fake** |
| Go WhatsApp bridge in payload | pin file + SHA256 check, build stages `JARVES_BRIDGE_EXE` | **fake** + dry run; `packaging/bridge_pin.json` now pins the reproducible build, payload check PASS with it (21 Oct) |
| Restricted token / AppContainer study | section 8, real probe | measured on this PC |

## 2. Clean-machine install test (no Windows Sandbox, no VM)

`smoke_windows.ps1` already installed into a temp folder with a private `JARVES_HOME` and Core port. L adds, around every install and uninstall, `install_audit.py` (read-only): HKCU `Run` values, user PATH, scheduled-task names, Start-menu folder, program folder, data folder hashes.

* After a default install (`-DefaultTasks`: no `/TASKS`, the installer's own defaults): autostart OFF, `Run` unchanged, PATH = old PATH plus exactly the program folder, no scheduled task, uninstaller and Start-menu entry present.
* After the silent uninstall: program folder, Start-menu entry, Run value, PATH entry and tasks gone; every other Run value and PATH entry equal to the "before" state; data folder byte-for-byte equal to just before the uninstall.
* After `/PURGE=1`: data folder gone, still no residue.
* First run: `jarvis setup --check-addons` (existing), setup answers, start/stop and doctor steps of the old smoke test stay.
* Timings of the silent steps go to `-MetricsOut` (section 7).

The audit's checks are pure functions over dicts, so they are tested with fake machine states (positive and every residue kind) without reading a real registry. "Second PC" rows of `docs/INSTALL_TEST.md` (OL-I1..OL-I8) remain the owner's 45 minutes: SmartScreen text, tray, 5x start/stop by hand, a real update from a real release.

## 3. Upgrade path and data migration

* Versions compare as numbers (`updates.version_tuple`): 3.0.0.1 > 0.1.0.999, 0.1.0.10 > 0.1.0.9. The CI series is already `3.0.0.<run>`.
* Inno `AppId` `{24F7B79A-973C-462D-BA26-896D8EA30913}` never changes (asserted by test), so every series upgrades in place; `[InstallDelete]` replaces `python\` and `face\`; data lives outside the program folder.
* `jarves/migrations.py`: marker `schema.json`; no marker + databases = schema 1 (0.1.0.x); 2 = 3.x additive columns; 3 = 4.x recall index. A pending step first takes a verified pre-migration backup (note `schema A->B`); a backup failure refuses to migrate. Data newer than the program is refused. **Down-path rule for every step:** restore the pre-migration backup (`migrations.down`, `updates.rollback`), because additive columns are not dropped in place. Tests migrate up from schema 1 and 2 and back, comparing every table's rows and every file.
* Core start calls `migrations.run` right after the existing version checkpoint, then `updates.note_start`; `mark_healthy` runs at CORE_READY.
* Setup stops the Core first (`PrepareToInstall`, existing); silent update args: `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS` plus `/RESTARTJARVES=1` (new `[Run]` entry starts the Core windowless; skipped unless asked).
* Settings and autostart survive: nothing in setup writes the Run key except the opt-in task; `UsePreviousTasks=no` so an upgrade cannot re-tick it; user choices live in the untouched data folder.

## 4. Update channel security and the failure story

Unchanged scheme (Ed25519-signed `latest.json`, HTTPS only, SHA256 and size pinned in the signed manifest, an installed build launches only what the signed manifest named). Added and tested:

* the `previous` block (version, installer, SHA256, size, URL) lives inside the signed manifest (`release.py build_manifest/previous_entry`), so a tampered block fails verification;
* the installer is written atomically (`.partial` then rename); every failure (wrong SHA, wrong size, HTTP 500, truncated, offline, tampered/unsigned/wrongly signed manifest, plain-HTTP source) leaves no installer, no `pending.json`, no data change; the old version is still the running one;
* downgrade refused in three places: `prepare_install` (UPDATE_NONE/UPDATE_DOWNGRADE), `launch_installer` (unless `rollback=True`), and setup itself (`downgrade_guard.iss`, compiled-tested with the real Inno compiler; refuses unless `/ROLLBACK=1`);
* "ask before install": nothing is downloaded before the yes; the CLI asks, the dashboard click is the approval.

## 5. Rollback

`updates/previous/` holds `previous.json` + the previous installer when this copy was itself installed by the updater; otherwise the record points at the signed manifest's `previous` entry and the installer is downloaded and verified on demand. `jarvis update --rollback [--yes] [--keep-data] [--window]` (and the Updates page button, which first asks a medium-risk approval) verifies the installer, restores the pre-update backup (the restore takes its own safety checkpoint, so data written after the update is not lost), then starts setup with `/ROLLBACK=1`. A first start of a new version that never reaches CORE_READY is noticed on the second start and offered through approvals (never automatic); a setup that never ran is detected when the old version starts again (`not_applied`).

Honest limits: rollback restores data to the moment of the pre-update backup (newer data stays in the safety backup, not in the live data); an owner who installed by hand and never used the updater has a rollback only if the signed manifest carried `previous` (publishing starts doing that with this change); backups are pruned by the existing retention (5 checkpoints). The CI scenario creates the "previous" record by hand (`seed_legacy.py previous`) because it cannot run the signed-update server end to end; the Python flow is covered by the local fake server tests.

## 6. Code signing decision (OD-08), recorded

Owner confirmed (20 Oct 2026): **stay unsigned for now.** Applied: `jarves.iss` has no SignTool; `package.yml` unchanged (it already publishes unsigned with the SmartScreen warning in the release notes); `INSTALL_TEST.md`, the Updates page and the guide tell users: "More info, then Run anyway", and to compare the SHA256 on the release page. Facts from the V4 package (CODE_SIGNING_FREE recheck of the same week; not re-verified online in this run): SignPath Foundation needs an OSI licence (owner chose none); Azure Artifact Signing for individuals is limited to USA/Canada. Open option when the owner wants it: a self-signed certificate imported on the owner's own PCs (no effect for strangers) or a paid OV certificate. Integrity for updates does not depend on Authenticode: the app verifies its own Ed25519 signature + SHA256 + size.

## 7. Budgets for package M

`core/perf_budgets.toml` `[installer.*]`: size 198 MB baseline (owner 240, CI 300) and loose time ceilings with `baseline_pending = true` (install, uninstall, upgrade, rollback, build) until the first CI numbers exist. `smoke_windows.ps1 -MetricsOut` plus `build-info.json` (`build_s`, size) feed `installer_metrics.py measure`; CI runs `installer_metrics.py check --class ci`; `perf_gate.py --installer-json FILE` merges the rows into M's gate. A metric not measured is `n/a`, never a pass-by-silence failure.

## 8. Restricted token / AppContainer feasibility for helper isolation

Goal (R-01 open item): approved helper commands must not read `<home>` (`answer.key`, `secrets.env`). Today they run in a Job Object that limits resources but not files.

**Measured** (this PC, Windows 11, `core/tools/token_probe.py`, `cmd.exe` under restricted copies of the caller's own token, `CreateProcessAsUserW`, no extra privilege needed):

| token | starts | read owner-only file | read world-readable file | same, path inside the profile | write owner-only file |
|---|---|---|---|---|---|
| plain | yes | yes | yes | yes | yes |
| deny-only user SID + Administrators, max privileges removed | yes | **no** | yes | **no** | **no** |
| write-restricted (restricting SID Everyone) | yes | yes | yes | yes | **no** |

Findings:

1. `CreateRestrictedToken` with the user SID as deny-only does hide owner-only files: it would hide `answer.key`/`secrets.env` if `<home>` is owner-only. It also hides everything else that is granted only via the user SID, including the whole profile root path: a file granted to Everyone but located under `%LOCALAPPDATA%` is unreachable. **Consequence: the bundled interpreter in `%LOCALAPPDATA%\Programs\JARVES` and the user's workspace would be unreachable** unless those folders are reachable through a group SID kept enabled, which means changing ACLs on the profile root path. Not acceptable to do from a per-user installer.
2. A **write-restricted** token keeps all reads, so it does not hide secrets by itself. But a directory with an owner-only (protected) DACL was **not traversable** by it either (measured: a world-readable file inside an owner-only folder was unreadable, readable again once inheritance was restored). So "make `<home>` a protected owner-only folder and run helpers write-restricted" hides the home contents while keeping the rest of the profile readable and the program folder usable. Writes by the child are blocked everywhere that does not grant the restricting SID, so a helper that must write (npm into user folders, the workspace) needs explicit ACEs for the restricting SID on those folders.
3. `CREATE_NO_WINDOW` fails under both restricted tokens (console host init error 0xC0000142); `DETACHED_PROCESS` with inherited pipes works, which is what the helper already uses (captured stdio).
4. **AppContainer/LPAC**: not run (needs `CreateAppContainerProfile`, which writes a per-user registry profile; excluded by this task's no-registry rule). Known costs from the platform docs and from findings 1-2: the container SID needs explicit ACEs on every folder it uses (program folder, workspace), no loopback to the Core without a loopback exemption (admin-only `CheckNetIsolation`, which is a machine setting), and the profile is a registry write. Stronger than a restricted token, but not installable per user without admin; recommended only as a later experiment on a Pi-class sandbox decision.

**Decision / recommendation (not implemented in the product):** the safe, testable step is *protected owner-only `<home>` DACL + write-restricted helper token + ACEs for the restricting SID on the helper's declared work folders*. It changes how every helper starts (`CreateProcessAsUserW` with `STARTUPINFOEX`, pipes, job assignment before resume), needs per-command re-testing (winget, npm, git), and the DACL on `<home>` must be set by the Core at start and kept by backup/restore. That is a dedicated work package with a real-command test matrix, so it was **not** wired in. What is committed is the probe and its test (`test_token_probe.py`, Windows-only) so the claims above stay checked. Linux: Landlock/bwrap as in the R-01 report (unchanged).

## 9. Not verified / needs others

* A real installer was not built here (no 600 MB payload build). All installer-level steps (`-DefaultTasks`, `-UpgradeFrom`, `-Rollback`, `/ROLLBACK=1` refusal at setup level, `/RESTARTJARVES=1`) are scripts that CI will run; the Pascal pieces compile (downgrade guard harness) but the full `jarves.iss` compile with the new code was not run locally.
* `packaging/upgrade_fixtures.json` is empty: pin real 0.1.0.x and 3.0.0.x installers (URL + SHA256) for the matrix; until then CI prints "UPGRADE MATRIX: NOT RUN".
* `[installer.*]` time baselines are pending the first CI numbers.
* The Go bridge is pinned (`packaging/bridge_pin.json`, 21 Oct); CI compares the rebuilt binary with the pin.
* No second-PC run, no SmartScreen observation, no real update from a published release.
