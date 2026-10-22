# JARVES user guide (V4)

English. A Hebrew version with the same sections is in [USER_GUIDE.he.md](USER_GUIDE.he.md).

JARVES is a voice assistant that runs on your Windows PC: a face on the screen, a live voice conversation, memory, Google, music, a dashboard and more. Everything is stored on your computer. This guide describes what V4 does today. Where a part is not in your build yet, or is not yet checked on real hardware or accounts, it says so.

How to read the labels: **verified** means checked in a real run, **tested with fakes** means automatic tests with fake services, **not yet verified** means the code exists but nobody has run it against the real thing.

## 1. Install and first run

1. Download the installer from the release page and run it. The build is not code-signed yet, so Windows SmartScreen shows a warning: choose **More info, then Run anyway**, and compare the SHA256 on the release page with the file. Details and the owner test list: [INSTALL_TEST](INSTALL_TEST.md).
2. Start JARVES from the Start menu or type `jarvis` in a terminal. A loading screen appears, then the room with the face.
3. The first run asks how to connect: (1) your ChatGPT plan, (2) plan plus an OpenAI API key for voice, (3) an API key only. Change it later with `jarvis setup`.
4. Set a monthly spend limit in your OpenAI project. JARVES also keeps a local daily budget (section 5).
5. `jarvis doctor` checks the key, the microphone, the wake model and Pear and says what to fix. Without any key JARVES still works offline for simple commands.

Always-on: `jarvis start` runs JARVES in the background with a tray icon. `jarvis autostart on` starts it at Windows login (off by default). `jarvis stop` stops it.

## 2. Talking to JARVES

- Say **"Hey Jarvis"**. A short chime means he heard you, a second chime means the live session is connected. Then talk normally; you can interrupt him at any time.
- In `conversation` mode JARVES opens already connected; in `wake_word` mode he stays silent until called. Setting: `[voice] activation`.
- The pill on the face shows what he is doing; the corner dot shows the connection (green ready, blue live, amber connecting, red offline, grey muted).
- Software mute stops capture. It is not a hardware privacy switch.
- The wake word is a small model that runs only on this computer; nothing goes to the cloud until the wake word is heard. V4 ships a head trained on the owner's own recordings (`jarvis wake train`, undo with `jarvis wake train --revert`). Test and tune it with `jarvis wake test --apply`. Measured on the owner's voice it wakes more reliably and falsely wakes less than the stock model, but it still wakes on a bare "Jarvis" and a second wake within about 4 seconds can be missed.

## 3. The dashboard

`jarvis dashboard` opens a page on this computer (English by default, Hebrew in Settings; Arabic and Russian were removed in V4). The link carries a private key that is valid until the dashboard stops.

Main pages: Home (privacy switch, health, approvals), Conversations and Recall, Memory, Skills and tools, Features, Settings, Costs, Timers and Lists, Approvals, Coding agents, WhatsApp, Health, Updates, Logs. The Features page shows every switch with its state and the reason when something is unavailable. Every page is keyboard-usable and has text contrast checked against WCAG AA. On a phone, home-network access is opt-in (`dashboard_lan`) and needs pairing.

Every feature also has a guide: dashboard Guides, or `jarvis help <topic>`. Ask JARVES "how do I set up X?" and he walks you through it one step at a time.

## 4. Approvals

Anything risky asks first. A compact popup shows one line ("Run a command?"), a countdown, and two buttons: **Approve** (Enter, after a short arming delay) and **Deny** (Esc). Details are behind a Details toggle. Dangerous requests need a typed code.

- You may approve by voice only for low-risk actions. The spoken question names only the kind of action, never the details or the number.
- Answers are signed with a per-run key. A forged answer file or a replayed popup result is ignored. The popup language is English unless `[ui] popup_language = "he"`.
- `jarvis approvals list | approve | deny | history`, or the Approvals page.
- Text that came from outside (mail, web pages, WhatsApp, agents, recalled conversations) is treated as untrusted: while it is in a turn, JARVES refuses action tools.

## 5. Budget and delegation (client mode)

Voice runs on GPT-Live. In **client mode** JARVES, not the cloud voice model, runs the heavy "thinking" request itself, so every backend request is counted against your budget before it is sent and the conversation ends at the limit.

- Set limits: `jarvis budget set --daily-usd 1 --turn-usd 0.05`. Check the cap: `jarvis budget test-cap`. See costs: `jarvis cost` and the Costs page. Numbers are estimates; your OpenAI bill is the truth.
- Measured with 10 live questions: the added wait of client mode was not worse than the default (p95 7.7 s against 11.3 s, backend time dominates).
- Guide: `jarvis help client_delegation`. Report: [SWV4_D_DELEGATION](reviews/SWV4_D_DELEGATION.md).

## 6. Privacy

- Privacy mode (Home switch, `log.privacy`): the history keeps only lengths and timings, and logs hold no conversation text. Turning it off shows a banner on the dashboard and in `jarvis status`.
- Logs use fixed messages; variable data is classified (safe, secret, content, personal) and redacted by class. A planted-secret audit of 36 sinks (logs, diagnostics, tickets, approvals audit, backups and more) found 0 leaks in both modes.
- `jarvis diag` builds a support bundle that always strips content and hashes personal data. Review it before sharing.
- Secrets stay in a private file outside the project and never in logs or backups. Keys and sign-ins are per profile.
- Camera and sending mail or messages are off by default. The camera indicator and privacy strip show when a sensor is live.
- Whether new installs start with privacy mode on is an open owner decision (OD-12).
- Guides: `jarvis help privacy`, `jarvis help privacy_indicators`.

## 7. Memory and recall

Memory holds only what you ask JARVES to remember. **Recall** searches past conversations, notes and reports: ask "what did I say about the dentist?" or "what did we talk about last week?". He answers briefly with the date and a short quote, and says so when nothing is found. Hebrew spelling variants and date phrases in both languages are understood (40 of 40 test questions found in the top 3).

- Local index per profile; nothing is indexed in privacy mode; deleting a conversation removes it from the index; "off the record" keeps a sentence out; after 90 days a short summary remains. Recalled text never triggers an action.
- `jarvis recall "the dentist" --since "last week"`, `jarvis recall --stats | --repair | --rebuild | --forget`. Dashboard: Recall page.

## 8. Coding agents (Agent Orchestrator)

Switch **Coding agents** (`agent_orchestrator`, off by default). JARVES can list, watch and talk to your Claude Code and Codex sessions: "what are my agents doing?", "tell the agent to run the tests", "start an agent on the parser project".

- Setup: add projects to the allow-list, install the plugin (`jarvis agents install-plugin claude|codex --yes`), check with `jarvis agents doctor`.
- Commands: `jarvis agents list | status | ask | tell | continue | pause | interrupt | stop | start | resume | approvals | approve | deny | log | digest | projects`.
- Safety: local named pipe with mutual authentication, no network port; start and stop are approved on the screen only; one writer per folder; agent text is untrusted; a turn cap and a daily cost-estimate cap apply; API billing is off by default.
- Not yet verified on live paid sessions of both tools. Guide: `jarvis help agent_orchestrator`.

## 9. WhatsApp (bridge)

A separate local program (a Go bridge) reads WhatsApp as a linked device. Read mode first; sending is a separate switch and needs 14 quiet days in read mode and your explicit go.

- Only you, on the PC, can link, change mode or edit the allow-lists (`jarvis whatsapp link | mode | allow | kill | purge ...`). A paired phone cannot.
- Sends go only to allow-listed contacts, always through the approvals popup that shows recipient, number and the full text. Limits: 10 per hour, 30 per day; no groups or media.
- Message text goes to OpenAI only for the messages you ask about. Everything is untrusted text.
- Unofficial linked-device access can break WhatsApp's terms and the account can be restricted. Use it knowingly. The real-account run is not yet verified; automatic tests use a fake bridge. Until send mode is on, JARVES uses the older wa.me handoff.
- Guide: `jarvis help whatsapp_bridge`.

## 10. Assisted dialing

"Call Dana" resolves the contact, reads the number back digit by digit, shows name, number and its source in the approvals popup, and only after your yes opens a `tel:` link. **You press Call** in Phone Link or the default dialer; JARVES never dials, answers or records. Emergency numbers are never routed through contacts. A number that came from mail or the web is refused.

This package is part of the V4 build. It has not been verified with a real Phone Link and phone. The feature is `assisted_dialing`, off by default; the "Phone calls" page and `jarvis dial` appear when you turn it on.

## 11. Other channels and devices

Telegram bot, Instagram DMs and Home Assistant are optional connections (guides: `telegram`, `instagram`, `home_assistant`). Their messages are untrusted text. Live verification with your accounts and rocket alerts are not part of V4 yet.

Google Calendar, Gmail and Contacts: [GOOGLE_SETUP](GOOGLE_SETUP.md). Camera and face recognition (off by default): [CAMERA_SETUP](CAMERA_SETUP.md). Music through Pear Desktop: `jarvis music open`, then `jarvis music pair` once.

## 12. Health and self-diagnostics

The **Health** page and `jarvis health` show a score, component tiles and ranked findings.

- A background monitor stores numbers only (latencies, CPU, memory, tool failures), learns your 14-day baselines and flags real changes. After an update it compares the first 24 hours with the previous version and suggests rollback when something got worse.
- Findings point at the code that wrote them (file, line, last change) when the build has the index.
- Repairs come from a closed list, every one through an approval with a dry-run text. Ticket drafts stay on this computer until you set a repository yourself.
- An optional AI review runs every 2 days at most, with caps ($0.10 per run, $1 per month), and sees only redacted data.
- Preflight runs at start; nightly canaries test voice, music, calendar, dashboard and the installer in throw-away homes.
- Commands: `jarvis health`, `health report`, `health preflight`, `health canary`, `health ai-review --preview`, `health ticket <finding>`. Guide: `jarvis help health`.

## 13. Performance

The idle Core targets about 93 MB private memory, 11 threads and 0.3% CPU, with idle disk writes kept small. CI enforces budgets from `core/perf_budgets.toml` with `core/tools/perf_gate.py` (ceilings for memory, threads, startup, features list and dashboard load). After the idle-write fix the Core wrote about 4-8 KB/min while idle (strict owner ceiling 10 KB/min, measured on one PC). See [SWV4_PERF_BUDGETS](reviews/SWV4_PERF_BUDGETS.md).

## 14. Update, rollback and uninstall

- `jarvis update check`, then `jarvis update install`. Nothing is downloaded before your yes. The manifest is Ed25519-signed, the installer's hash and size are pinned, a downgrade is refused, and your data is backed up first.
- `jarvis update --rollback` (and a button on the Updates page) restores the previous version and the data backup taken before the update. Data written after that backup stays in a safety backup.
- Upgrade and rollback are covered by tests with fakes. The real installer round trip runs in CI and on a second PC (owner test list in INSTALL_TEST); this is not yet verified by the owner. The installer is unsigned (SmartScreen shows a warning); the update manifest is signed.
- Uninstall keeps your data unless you choose to delete it. Backups: `jarvis backup`, `jarvis restore`.

## 15. Profiles

`jarvis profile create <name>` makes a separate memory, budget, keys and sign-ins. `jarvis profile create test` makes a safe test profile with AI offline. Run one with `jarvis --profile <name>`. Separation is by the app; use separate Windows accounts for full separation.

## 16. Troubleshooting

| Symptom | What to do |
|---|---|
| Nothing happens when I say "Hey Jarvis" | `jarvis doctor`; `jarvis wake test --apply`; check the microphone is not muted |
| Many false wakes | `jarvis wake test --false-wakes 30`; `jarvis wake train --revert` returns to the stock model |
| No answer, "offline" | Check the key (`jarvis key`) and the OpenAI balance; `jarvis status` |
| Feature shows "unavailable" | The Features page names the reason; `jarvis help <feature>` |
| Something looks wrong | Health page or `jarvis health`; `jarvis logs --lines 30` works offline |
| Need to ask for help | `jarvis diag` creates a redacted bundle; read it before sharing |
| `jarvis status` exit code | 0 running, other codes as in `jarvis status --help`; 4 means headless (face absent) |

More: [core/README](../core/README.md), [RELEASE_NOTES_4.0](RELEASE_NOTES_4.0.md), [CHANGELOG](../CHANGELOG.md).
