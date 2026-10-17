# JARVIS S1.T01a - parametric blockout generator. ALL DIMENSIONS PROVISIONAL (UNVERIFIED).
# Run: "C:\Users\97254\AppData\Local\Programs\FreeCAD 1.1\bin\freecadcmd.exe" cad\build_blockout.py
# Frame: origin = base centre, Z up, front = -Y, X = width.
import os
import FreeCAD as App
from FreeCAD import Vector, Rotation

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "JARVIS_blockout.FCStd")

doc = App.newDocument("JARVIS_blockout")

# Parameter list (mm, PROVISIONAL). Edit here or in the "Params" spreadsheet.
params = [
    ("Height", 280, "overall height"),
    ("Width", 200, "overall width"),
    ("Depth", 160, "overall depth"),
    ("HeadD", 190, "head disc diameter"),
    ("BodyH", 110, "body height"),
    ("ScreenD", 150, "screen opening diameter"),
    ("ScreenT", 3, "screen thickness"),
    ("SpkD", 50, "speaker region diameter"),
    ("SpkT", 2, "speaker region thickness"),
    ("DoorT", 3, "rear door thickness"),
]
sh = doc.addObject("Spreadsheet::Sheet", "Params")
for i, (name, val, note) in enumerate(params, start=1):
    sh.set(f"A{i}", name)
    sh.set(f"B{i}", f"{val} mm")
    sh.set(f"C{i}", note + " (PROVISIONAL)")
    sh.setAlias(f"B{i}", name)
doc.recompute()


def solid(kind, name, props, exprs, axis_y=False):
    o = doc.addObject(f"Part::{kind}", name)
    for k, v in props.items():
        setattr(o, k, v)
    if axis_y:  # cylinder axis Z -> +Y
        o.Placement.Rotation = Rotation(Vector(1, 0, 0), -90)
    for prop, e in exprs.items():
        o.setExpression(prop, e)
    return o


P = "Params."
# Y-axis cylinders extend +Y from their base, so Base.y is the rear-most... front-most face.
solid("Cylinder", "Head", {}, {
    "Radius": f"{P}HeadD / 2", "Height": f"{P}Depth - {P}ScreenT",
    "Placement.Base.y": f"-{P}Depth / 2 + {P}ScreenT",
    "Placement.Base.z": f"{P}Height - {P}HeadD / 2"}, axis_y=True)
solid("Box", "Body", {}, {
    "Length": f"{P}Width", "Width": f"{P}Depth - {P}SpkT - {P}DoorT", "Height": f"{P}BodyH",
    "Placement.Base.x": f"-{P}Width / 2", "Placement.Base.y": f"-{P}Depth / 2 + {P}SpkT"})
solid("Cylinder", "Screen", {}, {
    "Radius": f"{P}ScreenD / 2", "Height": f"{P}ScreenT",
    "Placement.Base.y": f"-{P}Depth / 2",
    "Placement.Base.z": f"{P}Height - {P}HeadD / 2"}, axis_y=True)
solid("Cylinder", "Camera", {}, {  # above screen, centred
    "Radius": "4 mm", "Height": f"{P}ScreenT",
    "Placement.Base.y": f"-{P}Depth / 2",
    "Placement.Base.z": f"{P}Height - {P}HeadD / 2 + {P}ScreenD / 2 + 10 mm"}, axis_y=True)
solid("Cylinder", "Mic_Top", {}, {  # top mic region, vertical disc flush with head top
    "Radius": "10 mm", "Height": "6 mm",
    "Placement.Base.z": f"{P}Height - 6 mm"})
for side, sx in (("L", -1), ("R", 1)):
    solid("Cylinder", f"Speaker_{side}", {}, {
        "Radius": f"{P}SpkD / 2", "Height": f"{P}SpkT",
        "Placement.Base.x": f"{sx} * {P}Width / 4",
        "Placement.Base.y": f"-{P}Depth / 2",
        "Placement.Base.z": f"{P}BodyH / 2"}, axis_y=True)
solid("Box", "Door_Rear", {}, {  # removable access panel on the rear of the body
    "Length": f"{P}Width * 0.6", "Width": f"{P}DoorT", "Height": f"{P}BodyH * 0.6",
    "Placement.Base.x": f"-{P}Width * 0.3", "Placement.Base.y": f"{P}Depth / 2 - {P}DoorT",
    "Placement.Base.z": f"{P}BodyH * 0.2"})

doc.recompute()
doc.saveAs(OUT)
print("saved", OUT)
