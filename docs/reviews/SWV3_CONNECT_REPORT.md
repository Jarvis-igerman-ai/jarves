# SW-V3 `jarvis connect`: guided account setup (20 Oct 2026)

Issue [#17](https://github.com/igalgerman/JARVIS/issues/17), branch `v3-connect` (off `origin/v3-final`).
Owner request: setup of external accounts must be as automatic as possible; "the command should guide me".

## What `jarvis connect` does

One command checks Google (Calendar + Gmail), Telegram, the Pear Desktop API plugin and the wake word, plus
Instagram and Home Assistant as optional accounts. It walks the owner through only what is missing and ends with a
summary table (connected / skipped / needs attention / failed, with the next step per row).
Flags: `--only google|telegram|pear|wake|instagram|home`, `--status` (changes nothing; `--json` for the dashboard
shape), `--yes` (accept default answers, skip the optional accounts and the wake calibration),
`--reminders on|off`. Exit code: 0 unless a step really failed (a credential was rejected or a wizard errored); 130 on
Ctrl+C with progress kept. Something the owner still has to do outside (start Pear, press Start in the bot) is
"needs attention", not a failure.

Existing wizards are reused, not rewritten:

| Account | Reused | What `connect` automates around it |
|---|---|---|
| Google | `google_setup.Wizard` (`jarvis google start`) | opens the Cloud pages, clipboard values, picks the `client_secret*.json` from Downloads, sign-in, live self-check (all already in the wizard); `connect` passes its browser / clipboard / Downloads |
| Telegram | `telegram_channel.Wizard` (`jarvis telegram start`), now with optional hooks | opens t.me/BotFather, puts `/newbot` on the clipboard, watches the clipboard for `\d+:[A-Za-z0-9_-]{30,}` (asks consent first, clears the clipboard after use, only if it still holds the token), validates with getMe BEFORE saving, opens `t.me/<bot>?start=<one-time code>`, long-polls getUpdates until the owner taps Start, pairs only the chat that sent the code, sends a confirmation message |
| Pear | `PearDesktopPlayer.pair` (`jarvis music pair`) | probes 127.0.0.1:26538, prints the exact plugin steps in Hebrew and English (Plugins > API Server, host, port, `jarvis music open`), waits for Pear to appear, triggers pairing and tells the owner to press Allow |
| Wake word | `jarvis wake test --apply` | detects an existing calibration / trained phrase / 3+ recordings in `<state>/wake/samples`; offers the calibration run |
| Instagram, Home Assistant | their wizards | optional, skipped by `--yes`, hidden input for the secrets (already in those wizards) |

Telegram security detail: pairing uses a one-time code in the deep link instead of "the first message wins", so a
stranger who finds the bot username cannot get paired. Non-matching updates are acknowledged (offset) so a stranger's
message is not served again in a loop. Typing a token (hidden, getpass) is only the fallback when the clipboard watch is
declined or finds nothing.

## Other parts of the request

1. Daily reminder: `connect.maybe_nudge`, called from the scheduler tick (checked every 15 min). Text "לא חיברת עדיין
   גוגל, להריץ jarvis connect?" (or the reconnect variant). At most once per 24 h; never in quiet hours (22:00-07:00),
   while paused, when proactive alerts are off, when autonomy is `observe_only`, when `guided_setup` is off, or after
   `jarvis connect --reminders off`. Delivered through the alert engine: voice (speech channel, falls back to the
   popup). New settings `connect.nudge`, `connect.last_nudge`.
2. Dashboard Connections page: a "Run guided setup" card with the same checklist as `--status`
   (`GET /api/connect`) and a button (`POST /api/connect/run`) that opens `jarvis connect` in its own console window on
   Windows; elsewhere it shows the command. Loopback owner only (not on the paired-phone allow-list).
3. First-run wizard: new last step `connect` offers `jarvis connect` (interactive only; the summary points to it
   otherwise).
4. Feature `guided_setup`: on by default, low risk, not in `SAFE_OFF`. Guide `guided_setup.toml`, capabilities row
   (Windows / Pi targets), wizard group `core`, bundle check, Hebrew texts (`hebrew.py` TEXT), dashboard locale keys.

## Tests

`core/tests/test_connect.py` (32 tests, no skips, no sleeps on a clock): real HTTP fake Telegram (new long-poll
`getUpdates` with offset acknowledgement, `polled` / `wake` events and `deliver()` in `channels_fakes.py`), fake Pear
API Server, fake Instagram Graph API, the existing fake Google + fake browser + tmp Downloads from
`test_google_setup.py`, and `FakeClipboard` (implements `connect.Clipboard`).
Waits are events: the fake browser sets a `threading.Event` when the t.me link opens, the test then delivers the
message while the wizard is blocked in the long poll.

Passed one file at a time: test_connect 32, test_hebrew_texts 17, test_guides 24, test_setup_wizard 21,
test_channels 27, test_capabilities + test_features + test_m3_notes_alerts 42, test_dashboard + test_google_setup +
test_instagram 55, test_dashboard_locales 17, test_launcher_help + test_packaging + test_selfcheck (48 together with the
locale file before the key fix), test_m3_scheduler + test_m3_core 61. The full suite was NOT run (low RAM).
Real-machine smoke: `python -m jarves connect --status` on this PC printed the table and changed nothing; the Windows
clipboard read through user32 returned text.

## UNVERIFIED (needs the owner's real accounts or a person at the keyboard)

- Real BotFather flow: the clipboard watch and the t.me deep link against the real Telegram app (the fake is the Bot API
  only; `?start=<code>` delivering `/start <code>` is documented Telegram behaviour, not exercised live).
- Real Google run through `connect` (the wrapped wizard was verified live on 19 Oct; `connect` only injects its
  helpers). Calendar/Gmail validation after `connect` is the wizard's own self-check.
- Real Pear: the exact menu wording "Plugins > API Server" follows the existing guide; the Allow dialog is not
  automated (a click is the authorization).
- `SystemClipboard.write` / `.clear` and the non-Windows clipboard tools (pbpaste / wl-paste / xclip) are not
  exercised by tests (they would overwrite a developer's clipboard); only `read` is smoke-tested.
- The dashboard button launching a console window (Windows `CREATE_NEW_CONSOLE`) and the page rendering in a browser
  were not run; the API and the shipped JS strings are tested.
- Wake calibration through `connect` runs `python -m jarves wake test --apply` in a child process; needs the mic and the
  owner's voice. The "wake samples" check counts a saved calibration, a trained `.npz`, or 3+ WAVs in `wake/samples`
  (no recorder command exists for that folder yet).
- Pi: nothing run.
