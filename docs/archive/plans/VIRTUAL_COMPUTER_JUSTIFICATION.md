# Sandboxed virtual computer prototype: justification (#17)

Status: analysis, 2026-10-20. Costs are estimates, not quotes.

## What it is
A disposable VM/container desktop (Windows Sandbox, Hyper-V VM, or Linux container with a virtual display) where JARVIS drives a browser/apps with computer-use style tools, isolated from the owner's real PC and accounts.

## Use cases
- Automation of sites with no API (bookings, forms, price checks) without touching the owner's logged-in browser.
- Running untrusted downloads/installers or generated code safely.
- Reproducible, silent, headless UI testing of JARVIS itself.
- Long tasks that must not hijack the owner's screen or mouse.

## Against / risks
- Computer use is slow, token-costly and brittle (UI changes, captchas, bot detection).
- Credentials: logging into real accounts inside the VM recreates the exposure; needs a scoped secret broker and confirmation for purchases/sends.
- Prompt injection from web pages; mitigate with domain allowlist, no secrets in the VM, human confirmation for side effects.
- Windows Home has no Hyper-V and no Windows Sandbox; use WSL2/Docker or VirtualBox, or a cloud sandbox.
- The Pi 5 cannot host it; needs the PC or cloud.

## Cost (estimate)
- Local: no license cost, 4-8 GB RAM while running, about 2-4 days of effort for a prototype.
- Cloud sandbox: cents per VM-hour plus model tokens; screenshot loops dominate cost.

## Recommendation
Do a narrow prototype, not a general virtual PC: a Linux container (WSL2/Docker, works on Windows Home) with headless browser (Playwright) plus screenshot tool, no stored credentials, domain allowlist, confirmation before irreversible actions, and a reset/kill command. Success criterion: complete 3 real owner tasks (a form, a price lookup, a UI test) reliably. Prefer APIs and official integrations first; use the sandbox only where none exist. Defer a full Windows-desktop VM until the container prototype proves value.

## Open questions for owner
- Which 2-3 concrete tasks justify it?
- Local (Home edition PC) vs cloud sandbox budget?
