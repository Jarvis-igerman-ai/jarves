# JARVIS S1.T01a - orthographic preview (front/side/top + iso) of JARVIS_blockout.FCStd. Headless.
# Run: freecadcmd.exe cad\render_preview.py
import os
import FreeCAD as App
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

here = os.path.dirname(os.path.abspath(__file__))
doc = App.openDocument(os.path.join(here, "JARVIS_blockout.FCStd"))
colors = {"Head": "#e6e6e6", "Body": "#cfcfd2", "Screen": "#10101a", "Camera": "#3344ff",
          "Mic_Top": "#e08020", "Speaker_L": "#666666", "Speaker_R": "#666666", "Door_Rear": "#8fb0e0"}
views = [("Front (from -Y)", 0, -90), ("Side (from +X)", 0, 0), ("Top", 90, -90)]
fig = plt.figure(figsize=(14, 5))
for i, (title, elev, azim) in enumerate(views, 1):
    ax = fig.add_subplot(1, 3, i, projection="3d", proj_type="ortho")
    for name, col in colors.items():
        v, f = doc.getObject(name).Shape.tessellate(0.5)
        tris = [[(v[a].x, v[a].y, v[a].z) for a in t] for t in f]
        ax.add_collection3d(Poly3DCollection(tris, facecolor=col, edgecolor="none", alpha=1.0))
    ax.set_xlim(-140, 140); ax.set_ylim(-140, 140); ax.set_zlim(0, 280)
    ax.set_box_aspect((280, 280, 280))
    ax.view_init(elev=elev, azim=azim); ax.set_title(title); ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z")
fig.suptitle("JARVIS blockout - PROVISIONAL dimensions (H280 W200 D160 mm), not a fit drawing")
fig.savefig(os.path.join(here, "JARVIS_blockout_preview.png"), dpi=80)
print("preview written")
