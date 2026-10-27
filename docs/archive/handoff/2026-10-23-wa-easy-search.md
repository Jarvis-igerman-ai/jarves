# WhatsApp: easy, forgiving chat-name search - 23 Oct 2026 (#17)

Based on `fix/wa-chat-name-search` (d05df94, not merged to main yet). Branch `feat/wa-easy-search`. No PR opened.

## Asked
Owner (Hebrew, real PC): hard to find a contact such as "רשימת קניות" ("shopping list") or a similar name; wants an
easy, regex-like search. Chat-NAME search only (message-text search is `feat/wa-archive`'s and is untouched).

## Design
New pure module `core/src/jarves/wa_namesearch.py` (no model, no network). `search()` returns ranked `Hit(chat, score, reason)`,
best first, max 10:
exact 100 / starts with 90 (whole first words) / word match 70, 65 for spelling variants / starts with 60 (inside the first
word) / pattern 60 / contains 50 / similar (typo) 40 / similar (keyboard layout) 30 / similar (spelling) 25.
- Typos: per word, Damerau edit distance 1 for query words of 4+ letters, 2 for 8+, 0 below. Hebrew variants: prefixes
  ה/ו/ב/ל/מ/ש/כ/של, final letters, niqqud, geresh/apostrophes dropped inside words, and a spelling key `canon()` (construct
  ת~ה ending, plural ות/ים, plene ו/י): רשימת = רשימה, קניות = קניה.
- Wrong keyboard layout: standard Hebrew<->QWERTY map, only for single-script queries (akuo -> שלום, גשמש -> dana).
- Latin<->Hebrew by sound: consonant skeletons in a shared alphabet (dana ~ דנה, moshe ~ משה, minimum 2 consonants); a
  translation (shopping ~ קניות) is deliberately not matched.
- Mini-language (only when the text really uses it; a stray `?` or `"` in a sentence is not a pattern): `*`, `?`, `"phrase"`,
  `a|b`, leading `^`, trailing `$`, several words = all of them. `/regex/`, `--regex` and the tool's `regex=true` use a
  small Thompson/Pike NFA (`Regex`): linear in text x program, no backtracking, no backreference/lookaround, query max 80
  chars, program max 240 instructions, repeats max 30, 1 s total guard. Invalid -> `QueryError` with `.en` and `.he`.
- `resolve(fuzzy=True)` returns only the hits that share the best score: one = used by chat/summarize tools, several =
  existing `WA_AMBIGUOUS` (never auto-picked). `fuzzy=False` (compose, send, allow/remove) is unchanged and exact.
  `_resolve_one` (read tools) also accepts patterns; a bad pattern gives `WA_BAD_PATTERN`.
- `whatsapp_find_chat(text, regex=False, limit<=10)`: rows carry `reason`, data has `clear_best_match`; `jarvis whatsapp find
  [--regex PATTERN] <text>` prints `[reason]` per row, `--json`, a "none is picked for you" line, and the pattern help
  on empty/invalid input (EN+HE through `clilang`).
- Instructions (`BACKEND_RULE`, `NOT_FOUND_NEXT`, tool descriptions): try `whatsapp_find_chat` with part of the name, a
  similar spelling or a pattern and show ranked candidates instead of "cannot find"; the owner picks.

## Files
`core/src/jarves/wa_namesearch.py` (new), `whatsapp_bridge.py` (resolve/find_ranked/tool/instructions, `WA_BAD_PATTERN`),
`whatsapp_admin.py` (find, `--regex`, help), `guides/data/whatsapp_bridge.toml` (say lines, two steps incl. "search
patterns", problem text, error code), tests `test_wa_namesearch.py` (new), `test_whatsapp_surfaces.py`,
`test_whatsapp_find_chat.py` (schema now has `regex`).
`cli_parity` needed no change (`find` is already under the `whatsapp` group). No dashboard strings were added, so
`catalog_add.py` was not needed; the Hebrew lives in the guide and `clilang` pairs.

## Not verified
- Live bridge with real chats / a real model conversation; only the fake bridge, offline.
- Real-world false-positive rate of "similar" tiers on a large Hebrew contact list (auto-use of a single similar hit is
  by design; several are always offered). Spelling-by-sound is a heuristic (ח/כ/ק, ה dropped, vowels ignored).
- Pattern/`regex` cap is 80 characters in the tool schema as well.
