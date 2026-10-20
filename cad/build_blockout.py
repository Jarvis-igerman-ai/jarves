# JARVIS CAD generator (S1.T01a blockout + S1.T01b layout/enclosure). ALL DIMENSIONS PROVISIONAL unless
# the note column says VERIFIED. Run: "C:\Users\97254\AppData\Local\Programs\FreeCAD 1.1\bin\freecadcmd.exe" cad\build_blockout.py
# Frame: origin = base centre, Z up, front = -Y, X = width. Helper objects are prefixed "_".
import os
import FreeCAD as App
from FreeCAD import Vector, Rotation

OUT = os.environ.get("JARVIS_CAD_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "JARVIS_blockout.FCStd")
doc = App.newDocument("JARVIS_blockout")

# Parameter list (mm). Edit here or in the "Params" spreadsheet.
params = [
    ("Height", 280, "overall height"),
    ("Width", 200, "overall width"),
    ("Depth", 160, "overall depth"),
    ("HeadD", 200, "head disc diameter"),
    ("BodyH", 79, "body height (head bottom is at Height-HeadD=80)"),
    ("ScreenD", 140, "screen opening diameter"),
    ("ScreenT", 3, "screen cover thickness"),
    ("SpkD", 50, "speaker grille plate diameter"),
    ("SpkT", 2, "speaker grille plate thickness"),
    ("DoorT", 3, "rear door thickness"),
    ("Wall", 3, "shell wall thickness"),
    ("SplitY", 40, "head front/back shell split plane (y)"),
    ("ScreenZoff", -8, "display centre below head centre (room for camera)"),
    ("DispD", 148, "round display module outer diameter (SKU not chosen)"),
    ("DispT", 12, "display module thickness"),
    ("CamW", 25, "Camera Module 3 Wide width: VERIFIED raspberrypi.com docs (~25x24x12.4)"),
    ("CamH", 24, "Camera Module 3 Wide height: VERIFIED raspberrypi.com docs"),
    ("CamD", 12.4, "Camera Module 3 Wide depth incl. lens: VERIFIED raspberrypi.com docs"),
    ("MicW", 108, "XVF3800 USB array width (retailer figure 108x108x18)"),
    ("MicL", 108, "XVF3800 USB array length (retailer figure)"),
    ("MicH", 18, "XVF3800 USB array height (retailer figure)"),
    ("MicTopZ", 258, "z of mic array top face"),
    ("MicY0", -50, "mic array front edge (y)"),
    ("PiW", 56, "Pi 5 board width (public spec 85x56, datasheet fetch failed)"),
    ("PiL", 85, "Pi 5 board length (public spec; confirm vs datasheet)"),
    ("PiH", 20, "Pi 5 height incl. active cooler"),
    ("PortClear", 25, "Pi port edge to rear wall inner face (cable plug room)"),
    ("TrayZ", 6, "Pi tray bottom z"),
    ("TrayT", 2, "Pi tray thickness"),
    ("Standoff", 4, "Pi standoff height (not modelled)"),
    ("SpkDrvD", 46, "speaker driver diameter"),
    ("SpkDrvL", 25, "speaker driver depth"),
]
sh = doc.addObject("Spreadsheet::Sheet", "Params")
for i, (name, val, note) in enumerate(params, start=1):
    sh.set(f"A{i}", name)
    sh.set(f"B{i}", f"={val} mm")  # leading "=" so negatives parse as quantities
    sh.set(f"C{i}", note if "VERIFIED" in note else note + " (PROVISIONAL)")
    sh.setAlias(f"B{i}", name)
doc.recompute()


def solid(kind, name, exprs, axis_y=False):
    o = doc.addObject(f"Part::{kind}", name)
    if axis_y:  # cylinder axis Z -> +Y, extends +Y from its base
        o.Placement.Rotation = Rotation(Vector(1, 0, 0), -90)
    for prop, e in exprs.items():
        o.setExpression(prop, e)
    return o


def cut(name, base, *tools):
    t = tools[0]
    if len(tools) > 1:
        t = doc.addObject("Part::MultiFuse", f"_{name}_tools")
        t.Shapes = list(tools)
    c = doc.addObject("Part::Cut", name)
    c.Base, c.Tool = base, t
    return c


P = "Params."
HC = f"({P}Height - {P}HeadD / 2)"                      # head centre z
SC = f"({HC} + {P}ScreenZoff)"                          # display centre z
CAMZ = f"({SC} + {P}DispD / 2 + 1 mm + {P}CamH / 2)"    # camera board centre z
FRONT = f"(-{P}Depth / 2)"
WALL_IN = f"(-{P}Depth / 2 + {P}ScreenT + {P}Wall)"     # head inner front face y
BODY_Y0 = f"(-{P}Depth / 2 + {P}SpkT)"                  # body front y
PI_Y0 = f"({P}Depth / 2 - {P}DoorT - {P}Wall - {P}PortClear - {P}PiL)"


def cyl_y(name, r, y0, h, z, x="0 mm"):
    return solid("Cylinder", name, {"Radius": r, "Height": h, "Placement.Base.x": x,
                                    "Placement.Base.y": y0, "Placement.Base.z": z}, axis_y=True)


def box(name, lx, ly, lz, x0, y0, z0):
    return solid("Box", name, {"Length": lx, "Width": ly, "Height": lz, "Placement.Base.x": x0,
                               "Placement.Base.y": y0, "Placement.Base.z": z0})


# --- S1.T01a envelope/silhouette solids (names preserved) ---
cyl_y("Head", f"{P}HeadD / 2", f"-{P}Depth / 2 + {P}ScreenT", f"{P}Depth - {P}ScreenT", HC)       # head envelope
box("Body", f"{P}Width", f"{P}Depth - {P}SpkT - {P}DoorT", f"{P}BodyH", f"-{P}Width / 2", BODY_Y0, "0 mm")  # body envelope
cyl_y("Screen", f"{P}ScreenD / 2 + 4 mm", FRONT, f"{P}ScreenT", SC)
cyl_y("Camera", "4 mm", FRONT, f"{P}ScreenT", CAMZ)
solid("Cylinder", "Mic_Top", {"Radius": "10 mm", "Height": "6 mm", "Placement.Base.z": f"{P}Height - 6 mm"})
for side, sx in (("L", -1), ("R", 1)):
    cyl_y(f"Speaker_{side}", f"{P}SpkD / 2", FRONT, f"{P}SpkT", f"{P}BodyH / 2", f"{sx} * {P}Width / 4")
box("Door_Rear", f"{P}Width * 0.6", f"{P}DoorT", f"{P}BodyH * 0.8", f"-{P}Width * 0.3",
    f"{P}Depth / 2 - {P}DoorT", f"{P}BodyH * 0.1")

# --- S1.T01b modular shells: Head_Front / Head_Back (split), Body_Shell, removable Door_Rear, Pi_Tray ---
hr = f"{P}HeadD / 2"
hy0 = f"-{P}Depth / 2 + {P}ScreenT"
void_h = cyl_y("_Head_Void", f"{hr} - {P}Wall", WALL_IN, f"{P}Depth - {P}ScreenT - 2 * {P}Wall", HC)
front_c = cyl_y("_HeadFront_outer", hr, hy0, f"{P}SplitY - ({hy0})", HC)
back_c = cyl_y("_HeadBack_outer", hr, f"{P}SplitY", f"{P}Depth / 2 - {P}SplitY", HC)
scr_hole = cyl_y("_ScreenHole", f"{P}ScreenD / 2", FRONT, "8 mm", SC)
cam_hole = cyl_y("_CameraHole", "5 mm", FRONT, "8 mm", CAMZ)
mic_hole = solid("Cylinder", "_MicHole", {"Radius": "10.5 mm", "Height": "6 mm",
                                          "Placement.Base.z": f"{P}Height - {P}Wall - 1 mm"})
cut("Head_Front", front_c, void_h, scr_hole, cam_hole, mic_hole)
cut("Head_Back", back_c, void_h)

body_void = box("_Body_Void", f"{P}Width - 2 * {P}Wall", f"{P}Depth - {P}SpkT - {P}DoorT - 2 * {P}Wall",
                f"{P}BodyH - 2 * {P}Wall", f"-({P}Width / 2 - {P}Wall)", f"{BODY_Y0} + {P}Wall", f"{P}Wall")
door_open = box("_DoorOpening", f"{P}Width * 0.6 - 6 mm", "5 mm", f"{P}BodyH * 0.8 - 6 mm",
                f"-({P}Width * 0.3 - 3 mm)", f"{P}Depth / 2 - {P}DoorT - {P}Wall - 1 mm", f"{P}BodyH * 0.1 + 3 mm")
spk_holes = [cyl_y(f"_SpkHole_{s}", f"({P}SpkD - 4 mm) / 2", FRONT, f"{P}SpkT + {P}Wall + 1 mm",
                   f"{P}BodyH / 2", f"{k} * {P}Width / 4") for s, k in (("L", -1), ("R", 1))]
cut("Body_Shell", doc.getObject("Body"), body_void, door_open, *spk_holes)

# --- S1.T01b component placeholders (envelopes only) ---
cyl_y("Display_Module", f"{P}DispD / 2", WALL_IN, f"{P}DispT", SC)
box("Camera_Module", f"{P}CamW", f"{P}CamD", f"{P}CamH", f"-{P}CamW / 2", WALL_IN, f"{CAMZ} - {P}CamH / 2")
box("Mic_Array", f"{P}MicW", f"{P}MicL", f"{P}MicH", f"-{P}MicW / 2", f"{P}MicY0", f"{P}MicTopZ - {P}MicH")
box("Pi_5", f"{P}PiW", f"{P}PiL", f"{P}PiH", f"-{P}PiW / 2", PI_Y0, f"{P}TrayZ + {P}TrayT + {P}Standoff")
box("Pi_Tray", f"{P}PiW + 14 mm", f"{P}PiL + 12 mm", f"{P}TrayT", f"-({P}PiW + 14 mm) / 2", f"{PI_Y0} - 6 mm", f"{P}TrayZ")
for side, sx in (("L", -1), ("R", 1)):
    cyl_y(f"Speaker_Driver_{side}", f"{P}SpkDrvD / 2", f"{BODY_Y0} + {P}Wall", f"{P}SpkDrvL", f"{P}BodyH / 2",
          f"{sx} * {P}Width / 4")
box("Reserve_USB_Hub", "50 mm", "45 mm", "20 mm", "-90 mm", "20 mm", "5 mm")  # spare volume placeholder

doc.recompute()
bad = [o.Name for o in doc.Objects if "Invalid" in o.State or not o.isValid()]
if bad:
    raise RuntimeError(f"recompute errors: {bad}")
doc.saveAs(OUT)
print("saved", OUT)
