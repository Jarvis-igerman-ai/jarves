# JARVES — Non-negotiable project principles

**Purpose:** durable product and engineering guardrails across chat sessions, Claude Code, Codex and GitHub Issues. These are enduring principles, **not authorization** to implement features outside `docs/NOW.md`.

## Product and user review
- Build **JARVES**, a beautiful, cute, friendly, impressive **stationary** home companion, beginning with a runnable Windows 3D simulation before physical fabrication.
- **User owns aesthetic decisions** after hands-on simulator review. The current **A Pebble-inspired** direction is a starting point; precise colors, materials, finish and expressive details are **not approved**. Expect multiple review/revision rounds.
- Treat visible renders and CAD blockouts as **provisional**, not proof of physical feasibility.
- When the user submits visual feedback, group it into sensible review batches. Fix the **source of truth**: FreeCAD for structure/fit; Blender for appearance; Godot for interaction; Core/DevicePort for behavior.

## Practical DIY buildability — the critical engineering bar
- The user is not expected to know electronics or additive manufacturing. **Agents must explain choices in plain language**, compare meaningful options, identify what is uncertain, and actively flag unsafe/unbuildable assumptions. Never request blind sign-off on a technical layout.
- Prefer realistic, low-complexity, available, off-the-shelf components and practical home FDM 3D printing over product-grade complexity and speculative features.
- The finished assembly must be **stable, attached, printable, accessible and serviceable**. A pretty 3D model is insufficient.
- Design a continuous **head → neck → chassis → base** structural path. Consider fastener access, inserts, printed layer strength, cable strain and tip resistance; require real-world strength/stability tests before safety claims.
- Design parts to suit a **known printer build volume, material and print orientation**, minimize support/glue, and provide simple assembly/disassembly order. Printer and materials are **not selected yet**.
- Check component fit in 3D with **actual drawings, connectors, cable bends, cooling, wall thickness, mounting holes, speaker chamber and assembly-tool clearance**, not idealized component envelopes alone.
- Plan access for replacing display, microphones, camera, Pi, fans, speakers and power wiring without breaking the enclosure. Avoid buried screws.
- Leave **modest, justified** expansion room and accessible connections, not arbitrary empty bulk. Evaluate a future **M.2 SSD + adapter, 2.5-inch SSD, or external USB SSD**; do **not** install or lock one now. Consider added power, cabling, heat and center of mass.
- Design for adequate ventilation, acoustic isolation, accessible wiring/power strain relief and realistic tolerances. Model/diagram where useful but do not overspecify beyond the approved milestone.

## Israel-first procurement — mandatory
- The user lives in **Israel** and wants parts easy to source locally. **Prioritize verified inventory at Israeli retailers**, VAT-inclusive ILS prices, reasonable local delivery, warranty and return options.
- For each significant display, mic, speaker, power or storage recommendation, record a real product URL, seller, checked date, availability, delivered cost and (if possible) a locally obtainable alternative. Distinguish in-stock locally from "ships to Israel", preorder and speculative listings.
- Import-only specialist parts are fallback options with explicit risks, never unnoticed single points of dependency. Do not make cosmetic/mechanical commitments to unavailable parts.
- No fixed budget or final component/vendor choice has been approved. Present options and leave the purchase decision to the user.

## Evidence and purchasing gates
- Mark every relevant claim as **VERIFIED BY SOURCE**, **PROVISIONAL**, or **REQUIRES PHYSICAL TEST**. State source and date when applicable. A vendor performance claim is not a validated result.
- Living room target **35–50 m²**, speaking range **3–5 m**; this is a **test target**, not a guarantee. Test with TV/music, barge-in, echo and reverberation later.
- Compare 2–3 display options and XVF3800 vs alternatives, but **do not finalize SKU, price ceiling, purchases or color palette** until the user has reviewed the options.
- Before buying or printing the full enclosure: provide a **human-readable BOM**, labelled internal/exploded view, fit checks, step-by-step assembly pictures, top 3–5 failure risks, and a plan for cheap **small test prints** (mount, insert and connector samples). Evidence must support sign-off.
- If large size/expense/complexity is proposed, show the tangible benefit and a simpler alternative. Prefer a modest A Pebble envelope.

## Agent operating contract
- Scope is governed by `docs/NOW.md`. GitHub Issues record proposals, decisions, review findings and commit links; an Issue is not automatic permission to implement every future stage.
- Plan briefly, work in approved **milestone bundles** without interrupting for every tiny subtask, ask only at blockers/material decisions, use focused checks and modest token budgets; do not implement speculative functionality.
- Update `docs/HANDOFF.md` on completion with verified results, unresolved issues, next approval gate and links. Preserve existing file/asset identifiers until a separately approved rename.
- **CI status must be factual:** draft CI under `ci/` is not active GitHub Actions until a workflow runs under `.github/workflows/` and is validated.

## Session recovery: minimum reading
1. `AGENTS.md` + `docs/NOW.md` for agent task scope.
2. `docs/PROJECT_PRINCIPLES.md` **when proposing design, mechanical, acoustic, component, print or procurement decisions** (do not read automatically for unrelated narrow code tasks).
3. `docs/CONTEXT_MAP.md` to open only other pertinent source documents.
4. `docs/HANDOFF.md` when resuming/reviewing the previous milestone.

**Do not turn these principles into a giant always-loaded context document.** Keep detailed comparisons and historical discussions in issue threads and linked reviews.
