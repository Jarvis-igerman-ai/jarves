# SW-V3: Approvals service + Helper Skill (#17, branch `v3-approvals`, 20 Oct 2026)

Owner requests (20 Oct): (A) a visible approval popup even when the face is hidden and the Core runs in the
background, one shared approvals service for every confirmation path; (B) a Helper Skill that runs commands and
installs software only with explicit per-action approval.

## A. One approvals service: `core/src/jarves/approvals.py`
- `Core.ask_confirm` (which is `registry.confirm`) now calls `ApprovalService.ask`. Every existing path goes through
  it: destructive tools and `confirm` prompts (memory delete, Telegram/Instagram send, Gmail send, calendar,
  Home Assistant risky), `feature_set` for medium/high-risk switches (e.g. enabling Google Contacts), the face camera
  button, scheduler/autonomy/guides confirmations, and the Helper. Terminal-only sessions (`jarvis execute`,
  `jarvis contacts/calendar/mail`) keep their terminal prompt but write to the same audit log (`approvals.audited`).
- One request is shown on every available channel at once; the first answer wins and the others close:
  - **popup**: `approval_popup.py`, a small always-on-top Tk window in its own process (bottom-right). It shows the
    title, the exact request, why (marked "JARVES's words, not verified"), the risk (colour), the details (command,
    folder, package, version, source) and a countdown. Deny has the focus; Enter/Escape/closing the window = Deny;
    Approve is enabled only after 0.9 s and, for typed requests, only after the 4-digit code is typed. Only on real
    runs (`use_real_audio`), so tests and silent runs never open windows.
  - **tray**: a notification balloon ("JARVES needs your approval: ...").
  - **face**: the existing Godot dialog; new `confirm.cancel` closes it when answered elsewhere.
  - **dashboard**: a banner on every page plus the `#/approvals` page (pending + recent decisions), from
    `GET /api/approvals` / `POST /api/approvals/<id>`; works from another process through
    `<home>/approvals/pending.json` + `answers/<id>.json`.
  - **voice / typed**: a pure yes/no ("כן", "אישור", "approve" / "לא", "בטל") from the owner's own transcript (live
    input transcription, push-to-talk STT, or text typed on the face) while exactly one request waits. Speech
    transcribed less than 1.5 s after the request appeared is ignored, because it belongs to what was said before.
  - **CLI**: `jarvis approvals list | approve <id> [--code N] | deny <id> | history`.
- Rules enforced in code: default deny on timeout (60 s; Helper 120 s), shutdown, cancellation and "nobody could be
  asked" (`no_channel`). No approval from model-controlled text: `answer()` refuses inside any Skill tool handler
  (new `skills.IN_TOOL` contextvar), no tool answers, and voice answers come only from the owner's transcript.
  Typed-code requests cannot be approved by voice or the face. An identical request that is already waiting shares
  its one prompt. A model request identical to one the owner just denied is denied without asking for 30 s, unless the
  owner speaks or types again. More than 6 new requests per minute are denied.
- Audit: `<home>/approvals/audit.jsonl` (decision, channel, where it was shown, wait time, kind, risk; prompt text
  masked in privacy mode except shell/install commands), rotated at 1 MB.
- Toast vs window: a WinRT toast with buttons needs a registered AppUserModelID and a COM activator for an
  unpackaged app, and Focus Assist hides it without any sign. A topmost Tk window (standard library, works under
  pythonw and on the Pi desktop) is always seen. The toast was not built; the tray balloon is the extra nudge.

## B. Helper Skill: `core/src/jarves/skills/helper.py`
- Feature switches: `helper` (OFF by default, high risk, SAFE_OFF) and `helper_readonly_auto` (OFF, medium,
  needs helper). Both have a wizard group "Computer helper", a bundle check, capability rows, guide
  `guides/data/helper.toml` and Hebrew texts (`helper.HE_TEXTS`).
- Tools: `shell_propose(command, purpose, cwd, timeout_s)`, `install_propose(manager, package, version, purpose)`,
  `helper_history`.
- `classify()` policy:
  - **refused**: elevation (runas/sudo/gsudo/-Verb RunAs), anything that touches JARVES's own data folder (outside
    the helper sandbox `<home>/helper/work`), package installs outside `install_propose`, multi-line or overlong
    commands, programs that are not found, and JARVES itself running as administrator or root.
  - **dangerous**, needs the typed code: format/diskpart/bcdedit, recursive deletes outside the sandbox,
    reg delete/add, net user, sc/schtasks/setx, `powershell -enc`, iex/DownloadString, curl|sh, shutdown,
    takeown/icacls, taskkill, credential stores and key files, system folders, `git push --force` / `reset --hard`,
    and any pipe, redirect or chain.
  - **readonly**: dir, ls, git status/log/diff --stat, `<tool> --version`, pip list, where. These run without the
    popup only when `helper_readonly_auto` is on, and are still logged.
  - **normal**: everything else needs the popup.
- Installs: winget (`--exact --source winget`, agreements accepted, UAC left to Windows), pip (a system Python, never
  JARVES's own venv, `--user`, PyPI index pinned), npm (project folder, registry pinned, `--ignore-scripts`). The
  exact package, source, version ("latest (not pinned)" when none is given) and the full command are shown. Installs
  are always high risk and never run automatically.
- Running: as the normal user; stdin closed; credential-like environment variables and `JARVES*` removed; its own
  process group; on timeout the whole process tree is killed; output goes to `<home>/helper/runs/<id>.log` (2 MB
  cap) and the summary is the last 20 lines, redacted, labelled UNTRUSTED. The tool is `untrusted_output`, so no
  further model action runs in the same turn. Audit: `<home>/helper/audit.jsonl`.

## Tests (run one file at a time: the machine was short of RAM)
`core/.venv/Scripts/python.exe -m pytest -o pythonpath=src tests/test_helper_approvals.py` gave **41 passed** with
real child processes in temp folders, the real approvals service, the real dashboard route functions, the answers
file, the real Tk popup (invisible, the window presses its own button), the face link and typed "כן". The approval
decision path is never mocked.
Regression files run one by one, all green: test_core 17, test_security 19, test_suite_voice_fixes 15,
test_features 19, test_setup_wizard 21, test_guides 24, test_channels 27, test_dashboard_p02 3,
test_privacy_state 11, test_capabilities 7, test_execute 7, test_workspace_memory 34, test_dashboard 10,
test_camera 17 (+1 existing skip), test_instagram 11, test_m3_scheduler 57, test_runtime 27, test_issue16 50,
test_issue13 16, test_skill_creation 47, test_m3_core 4, test_m3_sandbox 32, test_live 6, test_voice_faults 42,
test_feature_setup 26, test_dashboard_phone 7, test_contacts 19, test_calendar_write 42, test_launcher_help 2.
The full suite was not run (RAM limit); CI on the pushed commit is the full-suite evidence.

## Unverified
- **The real popup on the owner's desktop was not seen by a person.** Not checked: the Hebrew rendering/RTL in Tk,
  the position, whether it stays on top over full-screen apps, and pythonw from the background runtime. Tests ran it
  invisibly (alpha 0).
- The tray balloon, the Godot `confirm.cancel` dialog close (the Godot protocol test is skipped here because there
  is no Godot binary), and voice "כן" in a real live session (echo of JARVES's own speech through the mic has not
  been measured).
- No real install was run (tests deny the install request). Real winget/pip/npm behaviour, and a UAC prompt from
  machine-scope winget installers, have not been tried.
- `hebrew.py` (`v3-p02-dashboard`) is not on main: the Helper's Hebrew texts are in `helper.HE_TEXTS` and should
  be merged into `hebrew.TEXT` when the branches meet.
- The Raspberry Pi has not been tried (Tk needs a desktop session; a headless Pi falls back to the dashboard/CLI).
