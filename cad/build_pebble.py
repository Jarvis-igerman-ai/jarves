# JARVES Pebble A — R5b "structural base cover" (S1.T03R5, Issue #4). Parametric CAD source of truth.
# ALL DIMENSIONS PROVISIONAL unless noted; strength, tolerances, tip safety UNVERIFIED (needs physical tests).
# Run: "C:\Users\97254\AppData\Local\Programs\FreeCAD 1.1\bin\freecadcmd.exe" cad\build_pebble.py
# Frame: origin = base centre on the table, Z up, front = -Y, X = width. Helper objects are prefixed "_".
# Architecture (docs/reviews/S1_T03R5_ARCHITECTURE_REVIEW.md): Pi 5 in the base on the tray; display, camera and
# 2-mic board in the head; a printed neck column bolted under a ribbed 6 mm base-cover top; no separate chassis.
# Load path: head body floor -> 3 M3 -> neck column -> flange -> 3 M3 into cover-top bosses -> ribbed top -> cover
# wall -> 4 M3 -> tray -> feet.  Captive M3 nuts in printed pockets; M2.5/M3 self-tapping only for rarely opened parts.
# Measured component data (manufacturer files, 2026-10-17): 5" round HDMI display Ø150 x 18.05 (panel 11.5, driver
# board 85.5 x 58); Camera Module 3 Wide ~25 x 24 x 12.4; ReSpeaker Lite 35 x 86 mm (Seeed wiki); Seeed mono
# enclosed speaker 4R 5W 50 x 45 x 22 mm (retailer listings). Mounting holes of mic board/camera: PROVISIONAL.
import math
import os
import FreeCAD as App
from FreeCAD import Vector, Rotation

OUT = os.environ.get("JARVIS_PEBBLE_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                          "JARVIS_pebble.FCStd")
doc = App.newDocument("JARVIS_pebble")

P = dict(  # name: (value mm, note)
    BaseR=(105, "base radius (Ø210)"), BaseH=(85, "base height"), BaseRbTop=(25, "base top rounding"),
    BaseRbBot=(8, "base bottom rounding"), Wall=(3, "shell wall"), NeckGap=(17, "visible neck height"),
    FlangeT=(6, "head seat rise above neck top"), TubeR=(22, "neck cable bore radius (Ø44)"),
    HeadRX=(107.5, "head half-width"), HeadRY=(112, "head back radius"), HeadRZ=(97.5, "head half-height"),
    SeatH=(4, "flat head seat on the neck top"), FaceR=(96, "face facet radius (<= HeadRZ)"),
    CrownR=(40, "flat mic crown radius"), SplitY=(50, "head body / service lid split plane"),
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
HC = V["BaseH"] + V["NeckGap"] + V["FlangeT"] + V["HeadRZ"] - V["SeatH"]          # head centre z (201.5)
ZC = V["HeadRZ"] * math.sqrt(1 - (V["CrownR"] / V["HeadRX"]) ** 2)                 # crown plane above HC
F = V["HeadRY"] * math.sqrt(1 - (V["FaceR"] / V["HeadRZ"]) ** 2)                   # facet plane in front of HC
SEAT = HC - V["HeadRZ"] + V["SeatH"]                                               # head seat z (108)
S = "Params."
E_HC = f"({S}BaseH + {S}NeckGap + {S}FlangeT + {S}HeadRZ - {S}SeatH)"
E_ZC = f"({S}HeadRZ * sqrt(1 - ({S}CrownR / {S}HeadRX) ^ 2))"
E_F = f"({S}HeadRY * sqrt(1 - ({S}FaceR / {S}HeadRZ) ^ 2))"
E_SEAT = f"({E_HC} - {S}HeadRZ + {S}SeatH)"
CATS = {}  # part name -> category (exterior / structure / component / reserved / alternate)
W = V["Wall"]
DZ = -14.0                     # display centre below head centre (clears seat + camera/crown)
Y0 = -F + W                    # display glass plane (behind the 3 mm face wall)
NUT_R = 3.25                   # M3 nut pocket (5.5 AF hex modelled as Ø6.5 round pocket)
NUT_T = 2.6


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


def polar(r, deg):
    return r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg))


# =====================================================================================================================
# HEAD: ellipsoid shell, flat face facet, flat crown, flat seat. Split at y=+50: head BODY (face, crown, seat, display
# and mic mounts in one piece) + service LID (2 hooks + 2 screws).
# =====================================================================================================================
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
SEAT_PTS = [polar(33, a) for a in (7, 90, 173)]            # 3 head->neck screws on the saddle flare (y >= 4: clear
                                                           # vertical driver path behind the display driver board)
# seat reinforcement: C-ring r23..40, 6 mm, only behind the display + retainer (y >= 0)
seat_ring_full = ring("_SeatRing_full", 38, 23, SEAT + W, 7)      # stays inside the curved shell
seat_ring = multi("Common", "_SeatRing", [seat_ring_full, box("_SeatRing_clip", -60, 0, SEAT, 120, 60, 20)])
head_solid = multi("Fuse", "_Head_Solid", [boolean("Cut", "_Head_ShellRaw", outer, [inner]), seat_ring])
face_open = cyl("_FaceOpening", 66, 10, Vector(0, -F - 5, HC + DZ), Vector(0, 1, 0))   # shows display active area
cam_win = cyl("_CameraWindow", 4, 10, Vector(0, -F - 5, HC + DZ + 88), Vector(0, 1, 0))
cable_hole = cyl("_HeadCableHole", V["TubeR"] + 1, 14, Vector(0, 0, SEAT - 3))
seat_holes = [cyl(f"_SeatHole_{i}", 1.7, 16, Vector(x, y, SEAT - 3)) for i, (x, y) in enumerate(SEAT_PTS)]
MIC_Y0, MIC_TOP = -3.5, HC + 81.0                            # mic board (2-mic class) under the crown
MIC_PORTS = [(-25, MIC_Y0 + 17.5), (25, MIC_Y0 + 17.5)]      # PROVISIONAL: align to the chosen board's mics
mic_ports = [cyl(f"_MicPort_{i}", 0.8, 14, Vector(x, y, HC + ZC - 8)) for i, (x, y) in enumerate(MIC_PORTS)]
vents = [box(f"_LidVent_{i}", -32, 60, HC - 46 + i * 9, 64, 80, 3.5) for i in range(4)]   # rear lid vents
head_cut = boolean("Cut", "_Head_Shell", head_solid, [face_open, cam_win, cable_hole] + seat_holes + mic_ports)

SY = V["SplitY"]
s_y = math.sqrt(1 - (SY / (V["HeadRY"] - W)) ** 2)          # inner ellipse scale at the split plane
seam = lambda a: (0.94 * (V["HeadRX"] - W) * s_y * math.cos(math.radians(a)),
                  HC + 0.94 * (V["HeadRZ"] - W) * s_y * math.sin(math.radians(a)))
LID_SCREWS = [seam(a) for a in (60, 120)]
LID_HOOKS = [seam(a) for a in (250, 290)]
# head body: bosses at the seam with captive-nut pockets (nut pushed in from inside the head)
bbody = [cyl(f"_SeamBossBody_{i}", 5, 12, Vector(x, SY - 12, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(LID_SCREWS)]
nbody = [cyl(f"_SeamNut_{i}", NUT_R, NUT_T + 1, Vector(x, SY - 13, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(LID_SCREWS)]
hbody = [cyl(f"_SeamHoleBody_{i}", 1.7, 14, Vector(x, SY - 13, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(LID_SCREWS)]
# hook ledges in the head body for the lid's bottom hooks
ledges = [box(f"_HookLedge_{i}", x - 7, SY - 8, z - 6, 14, 8, 3) for i, (x, z) in enumerate(LID_HOOKS)]
# display retainer bosses (4) on the inside of the face wall, M3 self-tapping
RET_ANG = (20, 160, 200, 340)
RET_PTS = [(82 * math.cos(math.radians(a)), HC + DZ + 82 * math.sin(math.radians(a))) for a in RET_ANG]
rbosses = [cyl(f"_RetBoss_{i}", 4.5, 11.5, Vector(x, Y0, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(RET_PTS)]
rholes = [cyl(f"_RetHole_{i}", 1.25, 10, Vector(x, Y0 + 2, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(RET_PTS)]
# mic board bosses (2) hanging from the crown, M2.5 self-tapping (positions PROVISIONAL)
mbosses = [cyl(f"_MicBoss_{i}", 3, ZC - W - (MIC_TOP - HC) + 0.5, Vector(x, MIC_Y0 + 17.5, MIC_TOP), cat=None)
           for i, x in enumerate((-30, 30))]
split_f = box("_SplitFront", -300, -300, 0, 600, 300 + SY, 600)
split_b = box("_SplitBack", -300, SY, 0, 600, 300, 600)
hf_raw = multi("Common", "_HeadBody_raw", [head_cut, split_f])
hf_b = multi("Fuse", "_HeadBody_bossed", [hf_raw] + bbody + ledges + rbosses + mbosses)
hf = boolean("Cut", "_HeadBody", hf_b, nbody + hbody + rholes)
face_zone_box = box("_FaceZoneBox", -300, -F - 1, 0, 600, W + 1.01, 600)
multi("Common", "Head_Face_Zone", [hf, face_zone_box], "exterior")    # dark colour zone of the head body print
boolean("Cut", "Head_Front", hf, [face_zone_box], "exterior")         # head body (white zone)
# service lid: clearance bosses + counterbored screw holes from outside, 2 hooks under the body ledges, vents
hb_raw = multi("Common", "_Lid_raw", [head_cut, split_b])
blid = [cyl(f"_SeamBossLid_{i}", 5, 10, Vector(x, SY, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(LID_SCREWS)]
hooks = [box(f"_LidHook_{i}", x - 6, SY - 7, z - 3, 12, 7.5, 3) for i, (x, z) in enumerate(LID_HOOKS)]
hooks_up = [box(f"_LidHookArm_{i}", x - 6, SY - 0.5, z - 3, 12, 3, 8) for i, (x, z) in enumerate(LID_HOOKS)]
lid_b = multi("Fuse", "_Lid_bossed", [hb_raw] + blid + hooks + hooks_up)
lid_holes = ([cyl(f"_LidHole_{i}", 1.7, 80, Vector(x, SY - 1, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(LID_SCREWS)]
             + [cyl(f"_LidCb_{i}", 3.2, 80, Vector(x, SY + 10, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(LID_SCREWS)])
boolean("Cut", "Head_Back", lid_b, lid_holes + vents, "exterior")
# display retainer ring (printed): presses the display rim against the face wall; 4 tabs to the bosses
ret_ring = obj("Cylinder", "_Ret_o", props=dict(Radius=74.5, Height=4, Placement=App.Placement(
    Vector(0, Y0 + 11.5, HC + DZ), Rotation(Vector(0, 0, 1), Vector(0, 1, 0)))))
ret_in = obj("Cylinder", "_Ret_i", props=dict(Radius=66, Height=6, Placement=App.Placement(
    Vector(0, Y0 + 10.5, HC + DZ), Rotation(Vector(0, 0, 1), Vector(0, 1, 0)))))
tabs = []
for i, a in enumerate(RET_ANG):
    rot = Rotation(Vector(0, 1, 0), -a)          # radial tab in the x-z plane
    c, s_ = math.cos(math.radians(a)), math.sin(math.radians(a))
    t = box(f"_RetTab_{i}", 0, 0, 0, 14, 4, 10)
    t.Placement = App.Placement(Vector(72 * c + 5 * s_, Y0 + 11.5, HC + DZ + 72 * s_ - 5 * c), Rotation(Vector(0, 1, 0), -a))
    tabs.append(t)
ret_body = multi("Fuse", "_Ret_body", [boolean("Cut", "_Ret_ring", ret_ring, [ret_in])] + tabs)
boolean("Cut", "Display_Retainer", ret_body,
        [cyl(f"_RetTabHole_{i}", 1.7, 8, Vector(x, Y0 + 10, z), Vector(0, 1, 0)) for i, (x, z) in enumerate(RET_PTS)],
        "structure")

# =====================================================================================================================
# NECK: printed column Ø60 / bore Ø44, flange Ø90 under the cover top; 3 captive nuts at the top (inserted from the
# bore), 3 M3 up through the flange into nut bosses under the cover top. Prints flange-down, no supports.
# =====================================================================================================================
COVER_IN = V["BaseH"] - 6                       # ribbed 6 mm cover top: underside z = 79
FL_Z0 = COVER_IN - 10 - 6                       # flange 63..69, cover bosses 69..79
FL_PTS = [polar(38, a) for a in (30, 150, 270)]
flare = obj("Cone", "_Col_flare", props=dict(Radius1=30, Radius2=45, Height=15,          # 45 deg, no supports
                                             Placement=App.Placement(Vector(0, 0, SEAT - 15), Rotation())))
saddle = boolean("Cut", "_Col_saddle", cyl("_Col_saddle_c", 45, 14, Vector(0, 0, SEAT - 0.01)),
                 [obj("Ellipsoid", "_HeadE_clear", {"Radius1": f"{S}HeadRZ + 0.2 mm", "Radius2": f"{S}HeadRX + 0.2 mm",
                      "Radius3": f"{S}HeadRY + 0.2 mm", "Placement.Base.z": E_HC}),   # 0.2 mm seat clearance, follows Params
                  box("_Col_saddle_front", -60, -200, SEAT - 30, 120, 200 - F, 60)])
flare_c = boolean("Cut", "_Col_flare_c", flare, [box("_Col_flare_front", -60, -200, SEAT - 30, 120, 200 - F, 60)])
col = multi("Fuse", "_Col_body", [cyl("_Col_shaft", 30, SEAT - FL_Z0, Vector(0, 0, FL_Z0)),
                                  cyl("_Col_flange", 45, 6, Vector(0, 0, FL_Z0)), flare_c, saddle])
col_bore = cyl("_Col_bore", V["TubeR"], SEAT - FL_Z0 + 2, Vector(0, 0, FL_Z0 - 1))
# hidden hex pockets from below in the flare (nut pressed up into the pocket, screw from inside the head)
col_top_nuts = [cyl(f"_ColNutPocket_{i}", NUT_R, 11, Vector(x, y, SEAT - 16)) for i, (x, y) in enumerate(SEAT_PTS)]
col_holes = ([cyl(f"_ColTopHole_{i}", 1.7, 14, Vector(x, y, SEAT - 6)) for i, (x, y) in enumerate(SEAT_PTS)] +
             [cyl(f"_ColFlHole_{i}", 1.7, 10, Vector(x, y, FL_Z0 - 1)) for i, (x, y) in enumerate(FL_PTS)])
boolean("Cut", "Neck_Column", col, [col_bore] + col_top_nuts + col_holes, "structure")

# =====================================================================================================================
# BASE: rounded puck cover (structural ribbed 6 mm top, colour-change navy band zone, front speaker grille, rear door
# opening, removable LED groove), tray (floor) with Pi bosses, intake slots; rear door.
# =====================================================================================================================
def puck(name, r, h, rb_bot, rb_top, z0=0.0):
    parts = [cyl(f"_{name}_mid", r, h - rb_bot - rb_top, Vector(0, 0, z0 + rb_bot)),
             cyl(f"_{name}_bot", r - rb_bot, rb_bot, Vector(0, 0, z0)),
             cyl(f"_{name}_top", r - rb_top, rb_top, Vector(0, 0, z0 + h - rb_top)),
             obj("Torus", f"_{name}_tb", props=dict(Radius1=r - rb_bot, Radius2=rb_bot,
                                                    Placement=App.Placement(Vector(0, 0, z0 + rb_bot), Rotation()))),
             obj("Torus", f"_{name}_tt", props=dict(Radius1=r - rb_top, Radius2=rb_top,
                                                    Placement=App.Placement(Vector(0, 0, z0 + h - rb_top), Rotation())))]
    return multi("Fuse", name, parts)


b_out = puck("_Base_Outer", V["BaseR"], V["BaseH"], V["BaseRbBot"], V["BaseRbTop"])
b_in = puck("_Base_Void", V["BaseR"] - W, V["BaseH"] - 2 * W, V["BaseRbBot"] - W + 1, V["BaseRbTop"] - W, W)
floor_box = box("_FloorBox", -300, -300, -1, 600, 600, 1 + 4)
neck_hole = cyl("_BaseNeckHole", 30.5, 12, Vector(0, 0, COVER_IN - 1))
SPK_Z = 33.5                                    # speaker centre height (navy band zone 18..62)
spk_holes = []
for i in range(11):                             # single front grille window: 11 x 5 holes Ø3.5
    for j in range(5):
        x = -30 + 6 * i
        spk_holes.append(cyl(f"_SpkHole_{i}_{j}", 1.75, 14, Vector(x, -110, SPK_Z - 13 + 6.5 * j), Vector(0, 1, 0)))
LED_Z0, LED_Z1 = 74.0, 80.0
backer = ring("_LED_Backer", 96, 84, 72, 10)                      # wall thickening behind the LED groove
led_groove = ring("_LED_Groove", 112, 90, LED_Z0, LED_Z1 - LED_Z0)
led_wire = cyl("_LED_WireHole", 2, 14, Vector(0, 80, 77), Vector(0, 1, 0))
door_box = box("_DoorBox", -55, 80, 6, 110, 40, 66)            # low enough to reach the Pi microSD
top_thick = cyl("_CoverTopThick", 78, 3.01, Vector(0, 0, COVER_IN))            # 3 -> 6 mm structural top
ribs = []
for k in range(6):                                                             # radial ribs under the top
    rb = box(f"_TopRib_{k}", 46, -1.5, COVER_IN - 6, 32, 3, 6.5)
    rb.Placement = App.Placement(Vector(46 * math.cos(math.radians(k * 60 + 0)) + 1.5 * math.sin(math.radians(k * 60)),
                                        46 * math.sin(math.radians(k * 60)) - 1.5 * math.cos(math.radians(k * 60)),
                                        COVER_IN - 6), Rotation(Vector(0, 0, 1), k * 60))
    ribs.append(rb)
cbosses = [cyl(f"_TopBoss_{i}", 5, 10.5, Vector(x, y, COVER_IN - 10)) for i, (x, y) in enumerate(FL_PTS)]
cnuts = []
for i, (x, y) in enumerate(FL_PTS):            # side-entry nut slot in each top boss (cover is upside-down at assembly)
    a = math.atan2(y, x)
    sl = box(f"_TopNutSlot_{i}", 0, -NUT_R, 0, 12, 2 * NUT_R, NUT_T)
    sl.Placement = App.Placement(Vector(x + NUT_R * math.sin(a), y - NUT_R * math.cos(a), COVER_IN - 6),
                                 Rotation(Vector(0, 0, 1), math.degrees(a)))
    cnuts.append(sl)
cholds = [cyl(f"_TopBossHole_{i}", 1.7, 9, Vector(x, y, COVER_IN - 11)) for i, (x, y) in enumerate(FL_PTS)]
FLOOR_BOSS = [polar(96.5, a) for a in (15, 165, 215, 325)]
fbosses = [cyl(f"_FloorBoss_{i}", 5, 12, Vector(x, y, 4)) for i, (x, y) in enumerate(FLOOR_BOSS)]
fnuts = [cyl(f"_FloorNut_{i}", NUT_R, NUT_T, Vector(x, y, 9)) for i, (x, y) in enumerate(FLOOR_BOSS)]
fnslot = []
for i, (x, y) in enumerate(FLOOR_BOSS):        # side-entry slot toward the centre for the floor-screw nut
    a = math.atan2(-y, -x)
    sl = box(f"_FloorNutSlot_{i}", 0, -NUT_R, 0, 8, 2 * NUT_R, NUT_T)
    sl.Placement = App.Placement(Vector(x + NUT_R * math.sin(a), y - NUT_R * math.cos(a), 9), Rotation(Vector(0, 0, 1), math.degrees(a)))
    fnslot.append(sl)
fholes = [cyl(f"_FloorBossHole_{i}", 1.7, 12, Vector(x, y, 3)) for i, (x, y) in enumerate(FLOOR_BOSS)]
shell_body = boolean("Cut", "_Base_ShellBody", b_out, [b_in, floor_box, neck_hole] + spk_holes)
shell_bk0 = multi("Fuse", "_Base_ShellBacked0", [shell_body, backer, top_thick] + ribs + cbosses + fbosses)
shell_bk = boolean("Cut", "_Base_ShellBacked", shell_bk0, [neck_hole] + cnuts + cholds + fnuts + fnslot + fholes)
shell_raw = boolean("Cut", "_Base_ShellRaw", shell_bk, [led_groove, led_wire])
shell = boolean("Cut", "_Base_ShellNoDoor", shell_raw, [door_box])
band_box = box("_BandZoneBox", -300, -300, 18, 600, 600, 44)
multi("Common", "Base_Shell_Band", [shell, band_box], "exterior")   # navy colour zone of the base cover print
boolean("Cut", "Base_Shell", shell, [band_box], "exterior")          # sky-blue zone
door_raw = multi("Common", "_Door_Raw", [shell_raw, door_box])
dvents = [box(f"_Vent_{i}", -30, 80, 22 + i * 7, 60, 40, 3) for i in range(5)]
cable_notch = box("_PowerNotch", 30, 80, 12, 12, 40, 7)              # official PSU cable passes under the door
boolean("Cut", "Base_Door", door_raw, dvents + [cable_notch], "exterior")
# tray (floor): Pi bosses, cover screw holes, intake slots
PI_X, PI_Y0 = -28, -14                          # Pi 5: long axis along y, microSD edge toward the rear door
PI_HOLES = [(-24.5, PI_Y0 + 85 - 3.5), (24.5, PI_Y0 + 85 - 3.5), (-24.5, PI_Y0 + 85 - 61.5), (24.5, PI_Y0 + 85 - 61.5)]
floor_raw = multi("Common", "_Floor_raw", [b_out, floor_box])
pi_bosses = [cyl(f"_PiBoss_{i}", 3.2, 6, Vector(x, y, 4)) for i, (x, y) in enumerate(PI_HOLES)]
cradle = box("_SpeakerCradle", -25, -96, 4, 50, 22, SPK_Z - 22.5 - 4)          # speaker cradle (strap screw)
floor_b = multi("Fuse", "_Floor_bossed", [floor_raw, cradle] + pi_bosses)
intake = [box(f"_Intake_{i}", -18, PI_Y0 + 12 + i * 12, -1, 36, 3, 7) for i in range(5)]
floor_holes = ([cyl(f"_FloorHole_{i}", 1.7, 8, Vector(x, y, -2)) for i, (x, y) in enumerate(FLOOR_BOSS)] +
               [cyl(f"_PiHole_{i}", 1.1, 12, Vector(x, y, 0)) for i, (x, y) in enumerate(PI_HOLES)])
boolean("Cut", "Base_Floor", floor_b, floor_holes + intake, "structure")


def led_half(name, side, cat):                 # diffuser / blank cover half (front -y or back +y), flush with shoulder
    cone = obj("Cone", f"_{name}_cone", props=dict(Radius1=100.4, Radius2=95.2, Height=LED_Z1 - LED_Z0 - 0.4,
                                                   Placement=App.Placement(Vector(0, 0, LED_Z0 + 0.2), Rotation())))
    bore = cyl(f"_{name}_bore", 91.4, 10, Vector(0, 0, LED_Z0 - 2))
    rng = boolean("Cut", f"_{name}_ring", cone, [bore])
    half = box(f"_{name}_half", -150, -150.2 if side < 0 else 0.2, 0, 300, 150, 200)
    return multi("Common", name, [rng, half], cat)


led_half("LED_Blank_Front", -1, "exterior")                        # DEFAULT: printed blank ring halves (snap-in)
led_half("LED_Blank_Back", 1, "exterior")
led_half("LED_Diffuser_Front", -1, "alternate")                    # OPTION: translucent halves when LEDs are added
led_half("LED_Diffuser_Back", 1, "alternate")

# =====================================================================================================================
# COMPONENT ENVELOPES (vendor-dependent; PROVISIONAL, not a SKU lock) and reserved volumes
# =====================================================================================================================
cyl("Display_Panel", 75, 11.5, Vector(0, Y0, HC + DZ), Vector(0, 1, 0), "component")
box("Display_Driver", -44.5, Y0 + 11.5, HC + DZ - 30, 85.5, 18.05 - 11.5, 58, "component")
box("Keepout_Display_Cable", -44.5, Y0 + 18.05, HC + DZ - 30, 85.5, 25, 58, "reserved")
box("Camera_Module", -12.5, Y0, HC + DZ + 76, 25, 12.4, 24, "component")
box("Mic_Board", -43, MIC_Y0, MIC_TOP - 10, 86, 35, 10, "component")          # ReSpeaker Lite class 35 x 86
box("Pi_5", PI_X, PI_Y0, 10, 56, 85, 21, "component")                         # + Active Cooler (height PROVISIONAL)
box("Reserve_M2_HAT", PI_X, PI_Y0, 31, 56, 85, 21, "reserved")                 # official M.2 HAT+ stack height
box("Speaker_Module", -25, -96, SPK_Z - 22.5, 50, 22, 45, "component")         # Seeed mono enclosed 4R 5W 50x45x22
box("Reserve_Amp", -80, 0, 4, 40, 30, 20, "reserved")                          # fallback: 3.5 mm-in amp if needed
box("LED_Connector", 26, 82, 60, 15, 8, 8, "alternate")                        # optional LED plug, behind rear door
ring("LED_Strip", 91.2, 90.0, LED_Z0 + 1, 3.5, "alternate")                    # optional LED strip in the groove
cyl("Keepout_Neck_Cables", V["TubeR"] - 0.5, SEAT - FL_Z0 + 6, Vector(0, 0, FL_Z0 - 3), cat="reserved")

doc.recompute()
bad = [o.Name for o in doc.Objects if "Invalid" in o.State or not o.isValid()]
if bad:
    raise RuntimeError(f"recompute errors: {bad}")

# ---------------- assembly metadata: kind, step, explode offset (mm, CAD frame), note[, print group] ----------------
# kind: printed | purchased | placeholder (electronics, no SKU lock) | reserved | alternate
H = (0, 0, 190)                                # whole head lifts this much in the exploded view
META = {
    "Base_Floor": ("printed", 1, (0, 0, -70), "TRAY: print flat; Pi bosses (M2.5 self-tap), intake slots, 4 cover screws"),
    "Pi_5": ("placeholder", 1, (0, 0, -40), "Raspberry Pi 5 + Active Cooler; 4x M2.5x6; microSD edge faces the rear door"),
    "Reserve_M2_HAT": ("reserved", 1, (0, 0, -20), "RESERVED: official M.2 HAT+ (SSD) stack height"),
    "Speaker_Module": ("placeholder", 1, (0, -45, -40), "Seeed mono enclosed speaker 4R 5W 50x45x22 (for ReSpeaker Lite); cradle + 1 strap screw"),
    "Reserve_Amp": ("reserved", 1, (-30, 0, -40), "RESERVED: only if a 3.5 mm-input amplifier is needed (fallback)"),
    "LED_Connector": ("alternate", 1, (0, 40, -20), "optional LED plug, behind rear door"),
    "Neck_Column": ("printed", 2, (0, 0, 35), "print flange-down; 45 deg saddle flare under the head (3 hidden nut pockets); 3x M3x12 up into cover bosses"),
    "Base_Shell": ("printed", 3, (0, 0, 70), "COVER: print top-down; ribbed 6 mm top, 3 nut bosses; 4x M3x12 from underside", "Base_Shell"),
    "Base_Shell_Band": ("printed", 3, (0, 0, 70), "navy colour-change zone of the cover print (same part)", "Base_Shell"),
    "LED_Blank_Front": ("printed", 4, (0, -55, 95), "blank ring half, snap-in (default)"),
    "LED_Blank_Back": ("printed", 4, (0, 55, 95), "blank ring half, snap-in (default)"),
    "LED_Diffuser_Front": ("alternate", 4, (0, -55, 95), "optional translucent half when LEDs are fitted"),
    "LED_Diffuser_Back": ("alternate", 4, (0, 55, 95), "optional translucent half when LEDs are fitted"),
    "LED_Strip": ("alternate", 4, (0, 0, 85), "optional addressable strip; needs a no-solder kit (not selected)"),
    "Head_Front": ("printed", 5, (0, 0, 0), "HEAD BODY: print face-down; seat ring, 3 seat holes, retainer + mic bosses, lid nuts", "Head_Front"),
    "Head_Face_Zone": ("printed", 5, (0, 0, 0), "dark colour zone of the head body print (same part)", "Head_Front"),
    "Display_Panel": ("placeholder", 5, (0, -70, 0), "5in round HDMI display (measured STEP envelope); glass is the face"),
    "Display_Driver": ("placeholder", 5, (0, -60, 0), "display driver board envelope"),
    "Keepout_Display_Cable": ("reserved", 5, (0, -50, 0), "RESERVED: HDMI/USB plug + bend room"),
    "Display_Retainer": ("printed", 5, (0, -35, 0), "print flat; 4x M3x10 self-tap into face-wall bosses"),
    "Camera_Module": ("placeholder", 5, (0, -60, 20), "Camera Module 3 (Wide); 2x M2 self-tap; 500 mm Pi 5 ribbon"),
    "Mic_Board": ("placeholder", 5, (0, 0, 40), "ReSpeaker Lite (XU316, 2 mics) 35x86; 2x M2.5 self-tap under crown"),
    "Keepout_Neck_Cables": ("reserved", 3, (0, 0, 35), "RESERVED: 5 cables through the Ø44 bore"),
    "Head_Back": ("printed", 6, (0, 70, 0), "SERVICE LID: print open side down; 2 hooks + 2x M3x16 into body nuts"),
    "Base_Door": ("printed", 7, (0, 70, 70), "rear door: 2 hooks + 1x M3; vents; PSU cable notch"),
}
PRINT_ROT = {"Head_Front": ("X", 90), "Head_Face_Zone": ("X", 90), "Head_Back": ("X", 90), "Base_Shell": ("X", 180),
             "Base_Shell_Band": ("X", 180), "Base_Door": ("X", -90), "Display_Retainer": ("X", 90),
             "Neck_Column": ("Z", 0)}
PREFIX = {"printed": "P", "purchased": "B", "placeholder": "E", "reserved": "R", "alternate": "A"}
missing = sorted(set(CATS) - set(META))
extra = sorted(set(META) - set(CATS))
if missing or extra:
    raise RuntimeError(f"metadata mismatch: missing {missing}, extra {extra}")
PID, counters = {}, {}
for k in sorted(CATS, key=lambda n: (META[n][1], n)):          # part numbers follow the assembly order
    if len(META[k]) > 4 and META[k][4] != k:                    # colour zone: numbered with its print below
        continue
    pre = PREFIX[META[k][0]]
    counters[pre] = counters.get(pre, 0) + 1
    PID[k] = f"{pre}{counters[pre]:02d}"
for k in CATS:                                                  # colour zones share the ID of their print
    grp = META[k][4] if len(META[k]) > 4 else k
    PID[k] = PID[grp]
sh.set("E1", "categories")
for i, (k, c) in enumerate(sorted(CATS.items()), start=2):  # stored for export / downstream checks
    kind, step, ex, note = META[k][:4]
    grp = META[k][4] if len(META[k]) > 4 else k
    sh.set(f"K{i}", PID[k])
    rot = PRINT_ROT.get(k, ("Z", 0))
    sh.set(f"L{i}", f"'{rot[0]},{rot[1]}")
    sh.set(f"M{i}", grp)
    if step >= 5 and k not in ("Base_Door",):   # head sub-assembly rides on the head lift
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
