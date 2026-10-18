# Current handoff — 18 October 2026

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
