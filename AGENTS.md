# JARVES — agent contract (Claude Code / Codex)

## Read budget
- First read **only** `AGENTS.md` and `docs/NOW.md`. Do not recursively read docs, browse the archive, or scan the entire repo.
- If the task requires more information, open **only the specific file** named in `docs/CONTEXT_MAP.md`. Never load documents "just in case".

## Execution rules (mandatory)
1. **One approved task per session.** Work only within the scope and files of `docs/NOW.md`. When done, STOP; do not auto-start the next task.
2. Before writing code, output **up to five short plan steps**, impacted files and **one verification action**. If requirements are ambiguous or unverified critical data is needed, ask first.
3. No unrequested features, refactors, abstractions, frameworks, migrations, future hardware adapters, "nice-to-have" UI, or unrelated cleanup. Suggest separately, do not implement.
4. Prefer smallest working change. Reuse code. Do not turn a simple task into a multi-agent operation.
5. Tests: **one relevant targeted test/check after the change**. Run broader tests only for cross-cutting changes or explicit request. Never continuously rerun builds, full suites or rendering jobs.
6. Debug budget: **max two distinct fix attempts** for a blocker; then stop, report evidence and ask for direction. Never loop or silently expand scope.
7. Tool/context budget: use bounded searches, targeted file reads, concise outputs, no giant logs or repeated repo exploration. Avoid installs/downloads/expensive model calls unless needed for the approved task; ask if materially costly.
8. Do not claim success without evidence. Mark unknown mechanical dimensions `UNVERIFIED`; no physical 1:1 accuracy claim before measuring actual components and test fitting.
9. Never commit credentials. Keep external network access, microphone/camera permissions and destructive changes under explicit control.
10. Claude Code and Codex: **one implementing owner at a time**. Optional second-agent review only when requested, based on a specific diff; never concurrent conflicting edits.

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

## End-of-task response: 6 lines max
Outcome; changed files; one command to reproduce; check actually run/result; blockers/UNVERIFIED; proposed next task (NOT started).
Update `docs/HANDOFF.md` succinctly, then stop for user approval.
