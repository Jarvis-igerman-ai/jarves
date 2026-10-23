# Changelog

All notable changes to JARVES. Newest first. Details per release: [docs/RELEASE_NOTES_4.0.md](docs/RELEASE_NOTES_4.0.md). Dates are 2026.

## 4.0.1 (unreleased)

- Added: software project manager (`jarvis project ...`, Projects dashboard page, voice tools, feature `project_manager`, off by default). Open a git project; JARVES splits the goal into small tasks, runs short-lived coding agents (Claude Code, Codex, OpenCode) in parallel worktrees, passes work on by handoff, merges into its own integration branch only after the project check passes, and answers status, new task, update, deep dive and talk-to-agent. Never pushes or merges your branches. Real-agent runs unverified. See docs/plans/PROJECT_MANAGER.md.
- Added: release notes. Dashboard page Release notes (pick any version, English and Hebrew), `jarvis release-notes [--version V] [--list] [--open] [--he]` and a standalone HTML page that the installer's last page opens ("Show what is new", on by default, skipped in silent installs). The notes are this CHANGELOG, shipped next to the program; a beta shows the notes of its stable version. Local only, no network.
- Added: Setup and tour, one wizard for every topic (dashboard page `#/setup`, `jarvis tour`, voice "show me around"): set up (or skip) -> safe test with Works / Does not work / Skip (a failure writes a bug report with content-free diagnostics) -> 30-second learn card. First run is the full baseline; later versions show what is new plus a smoke check. Your answers become a per-feature "verified in version X by owner" record and generate `docs/reviews/MANUAL_TEST_<version>.md` for the release gate. The old add-ons page moved to `#/addons`; `jarvis setup` offers the tour at its end.
- Added: update channels. `jarvis update --channel beta` (dashboard Updates page, English and Hebrew) follows every green `main` build as a signed beta; beta builds show BETA in status and the dashboard header. Beta publishing is off until the owner sets `ENABLE_BETA_PUBLISH`.
- Added: stable releases are tag-driven and gated by a manual test sign-off (`jarvis release signoff`, dashboard page Release sign-off), see docs/RELEASING.md; `core/tools/release_rollback.py` with a dry run.
- Changed: CI runs the Windows Python tests in 3 shards and the real-browser tests as their own job with one retry; every green `main` builds a signed candidate (not published).
- Added: SECURITY.md, Dependabot, CodeQL (Python), secret scanning exclusions for the planted test tokens.
- Added: Issues registry. JARVES lists the problems he finds in himself (self-test, speed test, diagnostics and crash findings, tools that keep failing, crashes, dashboard script errors), de-duplicated and content-free: `jarvis issues`, dashboard page Bug reports (English and Hebrew) with a Report button that shows the exact text, asks in the approvals popup, then files an issue labelled `from-diagnostics`. Voice: "what issues do you have?". Issues the CHANGELOG lists on a Fixes line are marked fixed in that version. See docs/BUGFIX_LANE.md and `core/tools/triage_issues.py`.
- Added: self-management (`jarvis manage ...`, `jarvis undo`, the Self-management dashboard page and the `self_manage` skill): JARVES shows what is connected or broken and, only after an on-screen approval with BEFORE and AFTER, changes its own files, memory, features, skills and settings, with backup and undo; secrets, generated files and safety switches are out of reach.
- Added (V5, software only, owner-approved): Goals and Adaptive Memory (confirmed preferences in the model instructions, goal-done line), goal executor (plans of Skills with approvals), proactive offers and signals (continuity across phone and desktop), Development Manager and Development status (read-only; changes ask first), web Chat page and `jarvis web`, phone access over Tailscale, optional phone approvals, `jarvis connect` unset-only checks.
- Added: wake train production loop and restricted-token isolation (`winsandbox`), phone end-to-end tests, setup Hebrew console fix that keeps existing settings, manual test sheet for 4.0.0 and the owner upgrade check.
- Changed: privacy and delegation defaults for NEW installs (OD-12, OD-15); model-facing tool descriptions trimmed by at least 30% (prompt budget unchanged).
- Fixed: bug sweep 4.1 part 1 (leaked memory.db handles, quiet-wait and warm-up in tests, suite V04/V05/S12 triage).
- Docs: release and test system plan, quality review, migration audit and owner decisions, GREEN-API/Flow API evaluation.

## 4.0.0 (release candidate, not yet released)

- Added: privacy hardening and redaction audit; independent security re-review fixes; compact approvals popup with signed answers; client-mode GPT-Live delegation with a strict budget; Agent Orchestrator for Claude Code and Codex; WhatsApp bridge (read first, approved sends); conversation recall; Health page and Self-Diagnostics 2.0; performance budgets and nightly CI; owner-trained wake head; dashboard in English and Hebrew with accessibility fixes.
- Added (continued): assisted dialing (approval, then a `tel:` link; off by default); installer silent upgrade, downgrade guard, signed update manifest with a `previous` entry, `jarvis update --rollback`; the WhatsApp bridge is pinned by SHA256 in `packaging/bridge_pin.json`.
- Version: `core/pyproject.toml`, `jarves.__version__` and `core/uv.lock` were 4.0.0 (now 4.0.1); installer builds are 4.0.0.N.
- Changed: `jarvis status` exit 4 means headless; `jarvis features on` no longer refuses an unavailable feature; Arabic and Russian dashboards removed; update source repository is `igalgerman/jarves`; ticket drafts stay local by default; Pear history trimming off by default.
- Docs: new [user guide](docs/USER_GUIDE.md) (English and Hebrew), docs index, release notes.

## 3.x

SW-V3: profiles, approvals, Google Calendar/Gmail/Contacts, camera and face recognition, browser, timers and lists, scheduler, dashboard, self-check, installer. See [docs/JARVES_SW_V3_WORK_PACKAGE.md](docs/JARVES_SW_V3_WORK_PACKAGE.md).

## 0.1 and SW-V1/V2

Core, face, GPT-Live voice, memory, YouTube Music. See [docs/SW_V1_WORK_PACKAGE.md](docs/SW_V1_WORK_PACKAGE.md) and [docs/JARVES_SW_V2_IMPLEMENTATION_SPEC.md](docs/JARVES_SW_V2_IMPLEMENTATION_SPEC.md).
