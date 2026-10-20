# JARVES simple-build concept diagram (S1.T03R4 design-direction review). Schematic, NOT to scale, not CAD.
# Run: freecadcmd design/review/simple_build_concept.py  (any Python 3 + matplotlib)
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyBboxPatch, Rectangle, FancyArrowPatch

out = os.path.dirname(os.path.abspath(__file__))
fig, ax = plt.subplots(figsize=(13, 9.5))
P, B, E = "#cfe3f7", "#f5d7a8", "#b9e3c6"     # printed / bought accessory / electronics


def part(x, y, w, h, txt, c, shape="box"):
    if shape == "ell":
        ax.add_patch(Ellipse((x + w / 2, y + h / 2), w, h, fc=c, ec="#333", lw=1.5))
    else:
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=8", fc=c, ec="#333", lw=1.5))
    ax.text(x + w / 2, y + h / 2, txt, ha="center", va="center", fontsize=9)


def arrow(x0, y0, x1, y1, label=""):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=16, color="#e07b00", lw=2))
    if label:
        ax.text((x0 + x1) / 2 + 6, (y0 + y1) / 2, label, fontsize=8, color="#b35f00")


# exploded column (schematic)
part(-110, 430, 95, 150, "1 Head FRONT\n(bezel holds\ndisplay + camera)", P, "ell")
part(15, 430, 110, 150, "2 Head BACK\n(mic board pocket\nunder the crown)", P, "ell")
part(-30, 340, 60, 55, "3 NECK\n(one printed tube,\ncables inside)", P)
part(-105, 200, 210, 95, "4 BASE SHELL (sky blue)\nprinted speaker grille, rear opening,\nneck seat with nut pockets", P)
part(-105, 70, 210, 60, "5 ELECTRONICS TRAY (floor)\nPi bosses, speaker cradles, cable clips", P)
part(130, 95, 60, 45, "6 Rear door\n(snaps in)", P)
part(-205, 470, 75, 55, "5\" round display\n(own glass)", E)
part(-205, 400, 75, 45, "USB camera", E)
part(150, 500, 80, 45, "USB mic board", E)
part(-200, 85, 80, 45, "Raspberry Pi 5\n+ push-pin cooler", E)
part(-200, 20, 80, 45, "2 ready-made\nspeakers (plug)", E)
part(130, 20, 90, 45, "USB-C power\nsupply (official)", B)
part(130, 200, 90, 45, "screw + nut bag\n(one size, M3)", B)
arrow(-130, 497, -105, 505, "place, 4 screws")
arrow(-130, 422, -95, 470, "clip in")
arrow(150, 522, 110, 540, "place, 2 screws")
arrow(-120, 107, -90, 100, "4 screws")
arrow(-120, 42, -60, 80, "drop into cradle")
arrow(0, 430, 0, 397, "3+3 screws")
arrow(0, 340, 0, 297)
arrow(0, 200, 0, 132, "lower onto tray, 3 screws underneath")
ax.text(-205, 610, "JARVES simple build — schematic (not to scale)", fontsize=14, weight="bold")
ax.text(-205, 592, "6 printed parts (+ optional colour/LED extras) · ready-made plug-in electronics · one screwdriver",
        fontsize=10)
for i, (c, t) in enumerate(((P, "printed by the shop"), (E, "ready-made electronics"), (B, "ready-made accessory"))):
    ax.add_patch(Rectangle((-205 + i * 150, -40), 18, 14, fc=c, ec="#333"))
    ax.text(-182 + i * 150, -33, t, fontsize=9, va="center")
ax.text(-205, -70, "Cables (all ready-made, plug-in): micro-HDMI→HDMI, 2-3 short USB cables, speaker plug lead, USB-C power.\n"
        "No soldering, glue, drilling, cutting, filing or crimping. Strength, fit and power still need a physical test.",
        fontsize=9)
ax.set_xlim(-215, 250)
ax.set_ylim(-85, 625)
ax.axis("off")
fig.savefig(os.path.join(out, "simple_build_concept.png"), dpi=100, bbox_inches="tight")
print("CONCEPT DONE")
