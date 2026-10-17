# JARVES S1 design review (Issue #3): three industrial-design CONCEPT previews. Visual only -
# not CAD, not a fit claim. Sizes are approximate and PROVISIONAL; internals must be re-checked in FreeCAD.
# Run: blender -b --python design/concepts/build_concepts.py
# Writes design/concepts/concept_{A,B,C}_views.png (front/side/rear, orthographic) and concepts_hero.png.
import math
import os
import bmesh
import bpy

out = os.path.dirname(os.path.abspath(__file__))
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ---------------- materials ----------------
def mat(name, color, rough=0.4, coat=0.0, metal=0.0, emit=None, strength=0.0, sheen=0.0):
    m = bpy.data.materials.new(name)
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    b.inputs["Metallic"].default_value = metal
    b.inputs["Sheen Weight"].default_value = sheen
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


def _bump(m, scale, strength):
    nt = m.node_tree
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = scale
    bp = nt.nodes.new("ShaderNodeBump")
    bp.inputs["Strength"].default_value = strength
    nt.links.new(n.outputs["Fac"], bp.inputs["Height"])
    nt.links.new(bp.outputs["Normal"], nt.nodes["Principled BSDF"].inputs["Normal"])


def fabric(name, color):
    m = mat(name, color, rough=0.95, sheen=0.6)
    _bump(m, 900, 0.25)
    return m


def _ramp(m, fac_socket, c0, c1, pos):
    nt = m.node_tree
    r = nt.nodes.new("ShaderNodeValToRGB")
    r.color_ramp.interpolation = "CONSTANT"
    r.color_ramp.elements[0].color = (*c0, 1)
    r.color_ramp.elements[1].position = pos
    r.color_ramp.elements[1].color = (*c1, 1)
    nt.links.new(fac_socket, r.inputs["Fac"])
    nt.links.new(r.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])


def grille(name, base, hole, scale):  # regular perforation pattern
    m = mat(name, base, rough=0.5)
    nt = m.node_tree
    tc = nt.nodes.new("ShaderNodeTexCoord")
    v = nt.nodes.new("ShaderNodeTexVoronoi")
    v.inputs["Scale"].default_value = scale
    v.inputs["Randomness"].default_value = 0.0
    nt.links.new(tc.outputs["Object"], v.inputs["Vector"])
    _ramp(m, v.outputs["Distance"], hole, base, 0.3)
    return m


def wood(name):
    m = mat(name, (0.3, 0.17, 0.08), rough=0.45, coat=0.4)
    nt = m.node_tree
    w = nt.nodes.new("ShaderNodeTexWave")
    w.inputs["Scale"].default_value = 18
    w.inputs["Distortion"].default_value = 6
    r = nt.nodes.new("ShaderNodeValToRGB")
    r.color_ramp.elements[0].color = (0.22, 0.11, 0.05, 1)
    r.color_ramp.elements[1].color = (0.42, 0.25, 0.12, 1)
    nt.links.new(w.outputs["Fac"], r.inputs["Fac"])
    nt.links.new(r.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    return m


GLASS = mat("Screen_Glass", (0.004, 0.005, 0.01), rough=0.18, coat=0.6)
RIM = mat("Screen_Rim", (0.78, 0.8, 0.84), rough=0.2, metal=0.9)
DARK = mat("Port_Dark", (0.02, 0.02, 0.025), rough=0.5)


# ---------------- geometry helpers ----------------
def _finish(o, coll, material, smooth_angle=None):
    for c in list(o.users_collection):
        c.objects.unlink(o)
    coll.objects.link(o)
    if material:
        o.data.materials.append(material)
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    if smooth_angle is None:
        bpy.ops.object.shade_smooth()
    else:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(smooth_angle))
    o.select_set(False)
    return o


def _apply(o, bevel=0.0, segs=8):
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        m = o.modifiers.new("Bevel", "BEVEL")
        m.width, m.segments, m.limit_method = bevel, segs, "ANGLE"
        bpy.ops.object.modifier_apply(modifier=m.name)
    o.select_set(False)


def ellipsoid(coll, name, material, r, loc):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=1, location=loc)
    o = bpy.context.object
    o.name, o.scale = name, r
    _apply(o)
    return _finish(o, coll, material)


def rbox(coll, name, material, half, loc, bevel):
    bpy.ops.mesh.primitive_cube_add(size=2, location=loc)
    o = bpy.context.object
    o.name, o.scale = name, half
    _apply(o, bevel)
    return _finish(o, coll, material, 35)


def cyl(coll, name, material, r, depth, loc, rot=(0, 0, 0), bevel=0.0, r2=None):
    if r2 is None:
        bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=r, depth=depth, location=loc, rotation=rot)
    else:
        bpy.ops.mesh.primitive_cone_add(vertices=128, radius1=r, radius2=r2, depth=depth, location=loc, rotation=rot)
    o = bpy.context.object
    o.name = name
    _apply(o, bevel)
    return _finish(o, coll, material, 35)


def torus(coll, name, material, major, minor, loc, rot=(0, 0, 0), lower_half=False):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, major_segments=96,
                                     minor_segments=24, location=loc, rotation=rot)
    o = bpy.context.object
    o.name = name
    if lower_half:  # smile: keep the arc below centre (local +y maps to world +z for rot x=90)
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.y > 0.15 * major], context="VERTS")
        bm.to_mesh(o.data)
        bm.free()
    return _finish(o, coll, material)


def cap(coll, name, material, r, loc, radius, grow):
    """Spherical-cap patch: ellipsoid surface (scaled by grow) inside a front-facing cylinder of `radius`."""
    o = ellipsoid(coll, name, material, tuple(x * grow for x in r), loc)
    bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=radius, depth=0.2, location=(loc[0], loc[1] - 0.1, loc[2]),
                                        rotation=(math.pi / 2, 0, 0))
    cutter = bpy.context.object
    m = o.modifiers.new("Cap", "BOOLEAN")
    m.operation, m.object, m.solver = "INTERSECT", cutter, "EXACT"
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cutter)
    return o


def surf_y(r, x, dz, grow=1.0):  # front surface y of an ellipsoid at local (x, dz)
    rx, ry, rz = (v * grow for v in r)
    return -ry * math.sqrt(max(0.0, 1 - (x / rx) ** 2 - (dz / rz) ** 2))


def face(coll, prefix, head_r, hz, eye, emit_mat, grow, eye_dx=0.03, cheeks=None, flat_y=None):
    """Eyes + smile on a domed (ellipsoid) or flat (flat_y) screen."""
    sx, sz = eye
    for side, x in (("L", -eye_dx), ("R", eye_dx)):
        y = flat_y if flat_y is not None else surf_y(head_r, x, 0.012, grow)
        ellipsoid(coll, f"{prefix}_Eye_{side}", emit_mat, (sx, 0.004, sz), (x, y, hz + 0.012))
    y = flat_y if flat_y is not None else surf_y(head_r, 0, -0.028, grow)
    torus(coll, f"{prefix}_Smile", emit_mat, 0.014, 0.0022, (0, y - 0.001, hz - 0.018), rot=(math.pi / 2, 0, 0),
          lower_half=True)
    if cheeks:
        for side, x in (("L", -0.052), ("R", 0.052)):
            y = flat_y if flat_y is not None else surf_y(head_r, x, -0.012, grow)
            ellipsoid(coll, f"{prefix}_Cheek_{side}", cheeks, (0.009, 0.003, 0.005), (x, y, hz - 0.012))


def mic_holes(coll, prefix, z_top, ring=0.03):
    for i in range(4):
        a = i * math.pi / 2 + math.pi / 4
        cyl(coll, f"{prefix}_MicPort_{i}", DARK, 0.0018, 0.002, (ring * math.cos(a), ring * math.sin(a), z_top))


def new_coll(name):
    c = bpy.data.collections.new(name)
    scene.collection.children.link(c)
    return c


# ---------------- Concept A: "Pebble" — pearl-white orb on a fabric puck ----------------
def concept_a():
    c = new_coll("Concept_A")
    white = mat("A_Pearl", (0.93, 0.92, 0.9), rough=0.22, coat=0.8)
    grey_fab = fabric("A_Fabric", (0.33, 0.32, 0.31))
    silver = mat("A_Satin", (0.78, 0.78, 0.8), rough=0.3, metal=0.85)
    blue = mat("A_FaceBlue", (0.2, 0.5, 0.9), rough=0.3, emit=(0.3, 0.72, 1.0), strength=10)
    ring = mat("A_Ring", (0.3, 0.7, 1.0), rough=0.3, emit=(0.35, 0.75, 1.0), strength=6)
    cyl(c, "A_Base", white, 0.095, 0.085, (0, 0, 0.0425), bevel=0.03)
    cyl(c, "A_FabricBand", grey_fab, 0.0962, 0.034, (0, 0, 0.044))
    torus(c, "A_LightRing", ring, 0.082, 0.0025, (0, 0, 0.004))
    cyl(c, "A_Neck", silver, 0.03, 0.03, (0, 0, 0.096), bevel=0.004)
    hr, hz = (0.104, 0.088, 0.088), 0.187
    ellipsoid(c, "A_Head", white, hr, (0, 0, hz))
    cap(c, "A_ScreenRim", RIM, hr, (0, 0, hz), 0.077, 1.003)
    cap(c, "A_Screen", GLASS, hr, (0, 0, hz), 0.072, 1.006)
    face(c, "A", hr, hz, (0.011, 0.017), blue, 1.006)
    ellipsoid(c, "A_Camera", DARK, (0.0045, 0.003, 0.0045), (0, surf_y(hr, 0, 0.078), hz + 0.078))
    mic_holes(c, "A", hz + 0.088 - 0.0005)
    # rear: service door, vents, USB-C power inlet (base), head rear vent slots
    rbox(c, "A_RearDoor", mat("A_Door", (0.86, 0.85, 0.83), rough=0.35), (0.045, 0.004, 0.022), (0, 0.093, 0.045), 0.006)
    for i in range(4):
        rbox(c, f"A_Vent_{i}", DARK, (0.012, 0.003, 0.0016), (0, 0.0965, 0.032 + i * 0.008), 0.0012)
    rbox(c, "A_USBC", DARK, (0.0045, 0.003, 0.0016), (0.03, 0.0965, 0.03), 0.0012)
    return c


# ---------------- Concept B: "Lantern" — soft-square head, fabric column, walnut plinth ----------------
def concept_b():
    c = new_coll("Concept_B")
    warm = mat("B_WarmWhite", (0.9, 0.87, 0.82), rough=0.5, coat=0.2)
    oat = fabric("B_Oat", (0.6, 0.52, 0.42))
    brass = mat("B_Brass", (0.82, 0.64, 0.38), rough=0.3, metal=1.0)
    amber = mat("B_FaceAmber", (0.9, 0.6, 0.3), rough=0.3, emit=(1.0, 0.68, 0.35), strength=10)
    cyl(c, "B_Plinth", wood("B_Walnut"), 0.096, 0.016, (0, 0, 0.008), bevel=0.004)
    cyl(c, "B_Column", oat, 0.09, 0.096, (0, 0, 0.064), r2=0.078)
    cyl(c, "B_ColumnCap", warm, 0.079, 0.008, (0, 0, 0.116), bevel=0.003)
    cyl(c, "B_Neck", brass, 0.034, 0.012, (0, 0, 0.126), bevel=0.003)
    hz, hy = 0.226, 0.065
    rbox(c, "B_Head", warm, (0.1, hy, 0.094), (0, 0, hz), 0.022)
    cyl(c, "B_ScreenRim", brass, 0.077, 0.002, (0, -hy - 0.0005, hz), rot=(math.pi / 2, 0, 0))
    cyl(c, "B_Screen", GLASS, 0.072, 0.0025, (0, -hy - 0.0012, hz), rot=(math.pi / 2, 0, 0))
    face(c, "B", None, hz, (0.012, 0.016), amber, 1.0, flat_y=-hy - 0.0025)
    ellipsoid(c, "B_Camera", DARK, (0.0045, 0.002, 0.0045), (0, -hy - 0.0003, hz + 0.084))
    mic_holes(c, "B", hz + 0.094 - 0.0005, ring=0.035)
    rbox(c, "B_RearDoor", warm, (0.05, 0.004, 0.03), (0, 0.083, 0.062), 0.006)
    for i in range(5):
        rbox(c, f"B_Vent_{i}", DARK, (0.03, 0.003, 0.0015), (0, 0.0865, 0.045 + i * 0.008), 0.0012)
    rbox(c, "B_USBC", DARK, (0.0045, 0.003, 0.0016), (0, 0.095, 0.008), 0.0012)
    for i in range(4):
        rbox(c, f"B_HeadVent_{i}", DARK, (0.05, 0.003, 0.0015), (0, hy + 0.0005, hz - 0.03 + i * 0.009), 0.0012)
    return c


# ---------------- Concept C: "Buddy" — character head with ear pods, pastel two-tone ----------------
def concept_c():
    c = new_coll("Concept_C")
    white = mat("C_White", (0.95, 0.95, 0.95), rough=0.25, coat=0.7)
    sky = mat("C_Sky", (0.5, 0.7, 0.93), rough=0.45, coat=0.3)
    navy = grille("C_Grille", (0.18, 0.28, 0.45), (0.03, 0.04, 0.07), 260)
    cyan = mat("C_FaceCyan", (0.2, 0.6, 0.9), rough=0.3, emit=(0.35, 0.85, 1.0), strength=10)
    pink = mat("C_Cheek", (1.0, 0.5, 0.6), rough=0.4, emit=(1.0, 0.45, 0.55), strength=3)
    glow = mat("C_EarGlow", (0.4, 0.85, 1.0), rough=0.3, emit=(0.4, 0.85, 1.0), strength=5)
    br, bz = (0.092, 0.082, 0.052), 0.047
    ellipsoid(c, "C_Base", sky, br, (0, 0, bz))
    cap(c, "C_Grille", navy, br, (0, 0, bz), 0.04, 1.004)
    cyl(c, "C_Neck", white, 0.026, 0.02, (0, 0, 0.1), bevel=0.004)
    hr, hz = (0.1, 0.09, 0.093), 0.19
    ellipsoid(c, "C_Head", white, hr, (0, 0, hz))
    cap(c, "C_ScreenRim", RIM, hr, (0, 0, hz), 0.077, 1.003)
    cap(c, "C_Screen", GLASS, hr, (0, 0, hz), 0.072, 1.006)
    face(c, "C", hr, hz, (0.014, 0.02), cyan, 1.006, eye_dx=0.032, cheeks=pink)
    ellipsoid(c, "C_Camera", DARK, (0.0045, 0.003, 0.0045), (0, surf_y(hr, 0, 0.08), hz + 0.08))
    for side, sx in (("L", -1), ("R", 1)):
        cyl(c, f"C_Ear_{side}", sky, 0.032, 0.024, (sx * 0.1, 0, hz), rot=(0, math.pi / 2, 0), bevel=0.008)
        torus(c, f"C_EarRing_{side}", glow, 0.019, 0.0025, (sx * 0.1125, 0, hz), rot=(0, math.pi / 2, 0))
    mic_holes(c, "C", hz + 0.093 - 0.0005)
    rbox(c, "C_RearDoor", white, (0.04, 0.004, 0.018), (0, 0.078, 0.045), 0.006)
    for i in range(3):
        rbox(c, f"C_Vent_{i}", DARK, (0.012, 0.003, 0.0015), (0, 0.0815, 0.036 + i * 0.008), 0.0012)
    rbox(c, "C_USBC", DARK, (0.0045, 0.003, 0.0016), (0.026, 0.0815, 0.036), 0.0012)
    return c


concepts = {"A": concept_a(), "B": concept_b(), "C": concept_c()}
for c in concepts.values():  # sources are only rendered through instances
    bpy.context.view_layer.layer_collection.children[c.name].exclude = True

# ---------------- studio ----------------
bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 0, 0))
bpy.context.object.data.materials.append(mat("Floor", (0.5, 0.49, 0.48), rough=0.7))
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
    o = bpy.data.objects.new(name, d)
    scene.collection.objects.link(o)
    o.location = loc
    t = o.constraints.new("TRACK_TO")
    t.target, t.track_axis, t.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"


light("Key", (0.9, -1.4, 1.3), 85, 1.2, (1.0, 0.96, 0.92))
light("Fill", (-1.3, -0.9, 0.5), 30, 1.5, (0.9, 0.94, 1.0))
light("Rim", (0.2, 1.4, 1.1), 60, 1.0)
light("RearFill", (-0.6, 1.2, 0.4), 25, 1.2)
cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
scene.collection.objects.link(cam)
scene.camera = cam
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 40
scene.cycles.use_denoising = True
scene.view_settings.look = "AgX - Medium High Contrast"


def instance(coll, x, rot_z):
    e = bpy.data.objects.new(f"inst_{coll.name}_{x:.2f}", None)
    e.instance_type, e.instance_collection = "COLLECTION", coll
    e.location, e.rotation_euler = (x, 0, 0), (0, 0, rot_z)
    scene.collection.objects.link(e)
    return e


def render(path, insts, w, h):
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    for e in insts:
        bpy.data.objects.remove(e)


# orthographic front / side (+X face) / rear strips
cam.data.type, cam.data.ortho_scale = "ORTHO", 1.02
cam.location, cam.rotation_euler = (0, -2.0, 0.155), (math.pi / 2, 0, 0)
for key, coll in concepts.items():
    insts = [instance(coll, -0.33, 0), instance(coll, 0, -math.pi / 2), instance(coll, 0.33, math.pi)]
    render(os.path.join(out, f"concept_{key}_views.png"), insts, 1800, 640)

# hero: all three, 3/4 perspective
cam.data.type, cam.data.lens = "PERSP", 50
cam.location = (0.55, -1.55, 0.48)
cam.rotation_euler = (0, 0, 0)
t = cam.constraints.new("TRACK_TO")
t.target, t.track_axis, t.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
target.location = (0, 0, 0.13)
insts = [instance(concepts[k], x, math.radians(-12)) for k, x in (("A", -0.3), ("B", 0.0), ("C", 0.3))]
render(os.path.join(out, "concepts_hero.png"), insts, 1800, 900)
print("CONCEPTS DONE")
