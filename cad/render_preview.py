# JARVIS preview renderer (headless): writes cad/JARVIS_blockout_preview.png (exterior) and
# cad/JARVIS_layout_preview.png (internal layout, shells transparent). Dimensions PROVISIONAL.
# Run: freecadcmd.exe cad\render_preview.py
import os
import FreeCAD as App
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

here = os.path.dirname(os.path.abspath(__file__))
doc = App.openDocument(os.path.join(here, "JARVIS_blockout.FCStd"))

OUTER = {"Head_Front": "#e6e6e6", "Head_Back": "#d8d8d8", "Body_Shell": "#cfcfd2", "Screen": "#10101a",
         "Camera": "#3344ff", "Mic_Top": "#e08020", "Speaker_L": "#666666", "Speaker_R": "#666666",
         "Door_Rear": "#8fb0e0"}
SHELLS = ("Head_Front", "Head_Back", "Body_Shell", "Door_Rear")
INNER = {"Display_Module": "#1a1a2e", "Camera_Module": "#3344ff", "Mic_Array": "#e08020",
         "Pi_5": "#2a9d4a", "Pi_Tray": "#7a7a7a", "Speaker_Driver_L": "#555555", "Speaker_Driver_R": "#555555",
         "Reserve_USB_Hub": "#b07ad0"}
# view: (title, horizontal axis, vertical axis, depth axis, depth sign: larger = nearer)
VIEWS = [("Front (from -Y)", 0, 2, 1, -1), ("Side (from +X)", 1, 2, 0, 1), ("Top (from +Z)", 0, 1, 2, 1)]


def tris(name):
    v, f = doc.getObject(name).Shape.tessellate(0.5)
    return [[(v[a].x, v[a].y, v[a].z) for a in t] for t in f]


def draw(path, title, layers, label):
    fig, axes = plt.subplots(1, 3, figsize=(15, 6))
    for ax, (vt, h, vv, d, sgn) in zip(axes, VIEWS):
        for names, cols, alpha, edge in layers:
            polys = []
            for n in names:
                for t in tris(n):
                    polys.append((sgn * sum(p[d] for p in t) / 3, [(p[h], p[vv]) for p in t], cols[n]))
            polys.sort(key=lambda x: x[0])  # far first
            ax.add_collection(PolyCollection([p[1] for p in polys], facecolors=[p[2] for p in polys],
                                             edgecolors=edge, linewidths=0.2, alpha=alpha))
        if label:
            for n in label:
                c = doc.getObject(n).Shape.BoundBox.Center
                ax.text((c.x, c.y, c.z)[h], (c.x, c.y, c.z)[vv], n.replace("_", " "), fontsize=6,
                        ha="center", va="center", color="white", bbox=dict(fc="black", alpha=0.55, pad=0.8, lw=0))
        ax.set_xlim(-130, 130) if h == 0 else ax.set_xlim(-100, 100)
        ax.set_ylim((0, 285) if vv == 2 else (-100, 100))
        ax.set_aspect("equal"); ax.set_title(vt); ax.grid(alpha=0.3)
    fig.suptitle(title)
    fig.savefig(path, dpi=85, bbox_inches="tight")


NOTE = "PROVISIONAL dimensions (H280 W200 D160 mm) - not a fit drawing"
draw(os.path.join(here, "JARVIS_blockout_preview.png"), "JARVIS exterior - " + NOTE,
     [(list(OUTER), OUTER, 1.0, "none")], None)
draw(os.path.join(here, "JARVIS_layout_preview.png"), "JARVIS internal layout (shells transparent) - " + NOTE,
     [(SHELLS, OUTER, 0.12, "#999999"), (list(INNER), INNER, 1.0, "#222222")], list(INNER))
print("previews written")
