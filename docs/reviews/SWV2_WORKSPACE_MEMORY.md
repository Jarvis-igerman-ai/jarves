# SW-V2 WP-D: workspace files, self-settings, memory curator (Issue #17 items 10, 11, 17)

Date: 2026-10-19. Model: Claude Opus 5.5. Branch: WP-D worktree. Not pushed or merged; the lead integrates it.

## 1. Research and decision (checked 2026-10-19)

| Option | What it is | Fit for JARVES (Pi 5, Hebrew, small) |
|---|---|---|
| OpenClaw workspace ([docs](https://docs.openclaw.ai/concepts/agent-workspace)) | Markdown files injected every session: AGENTS.md (rules), SOUL.md (persona), USER.md (user, own 4,000-char budget), IDENTITY.md, TOOLS.md (guidance only), HEARTBEAT.md (optional checklist), MEMORY.md (curated), `memory/YYYY-MM-DD.md` daily logs read on demand. Caps of 20,000 chars per file and 60,000 in total, a 2 MiB write limit, no secrets in the workspace, a private git repo recommended for backups. | **Adopted as the file layout.** It is plain files the user can edit, has no dependencies and handles Hebrew as ordinary UTF-8. |
| Claude memory tool ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)) | A client-side file directory (`/memories`) the model reads and edits through a tool. Paths are confined to that directory (path-traversal protection). | The same idea of a confined, tool-mediated directory. Adopted: only known file names are allowed and an arbitrary path is refused. |
| mem0 / Letta / Zep ([overview 2026](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/), [self-hosted comparison](https://hindsight.vectorize.io/blog/2026/08/11/open-source-agent-memory-systems), [Zep alternatives](https://vectorize.io/articles/zep-alternatives)) | mem0 needs a vector store (Qdrant/Chroma/pgvector). Letta is a server with an agent-managed tiered memory, SQLite by default. Zep's Community Edition is deprecated (cloud plus a Neo4j/FalkorDB graph). | **Not adopted.** Each adds a server, a vector or graph DB, or embeddings calls on every turn. That is too heavy for a Pi 5 and a few dozen personal facts. None of them shows a measured Hebrew advantage. |

**Decision:** our own small implementation. OpenClaw-style files (`workspace.py`) plus the existing SQLite store, extended with provenance (`memory.py`), plus a rule-first curator (`curator.py`). The optional LLM pass reuses the existing OpenAI backend model (`ai.text_model`, GPT-6 family) under the shared Budget, so no new vendor is involved. Retrieval is "inject the top N facts". This should be revisited only if the fact count grows into the hundreds; at that point SQLite FTS5 would be the next step, still with no new dependency.

## 2. What was built

- **`core/src/jarves/workspace.py`** creates `<state_dir>/workspace/` on first run, with Hebrew-aware defaults:
  - **AGENTS.md:** user-only. Injected into the backend.
  - **SOUL.md:** the agent may edit it after the user confirms. Injected into the voice (front) and the backend. The default is comments only, so WP-A's tuned persona is unchanged.
  - **IDENTITY.md:** the agent may edit it after the user confirms. Injected into front and backend. It contains "ג'ארוויס (נכתב JARVES)".
  - **USER.md:** the agent may edit it. Injected into front and backend.
  - **MEMORY.md:** the agent may edit it. Injected into the backend.
  - **TOOLS.md:** generated from the live Skill registry, settings and voices at every start. Read-only.
  - **HEARTBEAT.md:** reserved and not injected.
  - **`memory/YYYY-MM-DD.md`:** the curator's daily notes.
  - **`.history/<file>/`:** a backup on every write, 50 kept per file.
  - **Injection:** `<!-- comments -->` are stripped and a file with only headings counts as empty. Caps are set per file and in total: 2,500 chars front (GPT-Live latency) and 9,000 backend.
  - **Validation:** UTF-8 only, no NUL, at most 20,000 chars per file, at most 3,000 chars changed per agent edit. Secret-looking text is refused (sk- keys, `password:`, `token=`, 13–19 digit numbers). Only known file names are accepted (`../config.toml` is refused).
  - **Hot reload:** files are re-read only when their mtime or size changes, and `augment_instructions` appends the backend section on every request, so an edit made in a text editor applies on the next typed/backend request. A running GPT-Live session receives a changed SOUL/IDENTITY/USER as a silent `session.thinking.append` note (`note_live`, the same mechanism as the clock note). The session's delegated backend instructions and the voice refresh at the next session.
  - **Dashboard API (plain Python, for WP-B):** `Workspace.files()`, `read(name)`, `write(name, text, by="user")` (returns a diff and version), `preview(name, text)` (diff without writing), `diff()`, `history(name)`, `read_version()`, `restore(name, version)`, `undo(name)`, `daily_notes()`. Change listeners are in `Workspace.listeners`.
- **`core/src/jarves/skills/settings.py` (`SettingsSkill`)** provides:
  - `workspace_read` (read).
  - `workspace_note` (action, pre-authorised): appends one line to USER/MEMORY.
  - `workspace_edit`: destructive, so the user always confirms, and the prompt shows the added text. Used for "תשנה את האופי שלך" (SOUL/IDENTITY/USER/MEMORY). AGENTS/TOOLS/HEARTBEAT are not even accepted by the schema.
  - `workspace_undo` (confirm).
  - `list_skills` and `list_settings` (read).
  - `set_voice` (action): "תחליף קול". It validates the name against the official GPT-Live list (22 voices, checked 2026-10-19 at [live-conversations](https://developers.openai.com/api/docs/guides/live-conversations): the voice is fixed per session and a new session is needed to change it). By default it restarts the session after the confirmation is spoken; otherwise the change applies to the next conversation.
  - `set_setting`: an **allowlist only** (`voice.voice`, `curator.auto`, `curator.llm`). Overrides live in the `memory.db` settings table. `config.toml`, `secrets.env` and keys are unreachable.
  - Front awareness: `attach_live` adds a short "delegate voice/personality/settings requests" line, the front workspace section and up to 8 facts with confidence ≥ 0.6 to the GPT-Live instructions.
- **`core/src/jarves/curator.py`** runs at the end of a live session, after 120 s of quiet typed chat, and at Core shutdown. It extracts durable facts from the **user's** lines only:
  - Rules: name, how to address the user, where they live, work, birthday, family and pet names, favourites, likes and dislikes, preferred language, in Hebrew and English. Questions, "תזכור/תשכח..." and anything secret-like (סיסמה/קוד/אשראי/ת"ז, 6 or more digits) are skipped.
  - Optional LLM pass: `curator.llm=on`, **default off**, API-key route only. One budgeted `ai_requests` call per conversation, at most 10 a day, capped at confidence 0.95, secret-filtered.
  - Storage: `MemoryStore.upsert` dedupes, and each fact carries source `auto`, session id, date, confidence and kind. Facts are visible through `recall` (source + date) and the daily note, deletable with `forget` or `Curator.forget_session(id)`.
- **`memory.py`** migrates an old DB in place (adds the provenance columns) and gains `upsert` / `find_duplicate` (normalised and fuzzy, 0.85), `for_prompt` (user-stated facts first, then by confidence and recency), `get`, `delete_session` and `delete_setting`. `remember` now dedupes too.

## 3. Evidence

- `python -m pytest core/tests -q` → **343 passed, 1 skipped** (31 new tests in `core/tests/test_workspace_memory.py`, including the offline e2e "fact told in passing in voice session 1 → in the backend and voice instructions and in `מה אתה זוכר` after a Core restart").
- **Real silent e2e** `uv run python tools/silent_memory.py` (virtual mic/speaker, real GPT-Live + gpt-6-luna):
  - Session 1, the user says "אגב, קוראים לי איגל, והכלב שלי נקרא רקס" with no "remember". At shutdown the curator stored `שם המשתמש: איגל` and `לכלב של המשתמש קוראים רקס` (auto, 0.9 / 0.85).
  - Session 2, a fresh Core: "איך קוראים לכלב שלי?" → JARVES: **"רקס."**, answered at once from the front facts without delegating. **PASS.**
- **Real voice change** `tools/silent_memory.py --voice-check`: "תחליף את הקול שלך לקול מרידיאן" → backend `set_voice(meridian)` → "רגע, אני מחליף." → session restarted 5 s later → the new session greets "זה הקול החדש שלי. מה דעתך?". **PASS.** Whether the audio really sounds like meridian was not checked by listening (silent test only).
- Real-run findings fixed:
  - (a) GPT-Live cuts the user's input transcript when the answer starts, and the tail arrives as a separate line, even mid-word ("...נקרא בנ" + "זו"). The curator glues live tails that arrive within 2.5 s.
  - (b) The name rule captured "איגל והכלב". A second word starting with ו now ends the name.
- One run of 4 failed with "live greeting audio was not received" (15 s of silence, no error). It passed on retry with identical instructions, so it looks like the known greeting flakiness (WP-A).
- **Cost:** 4 short real runs, about 2.5 voice-minutes plus 2 backend calls ≈ **$0.15**.

## 4. Integration hooks (files owned by others; smallest change)

- `app.py` (≈35 lines, all marked WP-D):
  - In start: build Workspace, Settings, SettingsSkill, TOOLS.md, the change listener → `note_live`, the Curator, and `augment_instructions(self.agent)`.
  - In `_start_live`: `voice=self.settings.get("voice.voice") or v.voice`, then `wsp.attach_live(live, …)`.
  - In `on_transcript`: `curator.observe(..., live=True)`. In `on_end`: spawn `curator.run`. In `handle_utterance`: observe user and reply, with an idle timer for typed chat. In `stop`: a rules-only curator run before `memory.close()`.
  - New methods `_on_voice_change` and `_restart_live_for_voice`.
- No edits to `live.py`, `brain.py` or `chatgpt_brain.py`: the hooks wrap `agent.instructions` and `live.session_config` from outside.
- **Recommended for WP-A/WP-C (live.py):**
  - Keep the user transcript open until its deltas stop, instead of flushing it at the first output delta. The cut transcript also reaches the face and the logs, not only the curator.
  - FRONT_INSTRUCTIONS could list "voice / personality / settings" among the quick actions to delegate. `attach_live` currently adds one line for this.
- `brain.py` INSTRUCTIONS still lists `memory.list(20)` (newest first). `memory.for_prompt(20)` would put user-stated facts first. That is optional and is WP-C's call.

## 5. Open questions for the user

1. **Auto-learning default:** the curator's local rules are **on** by default (no network; every fact is visible, deletable, and logged in `workspace/memory/`). The LLM pass is **off**. Keep this, or ask before enabling auto-learning? Recommendation: keep it on. It only stores the user's own explicit statements, and `curator.auto=off` turns it off.
2. **Voice choice:** no GPT-Live voice lists Hebrew. If you want to try another voice, ask "תחליף קול ל-meridian/stone/…" and listen.
3. Whether to show the workspace files and auto-learned facts in the dashboard (WP-B) with edit, undo and delete actions. The API is ready.
