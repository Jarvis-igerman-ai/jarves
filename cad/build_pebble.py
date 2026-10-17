# JARVES Pebble A (S1.T03R2, Issue #4) - parametric CAD source of truth for the visual redesign.
# ALL DIMENSIONS PROVISIONAL unless noted; strength, tolerances, tip safety UNVERIFIED (needs physical tests).
# Run: "C:\Users\97254\AppData\Local\Programs\FreeCAD 1.1\bin\freecadcmd.exe" cad\build_pebble.py
# Frame: origin = base centre on the table, Z up, front = -Y, X = width. Helper objects are prefixed "_".
# Shell geometry follows the "Params" spreadsheet (expressions); internal envelopes are placed from the same
# values at build time (re-run this script after changing Params to move them).
# Measured component data (manufacturer STEP, 2026-10-17, see docs/reviews/S1_T03R1_ENGINEERING_REVIEW.md):
#   5" round HDMI display Ø150 x 18.05 (panel 11.5, driver board 85.5 x 58); Camera Module 3 Wide 25 x 24 x 12.4.
import math
import os
import FreeCAD as App
from FreeCAD import Vector, Rotation

OUT = os.environ.get("JARVIS_PEBBLE_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                          "JARVIS_pebble.FCStd")
doc = App.newDocument("JARVIS_pebble")

P = dict(  # name: (value mm, note)
    BaseR=(105, "base radius (Ø210)"), BaseH=(85, "base height"), BaseRbTop=(25, "base top rounding"),
    BaseRbBot=(8, "base bottom rounding"), Wall=(3, "shell wall"), NeckGap=(17, "visible neck collar height"),
    FlangeT=(6, "neck flange thickness"), TubeR=(20, "aluminium neck tube outer radius (Ø40x2)"),
    HeadRX=(107.5, "head half-width"), HeadRY=(112, "head back radius"), HeadRZ=(97.5, "head half-height"),
    SeatH=(4, "flat head seat on the top flange"), FaceR=(96, "face facet radius (<= HeadRZ)"),
    CrownR=(40, "flat mic crown radius"), SplitY=(5, "head front/back split plane"),
)
sh = doc.addObject("Spreadsheet::Sheet", "Params")
for i, (k, (v, note)) in enumerate(P.items(), start=1):
    sh.set(f"A{i}", k)
    sh.set(f"B{i}", f"={v} mm")
    sh.set(f"C{i}", note + " (PROVISIONAL)")
    sh.setAlias(f"B{i}", k)
doc.recompute()
V = {k: v for k, (v, _) in P.items()}

# derived (Python mirror of the expressions below)
HC = V["BaseH"] + V["NeckGap"] + V["FlangeT"] + V["HeadRZ"] - V["SeatH"]          # head centre z
ZC = V["HeadRZ"] * math.sqrt(1 - (V["CrownR"] / V["HeadRX"]) ** 2)                 # crown plane above HC
F = V["HeadRY"] * math.sqrt(1 - (V["FaceR"] / V["HeadRZ"]) ** 2)                   # facet plane in front of HC
SEAT = HC - V["HeadRZ"] + V["SeatH"]                                               # head seat z
S = "Params."
E_HC = f"({S}BaseH + {S}NeckGap + {S}FlangeT + {S}HeadRZ - {S}SeatH)"
E_ZC = f"({S}HeadRZ * sqrt(1 - ({S}CrownR / {S}HeadRX) ^ 2))"
E_F = f"({S}HeadRY * sqrt(1 - ({S}FaceR / {S}HeadRZ) ^ 2))"
E_SEAT = f"({E_HC} - {S}HeadRZ + {S}SeatH)"
CATS = {}  # part name -> category (exterior / structure / component / reserved)


def obj(kind, name, exprs=None, props=None, rot=None, cat=None):
    o = doc.addObject(f"Part::{kind}", name)
    for k, v in (props or {}).items():
        setattr(o, k, v)
    if rot is not None:
        o.Placement.Rotation = rot
    for k, e in (exprs or {}).items():
        o.setExpression(k, e)
    if cat:
        CATS[name] = cat
    return o


def boolean(kind, name, base, tools, cat=None):
    t = tools[0]
    if len(tools) > 1:
        t = doc.addObject("Part::MultiFuse", f"_{name}_tools")
        t.Shapes = list(tools)
    o = doc.addObject(f"Part::{kind}", name)
    o.Base, o.Tool = base, t
    if cat:
        CATS[name] = cat
    return o


def multi(kind, name, shapes, cat=None):
    o = doc.addObject(f"Part::Multi{kind}", name)
    o.Shapes = list(shapes)
    if cat:
        CATS[name] = cat
    return o


def box(name, x0, y0, z0, lx, ly, lz, cat=None, rot=None):
    return obj("Box", name, props=dict(Length=lx, Width=ly, Height=lz,
                                       Placement=App.Placement(Vector(x0, y0, z0), rot or Rotation())), cat=cat)


def ebox(name, x0, y0, z0, lx, ly, lz):  # expression-driven box (all args are expression strings)
    return obj("Box", name, {"Length": lx, "Width": ly, "Height": lz, "Placement.Base.x": x0,
                             "Placement.Base.y": y0, "Placement.Base.z": z0})


def cyl(name, r, h, base, axis=Vector(0, 0, 1), cat=None):
    rot = Rotation(Vector(0, 0, 1), axis)
    return obj("Cylinder", name, props=dict(Radius=r, Height=h, Placement=App.Placement(base, rot)), cat=cat)


def ring(name, r_out, r_in, z0, h, cat=None):
    a = cyl(f"_{name}_o", r_out, h, Vector(0, 0, z0))
    b = cyl(f"_{name}_i", r_in, h + 2, Vector(0, 0, z0 - 1))
    return boolean("Cut", name, a, [b], cat)


# ---------------- head: ellipsoid shell, flat face facet, flat crown, flat seat ----------------
def ellipsoid(name, d):  # d = wall offset (0 outer, Wall inner)
    off = f" - {d}" if d else ""
    return obj("Ellipsoid", name, {"Radius1": f"{S}HeadRZ{off}", "Radius2": f"{S}HeadRX{off}",
                                   "Radius3": f"{S}HeadRY{off}", "Placement.Base.z": E_HC})


def clip(name, d):
    off = f" + {d}" if d else ""
    return ebox(name, "-300 mm", f"-{E_F}{off}", f"{E_SEAT}{off}", "600 mm", "600 mm",
                f"{E_HC} + {E_ZC} - ({E_SEAT}){' - 2 * ' + d if d else ''}")


outer = multi("Common", "_Head_Outer", [ellipsoid("_HeadE_o", None), clip("_HeadClip_o", None)])
inner = multi("Common", "_Head_Void", [ellipsoid("_HeadE_i", f"{S}Wall"), clip("_HeadClip_i", f"{S}Wall")])
face_open = cyl("_FaceOpening", V["FaceR"] - 4, 10, Vector(0, -F - 5, HC), Vector(0, 1, 0))
crown_open = cyl("_CrownOpening", V["CrownR"] - 3, 10, Vector(0, 0, HC + ZC - 6))
tube_hole_h = cyl("_HeadTubeHole", V["TubeR"] + 1, 10, Vector(0, 0, SEAT - 2))
# R4 mechanical details: 4x M3 seat holes (into top-flange inserts), 2 anti-rotation pin holes, seam bosses
SEAT_PTS = [(25 * math.cos(math.radians(a)), 25 * math.sin(math.radians(a))) for a in (45, 135, 225, 315)]
PIN_PTS = [(-26, 20), (26, 20)]
seat_holes = [cyl(f"_SeatHole_{i}", 1.7, 14, Vector(x, y, SEAT - 3)) for i, (x, y) in enumerate(SEAT_PTS)]
pin_holes = [cyl(f"_PinHole_{i}", 2.15, 10, Vector(x, y, SEAT - 1)) for i, (x, y) in enumerate(PIN_PTS)]
head_shell = boolean("Cut", "_Head_Shell", outer, [inner, face_open, crown_open, tube_hole_h] + seat_holes + pin_holes)
split_f = box("_SplitFront", -300, -300, 0, 600, 300 + V["SplitY"], 600)
split_b = box("_SplitBack", -300, V["SplitY"], 0, 600, 300, 600)
SY = V["SplitY"]
SEAM = [  # 4 boss pairs on the head seam: front = clearance + counterbore, back = heat-set insert
    (0.955 * (V["HeadRX"] - 3) * math.cos(math.radians(a)), HC + 0.955 * (V["HeadRZ"] - 3) * math.sin(math.radians(a)))
    for a in (25, 155, 205, 335)]
fb = [cyl(f"_SeamBossF_{i}", 5, 12, Vector(x, SY - 12, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(SEAM)]
fh = [cyl(f"_SeamHoleF_{i}", 1.7, 14, Vector(x, SY - 13, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(SEAM)]
fcb = [cyl(f"_SeamCbF_{i}", 3.2, 6, Vector(x, SY - 13, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(SEAM)]
bb = [cyl(f"_SeamBossB_{i}", 5, 12, Vector(x, SY, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(SEAM)]
bh = [cyl(f"_SeamInsB_{i}", 2.0, 9, Vector(x, SY - 1, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(SEAM)]
hf_raw = multi("Common", "_HeadFront_raw", [head_shell, split_f])
hb_raw = multi("Common", "_HeadBack_raw", [head_shell, split_b])
hf_b = multi("Fuse", "_HeadFront_bossed", [hf_raw] + fb)
hb_b = multi("Fuse", "_HeadBack_bossed", [hb_raw] + bb)
boolean("Cut", "Head_Front", hf_b, fh + fcb, "exterior")         # face bezel + front half (print face-down)
boolean("Cut", "Head_Back", hb_b, bh, "exterior")                # rear dome (print open side down)
fc = cyl("_FaceCoverDisc", V["FaceR"] - 4, V["Wall"], Vector(0, -F, HC), Vector(0, 1, 0))
fclip = box("_FaceCoverClip", -300, -300, SEAT + V["Wall"], 600, 600, HC + ZC - V["Wall"] - SEAT - V["Wall"])
multi("Common", "Face_Cover", [fc, fclip], "exterior")             # tinted acrylic cover lens (PROVISIONAL)
mic_cap = cyl("_MicCapDisc", V["CrownR"] - 3, V["Wall"], Vector(0, 0, HC + ZC - V["Wall"]))
ports = [cyl(f"_MicPort_{i}", 0.8, 10, Vector(20 * math.cos(a), 20 * math.sin(a), HC + ZC - 5))
         for i, a in enumerate([k * math.pi / 2 + math.pi / 4 for k in range(4)])]
boolean("Cut", "Mic_Cap", mic_cap, ports, "exterior")               # port pattern PROVISIONAL (align to chosen board)

# ---------------- neck / load path ----------------
ring("Neck_Collar", 32, V["TubeR"] + 0.5, V["BaseH"], V["NeckGap"], "exterior")       # cosmetic satin sleeve
FLAT_X = V["TubeR"] - 1                         # 1 mm anti-rotation flat filed on the tube (+x side)


def keyed_flange(name, r_out, z0, holes, extra=()):
    rng = ring(f"_{name}_ring", r_out, V["TubeR"], z0, V["FlangeT"])
    key = multi("Common", f"_{name}_key", [cyl(f"_{name}_keyc", V["TubeR"], V["FlangeT"], Vector(0, 0, z0)),
                                           box(f"_{name}_keyb", FLAT_X, -50, z0 - 1, 50, 100, V["FlangeT"] + 2)])
    body = multi("Fuse", f"_{name}_body", [rng, key] + list(extra))
    return boolean("Cut", name, body, holes, "structure")


saddle_c = cyl("_Saddle_c", 45, 12, Vector(0, 0, SEAT))
saddle = boolean("Cut", "_Saddle", saddle_c,
                 [doc.getObject("_HeadE_o"), cyl("_Saddle_bore", V["TubeR"], 14, Vector(0, 0, SEAT - 1))])
pins = [cyl(f"_Pin_{i}", 2.0, 5, Vector(x, y, SEAT)) for i, (x, y) in enumerate(PIN_PTS)]
ins_hi = [cyl(f"_FlangeHiIns_{i}", 2.0, 8, Vector(x, y, SEAT - V["FlangeT"] - 1)) for i, (x, y) in enumerate(SEAT_PTS)]
keyed_flange("Neck_Flange_High", 45, SEAT - V["FlangeT"], ins_hi, [saddle] + pins)   # saddle follows the head underside
LOW_PTS = [(28 * math.cos(math.radians(a)), 28 * math.sin(math.radians(a))) for a in (45, 135, 225, 315)]
keyed_flange("Neck_Flange_Low", 35, 60,
             [cyl(f"_FlangeLoHole_{i}", 1.7, 8, Vector(x, y, 59)) for i, (x, y) in enumerate(LOW_PTS)])
tube_ring = ring("_Tube_ring", V["TubeR"], V["TubeR"] - 2, 60, SEAT - 60)
boolean("Cut", "Neck_Tube", tube_ring, [box("_Tube_flat", FLAT_X, -50, 50, 50, 100, 100)], "structure")  # Al 6061 Ø40x2
box("Head_Spine", -30, 27, HC - 80, 60, 6, 130, "structure")                       # carries display/camera/mic mounts
box("Head_Spine_Foot", -12, 22, SEAT + V["Wall"], 24, 6, HC - 80 - SEAT - V["Wall"], "structure")
POSTS = {"FL": (-68, -28), "FR": (68, -28), "RL": (-68, 55), "RR": (68, 55)}
bridge_raw = box("_Bridge_raw", -70, -35, 56, 140, 100, 4)
boolean("Cut", "Bridge_Plate", bridge_raw,   # M3 through-holes: 4 to standoffs, 4 for the lower flange (bolt + nut)
        [cyl(f"_BridgeHole_{k}", 1.7, 8, Vector(x, y, 55)) for k, (x, y) in POSTS.items()] +
        [cyl(f"_BridgeFl_{i}", 1.7, 8, Vector(x, y, 55)) for i, (x, y) in enumerate(LOW_PTS)], "structure")
for nm, (x, y) in POSTS.items():               # purchased M3 hex standoffs (female-female), screws top and bottom
    cyl(f"Bridge_Post_{nm}", 3.2, 52, Vector(x, y, 4), cat="structure")


# ---------------- base: rounded puck shell, floor, rear door, band, light ring ----------------
def puck(name, r, h, rb_bot, rb_top, z0=0.0):
    parts = [cyl(f"_{name}_mid", r, h - rb_bot - rb_top, Vector(0, 0, z0 + rb_bot)),
             cyl(f"_{name}_bot", r - rb_bot, rb_bot, Vector(0, 0, z0)),
             cyl(f"_{name}_top", r - rb_top, rb_top, Vector(0, 0, z0 + h - rb_top)),
             obj("Torus", f"_{name}_tb", props=dict(Radius1=r - rb_bot, Radius2=rb_bot,
                                                    Placement=App.Placement(Vector(0, 0, z0 + rb_bot), Rotation()))),
             obj("Torus", f"_{name}_tt", props=dict(Radius1=r - rb_top, Radius2=rb_top,
                                                    Placement=App.Placement(Vector(0, 0, z0 + h - rb_top), Rotation())))]
    return multi("Fuse", name, parts)


W = V["Wall"]
b_out = puck("_Base_Outer", V["BaseR"], V["BaseH"], V["BaseRbBot"], V["BaseRbTop"])
b_in = puck("_Base_Void", V["BaseR"] - W, V["BaseH"] - 2 * W, V["BaseRbBot"] - W + 1, V["BaseRbTop"] - W, W)
floor_box = box("_FloorBox", -300, -300, -1, 600, 600, 1 + 4)
tube_hole_b = cyl("_BaseTubeHole", V["TubeR"] + 1, 10, Vector(0, 0, V["BaseH"] - 6))
SPK = [(-1, "L"), (1, "R")]
spk_holes = []
SPK_Z = 34.0                                   # speaker centre height (band 18..62)
for sgn, s in SPK:                             # 9 x 5 grille of Ø3.5 holes per speaker, hidden behind the fabric
    th = math.radians(-90 + sgn * 35)
    d = Vector(math.cos(th), math.sin(th), 0)
    tg = Vector(-d.y, d.x, 0)
    for i in range(9):
        for j in range(5):
            c = d * 96 + tg * (-24 + 6 * i)
            spk_holes.append(cyl(f"_SpkHole_{s}_{i}_{j}", 1.75, 12, Vector(c.x, c.y, SPK_Z - 13 + 6.5 * j), d))
# --- removable LED status ring on the upper base shoulder (groove z 74..80, above the rear door)
LED_Z0, LED_Z1 = 74.0, 80.0
backer = ring("_LED_Backer", 96, 84, 72, 10)                      # local wall thickening behind the groove
led_groove = ring("_LED_Groove", 112, 90, LED_Z0, LED_Z1 - LED_Z0)
led_wire = cyl("_LED_WireHole", 2, 14, Vector(0, 80, 77), Vector(0, 1, 0))
door_box = box("_DoorBox", -55, 80, 12, 110, 40, 60)
shell_body = boolean("Cut", "_Base_ShellBody", b_out, [b_in, floor_box, tube_hole_b] + spk_holes)
FLOOR_BOSS = [(96.5 * math.cos(math.radians(a)), 96.5 * math.sin(math.radians(a))) for a in (15, 165, 270)]
fbosses = [cyl(f"_FloorBoss_{i}", 5, 12, Vector(x, y, 4)) for i, (x, y) in enumerate(FLOOR_BOSS)]
fins = [cyl(f"_FloorIns_{i}", 2.0, 10, Vector(x, y, 3)) for i, (x, y) in enumerate(FLOOR_BOSS)]
shell_bk0 = multi("Fuse", "_Base_ShellBacked0", [shell_body, backer] + fbosses)
shell_bk = boolean("Cut", "_Base_ShellBacked", shell_bk0, fins)
shell_raw = boolean("Cut", "_Base_ShellRaw", shell_bk, [led_groove, led_wire])
boolean("Cut", "Base_Shell", shell_raw, [door_box], "exterior")
door_raw = multi("Common", "_Door_Raw", [shell_raw, door_box])
vents = [box(f"_Vent_{i}", -30, 80, 22 + i * 7, 60, 40, 3) for i in range(5)]
usbc = box("_USBC_Hole", 33, 80, 18, 10, 40, 4)
boolean("Cut", "Base_Door", door_raw, vents + [usbc], "exterior")   # 2 captive M3 thumb screws (not modelled)
floor_raw = multi("Common", "_Floor_raw", [b_out, floor_box])
floor_holes = ([cyl(f"_FloorHole_{i}", 1.7, 8, Vector(x, y, -2)) for i, (x, y) in enumerate(FLOOR_BOSS)] +
               [cyl(f"_FloorPost_{k}", 1.7, 8, Vector(x, y, -2)) for k, (x, y) in POSTS.items()] +
               [cyl(f"_FloorPi_{i}", 1.4, 8, Vector(x, y, -2)) for i, (x, y) in
                enumerate([(-24.5, -8.5), (24.5, -8.5), (-24.5, 49.5), (24.5, 49.5)])])
boolean("Cut", "Base_Floor", floor_raw, floor_holes, "structure")  # carries standoffs, Pi, pods; screws from underside


def led_half(name, side, cat):                 # diffuser / blank cover half (front -y or back +y), flush with shoulder
    cone = obj("Cone", f"_{name}_cone", props=dict(Radius1=100.4, Radius2=95.2, Height=LED_Z1 - LED_Z0 - 0.4,
                                                   Placement=App.Placement(Vector(0, 0, LED_Z0 + 0.2), Rotation())))
    bore = cyl(f"_{name}_bore", 91.4, 10, Vector(0, 0, LED_Z0 - 2))
    rng = boolean("Cut", f"_{name}_ring", cone, [bore])
    half = box(f"_{name}_half", -150, -150.2 if side < 0 else 0.2, 0, 300, 150, 200)
    return multi("Common", name, [rng, half], cat)


led_half("LED_Diffuser_Front", -1, "exterior")                    # translucent PETG, snap-in from outside
led_half("LED_Diffuser_Back", 1, "exterior")
led_half("LED_Blank_Front", -1, "alternate")                      # opaque printed blank cover (same groove)
led_half("LED_Blank_Back", 1, "alternate")
ring("LED_Strip", 91.2, 90.0, LED_Z0 + 1, 3.5, "component")        # addressable LED strip placeholder (no SKU)
box("LED_Connector", 26, 82, 60, 15, 8, 8, "component")           # detachable 3-pin connector, reachable via rear door
band_raw = ring("_Band_Ring", V["BaseR"] + 1.5, V["BaseR"], 18, 44)
band_gap = box("_BandDoorGap", -62, 0, 0, 124, 200, 100)          # band stops either side of the rear door
boolean("Cut", "Base_Band", band_raw, [band_gap], "exterior")     # fabric speaker band (frame not modelled)

# ---------------- component envelopes (high-level, PROVISIONAL; not a SKU lock) ----------------
y0 = -F + W                                    # behind the face cover
DZ = -14.0                                     # display centre below head centre (clears seat + camera/crown)
cyl("Display_Panel", 75, 11.5, Vector(0, y0, HC + DZ), Vector(0, 1, 0), "component")
box("Display_Driver", -44.5, y0 + 11.5, HC + DZ - 30, 85.5, 18.05 - 11.5, 58, "component")
box("Keepout_Display_Cable", -44.5, y0 + 18.05, HC + DZ - 30, 85.5, 25, 58, "reserved")
box("Camera_Module", -12.5, y0, HC + DZ + 76, 25, 12.4, 24, "component")
box("Mic_Board", -43, -3, HC + 79 - 12, 86, 40, 12, "component")              # 2-mic-class board, top 79 mm above HC
box("Pi_5", -28, -12, 10, 56, 85, 21, "component")                            # 85x56 + Active Cooler (height PROVISIONAL)
for nm, (x, y) in {"1": (-24.5, -8.5), "2": (24.5, -8.5), "3": (-24.5, 49.5), "4": (24.5, 49.5)}.items():
    cyl(f"Pi_Standoff_{nm}", 2.5, 6, Vector(x, y, 4), cat="structure")
box("Reserve_M2_HAT", -28, -12, 31, 56, 85, 21, "reserved")                  # official M.2 HAT+ stack height
box("Reserve_SSD_USB", -62, -2, 4, 28, 75, 12, "reserved")                   # M.2-in-USB enclosure class
for sgn, s in SPK:
    th = math.radians(-90 + sgn * 35)
    rot = Rotation(Vector(0, 0, 1), math.degrees(th) - 90)   # local +y = radial outward
    ux, uy = math.cos(th), math.sin(th)                       # radial unit
    px, py = -uy, ux                                          # tangential unit
    # sealed pod: 60 wide (tangential) x 32 deep (radial 54..86) x 42 high; local +x maps to -tangential
    org = Vector(ux * 54 + px * 30, uy * 54 + py * 30, 13)
    box(f"Speaker_Pod_{s}", org.x, org.y, org.z, 60, 32, 42, "component", rot)
    obj("Cone", f"Speaker_Driver_{s}", props=dict(Radius1=12, Radius2=24, Height=10, Placement=App.Placement(
        Vector(ux * 86, uy * 86, SPK_Z), Rotation(Vector(0, 0, 1), Vector(ux, uy, 0)))), cat="component")

doc.recompute()
bad = [o.Name for o in doc.Objects if "Invalid" in o.State or not o.isValid()]
if bad:
    raise RuntimeError(f"recompute errors: {bad}")
# ---------------- assembly metadata: kind, step, explode offset (mm, CAD frame), print / fit note ----------------
# kind: printed | purchased (stock part, e.g. tube, fabric, acrylic, standoffs) | placeholder (electronics, no SKU)
#       | reserved (empty volume) | alternate (swap-in part, not installed with its counterpart)
H = (0, 0, 200)                                # whole head lifts this much in the exploded view
META = {
    "Base_Floor": ("printed", 1, (0, 0, -90), "print flat, bottom down; rubber feet + ballast pocket (feet not modelled)"),
    "Bridge_Post_FL": ("purchased", 2, (0, 0, -50), "M3 hex standoff F-F ~50-52 mm (brass/alu); M3 screw each end"),
    "Pi_Standoff_1": ("purchased", 2, (0, 0, -60), "M2.5 brass standoff 6 mm"),
    "Pi_5": ("placeholder", 3, (0, 0, -35), "Raspberry Pi 5 + Active Cooler envelope; 4x M2.5x6"),
    "Reserve_SSD_USB": ("reserved", 3, (-40, 0, -35), "RESERVED: M.2-in-USB enclosure (not installed)"),
    "Reserve_M2_HAT": ("reserved", 3, (0, 0, -15), "RESERVED: official M.2 HAT+ stack height (not installed)"),
    "Speaker_Pod_L": ("printed", 4, (-45, -55, -30), "sealed pod; print open side up; 2x M3 to floor"),
    "Speaker_Driver_L": ("placeholder", 4, (-60, -75, -30), "full-range driver ~Ø50 envelope (no SKU)"),
    "LED_Connector": ("placeholder", 5, (0, 60, -10), "detachable 3-pin LED connector, behind rear door"),
    "Bridge_Plate": ("printed", 6, (0, 0, 0), "print flat; 4x M3 to standoffs + 4x M3 bolt/nut to lower flange"),
    "Neck_Flange_Low": ("printed", 6, (0, 0, 20), "print flat; keyed bore fits tube flat; 4x M3 bolt + nut through bridge"),
    "Neck_Tube": ("purchased", 6, (0, 0, 45), "Al tube Ø40x2 cut to ~48 mm; file 1 mm flat full length (anti-rotation)"),
    "Base_Shell": ("printed", 7, (0, 0, 70), "print top-down; 3 floor bosses with M3 inserts; split F/B for 250x210 beds"),
    "Base_Band": ("purchased", 7, (0, 0, 70), "fabric wrap over the grille, ends at the rear door"),
    "Base_Door": ("printed", 7, (0, 80, 70), "print flat (outer face down); 2 captive M3 thumb screws + hooks"),
    "LED_Strip": ("placeholder", 8, (0, 0, 95), "addressable LED strip in groove (no SKU; 5 V, signal level UNVERIFIED)"),
    "LED_Diffuser_Front": ("printed", 8, (0, -55, 105), "translucent PETG half ring; snap-in from outside (UNVERIFIED)"),
    "LED_Diffuser_Back": ("printed", 8, (0, 55, 105), "translucent PETG half ring; snap-in from outside (UNVERIFIED)"),
    "LED_Blank_Front": ("alternate", 8, (0, -55, 105), "opaque blank cover half - replaces diffuser if LED removed"),
    "LED_Blank_Back": ("alternate", 8, (0, 55, 105), "opaque blank cover half - replaces diffuser if LED removed"),
    "Neck_Collar": ("printed", 9, (0, 0, 120), "cosmetic sleeve, slides over tube"),
    "Neck_Flange_High": ("printed", 9, (0, 0, 145), "print flat (saddle up); keyed bore, 2 pins, 4 M3 heat-set inserts"),
    "Head_Back": ("printed", 10, (0, 70, 0), "rear dome; print open side down; 4 seam inserts; 2x M3 into flange"),
    "Head_Spine_Foot": ("printed", 11, (0, 15, 0), "2x M3 through head floor"),
    "Head_Spine": ("printed", 11, (0, 15, 0), "print flat; carries display / camera / mic mounts"),
    "Display_Panel": ("placeholder", 12, (0, -110, 0), "5in round display envelope (measured STEP; no SKU lock)"),
    "Display_Driver": ("placeholder", 12, (0, -95, 0), "display driver board envelope"),
    "Keepout_Display_Cable": ("reserved", 12, (0, -80, 0), "RESERVED: HDMI plug + bend room"),
    "Camera_Module": ("placeholder", 12, (0, -110, 15), "Camera Module 3 Wide envelope; CSI ribbon length UNVERIFIED"),
    "Mic_Board": ("placeholder", 12, (0, 0, 60), "2-mic-class board envelope under crown (no SKU)"),
    "Head_Front": ("printed", 13, (0, -95, 0), "face bezel; print face-down; 4x M3x12 into rear-dome inserts; 2x M3 into flange"),
    "Face_Cover": ("purchased", 13, (0, -160, 0), "laser-cut tinted acrylic 3 mm (camera window UNVERIFIED)"),
    "Mic_Cap": ("printed", 14, (0, 0, 110), "print flat; port holes aligned to chosen mic board"),
}
for side in ("R",):                            # mirror left-hand entries
    META["Speaker_Pod_R"] = ("printed", 4, (45, -55, -30), META["Speaker_Pod_L"][3])
    META["Speaker_Driver_R"] = ("placeholder", 4, (60, -75, -30), META["Speaker_Driver_L"][3])
for nm in ("FR", "RL", "RR"):
    META[f"Bridge_Post_{nm}"] = META["Bridge_Post_FL"]
for k in ("2", "3", "4"):
    META[f"Pi_Standoff_{k}"] = META["Pi_Standoff_1"]
# print orientation for exported STL: rotation (axis, degrees) that puts the best face on the bed
PRINT_ROT = {"Head_Front": ("X", 90), "Head_Back": ("X", 90), "Base_Shell": ("X", 180), "Base_Door": ("X", -90),
             "Head_Spine": ("X", 90), "Head_Spine_Foot": ("X", 90)}
PREFIX = {"printed": "P", "purchased": "B", "placeholder": "E", "reserved": "R", "alternate": "A"}
missing = sorted(set(CATS) - set(META))
if missing:
    raise RuntimeError(f"parts without assembly metadata: {missing}")
PID, counters = {}, {}
for k in sorted(CATS, key=lambda n: (META[n][1], n)):          # part numbers follow the assembly order
    pre = PREFIX[META[k][0]]
    counters[pre] = counters.get(pre, 0) + 1
    PID[k] = f"{pre}{counters[pre]:02d}"
sh.set("E1", "categories")
for i, (k, c) in enumerate(sorted(CATS.items()), start=2):  # stored for export / downstream checks
    kind, step, ex, note = META[k]
    sh.set(f"K{i}", PID[k])
    rot = PRINT_ROT.get(k, ("Z", 0))
    sh.set(f"L{i}", f"'{rot[0]},{rot[1]}")
    if step >= 10:                             # head sub-assembly rides on the head lift
        ex = tuple(a + b for a, b in zip(ex, H))
    sh.set(f"E{i}", k)
    sh.set(f"F{i}", c)
    sh.set(f"G{i}", kind)
    sh.set(f"H{i}", str(step))
    sh.set(f"I{i}", "'" + ",".join(f"{v:g}" for v in ex))
    sh.set(f"J{i}", "'" + note)
doc.recompute()
doc.saveAs(OUT)
print("saved", OUT, "parts", len(CATS))
