# 5.0 phone sheet, visual re-run (25 Oct 2026, issue #193)

Real Android phone (Chrome, Hebrew UI, 1080x2412) over adb screenshots/taps only. JARVES 4.0.1.722 standalone dashboard (`dashboard --mobile --tls-selfsigned`, no Core) on the PC. Phone left unlocked-state untouched, no PIN/settings, no messages, calls or purchases. `mobile_access` was turned on for the run and switched off again afterwards.

Result: PASS 14 / FAIL 0 / NEEDS-OWNER 9 / NOT-RUN 4 (earlier API-level run: PASS 9).

New PASS (visual, real phone):
- Phone reaches `https://<PC-IP>:8443/m/` over Wi-Fi; Chrome shows the untrusted-certificate page (Hebrew, ERR_CERT_AUTHORITY_INVALID) as expected.
- Pairing screen renders in Hebrew RTL, code pre-filled from the link, name field, Pair button.
- Pairing succeeds; status pill shows connected; Home page renders.
- Tabs Home, Chat, Voice, Approvals all render; Chat without a Core says chat is not active here; Voice explains it needs a secure connection and shows the hold-to-talk button; Approvals shows the "phone approvals off" state and "nothing waiting".
- EN/HE switch works (LTR English, RTL Hebrew).
- Revoke on the PC: the phone's next tap returns to the pairing screen at once.
- Re-using the used code: "Wrong or expired code. Make a new one on the computer."
- Audit list holds pairing and mode events with no message text or tokens.

Still NEEDS-OWNER: camera QR scan, Chrome certificate decision (the run proceeded past it only to reach the page), microphone permission and real voice, Wi-Fi off/on resend, Add to Home screen, mic strip when locked/backgrounded, push while locked, calls.

NOT-RUN (needs a running Core, not a standalone dashboard): chat answer round trip, real approval Allow/Deny on a tool, approve-from-phone on the PC, voice transcript.

Observations (cosmetic, no issue filed): in English the last tab ("Approvals") is clipped at the right edge of the scrollable tab row at first view; the Voice tab says it needs a secure connection while Chrome is on a certificate-warning page (expected, secure context is not granted until the certificate is trusted).
