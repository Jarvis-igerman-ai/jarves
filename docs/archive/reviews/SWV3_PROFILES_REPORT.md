# SW-V3 Profiles: several users + a test profile (20 Oct 2026) — #17

Branch `v3-profiles` (from `origin/main` 882ae27). Owner request: JARVES supports several users and a test profile that never touches real data.

## Design
- **A profile is one state dir.** JARVES already keeps everything in the state dir, so a profile is just "which state dir". That covers memory.db, store.db (conversations, costs), the workspace files (SOUL / AGENTS / USER / MEMORY), config.toml, features.json, identity.json (name, wake phrase, voice), secrets.env, the google/ and chatgpt/ tokens, the privacy flag, telegram/ and the other channels, logs and backups.
  - The `default` profile is today's location, unchanged: `JARVES_HOME`, else `%LOCALAPPDATA%\jarves`.
  - Other profiles live in `<base>/profiles/<name>/`, with a `profile.json` holding `{name, kind, created, port, from, copied}`.
- **Choosing a profile.** The first match wins:
  1. `jarvis --profile <name> …` (it sets `JARVES_PROFILE` for this process and its children),
  2. the `JARVES_PROFILE` environment variable,
  3. `jarvis profile use <name>` (stored in `<base>/profiles.json` `active`),
  4. `default`.

  Both Windows launchers (`jarvis.cmd`, `packaging/windows/jarvis.cmd`) accept `--profile NAME` as the first argument.
- **One Core per profile.** Each profile has its own `core.lock`. Each also gets its own link port, picked at create time from 8766–8865: not used by any profile and bindable at that moment. `jarvis run` checks again and refuses a port that another profile is configured on.
  - The dashboard always takes a free port. Its login cookie is named per profile (`jarves_dash` for default, `jarves_dash_<name>` for others), so two dashboards on 127.0.0.1 no longer log each other out.
  - Autostart has one entry per profile: Run key value `JARVES-<name>` and unit `jarves-<name>.service`, with `--profile` in the command.
  - The tray tooltip shows `JARVES [<name>]`.
- **Test profile.** `jarvis profile create test`, or `--test` for any name, creates it with:
  - `ai.provider = offline`, `voice.mode = off`, `music.provider = none`, smaller daily limits and `history.llm_review = false`;
  - sensitive and outward switches off (camera, Gmail, calendar write, Telegram, Home Assistant, LAN dashboard, autostart, tray, web search…);
  - a separate monthly budget of $10 (the owner's test-campaign budget), and a USER.md that says it holds no real data;
  - no first-run wizard;
  - a red **TEST PROFILE** banner on the dashboard (every page) and on the Godot face. The Core's `status` snapshot now carries `profile` = {name, kind, test}.
- **Commands:**
  - `jarvis profile list | show | create <name> [--from <name> --copy-memory --copy-secrets --test] | use <name> | delete <name> [--confirm NAME] | export <name> [--out zip]`
  - Export is a verified backup archive (backup.py rules: no credentials). Restore it with `jarvis --profile <name> restore <zip>`.
- **Dashboard.**
  - Top bar: a profile switcher. Picking another profile opens that profile's own dashboard, as its own process, port and login, in a new tab.
  - Profiles page (`#/profiles`): list, create (empty / copy settings / copy memory / test), make default, open, and delete with the name typed back. Profile management is loopback-only; paired phones are refused.
  - Usage strip, budget and costs are per profile by construction, because each dashboard reads its own memory.db and store.db.
- **Voice.**
  - `profile_list` is a read tool.
  - `profile_switch` asks for approval on the screen (`registry.confirm`, the face dialog), never a spoken yes alone, and is refused when no screen is connected. After approval it sets the active profile, starts that profile's Core (`run --background --show`, without this profile's keys in the environment) and quits the current Core after the goodbye.
  - No speaker or profile recognition in v1: the person says the profile name.
- **Registered as a feature.** `profiles`: medium risk, on by default, with:
  - capability row: Windows and Pi `implemented_unverified`, no matrix problems;
  - setup-wizard group "Profiles", bundle check (`jarves.profiles`, `profiles.js`, guide), dashboard plugin;
  - guide `guides/data/profiles.toml` (he + en);
  - Hebrew texts for about, evidence and blockers in `profiles.HE`, which merge into `hebrew.TEXT` automatically once the P0.2b `hebrew.py` is on main;
  - loaded setup knowledge (`setup_guide.md`) and the core README.

## Security
- A Core, dashboard or CLI opens only its own profile dir. Names are validated: 1–24 of `a-z0-9_-`, no Windows device names or paths. Delete refuses symlinks and anything outside `<base>/profiles`.
- A non-default profile **drops secret environment variables inherited from its parent** (`OPENAI_API_KEY`, `OPENAI_ADMIN_KEY`, `TELEGRAM_BOT_TOKEN`, `HOME_ASSISTANT_TOKEN`) and loads only its own `secrets.env`. `JARVES_PROFILE_SECRETS_FROM_ENV=1` is the explicit opt-in.
- `create --from` copies only settings: config (with a new port), features, identity (its trained wake models are copied and repointed), setup state, the persona files (SOUL / AGENTS / IDENTITY) and memory.db settings.
  - The budget counters are reset.
  - Memories are deleted and the file vacuumed unless `--copy-memory` is given (that also copies USER.md, MEMORY.md and the daily notes).
  - It **never** copies secrets.env (only with `--copy-secrets`, terminal only, the dashboard refuses it), Google / ChatGPT tokens, Telegram pairing, link_token, paired phones, Pear pairing, faces.db, conversations, logs or backups.
- Delete needs the name typed back. It refuses the default profile, a profile whose Core is running, and (on the dashboard) the dashboard's own profile.
- The default profile's backup and diagnostic inventory skip `profiles/` and `profiles.json`, so other people's data is not in your backup.
- **Limit (truthful):** this is application-level separation. All profiles belong to the same Windows user, which can read every folder. Separate Windows accounts remain the strong boundary between people.
- Inside one process the log module is a singleton. Production runs one Core per process. The in-process two-Core test therefore checks the data stores, and the process test checks the logs.

## Best-in-class check (20 Oct 2026)
The per-profile state dir chosen by `--profile` or an environment variable, with ports and service names scoped per profile, is the same pattern as the OpenClaw CLI. There, `--profile <name>` isolates state under `~/.openclaw-<name>` and scopes `OPENCLAW_STATE_DIR` / `OPENCLAW_CONFIG_PATH` plus service names. Sources are third-party documentation mirrors, not verified against the official site:
- [docs.openclaw.ai/hi/cli](https://docs.openclaw.ai/hi/cli)
- [multiple-gateways mirror](https://huggingface.co/spaces/nikhtu10/OpenClawBot/blob/main/docs/gateway/multiple-gateways.md)

No new library or model was needed: stdlib only.

## Evidence (local, focused, run one file at a time because the machine was low on RAM)
- `core/tests/test_profiles.py`: **33 passed**. It covers:
  - default location unchanged; name validation; distinct and bindable ports plus conflict detection;
  - test-profile defaults and budget; select / env / use / delete; typed confirmation; refusing to delete a running profile; one lock per profile;
  - copy without secrets / data, `--copy-memory`, `--copy-secrets`; inherited-key drop; backup and diag exclusion; export without credentials; per-profile autostart;
  - the CLI commands; the run port guard;
  - feature / guide / bundle / wizard / matrix registration and Hebrew texts;
  - dashboard login per profile (cross-cookie refused), page, create / delete / open / use and memory isolation;
  - **two real in-process Cores side by side** (memory, budget, switches, identity, link token, recall: no cross-leak; status `profile`);
  - **two real Core processes** (`python -m jarves --profile alice|test run`) at the same time: both running, own status; a second alice exits 3; stopping alice leaves test running; alice's log is not in test's;
  - voice switch: declined on screen → nothing happens; approved → spawn + active + Core quits; no screen → refused.
- Also passed, unchanged: test_guides 24, test_setup_wizard 21, test_capabilities 7, test_features 19, test_dashboard 10, test_dashboard_lan 12, test_dashboard_p02 3, test_runtime 27, test_backup 15, test_core 17, test_security 19, test_usage 12, test_identity 6, and test_face_protocol_godot 2 with real headless Godot 4.7.2.
- Godot `face_app --check`: PASS, including new banner and title checks.
- **Not run:**
  - the full suite (stopped on the coordinator's RAM request);
  - `test_launcher_help.py`, which would build a venv in the worktree;
  - final-commit CI (not claimed).
- **UNVERIFIED:**
  - owner hands-on use of the profile switcher, banner and voice switch;
  - a real voice switch with a live session (the test used a face client and fake audio);
  - `jarvis.cmd --profile` executed end to end;
  - Pi.

## Questions for the owner
1. Who gets a profile (names), and should the test profile be the agents' default for the FULL_TEST_PLAN campaign? Recommended yes, with a key added via `jarvis --profile test key` only for live passes.
2. Should two voice-enabled profiles be allowed to run at the same time? Both would answer "Hey Jarvis". Recommended: one voice profile at a time; voice switch already stops the old Core.
3. Should a switch by voice also be possible from the dashboard (a real "switch" that restarts the Core), or is "open its dashboard" enough?
4. Is app-level separation acceptable, or should family members get separate Windows accounts? Recommended: separate accounts for privacy-sensitive users.
5. Speaker recognition to pick the profile automatically: a later milestone? (Not in v1, by request.)
