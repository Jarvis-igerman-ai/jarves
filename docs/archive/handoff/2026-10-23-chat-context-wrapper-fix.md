# Fix: `jarvis chat` failed on every message (CORE_INTERNAL)

**Reported by the owner** while testing WhatsApp: every typed chat message answered "Something went wrong; see the log." Log:
`CHAT_TURN_FAILED ... TypeError: attach_brain.<locals>.reply() got an unexpected keyword argument 'on_delta'`
(`exc_frames`: `chat_service.py:221 in _turn`, `brain.py:583 in respond`). `jarvis ask` worked.

**Cause:** `context_trace.attach_brain()` replaces `agent.reply` with a wrapper `reply(text)`. The terminal chat
(`chat_service._turn`, added later the same day, 21 Oct 22:45) always calls `brain.respond(..., on_delta=...)`, which calls
`agent.reply(text, on_delta=...)`. The wrapper did not accept `on_delta`, so every chat turn raised `TypeError` whenever the
context-capture feature was on (its default).

**Fix:** the wrapper is now `reply(text, on_delta=None)` and forwards `on_delta` only when given (a brain that does not stream
is called exactly as before). One regression test: `test_typed_chat_wrapper_passes_on_delta_through` in
`core/tests/test_context_trace.py` (fails with the exact TypeError without the fix; passes with it; also covers a non-streaming brain).

**Verified:** `test_context_trace.py` (11) and `python core/tools/prepush.py`. **Not verified:** a real `jarvis chat` session on the
owner's PC after the fix (the owner re-tests after updating). Voice uses a different path (`attach_live`) and was not touched.

**Decisions needed:** none.
