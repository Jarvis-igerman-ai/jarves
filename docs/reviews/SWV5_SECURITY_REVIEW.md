# SW-V5 security review: PC-hosted mobile access (V5-F)

Scope: `mobile.py`, `qrmini.py`, the `Handler._handle` gate in `dashboard.py`, `dashboard_auth.py` (host/ADMIN_ONLY), `approvals.py` (phone channel), `core_bridge`, the PWA (`dashboard_static/mobile/`), `mobile_admin.js`, `app.py` wiring. Method: read the code against the checklist below, probe with the existing HTTP harness, add a regression test per finding (`core/tests/test_mobile_access.py`, `test_sr*`). Reviewer: a second pass by the same toolchain with fresh reading of the code, not a human or third-party audit; evidence class `virtual/unit`. The real-phone checks (V5-H) stay with the owner.

## Checklist result

| Item | Result |
|---|---|
| Token hashing | OK. 256-bit secret, only SHA-256 stored, `compare_digest`, hash wiped on revoke, id and hash never in the owner view. Lookup by id then constant-time hash compare. |
| One-time code brute force / expiry | OK. 8 digits, 5 tries per address, 20 total then burnt, single use, 5 min TTL, identical 403 for wrong/expired/burnt (no oracle), the per-address lock is checked before the compare. Residual: a LAN peer can burn a code with 20 wrong tries (denial of pairing only; the owner makes a new one). |
| Rate limits | OK. 120/min per address before auth, 120/min per device, chat 20, voice 10, 10 bad tokens per address. See SR-4 for concurrency. |
| Scope checks | OK. Every route calls `need(scope)`; `me` and `unpair` need only a valid token. Owner routes are `ADMIN_ONLY` and unreachable from a non-loopback client in phone mode. |
| CSRF / origin | OK. Bearer in `Authorization`, no cookie; writes need `X-Jarves: 1`, `Sec-Fetch-Site` same-origin/none and `Origin == scheme://Host`; Host must be loopback or this PC's LAN address (DNS rebinding). |
| TLS-only microphone | OK. `/api/m/voice` returns 409 unless the server really runs TLS; the PWA also disables the button outside a secure context. |
| QR content | OK. Only `scheme://LAN-ip:port/m/#c=<one-time code>`: never a token, the code is in the fragment (not sent to a server) and dies after one use or 5 min. |
| Audit has no content | OK, with a hardening (SR-2). Fields are ids, paths, statuses, counts, reasons. Regression test plants a PIN, an API-key-shaped string, a token and a code and greps the file. |
| Approvals vs typed codes | OK. The in-process service rejects an approve without the code (the phone never has it); the file path pre-check does too; phone text goes to the Brain only, never `answer_words`. Regression test `test_sr5`. |
| Revoke immediate | OK. The token store is read on every request (no cache). Test `test_sr7`. |
| Nothing beyond localhost while OFF | Mostly OK: default `Dashboard` binds `127.0.0.1`, phone paths 404, `jarvis dashboard --mobile` refuses. Gap found, SR-1. |

## Findings

| ID | Severity | Finding | Fix |
|---|---|---|---|
| SR-1 | Medium | The `mobile_access` switch was read once when the dashboard started. Turning it OFF at run time (`applies: restart`) left the phone server answering on the home network until the next restart, contradicting "OFF = nothing served". | `MobileAccess.enabled` is now live: a `gate` callable (the Core passes `features.enabled("mobile_access")`) is checked on every request and fails closed on error. Tokens survive an OFF/ON cycle. Test `test_sr1`. |
| SR-2 | Low | Unauthenticated requests (wrong codes, bad tokens, rate-limit hits, cross-origin refusals) each wrote an audit line. A LAN peer could fill the size-bounded audit (512 KB, one rotation) and push real events (paired, revoked, approvals) out. | `audit_noisy`: 5 lines per address and event per minute, then a `suppressed` count on the next window. Test `test_sr2`. |
| SR-3 | Low | Device store temp files and the self-signed TLS key were created with default permissions and chmod-ed afterwards (a short readable window on POSIX). | `write_private` creates them `0600` from the start. Test `test_sr3`. |
| SR-4 | Low | A paired phone could run unlimited parallel chat/voice turns (within the per-minute limit): a double tap or a retry after a Wi-Fi blip ran the same message twice and piled up Brain calls. | One turn per phone at a time: a second concurrent call returns 409 `BUSY`; the PWA shows a localized message (EN+HE). Test `test_sr4`. |

## Accepted / documented, not fixed

- **Plain HTTP on the home Wi-Fi** (no TLS): a device on the same network can read traffic and the bearer token. Known limit in the work package; TLS is the mitigation (`--tls-selfsigned`), required for voice. Owner decision 1.
- **Header-read slowloris**: the 30 s socket timeout starts after the request line/headers are read; a hostile LAN peer can hold many threads. The server is LAN-only and the peer must be on the owner's network. Not fixed (a global handler timeout could break long dashboard requests).
- **Two processes, one store**: a standalone `jarvis dashboard --mobile` and the Core both writing `mobile_devices.json` could lose an update (read-modify-write, per-process lock). Use one at a time.
- **Phone approvals for high risk** (owner decision 3): once the owner enables phone approvals, a phone with the `approvals` scope can approve any non-typed request. Default OFF; typed-code requests are always PC-only.
- **Stolen unlocked phone**: chat with the `chat` scope runs the same Brain as the desktop; risky tools still need a PC approval. Revoke from the dashboard is immediate.
- **Stale LAN address**: the Host allow-list holds the LAN IP seen at start; a DHCP change needs a restart (availability only).

## Not covered

No external penetration test, no real-browser or real-phone run (V5-H), no fuzzing of the WAV decoder beyond the 2 MB cap and the 502 on any decoder error.
