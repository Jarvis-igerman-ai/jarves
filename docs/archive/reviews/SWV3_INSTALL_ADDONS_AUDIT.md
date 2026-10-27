# SW-V3 audit: does the installer ship every add-on, and can the user choose them? (Issue #17)

Owner question: *"the installer will also install all add-ons like Google, camera - check them all, right? choose
what to enable and what not, choose the name... everything right?"*

**Short answer.** Before this change: the code of every add-on was already in the installer payload (the build
installs the `voice`, `camera` and `tray` extras from `uv.lock` plus the jarves wheel, which carries the YuNet model,
the guides, the dashboard and the wake-word models through `pyopen-wakeword`), **but the installer's setup only asked
"how do I connect" and for the key**. Names, wake phrase, voice and the ON/OFF choice of each add-on were not part
of setup, and nothing checked the *installed* app for missing add-on parts. Now:

- `jarvis setup` (the installer's last page, the first `jarvis` run, the Start-menu "JARVES setup" entry) is a full
  first-run wizard: language → connection + key / ChatGPT sign-in → display name, spoken name, how to address you →
  wake phrase (honest) → voice → **every registered add-on ON/OFF, grouped**, with explanation, cost/privacy note
  and a live availability probe → privacy mode → monthly API budget → Google now-or-later → summary + next steps.
- Silent installs: `JARVES-Setup.exe /VERYSILENT /SETUPANSWERS=answers.json` (and `jarvis setup --answers FILE`,
  `--defaults`). No secrets in the answers file (refused); the API key only from the environment on request.
- `jarvis setup --check-addons` checks each add-on's code, wheels, models and data in the installed app;
  `packaging/smoke_windows.ps1` (CI installer job) now runs it, applies an answers file through the installer and
  checks that a `--defaults` re-run changes nothing.
- The dashboard has the same page: **Setup & add-ons** (`#/setup`), built from the same data (`GET /api/setup`),
  reusing the Features switches, the Identity & voice page and the Guides.

Evidence: `core/tests/test_setup_wizard.py` (scripted runs, fake probes, Hebrew/English, safe defaults, idempotent
re-run, resume, bundle check, CLI, dashboard view); local `jarvis setup --check-addons` on the dev venv = all OK; a
locally built jarves wheel contains `camera/models/face_detection_yunet_2023mar.onnx`, `dashboard_static/setup.js`,
`guides/data/*.toml`, `setup_guide.md` (52 data files). The installed-app check runs in CI (not run on this PC: no
Inno/Godot build here, disk nearly full).

## Per add-on

"In payload" = verified by `--check-addons` imports/files (BUNDLE in `setup_wizard.py`). Default = the catalog
default (`jarves.features`); the wizard's first-run recommendation equals it, except SAFE_OFF items are forced OFF
even if something else switched them on.

| Add-on (feature id) | In payload | External prerequisite / consent | Default | Notes / gap |
|---|---|---|---|---|
| Music (`music`) | `skills.music`, httpx | **Pear Desktop** (separate free app, not bundled) + API Server plugin + one-time `jarvis music pair` | ON | Wizard shows "not running / not installed" + download link; installer has the unticked "Get Pear Desktop" page |
| Web search (`web_search`) | openai-agents | OpenAI (key or ChatGPT plan) | ON | — |
| Background tasks (`background_tasks`) | `jarves.tasks` | OpenAI **API key** (plan alone not enough) | ON | Shows "needs key" in the summary |
| Learn from conversations (`memory_curator`) | `curator` | none (local rules) | ON | — |
| Memory LLM pass (`memory_llm_pass`) | `curator` | API key; one paid request per conversation | OFF | — |
| Self-diagnosis LLM pass (`diagnosis_llm_pass`) | `diagnose` | API key; budgeted | ON | owner decision (SW-V2) |
| Google Calendar read (`google_calendar`) | `google_auth`, `google_setup`, `skills.google` | Google account; **Google Cloud console setup** (`jarvis google start`, ~10 min) because CI builds ship no app-owned OAuth client | ON | Wizard offers to launch `jarvis google start` now (interactive only) or lists it under next steps |
| Calendar add/update (`calendar_write`) | same | separate events-permission consent | **OFF (safe)** | never deletes |
| Gmail read (`gmail_read`) | same | Google consent | ON | — |
| Gmail send (`gmail_send`) | same | Google consent | **OFF (safe)** | explicit yes needed |
| Google Contacts (`google_contacts`) | + `skills.contacts` | separate Contacts consent + People API | OFF | — |
| Camera (`camera`) | OpenCV (camera extra), `camera.service`, **YuNet model** | a **webcam**; Windows camera privacy setting | **OFF (safe)** | probe never opens the sensor ("found: webcam X" / "no camera connected") |
| Face recognition (`face_recognition`) | OpenCV, `camera.faces` | camera ON; **SFace model 38.7 MB downloaded on first switch-on** (official URL, SHA256-pinned, `feature_setup.prepare`) | **OFF (safe)** | Not in the payload by design (size); the wizard runs the same prepare step as `jarvis features on` |
| Camera when away (`camera_when_away`) | OpenCV, `camera.presence` | camera ON | **OFF (safe)** | — |
| Wake word "Hey Jarvis" (`wake_word`) | sounddevice (PortAudio DLL in wheel), pyopen-wakeword + `models/hey_jarvis.tflite` + TFLite DLL | **microphone** (Windows mic privacy setting) | ON | built-in phrase only |
| Custom wake phrase (identity, not a switch) | `voice.wake_train` (numpy) | user trains on this PC: `jarvis wake train "<phrase>" --say "<Hebrew>"` | Hey Jarvis | Wizard says so honestly and lists trained models to pick; not verified on the Pi |
| Greeting (`greeting`) | `voice.tts` | speaker | ON | — |
| Chimes (`chimes`) | sounddevice | speaker | ON | — |
| Voice change (`voice_change`) + voice choice | `skills.settings`, `agents.realtime` | live voice = API key | ON | Wizard lists the voices of `identity.voice_catalog`; another model's voices need explicit cost approval; no fake preview |
| Dashboard (`dashboard`) | `dashboard`, `dashboard_static/*` incl. `setup.js` | none (loopback) | ON | — |
| Dashboard on home Wi-Fi (`dashboard_lan`) | `dashboard_auth`, `pair.html` | pairing code + login on the phone | **OFF (safe)** | — |
| Persona files (`workspace_agent_edit`) | `workspace`, `setup_guide.md` | none | ON | "How to address you" from the wizard is one managed line in USER.md |
| Assistant name (`assistant_identity`) | `identity` | none | ON | Wizard sets display + spoken name (identity.json) |
| Appearance (`appearance`) | `appearance`; avatars in the Godot `.pck` (export filter all_resources) | none | ON | the face itself is checked by the smoke test (`-RequireFace`), not by `--check-addons` |
| Skill creation (`skill_creation`) | `skills.generated` | user reviews/approves every Skill | ON | — |
| Scheduler / reports / notes / files / alerts / autonomy | `scheduler`, `notes`, `sandbox`, `alerts`, `autonomy`; **tzdata** (base dependency; checked with `ZoneInfo("Asia/Jerusalem")`) | none | ON | No Hebrew-calendar (hebcal-type) data is used anywhere: nothing to ship |
| Usage monitor (`usage_monitor`) | `usage` | optional read-only **OpenAI Admin key** (`jarvis key --admin`) for the real bill | ON | Wizard asks the monthly API budget (API modes only) |
| Backup (`backup`) | `backup` | none | ON | — |
| Background / show-hide / tray / start at login (`background_core`, `display_control`, `tray_icon`, `autostart`) | `runtime.*`, pystray + Pillow (tray extra) | none | ON / ON / ON / OFF in the catalog | the installer task "Start JARVES when I sign in" (ticked) runs `jarvis autostart on` |
| Update check (`update_check`) | `updates` | GitHub reachable (private repo: honest "not reachable") | ON | installs only after approval |
| Setup suggestions (`proactive_help`) | `guides` + data | none | ON | — |
| Privacy mode (not a switch; `privacy.py`) | `privacy` | none | current `log.privacy` (OFF) | Wizard asks it, with the honest note that OFF keeps text on this PC only |
| Live voice / ChatGPT sign-in / Windows TTS / offline STT (shared parts) | `agents.realtime`, websockets, `chatgpt_auth` + PyJWT, winrt speech, faster-whisper | key / browser sign-in | — | The Whisper model for offline STT downloads on first use (fallback path only; not shipped) |

## Gaps found and what was done

1. **Setup did not let the user choose add-ons, name, wake phrase or voice** → fixed (wizard + dashboard page).
2. **No check that the installed app really contains each add-on** → `jarvis setup --check-addons` + smoke test.
   Every registered feature must have a BUNDLE declaration and a wizard group (test fails otherwise), so a new
   add-on cannot be advertised without being checked.
3. **Silent installs could not carry choices** → `/SETUPANSWERS=` run line in `jarves.iss` (runs only in silent
   mode; without the parameter it is a no-op).
4. Not fixable in the installer (external, documented in the wizard's "what to do next" and INSTALL_TEST.md):
   Pear Desktop install, OpenAI key, ChatGPT browser sign-in, Google Cloud console + consent, webcam/microphone
   hardware and Windows privacy permissions, SFace download (on demand, pinned).
5. Open: the CI build ships no app-owned Google OAuth client (`-GoogleOAuthClientFile` not passed), so every user
   does the Google console steps. Shipping one needs an owner decision (a Google Cloud project for JARVES).

## Not verified here

- The installed-app `--check-addons` and `/SETUPANSWERS` path run only in the CI installer job (this PC has no
  Inno Setup/Godot build cache and almost no disk space).
- Interactive wizard on a real console with Hebrew right-to-left rendering (scripted tests only).
- Raspberry Pi: no packaging yet; the wizard is pure Python.
