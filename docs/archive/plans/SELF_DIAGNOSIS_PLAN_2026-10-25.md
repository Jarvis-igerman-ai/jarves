# Self-diagnosis on failure: ask first (issue #336)

Status: PLAN (docs only). Owner rule 25 Oct: on a failure JARVES asks ONE short question first; nothing deeper runs without his yes.

## 1. What exists (reuse, do not rebuild)
- `ToolResult(ok, message, data, code)` + `to_model()` in `skills/__init__.py`; stable `code` per failure; PR #334 `tool_failed` rule makes the model report failures honestly.
- `selfcheck.py`: `SelfCheck`, `Finding` (cause, fix, evidence, effort), `ticket_draft()` (redacted Markdown), `propose()` (approval-gated), `jarvis selftest`/`doctor`/`speedtest`.
- `logs.redact` / `redact_deep` / `redact_pii`; `ApprovalService` (`approvals.py`, chat/dashboard/voice/phone, audit log); guide `problems` entries with error codes.
- `orchestrator/service.py` `start_session(provider, project, task, by, approved)` = Development Manager / coding agents (issue #2), worktree per session.

## 2. Flow (one failure, three choices)
```
tool/skill returns ok=false (code C, area A)
  -> FailureGate (new, per turn, de-duplicated by code+area, 10 min cooldown, never inside a diagnosis itself)
  -> ask ONE question via the existing question/approval path (chat card, dashboard card, voice yes/no/choice)
       EN: "That failed. Want me to look into it? 1) quick self-check  2) hand to a coding agent  3) skip"
       HE: "זה נכשל. רוצה שאבדוק? 1) בדיקה עצמית מהירה  2) להעביר לסוכן קוד  3) לדלג"
  -> 1: Slice A runs read-only, replies with plain cause + fix, then offers "open a ticket?" (Slice B)
  -> 2: Slice C: approval card shows the redacted brief; only on yes does Orchestrator start a session
  -> 3 / no answer / timeout (60 s): drop it, log `diag.skipped`, do not ask again for the same code for the cooldown
```
Rules: the question is short and never lists errors; voice reads only the three options; "skip" is always the default; automations and background jobs (no user turn) never ask, they write a Finding for the next self-check; setting `diagnosis.ask_on_failure` (on/off, default on) lives in the dashboard; one question open at a time per channel.

## 3. Slice A: read-only self-check per area
New `diag/areas.py`: `run(area, failure) -> DiagReport`. Registered runners (all read-only, no network writes, 10 s budget, cancellable):
- `anydo`, `whatsapp`, `google`, `voice` (first four). Each collects: error `code`, raw response SHAPE (keys, types, HTTP status, no values), connection state (configured / token present / expired, never the token), permission or scope state, JARVES version + OS, last 20 log lines for the area (through `logs.redact_deep`).
- Matching: `code` + area -> guide `problems` entry (existing ids); no match -> generic "unknown", suggests option 2.
- Output `DiagReport{area, code, evidence[], cause_en, cause_he, fix_en, fix_he, guide_id, confidence}`; spoken/chat text is 2 sentences max (cause, then fix), Hebrew from catalog strings, no model call needed (deterministic); optional model polish only if already online and allowed.
- `jarvis diagnose <area> [--code C] [--json]` CLI = same runner (parity); reuse `SelfCheck` Finding for persistence so it shows in the dashboard self-check page.

## 4. Slice B: ticket
`DiagReport -> ticket_draft`-style Markdown (title `[diag] <area>: <code>`), evidence already redacted, a final redaction pass plus a scan that blocks on token/phone/email patterns. Asks "open a ticket?" first; creates a local draft by default (`jarvis ticket --from-diag ID`); a GitHub issue is posted only after approval (existing `jarvis ticket` path). No conversation text.

## 5. Slice C: hand-off to a coding agent
After an approval card showing: target project, the redacted brief (code, area, evidence, guide link, "propose a fix as a PR, no merge"), model/cost cap. On yes: `Orchestrator.start_session(..., by="diag", approved=True)` in a fresh worktree. Constraints: tokens/secrets never in the brief or env passed on beyond what the session already has; the brief is built only from the redacted report; the agent can open a draft PR, never merge (no auto-merge; normal CI + review); session id is written back to the ticket. Tests use a fake provider (no live agent runs, no spend).

## 6. Data flow and trust
Failure -> gate (local) -> question (local UI) -> runner (local reads) -> redact -> {screen | local ticket | approved GitHub issue | approved agent brief}. Nothing leaves the PC before a yes and redaction. Audit line per step in the approvals audit log (`diag.asked/answered/ran/ticket/handoff`).

## 7. Risks
- Question fatigue -> cooldown, dedupe, per-hour cap (3), off switch, silent in automations.
- Secret leakage in evidence -> shape-only values, `redact_deep` + final scan, golden tests with seeded fake secrets.
- Voice mis-hearing "2" -> hand-off still needs a second approval card.
- Runner side effects -> read-only contract enforced by a test that patches network/file writes.
- Wrong diagnosis -> report states confidence and "unknown" is allowed.

## 8. Tests
Gate: dedupe, cooldown, skip, timeout, off switch, no ask in background. Runners: per area with recorded response shapes, redaction golden tests, read-only guard. Matching to guide problems (every `code` resolves). Flow: chat/dashboard/voice option parsing EN+HE ("one/two/skip", "אחד/שניים/דלג"). Ticket: no secret/PII, blocked on seeded secret. Hand-off: fake provider receives redacted brief only, requires approval, no merge path. CI only for the full suite; targeted local tests.

## 9. Guide, CLI parity, Hebrew
`guides/data/self_diagnosis.toml` (EN+HE: what it does, on/off, what is shared, fixing), new `problems` entries for the four areas, CLI parity rows (`diagnose`, `ticket --from-diag`, `config diagnosis.ask_on_failure`), features row with honest status, Hebrew strings through `core/tools/catalog_add.py`; `test_guides` and `test_cli_parity` must pass.

## 10. PR slices (max 4, each small, one lane open at a time)
1. Gate + question (chat, dashboard card, voice), setting, guide stub, EN/HE strings; options 1/2 return "not available yet" until later slices.
2. Slice A: `diag/areas.py` runners for the four areas + CLI + guide `problems` links.
3. Slice B: ticket from report (+ final redaction scan).
4. Slice C: Orchestrator hand-off with approval card and fake-agent tests.

## 11. Owner decisions (safe default if silent)
1. Ask on every failure or only repeated/user-visible ones? Default: user-visible only, cooldown 10 min, max 3 per hour.
2. Option 1 may call the online model to word the cause? Default: no, deterministic text only.
3. Tickets: local draft only or post to GitHub after approval? Default: local draft; GitHub only on explicit yes.
4. Which coding agent for option 2: Claude Code or Codex? Default: the project's configured Orchestrator default, with its cost cap.
5. Voice: ask aloud or only show a card? Default: ask aloud only when the failure happened in a voice turn.
