# JARVES Pebble A CAD check (S1.T03R2): validity, containment, collisions, continuous load path, print sizes.
# Run: freecadcmd cad/check_pebble.py  -> prints RESULT, exits non-zero on FAIL. Geometry only: strength UNVERIFIED.
import itertools
import os
import sys
import FreeCAD as App

TOL = 1e-3
HEAD_INSIDE = ["Display_Panel", "Display_Driver", "Keepout_Display_Cable", "Camera_Module", "Mic_Board",
               "Head_Spine", "Head_Spine_Foot"]
BASE_INSIDE = ["Pi_5", "Reserve_M2_HAT", "Reserve_SSD_USB", "Speaker_Pod_L", "Speaker_Pod_R", "Speaker_Driver_L",
               "Speaker_Driver_R", "Bridge_Plate", "Neck_Flange_Low", "Bridge_Post_FL", "Bridge_Post_FR",
               "Bridge_Post_RL", "Bridge_Post_RR", "Pi_Standoff_1", "Pi_Standoff_2", "Pi_Standoff_3", "Pi_Standoff_4"]
# head -> table: every link must touch (distance 0) so nothing floats
LOAD_PATH = [("Head_Spine", "Head_Spine_Foot"), ("Head_Spine_Foot", "Head_Back"), ("Head_Front", "Neck_Flange_High"),
             ("Head_Back", "Neck_Flange_High"), ("Neck_Flange_High", "Neck_Tube"), ("Neck_Tube", "Neck_Flange_Low"),
             ("Neck_Flange_Low", "Bridge_Plate"), ("Bridge_Plate", "Bridge_Post_FL"), ("Bridge_Plate", "Bridge_Post_RR"),
             ("Bridge_Post_FL", "Base_Floor"), ("Bridge_Post_RR", "Base_Floor"), ("Base_Floor", "Base_Shell"),
             ("Pi_Standoff_1", "Base_Floor"), ("Face_Cover", "Head_Front"), ("Mic_Cap", "Head_Front"),
             ("Base_Door", "Base_Shell")]
PRINTED = ["Head_Front", "Head_Back", "Mic_Cap", "Base_Shell", "Base_Door", "Base_Floor", "Neck_Collar",
           "Neck_Flange_High", "Neck_Flange_Low", "Head_Spine", "Bridge_Plate", "Speaker_Pod_L", "Base_LightRing"]
BED = 220.0  # common hobby bed (e.g. 220-256 mm); printer NOT selected


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
    for a, b in itertools.combinations(sorted(cats), 2):
        if not S(a).BoundBox.intersect(S(b).BoundBox):
            continue
        v = S(a).common(S(b)).Volume
        if v > TOL:
            fails.append(f"collision {a} x {b}: {v:.1f} mm3")
    for a, b in LOAD_PATH:
        d = S(a).distToShape(S(b))[0]
        if d > 0.01:
            fails.append(f"load path gap {a} -> {b}: {d:.2f} mm")
    report.append(f"load path: {len(LOAD_PATH)} contacts checked (head -> spine -> seat -> flange -> Al tube -> "
                  f"flange -> bridge -> posts -> floor)")
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
        if c == "exterior":
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
