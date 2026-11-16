# Terminal chat (#331): what is left, and whether to build it (JAR-102)

Date 2026-11-09. Base: `origin/main` 89832588. Proposal only: no code changed, nothing here authorises work.
No real terminal, phone or device was exercised. Every real-console outcome below is UNVERIFIED.

## Why this gap

#331 is an open v5 owner request: make `jarvis chat` "feel like Claude Code". JAR-66 listed it as "not assessed"; JAR-81 and JAR-97 do not cover it. No open PR or Paperclip task touches it (`gh pr list` and the task list, 9 Nov). The stage question raised by evidence pack C stays with JAR-81 finding 1 and is not reopened here.

**`docs/NOW.md` names no product scope** (same finding as JAR-66), so the owner must name this as scope before implementation.

## Problem

Two slices have merged (#848, and commit 3c58971b) but #331 stays open with no list of what remains. The code shows most of the wish list is done. Few gaps remain, but one cannot be fixed with the current input method. Nobody has yet looked at the result in a real Windows console, which is where the original complaint came from.

## Evidence: wish list against `main`

| #331 item | State | Evidence |
|---|---|---|
| Clean prompt and spacing | Built | #848; `chat_cli.py` |
| Streaming answers | Built | `chat_render.MarkdownStream` |
| Compact tool lines, collapsing | Built, colour terminal only | `chat_render.tool_group`; `test_successful_tools_in_a_row_collapse_into_one_rewritten_line` |
| Markdown rendering | **Partial** | Headings, bold, inline code, bullets, fences. No table handling in `chat_render.py`, and no styling on any line containing Hebrew (fact 2) |
| Hebrew display | Built by rule, **real look UNVERIFIED** | Logical order, no escape codes in Hebrew lines (`test_hebrew_lines_get_no_escape_codes_and_are_never_reordered`). The display itself is left to the terminal |
| Up-arrow history | **Gap on Windows** | Fact 1 |
| Multi-line input | Built, by typed convention | Trailing `\` or `"""` block. No Shift+Enter |
| `/help` and slash commands | Built, **no completion** | Command list at `chat_cli.py:40`; no Tab or pop-up handling in the file |
| Esc / Ctrl+C stops a turn | Built; Esc is Windows console only | `console_keys` returns `None` off Windows; `test_esc_stops_the_answer_and_typed_ahead_keys_wait_for_the_next_prompt` |
| Status line | **Partial** | Printed after the banner and on some changes (`show_status_line`). It scrolls away; #848 listed a persistent footer as a follow-up |
| Guide, parity, Hebrew strings | Built | `text_chat.toml`; `text_chat -> chat` |

## Facts that shape the options

1. **Saved history is not recalled on Windows.** `History` writes `chat_history.txt` and hands it to the line editor only through `import readline` (`chat_cli.py:110`). That import fails on the Windows Python checked here (3.12.8, conda-forge: `No module named 'readline'`). A terminal line comes from plain `input()` (`chat_cli.py:172`), so recall depends on the console's own buffer. From the code, the file is written on every message and its contents never reach the Windows prompt. The console's actual behaviour across sessions is UNVERIFIED.
2. **Hebrew answers get less formatting than English ones.** Once a line contains Hebrew, `_put` emits no styling. Markers such as `**` are still removed, so emphasis disappears silently. This is the deliberate #329 guard. It should stay unless a real-console test shows escape codes are safe.
3. **Standard library only is a stated design.** Both module docstrings say so, and `core/pyproject.toml` has no line-editing library.
4. **A new dependency is Tier R by the tool.** `risk_class.py` reports `core/pyproject.toml` as a high-risk path. It reports no other path in the list `chat_cli.py`, `chat_render.py`, `text_chat.toml`, `pyproject.toml`.
5. **Candidate library, checked 9 Nov on [PyPI](https://pypi.org/project/prompt-toolkit/):** `prompt-toolkit` 3.0.53, Python >=3.10, one dependency (`wcwidth`). Its right-to-left behaviour in a Windows console was not tested and is the main unknown.
6. **`text_chat` has never been run by hand.** Its own Windows row says "no hands-on run in a real Windows console yet" (`chat_service.py:39`).

## Options

| Option | What | Size | Tier | Benefit | Cost and risk | Verdict |
|---|---|---|---|---|---|---|
| A. Owner check, then close or narrow #331 | Ten-minute run of the list below; split what remains into exact items | S | N | Replaces guesses with what the owner actually dislikes | Owner time only | **Recommended first** |
| B. Windows-only history on the current input | Custom key loop over `msvcrt` | M | N | Fixes fact 1 with no dependency | Rebuilds a line editor: cursor movement, Hebrew editing, wide characters and paste become ours to maintain | Reject |
| C. Adopt `prompt-toolkit` for the terminal path only | History from the existing file, Tab completion of slash commands, bottom status bar; keep `input()` for pipes and as fallback | M | **R** (dependency) | One maintained library could cover all three input gaps | New dependency in the installed product. Must coexist with the worker-thread reader, type-ahead and approval prompts. Hebrew editing unknown (fact 5) | **Only if A shows history or footer matter** |
| D. Full-screen terminal app | Replace the scrolling chat | L | R | Closest to the look of Claude Code | Takes over the display, largest rewrite, highest Hebrew risk | Reject |

## Recommendation

Take **A**. If the owner still wants history, completion or a footer after using the chat, take **C** behind a spike. Do not start C on code reading alone: the problems the owner sees may be different ones, and C turns a Tier N area into a Tier R change.

**Spike gate for C, before any product change:** in a real Windows console, type and edit a mixed Hebrew and English line, recall it, and paste several lines. If Hebrew editing is worse than `input()`, stop and keep the current input.

## Owner check for option A (about 10 minutes, installed build)

Each item: Works / Does not work / Do not care.

1. A Hebrew answer reads correctly, with no stray marks.
2. A mixed Hebrew and English answer with a list and code reads correctly.
3. Type a Hebrew message and correct a word in the middle of it.
4. Tool lines fold into one line and a failure is clearly shown.
5. Esc and Ctrl+C both stop an answer, and the chat continues.
6. Up-arrow recalls a message from this session, then from an earlier session (the second part is expected to fail, fact 1).
7. Is the one-time status line enough, or should it stay visible?
8. Is Tab completion for `/` commands wanted?
9. A Markdown table in an answer is acceptable as raw text.

These results can feed the owner test list in JAR-66 gap 1 if that is built; this check does not depend on it.

## Risks

- **Privacy:** `chat_history.txt` already holds typed messages. Option C makes them easier to bring back on screen and must keep approval answers and hidden-code prompts out of history. No outside service is added.
- **Security:** the approval prompt is the sensitive path. Option C needs a regression test that type-ahead and history can never answer an approval. The package must be pinned through the maintained setup path.
- **Cost:** none at runtime. Option C adds a package to the installed product and to dependency upkeep.
- **False confidence:** closing #331 on tests alone would repeat the problem. It should close only on the owner's own run.

## Tier

- **This doc PR:** Tier N (docs only).
- **Option A:** no code. For any resulting small rendering fix, run `risk_class.py` on the final file list.
- **Option C:** Tier R because of `core/pyproject.toml`: owner decision, Opus review, `no-automerge`.

## Owner decisions needed (no code until answered)

1. Name #331 as product scope in `docs/NOW.md`? Recommended: yes, for option A only.
2. Is standard-library-only still a requirement for `jarvis chat`? Recommended: decide after A; it matters only if C is wanted.

## Acceptance for option C, if chosen later

- Up-arrow recalls messages from an earlier session on Windows, read from the existing file with no format change.
- A multi-line message is recalled as one entry.
- Pipes, `NO_COLOR` and the #329 rules behave as they do now (`test_chat.py` and `test_chat_render.py` still pass).
- Approval and code prompts never receive history, completion or type-ahead text (test).
- If the library is missing or fails to start, the chat falls back to `input()` (test).
- Guide updated in English and Hebrew. Real-console Hebrew editing stays UNVERIFIED until the owner runs it.

## Real-device verification

This analysis needs none. Real Windows console look, Hebrew editing and all Raspberry Pi behaviour stay UNVERIFIED.
