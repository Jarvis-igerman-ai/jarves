# Windows installer speed and upgrade behavior (#58)

Status: implementation and measurement in PR #68; owner-PC timing and guided dashboard wizard remain separate verification gates.

## Measurements

Baseline: [green run 39737015500](https://github.com/igalgerman/jarves/actions/runs/39737015500), installer job 119685890222.
First speed checkpoint: [run 39741458836](https://github.com/igalgerman/jarves/actions/runs/39741458836), head 84742a9. Both installer smoke profiles passed; the overall run failed an unrelated workspace-memory test.

| Stage | Baseline | Green installer checkpoint (76983c5) |
|---|---:|---:|
| Python extraction | 1.43 s | 1.00 s |
| Dependencies | 8.39 s | 3.65 s |
| Wheel/log index and Python checks | about 7.95 s | 17.16 s |
| Godot export | 23.09 s, serial | 25.35 s, overlaps Python |
| Wait for parallel face bundle | absent | See build-stages.json |
| Payload check | 2.27 s | 1.09 s |
| Archive plus import verification | absent | 5.45 s |
| Release compression (lzma2/max) | 91.26 s | 85.90 s |
| Fast CI compression (lzma2/fast) | absent | 14.00 s |
| Release script build | 134.4 s | 118.7 s |
| CI smoke script build (shared bundle plus fast compression) | absent | 46.8 s |
| Clean release install on GitHub runner | 50.8 s | 41.9 s |
| Clean fast-profile install on GitHub runner | absent | 51.0 s |
| Release same-version upgrade | about 67.7 s | 39.7 s |
| Release uninstall | 19.5 s | 12.1 s |

The green checkpoint is [run 39747590940](https://github.com/igalgerman/jarves/actions/runs/39747590940), head 76983c5. All jobs passed, including both smoke profiles and the real old-version matrix. Old-version upgrade took 36.7s; rollback, including waiting for Setup, took 69.8s. A following one-directive change makes the issue's English fresh-install default explicit; the exact final-head result is recorded in PR #68.

The build job was 3m43s, including Go/tool setup and both compiler profiles. The old 7m42s job also included sequential smoke checks; those are now separate concurrent jobs. This is **not** evidence that all installer checks finish within five minutes: release smoke finished at 08:34:30Z after the build began at 08:27:33Z, about 6m57s. All installer jobs completed by 08:35:19Z, about 7m46s; the under-five-minute complete-validation target is not met. The old-version fixture adds real coverage which the empty baseline matrix never ran. Build, clean install, upgrade, rollback and all-checks elapsed time must be reported separately.

The owner's roughly four-minute install and the under-two-minute target are not verified by GitHub runner timings. The full checkpoint stages 7,918 files, including one archive containing 2,656 library files (16.98 MB). Runtime prompt/resource packages and native modules remain unpacked. Exact final-head figures are recorded in the PR after CI.

## Build and publication

Godot export and Python staging run independently and join before payload checks. Tool downloads remain SHA-pinned and use a cache key based on packaging/windows/tool-cache.txt rather than every build-script edit. No bytecode precompilation runs. The archive uses ZIP_STORED to avoid recompressing libraries before Inno compression; import probes disable bytecode writes. Native DLLs, certificates, metadata, model files, unknown dependencies, and JARVES resources stay on disk.

Release artifacts retain lzma2/max. Fast CI smoke artifacts have channel/build_profile=ci-smoke and are rejected by packaging/release.py even if the channel is changed to main. Publishing still requires the complete jarves-checks run to be green. Stage timings and file counts are artifacts; smoke/upgrade metrics include individual silent operations.

The versions are retained, not upgraded in this change. References checked 22 Oct 2026: [Inno compression](https://jrsoftware.org/ishelp/topic_setup_compression.htm), [Python zipimport limits](https://docs.python.org/3.12/library/zipimport.html), [GitHub cache scopes](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache).

## Setup and preservation

English/Hebrew are shown on interactive installs and upgrades. English is the fresh-install default; a previously chosen installer language is retained. Existing program/data paths are shown; keep settings is ON. Autostart and desktop shortcut default OFF; interactive unchecked choices remove those integrations, while silent upgrades preserve existing choices. Language sets the initial voice language only for a new config; keeping an existing config preserves it byte-for-byte. Silent installs force keep regardless of /KEEPDATA=0, never open a browser, and retain the existing autostart state unless explicitly requested. The legacy /SETUPANSWERS parameter still applies an explicitly supplied answer file.

Before replacing an installed runtime, the old bundled Python runs the installer adapter with the app backup tool. Every archive is verified and unreadable-file failures stop installation. Each existing profile gets its own backup. A data-only installation uses the new runtime to verify backup before any data migration. Migrations reuse the verified checkpoints; failure restores the old database state. The portable backup deliberately excludes credentials and models; those stay untouched in the original data folder. A fresh interactive choice renames the entire previous folder, retaining excluded private files too.

The old Python/face/bridge/launcher are renamed into the installed rollback directory on the same volume, avoiding a copy of thousands of files. Setup failure restores those program parts. One prior program version is retained. A successful installation leaves install-state.json with the verified backup locations. Stop JARVES and run the installed rollback_windows.ps1 with PowerShell for an offline snapshot rollback. It restores verified data first, then the previous program, retaining the newer program under failed-upgrade-*. It does not change Windows Apps' displayed installer version; reinstall the matching old installer to align registration. The app's ordinary updater rollback remains available when it retained or can download the prior installer. Uninstall removes program snapshots and restores retired shims; data backups remain unless purge is explicitly requested.

The installed folder is first in the **user** PATH. Windows still searches the current directory and machine PATH according to its own rules. Only recognized JARVES wrappers in known per-user Python/uv shim locations are retired; unrelated executables and developer source launchers are retained. Uninstall restores the previous PATH when otherwise unchanged and preserves unrelated PATH edits made later. A user-created replacement shim is never overwritten.

The final-page button uses pythonw and the dashboard API. A registered guided setup route opens #/setup; until that route is available it opens home. It preserves the dashboard's private authentication URL and never writes that URL to installer logs. The v5/wizard agent owns the guided pages; this PR does not edit them, setup_wizard.py, cli.py or console files.

## Verification

The targeted suite covers real ZIP imports; resource/native exclusions; smoke publication rejection; verified backup, corruption and migration failure; preserved wake/model/profile/credential fixtures; fresh-choice retention; shim reversal; and the dashboard fallback. Inno compiles locally. Visual preview was approved by the user, but the desktop tool reported app approval/elicitation unavailable; no preview window opened. Inno compilation passed; visual layout is unverified. The installer-specific assertion in test_setup_wizard.py was updated for the final-page button; the wizard implementation is untouched.

The Windows smoke installs into a disposable CI runner, audits HKCU Run/PATH/menu residue and exercises both compression profiles. The upgrade matrix pins public 4.0.0.153 by SHA256, seeds fake legacy rows, a 0.95 wake threshold, custom model, profile and fake credential files, upgrades silently, verifies preservation, then exercises updater rollback. The test waits for rollback Setup to exit and the old Core to restart before stopping it and auditing uninstall. Fixture timestamps are recent so the app's normal 90-day history retention does not prune the seeded conversation. Do not run this smoke on a personal PC casually: even with isolated app/data paths, it changes that Windows user's PATH, Run key and uninstall registration.

## Follow-up: overlap and independent smoke scenarios

The next CI pass starts the Windows build worker before Go setup and bridge compilation. Python, Godot and cold-cache Inno preparation proceed concurrently. The worker waits for the parent to signal a successful SHA-pinned bridge build before staging/checking the complete payload. Worker failure, timeout or bridge failure fails the installer job; artifact upload remains downstream of the join. Worker status/logs are outside the disposable build output. build_s now includes the overlapped bridge wait; compare job and all-check elapsed time as well as component stages, rather than treating this as the old Go-exclusive script timing.

The smoke matrix uses one fresh Windows VM per profile/scenario: functional (CLI, Core/face, answers upgrade, uninstall keeps data) and autostart-purge (retained fake data without an installed program, startup integration, explicit purge). The real older-version upgrade/rollback matrix remains separate. This preserves three candidate installs per profile across the two scenarios; HKCU/AppId operations never run concurrently on one Windows user. The default local full scenario is retained for compatibility.

References checked 22 Oct 2026: [Windows Start-Process lifetime](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process?view=powershell-7.5) and [fresh hosted VMs per job](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job). Worker log redirection happens inside the detached process so the launching step does not retain its output pipe. The Windows regression test exercises actual separate processes and success/failure/abort handling with fake builds, never a real installer.

Merge gate (owner): hold #68 unmerged; #67 then #72 must merge first, then synchronize #68 and obtain green exact-head CI before its merge. No product/batch or v5/wizard files are changed in this follow-up. #58 remains open for owner-PC timing and visual review.
