# Error messages: clarity audit of the top 10 (JAR-128, 16 Nov 2026)

Rule: every user-facing error says **what happened** and **what to do**, in English and Hebrew.
"Top" means most likely to be seen. Picked by how often each string appears in the code (`"error": "..."` counts across
`core/src/jarves`, `Failed: ` sites in `dashboard_static`) and by surface: page load, chat, paired device, Drive.
Base: origin/main `f4390920`.

## Cross-cutting findings

1. **Server errors stayed English in Hebrew mode.** 13 dashboard sites (`health.js`, `selfcheck.js`, `bugs.js`,
   `selfspeed.js`) printed `נכשל: ` + the raw English server text. None of the server error strings below had a Hebrew catalog
   entry, so even the sites that called `JL.t` showed English. Fix: the shared `api()` in `app.js` now translates
   `data.error` once through the catalog. The English source stays on `err.en`, and `err.code` is passed on.
2. **Chat code `CHAT_STOPPING` had no words.** `chatview.js` `WORDS` covered `CHAT_BUSY`, `CHAT_UNAVAILABLE`, etc., but not
   `CHAT_STOPPING`. The user saw the raw "shutting down" or "the chat engine is not running".

## The ten strings

| # | Where | Before | After (EN) | Hebrew |
|---|---|---|---|---|
| 1 | `app.js` `fail()` (every page) | `Could not load: <msg>` | + "Check that JARVES is running (jarvis status), then refresh the page." | added |
| 2 | `chatview.js` `CHAT_STOPPING` | raw `shutting down` | "JARVES is stopping or restarting. Wait a few seconds and send again." | added |
| 3 | `chatview.js` `CHAT_TOO_LONG` | "The message is too long." | + limit (4000) + "Shorten it or split it into several messages." | updated |
| 4 | `chatview.js` failed turn | "Something went wrong. See the log." (which log?) | "Try again; if it repeats, open the Health page or run jarvis logs." | updated |
| 5 | `dashboard.py`, `chat_engine.py` (paired device refused) | "only the computer itself can do this" | + ": do it in the dashboard on that computer" | added |
| 6 | `runtime/__init__.py`, `camera/surface.py` | "JARVES is not running; start it first (jarvis start)" | unchanged (already clear) | added |
| 7 | `mobile_drive.py`, `drive_actions.py`, phone APIs | "too many requests: wait a minute" | unchanged | added |
| 8 | `swr.py` | "still loading, retry in a few seconds" | unchanged | added |
| 9 | `mobile_drive.py` briefing | "the briefing is not available right now" | + ": try again in a minute" | added |
| 10 | `chat_engine.py` | "chat is not running here: start `jarvis web` or JARVES" | unchanged | added |

The same catalog part also adds Hebrew for the related server texts: "shutting down", "the chat engine is not running",
"the previous message is still being answered", "chat is not running here", "message too long: ...",
"wait a minute before trying again" and "profiles are managed only on this computer".
Part file: `core/src/jarves/dashboard_static/locales/parts/2026-11-16-jar-128-error-messages.json`.

## Guard

`core/tests/test_error_messages.py` checks that each of the ten strings is still in its source file, has an English action
cue, has a Hebrew catalog entry with a Hebrew action verb, that `api()` translates errors, and that every chat code the
view can receive has words.

## Not done (follow-ups)

- The phone app (`dashboard_static/mobile/*.js`) shows `e.message` directly and does not load the dashboard catalog.
  In Hebrew mode the phone still shows English server errors (for example, the Drive briefing). The English is clearer now;
  the Hebrew on the phone needs its own change.
- CLI error strings were not audited in this pass.
- UNVERIFIED: not checked in a real browser on the installed product. The evidence is source and catalog tests only.
