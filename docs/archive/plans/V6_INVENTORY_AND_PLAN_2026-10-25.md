# V6 inventory and plan (25 Oct 2026)

Planning document. Read-only research on `jarves/main` at `8b99758` (Jarvis-igerman-ai/jarves). **It does not authorize code.** [NOW](../../NOW.md) is still the V4.5 brief and lists V6 as out of scope; [ROADMAP](../../ROADMAP.md) approves the six V6 pillars as a roadmap only, gated on the V5 real-phone gates. Under the owner's non-stop rule, free agents may build V6 agent-doable work on **local branches** before then; PRs wait until NOW authorizes V6 and a PR slot is free. Sources: ROADMAP (V6 section), [PRODUCT_VISION](../../PRODUCT_VISION.md) stage 3, [V7 proposal](V7_PROPOSAL_2026-10-21.md) (#50), the V5 plan (branch `docs/v5-status-181`, `V5_INVENTORY_AND_PLAN_2026-10-24.md`), V5 milestone issues #181-#206, the code.

Standing owner decisions this plan respects: hands-free phone voice moved from V5 to V6; live Claude Code/Codex demos not approved; real-device tests are owner-gated with safe defaults; no hardware orders or physical tests by agents; no paid usage beyond approved caps; a stable release needs the owner's signed sheet.

## 1. What V6 is, and what already exists on main

V6 = **JARVES Drive & Act**, product stage 3: use the **same** JARVES (one Core on the PC, one memory) hands-free while driving, through whatever phone/Bluetooth/Android Auto path is legitimately supported. Never vehicle control. No second "car brain". All paths below are under `core/src/jarves/`.

| Pillar | What it means | What V6 builds on (main today) |
|---|---|---|
| P1 Driving Companion | Short spoken answers, barge-in, "stop"/"עצור" always cancels, no long text or screen work while driving | `voice/live.py` (GPT-Live full duplex, barge-in, `interrupt()`), `voice/echo_gate.py`, `chat_engine.py` / `chat_service.py` (one shared chat), `continuity.py` (command ledger: a resend runs once) |
| P2 Journey Intelligence | Trip plan from calendar + route estimate + weather, with freshness and provider labels; never invented traffic | `briefing.py` (calendar, Open-Meteo weather, reminders; sections fail independently), `skills/google.py` (Calendar), `scheduler.py` |
| P3 Drive Actions | Calls, messages, reminders, music/navigation handoff, calendar/tasks, with driving-safe confirmations | `dialing.py` (tel: handoff, owner presses Call, untrusted-number rule), `telegram_channel.py`, `whatsapp_bridge.py` (sends with approval), `timers.py`, `lists.py`, `skills/music.py` (Pear, PC only), `approvals.py` (one approvals service), `skills/__init__.py` (permission policy, untrusted-turn rule) |
| P4 Voice Engine 2.0 | Mic/Bluetooth/car-audio routing, echo/noise, device changes, network transitions, measured latency; **hands-free phone voice** (moved from V5) | `voice/audio.py` (PortAudio + simulated device), `voice/echo_gate.py`, `voice/wake_*.py`, `speedtest.py` / `selfspeed.py`; phone voice today is push-to-talk only (`mobile.py` `/api/m/voice`, STT then reply) |
| P5 Contextual Briefings | Opt-in pre-drive and journey-aware updates; no spam; no sensitive text read aloud to passengers by default | `briefing.py`, `proactive.py` and `continuity.py` notices (opt-in, quiet hours 22:00-07:00, dedupe, stop/snooze, limits), `calm.quiet_hours_now` |
| P6 Skills Platform 2.0 | Vetted, permission-scoped Skills with manifest, versions, dependencies, surfaces, setup, diagnostics, update and rollback | `skills/generated.py` (SKILL.md frontmatter, static validation, sha256 approve, versions, grants, rollback, sandboxed child), `skills/__init__.py` (one SkillRegistry), `plugin_registry.py` + `plugins.d/`, `guides/data/*.toml`, `capabilities.py` (targets `windows`, `linux_arm64_pi` only) |

Also reused: `mobile.py` + `dashboard_static/mobile/` (PWA, pairing, tokens, scopes), `tailnet.py`, `deviceport.py`, `release_signoff.py`, `release_gate.py`. **No V6 code exists.**

## 2. Definition of done per pillar (from ROADMAP gates, made testable)

- **P1:** start and end a hands-free conversation on the validated car path in a parked compatible car, continue it on desktop/phone with no lost context and no duplicate command; "stop" cancels within 1 s in tests; answers in drive mode stay under a set length.
- **P2:** one real calendar trip planned from permitted inputs; output shows estimate vs observed time, provider and data age; missing location/route/weather gives a useful fallback, never a made-up value.
- **P3:** each action (call, message, reminder, calendar, music/nav handoff) works with a driving-safe confirmation or is deferred until stopped; denied and unsupported actions say so and never claim success.
- **P4:** tests for Bluetooth disconnect/reconnect, headset change, phone lock/background, incoming call, lost data, echo/noise; phone hands-free voice works with a visible cloud-audio indicator; latency recorded per path. Real-device evidence for every "verified" claim.
- **P5:** opt-in pre-drive briefing with quiet-hours, passenger-privacy and dedupe tests; off by default.
- **P6:** two maintained Skills on manifest v2 with separate permissions, an update and a rollback, and no regression to V4/V5 Skills (existing tests stay green).
- **All:** Opus security/privacy review, CI + real-device regression matrix, an honest Android Auto matrix (official in-car / phone-mediated / blocked), a guide (EN+HE), CLI parity entry and capability row per feature, no open P0-P2 in the 6.0 bug table.

## 3. Gaps

| # | Gap | Pillar | Tag | Safe default |
|---|---|---|---|---|
| G1 | No current official feasibility record: Android Auto app categories, Car App Library mic access, notification/messaging surfaces, PWA mic when the screen locks, Bluetooth HFP vs media routing, Play/sideload rules (with doc dates) | all | AGENT-DOABLE (web research) | assume phone-mediated path only |
| G2 | No drive-mode policy: what is allowed while driving, confirmation style, a deferral queue for unsafe steps | P1 P3 | AGENT-DOABLE (Opus review) | drive mode off; everything defers to "when stopped" |
| G3 | No journey module: route estimate adapter, Maps/Waze deep links, freshness labels, provider fallback | P2 | AGENT-DOABLE (fake servers) | free providers only, labelled "no live traffic" |
| G4 | No pre-drive briefing builder or passenger-privacy filter | P5 | AGENT-DOABLE | off by default; senders/subjects never spoken in drive mode |
| G5 | Skills manifest v2 (min Core version, dependencies, surfaces, setup/diagnostic hooks), discovery API, update/rollback for bundled Skills, local trust/signing | P6 | AGENT-DOABLE (Opus review) | only owner-approved local Skills; no third-party import |
| G6 | Audio route/device-change handling and fault tests (simulated Bluetooth drop, device switch) | P4 | AGENT-DOABLE | fall back to push-to-talk, say so |
| G7 | Hands-free phone voice: streaming from the PWA through the Core (or ephemeral Realtime session), VAD, barge-in, indicator | P4 P1 | AGENT-DOABLE code + OWNER-GATED use (paid minutes) | off; push-to-talk stays default; under the existing voice budget |
| G8 | Phone "drive screen": one big button, status, stop; mobile actions as phone deep links (tel:, maps, YouTube Music) | P1 P3 | AGENT-DOABLE after V5 phone PRs merge | PWA unchanged |
| G9 | `capabilities.py` has no phone/car target, so the honest matrix cannot be shown | all | AGENT-DOABLE after V5 #183-#185 merge | rows say "planned" |
| G10 | Phone location to the Core | P2 P5 | OWNER-GATED (privacy) | off; owner types or says start/destination |
| G11 | Real car and real phone runs: parked car, Bluetooth, incoming call, noise | P1 P4 | OWNER-GATED (physical) | 6.0 ships as beta with those rows NOT-RUN; rows stay implemented_unverified |
| G12 | Live traffic / paid routing (for example Google Routes) | P2 | OWNER-GATED (spend) | free route estimate without traffic, clearly labelled |
| G13 | Native Android Auto app (Car App Library), steering-wheel assistant button, locked-screen/background mic, Play distribution | P1 P4 | BIG-BLOCKER -> V7 (native Android is stage 4) | phone PWA + Bluetooth audio, screen on, phone mounted |
| G14 | Live push/steer into running coding-agent sessions (carried from V5 G19) | - | BIG-BLOCKER -> V7 | status-only |
| G15 | No V6 milestone or issues | - | AGENT-DOABLE | one issue per gap when issue creation is allowed |

## 4. Work breakdown (small PRs, agent-hours, order)

**Start now, local branches, files disjoint from open V5 work.** V5 branches touch `mobile.py`, `dashboard_static/mobile/*`, `capabilities.py`, `skills/generated.py`, `features.py`, `calm.py`, `continuity.py`, `devstatus.py`, `dialing.py`; the items below avoid them (new modules, new tests, new guide files, wiring later).

| Order | Branch / PR | Gaps | Est. | Files | Merge |
|---|---|---|---|---|---|
| 1 | docs: Android Auto / phone / Bluetooth feasibility record + V6 issues | G1 G15 | 3 h | `docs/research/V6_PLATFORM_FEASIBILITY.md` | docs, auto |
| 2 | drive-mode policy core (pure, no wiring) + tests | G2 | 4 h | new `drive_mode.py`, `tests/test_v6_drive_mode.py` | `no-automerge`, Opus |
| 3 | journey providers: route adapter, deep links, freshness, fakes | G3 | 5 h | new `journey.py`, `tests/test_v6_journey.py` | low |
| 4 | pre-drive briefing builder + passenger filter (reuses `briefing.py` sections read-only) | G4 | 3 h | new `drive_briefing.py`, tests | low |
| 5 | Skills manifest v2 parser/validator + discovery (no registry change yet) | G5 | 5 h | new `skills/manifest2.py`, tests | `no-automerge`, Opus |
| 6 | audio route/device-change handling + fault tests (coordinate with 4.5 row #122) | G6 | 4 h | `voice/audio.py`, new tests | medium |
| 7 | car-audio/Bluetooth simulated fixtures for the test harness | G6 G11 | 2 h | `tests/fixtures/car_audio/` | tests, auto |

**Must wait for V5 (phone PRs #183-#185 merged, V5 security fixes #200-#205 merged, NOW authorizes V6).**

| Order | PR | Gaps | Est. | Merge |
|---|---|---|---|---|
| 8 | capabilities: `phone` and `car` targets + V6 rows (honest status) | G9 | 2 h | low |
| 9 | wire drive mode into `approvals.py` / SkillRegistry (defer queue, voice-only confirm for known contacts only) | G2 | 5 h | `no-automerge`, Opus |
| 10 | Drive Actions: tel:/maps/music deep links from the phone, messages and reminders via existing Skills | G8 | 6 h | `no-automerge` |
| 11 | phone drive screen (PWA) | G8 | 5 h | medium |
| 12 | hands-free phone voice (streaming, VAD, barge-in, indicator; fake transport in CI) | G7 | 8 h | `no-automerge`, Opus |
| 13 | Skills Platform 2.0 wiring: migrate two Skills (proposal: timers, lists), update + rollback, guide | G5 | 6 h | `no-automerge`, Opus |
| 14 | guides (EN+HE), CLI parity, Hebrew catalog for every V6 feature | all | 4 h | docs, auto |
| 15 | V6 scenario e2e: emulated phone + simulated car audio + desktop resume | all | 5 h | tests, auto |
| 16 | Opus security/privacy review of V6 + fix PRs; 6.0 bug table; fix every P0-P2 | all | 6 h + fixes | per bug |
| 17 | release prep 6.0.0: version, CHANGELOG, checklist, sheet rows (car + phone), gate dry run | - | 3 h | `no-automerge` |

Total about 76 agent-hours. Items 1-7 can run in parallel now (5-6 agents). 8 first after V5; 9-13 in parallel after 8; 15 after 9-13; 17 last. Guides and capability rows go with each feature PR (rule in CLAUDE.md); item 14 only fills what is left.

## 5. Risks and security review plan

- **Platform risk:** Android Auto may not allow an AI voice app; the phone path may lose the mic when the screen locks. Mitigation: item 1 first; design for phone + Bluetooth; anything native goes to V7 (G13). No promise of steering-wheel button or car-screen chat.
- **Driver distraction:** no screen approvals while driving; only short spoken confirmations; unsafe steps queue until stopped. Tested by policy tests, not prompt text.
- **Wrong action by voice in noise:** new recipients, unknown numbers and sends to new chats are never confirmed by voice alone (keeps the `dialing.py` rule); untrusted-turn rule stays.
- **Privacy:** location off by default; outbound route requests send only coordinates/place names the owner gave, never calendar text; passengers: no message content, senders or mail subjects read aloud by default.
- **Cost:** hands-free voice and any paid provider stay inside the approved caps; free-only default; cost shown per session.
- **Skills trust:** no arbitrary third-party code; manifest v2 keeps sha256 approval, sandbox and grants; signing is a local owner key at most.
- **Opus security review (mandatory, `no-automerge`):** items 2, 5, 9, 10, 12, 13 and anything touching `approvals.py`, `mobile.py`, phone tokens/scopes, voice streaming, deep links that leave the PC, messaging/calls, accounts or location. Browser work is V7, not V6. One review at the end of V6 over all pillars plus a per-PR review for the risky ones.

## 6. Release gates and test plan ("I test and release")

- **Automated gates (agent):** `jarves-checks` green on main (all shards, phone e2e browser job, `test_guides`, `test_cli_parity`), V6 scenario e2e with simulated car audio, `jarvis selftest` / `speedtest` clean, log_lint 0, no open P0-P2 in the 6.0 bug table, `release_gate.py --tag v6.0.0` dry run passes.
- **Installed-beta smoke on the owner PC (agent):** backup first; install the 6.0 beta over 5.x; Core, dashboard and face start; turn on drive mode with the emulated phone; run one journey plan with fake providers, one deferred action, one pre-drive briefing; `jarvis update --rollback` and back. Report; the owner can revert.
- **Beta:** published by `package.yml`; beta channel kept current.
- **Stable 6.0.0:** needs the owner's signed sheet (`jarvis release signoff --version 6.0.0`), including the real phone and parked-car rows (G11). Until then the stable tag is prepared, not pushed, and V7 planning goes on.
- **Evidence table:** each DoD line marked EMU-PASS / REAL-PASS / NOT-RUN; the Android Auto matrix says official / phone-mediated / blocked. Nothing is "verified" without device evidence.

## 7. Questions for the owner (each has a default)

1. **Car path:** is "phone app on the mount + car Bluetooth" the V6 car path, with a native Android Auto app moved to V7? Default: yes.
2. **Hands-free phone voice cost:** may it use cloud realtime voice within the existing daily voice budget? Default: built and tested with fakes, switched off; push-to-talk stays the default until you turn it on.
3. **Traffic data:** free route estimates without live traffic, or a paid traffic provider? Default: free only, clearly labelled "no live traffic".
4. **Location:** may the phone share its location with your PC while drive mode is on? Default: no; you say or type where you are going.
5. **Car test:** when can you do one parked-car session (about 20 min, phone + Bluetooth)? Default: 6.0 goes out as beta with car rows NOT-RUN; stable waits for your sheet.
