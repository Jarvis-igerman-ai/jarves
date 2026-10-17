# JARVIS S1.T01b layout check (headless): containment, pairwise collisions, key clearances.
# Run: freecadcmd.exe cad\check_layout.py   (exit message PASS/FAIL; all numbers PROVISIONAL)
import os
import itertools
import FreeCAD as App

doc = App.openDocument(os.path.join(os.path.dirname(os.path.abspath(__file__)), "JARVIS_blockout.FCStd"))
S = lambda n: doc.getObject(n).Shape
TOL = 1e-3
fails = []

head_parts = ["Display_Module", "Camera_Module", "Mic_Array"]
body_parts = ["Pi_5", "Pi_Tray", "Speaker_Driver_L", "Speaker_Driver_R", "Reserve_USB_Hub"]
for n in head_parts:
    if S(n).cut(S("_Head_Void")).Volume > TOL:
        fails.append(f"{n} sticks out of head cavity")
for n in body_parts:
    if S(n).cut(S("_Body_Void")).Volume > TOL:
        fails.append(f"{n} sticks out of body cavity")

# Real parts only (Head/Body are envelope references, not parts).
parts = head_parts + body_parts + ["Head_Front", "Head_Back", "Body_Shell", "Door_Rear", "Screen", "Camera",
                                   "Mic_Top", "Speaker_L", "Speaker_R"]
for a, b in itertools.combinations(parts, 2):
    v = S(a).common(S(b)).Volume
    if v > TOL:
        fails.append(f"collision {a} x {b}: {v:.1f} mm3")

# Clearances (mm) and accessibility
pi, door_open = S("Pi_5").BoundBox, S("_DoorOpening").BoundBox
if not (door_open.XMin <= pi.XMin and pi.XMax <= door_open.XMax and door_open.ZMin <= pi.ZMin
        and pi.ZMax <= door_open.ZMax):
    fails.append("Pi port edge not inside rear door opening")
rear_gap = S("_Body_Void").BoundBox.YMax - pi.YMax
dist = lambda a, b: S(a).distToShape(S(b))[0]
print(f"Pi port edge -> rear wall: {rear_gap:.1f} (cable room, PROVISIONAL)")
print(f"Pi -> Body_Shell min gap:  {dist('Pi_5', 'Body_Shell'):.1f}")
print(f"Camera_Module -> Display:  {dist('Camera_Module', 'Display_Module'):.1f}")
print(f"Mic_Array -> Head_Front:   {dist('Mic_Array', 'Head_Front'):.1f}")
print(f"Driver L -> Pi_Tray:       {dist('Speaker_Driver_L', 'Pi_Tray'):.1f}")
print("RESULT:", "FAIL " + "; ".join(fails) if fails else "PASS (containment, collisions, door access)")
