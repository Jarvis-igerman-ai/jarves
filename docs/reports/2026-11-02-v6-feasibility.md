# V6 feasibility record: Android Auto, phone, Bluetooth (2 Nov 2026)

Task V6-FEASIBILITY (issue #208). Docs only, no code. Not legal advice.
Base research: `docs/archive/plans/V6_ANDROID_AUTO_FEASIBILITY_2026-10-25.md` (sources fetched 25 Oct 2026).
Re-checked 2 Nov 2026: https://developer.android.com/training/cars/apps lists only navigation, point of
interest, IoT and weather for the Car App Library, plus templated media, communication (messaging/calling)
and parked apps outside it. No assistant/conversational/AI category appears. Nothing was read about Android 15/16
category changes (not on that page): UNVERIFIED.

Legend: official = a documented, supported path for a third-party app; phone-mediated = works only through
the phone (PWA/app on a mount, Bluetooth audio), no head-unit integration; blocked = no supported path today.

| Capability | Class | Source (accessed) | Notes |
|---|---|---|---|
| Native JARVES app on the head unit (Car App Library) | blocked | developer.android.com/training/cars/apps (2 Nov 2026) | No assistant category; Play listing requires a supported category. |
| Android Auto messaging/calling surface for JARVES | blocked | same page | Notification-style communication apps only, not a free voice chat. Existing apps (Telegram/WhatsApp) show their own notifications; not owned by JARVES. |
| Parked-mode JARVES screen | blocked for driving | same page; 25 Oct note | Parked apps stop on motion; unusable while driving. |
| Car microphone via CarAudioRecord | blocked | developer.android.com/training/cars/apps/library/car-microphone (25 Oct) | Needs an allowed app category; review outcome UNVERIFIED. |
| Phone on a mount, JARVES PWA, screen on | phone-mediated | android docs: fgs/restrictions-bg-start (25 Oct) | Device behaviour NOT-RUN. |
| Two-way voice over car Bluetooth (HFP/SCO or BLE audio) | phone-mediated | developer.android.com/develop/connectivity/bluetooth/ble-audio/audio-manager (25 Oct) | setCommunicationDevice route; A2DP alone has no mic. Head unit differences need a parked-car test (owner gated). |
| Audio focus (speech, transient) and call interruption | official (platform API) | developer.android.com/media/optimize/audio-focus (25 Oct) | Applies to the phone app. |
| Push-to-talk fallback when the route drops | phone-mediated | project design | Matches V6 drive-mode policy. |
| Hands-free with locked screen | blocked | restrictions-bg-start (25 Oct) | Browser PWA loses mic when locked: UNVERIFIED, needs a device test. |
| Steering-wheel button | blocked (V7) | none | No supported path found. |
| Legal use while driving (Israel, travel countries) | UNVERIFIED | none | Owner/counsel must confirm; guide carries a safety notice, no legal claim. |
| Driver-distraction numeric limits (NHTSA 2 s / 12 s) | UNVERIFIED | NHTSA page returned 403 on 25 Oct | Treat as guidance only. |

## Consequence
V6 stays phone-mediated: voice-only, short, cancellable, no vehicle control, cloud-audio indicator, push-to-talk
fallback. All device rows remain NOT-RUN until one owner-approved parked-car session (HFP routing, mic with
screen on, incoming-call behaviour). Re-check the Auto category list before V7.

## Open owner items (defaults stay, none blocks this record)
1. Parked-car session (physical test: owner gate).
2. Destination-country rules on voice use while driving.
