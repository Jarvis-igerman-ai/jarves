# JARVES Pebble A - proposed physical layout, TO SCALE (mm), for review (S1.T03R1, Issue #3). Not CAD.
# Local-first arrangement: growth head W215, 5" HDMI round display (stocked in Israel; measured STEP), Camera
# Module 3 Wide above it, mic array in a raised crown pod, aluminium neck tube, base with bridge plate over the Pi 5.
# (Import-only fallback: 4" DSI display in a W205 x D150 head - see pebble_fit_study.py.)
# Prints numeric containment checks and writes pebble_layout.png. Run: freecadcmd design/review/pebble_layout.py
import math
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, Rectangle, Circle, FancyBboxPatch, Polygon

out = os.path.dirname(os.path.abspath(__file__))
WALL = 3.0
# ---- head (from pebble_fit_study.py "growth" variant, 5in HDMI + pod) ----
RX, RY, RZ = 107.5, 100.0, 97.5
FACE_R, DZ = 96.0, -17.0                       # facet radius, display centre below head centre
F = RY * math.sqrt(1 - (FACE_R / RZ) ** 2)     # facet plane in front of head centre (~50)
BASE_R, BASE_H, NECK = 105.0, 85.0, 15.0       # modest-growth base (compact alt: R100 x H75)
HC = BASE_H + NECK + RZ                         # head centre height
CROWN_R = 50.0
ZC = RZ * math.sqrt(1 - (CROWN_R / RX) ** 2)    # crown plane above head centre
POD_H = 15.0                                    # raised mic crown pod (XVF3800 101 x 102 x 9 inside)
checks = []


def check(name, ok):
    checks.append((name, ok))


# ---- base plan items (x, y, w, d) in plan, y+ = rear ----
PI = (-28, -12, 56, 85)                         # Pi 5 85 x 56 (public spec), ports (USB/Ethernet) at rear edge
SSD = (36, -2, 28, 75)                          # RESERVED: M.2 2242 USB enclosure class (PROVISIONAL size)
SPK = [(-46, -66), (46, -66)]                   # driver centres, front-firing at ~+-35 deg
SPK_D, POD = 50.0, (60, 38)                     # driver class Ø50, sealed pod 60 wide x 38 deep (PROVISIONAL, tight)
r_in = BASE_R - WALL - 4                        # inner radius minus bevel allowance
for name, (x, y, w, d) in (("Pi 5", PI), ("SSD bay (RESERVED)", SSD)):
    far = max(math.hypot(px, py) for px in (x, x + w) for py in (y, y + d))
    check(f"{name} inside base plan (max r {far:.0f} <= {r_in:.0f})", far <= r_in)
POD_POLYS = []
for cx, cy in SPK:
    ang = math.atan2(cy, cx)
    # pod extends from the driver face inward (towards the centre) by POD[1], width POD[0]
    pts = [(cx - dx * math.cos(ang) - dy * math.sin(ang), cy - dx * math.sin(ang) + dy * math.cos(ang))
           for dx in (0, POD[1]) for dy in (-POD[0] / 2, POD[0] / 2)]
    POD_POLYS.append([pts[0], pts[1], pts[3], pts[2]])
    far = max(math.hypot(px, py) for px, py in pts)
    gap = PI[1] - max(py for _, py in pts)
    check(f"speaker pod at ({cx},{cy}) inside base (max r {far:.0f} <= {r_in:.0f}) and {gap:.0f} mm in front of Pi",
          far <= r_in and gap >= 3)
plug_room = (BASE_R - WALL) - (PI[1] + PI[3])
check(f"Pi port edge -> rear wall plug room {plug_room:.0f} mm (>= 25 target)", plug_room >= 25)
# vertical stack in base (z, mm): floor 0-4, Pi board 10, Pi+Active Cooler top ~31 (PROVISIONAL),
# optional M.2 HAT+ stack (16 mm header) top ~52, bridge plate 56-60, neck flange 60-66, base top 85
check("M.2 HAT+ option (Pi top 31 + 16 + 5) below bridge plate at 56", 31 + 16 + 5 <= 56)
check(f"neck flange top 66 below base top {BASE_H:.0f}", 66 <= BASE_H)
# head: tip angle estimate (UNVERIFIED masses, g) -> COM
parts = [(750, HC), (180, 260), (120, 70), (60, 25), (130, 40), (250, 45), (400, 7)]   # head, pod+mic, neck+bridge, Pi, speakers, base shell, ballast
com = sum(m * z for m, z in parts) / sum(m for m, _ in parts)
com0 = sum(m * z for m, z in parts[:-1]) / sum(m for m, _ in parts[:-1])
tip = math.degrees(math.atan((BASE_R - 10) / com))
tip0 = math.degrees(math.atan((BASE_R - 10) / com0))
check(f"tip angle ~{tip:.0f} deg with 400 g ballast, ~{tip0:.0f} deg without (masses UNVERIFIED, target >= 30)", tip0 >= 30)
H_TOTAL = HC + ZC + POD_H
for n, ok in checks:
    print(("PASS " if ok else "FAIL ") + n)
print(f"overall: H {H_TOTAL:.0f} x W {2 * max(RX, BASE_R):.0f} x D {2 * BASE_R:.0f} mm (head depth {F + RY:.0f})")

# ---------------- drawing ----------------
fig, (a, b) = plt.subplots(1, 2, figsize=(17, 8), gridspec_kw={"width_ratios": [1.05, 1]})
SH, ST, PCB, DK, RES = "#e4e4e4", "#4a6fa5", "#2e8b57", "#333", "#b07ad0"
# (a) side section x=0, front = left (-y)
a.set_title("(a) Side section at centre line (front = left) - TO SCALE, mm", fontsize=11)
head = Ellipse((0, HC), 2 * RY, 2 * RZ, fc=SH, ec="#888", lw=2)
a.add_patch(head)
a.add_patch(Rectangle((-RY - 5, HC - RZ - 5), RY - F + 5, 2 * RZ + 10, fc="white", ec="none"))     # cut by facet
a.add_patch(Rectangle((-RY - 5, HC + ZC), 2 * RY + 10, RZ, fc="white", ec="none"))                # cut by crown
a.plot([-F, -F], [HC - FACE_R * 0.98, min(HC + FACE_R * 0.98, HC + ZC)], color="#888", lw=2)
a.plot([-RY * CROWN_R / RX, RY * CROWN_R / RX], [HC + ZC, HC + ZC], color="#888", lw=2)
a.add_patch(Rectangle((-F + WALL, HC + DZ - 75), 11.5, 150, fc="#111"))                             # 5" panel
a.add_patch(Rectangle((-F + WALL + 7.7, HC + DZ - 30), 10.35, 58, fc=PCB))                          # driver board
a.add_patch(Rectangle((-F + WALL + 18.05, HC + DZ - 30), 25, 58, fc="none", ec="#d2691e", ls="--"))  # HDMI plug keep-out
a.add_patch(Rectangle((-F + WALL, HC + DZ + 76), 12.4, 24, fc=PCB))                                 # camera
a.add_patch(FancyBboxPatch((-58, HC + ZC), 116, POD_H, boxstyle="round,pad=0,rounding_size=6", fc=SH, ec="#888", lw=2))
a.add_patch(Rectangle((-51, HC + ZC + 3), 102, 3.5, fc=PCB))                                        # XVF3800
a.add_patch(Rectangle((40, HC - 70), 8, 140, fc=ST))                                                # head spine (behind display stack)
a.add_patch(Rectangle((-20, BASE_H - 20), 40, NECK + 30, fc="#9aa7b8", ec=ST))                      # neck tube Ø40
a.add_patch(Rectangle((-35, 60), 70, 6, fc=ST))                                                     # neck flange
a.add_patch(Rectangle((-35, HC - RZ + 3), 70, 6, fc=ST))                                            # top flange
a.add_patch(FancyBboxPatch((-BASE_R, 0), 2 * BASE_R, BASE_H, boxstyle="round,pad=0,rounding_size=26", fc="none",
                           ec="#888", lw=2))
a.add_patch(Rectangle((-BASE_R + 8, 56), 2 * BASE_R - 16, 4, fc=ST))                                # bridge plate
a.add_patch(Rectangle((PI[1], 10), PI[3], 21, fc=PCB))                                              # Pi + cooler
a.add_patch(Rectangle((PI[1], 31), PI[3], 21, fc="none", ec=RES, ls="--"))                          # M.2 HAT+ space
a.add_patch(Rectangle((-95, 15), 40, 50, fc="#555"))                                                # speaker pod
a.add_patch(Rectangle((-80, 4), 60, 6, fc="#5d6d7e"))                                               # ballast
a.add_patch(Rectangle((BASE_R - 3, 12), 3, 55, fc="#c8d3e0", ec=ST))                                # rear door
for t, x, y in [("5in HDMI round display Ø150 x 18 (measured STEP)\n+ HDMI plug keep-out (dashed)", -205, HC + DZ), ("Camera Module 3 Wide (verified)", -205, HC + 75),
                ("mic array (e.g. XVF3800) in raised crown pod\n(port path ~6 mm)", 70, HC + ZC + 28), ("head spine plate", 55, HC + 10),
                ("Al neck tube Ø40x2: cables inside", 30, BASE_H + 5), ("bridge plate (carries neck)", 112, 62),
                ("Pi 5 + Active Cooler", 112, 22), ("RESERVED: M.2 HAT+ height", 112, 42),
                ("front speaker pod (sealed, off-centre, projected)", -205, 40), ("ballast plate (optional)", -205, 6),
                ("rear service door", 112, 75)]:
    a.text(x, y, t, fontsize=8, va="center")
a.set_xlim(-210, 230)
a.set_ylim(-10, H_TOTAL + 15)
a.set_aspect("equal")
a.grid(alpha=0.25)
# (b) base plan (top view, front = bottom)
b.set_title(f"(b) Base plan, top view (front = down) - base Ø{2 * BASE_R:.0f}, TO SCALE, mm", fontsize=11)
b.add_patch(Circle((0, 0), BASE_R, fc="#f2f2f2", ec="#888", lw=2))
b.add_patch(Rectangle((PI[0], PI[1]), PI[2], PI[3], fc=PCB, alpha=0.85))
b.text(0, PI[1] + PI[3] / 2, "Pi 5\n85 x 56\n(under bridge)", ha="center", va="center", color="white", fontsize=8)
b.annotate("", xy=(0, BASE_R - 3), xytext=(0, PI[1] + PI[3]), arrowprops=dict(arrowstyle="<->", color=DK))
b.text(4, PI[1] + PI[3] + 8, f"{plug_room:.0f} mm plug room\nUSB/Ethernet -> door", fontsize=7.5)
b.add_patch(Rectangle((SSD[0], SSD[1]), SSD[2], SSD[3], fc="none", ec=RES, lw=2, ls="--"))
b.text(SSD[0] + 14, SSD[1] + 37, "RESERVED\nM.2 USB\nenclosure", ha="center", va="center", fontsize=7, color=RES)
for (cx, cy), poly in zip(SPK, POD_POLYS):
    b.add_patch(Polygon(poly, fc="#666", alpha=0.6))
    b.add_patch(Circle((cx, cy), 5, fc=DK))
b.text(0, -78, "2 front-firing speaker pods", ha="center", fontsize=8)
b.add_patch(Circle((0, 0), 20, fc="none", ec=ST, lw=2.5))
b.text(0, 0, "neck\nØ40", ha="center", va="center", fontsize=7, color=ST)
b.add_patch(Polygon([(-60, 92), (60, 92), (55, 101), (-55, 101)], fc="#c8d3e0", ec=ST))
b.text(0, 112, "rear door: 2 captive M3 thumb screws, inlet vents,\nUSB-C power inlet + 1 panel-mount spare USB",
       ha="center", fontsize=8)
for x in (-70, 70):
    b.add_patch(Circle((x, -40), 4, fc=DK))
b.text(-70, -52, "M3 base screws\n(from underside)", ha="center", fontsize=7)
b.set_xlim(-125, 125)
b.set_ylim(-125, 135)
b.set_aspect("equal")
b.grid(alpha=0.25)
fig.suptitle(f"JARVES Pebble A - proposed local-first layout  ·  ~H{H_TOTAL:.0f} x W{2 * max(RX, BASE_R):.0f} x "
             f"D{2 * BASE_R:.0f} mm  ·  PROVISIONAL, not CAD, fit/strength UNVERIFIED", fontsize=12)
fig.savefig(os.path.join(out, "pebble_layout.png"), dpi=100, bbox_inches="tight")
print("LAYOUT DONE")
