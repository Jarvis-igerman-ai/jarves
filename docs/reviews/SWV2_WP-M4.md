# SW-V2 WP-M4: capability matrix, Calendar write, identity & voice, custom wake phrase, dashboard LAN mode

**Scope:** spec sections 5, 6.3, 6.6, 8 and 12 (LAN security); decisions D02, D05, D12, D18. Issue #17. The Pi gaps are tracked in **#18**, a real issue created for this package with `gh issue create`.
**Date:** 2026-10-19. **Model:** Claude Opus 5.5.
**Spend:** $0. There were no paid API calls. All Google calls were offline fakes, and no Google login was made.

## 1. Capability matrix (spec 5)

- `core/src/jarves/capabilities.py` holds one record per feature, for each target (`windows`, `linux_arm64_pi`):
  - status: `verified_on_device | implemented_unverified | planned | blocked | not_applicable`
  - `evidence`, `blocker`, `roadmap_issue`, `last_checked`, `permissions`, `settings`
- It reads both ways other packages declare targets: `features.register(..., targets=...)` and assigning `feature.targets` directly.
- Rules, checked by `problems()` and tested in `test_capabilities.py`:
  - every Pi gap has a blocker;
  - a status marked verified has evidence;
  - roadmap links only point to issues that exist (#17, #18, #19-#23, checked with `gh issue list`);
  - no Pi entry claims `verified_on_device`, because there is no Pi yet;
  - an undeclared feature shows "planned / not declared" and is never hidden.
- Windows statuses marked `verified_on_device` have real evidence in the HANDOFF: music playback (18 Oct), the wake word through the air (4/4), the greeting, and the chimes. Everything else is `implemented_unverified` (offline tests only) or `planned`.
- The dashboard **Features** page (`m4.js`) has these columns: feature, enabled (switch), health, settings, permissions, Windows, Pi, blocker, roadmap link. Filters: All / Enabled / Not ready on Pi / Needs attention.
  - "Needs attention" means one of: an approval request, enabled but unavailable, or a rule problem.

## 2. Google Calendar write, never delete (D05, spec 6.6, 8)

- `skills/google.py`:
  - `CalendarWriter` (shared by voice and dashboard) and `CalendarWriteSkill` with three tools:
    - `calendar_find_events` (read, untrusted output, gives handles e1, e2...);
    - `calendar_create_event`;
    - `calendar_update_event`.
  - Both write tools are `destructive` with an `impact` read-back, so the user always approves the exact text first. The read-back covers the calendar, title, Hebrew weekday and date, time, timezone, recurrence and reminders.
  - After writing, the result is read back from what Google stored.
  - Recurring events:
    - an update without `scope` is refused with "only this time or the whole series?";
    - `this` patches only that instance;
    - `series` patches the master event and keeps the series' first date (no occurrences are dropped);
    - moving a whole series to another day is refused.
- The switch `calendar_write` is **off by default** (risk high). The scope `calendar.events` is requested only by `jarvis google login --calendar-write`, and only while the switch is on. A normal login never asks for it.
- **No delete path anywhere:**
  - `calendar_guard` runs on every `GoogleApi.request`, before a token is fetched. It refuses DELETE on any Calendar URL, `events.move`, `calendars.clear`, and any write with `status: cancelled`.
  - `guardrails.check_tool` is a hook in `SkillRegistry.register`, so no Skill (generated ones included) can register a delete-like calendar tool.
  - The dashboard has no delete route or button. `op: "delete"` returns 400.
  - Note: Google has no "edit but not delete" scope (`calendar.events` = "View and edit events on all your calendars", checked 2026-10-19), so this rule is enforced in JARVES itself.
- The dashboard **Calendar** page shows the OAuth status, calendars, the next 7 days, and an add form with preview → "Yes, save" (commit). Each event has a Change button; for a recurring event you must pick the scope.

## 3. Identity & voice (D12, D18, spec 6.3)

- `identity.py` stores everything in `identity.json` and applies it over config.toml at Core start. Display name, spoken name and wake phrase are kept separate.
- Rename by voice is `assistant_rename` (destructive, read-back). The read-back says the wake phrase stays the same.
- Voice catalog:
  - 22 GPT-Live voices plus 10 gpt-realtime voices (official list checked 2026-10-19; marin and cedar are recommended);
  - provider, model, accent, $/min (estimate for realtime);
  - preview is honestly "not available".
- Cost rules:
  - same voice model: allowed from the dashboard or by speech (`set_voice`);
  - another model: the dashboard needs `approve_cost` (HTTP 409 with the price difference otherwise); by speech it is refused.
- If a stored voice is unsupported, it falls back to vesper or cedar with a warning.
- Dashboard page: **Identity & voice**.

## 4. Custom wake phrase (user decision: not left blocked)

- Research (2026-10-19): openWakeWord's own training notebook is not feasible here.
  - It generates around 100k clips with Piper, which is English-only.
  - It downloads tens of GB of negative features and room impulse responses.
  - It needs PyTorch and a GPU or Colab. That doesn't fit this PC (no GPU, tight RAM), a Pi, or Hebrew.
- What works instead: keep openWakeWord's frozen front end (melspectrogram + speech embedding, already bundled in pyopen-wakeword) and train only a small classifier head on top, as openWakeWord custom models do.
  - The head is plain numpy (`.npz`), so it runs on Windows and the Pi with no new dependency.
  - The detector is `voice/wakeword.py` `HeadWakeDetector`; `make_detector` loads `.npz`, `.tflite` or a built-in model.
- Pipeline `voice/wake_train.py`, command `jarvis wake train "<phrase>" --say "<Hebrew spelling>" [--samples DIR] [--apply]`:
  - local TTS voices (Windows OneCore David, Zira, Mark and Hebrew Asaf; espeak-ng on Linux/Pi);
  - augmentation (speed, gain, reverb, noise);
  - confusable and everyday Hebrew/English negatives;
  - one voice held out for testing;
  - the threshold is picked for no more than 0.5 synthetic false wakes per hour;
  - outputs a report `.json` next to the model;
  - free, nothing played, nothing sent.
- **Real run on this PC** ("hey jarves" / "היי ג'ארוויס"):
  - 435 s;
  - the held-out voice (David) was heard 24/24 times;
  - 0 false wakes in 20 min of held-out synthetic speech/noise;
  - threshold 0.9.
- A streaming check through the runtime detector gave:

  | Phrase | Trained head | Built-in model |
  |---|---|---|
  | Asaf "היי ג'ארוויס" | 1.00 | 0.37 |
  | Hebrew/English sentences | 0.00 | 0.00 |
  | "jarvis" alone | 0.10 | 0.96 |

- The first run had a real false wake: Asaf saying "מה השעה עכשיו" scored 0.72. Fixed by rendering negatives with as many voice variants as the phrase, plus more Hebrew sentences.
- Calibration:
  - `jarvis wake test --model <file>` checks your voice; `--apply` saves the threshold for that model;
  - `jarvis wake test --model <file> --false-wakes 30` counts false wakes over N minutes of real room audio.
- The dashboard Identity page lists trained phrases with their synthetic check and a "Use" button, limited to `<state_dir>/wake`.
- Status: Windows `implemented_unverified`. The blocker: never tried with the owner's voice and mic. Pi `implemented_unverified` (#18).
- With only 4 local voices (1 Hebrew), recordings of the owner (`--samples`) are the recommended next step.

## 5. Dashboard LAN mode (D02, spec 12)

- `dashboard_auth.py` (`Access`) handles access; `dashboard.py` calls it in a small gate. Loopback stays the default and works as before.
- `jarvis dashboard --lan` needs the switch `dashboard_lan` (default off, high risk, cannot be switched from a phone).
  - It binds 0.0.0.0 on port 8766 but answers only private, loopback, link-local or ULA client addresses. A port-forward from the internet is refused.
- Host must be loopback or the LAN IP (rebinding and spoofed Host are refused). On writes, `Origin` must be exactly `http://<Host>` and `Sec-Fetch-Site` must not be cross-site.
- Pairing:
  - an 8-digit code, 5 minutes, single use, made on the computer only;
  - 5 wrong tries per address and 20 in total burn the code;
  - the device secret is stored only as SHA-256;
  - cookie: HttpOnly, SameSite=Strict, expires after 30 days idle.
- Device writes need `X-Jarves` plus a per-device `X-CSRF-Token`.
- Devices cannot pair others, list or revoke other devices, read the audit log, or switch LAN mode. A phone can un-pair itself.
- Rate limit: 240 requests per minute per address (429).
- Audit log `dashboard_audit.jsonl` (size-bounded): pairing, refusals, revocation, every write. No bodies or secrets.
- Pages: **Devices** (pair a phone, list and revoke, audit) and `/pair`. The phone layout makes the matrix one card per feature, with finger-sized buttons.
- Known limit: plain HTTP on the home LAN (no TLS). See the questions.

## Screenshots

Automatic headless Edge captures, not a hands-on check (`core/tools/m4_dashboard_screens.py`), in `docs/reviews/swv2_m4/`:
- Features: light, dark "not ready on Pi", phone;
- Identity;
- Devices;
- Calendar (not connected).

## Tests

- New tests: `test_capabilities.py`, `test_calendar_write.py`, `test_identity.py`, `test_wake_train.py`, `test_dashboard_lan.py`.
- Changed: `test_security.py` renames its sample tool `wipe_calendar` to `wipe_notes`, because the new guardrail now refuses calendar-delete tools.
- Full suite after merging swv2-integrate (61a3b0e): **862 passed, 3 skipped** (`python -m pytest tests -q`, run from `core/` with the main venv and `PYTHONPATH=src`).

## Integration hooks (files owned by others; smallest changes)

- `skills/__init__.py`: `register()` calls `guardrails.check_tool`.
- `app.py`:
  - `Identity(...).apply_to_config` after the features;
  - registers the identity Skill;
  - Live voice comes from `identity.voice()` (recovers unsupported voices);
  - the wake health text uses `voice.wake_phrase`.
- `config.py`: `voice.wake_phrase`.
- `cli.py`: `wake train`, `wake test --model / --false-wakes`, `dashboard --lan`.
- `features.py`: `register(targets=, permissions=, settings=)`, matrix fields in `status()`, three new switches.
- `dashboard.py`: access gate, `/pair`, audit of writes, `lan` argument. WP-H's changes are kept.
- `dashboard_static/app.js`: CSRF header. `index.html`: m4.css/m4.js plus nav links. m4.js registers in WP-H's `PAGES`.

## Questions for the user

1. **LAN over plain HTTP:** is that acceptable on your home Wi-Fi, or should JARVES add a self-signed HTTPS certificate? With HTTPS, the phone shows a one-time warning that you would have to accept. Recommendation: HTTP for now, HTTPS before the Pi lives in the house. USER DECISION REQUIRED.
2. **Custom wake phrase:** do you want to record about 20 samples of yourself saying the phrase, for `--samples`? Training on local voices alone (1 Hebrew voice) is unproven for your voice. Recommendation: yes; then run `jarvis wake test --model ... --false-wakes 30`. USER DECISION REQUIRED.
3. **Calendar write:** turn it on (`jarvis features on calendar_write`, then `jarvis google login --calendar-write`) when you want to try it. The first real create and update stay UNVERIFIED until then.
