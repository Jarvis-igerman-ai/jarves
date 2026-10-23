# Chat message timestamps (23 Oct 2026)

Follow-up to PR #85 (chat UI polish), owner asked for message timestamps.

- Server: `chat_engine.ChatEngine._add_message` stamps every stored message with `ts` (epoch seconds); events already carried `ts`. Both the dashboard Chat page and the phone page read the same state and events, so both show times.
- Page (`chatview.js` / `chatview.css`): a small time under each user and assistant message (time only today, date + time on an older day), full date and time on hover. Done with a `data-time` attribute and CSS `::after`, so the message text, copy and tests are unchanged. Hidden while an answer is still streaming (the caret uses `::after`). Messages without a time (a resumed older conversation) show none. Tool lines and notices show no time.
- Tests: `test_web_chat` (every message and event has a float `ts`), `test_chat_markdown_browser` (time shown, none without `ts`, CSS content equals `data-time`).
- Not done on purpose: `jarvis chat` (terminal) has no timestamps; the terminal scrolls in order and adds noise. Say if wanted.
- `test_web_chat::test_silent_turns_never_speak_ring_or_light_the_face` failed once in a combined local run and passed on re-run (alone, with and without this change, and in the full file): a timing flake, not caused by this change. If it shows up in CI, treat it as its own bug.
