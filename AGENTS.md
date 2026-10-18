# JARVES — agent contract (Claude Code / Codex)

## Read budget
- First read **only** `AGENTS.md` and `docs/NOW.md`. Do not recursively read docs, browse the archive, or scan the entire repo.
- If the task requires more information, open **only the specific file** named in `docs/CONTEXT_MAP.md`. Never load documents "just in case".

## Model selection and token budget — Claude Code AND Codex
- **Reasoning-first, execution-efficient:** use the strongest *actually available* model for bounded architecture, difficult mechanical/design decisions, and stubborn cross-layer failures. Use an appropriate faster/cheaper available model for routine edits, exports, focused tests and documentation. Do not assume Claude model names or subscription quotas apply to Codex; use equivalent roles, not fictional cross-provider model IDs.
- Prefer **one implementing owner** and a compact approved plan. Avoid repeatedly switching models when reloading context would negate savings; a hard task may be cheaper end-to-end on a stronger model.
- Escalate to stronger reasoning only when warranted by complexity or repeated failure (within the two-attempt debugging limit). No automatic premium-model chains or duplicated agent reviews.
- Both Claude Code and Codex must use **the same GitHub Issues, commits, docs/NOW.md, docs/HANDOFF.md and source-of-truth pipeline**. Handoff between tools: stop current writer, commit a clean state, record tests/blockers and next action, then let the other tool pull and resume. Never have both agents editing the same files concurrently.
- Neither agent can inspect the other's remaining quota without explicit available data. Do not invent token savings or model availability. Report model used when known; user decides whether to spend premium quota.

## Deliverable evidence and cost-aware delegation (mandatory)

- For every **visual or mechanical review milestone**, automatically export reproducible **actual project** screenshots/previews (front, side, rear, 3/4, internals, selected finish) and provide direct repository file links in the handoff. The user should not have to manually capture standard review angles. Check generated assets exist and correspond to the current commit; distinguish automatic headless screenshots from hands-on UI confirmation. Do not substitute invented/AI-generated concept art for project renders.
- When assembly/printability is the approved task, deliver a legible exploded view and a concise printed-vs-purchased-vs-placeholder part inventory with print orientation, joining sequence, fasteners, access and unresolved fit risks. Assembly animation only if feasible and specifically approved; no manufacturing claim without physical tests. Avoid generating redundant large render sets.
- **Division of work:** reserve a premium reasoning model (e.g. Claude Opus when available) for difficult architecture, product design judgement, root-cause/engineering decisions and final bounded critique. Prefer Sonnet or an appropriately cheaper capable model for scoped CAD/Blender/Godot modifications, exports, screenshots, builds, routine testing and documentation. Use a cheaper lightweight model (e.g. Haiku when available) for log triage, formatting, file inventories and repetitive reporting — not as an unverified substitute for CAD/physics/critical code review.
- **Subagents are optional, not automatic.** Delegate only narrowly specified, independently checkable, read-only or non-overlapping tasks when parallelism clearly saves cost/time. One writer owns the integration, no concurrent modifications of the same files, no duplicated context-loading, no multi-agent debates or unnecessary reviews. Never assume model routing/subagent selection is supported; report the model actually used and fall back to single-agent execution.
- A requested image/export is **not** evidence of a successful real run until its files and source synchronization are checked. Use targeted checks and report failure; do not inflate cost by rerunning the entire rendering pipeline.

## Best-in-class technology (mandatory — user decision, 18 Oct 2026)
- For **every stage** (AI models, voice, speech, agents, libraries, tools, CAD/render/print tooling, hardware candidates), **research the newest best-in-class option before choosing**: check official docs/model lists/changelogs dated now, not training memory. Never default to older generations (e.g. gpt-4o-era models) when a newer recommended one exists.
- Prefer one coherent, maintained vendor stack where it is best (currently **OpenAI**: Agents SDK, GPT-Live voice, GPT-6 family backend, hosted web search) over mixing vendors without a measured quality reason.
- Cloud/paid APIs are acceptable for quality; local components must be best-in-class or clearly labelled **fallback**. Target runtime is Raspberry Pi 5 — heavy local models are fallback only.
- Record the chosen model/library IDs, the date checked and the source link in the relevant decision doc; re-check at each milestone and upgrade when a better recommended version appears. Keep costs metered and keys in the environment only.

## Execution rules (mandatory)
1. **Only approved scope per session.** Work only within the scope and files authorized by `docs/NOW.md`. Normally finish one approved task and STOP; **when NOW explicitly authorizes a bounded multi-stage/multi-Issue work package (such as SW-V1), autonomously proceed through its defined phases and commit checkpoints, stopping only for material blockers or its final review gate.** Do not auto-start anything beyond the approved bundle.
2. Before writing code, output **up to five short plan steps**, impacted files and **one verification action**. If requirements are ambiguous or unverified critical data is needed, ask first.
3. No unrequested features, refactors, abstractions, frameworks, migrations, future hardware adapters, "nice-to-have" UI, or unrelated cleanup. Suggest separately, do not implement.
4. Prefer smallest working change. Reuse code. Do not turn a simple task into a multi-agent operation.
5. Tests: **one relevant targeted test/check after the change**. Run broader tests only for cross-cutting changes or explicit request. Never continuously rerun builds, full suites or rendering jobs.
6. Debug budget: **max two distinct fix attempts** for a blocker; then stop, report evidence and ask for direction. Never loop or silently expand scope.
7. Tool/context budget: use bounded searches, targeted file reads, concise outputs, no giant logs or repeated repo exploration. Avoid installs/downloads/expensive model calls unless needed for the approved task; ask if materially costly.
8. Do not claim success without evidence. Mark unknown mechanical dimensions `UNVERIFIED`; no physical 1:1 accuracy claim before measuring actual components and test fitting.
9. Never commit credentials. Keep external network access, microphone/camera permissions and destructive changes under explicit control.
10. Claude Code and Codex: **one implementing owner at a time**. Optional second-agent review only when requested, based on a specific diff; never concurrent conflicting edits.

## Future runtime operability gate (mandatory when software scope is approved)
- Before implementing any Python Core, DevicePort, admin UI, networking or runtime service, read `docs/JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md`. From the **first running Core↔Godot integration**, include bounded structured local logs, truthful health/error states, targeted disconnect/recovery tests, privacy-safe diagnostic export and a documented no-network troubleshooting path appropriate to the approved slice. Do not postpone all observability to the final hardware build or add heavyweight monitoring infrastructure. This rule **does not authorize** software work during the current mechanical milestone.

## Design constraints
- For design, sourcing, printability, assembly, acoustics, component selection or service tasks, read `docs/PROJECT_PRINCIPLES.md` after the two default entry files. Explain physical choices to the user in plain language and flag what must be physically tested.
- The user buys hardware in Israel: require credible local stock, VAT-inclusive ILS price, warranty, delivery and available substitutes. Import-only options are fallback, never assume local availability. No final SKU or purchasing approval yet.

- JARVES is currently stationary. NO motors, servos, wheels, drive simulation or navigation.
- CAD model (FreeCAD) defines geometry; Blender creates visual assets; Godot simulates interactions; Python Core handles behavior; physical I/O behind one DevicePort boundary.
- The same *face scene/state machine* should be reused on the future device if Godot on Raspberry Pi passes performance tests. Blender/FreeCAD never run on-device.
- Do not implement later stages. `docs/NOW.md` alone grants current scope.

## Change propagation — mandatory
- For every user-reported issue, identify the **owning source layer** before editing: FreeCAD = physical geometry, dimensions, printability and mounts; Blender = materials, visual finish and exported asset appearance; Godot = viewer interactions, rendering and runtime UI; Python Core/DevicePort = behavior and hardware abstraction when those stages exist.
- **Feedback may travel backward** to any upstream owner. Apply the fix at the authoritative layer, then regenerate/reimport **only affected downstream outputs** and run focused checks at changed boundaries. Never patch a geometry/fit defect solely in Blender or Godot.
- No need to propagate a local-only fix upstream: Godot UI/camera changes stay in Godot; Blender-only material changes need only refresh downstream visuals, not FreeCAD.
- On handoff describe: problem → root cause/owning layer → files changed → downstream artifacts refreshed → checks actually passed or not tested. Mark unverified physical claims explicitly.
- Don't silently expand the approved milestone while propagating a correction; request approval if a necessary fix materially changes scope.

## Closeout housekeeping
- On milestone completion, record acceptance evidence and linked commit/PR in the Issue; close completed Issues, leave blocked/unfinished ones open with reason. Avoid duplicate Issues and check stale open work periodically.
- Review changes for tracked caches, duplicate generated files, temporary artifacts and scratch outputs; ignore or clean **only clearly safe** disposable artifacts in scope. Never do sweeping deletion of source, essential renders or historical data without approval. Update README links if authoritative file locations change.
- Spoken-distance expectations are undecided; earlier 3–5 m is only an exploratory research scenario, not a fixed requirement.

## Mandatory question handling at every handoff

- At every milestone completion, review **every** question left by the implementing agent, including questions carried forward from previous handoffs. Never silently omit one or treat a proposed default as user approval.
- Maintain a compact **Decision queue** in `docs/HANDOFF.md`: question, agent recommendation with one concrete rationale/tradeoff, status (**ANSWERED / USER DECISION REQUIRED / DEFERRED / BLOCKED**), and the recorded answer or exact next gate. Resolve technical/operational questions using available project evidence and existing user decisions; do not repeatedly ask questions already answered.
- **User-owned choices** (appearance, personality, purchases, final component lock, budget and physical risk acceptance) require real user confirmation. Offer an informed provisional recommendation, but do not silently choose on their behalf. Questions that cannot be judged without trying the interactive viewer stay **USER DECISION REQUIRED**.
- **Deferred questions** must state why they are not needed for the active milestone and which future stage will revisit them. A deferred question does **not** block unrelated work.
- The orchestrating/review agent must explicitly address every handoff question before authorizing further implementation: answer from established evidence, get the minimum necessary user decision in one grouped review, or explicitly defer it. No unanswered question may disappear between Claude Code, Codex and ChatGPT.
- Keep the user-facing closeout short: summarize only material pending decisions and link the complete queue rather than repeating a long questionnaire. Do not start the next milestone without its required approvals.

## End-of-task response: 6 lines max
Outcome; changed files; one command to reproduce; check actually run/result; blockers/UNVERIFIED; proposed next task (NOT started).
Update `docs/HANDOFF.md` succinctly, then stop for user approval.
