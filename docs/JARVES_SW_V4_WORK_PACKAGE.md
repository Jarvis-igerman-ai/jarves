> **Status correction — 20 Oct 2026 (later owner decision):** V4 was approved; see [PRIORITIES](plans/PRIORITIES.md). **Public `igalgerman/jarves` alone** is the writable source of truth. The former private repo is deprecated/read-only historical migration input and MUST NOT be updated or synced. The older lines below describing the plan as awaiting approval, private sync or updating the private repo are obsolete and superseded. Current source transfer and release gates: [MIGRATION_STATUS](MIGRATION_STATUS.md), public [Issues #1–#5](https://github.com/igalgerman/jarves/issues), public draft PRs #6–#10. These drafts are not merged; V4 has NOT been declared released. No newly authorized paid spending: use the pre-existing $25 total cap with consumed spending deducted. V5/V6 are future roadmap only.

# JARVES — SW-V4 Work Package (authoritative plan)

**Status:** PLAN for owner approval, written 20 Oct 2026 ([#17](https://github.com/igalgerman/JARVIS/issues/17)). It replaces the earlier draft on branch `v3-v4-plan`. Nothing here is authorized until the owner approves it and `docs/NOW.md` points to it.
**Owner instructions (20 Oct 2026), binding for this plan:**
1. V4 is **SOFTWARE ONLY**. The phone as a JARVES device, Raspberry Pi, hardware, Tailscale remote access, multi-device and every language other than English and Hebrew are **out of scope** and listed under [Later / out of scope](#14-later--out-of-scope).
2. Every V4 item is planned as a **COMPLETE feature**: full scope, full acceptance criteria and full tests. No "MVP", "first step" or "phase 1" items. When a provider makes part of a feature impossible (for example, attaching to an arbitrary running Codex terminal), the complete feature includes the honest "unsupported" path and the supported alternative. A missing path is a defect.

**Starts after:** `v3-final` is integrated and green, and `v3.0.0` is released (PRIORITIES.md items 1-3). Two packages already ran on V3 code: V4 P0.3 parity (`v4-p03-parity`, merged into `v3-final`) and P01 owner verification (`v4-p01-verify`, [report](reviews/SWV4_P01_VERIFY.md) on that branch). Their results are carried into package A and package C.
**Development home after the release:** the public repo `igalgerman/jarves` for code and CI (standard runners are free there). The private repo `igalgerman/JARVIS` keeps HANDOFF, personal notes, logs and reviews. They are synced with `scripts/make_public_snapshot.py` (owner answer, 20 Oct).
**Final delivery:** `main` on the public repo, tag `v4.0.0`, the installer published, and the private repo updated.

**Inputs read for this plan:** `docs/plans/PRIORITIES.md` (branch `v3-priorities`, with the owner answers of 20 Oct late), `docs/plans/WHATSAPP_REAL_ACCESS.md`, `docs/plans/FEATURE_SUGGESTIONS.md`, `docs/plans/FULL_TEST_PLAN.md`, `docs/plans/CODE_SIGNING_FREE.md`, `docs/ROADMAP.md`, `docs/JARVES_SW_V3_WORK_PACKAGE.md`, `docs/reviews/SWV3_INTEGRATION_REPORT.md`, `SWV3_CAMPAIGN_FEATURES.md` / `_BUG_TABLE.md`, `SWV3_CAMPAIGN_DASHBOARD.md` / `_BUG_TABLE_DASH.md`, `SWV3_CAMPAIGN_VOICE.md` / `_BUG_TABLE_VOICE.md`, `SWV3_SELFCHECK_REPORT.md`, `SWV4_P01_VERIFY.md` (branch `v4-p01-verify`), the earlier V4 draft, `docs/HANDOFF.md` (decision queues), `AGENTS.md`, and GitHub issues #7, #14, #15, #16, #17, #24, #25, #26, #27, #28, #49. **No `SWV4_WAKE_TRAIN.md` exists:** there is no `v4-wake-train` branch on origin. The wake-training code (`voice/wake_train.py`, `wake_calib.py`, `wake_confirm.py`, `wake_eval.py`) came in from `v3-wake` and is merged into `v3-final`. Package J builds on it.

---

## Contents

1. [Goal](#1-goal)
2. [Rules for every package](#2-rules-for-every-package)
3. [Packages at a glance (list, size, order)](#3-packages-at-a-glance)
4. [Dependency graph and delivery order](#4-dependency-graph-and-delivery-order)
5. Packages A to O: [A](#a--owner-assisted-verification-and-issue-closure), [B](#b--privacy-hardening-and-full-redaction-audit), [C](#c--independent-code-re-review-and-fixes), [D](#d--client-mode-gpt-live-delegation-strict-budget--plan-backed-reasoning), [E](#e--agent-orchestrator-for-claude-code-and-codex), [F](#f--whatsapp-real-access-bridge), [G](#g--conversation-recall), [H](#h--assisted-dialing-phone-link), [I](#i--telegram--instagram--home-assistant-live-verification-then-rocket-alerts), [J](#j--custom-wake-word-in-production), [K](#k--dashboard-completion), [L](#l--installer-and-updates), [M](#m--performance-budgets-ci-matrix-and-test-platform), [N](#n--docs-and-guides-refresh), [O](#o--self-diagnostics-20)
6. [Testing — automatic](#6-testing--automatic)
7. [Testing — manual: owner script](#7-testing--manual-owner-script)
8. [Testing — manual: agent-driven script](#8-testing--manual-agent-driven-script)
9. [Campaign protocol](#9-campaign-protocol)
10. [Release checklist](#10-release-checklist)
11. [Owner decisions: open, and already answered](#11-owner-decisions)
12. [What V3 verified vs not (carried forward)](#12-what-v3-verified-vs-not)
13. [Definition of done for V4](#13-definition-of-done-for-v4)
14. [Later / out of scope](#14-later--out-of-scope)

---

## 1. Goal

V3 built many features. It tested them with real software, but mostly without the owner's real accounts and hardware. V4 has four goals:

1. **Prove and close.** Turn every remaining BLOCKED row into PASS or FAIL with owner evidence. Then close #17, #24, #25 and #28 (#7, #26 and #27 are closed already; their leftover live checks move here).
2. **Make it safe and trustworthy.** Privacy hardening with planted-secret proofs (#15), an independent code review with fixes (#16 and later code), and Self-Diagnostics 2.0, which finds problems before the owner does.
3. **Add the big features the owner chose, each one complete.** The Agent Orchestrator (#49), real WhatsApp access, client-mode delegation with a strict budget (#14), conversation recall, assisted dialing, the custom wake word in production, and the Telegram/Instagram/Home Assistant live paths. Rocket alerts come last.
4. **Make shipping cheap and repeatable.** Clean-machine install, silent upgrade and rollback, performance budgets enforced in CI, a nightly user-journey canary, and up-to-date docs.

## 2. Rules for every package

- **Complete, not minimal.** Each package has its full scope, acceptance criteria, automatic tests and manual tests. A package is done only when every acceptance row is PASS, or BLOCKED with a reason the owner has accepted in writing (HANDOFF).
- **Research first.** Check current official docs, model lists and changelogs (AGENTS.md "Best-in-class technology"). Record in the package report the chosen library or model ID, the date checked and the source link. Reuse the existing JARVES modules (`approvals.py`, `features.py`, `scheduler.py`, `store.py`, `logs.py`, `selfcheck.py`, `diagnose.py`, `diagbundle.py`, `browser.py`, `whatsapp.py`, the guides, the capability matrix, `cli_parity.py`). No refactor beyond what the package needs.
- **Acceptance means a real system.** Acceptance tests run against a real Core process on the offline `test` profile, with real HTTP, CLI, SQLite and Godot. The real profile is used only where the owner says so. Fakes are allowed for third-party services in CI, but a fake run is never acceptance evidence for the real service.
- **Evidence classes are never mixed:** `real` (real process/service/account), `virtual` (virtual mic, speaker or camera, never played aloud), `owner` (owner present, real hardware or account), `blocked`. A virtual-mic run is not a test of the physical mic.
- **Zero skips, zero flakes.** Any skip fails CI (the conftest hook and the CI grep stay). A test that cannot run is reported as BLOCKED in a real-system suite, never as skipped. A flaky test is a P2 bug, fixed in the package that touches it (flake policy in [6.4](#64-flake-policy)).
- **Owner gates stay.** Account sign-ins, paid calls above the approved budget, real sends (Telegram, Instagram, email, WhatsApp), linking WhatsApp, installs on the owner's PC, Claude Code / Codex plugin installs, purchases and repo visibility changes each need the owner's explicit go. Agents ask in chat and in HANDOFF, then wait.
- **Every feature** keeps a feature switch, a dashboard status, a CLI entry (enforced by `tests/test_cli_parity.py`), a guide page, a capability row (Windows status; Pi `planned` with an issue link), bounded logs that pass the B redaction tests, and English and Hebrew texts (no Arabic or Russian).
- **Model choice per task.** The lead may move a task to a stronger model, never to a weaker one for the listed areas.
  - **Opus 5.5:** security and privacy design or review, voice and conversation behaviour, UX design and review, performance investigation, and the **independent review** of every consequential change. The reviewer is never the agent that wrote the change.
  - **Sonnet 5.5:** tests, docs, catalogs, CLI and dashboard wiring, CI YAML and other mechanical changes.
  - **Haiku (the current version):** read-only scanning: log triage, string inventories, grep-style coverage audits, link checks.
- **Repo hygiene.** Never `git add -A`; add files by name. Commits reference `(#17)` (plus the package issue) and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Merge only when every `jarves-checks` job is green on the merged SHA. Run one pytest process at a time (RAM). No secrets, transcripts, contact data or private screenshots go into Git.
- **Live budget.** Total V4 live spend is capped at what the owner approves (decision OD-01, proposal $15). Every package reports its measured cost. Paid suites print their estimate first and stop at their own cap.

## 3. Packages at a glance

Effort is agent working days (one focused agent; reviews included). Owner time is the time the owner must be present.

| ID | Package | Issues | Main model | Agent effort | Owner time | Depends on |
|---|---|---|---|---|---|---|
| A | Owner-assisted verification and issue closure | #17 #24 #25 #28 (#7 #26 #27 closure) | Sonnet driver, Opus voice review | 3 d | ~2.5 h (two sittings) | v3.0.0 released |
| B | Privacy hardening and full redaction audit | #15 | Opus design + review, Sonnet tests | 4 d | 10 min (OD-12) | none |
| C | Independent code re-review and fixes (incl. open P3 backlog) | #16 | Opus reviewer (fresh context), Sonnet fixes | 4 d | 10 min (Pear check) | B design (shared logging) |
| D | Client-mode delegation: strict budget, plan-backed reasoning | #14 | Opus | 4 d | 15 min | B, C voice fixes |
| E | Agent Orchestrator for Claude Code and Codex | #49 | Opus design/security, Sonnet adapters/tests | 12 d | ~1.5 h over 3 sittings | B, O (health/approvals hooks) |
| F | WhatsApp real access bridge | new issue | Opus security, Sonnet bridge/tests | 9 d | ~1 h + 2-week watch | B |
| G | Conversation recall | new issue | Opus design/voice, Sonnet wiring | 5 d | 20 min | B |
| H | Assisted dialing (Phone Link) | new issue | Sonnet impl, Opus security | 3 d | 20 min | A (contacts check) |
| I | Telegram / Instagram / Home Assistant live verification, then rocket alerts | new issues | Sonnet, Opus security + safety | 5 d | ~1 h | A; rocket alerts last of all |
| J | Custom wake word in production | new issue | Opus (voice) | 4 d | 30 min recording + 15 min check | A (samples), M (budget) |
| K | Dashboard completion (en+he, polish, real-browser UX review) | new issue | Opus UX, Sonnet catalogs, Haiku inventory | 5 d | 30 min review | after the UI parts of E/F/G/O |
| L | Installer and updates | new issue | Sonnet, Opus security review | 4 d | 45 min (second PC) | M (CI), OD-08 signing |
| M | Performance budgets, CI matrix and test platform | new issue | Opus perf, Sonnet CI | 5 d | none | A real starts |
| N | Docs and guides refresh | #17 | Sonnet, Haiku link check | 3 d | 15 min | all features merged |
| O | Self-Diagnostics 2.0 (incl. scheduled selective AI review) | new issue | Opus design, Sonnet impl | 8 d | 20 min | B (redaction), M (metrics) |

**Total: 15 packages, about 78 agent-days** (roughly 3.5-4 calendar weeks with 3 agents in parallel on separate files), plus **about 10 hours of owner time** spread over the period, a 2-week WhatsApp watch window, and live spend within OD-01. The biggest items are E (12 d), F (9 d) and O (8 d).

## 4. Dependency graph and delivery order

```
                v3.0.0 released (PRIORITIES 1-3)
                          |
        +-----------------+------------------+-----------------+
        |                 |                  |                 |
        A owner pass      B privacy          M CI/perf         C re-review (reads code;
        (needs D4 slot)   (logging core)     platform          fixes after B's logging API)
        |                 |                  |                 |
        |      +----------+-------+----------+------+          |
        |      |          |       |                 |          |
        |      O self-diag 2.0    G recall    F WhatsApp       D client delegation
        |      (needs B + M)      (needs B)   (needs B)        (needs B + C voice fixes)
        |      |                                               |
        |      +--------------------> E Agent Orchestrator <---+ (uses O health, B redaction,
        |                             (largest)                   approvals; D optional)
        +--> J wake (A samples, M budget)
        +--> H dialing (A contacts check)
        +--> I live channels + HA (A) ............. rocket alerts LAST
                          |
                 K dashboard completion (after the UI of E/F/G/O exists)
                 L installer & updates (after M; signing decision OD-08)
                          |
                 N docs refresh -> release checklist -> v4.0.0
```

**Order and parallel lanes** (one writer per shared file; `logs.py`, `cli.py`, `dashboard.py`, `index.html`, the catalogs and `jarves-checks.yml` each have one owner at a time):

1. **Wave 1 (start at once):** A preparation and the owner sitting (D4 slot); B (owns `logs.py`, `diagbundle.py`, `cli._private_line`); M (owns `jarves-checks.yml` and the new `nightly.yml`); the C review pass (read-only until B's logging API lands, then fixes).
2. **Wave 2:** O (after the B API and the M metrics schema); G; F (bridge plus fake; the owner link step waits for the WhatsApp decisions); D (after the C voice fixes).
3. **Wave 3:** E (after O's health and approval hooks); J (after the A samples); H; I (Telegram, Instagram and Home Assistant first).
4. **Wave 4:** K (whole-dashboard pass once the new pages exist); L (release path); I rocket alerts (last feature); N (docs); then the release checklist and `v4.0.0`.

When Claude quota, RAM or disk run low, pause in this order: rocket alerts, K polish, H, J, G, F, E, D. Protect A, B, C, M, O and the release. Commit short checkpoints often.

---

## 5. Packages

Each package below states: goal, full scope, acceptance criteria, tests (automatic + manual row IDs from sections 7 and 8), model, dependencies, agent effort, risks and owner decisions.

## A — Owner-assisted verification and issue closure

**Goal.** Every V3 feature that was only tested offline gets one owner-present run on the real PC and real accounts. Then #17, #24, #25 and #28 close with evidence, and the leftovers of the closed #7, #26 and #27 are verified or moved to a named V4 package.

**Already done (do not repeat):** `SWV4_P01_VERIFY.md`: real Pear skill rows S01-S13 (12/13, S10 honest FAIL P3), voice V01-V10 (virtual), K01 5-min soak, Google G1-G6 (test, calendar via tool and CLI, Gmail headers, briefing, delete guard). G7 "email sending is OFF" is FAIL (B1), but the owner has since answered: keep `gmail_send`, `calendar_write`, `camera`, `camera_when_away`, `face_recognition` and `dashboard_lan` ON, with exact confirmation for sends. G7 is re-scored as PASS against the new rule: send needs exact confirmation, and there is no delete tool. `jarvis connect` is done for Google, Pear and wake. Telegram, Instagram and Home Assistant were skipped and are done in package I.

**Scope (full).** One prepared script, `core/tools/owner_pass.py`. It is idempotent, resumable by row ID, prints one instruction at a time, records PASS/FAIL/BLOCKED with evidence class `owner`, and writes `docs/reviews/SWV4_A_OWNER_PASS.md` (counts and timings only, no personal content). The rows are the owner script sections [7.1-7.9](#7-testing--manual-owner-script). Grouped by issue:

| Issue | Remaining acceptance items | Rows |
|---|---|---|
| #17 (SW-V2 umbrella) | the 18 checklist items: each gets a ticked box with an evidence link to the V2/V3/V4 report that proves it, or is moved to a named V4 package; live Calendar natural-language acceptance (voice: "מה יש לי מחר?", confirmed create, no delete); P0 items from the 19 Oct log review (full-exit intent, pending-answer recovery), verified by a live conversation | OA-01..OA-12 |
| #24 (WP-K Google/Connections/privacy) | real first-time `jarvis google start` (console links, clipboard, Downloads pickup) on a clean Google test project or a re-run; weekly reconnect after a real 7-day Testing expiry, via the CLI and the dashboard Reconnect button; live privacy switch during a real conversation (the Core follows `privacy.json` within 1 s). Pi rows move to Later. | OG-01..OG-07 |
| #25 (WP-L guides/help/suggestions) | hands-on dashboard Guides check (index, per-feature pages, Test now buttons, Home checklist); owner voice "איך אני מחבר את גוגל?", "מה אתה יכול לעשות?", step-by-step "סיימתי"/"הבא"; a proactive suggestion said once at a natural pause, honouring snooze and "תפסיק להציע" over 3 days; the real API bill guide with a read-only admin key (`jarvis connect openai-admin` or the Usage page, see `OPENAI_ADMIN_KEY.md`, if the owner has one; otherwise BLOCKED with OD-05). Pi rows move to Later. | OL-01..OL-08 |
| #28 (privacy strip + camera preview) | preview with the real webcam (frame rate and latency measured); strip states during a real GPT-Live conversation (listening / paused while speaking / audio to cloud) matching what is heard; owner review of colours and words (colour-blind safety by eye). Pi camera and the round display move to Later. | OC-01..OC-06 |
| #7 (closed) leftovers | the owner's own Hebrew voice test on the real mic; owner approval of the face | OV-01..OV-10 |
| #26 (closed) leftovers | live Contacts consent and one read-only lookup; one confirmed invitation to the owner's own second address | OG-08, OG-09 |
| #27 (closed) leftovers | `jarvis execute` live natural-language Calendar request with a terminal confirmation | OA-05 |
| Approvals / popup | real popup on the desktop (Hebrew/RTL, position, on top, Enter/Esc), voice "כן" in a live session (echo check), tray balloon, spoken approval naming the recipient's first name (owner answer) | OP-01..OP-08 |
| Wake samples | record about 20 "Hey Jarvis" positives, 20 "היי ג'רוויס" positives (if OD-19 = yes) and 20 negatives into the private state dir (never committed); feeds J | OW-01..OW-03 |
| Music | the real-Pear rows still open after P01: S10 re-run after the C fix; voice V02 re-run on the real mic | OM-01..OM-04 |
| Browser | one allow-listed site the owner picks: read, click, a refused purchase field; a spoken browser conversation | OB-01..OB-04 |

**Issue closure steps (part of the scope).** For each issue: tick each box with a link (commit, report row, CI run URL of the final commit). When every box is done, close it with a summary comment. If a box remains, keep the issue open, name the exact blocker, and link the V4 package or Later issue that owns it. Pi-only boxes in #24, #25 and #28 move to a new narrowly scoped "Pi verification" issue (Later) before closing, per AGENTS.md issue lifecycle rule 6. #17 closes last, after N, with a link to this plan's final verified-vs-not table.

**Acceptance criteria.**
- Every row has PASS / FAIL / BLOCKED, evidence class `owner`, the exact command, commit SHA, latency and cost. No row is silently dropped.
- Every FAIL at P0-P2 is fixed in this package with a regression test and re-run with the owner in the same or the next sitting. P3 goes to the V4 bug table (`docs/reviews/SWV4_BUG_TABLE.md`).
- Log review after each sitting: every WARN/ERROR classified as expected / bug / noise and compared with the 19 Oct baseline and the P01 classification.
- #24, #25 and #28 are closed (Pi parts moved). #17 has every box ticked or linked and closes at release. The #7/#26/#27 leftovers are PASS or moved with a link.
- Owner time is at most 2.5 h, in at most two sittings. Live spend at most $2.

**Tests.** Automatic: `tests/test_owner_pass.py` (row registry complete, resume works, the report has no personal content: a planted name and email never appear). Manual: owner script 7.1-7.9.
**Model.** Sonnet 5.5 drives and writes the report. Opus 5.5 reviews voice behaviour (barge-in, echo, wake, greeting) and any security-relevant fix.
**Dependencies.** `v3.0.0` released; owner slot (OD-04).
**Effort.** 3 agent-days (1 preparation, 2 sittings with fixes, closure).
**Risks.** Real sends reach real people: use only the owner's own chats and addresses, with exact-text confirmation. Google Testing-mode tokens expire weekly: schedule OG-05 for the expiry day. Echo on real speakers: test half_duplex both ways. Owner time is scarce: no repeated runs.
**Owner decisions.** OD-04 (slot), OD-05 (admin key yes/no), OD-19 (Hebrew phrase samples).

---

## B — Privacy hardening and full redaction audit

**Goal.** No secret ever reaches any output, in any mode. No personal content reaches any output in privacy mode or in a support/diagnostic bundle. This is proven by tests that plant secrets and personal data at every input and search every output.

**Scope (full).**
1. **#15 fix: structured log messages.** `logs.*(component, CODE, msg, **fields)`: `msg` becomes a fixed, developer-written description. Every variable part moves into typed fields. Each field name is classified once in a registry (`logs.FIELD_CLASS`): `safe` (counts, durations, IDs, enum states), `secret` (always masked), `content` (user or provider text; masked in privacy mode and always stripped from bundles), `pii` (names, numbers, emails, addresses; masked in privacy mode, hashed in bundles). Unknown fields default to `content`. Provider error text (`LiveVoice._on_error`, `err.message`, HTTP bodies, exception strings) goes to `provider_error` (content). An AST test fails the build if a `logs.*` call passes a non-literal message (f-strings and `%` formatting with variables included), or if a field name is not in the registry.
2. **Every sink audited and fixed.** The inventory, kept as `docs/reviews/SWV4_B_SINKS.md`, covers: `jarves.log` (JSON) and its rotations, the console formatter, `ConsoleFormatter` under `jarvis run`, `store.db` (issues, selfcheck findings, context traces, delegations, usage, approvals, tasks), `memory.db`, `health.json`, `runtime.json`, crash output (`faulthandler`, unhandled-exception hooks, asyncio exception handler), `jarvis diag` ZIP (`diagbundle.py`, `cli._private_line`), self-check ticket drafts, scheduler reports, approval result files and popup arguments, Godot face logs and the face link messages, dashboard log view and download, CI artifacts, third-party loggers (`httpx`, `openai`, `urllib3`, `websockets`, `playwright`; URLs with query tokens), environment dumps in `doctor` and `status --json`, Telegram/Instagram/HA/WhatsApp-bridge logs, the browser skill logs, and the new E/F/G/O tables.
3. **Bundle rule.** `jarvis diag` **always** strips `content` and hashes `pii`, whatever `log.privacy` says, including nested JSON, malformed lines (a malformed line is dropped and counted, never copied), stack traces (frames kept, local values and exception text dropped) and SQLite exports.
4. **Development mode clarified** (`log.privacy=false`, today's default). Secrets are masked in both modes. Content is written to disk only when privacy is off, and the dashboard and `jarvis status` show "Privacy: off — conversation text is kept in logs". The default for new installs is decided by the owner (OD-12; recommendation: ON for new installs, OFF kept for the owner's existing profile until he flips it).
5. **Secret patterns kept current:** OpenAI keys (`sk-`, `sk-proj-`, admin keys), ChatGPT OAuth tokens and ID tokens (JWT), Google `ya29.` access and `1//` refresh tokens and client secrets, Telegram bot tokens, Instagram/Meta tokens, Home Assistant long-lived JWTs, the dashboard link token, the WhatsApp bridge bearer token and device-store paths, Claude Code / Codex session tokens (E), DPAPI blobs, Windows user-profile paths (username → `<user>`).
6. **Retention and delete paths.** Every new store has a retention setting and a delete path. "Forget" and profile delete remove rows from every index (G, O, E, F).

**Acceptance criteria.**
- The planted-data audit `tools/redaction_audit.py` (see [6.1 layer 9](#61-test-pyramid)) reports **0 secret hits in every sink in both modes**, **0 content/PII hits in every sink in privacy mode**, and **0 content/PII hits in every `jarvis diag` bundle in both modes**. Encodings searched: raw, JSON-escaped, URL-encoded, base64, UTF-16LE, Hebrew NFC/NFD.
- Both #15 reproductions (the `logs.info` free text, and `_private_line` with `msg`) are regression tests and pass.
- The AST lint covers 100% of `logs.*` call sites. The field registry has no unknown fields.
- Every sink in the inventory has a test row. Opus review sign-off is recorded in the report.
- #15 closes with the final-commit CI run URL.

**Tests.** Automatic: `tests/test_redaction_planted.py` (unit level, each sink writer), `tests/test_log_message_lint.py` (AST), `tests/test_diag_bundle_strip.py`, plus the system-level `tools/redaction_audit.py --both-modes`, which drives a real Core on the test profile through typed turns, tool errors, a provider error injected by the fake, a crash, a diag export, a self-check ticket and a dashboard log download. Manual: owner rows OR-01..OR-04 (privacy switch, diag ZIP inspected by eye) and agent rows AR-01..AR-05.
**Model.** Opus 5.5 for the design and the review; Sonnet 5.5 for the call-site migration and tests; Haiku for the call-site inventory.
**Dependencies.** None. B owns `logs.py`, `diagbundle.py` and `cli._private_line`; other packages call its API.
**Effort.** 4 agent-days (about 300 call sites to migrate mechanically).
**Risks.** Over-redaction makes debugging hard: keep codes, IDs and timings rich. Mass call-site edits conflict with other branches: land B early, in one PR, before waves 2-3. Third-party loggers can change format: the audit catches it.
**Owner decisions.** OD-12 (privacy default for new installs).

---

## C — Independent code re-review and fixes

**Goal.** A reviewer that did not write the code reviews the whole V3/V4 code base from a fresh context. Every finding is triaged, every confirmed P0-P2 is fixed with a test, and #16 is closed.

**Scope (full).**
1. **#16 triage and fixes** (static review of 18-19 Oct, partly handled in SW-V2 WP-E). Each item is labelled confirmed bug / design choice / unreproduced risk, with evidence:
   - P1 playback confirmed while Pear is stalled: separate "player says playing" from "playback confirmed" (elapsed progress observed across two polls, or a Pear event if one exists), else an honest `MUSIC_NOT_CONFIRMED`.
   - P2 permissive track match and search relevance: title + artist, a narrow same-recording exception, relevance judged per candidate (Hebrew transliteration, mood/artist-only queries).
   - P2 automatic queue trimming changes Pear history: a config switch `music.trim_history` with a documented default (OD-14).
   - P2 the 25 s budget expiring during the candidate loop: one end-to-end deadline, per-candidate polling bounded, a truthful final result before the tool timeout.
   - Risk: false-wake timeout during a long utterance with delayed transcripts: a deterministic 20-40 s continuous-speech test with delayed transcript events; the watchdog also looks at mic speech energy.
   - Risk: captions for suppressed audio: align caption visibility with delivered audio; test concurrent speech and early output.
2. **Full re-review** of the diff from `38948e2` (the #16 baseline) to the V4 head, with focus on security boundaries (approvals, taint guard, dashboard auth, helper, browser policy), concurrency (asyncio tasks, the cross-process approvals poller, SQLite writers), error handling and honesty of tool results, resource bounds (queues, logs, caches), and the voice lifecycle. Output: `docs/reviews/SWV4_C_REVIEW.md` with at most 40 ranked findings, each with file:line, severity, evidence and a proposed fix.
3. **Open defect backlog** from the V3 and P01 tables, fixed in the same package: O-03 (`google_connect_help` ok=True with the code), O-04 (browser decisions and normal face disconnect at INFO), O-05 (distinct `jarvis status` exit code for "headless, face absent", documented in help), O-06 (timer "ringing" state), P01 B2 (S10 pause after a fast next: wait for the Pear track change), B3 (a correct calendar-delete refusal logged at INFO/WARN, not ERROR), B4 (Hebrew briefing city "בTel Aviv": localized city name), B5 (V02 empty-transcript claim: the false-claim detector covers "stopped" without a tool call), Q08 (two reminders due in the same tick spoken back to back), CV-16 (צחי pronunciation: no default dictionary entries, per the owner), CV-17 (17-20k input tokens per delegation: trim the tool list and instructions, measure before and after), integration open point 1 (the `helper: ok` health row on a fresh test profile).
4. **Optional second opinion** by another vendor family (OpenAI Codex on the owner's subscription), only if OD-13 = yes. It gets the same diff and the same template. Disagreements are resolved by evidence (a test), not by vote.

**Acceptance criteria.**
- Every #16 item and every review finding has a triage label and evidence. Every confirmed P0-P2 is fixed, with a test that fails before the fix and passes after.
- Real-Pear validation (owner present or Pear open with volume forced to 0): candidate fallback, next/pause/stop/status, a long queue, and a slow/stuck player simulated by pausing Pear's process. `suite_music.py --groups skill` is 13/13 PASS.
- Voice regression: `suite_v3_voice.py` groups greeting, long_utterance, barge-in, idle, budget are PASS after the voice-side fixes (cost at most $1.5).
- CV-17: input tokens per delegation are measured and reduced by at least 30% with no scenario regression.
- #16 closes with the final-commit CI URL. The C report lists every finding with its status.

**Tests.** Automatic: one regression test per fixed item (in `tests/test_music_*.py`, `test_voice_*.py`, `test_campaign_features.py`), plus the slow-Pear fake (`tests/fakes/pear_slow.py`). Manual: owner OM-01..OM-04; agent AM-01..AM-06.
**Model.** Reviewer: **Opus 5.5 in a fresh context** that has not authored the changes; second opinion OpenAI Codex if OD-13. Fixes: Sonnet 5.5 (mechanical), Opus 5.5 (voice and prompt).
**Dependencies.** The review is read-only and starts at once. Fixes that touch logging wait for the B API.
**Effort.** 4 agent-days (1.5 review, 2 fixes, 0.5 validation).
**Risks.** Prompt and token trimming can shift voice behaviour: re-run the full voice groups. Exit-code changes can break scripts: document them in the release notes.
**Owner decisions.** OD-13 (Codex second opinion), OD-14 (Pear history trimming default).

---

## D — Client-mode GPT-Live delegation: strict budget + plan-backed reasoning

**Goal.** JARVES itself runs every backend request that GPT-Live delegates (client delegation mode, [OpenAI live-delegation guide](https://developers.openai.com/api/docs/guides/live-delegation), recheck at start). Each request is then reserved against the budget **before** it is sent, which gives a strict cap. In `combined` mode the reasoning can run on the owner's ChatGPT plan instead of the API.

**Scope (full).**
1. **Research note** (dated): the client-delegation events and flow on the current GPT-Live version, the response format back to the live session, the timeout and cancellation semantics, and the measured added latency (10 runs, p50/p95) against server delegation.
2. **Delegation engine** `voice/delegation_client.py`: receives the delegation event, reserves the cost estimate in the shared `Budget` (atomic, cross-process, the existing reservation table), runs the request on the configured brain (`api_key` → Responses API; `chatgpt` / `combined` → the plan route in `chatgpt_brain.py`), streams progress notes, returns the result to the session, and settles the reservation with the real usage. Overlapping turns, barge-in cancellation (the request is cancelled and the reservation released), and per-turn and per-day caps are all handled.
3. **Strict cap semantics.** A request that would exceed the remaining budget is **never sent**. JARVES says so honestly ("הגעתי לתקציב היומי…") and offers the local-only answer path. Plan limit reached: AI pauses and never moves to the API by itself (owner rule since cc5d317).
4. **Fallback ladder** (each step logged and shown): client delegation → (if the client path fails for a protocol reason, and only if `delegation.fallback = server` is set by the owner) server delegation with the existing counting cap → local answer ("I can't look that up now"). Never a silent route or billing change.
5. **Config and surfaces.** `ai.delegation_mode = server | client` (default decided by OD-15 after measurement), the dashboard Costs page shows reservations live, `jarvis budget status` and `jarvis budget test-cap`, a guide page, and a capability row.
6. **Live scenario suite** `suite_v3_voice.py --group delegation` (new scenarios 51-62): simple lookup, web search, two overlapping questions, barge-in during a lookup, cap reached mid-conversation, plan route in combined mode, plan limit simulated (fake 429 from the plan endpoint), network drop mid-request, a 30 s slow backend, a tool call inside a delegated turn, Hebrew and English, cost reconciliation against the provider's usage report.

**Acceptance criteria.**
- Offline: under 50 random overlapping-turn schedules (property test, fixed seed), spend never exceeds the cap by even one request. Reservations are always settled or released, including on crash and restart (stale-reservation recovery).
- Live: scenarios 51-62 PASS on real GPT-Live (virtual mic/speaker). The added latency p95 is at most 400 ms over server delegation, or the owner accepts the measured number. The cap test spends at most the cap plus 0. Combined mode shows plan-billed reasoning with API cost $0 for the reasoning part.
- Cost reconciliation: JARVES estimates within ±10% of the OpenAI usage numbers over the suite.
- #14 closes with the owner's decision on the default recorded.

**Tests.** Automatic: `tests/test_delegation_client.py` (fake live server emitting delegation events; fake brains; property-based overlap test; crash-recovery test), contract tests that the event schema matches a recorded real session (recorded once, redacted). Manual: owner rows OD-T1..OD-T3 in 7.10; agent AD-01..AD-04.
**Model.** Opus 5.5.
**Dependencies.** B (logging), C (voice fixes, CV-17 token trim).
**Effort.** 4 agent-days. Live cost about $2.
**Risks.** Client mode adds latency: measure first, make it the default only if accepted. Protocol changes in a young API: pin and contract-test. The plan route's terms: use only the official OAuth plan route already implemented.
**Owner decisions.** OD-15 (default mode, fallback-to-server allowed?), OD-01 (budget).

---

## E — Agent Orchestrator for Claude Code and Codex

**Goal (#49, the owner's words).** JARVES is the owner's general manager for AI coding agents. It knows which Claude Code and Codex sessions exist, including ones the owner **started by hand**. It talks to them both ways: it asks what they are doing and gets a real answer from that same session, receives their unsolicited status, continues or pauses them, starts new sessions when asked, routes their approval requests to the owner, and does all of it by voice, CLI and dashboard. It uses subscription-supported workflows and a tiny, dependable local integration, not a costly custom agent platform.

**Scope (full).** All six user journeys of #49 (UC1-UC6), for both providers, with an honest capability matrix where a provider cannot do something.

1. **Research and capability matrix (first step of the package, not a separate phase).** On the owner's exact installation, record versions, OS, feature flags, subscription login status and channel eligibility. No token or key extraction. Output: `docs/reviews/SWV4_E_CAPABILITIES.md`, a matrix `provider × mode (pre-existing interactive terminal, pre-existing VS Code, JARVES-started managed, resumable old session, cloud session) × (observe, ask, steer, resume, start, interrupt, approve, auth, cost)` with VERIFIED / UNSUPPORTED and the evidence. Sources to recheck: Claude Code Channels, hooks, plugins, Remote Control, Agent SDK sessions; Codex App Server (`thread/start|resume|fork`, `turn/start|steer|interrupt`, item events), the Codex SDKs; ACP and A2A only as later mapping targets. The "Buzz" name stays unconfirmed and is not used.
2. **Architecture** (inside the Core, not a second brain):
   - `agents/registry.py`: a SQLite registry in `store.db` (sessions, capabilities, last_seen/last_progress, status source, launch origin, project root/branch/worktree, provider session ID).
   - `agents/inbox.py`: a durable inbox/outbox with the #49 envelope (`schema_version`, `message_id` for idempotency, `correlation_id`, `kind`, `expires_at`, severity, an allow-listed payload). At-least-once delivery with dedup, ACK, retry with backoff, TTL, cancellation, and a hop limit against loops (Claude asks JARVES asks Claude).
   - `agents/bridge.py`: **one** local transport: a Windows named pipe (`\\.\pipe\jarves-agents-<instance>`) with a per-instance token in a 0600 file. On Linux, a Unix socket for CI. No TCP port, no Redis/Kafka/Docker.
   - **Claude Code adapter:** an installable JARVES plugin (`integrations/claude-code/jarves-plugin/`) containing (a) hooks (`SessionStart`, `UserPromptSubmit`, `PostToolUse` throttled, `Notification`, `Stop`, `SessionEnd`, `SubagentStop`) that register the session and push heartbeat, progress and summary-on-stop to the bridge; (b) a Channels MCP server that lets JARVES push a message into the running session and receive the reply, where Channels is available; (c) MCP tools `jarves.report_status`, `jarves.reply`, `jarves.heartbeat`. Where Channels is not enabled on a session, "ask" is answered from the latest pushed snapshot (labelled with its age), or queued for the next safe boundary (the next `Stop` hook), and JARVES says which.
   - **Codex adapter:** a client of the Codex App Server (JSON-RPC, `initialize`/`initialized`) for managed threads: start, list, status, resume, steer, interrupt, plus the event stream. Externally started Codex TUIs get `observe` only if a supported discovery path exists; otherwise they are listed as `UNSUPPORTED_ATTACH`, with the offer "resume this thread under JARVES" (owner approval; never two writers on one thread).
   - Lifecycle states: `DISCOVERED / RUNNING / IDLE / WAITING_INPUT / WAITING_APPROVAL / PAUSED / STOPPED / DISCONNECTED / UNKNOWN`. "Last known" is always distinguished from "live reply", with a timestamp. A session is never reported as running based only on an old file.
3. **Commands, the same on every surface** (CLI parity test extended):
   - CLI: `jarvis agents list | status <id|project> | ask <id> "<q>" | tell <id> "<msg>" | continue <id> | pause <id> | stop <id> | start <provider> <project> "<task>" | resume <id> | approvals | approve <req> | deny <req> | log <id> | digest | install-plugin claude|codex | doctor`.
   - Voice tools: `agents_list`, `agents_status`, `agents_ask`, `agents_tell`, `agents_start`, `agents_stop`, `agents_digest`. Hebrew and English ("תשאל את קלוד מה המצב בפרויקט ג'רבס", "which agents are blocked?"). Disambiguation is required before any state-changing action when more than one session matches.
   - Dashboard page **Coding agents** (Activity group): a live table of sessions per project (provider, state, last progress, age, blocker, next step, cost so far), a session page (timeline of events, ask/tell box, approve/deny), the portfolio digest, and an empty state with install instructions.
4. **Agent-initiated contact (UC5).** Workers push `progress`, `need_input`, `approval_requested`, CI/test results and `completed`. JARVES aggregates and interrupts the owner only for useful signals: approval needed, blocked, done, failed. It speaks only if a conversation is open and not in quiet hours; otherwise a tray popup and the dashboard. The interruption rules are configurable per project.
5. **Approvals for risky actions.** An approval request from a worker is shown in the standard approvals popup with the provider, project and exact command. The request ID and session ID are validated, so one session cannot spoof another. JARVES **never auto-approves** `git push`, merge, deletion, writes outside the worktree, privileged commands, paid-API switches or money. Read-only status is automatic. continue/pause/stop/start need the owner (voice yes for continue/pause; on-screen approval for start/stop and for any worker approval of a risky action).
6. **Multi-project and sandboxing.** A project allow-list (`agents/projects.json`, edited only on the PC). One active writer per repo or worktree is enforced (JARVES refuses to start a second writer and offers a new worktree). JARVES never launches an agent with permission-skipping flags. Started sessions use the provider's normal permission mode, in a worktree JARVES creates under the project. JARVES never sends source code, transcripts or prompts to its own cloud model. Only sanitized summaries are kept (B classes).
7. **Cost and budget awareness.** It records the usage the provider reports (Codex token-usage events; Claude Code session cost/usage where hooks or the status line expose it). It labels the auth mode per session (subscription vs API key). If only API billing is possible, it gives an explicit error and never switches silently. A per-day cap on JARVES-initiated turns (`agents.max_turns_per_day`, default 20) and on "ask" probes that would cost a turn. "Status" questions are answered from snapshots first, so a busy model is not interrupted (#49 clarification).
8. **Disconnect and resume (UC6).** After a restart of JARVES, the agent, the PC or the pipe: show the last verified status with a timestamp, replay unacked messages idempotently, re-register live sessions through `SessionStart`/heartbeat or App Server listing, and resume only where supported and approved.
9. **Audit.** Table `agent_audit` (who, what, session, request ID, decision, result) with retention 90 days; the dashboard audit view; `jarvis agents log`.
10. **Install and uninstall** of the plugin and the Codex config, through `jarvis agents install-plugin`, with an owner gate and a clean uninstall. `jarvis agents doctor` checks versions, flags and the pipe.

**Acceptance criteria (the #49 definition of done, extended to everything).**
- **UC1:** the owner starts Claude Code by hand in a project (terminal and VS Code, separately), leaves it working, asks JARVES in Hebrew by voice what it is doing, and hears a truthful current answer from that session (current task, last activity, files/commit evidence, blocker, next step, age of status). The terminal session continues intact. No new CLI run is used as a substitute.
- **UC2:** "ask what is blocking it", "tell it to continue", "pause", "run targeted tests" each reach the same session, with acknowledgement and an async reply. A status question during a busy turn is answered from the snapshot without interrupting it.
- **UC3:** Codex: start, list, status, resume, steer and interrupt on managed threads, with completion events. An externally started Codex TUI shows its honest capability (observe or `UNSUPPORTED_ATTACH`) and the resume offer.
- **UC4:** with 2 Claude Code sessions and 1 Codex thread in 3 projects at once: list, "what progressed since I left", "which project is blocked", start a new Codex session, stop gracefully. No collisions, and disambiguation is asked when needed.
- **UC5:** a worker's approval request appears in the popup within 2 s, is approved/denied, and the decision reaches the right session. A spoofed request ID from another session is refused.
- **UC6:** kill JARVES mid-conversation and restart: pending messages are delivered once, statuses show "last known at …", and live sessions re-register within 10 s of their next hook.
- Resource cost: the bridge plus registry idle at most 0.2% CPU and 15 MB RAM on the owner's PC. No polling loop faster than once per 30 s (event-driven).
- Every explicit case is covered by a test and spoken correctly: `NO_LIVE_CHANNEL`, `SESSION_CLOSED`, `WAITING_FOR_APPROVAL`, `AUTH_REQUIRED`, `UNSUPPORTED_ATTACH`, `BUDGET_REACHED`, `AMBIGUOUS_SESSION`.
- The capability matrix is complete: every cell VERIFIED or UNSUPPORTED with evidence. Security review by Opus signed off. #49 closes.

**Tests.** Automatic: `tests/test_agents_inbox.py` (order, dedup, TTL, retry, hop limit, crash recovery), `tests/test_agents_bridge.py` (pipe auth, wrong token refused, two instances), `tests/test_agents_policy.py` (never auto-approve list, one-writer rule, spoof refusal), and contract tests against `tests/fakes/fake_claude_host.py` (emits the hook payloads and the Channels protocol from recorded real samples) and `tests/fakes/fake_codex_app_server.py` (JSON-RPC, recorded real transcripts). A system test runs a real Core plus both fakes, driven by CLI and the dashboard API. Voice scenarios 63-70 on virtual mic against the fakes. Manual: owner script 7.11 (OE-01..OE-14) on the real Claude Code and Codex; agent AE-01..AE-08.
**Model.** Opus 5.5 for the architecture, the security policy and the voice behaviour; Sonnet 5.5 for the adapters, CLI, dashboard and tests.
**Dependencies.** B (redaction classes), O (health components and the approvals hooks), the approvals service. D is optional (agent questions can be delegated through it).
**Effort.** 12 agent-days. Live cost: agent turns run on the owner's subscriptions; JARVES voice cost about $1.
**Risks.** Channels is a research preview and may need a launch flag or an allow-list: the capability matrix and the snapshot fallback handle it, and the owner may need to start Claude Code with the channel flag (OD-20). Provider protocols change: pin versions, add contract tests, and run `jarvis agents doctor` in the nightly canary. Feedback loops: hop limit. Two writers: enforced refusal. Privacy of code: no code leaves for JARVES's own model.
**Owner decisions.** OD-20 (plugin install, Channels flag), OD-21 (Codex auth mode: subscription only?), OD-22 (project allow-list), OD-23 (may JARVES start sessions without on-screen approval? recommendation: no), OD-24 (daily turn cap).

---

## F — WhatsApp real access bridge

**Goal.** JARVES really reads incoming WhatsApp messages and, after confirmation, sends them on the owner's personal account, by voice, CLI and dashboard. It is safe by construction: an isolated bridge process, an encrypted session, a kill switch, allow-lists, and incoming text treated as untrusted. Design: [WHATSAPP_REAL_ACCESS.md](plans/WHATSAPP_REAL_ACCESS.md) section 4, **adjusted by the owner's answer: the main number, read-only first** (no secondary-number stage).

**Scope (full; stages S1-S3 of the research doc, all in V4; S4 phone is out of scope).**
1. **Bridge** `bridge/whatsapp/` (Go, whatsmeow at a pinned commit; neonize in a separate Python process is plan B if OD-30 says no Go). It runs as a separate process with its own state dir. The device store and message DB are encrypted at rest: SQLCipher, or app-level AES-GCM with the key wrapped by **Windows DPAPI** (CurrentUser scope). A copied store does not decrypt on another PC or user. It has no model and no internet except WhatsApp. It exposes a loopback-only API `127.0.0.1:<random>` with a bearer token in a 0600 file. It enforces by itself: mode `off | read | send`, the read and send allow-lists, rate limits, maximum length, single-use approval IDs signed by the Core approvals service, and the kill file.
2. **BridgeProtocol v1**: `GET /status`, `GET /chats?since=`, `GET /messages?chat=&since=&limit=`, `POST /send {to, text, approval_id}`, `POST /mode` and `POST /kill` (local principal only), WS `/events` (`message.new`, `receipt`, `connection.state`, `account.warning`, `logged_out`).
3. **Pairing** from the dashboard on the PC only (QR or pairing code). Status shows "JARVES bridge" as a linked device. Unlinking on the phone is the always-available hard kill.
4. **Core `WhatsAppChannel`.** Read tools (`whatsapp_unread`, `whatsapp_chat`, `whatsapp_search` within retention) with `untrusted_output=True`, under `untrusted_*` keys. The taint guard makes any side-effect tool in the next 60 s ask first. Bidi and zero-width characters are stripped and shown. Media is metadata only, never downloaded or opened. Links are never opened. JARVES never auto-replies.
5. **Send** (mode `send`, only after the read-only watch period and OD-31): `whatsapp_send` (destructive) → the approvals popup shows the recipient's name, the number and the **full text**, and "new chat" / "group" warnings. The spoken question names the kind of action and the recipient's first name (owner answer). The first message to a person needs on-screen approval. Replies come first ("reply to Dana: …"), then new messages to send-allow-listed contacts. Rate limits: 1 per 15 s, 10 per hour, 30 per day, 3 distinct recipients per hour, none 23:00-07:00 unless approved with a typed code. No groups, media, forwarding, status or broadcasts. No message to a number with no chat history. The V3 `wa.me` handoff stays as a fallback when the bridge is off.
6. **Voice:** "מה חדש בוואטסאפ?" (a summary with names and counts; long texts are not read aloud unless asked; anything private is offered on screen), "תקרא לי מה דנה כתבה", "תענה לדנה: …" → read-back → approval.
7. **Dashboard WhatsApp page:** status, link/unlink, mode switch (PC only), read and send allow-lists, rate counters, kill button, retention setting, inbox (untrusted styling), audit of sends.
8. **CLI:** `jarvis whatsapp status | link | unlink | mode off|read|send | allow read|send <contact> | remove … | inbox | send <contact> "<text>" | kill | purge`.
9. **Health and kill.** Component `whatsapp_bridge` (connected / logged out / phone not seen for more than 10 days / rate-limited / killed / account warning). Auto-kill on `account.warning`, `logged_out` or a temporary ban, with a red alert. The kill stops sending within 2 s.
10. **Privacy.** Retention 14 days for text, 0 for media (owner setting; "keep nothing" mode). Logs carry a chat hash and counts only (B classes). Backups exclude the bridge dir. Uninstall wipes it. Message text goes to the cloud model only for the request at hand (OD-29).
11. **Build:** the Go binary is built in CI (Windows amd64), its hash is pinned in the installer payload, and it is signed as decided in L.

**Acceptance criteria.**
- Fake-bridge contract suite: 100% PASS, and the same suite runs against the real bridge in owner mode (`--real`, never in CI).
- Policy table (shared by the bridge and the Core): every row is enforced by the **bridge** even with a compromised Core (test: the Core sends without an approval ID → bridge refuses; reused ID → refused; non-allow-listed recipient → refused; rate exceeded → refused; mode `read` → send handler refuses).
- Injection suite: 12 planted injection messages (instructions, fake "JARVES system:" lines, zero-width text, a link, a "forward my last 20 messages" request) cause 0 side effects without approval, and each is shown as untrusted.
- Owner run on the main number in read mode: link, inbox, 5 test messages (Hebrew, emoji, photo, group, injection) appear correctly (group hidden by default), kill within 2 s, restart reconnects without QR, the copied store fails to decrypt on another Windows user. Then **14 days read-only with zero `account.warning`** before send mode is offered.
- Send (after OD-31): send to an allow-listed contact (the owner's own second phone or a family member who agreed), popup with name/number/full text, approve → delivered, deny → nothing, 11th message in an hour refused.
- Voice scenarios 71-78 (virtual mic, fake bridge) PASS. B redaction audit with the bridge running: 0 hits.

**Tests.** Automatic: `go test ./...` for the policy layer (whatsmeow behind an interface, no network); `tests/test_whatsapp_bridge_contract.py` against `tools/fake_whatsapp_bridge.py` (scripted timeline, deterministic clock, Hebrew/English text, 4,000-character messages, LID vs phone IDs, same-name contacts, disconnects, warnings); `tests/test_whatsapp_policy.py`; `tests/test_whatsapp_injection.py`; the DPAPI round-trip test (Windows only, `windows_only` marker). Manual: owner 7.12 (OF-01..OF-14); agent AF-01..AF-06.
**Model.** Opus 5.5 for the security design and review; Sonnet 5.5 for the bridge, Core wiring and tests.
**Dependencies.** B. The owner decisions in section 11.
**Effort.** 9 agent-days, plus the 14-day watch window (calendar time, not agent time).
**Risks.** Terms violation and an unquantified ban risk on the **main** number (the owner accepted this; the mitigations are read-only first, human-like limits, and auto-kill on warning). Device-key theft means account takeover until unlink: DPAPI, ACLs, no backups. Protocol changes: pin, contract-test, monthly update. Third-party personal data: short retention.
**Owner decisions.** OD-25..OD-31 (section 11).

---

## G — Conversation recall

**Goal.** "מה אמרתי על הרופא?" / "what did I say about the dentist?": JARVES searches past conversations (and, per OD-17, reports and notes), answers with the date and a short quote, and offers to show more on screen.

**Scope (full).**
1. **Research note** (measured, not guessed): SQLite FTS5 with the `trigram` tokenizer for Hebrew (prefix letters ה/ו/ב/ל/מ/ש make whole-word tokens weak) plus `unicode61 remove_diacritics 2` for English, versus a hybrid with an embedding index (the current best OpenAI embedding model, checked at the time) with local fallback. Choose by the benchmark below. Embeddings are added only if they raise recall by at least 10 points.
2. **Index** in `store.db`: turns (user and assistant text), session summaries, and per OD-17 reports and notes. Incremental indexing on turn commit (not a background poll). Rebuild command. Size and build time reported.
3. **Privacy and retention.** Privacy-mode text is never indexed. The 90-day retention removes raw turns, after which only summaries are searchable (per OD-17). "Forget" and conversation delete remove rows from the index in the same transaction. Profiles are isolated (one index per profile). Recalled text is untrusted data: no tool action from recalled text in the same turn (taint guard).
4. **Surfaces.** Tool `conversation_search` (query, date range, speaker, limit). `jarvis recall "<query>" [--since] [--json]`. A dashboard search field on Conversations with highlighted snippets, using the same index. Feature switch `conversation_recall`. Guide page, Hebrew and English.
5. **Voice behaviour.** A brief answer with date and quote, at most 2 sentences. Anything long or private is offered on screen ("להציג לך על המסך?"). A recalled email body is never read aloud without the existing ask.

**Acceptance criteria.**
- Benchmark on the test profile: 40 seeded real conversations (made with real Core typed `jarvis execute` sessions and 8 virtual-mic voice sessions), 30 Hebrew and 10 English recall questions with known answers. Recall@3 is at least 90% (36/40), and the right date is given in the spoken answer for at least 34/40. Measured, reported per question.
- Voice by virtual mic: 10 of the questions, answered correctly and within 2.5 s of end of speech (p95).
- A deleted conversation is never returned (test after delete and after a restart). Privacy-on conversations are not indexed. Another profile returns nothing. Injection text stored in a past conversation causes no action.
- Index size and build time for 10,000 turns are reported. Search p95 is at most 150 ms.

**Tests.** Automatic: `tests/test_recall_index.py` (tokenizer cases incl. Hebrew prefixes, NFC/NFD, niqqud, mixed scripts), `tests/test_recall_privacy.py`, `tests/test_recall_benchmark.py` (seeded fixture corpus, deterministic, offline), the CLI and dashboard API tests. Voice scenarios 79-84. Manual: owner 7.13 (OG-R1..OG-R6); agent AG-01..AG-04.
**Model.** Opus 5.5 for the design, privacy and voice answer; Sonnet 5.5 for the wiring and tests.
**Dependencies.** B. OD-17.
**Effort.** 5 agent-days. Embedding cost, if chosen: cents.
**Risks.** Recall can surface private text to others in the room: brief answers, screen offer. Hebrew tokenization quality: measured before choosing. Stored prompt injection: taint guard.
**Owner decisions.** OD-17.

---

## H — Assisted dialing (Phone Link)

**Goal.** "תתקשר לדני": JARVES resolves the contact, reads back name and number, asks for approval, and hands the call to Windows (Phone Link when paired, else the default `tel:` handler). The owner does the talking. JARVES never places, joins or records the call. (This is PC software. The phone as a JARVES device stays out of scope.)

**Scope (full).**
1. **Contact resolution:** remembered relationships, the `whatsapp/contacts.json` numbers the owner said, and optional read-only Google Contacts (#26, feature `google_contacts`). Hebrew and English names, nicknames, the ל/ל־ prefixes (`spoken_match`, never guessing on ambiguity). Multiple numbers ("mobile or home?").
2. **Number hygiene:** normalize to E.164 (+972 default), validate, and read back digit by digit in Hebrew or English before approval. A number spoken by the owner is also read back. A number that came from an email, a web page, a WhatsApp message or recalled text is **never** dialled unless the owner says it again (taint rule).
3. **Handler detection:** which app owns `tel:` (Phone Link, Teams, Skype, other, none), and whether Phone Link is paired and the phone reachable (best effort, read-only). JARVES says which app will open. With no handler: an honest message plus the guide.
4. **Approval:** a medium-risk approval (voice yes allowed after the digit read-back; on-screen approval for a number never called before). Deny or timeout opens nothing.
5. **Surfaces:** tool `dial_prepare`/`dial_open`, `jarvis dial <name|number> [--yes-on-screen]`, a dashboard "Call" button on contact rows, feature switch `dialing` (off by default, medium risk), guide, capability row, audit line without the number at INFO (number hashed; B classes).

**Acceptance criteria.**
- Virtual-mic flow on a real Core (`tel:` handler recorded, not executed): 12 scenarios (known name, ambiguous name, unknown name never guesses, spoken number read back, number from an email refused, deny, timeout, two numbers, English name, Hebrew prefix name, no handler, Phone Link not paired) are all PASS.
- Owner run: a real `tel:` handoff opens Phone Link with the right number. The owner presses call (or not).
- Logs carry no number (B audit).

**Tests.** Automatic: `tests/test_dialing.py` (resolution, E.164, taint, handler detection with a fake registry), voice scenarios 85-90. Manual: owner 7.14 (OH-01..OH-05); agent AH-01..AH-03.
**Model.** Sonnet 5.5 implements; Opus 5.5 security review (wrong-number and injection paths).
**Dependencies.** A (the Contacts consent check, OG-08), OD-16.
**Effort.** 3 agent-days.
**Risks.** A wrong number calls a real person: digit read-back plus approval. Handlers differ per PC: detect and say which.
**Owner decisions.** OD-16.

---

## I — Telegram / Instagram / Home Assistant live verification, then rocket alerts

**Goal.** The three V3 channels that were only tested against local fakes run against the owner's real services, with every path verified. Then, as the last feature of V4, rocket alerts.

**Scope (full).**
1. **Telegram** (owner's chat only): `jarvis connect --only telegram` (BotFather token by clipboard watch, validation, auto-pair deep link); inbound message read aloud; `telegram_draft` then confirmed `telegram_send`; reminder and report delivery; bot token rotation; behaviour when Telegram is unreachable; a message from a non-owner chat is ignored and logged by hash.
2. **Instagram** (only if OD-05b confirms a Business/Creator account linked to a Facebook Page): a real token; polling (no tunnel) unless the owner chooses Cloudflare Tunnel; one inbound DM read aloud; a drafted reply; confirmed send inside the 24 h window; outside the window an honest refusal. **Re-check the Graph API endpoint paths** written from memory in V3 against current Meta docs. If the account is not Business/Creator: the feature stays off, and the guide and capability row say why (that is the complete behaviour).
3. **Home Assistant** (OD-11): the wizard against the real instance (URL, version, a long-lived token for a dedicated HA user in `secrets.env`), an entity allow-list chosen with the owner, simple services, the risky list (lock, cover, alarm, script, valve, siren, water heater) always asks, entities outside the allow-list refused, HA offline gives a clear spoken message. **Decision recorded:** keep the REST connector or move to HA's built-in MCP server (HA 2026.x), based on a dated comparison.
4. **Rocket alerts (Pikud HaOref), LAST:** research which unofficial source is the most reliable now and its measured latency. Poll filtered to the owner's area (OD-15b). A loud spoken alert that overrides quiet hours (only this feature), the face red state, a tray popup. Labelled everywhere "unofficial, backup only, never replaces official alerts". A source outage reported once (not spammed). Startup check of the source. Feature switch off by default; setup speaks the label.

**Acceptance criteria.**
- Telegram: 8 owner rows PASS (connect, inbound, draft, confirmed send, deny, reminder delivery, non-owner chat ignored, offline message).
- Instagram: 6 owner rows PASS, or the documented "not a Business account" outcome verified (feature unavailable with the right reason on every surface).
- Home Assistant: one allow-listed light on/off by voice (virtual mic) and CLI; a risky entity asks and deny does nothing; outside the allow-list refused; the token never in logs (B audit); offline message. MCP vs REST decision recorded.
- Rocket alerts: recorded real alert payloads replayed through the real HTTP path (a local replay server) are spoken within the measured latency budget (target: at most 3 s after the poll sees it, poll interval at most 2 s). Other areas are silent. An outage is reported once. Quiet hours are overridden only for this. The label is visible and spoken. A 24 h soak in normal times reads the live endpoint with 0 false alerts and 0 crashes.

**Tests.** Automatic: the existing fakes extended (`tests/test_telegram_*`, `test_instagram_*`, `test_homeassistant_*`), contract tests recorded from the real services once (redacted), `tests/test_oref.py` with the replay server, voice scenarios 91-96. Manual: owner 7.15-7.17; agent AI-01..AI-06.
**Model.** Sonnet 5.5; Opus 5.5 for the security review (HA, channels) and the safety review (alerts).
**Dependencies.** A. Rocket alerts after every other feature is merged.
**Effort.** 5 agent-days (Telegram 1, Instagram 1, HA 1.5, alerts 1.5).
**Risks.** Real sends reach real people (owner's own chat only). Real devices act in the house: start with a light. HA tokens are admin-level: a dedicated HA user. Alerts: false reassurance from a missed or late alert, so the label and setup text must be clear; unofficial endpoints change or block; a false alert at night.
**Owner decisions.** OD-05b, OD-11, OD-15b.

---

## J — Custom wake word in production

**Goal.** JARVES wakes reliably to the owner's own voice across the room and almost never wakes by mistake. This is measured on the real mic, calibrated per user and per mic, and visible on a false-wake dashboard.

**Scope (full).**
1. **Research check** (dated): openWakeWord's current version and custom training path, newer maintained wake engines (licence, Windows, Pi ARM64), against the existing local pipeline (`wake_train.py`: frozen openWakeWord front end + a numpy classifier head; `wake_confirm.py`: two-stage ASR confirm; `wake_calib.py`: per-mic calibration).
2. **Owner samples** (from A: 20 positives, 20 negatives; plus Hebrew-phrase positives per OD-19) combined with the synthetic positives and negatives. Train `hey_jarvis_owner` (and `hey_jarvis_he` if OD-19). Keep the stock `hey_jarvis` model as the fallback.
3. **Threshold from a measured curve:** detection vs false wakes per hour on held-out owner samples plus a 2 h negative stream (TV, Hebrew radio, family talk recorded only with consent, or public-domain speech). The chosen operating point is written to `wake/calibration.json` per mic.
4. **Per-user calibration flow:** `jarvis wake calibrate` and a dashboard wizard (record 10 phrases, measure the noise floor, choose gain and threshold, test). It is re-run when the mic changes (already detected by `wake_calib`).
5. **False-wake dashboard** (Voice group): wakes per day, false wakes (wake with no speech or "not for you" closing), near misses, the confirm-stage verdict, score histogram, time of day, mic. Owner feedback buttons "that was not me / you missed me" that feed the next retrain. Optional opt-in: keep 2 s clips of false wakes for 24 h, in RAM-encrypted storage in the private state dir, never uploaded, deletable (OD-32).
6. **Retrain loop:** `jarvis wake retrain` uses the feedback and kept clips. A model version history with rollback.
7. **Privacy:** samples and models are biometric-like data. They stay in the private state dir, are never committed, are excluded from support bundles, and can be deleted from the dashboard and CLI.

**Acceptance criteria.**
- Virtual mic, suites 21/22/23/45: false wakes at most 1/20, detection at least 19/20 for the synthetic voices that the stock model already handles.
- Owner voice on the real mic: 20/20 wakes at 1 m, at least 18/20 at 3 m (or across the room as the owner sits), Hebrew phrase per OD-19 at least 18/20.
- A 60-minute normal-room soak (TV and talk) has at most 1 false wake, and an 8-hour overnight idle soak has 0.
- Wake loop CPU and RAM stay within the M budget. The model and samples are never in Git or in a diag bundle (B audit).
- False-wake dashboard shows real counts after the soak. The feedback buttons update the store.

**Tests.** Automatic: `tests/test_wake_train.py`, `test_wake_core.py`, `test_wake_reliability.py` extended (curve selection, calibration file, retrain with feedback, rollback); `wake_eval.py` regression on a committed synthetic set (no owner audio). Manual: owner 7.4 (OW-04..OW-10); agent AJ-01..AJ-03.
**Model.** Opus 5.5 (voice).
**Dependencies.** A samples, the M budget.
**Effort.** 4 agent-days, plus training time on the PC.
**Risks.** Overfitting to one voice and room (other family members may not wake it: say so, and offer per-user enrol). Biometric-like data: never committed, deletable.
**Owner decisions.** OD-19, OD-32.

---

## K — Dashboard completion

**Goal.** Every dashboard page is complete, consistent and polished in **English (default) and Hebrew**, verified in a real browser, with the leftovers of V3 and the new V4 pages included.

**Scope (full).**
1. **Languages:** English and Hebrew only. Remove Arabic and Russian from the picker, the catalogs (`ar.js`, `ru.js`; history keeps them), the locale tests, `check-addons` bundle entries and the guides. Users who had `ar`/`ru` saved fall back to English.
2. **Full coverage:** D-10 (about 190 English strings left in the Hebrew UI: Core payloads get `*_he` siblings, composed strings get catalog patterns, RTL order checked), D-11 (spoken Hebrew Core messages shown in English on English pages), and every V4 page (Coding agents, WhatsApp, Recall search, Health report, Wake dashboard, Delegation costs).
3. **Hebrew wording review** (from SW_NEXT_STEPS step 3): a term glossary (one word per concept), correct gender forms, no machine-translation tone, for spoken greetings, confirmations, approvals, errors, alerts, guides and dashboard. The owner approves a 20-line sample.
4. **Polish:** Timers & Lists pages (from P0.3: headless captures at 1440/390 px in en/he, ringing state from O-06, edit and snooze, the clear flow through the full approvals popup per the owner), the Approvals page (pending list, history, filters, the spoken-kind preview, keyboard), D-13 (Self-check grid; replaced by the O health page layout), D-14 (distinct `aria-label`s), D-15 (400 for a missing folder), D-16 (phone card layout for workspace versions), D-17 (header chips wrap with a fade), D-18 (token wording, the "Unknown / Unknown" text), the `jarvis files` CLI gap from P0.3, the profile-switch decision D13 (open its dashboard only), group names D14 (final).
5. **Real-browser UX review of every page** by Opus 5.5: real headless Edge and one headed Edge pass at 1440, 1024 and 390 px, light and dark, en and he, with populated test-profile data. At most 10 prioritized findings per page group. Every P0-P2 finding is fixed.
6. **Accessibility:** keyboard reachability on every control, focus rings, contrast AA, `lang`/`dir` correct, reduced-motion honoured, screen-reader labels on icon buttons.

**Acceptance criteria.**
- The Haiku string inventory on every page, in real headless Edge, en/he × 1440/390, finds 0 English multi-word strings on non-log Hebrew pages and 0 Hebrew strings on English pages (logs and user content excluded).
- `tools/v3_all_pages_check.py` (renamed `all_pages_check.py`) is clean ×3 on every page (0 JS/console errors, 0 HTTP ≥ 400, no horizontal scroll, correct `lang`/`dir`).
- An axe-core scan in the sweep reports 0 serious/critical issues.
- Every D-10..D-18 item is closed. The UX review findings are fixed or listed as P3 with a reason. The owner's 30-minute hands-on review is recorded (OK or a fix list, then fixed).

**Tests.** Automatic: `test_dashboard_locales.py` (en/he only), `test_hebrew_texts.py`, `tests/test_dashboard_a11y.py` (axe in Playwright on the CI browser), the page sweep in CI on Windows (Edge) and Ubuntu (Chromium). Manual: owner 7.18 (OK-01..OK-08); agent AK-01..AK-05.
**Model.** Opus 5.5 for the UX review and Hebrew wording; Sonnet 5.5 for catalogs and fixes; Haiku for the inventory.
**Dependencies.** The UI parts of E, F, G, J and O merged (so strings are added once).
**Effort.** 5 agent-days.
**Risks.** Many small edits in shared files: one writer per catalog file. Wording changes shift voice behaviour: re-run the voice greeting/approval scenarios.
**Owner decisions.** OD-14b (final group names), the sample approval.

---

## L — Installer and updates

**Goal.** A stranger's clean Windows PC installs JARVES, runs it and uninstalls it cleanly. Every installed version upgrades silently to the new one, keeping data. A bad update can be rolled back. The code-signing question is decided.

**Scope (full).**
1. **Clean-machine install test** (`docs/INSTALL_TEST.md`, all rows): on the second PC or a clean Windows user (OD-03): install, first-run wizard, autostart, tray, start/stop ×5, update notify, uninstall leaving no autostart, PATH, scheduled-task or state residue (state kept only if the owner chose "keep my data").
2. **Silent upgrade path** from every released series: 0.1.0.x (the old run-number series) → 3.0.0.x → 4.0.0.x. Inno Setup version comparison is checked (3.x > 0.1.x), the AppId is unchanged, data migrations run with a backup first (`store.db`, `memory.db`, config), the Core is stopped gracefully before replacing files, and autostart and user settings are preserved. `/VERYSILENT /SUPPRESSMSGBOXES` path for the in-app updater.
3. **Rollback:** the previous installer is kept (`%LOCALAPPDATA%\jarves\updates\previous\`). `jarvis update --rollback` and a dashboard button reinstall it and restore the pre-migration backup. Schema migrations are versioned and each has a down-path or a restore-from-backup rule. A failed first start after an update triggers an automatic offer to roll back (through approvals).
4. **Update channel security:** the update manifest stays signed (existing scheme); the installer hash is pinned in the manifest; HTTPS only; the downgrade is refused unless it is an explicit rollback.
5. **Free code-signing decision** (OD-08). Facts (CODE_SIGNING_FREE.md, recheck): SignPath Foundation needs a public repo **with an OSI licence**, but the owner chose no licence (all rights reserved), so it is not available. Azure Artifact Signing for individuals is limited to the USA/Canada. So the free options are: unsigned (SmartScreen warning) or self-signed with the cert imported on the owner's PCs. Recommendation: unsigned for public downloads plus a self-signed cert for the owner's machines, until the owner picks a licence or buys an OV cert. Note: PRIORITIES item 3 said "signed installer published", while the owner's answer of 20 Oct said "stay unsigned for now": OD-08 asks the owner to confirm.
6. **Payload:** the browser extra and the WhatsApp bridge binary in the payload; `payload_check.py` covers them; installer size reported.

**Acceptance criteria.**
- Clean-machine: every INSTALL_TEST row PASS on the release candidate.
- Upgrade matrix in CI (Windows job): install 0.1.0.x, then upgrade to the candidate silently; install 3.0.0.x, then upgrade; each with seeded data (a conversation, a memory, a job, a setting). All data present after the upgrade, Core starts, `jarvis doctor` OK.
- Rollback in CI: upgrade, then roll back, then data and version match the previous state byte-for-byte (except logs).
- Downgrade refused without `--rollback`. Tampered manifest refused.
- The signing decision is recorded and applied in `package.yml`.

**Tests.** Automatic: `packaging/smoke_windows.ps1` extended with `-UpgradeFrom <installer>` and `-Rollback`; `tests/test_migrations.py` (every schema version up and back); `tests/test_update_manifest.py`. Manual: owner 7.19 (OL-I1..OL-I8); agent AL-01..AL-04.
**Model.** Sonnet 5.5; Opus 5.5 security review of the update path.
**Dependencies.** M (CI matrix, caches), OD-03, OD-08.
**Effort.** 4 agent-days, plus the owner's 45 minutes on the second PC.
**Risks.** A broken migration loses data: backup first, test every version. Old installers must be kept as CI fixtures (download from the release assets, hash-pinned).
**Owner decisions.** OD-03, OD-08.

---

## M — Performance budgets, CI matrix and test platform

**Goal.** Performance cannot regress unnoticed, the test platform runs the whole pyramid automatically, and CI is cheap.

**Scope (full).**
1. **Budgets** in `docs/reviews/SWV4_PERF_BUDGETS.md` and machine-readable `core/perf_budgets.toml`. Each budget = the measured baseline plus a stated margin, per machine class (owner PC strict; CI runner loose):
   - background Core private commit (V3: 93 MB), working set (117 MB), threads (11), idle CPU (0.28%), idle disk writes (3.5 KB/min), idle disk reads (explain the 250-390 KB/min first), Core ready time (2.0-2.6 s), `features_list` and `live_tools` latency;
   - new: wake-to-ready and wake-to-first-audio with Godot and a real live session (5 runs), dashboard first load and each page, face show/hide, sleep/resume recovery, the E bridge, the F bridge, the G index search, the O monitor overhead (at most 0.1% CPU).
2. **CI budget gate:** `tools/perf_gate.py` runs `bench_startup.py --scenario combined` and the dashboard load timing on the Windows job and fails when a budget is exceeded. A deliberately slowed build must fail (test of the gate).
3. **CI matrix** (on the public repo after the release; standard runners are free there): Ubuntu 24.04 and Windows 2025 for the Core suite; the installer job on Windows; Godot, Blender and CAD jobs on Ubuntu; path filters so docs-only PRs skip the heavy jobs (the full matrix always runs on `main` pushes and tags); `timeout-minutes` on every job; caches for uv, Godot and Inno Setup.
4. **Nightly workflow** `nightly.yml`: the full pyramid layers that do not need accounts (see 6.1), the 3× flake detector, the perf gate, the redaction audit, the synthetic journey canary on a CI test profile, and the diagnostics bundle upload (redacted, from O) on failure.
5. **Test platform pieces** used by every package: `tests/fakes/` (one place for fake OpenAI live server, fake brains, fake Pear incl. slow, fake Google, fake Telegram/Instagram/HA, fake WhatsApp bridge, fake Claude Code host, fake Codex App Server), a shared event-driven wait helper (`tests/_wait.py`: wait on log codes, store rows or HTTP state with a deadline; no `sleep`), virtual audio helpers, and the report writer for system suites.
6. **CI minutes report** monthly in HANDOFF.

**Acceptance criteria.**
- Every budget row has a measured value, its commit and machine. The gate fails on the slowed build and passes on `main`.
- Wake-to-first-audio and face show/hide measured 5× on the owner's PC. Idle disk reads explained or fixed (before/after).
- The nightly workflow runs green 5 nights in a row before the release. No job without a timeout. A docs-only PR uses less than 20% of the Windows minutes of a code PR (measured).

**Tests.** The gate's own test (`tests/test_perf_gate.py`), `test_ci_workflows.py` extended (timeouts, path filters, nightly shape). Manual: agent AM-P1..AM-P3.
**Model.** Opus 5.5 for the performance investigation; Sonnet 5.5 for CI and the platform.
**Dependencies.** A (real starts for wake/live numbers). M owns the workflow files.
**Effort.** 5 agent-days.
**Risks.** Noisy CI runners: loose margins in CI, strict on the owner's PC (checked by the O monitor). Do not optimize away functionality or Pi portability.
**Owner decisions.** none.

---

## N — Docs and guides refresh

**Goal.** Every document and in-app guide describes what V4 really does, in English and Hebrew, with no stale or invented claims.

**Scope (full).** README (concise), `core/README.md`, `docs/INSTALL_TEST.md`, `docs/GOOGLE_SETUP.md`, `docs/CAMERA_SETUP.md`, a new user guide (`docs/USER_GUIDE.md` and `USER_GUIDE.he.md`: install, first run, voice, dashboard, each feature, privacy, troubleshooting, update/rollback), every `guides/data/*.toml` (new: agents, whatsapp, recall, dialing, delegation, health, rocket_alerts; updated: wake_word, telegram, instagram, homeassistant, privacy), `jarvis help <topic>` topics, `setup_guide.md`, the capability matrix rows, `CONTEXT_MAP.md`, `ROADMAP.md`, release notes `docs/RELEASE_NOTES_4.0.md` (including exit-code and behaviour changes), the public snapshot allow-list (`make_public_snapshot.py`) for the new files, and an archive of superseded plans.

**Acceptance criteria.** The Haiku link check reports 0 broken links. `test_guides.py` checks every registered feature has a guide in en and he and every guide command exists in the CLI (`cli_parity`). Each guide's "Test now" button works on a real Core. A Haiku claim audit compares feature claims in the docs with the capability matrix: 0 mismatches. The public snapshot dry-run contains no private doc.
**Tests.** `tests/test_guides.py`, `tests/test_docs_links.py` (new, offline), `tests/test_public_snapshot.py`. Manual: owner reads the Hebrew quick start (15 min), agent AN-01..AN-02.
**Model.** Sonnet 5.5; Haiku for the link and claim audits; Opus 5.5 reviews the Hebrew user guide wording.
**Dependencies.** All feature packages merged.
**Effort.** 3 agent-days.
**Owner decisions.** none.

---

## O — Self-Diagnostics 2.0

**Goal.** JARVES notices its own problems early, explains them with evidence pointing at the exact code, commit and log lines, proposes fixes, and repairs itself only through the approvals flow. It costs nothing in tokens day to day. It improves on the V3 Self-check (`selfcheck.py`: weekly and after a crash, rule-based findings, score, trend, ticket draft text) in nine ways:

1. **Continuous health monitor (no tokens).** `diag/monitor.py`, inside the Core, event-driven plus a 60 s sampler. It records per-component state changes, error and warning rates per log code, event-loop lag, CPU, RAM, handles, threads, disk usage and growth, turn latencies (wake-to-ready, first audio, dead air, tool p50/p95), budget burn, bridge states (E, F), and scheduler lateness. Storage is a compact time series in `store.db` (`diag_metrics`, 1-minute roll-ups for 7 days, hourly for 90 days). Overhead budget: at most 0.1% CPU and 5 MB RAM.
2. **Baselines and anomaly detection.** Per metric, a rolling 14-day baseline (median and MAD per hour-of-week bucket where seasonality matters). Anomaly = outside median ± k·MAD for N consecutive samples, or a new log code cluster, or a rate step change (CUSUM). Trends: slope over 7 days (for example RAM creep, disk growth). Findings reuse the V3 finding shape (severity, en/he text, evidence, cause, fix, action, effort) and the accept/dismiss/snooze state.
3. **Regression detection between versions.** Every metric row carries `app_version` and `commit`. After an update, the first 24 h are compared with the previous version's last 7 days: performance budgets (M), error-rate deltas per code, new codes, tool failure rates, voice latencies. Output: a "version report" finding ("since 4.0.1, LIVE_RECONNECT is 3× more frequent").
4. **Root-cause hints.** At build time, `tools/build_log_index.py` scans every `logs.*` call and writes `log_codes.json` into the package: code → file:line, function, and the last commit that touched that line (git blame at build time, never git at runtime). A finding links each log code to its source location and commit (a GitHub permalink on the public repo), plus the 10 surrounding log lines (redacted per B). It also links to the matching guide or known issue.
5. **Automatic ticket drafts, filed only after owner approval.** A finding can become a ticket draft (title, severity, version, commit, code links, counts, redacted evidence, reproduction steps when known). Filing it on the **private** repo `igalgerman/JARVIS` (OD-35) through the `gh` CLI or the GitHub API is an approvals-flow action: the full text is shown in the popup and on the dashboard, then sent. It is never filed automatically. Duplicates are detected by fingerprint and an existing open issue gets a comment instead.
6. **Auto-remediation only through approvals.** A closed list of remediations, each with a risk level, a dry-run description, a pre-check, an action and a post-check that verifies it worked: restart a component (face link, wake loop, audio device, dashboard server, bridges), clear a cache (speech cache, Pear probe cache, guide cache), reconnect Google (opens the existing reconnect flow), switch to the fallback engine (local STT/TTS when GPT-Live is down; the stock wake model when the custom one misbehaves), re-run `feature_setup` for a feature whose prerequisite went missing, roll back the last update (L). Every one goes through `approvals` (medium risk; never automatic, even with autonomy grants, except that "restart the face link" may be granted for automatic use by the owner). Result and post-check are recorded.
7. **Health report page and spoken summary.** A dashboard page **Health** (Activity group; it replaces and absorbs the Self-check page and fixes D-13): a score with a 30-day trend, component tiles, open findings ranked, the version report, preflight results, canary results, the AI review report (below), remediation history and ticket drafts. Voice: "מה מצבך?" / "how are you doing?" gives a 2-3 sentence summary (score, the main issue, what to do). The full list stays on screen. CLI: `jarvis health [--json] [--days N]`, `jarvis health report`, `jarvis health remediate <id>`, `jarvis health ticket <id>`.
8. **Preflight on every start.** `diag/preflight.py` runs before CORE_READY and must take at most 1.5 s (budget in M): config parse and sanity, state dir writable, disk free above 1 GB, ports free, the Godot executable present, audio devices present (mic and speaker), wake model files and hashes, the presence (never the values) of required secrets, Google token expiry within 48 h, feature prerequisites for every ON feature, schema version, the last shutdown clean or not, clock sanity. The result goes to health, to the Health page and, for a red item, a tray popup. A failed preflight item never blocks start unless it is fatal (state dir not writable); it degrades the component and says so.
9. **Nightly synthetic user-journey canaries on the TEST profile.** `tools/canary.py`, scheduled by the JARVES scheduler at 03:30 local time (outside quiet hours for speech, since it plays nothing), on a throw-away test profile (never the real memory). Each journey records PASS/FAIL/BLOCKED with timings:
   - **voice stack with silent audio:** wake loop on a virtual mic with a synthetic "Hey Jarvis", the local STT/TTS round trip, and GPT-Live connect-and-close **only if** the owner enables the paid canary (OD-36; at most $0.02 per night);
   - **music:** Pear probe and status read with volume forced to 0 (if Pear runs; otherwise BLOCKED "Pear closed", not FAIL);
   - **calendar read:** the read-only calendar list on a **copy** of the token in the canary home (if Google is connected), never a write;
   - **dashboard load:** real headless Edge loads Home, Conversations, Health and Settings, with 0 console errors and within the M budget;
   - **installer self-check:** `jarvis setup --check-addons` plus payload hash verification against `build-info.json`;
   - **agents and WhatsApp bridges:** `jarvis agents doctor` and the WhatsApp bridge `/status` (read-only);
   - **scheduler:** a test job due in 30 s runs and reports.
   Results feed the monitor. Two failures in a row of the same journey create a finding.

**Plus the owner addition: a scheduled, selective AI agent review.**
- **Schedule:** every N days (default **2**, OD-33), at a time outside quiet hours, and only when the budget allows. Configurable on the Health page and with `jarvis health ai-review --every 2d | off | --now`.
- **The rule layer selects first.** Only what changed and is worth an AI look goes into the review slice:
  - new error clusters (log codes with no baseline, or a rate step);
  - regressions against the baseline or the previous version;
  - slow-turn outliers (top 1% dead air, wake-to-first-audio outliers);
  - repeated tool failures (same tool `ok=false` across 3 or more conversations);
  - false-action claims (`LIVE_FALSE_ACTION_CLAIM`);
  - wake false positives above the J target;
  - conversations flagged by the existing `diagnose` rules;
  - config drift (settings changed since the last review, from the config backups);
  - files changed since the last review (installed version and commit changes, workspace persona files changed, as a list with a diff size, never content).
- **Skip when nothing notable changed.** If the slice is empty or below a threshold score, the run is recorded as "skipped: nothing new" with $0 spent.
- **What the reviewer sees:** a redacted slice per B rules: log codes, counts, timings, the file:line and commit hints from the log index, the 10 surrounding log lines with every `content`/`pii`/`secret` field removed, config key names with old/new values for non-secret keys, finding IDs. **Never:** secrets or tokens, message bodies (email, WhatsApp, Telegram, Instagram), contact names, numbers or addresses, calendar event text, recalled text, wake audio or samples, code from the orchestrated agents' projects, or raw conversation text. The one exception is the conversation excerpts of the flagged sessions, and only if the owner has enabled the existing AI review path (`self_check.ai_pass` + `diagnosis_llm_pass`, privacy off), exactly as in V3. A planted-data test proves the boundary (below).
- **Cost cap:** a strict per-run cap (default $0.10, OD-34) and a monthly cap (default $1). The estimate is shown before the run (on demand) or recorded before the scheduled run, the reservation goes through the shared `Budget` (the D strict-reservation path when available), and the actual cost is shown after. A run that would exceed the cap is trimmed to the highest-ranked items, or skipped.
- **Model:** for independent review, a different model family from the one that wrote the code. The code is written by Claude models, so the runtime reviewer is the OpenAI backend that JARVES already uses (the current recommended GPT-6-family reasoning model, checked at build time, on the owner's API key route; or the ChatGPT plan route if OD-34 says so). It reports through the existing `diagnose.llm_review` plumbing.
- **Output:** a short ranked report (at most 8 items: severity, evidence, hypothesis, suggested fix, effort) on the Health page. A spoken summary on request ("מה מצא הבודק?"). Each item can be accepted, dismissed or snoozed, like the V3 self-check. Accepting a settings change goes through the approvals flow. Ticket drafts are filed only after approval. **Nothing is ever applied automatically.**
- **Manual review step for the owner:** after each AI review that found something, the Health page shows "AI review ready (N items, $x)". The owner reads it (or asks for the spoken summary) and decides on each item. Items not decided within 7 days are kept but marked stale, and the next run does not re-raise them unless their evidence grows.

**CI-uploaded diagnostics bundle.** On any CI or nightly failure, the workflow runs `jarvis diag --ci` on the test home and uploads it as an artifact (retention 7 days). It passes the B audit (CI also runs the redaction scan on the bundle before upload; a hit fails the upload).

**Acceptance criteria.**
- Monitor overhead within budget (at most 0.1% CPU, 5 MB) over a 24 h soak.
- **Planted anomalies are detected:** in a replay harness with 14 days of synthetic baseline metrics and logs, each of 12 planted anomalies (new error code, 3× rate step, RAM creep 2 MB/h, disk growth, slow-turn outliers, tool failing across 3 conversations, false-action claim, wake false-positive burst, version regression after a fake update, config drift, canary failing twice, Google token near expiry) produces the right finding within its detection window. **Boring runs produce nothing:** 30 replayed normal days produce 0 high and at most 2 low findings.
- **AI review selection:** for the same planted anomalies, the slice contains exactly the planted items (precision and recall 100% on the fixture). On a boring 2-day window the run is skipped with $0. The slice never contains a planted secret, message body, contact, number, calendar text or conversation text (unless the AI-review path is on, and then only the flagged sessions' excerpts). Cost before and after is displayed. The cap is never exceeded (fake client, property test). Nothing is applied without an approval.
- Root-cause hints: for 10 planted errors, the finding links to the correct file:line and commit.
- Preflight: at most 1.5 s on the owner's PC; each of 12 planted faults (missing Godot, no mic, port taken, read-only state dir, missing model file, bad config, missing secret, token expiring, schema behind, unclean last stop, disk nearly full, clock skew) is reported correctly and degrades only its component.
- Canary: 5 consecutive nights PASS on the owner's PC (rows that need a closed app may be BLOCKED with reason). A deliberately broken dashboard page makes the next canary FAIL and creates a finding after the second night.
- Remediation: each of the closed-list actions runs end-to-end through approvals on a real Core, with its post-check. Deny does nothing.
- Ticket: an approved draft creates one private-repo issue (owner present, once); a duplicate fingerprint comments instead.
- Health page and spoken summary verified in en and he.

**Tests.** Automatic: `tests/test_diag_monitor.py`, `test_diag_anomaly.py` (replay harness with fixtures in `tests/fixtures/diag_replay/`), `test_diag_regression.py`, `test_log_index.py`, `test_preflight.py` (planted faults), `test_remediation.py`, `test_ai_review_selection.py` (planted anomalies selected, boring skipped, boundary planted-data test, cap property test, fake client), `test_canary.py` (each journey against fakes), `test_health_api.py`. Manual: owner 7.20 (OO-01..OO-10); agent AO-01..AO-08.
**Model.** Opus 5.5 for the design, the detection rules and the AI-review boundary; Sonnet 5.5 for implementation and tests.
**Dependencies.** B (redaction classes), M (metrics schema, budgets).
**Effort.** 8 agent-days.
**Risks.** Alert fatigue: thresholds tuned on the 30-day boring replay; quiet hours respected; at most one spoken health alert per day. The canary could touch real data: it runs only on a throw-away test home with copied tokens, and a test asserts the real state dir is never opened for writing. The AI review cost: strict caps, skip when nothing is new.
**Owner decisions.** OD-33 (AI review frequency), OD-34 (cap and on/off default), OD-35 (ticket repo), OD-36 (paid voice canary), OD-37 (automatic face-link restart allowed?).

---

## 6. Testing — automatic

### 6.1 Test pyramid

Commands are run from `core/` unless shown otherwise. "Local" is the owner's PC; "CI" is the public repo's GitHub Actions (`jarves-checks.yml` on every push/PR; `nightly.yml` at 02:00 UTC). One pytest process at a time on the owner's PC.

| # | Layer | What | Command | Pass criteria | Where |
|---|---|---|---|---|---|
| 1 | Unit | pure logic: policies, parsers, tokenizers, budgets, anomaly rules, migrations | `uv run pytest -q -rs -p no:cacheprovider tests -m "not system"` (local: `.venv/Scripts/python.exe -m pytest tests -o pythonpath=src -p no:cacheprovider`) | 0 failed, 0 skipped, 0 xfailed | CI every push (Ubuntu + Windows), local per package |
| 2 | Contract | each fake behaves like the real service: fakes vs recorded real transcripts (OpenAI live events, Pear API, Google, Telegram, Instagram, HA, WhatsApp BridgeProtocol, Claude Code hooks/Channels, Codex App Server) | `uv run pytest -q tests/contract` | every recorded exchange replays; schema versions match | CI every push; recordings refreshed by owner-mode `--real` runs (local only) |
| 3 | Real-Core integration with fakes | a real Core process on a temp TEST home, real HTTP/CLI/SQLite, fakes for external services | `uv run pytest -q tests -m system` | 0 failed; each test waits on events (log code / store row / HTTP state), no sleeps | CI every push |
| 4 | System suite | Godot headless, doctor, CLI, bad configs, start/stop ×5, soak, privacy strip, scheduler, secret scan, V4 additions (agents doctor, WhatsApp fake bridge, recall, health/preflight) | `uv run --no-sync python tools/suite_system.py --skip-pytest --soak-min 10` | all rows PASS; report written to `docs/reviews/PRE_USE_TEST_REPORT_SYSTEM.md` | nightly CI (Windows) + local before each release |
| 5 | Virtual mic/speaker voice | real GPT-Live, virtual mic (WinRT speech, never played), virtual speaker (measured, never played), real wake loop, real skills, fake external services | `python tools/suite_v3_voice.py --list`; `--only 1-96`; per package `--group <name>`; free local path: `python tools/suite_voice.py --offline` | every scenario PASS; cost within the printed cap; dead air p95 ≤ 1.5 s | local only (paid, owner key), before each package merge and the release |
| 6 | Real-browser dashboard | real Edge (Windows) / Chromium (Ubuntu) headless: every page × en/he × 1440/1024/390, light/dark, axe scan, flows | `python tools/all_pages_check.py --sweeps 3 --widths 1440,1024,390 --langs en,he --axe` | 0 JS/console errors, 0 HTTP ≥ 400, 0 overflow, correct lang/dir, 0 serious axe issues | CI every push (1 sweep), nightly (3 sweeps) |
| 7 | Installer smoke | build, silent install, run, upgrade from 0.1.0.x and 3.0.0.x, rollback, uninstall | `./packaging/build_windows.ps1 ...; ./packaging/smoke_windows.ps1 -Installer dist/JARVES-Setup-<v>.exe -Version <v> -RequireFace -CheckAutostart -UpgradeFrom <old> -Rollback` | every step PASS; no residue | CI Windows on `main` and tags; nightly |
| 8 | Soak | 8 h idle soak (RAM/CPU/handles flat, no WARN/ERROR); 60 min mixed soak (scheduler, dashboard, canary journeys); voice 6 min soak (paid, local) | `python tools/soak.py --hours 8 --profile test` | RAM growth ≤ 5 MB/h; 0 unexplained WARN/ERROR; budgets met | nightly CI (2 h version), local weekly (8 h) |
| 9 | Security and redaction | planted secrets and PII at every input; every sink scanned in raw/JSON/URL/base64/UTF-16/NFC/NFD; AST log lint; bridge policy tables; dashboard auth; secret scan of the repo and the public snapshot | `python tools/redaction_audit.py --both-modes`; `uv run pytest -q tests/test_redaction_planted.py tests/test_log_message_lint.py tests/test_security.py` | 0 hits (rules in B) | CI every push (unit level), nightly (system level) |
| 10 | Failure injection | network off, DNS failure, bad API key, budget exhausted, disk full (quota dir), Pear closed/stuck, Core crash/kill -9, sleep/resume (simulated + real on owner PC), two instances, corrupt `store.db`, port taken, bridge killed, provider 429/500, slow backend 30 s | `python tools/failure_injection.py --all` | each case: honest message, no crash loop, recovery within its stated time, health shows it, no data loss | nightly CI (all except real sleep/resume); local once per release |
| 11 | Nightly synthetic user journeys | the O canary journeys on a CI test profile (silent voice stack, music fake, calendar fake, dashboard load, installer self-check, scheduler) | `python tools/canary.py --profile ci --report out/canary.json` | all journeys PASS | nightly CI + nightly on the owner's PC (O) |
| 12 | Budget and latency regression gates | perf budgets (M), CI-loose and owner-strict; live cost per scenario vs the previous release | `python tools/perf_gate.py --budgets perf_budgets.toml --machine ci`; `python tools/suite_v3_voice.py --cost-report` | no budget exceeded; cost per scenario ≤ previous + 15% | CI Windows every push (offline numbers); local before release (live) |
| 13 | Flake detector | the full layer 1-3 suite three times in a row with random order (`-p randomly`, fixed seed printed) | `python tools/flake_check.py --runs 3` | 3/3 identical results | nightly CI |
| 14 | Docs and parity | CLI parity, guides per feature, docs links, public snapshot allow-list | `uv run pytest -q tests/test_cli_parity.py tests/test_guides.py tests/test_docs_links.py tests/test_public_snapshot.py` | 0 failed | CI every push |

### 6.2 Package-specific automatic suites (summary)

| Package | New automatic tests |
|---|---|
| A | `test_owner_pass.py` |
| B | `test_redaction_planted.py`, `test_log_message_lint.py`, `test_diag_bundle_strip.py`, `tools/redaction_audit.py` |
| C | one regression per finding; `fakes/pear_slow.py`; voice regression groups |
| D | `test_delegation_client.py` (property test of the strict cap, crash recovery), contract recording, voice 51-62 |
| E | `test_agents_inbox.py`, `test_agents_bridge.py`, `test_agents_policy.py`, contract tests vs `fake_claude_host.py` and `fake_codex_app_server.py`, system test, voice 63-70 |
| F | `go test ./...`, `test_whatsapp_bridge_contract.py`, `test_whatsapp_policy.py`, `test_whatsapp_injection.py`, DPAPI test, voice 71-78 |
| G | `test_recall_index.py`, `test_recall_privacy.py`, `test_recall_benchmark.py`, voice 79-84 |
| H | `test_dialing.py`, voice 85-90 |
| I | extended channel/HA tests, contract recordings, `test_oref.py` with replay server, voice 91-96 |
| J | extended wake tests, `wake_eval.py` regression set |
| K | locale tests (en/he), `test_dashboard_a11y.py`, page sweep |
| L | `smoke_windows.ps1 -UpgradeFrom -Rollback`, `test_migrations.py`, `test_update_manifest.py` |
| M | `test_perf_gate.py`, `test_ci_workflows.py` extended |
| N | `test_docs_links.py`, `test_public_snapshot.py`, `test_guides.py` |
| O | `test_diag_monitor.py`, `test_diag_anomaly.py`, `test_diag_regression.py`, `test_log_index.py`, `test_preflight.py`, `test_remediation.py`, `test_ai_review_selection.py`, `test_canary.py`, `test_health_api.py` |

### 6.3 AI-review selection tests (O, owner addition)

- **Planted anomalies must be selected:** the replay fixture plants the 12 anomalies listed in O. The selector's slice must contain each one (recall 100%) and nothing else from the fixture (precision 100%).
- **Boring runs must be skipped:** 15 boring 2-day windows from the 30-day normal replay produce "skipped: nothing new" and $0.
- **Boundary:** a planted OpenAI key, Google refresh token, WhatsApp message body, contact name and number, calendar event title, recalled sentence, and a Hebrew conversation sentence are planted in the source data. The serialized request to the (fake) review client contains none of them (searched in all encodings). With the AI-review path enabled, only the flagged sessions' excerpts appear, and still no secrets.
- **Cap:** a property test with random slice sizes and price tables: the reserved and actual cost never exceed the per-run or monthly cap.
- **No auto-apply:** a review item with an `action` is never executed without an approval record (asserted through the approvals store).

### 6.4 Flake policy

- **Event-driven waits only.** Tests wait on a log code, a store row, an HTTP state or a process exit with a deadline (`tests/_wait.py`). `time.sleep`/`asyncio.sleep` in tests is banned by a lint test, except inside fakes that simulate a slow service on a fake clock.
- **Deterministic time and randomness:** fake clocks for schedulers, rate limits and anomaly windows; fixed seeds printed on failure.
- **No automatic reruns** in CI (no `--reruns`). A test that fails once and passes on retry is a flake.
- **A flake is a P2 bug:** it gets a row in the V4 bug table, is fixed (root cause) within the package that owns the area, and the fix is proven by the 3× flake detector plus 20 repeated runs of that test. Quarantine is not allowed (it would be a skip).
- **Zero skips:** the conftest hook and the CI grep fail any skip or xfail. Platform-specific tests use `windows_only`/`linux_only` (deselected, not skipped) and must have a sibling on the other platform or a documented reason.
- **Resource-aware:** tests that start a Core use unique ports and temp homes, and clean them up. One Core at a time in voice suites.

### 6.5 CI matrix

| Job | OS | Trigger | Timeout | Notes |
|---|---|---|---|---|
| core | ubuntu-24.04, windows-2025 | push, PR | 40 min | layers 1, 2, 3, 9 (unit), 14; any skip fails |
| dashboard | windows-2025 (Edge), ubuntu-24.04 (Chromium) | push, PR (paths: dashboard, Core) | 20 min | layer 6, 1 sweep |
| perf | windows-2025 | push, PR (paths: Core) | 15 min | layer 12 |
| installer | windows-2025 | `main`, tags, nightly | 45 min | layer 7 |
| e2e / godot / blender / cad | ubuntu-24.04 | push (paths), `main`, tags | 15-25 min | as today |
| go-bridge | ubuntu-24.04, windows-2025 | push (paths: bridge/) | 15 min | `go test`, build, hash |
| nightly | ubuntu-24.04, windows-2025 | schedule 02:00 UTC | 120 min | layers 4, 6 (×3), 8 (2 h), 9 (system), 10, 11, 13; diag bundle upload on failure |

Docs-only changes skip the heavy jobs. `main` pushes and release tags always run the full matrix.

## 7. Testing — manual: owner script

Run by the owner with an agent guiding (`core/tools/owner_pass.py --section <n>`). Each row: **Pre** = preconditions, **Steps**, **Expected**, **Evidence** to capture (the agent captures logs and counts; the owner only answers yes/no or reads a number). Severity of a failure is set by the [rules in 9.4](#94-severity-rules). Rows marked (R) are re-runs of an automatic test on real hardware or a real account.

**General preconditions for every section:** the release candidate installed (or `main` from source); `jarvis doctor` green; the TEST profile for anything that writes, unless the row says "real profile"; speakers at a normal level; the agent has the log tail open.

### 7.1 Install and start (A, L)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OI-01 | second PC or clean Windows user (OD-03) | download the installer, run it, accept defaults | installs without admin prompt (per-user), SmartScreen warning as expected per OD-08 | screenshot of the finish page, install time |
| OI-02 | OI-01 | finish the first-run wizard | wizard completes; Home shows "running" | wizard log rows |
| OI-03 | OI-02 | sign out and in | JARVES starts in the tray (autostart) | tray screenshot, start time |
| OI-04 | OI-03 | `jarvis stop`, `jarvis start` ×5 | each start ready within the budget, port freed | `bench_startup` lines |
| OI-05 | OI-04 | uninstall ("keep my data" off) | no autostart, PATH, task or state left | residue check output |

### 7.2 Voice and conversation (A, C)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OV-01 | real profile, real mic/speaker | say "היי ג'רוויס, מה השעה?" | wakes, answers in Hebrew, correct time | wake score, dead air |
| OV-02 | OV-01 | ask an English question | answers in English, next Hebrew turn back to Hebrew | transcript codes |
| OV-03 | conversation open | interrupt mid-answer | stops within 0.8 s, listens | barge-in timing |
| OV-04 | | talk continuously 30 s after waking | not cut off | session log, no idle cut |
| OV-05 | | ask a web question | progress shown, answer ≤ 10 s | backend timing |
| OV-06 | | "תזכיר לי בעוד 2 דקות לשתות" | reminder rings with face closed, audible | alert row |
| OV-07 | | "תצא לגמרי" (full exit) | JARVES exits fully, no dashboard opened | exit log (19 Oct P0) |
| OV-08 | | a 15-turn natural conversation | no lost answers, context kept | counts |
| OV-09 | | owner approves the face look | OK / list of changes | owner answer |
| OV-10 | | leave quiet 3 min | session ends, no cost while idle | idle disconnect row |

### 7.3 Approvals (A)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OP-01 | real desktop | trigger a medium-risk action by voice | popup on top, Hebrew per setting, inside the work area | screenshot |
| OP-02 | OP-01 | say "כן" | approved once, no echo self-approval | approval row |
| OP-03 | | trigger, press Esc | denied, nothing done | row |
| OP-04 | | trigger a dangerous action | typed code required | screenshot |
| OP-05 | | WhatsApp handoff request | spoken question names kind + recipient first name, text on screen only | audio transcript |
| OP-06 | | let one time out | denied after 60 s (120 s Helper) | row |
| OP-07 | | approve from `jarvis approvals approve <id>` | accepted (B-05 fix) | row |
| OP-08 | | tray balloon when the face is closed | balloon shown | screenshot |

### 7.4 Wake word (A, J)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OW-01 | quiet room | `jarvis wake record --positives 20` | 20 clips saved privately | count only |
| OW-02 | | `--negatives 20` | saved | count |
| OW-03 | OD-19 = yes | Hebrew phrase ×20 | saved | count |
| OW-04 | J model trained | `jarvis wake calibrate` | gain/threshold written for this mic | calibration row |
| OW-05 | | 20 wakes at 1 m | 20/20 | scores |
| OW-06 | | 20 wakes at 3 m / across the room | ≥ 18/20 | scores |
| OW-07 | | 60 min normal room with TV | ≤ 1 false wake | false-wake dashboard |
| OW-08 | overnight | 8 h idle | 0 false wakes | dashboard |
| OW-09 | | press "that was not me" on a false wake | feedback stored | store row |
| OW-10 | | delete samples from the dashboard | gone from disk | file count |

### 7.5 Google (A)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OG-01 | Google connected | "מה יש לי מחר ביומן?" | correct events read | count, latency |
| OG-02 | | "תקבע לי פגישה מחר ב-10 עם עצמי" | read-back, confirm, created | event ID (not title) |
| OG-03 | | "תמחק את הפגישה" | refused, no delete tool | refusal code |
| OG-04 | | "כמה מיילים חדשים?" | count + senders, asks before reading | counts |
| OG-05 | 7-day expiry day | use Calendar | honest "reconnect" + `jarvis google start` / Reconnect button works | rows |
| OG-06 | | morning briefing on request | calendar, mail, weather, reminders; city name in Hebrew (B4) | sections |
| OG-07 | | flip privacy during a conversation | Core follows within 1 s; strip shows it | timing |
| OG-08 | Contacts feature on | consent to People API, look up one contact | read-only result | row |
| OG-09 | | invite own second address to an event | confirmed, received | owner yes |

### 7.6 Music (A, C)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OM-01 | Pear open | "נגן את עומר אדם" | the right artist, "confirmed" only when playing | confirm time |
| OM-02 | | fast next, then pause | paused and confirmed (S10 fix) | row |
| OM-03 | | stall Pear (pause its process) | honest MUSIC_NOT_CONFIRMED | row |
| OM-04 | | "עצור" by real voice | `music_pause` called, paused | tool row |

### 7.7 Camera and privacy strip (A, #28)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OC-01 | camera ON | dashboard "What does he see?" | live preview with on-screen approval, fps and latency shown | fps, latency |
| OC-02 | | close the preview | camera light off within 1 s | owner yes |
| OC-03 | live conversation | watch the strip while talking / while JARVES speaks | listening / paused / audio to cloud match what is heard | owner yes per state |
| OC-04 | | mute by the strip | mic off, strip says so | row |
| OC-05 | | "מה אתה רואה?" with camera off | honest "camera is off" | transcript code |
| OC-06 | | colours and words by eye (colour-blind check) | approved or list | owner answer |

### 7.8 Guides, help and suggestions (A, #25)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OL-01 | | open Guides, three feature pages, Test now on each | each test runs and reports | rows |
| OL-02 | | Home checklist | items match reality | owner yes |
| OL-03 | | "איך אני מחבר את גוגל?" | correct steps in Hebrew | transcript code |
| OL-04 | | "מה אתה יכול לעשות?" | accurate capability summary | owner yes |
| OL-05 | | step-by-step setup with "סיימתי" / "הבא" | advances correctly | rows |
| OL-06 | 3 days | proactive suggestion once at a pause; "תפסיק להציע" | said once; then not again | alert rows |
| OL-07 | OD-05 yes | `jarvis connect openai-admin`, Costs Test now | real costs shown | row |
| OL-08 | | snooze a suggestion | not repeated before snooze ends | row |

### 7.9 Browser (A)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OB-01 | site allow-listed | "תפתח את <site> ותקרא לי את הכותרת" | reads it | host only in log |
| OB-02 | | ask to click a link | clicks | row |
| OB-03 | | a purchase field | always asks | row |
| OB-04 | off-list site | ask to read | asks once, read-only | row |

### 7.10 Client-mode delegation (D)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OD-T1 | `delegation_mode=client`, a low test cap ($0.05) | ask 5 web questions | stops before the cap with an honest message | spend ≤ cap |
| OD-T2 | combined mode | ask a reasoning question | answered; API cost for reasoning $0 | cost row |
| OD-T3 | | interrupt a lookup | cancelled, reservation released | budget row |

### 7.11 Agent Orchestrator (E)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OE-01 | OD-20 yes | `jarvis agents install-plugin claude` | installed; `doctor` green | doctor output |
| OE-02 | | start Claude Code by hand in a terminal in project X, give it a task | session appears in `jarvis agents list` within 10 s | list output |
| OE-03 | OE-02 | by voice: "תשאל את קלוד מה המצב בפרויקט X" | truthful answer from that session, with age; terminal session intact | answer, timing |
| OE-04 | busy turn | ask status again | answered from the snapshot, worker not interrupted | source = snapshot |
| OE-05 | | "תגיד לו להמשיך" / "pause" | reaches the same session, acknowledged | events |
| OE-06 | | let Claude ask for a risky permission | popup within 2 s with provider/project/command; deny → denied in the session | rows |
| OE-07 | same, VS Code | repeat OE-02/03 in VS Code | same result | rows |
| OE-08 | OD-21 | `jarvis agents start codex X "<task>"` | thread starts, events stream, completion notified | events |
| OE-09 | | steer and interrupt the Codex thread | works | events |
| OE-10 | Codex TUI started by hand | list | honest capability (observe or UNSUPPORTED_ATTACH) + resume offer | list row |
| OE-11 | 3 sessions, 3 projects | "which project is blocked?", "what progressed since I left?" | correct digest | digest |
| OE-12 | | try to start a second writer in the same worktree | refused, new worktree offered | row |
| OE-13 | | kill JARVES, restart | last-known statuses with time; pending messages delivered once | rows |
| OE-14 | | dashboard Coding agents page en/he | matches CLI | screenshot |

### 7.12 WhatsApp (F)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OF-01 | OD-25..31 answered | dashboard → WhatsApp → Link (main number, read mode) | QR scanned; status connected; phone shows "JARVES bridge" | status |
| OF-02 | | from another phone send 5 messages (Hebrew, emoji, photo, group, injection) | 1:1 texts shown; photo as metadata; group hidden; injection marked untrusted | inbox counts |
| OF-03 | | "מה חדש בוואטסאפ?" | short summary with names, nothing long read aloud | transcript code |
| OF-04 | | "תעשה מה שכתוב בהודעה" (for the injection) | asks first; nothing done | rows |
| OF-05 | | press Kill | disconnected within 2 s | timing |
| OF-06 | | restart the PC | reconnects without QR | status |
| OF-07 | | copy the bridge dir to another Windows user, start | fails to decrypt | error code |
| OF-08 | | unlink on the phone | status "logged out", red alert | alert row |
| OF-09 | 14 days read mode | check the warning counter | 0 account warnings | counter |
| OF-10 | OD-31 = go | switch to send mode on the PC | mode changes; refused from voice/remote | rows |
| OF-11 | | "תענה ל<contact>: אני מאחר" | read-back, popup with name/number/full text, approve → delivered | delivered receipt |
| OF-12 | | deny one | nothing sent | row |
| OF-13 | | 11 sends in an hour (test contact) | 11th refused | row |
| OF-14 | | retention purge | text older than the setting gone | count |

### 7.13 Conversation recall (G)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OG-R1 | real profile with history | "מה אמרתי על …?" (owner picks a real topic) | right date + short quote | owner yes |
| OG-R2 | | English recall question | right answer | owner yes |
| OG-R3 | | delete a conversation, ask about it | nothing returned | row |
| OG-R4 | | dashboard search | same results as CLI | screenshot |
| OG-R5 | | private result | offered on screen, not read aloud | transcript code |
| OG-R6 | | `jarvis recall` on another profile | nothing from this profile | output |

### 7.14 Dialing (H)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OH-01 | Phone Link paired (OD-16) | "תתקשר ל<name>" | name + digits read back, approval, Phone Link opens the right number | owner yes |
| OH-02 | | an ambiguous name | asks which one | row |
| OH-03 | | an unknown name | no guess | row |
| OH-04 | | say a number | read back digit by digit | transcript code |
| OH-05 | | deny | nothing opens | row |

### 7.15 Telegram (I)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OT-01 | | `jarvis connect --only telegram` | token validated, paired by deep link | status |
| OT-02 | | send a message to the bot | read aloud / shown | row |
| OT-03 | | "תשלח לי בטלגרם: בדיקה" | draft, confirm, delivered | receipt |
| OT-04 | | deny | nothing sent | row |
| OT-05 | | a reminder by Telegram | delivered on time | timing |
| OT-06 | | message from another account | ignored, hashed in log | row |
| OT-07 | | Wi-Fi off, send | honest offline message, retried later | rows |
| OT-08 | | rotate the token | old refused, new works | rows |

### 7.16 Instagram and Home Assistant (I)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OIG-1 | OD-05b Business account | connect, receive one DM | read aloud | row |
| OIG-2 | | reply by voice + popup | delivered inside 24 h | receipt |
| OIG-3 | | reply after 24 h | honest refusal | row |
| OIG-4 | not Business | open the Instagram guide | says why unavailable on every surface | screenshot |
| OHA-1 | OD-11 | wizard with a dedicated HA user | connected, version shown | status |
| OHA-2 | | "תדליק את האור בסלון" | light on; "תכבה" off | HA state |
| OHA-3 | | a lock action | asks; deny does nothing | row |
| OHA-4 | | an entity outside the allow-list | refused | row |
| OHA-5 | | HA off | clear spoken message | transcript code |

### 7.17 Rocket alerts (I, last)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| ORA-1 | OD-15b | enable the feature | setup speaks "unofficial, backup only" | transcript code |
| ORA-2 | | `jarvis alerts test-replay --area <owner area>` | loud alert within budget, face red, overrides quiet hours | timing |
| ORA-3 | | replay another area | silent | row |
| ORA-4 | | block the source (hosts file) | reported once | alert rows |

### 7.18 Dashboard (K)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OK-01 | | walk through every group in Hebrew | no English multi-word strings, RTL correct | owner notes |
| OK-02 | | same in English | no Hebrew | notes |
| OK-03 | | Timers: add, ring, snooze, cancel | works; "ringing" state | rows |
| OK-04 | | Lists: add, check, clear (full popup) | works | rows |
| OK-05 | | Approvals page with 2 pending | approve/deny, history | rows |
| OK-06 | | phone (390 px) on the owner's phone browser via LAN | usable, no sideways scroll | screenshot |
| OK-07 | | language picker | English and Hebrew only | screenshot |
| OK-08 | | read the 20-line Hebrew wording sample | approved or changes | owner answer |

### 7.19 Install, update, rollback (L)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OL-I1 | 3.0.0.x installed with data | accept the in-app update to 4.0.0 | silent upgrade, data kept | data counts |
| OL-I2 | | `jarvis doctor` | green | output |
| OL-I3 | | `jarvis update --rollback` | back to 3.0.0.x, data as before | counts, version |
| OL-I4 | | update again | works | version |
| OL-I5 | | tampered manifest (agent prepares) | refused | row |
| OL-I6 | second PC | every INSTALL_TEST row | PASS | INSTALL_TEST table |
| OL-I7 | | uninstall keep data / not | as chosen | residue output |
| OL-I8 | | SmartScreen / signature per OD-08 | as decided | screenshot |

### 7.20 Self-Diagnostics 2.0 (O)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OO-01 | | "מה מצבך?" | 2-3 sentence summary | transcript code |
| OO-02 | | open Health | score, trend, tiles, findings | screenshot |
| OO-03 | agent unplugs the mic | restart | preflight reports no mic, audio degraded, rest works | preflight row |
| OO-04 | | accept "restart wake loop" remediation | runs after approval, post-check OK | row |
| OO-05 | | deny a remediation | nothing done | row |
| OO-06 | morning after | canary result on Health | nightly journeys listed | screenshot |
| OO-07 | AI review ran with findings | read the report, accept one, dismiss one, snooze one | states stored; nothing applied without approval | rows |
| OO-08 | | ask "מה מצא הבודק?" | spoken summary | transcript code |
| OO-09 | | approve one ticket draft | one issue on the private repo | issue URL |
| OO-10 | | set AI review to every 3 days / off | schedule updated | job row |

### 7.21 Privacy (B)
| ID | Pre | Steps | Expected | Evidence |
|---|---|---|---|---|
| OR-01 | | `jarvis diag`, open the ZIP | no conversation text, names, numbers, keys | owner yes |
| OR-02 | privacy ON | have a short talk, open the log view | content masked | screenshot |
| OR-03 | privacy OFF | same | content visible, banner "Privacy: off" | screenshot |
| OR-04 | | `jarvis status --json` | no secret values | output |

## 8. Testing — manual: agent-driven script

The same areas, driven by an agent without the owner, on the TEST profile, with virtual audio and fakes or copied tokens. `core/tools/agent_pass.py --section <n>` runs them, records evidence class `virtual`/`real` and writes `docs/reviews/SWV4_AGENT_PASS.md`. These rows never count as owner evidence.

| Area | Rows | What the agent drives |
|---|---|---|
| Install (L) | AL-01..AL-04 | build installer, silent install in a fresh Windows user created by the script (only with the owner's prior OK), upgrade from both old series, rollback, uninstall residue |
| Voice (C, D) | AM-01..AM-06, AD-01..AD-04 | `suite_v3_voice.py` groups greeting, long_utterance (delayed transcripts), barge-in, idle, delegation 51-62; cost report |
| Approvals | AP-01..AP-04 | popup `--test-invoke approve|deny|wrong-code|hold`, CLI approvals, cooldown, spoken-kind text |
| Wake (J) | AJ-01..AJ-03 | `wake_eval.py` on the synthetic set, suites 21/22/23/45, retrain from stored feedback on fixtures |
| Google | AG-G1..AG-G3 | calendar read and briefing on a COPY of the token in a temp home; delete guard probes |
| Music | AMU-1..AMU-3 | `suite_music.py --groups skill` with volume forced to 0; slow-Pear fake |
| Dashboard (K) | AK-01..AK-05 | `all_pages_check.py` 3 sweeps, axe, string inventory, flows, captures of new pages |
| Privacy (B) | AR-01..AR-05 | `redaction_audit.py --both-modes`, diag ZIP scan, crash dump scan, third-party logger scan, public snapshot scan |
| Orchestrator (E) | AE-01..AE-08 | fakes: register, ask, tell, approvals, spoof, dedup, restart, two providers; a real Claude Code session in a scratch repo **only** with OD-20 yes |
| WhatsApp (F) | AF-01..AF-06 | fake bridge contract, policy table, injection suite, kill timing, retention, voice 71-78 |
| Recall (G) | AG-01..AG-04 | benchmark, privacy, delete, profile isolation |
| Dialing (H) | AH-01..AH-03 | 12 virtual scenarios with the `tel:` handler recorded |
| Channels/HA/alerts (I) | AI-01..AI-06 | fakes + recorded contract replays; alert replay server; 24 h live-endpoint read soak |
| Perf (M) | AM-P1..AM-P3 | `bench_startup.py --log`, perf gate on a slowed build, idle disk read trace |
| Docs (N) | AN-01..AN-02 | link check, claim audit vs capability matrix |
| Self-diag (O) | AO-01..AO-08 | replay harness anomalies, boring days, preflight planted faults, remediation via approvals on the test Core, canary, AI-review selection with a fake client, cost display, ticket draft (not filed) |
| Failure injection (Pass F) | AX-01..AX-14 | `failure_injection.py` cases one by one, with log review |

## 9. Campaign protocol

### 9.1 Passes (as in `docs/plans/FULL_TEST_PLAN.md`, extended for V4)
1. **Pass A — install and start:** clean install, first-run wizard, autostart, tray, start/stop ×5, upgrade and rollback, privacy default (owner 7.1, 7.19; agent AL).
2. **Pass B — feature by feature (agent, real Core):** every registered feature ON/OFF/available/unavailable, CLI + dashboard + voice-tool parity, guide present, error paths (`campaign_features.py` extended with the V4 features).
3. **Pass C — voice:** `suite_v3_voice.py` all groups incl. 51-96 (virtual), then the owner's 7.2/7.4 rows.
4. **Pass D — dashboard:** every page, en/he, three widths, light/dark, flows, a11y (agent AK), then owner 7.18.
5. **Pass E — owner-assisted:** sections 7.3-7.17, 7.20, 7.21 in the prepared sittings.
6. **Pass F — failure injection:** `failure_injection.py --all` plus real sleep/resume and a real network unplug on the owner's PC.

### 9.2 Log review after each pass
Scan `%LOCALAPPDATA%\jarves\logs\jarves.log` (and the test homes), `store.db` issues, self-check / Health findings, the O anomaly list and the diagnose rules. Classify every WARN/ERROR as **expected** (with the reason), **bug** (a bug-table row) or **noise** (a row to lower its level). Compare with the 19 Oct baseline and the previous pass. A Haiku agent does the first triage; Opus reviews every row classified "expected" for voice/security codes. Output: a "Log review" section in the pass report with counts per class.

### 9.3 Bug table format
File: `docs/reviews/SWV4_BUG_TABLE.md` (one table for V4; pass-specific tables link to it).

| ID | Sev | Pass/row | Area | Steps to reproduce | Expected | Actual | Evidence (log code, commit, screenshot path) | Root cause | Fix commit | Regression test | Retest (date, result, evidence class) | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

IDs: `V4-<area>-<n>` (for example `V4-WA-3`). Status: open / fixing / fixed-awaiting-retest / closed / wont-fix (owner-approved only) / blocked (reason).

### 9.4 Severity rules
- **P0** data loss, a secret or personal data leaked, an unapproved external side effect (message sent, call placed, purchase, deletion), a security boundary broken, a crash loop. Stop the pass; fix at once.
- **P1** a documented path cannot work, a wrong result stated as success, an approval bypass with no external effect, an install/upgrade failure.
- **P2** wrong or crashing behaviour with a workaround, a flaky test, a budget exceeded, a misleading message, an accessibility blocker.
- **P3** polish: wording, layout, log level, minor latency.
- Fix P0-P2 in the current package before moving on; list P3 (owner policy).

### 9.5 Retest rules
- Every fixed bug is retested **in the same scenario that found it**, with the same evidence class or a higher one (owner > real > virtual).
- A fix touching voice re-runs the voice groups that share the code path; a fix touching approvals re-runs 7.3/AP; a fix touching logging re-runs the B audit.
- Two failed fix attempts: stop and report (AGENTS.md debug budget).
- Retests of owner rows are batched into the next owner sitting; the owner is never asked to repeat a row twice for the same bug without a new fix.

### 9.6 Exit criteria (per campaign and for V4)
- No open P0 or P1. No open P2 without an owner-approved waiver.
- Every row PASS, or BLOCKED with a reason the owner accepted.
- Log review shows no unexplained ERROR, and every WARN classified.
- Flake detector 3/3, zero skips, nightly green 5 nights in a row.
- The clean-install pass on the release candidate is PASS.
- Live spend within OD-01.

## 10. Release checklist

All of these must be PASS on the release candidate SHA before tagging `v4.0.0`:

1. `jarves-checks` all jobs green on the exact SHA (public repo), run URL recorded.
2. Nightly green 5 consecutive nights ending on the candidate.
3. Offline suite 3× locally: identical, 0 skipped, 0 failed.
4. `suite_system.py` all rows PASS on the candidate.
5. Voice suites 1-96 PASS (latest run of each) within the cost cap; cost report attached.
6. Dashboard sweep ×3 clean, axe 0 serious; owner dashboard review done.
7. Redaction audit 0 hits in both modes; repo and public-snapshot secret scan 0 hits.
8. Performance gate PASS on CI and on the owner's PC (strict budgets).
9. Installer: clean-machine INSTALL_TEST PASS; upgrade from 0.1.0.x and 3.0.0.x PASS; rollback PASS; signing per OD-08.
10. Failure-injection suite PASS.
11. Owner passes: every section 7 row PASS or owner-accepted BLOCKED.
12. V4 bug table: no open P0-P2.
13. Capability matrix updated (Windows status per feature; Pi rows `planned` with issue links).
14. Docs/guides refreshed (N), release notes written, HANDOFF updated, decision queue reviewed.
15. Issues: #14, #15, #16, #24, #25, #28, #49 closed with evidence; #17 closed; new V4 issues closed or moved to Later.
16. Public snapshot dry-run reviewed (file list), then pushed; private repo synced.
17. Tag `v4.0.0`, installer published with SHA-256, update manifest signed.

## 11. Owner decisions

### 11.1 Open decisions (needed)
"Needed by" is the first package that waits for the answer. The recommendation is the agent's.

| # | Decision | Recommendation | Needed by |
|---|---|---|---|
| OD-01 | V4 live spend cap (GPT-Live + backend) | $15 total, reported per package | all |
| OD-03 | Clean-install test on a second PC or a clean Windows user on this PC | second PC | L |
| OD-04 | When are the owner sittings (A, then I/E/F)? | two 75-min sittings for A, then one each for E, F, I | A |
| OD-05 | Read-only OpenAI admin key for the real bill guide (#25)? | yes if you have one; else BLOCKED and closed as "not used" | A |
| OD-05b | Instagram: Business/Creator account linked to a Page? Polling or Cloudflare Tunnel? | test only if Business; polling | I |
| OD-08 | Code signing: confirm "unsigned" (SignPath needs an OSI licence, which you chose not to add; Azure is not open to individuals in Israel), plus a self-signed cert on your own PCs? Or buy an OV cert (~$200-500/yr)? PRIORITIES said "signed installer", your later answer said "unsigned for now". | unsigned + self-signed for own PCs | L |
| OD-11 | Home Assistant URL, version, devices, a dedicated HA user, keep the default risky list? | dedicated user; keep the list; start with one light | I |
| OD-12 | `log.privacy` default for NEW installs (yours stays as is) | ON for new installs | B |
| OD-13 | Second-opinion code review by OpenAI Codex on your subscription? | yes, one pass | C |
| OD-14 | Pear history trimming (removes played items automatically): default on or off? | off by default, switch in Settings | C |
| OD-14b | Final dashboard group names and order | keep the current provisional ones | K |
| OD-15 | Delegation default: client mode (strict cap, +latency) or server mode? Allow fallback to server mode on protocol errors? | decide after the measured latency; fallback allowed, logged | D |
| OD-15b | Rocket alerts: your area/city; accept an unofficial source labelled "backup only"? | yes, with the label | I |
| OD-16 | Is Phone Link paired, with which phone? | pair the main phone | H |
| OD-17 | Recall scope: conversations only, or also reports and notes? Search summaries after 90 days? | conversations + reports + notes; summaries after 90 days | G |
| OD-19 | Wake phrase: "Hey Jarvis" only, or also "היי ג'רוויס"? | both, if the samples allow | A/J |
| OD-20 | Install the JARVES Claude Code plugin; OK to start Claude Code with the Channels flag when needed? | yes | E |
| OD-21 | Codex: subscription sign-in only (no API billing ever)? | subscription only | E |
| OD-22 | Which projects may JARVES see and steer (allow-list)? | `C:\work\jarvis` + named others | E |
| OD-23 | May JARVES start a new agent session from a voice "yes", or only with on-screen approval? | on-screen approval | E |
| OD-24 | Daily cap on JARVES-initiated agent turns | 20 per day | E |
| OD-25 | WhatsApp read scope: all 1:1 chats or an allow-list? Groups never / read-only? | all 1:1, groups hidden | F |
| OD-26 | WhatsApp send scope: replies only, or new messages to allow-listed contacts? Who is on the first send list? | replies + a short list you name | F |
| OD-27 | WhatsApp approval: PC popup always, or a spoken yes for a known contact after the first message? | popup for the first message to a person, spoken yes after | F |
| OD-28 | WhatsApp retention: 14 days, less, or nothing (live only)? | 14 days text, 0 media | F |
| OD-29 | OK that message text you ask about is sent to OpenAI for summarising? | yes, only for the request at hand | F |
| OD-30 | OK to ship a small Go binary (built in CI), or prefer the Python neonize plan B? | Go | F |
| OD-31 | After 14 quiet days in read mode on the main number: enable send mode? | yes, if 0 warnings | F |
| OD-32 | Keep 2 s clips of false wakes for 24 h (local, encrypted) to retrain? | yes | J |
| OD-33 | Selective AI review frequency | every 2 days | O |
| OD-34 | AI review on/off by default, cap per run and per month, route (API key or ChatGPT plan) | ON, $0.10/run, $1/month, API key route (redacted metadata only) | O |
| OD-35 | Ticket drafts filed on the private repo `igalgerman/JARVIS` after approval? | yes, private repo only | O |
| OD-36 | Paid nightly voice canary (GPT-Live connect/close, ≤ $0.02/night)? | no (the free local path is enough); weekly instead if you want | O |
| OD-37 | May "restart the face link" run automatically (all other remediations always ask)? | yes | O |
| OD-38 | Popup: Enter approves once armed (current) or Enter = Deny? | keep Enter = Approve after the arm delay | A |
| OD-39 | Browser: keep logins for allow-listed sites, ask for registrations/sending too, downloads into the Files sandbox? | fresh sessions; yes, ask; downloads after asking | later browser work (not a V4 package); asked once in A |

### 11.2 Already answered — do not ask again
| Topic | Answer (date) |
|---|---|
| Scope | V4 software only; phone, Pi, hardware, Tailscale remote, multi-device out (20 Oct) |
| Languages | English (default) and Hebrew only; Arabic and Russian dropped, remove from the picker (20 Oct late) |
| Completeness | every V4 item complete, no MVPs (20 Oct) |
| WhatsApp number | main number, read-only first (20 Oct, via the coordinator) |
| Spoken approvals | name only the kind of action plus the recipient's first name; full text on screen (20 Oct late evening) |
| Dashboard Clear | uses the full approvals popup (20 Oct late evening) |
| Language of answers | answer in the language of the user's sentence (done, CV-05) |
| Timers/Lists pages + CLI | build them (done in V4 P0.3) |
| Unavailable feature ON | save as ON and show "unavailable" on every surface (done in V4 P0.3) |
| Real-profile switches | keep `gmail_send`, `calendar_write`, `camera`, `camera_when_away`, `face_recognition`, `dashboard_lan` ON; sends need exact confirmation; no delete tool |
| Pronunciation | ship no default dictionary entries ("לצחי" sounds better without) |
| Owner's name | יגאל (not איגל); remove the default איגל entry |
| Licence and repos | no licence (all rights reserved); public `igalgerman/jarves` for code + CI; private repo for notes/HANDOFF/logs; sync with `make_public_snapshot.py`; tag v3.0.0; close old PRs #32 #34 #38 #41 #42 #45 #48 after the release |
| Connect | `jarvis connect` done for Google, Pear, wake; Telegram/Instagram/Home skipped (for now) |
| Popup | approved design; small, always on top |
| Morning briefing | on request only, no schedule |
| Quiet hours | 22:00-07:00, alarms the owner set still ring |
| Telegram | owner's chat only |
| Self-check (V3) | weekly Sunday 10:00 + after a crash; AI pass off by default (the V4 selective review is a new decision, OD-34) |
| Helper | always asks per command |
| Instagram sends | voice + on-screen popup |
| Installer signing (V3) | unsigned for now (OD-08 only confirms it for V4) |
| Fix policy | fix P0-P2 at once, list P3 |
| Test data | agents use the TEST profile / temp homes; the real memory is never modified |
| Profiles | one voice profile at a time |
| Live budget V3 | about $10 (V4: OD-01) |
| Conversation recall | build after the campaign (G) |
| Dialing | Phone Link / default dialer (H) |
| Wake samples | the owner records ~20 when asked (A) |
| Dashboard language | separate from the voice/CLI language |

## 12. What V3 verified vs not

Carried forward from the draft and updated with P01. V4 fills the right-hand column.

| Area | Verified (class) | Not verified / BLOCKED | V4 package |
|---|---|---|---|
| Offline tests | 1454 passed ×3, 0 skipped (integration report) | CI on the public repo; flake detector | M |
| System suite | 20/20 PASS | re-run on the final head | M (nightly) |
| Features (Pass B) | 1429 PASS, 1 FAIL (P3), 2 BLOCKED | Telegram, Instagram, HA, WhatsApp only against fakes; O-03..O-06 | C, F, I |
| Dashboard (Pass D) | 186 loads clean; D-01..D-09 fixed | live strip/preview, setup actions, D-10..D-18, owner review | A, K |
| Voice (Pass C) | 44/47 PASS + follow-up fixes (real GPT-Live, virtual audio) | physical mic/speaker by the owner; CV-04, CV-07 (wake), CV-16, CV-17 | A, C, J |
| Music | P01: real Pear 12/13 + K01 soak | S10 (P3), #16 P1/P2 items | C |
| Google | P01: G1-G6 PASS; G7 re-scored per owner answer | voice Calendar/Gmail live, weekly reconnect, Contacts | A |
| Approvals | dashboard API, CLI, popup screenshots | real desktop popup with voice yes, tray balloon | A |
| Camera | virtual + one real in-memory capture | real webcam preview and strip in a live conversation | A |
| Wake | stock model; synthetic training pipeline | owner samples, per-user calibration, production thresholds | A, J |
| Browser | real headless Edge on a local page | a real site, a spoken browser conversation | A |
| Performance | V3 before/after on the owner's PC | wake/live/dashboard/face/sleep numbers, disk reads, CI gate | M |
| Installer | CI build + silent smoke | clean machine, upgrade from old series, rollback | L |
| Privacy | V3 masking, diag masking (key-based) | free-form `msg` (#15), full sink audit | B |
| Self-check | rule-based weekly run (fixtures) | real logs, continuous monitoring, canaries, AI review | O |

## 13. Definition of done for V4

V4 is done when: every package A-O is merged with its acceptance rows PASS or owner-accepted BLOCKED; the release checklist (section 10) is fully PASS; the V4 bug table has no open P0-P2; the issues listed in checklist item 15 are closed with final-commit CI evidence; `v4.0.0` is tagged and the installer is published; the verified-vs-not table above is updated with V4 evidence; and every Pi/phone/hardware gap is listed with an issue under Later.

## 14. Later / out of scope

Not started in V4. Each one needs its own owner approval and plan:

- **Phone as a JARVES device** (OnePlus 11): dashboard + face display, satellite mic/speaker/camera over DevicePort, Core-on-phone experiment (`docs/ROADMAP.md`). Includes WhatsApp stage S4 (notification listener + RemoteInput reply).
- **Multi-device and remote access over Tailscale** (`docs/plans/MULTI_DEVICE_REMOTE.md`): one brain, many satellites, remote approvals.
- **Raspberry Pi 5:** install, boot, Pi parity (spec M7), render performance, thermals, and the Pi rows moved out of #24, #25 and #28 (a new "Pi verification" issue).
- **Hardware:** mic array and acoustics, camera module mounting and printed privacy shutter (Issue #8), physical mute, round 1080×1080 display layout (#28), purchases and prints.
- **Languages other than English and Hebrew** (Arabic, Russian and any other).
- **Virtual-computer prototype** (V3 P2.1; justification gate D7 not passed: the owner has not named 2+ tasks the browser skill cannot do).
- **JARVES-managed phone calls** (V3 P2.2 stage B), ChatGPT history import (F10), shared PC + robot memory (F01), skill/avatar library (F05), speaker recognition, HTTPS for the LAN dashboard before the robot is in the house, ACP/A2A adapters and other coding agents (OpenCode) for the orchestrator unless a concrete use case appears.

---

## Package P — Performance improvement (added by the owner, 20 Oct 2026)

**Goal.** Make JARVES measurably faster, lighter and more responsive, and keep it that way. Package M enforces budgets (regression gates); package P *improves* the numbers below the V3 baselines. Complete, not an MVP.

**Scope (all of it)**
1. **Voice latency.** Wake-to-first-audio, end-of-speech to first reply audio (V3 median 1.78 s, p90 2.52 s), live-session start after Core start (V3: 8.6–15 s), barge-in stop time (0.5–0.7 s), dead air. Targets: wake-to-first-audio p50 under 2.0 s, session start under 6 s, p90 reply audio under 2.0 s. Includes keeping a warm session/prewarm policy, connection reuse, avoiding work on the event loop, and measuring on the real GPT-Live path (virtual mic/speaker, within the shared $25 cap).
2. **Backend cost and latency (CV-17).** About 20k input tokens per backend call: trim the persona/tool schema, share cached prefixes, send only the tools the turn can use (feature-gated tool lists), compact conversation context, verify prompt caching hit rate (V3: 84% cached). Target: at least 40% fewer input tokens per call with no drop in the voice scenarios.
3. **Startup and idle.** Core ready (V3 1.7 s), time to a usable dashboard, idle CPU (V3 0.28%), idle memory (V3 93 MB private), threads (11), and the **unexplained idle disk reads of about 250–390 KB/min**: find and remove the cause. Face show/hide time (5x), sleep/resume recovery, network-loss recovery.
4. **Dashboard.** First load, per-page API time, static asset size/caching (ETag, gzip), no polling storms, lazy-loading of heavy pages, search index speed; measured in the real browser at 1440 and 390 px.
5. **Test-suite and CI speed.** Suite wall time (1600+ tests, about 10 min locally, 15–17 min Windows CI): profile the slowest tests, share expensive fixtures safely, remove real sleeps, keep zero skips and zero flakes; reassess parallelism only if provably flake-free (package M decided serial).
6. **Memory.** Per-feature memory cost table (each add-on on/off), lazy import of rarely used features, release of camera/browser/wake resources when switched off.
7. **Installer and update.** Installer size (198 MB), install time, first-start time, update download size (delta or skip unchanged payload if feasible), startup after install.

**Method.** Baseline first (`core/tools/bench_startup.py`, `perf_gate.py`, voice suites, new browser timing tool), one change at a time with before/after numbers in `docs/reviews/SWV4_P_PERF.md`, behaviour unchanged (full suite green), budgets in `core/perf_budgets.toml` tightened to the new numbers with a `[[history]]` entry. Measurements only on an idle machine (no other agents running); noisy runs are discarded and repeated (median of 5).

**Acceptance.** (a) Each target above met or the report explains why not, with evidence; (b) the performance gate in CI passes with the tightened budgets; (c) the 47+ voice scenarios still pass, no new flakes; (d) the idle-read cause is identified (or proven to be OS/AV noise); (e) no feature regression (full suite zero skipped).

**Testing.** Automatic: perf gate (owner and CI classes), voice latency suite with percentile assertions, dashboard timing sweep, token-count regression test for the backend prompt (fails if input tokens grow more than 10%), memory-per-feature table test. Manual: owner stop-watch script (wake-to-first-audio x10, face show/hide x5, startup to usable x3) with expected ranges.

**Model.** Opus 5.5 (measurement design, voice/prompt work); Sonnet 5.5 for the dashboard/CI mechanical parts. **Effort.** About 7 agent-days. **Depends on:** M (gates), B/C finished (to avoid conflicts in `live.py`/`app.py`). **Risks.** Latency gains depend on OpenAI-side behaviour; prompt trimming can change answers (guarded by the voice scenarios); idle-read cause may be outside JARVES.
