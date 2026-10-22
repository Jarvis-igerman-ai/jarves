# SW-V5 manual acceptance: Android phone (V5-H)

Owner steps, one line each. Phone and PC on the same Wi-Fi. Mark each PASS/FAIL.

1. On the PC run `jarvis features on mobile_access`, then `jarvis dashboard --mobile --tls-selfsigned`; the dashboard Phone page shows a QR code and a 6-digit code.
2. If Windows Firewall asks, allow JARVES on Private networks only; confirm the Phone page shows the phone address (https://<PC-IP>:<port>).
3. On the phone open the Camera app, scan the QR code and open the link in Chrome.
4. Chrome warns the certificate is not trusted: choose Advanced, then Proceed (self-signed certificate; expected, the connection is still encrypted).
5. Pairing screen: the code is pre-filled from the QR; give the phone a name and tap Pair; the status pill shows Online.
6. Reusing the same QR/code on a second device must fail (single use), and a wrong code must fail.
7. Chat tab: send "hello"; the answer arrives; the message also appears in the PC typed-chat history.
8. Turn Wi-Fi off for 5 seconds right after sending a message, turn it on; the answer arrives once and the PC ran the message once (client message id resend).
9. Voice tab: the note says voice is ready (needs https); hold the push-to-talk button, say "what time is it", release; the transcript and the spoken-style answer appear.
10. Allow the microphone in Chrome when asked; if denied, the Voice tab explains how to re-enable it.
11. On the PC dashboard Phone page enable "approve from phone"; trigger an approval on the PC (for example ask JARVES to send a test message).
12. On the phone Approvals tab the request appears within 5 seconds; tap Deny, repeat, tap Approve; the PC acts accordingly.
13. A typed-code approval (high risk) must NOT be approvable from the phone.
14. Disable "approve from phone" on the PC; the phone Approvals tab shows an empty/disabled state.
15. Optional: tap Chrome menu, Add to Home screen; the PWA opens full screen in English and in Hebrew (RTL).
16. On the PC Phone page press Revoke on the phone; the phone's next request returns to the pairing screen at once.
17. Switch `mobile_access` OFF (dashboard or `jarvis features off mobile_access`); the phone address stops answering immediately.
18. Check the audit list on the Phone page: pairing, requests and revoke are listed with no message text and no tokens.

## Emulated-phone run (automated, `core/tests/test_mobile_phone_e2e.py`)

Headless Chromium/Edge as a Pixel-class phone (390x844, touch, mobile UA, fake microphone), real dashboard server, fake Core. Mark: **EMU-PASS** = proven in the emulated phone; **REAL** = still needs a real phone.

| Step | Emulated | Real phone still needed |
|---|---|---|
| 1-2 | EMU-PASS (code + QR payload; QR decoded back to the URL with OpenCV) | Windows Firewall prompt, PC IP shown |
| 3-4 | EMU-PASS (open link, self-signed https secure context) | Camera app scan, Chrome certificate warning wording |
| 5-6 | EMU-PASS (pair by QR link and by typed code; second device and wrong code refused) | - |
| 7-8 | EMU-PASS (chat; lost answer then resend with the same id runs ONCE; replay flagged) | real Wi-Fi off/on |
| 9 | EMU-PASS with TLS (hold-to-talk, 16 kHz mono WAV reaches the Core); refused (disabled + HTTP 409) without TLS | real microphone and speech recognition quality |
| 10 | EMU-PASS (denied microphone explained) | Chrome's real permission dialog |
| 11-14 | EMU-PASS (list, Deny, Approve, typed-code refused 403 TYPED_PC_ONLY, switch off = disabled) | the PC acting on a real tool approval |
| 15 | EMU-PASS (manifest, service worker, offline shell, Chromium installability except secure-origin; EN + HE, light + dark, 390 px: no sideways scroll, tap targets >= 44 px, contrast) | Add to Home screen, trusted-cert install check, real Hebrew keyboard |
| 16 | EMU-PASS (revoke effective on the very next request, app returns to pairing) | - |
| 17 | EMU-PASS (`/m/` and every `/api/m/*` return 404; no new code) | - |
| 18 | EMU-PASS (audit holds no message text, tokens or codes) | - |
| rate limit | EMU-PASS (429 + Retry-After, app says to slow down) | - |

Bugs found and fixed by this run: approval count badge contrast 2.99:1 (now >= 4.5), language button tap target 36 px (now 44 px), language button label always said "Language" (now names the language it switches to), light-theme ok/bad/accent colours under 4.5:1.
