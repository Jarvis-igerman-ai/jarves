# Context map — open only what this task requires

| Need | Read only |
|---|---|
| Durable project principles, printability, Israel sourcing and user decisions | `PROJECT_PRINCIPLES.md` |
| Current scope / next action | `NOW.md` (always) |
| Layer boundaries and model pipeline | `ARCHITECTURE.md` |
| JARVES user controls, optional touch, phone/PC admin, safe shutdown, debug logging, remote connection and offline recovery | `JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md` |
| Simulated vs physical GPIO/camera/audio contracts | `DEVICE_CONTRACT.md` |
| Face renderer same on desktop/device | `FACE_RUNTIME.md` |
| Camera software: switches, privacy rules, models, setup | `CAMERA_SETUP.md` (plan: `plans/CAMERA_PLAN.md`) |
| Real WhatsApp send/read: options, ban risk, isolated bridge design | `plans/WHATSAPP_REAL_ACCESS.md` |
| Optional read-only OpenAI admin key (usage/cost checks), how to create and save it | `OPENAI_ADMIN_KEY.md` |
| Face / 2D avatar / 3D avatar / hidden presentations, `appearance.set` | `APPEARANCE.md` |
| JARVES-generated Skills: draft, validation, approval, grants, sandbox | `SKILL_CREATION.md` |
| Assistant software: stack, models, costs (checked dates) | `reviews/SW_V1_TECH_DECISION.md` |
| Run/debug the Core, Skills, music pairing | `../core/README.md` |
| Using JARVES end to end (en/he), V4 features, troubleshooting | `USER_GUIDE.md`, `USER_GUIDE.he.md` |
| Which document is where | `README.md` (docs index) |
| What V4 changed (behaviour, exit codes) | `RELEASE_NOTES_4.0.md`, `../CHANGELOG.md` |
| V4 package evidence (privacy, orchestrator, WhatsApp, recall, diagnostics, perf) | `reviews/SWV4_*.md` (one per package) |
| Historical decisions and alternatives | `archive/DECISIONS.md` |
| Component slots/fit/assembly | `archive/MECHANICAL.md` |
| Connections, power, Pi vs PC, USB bridge | `archive/HARDWARE.md` |
| Stage order / physical transition | `archive/ROADMAP.md` |
| Phone access over Tailscale (setup EN/HE) | `PHONE_ACCESS_TAILSCALE.md`, `PHONE_ACCESS_TAILSCALE_HE.md` |
| Future features/personal assistant | `archive/PRODUCT.md` |
| Open risks/questions | `archive/OPEN_ITEMS.md` |
| Prior artifacts/PDF/old simulator | `../archive/legacy/` ONLY if asked |

Do **not** read the entire table target list. `AGENTS.md` and `NOW.md` are the only default inputs. Do not recursively crawl `docs/`.
