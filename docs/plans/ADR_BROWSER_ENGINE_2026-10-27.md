# ADR: which browser engine for Browser Studio (local Playwright vs Browser Use cloud, Cloudflare browser, Aether)

Status: accepted for V7 (27 Oct 2026). Issue [#401](https://github.com/Jarvis-igerman-ai/jarves/issues/401) (V7 slice B6). Owner decision needed only to change it.
Scope: the engine behind the browser pilot (B1 to B5: read-only pilot, live view, takeover, phone viewer, recording).

## Decision

1. **Local Playwright (the dedicated headless Edge/Chromium on the owner's computer) stays the only engine in V7.** It is what B1 to B3 and the pilot proxy already run on.
2. **Cloud browsers stay optional and OFF.** No cloud engine is wired, no account is created, no key is stored, nothing is spent in V7. Adding one needs a new ADR, an owner decision naming the provider and a spend cap, and a Tier R review.
3. The browser engine is an **adapter behind the existing pilot driver contract** (`open / text / screenshot / close / pin_hosts / current_url / human_input`). A future cloud adapter would have to satisfy the same contract and the same server-side rules (loopback or vetted addresses only, read-only for the agent, grant-bound live view, owner-only takeover). It must not be allowed to weaken them.

## What we need from an engine (the criteria)

| # | Criterion | Why it matters here |
|---|---|---|
| 1 | The network door is ours | The pilot proxy decides every connection (vetted addresses only, no rebinding, one local port). An engine we do not run cannot be forced through it. |
| 2 | Read-only is enforced by the server, not by the page | The agent must not be able to click or type; the owner's takeover is a separate, grant-bound path. |
| 3 | Typed secrets never leave the PC | Takeover exists so the owner can log in himself; his keystrokes must not travel to a third party. |
| 4 | No page content reaches another provider without consent | Rule: no private code or private page content to another provider. |
| 5 | Cost is bounded and visible | Hard stop: no paid API beyond approved caps. |
| 6 | Startup, recording and live view work offline or on a LAN | The Raspberry Pi 5 and the car/phone use cases. |
| 7 | Low lock-in | Switching engine must stay a small adapter change. |

## The options

### A. Local Playwright (chosen)
- Meets 1 to 7: runs on the computer, goes through our proxy, keystrokes go only to the local page, no third party sees page content, no per-use cost.
- Costs: RAM and CPU on the owner's machine (not yet measured on a Pi 5, see the browser feature row), we ship and update the browser dependency, a fresh context per session (no profile, no cookies on disk) which is also a privacy advantage.
- Limits we accept: video is written only when a context closes and the container format is not promised by Playwright (B5 labels what was produced); Chrome's default profile cannot be automated, so a separate directory is required (matches the fresh-context rule in `jarves/browser.py`).

### B. Browser Use cloud
- Facts from its documentation (see the V7 work package, browser track): the live view URL is itself a credential; read-only viewing is a UI restriction and not enforced by the server; recording is off by default and MP4 is available only after the browser stops.
- Against our criteria: fails 2 (read-only is not server-enforced), 3 (keystrokes would go to the provider) and 4 (page content is processed on their side) unless the owner accepts that explicitly; 1 cannot be met (their network, not our proxy); cost is per use.
- Possible later use: a clearly labelled "cloud, public pages only, no login" mode for tasks the local machine cannot do. Not in V7.

### C. Cloudflare browser (hosted browser rendering)
- Not evaluated in depth; **claims about its limits, pricing and session model below are unverified and must be checked against Cloudflare's current documentation before any sign-up.** What is certain by construction: it is a hosted browser, so criteria 1, 3 and 4 fail in the same way as B for pages that need a login, and cost is metered.
- Possible later use: read-only fetch and screenshot of PUBLIC pages (where criteria 3 and 4 do not apply), if local resources prove too thin on the Pi. Needs its own ADR and spend cap.

### D. Aether (and similar agent-browser products)
- Not evaluated in depth; **no claim here is verified.** Treated like C: any product that runs the browser or the agent loop on its own side fails criteria 1 to 4 for logged-in pages, and adds lock-in (7) if it replaces our driver contract instead of sitting behind it.
- A product that only provides a better local engine or recorder, running on the computer, would be judged as an option for A.

## Consequences
- B2 to B5 stay on local Playwright. B5 (recording) is local, default OFF, pausing at password, card and one-time-code fields.
- The guide and the features row keep saying: local test pages only for now; public and real sites are a separate owner-run, Tier R step.
- If a cloud engine is ever proposed: write the ADR first, name the provider and the cap, show how criteria 1 to 4 are met or explicitly waived by the owner for a named class of pages, add the adapter behind the driver contract with contract tests, review it as Tier R.

## Revisit when
- local resource use on the Raspberry Pi 5 is measured and is too high for the pilot (then C or D for public pages only);
- a provider offers server-enforced read-only sessions, bring-your-own-proxy and no content retention, with a price the owner accepts;
- the owner asks for tasks that cannot run locally.

## Not decided here
No spend, no account and no key. The owner is not asked anything for this ADR.
