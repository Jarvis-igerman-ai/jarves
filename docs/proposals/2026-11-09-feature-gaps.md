# Feature gap analysis: scope and roadmap vs shipped code (JAR-66)

Date 2026-11-09. Base: `origin/main` 9571c1c7. Proposal only: no code changed, nothing here authorises work.
Method: `docs/NOW.md`, `docs/ROADMAP.md`, `core/src/jarves/workflow/version_plan.json` (94 requirements), open GitHub issues labelled v5/v6/v7, then each candidate checked against the code. Capability counts come from importing every module of a `main` snapshot and calling `features.stage_of()` / `capabilities.view()`.
No device, phone, car or live sign-in was exercised. Every device outcome below is UNVERIFIED.

## Main finding

**The tracker overstates what is unbuilt and understates what is unproven.** Most open roadmap issues are already merged and wait only for a real-device check. The largest gap is proof, not code.

| Measure (`main`) | Count |
|---|---|
| Features in the catalog | 113 |
| Stage `beta` / `stable` | 102 / 11 |
| Windows rows `verified_on_device` | 7 (music, wake_word, greeting, chimes, background_core, display_control, tray_icon) |
| Windows rows `implemented_unverified` | 104 |
| Phone or car rows `verified_on_device` | 0 |

## Decision needed first: `docs/NOW.md` has no product scope

NOW authorises only the workflow ledger and console work, which was deprecated on 8 Nov (banner in `docs/workflow/OPERATING_MODEL.md`). AGENTS.md rule 1 limits work to NOW's scope, and `VERSION_MAPPING_2026-10-30.md` says imported scope is not permission. So **none of the gaps below can start until the owner names them as scope.** ROADMAP also still calls V7 "proposal only", while the mapping records approval on 25 Oct (conflict 1 in that file).

## Ranked gaps

| # | Gap | Size | Benefit | Tier |
|---|---|---|---|---|
| 1 | No owner acceptance path outside release sign-off | M | High | N if kept apart from sign-off |
| 2 | Offline sync: Core store has no route and no caller | M | High for V7 offline | **R** (`mobile.py`) |
| 3 | Android app has no tasks screen | S-M | Medium | Run `risk_class` on the final file list |
| 4 | Phone capability rows and Android sheet are stale | S | Medium, cheap | N |
| 5 | No phone-side performance measurement | S-M | Medium | N if client-only |

### 1. No owner acceptance path outside release sign-off

- **Evidence:** the table above. `verified_on_device` appears only in hand-written declarations (`capabilities.py`, `appearance.py`, `runtime/__init__.py`, `features.py`); no CLI command or tool records a result. `docs/reviews/ANDROID_COMPANION_ACCEPTANCE.md` has 21 `NOT-RUN` markers. The `signoff_steps` wizard already has the right interaction (one check per screen; Works / Does not work / Cannot test now) but works only on a release sheet.
- **Merged, waiting on a device or live check (triage comments or code on `main`):** #390, #391, #392 (approvals and share parts), #399, #140; code also exists for #357 (`dashboard.enable_phone`), #364 (`triage.py`), #365 (`ui_blocks.py`), #336 (`diag/`). #510 needs the owner's phone retest. Requirements V5-LIVE-AGENTS and V5-LIVE-GOAL also need a live run and have no linked issue.
- **Proposal:** a short owner test list, ordered by value, reusing the wizard's interaction. Results are saved locally with date, device and one line of evidence. Moving a capability row to `verified_on_device` stays a reviewed PR.
- **Cost / benefit:** about two small PRs, plus guide, parity and Hebrew strings. It turns scattered owner effort into a list and shows which of about 100 unproven features matter.
- **Risks:** an agent must not be able to write a pass (same concern as #738), so recording needs an owner-only surface. It must not touch release sign-off files, which are Tier R. Results stay local, since evidence may hold personal data.
- **Acceptance:** owner sees the next untested item with exact steps; a result survives restart; an agent-context call cannot record a result (test); no capability status changes automatically; sign-off sheet files are unchanged in the diff.

### 2. Offline sync: Core store has no route and no caller

- **Evidence:** `core/src/jarves/offline_sync.py` is imported only by `core/tests/test_offline_sync.py`. `GET /api/m/sync` in `mobile.py:853` is the unrelated continuity poll (turns, notices, goals), despite the shared name. #393 (V7-A5) asks for a "sync endpoint" and is open with no comments.
- **Boundary with JAR-40:** PR #985 is Android-only and states that sync transport and UI are "PR 2". This gap covers only the Core route and scope. Its contract must be agreed with JAR-40 PR 2 so the two sides are not designed separately.
- **Without it:** notes and reminders stay on the phone and the PC never sees them.
- **Open question:** whether synced items stay in their own store or map to an existing Core feature. Not settled by the code reviewed here.
- **Cost / benefit:** one route, a scope, tests. Completes V7-A5 and makes V7-A6 useful beyond one device.
- **Risks:** `risk_class.py` reports `mobile.py` as a high-risk path, so this is **Tier R: owner decision, Opus review, no auto-merge.** New authenticated write path from the phone; tombstones must stay content-free (already so in `offline_sync.py`); needs a size and rate limit. Use a distinct path name to avoid confusion with `/api/m/sync`.
- **Acceptance:** fake-client tests for auth, missing scope, oversize body, and repeated or reordered merges converging; a deleted note does not return; two devices converge. Real phone: UNVERIFIED.

### 3. Android app has no tasks screen

- **Evidence:** #392 (V7-A4) is titled "tasks and approvals screens". Its triage comment records only the approvals card and share intent as merged (#694). A case-insensitive search for `task` in `android/app/src/main/kotlin` finds nothing. The Core API already exists (`GET /api/m/plans`, `POST /api/m/plans/op`, `POST /api/m/goals/op`, documented in `docs/api/companion_v1.md`).
- **Cost / benefit:** client-only, reuses `Net.kt` and the approvals UI pattern. Medium value, and it matters mainly if the native app becomes the daily client.
- **Risks:** `tasks` is an optional per-device scope, so the screen must handle its absence. Plan delete stays PC-only as the contract says.
- **Acceptance:** JVM tests for list, each op, and scope-missing state; Hebrew strings present; `docs/reviews/ANDROID_COMPANION_ACCEPTANCE.md` rows added. Real phone: UNVERIFIED.

### 4. Phone capability rows and Android sheet are stale

- **Evidence:** `journey`, `drive_briefing` and `drive_mode` phone rows are `planned` and cite #218, which is closed, while `/api/m/drive`, `/api/m/drive/journey`, `/api/m/drive/briefing` and `/api/m/drive/action` exist. The `audio_route` phone row says hands-free "is not built", while `dashboard_static/mobile/hands_free.js` and the `phone_hands_free` feature exist. The Android sheet (28 Oct) still says the share and offline-screen parts are not built and describes push-to-talk as in review, though #690, #700 and #694 are recorded as merged.
- **Cost / benefit:** data and text only, roughly one small PR. Product status screens stop contradicting the code.
- **Risks:** moving rows must land on `implemented_unverified` with a `NOT-RUN` blocker, never `verified_on_device`. Check each row individually: `drive_mode` may correctly remain limited, since its row states the phone cannot switch the mode.
- **Acceptance:** each row cites a real test as evidence; no phone row claims verification; `test_capabilities` and `test_v5_capability_evidence` pass; sheet rows move from `NOT-BUILT` to `NOT-RUN` only for merged parts.

### 5. No phone-side performance measurement

- **Evidence:** V5-PHONE-PERF is a required device-level item with no linked issue. A search of `dashboard_static/mobile` and the Android Kotlin for timing code (`performance.now`, latency, elapsed) finds nothing. The `2026-11-04-perf-baseline.md` method used no microphone or voice, and `diag/monitor.py` measures desktop turns. JAR-65 covers only idle and wake CPU, memory and log size.
- **Proposal:** client-side timers for send-to-first-reply and push-to-talk round trip, kept on the device and shown in the existing health view.
- **Cost / benefit:** small, and gives a number for "the phone feels slow" reports.
- **Risks:** timings only, no content. A report route on the Core would touch `mobile.py` and become Tier R, so start client-only.
- **Acceptance:** hermetic timer tests; no text or audio in the recorded data; real values UNVERIFIED until captured on the owner's phone.

## Considered and left out

- **Covered elsewhere:** JAR-40 Android offline (#394), JAR-55 receipts (V7-X1 #420), JAR-65 idle performance.
- **Owner or hardware gated:** #194 trusted certificate, #193 phone sheet, #407 and #419 paid live trials, #414 standing approvals, #3 and #4 Raspberry Pi and physical design.
- **Deferred by the owner:** #358 face recognition.
- **Not assessed:** #331 terminal chat against its full wish list. Evidence pack C already flags its `stable` stage as a risk.
- **Workflow issues** (#732 and similar) target the deprecated system.

## Recommended order

1. Owner updates NOW with product scope (decision above).
2. Gap 4, as it is cheap and corrects what the product reports.
3. Gap 1, so the existing backlog of built features can be proven.
4. Gap 2 as an owner-reviewed Tier R change, timed with JAR-40 PR 2.
5. Gaps 3 and 5 if the owner confirms the native app is the daily client.

Also worth reconciling: 18 requirements still have no granular issue mapping (`VERSION_MAPPING_2026-10-30.md`).
