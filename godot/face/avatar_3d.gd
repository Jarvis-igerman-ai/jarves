extends SubViewportContainer
## Low-poly 3D avatar head (appearance "avatar3d"): faceted primitives built in code (flat-shaded SphereMesh /
## CylinderMesh / BoxMesh, no imported assets), rendered in its own SubViewport + World3D so it never touches the
## 3D twin scene. Like avatar_2d.gd it owns NO motion: it draws face.gd's pose() every frame, so blink timing,
## gaze, breathing, reactions and the speech-level mouth are the face's own (calm) behaviour.
## The head turns at most a few degrees with the gaze; the eyes do the looking. Swap the look via STYLE or replace
## the meshes with a Blender .glb later: the contract is only `face` + `apply_pose(pose)`.

const STYLE := {
	"shell": Color(0.8, 0.84, 0.9), "visor": Color(0.05, 0.07, 0.11), "trim": Color(0.55, 0.6, 0.68),
	"pupil": Color(0.03, 0.04, 0.07), "blush": Color(1.0, 0.45, 0.6),
	"yaw_per_look": 0.16, "pitch_per_look": 0.12,     # rad per look unit (idle look <= ~0.25 -> <= ~2.3 deg)
	"breath_y": 0.006, "eye_glow": 1.6,
}
const MOUTH_SEGS := 11

var face: Control
var p := {}
var head: Node3D
var eyes: Array[MeshInstance3D] = []
var pupils: Array[MeshInstance3D] = []
var brows: Array[MeshInstance3D] = []
var cheeks: Array[MeshInstance3D] = []
var mouth_top: Array[MeshInstance3D] = []
var mouth_bot: Array[MeshInstance3D] = []
var antenna_tip: MeshInstance3D
var ears: Array[MeshInstance3D] = []
var eye_mat: StandardMaterial3D
var mouth_mat: StandardMaterial3D
var glow_mat: StandardMaterial3D
var blush_mat: StandardMaterial3D
var viewport: SubViewport
# derived values (read by the self-test)
var eye_open := 1.0
var head_rot := Vector2.ZERO               # (pitch, yaw) rad
var mouth_gap := 0.0


func _init() -> void:
	stretch = true
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	viewport = SubViewport.new()
	viewport.own_world_3d = true
	viewport.transparent_bg = true
	viewport.msaa_3d = Viewport.MSAA_2X
	add_child(viewport)
	_build()


func _process(_delta: float) -> void:
	if face != null and visible:
		apply_pose(face.pose())


static func faceted(mesh: PrimitiveMesh) -> ArrayMesh:
	# flat shading: every triangle gets its own normal (the low-poly look), pointing away from the mesh centre
	var arr := mesh.get_mesh_arrays()
	var v: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
	var idx: PackedInt32Array = arr[Mesh.ARRAY_INDEX]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in range(0, idx.size() - 2, 3):
		var a := v[idx[i]]
		var b := v[idx[i + 1]]
		var c := v[idx[i + 2]]
		var n := (b - a).cross(c - a)
		if n.length() < 1e-8:
			continue
		n = n.normalized()
		if n.dot((a + b + c) / 3.0) < 0.0:
			n = -n
		st.set_normal(n)
		st.add_vertex(a)
		st.set_normal(n)
		st.add_vertex(b)
		st.set_normal(n)
		st.add_vertex(c)
	return st.commit()


static func _mat(col: Color, rough := 0.7, emit := 0.0) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = col
	m.roughness = rough
	if emit > 0.0:
		m.emission_enabled = true
		m.emission = col
		m.emission_energy_multiplier = emit
	return m


func _part(parent: Node3D, mesh: Mesh, mat: Material, pos: Vector3, scl := Vector3.ONE) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = mat
	mi.position = pos
	mi.scale = scl
	parent.add_child(mi)
	return mi


func _sphere(r: float, h: float, seg: int, rings: int) -> ArrayMesh:
	var s := SphereMesh.new()
	s.radius = r
	s.height = h
	s.radial_segments = seg
	s.rings = rings
	return faceted(s)


func _build() -> void:
	var root := Node3D.new()
	viewport.add_child(root)
	var env := WorldEnvironment.new()
	env.environment = Environment.new()
	env.environment.background_mode = Environment.BG_CLEAR_COLOR
	env.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.environment.ambient_light_color = Color(0.4, 0.45, 0.55)
	env.environment.ambient_light_energy = 0.55
	env.environment.glow_enabled = true
	env.environment.glow_intensity = 0.6
	root.add_child(env)
	var cam := Camera3D.new()
	cam.position = Vector3(0, 0.02, 3.3)
	cam.fov = 32.0
	root.add_child(cam)
	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-30, 28, 0)
	key.light_energy = 1.15
	root.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-10, -140, 0)
	fill.light_energy = 0.35
	fill.light_color = Color(0.6, 0.75, 1.0)
	root.add_child(fill)
	# neck / shoulders hint (static)
	var body := CylinderMesh.new()
	body.top_radius = 0.22
	body.bottom_radius = 0.5
	body.height = 0.36
	body.radial_segments = 9
	body.rings = 1
	_part(root, faceted(body), _mat(STYLE.trim, 0.8), Vector3(0, -0.83, 0))
	head = Node3D.new()
	root.add_child(head)
	var shell := _mat(STYLE.shell, 0.65)
	_part(head, _sphere(0.62, 1.16, 10, 6), shell, Vector3.ZERO)
	var visor := _mat(STYLE.visor, 0.22)
	visor.metallic = 0.25
	_part(head, _sphere(0.5, 0.78, 12, 6), visor, Vector3(0, 0.0, 0.22), Vector3(1.0, 1.0, 0.9))
	eye_mat = _mat(Color(0.36, 0.78, 1.0), 0.4, STYLE.eye_glow)
	mouth_mat = _mat(Color(0.7, 0.9, 1.0), 0.4, 1.2)
	glow_mat = _mat(Color(0.36, 0.78, 1.0), 0.4, 0.6)
	blush_mat = _mat(STYLE.blush, 0.9, 0.8)
	blush_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	var pupil_mat := _mat(STYLE.pupil, 0.3)
	var eye_mesh := _sphere(0.085, 0.17, 10, 5)
	var pupil_mesh := _sphere(0.036, 0.072, 8, 4)
	var brow_mesh := BoxMesh.new()
	brow_mesh.size = Vector3(0.15, 0.022, 0.03)
	var cheek_mesh := _sphere(0.06, 0.04, 8, 3)
	for sx in [-1.0, 1.0]:
		eyes.append(_part(head, eye_mesh, eye_mat, Vector3(sx * 0.19, 0.07, 0.6)))
		pupils.append(_part(head, pupil_mesh, pupil_mat, Vector3(sx * 0.19, 0.07, 0.68)))
		brows.append(_part(head, brow_mesh, _mat(STYLE.trim, 0.6), Vector3(sx * 0.19, 0.23, 0.6)))
		cheeks.append(_part(head, cheek_mesh, blush_mat, Vector3(sx * 0.33, -0.08, 0.5)))
		var ear := CylinderMesh.new()
		ear.top_radius = 0.11
		ear.bottom_radius = 0.13
		ear.height = 0.1
		ear.radial_segments = 8
		ear.rings = 1
		var e := _part(head, faceted(ear), glow_mat, Vector3(sx * 0.6, 0.02, 0))
		e.rotation_degrees = Vector3(0, 0, 90)
		ears.append(e)
	var seg := BoxMesh.new()
	seg.size = Vector3(0.036, 0.016, 0.016)       # overlapping segments read as one bent light strip
	for i in MOUTH_SEGS:
		mouth_top.append(_part(head, seg, mouth_mat, Vector3.ZERO))
		mouth_bot.append(_part(head, seg, mouth_mat, Vector3.ZERO))
	var stalk := CylinderMesh.new()
	stalk.top_radius = 0.015
	stalk.bottom_radius = 0.02
	stalk.height = 0.18
	stalk.radial_segments = 6
	_part(head, stalk, _mat(STYLE.trim, 0.6), Vector3(0, 0.64, 0))
	antenna_tip = _part(head, _sphere(0.045, 0.09, 8, 4), glow_mat, Vector3(0, 0.75, 0))


func apply_pose(pose: Dictionary) -> void:
	p = pose
	var look: Vector2 = pose.get("look", Vector2.ZERO)
	var t: float = float(pose.get("t", 0.0))
	var col: Color = pose.get("col", Color(0.36, 0.78, 1.0))
	var bright: float = float(pose.get("bright", 1.0))
	var asleep: bool = pose.get("asleep", false)
	var bounce: float = float(pose.get("laugh", 0.0)) * 0.012 * (0.5 - 0.5 * cos(TAU * 2.0 * t))
	head_rot = Vector2(look.y * STYLE.pitch_per_look, look.x * STYLE.yaw_per_look)   # look +y = down, +x = right
	head.rotation = Vector3(head_rot.x, head_rot.y, 0.0)
	head.position = Vector3(0, STYLE.breath_y * float(pose.get("breath", 0.0)) + bounce, 0)
	eye_open = clampf(float(pose.get("open", 1.0)) * (1.0 - float(pose.get("sleep", 0.0))), 0.0, 1.3)
	var curve: float = float(pose.get("curve", 0.0))
	var lid_low: Vector2 = pose.get("lid_low", Vector2.ZERO)
	var lid_up: Vector2 = pose.get("lid_up", Vector2.ZERO)
	var tilt: Vector2 = pose.get("lid_tilt", Vector2.ZERO)
	for i in 2:
		var sx := -1.0 if i == 0 else 1.0
		var squint: float = (lid_low.x if i == 0 else lid_low.y) + curve * 0.4
		var h := maxf(eye_open * (1.0 - clampf(squint, 0.0, 0.8)), 0.06)
		eyes[i].scale = Vector3(1.0 + curve * 0.15, h, 0.6)
		eyes[i].position.y = 0.07 - (1.0 - h) * 0.02 + curve * 0.01
		var off := Vector2(clampf(look.x * 0.05, -0.04, 0.04), clampf(-look.y * 0.04, -0.035, 0.035))
		pupils[i].position = Vector3(sx * 0.19 + off.x, eyes[i].position.y + off.y, 0.66)
		pupils[i].visible = h > 0.3 and curve < 0.5 and not asleep
		pupils[i].scale = Vector3.ONE * clampf(float(pose.get("iris", 1.0)), 0.4, 1.2)
		var up: float = lid_up.x if i == 0 else lid_up.y
		var tl: float = tilt.x if i == 0 else tilt.y
		brows[i].position = Vector3(sx * 0.19, 0.23 - up * 0.08, 0.6)
		brows[i].rotation = Vector3(0, 0, sx * tl * 0.35)
	eye_mat.albedo_color = col
	eye_mat.emission = col
	eye_mat.emission_energy_multiplier = STYLE.eye_glow * bright * float(pose.get("glow", 1.0)) * (0.35 if asleep else 1.0)
	var ring: float = clampf(float(pose.get("ring", 0.0)) + float(pose.get("spin", 0.0)) * (0.3 + 0.3 * sin(t * 1.2)), 0.0, 1.5)
	glow_mat.albedo_color = col
	glow_mat.emission = col
	glow_mat.emission_energy_multiplier = (0.25 + ring) * bright
	blush_mat.albedo_color = Color(STYLE.blush, float(pose.get("blush", 0.0)) * 0.6)
	blush_mat.emission_energy_multiplier = float(pose.get("blush", 0.0)) * 0.8
	# mouth: two rows of segments along a curve; the lower row drops with the speech level
	var w: float = float(pose.get("mouth_w", 0.085)) * 1.7
	var smile: float = float(pose.get("smile", 0.0))
	mouth_gap = float(pose.get("mouth_open", 0.0)) * 0.1 + float(pose.get("mouth_o", 0.0)) * 0.05
	mouth_mat.emission = col.lerp(Color.WHITE, 0.5)
	mouth_mat.emission_energy_multiplier = (0.4 if asleep else 1.2) * bright
	for i in MOUTH_SEGS:
		var x := -1.0 + 2.0 * i / (MOUTH_SEGS - 1)
		var bend := 1.0 - x * x
		var y := -0.17 - smile * 0.05 * bend
		var px := x * w + float(pose.get("mouth_x", 0.0)) * 0.6
		var z := 0.22 + 0.9 * sqrt(maxf(0.25 - px * px - y * y, 0.0)) + 0.008    # on the visor surface
		mouth_top[i].position = Vector3(px, y, z)
		mouth_top[i].rotation = Vector3(0, px * 1.6, atan(smile * 0.1 * x / maxf(w, 0.01)))
		mouth_bot[i].position = Vector3(px, y - mouth_gap * bend, z)
		mouth_bot[i].rotation = mouth_top[i].rotation
		mouth_bot[i].visible = mouth_gap > 0.004
	antenna_tip.scale = Vector3.ONE * (1.0 + 0.15 * clampf(ring, 0.0, 1.0))
