# Current handoff
Task: S1.T03R1 — Pebble A component selection & engineering fit review (Issue #3). Status: REVIEW DELIVERED, awaiting user decisions. **Research/design only: no purchase, no SKU lock, no CAD/Blender/Godot redesign; S1.T04 not started.**
Model: Claude Opus 5.5 (`claude-opus-5-5`).
Read first: `docs/reviews/S1_T03R1_ENGINEERING_REVIEW.md` (bottom line, display/mic tables with Israel sourcing, fit study, dimensions, structure, assembly, cables/power, cooling, SSD, risks, validation plan, checklist, decisions, sources).
New files: docs/reviews/S1_T03R1_ENGINEERING_REVIEW.md, design/review/{pebble_fit_study.py,pebble_layout.py,pebble_layout.png}. Updated: docs/reviews/S1_DESIGN_REVIEW.md (pointer), docs/{HANDOFF,NOW}.md.
Reproduce: `freecadcmd design\review\pebble_fit_study.py` (packing table); `freecadcmd design\review\pebble_layout.py` (8 layout checks, all PASS + drawing).
Evidence: display/mic sizes MEASURED from manufacturer STEP files in FreeCAD (5" Ø150x18.05; 4" Ø126x18.2; XVF3800 PCB ~100.5x101.8, 9.03 thick). Israel stock checked 2026-10-17: Piitel lists 5" round HDMI at ₪590 incl. VAT (add-to-cart; stock not explicit); no local far-field mic array or round DSI display found (import-only). Downloaded STEP files were kept in the session scratchpad only (not committed).
Key findings: local 5" display needs a W215 head; XVF3800 needs a raised crown pod (else 25-41 mm ports resonate in speech band); proposed local-first size ~H299 x W215 x D210 (PROVISIONAL); base layout fits on paper but speaker pods only 6 mm from Pi; tip ~33°/39° (masses PROVISIONAL); Pi 5 USB power budget may be exceeded (measure).
Unverified: Pi 5 USB current limit figure (recalled), CSI cable lengths, HDMI plug size, Israeli import VAT/shipping, Piitel warranty terms, all masses/strength/thermal/acoustic performance.
Decisions needed: accept imported mic; local-first size vs compact import variant; crown-pod look; bench-test purchase approval; printer/bed/material.
Next (not started): after decisions, bench V1-V2 with bare boards, then FreeCAD redesign with real STEP parts.
CI: still not active (Issue #2).
User approval: PENDING
