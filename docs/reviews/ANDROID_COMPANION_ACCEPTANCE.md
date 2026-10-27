# Android companion: acceptance sheet, battery/OEM matrix and release notes (V7 A7, #395)

Status as of 28 Oct 2026: **nothing here has been run on a device.** Every row below is `NOT-RUN` until the owner (or an
agent with a real phone, never the owner's own data) fills it in. Rows for parts that are not built yet say `NOT-BUILT`;
they become `NOT-RUN` when the slice merges. A row is `PASS` or `FAIL` only with a date, the phone model, the Android
version and one line of evidence. Never write `PASS` for a row nobody ran. Owner signs the sheet: [ ] signed ____ date ____

What exists today (debug APK only, built by CI, no Play Store, no release signing): the **A2 app shell**
(`android/`, package `ai.jarves.companion`): pair with the 8-digit code over HTTPS with a pinned certificate, status
screen, "PC offline" banner, unpair. Network permission only: no microphone, no notifications, no foreground service.
Push-to-talk and notifications (A3) are in review; share intent and screens (A4), offline basics (A5/A6) are not built.
Contract: [companion_v1.md](../api/companion_v1.md). Decision record: `docs/plans/ANDROID_DAILY_COMPANION_ADR_2026-10-26.md`.

## 1. Acceptance rows

Setup for all rows: PC and phone on the same Wi-Fi (or both on Tailscale); on the PC `jarvis features on mobile_access`
and `jarvis dashboard --mobile --tls-selfsigned`; the Phone page shows an 8-digit code (valid 5 minutes, single use).

| # | Slice | Step | Expected | Result |
|---|---|---|---|---|
| 1 | A2 | Install the debug APK from the CI artifact (allow "install unknown apps" for the file manager only) | App icon "JARVES" appears; first screen asks for the PC address and code | NOT-RUN |
| 2 | A2 | Type the PC address (host:port) and the code; a screen shows the certificate fingerprint | Fingerprint equals the one on the PC Phone page; Pair is offered only after you confirm | NOT-RUN |
| 3 | A2 | Confirm: pair | Status screen shows Online and the PC name | NOT-RUN |
| 4 | A2 | Reuse the same code on a second phone; type a wrong code | Both refused (single use; wrong code); no crash | NOT-RUN |
| 5 | A2 | Stop the Core on the PC (`jarvis stop`) while the app is open | "PC offline" banner within 15 s (the app asks every 10 s while it is on screen); no crash; back to Online after `jarvis start` | NOT-RUN |
| 6 | A2 | Turn Wi-Fi off on the phone | "Unreachable" state, not "revoked"; recovers when Wi-Fi returns | NOT-RUN |
| 7 | A2 | Revoke the phone on the PC Phone page | Next poll shows "revoked" and returns to the pairing screen | NOT-RUN |
| 8 | A2 | Change the PC certificate (restart the phone server with a new self-signed one) | App shows "certificate changed" and refuses to talk until re-paired | NOT-RUN |
| 9 | A2 | Unpair in the app | Token deleted from the phone; the PC lists the phone as gone or revoked | NOT-RUN |
| 10 | A2 | Rotate the screen, switch Hebrew/English system language, dark mode | No sideways scroll, text readable, RTL correct | NOT-RUN |
| 11 | A2 | Android backup/restore (adb or a new phone from backup) | The token is NOT restored (`allowBackup=false`); pairing is asked again | NOT-RUN |
| 12 | A3 | Hold push-to-talk, ask a question, release | Transcript and answer; mic starts only from the visible screen or notification | NOT-BUILT |
| 13 | A3 | Notification when an approval is waiting | Arrives (polling first, no Firebase unless you linked it); tapping opens the approval | NOT-BUILT |
| 14 | A4 | Share a web page or text to JARVES from another app | Arrives as a task; approvals approve/skip as in V5; delete stays PC-only | NOT-BUILT |
| 15 | A5/A6 | Create a reminder and a timer on the phone with the PC off; turn the PC on | Reminder fires on time (exact alarm); syncs when the PC is reachable; conflict rule: last writer wins with tombstones | NOT-BUILT |

Honest limits (state them to the owner, do not hide): the debug APK is for testing; it is not a store release. No cloud
relay exists: the phone only works when the PC is reachable. Hands-free (always listening) is V6 and out of scope.

## 2. Battery and OEM matrix

What to learn: does polling and a notification survive the phone's power management? Fill one row per phone. Cells are
`NOT-RUN` until tested; a result is `OK`, `DELAYED (minutes)` or `KILLED`, with the setting you had to change.

Test for each phone (needs A3 for the notification timing): app in background with the screen off for 30 minutes, then trigger an approval on the PC; note how
long until the notification arrives (A3), and whether the app is still paired and the polling alive on return.

| Phone / maker (Android version) | Default battery mode | After 30 min screen off | After a night (doze) | "Unrestricted battery" setting needed? | Exact alarm allowed by default? (A6) | Notes |
|---|---|---|---|---|---|---|
| Google Pixel (stock Android) | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | |
| Samsung Galaxy (One UI: "sleeping apps") | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | |
| Xiaomi / Redmi / Poco (MIUI or HyperOS autostart) | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | |
| OnePlus / Oppo / Realme (ColorOS battery optimisation) | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | |
| Huawei / Honor (no Google services) | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | |
| The owner's test phone (87a316d4, dedicated for tests) | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | NOT-RUN | |

Today (A2) the app asks the PC only while it is on screen (every 10 s) and stops when it goes to the background, so
the 30-minute columns matter from A3 on (notifications by polling); until then they only record what the phone does to the
app. Known risks to check (from the ADR, not yet measured): some makers kill background apps without notice; polling then
stops until the app is opened; the fix is the maker's "unrestricted" or "autostart" setting, which the guide tells the
owner to set. Exact alarms (reminders, timers) need the "Alarms and reminders" permission on Android 12+ and are off by
default on Android 14 for newly installed apps that are not clock or calendar apps.

## 3. Release notes (draft for the beta page; do not publish before rows 1-11 are PASS on a real phone)

- **New (beta, off by default, debug build only):** an Android companion app shell. Pair your phone with your PC using the
  8-digit code, see whether your PC is online, and unpair at any time. The connection is encrypted and pinned to your PC's
  certificate; there is no cloud relay and no account.
- **Not in this build:** push-to-talk, notifications, sharing to JARVES, offline reminders (coming slice by slice).
- **Known limits:** works only when the PC is reachable; behaviour on phones with aggressive battery management is not yet
  measured (see the matrix above).
- **How to go back:** unpair in the app, then revoke the phone on the PC (Phone page). Nothing else was changed on the PC.

## 4. Sign-off

Owner signs only the rows he ran himself. Rows an agent ran carry the agent's name and the phone used. The stable gate
never counts a `NOT-RUN` or `NOT-BUILT` row as PASS.
