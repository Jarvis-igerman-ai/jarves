# ADR: Android daily companion architecture (V7 slice A0, issue #388)

Status: **proposed** (docs only, 26 Oct 2026). No code, no install, no phone change. Umbrella: issue #50 (V7 work package of 26 Oct 2026). Gate: V5 phone acceptance (#193) before any A1+ code. Index: [DOC_MAP](../DOC_MAP.md).

Sources are official pages read on 26 Oct 2026. Short quotes are attributed; everything else is paraphrase. Items marked **unverified** were not re-read.

## 1. Context

The owner wants JARVES to be a daily companion on his Android phone: talk to it, get notifications and reminders, approve or skip actions. Standing constraints:

- One brain: the Python Core on the PC stays authoritative (see [MULTI_DEVICE_REMOTE](MULTI_DEVICE_REMOTE.md)). No second memory, approval ledger or auth path.
- The phone already reaches the Core over home Wi-Fi and Tailscale, off by default, with pairing, per-phone tokens, scopes, rate limits and an audit log ([PHONE_ACCESS_TAILSCALE](../PHONE_ACCESS_TAILSCALE.md), [V5 package](../JARVES_SW_V5_WORK_PACKAGE.md)).
- The owner wants **no cloud server** of our own.

## 2. Decision

1. **PC-hosted Core first.** The phone is a thin client (voice, notifications, approvals, status). The Core, memory, approvals and skills run only on the PC. If the PC is off, the app shows a "PC offline" banner and does only the offline basics below.
2. **Client form: start with the existing phone web client (PWA, V5); build a native Kotlin/Compose shell only for what a PWA cannot do reliably** (notification actions while the app is closed, exact alarms, share-to-JARVES, push-to-talk from a notification). Both talk to the same companion API (slice A1) over the existing pairing link.
3. **Offline basics, no second brain:** reminders, timers and notes are stored locally on the phone and synced to the Core later (last-writer-wins with tombstones and a per-item version, slices A5/A6). The phone never runs a model, never holds memory beyond those three item types, never approves anything offline.
4. **Voice is push-to-talk only** (started from a visible activity or a notification action). No always-on listening and no phone wake word in V7.
5. **Push without a cloud server: poll plus LAN/Tailscale first; optional self-hosted ntfy (UnifiedPush) second; FCM only if the owner explicitly links his own Firebase project** (default: not enabled).

## 3. Android platform limits (official)

- **Foreground service from the background.** Apps targeting Android 12+ cannot start a foreground service from the background except in listed cases, such as starting from a visible activity, the user tapping a notification, bubble or widget, a high-priority FCM message, an exact alarm for a user-requested action, boot-completed, or the user turning off battery optimisation for the app. [FGS background-start restrictions](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start)
- **Microphone is "while-in-use".** On Android 14+, an app cannot create a foreground service that needs the microphone (or camera, location) while in the background even if another exemption applies; it gets a `SecurityException`. Notification and widget interaction are listed exceptions, as is an app providing `VoiceInteractionService`. Same page. **Consequence:** push-to-talk must start from the visible app or a notification action; there is no hands-free wake word.
- **Microphone service type.** Needs `foregroundServiceType="microphone"`, the `FOREGROUND_SERVICE_MICROPHONE` permission and a granted `RECORD_AUDIO`; it cannot be created from a `BOOT_COMPLETED` receiver. [Foreground service types](https://developer.android.com/develop/background-work/services/fgs/service-types)
- **Assistant role.** The `VoiceInteractionService` exemption exists only for the app holding the assistant role. JARVES does not claim that role in V7 (it would replace the system assistant; a separate ADR later). [VoiceInteractionService](https://developer.android.com/reference/android/service/voice/VoiceInteractionService)
- **Notifications** need the runtime `POST_NOTIFICATIONS` permission on Android 13+, and exact alarms need exact-alarm access on recent versions (**unverified in this pass**; A3/A6 re-check the current pages before coding).
- **OEM battery managers** that kill background apps beyond stock Android are not an official Android topic; A7 builds a per-device matrix from real tests on the dedicated test phone (**unverified**).

## 4. Push options without our own cloud server

| Option | How it works | For | Against |
| --- | --- | --- | --- |
| A. Poll or long-poll over LAN/Tailscale while the app is alive | App asks the Core; the Core answers over the paired link | No third party, simplest, fits "no cloud server" | Only while the app process is alive and the PC reachable; Doze delays background network |
| B. Foreground "connected" service with a persistent notification | Keeps a socket to the Core open | Instant, no third party | Always-visible notification, battery cost, must be started from a visible state or an exemption (section 3) |
| C. Self-hosted ntfy / UnifiedPush | The Core publishes to a push server the owner runs on the PC or tailnet; the ntfy app (a UnifiedPush distributor) delivers to the phone | No Google, no cloud we own. ntfy docs say self-hosted servers use its instant-delivery path (a foreground service), not FCM: [ntfy Android](https://docs.ntfy.sh/subscribe/phone/). Distributor and self-host options: [UnifiedPush distributors](https://unifiedpush.org/users/distributors/) | A second app and a second service to install and keep running; the same persistent-notification trade-off; the server must be reachable from the phone (Tailscale) |
| D. FCM (owner-linked Firebase) | The Core sends via Google's servers | Most battery-friendly on stock Android; high-priority FCM is an official foreground-service exemption (section 3) | Needs a Google/Firebase account and Google as middleman; keep payloads content-free ("open JARVES"); the owner said no cloud server |

Honest take: for a phone usually on the home network or Tailscale, A plus B are enough for V7. C is the preferred upgrade when the owner wants delivery while the app is closed without Google. D stays optional and OFF. The ntfy page says the Play build uses FCM only for the public ntfy.sh host, so JARVES never uses ntfy.sh.

Notification content rule for any push path: only a short non-sensitive prompt ("JARVES needs an approval"); details are fetched from the Core after the app opens over the authenticated link.

## 5. Permissions per skill (phone side)

| Capability | Android permission or access | When asked | Notes |
| --- | --- | --- | --- |
| Voice (push-to-talk) | `RECORD_AUDIO`, `FOREGROUND_SERVICE_MICROPHONE` | First press of the talk button | Service started only from a visible activity or notification action |
| Notifications and approvals | `POST_NOTIFICATIONS` | At first pairing, explained | Approve/skip actions only for risk levels the V5 policy allows; typed-code approvals stay on the PC |
| Reminders and timers (offline) | exact-alarm access | When the first reminder is created | Used only for user-created reminders |
| Notes (offline) | none | - | App-private storage |
| Share to JARVES | none (share intent) | - | Content goes to the Core as task input, treated as untrusted text |
| Camera, contacts, calendar, SMS, location | **not requested in V7** | - | Each needs its own ADR and owner approval |
| Network | `INTERNET`, local network access | - | Only to the paired Core address |

## 6. Security

- **Pairing:** reuse the existing QR pairing and per-phone tokens; no second auth path (slice A1 adds endpoints, not authentication). Token storage on the phone uses Android Keystore-backed storage (**verify at A2**).
- **Scopes:** the phone gets only the scopes it needs (status, tasks, approvals, reminders, voice); settings, deletes, secrets and risky approvals stay PC-only, as in V5.
- **Transport:** HTTPS over Tailscale certificates or the existing self-signed certificate pinned to the paired certificate; plain HTTP never carries voice or tokens.
- **Lost phone:** one switch revokes all phones (`jarvis phone revoke-all`); the app wipes its local store when it sees a revoke.
- **Replay and rate limits:** nonces and the existing rate limits apply to the companion API; contract tests cover auth, replay and revoked tokens (A1).
- **Prompt injection:** shared text, notification text and notes are untrusted input to the Core, like any other channel.
- **Review gate:** Opus review for A1 (pairing/approvals) and A3 (mic permission), as in the work package.

## 7. Alternatives considered

| Alternative | Why not now |
| --- | --- |
| PWA only | Cannot reliably deliver notification actions with the app closed, exact alarms or a notification-started mic; kept as the first client and fallback |
| Native app only, drop the PWA | Throws away the working V5 client and slows the first phone test |
| Cross-platform (Flutter, React Native) | Extra CI toolchain for the one platform the owner uses; Kotlin/Compose gives direct access to the foreground-service and alarm APIs above |
| Full on-phone Core (offline assistant) | A second brain with its own memory and approvals; rejected by the one-brain rule |
| Hosted relay/cloud server | Rejected by the owner; cost, privacy and extra attack surface |
| Claim the Android assistant role | Allows some background-mic exemptions but replaces the system assistant and widens review scope; separate ADR later |
| Always-on wake word on the phone | Blocked in practice by the microphone background limits in section 3 |

## 8. Consequences

- A1 to A7 (issues #389 to #395) follow this order; A2+ need the dedicated test phone and the V5 acceptance (#193).
- The PC must be on and reachable for anything beyond the offline basics; the app says so plainly.
- Push is "good enough, not instant" until the owner chooses option C or D. Owner decision, default: only A and B.
- No new cloud account, cost or Google dependency is introduced by default.
- Open owner questions (safe default in brackets): enable self-hosted ntfy later (no); link Firebase (no); claim the assistant role (no).
