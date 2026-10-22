> **Historical, superseded by [CHANGELOG](../CHANGELOG.md) (V2 delivered).** Kept as evidence and owner decision record; do not use it as the current plan. Index: [DOC_MAP](DOC_MAP.md).

# JARVES — SW-V2 Software Product & Engineering Specification

**Version:** 1.0 — 19 October 2026  
**Status:** Product requirements approved for planning; implementation must be staged and coordinated with the current GitHub work.  
**Repository:** `igalgerman/JARVIS`  
**Reviewed baseline:** `main` at `38948e2` on 19 October 2026. **Recheck HEAD and in-progress branches before modifying code.**  
**Related document:** `FUTURE_IDEAS.md`.  
**One open decision:** Update policy (Q16). The owner's response was `ר`, not an unambiguous selection. **Do not implement unattended installation of updates until clarified.**

## 1. Vision and constraints

Turn the existing JARVES into a persistent, proactive **personal computer agent** first, with the **same functional product** ultimately deployable on a Raspberry Pi 5/Linux-based physical robot. The Core runs in the background without a visual window, responds to local wake-word activation, can show itself or hide on command, maintains notes and schedules, works with Google Calendar, prepares reports, generates approved files inside a sandbox, diagnoses problems and proposes improvements, and exposes a local web Dashboard for settings and operational control.

**All features must have a Raspberry Pi support path.** For every feature, the Dashboard must distinguish: `verified_on_device`, `implemented_unverified`, `planned`, `blocked`, `not_applicable` (only for device-specific UI mechanics with an equivalent control). Each non-verified Pi capability must have a real blocker description and a linked roadmap task; missing support may never silently disappear from the product. Do not present passing x86/Linux CI as physical Pi verification.

**Deployment now:** One independent Windows installation. **Deployment later:** one independent Pi installation with equivalent capabilities. Shared multi-device memory, jobs and synchronization are important *future ideas*, not a prerequisite for this release. Both installations must be possible without forcing a central service. Shared system proposal belongs in `FUTURE_IDEAS.md`.

**Avoid duplicate infrastructure.** Extend the current Python Core, SkillRegistry, Godot frontend, DevicePort, SQLite/persistence, diagnostics and CI instead of creating a second agent framework, skill loader or service stack. The owner reports a Markdown-file-based Skill mechanism is currently being developed: inspect that work before starting; merge its approach rather than competing with it.

## 2. Confirmed product decisions

| ID | Owner decision | Implementation implication |
|---|---|---|
| D01 | PC and Pi independent for now; shared system later | Maintain portable contracts; do not implement sync in V2 |
| D02 | Dashboard accessible via PC and phone on the **home LAN** | Local authenticated responsive web UI; no public internet exposure |
| D03 | Start automatically with Windows and run invisibly in background | Tray, autostart, Core lifecycle independent of Godot |
| D04 | Proactive and spoken alerts subject to Dashboard settings | Quiet hours, alert channel/priority and notification controls |
| D05 | Google Calendar: everything discussed **except event deletion** | Read, search, create, update, remind; no delete tool or indirect deletion |
| D06 | Scheduled tasks save reports/results, which JARVES can later read aloud | Durable jobs, run history and report retrieval |
| D07 | Files and notes live in a private sandbox; external folder access requires approval | Scoped workspace grants, paths and storage quotas |
| D08 | Three selectable presentations: **Face, 2D avatar, 3D avatar** | One shared expression/event contract; optional hidden mode |
| D09 | Windows installer first; Pi installer/deployment later | Build installable Windows artifact in CI; retain three latest builds |
| D10 | Usage monitor covers **ChatGPT plan and OpenAI API** | Meter per actual provider/limit; 75%/90%/100% warnings; footer widget |
| D11 | Diagnostics/proactive proposals notify the user and need approval by default | Dashboard-configurable bounded autonomy for safe actions |
| D12 | Voice selection from many available voices by UI or speech | Provider capability-aware voice catalog and settings |
| D13 | When quota information is unavailable: explicitly Unknown, not fabricated | No imaginary percentage, threshold or reset time |
| D14 | Missed scheduled jobs should run once after reboot/startup | Catch-up semantics, idempotency and maximum staleness |
| D15 | JARVES may **create and install new Skills with approval** | Review, test, scoped grants and disabled-by-default install |
| D16 | Update policy not confirmed (`ר` response) | Safe temporary default: notify and request approval; no auto-install |
| D17 | Automatic local backup | Database-consistent backups, recovery and restore tests |
| D18 | Assistant's name must be user-selectable and changeable | Separate display name, spoken name and actual wake phrase support |

### Additional core rules

- Hebrew-first natural speech; Dashboard may later add languages. Never couple the Core to a visible face.
- Preserve known working software behavior, budgets and confirmation policy. Never silently switch to billable API after a ChatGPT-plan quota or failure.
- No Google account access until the user explicitly authorizes OAuth. No unapproved paid providers, remote access, external writes or destructive file operations.
- Deliver working increments with tests and traceability; do not build a large inert architecture in advance.

## 3. Existing implementation to reuse

At the reviewed baseline, the repo already contains:

- `core/src/jarves/app.py`: the Core lifecycle, voice orchestration, Godot link and service registration.
- `core/src/jarves/skills/__init__.py`: tool/Skill registry, typed arguments, permissions, confirmation and timeouts.
- `core/src/jarves/skills/builtin.py` and `music.py`: built-in and Pear/YouTube Music Skills.
- `core/src/jarves/config.py`, `core/config.example.toml`: validated settings.
- `core/src/jarves/voice/`: local wake, Live/Realtime voice, audio, TTS/STT.
- SQLite-backed state/memory, budget, health and structured logs.
- `godot/face/`: renderer and protocol client, plus simulator viewer.
- `.github/workflows/jarves-checks.yml`: Windows/Linux Core tests and Godot/Blender/CAD/Core↔Godot E2E.

The **Markdown-based dynamic Skills system was not present in the reviewed `main` tree**, but may exist in a newer branch or active worktree. Before coding, inspect relevant commits/branches and open work. A Markdown instruction is **not** by itself an executable permission-bearing tool; skill activation must still pass central policy checks.

Keep one Python Core orchestration owner. A Dashboard and renderer are clients; neither should run their own scheduler/agent state. Keep existing structured Core↔Godot event/control boundaries. DevicePort remains the hardware abstraction. Source-of-truth, no needless services, no broad refactors.

## 4. Runtime, visibility and portability

### 4.1 Windows always-on

- User option to start at login; one Core instance per user.
- Core stays active when the Godot window is hidden or closed; change the current desktop shutdown-on-window-close interaction carefully, with dedicated regression tests.
- System tray menu: Dashboard; Show/Hide; Software Mic Mute; Pause Proactive Alerts; Status; Quit; Diagnostics.
- Wake engine should listen locally without maintaining a billable cloud voice connection while idle. After activation, create a bounded voice session and return to local wake at idle. Existing voice behavior and budgets remain authoritative until explicitly changed.
- Commands such as **"show yourself"**, **"hide yourself"**, **"change your voice"**, **"your name is X"** must work without rebuilding the application and without exposing hidden admin controls by voice when approval is needed.
- Separate four states: display hidden, software mic muted, agent notifications paused, Core stopped.
- Survive sleep/resume, network loss, headset unplugging, UI crash and new login; never spawn duplicate Core/voice sessions.

### 4.2 Linux ARM64/Pi contracts

- Same Core, scheduler, notes, report and Skill APIs; platform/device adapters only for boot, tray equivalents, local audio, secure credentials, notifications and rendering.
- Headless mode is first-class. Physical mute (when built) has priority over software settings and is reported truthfully.
- All resources must be deployable under non-root accounts, with reasonable CPU/RAM/thermal behavior once physically tested.
- Renderer portability is not assumed. If 3D cannot run at target performance, track a blocker and an adapter/performance remediation task rather than claiming support.
- Pi UI management through authenticated local Dashboard, including a paired phone; no touchscreen dependency.

## 5. Capability and Skills registry — source of truth

Every visible feature and Skill needs a stable record:

```yaml
id: music.play
name: Music playback
category: skill
enabled: true
permissions: [action]
configuration_schema: music-settings-v1
targets:
  windows:
    status: verified_on_device
    evidence: '<commit / test / QA URL>'
  linux_arm64_pi:
    status: planned
    blocker: 'Pear/MPRIS ARM64 real-device playback not yet verified'
    roadmap_issue: '<actual issue URL once created>'
last_checked: '<timestamp>'
```

Schema is illustrative; adapt to the active Markdown Skills implementation rather than creating a parallel catalog.

Required Dashboard table columns: feature, enabled state, operational health, settings, permissions, Windows status, **Pi status**, blocker and roadmap link. Filters: All / Enabled / Not ready on Pi / Needs attention. **Never fabricate issue URLs:** create an actual issue before linking one.

For every new feature, enforce a Definition of Done: working Windows behavior; permissions and toggles; error recovery; tests; portability story; truthful Pi status; corresponding issue/roadmap if Pi is incomplete; documented version and evidence.

## 6. Dashboard — pages and behavior

Prefer a lightweight responsive **local web application** attached to the Core's authenticated management API. Do not require Godot to show settings. Default bind to localhost; separately enable LAN access with pairing and authentication. No unprotected network listener or internet port.

### 6.1 Home

- Agent Core and local wake/voice states; current device/platform/version; Show/Hide visual controls; pause/quiet/mute statuses; next scheduled jobs; recent alerts; Health.
- Compact **usage strip at bottom**: ChatGPT and OpenAI API meters side by side, with source, limit/window and `Unknown` when a true percent cannot be obtained.
- Genuine network/device state and errors, not optimistic green badges.

### 6.2 Skills

- List installed and proposed Skills, enable/disable individually, edit validated settings, see requested permissions, configure providers, test/diagnose, check platform parity, see last error.
- Generated Skill workflow: propose → generate manifest/Markdown/code into isolated draft → summarize permissions and changes → static validation/tests → explicit owner approval → install **disabled** → owner grants bounded capabilities and enables it. Preserve rollback information.
- Disablement applies to voice, Dashboard, scheduler and model tool invocation equally. No Skill executes outside central grants, even with a permissive model prompt.

### 6.3 Identity and Voice

- Change JARVES's name/display name/spoken self-reference; spoken name change must not imply automatically trained activation phrase.
- Voice catalog listing provider, supported languages, voice names, relevant costs/quotas and available previews; voice selection from Dashboard or voice command.
- If changing voice/provider would increase costs or change credentials, require explicit approval. Store choice persistently and recover on unsupported voice.
- Wake phrase capability must be shown separately: current supported phrase, platform test status, proposed custom-name support and blocker.

### 6.4 Appearance

- **Three choices**: original Face, 2D avatar, 3D avatar. Separately choose Hidden (no renderer). Allow switching while Core continues.
- One consistent expression/activity/speech-level event contract consumed by each renderer; independent Core/voice lifecycle.
- Per-mode Windows/Pi status with actual evidence, performance limits and a linked Pi gap.

### 6.5 Jobs / Reports / Notes / Files

- Human-readable schedules + cron/recurrence controls; edit/pause/run now/history; clear next local run time and catch-up status.
- Saved reports with timestamps, source references, partial/failure status and "Read aloud" action.
- Searchable notes with title/tags/created/updated metadata, editable and exportable.
- Authenticated workspace browser for approved file generation, download and folder grants.

### 6.6 Calendar

- Google OAuth status; selectable calendars; upcoming events and reminders; create/update confirmation; no event deletion buttons/tools or indirect actions.

### 6.7 Proactivity / Calibration / Autonomy

- Configure quiet hours, urgency and delivery (quiet popup vs speech), repeated-alert limits and Skill-specific proactive behavior.
- Diagnostics can propose calibration improvements (mic sensitivity, wake threshold, repeated failures) with evidence and test result. Default: proposal requires approval.
- Autonomy levels: `observe_only`, `suggest_and_ask` (**default**), `approved_low_risk`. More autonomy must be explicit, scoped, reviewable and reversible; never overrides Calendar no-delete or other hard guardrails.

### 6.8 Usage / Backup / Updates / Diagnostics

- Usage sources/estimates/unknown values, threshold settings/history and reset windows; backups/restore; current app version; available updates; logs and sanitized diagnostic bundles.

## 7. Durable scheduler and proactive operation

Implement a small, persistent scheduler rather than a heavyweight task broker. It must support one-off and recurring jobs, cron-like expressions, local timezones and DST, notifications, report generation and safe catch-up after reboot.

### Jobs

A job record should include: ID, owner, name, enabled, recurrence/timezone, next/last run, last outcome, idempotency key, task description, permitted Skills, cost budget, retry policy, output retention and optional delivery channels.

- Parse spoken instructions into a schedule preview and ask for approval before activation.
- After reboot, run at most **one** missed occurrence per job. Label it "late" and enforce a configurable maximum age so outdated reports/reminders are not blindly generated. Ensure no duplicate external actions after a crash.
- Prevent overlapping runs unless explicitly enabled; bounded retries with backoff and observable errors.
- Timezone default configurable, with DST spring-forward/fall-back behavior tested; do not assume UTC/Israel always share the same hour.
- Avoid opening metered voice sessions simply to poll jobs. Prefer local timer/event triggers.
- Every report is saved and retrievable via Dashboard or speech: e.g. "read this morning's AI report" reads an existing artifact rather than regenerating it automatically.

### Proactive alerts

An event-driven engine (not endless LLM polling) may act on upcoming appointments, due jobs, threshold crossings, calendar changes, repeated errors or calibration recommendations. Respect user-defined quiet hours and escalation. If display is hidden, use authorized OS notification or voice as configured; record pending alerts when output is unavailable. Use deduplication and rate limits.

## 8. Google Calendar Skill

Owner explicitly wants read, search, reminders, **create and update**; **no event deletion**.

- Use Google OAuth with minimum necessary scopes, user-led consent and secure token storage; don't access account content until connected.
- Show target calendar, title, date/time/timezone and scope of recurrence before approved writes; read back committed changes and report truthfully.
- Default confirmation for calendar writes; Dashboard may later grant narrow reusable scopes under autonomy rules. Hard delete ban remains enforced for model calls, UI and generated Skills.
- Decide whether recurring edits affect one occurrence or series; never silently change all occurrences.
- Handle auth expiry, quota errors, disconnected network and webhook/polling gaps; store and deduplicate reminders.
- Support Pi with an OAuth flow that can be authorized on a paired PC/phone, avoiding reliance on a robot touchscreen.

## 9. Notes, file sandbox and Skill creation

- Notes as first-class persistent records (title/text/tags/timestamps), searchable, editable and exportable as Markdown. Deletion is subject to existing destructive-action approval policy.
- File generation only in per-install **workspace**; approved folder grants are explicit and revocable. Guard path traversal, symlinks, overwriting protected files, excessive disk usage and unapproved executable launch.
- A generated Markdown Skill does not automatically receive disk/network/Google calendar/process privileges. Review requested permissions and source; install disabled, then explicitly enable.
- Test sandbox boundaries and all invocation paths (voice, scheduled job, UI and agent), including a malicious/incorrect generated Skill.
- Keep generated files and private memory separate from application binaries so installs/updates do not overwrite user data.

## 10. Usage Monitor Skill and alerts

**Targets: ChatGPT subscription plan + OpenAI API only (initially).**

Each displayed meter has: provider/source, exact metric and unit, used, total limit (if known), time window/reset (if known), last verified measurement, confidence and measurement category (`provider_authoritative`, `app_local_counter`, `estimate`, `unavailable`).

- **75%, 90%, 100% threshold alerts** are emitted once per threshold per *known* meter/window, persisted across reboot; no duplicate storm and no fake reset.
- Bottom Dashboard footer shows two compact provider indicators. If denominator/limit/window is unknown, show `Unknown / unavailable` rather than a fake usage percentage. A separately labeled local JARVES counter may still be shown.
- Never conflate number of tokens, requests, realtime minutes, monetary API cost, and ChatGPT subscription limits. Track them separately.
- ChatGPT plan remaining quota and reset may be unavailable to the integration. **Do not scrape UI, invent account-wide caps or label JARVES-only usage as full account usage.**
- Reuse provider-returned usage and Core's existing budget records, plus provider-authorized read-only usage APIs where available. Refresh without expensive model calls.
- Alert delivery obeys general notification settings but 100% limit state remains visible in Dashboard.
- Do not automatically charge the API as a fallback when the user's ChatGPT plan runs out.

## 11. Packaging, versioning and retention

- First shipping deliverable: **user-friendly Windows installer** containing Core, necessary renderer assets and a supported startup mechanism. Another PC user must be able to run setup without Python/Godot toolchains.
- On every eligible successful `main` CI run, build a real installable package **after gates pass**; publish GitHub artifact/release reference, commit hash, build time, SHA256 and platform/version metadata. Do not publish untrusted PR outputs as stable releases.
- Retain latest **three successful installable packages per platform/channel**, while preserving any explicitly pinned rollback release. Retention must not delete unrelated developer artifacts or history. If GitHub permissions cannot reliably delete artifacts, document retention enforcement until approved.
- Clean install, first-run wizard/config, credential setup, background autostart, tray, Dashboard, show/hide, upgrade and uninstall should get smoke tests. Never ship developer secrets, auth tokens, personal databases or logs.
- Linux ARM64/Pi package/service delivery is a later milestone, but CI metadata must show target status from the start.
- Update policy remains **OPEN** (Q16). For now, notify of new version and ask approval before install; no silent auto-update. Allow rollback and backup before migrations.
- Signed builds only when legitimate code-signing credentials and a maintained signing workflow exist; never claim signing without proof.

## 12. State, backup, recovery and security

- Prefer the existing SQLite database + validated config; do not introduce a new database for each feature.
- Persist names, voice choice, renderer mode, Skill settings/grants, schedules/history, notes, report references, usage-alert deduplication and proactive proposals.
- Automatic **consistent local backups**: proposed adjustable default seven daily + four weekly points; include important settings, user data and notes. Use SQLite-safe backup mechanism, verify archives and test restoring into isolated test profile. Never export live OAuth/API tokens in an unencrypted shareable backup.
- Before database migration/update, checkpoint with verified restore path; on failure preserve prior user data and show recovery guidance.
- Dashboard LAN mode: secure pairing/authenticated sessions, CSRF protection on writes, least privilege and rate limiting; no open internet exposure. Keep audit of configuration changes, approvals and autonomy-granted actions.
- Logs and shared diagnostic bundles are size-bounded and redact credentials and sensitive content by default.
- Explicit failure recovery when AI, wake model, mic, audio, Google, internet, Pear or display is unavailable. Offline operation should still offer feasible local functionality.
- Protect user data from installers, tests, untrusted Skills, and automated calibration.

## 13. Milestones (approve packages, not individual tiny steps)

### M0 — Inventory and integration gate

Inspect latest code **including in-flight Markdown Skills work**, open Issues and current `docs/NOW.md`; build feature/platform catalog and **actual** Pi-roadmap issues. Map potentially conflicting code review Issue #16. No duplicate Skill system. Deliver dependency-aware milestone plan and first bounded package.

**Exit:** source owners identified, Windows/Pi status matrix exists, no invented claims or tasks.

### M1 — Always-on desktop Core + dashboard foundation

Background/tray/autostart; close/show/hide Godot without stopping Core; locally authenticated Dashboard foundation; persisted settings, runtime health and Pi support table.

**Exit:** Windows agent and local wake remain operational with no visible UI; Dashboard from paired LAN phone; Core restart and audio recovery tests pass.

### M2 — Configurable Skills, identity, voice and appearance

Merge/extend existing Markdown Skill mechanism, per-Skill toggle/settings, approved Skill-generation flow; change assistant name and provider-supported voice by speech/UI; Face/2D/3D choices and hidden mode. Mark incomplete renderers/Pi capabilities accurately.

**Exit:** disabled Skills cannot execute from any route; identity and voice choices persist; visual switching does not affect Core.

### M3 — Schedules, notifications, reports, notes and sandbox

Persistent scheduler + once-only catch-up, local notifications/spoken reminders/quiet hours, report history and readback, Notes Skill, protected workspace and optional folder grants.

**Exit:** a missed daily report runs **once** after power-on, is stored and can be read aloud; sandbox escape tests fail safely.

### M4 — Google Calendar + autonomous calibration (bounded)

Authorized Google Calendar read/create/update/reminders; proactive diagnosis and evidence-based suggestions; configurable low-risk autonomy grants.

**Exit:** real account actions only after explicit OAuth/permissions; no Calendar delete path; suggestions are auditable and never rewrite settings without the appropriate grant.

### M5 — Usage tracking, alerts and backup

ChatGPT-plan/API authoritative/local/unknown meters, 75/90/100 threshold deduplication, compact footer, local backups and restore verification.

**Exit:** unknown quotas do not create fake percentages or warnings, and backup restores data reliably.

### M6 — Distributable Windows installer + CI retention

CI packages after success, installer build validation, clean install/upgrade, provenance/checksum, last-three retention, opt-in update behavior and recovery.

**Exit:** a second Windows PC can install and run JARVES without developer build tools or the original user's secrets.

### M7 — Physical Pi parity (future execution phase)

Linux ARM64 installer/service; real audio/voice/display integration; Pi resource/acoustic testing; resolve every planned/blocked feature with evidence, tracked scope or explicit owner decision.

**Exit:** compatibility flags reflect measured Pi operation; no unverified Pi feature called supported.

**Sequencing nuance:** a *minimal* Windows package smoke job can be delivered before M6 if it helps make early software builds testable; do not wait until end to discover installer limitations.

## 14. Required tests and acceptance scenarios

| Scenario | Windows unit/CI | Windows human/real | Linux ARM64 | Pi physical |
|---|---|---|---|---|
| Core stays alive after hidden/closed Godot | Required | Required | Required when target ready | Required before verification |
| Wake without visible UI | Simulated contract | Real microphone | Contract | Real mic/acoustics |
| Assistant rename + voice change | Required | Spoken commands and UI | Required | Real audio check |
| Face / 2D / 3D switching | Godot contract | Visual review | Import/render checks | Hardware performance |
| Skill disabled/approved/generated | Policy regressions | Approval UX | Policy regressions | End-to-end smoke |
| Scheduler DST/reboot/catch-up | Fault injection | Real restart | Required | Real reboot |
| Google Calendar read/create/update/no delete | Auth/API mocks | Owner-authorized | API contract | Authorized smoke |
| Report saved and read back | Required | Speech readback | Required | Audio readback |
| Sandbox notes/files grants | Path/fault tests | File preview | Required | Storage/perms |
| Thresholds 75/90/100 and Unknown | Clock/meter tests | UI review | Required | Dashboard check |
| Auto-backup and isolated restore | Required | Restore drill | Required | SD/SSD restore |
| CI installer and retention | Artifact test | Clean install | Platform target pending | Install target pending |

Do not trigger billable live voice testing indiscriminately. Run focused offline tests first; use limited, documented real integration probes once the code changes justify them. Store evidence and the actual commit link in the relevant GitHub issue.

## 15. Definition of Done for a feature

A feature is Done only if it is functional on its claimed target, configurable where relevant, persisted, permission-controlled, recoverable on failure, appropriately tested, and visible in the Dashboard with **truthful Windows and Pi support**. If Pi is not yet verified, the issue/roadmap and blocker are linked and remain open; do not make future work disappear when the Windows subtask is closed.

Keep `docs/HANDOFF.md` and current issue up to date; close only completed items, preserve open blockers and avoid worktree/agent edit collisions. Changes to `docs/NOW.md` should establish which authorized work package is actually active; don't silently expand other milestones.

## 16. Implementation handoff to Claude Code / Codex

1. Read `AGENTS.md` and `docs/NOW.md`; use `docs/CONTEXT_MAP.md` for narrowly selected context. Then read this specification and `FUTURE_IDEAS.md`.
2. Compare latest branch/commit to reviewed baseline and inspect **ongoing Markdown Skills development**, existing issue #16 and any new work. **Do not build a second Skills loader.**
3. Produce a short architecture/diff plan and split this specification into actionable GitHub issues with Pi compatibility acceptance criteria. Create real roadmap issue links for missing Pi support.
4. Start with one **approved** M0/M1 vertical slice, then implement its related subtasks autonomously. One code-writing agent at a time. Do not stop for every tiny decision, but stop for credentials/OAuth consent, destructive actions, new costs and unresolved significant architecture decisions.
5. Targeted regression tests → actual CI → evidence/commit/issue updates → concise handoff. Don't make broad refactors, ship mock-only claims, spend unapproved API credits or merge unfinished behavior.
6. Preserve Windows and future Pi Core contracts, no hardcoded Windows-only business logic, no OS-specific global paths in the shared agent runtime.
7. Treat update policy (Q16) as pending until explicitly clarified by the owner.

**Reference code review:** https://github.com/igalgerman/JARVIS/issues/16