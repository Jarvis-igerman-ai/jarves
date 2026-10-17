# NOW — S1.T03R4: Assembly & print-shop readiness (Issue #4 follow-up)

**Status (18 Oct 2026):** R5b CAD implementation **delivered — STOPPED for user review**. See [S1_T03R5B_IMPLEMENTATION_REVIEW.md](reviews/S1_T03R5B_IMPLEMENTATION_REVIEW.md) and [assembly/ASSEMBLY_GUIDE.md](assembly/ASSEMBLY_GUIDE.md). Next work waits for the answers in HANDOFF.

**Previous status:** **R5b APPROVED for CAD implementation + focused no-purchase feasibility checks** by user, 17 Oct 2026 ([authorization in Issue #4](https://github.com/igalgerman/JARVIS/issues/4#issuecomment-6240553020)). Design basis: `docs/reviews/S1_T03R5_ARCHITECTURE_REVIEW.md` (Pi in base; structural ribbed base cover; no separate chassis). **No purchase, supplier contact or physical print authorized.**

## Goal
JARVES must be realistically printable by an ordinary Haifa/Krayot 3D-printing service, affordable, and practical for a beginner to assemble at home (see `docs/PROJECT_PRINCIPLES.md` → "Local print-shop feasibility").

## Keep
Pebble A, finish C, current head proportions, R3 speaker grilles and removable LED ring. No unnecessary redesign.

## Approved work
1. Mechanical reliability: stronger neck seat + anti-rotation; review/model fasteners, screw bosses, insert holes, snap-fits, cable routing; flag what needs physical tests.
2. LEGO-style assembly instructions: numbered parts, step images with arrows, printed/purchased/electronic marking, tools, difficulty.
3. Practical printing: export real printable STL (printed parts only), check bed fit with margin, orientation, supports, tolerances; real slicer estimates (time, grams, supports); split fallback for 210–215 mm parts; simplify where it saves cost/effort.
4. Haifa print-shop feasibility: 2–3 real local services (research only, no contact), quote-ready package, per-part and total ILS **estimate** with assumptions.
5. Auto-exported assembly diagrams, exploded views, per-part previews with direct links; focused checks only.

## Not in scope
Purchases, contacting suppliers, physical printing, final SKUs, software features, CI.


## R5b implementation acceptance (user-approved, bounded)
- Implement **real parametric CAD and accessible novice assembly** for the R5b structure. Verify joint load path, realistic fastener/nut/tool access, component STEP fit and cable routing (including the mic-in-head to speaker-in-base lead). Fix genuine problems at the CAD owner; do not force a number of parts or screws.
- Research vendor-documented plug-and-play mic→speaker compatibility, amplifier and USB-power budgets, connector and ribbon fit; source only credible evidence. Clearly flag physical unknowns.
- Produce revised printer-ready **draft** part exports, actual slicer report, cost **estimate**, correct visual exports to Blender/Godot, exploded view and illustrated assembly/BOM. Preserve the accepted exterior and optional LED.
- Follow [engineering review gates](ENGINEERING_REVIEW_GATES.md) with **targeted no-purchase checks**; don't require exploratory purchases just to choose architecture. Hand off measured vs estimated vs untested results, commit and **stop at the next user review**.

## Completion gate
Plain-language answers (Haifa printability, cost range from evidence, assembly difficulty, what remains unverified); HANDOFF + Issue updated; commit; STOP for user review.
