# NOW — S1.T03R4: Assembly & print-shop readiness (Issue #4 follow-up)

**Status:** S1.T03R5 architecture review delivered (`docs/reviews/S1_T03R5_ARCHITECTURE_REVIEW.md`, recommends R5b "structural base cover", no chassis). Awaiting approval before CAD. Approved by user 17 Oct 2026.

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

## Completion gate
Plain-language answers (Haifa printability, cost range from evidence, assembly difficulty, what remains unverified); HANDOFF + Issue updated; commit; STOP for user review.
