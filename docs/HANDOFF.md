# Current handoff
Task: S1.T01a — CAD silhouette
Status: DONE, awaiting approval
Changed files: cad/JARVIS_blockout.FCStd, cad/build_blockout.py (generator), cad/render_preview.py, cad/JARVIS_blockout_preview.png, cad/ASSUMPTIONS.md, docs/HANDOFF.md. FreeCAD 1.1.4 installed via winget (user scope).
Result/demo: cad/JARVIS_blockout_preview.png (front/side/top). Rebuild: `"C:\Users\97254\AppData\Local\Programs\FreeCAD 1.1\bin\freecadcmd.exe" cad\build_blockout.py`
One verification + result: on a temp copy, set Params width 200→220 mm; recompute; Body X 200→220, Speaker_R x 50→55. Original file untouched.
UNVERIFIED/blockers: all dimensions PROVISIONAL (H280 W200 D160); no hardware fit verified; head is a disc primitive, not a final shell; preview was matplotlib projection (no GUI render).
Recommended next task (not started): S1.T01b — component envelopes and clearance checks, only after approval.
User approval: PENDING
