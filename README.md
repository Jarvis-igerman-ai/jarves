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
| FreeCAD → Blender → Godot → Core boundaries | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| Rules for agents and selective reading | [AGENTS](AGENTS.md), [CONTEXT_MAP](docs/CONTEXT_MAP.md) |
| Interactive viewer instructions | [godot/README](godot/README.md) |

## Current snapshot — 17 October 2026

FreeCAD CAD blockout, Blender renders/concepts and a working Windows Godot viewer exist. On the configured PC launch `godot/run_viewer.cmd`.

**Design direction:** improved A Pebble: cute, friendly and premium, with a practical flat display face and mic top. Colors, finishes, precise model and components remain undecided. Expect multiple visual review rounds after the user tries the simulator.

**Critical physical problem:** current CAD head is separated from the body by 1 mm. A genuinely strong, serviceable head/neck/chassis connection has not been built or physically validated.

**Current gate:** verify realistic display and microphone options, assembly, printability, exact internal fit, accessible wiring and cooling, possible future SSD space and practical risk tests. User buys parts **in Israel**: prefer easy local procurement with actual stock, VAT-inclusive pricing, warranty, delivery and credible substitutions. Imports are fallback only. No fixed budget, final component SKU or purchase authorization.

Living room target: 35–50 m², speech at 3–5 m; this is a testing goal, not a guarantee. GitHub Actions CI is not active ([Issue #2](https://github.com/igalgerman/JARVIS/issues/2)).

## How we work

**User approves appearance after hands-on simulation. Agents own technical explanations and evidence**, with plain-language comparisons; do not ask for blind sign-off on electronics or 3D printing. Prioritize simple printable replaceable parts, assembly instructions, fit evidence, and small physical test prints before full fabrication.

An approved milestone is worked autonomously in bounded steps with targeted checks; stop at its approval boundary. Fix geometry in FreeCAD, visual finish in Blender, scene interactions in Godot. Full details in [permanent principles](docs/PROJECT_PRINCIPLES.md).

The product is **JARVES**; some legacy paths and files still contain `JARVIS`. These are unchanged until a separate technical rename is approved.
