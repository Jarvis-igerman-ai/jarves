# V7 work package (planning plus execution), 26 October 2026

> **Status: owner APPROVED V7 for planning AND execution on 25 Oct 2026 ("i approve v7"; see the V7 lines in the owner's open-items file and [issue #50](https://github.com/Jarvis-igerman-ai/jarves/issues/50)). Execution order is gated: V4.5 / V5 / V6 gates in [NOW](../NOW.md) keep priority for PR slots.** Scope source: [V7 proposal](../archive/plans/V7_PROPOSAL_2026-10-21.md). Architecture invariant from the proposal still holds: the Python Core, the existing provider contract, DevicePort/Skills, approvals and the Orchestrator stay authoritative; V7 adds adapters and presentation layers, never a second Core, memory, approval ledger or GitHub provider.
>
> Not V7 (queued separately by the owner): smart web chat (#365) and tasks-from-messages (#364). Face recognition (#358) is future backlog. V6 car/audio/Skills pillars stay in V6.

## 1. Tracks and order

| Track | Owner item | What ships | Priority | Starts when |
| --- | --- | --- | --- | --- |
| G GitHub (full read+write, gated) | 5 | Read client, then write framework, then writes, then queue-only merge | P1 | first (no device needed) |
| M Model Lab and spend policy | 6 | Registry and hard spend policy first, OpenRouter adapter, eval harness, dashboard | P1 | first (policy must exist before any other new provider path) |
| D OpenCode harness | 4 | Read-only adapter, then third harness under the Orchestrator | P1 | first (D1 needs nothing) |
| A Android daily companion | 1 | PC-hosted companion app, then voice/notifications, then offline basics | P0 for the vision, but device-gated | A0 now (docs); A1+ after V5 phone acceptance |
| B Browser Studio | 2 | Read-only live view, then human takeover, then opt-in recording | P1 | after B1; B2+ need Opus review slots |
| R Research | 3, 7 | OpenClaw/alternatives and Buzz studies (docs only) | P2 | any free docs slot |
| X Extras backlog | 8 | V7.x list, pick 1-2 later | P3 | after tracks G, M, D, A |

Order of PRs (small slices, each at most about 400 changed lines, one PR lane at a time, max 4 open PRs overall as in the owner flow):

1. First three executed: **G1** (read-only GitHub client), **D1** (OpenCode read-only adapter), **M1** (ModelRegistry and SpendPolicy). They need no device, no login and no paid call. Docs filler in parallel when a slot is free: A0, R1, R2.
2. Then: G2, M2, D2, B1, A1 (A1 only after V5 phone acceptance).
3. Then: G3, M3, D3, B2, A2, G6.
4. Then: G4, M4, B3, A3, A4, A5.
5. Then: G5, B4, B5, A6, G7 (decision), A7.
6. Live rows last and owner-gated: D4, M5, B6 decisions.

Slices may not be merged out of dependency order. A slice opens as a DRAFT PR, becomes Ready only after `python core/tools/prepush.py` and its own tests pass, then follows the normal green-means-queue flow. No slice weakens an existing check.

## 2. Slices (one GitHub issue each, label `v7`; no V7 milestone exists in the repo, so none is set; #50 is the umbrella)

**Android**

| Slice | Issue | What | Size | Depends on | Tests (fakes first) | Review gate | Owner-gated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A0 | [#388](https://github.com/Jarvis-igerman-ai/jarves/issues/388) | Android companion ADR: native Kotlin vs reuse of the V5 phone web client; foreground-service, mic and notification limits | docs | V5 phone acceptance (#193) | docs links test | Sonnet | - |
| A1 | [#389](https://github.com/Jarvis-igerman-ai/jarves/issues/389) | Core companion API contract: status, tasks, approvals, reminders over the existing pairing/Tailscale link (no new auth) | ~350 | A0 | fake-device HTTP tests, auth/replay tests | Opus (touches pairing and approvals) | - |
| A2 | [#390](https://github.com/Jarvis-igerman-ai/jarves/issues/390) | Android app shell (Compose): pair, status, PC-offline banner; CI builds a debug APK only | ~400 | A0, A1 | assembleDebug in CI; Core fake server contract test | Sonnet | Device test on the dedicated test phone (adb sheet) |
| A3 | [#391](https://github.com/Jarvis-igerman-ai/jarves/issues/391) | Push-to-talk voice and notifications: mic service started only from a visible activity or notification; poll first, FCM only if the owner links Firebase | ~400 | A2 | unit tests with fake audio; adb sheet rows | Opus (mic permission) | New permission prompts (mic, notifications) on the phone; FCM account optional |
| A4 | [#392](https://github.com/Jarvis-igerman-ai/jarves/issues/392) | Share-to-JARVES intent plus tasks and approvals screens (approve/skip as in V5; delete stays PC-only) | ~380 | A2 | JVM tests plus adb rows | Sonnet | Phone rows |
| A5 | [#393](https://github.com/Jarvis-igerman-ai/jarves/issues/393) | Offline basics, Core side: sync endpoint and conflict rules for reminders, timers, notes (last-writer-wins with tombstones, per-item version) | ~350 | A1 | property tests on merge, fake clients | Sonnet | - |
| A6 | [#394](https://github.com/Jarvis-igerman-ai/jarves/issues/394) | Offline basics, phone side: local store, exact-alarm reminders and timers, notes; sync when the PC is reachable | ~400 | A2, A5 | JVM tests, adb rows (airplane mode) | Sonnet | Exact-alarm permission prompt on the phone |
| A7 | [#395](https://github.com/Jarvis-igerman-ai/jarves/issues/395) | Android acceptance sheet and guide (EN/HE), release notes, battery/OEM matrix | docs | A3-A6 | docs and guides tests | Sonnet | Owner signs the phone sheet |

**Browser Studio**

| Slice | Issue | What | Size | Depends on | Tests (fakes first) | Review gate | Owner-gated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1 | [#396](https://github.com/Jarvis-igerman-ai/jarves/issues/396) | Session model and event stream on the existing browser skill: states AGENT/HUMAN/PAUSED/STOPPED, action receipts, no new engine | ~350 | - | fake page driver, state-machine tests | Opus (gate/approvals) | - |
| B2 | [#397](https://github.com/Jarvis-igerman-ai/jarves/issues/397) | Read-only live view pilot: screencast of the dedicated Playwright context to the dashboard, loopback plus per-session token, Stop button; test site only | ~400 | B1 | local test-site E2E in CI (headless), token/expiry tests | Opus (new network surface) | Owner watches once on his PC |
| B3 | [#398](https://github.com/Jarvis-igerman-ai/jarves/issues/398) | Human takeover and return in the SAME session: agent input locked while HUMAN, page re-read on return, nothing the human types reaches a model prompt | ~400 | B2 | fake-page and local test-site tests; stale-ownership and injection tests | Opus (credential handling) | Real login test is owner-run on a throwaway account |
| B4 | [#399](https://github.com/Jarvis-igerman-ai/jarves/issues/399) | Phone viewer for live view and takeover (reuses the A2 shell or the phone web page) | ~300 | B3, A2 | adb rows | Sonnet | Phone rows |
| B5 | [#400](https://github.com/Jarvis-igerman-ai/jarves/issues/400) | Opt-in recording: Playwright per-context video, default OFF, pause at password/card/OTP fields, retention and delete, honest format label | ~350 | B3 | fake recorder, masking tests, retention tests | Opus (secrets) | Owner opt-in per session |
| B6 | [#401](https://github.com/Jarvis-igerman-ai/jarves/issues/401) | ADR: Browser Use cloud, Cloudflare browser, Aether vs local Playwright (cost, privacy, lock-in); cloud stays optional and OFF | docs | B2 | docs links test | Sonnet | Any cloud sign-up or spend |

**Research**

| Slice | Issue | What | Size | Depends on | Tests (fakes first) | Review gate | Owner-gated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | [#402](https://github.com/Jarvis-igerman-ai/jarves/issues/402) | OpenClaw, nanobot, IronClaw study: adopt/adapt/reject table for tools, skills lifecycle, approvals and sandboxing ideas (docs only, no installs) | docs | - | docs links test | Sonnet | - |
| R2 | [#403](https://github.com/Jarvis-igerman-ai/jarves/issues/403) | Buzz by Block investigation: confirm identity, license, hosting burden; adopt a pattern, build a narrow ACP bridge, or reject (docs only) | docs | - | docs links test | Sonnet | Owner confirms this is the product he meant |

**OpenCode**

| Slice | Issue | What | Size | Depends on | Tests (fakes first) | Review gate | Owner-gated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D1 | [#404](https://github.com/Jarvis-igerman-ai/jarves/issues/404) | OpenCode read-only adapter: health, sessions, event stream (SSE) against a fake server; loopback and password only | ~350 | - | fake server tests, auth-required tests | Sonnet | - |
| D2 | [#405](https://github.com/Jarvis-igerman-ai/jarves/issues/405) | OpenCode as third harness in the Orchestrator: start/stop session, send prompt only after approval showing target repo/branch/session; own worktree | ~400 | D1 | fake server tests, policy tests, cli parity | Opus (orchestrator gate) | - |
| D3 | [#406](https://github.com/Jarvis-igerman-ai/jarves/issues/406) | Per-agent model/profile and cost cap for OpenCode; status page rows; guide EN/HE | ~300 | D2, M1 | tests with fakes, test_guides, test_cli_parity | Sonnet | - |
| D4 | [#407](https://github.com/Jarvis-igerman-ai/jarves/issues/407) | Live smoke: one small real OpenCode task with a free or capped model | live | D3, M2 | manual run, transcript in handoff | - | Owner go and cap (default 1 USD, free-only first) |

**GitHub**

| Slice | Issue | What | Size | Depends on | Tests (fakes first) | Review gate | Owner-gated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | [#408](https://github.com/Jarvis-igerman-ai/jarves/issues/408) | Read-only GitHub client: fine-grained token store (local, redacted), repos/issues/PRs/checks reads, rate-limit handling, fake API | ~400 | - | fake API tests, redaction tests | Opus (token storage) | Owner creates a read-only fine-grained token (guide) |
| G2 | [#409](https://github.com/Jarvis-igerman-ai/jarves/issues/409) | Write framework, nothing written yet: write switch (default OFF), exact-action approval payload, audit log of every write, per-repo allow-list | ~400 | G1 | approval schema tests, audit tests, switch-off tests | Opus (approvals) | - |
| G3 | [#410](https://github.com/Jarvis-igerman-ai/jarves/issues/410) | Writes class 1: create/edit issue, comment, labels (approval each, batch per task allowed) | ~350 | G2 | fake API write tests, refusal tests | Opus first time, then Sonnet | Token gets Issues write for the chosen repos |
| G4 | [#411](https://github.com/Jarvis-igerman-ai/jarves/issues/411) | Writes class 2: open/update PR, PR comments and reviews, CI re-run; never touches branch protection, secrets, org or permissions | ~400 | G3 | fake API tests, forbidden-endpoint tests | Opus | Token gets Pull requests and Actions write |
| G5 | [#412](https://github.com/Jarvis-igerman-ai/jarves/issues/412) | Merge only through the merge queue: enqueue or enable auto-merge after required checks; direct merge, force push and bypass are not implemented | ~300 | G4 | fake API tests assert no direct-merge call exists, bypass always false | Opus | Token permission the queue call needs (verify at build time) |
| G6 | [#413](https://github.com/Jarvis-igerman-ai/jarves/issues/413) | Link agents to work: agent -> branch -> PR -> failed check -> next action, dashboard and voice question about what blocks a PR | ~350 | G1, D1 | fake API and fake orchestrator tests | Sonnet | - |
| G7 | [#414](https://github.com/Jarvis-igerman-ai/jarves/issues/414) | Standing approvals per repo and action class (owner decision later), expiry and revoke | ~300 | G5 | tests for expiry, revoke, audit | Opus | Owner decision (default: not enabled) |

**Model Lab**

| Slice | Issue | What | Size | Depends on | Tests (fakes first) | Review gate | Owner-gated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1 | [#415](https://github.com/Jarvis-igerman-ai/jarves/issues/415) | ModelRegistry and SpendPolicy: modes balanced/best/cheap/free-only/local-only, hard caps, NO hidden paid fallback, per-project provider allow-list for private code | ~400 | - | policy tests, fake provider, cap-exceeded tests | Opus (cost and privacy gate) | - |
| M2 | [#416](https://github.com/Jarvis-igerman-ai/jarves/issues/416) | OpenRouter provider adapter behind the provider contract: fake-first, free-only enforcement, resolved-model logging, key stored like other keys | ~350 | M1 | fake HTTP tests, free-only never calls paid | Opus (provider, key) | Owner OpenRouter key, only for live runs |
| M3 | [#417](https://github.com/Jarvis-igerman-ai/jarves/issues/417) | Evaluation harness: Hebrew, tool-call, coding datasets, runner, fake provider, JSON/CSV archive without secrets | ~400 | M1 | runner tests with fake models | Sonnet | - |
| M4 | [#418](https://github.com/Jarvis-igerman-ai/jarves/issues/418) | Dashboard comparison page: quality, latency, cost per accepted task, free vs paid labelled | ~350 | M3 | dashboard tests, EN/HE strings | Sonnet | - |
| M5 | [#419](https://github.com/Jarvis-igerman-ai/jarves/issues/419) | Live trial: free-only, public non-private test cases, small cap | live | M2, M3 | results archive in handoff | - | Owner go; default cap 2 USD total, free models first |

**Extras**

| Slice | Issue | What | Size | Depends on | Tests (fakes first) | Review gate | Owner-gated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| X1 | [#420](https://github.com/Jarvis-igerman-ai/jarves/issues/420) | Extras backlog (V7.x): MCP registry checks, action receipts, workflow recipes, local-model research, skill trust profiles, dashboards; pick 1-2 only after the tracks above | backlog | - | - | - | Owner picks |


## 3. Dependencies between tracks

- M1 (SpendPolicy) is a prerequisite for anything that can call a new paid or free provider: M2, D3, D4, M5. Nothing in V7 may call OpenRouter before M1 merges.
- G2 (write framework) is a prerequisite for every GitHub write; G5 needs G4; G7 needs the owner decision.
- A1 reuses the existing pairing and Tailscale link; it does not add a second authentication path.
- B2 needs B1; B4 needs the A2 shell or the existing phone web page.
- D2 needs D1; D3 needs M1; G6 needs G1 and D1.
- Everything waits for the V5 phone acceptance (#193) only where a real phone is involved (A2+, B4).

## 4. Facts the plan relies on (official sources, checked 26 Oct 2026)

Android (track A):
- Apps targeting Android 12+ cannot start a foreground service from the background except in listed exemptions (a visible activity, a notification or widget interaction, high-priority FCM, boot-completed, exact alarm for a user-requested action, and others). [FGS background-start restrictions](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start)
- Microphone is a while-in-use permission: on Android 14+ a microphone-type foreground service cannot be created from the background, so push-to-talk must start from a visible activity or a notification action. Same page. Consequence: A3 does not promise always-on listening or a hands-free wake word on the phone.
- The `VoiceInteractionService` exemption exists only for an app that holds the assistant role; JARVES does not claim the system assistant role in V7. [VoiceInteractionService](https://developer.android.com/reference/android/service/voice/VoiceInteractionService), [Android 17 background audio changes](https://developer.android.com/about/versions/17/changes/bg-audio)

GitHub (track G):
- Fine-grained token permissions: issues and comments need Issues write (or Pull requests write on PRs), PR create and review need Pull requests write, re-running a workflow needs Actions write, and the legacy merge endpoint is listed under Contents write. [Permissions for fine-grained tokens](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)
- The old `PUT .../merge` endpoint does not support merging with a merge queue; the async merge endpoint can enqueue (`merge_action`), has a `bypass_rules` flag (default false), and returns "enqueued" which is not "merged". [Pulls REST docs](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request) Decision for G5: V7 never calls the direct merge path and never sets bypass; it uses `gh pr merge`/auto-merge or the GraphQL `enqueuePullRequest` mutation, which adds a PR only if it can be queued. [Merging with a merge queue](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/merging-a-pull-request-with-a-merge-queue)
- The exact permission the queue/auto-merge calls need is not stated in those pages: G5 verifies it against the live API with a fake-free dry run on a scratch repo before asking the owner to widen the token.

Browser (track B):
- Playwright can record video per context, but the file is only written when the context closes, and the page does not state the container format, so B5 labels the output by what was actually produced. Automating Chrome's default profile is unsupported: a separate directory is required (matches the existing fresh-context rule in `jarves/browser.py`). [Playwright launchPersistentContext](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-persistent-context)
- Browser Use cloud: live view URL is a credential, read-only viewing is a UI restriction and not server-enforced, recording is off by default and MP4 is available only after the browser stops. This is why cloud stays optional and OFF, and why our own live view enforces read-only on the server (B2) rather than in the page. [Browser Use live view](https://docs.browser-use.com/cloud/browser/live-preview)

OpenCode (track D):
- `opencode serve` starts a headless HTTP server on 127.0.0.1:4096 by default; `OPENCODE_SERVER_PASSWORD` turns on basic auth; endpoints include `/global/health`, `/session`, `/session/:id/message`, `/session/:id/prompt_async`, SSE `/event` and an OpenAPI doc at `/doc`. [OpenCode server](https://opencode.ai/docs/server/) D1/D2 bind to loopback only and refuse to start the client without a password set.

OpenRouter (track M):
- `openrouter/free` picks a free model at random from the currently available free models; you cannot choose which; availability, latency and rate limits vary; a specific free model can be pinned with the `:free` suffix. [Free router](https://openrouter.ai/docs/guides/routing/routers/free-router) Consequence: M2 pins named `:free` models for reproducible evaluation, records the resolved model in every result, and treats `openrouter/free` as an experiment mode only.

## 5. Test plan per track (fakes first)

All slices run in CI with fakes: fake GitHub API, fake OpenCode server, fake OpenRouter, fake page driver and a local test web site, fake Android companion client. No CI job logs in anywhere, calls a paid API or touches a real account. Owner-gated rows are listed per slice in section 2 and collected in section 8.

| Track | Automated (agent) | Owner-gated rows |
| --- | --- | --- |
| G | fake API contract tests; token redaction in logs/dashboard/bundle; write switch OFF refuses every write; forbidden-endpoint test (no merge, no branch protection, secrets, org, permission endpoint can be reached); audit log entry per write | create the token; first real read; first real write approval on a scratch repo; queue enqueue on a scratch repo |
| M | policy tests: free-only never calls a paid model, cap exceeded stops the run, no silent fallback, private-code project never routes to a provider outside its allow-list; harness runs with fake models | OpenRouter key; live trial D4/M5 |
| D | fake server: health, sessions, SSE, auth required, loopback only, approval before send | one real OpenCode task |
| A | Core contract tests; debug APK builds in CI; JVM unit tests; offline merge property tests | adb sheet on the dedicated test phone; permission prompts; airplane-mode rows; owner signs the phone sheet |
| B | local test-site E2E headless; token expiry/revocation; state-machine and stale-ownership tests; prompt-injection page text; recording masking and retention | owner watches live view; takeover login on a throwaway account; opt-in recording |
| R | docs links and DOC_MAP tests | owner confirms the Buzz identity |

## 6. Gates per track

- **Sonnet review (default):** docs, harness, dashboard pages, read-only adapters, tests, guides. Weekly tokens are scarce: Sonnet is the default author and reviewer.
- **Opus-only review (security gate):** anything that touches approvals, the write framework, token or key storage, spend/privacy policy, the mic permission, a new network surface (live view), credential handling (takeover, recording), the Orchestrator gate, the installer or upgrade path. Marked "Opus" in the gate column. One Opus pass per such slice, final diff, before Ready.
- **Track gates:** G opens writes (G3) only after G2 is merged and reviewed by Opus; G5 only after G4; M2 only after M1; B3 only after B2 and an Opus review of the live-view surface; B5 only after a privacy checklist; A3 only after the permission and foreground-service review.
- Every feature slice ships its guide (EN and HE via `core/tools/catalog_add.py`), a CLI parity row and a capability row with an honest status (`implemented_unverified` until owner or device evidence exists); `test_guides` and `test_cli_parity` must pass. Nothing is closed without linked evidence; passing CI is not installed-app acceptance.

## 7. Budget and spend caps

- Default: **zero paid usage in V7 slices**; everything runs on fakes. The existing V4 budget is not extended.
- Live rows: D4 default cap 1 USD (free models first), M5 default cap 2 USD total, both only with the owner's explicit go, both inside the SpendPolicy hard cap from M1. Free-only mode never falls back to paid; there is no hidden paid fallback anywhere.
- Private code and private text go only to providers already approved for that project; free or third-party providers receive public test cases only until the owner approves a provider per project.
- Cloud browsers (Browser Use, Cloudflare) are not used in V7 unless the owner approves a specific spend; local Playwright is the baseline.
- Any slice that needs a cost it cannot cap stops and goes to the owner's open-items file.

## 8. What needs the owner (safe defaults, none blocks the line)

| Need | Used by | Safe default if silent |
| --- | --- | --- |
| Read-only GitHub fine-grained token | G1 live use | fakes only |
| Token with Issues / Pull requests / Actions write for chosen repos | G3-G5 live use | writes stay OFF |
| Standing approval per repo and action class | G7 | not enabled, approval each time |
| OpenRouter key and a spend cap | M2, D4, M5 | no live calls |
| Dedicated test phone adb sheet, permission prompts (mic, notifications, exact alarm), optional Firebase for FCM | A2-A7, B4 | polling, no FCM, no new permissions |
| Real-login takeover test on a throwaway account | B3 | local test site only |
| Opt-in per recording session | B5 | recording OFF |
| Confirm that "buz" means Buzz by Block | R2 | investigate Block Buzz only, no integration |
| Pick 1-2 extras | X1 | none |
| Phone acceptance sheet signature | A7 | A stays beta |

## 9. Hard stops (do not do, even to keep moving)

Paid API beyond approved caps; new device permissions without the owner; live logins or real accounts in tests; private code or private text to any provider not approved for that project; recording secrets (recording pauses at credential fields and is off by default); direct merge, force push, bypassing branch protection or checks; org, permission or secret changes on GitHub; dismissing alerts without evidence; stable release without the owner's signed sheet; no second Core, memory, approval ledger or GitHub provider.

## 10. Proposed `docs/NOW.md` section

The same text is added to NOW.md as a new section "V7 work package" below the V4.5 content (nothing existing is removed): see [NOW](../NOW.md). Summary for agents: execute V7 slices in the order of section 1 when a PR slot is free and the V4.5/V5/V6 gate items are not waiting; first three are G1, D1, M1; docs filler A0, R1, R2; every slice has its issue (label `v7`, umbrella #50); gates and caps as in sections 6-9.

## 11. Open points

- The V7 milestone does not exist in the repo; create it with the owner's OK if wanted (default: label only).
- G5: exact token permission for queue calls to be verified at build time.
- A0 decides native Kotlin vs reuse of the V5 phone client; A1/A2 do not start before that ADR is merged.
