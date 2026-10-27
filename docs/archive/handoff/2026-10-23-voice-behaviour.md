# Handoff 2026-10-23: voice behaviour fixes (issue #17)

Branch `fix/voice-behaviour`. Evidence: owner PC `jarvis issues list` + `jarvis diagnose --days 3`. Offline fixes only; no real voice, camera or cloud was used.

| # | Evidence | Root cause (honest) | Fix |
|---|---|---|---|
| 1 | Confusing filler, user asked what "רוגס" means | No code emits "רוגס": it is GPT-Live (free speech) saying a radio-style "Roger" acknowledgement, heard as "רוגס". The persona banned only bare "ok/done" and never named jargon; the diagnose ACK list and live.py filler lists were two separate copies. | New `jarves/phrases.py` is the single reviewed list (jargon acks, plain acks, lead-in stems). The persona now bans jargon explicitly; `diagnose.py` and `voice/live.py` import it. |
| 2 | Camera shutdown / actions claimed before result; LIVE_FALSE_ACTION_CLAIM x2 | The persona forbade "מכבה/שולח" generally; camera, mute and notes were not named, and the detector missed "כיבתי"/"ניתקתי". The correction note asked for a plain "not done" only. | Persona names camera, mute, notes. Claim verbs extended. FALSE_CLAIM_NOTE now asks for a one-sentence self-correction saying what really happened and that it was NOT done. |
| 3 | Note created after "delete" | `note_delete` needed a numeric id the voice model did not have, and `note_add` was the only note tool it could use freely; it fell back to creating. | `note_delete` takes an id OR title words, resolves exactly one note (several matches or none: refuses, nothing deleted), and answers `deleted the note "<title>"`. `note_add` says "Never for delete/remove". Persona and text-agent prompt say delete is never create. |
| 4 | LIVE_UNDELEGATED | The watchdog already hands the turn to the backend after 3 s (CV-15). Cause is the model saying "רגע" without delegating. | Persona: a waiting word only together with the delegation in the same response. Watchdog unchanged. Not reproducible offline beyond the existing fake-based tests. |
| 5 | Rude wording on mute; unverified advice for ending the session | Persona had no rule for stop/mute requests or for explaining how to end a session. | Persona "Stop requests" rule (short polite word, comply, no lecture) and a ban on unverified how-to-end advice (delegate end_conversation instead). Text-agent prompt gets the same one-liner. |
| 6 | workspace_read 4x in 30 s | The model re-read the same file; nothing deduplicated read tools. | `Tool.cache_s` (read tools only): an identical successful call within the window returns the earlier result with a `cached` hint; any successful side effect or a new user turn drops the cache. `workspace_read` uses 30 s. |

Safety rules unchanged: approvals, untrusted-content guard, destructive confirmation (note delete still asks, now naming the note).

Tests: `core/tests/test_voice_behaviour.py` (prompt assertions plus fakes for notes, registry cache, false camera claim). Updated none of the existing tests except the wording they already asserted.

Not verified: real GPT-Live speech (whether the model stops saying "Roger" depends on the live model following the prompt; check the next `jarvis diagnose`), the real camera/mute flows, Hebrew pronunciation. Hebrew UI strings: none new in the dashboard; prompts and tool descriptions are English by design.
