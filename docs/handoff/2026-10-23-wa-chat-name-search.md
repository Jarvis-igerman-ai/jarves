# WhatsApp: find a chat by part of its name - 23 Oct 2026 (#17)

## Asked
Bug report from JARVES: a chat named "לב אשתי המושלמת לב" was not found by "לב" / "אשתי"; message-text search does not
resolve names; the assistant said it was "still searching" without any lookup running.

## Root cause
`whatsapp_bridge.resolve()` matched only a number, the full name or the first word. No tool searched chat names.

## Change
- `resolve()` is tiered, first non-empty tier wins: number, full name, first word, ANY word (Hebrew prefixes
  ה/ו/ב/ל/מ/ש/כ/של on both sides, up to two stacked), substring. `_norm` now also drops emoji/symbols, niqqud, bidi
  and control characters, punctuation, unifies Hebrew final letters, casefolds. One hit is used; several give the
  existing `WA_AMBIGUOUS` (5 candidates, name + last 4 digits, untrusted); none gives `WA_UNKNOWN_CONTACT` whose text
  says chat names were searched, with `next_step` (another part of the name, or `whatsapp_search`).
- `fuzzy=False` keeps the old exact rules (tiers 1-3) for `whatsapp_compose`, `jarvis whatsapp send` and
  `allow/remove` (a middle word never picks a recipient or an allow-list entry).
- New read tool `whatsapp_find_chat(text, limit<=10)`: names only, untrusted + taint, in `READ_TOOLS`.
  `whatsapp_search` description now says it searches message TEXT.
- `BACKEND_RULE`/`FRONT_RULE`: calls are synchronous, never say "still searching" without a tool call this turn,
  use `whatsapp_find_chat` with part of the name when a lookup fails, say which kind of search was done.
- CLI `jarvis whatsapp find <part of a name>` (names, `--json`, no message text, no model). Guide
  `whatsapp_bridge.toml`: new say line and problem entry (EN+HE).

## Tests
`core/tests/test_whatsapp_find_chat.py` (new), `test_whatsapp_surfaces.py::test_cli_find_searches_names_only`,
`test_whatsapp_policy.py` tool set. All other whatsapp tests unchanged and green.

## Not verified
- Live bridge with real chats and a real model conversation (only the fake bridge, offline).
- The chat list is capped at 200 chats and filtered by the bridge read scope: a chat outside the scope is not found
  (guide says so). Hebrew prefix stripping can add extra (never fewer) candidates; those are shown, not auto-picked.
- No dashboard strings were added, so `catalog_add.py` was not needed; the CLI text uses `clilang` inline.
