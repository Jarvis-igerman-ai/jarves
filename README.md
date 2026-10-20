# JARVES — Personal Companion Robot

A cute, friendly, premium-looking stationary home companion. **Simulation first, practical DIY build later.**

## Start here — persistent project context

| What you need | Source |
|---|---|
| **Permanent decisions and non-negotiable buildability principles** | [PROJECT_PRINCIPLES](docs/PROJECT_PRINCIPLES.md) |
| Current authorized work | [NOW](docs/NOW.md) |
| Latest implementation, tests, risks and handoff | [HANDOFF](docs/HANDOFF.md) |
| Current design and component discussion | [Issue #3](https://github.com/igalgerman/JARVIS/issues/3) and [design review](docs/reviews/S1_DESIGN_REVIEW.md) |
| Milestones | [ROADMAP](docs/ROADMAP.md) |
| **Engineering challenges, simulations and staged safety/quality reviews** | [ENGINEERING_REVIEW_GATES](docs/ENGINEERING_REVIEW_GATES.md) |
| **Active software / CI implementation package** | [SW-V1 approved plan](docs/SW_V1_WORK_PACKAGE.md) |
| **Future personal assistant, Skills, YouTube Music, controls and diagnostics** | [JARVES software requirements](docs/JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md) |
| FreeCAD → Blender → Godot → Core boundaries | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| Rules for agents and selective reading | [AGENTS](AGENTS.md), [CONTEXT_MAP](docs/CONTEXT_MAP.md) |
| Interactive viewer instructions | [godot/README](godot/README.md) |
| **Run the assistant (Core + face), setup, troubleshooting** | [core/README](core/README.md) · [SW-V1 review](docs/reviews/SW_V1_REVIEW.md) |

## Current snapshot — 18 October 2026

**Software SW-V1 (in review):**
- A working Python Core (`core/`) runs the face (Godot) end to end.
- Everything is ready for OpenAI GPT-Live voice + GPT-6 agent + web search, but those need your `OPENAI_API_KEY`.
- Or connect your **ChatGPT plan** (`jarves chatgpt login`): text on the plan, with voice local or on the API key ("combined").
- CI is active: [jarves-checks](https://github.com/igalgerman/JARVIS/actions/workflows/jarves-checks.yml), with Core tests, CAD, Blender and Godot on every push.
- Offline it handles Hebrew commands, memory, diagnostics and the YouTube Music skill via Pear Desktop (needs a one-time "Allow").
- **Quick start: type `jarvis`.** The first time, it asks how to connect: (1) ChatGPT plan, (2) plan + API-key voice, or (3) API key. Then it starts the Core and the face. Other commands: `jarvis setup`, `jarvis stop`, `jarvis status`. Details: [core/README](core/README.md).

## Earlier snapshot — 17 October 2026

**Pebble A visual review candidate** (Issue #4): CAD `cad/JARVIS_pebble.FCStd` (generator `cad/build_pebble.py`) → Blender (`blender/finishes.json`, renders in `blender/renders/`) → Godot viewer. On the configured PC launch `godot/run_viewer.cmd`. Review summary: [S1_T03R2_PEBBLE_VISUAL](docs/reviews/S1_T03R2_PEBBLE_VISUAL.md). The old blockout (`cad/JARVIS_blockout.FCStd`) is kept as "before".

**Design direction:** improved A Pebble: cute, friendly and premium, with a practical flat display face and mic top. Three finishes are provisional; colors, precise model and components remain undecided. Expect multiple visual review rounds.

**Physical status:** the CAD head is now connected through a continuous neck/spine/chassis load path (geometry-checked). Strength, tip safety and tolerances are **not physically validated**.

**Current gate:** verify realistic display and microphone options, assembly, printability, exact internal fit, accessible wiring and cooling, possible future SSD space and practical risk tests. User buys parts **in Israel**: prefer easy local procurement with actual stock, VAT-inclusive pricing, warranty, delivery and credible substitutions. Imports are fallback only. No fixed budget, final component SKU or purchase authorization.

Room estimate: 35–50 m². **Voice pickup distance undecided**: revisit realistic range, device placement and acoustics later; previous 3–5 m is an exploratory scenario, not a binding requirement. GitHub Actions CI is active since 18 Oct 2026 (`.github/workflows/jarves-checks.yml`, [Issue #2](https://github.com/igalgerman/JARVIS/issues/2)).

## Repository hygiene
After each milestone, link commits and test evidence to its Issue, close work that meets acceptance, and keep open blockers visible. Exclude caches, throwaway outputs and duplicate builds; never mass-delete valuable project files or history without review. [Persistent rules](docs/PROJECT_PRINCIPLES.md).

## How we work

**User approves appearance after hands-on simulation. Agents own technical explanations and evidence**, with plain-language comparisons; do not ask for blind sign-off on electronics or 3D printing. Prioritize simple printable replaceable parts, assembly instructions, and fit evidence; perform physical tests when risk-justified and separately approved, before claiming real-world safety or performance.

**Progressive reviews, not endless redesign:** follow [the engineering review and simulation gates](docs/ENGINEERING_REVIEW_GATES.md) as the relevant milestones arrive: focused pre-CAD challenge, mechanical/print/assembly simulations, project-generated visual reviews, software fault simulation, purchase-readiness checks, physical safety/acoustic tests and final acceptance. Bring in an independent read-only reviewer at consequential decision gates, not for each small edit. Record evidence, unresolved risks and the next safe decision point; do not start unapproved stages or buy hardware just to explore alternatives.

An approved milestone is worked autonomously in bounded steps with targeted checks; stop at its approval boundary. Fix geometry in FreeCAD, visual finish in Blender, scene interactions in Godot. Full details in [permanent principles](docs/PROJECT_PRINCIPLES.md).

The product is **JARVES**; some legacy paths and files still contain `JARVIS`. These are unchanged until a separate technical rename is approved.
