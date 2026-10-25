# V5 inventory and plan (24 Oct 2026)

Planning document, read-only research on `jarves/main` at `04ff433` (Jarvis-igerman-ai/jarves). It does not authorize code: [NOW](../NOW.md) is still the V4.5 brief and lists V5 phone acceptance as out of scope. The owner's non-stop pipeline is 4.5, then 5, then 6. This plan is what V5 needs after 4.5. Sources: [ROADMAP](../ROADMAP.md) (V5 section), [V5 package](../JARVES_SW_V5_WORK_PACKAGE.md), [V5_PILLARS](V5_PILLARS.md), [SWV5_ACCEPTANCE](../reviews/SWV5_ACCEPTANCE.md), [SWV5_SECURITY_REVIEW](../reviews/SWV5_SECURITY_REVIEW.md), merged PRs #25 #29 #33 #35 #36 #42 #49 #51 #52 #55 #56 #70 #88 #94 #96 and the code.

## 1. What is merged on main for V5

Status words come from `capabilities.py`. **No V5 feature is `verified_on_device`.** All guides below exist in English and Hebrew (`test_guides` enforces this). CI is not a device.

| # | Feature (feature id) | Code | Tests (count) | Guide EN+HE | Honest status |
|---|---|---|---|---|---|
| 1 | Phone access PWA: pairing code + QR, per-device tokens, revoke, scopes, rate limits, audit, optional TLS (`mobile_access`, off by default) | `mobile.py`, `qrmini.py`, `phone_cli.py`, `dashboard_static/mobile/` | `test_mobile_access` (35), `test_mobile_pwa` (13), `test_dashboard_phone` (7) | yes (`mobile_access`) | implemented_unverified |
| 2 | Emulated-phone end-to-end: headless Pixel-size browser, all 18 sheet rows EMU-PASS | `tests/test_mobile_phone_e2e.py`, CI browser job (#96) | 14 | n/a (test) | automated evidence only |
| 3 | Tailscale detection, tailnet-only bind, phone approvals (default off, typed-code approvals PC only) | `tailnet.py`, `mobile.py` | `test_tailnet` (24) | yes (`mobile_access`, PHONE_ACCESS_TAILSCALE EN/HE) | implemented_unverified |
| 4 | One shared chat: `jarvis web`, dashboard Chat page, `jarvis chat`; phone chat uses the same engine | `chat_engine.py`, `chat_service.py`, `web_cli.py`, `chat_cli.py` | `test_web_chat` (32), `test_chat` (26) | yes (`web_chat`, `text_chat`) | implemented_unverified |
| 5 | Continuity: command ledger (a resend runs once), presence, notices delivered once to the active device; proactive signals (`proactive_signals`, off) | `continuity.py` | `test_v5_continuity` (29) | yes (`proactive_signals`) | implemented_unverified |
| 6 | Goals + one calm "goal done" line (`goal_done_line`) | `goals.py` | `test_v5_pillars` (18, shared), `test_v5_followups` | yes (`goals`) | implemented_unverified |
| 7 | Goal executor: plans of existing Skills, approve once, pause/cancel/retry/skip, step and budget caps (`goal_plans`) | `goal_plans.py` | `test_v5_goal_plans` (11) | yes (`goal_plans`) | implemented_unverified |
| 8 | Adaptive Memory: stated preferences, confirm by explicit yes, stale/conflict, in the instructions (`prefs_in_prompt`) | `preferences.py` | `test_v5_pillars`, `test_v5_followups` | yes (`adaptive_memory`) | implemented_unverified |
| 9 | Proactive offers, on request by default (`proactive`) | `proactive.py` | `test_v5_followups` (27, shared) | yes (`proactive`) | implemented_unverified |
| 10 | Development Manager: local drafts (`dev_manager`) and read-only status of agents/PRs/CI (`dev_status`) | `devmanager.py`, `devstatus.py`, `orchestrator/` | `test_v5_followups`, `test_v5_devstatus` (4) | yes (`dev_manager`, `dev_status`) | implemented_unverified |
| 11 | Software project manager / factory: small tasks, parallel coding agents in worktrees (`project_manager`, off) | `projectmgr/` | `test_projectmgr` (58), `test_projectmgr_git` (4) | yes (`project_manager`) | implemented_unverified, no real agent run |
| 12 | Supporting, shipped in 4.x: setup and tour wizard (#70 #175), release sign-off wizard (#60), selftest/speedtest (#35) | `wizard.py`, `setup_wizard.py`, `release_signoff.py`, `selftest.py`, `speedtest.py` | `test_wizard` (24), `test_setup_wizard` (17), `test_release_signoff` (14) | yes | implemented_unverified |

Counts: 11 V5 features merged (plus 3 supporting ones), about 300 targeted tests, 15 bilingual guides, 0 verified on a device.

## 2. Definition of done (ROADMAP, unchanged)

1. On a **real Android phone**: pair/revoke, talk to the PC Core, resume the same conversation on Windows, read status, run a permitted Skill with the right approval. No second memory, no duplicate actions.
2. Live voice and reconnection on the phone: host down, network cut, app suspend/resume, permission denied, a clear cloud-audio indicator. Plus a real-vs-fake evidence table.
3. An approved multi-step goal: saved progress, cancel, bounded budget, a checked final report, and a failure-and-retry path.
4. A memory correction the user confirms, showing its source, edit/delete, and its effect **from both clients**.
5. Opt-in proactive suggestions with quiet hours, deduplication, stop/snooze and no unapproved action.
6. Development Manager reports from real Claude/Codex/GitHub data. It says when a live push/attach route is not supported. It never merges by itself.
7. Security/privacy review, automated and owner-present Windows/Android end-to-end checks, performance and cost recorded, a release artifact and a rollback path.
8. Project rule (CLAUDE.md): from 5.0 on, every release fixes all known bugs first (no open P0-P2), and every feature has a guide, a CLI parity entry and a capability row.

## 3. Gap list

| # | Gap | DoD | Tag | Safe default if the owner is away |
|---|---|---|---|---|
| G1 | Status docs are stale: the ROADMAP V5 block says "open PRs", V5 package rows V5-G and V5-J..M say "open/not started", DOC_MAP says #51 is open | - | AGENT-DOABLE | - |
| G2 | Repo move: `devstatus.DEFAULT_REPO`, `issue_registry.REPO`, `updates.REPO` and issue links still name `Jarvis-igerman-ai/jarves` | 6 | AGENT-DOABLE (updater part: `no-automerge`) | rely on GitHub redirects until merged |
| G3 | The phone has no Learned-preferences page (list/confirm/edit/forget), so DoD 4 "from both clients" fails | 4 | AGENT-DOABLE | - |
| G4 | The phone shows goals but not goal plans (progress, pause/cancel/retry) | 1, 3 | AGENT-DOABLE | approving a plan stays PC-only |
| G5 | The phone has no clear microphone/cloud-audio indicator, and no stop/snooze for proactive notices | 2, 5 | AGENT-DOABLE | - |
| G6 | Resuming a specific phone conversation from Windows (V5-G remainder) is not proven | 1 | AGENT-DOABLE | - |
| G7 | No V5 cross-pillar scenario test: phone and desktop together, memory correction on both, goal fail/retry with fake Skills, proactive dedupe, quiet hours | 3-5, 7 | AGENT-DOABLE | - |
| G8 | Capability evidence is too general: every pillar uses the shared `calm.TARGETS`, which points at `test_v5_followups.py` only | 7 | AGENT-DOABLE | - |
| G9 | The security review covers phone access only, not `goal_plans`, `continuity`, `devstatus`, `projectmgr` or prefs in the instructions | 7 | AGENT-DOABLE (Opus) | - |
| G10 | Dev status has never run against real GitHub data (a read-only GET costs nothing) | 6 | AGENT-DOABLE | read-only, public data only |
| G11 | No 5.0 bug table yet. Open: #163 (P1 showstopper flake), #149, #77, #71, plus selftest/speedtest findings | 8 | AGENT-DOABLE | - |
| G12 | 5.0.0 release mechanics: version (still 4.0.1), CHANGELOG, checklist, sheet rows, gate dry run, upgrade/rollback smoke from 4.5 | 7 | AGENT-DOABLE | beta channel only |
| G13 | Real Android acceptance: the 18 SWV5_ACCEPTANCE rows plus suspend/resume, host down, Wi-Fi change, real microphone | 1, 2 | OWNER-GATED (owner phone) | 5.0 ships as beta, `mobile_access` stays implemented_unverified, stable waits |
| G14 | Trusted certificate on the phone (Tailscale certificate or local CA) instead of the self-signed warning | 2 | OWNER-GATED (Tailscale account) | self-signed on home Wi-Fi |
| G15 | Live Claude Code/Codex sessions for Dev Manager and project-manager demos | 6 | OWNER-GATED (subscriptions) | fake agents + read-only GitHub |
| G16 | A goal plan that runs a real consequential Skill (sends something) | 3 | OWNER-GATED (external send) | demo with local-only Skills (notes, lists, timers) |
| G17 | Phone performance and cost on a real device (LAN latency, Wi-Fi echo, battery) | 7 | OWNER-GATED | record "not measured" honestly |
| G18 | Hands-free/live streaming voice from the phone (today: push-to-talk only), barge-in, background/wake | 2 | BIG-BLOCKER -> V6 backlog | push-to-talk counts as V5 voice |
| G19 | Live push/steer into running agent sessions (needs supported session interfaces) | 6 | BIG-BLOCKER -> V6/V7 backlog | status-only, labelled "unsupported" |
| G20 | No V5 tracking issues or milestone exist (the V5 issue search returns none) | - | AGENT-DOABLE | one issue per gap, when issue creation is allowed |

## 4. Work breakdown (small PRs, agent-hours, recommended order)

| Order | PR | Gaps | Est. | Risk / merge |
|---|---|---|---|---|
| 1 | docs: refresh V5 status (ROADMAP block, V5 package rows, DOC_MAP) + this plan | G1 | 1 h | docs, auto |
| 2 | issues: V5 milestone + one issue per gap (no PR) | G20 | 0.5 h | none |
| 3 | 5.0 bug table skeleton + triage of open issues and selftest/speedtest | G11 | 2 h | docs, auto |
| 4 | repo constants -> Jarvis-igerman-ai/jarves (devstatus, issue_registry, links) + tests; updater in a separate PR | G2 | 2 h | low; updater `no-automerge` |
| 5 | capability rows: one evidence string per pillar (real test files) | G8 | 1.5 h | low |
| 6 | phone: Learned-preferences page + `/api/m/prefs` (new scope `prefs`) + tests + guide update | G3 | 4 h | medium (new phone route) |
| 7 | phone: mic/cloud-audio indicator + notice stop/snooze | G5 | 3 h | medium |
| 8 | phone: goal-plan view (progress, pause/cancel/retry; approve stays on the PC) | G4 | 4 h | medium |
| 9 | continuity: resume a phone conversation on Windows (dashboard Chat + `jarvis chat --resume`) | G6 | 4 h | medium |
| 10 | dev status: read-only real-GitHub smoke + "unsupported route" labels | G10 G19 | 2 h | low |
| 11 | V5 scenario e2e test (emulated phone + desktop, fake Skills, injected clock) | G7 | 5 h | tests, auto |
| 12 | Opus security/privacy review of the V5 pillars + fix PRs | G9 | 4 h + fixes | `no-automerge` |
| 13 | fix every P0-P2 from the 5.0 bug table (one PR per bug) | G11 | varies | per bug |
| 14 | release prep 5.0.0: version, CHANGELOG, `RELEASE_CHECKLIST_5.0.0.md`, sheet rows (phone rows from SWV5_ACCEPTANCE), `release_gate.py` dry run | G12 | 3 h | `no-automerge` |

Total about 40 agent-hours. PRs 6-10 are independent and can run in parallel (5-6 agents, at most 3 open PRs). PR 11 follows 6-9. PR 14 comes last.

## 5. Release gates and test plan ("I test and release")

- **Automated gates (agent):** `jarves-checks` green on main (all shards, phone e2e browser job, `test_guides`, `test_cli_parity`), `jarvis selftest` and `jarvis speedtest` clean, log_lint 0, redaction audit, no open P0-P2 in the 5.0 bug table, `release_gate.py --tag v5.0.0` dry run passes.
- **Installed-beta smoke on the owner PC (agent, owner rule):** back up first. Install the 5.0 beta over 4.5. Check Core, dashboard and face start. Turn the phone server on with the emulated phone, run one goal plan with local Skills, confirm one preference, resume one chat. Then `jarvis update --rollback` and back. Report the result; the owner can revert.
- **Beta:** the 5.0.0 beta is published by `package.yml` and the beta channel stays current.
- **Stable 5.0.0:** needs the owner's signed sheet (`jarvis release signoff --version 5.0.0`). That includes the real-Android rows (G13, about 20 min) and any accepted-blocked rows with a note. Until then the stable tag is prepared but not pushed, and V6 planning goes on.
- **Evidence table:** each DoD line is marked EMU-PASS / REAL-PASS / NOT-RUN in the checklist. Nothing is marked verified without device evidence.

## 6. Questions for the owner (each has a default)

1. **Phone voice for 5.0:** is push-to-talk enough, with hands-free/live phone voice moved to V6? Default: yes.
2. **Real-phone sheet:** when can you run the 20-minute Android sheet? Default: 5.0 goes out as beta now, and stable waits for your sheet.
3. **Releases repo:** does `igalgerman/jarvis-releases` move to the organization too? Default: it stays where it is; only the source-repo constants change.
4. **Live agent demos:** may Dev Manager and the project manager run one real Claude Code/Codex session (subscription, no API spend)? Default: no; fake agents and read-only GitHub until you are present.
5. **Start V5 code:** may V5 PRs start once the 4.5 beta is cut (NOW updated to authorize V5)? Default: yes, and V5 PRs never block a 4.5 fix.
