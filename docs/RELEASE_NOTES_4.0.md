# JARVES 4.0 release notes

Status: release candidate prepared on branch `release/v4-prep` (version 4.0.0). Owner acceptance, the tag and the public release have not happened yet. "Verified" below means checked in a real run; everything else was tested with fakes. Owner-facing guide: [USER_GUIDE](USER_GUIDE.md) ([עברית](USER_GUIDE.he.md)).

## New

| Area | What it is | Evidence and limits |
|---|---|---|
| Privacy hardening | Fixed log messages, field classification, redaction of 36 sinks, `jarvis diag` strips content, privacy banner | Planted-secret audit: 0 leaks in both modes ([report](reviews/SWV4_B_PRIVACY.md)) |
| Independent re-review | P1 fixes: voice-approved dangerous commands, data-folder refusal bypasses, untrusted Instagram DMs, cross-profile secret reach | Exploit tests ([report](reviews/SWV4_C_REVIEW.md)) |
| Approvals popup | Compact card, Approve/Deny, hidden typed codes, signed answer files, popup nonce; spoken approvals name only the kind of action | [R-01 report](reviews/SWV4_R01_DEFENSE.md). Helper commands run in a Job Object but are not file-sandboxed |
| Client-mode delegation | JARVES runs the backend request itself with a strict reserved budget | Live latency measured ([report](reviews/SWV4_D_DELEGATION.md)) |
| Agent Orchestrator | Claude Code and Codex sessions: list, ask, tell, start, stop, approvals; plugin and local pipe | Fake agents only; not verified on live paid sessions ([report](reviews/SWV4_E_ORCHESTRATOR.md)) |
| WhatsApp bridge | Go bridge (whatsmeow), read mode first, allow-listed sends with approval, kill switch | Fake bridge and the real binary offline; real account not verified ([report](reviews/SWV4_F_WHATSAPP.md)) |
| Conversation recall | Local FTS index, Hebrew-aware, CLI and Recall page | Quality set 40/40; live voice 79-84 ([report](reviews/SWV4_G_RECALL.md)) |
| Dashboard | English and Hebrew only, accessibility fixes, Timers, Lists, Approvals, Coding agents, Health pages | Headless browser sweep ([report](reviews/SWV4_K_DASHBOARD.md)) |
| Performance gates | Budgets in `core/perf_budgets.toml`, `perf_gate.py`, docs-only CI skip, nightly workflow | [report](reviews/SWV4_M_PERF_CI.md), [budgets](reviews/SWV4_PERF_BUDGETS.md) |
| Self-Diagnostics 2.0 | Monitor, baselines, version regression, root-cause hints, preflight, canaries, remediation through approvals, scheduled AI review | [report](reviews/SWV4_O_SELFDIAG2.md) |
| Wake model | Head trained on the owner's recordings, `jarvis wake train [--revert]` | Measured by cross-validation ([report](reviews/SWV4_WAKE_TRAIN.md)) |
| Idle writes | Idle disk writes reduced from 36.5 to 4.8 KB/min | [report](reviews/SWV4_IDLE_WRITES.md) |
| Assisted dialing | Voice, CLI and page open a `tel:` link after approval; the owner presses Call | Off by default (`assisted_dialing`); Phone Link not verified ([report](reviews/SWV4_H_DIALING.md)) |
| Installer, update, rollback | Silent upgrade, signed manifest, `jarvis update --rollback`, migrations with backup | Fake-tested; real installer round trip and upgrade matrix not run ([report](reviews/SWV4_L_INSTALLER.md)) |

Not in V4: Telegram, Instagram and Home Assistant live verification, rocket alerts, a signed installer (the owner chose to stay unsigned for now), phone and robot hardware.

## Behaviour and exit-code changes

- `jarvis status` exits 4 when the Core runs headless (face absent). Scripts that treated every non-zero as "down" must be updated.
- `jarvis features on X` no longer refuses an unavailable feature: it saves the switch as on, every surface says `unavailable (reason)`, and the exit code is 0.
- Arabic and Russian dashboard catalogs were removed. A saved `ar` or `ru` falls back to English.
- Pear history trimming is off by default (a switch in Settings).
- Spoken approvals name only the kind of action; the popup and dashboard still show details.
- The popup language defaults to English (`[ui] popup_language`).
- Update source repository is `igalgerman/jarves`; signed installers stay in `igalgerman/jarvis-releases`.
- `health.ticket_repo` is empty by default: ticket drafts stay local until you set a repository and approve.
- Approval answer files must be signed; unsigned files from older versions are deleted on start.
- Slow feature probes answer from the last result; a never-checked feature shows "checking" for about a second after a start.
- Log messages are fixed literals, so scripts that parsed free text in `jarves.log` must read the fields.

## Known open items

Owner decisions OD-12 (privacy default for new installs), OD-33..37 (AI review and remediation), OD-20..31 (orchestrator and WhatsApp scopes); independent security review of dialing; the open OpenAI credit balance blocks part of the voice campaign. Tracked in [HANDOFF](HANDOFF.md).
