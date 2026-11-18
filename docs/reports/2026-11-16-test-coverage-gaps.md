# Test coverage gaps in core/src/jarves (JAR-123, 16 Nov 2026)

Method: an import-graph scan, not line coverage. A module counts as directly tested when some file under
`core/tests` names it as `jarves.<module>` or imports it by name (`from jarves... import <module>`).
`workflow/**`, `orchestrator/**` and `__init__.py` were left out. The scan was run at origin/main `5fb8529`.

Six modules had no direct test import:

| Module | Lines | Verdict |
|---|---|---|
| `voice/speechcache.py` | 44 | **Tests added**: `core/tests/test_speechcache.py` (8 tests). The tests found a crash: a cache file cut off mid-sample raised `ValueError` from `get()` and stopped the start-up greeting (`app.speak`). An 8-bit or stereo file, or one whose rate header is outside 8000–48000 Hz, was also played as noise. All of these are now a cache miss. |
| `voice/stt.py` | 82 | **Tests added**: `core/tests/test_stt_whisper.py` (8 tests, fake `faster_whisper`). They cover model fallback, the actionable `STT_MODEL_UNAVAILABLE` error and retrying after it, one shared load for concurrent callers, skipping short audio, and importing the module without the voice extra. These tests describe the current behaviour. A manual mutation check confirmed they catch regressions: dropping the retry reset or the short-audio guard fails a test. |
| `projectmgr/engine.py` | 765 | Covered indirectly. `test_projectmgr.py` and `test_projectmgr_git.py` drive the package. Leave as is. |
| `projectmgr/debate.py` | 144 | Covered indirectly by `test_projectmgr.py::test_debate_op_with_judge_and_with_owner`. |
| `skills/sandbox_child.py` | 112 | Started as a child process by `skills/generated.py`, which its own tests exercise. No in-process tests (by design it never imports jarves). Candidate: unit-test the restricted builtins and guarded import directly. |
| `mobile_prefs.py` | 82 | Covered through the HTTP routes in `test_mobile_prefs.py` and `test_mobile_phone_e2e.py`. |

Follow-ups noted, not done here:
- `SpeechCache.put` writes a fixed `speech_<key>.tmp` name. Two concurrent puts of the same key could race. Today
  only the single start-up greeting is cached, so the impact is low. A unique temp name would remove the race.
- `WhisperSTT.transcribe` logs the transcript text in `STT_DONE`. Whether privacy mode redacts it there is UNVERIFIED.
- A real line-coverage run (`coverage run -m pytest`) would find untested branches inside modules that are already
  imported. This scan cannot see them.
