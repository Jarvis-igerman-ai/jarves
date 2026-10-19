# SW-V2 WP-M6 — Windows installer, CI packaging + retention, update check (spec M6, §11, D09, D16)

Branch `worktree-agent-a1d40137e0c1e2558` (based on `swv2-integrate`), issue #17. Pi follow-up: #19 (real issue).

## Decisions and research (checked 19 Oct 2026 via the GitHub releases API / vendor pages)

| Need | Chosen | Why (vs alternatives) |
|---|---|---|
| Python on a PC without Python | **python-build-standalone** CPython 3.12.15 (`20261013`, Astral) + the exact wheels from `core/uv.lock` (`uv export --frozen --require-hashes`) installed into it; launcher runs `python -I -m jarves` | Same interpreter family uv already uses; fully relocatable folder; no freezing step, so dynamic imports (Agents SDK, winrt, onnxruntime, ctranslate2) just work; no PyInstaller bootloader (frequent antivirus false positives); every package keeps its license file. PyInstaller 6.22.3 = rejected (fragile with these native deps, AV flags). python.org embeddable zip = rejected (no pip/site handling, `._pth` quirks). A uv venv is not relocatable (absolute `home` in pyvenv.cfg). |
| Face / room | **Godot 4.7.2 headless export** (`--export-release "Windows Desktop"`) with the official `windows_release_x86_64.exe` template from the pinned `.tpz` (only the two Windows binaries are kept and cached; the 1.3 GB archive is deleted) | Smaller than shipping the editor, no editor UI; same 4.7.2 as CI. `main.gd` loads the GLB through `globalize_path`, which in an export resolves next to the .exe, so the raw GLB ships in `face\assets\`; `*.json` is included in the .pck. |
| Installer | **Inno Setup 7.1.0** (`SetupArchitecture=x64`, `PrivilegesRequired=lowest`) | User decision; per-user `{autopf}` = `%LOCALAPPDATA%\Programs\JARVES`, no admin; English + Hebrew wizard. |
| Signing | **none** | No code-signing certificate exists. SmartScreen warning documented (installer header, release notes, INSTALL_TEST.md). Never claimed. |
| Pear Desktop | **not bundled** | Separate app; optional post-install link + `jarvis music open`. |

All four downloads are pinned by URL + SHA256 in `packaging/build_windows.ps1` (test enforces it).

## What was built

- `packaging/build_windows.ps1` — stage Python + locked deps + jarves wheel + `_build_info.json` (version, commit,
  build time, channel), export the face, copy launcher/notices, **payload check**, ISCC, `.sha256`, `build-info.json`.
- `packaging/payload_check.py` — fails the build on secrets.env, tokens, OAuth files, *.db, *.log, user config,
  `logs/ chatgpt/ backups/ workspace/` folders, and on `sk-…`, GitHub tokens or private keys in JARVES's own files.
- `packaging/windows/jarves.iss` — per-user install, Start-menu (JARVES, setup, dashboard, doctor, update check,
  uninstall), tasks PATH / autostart (calls WP-M1 `jarvis autostart on|off`) / desktop icon; first-run hook =
  post-install "Set up JARVES now" (`jarvis setup` → connection choice + `jarvis key`) and `setup --if-needed` on
  first start; upgrade stops the Core and replaces `python\`+`face\` cleanly; uninstall keeps
  `%LOCALAPPDATA%\jarves` unless the user says Yes (default No) or passes `/PURGE=1` silently.
- `packaging/windows/jarvis.cmd` — installed launcher (same verbs as the dev `jarvis.cmd`, no uv).
- `packaging/smoke_windows.ps1` — silent install into a temp dir with its own `JARVES_HOME` + link port, checks
  files/Start menu, `--version`/status/features/doctor, offline Core start → `jarvis stop`, exported face self-checks,
  upgrade in place, uninstall keeps data, reinstall + `/PURGE=1` removes it.
- `packaging/release.py` — `verify` (tested commit, channel `main`, SHA256), `notes` (provenance + unsigned
  warning), `prune` (newest 3 `win-v*` releases + tags and newest 3 `jarves-windows-installer-main` artifacts;
  drafts, other prefixes, `[pinned]` releases and other artifacts untouched).
- CI: `jarves-checks.yml` new job **`installer`** (windows-2025, after `core`): build + smoke + upload artifact
  (`-main` 14 days on main pushes, `-pr` 3 days on PRs). New **`package.yml`**: on `workflow_run` of jarves-checks,
  only `conclusion == success && event == push && branch == main` → download *that run's tested* artifact →
  verify → `gh release create win-v<version> --target <sha> --latest` → prune. PR outputs can never be published.
- Update policy (D16, owner: notify + ask approval): `core/src/jarves/updates.py` — read-only HTTPS GET of the
  releases API at most daily (cached in `updates.json`), notice once per version (Hebrew/English text for voice/tray),
  install only after approval: download → SHA256 (release `.sha256` cross-checked with GitHub's asset digest; refuse
  without one) → **`backup.checkpoint` (WP-M5)** → stop Core → open the normal setup window. CLI
  `jarvis update [check|install|status]`, `jarvis --version`; dashboard API `GET /api/updates`,
  `POST /api/updates/check`, `POST /api/updates/install {"approve": true}`. Feature `update_check` registered
  (low risk, default on).

Version = `<pyproject version>.<jarves-checks run number>` (e.g. `0.1.0.57`), four numbers as Inno requires.

## Windows / Pi status (spec §5)

| Feature | Windows | Pi (linux_arm64) |
|---|---|---|
| windows_installer | implemented_unverified — CI silent install/run/uninstall passes; a clean second PC = owner test (docs/INSTALL_TEST.md) | planned — no ARM64 package/systemd user service; #19 |
| update_check | implemented_unverified — offline tests; live once a release exists and the PC can read it | planned — check works, no Pi package to install; #19 |

Recorded in `updates.PLATFORMS` (features.register has no `targets` field yet; passed automatically once it does).

## Tests and evidence

- Local (this PC): full build without the face export (140 MB installer, 616 MB installed) and the complete
  silent smoke (`-SkipDoctor`, because doctor records 1.5 s from the real microphone): **SMOKE RESULT: PASS**;
  verified no leftovers (Start menu, HKCU uninstall key, PATH).
- `core/tests/test_updates.py` (13) and `core/tests/test_packaging.py` (8): retention incl. pinned/drafts/other tags,
  artifact scoping, provenance verify, notes, payload scan, installer policy, SHA256 pins, approval gate, checksum
  refusal, backup without credentials, once-per-version notice, private-repo/offline = "unavailable".
- CI on throwaway branch `ci/m6-packaging-test` (deleted afterwards): see "CI evidence" below.

## CI evidence

(filled in below after the run)

## Open questions for the owner

1. **The repository is private.** Anonymous update checks get 404, and another PC cannot download releases without
   GitHub access. Options: make the repo (or a separate releases-only repo) public; or each PC gets a read-only
   fine-grained token in `secrets.env` as `JARVES_UPDATE_TOKEN` (supported, never shipped). Recommendation: a
   public `JARVIS-releases` repo (set `JARVES_UPDATE_REPO`, publish step pushes there) — needs a PAT secret.
2. Code signing: buy a certificate (or Azure Trusted Signing) to remove the SmartScreen warning? Until then unsigned.
3. Private-repo Actions minutes: the `installer` job costs ~15–25 Windows minutes per PR/push (2x billing).
   Keep it on every PR (catches installer breakage before merge) or only on `main`? Current: every PR.
4. Camera extra (opencv, 112 MB) is bundled so the camera feature can be enabled; drop it to shrink the installer?

## Integration hooks (outside WP-M6 files)

- `core/src/jarves/cli.py`: `--version`; `update` subcommand; `_launch_viewer` uses `JARVES_FACE_EXE` (installed
  face) before `GODOT_EXE`/WinGet; `_git_rev` falls back to the build's commit.
- `core/src/jarves/dashboard.py`: `"jarves.updates"` in `PLUGIN_MODULES`. Dashboard UI page/footer (WP-B) can show
  `GET /api/updates` (`notice`, `latest`, install button → POST with `approve: true`).
- WP-M1: installer calls `jarvis autostart on|off` (missing command is harmless); tray may show
  `UpdateChecker(home).notice()` and call `mark_notified()`. Core start may call `UpdateChecker(home).check()` in a
  background thread when `features.active("update_check")` (cached daily; not wired in app.py by WP-M6).
- WP-M5: `backup.startup_checkpoint` compares `__version__` (always `0.1.0`); comparing
  `updates.build_info()["version"]` would also checkpoint when a newer build is installed by hand.
- WP-M4 capability matrix: read `jarves.updates.PLATFORMS`.
