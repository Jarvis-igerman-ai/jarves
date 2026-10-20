# JARVES Core (Python)

The assistant brain: state, the Godot face link, DevicePort, voice, memory, Skills and music. Stack decision and sources: [SW_V1_TECH_DECISION](../docs/reviews/SW_V1_TECH_DECISION.md).

## Setup (Windows now, Raspberry Pi OS 64-bit later)

```powershell
winget install astral-sh.uv          # once (Pi: curl -LsSf https://astral.sh/uv/install.sh | sh)
cd core
uv sync --extra voice                # locked dependencies; Python 3.12 is installed by uv
```

On the Pi, also run `sudo apt install libportaudio2 espeak-ng`.

### Google Calendar and Gmail setup

**For distributed JARVES apps:** the release maintainer supplies one app-owned **Desktop OAuth client** to
`packaging/build_windows.ps1 -GoogleOAuthClientFile <private-path-to-app-client.json>`. The build writes only its
public-client registration fields into the installed package (`_google_app_client.json`), never user tokens or
the developer's private state. Do not use a personal testing client for public releases. Do not commit the JSON.
On a fresh installation, **Connect Google → choose account → approve permissions** now goes straight to Google's
browser consent flow; neither gcloud nor the seven Cloud Console steps are shown. Account tokens stay in each
user's private state directory. An explicitly imported private Desktop client takes precedence for development.

**Public release gate:** the maintainer must first supply the app-owned client, register the JARVES brand and
public homepage/privacy-policy domain, and complete Google's applicable scope verification. Gmail read/compose
are restricted scopes; transmitting restricted data through cloud AI must be disclosed and reviewed and may
require a security assessment. A local token store alone does not prove the whole data path is local. No app
client or Google approval has been provisioned by this code change, no public release has been made, and no
seven-day expiry guarantee is inferred for a bundled client. Keep the same app client registration across upgrades.
Sources checked **2026-10-19**: [Desktop public clients and PKCE](https://developers.google.com/identity/protocols/oauth2/native-app),
[restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).

**Developer / bring-your-own-client setup** (when the release has no app-owned client):
Run `jarvis google start`. Install the [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk)
and run `gcloud auth login` with the account that owns your project first. When `gcloud` is on PATH, the wizard
defaults to CLI setup: list accessible projects, choose an existing project by number or `n` to create one,
then enable Calendar/Gmail APIs. It verifies the selection again on resume; choosing a different project resets
the old project's checkmarks. Calendar-only setup enables only Calendar. Declining CLI setup, or having no
CLI installed, uses the manual browser route. No automatic CLI installation or billing setup occurs.

Consent settings, test users, scopes and the Desktop OAuth client still use Google's browser pages; sign-in
requires your approval. If CLI setup fails, the wizard stops before browser setup: check internet access,
run `gcloud auth login`, verify `gcloud projects list`, and retry. It does not print raw CLI output or credentials.
Saved progress is in the private state directory's `google/setup.json`; bounded local logs are under `logs/`
(`GOOGLE_SETUP_CLI_FAILED` identifies a failed CLI operation). No network means setup cannot finish, but saved
progress remains available for the next run. Existing connected accounts only run the existing self-check.

CLI choice checked **2026-10-19** against the official [project listing](https://docs.cloud.google.com/sdk/gcloud/reference/projects/list),
[API enablement](https://docs.cloud.google.com/sdk/gcloud/reference/services/enable) and
[Desktop OAuth setup](https://developers.google.com/workspace/guides/create-credentials) documentation.

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

**Two ways to talk** (`[voice] activation` in `config.toml`; this PC uses `conversation`):

| Mode | What happens |
|---|---|
| `conversation` (connected on open) | `jarvis` opens **already connected**: the greeting is spoken by the live session (~0.5 s after the face appears) and you just talk. One conversation stays open (no time limit, only the daily-minutes budget) and ends only after `idle_disconnect_s` of quiet (default 3 min). If the server closes it while you are talking, JARVES reconnects by itself. After an idle end, say **"Hey Jarvis"**: a soft chime sounds at once, a second chime ~1 s later means "connected, talk now" (no spoken delay). |
| `wake_word` (silent until called) | Silent; says hello once at start (cached after the first run, so it is instant). Say **"Hey Jarvis"** (chime, then talk). |

`voice.chime = false` turns the chimes off. The wake phrase is trained for **"Hey Jarvis"**. If it does not catch your voice, run **`jarvis wake test --apply`** (live score bar, saves a threshold for your voice and mic). **`jarvis doctor`** checks the key, ChatGPT sign-in, microphone (warns on silence), wake model and Pear and says what to fix; `jarvis doctor --live` also opens one real GPT-Live session (~1 cent, asks first).

**Face reactions and screen info:** the face laughs at jokes, cries at sad news and shows surprise or affection (from what is said); a small pill on the lower arc shows what JARVES is doing ("חושב…", "מחפש ברשת…", the song playing, "מתחבר…"). The corner dot shows the connection: green ready, blue live, amber connecting/degraded, red offline, grey muted.

**Silent tests (no speakers, no mic, safe at night):** `uv run python tools/silent_live.py` runs a scripted 12-turn Hebrew conversation against the **real GPT-Live** through a virtual mic/speaker and prints dead air and pauses per turn (`--idle-check` also tests the idle disconnect; costs about $0.05 per voice minute). `GODOT_EXE=<godot console exe> uv run pytest tests/test_face_protocol_godot.py` feeds the real headless face every kind of message, including broken ones.

The main status and face show local wake, connecting, listening, thinking, speaking, muted and disconnected states. **Options** reveals diagnostics/captions/software mute and simulator inspection controls. No Space/Talk button or typing box is needed or displayed. The face mouth follows actual streamed speaker levels; silence closes it, including when updates stop. Vesper is the selected masculine British GPT-Live voice; Cedar is the compatible Realtime/read-aloud fallback. Hebrew voice quality remains subject to listening review.

**Close the window to stop:** the desktop sends an authenticated shutdown request, ends live voice and speaker playback, and the Core exits normally. The launcher returns to the terminal without Ctrl+C / "Terminate batch job?". Repeated-launch log followers finish on `CORE_STOPPED`; ordinary connection loss still reconnects instead of being treated as a close.

**Startup:** `jarvis` first opens a lightweight **loading screen with a progress bar**. The 3D room is loaded only after the Core is initialized and, for mode 2, GPT-Live is connected and actual greeting audio has arrived. Greeting audio is held in the existing bounded memory queue; the physical mic and speakers are not opened until the room connects. In `conversation` mode the greeting comes from the live session (no separate request). If voice cannot become ready, the room stays closed and the terminal reports the failure. Closing even the loading window cancels startup and stops the owned Core.

Progress advances at startup milestones; ETA text is removed at the user's request. Wake-mode startup waits for actual local microphone frames, not merely the configured detector. Its hello is one short cloud speech request, cached on disk (`cache/`, fixed sentence only, never your speech or conversation). Timing-only cache metadata remains private. Hardware can consume the same startup/status events, but Pi validation is still pending.

**Terminal logs:**
- `jarvis --log-level debug` sets the level: `error`, `warning`, `info` (default), `debug` or `verbose`.
- `jarvis --module voice,wake` shows only those modules; `--hide link` hides one.
- `jarvis logs -f --module ai` follows the log of a JARVES that is already running.

The modules are `core`, `voice`, `live`, `realtime`, `wake`, `ai`, `chatgpt`, `skills`, `music`, `link`, `tts`, `budget` and `device`. Errors always show.

**Simplest:** type `jarvis` in a terminal with `C:\work\jarvis` on your PATH. This shows loading progress, then the **room with the actual CAD-derived model and live face**, and runs the Core with terminal logs. The first time, it asks how to connect and signs you in. `jarvis setup` changes that choice and `jarvis stop` stops JARVES. Any other word is passed to the Core, e.g. `jarvis status` or `jarvis chatgpt status`. Manual `uv run jarves run --desktop` uses the same readiness gate; `uv run jarves run` remains the headless/manual Core.

Cloud voice skips offline recognition model loading. Options are collapsed by default. Cloud reply/greeting speech streams to one output device stream as audio arrives; local speech engines remain buffered fallbacks. Each run appends to the rotated log below. `CORE_READY.ms`, `SPEECH_SYNTH_START`, `SPEECH_FIRST_AUDIO.ms`, `SPEECH_SYNTH_READY.ms` and `SPEECH_PLAY_START` separate startup, first-audio and full-generation/playback delays. Cloud connection/first-audio latency remains provider-dependent. Launcher errors before Core starts appear in the terminal. In mode 1, `WAKE_LISTENING` confirms mic frames are arriving and `WAKE_HEALTH` reports bounded frame/level/score counters every 15 s, without saving audio.

**Always on (SW-V2 M1):** `jarvis start` runs JARVES in the background with no console or window: a tray icon (Dashboard, Show/Hide, Mute, Pause alerts, Status, Diagnostics, Quit) and the local "Hey Jarvis" wake word, which opens no cloud connection while idle. Closing the window only hides JARVES; say "show yourself" / "hide yourself", or use `jarvis show` / `jarvis hide`. `jarvis status --brief` shows the four separate states (Core, window, mic mute, alerts paused); `jarvis stop` or Quit stops it. `jarvis autostart on|off` starts it invisibly at Windows login (HKCU Run key; Pi: systemd user service, unverified, issue #20). Only one Core per user can run.

**Profiles (several users + a test profile, Issue #17):** each profile is its own state dir with its own memory, conversations, costs and budget, persona files, settings and switches, name / wake phrase / voice, `secrets.env`, Google / ChatGPT sign-ins, privacy state and channels. The default profile is today's folder (`%LOCALAPPDATA%\jarves` or `JARVES_HOME`); others live in `<that folder>\profiles\<name>`. `jarvis profile list | show | create <name> [--from <name> --copy-memory --copy-secrets --test] | use <name> | delete <name> | export <name> [--out zip]`; run one with `jarvis --profile <name> [command]` (or `JARVES_PROFILE`). Each profile has its own instance lock, link port (picked free at create, checked again at `run`) and dashboard login, so two profiles can run side by side. `jarvis profile create test` makes the **test profile**: AI offline, voice off, music none, sensitive switches off, $10 monthly budget, red TEST PROFILE banner on the dashboard and the face. A non-default profile never inherits the parent's API keys from the environment; `--from` never copies keys (only with `--copy-secrets`) or sign-ins; delete asks for the profile name typed back. By voice: "תעבור לפרופיל של דנה" asks for approval **on the screen**, then JARVES restarts in that profile (no speaker recognition: say the name). Separation is by the app, not an OS boundary: use separate Windows accounts for full separation. Report: `docs/reviews/SWV3_PROFILES_REPORT.md`.

**Explore skills:** ask "What can you do?" or "Show me how to use your music skill." The AI is instructed to explain only the tools it has, check current service status and offer one example or walkthrough at a time. Current tools cover time, status, explicit personal memory and music control; web search depends on the active AI connection. Gmail/Calendar and other unimplemented integrations are not claimed.

Manual steps:

| Step | Command |
|---|---|
| 1. Core | `core\run_core.cmd` (or `uv run jarves run`) |
| 2. Face | `godot\run_face.cmd` (face display) or `godot\run_viewer.cmd` (3D twin with face + assistant panel) |
| 3. Talk | Say **Hey Jarvis**, wait for the listening cue, then ask your question. |
| Stop | `uv run jarves stop` |

## YouTube Music (Pear Desktop)

1. Open it with **`jarvis music open`**. It uses Pear Desktop 3.12.0 from [github.com/pear-devs/pear-desktop](https://github.com/pear-devs/pear-desktop/releases). A verified portable copy is in `%LOCALAPPDATA%\Programs\PearDesktop-3.12.0\app\YouTube Music.exe`.
2. Sign in to YouTube Music inside Pear. This is your own action; JARVES never touches your login.
3. In Pear, enable Plugins → **API Server**. This is already enabled on this PC, on 127.0.0.1:26538 with approval required.
4. Run `uv run jarves music pair` and click **Allow** in Pear's dialog. This is needed once.
5. Say or type "נגן את שלמה ארצי". JARVES reports "playing" only when Pear confirms the track is playing.

## Troubleshooting (works without network)

### Runtime setup and feature references

The runtime workspace includes read-only generated references: `SETUP.md`, built from
[`src/jarves/setup_guide.md`](src/jarves/setup_guide.md), and `FEATURES.md`, built from the actual
feature-switch catalog. Setup guidance covers JARVES installation, AI connections, Google, music/Pear,
voice and wake word, camera, dashboard, Skills, updates, backups and offline diagnostics. These are
references for guided help; they do not add general installer, shell or browser tools.

The backend receives bounded summaries and can fetch the full documents with the existing
`workspace_read` tool, following `next_offset` for pagination. The voice frontend delegates setup/help
questions to that backend. Generated references refresh at startup; `FEATURES.md` also refreshes when
switches change. Existing personal workspace documents are preserved. Restart a running Core to load
changed source. Use `jarvis --help` (or `jarvis -h`) for all subcommands and `jarvis features` for switches.

`jarvis features on ID` checks readiness before changing the switch, including when it is already on.
Source checkouts automatically install missing locked camera/wake dependencies and the verified official
SFace model when needed. Stop Core first if installation is required. Existing extras are preserved;
installed apps with missing bundled dependencies need repair/update. Hardware, Google consent, Pear
startup/pairing and per-person face enrollment still require the user. A readiness probe never opens sensors.
Calendar editing consent: `jarvis google login --calendar-write`, then `jarvis features on calendar_write`.

### Text requests while JARVES is stopped

`jarvis execute "add a calendar event tomorrow at 10 called Dentist"` runs one natural-language text
request and exits. No running Core, face window, microphone, speaker or camera service is needed or started.
It uses your configured AI connection and existing request budget, saved memory/relationships, Google
connection, feature gates and terminal confirmations. It does not acquire missing account permissions.
Use `jarvis execute --interactive "add a calendar event tomorrow"` for follow-up details in the same
conversation; enter an empty line to finish. In a single request, include the needed details or rerun with
the requested clarification. Exit status describes request processing; read the response for actual tool results.

Supported text-mode tools include Calendar, Gmail, Contacts, local memory, clock, music through an existing
Pear service, workspace/settings and feature inspection. Runtime camera capture, voice, face, scheduler
and background tasks require their normal running services and are not started by this command. Feature
activation remains `jarvis features on ID`. The older `jarvis ask` has a smaller tool set; use `execute` for
natural-language Google actions. Text-mode capability references stay in memory and preserve Core's
generated tool files. No new model, provider or dependency was selected.

### Contacts and event invitations

Say “remember my wife's email is …” to save an explicit relationship/address in ordinary local memory.
JARVES uses `contact_lookup` before addressing mail or inviting someone; multiple matches require your choice.
The remembered contact survives restarts and can be removed through the existing memory controls.
CLI: `jarvis contacts remember "my wife" --email spouse@example.com` (example only),
`jarvis contacts lookup "my wife"`, and `jarvis contacts forget MEMORY_ID`.

Google Contacts search is separate and off by default. Enable **People API** in the app's Cloud project,
run `jarvis google login --contacts`, approve read-only Contacts access, then
`jarvis features on google_contacts` and restart Core. `jarvis contacts search "Dana"` returns bounded
name/email candidates; it does not import, edit contacts or infer relationships. Existing Google grants are
requested again during incremental consent; actual returned grants determine readiness.

Calendar tools can invite guests to a new or selected existing event after exact read-back confirmation.
Sharing preserves existing guests, requires a choice for recurring events, and returns Google's event link
when available. Emailing that link uses the separate confirmed Gmail draft/send flow.
CLI: `jarvis calendar create --help`, `jarvis calendar share --help`, `jarvis mail draft --help`.
`mail draft --send` offers confirmation and sending in one process; without it only a draft is created.
No whole-calendar access is granted, and API acceptance does not prove delivery or attendance.

Official API references checked **2026-10-19**: [People searchContacts](https://developers.google.com/people/api/rest/v1/people/searchContacts)
(read-only scope, warmup and names/email mask), [Calendar invitations](https://developers.google.com/workspace/calendar/api/guides/create-events)
(`sendUpdates=all`), [event links](https://developers.google.com/workspace/calendar/api/v3/reference/events#resource).
Existing `httpx` REST integration is reused; no new dependency or model selected. Live contact lookup/invitation
delivery remains unverified until the user authorizes account-bound tests.

| Need | Command |
|---|---|
| What's wrong now | `uv run jarves status` (live health, or the last snapshot marked **stale** if the Core is down) |
| Logs | `%LOCALAPPDATA%\jarves\logs\jarves.log` (JSON lines, UTC, component/code/correlation ID, rotated 5 × 1 MB, secrets/transcripts redacted) |
| Support bundle | `uv run jarves diag` (zip with redacted logs + health + config; review before sharing) |
| Memory | `uv run jarves memory list` / `delete <id>` / `clear`. You can also say "תשכח את הכל", which asks for confirmation. |
| Dashboard | `jarvis dashboard` opens a local page (this computer only, one-time key in the link): every conversation with what you said / what he said, tools, reply delays, silences and cost; Costs; Issues; Memory; Skills & tools with health; Workspace files; Settings (read-only). Dark mode follows Windows or the Theme button. |
| Cost | `jarvis cost` (per day and per conversation; **estimates** from `[pricing]`: GPT-Live $0.05/min + backend tokens, checked 2026-10-19; your OpenAI bill is the truth) |
| Self-diagnosis | Runs after every conversation; `jarvis diagnose` on demand (`--import-log` first reads older conversations from jarves.log). Finds stalls, dead air, confusing fillers, misunderstandings, tool/backend failures, early-response drops, false wakes, high cost. |
| One-shot test | `uv run jarves ask "מה השעה"` · `uv run jarves say "שלום"` · `uv run jarves music status` |

**Config:** optional `%LOCALAPPDATA%\jarves\config.toml` ([template](config.example.toml)). Unknown keys, secrets or a non-loopback host are rejected with a clear message.

**Privacy:**
- Mode 1 listens locally for the wake word; mode 2 opens the mic for the live conversation after the greeting. Software mute stops capture, and closing the window requests Core/session shutdown.
- Software mute is **not** a hardware privacy switch.
- No audio is written to disk. Conversation history (`store.db`: what was said, tools, timings, issues) follows `log.privacy`: with privacy on only lengths and timings are kept. `[history] enabled = false` turns it off; `keep_days` (default 90) bounds it. `jarves diag` never includes it.
- Memory holds only what you ask JARVES to remember.
- The Agents SDK trace upload is disabled.

## Tests

`uv run pytest -q` runs 67 offline tests: units, the Core over a real WebSocket, fault injection, GPT-Live delegation and Sign in with ChatGPT (mock OAuth/Responses servers). Paid APIs are never called. CI: [jarves-checks](../.github/workflows/jarves-checks.yml) runs them on Ubuntu and Windows on every push.
