# JARVES Blender visual scene derived from CAD meshes (S1.T03R2 Pebble A). Visual only: NEVER a source for
# printed geometry. Inputs: build/parts/*.stl + build/parts/manifest.json (cad/export_meshes.py),
# blender/finishes.json (PROVISIONAL colour treatments). Units: STL mm -> scene metres. Part names = CAD names.
# Outputs: blender/JARVIS.blend, blender/JARVIS.glb, blender/JARVIS_manifest.json, blender/renders/pebble_*.png;
# also syncs JARVIS.glb + JARVIS_manifest.json + finishes.json into godot/assets/.
# Run: blender -b --python blender/build_scene.py   (`-- --no-render` skips renders, `-- --only-exploded` renders 2)
import json
import math
import os
import shutil
import sys
import bmesh
import bpy
from mathutils import Vector

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parts_dir = os.path.join(root, "build", "parts")
out = os.path.join(root, "blender")
renders = os.path.join(out, "renders")
no_render = "--no-render" in sys.argv
manifest = json.load(open(os.path.join(parts_dir, "manifest.json")))
fin = json.load(open(os.path.join(out, "finishes.json")))
ROLES, FINISHES, DEFAULT = fin["roles"], fin["finishes"], fin["default"]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def principled(m):
    return m.node_tree.nodes["Principled BSDF"]


def set_mat(m, spec, alpha=1.0):
    b = principled(m)
    b.inputs["Base Color"].default_value = (*spec["color"], 1)
    b.inputs["Roughness"].default_value = spec.get("rough", 0.5)
    b.inputs["Metallic"].default_value = spec.get("metal", 0.0)
    b.inputs["Coat Weight"].default_value = spec.get("coat", 0.0)
    if "emit" in spec:
        b.inputs["Emission Color"].default_value = (*spec["emit"], 1)
        b.inputs["Emission Strength"].default_value = spec.get("strength", 5)
    if alpha < 1.0:
        b.inputs["Alpha"].default_value = alpha
        try:
            m.surface_render_method = "BLENDED"
        except AttributeError:
            pass


def new_mat(name, spec, alpha=None):
    m = bpy.data.materials.new(name)
    set_mat(m, spec, spec.get("alpha", 1.0) if alpha is None else alpha)
    return m


# fixed (non-finish) materials by category / part
FIXED = {
    "structure": {"color": [0.22, 0.23, 0.25], "rough": 0.6},           # printed PETG structure
    "component": {"color": [0.05, 0.30, 0.14], "rough": 0.45},          # PCB-green envelopes
    "reserved": {"color": [0.55, 0.35, 0.80], "rough": 0.5},            # translucent purple reservations
}
SPECIAL = {"Neck_Tube": {"color": [0.75, 0.76, 0.78], "rough": 0.3, "metal": 1.0},
           "Display_Panel": {"color": [0.02, 0.02, 0.03], "rough": 0.2},
           "Speaker_Pod_L": {"color": [0.12, 0.12, 0.13], "rough": 0.6},
           "Speaker_Pod_R": {"color": [0.12, 0.12, 0.13], "rough": 0.6},
           "Speaker_Driver_L": {"color": [0.03, 0.03, 0.03], "rough": 0.7},
           "Speaker_Driver_R": {"color": [0.03, 0.03, 0.03], "rough": 0.7}}
for k in ("1", "2", "3", "4"):
    SPECIAL[f"Pi_Standoff_{k}"] = {"color": [0.8, 0.65, 0.3], "rough": 0.3, "metal": 1.0}
role_mats = {role: new_mat(f"Finish_{role}", spec) for role, spec in FINISHES[DEFAULT].items() if role != "label"}

# ---------------- import CAD parts ----------------
parts = {}
for n, info in manifest["parts"].items():
    bpy.ops.wm.stl_import(filepath=os.path.join(parts_dir, n + ".stl"), global_scale=0.001)
    o = bpy.context.selected_objects[0]
    o.name = o.data.name = n
    cat = info["category"]
    if n in ROLES:
        mat = role_mats[ROLES[n]]
    elif n in SPECIAL:
        mat = new_mat(f"M_{n}", SPECIAL[n])
    else:
        mat = bpy.data.materials.get(f"Cat_{cat}") or new_mat(f"Cat_{cat}", FIXED[cat],
                                                              0.35 if cat == "reserved" else 1.0)
    o.data.materials.append(mat)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.shade_smooth_by_angle(angle=math.radians(30))   # crisp flats, smooth curves
    o.select_set(False)
    o["category"] = cat
    parts[n] = o


def bbox_mm(o):
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return [round(min(p[i] for p in pts) * 1000, 2) for i in range(3)] + \
           [round(max(p[i] for p in pts) * 1000, 2) for i in range(3)]


# ---------------- visual-only face placeholder on the cover (real face = Godot face scene, later) ----------------
cover = bbox_mm(parts["Face_Cover"])
disp = bbox_mm(parts["Display_Panel"])
fy = cover[1] / 1000 - 0.0004
cz = (disp[2] + disp[5]) / 2000
face = []
for name, x in (("Face_Eye_L", -0.033), ("Face_Eye_R", 0.033)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(x, fy, cz + 0.014), segments=48, ring_count=24)
    o = bpy.context.object
    o.name, o.scale = name, (0.014, 0.0006, 0.02)
    face.append(o)
bpy.ops.mesh.primitive_torus_add(major_radius=0.016, minor_radius=0.0024, major_segments=64, minor_segments=16,
                                 location=(0, fy - 0.0008, cz - 0.02), rotation=(math.pi / 2, 0, 0))
o = bpy.context.object
o.name = "Face_Smile"
bm = bmesh.new()
bm.from_mesh(o.data)
bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.y > 0.15 * 0.016], context="VERTS")
bm.to_mesh(o.data)
bm.free()
face.append(o)
for o in face:
    o.data.materials.append(role_mats["face"])
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.shade_smooth()
    o["category"] = "exterior"

# ---------------- studio ----------------
bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 0, 0))
ground = bpy.context.object
ground.name = "Ground"
ground.data.materials.append(new_mat("Floor", {"color": [0.5, 0.49, 0.48], "rough": 0.7}))
scene.world = bpy.data.worlds.new("World")
bg = scene.world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.78, 0.79, 0.82, 1)
bg.inputs["Strength"].default_value = 0.22
target = bpy.data.objects.new("Target", None)
target.location = (0, 0, 0.15)
scene.collection.objects.link(target)


def light(name, loc, energy, size, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "AREA")
    d.energy, d.size, d.color = energy, size, color
    lo = bpy.data.objects.new(name, d)
    scene.collection.objects.link(lo)
    lo.location = loc
    t = lo.constraints.new("TRACK_TO")
    t.target, t.track_axis, t.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"


light("Key", (0.9, -1.4, 1.3), 85, 1.2, (1.0, 0.96, 0.92))
light("Fill", (-1.3, -0.9, 0.5), 30, 1.5, (0.9, 0.94, 1.0))
light("Rim", (0.2, 1.4, 1.1), 60, 1.0)
light("RearFill", (-0.6, 1.2, 0.4), 25, 1.2)
cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
scene.collection.objects.link(cam)
scene.camera = cam
cam.data.lens = 60
t = cam.constraints.new("TRACK_TO")
t.target, t.track_axis, t.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 32
scene.cycles.use_denoising = True
scene.view_settings.look = "AgX - Medium High Contrast"
scene.view_settings.exposure = -0.7
scene.render.resolution_x = scene.render.resolution_y = 1000

# ---------------- save .blend, .glb, manifest; sync to Godot ----------------
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "JARVIS.blend"))
bpy.ops.object.select_all(action="DESELECT")
for o in list(parts.values()) + face:
    o.select_set(True)
glb = os.path.join(out, "JARVIS.glb")
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True, export_extras=True)
man = dict(manifest)
man["parts"] = dict(manifest["parts"])
for o in face:
    man["parts"][o.name] = {"category": "exterior", "bbox_mm": bbox_mm(o), "visual_only": True,
                            "kind": "placeholder", "step": manifest["parts"]["Face_Cover"]["step"],
                            "explode_mm": manifest["parts"]["Face_Cover"]["explode_mm"],
                            "note": "visual face placeholder (real face = Godot face scene, later)"}
man["blender"] = bpy.app.version_string
with open(os.path.join(out, "JARVIS_manifest.json"), "w") as fh:
    json.dump(man, fh, indent=1)
gassets = os.path.join(root, "godot", "assets")
for src, dst in ((glb, "JARVIS.glb"), (os.path.join(out, "JARVIS_manifest.json"), "JARVIS_manifest.json"),
                 (os.path.join(out, "finishes.json"), "finishes.json")):
    shutil.copyfile(src, os.path.join(gassets, dst))


# ---------------- renders ----------------
def shot(path, loc, hide=()):
    for o in list(parts.values()) + face:
        o.hide_render = o.name in hide or o["category"] == "reserved" and "reserved" in hide
    cam.location = loc
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def apply_finish(key):
    for role, spec in FINISHES[key].items():
        if role != "label":
            set_mat(role_mats[role], spec)


def explode(on):
    for o in list(parts.values()) + face:
        e = man["parts"][o.name]["explode_mm"] if on else (0, 0, 0)
        lift = 100 if on else 0                # keep downward-exploded parts above the floor
        o.location = (e[0] / 1000, e[1] / 1000, (e[2] + lift) / 1000)


if not no_render:  # bounded S1.T03R3 review set (Godot exports the full screenshot set)
    os.makedirs(renders, exist_ok=True)
    alt = {n for n, i in man["parts"].items() if i["category"] == "alternate"}
    apply_finish(DEFAULT)
    if "--only-exploded" not in sys.argv:
        shot(os.path.join(renders, "pebble_r3_hero_C.png"), (0.55, -0.72, 0.34), hide={"reserved"} | alt)
        shot(os.path.join(renders, "pebble_r3_rear_C.png"), (-0.35, 0.85, 0.3), hide={"reserved"} | alt)
    explode(True)
    target.location = (0, 0, 0.36)
    cam.data.lens = 45
    shot(os.path.join(renders, "pebble_r3_exploded_3q.png"), (0.8, -1.05, 0.62), hide={"reserved"} | alt)
    shot(os.path.join(renders, "pebble_r3_exploded_side.png"), (1.3, 0, 0.38), hide={"reserved"} | alt)
    explode(False)
print("scene built; parts:", len(parts), "+ face", len(face))
