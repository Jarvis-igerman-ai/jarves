# JARVES — control, future software layers, diagnostics and recovery requirements

**Decision record: 2026-10-17.** Product intent and future acceptance gates; **NOT permission to implement features now**. Current scope is always `docs/NOW.md`. No new hardware SKU, network service, backend, remote-access provider or purchasing choice is approved here.

**User's core requirement:** JARVES should feel like a conversational companion, **not a tablet**, yet remain controllable and diagnosable when speech, internet, Wi-Fi, a remote computer, or an internal service fails. A pretty face with no fallback controls or debug path is **not acceptable**. Default to simple, repairable and privacy-preserving solutions.

## 1. Confirmed product direction vs uncommitted details

| Topic | Product direction / requirement | Decisions still open |
|---|---|---|
| Visible display | Primarily a **face** (eyes, expressions, conversational feedback), not the primary settings UI. Keep current 5-inch round display as **provisional physical envelope only**. | Actual screen model; non-touch vs touch cost/availability |
| Touch | **Optional**: do not make any essential feature depend on touching JARVES's face. The currently researched 5-inch unit includes touch; its existence does not create a requirement to use touch. | Enable any touch gestures later only if useful and intentionally approved; USB may also provide screen power, so simply 'removing touch' is not necessarily removing USB cabling |
| Physical controls | A **safe shutdown/startup mechanism** and a clearly identifiable **physical microphone privacy/mute control** must be designed. Optional simple volume buttons/knob if reasonable. | Exact circuitry, switch placement, boot/wake behavior, physical vs software mute implementation; must validate hardware privacy semantics |
| Everyday control | Voice for normal use when functional; local phone/computer **web management UI** for settings, including voice choice, language/personality preferences, speaker volume, mic status, Wi-Fi/network setup, updates and diagnostics. Avoid a standalone native app initially. | Final admin UI scope and delivery stage, safe initial provisioning/pairing workflow |
| Privacy | A physical mute should be **verifiable** (e.g. disconnect mic path or reliably cut capture power/enable). UI software mute must **not** be described as hardware privacy. Hardware mute always overrides remote commands. Indicate actual state truthfully. | Electrical design and proof on selected mic board |
| Removable light ring | Upper-base status ring is visually preferred and removable, with replaceable opaque cover; its patterns may show state only when installed. Never require it for control/recovery. | Electronics, power budget, patterns, brightness and testing |
| Remote access | Support management **from another computer or phone on the home network**. Access from outside the home is a separate, explicit **secure remote access** decision, not automatically public internet exposure. | HTTPS/auth/pairing or a secure private VPN/relay approach, if external connectivity is ever authorized |
| Offline operation | Basic physical controls and diagnostics must remain usable **without cloud/AI or working Wi-Fi**. Clearly communicate degraded operation and what still works. | Offline capability of voice, update policy and local recovery setup |

**The word 'shutdown' means an orderly OS shutdown, not simply disconnecting live Pi power.** An on/off control needs a real start/power-restoration design and will require hardware validation before purchasing; don't promise reliable power-up from a software-only switch.

## 2. Existing engineering layers — no duplicate stacks

- **FreeCAD** owns the actual physical geometry and printable manufacturing parts, fastener access and ports.
- **Blender** owns 3D presentation assets: materials, finishes, lighting, renders.
- **Godot** owns the interactive Windows digital twin and later the expression/face scene if practical on Raspberry Pi. It is **not** the source for manufacturing dimensions.
- **Python Core** will manage state, conversation skills, orchestration and settings, separate from drivers.
- **DevicePort** will mediate physical/simulated mic/speaker, button, camera, network and LED events; Core should not import GPIO drivers.
- **AI and speech modules** (VAD / wake / STT / LLM / TTS / memory) are replaceable bounded dependencies selected after the evidence-based library evaluation, not a reason to build a framework today.
- **Operations/admin surface** is a future *small* local UI or diagnostics CLI, connected to Core through an appropriately secured interface, **not** an entirely new platform by default.

Planned vertical order: first library review [Issue #5](https://github.com/igalgerman/JARVIS/issues/5), then **small Core↔Godot↔mock DevicePort** [Issue #6](https://github.com/igalgerman/JARVIS/issues/6) with **minimal structured logging and failure diagnostics in the first executable slice**, then face/voice [Issue #7](https://github.com/igalgerman/JARVIS/issues/7), then hardware prototyping [Issue #8](https://github.com/igalgerman/JARVIS/issues/8). Full web management/pairing is a later separately approved scope.

## 2A. Personal assistant, modular Skills and first real integration (product scope; not yet approved for implementation)

**User decision (2026-10-17):** JARVES is intended to be a **persistent, working personal assistant**, not a disposable demonstration, a voice-only chatbot, or a bespoke reimplementation of an entire AI agent harness. Its separate personal memory is acceptable. Reuse maintained agent/tool infrastructure and built-in capabilities where practical; create only the small robot-specific orchestration, UX and adapter pieces actually needed.

- **Initial assistant capability:** natural Hebrew voice conversation, actionable web information/search where supported, persistent user preferences/memory with privacy controls, realistic diagnostics, and a simple extensibility mechanism for future **Skills**. Consider reminders when designing persistence, but don't silently commit to always-on cloud infrastructure or background delivery from an unpowered device.
- **Google Gmail/Calendar/Drive account connections are NOT required for the initial release.** They are opt-in future Skills with proper authorization and approval for external side effects.
- **The first concrete external-action Skill must be YouTube Music playback** (user calls it "Google Music"). This is a proof that the *production assistant system* can select and run a real Skill, **not a throwaway mock demo**. Target end-to-end voice behavior: request a specific song/artist/playlist in Hebrew → resolve playable media → start real audio playback on the assistant's selected output → report actual status; support pause/resume, next, stop and volume. Explicit error/retry/recovery for a missing title, expired login, player unavailable or output disconnected. It must not fake success or merely return a URL.
- **Provider integration feasibility is an early research gate, not an assumption:** YouTube Music's consumer web player is official but Google does not currently document a general-purpose public YouTube Music playback-control API. YouTube Data API searches YouTube metadata; YouTube IFrame API controls *visible embedded YouTube video* subject to minimum player size, autoplay rules and restrictions on hiding the player or extracting/background-playing its audio. The official YouTube Music web app supports playback/media controls but is not the same as a supported external service API. Browser automation and unofficial wrappers may be brittle or contrary to service terms; don't silently commit to those for a reliable product. Reference research: https://developers.google.com/youtube/iframe_api_reference ; https://developers.google.com/youtube/terms/required-minimum-functionality ; https://developers.google.com/youtube/terms/developer-policies-guide ; https://support.google.com/youtubemusic/answer/9231765 .
- **Before implementation**, analyze supported, sustainable options for music playback on Windows during development and Raspberry Pi/Linux in the final robot, including whether video visibility conflicts with JARVES's expressive face, headless/background operation, account login, subscription entitlement, autoplay, Raspberry Pi media playback/DRM feasibility, speaker and mic/AEC interplay and exact control semantics. Recommend one real option with evidence and fallback, and surface a genuine blocker to the user rather than quietly switching from YouTube Music to generic YouTube, another music service, or an illegal/unstable workaround. Do not presume YouTube Premium grants third-party API access.
- **Architecture:** one minimal common Skill/tool interface with explicit capabilities, input validation, scoped permissions, consistent success/error reporting and provider-specific adapters; reuse existing agent SDK/tool registration or MCP where they materially help rather than inventing a complex plugin platform. Add a second Skill later without changing the entire Core. A Skill containing only instructions cannot control a player: it must invoke a real authorized tool/integration.
- **Completion evidence:** a real music command travels from voice intent to an authenticated/allowed playback mechanism, produces observable sound, confirms play/pause/next/stop/volume, survives ordinary recoverable failures, logs actionable status without exposing tokens, and runs through the same Core/DevicePort and future robot speaker path. Mocked audio remains valuable for unit/fault tests but **does not satisfy end-to-end acceptance**.

**Implementation timing:** this is a product requirement and a brief for the future library review (#5) and software milestones (#6/#7); it does not expand the currently approved mechanical/CAD task (#4). External login and any paid service/subscription decisions require explicit approval when reached.

## 3. Mandatory: observability and debug logging from the first runtime milestone

Avoid the recurrent failure mode of implementing features first and forgetting how to troubleshoot them. When the first Core integration is approved, add a **minimal, inspectable** diagnostic layer *at the same time*, without creating a large observability stack.

**Required behavior:**
1. **Structured logs** written to a documented, persistent local location, with timestamp (timezone specified; ISO-8601 UTC recommended), severity (DEBUG/INFO/WARN/ERROR), component (Core, Godot boundary, DevicePort, network, audio, AI, admin), stable event/error code and session/correlation ID for tracing the *same operation* across modules where practical.
2. **Actionable state transitions**: startup, ready, shutdown, configuration loaded/invalid, connection attempts, connected/disconnected/reconnected, retries/timeouts, device missing or permission denied, mic-muted/unmuted, audio chain unavailable, model/provider request failed, and graceful recovery. Always include *which layer failed* and *which action to try*; no silent hanging or misleading “connected”.
3. **Bounded storage**: rotation and retention/size cap (exact values set at implementation); INFO by default, DEBUG for a bounded temporary troubleshooting session, with automatic return to ordinary logging. Don't flood disk/SD or log raw audio/video.
4. **Safe privacy defaults:** **never log secrets**, Wi-Fi passwords, API keys, access tokens, authorization headers, private transcripts or raw camera/microphone data by default; redact IPs/device identifiers and sensitive payloads in shared diagnostic bundles as appropriate. Diagnostic export is **user initiated**, previewable, and redacted; no auto-upload or background cloud telemetry without opt-in.
5. **Diagnostics usable when network is down**: logs remain on device; provide a later documented local-console/USB/offline retrieval path that does not depend on working remote management. Distinguish software service outage from full power/storage failure; software cannot diagnose a completely dead device remotely.
6. **Lightweight health checks** that expose current truth: power/boot stage (as observable), Core running, Godot face link, network IP/reachability, mic privacy state, speaker availability, storage free space, thermal warnings on hardware, and last error. Do **not** fabricate an all-green status from stale last-known data. Preserve timestamps and mark disconnected/unknown state.
7. **A documented support bundle** for a user to share voluntarily: application version / commit, environment, recent bounded redacted logs, component health snapshot and an explicit error identifier, *without* secrets. A future web UI can offer “Download diagnostics”; initially a simple CLI/manual procedure is sufficient.

**No need for:** Elasticsearch, Grafana, distributed tracing backend, external brokers, always-on remote telemetry, or an AI agent constantly reading logs. Keep dependencies low and the routine developer workflow affordable.

## 4. Failure scenarios: what should happen and what to test

| User-visible problem | Runtime behavior and diagnostics required | Recovery / access path |
|---|---|---|
| JARVES is powered, but does not respond | Distinguish mic hardware mute, input unavailable, wake/VAD error, STT/AI/TTS offline and volume=0. Record component-specific error code, not a generic “something failed”. | Physical mute/volume check; local status, settings and diagnostic view; restart only affected service where safely supported |
| No response / display frozen | Watch for process crash, Core↔Godot connection timeout and incomplete startup. Record restart reason and avoid endless silent retries. | Minimal status on available surface, documented local recovery/restart; manual cold reboot as a last resort |
| PC/phone cannot connect on the **same home Wi-Fi** | Differentiate device off, JARVES service not started, Wi-Fi/IP changed, wrong network/guest isolation, DNS/mDNS failure, firewall/bind problem, authentication failure and expired session. Surface discoverable device address/hostname when possible. | Local physical/status access; known device address or LAN discovery; check network/port/reachability/auth separately, not “reinstall everything” |
| Cannot access from **outside** home | Report that LAN-only is expected unless secure external access was explicitly configured. Distinguish internet outage from VPN/relay/auth failure. | Never use default open router ports / unauthenticated remote commands; later opt-in secure remote solution only |
| Wi-Fi credentials changed / router replaced | Time-bounded reconnect attempts with status/error and no destructive reset by default. | A **local, documented Wi-Fi reprovisioning/fallback path** that does not require existing Wi-Fi (mechanism chosen later; may involve temporary setup mode or USB/console) |
| AI provider offline or subscription/rate limit reached | Bounded retries/backoff, visible degraded state and diagnostic code; avoid runaway token costs. | Core local settings/status and optional limited offline functions; do not silently substitute paid providers |
| Speaker, microphone, camera or LED absent | Per-device availability/error and ability to continue with remaining functions. Removable LED must never prevent boot or speech. Hardware mute status overrides UI. | DevicePort reconnect/disable path; physical wiring check. Do not overpromise online hot-swap |
| Power loss / SD-card or SSD trouble | Persist bounded relevant prior logs when storage is healthy; detect unclean shutdown on next boot if possible. | Safe-shutdown guidance, storage check and documented recovery/reimage process, not assumed automatic self-repair |
| Overheating / undervoltage | When hardware exists, record actual measured Pi thermal/power warnings and throttling; label simulation-only values truthfully. | Reduce load/shut down safely if necessary; verify PSU/ventilation physically |
| App update or settings change breaks startup | Record version, attempted migration and validation error, last known-good config where feasible. | Bounded rollback or documented offline restore, without deleting personal data silently |

**Acceptance tests for the first minimal Core↔Godot stage** (when separately approved): normal startup/status; Core stopped/unreachable; bogus configuration; simulated DevicePort disconnect/reconnect; actionable logs with a common correlation ID; logs retained and size-bounded; secrets excluded; documented reproduction and manual log retrieval. Add hardware/network/AI-specific fault injection **only as each capability is actually implemented**.

## 5. Beginner-friendly support / diagnostics experience (future)

A lightweight management page should have:
- **Home:** READY / MUTED / OFFLINE / DEGRADED / ERROR, last successful connection time, and a short human-readable explanation.
- **Settings:** speaker volume, voice/language preferences, software mic state (distinguish from physical mute), network status and updates.
- **Help/diagnostics:** “What is wrong?”, “Run basic checks”, “Show recent events”, “Export sanitized diagnostic report”, supported recovery guidance, current software version.
- **Security:** authenticated paired admin only, least privilege, no default exposed public server, never reveal credentials in exports; do not implement remote reboot/reset without access safeguards.
- **Resilience:** a complete network outage must have an **independent local recovery route**, not a phone web page that can only work after Wi-Fi returns.

No mock admin screen should be presented as already implemented or connected. A future UI can be demonstrated in Windows using simulated faults first.

## 6. Handoff / development quality gate

For **each new runtime feature from the first approved software milestone onward**, closeout should document:
- how to start it and how to check **actual** readiness;
- 1 relevant success test and 1 relevant failure/reconnect test where feasible;
- exact local log location, a representative redacted event/error code, and diagnostic instructions;
- fallback behavior without the affected service, known limitations and privacy/security implications.

Do not change the current design/assembly milestone, silently start implementation, purchase equipment, or create a new microservice for this document. The user approves implementation scope through `docs/NOW.md`.

## Questions intentionally deferred
1. Does the eventual selected screen keep touch enabled for a few emergency controls, or remain face-only?
2. Exact physical controls, safe shutdown/wake circuitry and *provable* microphone privacy switch.
3. Local admin pairing/authentication, network discovery and Wi-Fi reprovision method.
4. Whether secure remote administration from outside the home is needed; if yes, assess threat model and private VPN/relay vs managed service.
5. Retention period, log directory, diagnostic bundle structure, and software installation layout once an actual OS/runtime is selected.
6. Voice stack, offline capabilities and privacy/consent for AI/cloud features, subject to library and product reviews.
