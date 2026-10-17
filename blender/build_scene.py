# JARVIS S1.T02 - Blender visual scene derived from CAD meshes (build/parts/*.stl, made by cad/export_meshes.py).
# Visual only: NEVER a source for printed geometry. Units: STL mm -> scene metres. Part names = CAD names.
# Run: blender -b --python blender/build_scene.py   (add `-- --no-render` to skip the Cycles renders)
import math
import os
import sys
import bpy
from mathutils import Vector

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parts_dir = os.path.join(root, "build", "parts")
out = os.path.join(root, "blender")
no_render = "--no-render" in sys.argv

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# --- materials (name -> principled params) ---
WHITE = dict(c=(0.88, 0.88, 0.86), r=0.35, coat=0.6)
MATS = {
    "Head_Front": WHITE, "Head_Back": WHITE, "Body_Shell": dict(c=(0.84, 0.84, 0.83), r=0.4, coat=0.5),
    "Door_Rear": dict(c=(0.72, 0.78, 0.86), r=0.4, coat=0.3),
    "Screen": dict(c=(0.003, 0.003, 0.006), r=0.25, coat=0.0),
    "Camera": dict(c=(0.01, 0.01, 0.015), r=0.05, coat=1.0),
    "Mic_Top": dict(c=(0.08, 0.08, 0.09), r=0.5), "Speaker_L": dict(c=(0.12, 0.12, 0.13), r=0.6),
    "Speaker_R": dict(c=(0.12, 0.12, 0.13), r=0.6),
    "Display_Module": dict(c=(0.02, 0.02, 0.03), r=0.2), "Camera_Module": dict(c=(0.05, 0.3, 0.12), r=0.4),
    "Mic_Array": dict(c=(0.05, 0.05, 0.08), r=0.4), "Pi_5": dict(c=(0.0, 0.35, 0.1), r=0.35),
    "Pi_Tray": dict(c=(0.5, 0.5, 0.52), r=0.5), "Speaker_Driver_L": dict(c=(0.03, 0.03, 0.03), r=0.7),
    "Speaker_Driver_R": dict(c=(0.03, 0.03, 0.03), r=0.7), "Reserve_USB_Hub": dict(c=(0.5, 0.3, 0.7), r=0.5),
}


def material(name, c, r, coat=0.0, emit=None, strength=0.0):
    m = bpy.data.materials.new(name)
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*c, 1)
    b.inputs["Roughness"].default_value = r
    b.inputs["Coat Weight"].default_value = coat
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


# --- import CAD parts (mm -> m) ---
parts = {}
for n, spec in MATS.items():
    bpy.ops.wm.stl_import(filepath=os.path.join(parts_dir, n + ".stl"), global_scale=0.001)
    o = bpy.context.selected_objects[0]
    o.name = n
    o.data.name = n
    o.data.materials.append(material(n, spec["c"], spec["r"], spec.get("coat", 0.0)))
    bpy.ops.object.shade_smooth()
    parts[n] = o

# --- visual-only face placeholder on the screen (NOT CAD geometry; real face scene is Godot's job) ---
face_mat = material("Face_Blue", (0.1, 0.3, 0.8), 0.3, emit=(0.25, 0.6, 1.0), strength=8.0)
bb = [parts["Screen"].matrix_world @ Vector(v) for v in parts["Screen"].bound_box]
cx = sum(v.x for v in bb) / 8
cz = sum(v.z for v in bb) / 8
face_y = min(v.y for v in bb) - 0.0004
face = []
for name, x, z, sx, sz_ in (("Face_Eye_L", -0.028, 0.012, 0.011, 0.016), ("Face_Eye_R", 0.028, 0.012, 0.011, 0.016),
                            ("Face_Mouth", 0.0, -0.022, 0.012, 0.0035)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(cx + x, face_y, cz + z), segments=32, ring_count=16)
    o = bpy.context.object
    o.name = name
    o.scale = (sx, 0.0006, sz_)
    o.data.materials.append(face_mat)
    bpy.ops.object.shade_smooth()
    face.append(o)

# --- ground, lights, camera, world ---
bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 0, -0.0005))
ground = bpy.context.object
ground.name = "Ground"
ground.data.materials.append(material("Ground", (0.35, 0.36, 0.38), 0.7))
scene.world = bpy.data.worlds.new("World")
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.55, 0.6, 0.68, 1)
bg.inputs["Strength"].default_value = 0.25


def area(name, loc, energy, size, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "AREA")
    d.energy, d.size, d.color = energy, size, color
    o = bpy.data.objects.new(name, d)
    scene.collection.objects.link(o)
    o.location = loc
    con = o.constraints.new("TRACK_TO")
    con.target, con.track_axis, con.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"


target = bpy.data.objects.new("Target", None)
target.location = (0, 0, 0.15)
scene.collection.objects.link(target)
area("Key", (0.6, -0.8, 0.7), 70, 0.8, (1.0, 0.96, 0.9))
area("Fill", (-0.8, -0.5, 0.3), 25, 1.0, (0.85, 0.9, 1.0))
area("Rim", (0.0, 0.9, 0.6), 50, 0.6)
cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
cam.data.lens = 75
cam.location = (0.55, -1.0, 0.42)
scene.collection.objects.link(cam)
con = cam.constraints.new("TRACK_TO")
con.target, con.track_axis, con.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
scene.camera = cam

scene.view_settings.view_transform = "Standard"
scene.view_settings.exposure = -1.6
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 48
scene.cycles.use_denoising = True
scene.render.resolution_x = scene.render.resolution_y = 900

# --- save .blend and .glb (parts + face only, no ground/lights) ---
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "JARVIS.blend"))
bpy.ops.object.select_all(action="DESELECT")
for o in list(parts.values()) + face:
    o.select_set(True)
bpy.ops.export_scene.gltf(filepath=os.path.join(out, "JARVIS.glb"), export_format="GLB", use_selection=True)

if not no_render:
    scene.render.filepath = os.path.join(out, "JARVIS_render.png")
    bpy.ops.render.render(write_still=True)
    internal = {"Display_Module", "Camera_Module", "Mic_Array", "Pi_5", "Pi_Tray", "Speaker_Driver_L",
                "Speaker_Driver_R", "Reserve_USB_Hub"}
    for o in list(parts.values()) + face:
        o.hide_render = o.name not in internal
    cam.location = (0.5, -0.9, 0.55)
    scene.render.filepath = os.path.join(out, "JARVIS_internals_render.png")
    bpy.ops.render.render(write_still=True)
print("scene built; parts:", len(parts))
