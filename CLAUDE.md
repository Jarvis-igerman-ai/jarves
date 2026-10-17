# Claude Code — JARVIS

Follow `AGENTS.md` and the single active brief in `docs/NOW.md`.
Read extra documents only via `docs/CONTEXT_MAP.md`, on demand.
When the task is complete, update `docs/HANDOFF.md` and stop. Do not start the next task.

## Work through commits (GitHub is the source of truth)
- Everything you produce goes into committed files, not only into the chat: results, evidence, measurements, sources, risks, limitations, **open questions and decisions needed from the user**.
- `docs/HANDOFF.md` must contain a **"Questions for the user"** section with every open question, numbered and in plain language, with the options and your recommendation. A question asked only in chat is not asked.
- Detailed findings go in the relevant review/doc file (e.g. `docs/reviews/`); HANDOFF links to it.
- Commit and push at the end of every task (and at meaningful checkpoints), referencing the GitHub issue (`Refs #N`). Comment on the issue with the commit SHA.
- The chat reply is a short summary that points to the commit and files; it must not contain information that is missing from the repo.
