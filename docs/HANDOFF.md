## Complete retained-log quality review (19 Oct 2026) — [report](reviews/ALL_RETAINED_LOGS_19OCT2026.md)

- User explicitly requested improvement analysis of all logs beyond ERROR events. Reviewed all **988 JSON records**, all **100 voice text entries** (40 user/60 assistant), **74 history turns** and history backups. Found **26 raw-only turns** absent DB; backup snapshots add no unique transcript text. **19 typed AI completion records have no answer text**, so typed quality is unassessable. Input log SHA256 unchanged before/after and private DB counts unchanged (4 sessions/74 turns/13 tools/29 delegations/156 events/4 issues); no raw conversation/account content committed.
- Findings prioritized in report Q01–Q13: **P0 explicit full exit executed open_dashboard successfully**, and a historical request had backend completion then roughly **93 s to voice end without a retained substantive answer** (not 93 s backend inference). P1 covers camera grounding (source corrected, new voice test pending), current-score vs final-score intent drift, review lifecycle/retry defects, missing older-session backfill, end-to-end startup delay and suppression/recovery evidence. P2 covers opaque progress/acknowledgements, saved-preference expectations and help targeting. Historical music mistakes have source corrections; not re-opened as newly proven faults. A shutdown disconnect is distinguished from the older unexplained one. Good tool-grounded behavior and consent checks are recorded too.
- Verification: read-only parsing/count/timestamp reconciliation, all 74 DB turns matched exactly to raw records; retained history backup39 turns and ZIP snapshots11/26 add none absent raw. An **offline in-memory injected review failure** reproduced `already reviewed` with **0 completed review rows**. Real metadata shows2 review attempts without completion evidence; exact cause/charges unknown. All27 measured reply waits peak3.23s, but the unfinished older request lacks timing and is not exonerated by this statistic. No runtime suite, provider request, replay, device activation or private DB mutation in this review. Document/link/diff checks at closeout; final-commit CI not claimed absent a run.
- Report/NOW/ROADMAP/HANDOFF are this review's only tracked changes; concurrent runtime/test work remains with its owner. No new provider/library/model selected. Existing #7/#17 track implementation/live acceptance; no duplicate issues or closures. Commit/push remains mandatory. Proposed next implementation: app-exit routing and answer-delivery completion/recovery, **not started by this review**.
- Decision queue: full retained-log analysis — **ANSWERED**, report includes positive cases and confidence/coverage limits. Whether26 suppressed audio chunks mean lost answers — **ANSWERED: unknown**, chunks are not answers; duration/recovery traces needed. Cause of absent automatic review results — **BLOCKED on lifecycle evidence**, while failed-review non-retryability is reproduced offline. Appearance/personality/pronunciation preferences — **DEFERRED**, not needed for report; human listening/visual review is the gate. P0 implementation/live tests — **USER DECISION REQUIRED for this review's next work**, recommendation above because it addresses wrong actions and unanswered requests first. Earlier unrelated queues remain unchanged.

## Standalone execute CLI and grounded camera status (19 Oct 2026) — [#27](https://github.com/igalgerman/JARVIS/issues/27)

- **Latest-log review requested by user:** reviewed latest completed Core run **22:32:51–22:36:29 local**, latest log event **22:41:39**. **0 ERROR**, **0 failed tools**; all 5 tool results ok. Twelve measured reply waits, maximum **3.23 s**; 14 backend completions, maximum **8.6 s**; no backend-stall event. Confirmed bug is the unsupported camera connection claim described below. Latest `LINK_FACE_DISCONNECTED` at **22:36:28** follows `CORE_STOPPING` **22:36:23** and voice end **22:36:26**: normal shutdown context, distinct from the older unexplained mid-session disconnect. Voice-end counter records **14 early_response_drops**, meaning output audio chunks suppressed by the overlap guard in `voice/live.py`; this is a review concern, not evidence of 14 lost answers. Whole-answer impact is UNVERIFIED; no voice timing changes justified from this count alone. Track in existing #7/#17 live review; no new paid replay or capture authorized/performed. Logs were reviewed privately; no transcript/account content copied into source.
- Push integration: remote advanced by two commits affecting only `JARVES_SW_V3_WORK_PACKAGE.md`; fetched and rebased our local checkpoint cleanly, preserving both documentation commits. No source changed in integration and no force push. New future work-package document is preserved, not started by this task.

- **Later explicit user check:** user authorized the agent to test the webcam. The actual configured OpenCV adapter opened and returned a **320×240** frame in **2.47 s**, `is_dark=false`, mean **131.39**, spread **54.64**. Frame stayed in RAM and was dropped; camera closed and instance lock released, exit **0**. No image file, network, cloud model or enrollment used. This verifies local capture on this PC, superseding the capture-unverified note for this narrow path below; cloud vision, recognition identity and live spoken grounding remain unverified. Scratch reproduction `.cache/check-camera-local.py` is deliberately untracked; maintained adapter is `camera/backends.py`.

- User explicitly wants natural-language CLI requests with JARVES stopped. Added `jarvis execute "add a calendar event tomorrow at 10 called Dentist"`; `--interactive` preserves follow-up context until an empty reply. `text_session.py` reuses the existing brain/provider factory, request Budget, local memory/relationships, Google Skills, feature gates, workspace/settings, clock and existing Pear service. Terminal read-back confirms side effects; feature activation stays in its maintained CLI. No Core/Godot/audio/camera capture, scheduler or background services start. Text-only generated references are in memory and cannot replace the Core's tool files. Brain keeps default speech formatting but offers raw text for execute so links survive. No new model, dependency or provider selected.
- Camera report was an unsupported assistant claim, not an established hardware disconnect: latest run logged available at 22:32:55 and stream-on at 22:33:07 before a camera-unconnected reply at 22:34:09; no camera tool call preceded it. Core is now stopped. Fixed module CLI reading unused legacy local flags instead of saved Features; actual sensorless check now finds USB camera and all three ON, matching `jarvis features`. Front and backend instructions require camera_status evidence, never infer disconnection from a missing photo/enrollment tool. No device activated by this investigation. Logged stream readiness does not verify image quality or successful photo tools.
- Files: `text_session.py`, `cli.py`, `brain.py`, camera `__main__.py`, workspace voice instructions, focused tests, loaded setup guide/README/NOW/ROADMAP; AGENTS records the user's standing commit/push requirement. Owning layer Python Core/CLI, no downstream assets or UI layout changed. Reproduce offline text: `jarvis execute "what time is it"`; natural-language Google actions additionally require configured AI, account scopes and enabled features.
- Evidence: **7 execute tests passed** (stopped Core/device prohibition, persisted contact, confirmed/declined/disabled Calendar actions, follow-up state, raw links, Core reference isolation, CLI dispatch). **4 camera CLI/instruction tests passed**, actual sensorless camera status exit 0, execute help and diff check passed. Tests use fake Google and AI, no paid inference/account effects/capture. One assertion without behavior was removed after the passing run; no production changes followed. Final-commit CI has no run until branch PR; no pass claimed. #27 remains open for final CI and authorized live natural-language Calendar acceptance; #17 retains broader live camera/voice review.
- Decision queue: standalone natural-language execution — **ANSWERED**, implemented; optional text follow-up needed for clarification rather than invented event details. Commit/push every completed change — **ANSWERED**, saved in AGENTS. Camera connection claim — **ANSWERED root cause and code correction**; live voice adherence and camera image check remain **UNVERIFIED**, next gate is user-approved real interaction. No real event or email was requested by the generic example, and none was created/sent. Earlier queues remain in force; no next milestone started.

## Relationship contacts and event sharing (19 Oct 2026) — [#26](https://github.com/igalgerman/JARVIS/issues/26)

- User clarified this is generic JARVES behavior, not a specific spouse address or one-time event. Python Core now has `contact_remember`/`contact_lookup`: explicit relationships/name/email persist in ordinary memory, no fuzzy address deduplication or silent replacement, remain visible to recall/dashboard and deletable with existing forget/clear. Specific English/Hebrew remember forms route offline; backend guidance resolves relationships first, asks on ambiguity and never infers a spouse. No real address was supplied or stored.
- Optional `google_contacts_search` uses existing httpx REST, read-only `contacts.readonly`, People API warmup, names/email mask and bounded candidates. Default-off `google_contacts` feature; separate `jarvis google login --contacts`, then feature activation and Core restart. Incremental consent requests prior scopes again but trusts only Google's actual returned grants. Remote output is untrusted; genuine next-user-turn selection precedes action. People API must be enabled in the app project. No directory import or contact edits. Official references/date in README and contacts module.
- Calendar create accepts guests; `calendar_share_event` invites to a selected event, preserves guest objects and rejects concurrent changes via If-Match. Read-back confirms recipients and recurring scope. Only real Google htmlLink is returned; no fabricated link or calendar ACL sharing. Existing Gmail draft/send handles separately approved link email. Corrected feature ownership for real Gmail draft/send names and the new Calendar sharing tool. CLI groups `contacts`, `calendar`, `mail` expose the flow; `mail draft --send` keeps fresh-draft readback and confirmation in one process. Runtime setup guide and README explain actual gates.
- Evidence: new Contacts tests **19 passed**; Calendar write tests **41 passed**, then strengthened control-character case **5 passed** targeted. Cross-layer Google/Contacts/features integration **138 passed, 1 failed**; obsolete shortened-summary assertion corrected to full generated reference and exact case **1 passed**. Final local contact/CLI/simulated Core tests **22 passed**, including persistent memory/deletion, invalid addresses, missing scope, accepted/declined guest invitations and draft-only/declined/accepted Gmail sends using fakes. `git diff --check`, root CLI help and offline wheel build passed; packaged source synchronization checked at closeout. No live account/network content, capture or paid probe. Final-commit CI unavailable without branch PR; no CI pass claimed.
- Decision queue: relationship-aware generic contact/sharing capability — **ANSWERED**, requested and implemented; supersedes the earlier unanswered sharing-choice question below. No blanket automatic sharing of all events was requested or enabled. Read-only Contacts access and live invitation/email verification — **USER DECISION REQUIRED** at the explicit account-consent/live-test gate; no real actions performed. #26 stays open until final-commit CI and authorized real-account checks; #17 remains open for broader review. Earlier unrelated questions remain in force. No next milestone started.

## Feature prerequisites and diagnostic corrections (19 Oct 2026)

- Python Core owns both fixes. `cli.py`/new `feature_setup.py` check and repair allowlisted dependencies before enabling, even if already on; source installs use frozen lock/inexact sync, bounded verified SFace download and Core instance lock. Installed releases need repair if bundled dependencies are missing. Source launcher preserves extras; Windows packaging includes voice, camera and tray. Google readiness checks actual scopes; explicit Calendar-write consent can precede enabling its switch. Setup guide, README and camera instructions match this flow. No geometry/assets changed.
- Private live verification: locked OpenCV and pinned SFace installed; sensorless Windows readiness passed; user completed Calendar-write consent and `jarvis features on calendar_write` exited 0. No event, invitation, face enrollment or sensor capture performed. Current music readiness is blocked by Pear not running; autostart remains off. Other previously requested feature flags are enabled, with their normal consent gates retained.
- Diagnostics now retain nullable audio interval metadata and local speech buckets rather than treating a transcript completion timestamp as speech onset. Queued onset accounts for existing playback; interruption/session reset discards abandoned timing. Backed up private DB, imported only verified session metadata and resolved the two demonstrably false timing issues. Actual `jarvis diagnose`: 2 findings, 1 open issue (Godot disconnect, x2). Cause unproven; do not hide it. Physical playback timing remains UNVERIFIED: timestamps estimate queued playback.
- Focused evidence: prerequisite tests 26 passed; four-file integration 67 passed/1 failed, followed by test-only fake-Pear fixture correction and exact failed case passing. History/live tests initially 19 passed; final timing regression file 6 passed. Explicit consent-with-switch-off test 1 passed. Diff check and PowerShell packaging parser passed. Offline wheel contains exact current setup guide (SHA256 `f0cea15b89e6efd12c951df5b0b1f414901938d604b62f85b4914d1a2802a871`), no OAuth client. No broad suite, installer build or paid live probe. Final-commit CI unavailable without branch PR, no CI pass claimed. Issue #17 remains open; no broader milestone complete.
- Decision queue: automatic supported prerequisites — **ANSWERED**, implemented; enable webcam/all requested flags — **ANSWERED**, private changes above, capture unverified. Full CLI parity — **ANSWERED requirement / incomplete**, gaps below. Calendar sharing — **USER DECISION REQUIRED**: recommend a shared Family calendar so only chosen family events are shared; existing shared-calendar writes supported, sharing administration/invites not implemented. No access changed. Godot disconnect — **BLOCKED on a reproducible live trace**, warning stays open. ChatGPT plan usage — **ANSWERED explanation**: unavailable/stale quota telemetry yields Unknown, not proof of disconnection; saved credential/permission exist, live validity untested. Potential app-limit/account-quota conflation deferred for separate approved fix. Logging question — **ANSWERED**: terminal and file default INFO; dashboard can filter recorded logs, but cannot recover unrecorded VERBOSE. Set file level VERBOSE in Privacy & logs and restart if desired; settings unchanged. All older decision queues remain in force.

## Setup knowledge and root CLI help (19 Oct 2026)

- User asked to teach JARVES installation/feature help through the docs already loaded into his context. Added generated read-only `SETUP.md` from packaged `core/src/jarves/setup_guide.md`, plus `FEATURES.md` from the complete live feature catalog. Existing workspace reader now pages long docs with `next_offset`; bounded summaries remain within the existing context cap. Backend reads those docs for help, frontend delegates. Generated docs do not grant installer/shell/browser powers and preserve personal workspace files. Features refresh on switch changes; restart a running Core to load source changes.
- Owning layer: Python Core context/workspace/settings; Google connection help uses Connect Google for app clients and reports a maintainer blocker for installed builds missing registration. Root `jarvis.cmd` now forwards `--help`, `-h`, `--version`; installed launcher also forwards `-h`. No geometry, assets or runtime UI layout changed. README explains source-vs-generated docs and editing permissions.
- Verification: **67 focused tests passed** (workspace, Google setup, actual Windows help launcher), then **5 focused follow-up tests passed** after review fixes for long-document paging and missing release client. Offline wheel build includes the exact setup Markdown (SHA256 `3dc0e0584191f52945283083d86b962d4bf4bbab16404b6f07d40d0cf3ada129`), no OAuth registration file; this checks package data, not a released installer. Live model adherence and a new installed user remain UNVERIFIED. CI is unavailable without a branch PR; no pass claimed.
- User-authorized subagents: one README-only writer and one read-only bounded reviewer; reviewer later owns the separate CLI prerequisite implementation. Root integrates. Review findings addressed: pagination supports generated docs beyond 20k chars; installed app missing OAuth client no longer sends ordinary users into developer setup. No duplicate model reviews or full-suite reruns.
- Decision queue: loaded setup/all-feature docs and full root CLI help — **ANSWERED**, requested and implemented. Editable personal docs remain governed by existing approvals; generated SETUP/FEATURES/TOOLS are read-only. CLI parity for every option — **ANSWERED requirement, implementation incomplete**: existing feature switches are all accessible, but validated config, workspace, identity/voice, runtime sensor actions, appearance, privacy, scheduler/reports, notes, grants, alerts/autonomy, generated Skill management and paired-device actions have gaps; audited, not claimed done. Existing Google production/domain/verification and other user-owned questions below remain open. Current authorized next corrections: CLI prerequisites and reported diagnostic faults; no new milestone.

## Google Connect for future app users (19 Oct 2026)

- User clarified the desired audience is future **app users**. Python Core now accepts a release-supplied app-owned Desktop OAuth client; a fresh user goes directly to consent, with per-user token storage and no gcloud or Cloud Console setup. A private imported client still takes precedence. The dashboard shows **Connect Google** and hides developer setup instructions for the app-client route. Windows packaging optionally accepts `-GoogleOAuthClientFile`, strips everything except Desktop registration values and writes them only into the staged installed package. No real client or tokens were copied into source or release artifacts.
- Files: `core/src/jarves/{google_auth,google_setup}.py`, dashboard `home.js`, `packaging/build_windows.ps1`, `.gitignore`, focused setup tests, README/NOW/HANDOFF. No upstream CAD/Blender/Godot changes. Reproduce the app path in a release built with the maintainer's app-owned client: `jarvis google start` or dashboard Connect Google. Builds without that configuration retain the developer wizard.
- Verification: **80 passed** with `core/.venv/Scripts/python.exe -m pytest core/tests/test_google.py core/tests/test_google_setup.py -q`; fake browser/Google proves a fresh user requires no console input/CLI, app config never appears in status, private override and per-user tokens remain separate, malformed/web clients are rejected. PowerShell build-script parser, `node --check core/src/jarves/dashboard_static/home.js`, and `git diff --check` passed. Full installer build, visual browser review and live login of a new app user are **UNVERIFIED**; no production client has been supplied. Final-commit CI unavailable without a branch PR; no CI pass claimed. Existing personal connection was separately live-verified below; that does not prove the new app-release route.
- Decision queue: app-owned registration for future users — **ANSWERED**, user requested a normal app Connect Google experience. Recommendation: one maintainer-owned Desktop public client with PKCE and local per-user token storage. Production client + public brand/homepage/privacy-policy domain — **USER DECISION REQUIRED** before provisioning/distribution; owner must designate the app-owned project/client and domain, rather than shipping their personal testing file. Google restricted-scope verification / cloud-AI data-path review — **BLOCKED** until that registration and disclosures exist; may require a security assessment (official sources/date in README). Costs/purchases/submissions require the owner's decision. Earlier queue entries remain unchanged. No public rollout or next milestone started; Issue #17 remains open.

## Google wizard CLI setup (19 Oct 2026)

- **Live completion (user present):** installed official Google Cloud CLI 589.0.0 via `winget install --id Google.CloudSDK`; user completed CLI login and the remaining Google Console / OAuth steps. CLI found that the saved project ID differed from the actual accessible Jarvis project, selected the correct project and enabled both APIs. `jarvis google start` exited **0**, reporting **calendar ok, gmail ok** from real read-only API checks. Client file moved from Downloads into private state; no credentials or account content committed. `google_calendar` and `gmail_read` enabled; `gmail_send` remains OFF. This supersedes the earlier live-verification blocker below. Chat/voice tool execution and future weekly reconnect are still unverified. User may need a new terminal for the installed CLI PATH; this run prepended its installed bin directory to the process PATH.
- User asked to use Google's CLI after a step-4 permission error. Python Core owns the fix: `core/src/jarves/google_setup.py` now defaults to installed `gcloud`, lists accessible projects for explicit selection/creation, enables only requested APIs, and repeats project selection on resume. Changing projects clears stale checkmarks; CLI failure stops before browser pages and gives login/network/permission recovery instructions without raw output. No CAD/Blender/Godot propagation needed.
- Changed wizard, focused tests, `core/README.md`, `docs/NOW.md` and this handoff. CLI sources/date are in the README (official references checked 2026-10-19). Reproduce: `jarvis google start`; prerequisite: install Google Cloud CLI on PATH and run `gcloud auth login`. Missing/declined CLI retains the manual route. Consent/Desktop OAuth remains in the browser.
- Verification: `core/.venv/Scripts/python.exe -m pytest core/tests/test_google_setup.py -q` — **24 passed** (offline), including create/select, resumed invalid project, same-project progress, calendar-only APIs, failure/timeout/malformed output and no raw-output leaks. `git diff --check` passed. `gcloud` is not on this PC's PATH; real CLI login, project permissions and Google sign-in remain **UNVERIFIED**. Final-commit CI unavailable on the current branch without a PR; no CI success claimed. Issue #17 remains open for broader SW-V2 review.
- Decision queue: CLI preference — **ANSWERED**, user requested it; recommendation is the maintained Google Cloud CLI for supported operations. Account/project authorization — **ANSWERED**, user completed sign-in and the real API checks passed. Earlier user-owned decisions/questions below are unchanged; this fix does not resolve or discard them. Next gate: user tries Calendar/Gmail commands in JARVES; no next milestone started.

## Google wizard terminal correction (19 Oct 2026)

- User reported broken Hebrew in `jarvis google start` and requested English only, commit and push. Owner: Python Core CLI; `core/src/jarves/google_setup.py` now prints English messages, numbered steps, prompts, recovery links and completion summary. Shared dashboard/voice Hebrew remains available. No downstream asset refresh needed.
- Verification: `core/.venv/Scripts/python.exe -m pytest core/tests/test_google_setup.py -q` — **17 passed**, offline fake browser/Google; first run was blocked by sandbox temporary-folder permissions, approved rerun passed. Existing first-time test verifies no Hebrew in wizard output or input prompts. Real Google login remains unverified. Final-commit CI unavailable: this branch has no open PR and CI triggers only on main pushes or PRs; no CI success claimed. PR/merge remains the broader SW-V2 review gate.
- Decision queue: English-only terminal wizard — **ANSWERED**, explicitly requested by user. Earlier decisions/questions below remain unchanged. No next milestone started; Issue #17 remains open for its wider acceptance criteria.

## SW-V2 (19 Oct 2026) — Issue #17: IN PROGRESS on branch `swv2-integrate`

Merged so far (436 tests pass): WP-A conversation quality, WP-B store/cost/dashboard/diagnosis, WP-C backend trace + deadline/retry + background tasks, WP-D workspace files + curator + settings skill, WP-E part (Issue #16 fixes, volume/mute), WP-F Google/camera plans. Reports: `docs/reviews/SWV2_*.md`, `CONVERSATION_LOG_REVIEW_19OCT.md`.

**Still open:** WP-E suite runs (Godot, voice, music) and report update; WP-G Google Calendar + Gmail skills (restart; rate-limit); dashboard wiring (workspace/memory edit, costs from WP-C usage fields, tasks.jsonl); settings editable in dashboard (backend deadline etc.); persona line for background tasks; independent code review; CI on final commit; merge to main.

**WP-J camera (branch `worktree-agent-a32a688f84b7feccb`, not merged):** camera package + Skill + Godot gaze/indicator, all switches default OFF, no hardware used. Report `docs/reviews/SWV2_WP-J.md`, setup/privacy `docs/CAMERA_SETUP.md`. Open: greeting-on-presence default, indicator look (user), WP-I switch interface check, Pi C4 gate.

### User decisions (19 Oct)
- Google: BOTH Calendar and Gmail skills; all calendars; read calendar allowed; he ASKS before reading email aloud; sending email by voice allowed only after reading the draft back and explicit confirmation; weekly reconnect accepted.
- Camera: yes; face recognition yes (opt-in per person); physical shutter yes (next CAD round); away mode = camera OFF, configurable, default off.
- Dashboard DB keeps full text while tuning (log.privacy=false).
- Memory curator learns automatically, WITH the model pass (budgeted, ~$0.001/conversation).
- Backend stall deadline: configurable in the dashboard (default 25 s).
- Typed-chat background tasks in combined mode: use the ChatGPT plan.
- Tiny "שניה…" lead-in acceptable; task result waits up to 5 min for a quiet moment.

- Self-diagnosis: add the model pass, ON by default (budgeted).
- Dashboard: opened on request by `jarvis` AND a face button; edit workspace files + delete learned facts, with undo.
- Merge: PR from `swv2-integrate` -> main after green CI + independent agent review.

- Spec (docs/JARVES_SW_V2_IMPLEMENTATION_SPEC.md) review decisions: Markdown Skills = extend our own SkillRegistry (no other agent's work exists); calendar may also create/update events with confirmation, NEVER delete; dashboard on home LAN with pairing+login; CI should build the Windows installer after a merged green PR and keep the 3 latest (M6); updates: notify + ask approval; Pear closed -> offer to start it; medium-risk features enabled by voice need on-screen approval; camera greeting off (configurable); email search says count + senders then asks before reading; keep httpx for Google; face model download after one confirmation; keep the camera indicator ring.

- Music: `music.trim_queue` stays ON by default; no automatic Pear restart (a stall cannot be detected reliably) — he offers a confirmed `music_restart_player` instead (TODO, not built yet). AI review of conversations stays ON (budgeted). Open: typed text in dev logs (masked codes) — answer pending.

- Wave-2 decisions (19 Oct): tray + show/hide verified by the owner on Windows; first window close shows a one-time "still running in the tray" notice (TODO); installer ticks "start at login" by default; read-only OpenAI admin key + monthly API budget $20 are SETUP steps (guided in the dashboard/by voice); backups exclude face data and API keys; plain "avatar" = 2D avatar; quiet hours 22:00-07:00; missed-job max age 6 h and "ימי חול" = Sunday-Thursday, BOTH configurable (weekday set configurable is TODO); music restart tool with confirmation (TODO).

- Feature suggestions (docs/plans/FEATURE_SUGGESTIONS.md): owner is reading the list; build nothing from it until they choose. Already said: YES to an outbound-only Telegram channel; owner HAS Home Assistant (need URL/version/devices when starting #7); rocket alerts (#21) wait for their reading. Avatars: 2D good but improvable, 3D "creepy" -> WP-N restyle running.

- Late 19 Oct answers: phone dashboard stays HTTP for now (HTTPS before the robot is in the house); owner will record ~20 wake-phrase samples later; 3D avatar = cute robot (kept); 2D keeps iris eyes; installer: camera included, build only on main merges/packaging changes, unsigned for now, releases hosted on Cloudflare R2 (wrangler login done; bucket + public access + 5 GitHub secrets pending, agent WP-O building CI upload). Still open/in progress: WP-L (guides + self-aware help), WP-O (R2 hosting), full-suite re-run after disk was freed (C: was full -> 16 spurious failures), independent code review, CI, PR to main, long music/voice scenarios on fresh Pear, tray first-close notice, weekday setting, music_restart_player, Issue #16 stays open until review.

- Multi-device + remote (owner, 20 Oct): ONE brain (PC Core), MANY satellites (phones/Pi = mic+speaker+screen+camera) usable in parallel, each routed to the Core; remote use from the owner's main phone over a private VPN (Tailscale, free personal plan; nothing exposed to the internet); remote scope = talk, read reports/dashboard, approve or reject actions; risky settings stay on the PC. Roadmap: OnePlus 11 first. Research/design agent produces docs/plans/MULTI_DEVICE_REMOTE.md before any code.

- Multi-device/remote answers (20 Oct): max 2 conversations at once and 30 min/device/day; the main phone may approve reminders, jobs and drafts but NOT email sends, installs, file grants or camera; if Tailscale is down, home devices still connect directly over the home Wi-Fi; satellites are Tailscale TAGGED devices; camera preview NOT viewable from outside the house; reminders go to the last active device plus a phone notification; main phone = web page first (native app later); OnePlus 11 uses the OxygenOS 80% charge limit first, a smart plug only if needed. Plan: docs/plans/MULTI_DEVICE_REMOTE.md. Security-fix note: paired LAN phones now use an ALLOWLIST (feature switches, workspace edits, scheduler jobs, calendar commit, update check are PC-only; tell the lead if any should be allowed from a phone). Fresh installs recommend privacy ON in the wizard; the owner keeps OFF while tuning. Shipping JARVES's own Google app: not now (Gmail restricted scopes need a paid security review).

### Questions for the user
- Listen to the warmer persona/greeting wording and the new voices (no GPT-Live voice is documented for Hebrew).
## Overnight batch 18–19 Oct: real conversation fixes, calm face, silent testing

**User asks addressed:** the voice never answered; the hello was slow; "open connected"; no strange pauses; "disconnect only on long idle"; "the face moves too much / not natural"; laugh and cry at what is said; info on the screen; "all tests silent and headless".

**Root causes found by real two-sided tests (not by guessing):**
- **GPT-Live streams audio continuously, silence included.** JARVES treated every chunk as "JARVES is speaking", so the mic stayed muted for the whole session (the model never heard the user) and a ~3.5 s playback backlog never drained. Now only chunks above rms 0.004 count as speech (`live.py`, tests in `test_voice_faults.py`).
- **The cloud-voice greeting was slow** because `import agents.realtime` took 6–15 s. The greeting is now cached on disk and the libraries are pre-loaded.
- **In connected mode nothing listened after an idle end.** The wake word is now on in both modes.

**Now (measured on this PC, real GPT-Live, silent virtual audio, `core/tools/silent_live.py`):**
- 12-turn Hebrew conversation: **12/12 answered in ONE session**, dead air 0.36 s average / 1.01 s max, 0 disconnects, idle disconnect after 23 s of quiet (set to 25 s for the test). Three web-search turns have a 1–2 s quiet gap after the "checking" sentence.
- Context kept in the session ("what did I say about my favourite colour?" answered).
- Acoustic path (speakers → real webcam mic → wake word → GPT-Live): reconnect chime 0.5 s after "Hey Jarvis", connected 1.1 s, answers ~1.7 s.
- The Opus 5.5 tuning agent's report and sources are in [LIVE_CONVERSATION_TUNING](reviews/LIVE_CONVERSATION_TUNING.md): backend `reasoning.effort = none`, `service_tier = priority` (about $0.0005 per lookup), time answered locally, short "checking" notes, new config fields `ai.voice_reasoning_effort`, `ai.voice_service_tier`, `ai.voice_max_output_tokens`, `voice.progress_after_s`, `voice.echo_tail_s`.

**Features:** connected-on-open (`activation = "conversation"` is set on this PC); wake chime plus ready chime; no 10-minute stop (`session_max_s = 3600`) and an automatic resume when the server closes an active session; `face.emotion` (laugh, cry, sad, surprised, love) and `assistant.activity` (thinking, searching, music, time, memory, connecting) for the new face; `jarvis doctor`, `jarvis wake test --apply`, `jarvis logs`, readable terminal logs with levels and modules, privacy mode.

**Face (Opus 5.5):** a premium shader face with a connection dot and no instruction text, then calmer motion (blinks every 4–7 s, saccades every 3–8 s, breathing 0.3 %), reactions with tears, and an info pill on the lower arc. The headless fuzz test `test_face_protocol_godot.py` found and fixed crashes on malformed messages; it runs in CI.

**Known limits (honest):**
- The Live API has **no turn-detection / VAD settings** (checked the SDK types). The model sometimes starts answering on a pause and the echo guard then mutes the rest of the sentence; occasionally it announces a lookup that then does not start (1 in 3 web turns in one run). Mitigations: stricter instructions; headphones plus `half_duplex = false` removes the guard.
- The silent tests use a Windows voice for "the user" and virtual audio: real voices, rooms and the real mic will differ. Not yet verified live: your real voice on GPT-Live in Hebrew, and the Pi.
- Wake word: your pronunciation scored low on the stock "hey jarvis" model (peak ~0.17); the threshold on this PC is 0.15 via `jarvis wake test`. A trained custom model is the real fix (not done).
- `service_tier = priority` costs more per backend call; set `ai.voice_service_tier = "auto"` to turn it off.

## Real YouTube Music playback verified — 18 Oct 2026 (night)

- **Setup:** the user paired Pear Desktop 3.12 (`jarvis music pair` + Allow, signed in to YouTube).
- **Real playback via JARVES's Hebrew commands, on merged `main` `d071a65`:**
  - "נגן את שלמה ארצי" played "תתארו לכם" and was confirmed.
  - "נגן את עומר אדם" played "שני משוגעים" and was confirmed.
  - Volume, next and pause all worked.
- **Two bugs found and fixed on the way** (commit `94954cc`):
  - the queue-insert race made Pear play the wrong song;
  - a false "not confirmed" came from YouTube's song counterpart (different videoId) and slow buffering.
- **Evidence:** CI [39163447349](https://github.com/igalgerman/JARVIS/actions/runs/39163447349) is green, 6/6 jobs; 219 tests pass.
- **Launching Pear:** `jarvis music open`, the Start-menu entry or the desktop shortcut "Pear Desktop (YouTube Music)".

## Voice-only usability and visible connection — 18 Oct 2026

- **Outcome / owners:** Python Core owns wake→connect→conversation→idle return and actual mic readiness. Godot owns visible status, face motion, loading UI and camera. Fixed stale connection snapshots (up to 20 s previously), normal idle incorrectly failing startup, speech animation running from time rather than playback, and wake-mode boot spending/delaying on an unnecessary separate greeting. No CAD/Blender geometry or material change.
- **Changes:** `core/src/jarves/{app,config}.py`, `voice/{live,realtime,audio,tts}.py`, config example/README and affected tests; `godot/{main,loading}.gd`, shared face/UI/app and capture script. Desktop is voice-only: no Talk/Space/text controls. Options hides details/mute/inspection controls, while connection/activity stays visible. Local wake does not imply a cloud connection. Connected status uses the real session slot and immediate lifecycle broadcasts; muted/offline states override. Mouth uses bounded RMS updates from the live/Realtime player, expires after 250 ms, and idle eyes stop wandering. Buffered/local TTS amplitude remains unimplemented (mouth stays closed); no fake speech animation.
- **Flow:** selected local `wake_word`, enabled wake, `vesper` Live, separate `cedar` Realtime/read-aloud, `idle_disconnect_s=180`. Wake readiness requires mic frames before room opens. Wake starts one native Live acknowledgment; playback/tools and significant input energy extend quiet time. Idle uses the same once-only finalizer/billing and resumes local wake. Existing session/day limits unchanged. No paid call, actual microphone/speaker or hardware test this turn. Speech during the handshake is **not buffered**; wait for Listening. Supported trained phrase remains **Hey Jarvis**; arbitrary Hello Jarvis recognition is unverified.
- **CI correction:** the real-process harness initially mistook the new immediate wake-instruction GREETING for the time-question reply, and exited before the command response. The E2E collector now ignores GREETING messages; product replies are still required. No model/audio failure was inferred from that harness race. Local real-process reply/close assertions passed (Core exit 0); ad hoc wrapper exited 1 only when its trailing Unicode summary could not print through cp1255, after all assertions/cleanup completed.
- **Verification:** 53 focused voice/readiness cases passed; 27 affected startup/Core/wake cases passed after correcting greeting-disabled and injected-factory compatibility; 6 idle/wake cases passed including automatic mic recovery; 4 focused idle/playback-envelope/actual-wake-readiness cases passed. Godot face check passed including silence/stale-level mouth closure, actual envelope response, collapsed options and connected→offline state. Viewer check passed (28 parts, 3 finishes); new camera face height 19.1% vs desk 7.4%, unobstructed. `git diff --check` passed. Final-commit GitHub Actions URL/result is recorded on [open #7](https://github.com/igalgerman/JARVIS/issues/7); local checks alone do not establish CI or live speech success.
- **Actual project captures:** [room/status](../godot/previews/voice_review/room.png), [front](../godot/previews/voice_review/front.png), [side](../godot/previews/voice_review/side.png), [rear](../godot/previews/voice_review/rear.png), [3/4 / selected finish C](../godot/previews/voice_review/three_quarter.png), [internals](../godot/previews/voice_review/internals.png), [loading without ETA](../godot/previews/voice_review/loading.png). All seven checked nonempty; room and loading visually inspected. Hidden-window GL compatibility captures of real project sources, not hands-on user confirmation or invented art. Room capture shows initial Core connection, **not evidence of connected AI**. [Source/image SHA256 manifest](../godot/previews/voice_review/evidence.json) binds captures to their unchanged sources in this commit. Reproduce with `godot/capture_voice_review.ps1 -GodotExe <installed-Godot.exe>`.
- **Run:** `jarvis` (restart an older run first). Model used: Codex / GPT-6; single implementing owner. Existing unrelated music source/test edits excluded. Next proposed task is the user's real wake/voice/face review; no new milestone started.

### Decision queue — current answers supersede historical defaults below

| Question | Recommendation / concrete rationale | Status / answer or next gate |
|---|---|---|
| Mode 1 vs mode 2 / manual controls? | Local wake avoids cloud capture/billing while waiting; no interaction depends on a keyboard. | **ANSWERED**: latest user requests hardware-style voice activation; wake mode selected, automatic conversation retained as optional config. |
| Disconnect timing? | 180 quiet seconds after output/tools avoids ending normal conversational pauses while limiting idle voice billing. | **ANSWERED technically**: implemented; user usability review remains next gate. |
| Connected/listening/speaking visible? | Keep compact status outside Options and reuse connection/activity cues on the same face scene. | **ANSWERED**: real session/wake/mute/link events drive UI; actual playback drives mouth. Pi performance remains unverified. |
| Voice name/personality (supersedes SW-2 marin)? | Vesper matches masculine British preference; Cedar works on the separate Realtime voice list. | **ANSWERED**: user explicitly delegated best voice choice; selected after official docs check. Subjective sound/Hebrew approval still requires listening. |
| ETA, zoom and options? | Progress stays, no unmeasured countdown; larger device and collapsed controls keep focus on conversation. | **ANSWERED**: user explicitly requested these corrections; implemented, actual previews linked above. |
| Recognition, latency, engine quality, appearance? | Judge the real wake→Listening→question path, not mock tests or a render. | **USER DECISION REQUIRED**: run jarvis; review voice/understanding/new camera/face and report a concrete phrase/reply if wrong. Earlier duplicate software review questions roll into this single gate. |
| SW-3 cost ceilings / SW-7 billing? | Retain current limits and configured combined connection; do not silently switch billing. | **DEFERRED / retained**: no new spending route or limit approval inferred; confirm ceilings before expanding live usage. Existing authenticated/cached connectivity evidence stands, not a new inference test. |
| Earlier R3/R4 styling, architecture, exploded clarity, physical purchases, mic/display/print/range? | These need viewer/hardware evidence and do not block this software correction. | **DEFERRED** to existing #4 appearance / #8 hardware gates below; all historical questions retained, no purchasing/fit approval inferred. |

# Current handoff — 18 October 2026

## Ready-before-room startup, progress bar and measured latency (18 Oct, newest)

- **New real-run evidence / source owner:** session `ac8a7527`: `CORE_READY` 0.829 s; face connected 22:02:52.068; separate speech request began .070; first audio 22:03:05.416 (**13.343 s**); generation complete 22:03:09.426 (17.359 s). Window close requested shutdown 22:03:11.010, but `LIVE_STARTED` arrived afterward at 22:03:13.042. Core/voice ownership: redundant TTS connection dominated greeting latency; a connecting session could finish after shutdown began. Both fixed upstream in Core/voice, not by cosmetic Godot masking.
- **User-approved behavior:** lightweight loading screen with milestone progress bar and approximate seconds ETA; do not instantiate the room until actual readiness. `jarvis` now runs `run --desktop`; CLI owns the specific Godot child (loading and room share the window), cancels boot on window exit, and stops Core. Loader uses existing authenticated **CLI/read-only** status role, so it never triggers the face greeting. Core publishes `startup.phase/progress/ready/elapsed_s/eta_s`. Mode 2 starts one GPT-Live session with startup developer greeting instructions, waits for real output audio, then marks backend ready. Loader changes to `main.tscn`; only the actual room's face connection releases bounded buffered speech and physical mic/player. No room/GLB is loaded by the splash scene.
- **Speed / caching choice:** reuse the live connection for hello instead of making the slow additional TTS request. Followed [OpenAI Docs](https://developers.openai.com/api/docs/guides/live-conversations#greet-before-the-caller-speaks), checked 18 Oct; native greeting is not guaranteed by prompting, so missing audio times out and the room stays closed. Buffer is in RAM only. `startup_timing.json` caches only a settings fingerprint and successful Core→first-room-connect duration; no audio/transcripts/tokens. ETA is absent on first start, approximate thereafter, and overruns show finishing rather than false zero-second readiness. Future Pi can consume this status contract; actual hardware boot integration is not implemented.
- **Measured real API probe:** configured `gpt-live-1` / `marin`, one max-15-second session, existing shared SQLite budget/reservation gate, generated-silence input only, output held. Connection **4.09 s**, actual first greeting audio **4.81 s** end-to-end. Probe ended cleanly; duration/reservations metered. No physical mic, speakers, recording or action/search tools were opened. Log events `STARTUP_PROBE_START` / `STARTUP_PROBE_READY` distinguish this from a user run. This proves first-audio availability in one run, not user-audible output, recognition accuracy or every-run latency.
- **Checks:** 51 focused readiness/desktop/streaming/voice-fault tests passed: first audio gates readiness, silence-only pre-display input, held speaker, no-audio timeout, no duplicate TTS, loading-window close cancels boot, connecting voice is stopped if shutdown began, and existing budget/lifecycle paths. Actual Godot loading render exported, file verified nonempty (**16,511 bytes**) and visually inspected: [loading progress screen](../godot/previews/startup/loading.png). Automated hidden-window GL compatibility screenshot of actual project code, not hands-on UI confirmation or AI concept art. Existing room/front/side/rear/3/4/internals renders remain valid for unchanged model/room sources; no redundant body-render set. CI adds real loading-screen read-only status readiness before existing Godot/Core E2E/close checks. Final-commit CI recorded on [#7](https://github.com/igalgerman/JARVIS/issues/7).
- Reproduce product: `jarvis` (or `uv --directory core run --no-sync jarves run --desktop`). Focused check: `.\core\.venv\Scripts\python.exe -m pytest core/tests/test_readiness.py core/tests/test_desktop_voice.py core/tests/test_voice_faults.py -q`. Default optimized desktop mode currently requires `voice.mode = live`; explicit voice-off readiness is supported for offline checks, and headless/manual alternatives remain. No new provider/model/library chosen. Existing unrelated music source/test edits excluded.

### Decision queue — startup follow-up (all older unresolved entries remain below)

| Question | Recommendation / rationale | Status / answer or next gate |
|---|---|---|
| Show progress before the simulation? | Separate lightweight boot scene prevents an apparently usable room while voice is still initializing. | **ANSWERED**: user explicitly required a progress bar and no simulation until ready; implemented. |
| Seconds ETA? | Use last successful startup for the same voice settings; never invent a precise countdown for an unmeasured/network-dependent step. | **ANSWERED**: approximate ETA implemented; first start estimates, completion waits for evidence. |
| Cache for speed? | Reuse one voice session and its bounded RAM output buffer; keep only timing metadata persistently. Avoid introducing a conversation/audio cache. | **ANSWERED technically**: separate TTS request removed, one live probe measured faster; no personal content cache. |
| Hardware boot/loading? | Reuse portable readiness/progress events; validate Pi/audio hardware at #8. | **DEFERRED**: requirement recorded, no hardware implementation/test authorized here. |
| Does the full desktop now feel ready and understand speech? | First-audio and failure gates are tested; physical output and human speech still require user review. | **USER DECISION REQUIRED**: run `jarvis`, observe loading→room, speak after the greeting/listening indicator, then close the window. Report exact question/reply if understanding is still wrong. |

## Desktop lifecycle + mode 2 user correction (18 Oct, latest)

- **Problem / evidence:** user's real run `2bc30a62` became ready in 1.063 s. Face connected 21:45:45.092; greeting synthesis started .095, completed 21:46:00.208 (**15.109 s**); playback began .209 (1.8 s audio). No `WAKE_WORD` / `LIVE_STARTED` followed. Thus automatic greeting was requested, but delayed; the record does **not** demonstrate a failed AI interpretation. User clarified no clicks and selected mode 2 rather than waiting for a wake word.
- **Owning layers / changes:** Core `voice.activation = wake_word | conversation`; mode 2 greets then opens existing budgeted live voice automatically, mode 1 remains available. Selected `conversation` in `%LOCALAPPDATA%\jarves\config.toml` while retaining other settings. The mic uses the existing live-session/mute controls and session/day limits; no automatic paid-provider switch or reconnect loop. Without live voice, mode 2 explicitly reports unavailable rather than secretly changing connection.
- Windows launcher log following now exits at `CORE_STOPPED`. Shared Godot controls handle the actual OS close notification, send authenticated face-only `desktop.close`, await acknowledgment for up to 1.5 s and quit. Core stops speaker playback, finalizes live voice and exits; ordinary WebSocket loss still reconnects. `desktop.close` does not grant face clients the existing CLI-only `core.shutdown` message. Close before connection/after a link outage is not claimed verified; hard process kills are not graceful close.
- Core/DevicePort/audio and TTS adapters stream existing provider audio into one output stream instead of waiting for full synthesis/session teardown. Cloud reservations settle on completion, failure or cancellation; fallback is allowed before audio starts, never a replay after partial speech. Added `SPEECH_FIRST_AUDIO`, `WAKE_LISTENING`, 15 s bounded `WAKE_HEALTH` counters, and logged wake initialization/audio errors. No raw audio is saved. Provider first-audio latency remains **UNVERIFIED** after this fix.
- **AI / skills:** read-only authenticated model-list GET succeeded; configured text/live/speech models were listed. Current connection `combined`, web search enabled; ChatGPT sign-in/plan permission confirmed from cached metadata only (no live plan inference verified). Updated existing brain/front instructions to describe only supplied tools, check service status, and walk through one skill at a time. No new skill integration or provider/model/dependency selection.
- **Verification:** 34 focused offline activation/lifecycle/startup/Core/billing tests passed; after adding the Core-stream boundary case, all 8 desktop/streaming cases passed. Godot face self-check passed. Real headless Godot OS-close notification → real temporary offline Core stop → Godot exit 0 passed; its first harness returned 1 during cleanup because its in-process log handler was still open, not from application shutdown. CI now exercises that Godot close path and requires `CORE_STOPPED` plus a failed post-stop status request. Final-commit run/status recorded on open [#7](https://github.com/igalgerman/JARVIS/issues/7). No paid inference, live mic, speaker output or recognition-quality test claimed.
- No geometry/material/room assets changed; prior project renders are retained, with no redundant render set. This is a runtime lifecycle/voice correction, not a new visual/mechanical review. Existing unrelated `skills/music.py` / `test_units.py` edits remain excluded.

### Decision queue — latest feedback (previous unresolved entries below remain recorded)

| Question | Recommendation / concrete rationale | Status / answer or next gate |
|---|---|---|
| Wait for wake word or open conversation? | Mode 2 avoids requiring a wake phrase during desktop testing; mode 1 avoids continuously active cloud conversation in the eventual product. | **ANSWERED**: user explicitly chose mode 2 now, requested mode 1 retained; local setting updated. |
| Should window close stop everything? | Explicit close message ends Core/voice cleanly without treating an accidental link interruption as shutdown. | **ANSWERED**: user requested no Ctrl+C / Y. Implemented; connected close path tested. |
| Is AI connected and can it search? | Validate authentication separately from inference to avoid claiming an untested conversation. | **ANSWERED**: API model-list GET verified; web search enabled; plan permission is cached. Actual voice/search inference remains a live gate. |
| Can it explain skills? | Use the existing supplied tools and status rather than inventing capabilities. | **ANSWERED**: instructions now request short examples and step-by-step guidance on request; spoken output remains a live gate. |
| Did it misunderstand speech? | First test direct conversation, since the previous run had no detected wake or live session. | **USER DECISION REQUIRED**: launch `jarvis`, wait for conversation/listening status, speak a question and assess reply. If still wrong, inspect the new session/transcript events and record the exact phrase/reply. |
| Earlier physical/appearance choices? | They do not affect this runtime correction. | **DEFERRED** to their existing review gates below; no purchase/physical approval inferred. |

## User review: slow initial speech + desktop room (18 Oct)

- **Evidence / owners:** last real run: `CORE_STARTING` 21:32:25.026 → offline STT load 4.2 s → `CORE_READY` 21:32:30.721 → face connected 21:32:32.397 → synthesized greeting ready (`TTS_ENGINE`) 21:32:44.456. These timestamps identify delays; the log has no earlier launcher timing. Windows launcher chose the face-only scene; Core unnecessarily loaded offline recognition in combined/cloud mode and synthesized the full instructions before any playback.
- **Correction:** `jarvis.cmd` opens `godot/run_viewer.cmd` on both routes and removes the fixed 4-second delay. `core/src/jarves/app.py` selects voice mode before preloading STT (local fallback only), starts the link after speech initialization to avoid early-client races, speaks only a short hello while keeping instructions onscreen, and logs startup/synthesis/playback timings. No model/provider/billing choice, geometry or materials changed; eager CTranslate2 import remains for Windows safety.
- **Checks actually passed:** 7 targeted offline startup/greeting cases (cloud live/realtime, plan-only, missing-key fallback, explicit local, voice off, greeting/reconnect); isolated fresh/already-running CMD routes both open room without a timer; `git diff --check`. No paid/live API, mic, speaker or interactive user approval claimed. Cloud TTS still buffers the full generated audio; post-fix latency remains **UNVERIFIED** until a real run.
- **Actual project renders:** [room](../godot/previews/startup/room.png), [close-up](../godot/previews/startup/closeup.png), [front](../godot/previews/startup/front.png), [side](../godot/previews/startup/side.png), [rear](../godot/previews/startup/rear.png), [3/4 / selected default finish C](../godot/previews/startup/three_quarter.png), [internals](../godot/previews/startup/internals.png). Seven files verified nonempty (1,717,807 bytes total); room and close-up visually inspected. Automated hidden-window Godot 4.7.2 GL compatibility captures, `--no-core`; not hands-on UI confirmation or headless dummy images. Godot source/assets are unchanged from baseline `e4c54b1`; no CAD/Blender regeneration was needed. Reproduce: Godot `--path godot --rendering-method gl_compatibility -- --no-core --room --hide-ui --shot=<absolute PNG>` (other flags in `godot/main.gd`).
- **Logs:** normal Core runs append to `%LOCALAPPDATA%\jarves\logs\jarves.log` with bounded rotation. New events: `CORE_READY.ms`, `SPEECH_SYNTH_START`, `SPEECH_SYNTH_READY.ms`, `SPEECH_PLAY_START`. Pre-Core launcher failures remain terminal-only. Existing unrelated music source/test edits are excluded.
- **Issue / final evidence:** belongs to open [#7](https://github.com/igalgerman/JARVIS/issues/7); final-commit CI status and run URL are recorded there. Do not close real voice/face integration on offline tests.

### Decision queue — this correction (older unresolved questions below remain carried forward)

| Question | Recommendation / rationale | Status / answer or next gate |
|---|---|---|
| Desktop room or face-only? | Existing room/model view reuses the same live face. | **ANSWERED**: user explicitly prefers room with actual model; now launcher default. |
| Can the last run be diagnosed? | Rotated structured local log plus timing events isolates delays without logging raw audio. | **ANSWERED**: last run inspected; location and pre-Core limitation explained. |
| Is speech fast enough now? | Measure the next real run before changing voice/provider or adding streaming complexity. | **USER DECISION REQUIRED**: user restarts with `jarvis stop`, then `jarvis`, and judges responsiveness; inspect new timing events. No live improvement claimed yet. |
| Older appearance / hardware choices? | This correction changes launch selection and Core startup only. | **DEFERRED** unchanged to their existing visual/hardware gates below; no new approval inferred. |

## CMD launcher diagnosis (18 Oct)

- Follow-up: user's PowerShell resolves `jarvis`, but plain launch failed with `The syntax of the command is incorrect.` Reproduced in an isolated CMD trace: substring expansion on the undefined `FIRST` variable corrupts the argument-dispatch line. Fixed at the Windows launcher owner (`jarvis.cmd`) by jumping to startup before that expansion when no argument is supplied.
- Focused CMD routing check from `C:\Users\97254` passed for no arguments, `--log-level debug`, `status`, and an already-running Core. Core/uv/face actions were substituted with echoes; no real microphone, paid API or GUI startup was tested. No downstream assets needed refreshing. Final-commit CI remains unverified.
- `jarvis.cmd` exists, but `C:\work\jarvis` is absent from both the current process PATH and the saved user PATH. From outside the project, `where jarvis` cannot find it; the README's PATH claim does not match this machine.
- Verified `cmd.exe /d /c "C:\work\jarvis\jarvis.cmd status"`: launcher responds; Core is currently stopped. Historical health output is stale, not evidence of current component failures.
- Run from any CMD with `C:\work\jarvis\jarvis.cmd`, or add the project directory to PATH. No runtime code or persistent Windows settings changed. Existing music source/test edits were left untouched.

## Issue #13: GPT-Live overruns, disconnect accounting, reservation lifecycle (18 Oct, night)

- **Overlapping delegations (P1).**
  - The first delegation without reserved credit now closes the session **immediately**, instead of after the answer. Every server-started delegation is counted exactly once; continuations are never sent without a reservation.
  - The exposure is **not strictly bounded** (the API has no client gate), so the docs were corrected and `jarvis setup` / the README now **require a provider-side spend limit**.
  - Test: 3 overlapping delegations with limit 1, in all 6 completion orders: 3 counted, 2 overruns, no continuation sent.
- **Voice time after a disconnect (P1/P2).** GPT-Live usage now comes from the server's final `session.closed` figure. If that never arrives, it is the larger of the last reported usage and the wall-clock time since `session.started`, capped at the session limit plus 60 s, and it is reported exactly once.
- **Reservation lifecycle (P2).**
  - Expiry now happens only when the **owner process is gone** (checked with `OpenProcess`/`GetExitCodeProcess` on Windows, `kill(pid, 0)` on POSIX), with a 6-hour hard backstop.
  - Settlement is once only: committing a row that expiry already counted adds nothing.
  - Usage is settled on the reservation's own day.
  - Tests: an active owner past 1 hour, a crashed owner, the hard backstop, midnight, release after expiry, and real-process crashes.
- **Evidence:** `core/tests/test_issue13.py`, `test_budget_mp.py`.

## Real-microphone QA with a USB webcam mic (18 Oct, night)

The user plugged in a "2K HD Camera" USB webcam with a mic, and it became the default input.
- **Mic:** the speaker tone reached it at peak about 1.0. The Realtek laptop mic now also shows signal (0.14–0.19).
- **Wake word through the air:** speakers → webcam mic → openWakeWord. "Hey Jarvis" woke it 4 out of 4 times while JARVES was not speaking (scores 0.83–1.0). "What time is it" and "Good morning" gave no false wake. CPU is about 14–25 ms per 80 ms frame.
- **Offline Hebrew speech recognition through the air:** synthetic WinRT Hebrew voice played from the speakers.

| Model | Recognised as commands | Time per phrase |
|---|---|---|
| `small` | 1 of 5 | about 8 s |
| ivrit-ai turbo | 2 of 5 | about 19 s |

  So **offline recognition is too weak and too slow for daily use**. A real human voice may score better, but that is untested.
- **Who this affects:** modes 2 and 3 (`combined` / `api_key`) don't use local recognition at all, because "Hey Jarvis" opens a GPT-Live conversation. Only mode 1 (ChatGPT plan only) and fully offline use rely on it, since the plan has no audio route.
- **Logs:** privacy mode was added (`[log] privacy`). It is off by default for development, so logs show full content. Credentials are masked in both modes, and `jarvis diag` always masks content.

## Issue #9 follow-up F1–F3 + real-component QA (18 Oct, evening)

- **F3: cross-process budget.**
  - `budget.py` now keeps reservations as SQLite rows, taken in a `BEGIN IMMEDIATE` transaction, so the Core, `jarvis ask` and `jarvis say` share one atomic gate.
  - A crashed process's reservations expire after 10 min and are **counted**, not freed.
  - Tests: `test_budget_mp.py` (8 real processes race for 3 units, exactly 3 succeed; crash and stale cases; two instances).
- **F1: GPT-Live delegation.**
  - The docs show no way to approve or cancel a Responses delegation before the server sends it ([live-delegation guide](https://developers.openai.com/api/docs/guides/live-delegation), checked 18 Oct). It is an **API limit**.
  - Safest fallback now:
    - one unit is **reserved ahead** as credit before a session can start, and every delegation consumes it;
    - every continuation is reserved before `response.create`;
    - when no credit can be reserved, the mic input is closed and the session ends after that answer;
    - with 0 units left, no session starts.
  - **Remaining exposure:** ~~at most one extra delegation~~ **Corrected in #13: this is not strictly bounded by the client.** The server can start several delegations before the client sees them. All of them are counted (`budget_overrun`), and the session closes at the first one without credit. A **provider-side spend limit is required** for API-key use.
  - **Full fix:** client-mode delegation, where JARVES runs the backend itself, is tracked in [#14](https://github.com/igalgerman/JARVIS/issues/14).
  - Tests in `test_voice_faults.py`: boundary, concurrent delegations, refill, continuation, sharing a unit with the CLI.
- **F2: `jarvis say`.** It now uses the same `make_tts(cfg, budget)` as the Core. Plan-only mode never uses paid speech, even with a key. Tests: `test_say_billing.py`.
- **Real-component QA on this PC (zero cost).**
  - **The laptop mic records digital silence** (-80 dB) on every API and driver, while Windows reports it unmuted at 80% with privacy allowed. This points to a **hardware mic switch or key, or a driver problem**: a user check is needed.
  - The speakers were verified via Stereo Mix (peak 0.22 for a test tone).
  - With speech injected instead of the mic, the full chain passed: real Core, real wake word (0.99), real Hebrew STT, real Windows voice on the speakers and the real Godot face.
  - **Bug found and fixed:** importing faster-whisper after WinRT crashed the process (2/2 reproductions).
  - **UI fixes:** Hebrew captions now lay out right-to-left, and the status lines are friendlier.
- **Issue rule** recorded in `AGENTS.md` ("Issue lifecycle"). ROADMAP is synced with the Issues.

## Issue #9 review fixes + user requests (later on 18 Oct): integrated

All Issue #9 findings were reproduced in the code, with no false positives except part of CR-06; each is fixed with offline tests.

| ID | Fix | Main tests |
|---|---|---|
| CR-01 | A central `budget.py` reserves before every paid request and counts in-flight ones. It covers the API agent (SDK model wrapper), each ChatGPT-plan request, GPT-Live delegated backend responses and cloud speech; failed or cancelled sent requests are counted. One budget is shared through `app.py`. | `test_budget.py`, `test_voice_faults.py` |
| CR-02 | Live/Realtime sessions finalize once, whoever ends them. `on_end` frees `core.live`, sets health and notifies the user; Talk restarts a session. | `test_voice_faults.py`, `test_core::test_remote_close_frees_live_slot_and_restarts` |
| CR-03 | Enforced `permission` (read, action or destructive); model-requested deletes always need confirmation; unknown connectors are denied; side effects are shielded from cancellation. | `test_security.py` |
| CR-04 | `billing_routes()` / `billing_summary()`; status shows who pays for typed vs spoken use; the combined-mode text is fixed. | `test_budget.py`, `test_chatgpt::test_status_reports_who_pays` |
| CR-05 | `error` and `responses_validation_failed` mark voice degraded with an action; fatal codes end the session. | `test_voice_faults.py` |
| CR-06 | Bounded mic (2 s) and speaker (12 s) queues with drop counters, and flush on interrupt. Partly a false positive: GPT-Live has no interruption event, so a documented heuristic is used. | `test_voice_faults.py` |
| CR-07 | All `account.json` writes share one locked read-modify-write; the model cache moved to `models.json`. | `test_security.py` |
| CR-08 | Roles (face / cli) with per-role allow-lists and 4403 for unknown clients; confirmations are bound to the face connection that was asked. | `test_security.py` |
| CR-09 | Redaction at any depth, plus `safe_fields` for device events. | `test_security.py`, `test_units` |
| CR-10 | New CI job **Core-Godot E2E (real processes)**: round trip, wrong token rejected (4401), orderly stop. Windows launcher smoke test. | CI |
| CR-11 | `Core.stop` refuses new work, denies pending confirmations, waits briefly for in-flight work, then cancels and awaits every task and drains tools. | `test_core::test_shutdown_during_pending_confirmation_denies` |

**User requests in the same batch:**
- **"Hey Jarvis" wake word:** pyopen-wakeword 1.1.0 (Apache-2.0), detected on the device.
- **Greeting:** says hello and explains how to talk.
- **Feedback:** an activity line and mic level bar, and a `MIC_SILENT` warning.
- **Terminal logs:** levels and module filters, plus `jarvis logs`.
- **`jarvis` launcher:** runs in the foreground; with a Core already running it opens the face and follows the log.
- **Room simulation:** Subagent D, still in progress.

**Not verified (mock tests only):** real OpenAI calls, ChatGPT sign-in, the real mic, the real wake-word mic, Pear playback, and the Pi. On the user's run on 18 Oct, GPT-Live started but no speech was received for 10 minutes. That points to a silent microphone (Windows privacy setting or input device); a real check is needed.

## Product decision after SW-V1 review — 18 October 2026

**Confirmed by user:** JARVES must support **both** OpenAI API billing with user-supplied key and user-authorized ChatGPT Plus/eligible plan usage via official Sign in with ChatGPT OAuth. **Do not interpret this as a request for automatic paid fallback.** User must be able to choose a connection mode and understand which account/limits/costs apply. OAuth-based eligible Responses AI requests are possible for local personal tools, but ChatGPT plan usage does **not** cover GPT-Live/Realtime/audio input or transcription routes; combine with local STT/TTS only if an approved implementation finds the quality acceptable, or use the independently billable API voice path. Preserve offline behavior. See [controls & diagnostics requirements](JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md#18-oct-2026--confirmed-user-choice-both-chatgpt-plan-and-api-connection-options) for source-backed integration/limitations.

**Status (later 18 Oct, after the user asked for it directly: "i want option combined connect with gpt"):** **IMPLEMENTED, offline-tested; NOT yet connected live.**
- **Connection modes.** `ai.connection` is one of:
  - `api_key` (default, unchanged);
  - `chatgpt`: plan text plus local voice, with zero API spending;
  - `combined`: plan text plus GPT-Live voice billed to the API key, labelled in status.
- **Explicit choice only.** The mode is set by the user. There is no automatic switching, and a plan limit pauses AI (`CHATGPT_LIMIT`) instead of falling back to the API.
- **Code.** `core/src/jarves/chatgpt_auth.py` implements the official OAuth flow:
  - PKCE S256, loopback `127.0.0.1:1455/auth/callback`, dynamic client registration, stable `urn:uuid` host ID.
  - JWKS-verified ID token, with nonce and audience checks; the plan scope is checked before any inference.
  - Rotating refresh, serialized by a cross-process lock; revocation on logout.
  - Tokens are stored only in `<state>/chatgpt/account.json`.
- **Brain.** `core/src/jarves/chatgpt_brain.py`:
  - Calls `POST /v1/responses` with `store:false` and `stream:true`, sending stateless history.
  - Groups the Skills in a `jarves` namespace.
  - Drops web search if the plan disallows it.
  - Picks the model from `GET /v1/models`.
  - Maps every documented error code.
- **CLI.** `jarves chatgpt login|logout|status|models`, plus `jarves setup` (first-run choice). The one-word launcher `jarvis` is `jarvis.cmd` in the repo root, which is on the user PATH.
- **Tests.** 21 new tests in `core/tests/test_chatgpt.py` use mock OAuth/Responses servers and a local RSA key; **67 PASS** in total.
- **UNVERIFIED (user action):** run `uv --directory core run jarves chatgpt login` once and allow plan usage. Real plan eligibility, the model list, web-search availability and Hebrew quality on the plan are untested.

## CI ACTIVE — 18 October 2026 (Issue #2 closed)

`.github/workflows/jarves-checks.yml` first run [39140216460](https://github.com/igalgerman/JARVIS/actions/runs/39140216460): **all 5 jobs PASS**. The jobs are Core pytest on Ubuntu and on Windows, FreeCAD 1.1.4, Blender 5.2.2 and Godot 4.7.2. Actions were then upgraded (`checkout@v7`, `setup-uv@v10`) to clear the Node 20 deprecation warning.

## SW-V1 DELIVERED — STOPPED for the single user review

- **Read first:**
  - [SW_V1_REVIEW.md](reviews/SW_V1_REVIEW.md): what actually runs, the evidence, your 3 pending actions, cost and the Hebrew walkthrough.
  - [SW_V1_TECH_DECISION.md](reviews/SW_V1_TECH_DECISION.md): stack, model IDs, sources and check dates.
  - [core/README](../core/README.md): setup, run and troubleshooting.
- **Stack (user directive: newest best-in-class, OpenAI-only):**
  - OpenAI GPT-Live `gpt-live-1` voice → Responses delegation to `gpt-6-luna` + web search + JARVES Skills.
  - Alternative: `gpt-realtime-2.1` on Agents SDK 0.24.
  - Typed chat: Agents SDK `gpt-6-luna`.
  - Speech for typed replies: `gpt-realtime-2.1-mini`.
  - Local Whisper/WinRT/espeak are fallback only.
  - Music: Pear Desktop 3.12.0 API Server.
- **Runs now (offline, this PC):**
  - Core + Godot face E2E PASS.
  - 6 face states, in the twin and standalone.
  - Hebrew commands, memory, diagnostics, local voice in and out.
  - 46 tests PASS; clean clone with the lockfile PASS; `check_viewer` PASS.
- **Changed:**
  - `core/` (new Python package + tests).
  - `godot/face/*`, `godot/main.gd`, `godot/run_face.cmd`, `godot/previews/face/`.
  - `ci/cad-smoke.yml`.
  - `AGENTS.md` + `CLAUDE.md` (best-in-class rule).
  - README, CONTEXT_MAP.
- **Blocked (user):**
  1. `OPENAI_API_KEY`: live voice, AI and search are untested.
  2. Pear "Allow" click + YouTube sign-in: real playback.
  3. ~~`gh auth refresh -s workflow`: CI.~~ Done; CI active.
  4. Optional: `jarves chatgpt login` to use the ChatGPT plan (`ai.connection = "chatgpt"` or `"combined"`).
- **Unverified:**
  - GPT-Live Hebrew quality.
  - Real mic speech (level ≈ 0 in the test).
  - Pi 5 performance.
  - Echo/barge-in on speakers (half-duplex default).
- **Environment changes on this PC:**
  - Python 3.12 via uv.
  - The Hebrew Whisper models downloaded to the HF cache.
  - Pear Desktop portable in `%LOCALAPPDATA%\Programs\PearDesktop-3.12.0`, with its API Server plugin enabled on 127.0.0.1 and approval required. Pre-authorizing the client was denied by the safety classifier, correctly, so pairing stays with the user.
  - `7zr.exe` in `%LOCALAPPDATA%\Programs\7zr`.

### Decision queue additions (SW-V1)

| Question | Recommendation | Status |
|---|---|---|
| SW-1: Voice engine for Hebrew | GPT-Live first; switch to `gpt-realtime-2.1` if its Hebrew sounds worse | **USER DECISION REQUIRED** after trying with a key |
| SW-2: Voice name/personality | `marin` default, warm playful Hebrew persona | **USER DECISION REQUIRED** (taste) |
| SW-3: Daily cost ceilings | 30 voice min + 200 AI requests per day | **USER DECISION REQUIRED** (budget) |
| SW-4: Offline STT model | `small` (4 s, minor errors) vs ivrit-ai turbo (accurate, 19 s) | **ANSWERED by measurement**: `small`; revisit on the Pi (#8) |
| SW-5: CI activation | Grant the `workflow` scope; next agent activates and reports run URL | **DONE**: run 39140216460 green |
| SW-7: Which connection by default? | Keep `api_key` for the best voice; `combined` if you want answers on your Plus plan with premium voice; `chatgpt` for zero API cost | **USER DECISION REQUIRED** after trying `jarves chatgpt login` |
| SW-6: Music provider | Pear Desktop API (no cookies/extraction); Music Assistant rejected for now | **ANSWERED by engineering**; pairing = user action |

## Questions for the user (SW-V1, answer these first)
1. Please do the 3 actions in [SW_V1_REVIEW](reviews/SW_V1_REVIEW.md#your-actions-to-finish-v1-about-5-minutes): API key, Pear Allow, CI scope.
2. After hearing GPT-Live in Hebrew: keep it, or switch to `gpt-realtime-2.1`? And do you like the voice `marin`?
3. Are the cost ceilings OK (30 voice minutes and 200 AI requests per day)?
4. Do you approve the face look ([previews](../godot/previews/face/))?

---
## Previous: SW-V1 approval brief (USER APPROVED)

**Authority:** [NOW](NOW.md) and [SW-V1 work package](SW_V1_WORK_PACKAGE.md); user authorized work while away on **#2 + #5 + #6 + #7 plus the first YouTube Music Skill**, with automatic continuation between phases and bounded tests/commits. This supersedes the old R5b 'stop for review' as a **software sequencing** gate, not as a physical manufacturing sign-off.

**First implementing action:** sync latest branch; read `AGENTS.md` + `docs/NOW.md` + `docs/SW_V1_WORK_PACKAGE.md`; take the short research/CI phase into working software, not another docs-only output. Existing R5b code stays as baseline. CI runner activation requires write permissions under `.github/workflows/`; if denied, record blocker and continue SW. No hardware purchases, account sign-ins, paid API spend or supplier contacts.

**Deliverable:** working Python Core↔Godot, a maintainable Hebrew voice/AI assistant to the extent existing authorized credentials enable, persistent memory, modular Skills and reusable YouTube Music integration candidate, live CI proof if possible, targeted fault tests, accurate known-blocker list. One final user review.

---
## S1.T03R5b — CAD implementation DELIVERED, waiting for user review

- **Read first:**
  - [S1_T03R5B_IMPLEMENTATION_REVIEW.md](reviews/S1_T03R5B_IMPLEMENTATION_REVIEW.md): what was built, a status for each check, joints, electronics and power, the 5 neck cables, printing, BOM, costs and risks.
  - [assembly/ASSEMBLY_GUIDE.md](assembly/ASSEMBLY_GUIDE.md): what you receive, what to buy, tools, 7 illustrated steps, service.
  - [PEBBLE_PARTS_INVENTORY.md](reviews/PEBBLE_PARTS_INVENTORY.md): auto-generated part IDs, 26 screws + 13 nuts.
- **Results:**
  - 9 printed parts.
  - 925 g PETG, 80.8 h, ₪1,146–1,644 print estimate.
  - Electronics ≈ ₪1,790–2,090; total ≈ ₪2,970–3,790. **Not a quote.**
  - Joint SF ≥ 3.0 at a 20 N push (assumed 150 N per nut).
  - Tip-over 34°.
- **Images:**
  - `blender/renders/pebble_r5_*.png` (4)
  - `godot/previews/r5/*.png` (12)
  - `docs/assembly/img/` (overview, steps 1–7, parts)
- **Checks (all PASS):**
  - `cad\smoke_test.py` (incl. the HeadRX regression, fixed by making the saddle clearance follow Params)
  - `cad\check_pebble.py`
  - `cad\mech_estimates.py`
  - `blender\smoke_blender.py` (28 parts, 0.00 mm)
  - `godot\check_viewer.cmd`
  - slicer: 15 jobs
- **Rebuild:**
  1. `freecadcmd cad\build_pebble.py`
  2. `freecadcmd cad\export_meshes.py`
  3. `freecadcmd print\build_print_package.py`
  4. `freecadcmd cad\mech_estimates.py`
  5. `blender -b --python blender\build_scene.py`
  6. `godot\export_previews.cmd`
  7. `godot\export_instructions.cmd`
- **CI (user request):** `ci/cad-smoke.yml` now has 3 jobs.
  - cad: FreeCAD 1.1.4 AppImage with SHA check; smoke, check_pebble, export, mech.
  - blender: smoke.
  - godot: import + viewer check.
  - **Not active, never run on a runner.** To enable, run `gh auth refresh -h github.com -s workflow` and move the file to `.github/workflows/` (Issue #2).
- **Not done / unverified:**
  - Camera bosses, door hooks and the speaker strap are not modelled.
  - Speaker lead plug and length.
  - Heat, acoustics, and nut and snap strength (test print recommended).
- **No purchase, printing or supplier contact.**

## Questions for the user (R5b review — answer these first)
1. Does the R5b result look right (renders + `godot\run_viewer.cmd`), with the exterior unchanged?
2. **Base print:** find a shop with a 256 mm printer for the one-piece Ø210 base (recommended), or accept the split base (seam + 2 joints)?
3. May the next step be a **₪50–100 test print** (neck + nut-pocket coupon + snap ring segment) once a shop is chosen? Needs your separate approval.
4. **Speaker lead:** accept a ready-made extension cable if the Seeed lead is too short (decided after the plug type is confirmed)?
5. Grant the GitHub `workflow` scope so CI can be switched on?

## Previous: R5b approval (17 Oct 2026)

- **User approval (17 Oct 2026):** [Issue #4 approval and detailed acceptance criteria](https://github.com/igalgerman/JARVIS/issues/4#issuecomment-6240553020).


- [S1_T03R5_ARCHITECTURE_REVIEW.md](reviews/S1_T03R5_ARCHITECTURE_REVIEW.md): the chassis table is removed (the base cover top is made structural); 7 printed parts + 2 snap blanks, about 25 screws + 12 nuts, 2 screw types; the mic board's own 5 W amp drives 1 plug-in speaker (connector type unconfirmed); microSD reachable through the rear door.
- **Decision R5-1 (USER):** APPROVED for CAD implementation on 17 Oct 2026. Open spec items: speaker connector type, mono output, mic board current draw, mounting holes from Seeed files.


User chose (17 Oct 2026): improve the simple Pi-in-base build. Engineering decision recorded in [S1_T03R5_DESIGN_DECISION.md](reviews/S1_T03R5_DESIGN_DECISION.md). Next agent: implement it in CAD (see its "Next" section). The R4 notes below are history.

## Previous: S1.T03R4 design-direction gate

The user clarified mid-milestone that the target is a **consumer-style build**: place ready-made modules, plug in ready-made cables, close with a screwdriver. That means no soldering, glue, drilling, cutting, filing, custom cables or specialist tools. They asked for a simplest-design recommendation **before** any further CAD redesign.

- **Read first:** [docs/reviews/S1_T03R4_SIMPLE_BUILD_PROPOSAL.md](reviews/S1_T03R4_SIMPLE_BUILD_PROPOSAL.md), with its concept diagram `design/review/simple_build_concept.png`.
- **Verdict:** the current architecture is **not** beginner-friendly:
  - heat-set inserts (soldering iron);
  - an aluminium tube that must be cut and filed;
  - an acrylic cover from a laser shop;
  - a glued fabric band;
  - 16 printed + 11 bought mechanical parts, about 40 fasteners, 14 steps.
- **Recommendation:** a **6-part printed build** (head front holding the display directly, head back with the mic pocket, one printed neck, base shell, open electronics tray, snap-in rear door), plus optional navy band and LED ring halves. Ready-made plug-in electronics: USB camera and USB mic, speakers with plugs, official PSU. About 27 screws and 13 nuts, 0 inserts, one Phillips screwdriver, 8 home steps.
- **Evidence gathered this milestone:**
  - **Vendors:** [3D Lab Haifa](https://3dlab.co.il/print-service.html), verified by me 2026-10-17: max 250 × 210 × 220 mm per part; "from ₪30" up to 50 g, ₪100–300 for 50–250 g, "from ₪300" larger; PETG; 2–5 business days; WhatsApp files; VAT not stated. Also found by research: Shaked Lab / Ofer Systems (Haifa & Krayot, quote only, 14-day lead), DFUS3D and The 4th Dimension (national, quote only). No vendor was contacted.
  - **Slicer:** PrusaSlicer 2.9.6 CLI, portable ZIP in %LOCALAPPDATA%\Programs, SHA-256 checked.
    - Current 16-part set: 972 g PETG, 83 h at i3 reference speed, with organic supports.
    - Organic supports halved the large parts: Head_Back 525 → 281 g; Base_Shell 381 → 291 g.
    - Tier estimate ₪1,232–1,978. Simple build estimate about ₪1,000–1,500 (range ₪900–2,000), **not a quote**.
  - **Bed fit:** the Ø210 base shell and floor **do not fit 250 × 210 with margin** (they need a 256-class printer or a split); the head parts fit.
- **Work done before the clarification** (committed, checked, now the "current architecture" baseline):
  - CAD R4 refinements: saddle top flange, D-flat tube with keyed flanges, 2 anti-rotation pins, seat screw holes, 4 head-seam boss pairs, 3 base floor bosses, M3 standoffs, part IDs P/B/E/R/A, print orientations.
  - `print/build_print_package.py`, which writes `print/QUOTE_PACKAGE.md` + `print/print_report.json`; the STLs are regenerated and git-ignored.
  - Godot instruction-image tooling: `--step/--part/--overview/--list-parts` and `godot/export_instructions.cmd`.
  - The 42 generated LEGO-style images were **deleted** as premature (regenerable with one command).
- **Checks run:**

| Check | Result |
|---|---|
| `cad\smoke_test.py` | PASS |
| `cad\check_pebble.py` | PASS: 41 parts, load path, bed |
| `godot\check_viewer.cmd` | PASS: 44 parts, incl. step-mode check |
| Slicer runs | all 22 jobs sliced without error |

- **Next (not started):** after approval, a FreeCAD redesign to the simple architecture with real module mounting, then re-slice, quote package, and a beginner guide.

## Previous milestone — S1.T03R3 (delivered; superseded in part by the R4 direction review)

**S1.T03R3 — LED ring, speaker grille, exploded assembly & service review** (round 2 of [Issue #4](https://github.com/igalgerman/JARVIS/issues/4); scope in [NOW.md](NOW.md)).
**Models:** Opus 5.5 for design, CAD, integration and review. A Sonnet subagent handled the Godot exploded view, LED/blank swap and screenshot export, and was verified independently.
**Not covered:** no purchase, no SKU lock, no manufacturing release, no physical validation.

- **Read first:**
  - [docs/reviews/S1_T03R3_ASSEMBLY_REVIEW.md](reviews/S1_T03R3_ASSEMBLY_REVIEW.md): image links, LED ring, speaker fix, assembly, engineering review, checks, known issues.
  - [docs/reviews/PEBBLE_PARTS_INVENTORY.md](reviews/PEBBLE_PARTS_INVENTORY.md): auto-generated printed / purchased / placeholder / reserved / alternate parts, fasteners, 14-step sequence.
- **Images:**
  - `godot/previews/r3/*.png` (13 views: front, side, rear, ¾, top, interior, exploded ¾ / side / front, LED close-up, blank close-up, finish A/B).
  - `blender/renders/pebble_r3_{hero_C,rear_C,exploded_3q,exploded_side}.png`.
- **Run:**
  - `godot\run_viewer.cmd`: new toggles for **Exploded view** (with slider) and **Blank cover instead of LED ring**; views include Top and LED close-up.
  - `godot\export_previews.cmd` re-exports all review images.
- **Rebuild pipeline:**
  1. `freecadcmd cad\build_pebble.py`
  2. `freecadcmd cad\export_meshes.py` (also regenerates the inventory)
  3. `blender -b --python blender\build_scene.py` (`-- --no-render` for a fast export, `-- --only-exploded` for 2 renders)
  4. `godot\export_previews.cmd`
- **Checks actually run** (all PASS, nonzero on failure):

| Check | Result |
|---|---|
| `cad\smoke_test.py` | PASS |
| `cad\check_pebble.py` | PASS: containment, collisions, 16 load-path contacts, LED connector reachable via rear door, blank cover = exact drop-in, 220 mm bed; W215 × D218.5 × H292 mm |
| `blender\smoke_blender.py` | PASS: 44 parts, worst 0.28 mm, Godot sync |
| `godot\check_viewer.cmd` | PASS: explode error 0.000 mm, LED/blank swap, picking |
| `export_previews.cmd` | 13/13 images |

- **Problem → owning layer → fix:**
  - LED ring hidden under the base → FreeCAD → groove on the upper shoulder with snap-in halves, backer ring, wire hole, connector behind the door, blank cover.
  - Wheel-like speakers → FreeCAD → perforated grille windows behind the fabric, cone drivers at band height.
  - Parts sinking below the floor in the exploded view → Blender/Godot → +100 mm lift.
  - Finish C as default → Blender (`finishes.json`).
- **Changed:**
  - `cad/{build_pebble.py,check_pebble.py,export_meshes.py,JARVIS_pebble.FCStd}`
  - `blender/{build_scene.py,smoke_blender.py,finishes.json,JARVIS.blend,JARVIS.glb,JARVIS_manifest.json}`
  - `godot/{main.gd,README.md,export_previews.cmd (new),assets/*}`
  - `docs/reviews/{S1_T03R3_ASSEMBLY_REVIEW.md,PEBBLE_PARTS_INVENTORY.md}` (new)
  - `docs/{NOW,HANDOFF}.md`
- **UNVERIFIED:**
  - strength of the neck seat, set screws and inserts;
  - snap tabs, LED diffusion and power;
  - speaker pod volume (about 0.06 L) and grille acoustics;
  - HDMI plug through the tube, CSI length;
  - heat;
  - microSD access (blocked).
- **Cleanup suggestions** (not done, need approval): the R2 images `godot/previews/pebble_*.png` and `blender/renders/pebble_{front,side,rear,interior,finish_*}.png` are superseded history.
- **CI:** still not active (#2).

**Future work parked:** #2 CI; #5 library evaluation; #6 minimal Core integration; #7 face/voice; #8 hardware prototype preparation.

**Agent transition (Claude Code ↔ Codex):** one writer at a time. Commit, record changed paths, checks and blockers here, then the receiving agent pulls. Feedback goes to the owning source layer, followed by only the necessary downstream rebuilds.

## Decision queue (updated 17 Oct 2026 after S1.T03R3)

| Question | Recommendation / reason | Status |
|---|---|---|
| Overall appearance and proportions | Keep Pebble A | **ANSWERED**: user likes it |
| Side profile | Keep current | **ANSWERED** |
| Finish | C is the default; A/B kept only for comparison | **ANSWERED**: C, visual preference only |
| Light ring location | Upper base shoulder, removable halves + blank cover (implemented R3) | **ANSWERED** (user chose upper rim); look **USER DECISION REQUIRED** in R3 review |
| R3-1: Speaker look | The grille behind the fabric should remove the "wheel" look | **USER DECISION REQUIRED**: confirm in viewer |
| R3-2: Exploded view / inventory clarity | Enough detail for this stage? | **USER DECISION REQUIRED** |
| R3-3: Neck seat | Superseded: the R4 proposal replaces the tube/flange neck with one printed neck | **SUPERSEDED** by R4-1 |
| **R4-1: Architecture** | **B "head module + passive base"** (Pi in the head, printed neck column on the floor tray, 2 cables down the neck); fallback A′ (Pi in base) if the head heat test fails. Earlier 6-part proposal superseded. | **USER DECISION REQUIRED** |
| R4-2: Navy band / face ring | Mid-print colour change (0 extra parts); single colour if the shop can't (clip-on halves rejected: rattle risk over speakers) | **USER DECISION REQUIRED** |
| R4-3: LED ring | Printed blank ring now, plug-in LED add-on later (needs a no-solder 5 V / data solution) | **USER DECISION REQUIRED** |
| R4-4: Camera | With B, Camera Module 3 on its short standard ribbon (no USB load); USB camera only for fallback A′ | **ANSWERED by engineering** (follows R4-1) |
| R4-5: Base size / printing | Keep Ø210 + 256 mm shop (preferred) vs Ø194 base (fits 250 × 210, small visual change) vs split | **USER DECISION REQUIRED** (after shop gate G1) |
| R4-7: Mic envelope | XVF3800 + raised crown pod vs ReSpeaker Lite under flat crown | **USER DECISION REQUIRED** (then gate G5) |
| R4-6: Contact vendors | Prepare quote requests only after the design is approved; no contact without separate OK | **DEFERRED**: after R4-1 |
| R3-4: microSD not reachable | Recommend USB/SSD boot or network updates rather than redesigning around the slot | **DEFERRED**: software/hardware prototype stage (#8) |
| Mic crown / XVF3800 pod | Keep flat crown provisionally | **DEFERRED**: mic research gate |
| Imported mic vs local | Not approved | **DEFERRED**: hardware research gate |
| 5″ display envelope | Provisional CAD assumption only | **DEFERRED**: hardware fit/sourcing gate |
| Bench-test purchases | None authorised | **DEFERRED**: purchasing gate |
| Printer / bed / material | Keep 220 mm compatibility + split fallback | **DEFERRED**: print planning gate |
| Room placement / listening range | Undecided; no 3–5 m promise | **DEFERRED**: acoustics test planning |

## Questions for the user (R4 FINAL — answer these first; supersede the earlier R4 list)
Context: [S1_T03R4_FINAL_RECOMMENDATION.md](reviews/S1_T03R4_FINAL_RECOMMENDATION.md). An independent read-only reviewer formed its own alternatives first; I challenged them and made the final call.
1. Approve **architecture B: "head module + passive base"** as the direction for the next CAD round?
   - The Pi, display, camera and mic sit in the head, which can be tested on the table before closing.
   - The base is a speaker stand with a printed neck column.
   - Only 2 cables go down the neck. (recommended)
2. **Base size:** keep Ø210 and find a shop with 256 mm printers (recommended), or accept a slightly smaller Ø194 base that fits 250 × 210 printers?
3. **Mic envelope:** XVF3800 with a small raised crown pod (best pickup), or ReSpeaker Lite under today's flat crown (keeps the silhouette)?
4. **Colours by mid-print colour change** (navy band, dark face ring), with a single colour if the shop can't do it?
5. **LED ring:** a printed blank first, LEDs later as a plug-in add-on? (recommended)

## Questions for the user (R3, still open but lower priority)
1. Does the new **LED ring** on the upper base edge look right (see `r3_led_closeup.png` / `r3_3q.png`), and is the **blank cover** look acceptable when the LEDs are not fitted?
2. Do the **speakers** now look clean? The fabric band is unbroken outside; the grille shows only in exploded and shell-hidden views.
3. Is the **exploded view + parts inventory** clear enough, or do you want labels or a step-by-step picture series next?
4. May the next round add the **neck seat adapter ring + anti-rotation flat**, and model the screw bosses and snap tabs (still no physical claims)?

---

## User visual feedback (17 Oct 2026, from S1.T03R2 screenshots — recorded by management)
User likes the appearance very much, prefers **finish C**, and approves the current side head profile; do not propose head reshaping or A/B again without a concrete reason. Observations to investigate: wheel-like speaker openings, rear service and ventilation access, neck cable/strength/fastener path; requested a printable-part inventory and an exploded assembly with sequence (both delivered in R3). Future visual milestones must auto-export real snapshots.

## User decision — optional status-light ring (17 Oct 2026)
Improve the ring's position on the upper rim of the base rather than removing it. Keep LED lighting a **fully removable option**: nonstructural module, accessible detachable wiring and a printable blank cover. No LED board, diameter/power commitment or purchase yet. Sourcing reference only: a locally listed WS2812B ring (16 LEDs) or a 5 V addressable strip; size, Pi GPIO signal level, 5 V current and thermal limits must be verified. **R3 implements the geometry only.**

## Archived S1.T03R1 research handoff (historical reference)
See [docs/reviews/S1_T03R1_ENGINEERING_REVIEW.md](reviews/S1_T03R1_ENGINEERING_REVIEW.md). Summary:
- **Measured from STEP files:** 5″ display Ø150 × 18.05; 4″ display Ø126 × 18.2; XVF3800 PCB about 100.5 × 101.8 × 9.03.
- **Sourcing:** Piitel ₪590 for the 5″ display (2026-10-17). No local far-field mic array found.
- **Findings:**
  - The 5″ display needs a W215 head.
  - The XVF3800 needs a raised crown pod.
  - The Pi 5 USB power budget may be exceeded.
- **Open hardware questions** (all DEFERRED in the queue above): imported mic, display variant, bench purchases, printer, room/listening range.

## Owner answers (20 Oct 2026)
- Workweek default for "ימי חול": Sun–Thu (configurable).
- Home Assistant: LAST priority; build the generic, config-driven version and add a `jarvis home start` wizard (like `jarvis google start`).
- Telegram outbound: build it; owner creates the bot. Also add a `jarvis instagram start` setup wizard (Instagram channel; research API limits first, assisted/handoff if no official send API).
- Tray: show a one-time "still running in the tray" notice on first window close.
- Instagram: read DMs aloud + send replies (always with confirmation); official API only.
- Morning briefing includes: calendar today, important unread Gmail (summary), weather, reminders/lists.
- Rocket alerts: last priority. Phone (OnePlus 11) satellite: after V3 P0–P1.
- Browser skill: full control on an owner-managed allow-list (other sites read-only/ask first); purchases/payments/credentials always need confirmation.
- WhatsApp v1: assisted handoff (draft + open chat, owner presses send).
- Dashboard: Hebrew default with English toggle. Timers/alarms: Core speaks them even with the face closed.
- Virtual computer (P2): write a justification first, then decide. Calling (P2): assisted dialing only.
- Multi-device + Tailscale remote: after V3 P0–P1, together with the phone satellite.
- Installer signing: research free options (SignPath OSS etc.); stay unsigned meanwhile.
- Defaults: ask before any outward action; keep conversations/logs 90 days then summarize into memory; proactive suggestions rare/important only (quiet hours respected); installer pre-selects only core + music.

## V3 P1 basics (branch v3-p1-basics, Issue #17)
Done (offline tests in `core/tests/test_v3_p1.py`): (1) one-time tray notice on the first window close (pref `tray_notice_shown` in runtime.json; only when a tray icon exists); (2) workweek: `scheduler.workweek` (default `0-4` Sun-Thu, `1-5` allowed) already drove "ימי חול", now covered by tests; (3) `timers` feature: `timer_start` / `alarm_set` / `timers_list` / `timer_cancel`, stored in memory.db, rung by the Core itself (GPT-Live announce, else Core TTS) with no face needed; a ring missed by more than 10 min is recorded as missed, never rung late; (4) `lists` feature: shopping/todo lists (`list_add/show/check/remove/clear`, clear needs approval); (5) `jarvis help [topic]` prints the feature guides as Hebrew+English text.
Each new feature has a guide (`timers.toml`, `lists.toml`), capability matrix rows (operations.py M3_FEATURES), setup-wizard group and bundle check.
Not verified on real hardware: audible timer ring on the real speaker, the Windows toast for the tray notice. No dashboard pages for timers/lists yet (voice only).
