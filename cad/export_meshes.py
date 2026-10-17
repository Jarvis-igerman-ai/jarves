# JARVES CAD -> mesh export for Blender (CAD stays the source of geometry; STL in mm, Z up, front = -Y).
# Exports the Pebble A model (S1.T03R2): every categorised part from cad/JARVIS_pebble.FCStd, plus
# build/parts/manifest.json (part -> category + exact bbox in mm) used by the Blender and Godot checks.
# Run: freecadcmd.exe cad\export_meshes.py   -> build/parts/<PartName>.stl (generated, git-ignored)
import json
import os
import FreeCAD as App
import MeshPart

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(root, "build", "parts")
os.makedirs(out, exist_ok=True)
for f in os.listdir(out):  # stale meshes from an older model would leak into Blender
    if f.endswith(".stl"):
        os.remove(os.path.join(out, f))
doc = App.openDocument(os.path.join(root, "cad", "JARVIS_pebble.FCStd"))
sh = doc.getObject("Params")
cats, i = {}, 2
while True:  # categories stored by build_pebble.py in columns E/F; empty cell raises
    try:
        cats[sh.get(f"E{i}")] = sh.get(f"F{i}")
    except Exception:
        break
    i += 1
parts, ext = {}, None
for n, c in sorted(cats.items()):
    s = doc.getObject(n).Shape
    MeshPart.meshFromShape(s, LinearDeflection=0.15, AngularDeflection=0.15).write(os.path.join(out, n + ".stl"))
    b = s.optimalBoundingBox()
    parts[n] = {"category": c, "bbox_mm": [round(v, 2) for v in (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)]}
    if c == "exterior":
        ext = b if ext is None else ext.united(b)
manifest = {"model": "cad/JARVIS_pebble.FCStd", "units": "mm", "frame": "Z up, front -Y",
            "exterior_bbox_mm": [round(v, 2) for v in (ext.XMin, ext.YMin, ext.ZMin, ext.XMax, ext.YMax, ext.ZMax)],
            "parts": parts}
with open(os.path.join(out, "manifest.json"), "w") as fh:
    json.dump(manifest, fh, indent=1)
print(f"exported {len(parts)} parts to {out}")
