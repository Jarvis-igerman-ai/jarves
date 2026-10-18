# JARVES Core (Python)

The assistant brain: state, the Godot face link, DevicePort, voice, memory, Skills and music. Stack decision and sources: [SW_V1_TECH_DECISION](../docs/reviews/SW_V1_TECH_DECISION.md).

## Setup (Windows now, Raspberry Pi OS 64-bit later)

```powershell
winget install astral-sh.uv          # once (Pi: curl -LsSf https://astral.sh/uv/install.sh | sh)
cd core
uv sync --extra voice                # locked dependencies; Python 3.12 is installed by uv
```

On the Pi, also run `sudo apt install libportaudio2 espeak-ng`.

**Best quality (recommended).** Give JARVES your OpenAI key. It is read from the environment only and never stored in files or logs:

```powershell
jarvis key        # paste the key; saved in %LOCALAPPDATA%\jarves\secrets.env (outside the project, never logged)
```

> **Required: set a monthly spend limit** for your OpenAI project (platform.openai.com → Settings → Limits).
> - **Local guard:** JARVES's daily limits (`ai.daily_request_limit`, `ai.daily_realtime_minutes`) reserve before every request it sends.
> - **Not a hard cap:** in a GPT-Live conversation, OpenAI's server starts backend requests itself. The API offers no way to block them in advance. JARVES counts every one and ends the conversation at the limit, but a few extra requests can slip through.

You can also edit that file yourself; it holds one line, `OPENAI_API_KEY=sk-...`. If the `OPENAI_API_KEY` environment variable is set, it takes priority. `jarvis key --remove` deletes the key.

**Or use your ChatGPT plan (Plus etc.) instead of, or together with, a key.** This uses the official "Sign in with ChatGPT" for open-source local tools. You choose the connection in `config.toml` → `[ai] connection`; JARVES never switches it on its own:

| `connection` | Text answers (+ Skills, web search where your plan allows) | Voice | Billed to |
|---|---|---|---|
| `api_key` (default) | GPT-6 on your API key | GPT-Live (premium) | API key |
| `chatgpt` | your ChatGPT plan | local push-to-talk + local voice (the plan has no live voice/audio) | ChatGPT plan only |
| `combined` | your ChatGPT plan | GPT-Live (premium) | text → plan; voice → API key |

```powershell
uv run jarves chatgpt login      # opens your browser; you approve "plan usage" for JARVES
uv run jarves chatgpt status     # who is connected, which models your plan offers
uv run jarves chatgpt logout     # revokes and deletes the tokens
```

The sign-in tokens stay only in `%LOCALAPPDATA%\jarves\chatgpt\` and are never written to logs. JARVES doesn't see your ChatGPT chats, memories or files. When the plan limit is reached, JARVES says so and uses offline commands only; it does **not** switch to the API key.

Without a key or ChatGPT, JARVES runs **offline**: commands only (music, time, memory, status), local Whisper `small` push-to-talk and the robotic local voice.

## Run

**Talk:** say **"Hey Jarvis"**, then your question. It is detected on this PC and nothing is sent until it hears that. You can also hold Space, or type. The bar under the face shows the mic level; if it stays empty, check Windows Settings → Privacy & security → Microphone.

**Terminal logs:**
- `jarvis --log-level debug` sets the level: `error`, `warning`, `info` (default), `debug` or `verbose`.
- `jarvis --module voice,wake` shows only those modules; `--hide link` hides one.
- `jarvis logs -f --module ai` follows the log of a JARVES that is already running.

The modules are `core`, `voice`, `live`, `realtime`, `wake`, `ai`, `chatgpt`, `skills`, `music`, `link`, `tts`, `budget` and `device`. Errors always show.

**Simplest:** type `jarvis` in any terminal (`C:\work\jarvis` is on your PATH). The first time, it asks how to connect and signs you in. `jarvis setup` changes that choice and `jarvis stop` stops JARVES. Any other word is passed to the Core, e.g. `jarvis status` or `jarvis chatgpt status`.

Manual steps:

| Step | Command |
|---|---|
| 1. Core | `core\run_core.cmd` (or `uv run jarves run`) |
| 2. Face | `godot\run_face.cmd` (face display) or `godot\run_viewer.cmd` (3D twin with face + assistant panel) |
| 3. Talk | **Hold Space** or the Talk button. With a key, a tap starts a GPT-Live conversation and another tap ends it. Or type in the box. |
| Stop | `uv run jarves stop` |

## YouTube Music (Pear Desktop)

1. Use Pear Desktop 3.12.0 from [github.com/pear-devs/pear-desktop](https://github.com/pear-devs/pear-desktop/releases). A verified portable copy is in `%LOCALAPPDATA%\Programs\PearDesktop-3.12.0\app\YouTube Music.exe`.
2. Sign in to YouTube Music inside Pear. This is your own action; JARVES never touches your login.
3. In Pear, enable Plugins → **API Server**. This is already enabled on this PC, on 127.0.0.1:26538 with approval required.
4. Run `uv run jarves music pair` and click **Allow** in Pear's dialog. This is needed once.
5. Say or type "נגן את שלמה ארצי". JARVES reports "playing" only when Pear confirms the track is playing.

## Troubleshooting (works without network)

| Need | Command |
|---|---|
| What's wrong now | `uv run jarves status` (live health, or the last snapshot marked **stale** if the Core is down) |
| Logs | `%LOCALAPPDATA%\jarves\logs\jarves.log` (JSON lines, UTC, component/code/correlation ID, rotated 5 × 1 MB, secrets/transcripts redacted) |
| Support bundle | `uv run jarves diag` (zip with redacted logs + health + config; review before sharing) |
| Memory | `uv run jarves memory list` / `delete <id>` / `clear`. You can also say "תשכח את הכל", which asks for confirmation. |
| One-shot test | `uv run jarves ask "מה השעה"` · `uv run jarves say "שלום"` · `uv run jarves music status` |

**Config:** optional `%LOCALAPPDATA%\jarves\config.toml` ([template](config.example.toml)). Unknown keys, secrets or a non-loopback host are rejected with a clear message.

**Privacy:**
- The mic opens only while you talk.
- Software mute is **not** a hardware privacy switch.
- No audio or transcripts are written to disk.
- Memory holds only what you ask JARVES to remember.
- The Agents SDK trace upload is disabled.

## Tests

`uv run pytest -q` runs 67 offline tests: units, the Core over a real WebSocket, fault injection, GPT-Live delegation and Sign in with ChatGPT (mock OAuth/Responses servers). Paid APIs are never called. CI: [jarves-checks](../.github/workflows/jarves-checks.yml) runs them on Ubuntu and Windows on every push.
