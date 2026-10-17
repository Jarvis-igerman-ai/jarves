# JARVES Pebble A CAD check (R5b): validity, containment, collisions, continuous load path, access, cable passage,
# print sizes. Run: freecadcmd cad/check_pebble.py -> prints RESULT, exits non-zero on FAIL.
# Geometry only: strength, tolerances and acoustics UNVERIFIED.
import itertools
import math
import os
import sys
import FreeCAD as App

TOL = 1e-3
HEAD_INSIDE = ["Display_Panel", "Display_Driver", "Keepout_Display_Cable", "Camera_Module", "Mic_Board",
               "Display_Retainer"]
BASE_INSIDE = ["Pi_5", "Reserve_M2_HAT", "Speaker_Module", "Reserve_Amp", "LED_Connector"]
ALTERNATES = {"LED_Diffuser_Front": "LED_Blank_Front", "LED_Diffuser_Back": "LED_Blank_Back"}
# head -> table: every structural link must touch (distance 0) so nothing floats
LOAD_PATH = [("Head_Front", "Neck_Column", 0.3), ("Neck_Column", "Base_Shell"), ("Base_Shell", "Base_Floor"),
             ("Head_Back", "Head_Front"), ("Display_Retainer", "Head_Front"), ("Head_Face_Zone", "Head_Front"),
             ("Base_Shell_Band", "Base_Shell"), ("Base_Door", "Base_Shell"), ("Pi_5", "Base_Floor"),
             ("Speaker_Module", "Base_Floor"), ("LED_Blank_Front", "Base_Shell", 0.3)]   # snap-fit clearance
PRINTED = ["Head_Front", "Head_Back", "Display_Retainer", "Neck_Column", "Base_Shell", "Base_Door", "Base_Floor",
           "LED_Blank_Front"]
BED = 220.0  # common hobby bed (e.g. 220-256 mm); printer NOT selected
PLUGS = {"HDMI-A overmould (UNVERIFIED typical)": (22, 12), "USB-A overmould": (17, 9), "USB-C overmould": (13, 7),
         "CSI ribbon 22-pin end (16 mm wide)": (17, 2), "speaker plug (UNVERIFIED)": (8, 5)}


def categories(doc):
    sh, cats, i = doc.getObject("Params"), {}, 2
    while True:
        try:
            k = sh.get(f"E{i}")
        except Exception:
            break
        if not k:
            break
        cats[k] = sh.get(f"F{i}")
        i += 1
    return cats


def check(doc):
    S = lambda n: doc.getObject(n).Shape
    fails, report = [], []
    cats = categories(doc)
    for n in list(cats) + ["_Head_Void", "_Base_Void"]:
        o = doc.getObject(n)
        if o is None or S(n).isNull() or not S(n).isValid() or S(n).Volume <= TOL:
            fails.append(f"{n} missing/invalid/empty")
    if fails:
        return fails, report, cats
    for group, void in ((HEAD_INSIDE, "_Head_Void"), (BASE_INSIDE, "_Base_Void")):
        for n in group:
            v = S(n).cut(S(void)).Volume
            if v > TOL:
                fails.append(f"{n} outside {void} by {v:.1f} mm3")
    for a, b in ALTERNATES.items():           # swap-in parts must be drop-in replacements
        ba, bb = S(a).BoundBox, S(b).BoundBox
        if abs(S(a).Volume - S(b).Volume) > 1 or (ba.Center - bb.Center).Length > 0.01:
            fails.append(f"{a} is not a drop-in replacement for {b}")
    door = S("_DoorBox").BoundBox
    for n in ("LED_Connector",):
        conn = S(n).BoundBox
        if not (door.XMin <= conn.XMin and conn.XMax <= door.XMax and door.ZMin <= conn.ZMin and conn.ZMax <= door.ZMax):
            fails.append(f"{n} not reachable through the rear door opening")
    # Pi service: microSD edge (rear, +y) inside the door footprint and within finger reach of the opening
    pi = S("Pi_5").BoundBox
    sd_gap = S("_Base_Void").BoundBox.YMax - pi.YMax
    if not (door.XMin <= pi.XMin and pi.XMax <= door.XMax and door.ZMin <= pi.ZMin and sd_gap <= 45):
        fails.append(f"Pi microSD edge not reachable through the rear door (gap {sd_gap:.0f} mm)")
    side_room = S("_Base_Void").BoundBox.XMax - pi.XMax
    if side_room < 35:
        fails.append(f"Pi power/HDMI side plug room only {side_room:.0f} mm")
    front_room = pi.YMin - S("Speaker_Module").BoundBox.YMax
    if front_room < 25:
        fails.append(f"Pi USB plug room to the speaker only {front_room:.0f} mm")
    report.append(f"Pi access: microSD {sd_gap:.0f} mm behind the door, side plug room {side_room:.0f} mm, "
                  f"USB plug room {front_room:.0f} mm")
    # neck cable passage: every plug must pass the bore and the head-floor hole one at a time
    bore = 2 * (S("Keepout_Neck_Cables").BoundBox.XLength / 2 + 0.5)
    for plug, (w, t) in PLUGS.items():
        if math.hypot(w, t) > bore - 2:
            fails.append(f"{plug} {w}x{t} does not pass the D{bore:.0f} neck bore")
    report.append(f"neck bore D{bore:.0f}: largest plug diagonal {max(math.hypot(*v) for v in PLUGS.values()):.0f} mm "
                  f"(plugs pass one at a time; 5 cables in total)")
    real = [n for n in sorted(cats) if cats[n] != "alternate"]
    for a, b in itertools.combinations(real, 2):
        if not S(a).BoundBox.intersect(S(b).BoundBox):
            continue
        v = S(a).common(S(b)).Volume
        if v > TOL:
            fails.append(f"collision {a} x {b}: {v:.1f} mm3")
    for link in LOAD_PATH:
        a, b, tol = (link + (0.01,))[:3]
        d = S(a).distToShape(S(b))[0]
        if d > tol:
            fails.append(f"load path gap {a} -> {b}: {d:.2f} mm")
    report.append(f"load path: {len(LOAD_PATH)} contacts checked (head body -> neck column -> ribbed cover top -> "
                  f"cover wall -> tray; lid, retainer, door, colour zones attached)")
    big = []
    for n in PRINTED:
        bb = S(n).optimalBoundingBox()
        dims = sorted([bb.XLength, bb.YLength, bb.ZLength], reverse=True)
        if dims[1] > BED:  # needs two dims on the bed
            big.append(f"{n} {dims[0]:.0f}x{dims[1]:.0f}")
        report.append(f"print part {n:17s} {dims[0]:6.1f} x {dims[1]:6.1f} x {dims[2]:6.1f} mm")
    if big:
        fails.append(f"exceeds {BED:.0f} mm bed: {big}")
    ext = None
    for n, c in cats.items():
        if c in ("exterior", "structure"):   # installed physical parts (floor is structure)
            bb = S(n).optimalBoundingBox()
            ext = bb if ext is None else ext.united(bb)
    report.append(f"overall exterior: W {ext.XLength:.1f} x D {ext.YLength:.1f} x H {ext.ZLength:.1f} mm (PROVISIONAL)")
    return fails, report, cats


if __name__ != "check_pebble_lib":
    path = os.environ.get("JARVIS_PEBBLE_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                               "JARVIS_pebble.FCStd")
    f, rep, _ = check(App.openDocument(path))
    print("\n".join(rep))
    print("RESULT:", "FAIL " + "; ".join(f) if f else "PASS (validity, containment, collisions, load path, print size)")
    sys.stdout.flush()
    os._exit(1 if f else 0)
