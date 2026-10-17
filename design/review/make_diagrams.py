# JARVES S1 design review (Issue #3): head-mount section + rear service schematic. NOT TO SCALE.
# All loads, tolerances, masses and cable data are UNVERIFIED until physical validation.
# Run with any Python that has matplotlib, e.g. freecadcmd design/review/make_diagrams.py
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Ellipse, FancyArrowPatch, Circle

out = os.path.dirname(os.path.abspath(__file__))
fig, (a1, a2) = plt.subplots(1, 2, figsize=(19, 7.2))
SHELL, STRUCT, PCB, CABLE, LOAD, AIR = "#d9d9d9", "#4a6fa5", "#2e8b57", "#d2691e", "#c0392b", "#3a9ad9"


def note(ax, xy, txt, xt, yt, c="#222"):
    ax.annotate(txt, xy=xy, xytext=(xt, yt), fontsize=8.5, color=c,
                arrowprops=dict(arrowstyle="-", color="#777", lw=0.7), va="center")


# ---------- (a) side section: structural spine + neck ----------
ax = a1
ax.set_title("(a) Side section — proposed structural head mount (all concepts)\nschematic, not to scale; front = left",
             fontsize=11)
ax.add_patch(Ellipse((0, 190), 200, 180, fc=SHELL, ec="#888", lw=2, alpha=0.6))           # head skin
ax.add_patch(FancyBboxPatch((-95, 0), 190, 85, boxstyle="round,pad=0,rounding_size=28", fc=SHELL, ec="#888",
                            lw=2, alpha=0.6))                                                # base skin
ax.add_patch(Rectangle((-92, 172), 6, 40, fc="#111"))                                       # display (edge-on)
ax.add_patch(Rectangle((-86, 168), 4, 48, fc=STRUCT))                                       # display bezel mount
ax.add_patch(Rectangle((-10, 112), 8, 150, fc=STRUCT))                                      # head spine plate
ax.add_patch(Rectangle((-82, 205), 72, 5, fc=STRUCT))                                       # display arm to spine
ax.add_patch(Rectangle((-54, 262), 108, 6, fc=PCB))                                         # mic array
ax.add_patch(Rectangle((-54, 268), 108, 3, fc="#f4d03f"))                                   # foam gasket
ax.add_patch(Rectangle((-30, 254), 60, 8, fc=STRUCT))                                       # mic standoff bracket
ax.add_patch(Rectangle((-88, 252), 6, 14, fc=PCB))                                          # camera
ax.add_patch(Rectangle((-28, 106), 56, 6, fc=STRUCT))                                       # top flange
ax.add_patch(Rectangle((-15, 78), 30, 28, fc="#9aa7b8", ec=STRUCT, lw=1.5))                 # neck tube
ax.add_patch(Rectangle((-28, 72), 56, 6, fc=STRUCT))                                        # bottom flange
ax.add_patch(Rectangle((-85, 66), 170, 6, fc=STRUCT))                                       # chassis plate
ax.add_patch(Rectangle((-80, 6), 160, 7, fc="#5d6d7e"))                                     # ballast plate
ax.add_patch(Rectangle((-20, 28), 56, 18, fc=PCB))                                          # Pi 5
ax.add_patch(Circle((-62, 38), 22, fc="#555"))                                              # speaker driver
for x in (-22, 22):
    ax.add_patch(Rectangle((x - 1.5, 62), 3, 52, fc="#111"))                                # M3 bolts (both flanges)
ax.plot([0, 0, -40, -86], [250, 92, 92, 192], color=CABLE, lw=2.5, ls="--")                 # display cable
ax.plot([4, 4, 20], [258, 60, 46], color=CABLE, lw=2.5, ls="--")                           # USB / CSI to Pi
for y0, y1 in ((185, 120), (100, 80), (66, 20)):
    ax.add_patch(FancyArrowPatch((60, y0), (60, y1), arrowstyle="-|>", mutation_scale=16, color=LOAD, lw=2))
ax.text(66, 140, "load path\nhead -> spine -> flange\n-> neck tube -> chassis\n-> ballast/base -> feet",
        color=LOAD, fontsize=8.5)
note(ax, (-6, 160), "head spine plate (carries display, mic, camera)\nskins are cosmetic, not load-bearing", -345, 150)
note(ax, (0, 92), "neck tube Ø30-40, hollow cable pass\n(Al 6061 tube or printed PETG-CF) UNVERIFIED", -345, 100)
note(ax, (-22, 75), "2 flanges x 4 x M3 into heat-set inserts\nanti-rotation key; torque UNVERIFIED", -345, 55)
note(ax, (0, 69), "chassis plate = structural datum\n(Pi tray, speakers, neck all bolt to it)", -345, 15)
note(ax, (0, 9), "optional steel ballast plate\nlowers centre of mass (mass UNVERIFIED)", 105, 5)
note(ax, (0, 269), "mic array on standoffs + foam gasket\nsealed to top ports, isolated from shell", 105, 290)
note(ax, (-86, 205), "display bezel screws\n(hole pattern: needs SKU drawing)", -345, 230)
note(ax, (-85, 259), "camera above screen", -345, 268)
note(ax, (-40, 92), "display + USB + CSI cables\nthrough neck (bend radius UNVERIFIED)", 105, 70, CABLE)
ax.text(-345, -45, "Tip-over angle ≈ atan(footprint radius / COM height): e.g. r=85 mm, COM 110 mm -> ~38°;\n"
        "COM 150 mm (no ballast) -> ~29°. Masses & COM are UNVERIFIED estimates - weigh real parts.",
        fontsize=8.5, color="#333")
ax.set_xlim(-350, 250)
ax.set_ylim(-55, 300)
ax.set_aspect("equal")
ax.axis("off")

# ---------- (b) rear view: service, airflow, cables, power ----------
ax = a2
ax.set_title("(b) Rear view — service access, airflow, cable route, power\nschematic, not to scale", fontsize=11)
ax.add_patch(Ellipse((0, 190), 200, 180, fc=SHELL, ec="#888", lw=2, alpha=0.6))
ax.add_patch(FancyBboxPatch((-95, 0), 190, 85, boxstyle="round,pad=0,rounding_size=28", fc=SHELL, ec="#888",
                            lw=2, alpha=0.6))
ax.add_patch(Rectangle((-15, 85), 30, 22, fc="#9aa7b8"))
ax.add_patch(FancyBboxPatch((-60, 135), 120, 110, boxstyle="round,pad=0,rounding_size=20", fc="none", ec=STRUCT,
                            lw=2, ls="--"))
ax.add_patch(FancyBboxPatch((-55, 12), 110, 58, boxstyle="round,pad=0,rounding_size=10", fc="#c8d3e0", ec=STRUCT, lw=2))
for i in range(5):
    ax.add_patch(Rectangle((-30, 20 + i * 7), 60, 3, fc="#333"))                             # inlet vents (low)
for i in range(4):
    ax.add_patch(Rectangle((-35, 225 - i * 7), 70, 3, fc="#333"))                            # outlet vents (head back, high)
for x in (-45, 45):
    ax.add_patch(Circle((x, 62), 3.5, fc="#111"))                                            # captive thumb screws
ax.add_patch(Rectangle((60, 14), 14, 6, fc="#111"))                                          # USB-C PD inlet
ax.add_patch(FancyArrowPatch((0, 18), (0, 220), arrowstyle="-|>", mutation_scale=18, color=AIR, lw=2.5,
                             connectionstyle="arc3,rad=0.25"))
note(ax, (0, 245), "head back cover: 2 screws + locating tabs\n(opens to display/mic/camera connectors)", 105, 270)
note(ax, (40, 62), "rear service door: 2 captive M3 thumb screws,\nbottom hook tabs; Pi ports behind it", 105, 110)
note(ax, (-30, 30), "cool-air inlet slots (low, rear door)", -345, 20)
note(ax, (-35, 218), "warm-air outlet slots (high, head back)\n'chimney' airflow; Pi 5 active cooler", -345, 245, AIR)
note(ax, (67, 17), "USB-C power inlet (Pi 5 needs 5 V / 5 A PSU)\nstrain relief; fuse/protection UNVERIFIED", 105, 25)
note(ax, (0, 96), "all head<->base cables pass inside the\nneck tube; no external cables", -345, 120)
ax.text(-345, -45, "Thermal performance, vent area and fan noise are UNVERIFIED: needs Pi 5 load test inside a printed shell.",
        fontsize=8.5, color="#333")
ax.set_xlim(-350, 250)
ax.set_ylim(-55, 300)
ax.set_aspect("equal")
ax.axis("off")

fig.suptitle("JARVES — structural head mount & rear service proposal (Issue #3)  ·  dimensions PROVISIONAL, loads UNVERIFIED",
             fontsize=13, y=1.0)
fig.savefig(os.path.join(out, "head_mount_and_rear_service.png"), dpi=110, bbox_inches="tight")
print("DIAGRAM DONE")
