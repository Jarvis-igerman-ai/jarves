# JARVIS S1.T01b layout check (headless): containment, pairwise collisions, key clearances.
# Run: freecadcmd.exe cad\check_layout.py  -> prints RESULT and exits non-zero on FAIL. All numbers PROVISIONAL.
import os
import sys
import itertools
import FreeCAD as App

TOL = 1e-3
HEAD_PARTS = ["Display_Module", "Camera_Module", "Mic_Array"]
BODY_PARTS = ["Pi_5", "Pi_Tray", "Speaker_Driver_L", "Speaker_Driver_R", "Reserve_USB_Hub"]
# Real parts only (Head/Body are envelope references, not parts).
PARTS = HEAD_PARTS + BODY_PARTS + ["Head_Front", "Head_Back", "Body_Shell", "Door_Rear", "Screen", "Camera",
                                   "Mic_Top", "Speaker_L", "Speaker_R"]


def check(doc):
    """Return (fails, report_lines) for an open JARVIS document."""
    S = lambda n: doc.getObject(n).Shape
    fails, report = [], []
    for n in PARTS + ["_Head_Void", "_Body_Void", "_DoorOpening"]:
        if doc.getObject(n) is None or S(n).isNull() or S(n).Volume <= TOL:
            fails.append(f"{n} missing or empty")
    if fails:
        return fails, report
    for n in HEAD_PARTS:
        if S(n).cut(S("_Head_Void")).Volume > TOL:
            fails.append(f"{n} sticks out of head cavity")
    for n in BODY_PARTS:
        if S(n).cut(S("_Body_Void")).Volume > TOL:
            fails.append(f"{n} sticks out of body cavity")
    for a, b in itertools.combinations(PARTS, 2):
        v = S(a).common(S(b)).Volume
        if v > TOL:
            fails.append(f"collision {a} x {b}: {v:.1f} mm3")
    pi, door = S("Pi_5").BoundBox, S("_DoorOpening").BoundBox
    if not (door.XMin <= pi.XMin and pi.XMax <= door.XMax and door.ZMin <= pi.ZMin and pi.ZMax <= door.ZMax):
        fails.append("Pi port edge not inside rear door opening")
    dist = lambda a, b: S(a).distToShape(S(b))[0]
    report += [f"Pi port edge -> rear wall: {S('_Body_Void').BoundBox.YMax - pi.YMax:.1f} (cable room, PROVISIONAL)",
               f"Pi -> Body_Shell min gap:  {dist('Pi_5', 'Body_Shell'):.1f}",
               f"Camera_Module -> Display:  {dist('Camera_Module', 'Display_Module'):.1f}",
               f"Mic_Array -> Head_Front:   {dist('Mic_Array', 'Head_Front'):.1f}",
               f"Driver L -> Pi_Tray:       {dist('Speaker_Driver_L', 'Pi_Tray'):.1f}"]
    return fails, report


if __name__ != "check_layout_lib":  # smoke_test.py execs this file with that name to reuse check()
    path = os.environ.get("JARVIS_CAD_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                            "JARVIS_blockout.FCStd")
    f, rep = check(App.openDocument(path))
    print("\n".join(rep))
    print("RESULT:", "FAIL " + "; ".join(f) if f else "PASS (containment, collisions, door access)")
    sys.stdout.flush()
    os._exit(1 if f else 0)
