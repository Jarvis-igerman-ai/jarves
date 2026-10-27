# JARVES V4.5 — Stabilization, Security, Camera & Guided QA
## Owner-requested work package — 24 October 2026

> **STATUS: PLANNED — DOCUMENTATION ONLY. No implementation, release, scheduled jobs, account changes, external sends, device activation or automatic issue/alert dismissal is authorized by merging this document.** Execution starts only under an explicit bounded implementation authorization in [NOW](../../NOW.md). This is a stabilization milestone after V4/4.0.1, **not** a claim that version 4.5.0 exists. Already merged V5 components remain in place; V4.5 does not replace the separate V5 phone acceptance, V6 driving, V7 Android or physical-product stages.
>
> **Single development repo:** `Jarvis-igerman-ai/jarves`. Canonical V4.5 scope/acceptance: **this document**. Delivery status: [ROADMAP](../../ROADMAP.md); what may be executed now: [NOW](../../NOW.md). The current release/CI mechanics remain owned by [GITHUB_FLOW](../../GITHUB_FLOW.md), [RELEASING](../../RELEASING.md), [PREPUSH](../../PREPUSH.md). Do not create a second sign-off engine or tracker.

**Owner milestone classification (24 Oct 2026): V4 feature-development phase = CLOSED / delivered baseline.** This is a *scope/status decision*, not a claim that all V4 issue acceptance checkboxes, real-device tests, security findings or the 4.0.1 stable promotion are complete. Outstanding defects, integration verification and user/installed-device sign-off are carried forward as explicit V4.5 stabilization acceptance, while the original evidence trackers (notably #1, #2, #58, #77) stay open until their actual criteria are satisfied. No new feature development is scheduled under V4; fixes and release validation are tracked under V4.5. V5 components already delivered may continue to exist independently; V5 is partially implemented and not owner-accepted.

## Outcome and release contract

Make JARVES reliable enough for daily use: eliminate actionable security alerts, prune evidence-closed GitHub issues, prevent recurring security/quality regressions, make the camera valuable and understandable, simplify safe code integration, and provide a live **HTML Dashboard QA workspace with an accompanying conversational Skill** that guides the owner and records verifiable results. This is quality work, not expansion into V6/V7.

**Release gate:** all applicable high/critical security findings dispositioned with evidence; new high/critical actionable findings = 0; CI green on the exact released SHA; installed Windows beta round-trip and rollback; owner-guided human sign-off complete on **that** build; regression coverage and a brief release/known-limit report. Any accepted risk is explicitly documented with an owner decision, impact, mitigation and recheck date. **No blanket suppression.**

**Do not confuse:** a green CodeQL workflow with zero CodeQL alerts; passing simulated tests with a real microphone/camera/phone; a merged PR with installed acceptance; an issue being old with its requirements being completed.

## Workstream A — issue audit, deduplication and evidence-based closure (P0)

**Input:** all currently open GitHub Issues and related PRs/commits/CI/handoffs, local JARVES diagnostics **as a separate namespace**, CodeQL alerts (different numbering), and historic migration ledgers. Snapshot these live lists on kickoff; do not copy private user data into public GitHub.

1. Classify every Issue as `complete-evidence`, `partially-implemented`, `owner/device-gated`, `future/deferred`, `obsolete/duplicate`, or `still-actionable`; record owner, acceptance row, build, link to PR/commit/CI and missing evidence.
2. Cross-check actual main implementation, feature flags and install path. A unit-test-only fix is not a real-world sign-off. Close **only** fully satisfied or explicitly superseded issues; add a concise evidence comment, reason, replacement tracker where applicable, then close. Retain history.
3. **Current candidates, not pre-approved closures:** #89 (CI/merge friction: migrate its surviving release sign-off constraints to #60 before closure); #77 (source merged, pending installed diagnostics and owner live acceptance in #1). #58/#60/#61 still have outstanding scope. #1/#2/#5 retain genuine delivery/migration gates; #3/#4 are deferred hardware, #50 is proposed V7, #71 remains planned performance work. Reassess against fresh evidence rather than closing by this old snapshot.
4. Avoid duplicate Issues: keep #60 **the single owner of interactive/manual release sign-off**, #58 installer, #61 self-knowledge/help, #1 real-device acceptance, #2 agent live validation, #5 legacy archival, and this document as the V4.5 plan. Create narrowly scoped bug/implementation Issues only for genuinely uncovered actionable work; back-link to this plan. Do not bulk-migrate closed issues or open a parallel manual-testing epic.

**Acceptance:** reconciled audit table 100% of current open Issues, zero unsupported closures, deferred issues labelled/tracked but not lost; post-close links and counts verified with GitHub API.

## Workstream B — CodeQL/security backlog remediation (P0)

**Scope:** CodeQL alerts including the owner-supplied screenshot series #6–#37, plus any earlier/later alerts, Dependabot, secret scanning, custom review risks and new alerts discovered by authenticated security-event reads. Alert numbering **is not** Issue numbering. The screenshots alone do not prove full alert inventory, location or exploitability; refresh from GitHub Security using appropriately scoped security-events access on the owner's approved runner. If not authorized/available, export the alert list manually; mark the coverage incomplete instead of claiming zero.

**Risk-prioritized buckets from screenshot + source inspection (triage, not verdict):**
- #6–7 mobile `mobile.py` path-expression: the static allowlist may make these false positives, but prove normalization/encoded traversal/symlink/unknown-name rejection with HTTP tests before justified dismissal.
- #8–11 `brain.py`, #12 delegation-client regex, #37 dashboard email regex: test crafted long input and regex worst-case timing; replace problematic patterns with bounded or linear strategies where necessary.
- #13 approvals audit, #15 health snapshot, #19–21 CLI/logging, #25 release publishing, #14/#18 tools: trace user-controlled text/secrets to storage/console/CI artifacts; fix when real. Review both privacy modes, retention and sanitization boundaries.
- #16–17, #26–36 testing/intentional planted-secret/URL test patterns and #22–24 redaction-audit tools: determine whether alerts are deliberate negative tests vs real artifact leakage; never broadly exclude `tests/` or disable a useful rule merely to make a counter green.

For **each** alert: GH alert URL/number, query/rule, code-flow source→sink, reachable conditions, impact, verdict (`fixed`/`false-positive`/`accepted-risk`/`open`), targeted regression, owning PR + tested SHA, reviewer and CodeQL re-scan evidence. Fixed = code+test+re-scan; false positive = justified precise dismissal; accepted risk = explicit owner sign-off, expiry and mitigation. No mass dismissal, no hardcoding secrets into test fixtures, no public vulnerable-user-data repro.

**Baseline controls:** pinned dependencies, dependency-review on PR, least-privilege CI permissions, no secret exfiltration in issue drafts/artifacts, independent security review for changes to auth/approvals/browser/phone, isolate permission boundaries from prompt-only checks. Address [SWV4_C_REVIEW](../../reviews/SWV4_C_REVIEW.md) residual browser/voice/helper risks and [SWV5_SECURITY_REVIEW](../../reviews/SWV5_SECURITY_REVIEW.md) known phone limitations separately from CodeQL alert count. Provide a reproducible finding-to-fix register.

**Acceptance:** all accessible alerts triaged; every actionable High/Critical resolved or explicitly accepted with expiry; rerun CodeQL and focused tests; no unreviewed new High/Critical on V4.5 release SHA. Residual Low/Medium have bounded tracked remediation dates, not silent dismissal.

## Workstream C — configurable recurring security & complaint prevention (P0/P1)

**Reuse** existing `jarves-nightly`, `main-health`, Dependabot, CodeQL, issue registry and selfcheck/speedtest rather than spawning duplicate CI systems. Supply owner-configurable **cadence presets**: daily, N selected weekdays (including multiple/week), weekly, on-demand; optionally morning/evening local runtime if the PC is on. Document the difference between hosted GitHub scheduled Actions and PC-local jobs; if owner changes repository cron, require explicit authorized repo write and use a bounded workflow/variables implementation (do not let a Dashboard setting silently reconfigure GitHub).

- **Light scan** (suggest daily default, owner can change): fresh Security alert inventory, dependency changes, newly failing tests, duplicate or recurring diagnostics, CI main red, incidents since last successful scan; no LLM calls if no meaningful diff.
- **Full scan** (suggest weekly): CodeQL + SCA/dependency audit + secret-scan signal, permission/approval/browser/mobile boundary regressions, new/changed-code diff review, trend summary, flaky-test/issue recurrence, high-priority stale fixes. Keep heavy jobs off every developer PR unless risk tier demands them.
- **Trigger after important changes:** new/modified authentication, camera, browser, approvals, credentials, network, updates, install, dependencies → focused security tests + review **before merge**; compare new findings to a frozen baseline (do not block unrelated PRs on pre-existing debt).
- **Find→triage→act:** deduplicate by finding fingerprint+rule+code location+version, rank severity/exposure/reproducibility, draft or update *one* issue, recommend owner-approved repair PR for bounded safe bugfixes, run scoped tests, report resolved/regressed/deferred. Avoid notification spam; quiet hours/summary cadence and max auto-run/cost budgets.
- **User controls:** enable/disable, timezone, weekdays/frequency, reports, last-success/last-failure, next scheduled scan, history, budgets, dry-run, no surprise paid API. `auto_fix` defaults **OFF**; even if enabled, proposed PR only, never unapproved merge, deployment, secret change, external send or alert dismissal.

**Acceptance:** schedules tested across timezone/DST/offline/runner-not-run, failure/retry and dedup; a synthetic security finding creates one non-sensitive finding; a resolved alert is marked only with verified evidence; owner can choose three days/week or daily and see the actual next execution.

## Workstream D — camera usability, reliability and UX (P1)

**Preserve [CAMERA_SETUP](../../CAMERA_SETUP.md): camera is ON DEMAND; enabling its capability does not turn on a background sensor. No unattended presence surveillance, no default photo/video retention.** Improve the current capability rather than replacing CameraAdapter/VisionSkill.

- **Single Camera Dashboard:** visible state `Off / Available / Previewing / Capturing / Sending to AI / Busy / Error`, active device, privacy indicator, last request result/timestamp, safe permissions/configuration, actionable error explanations in Hebrew/English; never claim "camera on" when merely enabled.
- **User-initiated experience:** one-click **Test camera** (device probe vs actual frame test clearly separated), timed local preview with visible countdown and **Stop** button, choose camera/device and resolution/profile if supported, explicitly take one photo/retake/confirm share; in-Dashboard preview and result with full-width useful controls on mobile and desktop. State clearly whether an image is local only or sent to a provider; never auto-send test images.
- **Useful voice scenarios:** "מה אתה רואה", "מה אני מחזיק", "תקרא את זה", live spoken guidance to aim/retry/focus; one still on request, optional read-text/describe response, understandable `no camera/blocked/busy/blurred/too dark` next step; do not persist frames by default. Capture UX should feel immediate and confirm what happened rather than silently timing out.
- **Quality & performance:** autofocus/exposure/light guidance, image orientation/crop/resolution selection, time-to-first-frame, capture→answer latency, timeouts, concurrent requests and unplug/replug recovery. Measure real Windows webcam outcomes; fake-camera CI alone remains `implemented_unverified`.
- **Accessibility:** RTL Hebrew, EN/HE, touch targets, keyboard controls, responsive layout, indicator legible when hidden/minimized; privacy/audit states checked independently.

**Acceptance:** owner completes preview→capture→spoken result and cancellation/reconnect on an actual approved Windows webcam; sensor truly closes after each operation; no idle capture; no unintended upload; baseline vs updated clarity/latency recorded.

## Workstream E — integration of code / software factory (P1)

Improve existing [GITHUB_FLOW](../../GITHUB_FLOW.md), [PREPUSH](../../PREPUSH.md), [PROJECT_MANAGER](../../plans/PROJECT_MANAGER.md), [SW_FACTORY](SW_FACTORY.md), and CI; **do not build a second orchestrator**.

- Enforce one task = scoped branch/worktree; small reviewed PR; one integration writer/lock per repo; preserve task cards, handoffs and independent reviewer. Avoid shared-file hotspots via `pages.d`, `changelog.d`, locales `parts/`, guides; no forced pushes or `git add -A`.
- Flow: quick local checks → impacted PR CI → risk-based security/reviewer gates → green exact-head PR → protected merge/auto-merge → full integration/main CI → signed candidate → beta installed smoke → owner sign-off → stable. Never treat passing a PR check as proof installed app works. Mark "main red" as blocking subsequent merges until repaired.
- Fix high-friction conflicts, flaky tests, resource contention and CI queue duration by measurement, not unconditional retries or bypass. Pre-merge summary should show changed features, test coverage, new security alerts relative to baseline, release/rollback impact, outstanding user acceptance.
- Post-merge auto-generate concise release notes, evidence-linked bug closure and changelog mapping. Explicit rollback if smoke/security regresses. New security rules apply to source and automation itself.

### EARLY V4.5 PRIORITY — make patches faster while security remediation is underway (owner decision 24 Oct)

Ship the first safe PR/merge improvements **in parallel with workstreams A/B (issues and security)**, not at the end of V4.5. Reduce avoidable merge conflicts and owner intervention for concurrent Claude Code/Codex/OpenCode tasks, **without weakening required CI, code review, security approval or installed-app gates**. Reuse existing protected main/auto-merge and lessons from #89; don't build a parallel orchestration layer.

Before touching CI, record baseline from the last 20–50 comparable PRs (or the available honest sample): ready-to-merge elapsed time, PR-open-to-merge time, CI queue and runtime, genuine conflict incidence by touched file, forced branch synchronization/retest frequency, main regressions. Split docs-only, small patches, security-sensitive PRs and large features. Measure again after the first enabling changes.

| Candidate KPI (aspirational, not guaranteed) | Target for comparable small patches | Evidence / safeguard |
| --- | --- | --- |
| PR ready-for-review → merged | **Median under 15 min**, only where required CI is fast enough | GitHub timestamps and CI proof; no bypass; separately report PR-open→merge |
| Avoidable true merge conflicts | **At least 70% fewer** vs a comparable baseline | Conflicting PRs / parallel PRs, file hotspots, denominator and date range |
| Parallel agent development | **3–5 independent agents** in non-overlapping worktrees with one integration owner | Reproducible concurrent-work smoke and resource/cost/permissions limits |
| End-to-end bug fix | Issue → scoped patch → focused tests → independent review → green CI → merge → evidence-backed close | One traceable real case, with no premature sign-off |
| Repeated full CI | Reduce unnecessary full reruns from branches merely behind `main` | CI runs and queued minutes per merged patch; keep main integration verification |

**Early slices:** E0 establish baseline and the shared-file collision map; E1 enforce scoped worktrees, one writer and bounded merge queue; E2 reduce locales/page/changelog conflict hotspots and optimize required-check latency; E3 apply the flow to the first security fixes and compare before/after. Large/sensitive code still gets independent review. If the targets are unattainable due to irreducible CI runtime or insufficient sample, say so explicitly rather than redefining metrics or weakening safeguards.

**Acceptance:** demo two conflicting PRs plus a safe non-conflicting auto-merge; no lost changes, forced pushes or missing source review; trace commit↔issue↔CI↔release. Compare median PR cycle time and late regressions before/after.

## Workstream F — living HTML manual QA workspace + conversational QA Skill (P1, #60 canonical)

**This fulfills the existing [issue #60](https://github.com/Jarvis-igerman-ai/jarves/issues/60)** and reuses `release_signoff.py`, `jarvis release signoff`, `manual_test_sheet.py`, setup/tour, guides, feature registry and scenarios. #58 remains installer-entry integration, #61 remains self-knowledge/help. **Only one result store, one set of test IDs, one release gate and one owner sign-off authority.** HTML is rendered as a dynamic Dashboard **view**, not authored/executed untrusted HTML.

### Owner requirement: frictionless interactive testing, not a document checklist (24 Oct)

The primary interaction must be a **ready-to-use, fast, clean interactive HTML testing site inside the existing Dashboard**. The same tests and state must also be accessible through **JARVES voice guidance**, an optional small **in-app popup/overlay**, and the existing CLI — choose whichever is easiest at the moment. Do not force the owner to read Markdown, navigate 54 pages, remember commands, or separately type every result. Opening **Test everything** immediately presents the next actionable prefilled card; no setup before the first test.

**Ideal UX / concrete targets (design goals to validate with owner, not measured guarantees):**
- Dashboard home has one prominent **Start / Continue testing** action, with a clear progress fraction, version, current topic, failures, and "last checked". One-screen layout, large controls and no horizontal scrolling on PC or phone. First actionable item visible within 2 seconds on the target PC after the dashboard is ready (measure).
- A card contains just **1) what to do, 2) exactly what to say/click, 3) what should happen**, with contextual **Launch test**, **Worked**, **Failed**, **Later**. `Worked` advances to the next test with one tap; keyboard Enter/1–3 and optional voice equivalents. No long forms or redundant confirmations for recording a LOCAL test verdict.
- **Guided popup mode** stays on top beside the actual application and can advance/return without losing place; small and movable, keyboard accessible, non-blocking, with one-click jump to the relevant Dashboard screen. The popup and full Dashboard share the same state and never disagree.
- **Voice mode** is equally complete: “ג'ארביס, בוא נבדוק את המערכת” → he says the next test, can open the relevant panel/demo, asks if it worked, records the owner’s answer, and continues; “נעצור” stops and saves; “דווח תקלה” opens the same prefilled reporting flow. Explain in one or two sentences; no voice-driven disclosure or GitHub publication without explicit review/consent.
- Preserve working offline/local-first flow: the entire checklist and saved progress work even when GitHub is disconnected. Tests that require an account, a real camera/mic, paid AI or network stay clearly marked and only run with explicit permission.
- Group by feature and by quick **Smoke (5–10 min target)** vs **Full baseline** vs **Changed since last version**; automatically preselect relevant human-only rows using capability/guide/scenario evidence and show auto-checks separately. Time targets are aspirational until measured against the actual hardware/build.
- Fail should take **one tap + optional short description/voice-to-text**, auto-fill the evidence that can be collected safely. Do not demand a screenshot or log copy. All statuses are reversible with audit/history, and missing proof is not a PASS.

### Direct GitHub Issue reporting — Dashboard, guided popup, and voice

**Not merely create a local draft**: after explicit owner review/approval, the Dashboard or JARVES Skill must actually **create or update a GitHub Issue in `Jarvis-igerman-ai/jarves`** through the existing `issue_registry.py` report-to-GitHub integration. Reuse existing Bug Reports/diagnostics/dedup flows; do not create a competing registry, hard-code tokens or ask the user to manually paste JSON.

**Required workflow:** `Failed` → local bug record immediately with `test_id`, feature, sanitized expected/observed outcome, build/SHA, platform, relevant safe error codes and reproduction steps → **Preview GitHub report** (editable title/description, privacy-redacted diagnostics and labels) → **Submit to GitHub** with a clear owner confirmation → authenticated create or append to an existing matching Issue → show clickable issue number/URL, save link in the test card and local record → after fix, offer **Retest** and synchronize verified status. A simple, standalone **Report a problem** button must also exist without completing a formal test.

**Input options:** short typed note, one-tap classification, JARVES guided spoken description transcribed *locally through the existing permitted voice route* (no assumption offline ASR is available), optional chosen screenshot or content-free diagnostic bundle. The owner explicitly sees which attachments/fields would leave the PC; no raw voice recording, webcam image, secrets, account details or full conversations attached by default. Apply existing `clean_evidence` rules, redaction, size cap and safe GitHub permissions.

**Failure behavior:** no connection/permission/rate-limit = saved local pending report with clear Retry, never a false "Issue created" claim; avoid duplicate Issues via stable fingerprint, matching current issues and idempotent retries; concurrent popup/voice/browser submissions create at most one issue. Do not silently publish on a simple FAIL. Require explicit review for every outbound Issue create/comment, even when the owner recorded a local failure by voice. A disabled GitHub integration still permits local test reporting.

**Owner acceptance demonstrations:** finish two checks in a browser, fail a camera test from the popup, dictate a short report to JARVES, review a sanitized prefilled GitHub payload, confirm and receive the real issue URL, retry it without duplication, go offline and save a second report locally, then resume from Dashboard/voice with correct progress. Verify both keyboard/touch RTL and bilingual states on a real installed PC; label any emulated mobile results honestly.

### Dashboard UX
- "Test my JARVES" landing: **tested version/build + source SHA**, tabs/topics (voice, wake, conversation, music, camera, privacy, Google, WhatsApp, agents, browser, mobile, integrations, installs, security, updates/rollback); progress `Passed / Failed / Blocked / Needs retest / Not started`, total human time remaining. Filter by `New/changed since last verified`, `Failed`, `Requires real device/account`, `All`.
- Each **human** test card: one clear instruction, sample utterance/button, expected outcome, **Do it for me** when safe to launch a bounded test, **Works / Doesn't work / Can't test now**, optional note, content-free evidence, built-in issue link and **Retry**. A failure automatically drafts a bug, not a silent success. Owner decides human pass; AI can suggest and judge **only objectively observable** results.
- Smart guided mode: next untested/high-priority item, step-by-step with back/next/skip/resume, inline camera preview/audio prompts where permissioned, short explanations. English/Hebrew, good RTL, responsive 390 px+, accessible controls. No auto-access to camera/mic/external services.
- **Automatic rows** show actual CI/scenario/selftest evidence, SHA, test environment and freshness; marked `AUTO-PASS`, not forged `OWNER-PASS`. Missing evidence never becomes PASS. Real phone/camera/mic/WhatsApp remains owner/device gated.
- Persistence is local, versioned, resumable, migratable and backup-safe. Per-feature last verified version, test definition version, test result timestamps, who verified (owner / CI / AI-observed), proof and linked issue. Modified test or affected feature/code invalidates only impacted passes; prior history remains immutable.
- **New features onboard automatically:** a single test-definition registry linked to feature ID/guide/scenario/CHANGELOG metadata. CI rejects missing human acceptance definition or explicit `no-manual-test-needed` reason for new user-visible features. Avoid a parallel list in Markdown, Dashboard and Skill. Build the existing release-signoff Markdown/export for compatibility from the same registry; no version-specific hardcoded steps.
- **Release modes:** baseline first-time full guided run (can span sessions); later version = changed feature tests + short smoke + any invalidated prior results; preserve old evidence and require owner sign-off on the installed beta SHA for stable, with risk-dependent skips requiring a note.

### QA Skill
- Intent examples: "ג'ארביס, תעזור לי לבדוק הכול", "מה עוד לא בדקתי?", "תבדוק איתי את המצלמה", "תריץ מה שאפשר אוטומטית", "מה נכשל בגרסה האחרונה?", "בוא נמשיך מאיפה שהפסקנו".
- Skill is a thin safe client to the **same QA service/state** as Dashboard/CLI. It proposes the next test, reads expected result aloud, optionally triggers a safe test with explicit consent, retrieves observable evidence, asks whether it worked and records outcome with provenance. Never mark subjective image/audio/user experience PASS without owner confirmation, never invent results, never send a real message/payment/sensor capture automatically.
- Voice interruption/resume, concurrent browser/voice updates, audit and lost-connection idempotence, accessible fallback in Dashboard. After a failed test: reproduce if safe, collect sanitized diagnostics, **offer to preview and explicitly submit a real GitHub Issue through the existing issue registry**, link the returned URL (or retain a truthful offline draft), rerun after a fix, report concise status.

**Acceptance:** user starts on Dashboard, tests two items, switches to voice Skill without resetting, fails a camera test, resumes after update, sees only invalidated items reopened; a new feature added with its guide/test definition appears automatically; stable release blocked on incomplete mandatory human sign-off.

## Workstream G — recommended polish / reliability wins (P2, bounded)

1. **One truthful readiness screen:** installed vs source version, live/untested status by feature, PC/phone/agent connections, current permissions, next suggested action. Reuse Overview/Health instead of adding another dashboard.
2. **Practical observability:** first spoken-audio latency, false wake rate (owner samples only), camera capture/answer time, crashes, rate-limit warnings, model/cost vs useful result; dashboards show trend and actual provenance, not ungrounded percentages.
3. **Installer and recovery:** close #58 and #77 live-installed gaps; one-click guided post-upgrade smoke and rollback, preserve config/wake model; debug bundles never include secrets or private conversation.
4. **UX declutter:** merge duplicate Health/Self-check surfaces in presentation (not backend), less technical Setup, useful top-bar alert counts with correct provenance, no 54-item flat navigation recurrence.
5. **Spend/security honesty:** no surprise paid calls or new subscriptions; tests must show fake/live and local/cloud; retain privacy decisions and minimize notifications.

## Optional V4.5 integration proposal — Any.do MCP (#140; NOT authorized for implementation)

**Owner request recorded 24 Oct 2026.** Add [the standalone Any.do MCP plan](../../plans/V4_5_ANYDO_MCP_INTEGRATION.md) as a **P2 optional candidate** for the V4.5 planning backlog, after priority security/CI/QA stabilization. **No change to NOW authorization, release gate, existing seven streams, or already-running agent scope.** This is deliberately not a new V4 feature bucket or an excuse to broaden V5/V7.

Use the official Any.do beta MCP endpoint with OAuth 2.0, not scraping or reuse of ChatGPT connector credentials. Proposed first cut: read tasks/list → explicitly confirmed task/shop-item creation and reminders; completion/clear/edit requires provider-supported human **Execute all** preview and is blocked until verified live. No shadow task store, autonomous polling, credential exposure or task deletion. Track work and feasibility gates in [Issue #140](https://github.com/Jarvis-igerman-ai/jarves/issues/140) and the dedicated plan. Only a **separate owner decision reflected in NOW** can start development.

## Delivery sequence and tracked dependencies

| Package | Priority | Depends on | Delivery gate |
| --- | --- | --- | --- |
| 0. Inventory + baseline | P0 | latest main + authenticated inventories | issue/alert matrices, CI/release status snapshot, no changes to issue state yet |
| 1. Security source fixes | P0 | 0 | independent review, targeted tests, no new severe CodeQL alerts |
| 2. Evidence-backed Issue closures | P0 | 0; relevant verified fixes / installed gates | comment+link+close only fully satisfied Issues |
| 3. Recurring security/quality scan | P0/P1 | 0–1, current CI nightly | opt-in configuration, scheduled/dry-run evidence, dedupe, budget |
| 4. Guided QA + Skill | P1 | existing #60 signoff backend + #58 entry | shared state, real-user acceptance, updated release gate |
| 5. Camera improvements | P1 | camera current contract + QA test framework | real webcam consent, UX and latency tests |
| 6. Merge/CI polish | **Early P0 enabling slice**, then P1 polish | #89 learnings, baseline and security gates; starts alongside packages 0–1 | integrated main green, fewer measured conflicts and faster small PRs without bypass |
| 7. Reliability extras + sign-off | P2 | earlier packages | installed beta, manual sign-off, rollback, report |

**Parallelism:** security remediation, camera research/design and QA architecture may proceed in independent worktrees **only after an implementation package is explicitly authorized**; PR integration remains serialized when shared files overlap. Ship in small reviewable slices; do not wait to fix urgent security defects for all V4.5 features to finish.

## Open implementation decisions (not blockers for recording this plan)

- Cadence / selected weekdays and report destination for scheduled scans; suggested daily light + weekly full, **not turned on** until owner config.
- Whether security automation may draft repair PRs (proposed default: yes only after user enables, with no auto-merge); whether to require manual approval on every finding.
- Camera: which local device is authoritative, preview lifetime and whether explicit still images may be sent to a provider; defaults retain current privacy and consent.
- Scope/size of baseline QA human test run; use #60 owner's earlier baseline vs smoke decision, not a new test-sheet policy.
- External API privileges to fetch the **full** CodeQL/Dependabot/secret-scanning inventory (currently unavailable in ChatGPT connector); no pretending the screenshot is complete.

## Definition of done (V4.5)

- [ ] All open Issues inventoried; closures have linked verified evidence, no deferred/acceptance work accidentally lost
- [ ] All accessible alerts inventoried, actionable High/Critical resolved or owner-accepted with expiry; CodeQL run on release SHA; low/medium tracked
- [ ] Configurable scan runs with owner-selected daily / several-weekdays / weekly cadence; diff/dedup; non-sensitive findings and truthful reports
- [ ] Camera works on the owner's real approved Windows device, on-demand lifecycle and clear EN/HE responsive UX
- [ ] Dynamic HTML QA workspace + one shared sign-off service + voice Skill; new feature appears automatically; human vs automatic results separated
- [ ] Code integration faster and safe, review/CI/security gates measured, main and package green
- [ ] Existing #58/#60/#61/#77 handoffs correctly reflected, security and performance regressions absent, build/rollback/owner sign-off recorded
- [ ] Docs, guides, DOC_MAP, ROADMAP, NOW, issue statuses and release notes truthful; no unauthorized scope expansion to V5–V7 or physical hardware
