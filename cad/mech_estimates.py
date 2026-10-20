# JARVES R5b conservative hand-calculation checks (centre of mass, tip angle, joint loads). NOT physical proof.
# Masses: printed parts from print/print_report.json (slicer grams minus supports); components from vendor data where
# available (display 381 g, The Pi Hut listing) else PROVISIONAL estimates. Positions = CAD bounding-box centres.
# Run: freecadcmd cad/mech_estimates.py  -> prints a report and writes print/mech_estimates.json
import json
import math
import os
import FreeCAD as App

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
doc = App.openDocument(os.path.join(root, "cad", "JARVIS_pebble.FCStd"))
rep = json.load(open(os.path.join(root, "print", "print_report.json")))
printed = {r["name"]: (r["grams"] or 0) - (r["support_grams"] or 0) for r in rep["parts"]
           if not r["optional"] and not r["split"]}
COMP = {  # grams, source
    "Display_Panel": (381, "VERIFIED listing weight (The Pi Hut)"), "Pi_5": (70, "PROVISIONAL Pi 5 + Active Cooler"),
    "Speaker_Module": (60, "PROVISIONAL"), "Mic_Board": (15, "PROVISIONAL"), "Camera_Module": (5, "PROVISIONAL"),
}
EXTRA = [("cables in neck/head", 60, (0, 0, 160)), ("screws + nuts", 25, (0, 0, 100))]
head_parts = {"Head_Front", "Head_Back", "Display_Retainer", "Display_Panel", "Mic_Board", "Camera_Module"}
items = []
for n, g in list(printed.items()) + [(k, v[0]) for k, v in COMP.items()]:
    c = doc.getObject(n).Shape.BoundBox.Center
    items.append((n, g, (c.x, c.y, c.z)))
items += EXTRA
M = sum(g for _, g, _ in items)
com = [sum(g * p[i] for _, g, p in items) / M for i in range(3)]
head_m = sum(g for n, g, _ in items if n in head_parts) + 40   # + share of cables
head_com_z = sum(g * p[2] for n, g, p in items if n in head_parts) / (head_m - 40)
FOOT_R = 90.0                                                # stick-on feet near the rim (PROVISIONAL)
tip = math.degrees(math.atan2(FOOT_R - abs(com[1]), com[2]))
BALLAST = 400                                                # optional washers in the tray (PROVISIONAL)
com_b = (com[2] * M + 6 * BALLAST) / (M + BALLAST)
tip_b = math.degrees(math.atan2(FOOT_R, com_b))
g = 9.81
# joint loads: rigid joint tipping about the edge of its contact patch, worst of 360 deg; bolts with positive lever
# share the moment linearly (T_i = M * l_i / sum l^2). Conservative: ignores friction and compression of the patch.
def worst_bolt(bolts, patch_r, M, front_clip=None):
    worst = 0.0
    pts = [(patch_r * math.cos(math.radians(a)), patch_r * math.sin(math.radians(a))) for a in range(0, 360, 2)]
    if front_clip is not None:
        pts = [p for p in pts if p[1] >= front_clip] + [(x / 2.0, front_clip) for x in range(-2 * int(patch_r), 2 * int(patch_r) + 1)
                                                        if abs(x / 2.0) <= math.sqrt(max(0, patch_r ** 2 - front_clip ** 2))]
    for d in range(0, 360, 2):
        ux, uy = math.cos(math.radians(d)), math.sin(math.radians(d))
        piv = max(px * ux + py * uy for px, py in pts)
        lev = [piv - (bx * ux + by * uy) for bx, by in bolts]
        lev = [l / 1000 for l in lev if l > 0]
        if not lev:
            return float("inf")
        worst = max(worst, M * max(lev) / sum(l * l for l in lev))
    return worst


pol = lambda r, angs: [(r * math.cos(math.radians(a)), r * math.sin(math.radians(a))) for a in angs]
F_FACE = -19.6
JOINTS = [("head -> neck column (3x M3 + nut, r33 @7/90/173, saddle r45)", pol(33, (7, 90, 173)), 45.0, F_FACE, 108.0),
          ("neck column -> cover top (3x M3 + nut, r38, flange r45)", pol(38, (30, 150, 270)), 45.0, None, 66.0),
          ("cover -> tray (4x M3 + nut, r96.5)", pol(96.5, (15, 165, 215, 325)), 105.0, None, 4.0)]
ALLOW = 150.0          # N, conservative assumed pull-out of an M3 captive nut in a printed PETG pocket (UNVERIFIED)
cases = {"20 N push at the head top, any direction": lambda z0: 20 * (292 - z0) / 1000,
         "tipped 45 deg and caught: 2x head weight sideways at head COM": lambda z0: 2 * head_m / 1000 * 9.81 *
         math.sin(math.radians(45)) * (head_com_z - z0) / 1000,
         "5 N finger press on the touchscreen centre": lambda z0: 5 * (187.5 - z0) / 1000}
out = {"total_mass_g": round(M), "head_mass_g": round(head_m), "com_mm": [round(v, 1) for v in com],
       "tip_deg_no_ballast": round(tip, 1), "tip_deg_with_400g_ballast": round(tip_b, 1), "allowable_N": ALLOW,
       "joints": []}
print(f"mass {M:.0f} g (head {head_m:.0f} g), COM z {com[2]:.0f} mm, y {com[1]:+.0f} mm")
print(f"tip angle {tip:.0f} deg without ballast, {tip_b:.0f} deg with {BALLAST} g ballast (feet r {FOOT_R:.0f} mm)")
lift = (M - head_m) / 1000 * 9.81 * 2.0 / 3       # lifted by the head, 2x dynamic, 3 bolts share the hanging weight
print(f"lifted by the head: {lift:.1f} N per head bolt (SF {ALLOW / lift:.0f})")
for case, mfun in cases.items():
    for name, bolts, pr, clipy, z0 in JOINTS:
        T = worst_bolt(bolts, pr, mfun(z0), clipy)
        out["joints"].append(dict(case=case, joint=name, worst_bolt_N=round(T, 1), safety_factor=round(ALLOW / T, 1)))
        print(f"{case[:40]:40s} | {name[:44]:44s} | worst bolt {T:6.1f} N | SF {ALLOW / T:5.1f}")
json.dump(out, open(os.path.join(root, "print", "mech_estimates.json"), "w"), indent=1)
print("MECH DONE")
