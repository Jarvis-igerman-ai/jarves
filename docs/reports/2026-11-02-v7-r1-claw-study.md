# V7-R1: OpenClaw, nanobot and IronClaw study (issue #402)

Date checked: 2026-11-02. Docs only. Nothing was installed or run. Builds on the earlier note [R1 OpenClaw and alternatives](../archive/plans/R1_OPENCLAW_AND_ALTERNATIVES_2026-10-26.md), whose follow-up 1 (read nanobot and IronClaw from their own repositories) is done here. Index: [DOC_MAP](../DOC_MAP.md).

"UNVERIFIED" means not confirmed from an official page. "3P" means a third-party report. Facts come from page summaries (the OpenClaw README and releases pages were truncated), so re-check exact numbers before relying on them.

## 1. Bottom line

Reject all three as a dependency or replacement for the Python Core. Adapt ideas only. The strongest lessons are about the skills supply chain (OpenClaw's registry was poisoned), per-tool capability grants (IronClaw) and workspace-only defaults (nanobot).

## 2. Sources (date checked 2026-11-02)

| Project | Repo | Language | License | Latest release seen |
| --- | --- | --- | --- | --- |
| OpenClaw | [openclaw/openclaw](https://github.com/openclaw/openclaw) | UNVERIFIED (TS config, `crates` dir present) | MIT | 2026.10.9 stable, 29 Oct 2026 ([releases](https://github.com/openclaw/openclaw/releases)) |
| nanobot | [HKUDS/nanobot](https://github.com/HKUDS/nanobot) | Python 3.11+ | MIT | v0.4.0, 28 Oct 2026 ([releases](https://github.com/HKUDS/nanobot/releases)) |
| IronClaw | [nearai/ironclaw](https://github.com/nearai/ironclaw) | Rust | Apache-2.0 or MIT | ironclaw-v1.4.1, 29 Sep 2026 ([releases](https://github.com/nearai/ironclaw/releases)) |

Other pages used: [OpenClaw skills](https://docs.openclaw.ai/tools/skills), [OpenClaw sandboxing](https://docs.openclaw.ai/gateway/sandboxing), [OpenClaw advisories](https://github.com/openclaw/openclaw/security/advisories), [IronClaw advisories](https://github.com/nearai/ironclaw/security/advisories).

## 3. What each does

**Tools.** OpenClaw: tools, skills and plugins; new capabilities usually belong in plugins on a plugin SDK; tools run on the host for the main session unless sandboxed. nanobot: shell, web search/fetch, MCP, cron, image generation, multi-agent delegation, document readers. IronClaw: built-in, MCP and WASM tools; the agent can build a WASM tool from a description; tools and channels can be added without restart.

**Skills lifecycle.** OpenClaw is the only one with a documented lifecycle: install from ClawHub, skills.sh, Git or a local folder; `skills update --all` (ClawHub installs only); disable per skill; `skills verify`; `security.installPolicy` runs a local command that must approve each install; uploaded-archive installs off by default; seven precedence levels from workspace to plugin. nanobot: skills are reusable instructions managed in the WebUI; no registry found (UNVERIFIED). Its v0.4.0 notes say update install needs explicit confirmation and in-app install is disabled until release-approval proof exists. IronClaw: no skill lifecycle described (UNVERIFIED).

**Approvals.** OpenClaw: allow/deny tool policy applies before sandbox rules; `tools.elevated` runs exec outside the sandbox; unknown channel senders need `pairing approve`; the exec-approval flow was not read (UNVERIFIED). nanobot: per-project access mode, default workspace-only; DM pairing/allowlist and group policy checked separately; WebUI login grants full admin and binds to localhost. IronClaw: no human approval flow described; instead per-tool capability opt-in for HTTP, secrets and tool calls, host allowlists, rate limits, and injection policy levels Block/Warn/Review/Sanitize.

**Sandboxing.** OpenClaw: off by default; only tool execution moves in; backends Docker (default), Podman, SSH, OpenShell, Crabbox; docs say "not a perfect security boundary". nanobot: no sandbox described; a `docker-compose.bwrap.yml` hints at bubblewrap (UNVERIFIED). IronClaw: WASM isolation with memory/CPU/time limits plus Docker orchestrator/worker with per-job tokens; credentials injected at the host boundary with leak scanning (project claims, UNVERIFIED against code).

**Security record.** OpenClaw: official advisory page 1 (all 11 Sep 2026) lists High "Exec approvals could outlive their reviewed working directory", High "WhatsApp login tool could reach non-owner turns", Moderate "File-transfer approvals could widen durable authority", Moderate "Unicode fallback could escape workspaceOnly roots"; only page 1 of 73 was reviewed. ClawHub incident "ClawHavoc" (3P): Koi Security reported 341 of 2,857 skills flagged on 1 Feb 2026 with infostealer and backdoor payloads ([Antiy](https://www.antiy.net/p/clawhavoc-analysis-of-large-scale-poisoning-campaign-targeting-the-openclaw-skill-market-for-ai-agents/), [AIID](https://incidentdatabase.ai/cite/1368/), [Aviatrix](https://aviatrix.ai/threat-research-center/openclaw-2026-clawhub-malicious-skills/)); counts differ by source (Antiy: at least 1,184). A ranking-manipulation flaw was also reported (3P, UNVERIFIED). nanobot: none found; SECURITY.md not read. IronClaw: zero published advisories (young project, absence is weak evidence).

## 4. Adopt / adapt / reject

| Idea | Source | Verdict | JARVES action |
| --- | --- | --- | --- |
| Public skill registry with one-command install | OpenClaw | Reject | ClawHavoc shows the risk; JARVES skills stay in-repo, reviewed, with a guide. Revisit only with signing, sandboxing and owner approval |
| Install gate: a policy must approve every skill install | OpenClaw | Adapt | Matches "allowlisted, pinned packages through the maintained setup path"; keep and test that no model-supplied install runs |
| Skill verify command and trust card | OpenClaw | Adapt | Idea for a future `jarvis skills verify` (hash, source, declared permissions) |
| Skill enable/disable per entry and precedence layers | OpenClaw | Adapt | Per-skill switch exists via features; keep one precedence rule, documented |
| Tool allow/deny policy evaluated before sandbox | OpenClaw | Adopt as principle | Gate decides first; sandbox is a second layer, never a substitute |
| Elevated exec outside sandbox | OpenClaw | Reject | No unapproved escape path; any such action goes through the Gate |
| Approval bound to context (advisory: approval outlived its working directory, durable authority widened) | OpenClaw advisories | Adopt as a test rule | Check that approvals are scoped to project, path and expiry, and cannot widen later; add regression tests (follow-up) |
| Path-escape via Unicode fallback in workspace roots | OpenClaw advisories | Adopt as a test rule | Normalise paths before root checks; add a case to the file-tool tests |
| Pairing for unknown senders | OpenClaw, nanobot | Already have for phones | Keep rule for any future channel |
| Workspace-only default access per project | nanobot | Adopt | Matches allowed-projects model; keep default narrow |
| Admin-grade web login with secret in URL fragment | nanobot | Reject | Do not put credentials in URLs; keep current auth |
| Do not blindly replay interrupted tool actions or missed schedules | nanobot | Adopt | Aligns with the supervisor rule never to repeat ambiguous actions |
| Update install needs explicit confirmation plus published checksums | nanobot | Already have | Matches hotfix/update gates; keep |
| Per-tool capability grants (HTTP hosts, secrets, tool calls) with rate limits | IronClaw | Adapt | Target model for future third-party tools: declared capabilities, owner-visible, default none |
| WASM sandbox for untrusted tools | IronClaw | Adapt (later) | Candidate for untrusted extensions only; no code now, needs a measured design and a Pi 5 cost check |
| Host-side credential injection and leak scanning | IronClaw | Adapt | Tools never see raw keys; fits "keys in environment only" |
| Prompt-injection levels Block/Warn/Review/Sanitize | IronClaw | Adapt | Map onto existing untrusted-content wrapping; add a review level for inbound text |
| Agent builds its own tools at runtime | IronClaw | Reject | Conflicts with reviewed skills and no model-supplied installs |
| Replace Core with any of the three | all | Reject | Python Core stays authoritative |

## 5. Follow-ups (not started)

1. Read OpenClaw's exec-approval doc, the remaining advisory pages and nanobot SECURITY.md; update the table.
2. Candidate small tests: approval scope cannot outlive project/path; Unicode path normalisation in file tools.
3. Add "skills verify" and "tool capability declaration" to the V7.x extras backlog (#420).
4. No install or trial of any of these projects without the owner.
