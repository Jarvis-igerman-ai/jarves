# JARVES Companion API v1

Machine-readable twin: [`companion_v1.json`](companion_v1.json) (routes, scopes, request/response shapes, errors, rate limits,
what is not available). `core/tests/test_companion_contract.py` fails when a phone route exists that is not listed here, or
a listed route does not exist (it reads the server code and the PWA `mobile/*.js`).

Decision: the PC-hosted Core stays the brain. An Android app is a client of the EXISTING authenticated phone API
(`/api/m/*`): no new auth, no cloud relay, push-to-talk only. See `docs/plans/ANDROID_DAILY_COMPANION_ADR_2026-10-26.md`.

## Pairing and auth
1. The owner makes a one-time 8-digit code on the PC (dashboard, Phone page; valid 5 minutes, single use).
2. `POST /api/m/pair` with `{"code": "12345678", "name": "Pixel"}` and header `X-Jarves: 1` returns `{"ok": true, "device": "...", "token": "..."}`.
3. Every later call sends `Authorization: Bearer <token>`. Non-GET calls also send `X-Jarves: 1`. Tokens are stored hashed on the PC and
   can be revoked there at any time (the next call is `401`).

## Versioning
- Current version: `1`. The server states it in the `X-Jarves-Api: 1` response header and in `GET /api/m/companion/info` (`api`, `api_min`).
- Within v1 changes are additive only: new routes, new optional response fields. A client ignores unknown fields.
- A breaking change would be v2, with the v1 routes kept for a published period.

## Info (the first call an app makes)
`GET /api/m/companion/info` (any valid token, no scope, read-only):
```json
{"api": 1, "api_min": 1, "core_version": "4.0.1", "server_time": 1793000000.5,
 "pc": {"state": "online", "core": "running"},
 "scopes_supported": ["chat","voice","status","approvals","tasks","prefs","drive"], "scopes": ["chat","status"],
 "phone_approvals": false, "beta_features": false,
 "features": {"companion_api": {"stage": "beta", "enabled": false}}}
```
- `server_time` (epoch seconds): compare with the phone clock to detect skew (the `age_s` field of command bodies relies on it).
- `pc.state`: `online` when the Core answers; `offline` when the Core is not running or unreachable. If the PC itself cannot be
  reached, the request fails at the network level: the app shows "PC offline" and queues nothing by itself.
- `features[*].stage`: `stable` or `beta` from the generic release gate; a beta feature is OFF until the owner opts in on the PC.

## Scopes
Default: `chat`, `voice`, `status`, `approvals`. Optional (owner grants per device): `tasks`, `prefs`, `drive`, `browser` (beta: the browser pilot viewer, https only, `/api/m/browser*`, not used by the app).
A missing scope gives `403 {"error": "this device may not use <scope>"}`. Pages (`home`, `chat`, `approvals`, `health`) are a second per-device grant.

## Main calls (full list in the JSON)
| Call | Scope | Notes |
|---|---|---|
| `GET /api/m/me` | none | device, scopes, pages |
| `GET /api/m/status` | status | `{"core": "running", "components": {"audio": "ok"}, "tls": true}` |
| `POST /api/m/chat` | chat | `{"text": "hi", "id": "msg-0001"}` returns `{"reply": "...", "ok": true}`; same `id` replays the stored answer |
| `GET /api/m/sync` | status | one poll: turns, notices, goals, `approvals_waiting` |
| `GET /api/m/approvals` | approvals | `{"pending": [{"id","title","text","text_sha","kind","risk","seconds_left","typed","pc_only"}], "phone_approvals": false}`; `title` is a short label (120 characters); `text` is the first 400 characters of the real action text (redacted); `text_sha` is 16 lower-case hex characters fingerprinting all of it (redacted prompt, kind, details) |
| `POST /api/m/approvals/{id}` | approvals | `{"approved": true, "id": "cmd-00001", "text_sha": "<optional, from the list>"}`; with `text_sha`, a changed action text gives `409 STALE_TEXT` (and a missing one `409 APPROVAL_ALREADY_ANSWERED`) and nothing is answered; a malformed value is `400`; see rules below |
| `GET /api/m/plans` | tasks | steps and `can` list |
| `POST /api/m/plans/op` | tasks | `{"id": "cmd-00002", "plan": "12", "op": "pause"}`; ops: pause, cancel, resume, retry |
| `POST /api/m/goals/op` | tasks | `{"id": "cmd-00003", "op": "status", "goal": 3, "status": "paused"}` |
| `POST /api/m/voice` | voice | push-to-talk WAV, HTTPS only |
| `GET/POST /api/m/drive/*` | drive | Drive tab calls, links only |

Write calls carry a command `id` (8-64 chars of letters, digits, `_ . : -`): a resend with the same id returns the first result with `"replay": true`.

## Approval rules
- Phone approvals are OFF by default: `POST /api/m/approvals/{id}` returns `403 {"code": "PHONE_APPROVALS_OFF"}` until the owner turns them on on the PC.
- Even then: typed-code approvals (`TYPED_PC_ONLY`) and approvals flagged `pc_only` (`PC_ONLY`) are never approved from a phone. A phone may always deny.
- GitHub approvals are local-only. Plan steps `approve`, `skip`, `delete` are PC-only (`PLAN_PC_ONLY`).

## Errors
`400` bad body, `401` not paired or revoked, `403` scope/page/PC-only (with a `code`), `404` unknown route, `409` approval already answered,
`429` rate limited (`Retry-After: 60`), `503` Core part unavailable. Error bodies are `{"error": "...", "code": "..."}` (code optional).

## Rate limits
120 requests per minute per address and per device; chat 20, voice 10; 10 bad tokens per minute per address; chat long-poll 600.

## Not available to the app
Files, memory, secrets, settings, devices, backup, updates, skills, logs, dashboard (`403 PAGE_PC_ONLY`); typed-code and GitHub approvals;
plan approve/skip/delete; always-listening audio (push-to-talk only); a push registration route (placeholder: the app polls `/api/m/sync`).
Version 1 adds no new write capability.
