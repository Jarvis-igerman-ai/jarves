> **Historical, superseded by [CHANGELOG](../CHANGELOG.md) (V1 delivered).** Kept as evidence and owner decision record; do not use it as the current plan. Index: [DOC_MAP](DOC_MAP.md).

# JARVES — SW-V1: approved end-to-end software work package

**User authorization: 18 October 2026.** The user is going away and explicitly wants the engineering agent to make substantial progress **without asking approval at every intermediate step**, starting immediately after the delivered R5b CAD milestone. This document is **an approved bundled scope** for the next implementing Claude Code/Codex session; it supersedes earlier "parked/not authorized" wording in Issues #2, #5, #6 and #7 **for the specific work described below**. The active work is authorized in [NOW](NOW.md).

**Objective:** a **real, maintainable personal assistant product** called JARVES, not a throwaway AI demo or merely a responsive 3D model. The assistant is designed and run on the development PC now and portable to Raspberry Pi/Linux using the existing Core/DevicePort boundary. It should speak and understand Hebrew, support natural interruption where technology permits, express states on the Godot robot face, use real AI tools/search, retain user-controlled memory, support a small modular Skills interface and control actual music playback with the existing **YouTube Premium / YouTube Music** subscription if a compliant, reliable path is established. Additional paid Spotify Premium is **not** an option. Gmail/Calendar are **future optional Skills**, not part of this bundle.

**Scope limit:** "build all the software" means take the work **as far as genuinely possible without credentials, extra paid services, hardware purchases or external account access**. Make genuine functioning slices, NOT simulated implementations presented as complete. Full physical audio/camera performance, on-device thermal characteristics and third-party authorization require future gates. Never fabricate successful integration.

## Execution policy — move fast without cutting corners

- **One implementing engineer, one approved multi-issue work package.** Research briefly and then **implement**, committing and documenting after each coherent phase, **continue automatically** through the phases when feasible. Do **not** stop after research to seek permission for #6/#7, or after a minor feature, unless an actual blocker, material safety/privacy issue, paid spend, missing permission/credential or conflicting user decision is encountered. Finish with a single user review.
- Respect existing [AGENTS](../AGENTS.md), [project principles](PROJECT_PRINCIPLES.md), [architecture](ARCHITECTURE.md), [controls/diagnostics/Skills requirements](archive/JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md) and [review gates](ENGINEERING_REVIEW_GATES.md). Read relevant files on demand, not the whole repository. Use a short initial plan, focused tests and <=2 distinct attempts per hard debugging blocker; document and bypass an *independent* blocked component where safe rather than stalling all work.
- **Reuse** proven SDKs, agents, speech and player projects; no custom giant harness, media streaming engine, plugin marketplace, microservice swarm or speculative refactor. Use the simplest robust tools compatible with Windows development and Raspberry Pi 5 Linux later; document actual model/service names, licensing, cost, API availability and portability rather than guessing.
- Keep AI/cloud **API usage metered and explicitly configurable**; report estimated cost drivers, avoid expensive unattended traffic or tests hitting paid providers repeatedly. Do not buy credits or start subscriptions. Work with user-supplied environment credentials only; never ask a remote agent to harvest secrets, read personal mail, copy browser session cookies or auto-login to Google. Do not commit secrets.
- Keep the **R5b mechanical design as frozen development baseline**. Known camera mounting bosses, door hooks, speaker strap, print-bed constraint, nut strength, power/heat/acoustics remain tracked for future hardware gate #8; no fabrication, vendor contact, hardware purchase or unapproved mechanical changes.
- Publish exact source, real runs/screenshots when available, unit/integration/fault tests, run instructions and honest blocker list. Each issue may close on proven acceptance, otherwise leave open with narrow residual items. Update HANDOFF and linked Issues per phase; **don't duplicate work across agents**.

## WP0 — Enable actual continuous integration (Issue #2) — prioritize early

1. Review the existing **parked** `ci/cad-smoke.yml`, validate referenced FreeCAD/Blender/Godot versions/downloads/dependencies, remove brittle commands and fix negative exit-code behavior; limit runner cost with `permissions: contents: read`, concurrency, `timeout-minutes`, no secrets and light tests.
2. Install a working workflow at `.github/workflows/cad-smoke.yml` (or split jobs if sensible) with push/PR triggers. Add minimal **software** CI (`pytest` + mock/offline integration checks) when SW code lands; use pinned dependencies/OS as appropriate and keep expensive downloads/renders behind appropriate gates.
3. **Run GitHub Actions for real** and inspect actual status/logs; report run URL, failures and fixed causes. A YAML file in the repository is **not** proof of active CI.
4. If GitHub token lacks `workflow` write permission, **do not loop, bypass protections or falsely claim success**. Commit the corrected parked workflow and tests, record the precise permission needed (e.g. `gh auth refresh -h github.com -s workflow` when applicable), and **continue SW work anyway**. CI activation remains a single clearly marked external blocker for the user.

## WP1 — Focused technology decision (Issue #5)

Research only the short-list of genuinely necessary **maintained** options: OpenAI Agents SDK/Responses vs lightweight tool orchestration, supported realtime or STT+LLM+TTS Hebrew voice stacks and interruption, Godot↔Python IPC, SQLite-based persistent state/memory, tool-based web search, safe Skills/MCP registration, reusable music player projects. Check licensing, documentation, platform support, costs, memory/latency and recoverability.

**Music candidates first:** [Pear Desktop](https://github.com/pear-devs/pear-desktop) and its API Server/plugin with Linux ARM64 support; [Music Assistant](https://music-assistant.io) YouTube Music integration; reputable existing Skill/MCP integrations only after security, maintenance and ToS review. The user already has YouTube Premium, **not Spotify Premium**. YouTube Music has no broadly documented official complete third-party playback API; do not assume unofficial tools are stable/permitted, and do not bypass DRM, extract media streams, or treat a prompt-only Skill as an actual working player. Determine a defensible **voice→specific song→actual playback** strategy plus pause/resume/skip/volume/status, or label the part blocked with alternatives.

**WP1 exit:** brief evidence table with one selected integrated stack, major risks and implementation steps. **Continue to WP2 immediately**, not another review-only gate.

## WP2 — Production-oriented Python Core, Godot link, diagnostics (Issue #6)

Build working, modular and version-controlled software, not scaffolding without behavior:
- Python Core with typed state/event contracts, small practical config and entrypoint/installer instructions for Windows and Pi/Linux; simulated and future physical input/output only through DevicePort. Connect the **existing Godot scene** via a reliable lightweight local IPC channel; implement startup/shutdown, reconnection and truthful states.
- Agent/tool invocation, genuine user-scoped memory in SQLite or similarly lightweight durable store (explicit memory read/update/delete and privacy controls), context management and built-in web search **when authorized provider credentials/tools exist**. Offer clear actionable errors when unavailable; no fake web results.
- Minimal Skills/tools registration, validated arguments, permissions and user confirmation for any external side effect; straightforward provider adapters; avoid excess plugin infrastructure. Include a tiny safe built-in tool + tests establishing that a second Skill can be registered without Core rewrites.
- Persistent bounded structured logs (rotation, level/component/correlation ID), health/errors, secrets redaction, status/diagnostic export and offline troubleshooting path **from the first working slice**. Build a small authenticated local admin/status surface only to the extent necessary for practical troubleshooting; no unsecured exposed listener.
- Runnable startup and targeted automated success/failure tests, including broken/absent DevicePort, Godot disconnect/reconnect, invalid configuration, privacy/mute truth and restart of persisted state.

**WP2 exit:** an actual end-to-end PC workflow `Godot face ↔ Core ↔ mock/available devices` with logs and reproducible tests; proceed to WP3.

## WP3 — Actual voice interaction and face behavior (Issue #7)

- Replace mock speech with an accessible existing speech/agent stack, supporting **Hebrew input and output**, listening/thinking/speaking/idle, voice interrupts/barge-in where evidence allows, truthful fallback when no mic/cloud/credentials. Do not claim consumer far-field acoustics without physical microphone testing. Prefer reuse of Godot face logic and minimal visuals rather than remodelling Pebble A.
- Hook real tool requests into Core; demonstrable live question using tool/web search **only if credentials/services are available and charges explicitly authorized**; otherwise verified offline tool calls and a documented one-time user credential setup step. Instrument latency/errors and provider spend ceilings.
- Add real audio DevicePort adapter for existing Windows mic/speakers if accessible, configurable Linux adapter boundaries, push-to-talk fallback when hotword is not reliable; avoid uncontrolled always-recording / spying.
- Run actual testable voice→Core→expression→speech path if environment allows; otherwise mark precisely which step requires device or credentials, not 'done'.

**WP3 exit:** a functioning desktop personal assistant core/face with voice where prerequisites exist, provider setup and fallback documentation; continue to WP4.

## WP4 — First real external-action Skill: YouTube Music

- Reuse a suitable existing **YouTube Music / YouTube Premium** player integration if it survives code/security/ToS/ARM64 review. Prefer an ordinary signed-in playback session with secure per-user login **performed by the user**, rather than manipulating account passwords/cookies. Where appropriate integrate Pear Desktop's local controls / MPRIS; assess if specific-track search/playback is actually supported.
- Implement the provider-neutral `MusicSkill` interface (search/play track or artist/playlist, pause, resume, next, stop, volume, now-playing/status, errors, timeouts) with a **real adapter**, and mock fake player only for tests. Explicitly support an ordinary desktop player during PC development and document Pi/Linux target adaptation; no unapproved Spotify subscription.
- End-to-end spoken request for a song should play audible music in a real player, or **explicitly report** that actual playback is blocked by provider account, browser, hardware or terms. Never call mock playback 'working real YouTube Music'. Reuse one working adapter for future Music Skills; avoid uncontrolled clickbots, web scraping or stream extraction.
- Add focused tests for offline/login/session/player crashes and stop controls. Defer real speaker→mic echo / closed case loudness until device validation.

**WP4 exit:** functional music Skill to extent feasible and a precise gap list if a live authenticated player cannot be accessed without the user.

## WP5 — Integration hardening, real CI evidence, handoff

- Verify clean setup on a new development checkout where feasible, clear `README` run command, config template, dependency lock, minimal user onboarding for provider credentials/account logins and tests. Exercise full happy path plus offline/provider failure and restart.
- Actual source-to-UI evidence (Godot screenshots or run logs) and a **real GitHub Actions success/failure URL** if permission enabled; otherwise explicit blocker and a ready, reviewed workflow.
- Verify no token, raw audio, user transcript or OAuth credentials in repo/logs; no uncontrolled network exposure. Validate that memory persists and can be cleared by the user; music controls give truthful status.
- Record accomplishment vs blocked vs next hardware/user action, with a short nontechnical Hebrew-ready product walkthrough. Update `docs/HANDOFF.md`, `docs/NOW.md`, Issue #2/#5/#6/#7, commit stable phases and stop at **one final acceptance review**.

## Approval vs hard stops

**Approved now:** research and implementation of WP0–WP5, local dependency installation needed for these scopes within reasonable resources, routine file changes, CI workflow activation if permissions permit, focused no-charge/offline tests, commits, and moving among these software phases autonomously.

**Still requires user interaction or separate approval:** API credits/paid model charges above a pre-existing explicit budget, new subscriptions, Google/YouTube or other account OAuth/login, reading personal accounts, downloading/using protected music in unapproved ways, real-world email/calendar actions, hardware purchases, printing, exposing remote network interfaces, destructive removal of user data, and any security-critical change. If blocked, continue independent parts instead of inventing success.

**Meaning of progress:** do not claim “full software finished” until the intended PC interactions and approved real integrations actually run. The deliverable can be a complete tested core with well-documented credential/account-dependent limitations while the user is absent.

## References

- [#2 CI](https://github.com/igalgerman/JARVIS/issues/2)
- [#5 library research](https://github.com/igalgerman/JARVIS/issues/5)
- [#6 Core↔Godot](https://github.com/igalgerman/JARVIS/issues/6)
- [#7 face & voice](https://github.com/igalgerman/JARVIS/issues/7)
- [#4 frozen R5b CAD baseline](https://github.com/igalgerman/JARVIS/issues/4)
- [music and assistant requirements](archive/JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md)
