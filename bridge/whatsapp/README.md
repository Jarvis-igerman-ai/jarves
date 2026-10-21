# JARVES WhatsApp bridge (Go, whatsmeow)

A small separate process that connects to the owner's WhatsApp account as a **linked device named "JARVES bridge"**
and offers the loopback-only, token-protected **BridgeProtocol v1** ([PROTOCOL.md](PROTOCOL.md)) to the JARVES Core.
The bridge enforces every rule by itself (mode off/read/send, allow-lists, rate limits, quiet hours, single-use signed
approvals, kill switch, 1500-character limit), so a compromised Core still cannot send without the owner's approval.
Design: `docs/plans/WHATSAPP_REAL_ACCESS.md` section 4, SW-V4 package F, issue #17.

## Status: real WhatsApp end-to-end is BLOCKED until the owner links a device

Everything below is verified offline (unit tests, API tests against the fake engine, a local start/stop smoke test of
the real binary in mode `off`). **No test has ever linked an account or contacted WhatsApp's servers.** The real
engine path (QR / pairing-code linking, live messages, history sync, sending, receipts, logout, temporary-ban events)
stays **unverified** until the owner links a device and runs the owner-assisted checklist in
`docs/plans/WHATSAPP_REAL_ACCESS.md` section 6.

## Terms of Service and ban risk (honest note)

whatsmeow is an **unofficial** client of the WhatsApp Web multi-device protocol. Using it breaks WhatsApp's Terms of
Service (no access "through unauthorized or automated means"). WhatsApp has temporarily or permanently banned accounts
that used unofficial clients; the risk is real and **cannot be quantified** (enforcement is automated and not
public). The owner accepted this risk for his **main number, read-only first** (owner answer, SW-V4 F). The bridge
reduces the risk where it can (no read receipts, presence or typing; human-like rate limits; no groups, media,
forwarding or broadcasts; auto-kill on any warning, temporary ban, stream replacement or logout), but nothing makes
it compliant. Unlinking on the phone (Settings > Linked devices) always stops it.

## Build

Requirements: Go **go1.27.0** exactly (https://go.dev/dl/), no C compiler (pure Go, `CGO_ENABLED=0`).

```powershell
./scripts/build_whatsapp_bridge.ps1 -Version 0.1.0          # verify, vet, test, build
./scripts/build_whatsapp_bridge.ps1 -Version 0.1.0 -Verify bridge/whatsapp/dist/whatsapp_bridge.exe.sha256
```

Output (git-ignored): `bridge/whatsapp/dist/whatsapp_bridge.exe`, `whatsapp_bridge.exe.sha256`, `build-info.json`
(go version, whatsmeow version, commit, sha256). The build is reproducible: `-trimpath -buildvcs=false
-ldflags "-s -w -buildid= -X main.version=<v>"`, `GOTOOLCHAIN=local`, `GOAMD64=v1`; `-Verify` rebuilds into a temp
directory and fails unless the SHA-256 is identical (CI job `whatsapp-bridge` does exactly that and uploads the
artifact `whatsapp-bridge-windows`). `packaging/build_windows.ps1` ships the exe under `bridge\` when it was built,
and `packaging/payload_check.py` rejects it if it does not match its `.sha256`.

Development: `cd bridge/whatsapp; go vet ./...; go test ./...` (offline).

## Run

```
whatsapp_bridge --state-dir %LOCALAPPDATA%\jarves [--port 0]
whatsapp_bridge --version
```

The bridge listens on `127.0.0.1:<random port>`, writes `<state-dir>/whatsapp_bridge/endpoint.json` atomically
after listening, logs JSON lines `{ts, level, code, msg, fields}` to stderr (codes and counts only: never tokens,
text, names or numbers), and stops on Ctrl+C / SIGTERM / `POST /shutdown`, sealing the store first. The default mode
is `off`: nothing connects to WhatsApp until the owner sets `read` (or links a device) from the PC.

## Layout

| Path | What |
|---|---|
| `cmd/whatsapp_bridge` | the command (flags, store unlock/restore, wiring) |
| `internal/policy` | pure send policy (section 4) + approval verify/sign; runs `testdata/policy_table.json` |
| `internal/api` | HTTP/WS server (sections 2-5, 7): auth, Host guard, policy.json, KILL polling, watch window, retention, audit, nonces |
| `internal/engine` | `Engine` interface + in-memory `Fake` used by the API tests |
| `internal/engine/wm` | the whatsmeow engine |
| `internal/memdb` | the single in-memory SQLite DB (modernc, serialize/deserialize) |
| `internal/msgstore` | chats + messages (text, media metadata only) in that DB |
| `internal/securestore` | `store.enc` AES-256-GCM (`JWS1` + nonce + ct/tag), DPAPI-wrapped `store.key`, per-user dir ACL |

State directory `<state-dir>/whatsapp_bridge/` (DACL: current user only): `token`, `admin.token`, `approval.key`,
`endpoint.json`, `policy.json`, `KILL`, `store.enc`, `store.key`, `audit.jsonl`, plus `nonces.json` (used approval
nonces until their `exp`, so a restart cannot replay an approval).

## Pinned versions (checked 2026-10-20)

| Component | Version | Source |
|---|---|---|
| Go toolchain | go1.27.0 (`go 1.26.0` language, `toolchain go1.27.0`) | https://go.dev/dl/ |
| whatsmeow | `v0.0.0-20261017175727-b2b1e71d45e2` (commit b2b1e71d45e2, 2026-10-17) | https://github.com/tulir/whatsmeow |
| modernc.org/sqlite (pure-Go SQLite) | v1.60.1 | https://gitlab.com/cznic/sqlite |
| github.com/coder/websocket | v1.8.15 | https://github.com/coder/websocket |
| github.com/mdp/qrterminal/v3 | v3.2.1 | https://github.com/mdp/qrterminal |
| golang.org/x/sys (DPAPI, ACLs) | v0.49.0 | https://pkg.go.dev/golang.org/x/sys |
| actions/setup-go (CI) | v7 (v7.0.0, 2026-07-16) | https://github.com/actions/setup-go |

All module checksums are in `go.sum` and checked by `go mod verify`. Update whatsmeow monthly (WhatsApp changes its
protocol; an outdated client gets `client_outdated`, which auto-kills the bridge).

## Engine notes

- Device name "JARVES bridge" via `store.DeviceProps.Os`; everything else is the library's own linked-device
  registration. Pairing by code needs a `Browser (OS)` display name validated by WhatsApp's server; the bridge passes
  `Chrome (Windows)` with `PairClientChrome` as the library's API requires. QR linking (the default) needs none.
- Never sends read receipts, presence or typing (`MarkRead`, `SendPresence`, `SendChatPresence` are never called).
- Media is never downloaded: only mime, size and caption are stored. Status broadcasts and newsletters are ignored.
- History sync and live messages are stored only for readable chats and within retention (`keep_nothing` = no
  message rows at all). Chat rows (id, name, counters, `has_history`) are kept so the send rule "prior chat history"
  works after a retention purge.
- Auto-kill (creates `KILL` with the reason): `account.warning` (temporary ban, stream replaced, client outdated,
  account restricted / reach-out timelock, connect failure 402/403/406) and `logged_out`.
- `phone_last_seen` = timestamp of the newest `from_me` message that the bridge did not send itself.
