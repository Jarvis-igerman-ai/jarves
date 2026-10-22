> **Status (22 Oct 2026):** phone access (this package) is merged (PR #25). The other V5 pillars and the owner decisions of 21 Oct are tracked in [ROADMAP](ROADMAP.md) (status block) and [PRIORITIES](plans/PRIORITIES.md). Real Android phone acceptance is open: [SWV5_ACCEPTANCE](reviews/SWV5_ACCEPTANCE.md). Index: [DOC_MAP](DOC_MAP.md).

# JARVES — SW-V5 Work Package: PC-hosted mobile access

> Branch `v5/mobile-access`, base `v4/integrated-b-c-d-g-m-r01`. Planning authority: [ROADMAP](ROADMAP.md) (V5, vision stage 2), [V4 package](JARVES_SW_V4_WORK_PACKAGE.md) (rules, privacy package B, test pyramid), [PRIORITIES](plans/PRIORITIES.md). Only `docs/NOW.md` authorizes work on `main`; this is a **draft** for owner review, not merged.

## 1. Scope

**In:** a phone talks to the owner's PC Core. The phone side is a PWA that the Core's own dashboard server serves. There is **no cloud relay owned by us**: the phone reaches the PC directly over the home network. Everything is **default OFF** and nothing listens beyond `127.0.0.1` until the owner turns it on.

**Out (separate owner approval, not in V5-core):** Raspberry Pi / hardware, Tailscale or any multi-device network, an internet-reachable endpoint, a native Android app, Android Auto (V6), docked-phone robot, Family Intelligence. The other V5 roadmap pillars (Goals, Adaptive Memory, Proactive Intelligence, Development Manager) layer on this backbone later; they are listed in section 3 as later packages and not started here.

## 2. Security model

| Topic | Decision |
|---|---|
| Default | Feature switch `mobile_access` is OFF. Off: no phone path is served (404), the server binds `127.0.0.1`, `jarvis dashboard --mobile` refuses to start. |
| Who may connect | On: the server also listens on the home network, but a non-loopback client may reach **only** `/m/...` (PWA files) and `/api/m/...`. Public source addresses are refused (`network_ok`); the whole dashboard stays PC-only. No UPnP, no port forwarding, no relay. |
| Pairing | Owner presses **Pair a phone** on the PC dashboard (loopback session). An 8-digit one-time code, valid 5 minutes, plus a **QR code** with the same code in the URL fragment (`/m/#c=...`, never sent to a server). Wrong codes: 5 per address and 20 in total, then the code is burnt. Single use. |
| Credential | Per-device bearer token (256 bit, `id.secret`). Only SHA-256 of the secret is stored (`mobile_devices.json`, mode 0600). Token is sent in `Authorization`, never in a URL, never in a cookie (so no CSRF); stored in the phone's `localStorage` (CSP `script-src 'self'`, no `innerHTML`). 30-day idle expiry. |
| Revocation | From the dashboard **Phone** page (or from the phone itself): the hash is wiped, the token stops working immediately. Offline revocation works (the store is a file). |
| Least privilege | A phone token reaches only: `me`, `status`, `chat`, `voice`, `approvals`, `unpair`. Per-device scopes (`chat`, `voice`, `status`, `approvals`) can be narrowed by the owner. It opens **nothing** else of the dashboard (files, settings, memory, backup, skills, devices, audit, other phones). Phone management routes (`/api/mobile/*`) are loopback-only and added to `ADMIN_ONLY` so LAN-dashboard devices cannot use them. |
| Approvals | Still answered in the **PC popup**. The phone sees a read-only list (id, title, kind, risk, seconds left; no details). The owner may switch on **phone approvals** (default OFF, confirmation dialog); then a phone may approve/deny. Typed-code approvals can **never** be approved from a phone. A message typed or spoken on the phone goes through the Brain only, never through the spoken-approval path, so "yes" on the phone cannot answer a PC approval. New approval channel `phone` is signed-file based like `dashboard`/`cli`. |
| Transport | Default: plain HTTP on the home LAN, private addresses only (known limit: someone on the same Wi-Fi can sniff). Optional **TLS**: `mobile_tls/cert.pem` + `key.pem`, or `jarvis dashboard --mobile --tls-selfsigned` (EC P-256, 825 days, SAN = LAN IP). Browsers give the microphone only to secure contexts, so **voice is refused (409) without TLS**; text, approvals and status work without. The PWA says so honestly. |
| Browser attacks | Host header must be loopback or this PC's LAN IP (DNS rebinding); writes need `X-Jarves: 1`; `Sec-Fetch-Site` must be same-origin/none and `Origin` must equal `scheme://Host`; CSP `default-src 'self'`; `X-Frame-Options: DENY`; no-store. |
| Rate limits | 120 requests/min per address (before auth), 120/min per device, chat 20/min, voice 10/min, 10 bad tokens/min per address then 429 for that address. Request bodies: JSON 8 KiB, audio 2 MB; 30 s socket timeout. |
| Audit | `mobile_audit.jsonl` (size-bounded, rotated): code created, paired, refused, auth refused, rate limited, scope refused, every write (device, method, path, status, character/byte counts), revoked, settings changed. **Never** message text, transcripts, audio, tokens or the code. |
| Logging (V4 package B) | Every `logs.*` call has a fixed literal message and codes `MOBILE_*`; variable parts are registered fields only (`device`, `reason`, `status`, `path`, `decision`, `error_type`, `transport`, `port`). `core/tools/log_lint.py` stays at **0 violations**. Chat/voice text is passed to the Core only; the phone layer logs counts. Provider/Core errors return a fixed message to the phone, never exception text. |
| Cloud-audio honesty | Voice PTT uses the Core's existing STT and Brain. The phone shows when audio is being recorded/sent; whether the Core uses cloud or local speech follows the owner's existing settings and is shown in `status`. |
| Retention / delete | Revoked devices keep only id, name, times; audit is size-bounded. Deleting `mobile_devices.json` unpairs every phone. |

## 3. Packages

| ID | Package | State on this branch |
|---|---|---|
| V5-A | Pairing, token store (hashed), revoke, scopes, idle expiry, one-time code + QR (`mobile.py`, `qrmini.py`) | **Done**, unit + HTTP tests |
| V5-B | Authenticated mobile API: `me`, `status`, `chat`, `voice`, `approvals` list/answer, `unpair`; rate limits; audit (`mobile.serve`) | **Done** |
| V5-C | PWA shell, English + Hebrew (RTL), manifest, service worker (shell only), push-to-talk recorder, honest "PC unreachable" (`dashboard_static/mobile/`) | **Done**; not opened in a real phone browser |
| V5-D | Core wiring: `Dashboard(mobile_on=...)`, `core_bridge(core, loop)`, feature `mobile_access`, `jarvis dashboard --mobile [--tls-selfsigned]`, dashboard **Phone** page, capability row, setup-wizard group | **Done**; Core launches it from `open_dashboard` when the switch is on |
| V5-E | TLS: self-signed generator and `mobile_tls/` certificates | **Done** (unit + HTTPS test); trusted-certificate story (local CA, installing the cert on the phone) **open** |
| V5-F | Independent security/privacy review (Opus, fresh context) + planted-secret test through `tools/redaction_audit.py` | **Done** (second-pass review, `docs/reviews/SWV5_SECURITY_REVIEW.md`: 4 findings fixed with tests; planted-secret audit test added; `redaction_audit.py` run still open) |
| V5-G | One shared conversation: phone turns appear in the desktop history; resume a phone conversation from Windows; idempotent send (client message ids), dedupe on reconnect | **Partly done**: phone turns use `Core.respond_remote` (same Brain, same context, logged as a typed exchange so they show in the desktop history and join the open typed conversation); one turn per phone at a time (409 BUSY). **Open**: client message ids / dedupe on reconnect, resuming a specific phone conversation from Windows |
| V5-H | Real-device acceptance on the owner's Android phone (pair, revoke, chat, PTT, approvals, host unavailable, Wi-Fi change, app suspend/resume, permission denial) | **Open, owner present** |
| V5-I | Always-on phone start: start the dashboard server with the Core (not only on "open dashboard"), firewall prompt guidance, stable LAN name | **Done**: `Core.start_mobile` at boot when `mobile_access` is ON (default OFF = nothing starts); the switch is re-read on every request. **Open**: firewall prompt guidance, stable LAN name |
| V5-J..M | Goals, Adaptive Memory, Proactive Intelligence, Development Manager (roadmap pillars 2-5) | **Not started** (need the backbone proven on a real phone first) |

## 4. API summary

Phone (bearer): `POST /api/m/pair {code,name}` (no auth, rate limited) -> `{token, device}`; `GET /api/m/me`; `GET /api/m/status`; `POST /api/m/chat {text}` -> `{reply, ok}`; `POST /api/m/voice` (body: 16-bit PCM WAV, TLS only) -> `{transcript, reply, ok}`; `GET /api/m/approvals`; `POST /api/m/approvals/<id> {approved}`; `POST /api/m/unpair`. PWA: `GET /m/`, `/m/app.js`, `/m/style.css`, `/m/sw.js`, `/m/manifest.webmanifest`, `/m/icon.svg`.

Owner (loopback dashboard session only): `GET /api/mobile`, `POST /api/mobile/code`, `POST /api/mobile/settings {phone_approvals}`, `POST /api/mobile/devices/<id>/revoke`, `POST /api/mobile/devices/<id>/scopes`, `GET /api/mobile/audit`.

## 5. Test plan

### 5.1 Automatic (CI, no real browser, no skips, event-driven waits)

`tests/test_mobile_access.py` (real HTTP against a real `Dashboard`, fake Core bridge): default OFF and loopback bind; network gate (public, LAN, loopback, off); phone token opens nothing of the dashboard; single-use code, hashed storage, expiry, attempt burn; X-Jarves/Origin/Sec-Fetch/Host refusals; auth, revoke, self-unpair, two phones, scopes, idle expiry; chat reaches the Core, length caps, no text in the audit; status; voice refused without TLS and working over real TLS; honest Core failure; chat/address/bad-token rate limits with an injected clock; approvals stay on the PC until enabled, phone approve/deny through the real `ApprovalService` and answer file, typed approvals PC-only; audit and owner views.
`tests/test_mobile_pwa.py`: files served with CSP, whitelist and traversal refusal, no inline script/style, EN/HE catalogs identical and covering every `data-t`/`t()` key and element id, no `innerHTML`/token-in-URL, dashboard Phone page wired, QR structure (finder, timing, format BCH) and Reed-Solomon known vector, Core adapter (Brain without speaker, STT resample to 16 kHz, no status detail leak).
Also kept green: `test_log_message_lint.py` (0 violations), `test_cli_parity.py`, `test_capabilities.py`, `test_features.py`, `test_setup_wizard.py`, dashboard and LAN tests.
One manual check done by the agent: the QR matrix was decoded with OpenCV's `QRCodeDetector` (5 payloads, incl. Hebrew); not in CI because OpenCV is an optional extra.

### 5.2 Manual — owner script (real Android phone, evidence class `owner`)

1. `jarvis features on mobile_access`, start JARVES; open the dashboard **Phone** page; note "On" and the address.
2. Press **Pair a phone**; scan the QR with the phone camera; the PWA opens and pairs. Check: wrong code refused; the same QR a second time refused; code expires after 5 min.
3. Chat in English and Hebrew; the answer appears on the phone and the turn is in the desktop history.
4. With `--tls-selfsigned`: accept the certificate warning once; hold-to-talk; the phone shows the transcript and answer; refuse the microphone permission and read the message.
5. Trigger an approval on the PC (e.g. a risky tool). It appears in the Approvals tab as read-only; answer on the PC popup. Then switch **phone approvals** on, repeat, answer from the phone; check a typed-code request cannot be approved from the phone.
6. Turn Wi-Fi off/on, lock the phone, sleep the PC: the pill says "computer unreachable" and recovers; no message is sent twice.
7. Revoke the phone from the dashboard: the next request on the phone returns to the pair screen. Pair again; un-pair from the phone itself.
8. Open the audit log: no message text, tokens or codes anywhere.

### 5.3 Manual — agent-driven

Headless: run `jarvis dashboard --mobile --no-open` on the offline `test` profile and drive `curl` against the API (pair, chat with a fake brain, revoke); run `tools/redaction_audit.py --both-modes` with a planted secret in a chat message and a pairing code (V5-F).

## 6. Definition of done for V5-core

Packages A-E done and tested (this branch); F (review) and H (real phone) pass or are accepted as BLOCKED by the owner in writing; G (shared conversation) done; no change to the rule "approvals are answered on the PC unless the owner enables phone approvals". Evidence classes are never mixed: everything above 5.1 is `virtual/unit`; only 5.2 is `owner`.

## 7. Owner decisions needed

1. Is plain HTTP (text, approvals, status only) acceptable on the home Wi-Fi, with TLS required only for voice? Recommendation: yes, default off, TLS recommended.
2. Trusted certificate: accept the one-time self-signed warning, or add a local CA installed on the phone (more work, better UX)?
3. Should phone approvals ever be allowed for `high` risk requests, or only `low`/`medium`? (Now: all non-typed requests once the owner enables it.)
4. Should the Core start the phone server at boot (V5-I) when the switch is on, or only after "open the dashboard"? Recommendation: at boot.
