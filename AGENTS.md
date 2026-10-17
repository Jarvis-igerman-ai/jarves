# JARVIS — agent contract (Claude Code / Codex)

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
- JARVIS is currently stationary. NO motors, servos, wheels, drive simulation or navigation.
- CAD model (FreeCAD) defines geometry; Blender creates visual assets; Godot simulates interactions; Python Core handles behavior; physical I/O behind one DevicePort boundary.
- The same *face scene/state machine* should be reused on the future device if Godot on Raspberry Pi passes performance tests. Blender/FreeCAD never run on-device.
- Do not implement later stages. `docs/NOW.md` alone grants current scope.

## End-of-task response: 6 lines max
Outcome; changed files; one command to reproduce; check actually run/result; blockers/UNVERIFIED; proposed next task (NOT started).
Update `docs/HANDOFF.md` succinctly, then stop for user approval.
