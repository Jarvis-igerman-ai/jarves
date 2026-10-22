# SWV4 P01: owner-assisted verification (20 Oct 2026)

> **Migration note (20 Oct 2026 audit).** Ported from legacy private branch `v4-p01-verify` (head `67c85f7`), which
> was never merged into `v4-final` or `main`. Only the configured city name was replaced by a placeholder; the report
> already held counts only. Bug B1's question was answered by the owner the same day: keep those six features ON in
> the real profile (see `docs/plans/PRIORITIES.md`, "Owner answers, late evening"); sends still need exact confirmation.

Branch `v4-p01-verify` off `origin/v3-final`. Read-only and safe: Pear volume stayed 0 (watchdog: max 0, 0 violations), Google
checks ran on a COPY of the profile (tokens copied to a temp home; the real profile was only read), no mail sent, no calendar
event created/changed/deleted. No personal content is recorded here (counts only).

## Results

| Row | What | Result | Evidence |
|---|---|---|---|
| S01-S09, S11-S13 | `suite_music.py --groups skill` on the real Pear | PASS (12 of 13) | confirm 1.6-2.7 s per play; S11 10/10 median 2.1 s; S12 honest MUSIC_NOT_FOUND 0.6 s; S13 5/5 graceful; max volume seen 0 |
| S10 | rapid play, next, pause, resume, next (no waits) | FAIL (honest) | `music_pause` right after `music_next` was not confirmed within 4 s: Pear kept reporting "playing"; the tool said so and told the user to restart Pear. State stayed in sync, no false success. P3 (Pear needs a moment after a track change; 1 of 1 run) |
| V01-V10 | voice rows (real GPT-Live, synthetic Hebrew speech, virtual mic/speaker) | PASS 9 of 10, V02 re-run PASS | dead air 0.67-1.47 s; V02 first run: "עצור" transcribed as empty and the model answered "stopped" without calling `music_pause` (P3 flake/hallucination on synthetic audio; re-run V01+V02 passed: Pear paused in 2.83 s) |
| K01 | 5-min soak at volume 0 | PASS | 111 ops, 0 errors, 0 desyncs, 302.8 s (earlier 15-min soak on a 200-item queue had 38 honest failures; this fresh 5-min run was clean) |
| G1 | `jarvis google test` | PASS | `{"calendar": "ok", "gmail": "ok"}` |
| G2 | calendar via tool path (`calendar_list_events` today/tomorrow, `calendar_next_event`) | PASS | today: 1 event, tomorrow: 0, next-event ok |
| G3 | calendar via CLI (`jarves calendar find --day today/tomorrow`) | PASS | 1 event today, none tomorrow, untrusted-data note present |
| G4 | Gmail headers only (`gmail_search newer_than:2d`, `is:unread`, limit 10) | PASS | both ok; counts 10 and 10 (limit cap reached); no body read (`gmail_read` never called) |
| G5 | morning briefing with Google connected | PASS | BRIEFING_OK; sections calendar (1 event), email (0 important unread), weather (configured city; forecast values present), reminders; all ok |
| G6 | calendar delete guard | PASS | `calendar_guard` refuses DELETE, `/move`, `/clear`, PATCH `status=cancelled` (4 of 4 CALENDAR_DELETE_FORBIDDEN); no delete tool is registered |
| G7 | email sending is OFF | **FAIL** | see bug B1: `gmail_send` is ON in the owner's `features.json`, the token holds `gmail.compose`, and `gmail_send_draft` is registered. A send attempt from a model turn after reading mail is refused by the untrusted-turn guard (TOOL_UNTRUSTED_TURN), nothing was sent |
| W1 | `jarvis wake test` | BLOCKED (needs the owner's voice) | mic found (2K HD Camera), model hey_jarvis, threshold 0.15; 6 s of silence: best score 0.02, "no wake" (no false wake in the room noise, peak -5 dBFS). Detection of the real "Hey Jarvis" needs the owner to speak |
| L1 | log classification | see below | |

## Log classification

Real Core log (since 12:30 UTC) and the test-home log of the Google run, plus the voice-suite home:

| Level | Code | Count | Class |
|---|---|---|---|
| ERROR | google CALENDAR_DELETE_FORBIDDEN | 4 | expected: my 4 guard probes. Logged at ERROR for a correct refusal (B3, P3) |
| WARN | skills TOOL_UNTRUSTED_TURN | 1 | expected: the send attempt was refused after mail was read |
| WARN | link LINK_FACE_DISCONNECTED | 1 | expected: the voice-suite teardown closed the face link |
| WARN | budget BUDGET_STALE_RESERVATION | 1 (11:44 UTC, before these runs) | benign: a stopped process left a reservation (also 18 Oct, 20 Oct 04:23); self-heals |

No unexpected WARN/ERROR from these runs.

## Bugs

| ID | Sev | Finding | Status |
|---|---|---|---|
| B1 | P1 (state, not code) | The owner's `%LOCALAPPDATA%\jarves\features.json` has ON, set by `cli` on 19 Oct 19:06-19:22 UTC: `gmail_send`, `calendar_write`, `camera`, `camera_when_away`, `face_recognition`, `dashboard_lan`. `docs/HANDOFF.md` says `gmail_send` remained OFF after Google setup. The campaign tools use an isolated temp home, so the source is a manual or agent `jarvis features on` run against the real profile (no FEATURE_SET line for it in the log). With `gmail.compose` granted, JARVES can draft and, after exact confirmation, send mail. | NOT changed (owner's setting; I did not touch the real profile). Question for the owner: switch `gmail_send` (and the other five) OFF with `jarvis features off gmail_send` etc.? |
| B2 | P3 | S10: pause right after a fast `next` is not confirmed within 4 s on the real Pear (honest failure message) | open, Pear timing |
| B3 | P3 | A correct calendar-delete refusal is logged at ERROR, which inflates error counts and alerts | open |
| B4 | P3 | Briefing weather text says "ב<City>" (placeholder for the configured English city name) (English city glued to the Hebrew prefix); the city setting is English | open, cosmetic |
| B5 | P3 | Voice V02 flake: empty transcript, model claims "stopped" without a tool call; not reproduced | open, watch |

No P0-P2 code bugs found, so no code change and no new tests.
