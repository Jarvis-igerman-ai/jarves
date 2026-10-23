## Voice selection correction — checked 18 October 2026

User delegated the masculine/Jarvis-like voice choice. Selected **vesper** for existing GPT-Live: official table describes British English, masculine, natural. Kept **cedar** separately for Realtime/read-aloud because Live and Realtime voice lists differ. No model/provider/dependency change. Sources: [Live voice list](https://developers.openai.com/api/docs/guides/live-conversations), [Realtime recommended voices](https://developers.openai.com/api/docs/guides/realtime-conversations). Calm delivery instructions added; accent, Hebrew intelligibility and subjective preference require a real listening review. No actor identity imitation claimed.

# SW-V1 technology decision (WP1, Issue #5)

**Checked 18 Oct 2026** against official docs, model lists and package registries, not training memory. Per the user's rule ([AGENTS.md](../../AGENTS.md#best-in-class-technology-mandatory--user-decision-18-oct-2026)), this is the newest best-in-class stack in one coherent OpenAI vendor stack. Cloud is primary because the target is a Raspberry Pi 5; local components are **fallback only**.

## Selected stack

**Startup re-check, 18 Oct 2026:** retain `gpt-live-1` / `marin` and the existing SDK. [Official OpenAI Docs: greet before the caller speaks](https://developers.openai.com/api/docs/guides/live-conversations#greet-before-the-caller-speaks) specifies a startup `developer` message in `session.input` and continuous audio input, including silence. Desktop preparation now follows that route, sends generated silence without opening the physical mic, buffers output in the existing bounded queue, and considers greeting readiness only after actual output audio. The documentation explicitly says prompting alone does not guarantee a greeting; timeout remains an error, not readiness. One shared-budget, max-15-second real session probe received first audio in 4.81 s (4.09 s connection); no microphone/speaker was opened. It replaces the separate greeting TTS request that took 13.343 s before first audio in the user's last run. No new model/library or conversation-storage feature selected. ETA uses a local timing-only cache; full mic, Hebrew recognition, speaker quality and Pi boot performance remain unverified.

| Layer | Choice | Why / evidence | Status |
|---|---|---|---|
| Live voice (primary) | **OpenAI GPT-Live `gpt-live-1`**, Live WebSocket `wss://api.openai.com/v1/live/sessions`, PCM16 24 kHz, voice `marin` | Listed as the premier voice model with smooth interruption handling ([models](https://developers.openai.com/api/docs/models/all), [gpt-live-1](https://developers.openai.com/api/docs/models/gpt-live-1)). Full duplex. It delegates reasoning and tools to a backend ([delegation guide](https://developers.openai.com/api/docs/guides/live-delegation)). $0.05/min of voice, plus backend tokens. | Implemented (`voice/live.py`). **Live call UNTESTED: no API key.** OpenAI does not document Hebrew among GPT-Live's voice languages, so it must be tested. |
| Voice backend | **Responses delegation → `gpt-6-luna`** + hosted `web_search` + JARVES Skills as function tools | GPT-6 is the current flagship family; the delegation guide recommends `gpt-6-luna` as the starting backend and `gpt-6-sol` for harder tasks. | Implemented; function-call round trip tested offline |
| Alternative live voice | **`gpt-realtime-2.1`** via **OpenAI Agents SDK 0.24 `RealtimeAgent`** (MIT) | Newest Realtime-API model (released 6 Jul 2026), with tool use and server-side barge-in. A one-line switch (`voice.mode = "realtime"`) in case GPT-Live's Hebrew is weaker. | Implemented (`voice/realtime.py`); live call UNTESTED |
| Typed chat / agent | **OpenAI Agents SDK** `Agent` + `Runner`, model `gpt-6-luna`, hosted `WebSearchTool` | Maintained official SDK (MIT), with function tools and sessions. Tracing upload is disabled for privacy. | Implemented; live call UNTESTED |
| Speech for typed replies | **`gpt-realtime-2.1-mini`** (Realtime session reads text aloud) | OpenAI's deprecation notice names it as the replacement for `tts-1`/`gpt-4o-mini-tts` (removed 6 Jan 2027). **Correction 23 Oct 2026:** the code still tries `voice.speech_http_model` = `gpt-4o-mini-tts` on `/v1/audio/speech` first and uses `gpt-realtime-2.1-mini` as its backup (`voice/tts.py` `tts_chain`); no newer model exists on that endpoint (SDK `SpeechModel`, checked 23 Oct 2026). Move before 6 Jan 2027 (HANDOFF decision queue). | Implemented; live call UNTESTED |
| File transcription (live test tools) | `gpt-transcribe` | OpenAI speech-to-text guide: "start with" it for files (checked 23 Oct 2026); replaces `gpt-4o-transcribe` in `core/tools/say_roundtrip.py`, `suite_v3_voice.py`. | Changed; live call UNTESTED |
| Coding agents' OpenRouter route (project manager) | `openrouter/anthropic/claude-sonnet-5.5` | Newest Claude Sonnet on OpenRouter (released 28 Sep 2026); OpenCode `-m provider/model`. Paid, off by default. Coding agents without a model use each tool's own current default. | Configured; live call UNTESTED |
| Realtime input transcription | `gpt-live-transcribe` | New low-latency STT model (Jul 2026). `gpt-4o-*-transcribe` shut down on 26 Feb 2027. | Configured |
| Offline fallback STT | faster-whisper 1.2 (CTranslate2), model `small`; `ivrit-ai/whisper-large-v3-turbo-ct2` optional | Measured on this PC: `small` ~4 s per 2 s phrase with minor errors; ivrit-ai turbo accurate but ~19 s | **Measured** (TTS→STT round trip, 3/3 intents correct with ivrit, 2/3 with small) |
| Offline fallback TTS | Windows OneCore "Microsoft Asaf" (WinRT); espeak-ng on Linux | Always available, robotic. Fallback only. | **Measured**: spoke on PC speakers in the E2E run |
| Godot ↔ Core | localhost WebSocket (Godot `WebSocketPeer`, Python `websockets` 16), per-install token, loopback only | Built-in on both sides; no broker. | **Measured**: real Godot ↔ real Core E2E PASS |
| Memory / state | SQLite (stdlib): memories, settings, daily usage counters | Durable, zero-dependency. Conversation history is kept in RAM only (privacy). | Tested (restart persistence) |
| Skills | Small registry: pydantic-validated args, side-effect flag, user confirmation, timeouts, crash isolation; exported to Agents SDK / GPT-Live / Realtime tools | One interface for all three AI paths; a second Skill needs no Core change (tested) | Tested |
| Music | **Pear Desktop 3.12.0** (MIT, ~34k★, active, ARM64 builds) + its API Server plugin: `search` → `queue` → `play`/`pause`/`next`/`volume`/`song` | Plays through the official YouTube Music web player in the user's own signed-in window. JWT pairing approved by the user. No cookies or stream extraction. | Adapter tested with a mock; **real app verified**: listens on 127.0.0.1 only, enforces auth (401). **Real playback BLOCKED: needs one user "Allow" click** (+ optional YouTube sign-in) |
| Music (rejected for now) | Music Assistant YouTube provider (cookies + PO-token, best effort); small ytm MCP servers (Playwright, stream extraction) | Cookie handling and unmaintained risk; extraction conflicts with the ToS policy | Not used |
| Python tooling | uv + `uv.lock`, Python 3.12 | Reproducible; works on Windows and Pi | Clean-clone test PASS |

## Cost drivers (user-funded, metered)

| Driver | Price / rule | Guard |
|---|---|---|
| GPT-Live voice | $0.05/min of voice | `ai.daily_realtime_minutes` (default 30), `voice.session_max_s` (600 s); tap again to end |
| Backend tokens | `gpt-6-luna` tokens per delegated request (price not re-checked here); `gpt-6.1-sol` is $2/$10 per 1M input/output tokens | `ai.daily_request_limit` (default 200) |
| Offline commands | Music, time, memory and status run locally at **$0** | — |
| Tests and CI | Never call paid APIs | — |

## Main risks

1. GPT-Live Hebrew quality is undocumented; switch to `realtime` mode if it's weak.
2. Echo on open speakers. Half-duplex gating is the default; barge-in needs headphones or the AEC mic (ReSpeaker/XMOS on the device).
3. Pear Desktop is unofficial; YouTube site changes can break it. It is MIT-licensed and maintained; the adapter fails truthfully.
4. Pi 5 performance of Godot + Pear (Electron) + Core together is untested (#8).
5. A YouTube login is the user's own action.
