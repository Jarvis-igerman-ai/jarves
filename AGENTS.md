# JARVES: agent contract (Claude Code / Codex)

`Jarvis-igerman-ai/jarves` (remote `jarves`) is the only writable repo; `igalgerman/JARVIS` is deprecated history (no pushes, PRs or issues). Never copy personal logs, keys or transcripts into the public repo.

## Current workflow (owner decision, 30 Oct 2026)
Read [the operating model](docs/workflow/OPERATING_MODEL.md) with this file and NOW.
Owner walkthrough, explicit provider/model delegation and action-history/retro limits:
[workflow journey](docs/workflow/USER_JOURNEY.he.md) (open on demand).
Claude Code normally manages native subagents; occasional Codex work follows the
same task/evidence contract. Worker count follows independent ready work and test
capacity, not an always-busy quota. The operating model supersedes dated prompts
on task status, worker quotas, owner presence, batching and handoffs. Existing
execution, security, budget and protected release gates are not bypassed.
Present: use the interactive question inbox without the old once-a-day cap.
Away: checkpoint dependent work, retain the question and continue independent
authorised work. Silence never means approval. Keep the ledger current through
the workflow CLI; native hooks observe lifecycle, not verified completion.
Do not execute imported scope until ownership, prerequisites and authorisation
are reconciled. No inference of verification from native Stop, merge or green CI.

## Read budget
Read this file, `docs/NOW.md` and the current operating model. Open other files only via `docs/CONTEXT_MAP.md`. Never crawl `docs/` or the repo.

## Working rules
1. Work only inside the scope in `docs/NOW.md`; no unrequested features, refactors or cleanup (note them as an issue or in the ledger).
2. Before code: up to five plan steps, files, one verification action. Smallest working change; reuse code.
3. Tests: targeted tests after each change, `python core/tools/prepush.py` before every push, CI for the full suite. Tests are hermetic and clock-free. Never skip a failing test; a flake goes to quarantine with a `showstopper` issue.
4. Debug budget: two distinct fix attempts, then stop and report evidence.
5. No success claim without evidence (test, run URL, file). Hardware and real-world items are never closed on mocks alone; mark unknowns `UNVERIFIED`.
6. Never commit credentials. Network, microphone, camera and destructive actions stay under explicit control.
7. PRs: batch related changes of similar risk without an arbitrary item-count delay. Existing Tier R independent review and protected merge checks remain. Current release behavior is `core/tools/release_gate.py` and `docs/RELEASING.md`; older checklists are historical information. No force pushes, bypassed checks or merge-main-only pushes.
8. Issues: one per real defect (P0-P3); close only with evidence (PR, test, run link); a blocker is stated exactly; nits go to `docs/reviews/FINDINGS_LEDGER.md`.

## Best-in-class technology
For every stage (models, voice, agents, libraries, tools) check the newest recommended option from official docs dated now, not memory. Prefer one coherent maintained vendor stack (currently OpenAI: Agents SDK, GPT-Live voice, hosted web search) unless a measured reason says otherwise. Cloud is fine for quality; local components must be best-in-class or labelled fallback (target runtime Raspberry Pi 5). Record model/library IDs, date checked and source link in the relevant decision doc; keep keys in the environment only.

## Models
Sonnet by default for code, docs and fixes; Haiku for status and audits; Opus only for Tier R reviews, hard design the owner names, or security work. Report the model used when known.

## Guides rule (every new feature, skill, command, integration)
Ships with a `core/src/jarves/guides/data/<feature>.toml` entry (English and Hebrew, step by step, what it does, on/off, fixing it), a CLI parity entry (`[parity.*]` in that toml), a features/capabilities row with an honest stage (beta until verified), and Hebrew strings via `core/tools/catalog_add.py`. `test_guides` and `test_cli_parity` must pass. Every user-facing option has a CLI path (`jarvis --help` lists groups).

## One file per change (parallel PRs must not conflict)
CHANGELOG line: `changelog.d/<date>-<topic>.md`. Report: `docs/reports/<date>-<topic>.md`. Dashboard texts: `core/tools/catalog_add.py`. Server Hebrew: `core/src/jarves/hebrew_parts/`. Module: `core/src/jarves/plugins.d/<feature>.toml`. Dashboard page: `core/src/jarves/pages.d/<page>.toml`. Never append to a shared file.

## Security basics
Gate and approvals are never weakened. Enabling a feature checks prerequisites and installs only allowlisted, pinned packages through the maintained setup path; never run model-supplied install commands. Hardware, sign-ins, consent, OS permissions and paid access stay explicit owner gates. Distinguish configured, installed, available and live-verified. Operability: bounded structured logs, truthful health states, privacy-safe diagnostic export (`docs/JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md` is archived: `docs/archive/`).

## Design and layers (hardware product)
JARVES is stationary: no motors, servos, wheels or navigation. FreeCAD = geometry, Blender = visuals, Godot = face and UI, Python Core = behavior, physical I/O behind one DevicePort. Fix a defect at the owning layer, then refresh only affected downstream outputs; never patch geometry in Blender or Godot. Buying: Israel stock, VAT-inclusive ILS price, warranty; no final SKU without the owner. Read `docs/PROJECT_PRINCIPLES.md` for such tasks.

## End of task
Report in 6 lines max: outcome; changed files; one command to reproduce; check run and result; blockers/UNVERIFIED; proposed next task (not started). Then stop.
