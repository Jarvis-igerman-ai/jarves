# V5 phone + desktop scenario: what is real and what is a fake (task V5-SCENARIO, #187)

Test: `core/tests/test_v5_scenarios.py` (6 tests). Verified automated at code revision `8b7798be`
(main after PR #808): `PYTHONPATH=core/src pytest core/tests/test_v5_scenarios.py` -> 6 passed.
Level: automated, in-process. This is NOT an installed-app or real-phone check.

Covered (acceptance C1): memory correction seen by the phone Brain turn and the desktop view; a proposed
memory unused until approved; failed goal plan retried and done on both clients; proactive dedupe and snooze;
quiet hours hold signals back until morning.

Fakes (acceptance C2):
- Phone: emulated HTTP client over loopback against the real phone API (really paired device, scopes). No real Android device.
- Brain/chat: a stub `chat` callback that records the preferences block; no model call, no paid API.
- Voice and status bridge: stubs (`voice` returns `{}`, fixed status); `desktop_present` is False.
- Skills in the goal-plan test: fake Skills (`fake_read`, `flaky_archive`); no real file or integration is touched.
- Clock: injected `Clock` (noon, 23:30, 08:00 UTC on 22-23 Oct 2026); no real time.
- Network: loopback only; no external calls. Push notifications to a real phone are not exercised.

Still UNVERIFIED: the same scenario on the installed app with a real phone (device evidence).
