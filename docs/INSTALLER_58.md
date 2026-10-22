# Windows installer speed and upgrade behavior (#58)

Status: implementation and measurement in PR #68; owner-PC timing and guided dashboard wizard remain separate verification gates.

## Measurements

Baseline: [green run 39737015500](https://github.com/igalgerman/jarves/actions/runs/39737015500), installer job 119685890222.
First speed checkpoint: [run 39741458836](https://github.com/igalgerman/jarves/actions/runs/39741458836), head 84742a9. Both installer smoke profiles passed; the overall run failed an unrelated workspace-memory test.

| Stage | Baseline | First checkpoint |
|---|---:|---:|
| Python extraction | 1.43 s | 2.06 s |
| Dependencies | 8.39 s | 3.22 s |
| Wheel/log index and Python checks | about 7.95 s | 21.43 s |
| Godot export | 23.09 s, serial | overlaps Python; exact duration in build-stages.json |
| Payload check | 2.27 s | 1.90 s |
| Archive plus import verification | absent | 7.68 s |
| Release compression (lzma2/max) | 91.26 s | about 89.48 s |
| Fast CI compression (lzma2/fast) | absent | 17.40 s |
| Release script build | 134.4 s | 131.0 s |
| CI smoke script build (shared bundle plus fast compression) | absent | 59.2 s |
| Clean release install on GitHub runner | 50.8 s | 49.1 s |
| Clean fast-profile install on GitHub runner | absent | 45.3 s |
| Release uninstall | 19.5 s | 8.8 s |

The first build job was 4m11s, including Go/tool setup and both compiler profiles. The old 7m42s job also included sequential smoke checks; those are now separate concurrent jobs. This is **not** evidence that all installer checks finish within five minutes. The final old-version fixture adds real coverage which the empty baseline matrix never ran. Build, clean install, upgrade, rollback and all-checks elapsed time must be reported separately.

The owner's roughly four-minute install and the under-two-minute target are not verified by GitHub runner timings. The first archive reduced 11,236 staged files to 9,911. The final checkpoint also permits documentation-only files in reviewed code packages, including OpenAI; runtime prompt/resource packages and native modules remain unpacked. Final figures are recorded in the PR after CI.

## Build and publication

Godot export and Python staging run independently and join before payload checks. Tool downloads remain SHA-pinned and use a cache key based on packaging/windows/tool-cache.txt rather than every build-script edit. No bytecode precompilation runs. The archive uses ZIP_STORED to avoid recompressing libraries before Inno compression; import probes disable bytecode writes. Native DLLs, certificates, metadata, model files, unknown dependencies, and JARVES resources stay on disk.

Release artifacts retain lzma2/max. Fast CI smoke artifacts have channel/build_profile=ci-smoke and are rejected by packaging/release.py even if the channel is changed to main. Publishing still requires the complete jarves-checks run to be green. Stage timings and file counts are artifacts; smoke/upgrade metrics include individual silent operations.

The versions are retained, not upgraded in this change. References checked 22 Oct 2026: [Inno compression](https://jrsoftware.org/ishelp/topic_setup_compression.htm), [Python zipimport limits](https://docs.python.org/3.12/library/zipimport.html), [GitHub cache scopes](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache).

## Setup and preservation

English/Hebrew are Inno's language choices. Existing program/data paths are shown; keep settings is ON. Autostart and desktop shortcut remain OFF. Language sets the initial voice language only for a new config; keeping an existing config preserves it byte-for-byte. Silent installs force keep regardless of /KEEPDATA=0, never open a browser, and retain the existing autostart state unless explicitly requested. The legacy /SETUPANSWERS parameter still applies an explicitly supplied answer file.

Before replacing an installed runtime, the old bundled Python runs the installer adapter with the app backup tool. Every archive is verified and unreadable-file failures stop installation. Each existing profile gets its own backup. A data-only installation uses the new runtime to verify backup before any data migration. Migrations reuse the verified checkpoints; failure restores the old database state. The portable backup deliberately excludes credentials and models; those stay untouched in the original data folder. A fresh interactive choice renames the entire previous folder, retaining excluded private files too.

The old Python/face/bridge/launcher are renamed into the installed rollback directory on the same volume, avoiding a copy of thousands of files. Setup failure restores those program parts. One prior program version is retained. A successful installation leaves install-state.json with the verified backup locations. Stop JARVES and run the installed rollback_windows.ps1 with PowerShell for an offline snapshot rollback. It restores verified data first, then the previous program, retaining the newer program under failed-upgrade-*. It does not change Windows Apps' displayed installer version; reinstall the matching old installer to align registration. The app's ordinary updater rollback remains available when it retained or can download the prior installer. Uninstall removes program snapshots and restores retired shims; data backups remain unless purge is explicitly requested.

The installed folder is first in the **user** PATH. Windows still searches the current directory and machine PATH according to its own rules. Only recognized JARVES wrappers in known per-user Python/uv shim locations are retired; unrelated executables and developer source launchers are retained. Uninstall restores the previous PATH when otherwise unchanged and preserves unrelated PATH edits made later. A user-created replacement shim is never overwritten.

The final-page button uses pythonw and the dashboard API. A registered guided setup route opens #/setup; until that route is available it opens home. It preserves the dashboard's private authentication URL and never writes that URL to installer logs. The v5/wizard agent owns the guided pages; this PR does not edit them, setup_wizard.py, cli.py or console files.

## Verification

The targeted suite covers real ZIP imports; resource/native exclusions; smoke publication rejection; verified backup, corruption and migration failure; preserved wake/model/profile/credential fixtures; fresh-choice retention; shim reversal; and the dashboard fallback. Inno compiles locally. Visual preview is pending explicit approval required by the computer-use tool.

The Windows smoke installs into a disposable CI runner, audits HKCU Run/PATH/menu residue and exercises both compression profiles. The upgrade matrix pins public 4.0.0.153 by SHA256, seeds fake legacy rows, a 0.95 wake threshold, custom model, profile and fake credential files, upgrades silently, verifies preservation, then exercises updater rollback. Do not run this smoke on a personal PC casually: even with isolated app/data paths, it changes that Windows user's PATH, Run key and uninstall registration.
