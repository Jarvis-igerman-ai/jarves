# SW-V2 WP-G: Google Calendar + Gmail Skills (Issue #17 items 12, 13)

Branch `worktree-agent-a52d8d6cb4da523a9` (based on `swv2-integrate`). No Google login, no account access, no
paid API calls: everything is tested offline against a mock Google (httpx.MockTransport) with fixture JSON written
from the API reference shapes (`core/tests/fixtures/google_api.json`, fictional people).

## What was built

| File | What |
|---|---|
| `core/src/jarves/google_auth.py` (new) | Desktop OAuth: auth code + PKCE S256, loopback `127.0.0.1:<random>`, `access_type=offline`, `prompt=consent`; token in `<state_dir>/google/token.json` (owner-only, atomic); refresh; `invalid_grant` -> `needs_reconnect` + "צריך לחבר מחדש את גוגל"; revoke on logout; `google_status(home)` for the dashboard; CLI `jarvis google login [--client F] [--calendar-only] / status / logout`; dashboard plugin `GET /api/google`, `POST /api/google/disconnect`. |
| `core/src/jarves/skills/google.py` (new) | `CalendarSkill` (`calendar_list_events(day)`, `calendar_next_event`) over ALL calendars (calendarList, dedupe by iCalUID, cancelled/declined skipped); Hebrew day resolver (היום/מחר/מחרתיים/weekday/השבוע/date, local tz); `GmailSkill` (`gmail_search`, `gmail_read`, `gmail_create_draft`, `gmail_send_draft`); untrusted-text cleaner; `register_google()`; offline intents. |
| `core/tests/test_google.py` (new, 49 tests) | auth (loopback end-to-end, PKCE, bad state, denied consent, Desktop-only client, refresh, 401 retry, weekly expiry, scope missing, logout/revoke, no secrets in logs), calendar, gmail, injection corpus, untrusted-turn rule, draft/read-back/approval, intents, Brain offline, dashboard route, CLI. |
| `docs/GOOGLE_SETUP.md` (new) | Click-by-click Google Cloud + consent setup, Hebrew + English. |

## User decisions implemented

- **All calendars**, read only (no calendar write tool exists).
- **Ask before reading mail**: `gmail_search` returns only count + sender names (subjects fenced, addresses never
  handed out), message "יש N מיילים. לשאול אם להקריא אותם." plus a rule for the model. `gmail_read` accepts only ids
  that `gmail_search` offered in the last 30 min (code `GMAIL_NOT_OFFERED` otherwise). The *asking* itself is done by
  the model (conversation), the code only enforces "search/offer first".
- **Sending**: `gmail_create_draft` (action, pre-authorized only when `gmail.compose` was granted; nothing leaves
  the mailbox) returns a full read-back; `gmail_send_draft` is **destructive**: the registry ALWAYS asks via the
  existing `registry.confirm` (screen + voice note), from any origin, with JARVES's own stored copy of recipient,
  subject and text (not the model's words). Only drafts JARVES wrote in this session can be sent.
  Recipient by name ("תשלח מייל לדנה") is looked up in From/To/Cc of recent mail (no Contacts scope): one match ->
  used; several -> `GMAIL_RECIPIENT_AMBIGUOUS` with choices; none -> asks for the address.
- **Untrusted text**: tools marked `untrusted_output` (both calendar tools, `gmail_search`, `gmail_read`); after one
  ran, the registry refuses every model-origin action/destructive tool (`TOOL_UNTRUSTED_TURN`) until the user speaks
  again. Cleaner drops script/style, `display:none`, white/zero-size text, `hidden`/`aria-hidden`, attachments,
  zero-width and Unicode tag characters; links -> "[קישור]"; bodies capped at 1,500 chars. The spoken `message`
  never contains third-party text.
- **Weekly re-consent**: `invalid_grant` keeps the Skills registered and every tool answers
  "צריך לחבר מחדש את גוגל. אפשר מדף הניהול, או בפקודה jarvis google login."; health component `google` = degraded;
  `/api/google` shows `needs_reconnect: true`, `last_error`, `message_he`.
- **Tokens private**: `<state_dir>/google/` (outside the repo, tested), logs carry only codes/scope names (tested
  for refresh token, access token and client secret).

## Technology check (19 Oct 2026)

- Scopes (Google pages): `calendar.events.readonly` + `calendar.calendarlist.readonly` are the minimal pair for
  "events on all calendars" + calendar names; `gmail.readonly` (restricted) + `gmail.compose` (restricted; "manage
  drafts and send") instead of `gmail.modify`/`mail.google.com`.
- Desktop flow: loopback IP + PKCE is the supported method; OOB and custom schemes are gone; device flow excludes
  these scopes.
- **Deviation from the plan**: no `google-api-python-client` / `google-auth-oauthlib`. PyPI says the API client is
  "complete and in maintenance mode" (2.201.0); the flow is ~150 lines over httpx (already a dependency), testable
  with MockTransport, no new packages on the Pi. Google's hosted MCP servers stay the upgrade path (plan section 1).
- Windows has no tz database in the venv (`tzdata` missing): dates use `Asia/Jerusalem` when available (Pi/Linux),
  else the machine's local zone. UNVERIFIED on Windows across DST change.

## Integration hooks (files owned by other packages)

| File (owner) | Hook |
|---|---|
| `skills/__init__.py` (WP-D) | `Tool.untrusted_output`, `SkillRegistry.new_turn()`, `UNTRUSTED_GRACE_S = 3`, refusal `TOOL_UNTRUSTED_TURN` in `_authorize`, taint set in `call`. |
| `app.py` (WP-B/A) | 2 lines: `self.google = register_google(self.registry, self.home, self.health)` after the Settings Skill. |
| `voice/live.py` (WP-A/C) | `_remember_user` calls `registry.new_turn()` (user spoke again). |
| `brain.py` (WP-C) | `Brain.respond` calls `registry.new_turn()`; `_RULES += skills.google.INTENT_RULES`. |
| `cli.py` (WP-B) | `add_google_cli(sub)` + `"google": cmd_google`. |
| `dashboard.py` (WP-B) | `"jarves.google_auth"` added to `PLUGIN_MODULES` (route `/api/google`). No UI card yet: the dashboard page could show `message_he` + a Disconnect button. |

`new_turn` keeps a taint younger than 3 s, because a GPT-Live input transcript can complete just after the tool of
the same turn returned (otherwise the race would clear the block).

## Tests

- `python -m pytest core/tests -q` (worktree, `PYTHONPATH=src`): 485 passed, 1 skipped (3 min 7 s), 19 Oct 2026; `tests/test_google.py` 49 passed.
- Cost: $0 (no real GPT-Live/GPT-6 call, no Google call).

## Open questions for the user

| Question | Recommendation | Status |
|---|---|---|
| Try "Publish app" (unverified production) to stop the weekly reconnect? | Keep Testing first; try later with Calendar-only, since Google does not document it for restricted Gmail scopes | USER DECISION REQUIRED |
| Speak sender names before asking "לקרוא?" (others may be in the room)? | Yes, only count + sender names; subjects only after "yes" | Implemented as recommended; confirm |
| Should JARVES find recipients from Google Contacts (`contacts.readonly`)? | Not now: recent-mail lookup + asking for the address avoids another scope | DEFERRED |
| Real-account check (gates 5 + 7 of the plan) | User runs `jarvis google login`, then asks "מה יש לי מחר ביומן" and "יש מיילים חשובים?" once | BLOCKED on user setup (docs/GOOGLE_SETUP.md) |
