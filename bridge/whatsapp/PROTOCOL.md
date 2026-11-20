# JARVES WhatsApp BridgeProtocol v1

Normative contract between the JARVES Core (Python, `core/src/jarves/whatsapp_bridge.py`) and the WhatsApp bridge
process: the real engine (`bridge/whatsapp`, Go + whatsmeow, a linked device) and the fake used by every automatic
test (`core/tools/fake_whatsapp_bridge.py`). Both bridges implement exactly this document; the shared policy table
`bridge/whatsapp/testdata/policy_table.json` is run by the Go tests and the Python tests. Design and rationale:
`docs/plans/WHATSAPP_REAL_ACCESS.md` section 4, SW-V4 package F. Issue #17.

The bridge enforces every rule BY ITSELF (mode, allow-lists, rate limits, quiet hours, single-use signed approvals,
kill switch, maximum length). The Core repeats the checks only to give friendly answers early; a compromised Core
still cannot send without a valid approval, to a non-allow-listed contact, above the limits, or in read mode.

## 1. State directory `<state_dir>/whatsapp_bridge/` (per-user ACL; never in backups, logs or diagnostics)

| File | Owner | Content |
|---|---|---|
| `token` | bridge creates (0600) | user bearer: read + send. `wab_` + 43 base64url chars |
| `admin.token` | bridge creates (0600) | admin bearer: mode, allow-lists, kill, link, unlink, purge, retention. `waa_` + 43 base64url chars |
| `approval.key` | bridge creates (0600) | 64 hex chars = 32-byte HMAC key for approval ids (the Core approvals service signs) |
| `endpoint.json` | bridge writes atomically after listening | `{"v":1,"port":N,"pid":P,"engine":"whatsmeow"|"fake","version":"...","started_at":ts}` |
| `policy.json` | bridge (written only through admin calls) | mode, allow-lists, retention, watch window (section 5) |
| `KILL` | anyone on the PC (CLI, dashboard, the bridge itself) | presence = killed. Polled at least every 250 ms |
| `store.enc` | bridge | device keys + message DB, AES-256-GCM (section 6) |
| `store.key` | bridge | the store key wrapped by Windows DPAPI (CurrentUser); see section 6 |
| `audit.jsonl` | bridge | one line per send attempt: `{ts, chat_hash, text_sha256, chars, result}`; never text or numbers |

Tokens and keys are never logged, printed, put in an error message, or returned by any endpoint.

## 2. Transport

- HTTP/1.1 on `127.0.0.1:<random port>` only (never 0.0.0.0, never IPv6 any). Requests with a `Host` other than
  `127.0.0.1:<port>` or `localhost:<port>` are refused (403, DNS-rebinding guard). No CORS headers ever.
- `Authorization: Bearer <token>` on every request. Missing/wrong -> 401 `WA_UNAUTHORIZED`. An endpoint marked
  ADMIN refuses the user token with 403 `WA_FORBIDDEN` (the Core gives the model's tools the user token only).
- Bodies are JSON (`Content-Type: application/json`, at most 64 KiB; more -> 413 `WA_BAD_REQUEST`).
- Errors: HTTP status + `{"error": {"code": "WA_...", "message": "<fixed English sentence>", ...extra}}`. The
  message never contains message text, names, numbers, tokens or paths.
- Times are Unix seconds (float). Chat ids are WhatsApp JIDs: `<digits>@s.whatsapp.net` (phone), `<digits>@lid`
  (LID user), `<id>@g.us` (group). Numbers in E.164 with `+`.

## 3. Endpoints

| Method + path | Token | Purpose |
|---|---|---|
| `GET /status` | user/admin | health and counters (no names, numbers or text) |
| `GET /chats?since=&limit=` | user | readable chats (read allow-list applied; groups hidden by default) |
| `GET /messages?chat=&since=&limit=` | user | messages of ONE readable chat, oldest first |
| `GET /search?q=&limit=` | user | case-insensitive substring search in readable chats within retention |
| `POST /seen {"chat"}` | user | local unread counter to 0. NEVER sends a read receipt to WhatsApp |
| `POST /send {"to","text","approval_id"}` | user | send ONE text message (section 4) |
| `GET /events` (WebSocket) | user | push events (section 7) |
| `POST /mode {"mode", "override_watch"?}` | ADMIN | `off` / `read` / `send` (section 5) |
| `POST /allow {"list":"read"|"send","op":"add"|"remove","chat"}` | ADMIN | edit an allow-list; `chat` = JID or `+number` |
| `POST /read_scope {"all_direct":bool,"groups":bool}` | ADMIN | default read scope |
| `POST /kill {"on":bool,"logout":bool}` | ADMIN | kill (also by creating the `KILL` file) / release |
| `POST /link {"phone"?}` | ADMIN | start linking: QR, or a pairing code when `phone` is given |
| `GET /link` | ADMIN | `{"state":"idle"|"waiting"|"linked"|"timeout"|"error","qr":str,"qr_text":str,"pairing_code":str,"expires_at":ts}` |
| `POST /unlink` | ADMIN | log the linked device out on WhatsApp's side and wipe the store |
| `POST /retention {"days":int,"keep_nothing":bool}` | ADMIN | 1..90 days; keep_nothing = live only, nothing on disk |
| `POST /purge {"older_than_days":int|null}` | ADMIN | delete stored messages (null = all) -> `{"deleted":n}` |
| `GET /audit?limit=` | ADMIN | the send audit lines (hashes only) |
| `GET /policy` | ADMIN | `policy.json` content (allow-lists hold JIDs: shown only on the PC) |
| `POST /shutdown` | ADMIN | orderly stop (store sealed first) |

`GET /status` fields: `v, engine, version, mode, killed, kill_reason, connection` (`not_linked | linking | connecting |
connected | disconnected | logged_out`), `linked, device_name` ("JARVES bridge"), `phone_last_seen` (ts of the last
message sent from the owner's phone, or null), `account_warning` (null or `{code, at}`), `warnings_total`,
`read_since, watch_days_left, retention_days, keep_nothing, counts {chats, messages, unread}, rate {last_15s, hour, day,
recipients_hour, limits}, read_scope {all_direct, groups, allow}, send_allow` (count), `started_at, now`.

Chat object: `{"id","name","number","is_group","unread","last_ts","has_history","lid","can_send"}` (`can_send` = on the send allow-list; always false for a group). Message object:
`{"id","chat","from_me","sender_name","ts","kind","text","media","edited","deleted"}`; `kind` = `text | image | video |
audio | document | sticker | location | contact | other`; `media` = null or `{"mime","size","caption"}` (metadata only:
media is NEVER downloaded or opened); `text` = "" for non-text kinds.

Read gate: a direct chat is readable when `read_scope.all_direct` or it is on the read allow-list; a group only when
`read_scope.groups` AND it is on the read allow-list. Mode `off` refuses every read with 409 `WA_MODE_OFF`.

## 4. Sending: the policy (normative order; first failing rule wins)

1. killed -> 423 `WA_KILLED`
2. mode `off` -> 409 `WA_MODE_OFF`; mode `read` -> 409 `WA_MODE_READ`
3. text empty after trim -> 400 `WA_EMPTY`; more than 1500 Unicode code points -> 400 `WA_TOO_LONG`
4. `to` ends with `@g.us` -> 403 `WA_GROUP_REFUSED`; not `@s.whatsapp.net`/`@lid` -> 403 `WA_BAD_RECIPIENT`
5. `to` not on the send allow-list -> 403 `WA_NOT_ALLOWED`
6. `to` has no stored chat history -> 403 `WA_NO_HISTORY`
7. approval id: missing -> 403 `WA_APPROVAL_MISSING`; bad format or signature, or `exp` more than 300 s ahead ->
   403 `WA_APPROVAL_INVALID`; `exp` passed -> 403 `WA_APPROVAL_EXPIRED`; `to` or text hash differ -> 403
   `WA_APPROVAL_MISMATCH`; nonce already used -> 403 `WA_APPROVAL_REUSED`
8. local hour in 23:00-06:59 and the approval has no `night: true` -> 403 `WA_QUIET_HOURS`
9. rate limits over the sends that were really sent: one in the last 15 s -> `per_15s`; 10 in the last hour ->
   `per_hour`; 30 in the last 24 h -> `per_day`; 3 distinct OTHER recipients in the last hour -> `recipients_per_hour`.
   429 `WA_RATE_LIMITED` with `limit` and `retry_after_s`
10. not connected -> 503 `WA_NOT_CONNECTED`
11. send; the nonce is consumed only now (a refused request leaves the approval usable until it expires).

No groups, media, forwarding, status or broadcasts exist in v1: the only send is one text to one person.

**Approval id** (minted by the Core approvals service only after the owner approved the popup that showed the
recipient's name, number and the full text): `wa1.<B64>.<SIG>` where `B64` = base64url (no padding) of the UTF-8 JSON
`{"v":1,"n":"<32 hex nonce>","to":"<jid>","h":"<sha256 hex of UTF-8 to + '\n' + text>","exp":<unix s>,"night":bool}`
and `SIG` = base64url (no padding) of HMAC-SHA256(`approval.key`, ASCII `"wa1." + B64`). The signature is checked
over the exact received string before the JSON is parsed. Used nonces are kept until their `exp` passes.

## 5. Modes, watch window, kill

- `policy.json`: `{"v":1,"mode":"off","read_scope":{"all_direct":true,"groups":false},"read_allow":[],"send_allow":[],
  "retention_days":14,"keep_nothing":false,"read_since":null,"warnings_since_read":0}`. Default mode `off`.
- Mode changes only through ADMIN `POST /mode` (the PC: `jarvis whatsapp mode` or the dashboard's local session),
  never by the model, never by voice, never remotely.
- `read_since` is set when the mode becomes `read`, or when `read` is selected again while `read_since` is null; any `account.warning` resets it to null (and `warnings_since_read` counts up). `POST /mode {"mode":"send"}`
  is refused with 409 `WA_WATCH_WINDOW` (`days_left`) until 14 days in read mode with zero warnings have passed,
  unless `"override_watch": true` (the CLI asks the owner to type a confirmation first).
- Kill: `POST /kill {"on":true}` or the `KILL` file: sends stop at once, the engine disconnects within 2 s, reads keep
  working on stored data. `logout: true` also unlinks the device. The bridge kills ITSELF (creates `KILL` with the
  reason) on `account.warning`, `logged_out` and a temporary ban. Release: `POST /kill {"on":false}` (removes `KILL`).

## 6. Store encryption

All device keys, contacts and messages live in one SQLite database held in memory and written to disk only as
`store.enc` = `JWS1` magic + 12-byte nonce + AES-256-GCM(ciphertext + tag), additional data `"jarves-wa-store-v1"`,
atomically (temp + rename) at most every 5 s after a change and at shutdown. The 32-byte key is random, wrapped with
Windows DPAPI `CryptProtectData` (CurrentUser scope, entropy `"jarves-wa-store-v1"`) in `store.key`. A copied
directory therefore fails to decrypt for another Windows user or PC (`WA_STORE_LOCKED`). Off Windows (the Pi, later)
the key file is plain 0600 and status reports `"key_protection":"file"` (documented, unverified).

## 7. Events (`GET /events`, WebSocket, user token)

Server -> client text frames, JSON, increasing `seq`: first `{"type":"hello","seq":0,"status":{...}}`, then
`message.new {chat, id, ts, from_me, kind}` (NO text: the Core fetches text only when the owner asks),
`receipt {chat, id, state}`, `connection.state {state}`, `account.warning {code}`, `logged_out {reason}`,
`killed {on, reason}`, `mode.changed {mode}`. The server pings every 20 s. Events of unreadable chats are not sent.

## 8. Engine notes (real bridge only)

whatsmeow (pinned in `bridge/whatsapp/go.mod`) registers as a linked device named "JARVES bridge", exactly as the
open-source library does; nothing spoofs another client or evades WhatsApp's protections. No read receipts, no
presence, no typing indicators are ever sent. History sync is stored only for readable chats within retention.
