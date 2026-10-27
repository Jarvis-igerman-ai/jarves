# JARVES — progressive engineering reviews, simulation and challenge gates

**Status:** standing quality/review plan for future milestones; **NOT** authorization to start new work, buy hardware, contact suppliers, or print parts. Each milestone's actual scope is governed by [NOW](NOW.md) and user approval. Use alongside [PROJECT_PRINCIPLES](PROJECT_PRINCIPLES.md), [ROADMAP](ROADMAP.md), [AGENTS](../AGENTS.md), and [HANDOFF](HANDOFF.md).

## Intent

The goal is a **safe, reliable, attractive, practical, serviceable home companion**, not merely a nice render, minimum part count, or simulation that passes. Quality improves through **several well-timed, evidence-based challenges** as the product matures. Some defects cannot be known until real hardware exists; discover as much as possible cheaply beforehand and reserve real-world safety claims for physical evidence.

**User preference (17 Oct 2026):** avoid exploratory hardware purchases or repeated prototypes merely to decide between architectures. Prefer proven components, documented interfaces, sound engineering, conservative margins and reversible choices; only propose early experiments when a serious risk cannot be designed out. Some final physical checks will still be mandatory before using a real robot, and need separate user approval.

**Current mechanical direction:** see [S1.T03R5 design decision](archive/reviews/S1_T03R5_DESIGN_DECISION.md) (Pi in base, corrected simple build, CAD not implemented as of the recorded decision). This plan does **not** approve the estimated ~9 printed parts/~40 screws, and does **not** restart the architecture search automatically.

## Review types — use the lightest that can catch the risk

1. **Focused design challenge (before expensive implementation):** one engineer critiques the selected approach and the strongest credible alternative. Look for false assumptions, unnecessary parts, inaccessible assembly and expensive reversals. Decide and record the rationale; **do not** hold repeated architecture debates with no new evidence.
2. **Automated checks and simulations (as the relevant model becomes real):** run only targeted geometric, slicer, power-budget, software, fault-injection or behavior checks. Record model inputs, assumptions and limitations. Simulation supports a decision; it cannot certify safety or real acoustic quality.
3. **Independent, read-only challenge (at material risk gates):** a separate capable reviewer sees original user requirements and measured facts before prior agents' conclusions where possible, then critiques the actual artifact/diff. Main engineer is the sole writer and owns the final call. One bounded review, not a costly multi-agent loop.
4. **Physical validation (late and selectively):** the first real electronics, small coupons and final assembled JARVES require real tests when authorized. Do not label paper calculations, screenshots, CAD fits, supplier descriptions or open-air audio tests as physical proof of the closed final enclosure.
5. **User experience review:** show project-generated images, interactive Godot scenes, assembly diagrams and simple explanations; the user approves visible design, acceptable cost and practical build experience, **not hidden engineering choices**.

## Progressive checkpoints

These are **future review checkpoints**, not extra implementation milestones to start now. Schedule each when its corresponding work is actually reached.

| Checkpoint | When | Challenge / evidence to produce | Decision / exit |
|---|---|---|---|
| **R0 — Architecture freeze-for-now** | Before major CAD redesign (current R5 direction) | Challenge Pi-in-base layout against critical alternatives; identify the few decisions that can cause expensive rework. Compare real assembly effort and structural path, not just part counts. Identify assumptions. | Record **one preferred candidate**, rejected options, 3–5 main risks and cheap mitigations. Reopen only for new evidence or a genuine blocker. |
| **R1 — Mechanical digital prototype** | After meaningful FreeCAD assembly geometry | Parametric fit/clearance: real or explicitly provisional STEP envelopes, mounting holes and fastener reach; realistic plug dimensions and cable bend/strain clearance; component replacement path, neck-to-chassis load path, layer orientation, coarse center-of-mass/tip estimate. Consider simple structural calculations/FEA **only if inputs and boundary conditions are credible**. | Targeted CAD checks and an **exploded novice assembly sequence**. A reviewer challenges load path, nut traps, count of screws, print orientation, and service access. No physical strength certification. |
| **R2 — Printability / cost challenge** | After revised printable CAD, before releasing files to print shop | Reproducible slicer on **actual printable parts**: build plate **including adhesion/support margin**, material, infill/orientation, supports, g/time, parts, fit tolerances; PETG heat/load considerations; FDM service viability in Haifa/Krayot; cost ranges clearly not quotes; check finish C / navy band method for practical manufacturability. | Viable print strategy and quote-ready **draft** package; prefer unbroken reliable print, or justified splits. **No orders or print authorization.** |
| **R3 — Visual and assembly simulator review** | After updated FreeCAD → Blender → Godot export | True project-generated front/side/rear/interior and exploded views; inspect seams, service door, crown mic position, LED blank/optional ring, finish C, cable path and screwdriver access. Simulate assembly order/visibility where the viewer supports it; don't pretend animation proves physical fit. | User reviews exterior and assembly clarity; only materially justified changes return to the owning source layer and affected downstream exports. |
| **R4 — Software, interaction and failure simulation** | During approved Core/DevicePort/Godot work, before hardware dependency | Use mocked devices and replayable events: voice/face behavior, permissions, mic mute/stop, startup/shutdown, offline operation, reconnect, lost camera/mic/network, component timeouts, logging/privacy-safe diagnosis, recoverable failure. Verify bounded errors, not merely happy path. | Focused functional and fault-injection evidence; independent review if a high-impact runtime architecture/security change. **Simulated microphone input cannot prove actual far-field pickup or echo cancellation.** |
| **R5 — Hardware/BOM readiness** | Before *any* actual purchasing or fabrication commitment | Check exact orderable **off-the-shelf, preterminated** component chain, fit and mounts, camera ribbon and cable routing, amplifier/speaker/output connectors, USB and total power budget, approved PSU, mic/audio reference evidence, fan noise isolation approach, repair availability, delivered Israeli costs; maintain low-risk fallback. Challenge head heat/acoustics, power and stability again with evidence. | Plain-language BOM, uncertainty/risk list, cost and risk-based **minimal** real-world validation plan. User must separately approve purchases, vendor contact and prints. |
| **R6 — First physical build / safety & audio verification** | **Only after approvals**, when components and initial print(s) exist | Test as relevant: actual fasteners and reassembly, neck joint strength and head handling, stability/tip with actual masses, no accessible sharp or hot surfaces, safe power/strain relief, thermal run/no throttling, fan-to-mic noise, speech recognition in real room noise, speaker→mic echo/double-talk/AEC, camera/privacy indicators, service access and cable wear. Cheap coupons or one staged partial print may help when a specific unproven hazard justifies them. | Record measurements, pass/fail and corrections. **Do not declare physical safety, acoustic performance or manufacturing readiness before relevant real tests.** Stop and fix serious risks before ordinary use. |
| **R7 — Integrated product acceptance and iteration** | After a complete assembled, running JARVES, then on material changes | End-to-end typical daily interactions and fault recovery; extended thermal/audio runs as needed; visual finish, usability, physical robustness, repeatable assembly, total actual costs and residual limitations. Challenge the highest-impact remaining risks and plan small subsequent improvements. | Clear **v1 acceptance checklist**, known limitations, outstanding follow-up Issues. Subsequent improvements are targeted, not an automatic whole-project redesign. |

## Continuous risk review — small, auditable and inexpensive

Every relevant milestone should record a **short risk/delta note**, preferably in the existing Issue and HANDOFF rather than a new document:
- **What changed / what is assumed?** Critical physical/interface changes only.
- **Evidence level:** `MEASURED/TESTED`, `SUPPORTED BY SOURCE OR SIMULATION`, or `UNVERIFIED — PHYSICAL TEST NEEDED`. Give exact test/source and conditions.
- **Failure consequence and fallback:** acoustic quality, power/heat, tip/structural safety, wiring and maintenance are priority areas. List the next cheapest meaningful check.
- **What would cause re-opening the architecture?** A demonstrated safety/quality blocker or strongly better alternative; *not* an abstract 'what if' with no evidence.
- **Exit criterion:** pass with evidence, conditional carry-forward with owner and latest safe decision point, or stop on a critical issue.

### How to challenge without slowing development

- **Routine milestone:** the implementing engineer's own focused checklist/diff review + existing automated targeted checks. Avoid formal independent agent calls for trivial cosmetics or docs.
- **Independent challenge when useful:** before a **hard-to-reverse architecture decision**, after a **structural/power/acoustics/security risk** surfaces that ordinary evidence cannot resolve, or at **pre-print/pre-build and pre-v1 acceptance**. One narrowly scoped read-only reviewer; disclose unknowns; avoid shared writes.
- **Changes propagate from the owning layer:** FreeCAD (mechanical), Blender (materials), Godot (simulated UX), Python Core/DevicePort (behavior). Rebuild only necessary downstream artifacts. Changes in real hardware assumptions go back to the appropriate hardware/FreeCAD owner.
- **Do not require every test before CAD.** Perform the cheapest possible credible check at the stage when evidence exists; leave a justified physical gate at the last safe point. Conversely, don't make an expensive print/purchase based only on a diagram.
- **Do not create endless approval gates.** Bundle related changes into an approved milestone. Escalate only user-facing aesthetics, significant budget/time changes, material trade-offs, irreversible actions, and genuinely blocked decisions.
- **Document one recommended outcome**, not a giant menu of unresolved options. The user selects look, acceptable cost and what to buy; expert agents own normal technical trade-offs.

## Minimal milestone review template

Add this compact section to the relevant Issue/HANDOFF when a meaningful review is completed:

```md
### Engineering review — <milestone / commit>
- Purpose / change:
- Evidence inspected (links, source revision, measured vs simulated):
- Top findings (maximum 3–5, severity and consequences):
- Actions taken / owner / affected layer:
- Remaining physical unknowns and latest safe gate:
- Verdict: PASS / CONDITIONAL / BLOCKED
- Next approved action (or WAIT FOR USER):
```

**Never conflate three separate claims:** (1) *CAD or simulator works*, (2) *the selected hardware is purchasable and compatible*, (3) *the physical assembled robot is safe and performs well*. Progress is real at each step, but each requires its own evidence before being marked complete.
