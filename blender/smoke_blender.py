# JARVIS Blender smoke test (fast, no render). Exits non-zero on failure.
# Run: blender -b --python blender/smoke_blender.py
# Checks blender/JARVIS.glb: every CAD part (list from cad/export_meshes.py) present with a material,
# overall envelope ~ CAD H280 x W200 x D160 mm, and the two renders exist.
import ast
import os
import sys
import bpy
from mathutils import Vector

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
errors = []
src = open(os.path.join(root, "cad", "export_meshes.py")).read()
parts = ast.literal_eval(src[src.index("PARTS = ") + 8: src.index("]", src.index("PARTS = ")) + 1])

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=os.path.join(root, "blender", "JARVIS.glb"))
objs = {o.name.split(".")[0]: o for o in bpy.data.objects if o.type == "MESH"}
for n in parts:
    if n not in objs:
        errors.append(f"missing part {n}")
    elif not objs[n].data.materials:
        errors.append(f"{n} has no material")
for n in ("Face_Eye_L", "Face_Eye_R", "Face_Mouth"):
    if n not in objs:
        errors.append(f"missing {n}")

shells = [objs[n] for n in ("Head_Front", "Head_Back", "Body_Shell", "Door_Rear", "Screen") if n in objs]
pts = [o.matrix_world @ Vector(c) for o in shells for c in o.bound_box]
dims = [max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3)]  # m, Blender Z-up
want = (0.200, 0.160, 0.280)  # W, D, H (CAD envelope, PROVISIONAL)
for d, w, ax in zip(dims, want, "XYZ"):
    if abs(d - w) > 0.005:
        errors.append(f"envelope {ax}: {d * 1000:.1f} mm vs ~{w * 1000:.0f} mm")
for f in ("JARVIS.blend", "JARVIS_render.png", "JARVIS_internals_render.png"):
    if not os.path.exists(os.path.join(root, "blender", f)):
        errors.append(f"missing blender/{f}")

print("envelope mm:", [round(d * 1000, 1) for d in dims])
print("SMOKE RESULT:", "FAIL " + "; ".join(errors) if errors else f"PASS ({len(parts)} parts)")
sys.stdout.flush()
os._exit(1 if errors else 0)
