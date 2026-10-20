# JARVES — Multi-device satellites and remote access over a private VPN (design)

**Status:** design only, 19-20 Oct 2026, Issue #17 follow-up. No code, no install on any phone until the owner approves.
**Owner decisions:** ONE brain (the PC Core), MANY satellites (old Android phones starting with the OnePlus 11, later the Pi robot) used **in parallel**; remote use from the owner's main phone over **Tailscale** (free Personal plan), nothing exposed to the internet; remote scope = talk, read reports/dashboard, approve or reject actions; risky settings stay local on the PC.
**Supersedes for this scope:** FUTURE_IDEAS F01 (the "independent installations" model; here only the PC runs a Core), F04 (remote dashboard) and F08 (presence/routing).

**Sources (checked Oct 2026):** tailscale.com/kb/1312/serve, developers.openai.com/api/docs/models/gpt-live-1 and /guides/voice-webrtc, developer.android.com/develop/background-work/services/fgs/changes, source.android.com/docs/core/audio/implement-pre-processing, github.com/Re-MENTIA/openwakeword-android-kt, home-assistant.io/voice_control/about_wake_word.

## 1. What the code does today (constraints found)

- `link.py` is a **localhost-only** WebSocket with one per-install token and exactly **one face** (`self.face`; a second face replaces the first). It must stay that way: satellites get a separate listener, not a widened face link.
- `app.py` owns **one** conversation: `self.live`, guarded by "one conversation at a time" (the QA 19 Oct double-billing fix). Audio is attached by passing `audio_io=StreamingAudio(...)` into `LiveVoice`; the wake loop opens its own 16 kHz mic stream.
- `LiveVoice` only needs from `audio_io`: `mic_chunks(rate, chunk_bytes)`, `enqueue`, `flush`, `queued_s`, `run_player`, `stats`, `close`. **A network-backed class with this interface lets GPT-Live serve a phone with no change to `live.py`.** That is the main seam.
- `dashboard_auth.py` LAN mode admits only RFC 1918/ULA sources; Tailscale's CGNAT `100.64.0.0/10` is neither private nor global in Python, so it is refused (good). But behind `tailscale serve` every request arrives from **127.0.0.1**, and `principal()` treats "loopback + admin cookie" as the local owner. Serve must therefore proxy to a **dedicated backend port** whose requests are always principal `remote` (identity headers alone are not enough: tagged nodes get none). Most important security change.
- `budget.py` has a central daily `voice_seconds` ceiling; per-device sub-budgets fit on top.
- Wake is openWakeWord "hey_jarvis" on TFLite; the same models run on Android (LiteRT, or `openwakeword-android-kt` on ONNX Runtime).

## 2. Architecture

```
 Owner's main phone (anywhere)            Satellites (home; same tailnet)
 browser PWA / satellite app              OnePlus 11 app      Pi robot (later)
   push-to-talk, dashboard,               mic+AEC, wake, face, speaker, camera
   approvals                                     |                   |
        \______________ Tailscale (WireGuard, NAT traversal, DERP relay) ______/
                                   |
                   PC: tailscaled  +  `tailscale serve` (HTTPS :443, ts.net cert)
                     /      -> http://127.0.0.1:8768  (remote-only dashboard port: principal "remote")
                     /sat   -> http://127.0.0.1:8767  (SatelliteHub WebSocket)
                                   |
 +------------------------------ Core (one process) -------------------------------+
 | SatelliteHub: pairing, auth, per-device state, frames <-> NetworkAudio           |
 | Router: wake arbitration, rooms, which device answers / announces                |
 | SessionManager: one LiveVoice per active device (cap N), per-device budget       |
 | shared: SkillRegistry, memory, scheduler/tasks, confirmations, budget, privacy   |
 | GodotLink (unchanged, localhost) -> PC face   |  DevicePort for PC mic/speaker   |
 +----------------------------------------------------------------------------------+
                                   |
                       OpenAI GPT-Live (one WebSocket per active session)
```

The PC itself becomes "device `pc`" in the same model (its mic/speaker/face), so routing rules treat all devices alike.

## 3. Satellite <-> Core protocol (SatLink v1)

One WebSocket per satellite at `wss://<pc>.<tailnet>.ts.net/sat` (TLS by serve). JSON text frames for control, binary frames for media. Same envelope as `link.py`: `{"type","payload","cid"}`.

**Pairing (once, started on the PC):** "Add a device" on the PC dashboard -> 8-digit code (5 min, single use, `dashboard_auth` limits) plus a QR of `{url, code}`. App: `pair.request {code, name, kind: phone|pi|browser, model, os, caps}`; Core: `pair.ok {device_id, device_secret}` (only SHA-256 stored in `satellites.json`, 0600). Revoking on the PC closes the socket with 4401.

**Hello (every connect):** `hello {device_id, secret, protocol: 1, app_version, caps: {mic, aec_hw, wake, speaker, screen, camera, battery}}` within 3 s, else 4400. Defense in depth: when serve sends `Tailscale-User-Login`, it must equal the owner's login.

**Device -> Core events**
| type | payload |
|---|---|
| `wake.detected` | `score, t_device_ms, rms, room` |
| `talk.down` / `talk.up` | push-to-talk (remote phone) |
| `audio.start` / `audio.end` | `stream_id, rate: 16000, codec: pcm16|opus` |
| `privacy.state` | see below |
| `presence.changed`, `battery.state` | `present`; `level, charging, temp_c, health` |
| `confirm.reply` | `request_id, approve: bool` |
| `camera.frame_available` | `frame_id` (DevicePort semantics) |

**Core -> device commands:** `session.state (idle|listening|thinking|speaking)`, `face.set_state`, `face.emotion`, `assistant.text`, `user.transcript`, `speaker.stop` (barge-in flush), `wake.suppress {until_ms, reason}`, `confirm.request {request_id, prompt, risk}`, `camera.preview {on, fps<=2}`, `mic.set_mute`, `appearance.set`.

**Binary media frame:** 12-byte header `[u8 kind (1 mic, 2 speaker, 3 jpeg)][u8 flags][u16 stream_id][u32 seq][u32 t_ms]` + payload. Mic up: 16 kHz PCM16 mono, 20 ms frames (640 B), sent **only** while a session is open or a wake confirmation window is running (never continuous). Speaker down: 24 kHz PCM16 (GPT-Live's rate), 20-40 ms frames. 256 kbit/s up, 384 down: trivial on Wi-Fi; on mobile data **Opus** (24-32 kbit/s, 20 ms) in P3. The Core resamples 16->24 kHz in `NetworkAudio.mic_chunks`; bounded drop-oldest queues as in `StreamingAudio` (CR-06); ~60 ms jitter buffer on the device.

**privacy.state (per device, both directions, shown on every screen):**
`{mic: live|soft_muted|hw_muted|no_permission, camera: off|on|preview, cloud_audio: idle|streaming, words_kept: bool, remote: bool}`. The device owns mic/camera truth (the Android privacy dot is the real proof), the Core owns `cloud_audio` and `words_kept` (log.privacy). Soft mute is labelled truthfully (DEVICE_CONTRACT); while muted the device sends no mic frames and the Core drops any that arrive.

**Camera preview:** frames stay on the device by default. Preview is explicit (`camera.preview on`), JPEG <=2 fps, only to the requesting dashboard viewer, never stored, indicator on, auto-off after 60 s; off-LAN preview disabled by default (Q5).

## 4. Multi-satellite session model

**Rooms.** Each device has a `room` (set at pairing, editable on the PC). Devices in one room hear each other.

**Rules**
1. **One session per room, parallel across rooms.** A second wake in a room with an open session joins nothing; it is suppressed. This prevents two devices answering one phrase and stops device A's speaker triggering device B.
2. **Concurrency cap:** `satellites.max_sessions` default **2** (owner can raise). GPT-Live limits are counted in concurrent sessions (Tier 1 = 25), so the cap is ours, for cost. Over the cap: the device chimes "busy" and shows which device is talking.
3. **Wake arbitration:** on the first `wake.detected` the Router opens a **250 ms window**, collects wakes from the same room, picks the highest `score x rms` (the closest device), opens the session there, sends `wake.suppress` (session length + 1.5 s tail) to the others. The PC's own wake loop joins the same arbitration as device `pc`.
4. **Echo between devices:** rule 1 plus `wake.suppress` while any device in the room is speaking; on-device hardware AEC (`VOICE_COMMUNICATION` + `AcousticEchoCanceler`) handles the device's own speaker. Full-duplex is allowed only on devices reporting `aec_hw: true`; others run half-duplex (the existing `half_duplex` gate).
5. **Barge-in / floor control:** per session, local to the device (existing CR-06 flush now sends `speaker.stop` down). There is no cross-session floor; sessions are independent conversations.
6. **Per-device mute:** mute is a device property, never global. Muting the PC does not mute the kitchen phone. A "mute all" button exists on the PC only.
7. **Per-device budget:** `satellites.<id>.daily_minutes` (default 30) inside the global `ai.daily_realtime_minutes`; whichever is hit first ends the session ("budget"). Usage page shows minutes per device. False-wake cutoff (`no_speech_timeout_s`) applies per session.
8. **Which device announces:** reminders/job results go to the device with the most recent presence or interaction in the last 10 min, else the PC, else a text notification to the remote phone; quiet hours apply per room. Exactly-once via the existing task-id announcement tag; the scheduler stays single-owner because only the PC runs a Core.
9. **Shared brain, separate conversations:** memory, notes, schedules and Skills are shared; transcripts are per session. Side-effect Skills (music, calendar writes) take a global per-resource lock plus an idempotency key, so two rooms cannot double-book.
10. **Confirmations bind to the asking device:** `confirm.request` goes only to the device whose session asked (generalising `face_client_id()`), with a `request_id` single use, 60 s expiry.

App change: `self.live` becomes `SessionManager.sessions[device_id]`; the "one conversation" guard becomes "one per room + cap". Delegation is still started server-side by GPT-Live, so the budget F1 caveat applies per session.

**Why Core relay, not phone-direct WebRTC:** GPT-Live's WebRTC flow (server posts the SDP to `POST /v1/live/sessions`, key stays on the server) would cut a hop, but tool/delegation events would land on the phone's data channel and need a path back to the Core's Skills. Relay first (proven `live.py` path, ~20-40 ms extra on LAN); re-evaluate in P4.

## 5. Remote-over-VPN security model

- **Network:** Tailscale on the PC (Windows service), satellites and the main phone. **No port forwarding, no UPnP, no Funnel** (health check warns if `tailscale funnel status` shows anything). Core listeners bind **127.0.0.1 only**; `tailscale serve` is the only way in and provides a real TLS cert for `<pc>.<tailnet>.ts.net` (MagicDNS + HTTPS on in the admin console). This also gives phone browsers the secure context `getUserMedia` needs.
- **Tailscale policy (grants):** owner's devices and `tag:satellite` -> PC `tcp:443` only; satellites cannot reach each other or anything else; no exit node or subnet router. Tagged vs user-owned satellites: Q4. Key expiry off for the PC and fixed satellites, on for the main phone.
- **Principals:** `local` (PC loopback on the local port: everything), `satellite` (SatLink only), `remote` (paired phone/browser via the serve-only port: allow-list below). Every request audited as today.
- **Allowed remotely:** talk (push-to-talk or app), read Home/Reports/Jobs/Notes/Calendar/Usage/Health, approve or reject pending confirmations, mute a device, stop a session, un-pair itself.
- **PC-only:** feature switches, API keys/accounts, pairing new devices, privacy OFF, mask/delete history, updates/installers, backup restore, Skill creation/install, file-sandbox writes, autonomy level, camera enable, budget increases, LAN mode, Tailscale settings.
- **Approvals remotely:** low/medium-risk actions (send a calendar invite, play music) can be approved from the remote phone with a fresh tap (request-bound, single use, 60 s). High-risk actions (payments, deleting data, outgoing email to new recipients) say "approve on the PC" — Q2.
- **Stolen phone:** revoke on the PC (device list) and remove the node in the Tailscale console; secrets are per device, nothing reusable is stored on the phone except its own secret (Android Keystore).

## 6. Satellite app approach

- **OnePlus 11: native satellite.** Godot Android export of the face (branch `demo/android-apk-20261019`) plus a small Kotlin plugin: `AudioRecord(VOICE_COMMUNICATION)` + `AcousticEchoCanceler`, on-device wake (openWakeWord hey_jarvis; Porcupine only if accuracy fails, it needs a licence key), a **foreground service of type `microphone|camera`** (Android 14+ requires types; a while-in-use mic service cannot start from the background, so it starts with the app and stays alive), screen pinning, battery reporting, secret in Android Keystore.
- **Main phone remote:** PWA served by the dashboard over serve HTTPS: push-to-talk (`getUserMedia` + AudioWorklet -> SatLink frames), reports, approvals. No background mic, no wake word: by design.
- **Not chosen:** Home Assistant Companion/Wyoming satellites (STT->LLM->TTS pipeline, not full-duplex GPT-Live; community Android apps are flaky in 2026). Later option: a Wyoming endpoint so HA/ESP32 satellites attach half-duplex.
- **Pi robot:** runs the Python reference satellite (`tools/virtual_satellite.py` with real audio): CI fake and Pi client share code.

## 7. Phased plan

| Phase | Content | Effort |
|---|---|---|
| P0 | Owner setup: Tailscale account, PC + main phone join, MagicDNS+HTTPS on; serve maps `/dash`; `remote` principal + serve-proxy detection + allow-list; PWA read + approvals | M |
| P1 | SatelliteHub + SatLink v1 + pairing; `NetworkAudio` (StreamingAudio interface); Python virtual satellite; one remote session (push-to-talk from the main phone) | M |
| P2 | SessionManager (per-device sessions, rooms, cap, arbitration, suppress, per-device mute/budget, announce routing, confirm binding); dashboard Devices page with privacy.state | L |
| P3 | OnePlus 11 app: Godot face + Kotlin plugin (AEC, wake, FGS, kiosk, battery); Opus on mobile data | L |
| P4 | Camera preview; latency tuning; evaluate direct WebRTC; Pi satellite on real hardware | M |

## 8. Test plan (no physical phone for most of it)

- **Virtual satellites in CI:** N Python clients speaking SatLink with WAV fixtures (Hebrew wake + utterance), fake `live_factory` sessions (already used in tests), fake clock. Scenarios: two devices same room -> exactly one session; two rooms -> two sessions; cap reached -> busy; per-device budget ends only that session; mute on A doesn't affect B; reminder announced exactly once; confirmation reaches only the asking device; reconnect mid-session resumes or ends cleanly; revoked device refused.
- **Security tests:** loopback request with `Tailscale-User-Login` header never becomes `local`; each PC-only route returns 403 to `remote`; wrong owner login refused; pairing brute force limits; no listener on 0.0.0.0 (socket scan); muted device frames dropped.
- **Audio tests:** jitter/loss injection (drop 5 %, 200 ms delay) — no unbounded queues, flush within 100 ms; resampler correctness; Opus round-trip (P3).
- **Latency harness:** `tools/silent_live.py`-style measurement wake->first audio and end-of-speech->first audio over SatLink; target <= +80 ms vs PC-local on LAN, <= +250 ms on LTE.
- **Android:** Kotlin unit tests + emulator smoke (pairing, FGS start, reconnect) in CI; AEC, wake accuracy, battery/thermal only on the real OnePlus 11 (manual checklist, results into the capability matrix).

## 9. Risks

- **Battery swelling (always plugged in):** OxygenOS Battery health 80 % charge limit; else a smart plug cycling 40-80 % from `battery.state`; no case, ventilated spot, monthly check; Core alerts on `temp_c > 40` or 100 % held for hours.
- **Android background kills (OnePlus is aggressive):** foreground service, battery "Unrestricted", app locked in recents, screen pinning, start on boot; offline after 15 s without pings, reconnect with backoff.
- **Latency:** the relay adds a hop; DERP relaying (no direct WireGuard path) adds 50-150 ms remotely. Levers: Opus, direct WebRTC.
- **Cost:** parallel sessions multiply $0.05/min + backend tokens: section 4 caps plus a provider-side spend limit.
- **Privacy:** more microphones in the house: strip on every device, mic frames only after wake.
- **Tailscale outage:** blocks new connections; LAN fallback only by owner opt-in (Q3).

## 10. Windows / Pi status and what needs the owner

- **Windows:** Tailscale runs as a service; serve config should persist across reboots (verify on the owner's PC). All Core changes are cross-platform Python.
- **Pi:** Tailscale ARM64 is first-class; the Pi becomes a satellite (P4), `implemented_unverified` until hardware evidence.
- **Owner actions:** Tailscale account (free Personal plan: 6 users, unlimited user devices, 50 tagged resources since the April 2026 change; re-check the pricing page); install on PC and main phone; enable MagicDNS + HTTPS; approve our policy file; OnePlus 11: update OxygenOS, charge limit on, app battery Unrestricted, mic/camera/notification permissions, screen pinning; room names.

## 11. Open questions (ranked)

1. **Default parallel sessions cap and per-device daily minutes** (proposed 2 sessions, 30 min/device inside the global limit)?
2. **Remote approvals:** which actions may be approved from the main phone, and which must be on the PC (proposed: high-risk = PC only)?
3. **LAN fallback:** if Tailscale is down, should home satellites connect directly over the LAN (plain WS with device secret, or local TLS), or simply stop?
4. **Satellite identity in Tailscale:** run satellites as your user (simpler, identity headers) or as `tag:satellite` (cleaner isolation, no identity headers)?
5. **Remote camera preview:** ever allowed from outside the home, or LAN-only forever?
6. **Which device announces reminders** when nobody is near any device: PC speaker, the main phone as a notification, or silence until presence?
7. **Main phone:** browser PWA push-to-talk only, or also the native app (enables wake word on your main phone, more battery use)?
8. **OnePlus 11 placement and power:** which room, always on charger with an 80 % limit, and is a smart plug acceptable?
