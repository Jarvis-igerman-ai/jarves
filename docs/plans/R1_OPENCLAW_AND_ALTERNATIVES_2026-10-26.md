# R1: OpenClaw and alternatives, what JARVES can learn (V7 slice R1, issue #402)

Status: research note, docs only, 26 Oct 2026. Nothing installed or run. Index: [DOC_MAP](../DOC_MAP.md). Sources are the projects' own pages; **unverified** marks what was not confirmed from an official page.

## 1. Bottom line

**Reject replacing the Python Core. Adapt a few ideas; adopt none as code.** JARVES already has per-phone pairing, approvals, an audit log, a feature-switch model and a skills contract. OpenClaw's value for us is a checklist of what a personal-agent gateway must defend, not a dependency.

## 2. What OpenClaw is (official repo, read 26 Oct 2026)

[github.com/openclaw/openclaw](https://github.com/openclaw/openclaw): an open-source assistant that runs on your own computer and is reached through messaging channels (the page lists Discord, Slack, Telegram, WhatsApp, iMessage) plus companion apps for macOS, iOS, Android, Windows and Linux. MIT licence. Node.js/TypeScript (inferred from the Node requirement and pnpm workspace; the page does not name the language). Run by the OpenClaw Foundation, which the page calls an independent 501(c)(3) with no paid tier or token. Architecture: a **Gateway** control plane (sessions, tools, events, channels), channels, companion apps/nodes (voice, canvas, camera, screen), model providers, tools, skills, plugins and a public registry (ClawHub). The page shows very large activity; we do not use popularity as a quality signal.

Security guide ([docs.openclaw.ai/gateway/security](https://docs.openclaw.ai/gateway/security)), paraphrased: the gateway binds to loopback on a normal install; unknown DM senders get a pairing code instead of being processed; DM access has four modes (pairing, allowlist, open, disabled); groups are allowlisted behind a mention gate; one trust boundary per gateway, not hostile multi-tenant; untrusted content is wrapped; browser SSRF policy is strict by default; `openclaw security audit` checks for drift from safe defaults; per-agent profiles, read-only mode and sub-agent delegation limits exist. The page itself warns that container images default to an exposed bind and that agents with message tools can message across conversations by default. Exec approvals and skill/plugin trust are only summarised there (**unverified detail**, linked pages not read).

## 3. Alternatives (thin evidence)

- **nanobot (HKUDS):** described by third-party roundups as a minimal Python OpenClaw-like agent (about 4,000 lines claimed) with several chat channels and no GUI or marketplace ([itsfoss](https://itsfoss.com/openclaw-alternatives.md), [Hongkiat](https://www.hongkiat.com/blog/?p=74251)). **Unverified:** not read from its own repository.
- **IronClaw (NEAR AI):** only named in a roundup ([bitdoze](https://www.bitdoze.com/openclaw-alternatives/)); one article describes a Rust agent with a WASM sandbox and prompt-injection/exfiltration checks ([SuperFrameworks](https://superframeworks.com/articles/best-openclaw-alternatives)) but does not clearly name it. **Unverified:** a lead only; read NEAR AI's own repository before relying on any claim.

## 4. Adopt / adapt / reject

| Idea | Verdict | JARVES action |
| --- | --- | --- |
| Pair unknown senders with a one-time code, never process their messages | Have it for phones; **adapt** to any future messaging channel | Write "unknown sender gets a pairing prompt, nothing reaches a model" as a rule and test per channel |
| DM modes pairing/allowlist/open/disabled | **Adapt** | Default stays pairing or disabled; never offer "open" |
| Loopback bind by default, container warning | Already have (phone access OFF by default, tailnet-only bind) | Keep; exposure always needs an explicit step |
| Built-in security audit command | **Adopt the idea** | Add a "security posture" section to `jarvis selftest` (bind address, scopes, write switches, stale tokens); future slice |
| One trust boundary per gateway | **Adopt as a stated limit** | Document that one JARVES install serves one owner |
| Treat inbound content as untrusted and wrap it | Have for several channels; **adapt** | Check each new input path (Android share intent, GitHub text, OpenCode output) against one rule |
| Per-agent tool profiles, read-only mode, delegation limits | **Adapt** | Match our Orchestrator/approval levels; useful for OpenCode (D2/D3) and GitHub writes (G2) |
| Cross-conversation messaging allowed by default | **Reject** | An agent cannot message another channel without approval |
| Public skill/plugin registry (ClawHub) | **Reject for now** | Supply-chain risk for a home assistant; JARVES skills stay in-repo, reviewed, with a guide. Revisit only with signing and sandboxing |
| Companion nodes (camera, screen, device actions) | **Adapt** | Mirrors our DevicePort/phone design; camera and screen stay out of V7 |
| Node/TypeScript Gateway as the Core | **Reject** | The Python Core stays authoritative |

## 5. Follow-ups (not done here)

1. Read OpenClaw's exec-approval and plugin-trust pages and the NEAR AI repository, then update the table (one small docs PR).
2. Add the "security posture" idea to the V7.x extras backlog (#420).
3. No install or trial of any of these projects without the owner.
