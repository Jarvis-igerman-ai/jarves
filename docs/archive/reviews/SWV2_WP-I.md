# SW-V2 WP-I: feature switches (#17)

## What changed
- **New `core/src/jarves/features.py`.** A catalog of features plus a `Features` state object. Each feature has
  two states:
  - **ENABLED** is the user's switch. It is saved in `<state_dir>/features.json` with an atomic write. A corrupt
    file is renamed to `.bad.json` and the defaults are used.
  - **AVAILABLE** comes from a cheap probe: Pear reachable, API key, Google token, camera, wake-word package, mic or
    speaker health. Results are cached for 15 s.
  - `active(id)` is true when the feature is both enabled and available.
  - `needs` can name other features, and those must be active too (face recognition needs the camera).
- **Built-in features.** music, web_search, background_tasks, memory_curator, memory_llm_pass, diagnosis_llm_pass,
  google_calendar, gmail_read, gmail_send, camera, face_recognition, camera_when_away, wake_word, greeting,
  chimes, dashboard, workspace_agent_edit, voice_change. The privacy ones default **off**: camera,
  face_recognition, camera_when_away and gmail_send. Their risk is `high`.
- **One source of truth, backwards compatible.** The old config.toml keys still work, but now they only set the
  switch's default: `voice.wake_enabled`, `voice.greeting`, `voice.chime`, `ai.web_search`, `ai.tasks_enabled`, and
  `music.provider = "none"`. A switch in features.json always wins.
  - `apply_to_config` copies the switches back onto the loaded Config, so the existing Core code
    (`cfg.voice.chime`, `cfg.ai.web_search`, ...) follows them without any other change.
  - The original config defaults are cached before that copy, so `reset` returns to config.toml.
  - The old `memory.db` settings `override:curator.auto` and `override:curator.llm` are imported once.
  - After that, the Settings Skill keys `curator.*` read and write the switches.
- **Skill registry gate** (`skills/__init__.py`, small hook). `SkillRegistry.gate(skill, tool)` hides the tools of
  inactive features from `tools()` and `get()`. It also refuses their calls with `FEATURE_OFF` and the reason,
  because a model may still hold an older tool list. A broken gate never hides tools.
- **Truthful self-description.**
  - TOOLS.md gets a "Features" section listing what is off or unavailable, and why. It is regenerated on every
    change.
  - GPT-Live's instructions get a short note of the same (`features.attach_live`), so he can say
    "אין לי מצלמה מחוברת" without delegating.
- **Voice tools.** `features_list` (read) and `feature_set` (action).
  - Turning a feature off is always allowed.
  - Turning a low-risk feature on is allowed directly.
  - Turning a medium- or high-risk feature on needs the on-screen approval (`registry.confirm`, the face's confirm
    dialog). If it is declined or there is no screen, nothing changes and the dashboard shows a "JARVES was asked"
    request.
  - The dashboard and CLI are the user, so they can switch anything.
- **CLI:** `jarvis features [list | on ID | off ID | reset ID]`. `jarvis dashboard` refuses to start when the
  dashboard switch is off.
- **Dashboard.** Routes `GET /api/features` and `POST /api/features/<id>` (`{"enabled": bool}` or
  `{"reset": true}`) are added through the existing plugin point (`PLUGIN_MODULES` + `dashboard_routes`).
  - There is a new **Features** page: switch, Hebrew and English title, active/off/unavailable badge with the
    reason, risk, and when the change applies. A high-risk switch asks for a browser confirm first.
  - Screenshot: [swv2_features/features_page.png](../../reviews/swv2_features/features_page.png). It is a headless Edge capture
    on a throwaway data dir, not a hands-on check.
- **When a change takes effect:**
  - *Live, at the next tool list or turn:* tool gating, chimes, greeting, curator, and the wake word (the loop
    releases the mic when it is switched off, and switching it on starts the listener if it was never set up).
    The AgentBrain rebuilds its tool list on the next request.
  - *Next conversation:* web search for GPT-Live, because tools and hosted search are fixed per session.
  - *Next Core start:* music or background tasks switched on when they were not set up at start.
  - A change made in another process (dashboard, CLI) reaches the running Core within 20 s: the health loop calls
    `features.poll()`. Gated tool calls see it within 1 s, because the file mtime is checked at most once a second.

## Research / choice
I looked at OpenFeature (the CNCF feature-flag standard; Python SDK) and hosted flag services. For one device with
local switches, a remote flag provider is the wrong shape: it needs a network and a vendor, and still gives no
hardware availability. I kept OpenFeature's useful idea, an evaluation that reports a *reason* (here `source`:
user/config/default, plus the probe reason). It is implemented in about 500 lines of stdlib with no new dependency.

## Tests
- `core/tests/test_features.py`: 19 offline tests, silent, no hardware, $0. They cover:
  - defaults, enabled vs available, dependencies, the camera reported by a Skill, Google token and connector
  - persistence and a corrupt file, legacy config precedence and reset, a change made by another process
  - curator migration, Settings delegation, registry gate (live hide and refuse), a broken gate
  - voice-tool risk policy, truthful text, the third-party `register` API, the CLI, dashboard routes
  - a real Core following the switches (TOOLS.md, music gating, chime mirror, wake word off)
- Full suite: `python -m pytest core/tests -q`: **455 passed, 1 skipped**.
- Real API cost: **$0**. No GPT-Live calls were needed.

## Integration hooks (files owned by other packages)
- `skills/__init__.py` (WP-D): the `gate` attribute, `_gated`, and the filter in `tools`, `get` and `call`.
- `skills/settings.py` (WP-D): the `Settings.features` hook. `curator.*` get, set and reset go through the
  switches. Turning on a medium-risk one from a conversation is refused and points to `feature_set`.
- `app.py`: creates and attaches Features, imports the old settings, copies the switches onto cfg, sets the
  registry gate, registers the features Skill (`feature_set` is allowed by policy), regenerates TOOLS.md once after
  all Skills, uses the curator switch, polls in the health loop, checks `voice.wake_enabled` in
  `_wake_should_listen`, calls `feat.attach_live`, adds `_feature_changed`, and puts `features_off` in
  STARTUP_SUMMARY.
- `dashboard.py` (WP-B): `"jarves.features"` added to `PLUGIN_MODULES`. `dashboard_static/` gets a nav link, the
  `pageFeatures` page and 2 CSS rules.
- `cli.py`: `features` subcommand and the dashboard switch check. `config.example.toml`: a header note.

## Open questions
1. **Music when Pear is closed.** After the health loop marks Pear unavailable (within about 20 s), the music tools
   are hidden and refused with "Pear Desktop is not running". Should JARVES instead keep the tools and offer to
   start Pear?
2. **Medium-risk switches by voice** (Gmail read, calendar, persona edit, memory LLM pass) also need the on-screen
   approval. Should medium be direct like low?
3. **`diagnosis_llm_pass`** is a switch only. diagnose.py has no LLM pass yet (WP-B), so it should read
   `features.active("diagnosis_llm_pass")` when one is added.
4. Google, camera and face connectors do not exist yet. Their probes say "not connected" or "not installed". A
   future connector registers its Skill under the names listed in the catalog (calendar, gmail_* tools, camera,
   faces) and reports a `camera` health component.
