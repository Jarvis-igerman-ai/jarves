# R2: Buzz by Block, investigation (V7 slice R2, issue #403)

Status: research note, docs only, 26 Oct 2026. Nothing installed or run. Index: [DOC_MAP](../../DOC_MAP.md). **Unverified** marks gaps. Owner confirmation that this is the product he meant is still open (section 6).

## 1. What it is

[github.com/block/buzz](https://github.com/block/buzz) (read 26 Oct 2026): a self-hostable workspace where humans and AI agents share channels. Per the README, each message, reaction, workflow step, review and git event is a signed Nostr event. Block launched it on 21 July 2026 as a free open-source workspace for teams of people and agents; press describes it as a developer preview ([SiliconANGLE](https://siliconangle.com/2026/07/21/block-launches-buzz-open-source-workspace-humans-ai-agents/), [The Next Web](https://thenextweb.com/news/block-buzz-humans-ai-agents-workspace)). Block's own announcement page was **not** fetched (**unverified**); the press and the repository agree on the substance.

## 2. Facts

- **License:** Apache-2.0 (repository page).
- **Stack:** Rust workspace (relay, auth, audit, ACP harness, CLI), Tauri + React desktop app, Flutter mobile clients in progress, admin web app (split inferred from crate and folder names).
- **Maturity:** the README says it is not finished and sorts features into works today, being wired up and pending. The Windows build is called alpha and its installer is unsigned. Workflow approval gates are described as in progress. Very high star and commit counts are popularity, not maturity.
- **Hosting burden:** a relay with Postgres, Redis and S3-compatible storage (MinIO) via a Compose bundle or a one-click Railway deploy; local development needs Rust 1.88+, Node 24+ and pnpm. On Windows the agent shell tool needs Git Bash.
- **Agent model:** agents are channel members with their own Nostr keypair, memberships and a hash-chained audit log; they can open repos, send patches, review code, run YAML workflows and orchestrate other agents. Permission is identity and key based, as the page describes it.
- **Adapters:** `buzz-acp` is an ACP harness for Goose, Codex and Claude Code (ACP to MCP), `buzz-cli` gives JSON-in/JSON-out access for agents, `buzz-dev-mcp` offers shell and file-edit tools, `buzz-persona` holds persona packs. The maturity of the ACP harness is unclear (**unverified**).
- **Security policy:** SECURITY.md is linked but was not read; no audit results seen (**unverified**).

## 3. Does it help JARVES?

| Question | Answer |
| --- | --- |
| Adopt it as the agent workspace? | **No.** It is a Slack/GitHub-style team workspace with its own relay, database stack and identity system. It would add a second source of truth for messages, approvals and git events, against the V7 invariant (one Core, one approval ledger, one GitHub provider) |
| Are its patterns useful? | **Yes, as ideas.** (a) Each agent has its own identity and audit trail: add a per-agent identity to our audit log and Orchestrator rows. (b) A hash-chained audit log: a cheap integrity upgrade for our audit log (future slice). (c) Declarative YAML workflows triggered by events: compare with our SW factory stages. (d) Persona packs: compare with our skill/profile design |
| Would a narrow ACP bridge help? | **Maybe later.** ACP is the common ground for Goose, Codex and Claude Code, and `buzz-acp` is one implementation. Our Orchestrator already drives Claude Code and Codex directly and OpenCode arrives in D1/D2, so no bridge until a concrete need exists. Re-read the ACP specification (not read here) before any work |
| Ship it as a dependency? | **No.** A Rust relay plus Postgres, Redis and MinIO is far beyond a home-PC assistant install |

## 4. Risks

- Pre-release software with a fast-moving API; unsigned Windows installer.
- A heavy self-hosting burden and a new always-on network service (extra attack surface on the PC or tailnet).
- Agents with shell and file-edit tools in a shared workspace: prompt-injection and privilege issues that our approvals would not cover, because they sit outside the Core.
- Private code or messages could land on a relay we do not control if the hosted deploy path were used.
- "Buzz" is a common product name; confirm identity (section 6).

## 5. Decision (proposed)

**Reject adoption; adapt patterns; no adapter now.** (1) Do not install or run Buzz. (2) Add "hash-chained audit log" and "per-agent identity in the audit trail" to the V7.x extras backlog (#420). (3) Revisit an ACP bridge only if the owner asks for Goose or another ACP-only harness. (4) Re-read the Block announcement, SECURITY.md and the ACP docs before any revisit.

## 6. Open item for the owner

Confirm that Buzz by Block (block/buzz) is the product he meant. Safe default: yes, as researched; if it is another product we redo R2 for that one.
