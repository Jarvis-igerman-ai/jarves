# Claude Code — JARVIS

Follow `AGENTS.md` and the single active brief in `docs/NOW.md`.
Read extra documents only via `docs/CONTEXT_MAP.md`, on demand.
When the task is complete, update `docs/HANDOFF.md` and stop. Do not start the next task.
Every stage: research and use the newest best-in-class models/tools (see AGENTS.md "Best-in-class technology").
Work through commits: commit and push all results and open questions (in `docs/HANDOFF.md`), referencing the GitHub issue.
Every new feature, skill, command or integration ships with a guide: a `core/src/jarves/guides/data/<feature>.toml` entry (English and Hebrew, step by step, what it does, how to turn it on/off and fix it), a CLI parity entry, a features/capabilities row with an honest status, and Hebrew strings via `core/tools/catalog_add.py`. `test_guides` and `test_cli_parity` must pass. A feature without its guide is not done.
Every release from 4.1 and 5.0 on fixes all known bugs and issues before it ships: the bug tables in `docs/reviews/*BUG_TABLE*.md`, the open GitHub issues on igalgerman/jarves and the problems found by `jarvis selftest`/`speedtest` are triaged for that release. No open P0-P2 may remain; P3 items are fixed or listed with a reason and an owner decision. The release checklist records the bug-table state.
