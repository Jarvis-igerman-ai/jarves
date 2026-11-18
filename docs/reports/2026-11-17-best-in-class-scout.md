# Best-in-class scout 2026-11-17

Registry: `docs/decisions/best_in_class.toml`. Mode: online (official sources fetched).
The scout reports only; a switch is a separate PR with tests.

| Stage | Id | Recorded | Checked | Source |
|---|---|---|---|---|
| model | text_model | `gpt-6-luna` | 2026-11-16 | https://developers.openai.com/api/docs/models |
| voice | live_model | `gpt-live-1` | 2026-11-16 | https://developers.openai.com/api/docs/models |
| voice | realtime_model | `gpt-realtime-2.1` | 2026-11-16 | https://developers.openai.com/api/docs/models |
| voice | speech_model | `gpt-realtime-2.1-mini` | 2026-11-16 | https://developers.openai.com/api/docs/models |
| voice | speech_http_model | `gpt-4o-mini-tts` | 2026-11-16 | https://developers.openai.com/api/docs/models |
| agents | openai-agents | `0.24.0` | 2026-11-16 | https://pypi.org/pypi/openai-agents/json |
| voice | faster-whisper | `1.2` | 2026-11-16 | https://pypi.org/pypi/faster-whisper/json |
| voice | pyopen-wakeword | `1.1` | 2026-11-16 | https://pypi.org/pypi/pyopen-wakeword/json |
| voice | stt_model | `small` | 2026-11-16 | https://huggingface.co/ivrit-ai |

## Findings

- **missing** `speech_http_model`: gpt-4o-mini-tts is not listed on https://developers.openai.com/api/docs/models: check for deprecation or a successor
- **deprecated** `speech_http_model`: gpt-4o-mini-tts deprecated Oct 1, 2026, shutdown Jan 14, 2027, replacement gpt-realtime-2.1-mini (https://developers.openai.com/api/docs/deprecations)

## Actions

- `speech_http_model` deprecated (shutdown Jan 14, 2027): issue [#1224](https://github.com/Jarvis-igerman-ai/jarves/issues/1224) (P2). The `missing` row is the same cause.
- Everything else in the registry is current as of 17 Nov 2026: `gpt-6-luna`, `gpt-live-1`, `gpt-realtime-2.1`, `gpt-realtime-2.1-mini` have no newer id in their family on the official models page. The PyPI libraries' latest releases are already allowed by the pins: openai-agents 0.24.0, faster-whisper 1.2.1, pyopen-wakeword 1.1.0.
- The page also lists `gpt-6.1-terra` (a different tier, not the luna family) and `gpt-transcribe` / `gpt-live-transcribe`. These are possible options for a manual look, not findings.
- Nothing runs this scout every month automatically yet: there is no scheduled trigger. The commands are listed at the top of `docs/decisions/best_in_class.toml`.
