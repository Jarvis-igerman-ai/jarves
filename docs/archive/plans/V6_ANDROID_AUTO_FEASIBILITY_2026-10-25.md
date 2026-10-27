# V6 Android Auto / in-car voice feasibility (25 Oct 2026)

Research note for gap G1 of [V6 plan](V6_INVENTORY_AND_PLAN_2026-10-25.md). **Not legal advice.** Not a product decision, no code. Sources fetched 2026-10-25 (WebFetch/WebSearch summaries; a few pages returned 404/403 and are marked). Anything not read directly is **UNVERIFIED**.

## 1. Android Auto rules for third-party apps

- Supported app types on Android Auto: media, messaging (notification-based), templated (Car App Library), parked (Android 15+). Phone must be Android 9+; real-car installs must come from a trusted store (Google Play / ONE store). Source: https://developer.android.com/training/cars/platforms/android-auto (accessed 2026-10-25)
- Car App Library categories: navigation, point of interest, IoT, weather. An app "must belong to one of the supported categories" for Play listing. **No conversational/assistant category is listed.** Source: https://developer.android.com/training/cars/apps (accessed 2026-10-25)
- Messaging apps get only notification + reply surfaces; they are not a free-form voice-chat UI. (Inferred from the declaration table; the messaging guide itself was not read: UNVERIFIED.)
- Parked apps run full screen, exit when vehicle motion is detected, and cannot accept runtime permission dialogs on the head unit. Intents launching other apps are generally blocked. Same page as above.
- Car microphone: `CarAudioRecord` exists in the Car App Library, needs `RECORD_AUDIO` granted on the phone, audio focus (`AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE`, speech) before recording, and stop on focus loss. The page does not say which categories may use it; whether a non-listed category (AI assistant) would pass Play review is UNVERIFIED and likely no. Source: https://developer.android.com/training/cars/apps/library/car-microphone (accessed 2026-10-25)
- Conclusion: a native in-car JARVES app is probably not publishable under current categories. Fits plan G13 (V7, blocked). V6 stays phone-mediated.

## 2. Driver-distraction guidance

- Google: templates limit on-screen info, keep task flows short, restrict step-adding updates while driving, prefer pre-selected options and voice input. No numeric glance/step limits on the page read. Source: https://developer.android.com/design/ui/cars/guides/foundations/define-user-tasks (accessed 2026-10-25)
- Play quality review for Auto apps exists (test in the Desktop Head Unit or alpha/beta before submitting); exact current criteria page was not retrieved (the `/training/cars/driver-distraction` and `/platforms/android-auto/design` URLs returned 404): UNVERIFIED. Weak secondary: https://iut-fbleau.fr/docs/android/distribute/essentials/quality/auto.html
- NHTSA visual-manual guidelines (commonly cited: glances 2 s, 12 s total task time): page fetch got 403, figures from general knowledge: UNVERIFIED. https://www.nhtsa.gov/road-safety/distracted-driving
- Design implication for V6: voice-only, short answers, no screen approvals while driving, unsafe steps deferred (matches plan G2).

## 3. Phone on a mount + car Bluetooth (what is feasible)

- This path is not Android Auto: the phone is just a phone, so Android Auto category rules do not apply, but ordinary Android rules and local law do. Google Play policy on the app itself still applies if ever distributed there.
- Routing: from Android 13, apps should use `AudioManager.setCommunicationDevice()` / `clearCommunicationDevice()` instead of `startBluetoothSco()`; wait up to 30 s for confirmation; listen with `registerAudioDeviceCallback`; clear the device after the session. Source: https://developer.android.com/develop/connectivity/bluetooth/ble-audio/audio-manager (accessed 2026-10-25)
- A2DP is one-way stereo (no mic); two-way voice needs the HFP/SCO (or BLE audio) link, which lowers quality (narrowband/wideband voice). Page does not compare them; general knowledge: UNVERIFIED against a primary doc. Car head units differ; real behaviour needs a parked-car test (plan G11, owner-gated).
- Audio focus: request before speaking; use speech content type (not ducked on Android 8+); transient focus for short replies; stop/pause on loss; on Android 15+ focus requests fail unless the app is top or runs a foreground service; incoming calls take focus. Source: https://developer.android.com/media/optimize/audio-focus (accessed 2026-10-25)
- Background mic: since Android 14 a microphone foreground service cannot be created from the background; it must start while an activity is visible (or via notification tap/widget). With the screen locked, a browser PWA generally loses the mic: UNVERIFIED (not found in a primary doc; needs a device test). Source: https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start (accessed 2026-10-25)
- Practical V6 default (matches plan): PWA with screen on and phone mounted, Bluetooth audio, push-to-talk until hands-free is switched on by the owner, visible cloud-audio indicator, fall back to push-to-talk when the route drops.

## 4. Legal notes (high level, not legal advice)

- Many jurisdictions restrict hand-held phone use and screen interaction while driving; some allow voice-only/hands-free use, some restrict even that. Israel and the owner's travel destinations: not checked, UNVERIFIED. Owner or counsel must confirm before any public claim.
- Product stance to keep: voice-only, short, cancellable ("stop"/"עצור"), no vehicle control, no reading mail/message content aloud by default, no promise that use is legal anywhere, user remains responsible; show a driving-safety notice in the guide.
- Recording: car microphone captures passengers; cloud voice processing needs a clear indicator and passenger-privacy defaults (plan P5). Consent law for recording others varies: UNVERIFIED.

## 5. Android Auto matrix (honest)

| Path | Status |
|---|---|
| Official in-car JARVES app (Car App Library / assistant) | blocked under current categories (UNVERIFIED for future categories) |
| Android Auto messaging notifications from existing apps (Telegram/WhatsApp) | possible, owned by those apps, not by JARVES |
| Phone on mount + Bluetooth audio (PWA) | phone-mediated, V6 target, device evidence NOT-RUN |
| Steering-wheel button, locked-screen mic | blocked / V7 |

## 6. Open items for the owner (defaults stay)

1. One parked-car session to verify HFP routing, mic with screen on, incoming-call behaviour (default: NOT-RUN, beta only).
2. Confirm destination-country rules on voice use while driving (default: guide carries a safety notice, no legal claim).
3. Re-check the Auto category list before V7 (Google may add assistant categories).
