# JARVES Blender smoke test (fast, no render). Exits non-zero on failure.
# Run: blender -b --python blender/smoke_blender.py
# Checks blender/JARVIS.glb against blender/JARVIS_manifest.json (written from the CAD export):
# every part present with a material, per-part bbox within 1.5 mm of CAD, Godot assets in sync, renders exist.
import filecmp
import json
import os
import sys
import bpy
from mathutils import Vector

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
b = os.path.join(root, "blender")
errors = []
man = json.load(open(os.path.join(b, "JARVIS_manifest.json")))
fin = json.load(open(os.path.join(b, "finishes.json")))

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=os.path.join(b, "JARVIS.glb"))
objs = {o.name.split(".")[0]: o for o in bpy.data.objects if o.type == "MESH"}
worst = 0.0
for n, info in man["parts"].items():
    o = objs.get(n)
    if o is None:
        errors.append(f"missing part {n}")
        continue
    if not o.data.materials:
        errors.append(f"{n} has no material")
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    got = [min(p[i] for p in pts) * 1000 for i in range(3)] + [max(p[i] for p in pts) * 1000 for i in range(3)]
    dev = max(abs(g - w) for g, w in zip(got, info["bbox_mm"]))
    worst = max(worst, dev)
    if dev > 1.5:
        errors.append(f"{n} bbox off by {dev:.1f} mm")
extra = set(objs) - set(man["parts"])
if extra:
    errors.append(f"unexpected objects {sorted(extra)}")
for role_part in fin["roles"]:
    if role_part not in man["parts"]:
        errors.append(f"finish role for unknown part {role_part}")
for f in ("JARVIS.glb", "JARVIS_manifest.json", "finishes.json"):
    src = os.path.join(b, f)
    if not filecmp.cmp(src, os.path.join(root, "godot", "assets", f), shallow=False):
        errors.append(f"godot/assets/{f} out of sync with blender/{f}")
for f in ["JARVIS.blend"] + [f"renders/pebble_{v}.png" for v in ("front", "side", "rear", "interior")] + \
        [f"renders/pebble_finish_{k}.png" for k in fin["finishes"]]:
    if not os.path.exists(os.path.join(b, f)):
        errors.append(f"missing blender/{f}")
print(f"parts {len(man['parts'])}, worst bbox deviation {worst:.2f} mm")
print("SMOKE RESULT:", "FAIL " + "; ".join(errors) if errors else f"PASS ({len(man['parts'])} parts)")
sys.stdout.flush()
os._exit(1 if errors else 0)
