# Changelog

All notable changes to JARVES. Newest first. Details per release: [docs/RELEASE_NOTES_4.0.md](docs/RELEASE_NOTES_4.0.md). Dates are 2026.

## 4.0.1 (unreleased)

- Added: update channels. `jarvis update --channel beta` (dashboard Updates page, English and Hebrew) follows every green `main` build as a signed beta; beta builds show BETA in status and the dashboard header. Beta publishing is off until the owner sets `ENABLE_BETA_PUBLISH`.
- Added: stable releases are tag-driven and gated by a manual test sign-off (`jarvis release signoff`, dashboard page Release sign-off), see docs/RELEASING.md; `core/tools/release_rollback.py` with a dry run.
- Changed: CI runs the Windows Python tests in 3 shards and the real-browser tests as their own job with one retry; every green `main` builds a signed candidate (not published).
- Added: SECURITY.md, Dependabot, CodeQL (Python), secret scanning exclusions for the planted test tokens.

## 4.0 (unreleased, in integration)
## 4.0.0 (release candidate, not yet released)

- Added: privacy hardening and redaction audit; independent security re-review fixes; compact approvals popup with signed answers; client-mode GPT-Live delegation with a strict budget; Agent Orchestrator for Claude Code and Codex; WhatsApp bridge (read first, approved sends); conversation recall; Health page and Self-Diagnostics 2.0; performance budgets and nightly CI; owner-trained wake head; dashboard in English and Hebrew with accessibility fixes.
- Added (continued): assisted dialing (approval, then a `tel:` link; off by default); installer silent upgrade, downgrade guard, signed update manifest with a `previous` entry, `jarvis update --rollback`; the WhatsApp bridge is pinned by SHA256 in `packaging/bridge_pin.json`.
- Version: `core/pyproject.toml`, `jarves.__version__` and `core/uv.lock` are 4.0.0; installer builds are 4.0.0.N.
- Added: self-management (`jarvis manage ...`, `jarvis undo`, the Self-management dashboard page and the `self_manage` skill): JARVES shows what is connected or broken and, only after an on-screen approval with BEFORE and AFTER, changes its own files, memory, features, skills and settings, with backup and undo; secrets, generated files and safety switches are out of reach.
- Branches pending merge into the integration: assisted dialing (`v4/h-dialing`), installer, update and rollback (`v4/l-installer`).
- Changed: `jarvis status` exit 4 means headless; `jarvis features on` no longer refuses an unavailable feature; Arabic and Russian dashboards removed; update source repository is `igalgerman/jarves`; ticket drafts stay local by default; Pear history trimming off by default.
- Docs: new [user guide](docs/USER_GUIDE.md) (English and Hebrew), docs index, release notes.

## 3.x

SW-V3: profiles, approvals, Google Calendar/Gmail/Contacts, camera and face recognition, browser, timers and lists, scheduler, dashboard, self-check, installer. See [docs/JARVES_SW_V3_WORK_PACKAGE.md](docs/JARVES_SW_V3_WORK_PACKAGE.md).

## 0.1 and SW-V1/V2

Core, face, GPT-Live voice, memory, YouTube Music. See [docs/SW_V1_WORK_PACKAGE.md](docs/SW_V1_WORK_PACKAGE.md) and [docs/JARVES_SW_V2_IMPLEMENTATION_SPEC.md](docs/JARVES_SW_V2_IMPLEMENTATION_SPEC.md).
