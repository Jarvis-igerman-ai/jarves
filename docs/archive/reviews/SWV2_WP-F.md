# SW-V2 WP-F: research and plans (Issue #17 items 12, 13 and camera)

Scope: research and plan only. No code, no logins, no account access, no hardware. Sources checked 2026-10-19.

## Delivered
- [plans/GOOGLE_CALENDAR_GMAIL_SKILLS.md](../../plans/GOOGLE_CALENDAR_GMAIL_SKILLS.md): options table (direct API, Google hosted MCP, `gws` CLI, community MCP, OpenClaw/Hermes skills, IMAP), OAuth/consent limits, token storage, prompt-injection and confirm-before-send rules, Hebrew voice UX, Skill interface matching `skills/__init__.py`, step plan, fake-only test plan, user steps.
- [plans/CAMERA_PLAN.md](../../plans/CAMERA_PLAN.md): use cases, privacy design (off by default, shutter, no recording, opt-in recognition), Pi 5 hardware options, software and cloud-vision comparison, Godot gaze integration, phases C0-C7 with tests and cost, risks, CAD alignment with #8.
- ROADMAP: short summaries under "Planned optional Skills".

## Key findings
- Google Testing-mode apps get refresh tokens that expire in **7 days** (Google OAuth docs). `gmail.readonly` is a **restricted** scope; Calendar read scopes are narrower. The device-code flow does not allow Gmail/Calendar scopes, so the Pi needs the loopback flow (or consent on the PC and copy the token).
- Google's official Gmail/Calendar MCP servers exist (`gmailmcp` / `calendarmcp.googleapis.com/mcp/v1`) but are **Developer Preview**, need a Web OAuth client and preview-program enrollment; personal-account support not stated.
- **GPT-Live 1 does not accept image or video input** (OpenAI model page). `gpt-realtime-2.1` and `gpt-6-luna` do. Camera vision should go through the backend delegation path.
- Malicious mirror copies of the OpenClaw `gog` skill appear in skill catalogues: do not install skills from catalogues.

## Tests / cost
- Docs only; no code changed, so the Core suite is unaffected (run result in the final report). API cost: $0.

## Open questions (for HANDOFF decision queue)
- Calendar or Gmail first? Weekly re-connect acceptable? Ever allow sending by voice? (plan section 10)
- Camera in v1? Family recognition? Physical shutter in next CAD round? Away-mode snapshots? (camera plan section 9)
- Is unverified "production" publishing workable for restricted Gmail scopes on a personal account? UNVERIFIED, test with Calendar first.
