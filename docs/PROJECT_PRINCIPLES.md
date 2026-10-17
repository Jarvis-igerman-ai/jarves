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

## Assembly simplicity — outcome, not an arbitrary part count

- **Explicit user clarification (2026-10-17):** the user is **not** specifying 5 printed pieces or any numerical limit. A past casual example of "five parts" must **never** become a mechanical target, quota, or design acceptance criterion. The intended experience is: order a small practical set of printable enclosure parts from a normal local print service, place off-the-shelf electronics into accessible mounts, connect factory-made plug-in cables, close with basic accessible fasteners, and have a usable JARVES. The engineering agent — **not the user** — must determine and justify the *simplest viable assembly architecture*.
- **No user-performed soldering, gluing, drilling, cutting, hand filing, or specialist electronics work** should be required. Prefer pre-terminated harnesses, commercially available adapters and ready-made mechanical mounts. If any of these operations seems unavoidable, **stop, identify why, offer a plug-and-play alternative, price/complexity impact, and seek user approval before proceeding**. Do not assume a professional print shop will secretly do secondary machining for free.
- Minimize **overall effort**, not only the printed-part count: consider printing supports/risk, large-bed availability, cost, fasteners, insertion order, required tools, wiring, accessibility, service, and structural reliability together. One giant closed shell can be worse than several easily printed accessible pieces; many fragile snap-fits can also be worse than a few screws. Count parts as an **outcome of evaluated alternatives**, never start from a forced count.
- Compare the current design against the **simplest mechanically credible** alternative and recommend one plainly, showing actual printed components, off-the-shelf hardware, steps, tools, estimated novice assembly time and remaining engineering checks. Preserve the approved appearance. Detail a LEGO-like illustrated guide **after** proving a beginner-friendly assembly sequence, not to justify otherwise unnecessary complexity.
- Treat print-ready/source files and physical hardware fit/strength as **unverified until tested**. This design decision is a future gate, not authorization to interfere with active work.

## Local print-shop feasibility — required before committing to fabrication

- **User acceptance criterion:** JARVES must be practical for an ordinary **Haifa/Krayot-area FDM printing service** to quote and manufacture from supplied files, at a comprehensible reasonable cost and with low home-assembly complexity — not an industrial-only, specialist-resin, or unexpectedly expensive build. Preserve approved appearance while designing for printability. A CAD bounding-box PASS alone is **not** vendor-ready approval.
- At the next explicitly approved print/assembly readiness gate (not during unrelated tasks), create a **quote-ready package** of *only actual printed* parts, with each part in mm as printable STL/3MF (keep editable FreeCAD source), quantities, material/colour recommendation (including selected visual finish C), orientation, supports/brim needs, tolerance and required finishing. Clearly separate purchased metal/acrylic/fabric/electronics, mock envelopes, reserved volumes, alternative covers and printed parts; avoid charging to print non-parts.
- Check actual usable print bed and slicer **for every part**, including margin for brim/adhesion/supports, not just raw shape size. Pay particular attention to the ~210 mm base and ~215 mm head shells. Show a fallback split/join strategy if a local shop cannot reliably print the unsplit part, and document added labor/strength/aesthetic implications. Do not claim generic 220×220 compatibility guarantees reliable output.
- Estimate print times, material grams (including supports and any failures/reprints margin), number of print jobs, support removal, post-processing and shop labor with an actual slicer and recorded settings. Provide a **per-part and total preliminary ILS quote range**, clearly labelled *estimate, not an actual shop offer*. Compare simple PETG/PLA variants only where sensible and safer for thermal requirements. Don't invent slicer data or certainty.
- Identify 2–3 **real Haifa-area printing vendors** capable of receiving the files and ask for / prepare a no-obligation quote on the **full printable shell only**, with delivery, VAT, finish, lead time and support-removal costs shown separately. No request or quote implies purchase permission. Vendor responses, if any, must be quoted literally with date and scope. No fixed price ceiling has been approved.
- **Simplify aggressively where it materially reduces print price or assembly effort**: minimize supports, fragile snap-fits, glue, hard-to-reach screws, unnecessary cosmetic multi-piece splits and high part counts. Don't sacrifice structural safety merely to lower price. Show a short illustrated beginner-friendly assembly outline and a list of practical tools/materials a home assembler needs.
- Only after the user sees this cost/complexity evidence and approves, proceed to small test print coupons, fit trials or complete outsourced printing. Never place orders or release manufacturing STLs solely because a visual milestone passed.

## Israel-first procurement — mandatory
- The user lives in **Israel** and wants parts easy to source locally. **Prioritize verified inventory at Israeli retailers**, VAT-inclusive ILS prices, reasonable local delivery, warranty and return options.
- For each significant display, mic, speaker, power or storage recommendation, record a real product URL, seller, checked date, availability, delivered cost and (if possible) a locally obtainable alternative. Distinguish in-stock locally from "ships to Israel", preorder and speculative listings.
- Import-only specialist parts are fallback options with explicit risks, never unnoticed single points of dependency. Do not make cosmetic/mechanical commitments to unavailable parts.
- No fixed budget or final component/vendor choice has been approved. Present options and leave the purchase decision to the user.

## Evidence and purchasing gates
- Mark every relevant claim as **VERIFIED BY SOURCE**, **PROVISIONAL**, or **REQUIRES PHYSICAL TEST**. State source and date when applicable. A vendor performance claim is not a validated result.
- Room size estimate **35–50 m²**; **required voice pickup distance is NOT YET DECIDED**. Earlier 3–5 m was exploratory, not an approved specification. Revisit later with placement, room acoustics, realistic cost/complexity tradeoffs and tests with TV/music, barge-in and echo. Do not hard-code 5 m into hardware selection.
- Compare 2–3 display options and XVF3800 vs alternatives, but **do not finalize SKU, price ceiling, purchases or color palette** until the user has reviewed the options.
- Before buying or printing the full enclosure: provide a **human-readable BOM**, labelled internal/exploded view, fit checks, step-by-step assembly pictures, top 3–5 failure risks, and a plan for cheap **small test prints** (mount, insert and connector samples). Evidence must support sign-off.
- If large size/expense/complexity is proposed, show the tangible benefit and a simpler alternative. Prefer a modest A Pebble envelope.

## Repository and issue hygiene (persistent rule)
- At each **completed milestone**, review tracked files/diffs: keep source, essential reproducible assets, working previews and key decisions; exclude caches, binaries/installers, temporary renders, debug dumps, duplicate exports and local environments through appropriate ignore rules. Do not indiscriminately delete existing tracked files or history. Propose substantial cleanup separately before destructive changes.
- Treat **GitHub Issues as the task ledger**: each issue states scope, acceptance, evidence and linked commits/PR. On completion, record what was verified, remaining follow-ups and exact commit, then **close the issue** when acceptance is satisfied; otherwise keep open with explicit blockers or split a follow-up issue. Do not leave completed work open indefinitely. Never delete issues to tidy: use closed history, avoid duplications, and periodically review stale open issues.
- Keep README as a concise navigation index and docs/HANDOFF.md as the latest status; archive superseded details rather than duplicating or repeatedly rewriting everything. Avoid a massive context load.

## Model and tool portability
- Preserve a **provider-neutral workflow** across Claude Code and Codex: GitHub Issue scope → short plan → targeted changes → focused tests → linked commit → concise handoff → approval gate. The repository, not chat history, carries durable decisions.
- Spend premium reasoning selectively on architecture, design judgement and hard troubleshooting; routine coding/validation/docs use the cheaper suitable model **when it truly saves overall effort**. Do not prescribe the same vendor/version for different tools or trigger repeated context reloads for trivial savings.
- For a switch between agents, finish the current write, commit, update `docs/HANDOFF.md`, and make sure the next agent pulls before changing anything. One active implementer at a time.

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
