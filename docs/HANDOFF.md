# Current handoff — 17 October 2026

## Active authorized milestone

**S1.T03R2 — Pebble A visual redesign, [Issue #4](https://github.com/igalgerman/JARVIS/issues/4).** Approved for implementation in [NOW.md](NOW.md). One implementing agent; this management/documentation update does not establish implementation progress, test success, or visual approval.

**Source of truth:** [NOW.md](NOW.md) controls current scope and approval; [ROADMAP.md](ROADMAP.md) is planning only; [PROJECT_PRINCIPLES.md](PROJECT_PRINCIPLES.md) and [AGENTS.md](../AGENTS.md) govern execution. The previous S1.T03R1 decision-request below is historical, **not the active authorization gate**. No part SKU, purchase, microphone distance, printable release or software feature is approved.

**Completion pending:** FreeCAD source geometry and credible head-to-body load path → downstream Blender appearance/color variants → Windows Godot interactive viewer; focused evidence, multi-angle screenshots, commit linked to #4, remaining physically UNVERIFIED assumptions, and user visual review. Do not mark #4 completed without acceptance evidence. Avoid disrupting any agent's in-progress implementation.

**Future work parked:** #2 CI; #5 evidence-based library evaluation before software implementation; #6 minimal Core integration; #7 face/voice; #8 hardware prototype preparation. Do not open duplicate speculative issues. Close verified completed issues with evidence at milestone boundaries, retain history, and do not bulk-delete project assets.

**Agent transition (Claude Code ↔ Codex):** one writer at a time; finish/commit current work, update this handoff with actual changed paths, checks and blockers, then have receiving agent pull the latest. Feedback travels back to the owning source (FreeCAD/Blender/Godot/Core), followed by *only necessary* downstream rebuild and focused tests.

**Next implementing-agent action:** read NOW and Issue #4, check branch/worktree for current progress, and continue only the approved visual milestone; replace this status with factual commits/tests on delivery. No implementation completion has been verified by this documentation update.

---

## Archived S1.T03R1 research handoff (historical reference; superseded as active status)

Task: S1.T03R1 — Pebble A component selection & engineering fit review (Issue #3). Status: REVIEW DELIVERED, awaiting user decisions. **Research/design only: no purchase, no SKU lock, no CAD/Blender/Godot redesign; S1.T04 not started.**
Model: Claude Opus 5.5 (`claude-opus-5-5`).
Read first: `docs/reviews/S1_T03R1_ENGINEERING_REVIEW.md` (bottom line, display/mic tables with Israel sourcing, fit study, dimensions, structure, assembly, cables/power, cooling, SSD, risks, validation plan, checklist, decisions, sources).
New files: docs/reviews/S1_T03R1_ENGINEERING_REVIEW.md, design/review/{pebble_fit_study.py,pebble_layout.py,pebble_layout.png}. Updated: docs/reviews/S1_DESIGN_REVIEW.md (pointer), docs/{HANDOFF,NOW}.md.
Reproduce: `freecadcmd design\review\pebble_fit_study.py` (packing table); `freecadcmd design\review\pebble_layout.py` (8 layout checks, all PASS + drawing).
Evidence: display/mic sizes MEASURED from manufacturer STEP files in FreeCAD (5" Ø150x18.05; 4" Ø126x18.2; XVF3800 PCB ~100.5x101.8, 9.03 thick). Israel stock checked 2026-10-17: Piitel lists 5" round HDMI at ₪590 incl. VAT (add-to-cart; stock not explicit); no local far-field mic array or round DSI display found (import-only). Downloaded STEP files were kept in the session scratchpad only (not committed).
Key findings: local 5" display needs a W215 head; XVF3800 needs a raised crown pod (else 25-41 mm ports resonate in speech band); proposed local-first size ~H299 x W215 x D210 (PROVISIONAL); base layout fits on paper but speaker pods only 6 mm from Pi; tip ~33°/39° (masses PROVISIONAL); Pi 5 USB power budget may be exceeded (measure).
Unverified: Pi 5 USB current limit figure (recalled), CSI cable lengths, HDMI plug size, Israeli import VAT/shipping, Piitel warranty terms, all masses/strength/thermal/acoustic performance.
## Questions for the user
1. **Imported microphone?** No far-field mic array (with echo cancellation) is stocked in Israel. Options: (a) import one — recommended: Seeed ReSpeaker XVF3800, US$54.90; (b) cheaper import ReSpeaker Lite (~US$29, 2 mics, weaker at distance); (c) local-only mic (e.g. Pirate Audio Dual Mic ₪107, Piitel) — likely will not work at 3–5 m while JARVES plays sound. Recommendation: (a).
2. **Which size?** (a) Local-first: 5″ display bought in Israel (Piitel ₪590), robot ~H299 × W215 × D210 mm — recommended; (b) Compact: imported 4″ DSI display (backorder), ~H285 × W205 × D200 mm.
3. **Crown pod look:** is a small raised cap (~116 mm wide, +15 mm) on top of the head acceptable? It holds the mic board right under its sound holes. Alternative: no pod, but only with the 2-mic ReSpeaker Lite.
4. **Bench test purchase:** when you are ready, may we plan to buy the bare mic board(s) and the display only for a living-room test (V1–V2 in the review) before any enclosure is printed? Nothing will be bought without your explicit OK.
5. **Printer:** which 3D printer (model, bed size) and material do you have or plan to get? Head parts need a ≥220 mm bed or a split design.
6. **Room details (optional):** confirm living-room size (35–50 m²) and where JARVES will stand (table/sideboard, distance to TV).
Next (not started): after decisions, bench V1-V2 with bare boards, then FreeCAD redesign with real STEP parts.
CI: still not active (Issue #2).
User approval: PENDING
