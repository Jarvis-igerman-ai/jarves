extends SubViewportContainer
## 3D avatar (appearance "avatar3d"): a cute, clearly non-human robot buddy. A smooth rounded head whose shader
## (avatar_3d_head.gdshader) paints a soft matte shell and a dark glass visor with glowing eye panels and mouth,
## plus ear pods and an antenna, all built from primitive meshes in code (no imported assets) and
## rendered in its own SubViewport + World3D so it never touches the 3D twin scene. Soft wrapped lighting, no
## faceting, no skin, no eyeballs: the first low-poly human-ish head read as uncanny (owner feedback, SW-V2).
## Like avatar_2d.gd it owns NO motion: it draws face.gd's pose() every frame, so blink timing, gaze, breathing,
## reactions and the speech-level mouth are the face's own (calm) behaviour. The head turns at most a few degrees
## with the gaze; the eye panels do the looking. Restyle via STYLE or swap the meshes for a Blender .glb later:
## the contract is only `face` + `apply_pose(pose)`.

const HEAD_SHADER := preload("res://face/avatar_3d_head.gdshader")
const STYLE := {
	"shell": Color(0.86, 0.88, 0.93), "visor": Color(0.025, 0.035, 0.06), "trim": Color(0.62, 0.67, 0.76),
	"head_scale": Vector3(1.12, 0.96, 1.0), "head_r": 0.62,       # rounded, wider than tall (friendly, not human)
	"visor_r": Vector2(0.72, 0.54), "visor_y": 0.02,              # visor half size / offset (unit = head radius)
	"eye_pos": Vector2(0.27, -0.06), "eye_half": Vector2(0.095, 0.15), "look_shift": 0.05, "eye_energy": 2.2,
	"mouth_y": 0.27, "mouth_scale": 1.9, "relax_low": 0.06,
	"blush_col": Color(1.0, 0.45, 0.62), "heart_col": Color(1.0, 0.38, 0.55),
	"yaw_per_look": 0.16, "pitch_per_look": 0.12,     # rad per look unit (idle look <= ~0.25 -> <= ~2.3 deg)
	"breath_y": 0.006,
}

var face: Control
var p := {}
var head: Node3D
var head_mat: ShaderMaterial
var glow_mat: StandardMaterial3D               # ear caps + antenna bulb (accent glow)
var antenna_tip: MeshInstance3D
var viewport: SubViewport
# derived values (read by the self-test)
var eye_open := 1.0
var eye_h := 1.0                               # drawn eye panel height / full (squint + blink)
var eye_col := Color(0.36, 0.78, 1.0)          # current panel colour
var head_rot := Vector2.ZERO                   # (pitch, yaw) rad
var mouth_gap := 0.0


func _init() -> void:
	stretch = true
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	viewport = SubViewport.new()
	viewport.own_world_3d = true
	viewport.transparent_bg = true
	viewport.msaa_3d = Viewport.MSAA_4X
	add_child(viewport)
	_build()


func _process(_delta: float) -> void:
	if face != null and visible:
		apply_pose(face.pose())


static func _mat(col: Color, rough := 0.55, emit := 0.0) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = col
	m.roughness = rough
	m.diffuse_mode = BaseMaterial3D.DIFFUSE_LAMBERT_WRAP
	m.rim_enabled = true
	m.rim = 0.3
	m.rim_tint = 0.6
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


static func _sphere(r: float, seg := 48, rings := 24) -> SphereMesh:
	var s := SphereMesh.new()
	s.radius = r
	s.height = 2.0 * r
	s.radial_segments = seg
	s.rings = rings
	return s


static func _cyl(r_top: float, r_bot: float, h: float) -> CylinderMesh:
	var c := CylinderMesh.new()
	c.top_radius = r_top
	c.bottom_radius = r_bot
	c.height = h
	c.radial_segments = 32
	c.rings = 1
	return c


func _build() -> void:
	var root := Node3D.new()
	viewport.add_child(root)
	var env := WorldEnvironment.new()
	env.environment = Environment.new()
	env.environment.background_mode = Environment.BG_CLEAR_COLOR
	env.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.environment.ambient_light_color = Color(0.55, 0.6, 0.75)
	env.environment.ambient_light_energy = 0.6
	env.environment.glow_enabled = true
	env.environment.glow_intensity = 0.55
	env.environment.glow_bloom = 0.04
	root.add_child(env)
	var cam := Camera3D.new()
	cam.position = Vector3(0, 0.06, 3.5)
	cam.fov = 31.0
	root.add_child(cam)
	# gentle three-point light: soft warm key, cool fill, blue rim from behind for the silhouette
	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-32, -24, 0)
	key.light_energy = 0.9
	key.light_color = Color(1.0, 0.97, 0.92)
	root.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-8, 40, 0)
	fill.light_energy = 0.35
	fill.light_color = Color(0.7, 0.8, 1.0)
	root.add_child(fill)
	var rim := DirectionalLight3D.new()
	rim.rotation_degrees = Vector3(-25, 180, 0)
	rim.light_energy = 0.8
	rim.light_color = Color(0.55, 0.75, 1.0)
	root.add_child(rim)
	var shell := _mat(STYLE.shell, 0.55)
	head = Node3D.new()
	root.add_child(head)
	head_mat = ShaderMaterial.new()
	head_mat.shader = HEAD_SHADER
	for k in ["shell", "visor", "visor_r", "visor_y", "eye_pos", "eye_half", "look_shift", "eye_energy", "mouth_y",
			"mouth_scale", "relax_low", "blush_col", "heart_col"]:
		head_mat.set_shader_parameter(k, STYLE[k])
	var hr: float = STYLE.head_r
	var hs: Vector3 = STYLE.head_scale
	_part(head, _sphere(1.0, 96, 48), head_mat, Vector3.ZERO, hs * hr)
	glow_mat = _mat(Color(0.36, 0.78, 1.0), 0.4, 0.8)
	for sx in [-1.0, 1.0]:
		var pod := _part(head, _cyl(0.13, 0.15, 0.12), _mat(STYLE.trim, 0.5), Vector3(sx * hr * hs.x * 0.97, -0.02, 0))
		pod.rotation_degrees = Vector3(0, 0, 90)
		var cap := _part(head, _cyl(0.085, 0.085, 0.13), glow_mat, Vector3(sx * hr * hs.x * 1.0, -0.02, 0))
		cap.rotation_degrees = Vector3(0, 0, 90)
	_part(head, _cyl(0.014, 0.02, 0.17), _mat(STYLE.trim, 0.5), Vector3(0, hr * hs.y + 0.06, 0))
	antenna_tip = _part(head, _sphere(0.045, 24, 12), glow_mat, Vector3(0, hr * hs.y + 0.16, 0))


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
	eye_h = maxf(eye_open * (1.0 - clampf(lid_low.x + curve * 0.4, 0.0, 0.8)), 0.0)
	eye_col = col
	mouth_gap = float(pose.get("mouth_open", 0.0)) * 0.1 + float(pose.get("mouth_o", 0.0)) * 0.05
	var ring: float = clampf(float(pose.get("ring", 0.0)) + float(pose.get("spin", 0.0)) * (0.3 + 0.3 * sin(t * 1.2)), 0.0, 1.5)
	var v := {
		"t": t, "accent": col, "bright": bright, "glow": float(pose.get("glow", 1.0)) * (0.45 if asleep else 1.0),
		"look": look, "eye_open": eye_open, "lid_low": lid_low, "lid_up": pose.get("lid_up", Vector2.ZERO),
		"lid_tilt": pose.get("lid_tilt", Vector2.ZERO), "curve": curve, "smile": float(pose.get("smile", 0.0)),
		"mouth_gap": float(pose.get("mouth_open", 0.0)) * 0.11, "mouth_w": float(pose.get("mouth_w", 0.085)),
		"mouth_x": float(pose.get("mouth_x", 0.0)), "mouth_o": clampf(float(pose.get("mouth_o", 0.0)), 0.0, 1.0),
		"blush": float(pose.get("blush", 0.0)), "cry": float(pose.get("cry", 0.0)), "heart": float(pose.get("heart", 0.0)),
		"ring": ring, "asleep": 1.0 if asleep else 0.0,
	}
	for k in v:
		head_mat.set_shader_parameter(k, v[k])
	glow_mat.albedo_color = col
	glow_mat.emission = col
	glow_mat.emission_energy_multiplier = (0.35 + ring) * bright
	antenna_tip.scale = Vector3.ONE * (1.0 + 0.15 * clampf(ring, 0.0, 1.0))
