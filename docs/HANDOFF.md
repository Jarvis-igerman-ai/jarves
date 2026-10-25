- 24 Oct 2026: ReDoS alerts #8-#12, #37 fixed with linear code, see [handoff](handoff/2026-10-24-redos-pr1.md).

V4.5 approved by the owner 24 Oct 2026: [NOW](NOW.md) is now the bounded V4.5 authorization (the old NOW text is kept in [reviews/NOW_SWV1_18OCT2026](reviews/NOW_SWV1_18OCT2026.md)). Open questions for the owner are listed at the end of NOW. Docs only; nothing implemented yet.

PR #110 conflict-resolution evidence and carried decision gates: [handoff](handoff/2026-10-24-pr110-merge.md).

## V6 item 9: drive mode wired into SkillRegistry - 25 Oct 2026 / #216 (branch v6/drive-wire-216, label no-automerge)

- Done: `SkillRegistry.drive` (None = not wired) is consulted in `_authorize` after the TOOL_FORBIDDEN policy and BEFORE the untrusted-turn guard, precheck and approval prompt. `drive_mode.Gate.check`: switch off -> None (no change); allow -> None (normal approvals unchanged; drive mode never approves); defer -> queued (`DRIVE_DEFERRED`, label = tool name + recipient first name, never text); emergency from untrusted text -> `DRIVE_REFUSED` (not queued); model `feature_set drive_mode` -> `DRIVE_OWNER_ONLY`. Policy exception -> `DRIVE_ERROR` for action/destructive tools (fail closed); reads and hang-up/stop/cancel go on.
- Trust: `turn_source(origin, ROUTE, taint)` only; `by_voice = ROUTE == "voice"` (set by voice/live.py and delegation_client.py code). Model-written fields such as `number_source` never count.
- Wired in `app.py` (Core) and `text_session.py`. Guide `drive_mode.toml` EN+HE updated; catalog row evidence/blocker updated (still `implemented_unverified`).
- Tests: `core/tests/test_v6_drive_wire.py` (off = identical results on 10 scenarios + 6 existing registry test files rerun with an off gate via `_drive_off_plugin.py`; on: deferral, allowed answer, approvals still asked, emergency refused, hang-up always, model cannot switch off, fail closed, queue never run, no text in logs).
- Local gate: prepush `perf` (`test_backend_prompt_does_not_grow` 48780 > 48400) fails the same with this change stashed: pre-existing, not from this branch.
- Questions for the user: (1) `dial_open` has no recipient name, so every non-emergency call waits while driving; add a recipient to the dial draft? (dialing.py was left alone.) (2) Voice "I have stopped" phrase: today only `jarvis drive stopped` / `off` (and the dashboard switch) end it; a deterministic owner-phrase handler is a follow-up. (3) Read tools that return third-party text (mail) wait while driving; OK?

## Any.do MCP integration (gated Skill, off by default) - 24 Oct 2026 / #140 (branch feat/anydo-mcp, no PR yet)

- Done: `core/src/jarves/anydo.py` (OAuth discovery + dynamic registration + PKCE loopback, token store, allow-listed Streamable-HTTP MCP client, Skill with `anydo_status/lists/tasks/task/create/remind/update`), `jarvis anydo start|status|off`, features `anydo` and `anydo_update` (both OFF, `implemented_unverified`), guide `guides/data/anydo.toml` (EN+HE, parity table), Hebrew in `hebrew_parts/2026-10-24-anydo.json`, tests `core/tests/test_anydo.py` (stub MCP+OAuth server, no network).
- Safety: every write (create, reminder, update) shows the exact items/time/change and needs the owner's approval from every origin; no delete; writes never retried (`ANYDO_UNCERTAIN`); update is reported done only after read-back (`ANYDO_PREVIEW_REQUIRED` otherwise); task text is untrusted (no model write in the same turn); tokens in `<state_dir>/anydo/token.json` (owner-only), never in logs.
- Verified 24 Oct (Any.do support page): endpoint `https://mcp.any.do/mcp`, OAuth 2.0, tool names get_lists/get_tasks/get_task/create_personal_tasks/set_reminder/apply_bulk_task_updates. NOT verified: scope names (`access_anydo_data`/`update_anydo_data` come from the plan only), argument names of each tool (`list_id`, `cursor`, `limit`, `tasks[]`, `reminder_time`, `updates[]`), DCR/revocation support, the `update_task_preview` + "Execute all" behaviour (support page lists `apply_bulk_task_updates` and says nothing about preview), the `/sse` vs `/mcp` difference.
- Not done: no `jarvis connect` wizard step (the CLI command covers sign-in), no dashboard page.
- OWNER ACTION (later, feature stays off until then): run `jarvis anydo start`, sign in with your Any.do account in the browser, allow both permissions, then `jarvis features on anydo`. Try one harmless task + reminder. Only if "complete a task" really applies for you, also `jarvis features on anydo_update`; otherwise leave it off. Report any argument/scope mismatch (fix is a small change in `anydo.py`).
- Questions for the user: none blocking.

## Web chat: busy-flag race fixed — 23 Oct 2026

- CI hung (pytest timeout, Ubuntu shard 3/3 on PR #92) in `test_web_chat.py::test_new_resets_the_model_and_starts_a_new_stored_conversation`. Root cause in Python Core `chat_engine.py`: the `reply` event (in `_finish`) and a command's `notice` (in `_notice`) were published BEFORE the busy flag was cleared, so a client that reacted to the answer at once (the test sends `/new` right after the reply) could be refused with `CHAT_BUSY`; no notice ever came and the long-poll waited out its timeouts. A real user with a second device, or a fast client, could hit the same 409.
- Fix: clear the busy flag and publish the final event under the same lock, so a client that saw the answer can always send the next message. New regression test `test_a_client_that_saw_the_answer_may_send_at_once` (fails without the fix 3/3, passes 8/8 with it). No behavior change otherwise.
- Verification: `test_web_chat.py`, `test_chat.py`, mobile and phone tests pass locally; browser-based tests cannot run in this sandbox and rely on CI.

> **New handoff notes go in `docs/handoff/`, one file per task** ([README](handoff/README.md)); this file keeps the
> history up to 23 Oct 2026. The CI/merge/release work of 23 Oct is in [handoff/2026-10-23-ci-merge-release.md](handoff/2026-10-23-ci-merge-release.md).

## WhatsApp bridge: one signed "Download the WhatsApp bridge" step — 23 Oct 2026 / #17 (branch feat/whatsapp-bridge-download, no PR yet)

- Problem: with no bridge program the owner could only reinstall or build Go. Now the WhatsApp page (card "The bridge program is not installed"), `jarvis whatsapp install`, and the Connect flow (`jarvis connect whatsapp` asks; the dashboard Connect row opens the WhatsApp page) offer ONE step that downloads the prebuilt Windows bridge, verifies it and installs it to `<JARVES home>/bridge/whatsapp_bridge.exe` (the per-user location `bridge_command()` now also searches, after the shipped payload and the source-checkout `dist`). Backend: new `core/src/jarves/whatsapp_bridge_install.py`, shared by dashboard `POST /api/whatsapp/install` (local-only, like every WhatsApp change) and the CLI (`whatsapp_admin.install_bridge`).
- Trust (nothing new): the app fetches `whatsapp-bridge.json`, an Ed25519 envelope verified with the SAME built-in `updates.UPDATE_PUBLIC_KEYS`, but under its own signing domain (`jarves-whatsapp-bridge-manifest-v1`) so a signed installer manifest is not valid as a bridge manifest and vice versa (`updates.canonical/verify_manifest` got an optional `domain`, default unchanged). The signed manifest carries product, platform, asset name, version, SHA256, exact size and an https URL. Bytes are checked in memory, again on the temporary file, then moved in atomically with the signed manifest beside them (`whatsapp_bridge.manifest.json`). Before EVERY start `start_bridge` re-verifies that stored signature and re-hashes the exe (`BRIDGE_TAMPERED`, not started). Installed builds ignore `JARVES_WHATSAPP_BRIDGE_BASE_URL` and any key parameter (source checkouts may use an https override for testing). Owner-initiated only (`approved=True`, no background/voice/startup path), never while the bridge runs, never an older version than installed, no token sent, no secret logged (version/size/SHA256 only, code `WA_BRIDGE_DOWNLOADED`). Not published yet = honest EN/HE `BRIDGE_NOT_PUBLISHED` message, nothing changes.
- Publish side (script; the manual workflow below wraps it): `python packaging/release.py bridge-publish --dir bridge/whatsapp/dist [--dry-run]` re-checks the exe against `.sha256` + `build-info.json`, uploads `whatsapp_bridge-<version>.exe`, its `.sha256` and then the signed `whatsapp-bridge.json` (last = the switch) to the rolling pre-release tag `whatsapp-bridge` of `igalgerman/jarvis-releases` (same `RELEASES_TOKEN` + `JARVES_UPDATE_SIGNING_KEY` secrets; skips cleanly without them). The app reads `https://github.com/igalgerman/jarvis-releases/releases/download/whatsapp-bridge/whatsapp-bridge.json`.
- Guide `whatsapp_bridge.toml` (new first step, 2 new problems, EN+HE, new error codes), 3 Hebrew catalog strings via `catalog_add.py`, CLI parity unchanged (`install` is a new action of `jarvis whatsapp`; `status` now shows `bridge_download`), `test_guides`/`test_cli_parity` pass.
- Tests (targeted, no network): `core/tests/test_whatsapp_bridge_install.py` (26: happy path, atomic files, no token header, approval required, not published, 13 tamper/failure cases leave nothing behind, failed replacement keeps the old bridge, older/running/non-Windows refused, hash re-check before start, installed builds ignore overrides, dashboard/CLI surfaces, `release.py bridge-publish` -> local GitHub stub -> app install end to end). Plus `test_whatsapp_surfaces`, release, update and connect tests; `python core/tools/prepush.py` PASSED.
- NOT verified: a real download from GitHub, the real Windows exe running after download, SmartScreen/antivirus behaviour on the downloaded unsigned exe, the new button in a real browser (source assertions only), `payload_check.py` is unchanged (it covers the shipped `bridge/` payload, not `<home>/bridge`).
- MUST BE PUBLISHED for this to work end to end (owner/CI, one time per bridge version): (1) secrets `RELEASES_TOKEN` and `JARVES_UPDATE_SIGNING_KEY` set (docs/RELEASE_HOSTING.md); (2) run the reproducible build and `python packaging/release.py bridge-publish --dir bridge/whatsapp/dist` on a machine/runner with those secrets (the owner can run it locally after `./scripts/build_whatsapp_bridge.ps1 -Version 0.1.0`, with `RELEASES_TOKEN`/`JARVES_UPDATE_SIGNING_KEY` in the environment); until then the button answers "not published yet". The published exe should equal `packaging/bridge_pin.json` (0.1.0, sha256 15706e16...).
- Workflow (branch ci/bridge-publish-workflow, #17): `.github/workflows/bridge-publish.yml`, manual (`workflow_dispatch` only). How the owner runs it: GitHub > Actions > "Publish WhatsApp bridge" > Run workflow, branch `main`. FIRST with `dry_run` ticked (the default): it builds the bridge on windows-2025 (pinned Go 1.27.0, rebuild with `-Verify`, SHA-256 compared with `packaging/bridge_pin.json`), uploads exe + `.sha256` + `build-info.json` as a 1-day artifact, and on ubuntu-24.04 runs `release.py bridge-publish --dry-run` (prints what would be published; no secrets, no network). Check the log, then run it again with `dry_run` unticked: the same build, then the real publish (signed `whatsapp-bridge.json` last). `version` may stay empty (= the pin; any other value is refused).
- Secrets scope: `permissions: {}` at the top, `contents: read` per job, `persist-credentials: false`. `RELEASES_TOKEN`, `JARVES_UPDATE_SIGNING_KEY` (and var `RELEASES_REPO`) are set on ONE step only ("Publish", publish job); never in the build job, never in the artifact, no `set -x`. The Publish step runs only when `dry_run` is false AND the ref is `refs/heads/main`; a non-main real run fails early with a clear error. Concurrency group `bridge-publish` (no cancel). All `uses:` are full-SHA pinned (checkout and download-artifact SHAs copied from the other workflows; setup-go v7.0.0 and upload-artifact v7.0.2 resolved from the upstream tags with `git ls-remote`, because the other workflows still use `@v7` for those two). Contract: `test_ci_pipeline.py::test_bridge_publish_workflow_is_manual_least_privilege_and_scopes_secrets_to_one_step`; actionlint clean.
- NOT verified for the workflow: it was never run on GitHub (needs the owner's secrets and a Windows runner): artifact download layout on the runner, empty `RELEASES_REPO` var falling back to the default repo, the real Go build matching the pin there.
- Open questions for owner: (a) old versioned bridge assets are not pruned from the `whatsapp-bridge` release (a few MB each; prune keep-3 is a later change); (b) `JARVES_WHATSAPP_BRIDGE` (explicit command env) is still honoured by installed builds, unlike update sources; consider ignoring it there; (c) the downloaded exe is not Authenticode-signed, same as the installer.


## Software project manager — 23 Oct 2026

- Owner request: open a software project and let JARVES manage it with many coding agents (Claude Code, Codex, OpenCode, OpenRouter): small tasks, handoffs, parallel work, small contexts, status / new task / update / deep dive / talk to the agent. Design: [PROJECT_MANAGER](plans/PROJECT_MANAGER.md), reviewed by an Opus subagent before coding; its findings (integration worktree + merge lock, worktrees under the JARVES home, scope and protected-path enforcement, prompt on stdin, allow-listed child env, `dontAsk` permissions, check run by JARVES with `shell=False`, bounded restart recovery) are implemented.
- Code: `core/src/jarves/projectmgr/` (store, plan, cards, gitops, runners, service, surfaces), feature `project_manager` (default OFF), Skill `project` (4 tools), `jarvis project ...`, dashboard routes `/api/projects/...` and the Projects page, `ProjectsConfig` (`projects.*`), guide `project_manager.toml` EN+HE, CLI parity rows.
- Verification: `core/tests/test_projectmgr.py` (real git worktrees, a fake agent executable speaking Claude Code's JSON result); guides, parity, perf, Hebrew and log-lint gates. NOT verified: a real Claude Code / Codex / OpenCode run, OpenRouter, the output shapes of Codex `exec --json` and `opencode run --format json` (written from documentation, read leniently), the dashboard page in a real browser. Capability status stays `implemented_unverified`.
- Cut from the first release (Opus advice): the runner fallback beyond the project's order, a size-L split round, voice `open`/`archive` (CLI/dashboard only by design). Kept: planner agent, talk (read-only ask, or `--do` follow-up task), OpenCode and gated OpenRouter.
- Open question for owner: which check command each real project should use (set with `jarvis project config --check`), and whether OpenRouter (paid) should ever be enabled (`projects.allow_openrouter`, default off).
- Decision: **USER DECISION REQUIRED** — first live run: owner opens one real project, runs `jarvis project plan --goal ...`, approves, and reports what the agents did (manual sheet row to be added at release sign-off).
## Dashboard redesign: navigation tree — 23 Oct 2026

- Owner report: the dashboard is a mess, too much info, hard to find anything. Python dashboard owner (static UI only, no API change). The sidebar's 54 flat links in 7 groups became a keyboard-navigable tree: Home, Chat, Approvals and Setup guide on top, then Planner, Automations, Talks & memory, Do things, Assistant, Connections, Fix problems, Usage & cost, Development, System & help (icons, guide line, the branch you are in opens by itself, open branches remembered). All 54 pages stay reachable; search still finds every page. Renamed confusing pages (Search conversations, Personality files, Skills / Skill ideas, Phone app, Coding agents: all / This project's agent).
- Home: one hero with status, next step and a Go button; setup checklist, three tiles, last conversation; connections/features/privacy folded into "More details". Top bar: device and Pi chips moved into the Core chip tooltip, long chips truncated; the fixed bottom usage strip is hidden (the cost tile on Home shows it). New `tree.css`.
- UX review (sub-agent, from screenshots): applied nav regrouping, ARIA (`aria-current`, levels, roving tabindex, no `aria-live` on main), top bar and strip cleanup, contrast tweaks. **Open, not done:** collapse the six-row setup checklist to the first open step with a progress bar; make tiles links; unify "Issues 0" in the top bar with "7 open issues" on Home (different sources); sticky save bar and Advanced toggle on Settings; fold "Health & speed", "Self-check" and "Health" into one tabbed page; a count badge on Approvals; verify RTL by eye (my Hebrew capture did not switch language, only the locale tests cover it).
- Verification: all eight pre-push checks passed. Dashboard, guides, CLI-parity, locale and perf tests pass locally; the three browser a11y tests could not run in this sandbox (Playwright/Chromium version mismatch) and rely on CI. Eight screenshots are not committed.
- Decision: **OPEN** — owner to review the look after the beta installs.

## Release notes by version (dashboard, CLI, installer page) — 23 Oct 2026

- Owner request: see release notes per version in the dashboard, and an HTML page with the notes when installing. New `jarves/release_notes.py` parses CHANGELOG.md: dashboard page `#/releasenotes` (version picker, EN/HE), `GET /api/release-notes[?version=]`, `jarvis release-notes [--version V] [--list] [--open] [--he]` (writes `<home>/release-notes/release-notes-<ver>.html`, all text escaped; a beta shows its stable version's notes).
- Packaging: `build_windows.ps1` copies CHANGELOG.md into the stage (the installed app finds it in the program folder); `jarves.iss` adds a finish-page checkbox "Show what is new" (on by default, skipped for silent installs, so in-app silent updates do not pop it).
- Guide: new step in `updates.toml` (EN+HE); CLI parity unchanged (covered by `update_check` -> `update`); Hebrew strings via `catalog_add.py`. Tests: `core/tests/test_release_notes.py`; guides, cli_parity and the local pre-push gate pass.
- NOT verified: the real Inno Setup build, the finish-page checkbox and the page opening in a real browser on Windows (CI builds the installer; owner to confirm on the next beta install). Open question: also auto-open the page after a silent in-app update?

## Admin-key prompt for personal OpenAI accounts — 23 Oct 2026

- Owner report: on a personal OpenAI account the guided setup text ("as the organization owner") did not match what they see. Reworded the `jarvis connect openai-admin` steps, the "Real API bill" guide (he/en) and `OPENAI_ADMIN_KEY.md`: a personal account is its own organization and its owner; if the Admin keys page is missing, skip (`s`), everything works and cost shows as an estimate. No logic change; key handling unchanged.
- Open question for owner: whether a personal account shows Settings > Organization > Admin keys is **unverified** (no real account tested, OD-05 unchanged).
- Sandbox: `test_guides::test_help_walks_google_setup_one_step_at_a_time`, 7 `test_connect` and 2 `test_p_perf` cases fail identically without this change (environment); CI is the evidence.

## WhatsApp bridge: simple setup, missing-bridge message — 23 Oct 2026 / #17

- Problem: with no bridge program (`bridge_command()` is None) neither the dashboard nor Connect said so; the owner only got a Windows PowerShell build hint. Now `bridge_installed` is in `/api/whatsapp`, `jarvis whatsapp status --json` and the Connect row. When missing, the WhatsApp page and the Connect row show a plain EN/HE message (reinstall JARVES, or build from a source checkout with `scripts/build_whatsapp_bridge.ps1`, Go 1.27.0); Link/Start are disabled; the API returns the new code `WA_NOT_INSTALLED` (503) instead of `WA_BRIDGE_DOWN`.
- One "Set up WhatsApp" flow, one backend (`whatsapp_admin.setup_begin` / `_link`): dashboard WhatsApp page button (`POST /api/whatsapp/setup`: start bridge, begin linking, QR on the page, on linked offers read-only mode); dashboard Connect row (`/api/connect/run` id whatsapp starts the bridge and opens `#/whatsapp?setup=1`, which begins linking); `jarvis connect whatsapp` and new `jarvis whatsapp setup` (start, QR in the terminal, offer read mode). The phone scan stays manual; nothing is linked silently; mode stays off unless the owner says yes to read-only; no send path touched. Bridge Go code and policy unchanged.
- Guide `whatsapp_bridge.toml` (steps + problem "bridge not installed", EN+HE), 5 new Hebrew catalog strings via `catalog_add.py`, CLI parity unchanged (`whatsapp_bridge` -> `whatsapp`; `setup` is a new action of it).
- Verification: targeted tests (`test_whatsapp_surfaces`, `test_connect_unset`, `test_connect`, guides, cli_parity, locales) pass with the fake bridge; see prepush result in the PR. NOT verified: a real bridge binary, a real QR scan or real WhatsApp linking, the new JS in a real browser (only source-level assertions; Playwright is not installed here), the Connect console QR rendering.
- Open question for owner: auto-download a prebuilt, checksum-verified bridge from a release/CI artifact when it is missing? Not implemented; today the only fixes are reinstall or a source build. Also confirm the installer payload really ships `bridge/whatsapp_bridge(.exe)`.

## Dashboard controls while JARVES is stopped — 23 Oct 2026

- Report: with JARVES offline the dashboard could not manage things. Root cause: on the Controls page the mic-mute and alert-pause switches were disabled when the Core was stopped, and `POST /api/runtime` answered 409 for every change. Both are saved choices in `runtime.json` that the Core loads at start, so they now save directly while stopped (`runtime.service.save_prefs`) and the page shows and edits the saved values. Show/Hide window and Quit still need the running Core (409, as before); start-at-login already worked offline.
- Verification: `core/tests/test_runtime.py` updated (offline mute/pause saved, display still 409): 26 passed; the one failure is `test_pystray_backend_builds_without_running`, which only needs the optional `pystray` package missing in this sandbox. Local pre-push gate passed (`--changed-only`). Other dashboard pages that need a live Core (chat, camera preview, voice) were not changed and still say so honestly.
- Open question for the owner: if other pages felt blocked offline, name them and they will be triaged the same way.


## Camera on demand — 23 Oct 2026 / #74

- Owner requested installation: published beta 4.0.1-beta.4 / build 212 installed using `jarvis update install --yes --silent`; installed CLI version and beta update status verified. Backup created by the updater. This build predates the camera correction below.
- Report: enabling Camera kept the sensor on. Python Core owner: `CameraService.wants_stream()` automatically opened a local presence stream whenever a detector was available. It now streams only for an explicit timed preview; requested stills retain their open/capture/close lifecycle. Wake/activity/presence events and persisted legacy away settings never start capture or load models.
- Updated service/switch/wiring descriptions, bilingual camera/legacy-away guides, frontend catalogs, backend translation and camera setup documentation. No geometry, Godot assets, dependencies or provider/model changes.
- Verification: 32 focused camera, CLI and privacy-state tests passed with fake/synthetic frames and fake cloud responses. Initial sandbox run could not access pytest's temporary folder; rerun with filesystem access passed. No physical sensor capture or real cloud call. All eight required pre-push checks passed (lint, guides/CLI, locales, docs, prompt/perf, workflow contracts, network guard and dashboard timing). Source commit [d8c0ca0](https://github.com/igalgerman/jarves/commit/d8c0ca0c833281cb2a6c3dc4e53badf86b8ca3b4) passed [full exact-commit CI](https://github.com/igalgerman/jarves/actions/runs/39959142322): 21 successful jobs, including Windows/Linux shards, browser tests, Core-Godot, performance, installer smoke and upgrade/rollback. CodeQL passed separately. [PR #76](https://github.com/igalgerman/jarves/pull/76) contains the reviewed fix; live status and any subsequent beta/installation evidence are recorded there and on [#74](https://github.com/igalgerman/jarves/issues/74). This checkpoint does not claim the camera correction is installed.
- Reproduce from `core`: `python -m pytest tests/test_camera.py tests/test_privacy_state.py tests/test_camera_cli.py -q`.
- Decision: **ANSWERED** — owner requires camera as an on-demand skill; keep explicit timed local preview. No background-monitoring replacement feature requested. Existing decisions below remain unchanged; actual hardware acceptance is still an owner gate.

## Current individual-PR closeout (22 Oct 2026)

- Owner authorized fixes, tests, review and individual merges of #72, #68 and #70. #72 merged at
  [98230f8](https://github.com/igalgerman/jarves/commit/98230f89c353f7c7104f1e2b6a17c08d62aa3e36);
  [exact-head CI](https://github.com/igalgerman/jarves/actions/runs/39814940596) and
  [post-merge main CI](https://github.com/igalgerman/jarves/actions/runs/39817148513) passed.
- #68 final source head `9e4ae129050103e1a395f746463a929f7a87bae6` passed its full local gate, 45 installer tests,
  five PowerShell syntax checks and [full CI](https://github.com/igalgerman/jarves/actions/runs/39817252108), including
  four smoke scenarios and old-version upgrade/rollback. Merged individually at
  [cdce8fe](https://github.com/igalgerman/jarves/commit/cdce8fe8a5dcba78e1e608ccfa975afd37e0df55).
- #70's first hosted run exposed missing tour-routing rows and piped setup launching the tour. Added bilingual routing
  coverage and restricted the automatic tour offer to an interactive terminal with the dashboard enabled; 7 targeted
  setup/routing/budget tests passed. Final exact-head CI is linked in the PR. No acceptance criterion was removed.
- Final Windows CI also exposed a real workspace undo bug: equal backup mtimes sorted the unsuffixed filename ahead
  of newer numbered backups. The Python workspace owner now orders ties numerically and keeps suffixes increasing
  after retention pruning. A frozen-clock/equal-mtime regression covers numbers above 9 and pruning; all 36 focused
  workspace/chat tests passed. No history was mutated on the owner's profile; visual source hashes remain unchanged.
- Both remaining PRs use isolated worktrees, no force pushes, no runtime sensor/account/paid tests. Preview links and
  hashes are below. The owner-PC beta installed earlier is build 199; these merges do not prove that PC was updated again.
- Unrelated inherited `.github/workflows/next-flow.yml` failures on main predate this work; the branch flow remains
  disabled (`ENABLE_NEXT_FLOW` absent). The primary `jarves-checks` and package results are reported separately.
- GitHub temporarily retained active workflow records after all their jobs completed. Waited; when the stale main
  records disappeared, a fresh query confirmed no active main run and #68 merged without needing a rule override.
  Owner subsequently explicitly authorized proceeding past stale workflow records after verifying no actual jobs run.

### Decision queue (including carried questions)

| Question | Recommendation / tradeoff | Status and exact gate |
|---|---|---|
| Merge these three PRs? | Individual reviewed green merges preserve attribution and isolate failures. | ANSWERED: owner explicitly authorized; verify no actual main jobs run. Owner permits ignoring stale workflow records once every job finished. |
| Enable next branch / bot fast-forward? | Keep disabled for this work; current owner rule prefers individual merges. | DEFERRED: future explicit workflow decision; no variable/token changes. |
| Publish betas / daily promotion? | Keep existing beta publishing, keep stable behind owner sign-off. | ANSWERED for current beta: `ENABLE_BETA_PUBLISH=true`; daily/stable promotion DEFERRED to release policy. |
| Which sandbox for future scenario runs? | Use the current fake-data local tests and disposable hosted runners here. | DEFERRED: broader scenario-runner stage, not needed for these PRs. |
| Defender exclusion? | Leave exclusions unchanged; no need to weaken host protection for these tests. | DEFERRED: owner decision only if measured install performance still requires it. |
| More paid AI QA budget? | Make no paid calls in this task; prior overall cap is not a new allowance. | DEFERRED: reconcile actual spend and obtain any new budget at live-QA gate. |
| Branch protection / one required check? | Retain current settings; full exact-head CI evidence is still required for these merges. | DEFERRED: owner repository-settings decision. |
| Artifact retention? | Keep implemented 7-day PR artifacts; smoke artifacts retain 3 days. | DEFERRED: future retention change; no settings changed here. |
| Installer under 2 min / interactive layout? | Measure the final installer on the owner's PC; runner times are insufficient. | USER DECISION REQUIRED: #58 owner verification, still open. |
| Full release wizard, standing rows, real phone/voice/account sign-off? | Merge this tested baseline without treating mocks or viewport captures as owner approval. | USER DECISION REQUIRED at #60 acceptance; missing broader work stays open under #58/#60/#61. |
| Physical design, purchases and print risks? | Keep previous physical milestone gates; unrelated to these software merges. | DEFERRED: explicit future physical approval and real measurements. |

## Initial wizard checkpoint (historical; current recovery evidence below)

- Built: `core/src/jarves/wizard.py` (16 topics from the guides/features/CLI-parity tables, state in `wizard.json`, routes
  `/api/wizard/*`), dashboard page `#/setup` (`wizard.js`/`wizard.css`; set up / skip / later, "Do it for me" test, Works /
  Does not work / Skip, 30-second learn card, progress bar, topic list, resume, EN+HE, 390 px layout), `jarvis tour`
  (`--status`, `--json`, `--signoff`), voice tool `show_tour` ("show me around", "set up music"), `jarvis setup` offers the tour
  at its end, link from Settings, guide `setup_tour.toml`, feature `setup_tour` (CLI parity: `tour`).
- `#/setup` is now the wizard and is the stable entry for the installer button; the old add-ons page moved to `#/addons`.
- Results: Works writes per-feature "verified in X by owner on <date>" (wizard.json); `jarvis tour --signoff` / the page button
  generate `docs/reviews/MANUAL_TEST_<version>.md` (rows W-<topic> + W-AUTO; skipped = accepted-blocked with a note; other rows
  of an existing sheet are kept) for `jarvis release signoff --finish` and `tools/release_gate.py`.
- Baseline vs diff: diff mode (after "Finish the first full check") shows topics named in the CHANGELOG section of the version,
  topics with never-verified features, plus the SMOKE topics.
- Open / not done: standing rows from `tools/manual_test_standing.toml` are not automatically imported (existing
  sheet rows are preserved); main's connect/issues/self-management/web-chat integration has now been reconciled;
  "reuse an old connection" uses the `connect` registry states only; the learn card is spoken by asking JARVES, not auto-spoken;
  a failed topic's bug report is a local Issues-list entry (no GitHub upload). Installer pages are Codex's (packaging/ untouched).
- Questions for the user: none blocking.
## Wizard PR #70 recovery (22 Oct 2026)

- Reconciled CLI, setup, dashboard plugins, guides and EN/HE locales with main while retaining the wizard and current
  account/setup safeguards. Integrated the shared #72 pre-push prerequisite locally; merge remains individual.
- Fixed topic-specific Connect targeting, removed stale owner verification after a failed recheck, and corrected
  mixed-language learn cards and the missing Hebrew feature description. Added failed-recheck regression.
- Initial focused wizard/setup/CLI suite: 52 passed. Final checks and hosted exact-head evidence are recorded in #70.
- Actual headless Edge captures: [English desktop](../output/playwright/pr70/setup_en_1440.jpg),
  [Hebrew phone layout](../output/playwright/pr70/setup_he_390.jpg),
  [source hashes](../output/playwright/pr70/source-manifest.json), [8-page report](../output/playwright/pr70-results.json).
  All eight page checks passed; updated wizard regression: 13 passed. These are demo-profile headless captures,
  not owner account, real phone or voice confirmation. The complete local gate passed after backend Hebrew metadata repair.
  Reproduction: from core, `python tools/all_pages_check.py --pages setup,addons --langs en,he --widths 1440,390`.
- Decision queue: merge when reviewed/green ANSWERED; human setup/voice/account/phone acceptance remains USER DECISION
  REQUIRED at the existing owner sign-off gate. No owner result is inferred from tests; related issues stay open.
## Installer parallel follow-up — PR #68 held for #67 → #72 (Codex)

- Changes: separate functional/autostart-purge smoke VMs for each compression profile (same three candidate installs); start bundling before Go/bridge preparation; verify bridge before payload join; prepare cold-cache Inno concurrently. No batch product or wizard implementation/test files changed in this follow-up.
- Local verification: 48 targeted worker/workflow/packaging checks passed; actual Windows child process outlives its start step and success/failure/abort propagate. First hosted follow-up b287a36: build 4m09, both functional smokes and real upgrade/rollback passed; autostart/data-only verification caught an incorrectly shaped feature fixture. Corrected fixture to runtime schema, keeping strict byte-for-byte preservation; regression test checks both seeded choices through Features. Corrected exact-head timing refresh pending; prior complete green baseline is bc23260 / run 39749405345 (3m54 build job, 7m52 all installer checks).
- Decision queue: merge order ANSWERED — #67 first, then #72; hold #68 until owner notification and both gates. Published-branch synchronization must preserve the standing no-force-push rule; reconcile a literal rebase versus a merge update when that gate opens, before rewriting any remote history. Owner-PC timing and visual review remain DEFERRED in open #58; prior preview approval remains valid, desktop tool launch was unavailable.

## Installer #58 — PR #68 (22 Oct 2026, Codex writer)

- Scope: measured parallel bundle stages, stable pinned-tool cache, source-only code archive, separate non-publishable fast smoke profile; Inno EN/HE keep-data page, verified app backups and profile migrations, previous program snapshot/offline rollback, first user PATH with reversible known-shim retirement, windowless dashboard button. Restricted wizard/CLI/console implementation files untouched; one installer assertion in test_setup_wizard.py updated for the button.
- Baseline run 39737015500: installer job 7m42s, build 134.4s, release compression 91.26s, clean install 50.8s. Green checkpoint run 39747590940 (76983c5): build job 3m43s (now separate checks), release build 118.7s/clean install 41.9s, smoke build 46.8s/clean install 51.0s; 7,918 staged files, 2,656 archived. Both smoke profiles passed; the new matrix argument forwarding was fixed/regression-tested. Follow-up fixes compare the same preservation files, avoid 1970 timestamps triggering normal retention, and wait for actual rollback completion before uninstall. These are not owner-PC measurements or a claim all checks finish under 5 minutes.
- Local packaging/backup tests and Inno compile pass; new SHA-pinned 4.0.0.153 fixture covers real upgrade/rollback with fake 0.95 wake/model/profile/credential data. Merged coordinator main dd77cf0 cleanly; all jobs passed on 76983c5. A final directive makes English the fresh default; exact final-head evidence is recorded in PR #68. User approved visual preview; the desktop tool could not launch it (app approval/elicitation unavailable). No installer window opened or owner-PC install performed. See INSTALLER_58.md and PR #68 for current evidence; issue remains open for app wizard and owner verification. No merge authorized for this task.
## Installer PR #68 recovery (22 Oct 2026)

- Synchronized main and the declared #72 prerequisite without rewriting branch history. Reconciled DOC_MAP and
  included the installer smoke/upgrade jobs in the failure summary's dependencies as well as the required CI result.
- Focused local installer/packaging regression: 45 passed. Full local gate and exact-head hosted results are in #68.
- Owner-PC timing and interactive installer layout remain UNVERIFIED; issue #58 stays open. The installed beta was
  not replaced by local smoke tests. CI uses disposable Windows runners and fake account/data fixtures.
- Decision queue: merging this PR when reviewed and green ANSWERED by the owner's request; physical/account/live
  behavior gates DEFERRED. No new user decision is required for this bounded integration.

## PR readiness recovery (22 Oct 2026) — owner authorized #72, #68, #70

- #72 synchronized with current main, retaining both the local pre-push rule and individual-PR review/merge rule.
- The guide-state isolation fix is already on main. Corrected failure-summary classification: a test that failed again
  is not a test that passed on retry; focused regression added. No runtime behavior changed.
- Verification and exact-head hosted CI are recorded in PR #72. Subsequent #68/#70 merges require their own final-head
  checks and diff review; no force push, blind merge or test weakening is authorized.
- Decision queue: merge authorization ANSWERED by the owner's current request. Prior physical, account, paid QA and
  human sign-off gates remain DEFERRED to their owning milestones; green CI does not satisfy them.

## Release and test system plan (22 Oct 2026) — docs only, stacked on PR #57, not merged

- New plan: [docs/plans/RELEASE_AND_TEST_SYSTEM.md](plans/RELEASE_AND_TEST_SYSTEM.md) (DOC_MAP row added). Increments on
  #44/#57 only; no workflow file changed. Covers alpha/beta/stable without rebuild, `next` batch branch, 12 shards,
  affected tests + passed-hash cache, scenario runner (TOML, fake model via `OPENAI_BASE_URL`), Linux container + GitHub
  Windows VM sandbox, owner sheet 31 -> 9 human rows (about 14 min), installer speed, cost, phases (about 16.5 agent-days).
- Found: `ci/deterministic-tests` (netguard, quarantine) exists only locally (C:\work\jarves-det, f1977cf), not pushed.
- Questions for the user: the 6 decisions in section 11 of the plan (`next` branch, bot fast-forward of main, daily
  beta promotion, sandbox choice, Defender exclusion option, AI QA budget). Status: WAITING FOR USER.
## CI pass 3 (22 Oct 2026) — owner asked to continue, extends PR #57 on #44

- Scope: separate measured Linux/Windows shard plans; committed seeds from the full-green run 39733332477; refresh timing
  caches after successful same-repository PR Core runs as well as main/manual runs; isolate the feature-probe regression
  from unrelated dashboard HTTP startup. Product/runtime behavior, worker counts and release gates are unchanged.
- Seed provenance: JUnit artifacts junit-0..2 Linux, junit-3..5 Windows, 144/145 measured files respectively; only file
  names and aggregate seconds are committed. Cached timings are scoped by OS/schema and Actions branch rules (PR caches
  help only the same PR). No claim that PR timings automatically become a global main cache.
- Verification: 61 focused workflow/planner/perf regression tests passed locally in 30.84s, zero skips. The feature-probe
  regression took 1.73s locally. Final-commit full hosted verification and timing comparison are recorded in PR #57.
- Decision queue: no new owner choice needed for this authorized CI continuation. Existing beta/branch-protection and
  artifact-retention decisions remain DEFERRED to the release coordinator. Browser readiness, installer-stage caching
  and slow Core-start fixtures are DEFERRED beyond this bounded pass; none is needed to validate these changes.

## CI follow-up (22 Oct 2026) — stacked on PR #44, not merged

- Owner request in this session authorizes the follow-up PR and real Actions verification. One writer, isolated branch
  ci/linux-shards-and-fast-validation; base ci/speedup-and-continuous-release. No release, merge or branch-protection change.
- Changes: three serial Linux runners; early workflow/planner/docs validation gates all heavy jobs; setup CLI unit test
  fakes external setup actions but asserts connector wiring; fix the inherited cancellation assertion and DOC_MAP entries.
- Local check: from core, uv run pytest -q -rs --durations=10 tests/test_ci_workflows.py tests/test_ci_pipeline.py
  tests/test_ci_speed_tools.py tests/test_docs_links.py tests/test_units.py::test_cli_setup_paths — 78 passed in 32.16 s,
  zero skips. Setup test 3.80 s locally (prior hosted Windows 173.33 s; different hosts).
- Hosted CI on ed14a9f: https://github.com/igalgerman/jarves/actions/runs/39733332477 passed in 9m46s.
  Linux shards 4m33s / 2m04s / 4m41s; Windows 5m09s / 3m24s / 9m22s; installer 7m46s; validation 13s.
  Windows shard 3 recorded one socket-timeout flake in test_perf_probes.py; its failed file passed on the existing
  one-retry policy. CodeQL passed. This was green with a recorded flake, not a flake-free run.
- Upstream #44 advanced during verification: 672dbb9 independently fixed the same inherited failures, then c00a773
  incorporated this follow-up's implementation. Synced both updates, retained the stronger equivalent assertion and
  removed duplicate DOC_MAP rows. A temporary conflict-marker resolution failed validation and was corrected before
  final verification. Non-documentation content is identical to the full-green ed14a9f commit.
- PR #57 now carries evidence/documentation follow-up to the implementation already incorporated in #44. Its final
  commit receives the fast validation check; the full code run above remains the implementation evidence. No merge
  into main or release was performed here.
- Decision queue: existing branch-protection choice, beta activation, duplicate CodeQL setup and artifact retention remain
  DEFERRED to the release coordinator; no settings changed. Prior merge freeze is respected: this PR is not merged.
  Browser readiness changes, dependency-stage caching and platform-specific duration histories are DEFERRED; unnecessary
  to validate this bounded first follow-up. No new user decision is needed to test the requested PR.

## CI/release speed pass 2 (22 Oct 2026) - branch `ci/perf2` (on PR #44), NOT pushed (merge freeze until 4.0.0 is released)

- Installer build + smoke now runs in parallel with the tests; publishing waits for the whole green run. The installer artifact is `jarves-installer-<tree>` (tree = git tree minus docs); a `main` push whose tree is already proven by a green PR run skips every job, and `jarves-package` / `release.yml` use the PR's artifact (verify by tree, `release.py stamp` at manifest level, no rebuild). Duplicate runs removed, `ci-ok` single required check, path filters in `core/tools/ci_plan.py`, duration-planned shards + xdist (`CI_XDIST_WORKERS`), per-file known-flake retry (`ci_flake.py`), `release_status.py`, `gh workflow run package.yml -f ref=main`, Dependabot patch/minor auto-label (`DEPENDABOT_AUTOMERGE=false` disables).
- Numbers and honest limits (merge to publish est. 2-3 min; PR est. 8-9 min, installer is the long pole): `docs/CI_PERFORMANCE.md`, `docs/RELEASING.md`. Tests: `core/tests/test_ci_speed_tools.py`; actionlint 1.7.7 clean.
- Open, needs the first CI run after the freeze: replace the "est." numbers with measurements; confirm xdist is safe on Windows (set `CI_XDIST_WORKERS=1` if not); seed `durations.json` from the first run's junit artifacts; set the branch protection to `CI result (single required check)`; the old `jarves-windows-installer-main` artifact name is gone (docs/INSTALL_TEST.md updated).
- Questions for the user: (1) OK to require only `CI result (single required check)` in branch protection? (2) Keep PR installer artifacts 7 days (storage vs. long-lived PRs)?

## CI speed-up and continuous release (21 Oct 2026) - branch `ci/speedup-and-continuous-release`, public repo igalgerman/jarves

- Done: 3 Windows shards + Linux single job (`tests/conftest.py --shard/--browser`), real-browser job with one retry, uv/Go/Playwright caches, package.yml = signed candidate on every green main + beta job gated by `vars.ENABLE_BETA_PUBLISH == 'true'`, release.yml (tag `vX.Y.Z`, `core/tools/release_gate.py`), beta channel setting (CLI/dashboard/guide/feature row), `jarvis release signoff` + dashboard page, `manual_test_sheet.py`, `release_rollback.py` (dry run default), docs/RELEASING.md, SECURITY.md, dependabot, CodeQL, secret_scanning.yml. Tests: test_release_channels.py, test_release_signoff.py, test_ci_pipeline.py.
- Not verified: the workflows themselves (only CI shows it), a live beta publish (owner must set ENABLE_BETA_PUBLISH and secrets).
- Questions for the user: (1) branch protection required-check names change (see docs/RELEASING.md), please update after merge. (2) Enable `ENABLE_BETA_PUBLISH` when ready. (3) Enable CodeQL default setup off if the repo uses it, to avoid a duplicate.

---
## Setup: Hebrew console + keep existing settings (22 Oct 2026) - branch `fix/setup-hebrew-keep`, related to #58

- `console_rtl.py`: consoles without bidi (Windows conhost / Windows Terminal / VS Code terminal) get Hebrew in visual order (Hebrew runs reversed, commands/keys/paths/numbers kept left to right, run order reversed, brackets mirrored, long lines wrapped before reversing). `JARVES_CONSOLE_RTL=auto|logical|visual`, `--no-rtl-fix`; auto is visual only on a Windows tty outside mintty/WSL. Installed once in `cli.main` as a stdout/stderr wrapper, so every CLI print/prompt (setup, connect, features, doctor) goes through it; wizard prompts are written via `sys.stdout`.
- Wizard: Hebrew first offers (in English) to continue in the browser, Enter opens the dashboard `#/setup` page (`browser_setup` action; falls back to the terminal). The `<-` marker is now a `>` prefix on the current value and the pick is echoed.
- Existing profile (config.toml / secrets.env / setup_done): one-screen summary, default "keep my settings", only missing items asked (API key, ChatGPT sign-in, guided connect when something required is missing); `jarvis setup --yes` keeps everything non-interactively; choosing "change" or an answers file takes a backup first (`backup.create`) and stops if it fails. No secret value is ever printed.
- Open for the owner: the visual-order output is verified by unit tests only; please confirm on the real Windows Terminal that a Hebrew menu reads right. Guide: `guided_setup.toml` (EN+HE). No dashboard catalog strings were added, so `catalog_add.py` was not needed.

## Docs alignment: DOC_MAP and roadmap status (22 Oct 2026) - branch `docs/roadmap-alignment`, docs only

- New [DOC_MAP](DOC_MAP.md): the single index (per version V3-V7, status, owner doc, testing and manual testing, evidence reports). README and CONTEXT_MAP point to it; CLAUDE.md has one line.
- ROADMAP has a current status block (V4 = 4.0.0 on main plus the 4.0.1 PR list, V5 built vs real-phone gates, V6 depends on V5, V7 proposal); the SW-V1..V3 sections are marked historical. Historical banners on SW_NEXT_STEPS, FULL_TEST_PLAN, V1/V2/V3 packages, archive/ROADMAP; status banners on V4/V5 packages and release notes; PRIORITIES got the 21 Oct decisions.
- `test_docs_links` now fails when a doc under docs/ is not in DOC_MAP or a relative link breaks. Links to reports that never reached the public repo were turned into plain text.
- Open for the owner: the 4.0.0 installer is not yet published in jarvis-releases (newest is 3.0.0.130); PRs #44, #46, #47, #48, #29 add docs and must add their DOC_MAP rows when they merge.

## V4 package J: custom wake production loop + restricted-token isolation (21 Oct 2026) - #17, branch `v4/pkg-j-isolation` (off `main`, draft PR)

- Wake (`voice/wake_prod.py`, `jarvis wake train`): training writes a CANDIDATE to `wake/candidates/`; the working model changes only with `--apply` (the replaced file goes to `wake/history/`; `jarvis wake train --revert` puts the previous model file and wake setting back, a trained file is never deleted; `--out` onto an existing file is refused without `--apply`). Owner recordings are read from `%LOCALAPPDATA%\jarves\wake\samples` by default and quality-checked first (`--check-samples`: unreadable, silent, too quiet, clipped, too short/long, too noisy, duplicates; rejected files are left out, never deleted). The report holds a calibration table (threshold vs synthetic recall and false wakes per hour vs the owner's own recordings, all and held out); the chosen threshold = best owner recall among thresholds under 0.5 false wakes/hour; `--seed` (same seed + samples = same model) and a samples hash (no file names). `--apply` is refused when a near-miss sentence would wake it or the held-out owner recordings wake it under 80 % (`--accept-risk` overrides). Regression list `NEAR_MISS_SENTENCES` = the 20 sentences of suites 22/23 (`tools/suite_voice.py`; `SWV4_CAMPAIGN_VOICE.md` only has the aggregate). Tests `tests/test_wake_prod.py` (14): generated tones and fake TTS only, no microphone, no owner audio.
- Isolation (`winsandbox.py`, ctypes, wired into `isolation.popen/exec_async`): with `[helper] restricted_token = true` (OFF by default; current Job Object behaviour unchanged) helper commands and generated Skills are created suspended with a restricted token (all privileges stripped) at the LOW integrity level, then joined to the Job Object and resumed. It fails closed (a failed restricted launch does not run the command or Skill unrestricted). The existing `helper_isolation` health row says "restricted token, Low integrity" while it is on. Honest limits: reads and network are NOT restricted (needs an AppContainer or a deny ACE for the home dir, see `reviews/SWV4_R01_DEFENSE.md`); a Low child cannot write to normal folders, so installs and file-writing commands fail while it is on; not tried by the owner on the desktop. Tests `tests/test_winsandbox.py` (harmless children; Windows cases `windows_only`).
- Not measured: real owner recordings, real-mic detection, a 60-minute soak (no microphone used). log_lint: 0 violations.
- Questions for the user: (1) Turn on `restricted_token` for generated Skills only, or also for helper commands (it blocks installs)? Today one flag covers both. (2) Please record 20 clean phrases into the samples folder, then run `jarvis wake train "hey jarvis" --check-samples`.
## V5 owner decisions 2026-10-21 - branch `v5/owner-decisions` (off `v5/mobile-access`, draft PR)

- Done and tested (`core/tests/test_tailnet.py`, 26 tests, plus mobile/privacy/setup/guides/lint suites): OD-15 client delegation and OD-12 privacy on for NEW installs only (`config.NEW_INSTALL_DEFAULTS`); **Full text on request** (24 h, auto-revert in every process, dashboard + `jarvis privacy`); Tailscale detection, tailnet-only bind (never 0.0.0.0, refuses without an address), `tailscale cert` with self-signed fallback, tailnet URL + QR, `jarvis phone status|approvals|revoke-all|bind`; phone approvals for every risk level except typed codes, audited, warning toggle and revoke-all. Docs: `PHONE_ACCESS_TAILSCALE.md` (+ `_HE`), PRIORITIES, V5 work package section 8. log_lint 0 violations.
- Not verified: a real Tailscale install, a real phone. Not built yet (decided in PRIORITIES, belong on the #29 branch): Adaptive Memory in instructions, goal-finished line, proactive items, Development Manager drafts list.
- Questions for the user: none new. Status: REVIEW.
## V5 pillar 2: the goal executor (22 Oct 2026) - branch `v5/goal-executor`, draft PR into main (depends on #29 and #36)

- `goal_plans.py`: persisted plans (`goal_plans` / `goal_plan_steps` in memory.db). A step = one existing Skill tool with fixed args. States draft -> ready -> running -> done, plus paused / blocked / failed / cancelled. Owner approves the plan once (approval popup); the Core `PlanRunner` (polls every 2 s, feature `goal_plans`) runs it; every non-read step asks first (tools with their own confirm ask for themselves). Steps call `registry.call` as origin "model" on route "plan", so the permission policy and the untrusted-content guard apply: after a tool returning text from others a changing step is BLOCKED until the owner resumes.
- Caps per plan: 12 steps, run time (default 30 min, human wait not counted) and AI requests (default 20, counted from the shared budget; the daily budget stays in force) -> plan `blocked` with a note. Checkpoint per step; after a restart running plans are paused, a read step in flight is re-run, a consequential one becomes `unknown` and needs retry or skip (never repeated on a guess).
- Surfaces: voice tools `goal_plan_propose` (draft only), `goal_plan_show` (read), `goal_plan_run` (asks the owner); dashboard "Goal plans" page (EN+HE, approve/pause/resume/cancel/retry/skip/delete); `jarvis goals plan list|show|new|approve|pause|resume|cancel|retry|skip|delete`; guide `goal_plans.toml`; Hebrew via `tools/catalog_add.py`.
- Tests: `core/tests/test_v5_goal_plans.py` (11, fake skills + fake clock, kill/restart-resume, no network). Also green: guides, cli_parity, dashboard_locales, log lint (0), features, capabilities, m3_core, v5 pillars/followups.
- Known: `test_p_perf::test_backend_prompt_does_not_grow` was ALREADY failing on the base (58998 > 57310 chars); the three plan tools add about 870 chars. Needs an owner decision on the budget or a trim elsewhere.
- Questions for the user: should a finished plan tick off its linked goal's steps automatically (not done: goals still only track)? Should plan results also be spoken when done (now: shown by `goal_plan_show`, dashboard and CLI only)?

---

## V5 follow-ups: the four owner decisions of 21 Oct 2026 built - branch `v5/pillars-followups`, draft PR into `v5/mobile-access`

- **Preferences in every conversation:** `preferences.prompt_block/attach_live/augment_instructions`; confirmed, non-stale, non-sensitive items only, newest first, 120 estimated tokens max; a named block on the Context page; switch = feature `prefs_in_prompt` (dashboard checkbox on the Learned preferences page with the exact text sent, `jarvis prefs prompt on|off|show`). Applies from the next conversation (voice) / next request (typed).
- **Goal-done line:** `goals.GoalCheer` polled from the Core tick (feature `goal_done_line`): one fixed line ("Done, nice work." / Hebrew if the goal text has Hebrew), only if a conversation is open or the camera sees the owner (unknown = silent), never 22:00-07:00 (alerts' window; the alerts' own quiet switch does not unlock it), expires after 10 minutes. Gates in `calm.py`.
- **Proactive Intelligence:** `proactive.py`, feature `proactive_intel`, setting `proactive.mode` = `on_request` (DEFAULT) | `in_conversation`. Items come from the owner's goals (no movement 7 days, past its date); listed on the Proactive offers page / `jarvis proactive` / `proactive_show`. In `in_conversation` mode at most ONE item is added to the instructions when a voice conversation starts (not in quiet hours); nothing outside a conversation, no job, no alert.
- **Development Manager:** `devmanager.py`, feature `dev_manager`: local drafts only, saved with the existing `diag/tickets.py` code (`draft(prefix, origin)` parameters; `listing(dev=)` keeps them off the Health page). Page Development drafts, `jarvis dev`, tools `dev_suggest/dev_drafts/dev_discard`. Posting exists only through the existing approval-gated `tickets.file` (popup with full text, private repo in `health.ticket_repo`; empty = stays local); no voice tool posts.
- Every feature: guide TOML (EN+HE) - `goals`/`adaptive_memory` extended, new `proactive`, `dev_manager`; CLI parity entries; Hebrew `about` + dashboard catalog through `tools/catalog_add.py`; tool descriptions <= 80 characters.
- Tests: `core/tests/test_v5_followups.py` (27, no skips, fake clock, no network/browser); `log_lint` 0 violations.
- Questions for the user: (1) Proactive offers are OFF until the mode is switched to "in_conversation" (read "default = only on request" literally); should that be the default? (2) The goal line also needs the camera or an open conversation to know you are at the PC; without a camera it is only spoken during a conversation. OK? (3) Offers use goals only for now; which other sources (calendar, unread mail) may become items?

---

## V5 pillars 2+3 software slice: Goals and Adaptive Memory (21 Oct 2026) - branch `v5/pillars-goals-memory`, draft PR into `v5/mobile-access`

- Design: [plans/V5_PILLARS.md](plans/V5_PILLARS.md). New `goals.py`, `preferences.py`, `dashboard_static/pillars.js` (Goals + Learned preferences pages), `jarvis goals` / `jarvis prefs`, features `goals` and `adaptive_memory`, two guides, EN+HE catalog.
- Goals only track (never act); outward steps and deletes ask the owner; the check-in exists only on request. Preferences are proposals until an explicit yes; sensitive text is refused; all items visible/editable/removable.
- Verified locally: `test_v5_pillars.py` 18 passed; guides, cli_parity, dashboard_locales, log_message_lint (0 violations), capabilities, features, hebrew_texts, dashboard, backup, privacy, perf_probes pass. Full suite and CI not run.
- Questions for the user: see "Open for the owner" in the design (inject confirmed preferences into the model instructions now? spoken "well done" on a finished goal?).
## Issues pipeline (21 Oct 2026) - branch `v5/issues-pipeline`, public repo igalgerman/jarves

- Done: `core/src/jarves/issue_registry.py` (registry file issues_registry.json, ids JI-xxxxxxxx, content-free evidence by construction, feeders: selftest/speedtest via selfspeed, diag findings incl. crash finding, tool-failure and crash log sink, dashboard JS errors, `jarvis issues add`), dashboard page **Bug reports** (EN+HE, `bugs.js`; named so it does not clash with the existing conversation "Issues" page) with Report button: exact bundle shown, approvals popup, then GitHub issue labelled `from-diagnostics` on igalgerman/jarves (no token: saved to issue_reports/), `jarvis issues`, voice tool `list_issues`, fixed-in tracking from CHANGELOG "Fixes" lines, `core/tools/triage_issues.py`, docs/BUGFIX_LANE.md, guide issue_reports.toml, feature row `issue_reports`. Tests: core/tests/test_issue_registry.py (fake GitHub on loopback).
- Not verified: a real report filed with an owner token; the dashboard page in a real browser (no browser test added).
- Questions for the user: (1) page name: kept "Bug reports" because "Issues" already exists for conversation problems; say if you want them merged or renamed. (2) Create the label `from-diagnostics` on igalgerman/jarves.

---

## Self-test and speed test (21 Oct 2026) - branch `v5/self-and-speed-tests`, public repo igalgerman/jarves

- `jarvis selftest [--deep] [--audio] [--json]` (core/src/jarves/selftest.py, `selftest.run(deep=False)` returns `SelfTestResult` dataclasses): quick read-only checks (config, DB integrity, file permissions, feature probes, tool/guide/CLI-parity consistency, EN+HE locale completeness, log privacy, disk, version/updates). `--deep` adds a real Core start/stop in a temp home, wake model load, speech engine and audio device checks; nothing is spoken or recorded unless `--audio` (one quiet tone, 0.5 s mic level, dropped).
- `jarvis speedtest [--class owner|ci] [--gate] [--bench] [--no-net] [--no-store]` (speedtest.py, `speedtest.run()` returns `SpeedResult`): startup from the log, idle CPU/RAM/disk of the running Core (procstats.py, shared with bench_startup), dashboard and feature probes in-process (perf_gate now calls the same functions), tool p50/p95 from TOOL_RESULT, wake-to-first-audio and dead-air p95 from the monitor events, one HEAD to api.openai.com/v1/models (no key, nothing billed), installer size. Budgets come from core/perf_budgets.toml (5 new metrics with placeholder baselines); result pass/warn/fail, warn when more than 20 % worse than the median of the last 5 runs.
- selfspeed.py: history is ONE small file `selfspeed.json`, one atomic write at the end of a run (idle-writes gate unaffected); schedule policy (no scheduled run 22:00-07:00 per alerts.quiet_*, nothing heavy during a voice session, a skipped run is logged as `SELFSPEED_SKIPPED` and not queued); jobs: self-test daily 08:15, speed test weekly Sunday 13:00; voice tools `run_selftest` / `run_speedtest` (read, no approval, one sentence plus "details on the dashboard"); dashboard page `#/speed` "Health & speed" (EN+HE, inline SVG sparklines, Run now buttons); feature `self_tests`, guide `self_tests.toml`, CLI parity rows.
- CI: the perf job also runs `python -m jarves.cli speedtest --class ci --gate --no-store --no-net` (same code path). Tests: core/tests/test_selfspeed.py (fake clock, planted faults, no network or browser).
- For the self-management skill (branch v5/self-management): call `selftest.run(deep=False)` / `speedtest.run()`; the latest results are also in `selfspeed.History(home).latest("selftest" | "speedtest")`.
- Unverified: owner-PC numbers (the five new budget baselines are placeholders until the first real runs), deep/audio checks on real hardware, the schedule over a real week.

---

## MIGRATION HANDOFF — ONLY ACTIVE PUBLIC REPOSITORY (20 October 2026)

**Scope of this handoff: repository migration only; not a general V4 status report or future roadmap.**

- **Migration audit (20 Oct 2026, latest): [MIGRATION_AUDIT](MIGRATION_AUDIT.md).** All 59 legacy refs were compared by blob hash with public `main` and draft PRs #6-#11. Nothing is lost while the legacy repo stays readable. Three missing doc sets were restored in draft [PR #12](https://github.com/igalgerman/jarves/pull/12) (owner PRIORITIES, a FULL_TEST_PLAN answer, SWV4_P01_VERIFY). The five active-agent branches (E, F, F-go, K, O) must be ported after their agents stop; `v4-f-whatsapp` has no public PR yet. Legacy issue #17 was closed as done. The other 16 stay open, and their full checklists are copied into public #1-#4; owner actions are in #1. Public `main` CI is red: redaction-audit timeout + idle-write perf gate. **Questions for the user:** OD-15, OD-05, OD-12 (see #1); the Android demo branch (port in V5 or drop); when the agents finish, archive the legacy repo yourself.

- **Old private `igalgerman/JARVIS`: DEPRECATED / no longer relevant for development.** Preserve read-only as a source for migration; **never commit, push, sync, create issues or PRs there**. The repo is **not yet GitHub-archived**.
- **New active `igalgerman/jarves` (public): the ONLY writable source of truth.** Check local remotes, worktrees and running agents before any changes; never destroy uncommitted agent work.
- **Transferred so far:** 17 legacy open Issues mapped to public Issues [#1–#5](https://github.com/igalgerman/jarves/issues). Five public DRAFT, unmerged PRs: [#6 WhatsApp](https://github.com/igalgerman/jarves/pull/6) 23 files, [#7 Orchestrator](https://github.com/igalgerman/jarves/pull/7) 26, [#8 Self-Diagnostics](https://github.com/igalgerman/jarves/pull/8) 28, [#9 Delegation/Isolation](https://github.com/igalgerman/jarves/pull/9) 12, [#10 Dashboard QA](https://github.com/igalgerman/jarves/pull/10) 3. **92 new files preserved; integration changes NOT yet ported; no verified integrated build or final CI.**
- **Remaining migration steps:** compare old private V4 changes against current public code, migrate only needed safe modified files, validate imports/tests/CI, reconcile old Issue acceptance criteria, update lingering old links/remotes, and **only after no work is left** archive the old GitHub repo. Never publish private logs/keys/account/session data.
- **Second-agent reading:** [MIGRATION_HANDOFF](MIGRATION_HANDOFF.md) for exact pending work and [AGENT_HANDOFF_TEMPLATE](AGENT_HANDOFF_TEMPLATE.md) for a copy-ready migration-only instruction. **Do not expand this task to product features or other milestones.**

---

## Self-management (21 Oct 2026) - branch `v5/self-management` (off `jarves/main`)

- New `core/src/jarves/self_manage.py`: the `self_manage` Skill (4 tools, ~600 chars of description: `self_status` read; `self_change`, `self_connect`, `self_undo` destructive = always the approval popup with a plain BEFORE / AFTER), `jarvis manage status|file|memory|feature|skill|setting|connect|history|undo` + `jarvis undo`, dashboard page Self-management (`/api/selfmanage*`, EN+HE catalogs), guide `self_manage.toml`, feature `self_manage` (medium risk, honest `implemented_unverified`), CLI/tool parity entries.
- Reuses: Workspace (backup history, undo), MemoryStore, Settings.ALLOWED, Features + feature_setup.prepare, GeneratedSkills.set_enabled, connect.status_items, the approvals service (`spoken_kind` gained three entries: kind only, one language), the registry untrusted-turn guard (tainted turn = TOOL_UNTRUSTED_TURN for every change tool) and the delegation text (`workspace.FRONT_SELF`).
- Undo: files via workspace history (refused if the file changed again), other kinds via `<home>/self_manage/changes.json` (previous value only; never secrets). Audit: fixed-literal `SELF_MANAGE_*` log codes, never the written text.
- Out of reach: TOOLS/SETUP/FEATURES.md, HEARTBEAT, secrets, approvals policy, `PROTECTED_FEATURES` / `PROTECTED_RX` safety switches, settings not on the allow-list, built-in skills (follow their feature). Any text that looks like a secret is refused.
- **Connect interface:** `jarvis connect --check` (branch `v4/connect-unset-only`) is not on `jarves/main`. `self_manage.default_check` calls `connect.check_items(home, cfg, only)` when it exists, else `connect.status_items`. Adapt `default_check` once that branch merges.
- Not done / honest limits: the guided connection window opens on Windows only (elsewhere the answer names the `jarvis connect --only X` command); CLI approval is a typed `yes` in an interactive terminal (no `--yes`); `PROTECTED_FEATURES` list is a conservative first cut for the owner to review.
- Question for the user: should `AGENTS.md` edits by voice stay allowed (with the popup), or be CLI/dashboard only? (Implemented as allowed, per the request.)

---

## SW-V4 campaign PASS C — voice (21 Oct 2026) - #17, branch `test/v4-campaign-c`, draft [PR #20](https://github.com/igalgerman/jarves/pull/20), [report](reviews/SWV4_CAMPAIGN_VOICE.md), [bugs](reviews/SWV4_CAMPAIGN_BUG_TABLE_VOICE.md)

- 50 PASS, 3 FAIL (22/23 wake false positives P3; 39 fixed, live retest pending), 39 BLOCKED of 92. Live spend at most $2.45 of the $8 slice.
- Fixed with focused tests: V4-VC-1 harness reads V4 log fields; V4-VC-2 short answers said twice; V4-VC-3 a re-delegated approved repeat was refused and reported as failed; V4-VC-4 credits error was not fatal (also retested live); V4-VC-5 a hung live shutdown caused a background CRASH. 9 P3 open.
- Dead air p95 1.44 s over 83 turns, greeting 0.50-0.52 s, barge-in 0.6 s, wake connect 2.8-3.4 s.
- **Blocker:** the OpenAI account is out of credits (`credit_balance_exhausted` since 21:08 UTC 20 Oct; still empty 00:14 UTC 21 Oct). Not run: 51-62, 63-70, 71-84 (including the 80 re-run), 100-104 and the live retests of 39/4/24/35. The host stopped the full offline suite for low memory; the focused files passed (170) after merging integration head fd937bc.
- Questions for the user: (1) please add OpenAI credits; the blocked groups need about $4-5 (commands are in the report). (2) OD-05: a read-only admin key for scenario 62. Status: WAITING FOR USER (credits).

---

## Repoint to the public repo + clean shutdown (21 Oct 2026) - branch `fix/repoint-and-shutdown` (off `v4/integrated-b-c-d-g-m-r01`, draft PR into it)

- **V4-RP-1:** `updates.REPO = igalgerman/jarves` (source repo); signed installers + `latest.json` stay in `igalgerman/jarvis-releases` (`DEFAULT_RELEASES_REPO`). The "releases not reachable" text now names both repos and the download page. New `tests/test_repoint.py`: fake release host with a signed `latest.json` finds a newer 3.x/4.x build, never offers a downgrade, rejects a wrong signature.
- **V4-RP-2:** `health.ticket_repo` default is empty: drafts stay local (Health page, `jarvis health ticket`); `tickets.file` returns "stays local" without asking or calling gh until the owner sets a repo (Health settings) AND approves (the private-repo check of OD-35 still applies). Button text (he/en + locale catalogs) now "File on GitHub (asks first, only if a repo is set)".
- **V4-RP-3:** all `github.com/igalgerman/JARVIS` links in `core/src`, `packaging` (installer publisher URL) and tests now point at `igalgerman/jarves` (issue numbers kept: re-map them if the migrated public issue numbers differ). `test_no_product_file_links_to_the_deprecated_private_repo` guards core/src, packaging, .github, scripts (empty allow-list). README/AGENTS/docs/reviews/MIGRATION_* untouched on purpose.
- **V4-SC-1:** `SelfCheck.cancel` event checked between steps (`CheckCancelled`); the worker thread closes its own handles; `SelfCheckService.stop` sets the event and waits for the thread before `ops.stop` lets the databases close. Test with a stopping Core (fails on the old code with `ProgrammingError: closed database`).
- **P3:** V4-WA-2 new `Tool.precheck` hook runs before the owner is asked (unknown/used draft -> `WA_UNKNOWN_DRAFT`, no popup); V4-AG-1 taint refusal wording is source-neutral; V4-LG-1 websockets handshake-probe errors are logged at INFO with a fixed message.
- **Open question for the user:** the legacy issue numbers in capability/roadmap links (#14..#28) were repointed to `igalgerman/jarves/issues/N` mechanically; the public issues are numbered #1-#5 so these links need a mapping decision.

---

## V4 perf fix: slow feature probes block features_list (20 Oct 2026) - #1, branch `perf/fix-slow-probes` (off `v4/integrated-b-c-d-g-m-r01`, draft PR into it)

- **Problem:** with every V4 package registered (59 features) the gate's `features_list_ms` was 171-359 ms (median 260 ms over 7 fresh processes; budget ci 150 / owner 50; V3 baseline 10.5). Cause, per probe (first list, busy PC): camera 132 ms (camera.backends imported numpy, then the probe imported OpenCV), tray 39 ms (PIL + pystray), agent_orchestrator 33 ms on EVERY expired list (4-7 PATH scans for claude/codex), Google probes (httpx import), browser/wake/camera* 2-4 ms each, WhatsApp bridge = localhost HTTP status with a 2 s timeout. No probe spawned a subprocess; `claude agents --json` runs only in the orchestrator service, not in a probe.
- **Fix (features.py):** slow probes (`register(slow=True)`: music, camera x4, wake_word, Google x5, agent_orchestrator, whatsapp x2, tray_icon, display_control, browser; or any probe measured > 10 ms) answer from the last result and re-check in ONE background worker (`jarves-probe-refresh`, started with `_thread` so the caller never waits for thread start: `threading.Thread.start()` measured 10-80 ms on this busy PC). `list()` never runs a slow probe on the caller's thread; cheap ones run in line within a 30 ms budget. New status fields: `check` = fresh / stale / checking and `checked_s_ago`; never-checked = health `checking` with a fixed reason (en/he). `list(wait=True)` for one-shot CLI / diag bundle / self-check. `prewarm()` at Core start, live-session start and dashboard. Light `camera/probe.py` (no numpy, cv2 via find_spec); `google_auth` / `chatgpt_auth` import httpx where used; dashboard Features page shows "checking" / "checked N s ago, re-checking" and re-renders once.
- **Measured (alternating before/after, 7 fresh processes each, all modules registered, PC at ~100 % CPU from other agents):** features_list_ms before 171-359 (median 260) -> after 12-23 (median 15.7); live_tools_probe_ms 0.4-2.7 -> 0.0. All features expired (15 s idle worst case): 51-83 ms -> 5-13 ms. Full `perf_gate.py --class ci`: PERF GATE OK (features_list 1.8 ms). `--class owner`: everything ok except idle_disk_write_kb_per_min 36.5 > 10, which also fails on the base branch (32.7, health.json / store.db-wal / runtime.json) - pre-existing, not touched here.
- **Tests (one pytest at a time, 0 skips):** new `tests/test_perf_probes.py` (5: fake 2 s `claude` on PATH, stale labelling, background update, undeclared-slow learning, gate in the full-module condition: fails on the base with 1227 ms). Passed: test_perf_gate 44, test_features 19, test_swv3_p03_perf 8, capabilities, feature_setup, camera, camera_cli, log_message_lint, hebrew_texts, google, chatgpt, browser, selfcheck, cli_english, cli_parity, dashboard_lan/phone/wph, execute, m3_core, profiles, workspace_memory, guides, setup_wizard, google_setup, agents_contract, whatsapp_core/surfaces, diag_bundle_strip, runtime.
- **Pre-existing failures, also on the base branch (not caused here):** `test_dashboard_locales` ar/ru coverage 19.8 % < 20 %; `test_agents_system::test_voice_tools_cli_and_dashboard_see_the_same_sessions` (TypeError at line 141).
- **Questions for the user:** (1) OK that a never-checked slow feature shows "checking" for about a second on the very first Features view after a start (instead of blocking)? (2) The owner-class idle write budget (10 KB/min) fails on the integrated V4 tree (~33-37 KB/min): fix in a separate task, or raise the budget?

## SW-V4 H: assisted dialing through Phone Link (20 Oct 2026) - #17, branch `v4/h-dialing` (on `v4/integrated-b-c-d-g-m-r01`), [report](reviews/SWV4_H_DIALING.md)

- "Call Dana" by voice (`dial_prepare` / `dial_open`), `jarvis dial <name|number> [--yes-on-screen]`, and the dashboard Phone calls page: contact resolution (memory contacts, whatsapp book, read-only Google Contacts), E.164 + digit-by-digit read-back, popup with who / number / source / app, then a `tel:` link; the owner presses Call. Numbers from email, web, messages or recalled text are never dialled; a spoken number must be in the owner's own words (memory-only ring). Emergency numbers just open the dialer. Rate limit, hashes-only audit, feature `assisted_dialing` (SAFE_OFF), guide, capability row, wizard group, bundle check, en/he texts and catalogs.
- Verified: `core/tests/test_dialing.py` 91 passed, 0 skips (fakes only, nothing opened); log lint 0 violations; about 700 related tests green. Pre-existing: `test_redaction_audit` real-Core timeouts on this PC at the base commit too.
- NOT verified: a real `tel:` handoff into Phone Link (OH-01), Phone Link paired state, the spoken GPT-Live flow (scenarios 85-90), Google phone numbers with a real account, independent Opus security review.
- Questions for the user: (1) Is Phone Link the default `tel:` app on your PC? (OD-16, needed for OH-01). (2) Read the number aloud before every approval (current) or only for new numbers? Status: USER DECISION REQUIRED (1), DEFERRED (2).

---
## SW-V4 package L: installer, updates, rollback (20 Oct 2026) - #17, branch `v4/l-installer` (public repo, draft PR to main), [report](reviews/SWV4_L_INSTALLER.md)

- Done (fake/loopback tests, 0 skips): versioned data migrations with backup first and restore-from-backup down path; update flow against a local signed release server (SHA256+size, ask first, failures leave the old version, downgrade refused); `updates.rollback` + `jarvis update --rollback` + Updates page button (approval) + failed-first-start offer; silent installer args; signed-manifest `previous` block; clean-install residue audit; upgrade/rollback CI scenario; pinned-bridge payload check; installer size/time budgets feeding the M gate; restricted-token probe (study, not wired in).
- Not verified: no real installer was built or run here (CI-only scripts); old-installer fixtures are not pinned (`packaging/upgrade_fixtures.json` empty, CI says NOT RUN); no second-PC run; Go bridge not on main.
- Questions for the user: (1) **Autostart default**: the installer now leaves "start at sign-in" OFF (you asked for OFF by default), reversing the earlier owner decision "ticked by default" in `jarves.iss`/INSTALL_TEST. Confirm OFF? (2) OD-08 stays unsigned (recorded). (3) Pin real 0.1.0.x and 3.0.0.x installers (URL + SHA256) in `packaging/upgrade_fixtures.json` so the matrix runs. (4) Helper isolation via write-restricted token + protected `<home>` DACL is feasible (report section 8); want it as its own package? Status: WAITING FOR USER + CI run.

## SW-V4 B: privacy hardening + full redaction audit (20 Oct 2026) - #15 / #17, branch `v4-b-privacy` (off `origin/main`), [report](reviews/SWV4_B_PRIVACY.md), [sinks](reviews/SWV4_B_SINKS.md)

- `logs.*` messages are fixed literals (466 call sites migrated); variable parts are fields classified once in `logs.FIELD_CLASS` (safe/secret/content/pii, unknown = content); provider/exception text -> `provider_error` / `exc_text`; AST lint `tools/log_lint.py` (0 violations). Secret patterns extended (OpenAI/Anthropic, JWT, Google, Telegram, Meta, GitHub, DPAPI, URL tokens, bearer, user-profile paths). Third-party loggers and crashes (main/thread/asyncio/faulthandler) routed and classified.
- `jarvis diag` always strips content, hashes pii (per-bundle salt), drops + counts malformed lines; fixed sinks: ticket drafts, health, approvals audit, tasks.jsonl, context traces, dashboard log view, backups, Godot E2E print. "Privacy: off - conversation text is kept in logs" banner (dashboard + `jarvis status`, en/he).
- Verified locally: full suite 1657 passed, 0 skipped; `tools/redaction_audit.py --both-modes` (real Core, planted data, 36 sinks, raw/JSON/URL/base64/UTF-16/NFC/NFD) 0 violations in both modes.
- Open: independent Opus review (not the author), CI run URL to close #15, owner rows OR-01..OR-04, public export script key patterns (R4, branch v3-public-sync).
- Questions for the user: OD-12 - should NEW installs start with `log.privacy` ON (recommended; your profile stays as is)? Status: WAITING FOR USER (OD-12) + review.
## SW-V4 package C: independent re-review (20 Oct 2026) - #16, #17, branch `v4-c-review` (off `main` 5a98f13), [report](reviews/SWV4_C_REVIEW.md)

- **Fixed, each with an exploit test** that fails on 5a98f13 (`core/tests/test_review_c.py`, 28 tests, 0 skips):
  - C-01 (P1): helper dangerous commands could be approved by voice. Nested shells, late `-r` / `/s` flags, `-encodedc` / en-dash PowerShell, `python -c` rmtree and `find -delete` now need the typed code.
  - C-02 (P1 chain): the JARVES data-folder refusal was bypassed by a wildcard, a nested quoted path, traversal, a drive-less path, `\\localhost\C$` or `~`.
  - C-03 (P2): a failed untrusted tool did not taint the turn.
  - C-04 (P3): `.cmd` re-parse.
  - C-06 (P1): Instagram DMs were not marked untrusted.
  - C-07 (P3): the webhook hung on `Content-Length: -1`.
  - C-08 (P2): a named profile could reach the default profile's secrets (helper + folder grants).
  - C-09 (P3): `secrets.env` line injection.
- **#16:** every item triaged (see the paste-ready block in the report). OD-14 applied: Pear history trimming is OFF by default, with a switch in Settings.
- **Backlog:**
  - Fixed: O-04 (navigation refusals logged at INFO), O-05 (new `jarvis status` exit 4 = headless, face absent; documented in `--help`; campaign tools accept it), B5 (stop/skip claim verbs), and the helper health row now follows its switch.
  - Already done on main and verified: O-03, O-06, B3, B4, CV-16.
  - Not changed: Q08 (design) and CV-17 (needs paid live measurement).
- **Not run:** the full suite and CI, the real-Pear validation, the `suite_v3_voice` regression and the OD-13 Codex second opinion.
- **Questions for the user:**
  1. R-01: an approved helper command still runs with your full rights and could reach JARVES's data through a path it builds at run time. OK to plan OS-level isolation (a restricted token, or a deny ACL on the data folder) in package L/O?
  2. Release note: should `jarvis status` exit 4 (headless) go into v3.x, or wait for V4?
  3. Run the real-Pear S10 / stalled-player check and `suite_v3_voice` (≤ $1.5) now, so #16 can close?

  Status: WAITING FOR USER.

## SW-V4 G: conversation recall (20 Oct 2026) - #17, branch `v4-g-recall` (off `origin/main`)

- Report: [reviews/SWV4_G_RECALL.md](reviews/SWV4_G_RECALL.md). New `recall.py` + `recall_text.py`: SQLite FTS5 index per profile in store.db, indexed on every history commit (same transaction), Hebrew niqqud / final letters / clitic prefixes / suffixes, transliteration keys, bilingual concepts, date phrases he+en; tools `conversation_recall` / `conversation_search` (untrusted output) / `conversation_show`; `jarvis recall "q" [--since] [--json]` + `--stats/--rebuild/--repair/--forget`; dashboard Recall page, Conversations filter through the index with snippets, Delete conversation (approval popup); privacy mask/delete purge the index; 90-day retention keeps a local summary; size cap; feature/guide/wizard/bundle/capability/parity/catalogs.
- Score: 40-question set Recall@3 40/40, right day 40/40 (`python tools/recall_quality.py`); trigram baseline 31/40 (34 with prefix strip). 10 000 turns: build ~1 s, index 3.4 MB, search p95 19 ms. 95 new tests pass; related suites pass.
- Live voice 79-84 (`tools/suite_v4_recall.py`, ~$1.0 of $2): 79, 81, 82, 83, 84 PASS; 80 4/5 (fix for the STT name variant made after, not re-run: port busy). Two real bugs found live and fixed (the question quoted back from the open conversation; a private code read aloud from the tool data).
- Questions for the user: in the report (notes/reports indexed while privacy is on? summary retention 10 years? speak JARVES's own past words?). Status: WAITING FOR REVIEW + USER.
## SW-V4 full plan (20 Oct 2026) - #17, branch `v4-plan-full` (off `v3-final`)

- Docs only, no code/tests: [JARVES_SW_V4_WORK_PACKAGE.md](JARVES_SW_V4_WORK_PACKAGE.md) is now the authoritative V4 file (replaces the `v3-v4-plan` draft). Owner rules applied: software only (phone/Pi/hardware/Tailscale/multi-device/other languages under "Later"), every item a complete feature (no MVPs).
- 15 packages, about 78 agent-days, about 10 h owner time: A owner verification + closure of #17/#24/#25/#28 (+ #7/#26/#27 leftovers), B privacy (#15, planted-secret audit of every sink), C independent re-review (#16 + open P3 backlog), D client-mode delegation (#14), E Agent Orchestrator (#49, Claude Code + Codex, 12 d), F WhatsApp bridge (main number read-only first), G recall, H dialing, I Telegram/Instagram/HA live + rocket alerts last, J wake word production, K dashboard en+he (ar/ru removed), L installer upgrade/rollback/signing, M perf budgets + CI matrix + nightly, N docs, O Self-Diagnostics 2.0 (monitor, baselines, version regressions, root-cause hints, approved tickets/remediation, preflight, nightly canaries, scheduled selective AI review every 2 days with a cost cap).
- Testing: automatic pyramid (14 layers with commands, pass criteria, where they run), flake policy, CI matrix; owner manual script 7.1-7.21; agent script; campaign protocol A-F; release checklist.
- No `SWV4_WAKE_TRAIN.md` / `v4-wake-train` branch exists on origin; J builds on the merged `wake_train.py` pipeline.
- Questions for the user: the open decisions table in section 11.1 (OD-01 budget, OD-08 signing confirm, OD-12 privacy default, OD-15 delegation default, OD-20..24 orchestrator, OD-25..31 WhatsApp, OD-33..37 self-diagnostics AI review, and others). Already-answered decisions are listed in 11.2 so nobody re-asks. Status: WAITING FOR USER (approval of the plan + decisions).
## V4: custom "Hey Jarvis" head from the owner's recordings (20 Oct 2026) - #17, branch `v4-wake-train` (off `v3-final`), [report](reviews/SWV4_WAKE_TRAIN.md)

- Trained an openWakeWord-front-end head (`jarvis wake train`) on the 24 owner clips + synthetic TTS. Measured with 3-fold cross-validation (16 train / 8 test, each clip tested once) against `hey_jarvis` @0.15.
- At threshold 0.95: owner hold-out 21/24 vs 19/24 (65/72 vs 56/72 incl. noise/quiet); false wakes on the 24-min `wake eval` corpus 23-32 vs 42 (real WakeListener); campaign phrases 2-3/20 vs 3/20 (x4 voices: 7-9/56 vs 14/56); latency median +40 ms (310 vs 270 ms), max ~1.1 s; CPU equal (~6 ms/frame).
- **Applied** (better on both axes): `%LOCALAPPDATA%\jarves\wake\hey_jarvis.npz`, threshold 0.95, from the next Core start (the running Core was not restarted). Revert: new `jarvis wake train --revert` (test added), or delete the .npz.
- Code: `wake_train.NEAR_EN` hard negatives ("hey <name>", disjoint from the eval near-miss lists); `--revert`. No WAVs/models in git.
- Not measured: the two-stage Whisper confirm with the custom head (RAM). Still wakes on bare "Jarvis"; new false wake "A jar of peanut butter"; a second wake within ~4 s of a previous one can be missed (head ring reset).
- Questions for the user: (1) Try it in the room (`jarvis stop`, `jarvis`; then `jarvis wake test --false-wakes 30`), keep or revert? (2) If false wakes are noticeable, OK to raise to 0.99 (about half the false wakes, 1 fewer noisy detection)? (3) Record ~20 more clips (other rooms, "ok jarvis", far away) for a better head? Status: WAITING FOR USER.

## V4 research: real WhatsApp send/read (20 Oct 2026) - #17, branch `v4-whatsapp-research` (off `v3-final`)

- Docs only, no code/tests: [plans/WHATSAPP_REAL_ACCESS.md](plans/WHATSAPP_REAL_ACCESS.md). Compared Cloud API + coexistence, whatsmeow / neonize / Baileys / whatsapp-web.js / WAHA, mautrix / Beeper, Playwright/Desktop UI automation, Android notification listener / accessibility / Tasker, ToS, ban risk, privacy; sources with URLs, checked 20 Oct 2026.
- Findings: official Cloud API cannot run a personal account (business number or Business-app coexistence, templates + 24 h window, groups not synced, paid since 1 Oct 2026, +972 coexistence unconfirmed). Full send/read needs an unofficial linked-device client (ToS violation, real but unquantified ban risk, warnings reported even at low volume). Lowest-risk read = Android notification listener on the phone that runs WhatsApp.
- Recommended: isolated `whatsapp_bridge` process on whatsmeow (Go; neonize = plan B), loopback + token, encrypted store, modes off/read/send set only on the PC, read-only first on a SECONDARY number, sends only to allow-listed contacts via the approvals popup (recipient + full text), rate limits, kill switch, incoming text untrusted; staged S1 fake -> S1 read-only -> S2 confirmed send -> S3 voice -> S4 phone; fake-bridge test plan + owner checklist.
- Questions for the user: see section 7 of the plan (main-number ban risk vs secondary SIM, read/send scope, approval surface, retention, cloud model sees message text, Go binary OK, linked device vs Android route). Status: WAITING FOR USER.

## V4 P0.3: CLI / dashboard parity (20 Oct 2026) - #17, branch `v4-p03-parity` (off `v3-final`)

- Owner D1/D2: (1) Timers and Lists dashboard pages (Everyday group; list, add, cancel/complete, clear asks first) plus CLI `jarvis timers list|add|cancel`, `jarvis lists show|add|check|remove|clear [--everything] [--yes]` (clear asks on the terminal), read-only `jarvis notes|jobs|reports`; code in `cli_parity.py`, `timers.py`/`lists.py` (routes), `dashboard_static/m3.js`; en/he catalogs and nav/search keywords added once; guides timers/lists mention the CLI.
- (2) ONE rule for an unavailable feature: `features.switch()` saves it as ON and every surface (CLI, dashboard `state_text`, model tool) says `unavailable (reason)`. `jarvis features on X` no longer refuses (prerequisite install is best effort, exit 0).
- (3) Spoken approvals name only the kind of action: `approvals.spoken_kind()` / `Approval.spoken` feed `_VoiceApproval` and the live confirm note (which now forbids reading details); popup/dashboard text unchanged. Optional `details["recipient"]` is named (e.g. "Send a WhatsApp message to Dana?"); no caller sets it yet.
- Parity test `tests/test_cli_parity.py` (25 tests): every feature and every Skill group has a CLI command or a documented dashboard-only/voice-only marker in `cli_parity.FEATURES` / `TOOL_GROUPS`; new features/skills fail the test until listed. Not done: `jarvis files` CLI; headless Edge captures of the new pages at 1440/390 px (needs a display pass); Opus UX review.
- Questions for the user: (1) WhatsApp/Telegram/mail senders should pass the recipient first name in `details["recipient"]` so the spoken question can say "to Dana": OK to expose names aloud? (2) Dashboard clear uses a browser confirm; want the full approvals popup instead? Status: WAITING FOR USER.

## Approval popup redesign (20 Oct 2026) - #17, branch `v3-popup-ux` (off `v3-final`), [screenshots](reviews/swv3_popup/)

- Owner feedback: "where is the button for approve?", "too much text", "not beautiful". `core/src/jarves/approval_popup.py` is now a compact 420 px card: risk chip + "JARVES asks" + countdown, ONE bold line from the request kind ("Run a command?"), ONE sentence (title, max 90 chars, ellipsised), command / folder / why behind a collapsed "Details" toggle, thin countdown bar, two big buttons packed FIRST at the bottom (Approve = Enter once armed, Deny = Esc / close). Typed-code field only for dangerous requests (Approve disabled until it matches). Window <= 60% of the screen, always inside the work area (taskbar-safe), DPI aware, Segoe UI, light/dark from `AppsUseLightTheme`, Windows 11 rounded corners + matching caption.
- Language: new `[ui] popup_language` (default `en`; `he` = whole card in Hebrew, right-aligned, never both). No `v3-cli-english` branch exists on origin, so this is a separate setting; merge it with the CLI language setting when that lands. Found: `Segoe UI Variable Text` scrambles Hebrew letters in Tk 8.6 on Windows; plain `Segoe UI` renders correctly.
- Result-file protocol and `--test-invoke approve|deny|wrong-code|hold` unchanged (+ `show`: visible, never answers, for screenshots).
- Verified: `core/tools/popup_screens.py` builds 14 real windows (short/long/details open/dangerous/Hebrew/light/dark/150%/200% simulated scaling), asserts both buttons inside the window and the screen, saves PNGs + `layout_report.json`; `tests/test_approval_popup_ux.py` 17 passed; `tests/test_helper_approvals.py` 63 passed.
- Questions for the user: (1) Enter now approves (as asked) once the 0.9 s arm delay passed; keep that, or should Enter stay Deny for safety? (2) `C:\work\popup_demo.ps1` runs the main checkout (`C:\work\jarvis\core\src`): it shows the new card only after this branch is merged/checked out there. Status: WAITING FOR USER.

## SW-V3 integration on `v3-final` (20 Oct 2026) - #17, [report](reviews/SWV3_INTEGRATION_REPORT.md)

- Merged (no-ff, both sides kept): v3-fix-show-twice, v3-approvals, v3-ci-yaml-fix, v3-wake, v3-connect, v3-campaign-dash, v3-campaign-features, v3-campaign-voice (2 rounds), on top of the earlier 8 V3 branches.
- Fixed while integrating: workflow YAML test (`test_ci_workflows.py`), Google reminder spoke out loud while hidden/idle (now popup unless a conversation is open), dashboard listen backlog 5 -> 128 (random ERR_CONNECTION_REFUSED in Edge), approvals page + wake strip line in the en/he/ar/ru catalogs, 23 missing Hebrew feature texts, profile-switch test vs the approvals deny cooldown, suite_system S09/V06 harness.
- Verified locally (CI has no minutes): full offline suite 3x green, 0 skipped; suite_system 20/20 (Godot headless, start/stop x5, soak, V01-V08); `jarvis setup --check-addons` PASS; real Core 5x on a TEST profile; 32 dashboard pages x en/he/ar in headless Edge, 0 errors (`core/tools/v3_all_pages_check.py`).
- Final head `4e80261` (+ this doc commit): full offline suite 1454 passed x3, 0 skipped.
- Questions for the user: (1) `silent_live` (paid, needs your key) was not run: run it before you use the build? (2) Export `v3-final` to main / the public repo now? Status: WAITING FOR USER.
## Voice campaign follow-up: open voice items (20 Oct 2026) - #17, branch `v3-voice-fixes` (off `v3-campaign-voice`)

- Report section "Follow-up" in [reviews/SWV3_CAMPAIGN_VOICE.md](reviews/SWV3_CAMPAIGN_VOICE.md); statuses in the [bug table](reviews/SWV3_CAMPAIGN_BUG_TABLE_VOICE.md). Each fix verified on real GPT-Live with the virtual mic/speaker (`suite_v3_voice.py`, new scenarios 48 redelegate probe, 49 told-not-asked, 50 names after a prefix) plus `core/tests/test_voice_fixes_cv.py` (19 tests). Cost ≈ $1.2 (18.5 min live voice).
- FIXED: CV-15 (Core watchdog hands an acknowledged-but-undelegated turn to the session backend; honest "did not go through" if it cannot), CV-06 (unasked "רשמתי" taken back, asked-but-undone handed over), CV-05 (per-turn language note; English 6/6, back to Hebrew 2/2), CV-09 (no stale "still checking"/retry after the user moved on; barge-in counted), CV-14 (one spoken yes, result said ~2 s after the decision), CV-08 (exact local `calculate` tool, no web search), new CV-18 ("כרגע" taken for the filler: answers spoken twice). PARTLY: CV-16 ("לאביטל" now right; צחי audio inconclusive).
- Design notes: a client `response.create` returns as an ordinary `session.delegation.created` and GPT-Live speaks its result; an appended developer instruction naming Hebrew made GPT-Live skip the Hebrew answer (3/3), so the switch back is silent context.
- Questions for the user: (1) CV-16: listen once to "שלום לנועה, לאביטל ולצחי" - is צחי said right? (2) The approval note now reads the action aloud (e.g. the WhatsApp draft text): OK for private messages, or should it only name the kind of action? Status: USER CHECK (P3).

## SW-V3 campaign PASS C, voice (20 Oct 2026) - #17, branch `v3-campaign-voice`

- Report: [reviews/SWV3_CAMPAIGN_VOICE.md](reviews/SWV3_CAMPAIGN_VOICE.md); bugs: [reviews/SWV3_CAMPAIGN_BUG_TABLE_VOICE.md](reviews/SWV3_CAMPAIGN_BUG_TABLE_VOICE.md). Real GPT-Live, virtual mic/speaker, TEST-profile homes: suite_voice 1-35 + V3 36-47 (timers/alarms with the face closed, lists + spoken approvals, helper, browser on a local page, briefing without Google, WhatsApp/Telegram, profile switch, self-check, wake variants, names, greeting delay). Result: **44 PASS, 3 FAIL (P3), 0 BLOCKED**; cost ≈ $3.80 (60 min live voice). Barge-in stop 0.7 s, greeting 0.51-0.53 s after the face, dead air typically 0.1-0.9 s.
- Fixed (P2): CV-01 curator facts in privacy-mode logs, CV-02 "approve on the screen" hint when a spoken yes works, CV-03 silent suites opened the real popup, CV-10 model asked for approval without calling the tool, CV-11 briefing skipped missing Google parts silently, CV-12 Hebrew-spoken profile name not found, CV-13 profile switch accepted a spoken yes. Open: CV-15 (P2, intermittent "רגע" without delegating), P3 CV-04..09, 14, 16, 17.
- Re-run: `cd core; $env:PYTHONPATH="src"; python tools/suite_v3_voice.py --only 36-47` (COSTS MONEY; `--list`).
- Questions for the user: (1) WhatsApp asks twice (read-back yes + approval yes): keep both, or let the approval be the only question? (2) English questions are answered in Hebrew (CV-05): should JARVES switch to English for one English sentence? (3) Record the ~20 wake samples now (CV-04/07)? Status: USER DECISION REQUIRED (P3).

## SW-V3 campaign PASS B, feature by feature (20 Oct 2026) - #17, branch `v3-campaign-features`

- Report: [reviews/SWV3_CAMPAIGN_FEATURES.md](reviews/SWV3_CAMPAIGN_FEATURES.md); bugs: [reviews/SWV3_CAMPAIGN_BUG_TABLE.md](reviews/SWV3_CAMPAIGN_BUG_TABLE.md). 53 features x CLI/dashboard/Core tool, plus tools of timers, lists, notes, files, scheduler/reports, memory/curator, skills, Telegram/WhatsApp/Instagram/Home Assistant (local fakes), browser (local page), helper + approvals (answered through dashboard API and CLI), profiles, self-check, backup/restore, usage/cost, diag, camera (virtual). Result: 1429 PASS, 1 FAIL (open P3), 2 BLOCKED, 27 N/A.
- Fixed (P0-P2, each with a regression test): B-05 CLI/other-process approval answers rejected for tool-asked requests (P1), B-01 context_trace missing from CLI, B-04 helper refusals crashed the tool, B-06 face_recognition never available; P3 B-02, B-03. Open P3: O-01..O-06 (CLI parity for timers/lists, CLI-vs-dashboard "on" semantics, google_connect_help ok=False, WARNING noise, status exit 3, timer ringing state).
- Re-run: `core/.venv/Scripts/python.exe core/tools/campaign_features.py STAGE...` (see the report header).
- Questions for the user: (1) CLI `features on` refuses unavailable features but the dashboard allows them: which rule? (2) Build CLI/dashboard pages for timers and lists? Status: USER DECISION REQUIRED (P3).

## `jarvis connect` guided account setup (20 Oct 2026): branch `v3-connect`, [#17](https://github.com/igalgerman/JARVIS/issues/17), [report](reviews/SWV3_CONNECT_REPORT.md)

- One command checks Google, Telegram, the Pear API plugin, the wake word (and optional Instagram / Home Assistant), guides only what is missing, reuses the existing wizards, ends with a summary table. Flags `--only`, `--status`, `--yes`, `--reminders`. Telegram: clipboard token watch (consent, cleared after use), validated before saving, auto-pair through a one-time t.me deep link + confirmation message.
- Also: once-a-day Hebrew reminder when Google is not connected (quiet hours / autonomy / proactive setting respected), dashboard Connections "Run guided setup" card, first-run wizard offers it, feature `guided_setup` (on by default).
- Tests: `core/tests/test_connect.py` 32 passed plus the related files run one at a time (list in the report). UNVERIFIED: real BotFather / Google / Pear runs, clipboard write/clear on a real desktop, the dashboard button opening a console window. Questions for the user: none new.
## SW-V3 campaign Pass D: dashboard + log review (20 Oct 2026) - [report](reviews/SWV3_CAMPAIGN_DASHBOARD.md), [bugs](reviews/SWV3_CAMPAIGN_BUG_TABLE_DASH.md) - [#17](https://github.com/igalgerman/JARVIS/issues/17), branch `v3-campaign-dash`

- 186 page loads (31 pages x en/he/ar x 1440/390 px) plus interaction flows in real headless Edge on the offline TEST profile. 9 bugs fixed (1 P1: Home said "JARVES is off" 2 min after start because health.json was only written on change; P2: 429 from static files, row keyboard access, Undo after save, runtime table overflow, Hebrew schedules in the English UI), 9 open P3 (Hebrew/Arabic string gaps, Self-check card layout, small a11y/phone items). Log review: Core log 0 WARN/ERROR; no unexplained entries.
- BLOCKED (need Pass E hardware/accounts): live mic/camera states and the "What does he see?" preview, Google/Telegram setup, real captured Context trace.
- Questions for the user: none new.

## Browser Skill (20 Oct 2026, SW-V3 P1.2) — [#17](https://github.com/igalgerman/JARVIS/issues/17), branch `v3-p1-browser`

- Owner rule implemented in Python Core (`browser.py` service/policy/dashboard/CLI, `skills/browser.py` tools): **full control** (open/read/click/type) only on the owner's allow-list (`<state_dir>/browser_sites.json`, **default empty**, edited on the new dashboard **Browser** page or `jarvis browser allow|remove <site>`; no model tool can change it). Other sites: **read-only after an on-screen approval** (once per host per browser session). **Purchases/payments/credential entry always confirmed** (password / one-time-code / card fields, buy/pay/checkout buttons, payment forms, Luhn card numbers), decided from the real DOM element, value never shown in the prompt. Page text returned only under `untrusted_*` with a note; the camera taint guard (`camera/wire.install_taint_guard`, now parameterized) makes every non-browser side effect within 60 s ask first. Fresh in-memory context (no personal profile/cookies), downloads refused, service workers blocked, http/https only, ChatGPT/Claude account pages blocked, every top-level navigation (links/scripts/popups) route-checked, redirects re-checked after landing. Visible status: health component `browser` + dashboard page; logs carry host only.
- Engine choice (checked 2026-10-20): **Playwright 1.64.0** (PyPI, released 2026-10-13; win_amd64 + manylinux aarch64 wheels) driving the **installed Edge** on Windows (no browser download), Playwright/apt Chromium on the Pi. browser-use 0.13.11 is an LLM agent loop on top of Playwright/CDP (own model calls, less policy control) and raw CDP lacks Playwright's routing/locators: not chosen. New `browser` extra in `pyproject.toml`/`uv.lock`; Windows build exports it; `jarvis features on browser` installs it (feature_setup). Feature `browser`: high risk, default OFF, in SAFE_OFF, wizard group "Browser", BUNDLE check, capability row (Windows/Pi implemented_unverified), guide `guides/data/browser.toml`, setup_guide section.
- Evidence: `core/tests/test_browser.py` **18 passed** (policy, dashboard API, CLI, plus two tests driving the **real headless Edge 155** against a local 127.0.0.1 test page: read-only ask/decline, refused click/type, hidden text not read, taint guard, blocked/bad URLs, allow-listed search/click without prompts, refused off-host link, password/purchase/card confirmations, redirect catch). Related suites (setup_wizard, guides, capabilities, features, camera, feature_setup, launcher_help, dashboard, core, security, startup, readiness, swv3_p01, dashboard_wph, m3_core, packaging, privacy_state) **230 passed, 1 skipped**. `jarvis setup --check-addons` browser row OK; headless dashboard smoke of #/browser (add a site) without page errors. CI installs no extras: the two real-browser tests skip there. Playwright 1.64.0 was installed into the shared `core/.venv` (`uv pip install`) to run them.
- Not done / UNVERIFIED: no real spoken conversation with the browser yet; installer build with the browser extra not run (payload size + payload_check UNVERIFIED); Pi untested (#18). Downloads into the approved sandbox (work package) not implemented: downloads are refused for now.
- Questions for the user: (1) Keep logins between sessions for allow-listed sites (persistent private profile) or always start fresh (current, safest)? (2) Should "registrations / account changes / sending messages" on allow-listed sites also always ask (today only purchases/payments/credentials do, per your answer)? (3) Want downloads saved into the Files sandbox (asks first), or keep them blocked?
## SW-V3 P0.3 startup / always-on performance (20 Oct 2026): branch `v3-p03-perf`, [#17](https://github.com/igalgerman/JARVIS/issues/17), [report](reviews/SWV3_P03_REPORT.md)

- **Fixed, all measured before and after on the owner's PC.** Behaviour is unchanged.
  - The prewarm now imports only what the selected route uses. GPT-Live and ChatGPT-plan routes no longer load the
    Agents SDK, and the live-client lazy imports are now prewarmed.
  - The Core now runs with `OPENBLAS_NUM_THREADS=1` (a value the user set still wins).
  - `runtime.json` is no longer rewritten every second: it is saved on a change, with a 60 s heartbeat.
  - The Pear probe is warmed in a worker thread. When its cached result has expired, the old result is returned at
    once and re-checked in the background.
- **Background Core results (combined route).**
  - Private commit: 358 → 93 MB.
  - Working set: 159 → 117 MB.
  - Threads: 18 → 11.
  - Idle CPU: 0.57 → 0.28 %.
  - Idle disk writes: 58.6 → 3.5 KB/min.
  - First live session's event-loop work before the network: 387–553 → 9 ms.
  - `features_list` / `list_skills` / `live_tools` with Pear closed: about 320 ms → 2–22 ms.
- **Reproduce:**
  - `core/.venv/Scripts/python.exe core/tools/bench_startup.py --scenario combined`, silent and offline, no mic.
  - Add `--log <jarves.log>` to get stage timings of real starts.
- **Tests:** the full offline suite passed, 1141 passed and 3 skipped. New tests are in `core/tests/test_swv3_p03_perf.py`.
- **UNVERIFIED:**
  - End-to-end wake / desktop start with Godot and a billed live session.
  - Dashboard load and face show/hide.
  - Sleep/resume timings.
  - Pi 5.
  - Idle disk *reads* (250–390 KB/min) are unexplained.
  - Next gate: compare `bench_startup.py --log` on the owner's next real starts against the report table.
## SW-V3 P0.4 installed-system QA, virtual mic/speaker (20 Oct 2026) — #17, branch `v3-p04-qa`

- Matrix + bug table: [reviews/SWV3_P04_BUG_TABLE.md](reviews/SWV3_P04_BUG_TABLE.md); system report regenerated:
  [reviews/PRE_USE_TEST_REPORT_SYSTEM.md](reviews/PRE_USE_TEST_REPORT_SYSTEM.md) (now incl. V01-V08).
- Suites extended, not replaced: `suite_system.py` V01-V08 (privacy strip, feature switches CLI/dashboard/Core,
  camera preview with the Core's FakeCamera = virtual camera, calendar guardrails with Google NOT connected,
  scheduler incl. catch-up across a real restart); `suite_voice.py` 32-35 (live privacy strip, camera-off and
  Google-not-connected honesty, barge-in with half_duplex=false); `suite_music.py` runs mock groups with Pear closed.
- Fixed in Core: Q01 `jarvis features` missed add-on switches (scheduler, notes, backup...); Q04 dashboard "Run now"
  waited for the 30 s poll; Q05 greeting nudge when GPT-Live sends nothing after start (live effect UNVERIFIED: not
  re-occurred in 13 later starts); Q06 AI review started during shutdown kept memory.db locked / a reservation held.
- Evidence: real Core/GPT-Live/plan/CLI/HTTP + virtual mic/speaker/camera; ~15.6 live voice min (~$0.8). NOT tested:
  physical mic/speaker/camera, installed package/clean setup (JARVES not installed here; installing changes
  PATH/autostart = owner gate), Pi, Google account, real Pear (closed).
- Open: Q07 wake false positives 4/20 at 0.15 (model limit), Q08 one spoken alert per tick (observation).

### Questions for the user (P0.4)
- May the agent install the CI installer build on this PC (or do you test it on the second PC per docs/INSTALL_TEST.md)?
  Recommendation: second PC, so this PC's dev state stays clean. Status: **USER DECISION REQUIRED**.
- Real-Pear music rows (S01-S13, V01-V10, K01) need Pear Desktop open (volume is forced to 0 by the suite). Run them
  when you leave Pear open? Status: **USER DECISION REQUIRED**.
- Q06 trade-off: a conversation that ends because JARVES is stopping no longer gets the automatic AI review (rules
  still run; the dashboard Review button can run it). Recommendation: keep. Status: **ANSWERED by agent, reversible**.
## SW-V3 P0.2c Multi-language dashboard, English default (20 Oct 2026) — [report](reviews/SWV3_P02C_REPORT.md) · #17 · branch `v3-p02c-multilang`

- Built on origin/main plus `origin/v3-p02-dashboard` (merged as instructed by the coordinator).
- **English is the default** (`<html lang="en" dir="ltr">`). An explicit saved choice (`jarves-lang`, written only by the new top-bar **language picker** `<select>`) wins. The browser language is never used.
- **Locales:** en / he / ar / ru. Each is one catalog in `dashboard_static/locales/<code>.js` plus its script tag (the registry). Coverage: he 100 % (test-enforced), ar 26 %, ru 26 % (English fallback).
- **Server:** `jarves/locales.py` is the generic `<field>_<locale>` registry, with `hebrew.py` as its first translator.
- **RTL fixes:**
  - logical CSS only, enforced by a test
  - isolated English fallbacks: "?first" fixed
  - `JL.textDir` for server texts: a Hebrew sentence that starts with a Latin name no longer runs LTR
  - the log table scrolls inside its card (no page overflow)
  - module names are not translated
  - mirrored arrows
  - Arabic font and digits
- **Hebrew added** for the P1 channel feature texts, so `test_hebrew_texts` passes on the merged tree.
- **Evidence:**
  - `test_dashboard_locales.py`: 17 passed. Focused dashboard / hebrew / guides / usage / setup / identity / backup / phone files: 158 passed.
  - An earlier full suite: 1196 passed, 1 env failure (`test_launcher_help`: no `.venv` in the worktree).
  - Playwright: 38 captures, 0 problems, in `docs/reviews/swv3_p02c/` (`core/tools/v3_locale_screens.py --channel msedge`).
  - These are automatic captures, not an owner hands-on review.
- **Questions for the user:**
  1. Which languages should get full catalogs? **USER DECISION REQUIRED**
  2. Should the dashboard language also drive the voice / CLI language (currently separate)? **USER DECISION REQUIRED**
## SW-V3 Profiles: several users + test profile (20 Oct 2026) — [report](reviews/SWV3_PROFILES_REPORT.md) · [#17](https://github.com/igalgerman/JARVIS/issues/17) · branch `v3-profiles`

- Profile = isolated state dir (memory, conversations/costs, persona files, settings, switches, name/wake/voice, secrets.env, Google/ChatGPT tokens, privacy, channels). `default` = today's `JARVES_HOME` / `%LOCALAPPDATA%\jarves`; others `<base>/profiles/<name>`. `jarvis --profile <name> …` / `JARVES_PROFILE` / `jarvis profile list|show|create|use|delete|export`; own lock, link port (allocated free, checked at run), per-profile dashboard cookie, autostart entry and tray label.
- `test` profile: AI offline, voice off, music none, sensitive switches off, $10 budget, red TEST PROFILE banner (dashboard + Godot face). Non-default profiles drop inherited secret env vars; `--from` never copies keys (only `--copy-secrets`) or sign-ins; delete needs the typed name. Voice `profile_switch` only after on-screen approval; no speaker recognition (v1).
- Registered feature `profiles` (guide, capability row, wizard group, bundle check, Hebrew texts in `profiles.HE`, auto-merged into `hebrew.TEXT` when P0.2b lands). Evidence: `tests/test_profiles.py` 33 passed (incl. two real Core processes side by side) + 15 related files passed one by one + Godot face check PASS. Full suite NOT run (RAM request), final-commit CI not claimed; separation is app-level, not an OS boundary.
- **Questions for the user:** (1) which profiles/names; test profile as agents' default for the FULL_TEST_PLAN campaign? (recommended yes) (2) allow two voice profiles running at once? (recommended no) (3) dashboard "switch" that restarts the Core, or is "open its dashboard" enough? (4) app-level separation OK, or separate Windows accounts for family? (5) speaker recognition later? — all **USER DECISION REQUIRED**.

## SW-V3 Self-check: self-diagnostics + improvement suggestions (20 Oct 2026) — [report](reviews/SWV3_SELFCHECK_REPORT.md) · #17 · branch `v3-selfdiag`

- New `core/src/jarves/selfcheck.py` + dashboard **Self-check** page (`selfcheck.js`). On demand (voice `self_check_run` "תבדוק את עצמך", `jarvis diagnose --now`, dashboard Check now), weekly scheduler job (Sun 10:00, editable: Jobs page / `jarvis diagnose --schedule`), after an unclean stop and after an error burst (min interval, alerts respect quiet hours). Health + 7-day log scan (crashes, ERROR/WARN clusters, repeated failures, latency p50/p90/p95, budget burn, wake false/near-misses, tool errors, false action claims, Pear/Google/camera, update, disk/memory) + config sanity + feature gaps/setup/bundle/unused + existing diagnose issues. Ranked findings stored in store.db with history, trend and accept/dismiss/snooze; ticket draft (never posted); spoken Hebrew/English summary.
- Setting changes -> autonomy proposals (new medium-risk appliers `config_setting`, `feature_on`; never auto-applied); AI pass = existing `diagnose.llm_review` only, capped, budget-aware, cost shown; no transcript text in reports.
- Evidence: `tests/test_selfcheck.py` 19 passed + 11 related focused files passed (one at a time, low RAM); full suite and final-commit CI NOT run/claimed. Live use on the owner's logs UNVERIFIED.
- Questions for the user: (1) weekly default Sunday 10:00 — OK? **USER DECISION REQUIRED** (recommend yes, outside quiet hours). (2) `self_check.on_start`: crash only (default) or every start? **USER DECISION REQUIRED** (recommend crash: every start adds noise). (3) AI pass in self-check on by default (max 2 reviews/run)? **USER DECISION REQUIRED** (recommend yes; same path/caps as per-conversation review). (4) Merge note: Hebrew catalog (P0.2b `hebrew.py`) is not on main; add `self_check` strings when it lands — **DEFERRED** to that merge.
## V3 P1 channels: Telegram, morning briefing, WhatsApp handoff, Home Assistant (20 Oct 2026) — [#17](https://github.com/igalgerman/JARVIS/issues/17)

- Branch `v3-p1-channels` (from `swv2-integrate`). Four add-ons, each with a feature switch + capability row + setup-wizard group + bundle check + Hebrew/English guide + tests (`core/tests/test_channels.py`, 27 tests, real HTTP against local fake servers in `core/tests/channels_fakes.py`; full suite 1160 passed, 3 skipped).
- **Telegram** (`telegram_channel.py`, `jarvis telegram start|status|test|logout`, dashboard `POST /api/telegram/token`): owner creates the bot; token only in `secrets.env` (`TELEGRAM_BOT_TOKEN`), never logged/printed. Wizard pairs only private chats the owner confirms. Send = `telegram_draft` then `telegram_send` (destructive, impact None = confirmation from every origin, exact text read back). Off by default, in SAFE_OFF.
- **Morning briefing** (`briefing.py`, tool `briefing_get`, on by default): calendar today + important unread Gmail (count/senders only) via the existing Calendar/Gmail tools, weather from Open-Meteo (settings `briefing.city/lat/lon`, default Tel Aviv), reminders due today + notes tagged list/רשימה. Each part degrades alone (Google missing -> says `jarvis google start`; no network -> weather says so). Mail/calendar text is untrusted (no side effects in that turn).
- **WhatsApp handoff** (`whatsapp.py`, on by default, risk medium): `whatsapp_draft` -> read-back approval -> `whatsapp_open` opens `https://wa.me/<number>?text=...`; the owner presses send. No network/automation code (test asserts it). Numbers the owner says are kept in `whatsapp/contacts.json`.
- **Home Assistant** (`homeassistant.py`, `jarvis home start|status|test|logout`, last priority): URL + long-lived token (`HOME_ASSISTANT_TOKEN` in `secrets.env`), entity allow-list chosen in the wizard, fixed table of simple services per domain; risky entities (lock, cover, alarm, script, valve, siren, water_heater or owner-flagged) only via `home_control_risky` which always asks. Off by default, in SAFE_OFF.
- **UNVERIFIED (never against the real services):** a real BotFather bot, real Telegram delivery, real Open-Meteo, a real Home Assistant, the real WhatsApp app opening the link, a connected Google account for the briefing. Windows/Pi status for all four is `implemented_unverified`.
- **Instagram** (owner asked for `jarvis instagram start`): NOT built in this task (not in the brief); needs the API-limits research first.
- **Questions for the user:** (1) Telegram: should extra allowed chats (family) be supported beyond `--add-chat`? (2) Briefing: schedule it automatically every workday (Sun-Thu) at a time you choose? (3) Home Assistant: is the default risky list (lock, cover, alarm, script, valve, siren, water heater) right?

## SW-V3 P0.2 Dashboard redesign (20 Oct 2026) — [report](reviews/SWV3_P02_REPORT.md) · #17 · branch `v3-p02-dashboard`

- Hebrew-first RTL dashboard (default) with one global English switch; 7 grouped nav sections keep every page (test-enforced); phone drawer; global search (pages, features, guides, conversations/log search); app-bar chips for Core, host device, Pi readiness, mic/camera/audio-to-cloud and privacy; usage strip kept on every page plus today's estimated cost. New `i18n.js`, `shell.js`, `shell.css`; `/api/runtime` gains `host`.
- Evidence: 123 focused dashboard/guides/setup/usage tests passed (incl. new `test_dashboard_p02.py`); Playwright interactive review (1440 px + 390 px, he/en) and 16 headless Edge captures in `docs/reviews/swv3_p02/` (`tools/v3_dashboard_screens.py`). Not merged into `swv2-integrate`; final-commit CI not claimed.
- Gaps: server-provided detail texts (feature about/evidence, issue titles, settings labels, usage meter notes) remain English in Hebrew mode; owning layer = Python modules (`_he` fields). No owner hands-on review yet.
- Questions for the user: group names/order — **USER DECISION REQUIRED** (provisional in report); translate server texts as P0.2 follow-up before P0.5 — **USER DECISION REQUIRED** (recommended yes).
## Complete retained-log quality review (19 Oct 2026) — report `ALL_RETAINED_LOGS_19OCT2026` (private, not in the public repo)

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
- The Opus 5.5 tuning agent's report and sources are in `LIVE_CONVERSATION_TUNING` (private, not in the public repo): backend `reasoning.effort = none`, `service_tier = priority` (about $0.0005 per lookup), time answered locally, short "checking" notes, new config fields `ai.voice_reasoning_effort`, `ai.voice_service_tier`, `ai.voice_max_output_tokens`, `voice.progress_after_s`, `voice.echo_tail_s`.

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
## Status 20 Oct 2026
- SW-V2 merged to main via PR #29 (9e2149d). V3 is delivered as one PR per piece (P0.2 dashboard #31, P1 basics #32, research #33); more follow as agents finish (perf, QA, browser, channels, Hebrew server texts).
## Instagram channel (#17, branch v3-p1-instagram)
Built per docs/plans/INSTAGRAM_CHANNEL.md: `core/src/jarves/instagram_channel.py`, `jarvis instagram start|status|test|logout` (+ `serve` webhook receiver on 127.0.0.1, `refresh` long-lived token), feature `instagram` (off by default, SAFE_OFF, group Channels, bundle check, guide `instagram.toml`, capabilities row via features.register). Token/app secret/verify token only in secrets.env. Inbox (SQLite, dedup), DMs read aloud as untrusted data, LLM only drafts, `instagram_send` is destructive with impact None (exact text confirmed from every origin, one draft per send), 24h window checked at draft and send, audit log `instagram/audit.jsonl`. Tests: `tests/test_instagram.py` against a local fake Graph API (real HTTP) and a real webhook server.
Unverified: never run against real Instagram/Meta (needs owner's Meta app + Business/Creator account); endpoint paths/fields (graph.instagram.com/v21.0 /me, /me/conversations, /me/messages, /refresh_access_token) follow Meta docs from memory and must be checked at first live run; no tunnel set up; declines at the prompt are not audited (the registry never calls the tool).
Questions for the user: (1) Is the Instagram account Business/Creator? (2) Confirmation surface: voice+screen as built, or add Telegram buttons? (3) OK to run `jarvis instagram serve` behind a Cloudflare Tunnel, or poll only?
## Approvals service + Helper skill (#17, branch v3-approvals, 20 Oct 2026)
Report: [docs/reviews/SWV3_APPROVALS_REPORT.md](reviews/SWV3_APPROVALS_REPORT.md). One `ApprovalService`
(`core/src/jarves/approvals.py`) behind `Core.ask_confirm` / `registry.confirm`: feature_set requests, tool
confirmations, camera, Telegram/Instagram sends, Helper. Shown at once on an always-on-top Tk popup
(`approval_popup.py`, own process), tray balloon, face dialog, dashboard `#/approvals`, voice/typed yes-no, CLI
`jarvis approvals`. Default deny on timeout/shutdown/no channel; no answers from inside a tool; dedupe, 30 s
cooldown after a deny, 6/min rate limit; audit `approvals/audit.jsonl`. Helper skill (`skills/helper.py`,
feature `helper` off, SAFE_OFF): `shell_propose`, `install_propose` (winget/pip/npm, exact package/source/version),
blocklist with typed 4-digit code, elevation and JARVES-data refused, timeout + process-tree kill, untrusted
redacted output summary, audit. Owner decisions applied: Helper always asks (read-only allow-list not offered),
popup window primary (no toast), Instagram send via approvals + voice.
Unverified: the real popup on the owner's desktop (Hebrew/RTL, position, topmost over full-screen, pythonw from the
background runtime), tray balloon, Godot `confirm.cancel` (no Godot binary here), voice "כן" in a live session
(echo), real winget/pip/npm installs and UAC, Raspberry Pi. Full suite not run locally (low RAM): focused files only.
Questions for the user: (1) Please trigger one approval (e.g. ask JARVES to turn on Contacts) and confirm the popup
looks right and stays on top. (2) Timeout 60 s (Helper 120 s): OK? (3) Should the dashboard Approvals page also be
reachable from the phone (dashboard_lan) for approvals, or PC-only?

## Questions for the user (from research, branch v3-research, docs/plans/)
- Signing: SignPath OSS needs a public repo + OSI license; Azure Artifact Signing ~$9.99/mo, region-limited (verify price). Stay unsigned? Open-source the installer?
- Instagram: is the account Business/Creator and linked to a Facebook Page? Confirm sends by voice, dashboard or Telegram?
- Virtual computer: which 2-3 tasks would justify it; local (WSL2/Docker) or cloud budget?

## SW-V4 test campaign, pass B (feature by feature) - 20 Oct 2026, branch `test/v4-campaign-a`, draft PR #21 (#17)

- Offline TEST profiles, fakes only (WhatsApp fake bridge, fake Claude/Codex, channel fakes), no browser, no account, no paid call. Reports: [features](reviews/SWV4_CAMPAIGN_FEATURES.md) (1707 PASS, 0 FAIL, 2 BLOCKED, 28 N/A; 59 switches x CLI/dashboard/Core tool + recall, budget, health, WhatsApp, agents, privacy bundle with planted secrets (none leaked), helper isolation, approvals, update refusal paths, real `jarves run`), [bug table](reviews/SWV4_CAMPAIGN_BUG_TABLE.md).
- Fixed with regression tests: V4-HE-1 (P1, `jarvis health canary` unusable), V4-WA-1 (P2, WhatsApp switch not live), V4-HE-2, V4-DG-1, V4-CLI-1 (P3). Open: V4-SC-1 (P2 self-check outlives Core stop), V4-RP-1/2 (P2, owner decision), 7 x P3.
- BLOCKED: `assisted_dialing` (package H not in this tree); the V3 `browser` stage (launches Edge); update check against a signed local https release server (none exists).
- Questions for the user: (1) Where should update releases come from? `updates.REPO` is the deprecated private `igalgerman/JARVIS` and `jarvis-releases` is the old name (V4-RP-1). (2) Where should private diagnostic ticket drafts be filed now that the old private repo is deprecated (V4-RP-2)? Nothing was filed.
## Idle writes to owner budget + hook Hebrew fix (21 Oct 2026) - #17, branch `perf/idle-writes-owner-budget`

- Idle writes: 29-53 KB/min -> 4.8-8.7 KB/min (owner budget 10 unchanged). Cause: the diag monitor flushed `diag_metrics/diag_codes/diag_events` to store.db every 60 s (3 commits, ~30-45 KB of WAL pages). Now `Monitor.flush_due()` batches the in-memory buffer into ONE transaction every 5 min (and at shutdown via `flush(all_=True)`; crash loses at most 5 min of samples). `Store`: WAL + `synchronous=NORMAL`, `wal_autocheckpoint=4000`. `health.json` heartbeat window 50 -> 70 s (still < 120 s "off").
- Hook fix: `orchestrator/hook.py` printed the Stop reason with `ensure_ascii=False`; a Hebrew message hit the Windows console codec (UnicodeEncodeError, swallowed -> no output after the messages were already claimed). Now ASCII-escaped JSON.
- Tests: `test_swv4_idle_writes.py` (diag batching over 10 simulated minutes, WAL/NORMAL pragma, real idle Core < 12 KB/min).
- Open: perf gate `spawn_to_ready_s` 5.11 > 5 on this busy PC (unrelated, noise).

## V4 voice campaign C2 (21 Oct 2026) - #17, branch `test/v4-campaign-c2`

- Credits topped up; re-ran every blocked live scenario: 89 PASS, 2 FAIL (22/23 wake near-misses, P3 unchanged), 1 BLOCKED (62, needs OD-05 admin key). Spend about $2.8. Report: `docs/reviews/SWV4_CAMPAIGN_VOICE.md`, bugs: `SWV4_CAMPAIGN_BUG_TABLE_VOICE.md`.
- Product fixes: V4-VC-12 `BackendTrace.finish` duplicate `model` kwarg broke the ChatGPT-plan delegation route; V4-VC-13 own-health questions now delegated; V4-VC-14 WhatsApp inbox English phrasing.
- Harness: the WhatsApp suites restart the live session after switching the feature on; scenario 56 copies (never refreshes) the owner's ChatGPT token.
- Open: 79/80/84/101 occasionally miss on synthetic-ASR clips (pass on re-run). No questions for the user.
## `jarvis connect`: unset only + read-only checks (#17, branch v4/connect-unset-only)
`jarvis connect` now walks only integrations that are not connected and not skipped; a skip (gate `S`, `s` inside a step) is remembered (settings `connect.skipped` + `connect.state`) and never offered again. `connect --all` lists connected / skipped / not set; `connect <name>` always runs one (and un-skips); `--reset-skipped` clears skips; nothing left prints one friendly line (EN/HE), exit 0. Registry: `connect.register(Integration(...))` (status/step/check) so other modules (phone access, WhatsApp bridge, ...) add theirs; built in: openai, chatgpt, google, telegram, pear, wake, instagram, home, openai_admin. `connect_checks.py`: `--check` and the first part of a plain run test connected ones read-only (working / broken + fix command / unreachable / not tested), concurrent daemon threads, 8 s timeout, 60 s cache; plain run offers to reconnect broken ones first (never with `--yes`). Dashboard Connections: three-state pill, result pill, Connect/Skip/Un-skip/Test again (`/api/connect/{run,skip,check}`).
Notes: `core/tools/catalog_add.py` does not exist in this tree, so the strings are inline `tx(he, en)` in home.js. WhatsApp here is a draft-only wa.me link (no bridge), so it has no entry yet. Wake check only loads the detector; Google check uses the existing read-only self_check. Real accounts were never contacted (tests use MockTransport fakes).

## Optional read-only OpenAI admin key (21 Oct 2026) - branch `v4/admin-key-option` (OD-05)

- The Core already read `OPENAI_ADMIN_KEY` (usage.py Costs API, `jarvis key --admin`). Added: `jarvis connect openai-admin` (hidden input, skippable with `s`, `--yes` never asks), a Usage-page box (password field, Save / Remove / Test now, never echoes the key), one shared `usage.save_admin_key / remove_admin_key / admin_key_saved`, EN+HE guide steps in `api_bill.toml`, feature row "Usage checks (admin key)" (`implemented_unverified`: fake costs endpoint only, no real key tested). Docs: `docs/OPENAI_ADMIN_KEY.md` (create, save, hidden-input pattern for a local helper script; the owner's own script is untouched).
- Tests: `core/tests/test_usage_admin_key.py` (local fake costs endpoint, no network); log_lint 0 violations; Hebrew via `tools/catalog_add.py`.
- Open: voice scenario 62 stays BLOCKED until the owner saves a real admin key. Question for the user: create the key (docs/OPENAI_ADMIN_KEY.md) when convenient; OK to leave the switch default-on (shows "off by choice" until a key exists)?

## V5 mobile access (branch v5/mobile-access, draft PR) - core done, review and real phone open

Plan and security model: `docs/JARVES_SW_V5_WORK_PACKAGE.md`. Built: pairing (QR + one-time code), hashed per-device tokens, revoke, scopes, rate limits, audit without content, phone API (chat, voice PTT over TLS, approvals list/answer, status), PWA EN+HE, dashboard Phone page, `jarvis dashboard --mobile [--tls-selfsigned]`, feature `mobile_access` (default OFF). Tests: `test_mobile_access.py`, `test_mobile_pwa.py`; log lint 0 violations.
Open: independent security review (V5-F), shared-conversation continuity (V5-G), start phone server with the Core (V5-I), real Android phone acceptance (V5-H, owner). Questions for the user: see section 7 of the work package.

V5 review pass (21 Oct 2026): `docs/reviews/SWV5_SECURITY_REVIEW.md` - 4 findings (1 medium, 3 low), all fixed with regression tests (`test_sr*`): live switch gate, audit flood throttle, 0600 file creation, one chat turn per phone. Phone server now starts with the Core when `mobile_access` is ON (V5-I core); phone turns are logged into the shared typed history (V5-G core). Merged with V4 main (21 Oct). Client message ids closed: the phone sends an `id` per chat message and resends it on reconnect; the PC answers once and replays the stored answer (`test_chat_message_ids_*`). Manual Android steps: `docs/reviews/SWV5_ACCEPTANCE.md`. Still open: real phone run (V5-H, owner).

## Terminal text chat (`jarvis chat` / `jarvis text`) - branch v5/text-mode
- Core side `chat_service.py` (link role cli: chat.hello/turn/cancel/command/approval_answer), REPL `chat_cli.py` (stdlib only). Silent hours (quiet hours or --silent) = no speech, chime or face change, enforced by the Core; `/voice on` only outside them. Approvals: terminal channel beside popup/dashboard, first answer wins; no voice channel in chat turns. Streaming via `Runner.run_streamed` (API-key route); ChatGPT-plan route shows the finished text.
- Tests: `core/tests/test_chat.py` (30, real Core + fake model). UNVERIFIED: hands-on in a real Windows console (Ctrl+C at the idle prompt, RTL display). Merge held until v4.0.0 is tagged (target 4.0.1).

---

## Web chat: `jarvis web` + dashboard Chat page (21 Oct 2026) - #17, branch `v5/web-chat` (based on `v5/text-mode`, PR #42)

- `chat_engine.py` (WebChat: event log + long-poll, slash commands, streaming, inline approvals, first answer wins with popup/terminal/phone), dashboard Chat page (`chat.js`, `chatview.js`), `jarvis web` (works with JARVES stopped via `web_cli.py` text core; with a running Core it opens the Chat page), paired phones use the same engine on `/api/m/chat/*`.
- Phone/remote use: per-device allowed pages (default Home, Chat, Approvals, Health & speed); settings, secrets, files and devices are computer-only. Forbidden pages are refused SERVER-side with fixed codes; message ids make a resend idempotent (the stored answer, never a second run). `jarvis web --device-pages`, `--set-pages DEVICE chat,home`.
- Shared with `jarvis chat` (#42): same Brain/Skills/approvals/history, `TOOL_SINK`, quiet hours, `text_session.build`. Honest limit: `chat_engine.py` (HTTP) and `chat_service.py` (link WebSocket) still each carry their own small helpers (slash command table, recent sessions); merging them into one module is a follow-up.
- Fixed on the way: approval events overwrote the event cursor id; `/undo` passed the wrong file argument; two `/new` in one second merged into one stored conversation; a spoken reply finished after the reply event.
- Verified (targeted, fakes only, no browser): test_web_chat 32, test_chat, parity, guides, log lint, hebrew, features, wizard, locales, mobile/dashboard phone files all green. NOT verified: a real browser and a real phone.
## V5 continuity and proactive signals (branch v5/continuity, draft PR) - core done, real phone open

Built: `core/src/jarves/continuity.py` (SQLite `continuity.db`): command ledger (chat, approval answers, goal actions, signal snooze/stop; same client id runs once, also after a PC restart; a command the phone queued over 120 s is refused `STALE`), one shared conversation journal (phone + desktop turns, `/api/m/sync?since=`), presence and one-device routing (timers via `Operations._ring_once`, notices acknowledged per device, lost responses re-sent only to the same device), event-driven signal gate (off by default, quiet hours 22:00-07:00, snooze/stop, hourly/daily limits, cost cap 0, dedupe; no LLM calls). PWA: offline banner EN+HE, outbox with "not sent" and one auto-retry window (2 min), notes, goals list, snooze/stop; new device scope `tasks` (off until granted; phone cannot delete/edit goals). Feature `proactive_signals` (default OFF), guide `proactive_signals.toml` (+ mobile_access problems), CLI `jarvis proactive signals ...`, dashboard `/api/continuity`. Tests: `core/tests/test_v5_continuity.py` (fake clock, fake devices, restart/resend, no network, no browser).
Open: real phone/Wi-Fi/sleep run (owner); no calendar scheduler exists yet (`on_calendar_event` is the hook, tested with fakes); GPT-Live voice turns are not journaled yet; the desktop only sees notices via dashboard/CLI (never spoken). `test_p_perf::test_backend_prompt_does_not_grow` fails (58998 vs 57310 chars) already after merging PR #36 (v5/pillars-followups), not from this branch: slim the goal/pref/proactive/dev tool texts there.

## Chat UI polish: web Chat page + `jarvis chat` (23 Oct 2026)

- Owner report (screenshot): the web chat looked low quality and showed a stray "null" after "Not silent". Cause: `status.replaceChildren(pill, null)` renders the text "null"; fixed in `chatview.js` (spreads an array instead).
- `chatview.js/.css` (shared by the dashboard Chat page and the phone page): chat fills the window height (dvh), one toolbar row (status + options), welcome text in the empty state, bubbles with tails and a streaming caret, animated "Writing" indicator, composer as one box with an auto-growing field and a Send button that is off while empty, smart scroll (follows only when at the bottom) plus a jump-to-latest button, command chips scroll sideways. Class names the phone e2e tests use (`.jc-in .jc-send .jc-msg .jc-log`) are unchanged. Hebrew for the 3 new strings via `tools/catalog_add.py`.
- `chat_cli.py`: colour (assistant prefix, tool lines, failures) and a separator after each turn only on a real terminal and never with `NO_COLOR`; version shown in the header. All pinned strings unchanged.
- Verified: test_chat + test_web_chat (62), prepush green, headless-Chromium screenshots of the web view (filled and empty). NOT verified: the phone e2e (needs a browser fixture), a real Windows console, RTL look of the new bubbles in a real browser.
- Added (same day, owner approved): safe markdown in answers (headings, lists, quotes, bold/italic/inline code, fenced code with a Copy button, http(s) links only; DOM calls only, HTML in an answer stays text) and a keyboard slash-command pop-up fed by the server's command list. Test: `core/tests/test_chat_markdown_browser.py` (real headless browser, runs in the CI browser job via `BROWSER_FILES`).
- Open: message timestamps (low value, only on request).

## next-flow.yml parse failure - 23 Oct 2026

- Root cause: `workflow_run.workflows` listed the workflow's own name (`jarves-next-flow`), for the old automerge job. GitHub rejects the file (every push showed a failed run named by file path, zero jobs); YAML and actionlint 1.7.12 accept it, so only GitHub's server-side check catches it. Confirmed by a probe push: with the self-reference removed no failing next-flow run appeared on that branch.
- Fix (only this workflow): `workflow_run` now lists only `jarves-checks`; `automerge-into-next` runs inside the PR run after `ci-ok` and merges that one PR (open, not draft, no conflict, head unchanged). Still off unless `ENABLE_NEXT_FLOW`. `docs/NEXT_FLOW.md` line updated.
- NOT verified: a real automerge on a PR into `next` (flow is off), the fast-forward job (unchanged, untested).

## Viewer UI/UX simplification (23 Oct 2026) - branch `claude/keen-euler-ggmq14`

- Owner feedback (screenshot): left/right panels too small, too much on screen. Changed `godot/face/assistant_ui.gd` and `godot/main.gd`: one shared theme (`AssistantUI.make_ui_theme`: 20 px text, dark rounded cards, roomy buttons); right panel now shows only title, status dot, big activity line and the conversation text; Options holds details, mic mute, dashboard and a new "Model inspector" switch. The left inspector (renamed from "R5b review", no longer opened by Options) shows only the part layers and view buttons; finish, LED ring and exploded view moved under "More"; the controls help is one line.
- Signal `options_toggled` became `inspector_toggled` (only `main.gd` used it).
- NOT verified: no Godot or pytest in this cloud container, so no screenshot or `test_face_protocol_godot` run and `prepush.py` could not pass locally. CI/the owner's PC must confirm layout (long Hebrew text, 1280x800 window) and re-export `godot/previews/`.
- Follow-up (owner: "like Samsung/Apple, less text, options as a tree"): `assistant_ui.gd` Options is now a settings tree: root list (Status, Microphone, Dashboard, Help, Model inspector) and one small page per row with a Back row; the main view hides while Settings is open; the offline line is just "Not connected" (detail is under Status). Same caveat: not run in Godot here.
- Mini dashboard (owner: "extend the tree, see the dashboard"): Options root is now Status, Microphone, Manage, Help, Model inspector, Open full dashboard. Manage has 6 groups (Talk & memory, Everyday, Assistant, Connections, Health & costs, System) with 30 leaves; each leaf sends the existing `ui.open_dashboard {page}` so the Core opens that dashboard page in the browser (no Core or protocol change). Status now also shows today's AI requests and voice minutes. Not run in Godot here; in-panel editing (e.g. pause alerts, feature switches) would need `runtime.set`/settings messages allowed for the face role and is NOT done.

Conversation controls / local dashboard findings (#77/#80): implementation, verification and the complete decision queue are in [the dedicated handoff](handoff/2026-10-23-conversation-controls.md). Real microphone/camera/account acceptance remains deferred; no history deleted.

- CodeQL PR5 (tools/tests, PR #133, merged 196bdc3): on main the re-scan marks #24 and #29-36 fixed; #14/#18 (campaign evidence, redact not modelled), #25 (secret names), #16/#17/#22/#23/#26-28 (FAKE planted fixtures) remain open and need an owner precise dismissal with justification.
