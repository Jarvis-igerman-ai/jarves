extends Node3D
# JARVIS S1.T03 review viewer. Loads the CAD-derived GLB (metres, part names = CAD names).
# Left-drag orbit | right/middle-drag pan | wheel zoom | click selects a part.
# CLI (after "--"):  --check  (print PASS/FAIL, exit code)  |  --shot=<png> [--hide-shell] [--select=<Part>]

const GLB := "res://assets/JARVIS.glb"
const EXTERIOR := ["Head_Front", "Head_Back", "Body_Shell", "Door_Rear", "Screen", "Camera", "Mic_Top",
	"Speaker_L", "Speaker_R", "Face_Eye_L", "Face_Eye_R", "Face_Mouth"]
const INTERNAL := ["Display_Module", "Camera_Module", "Mic_Array", "Pi_5", "Pi_Tray", "Speaker_Driver_L",
	"Speaker_Driver_R", "Reserve_USB_Hub"]
const ENVELOPE_PARTS := ["Head_Front", "Head_Back", "Body_Shell", "Door_Rear", "Screen"]
const HOME := {"yaw": 0.6, "pitch": 0.2, "dist": 0.85, "target": Vector3(0, 0.14, 0)}

var parts := {}      # name -> MeshInstance3D
var bodies := {}     # name -> StaticBody3D (click collision)
var cam: Camera3D
var yaw: float = HOME.yaw
var pitch: float = HOME.pitch
var dist: float = HOME.dist
var target: Vector3 = HOME.target
var selected := ""
var show_shell := true
var show_internal := true
var info: Label
var chk_shell: CheckButton
var chk_int: CheckButton
var press_pos := Vector2.ZERO
var dragging := false
var hl_mat: StandardMaterial3D


func _ready() -> void:
	_build_environment()
	var model := _load_model()
	if model == null:
		push_error("Could not load " + GLB)
		get_tree().quit(2)
		return
	add_child(model)
	_collect(model)
	for n in parts:
		parts[n].create_trimesh_collision()
		bodies[n] = parts[n].get_child(parts[n].get_child_count() - 1)
	_build_ui()
	_apply_visibility()
	_update_camera()
	var args := OS.get_cmdline_user_args()
	for a in args:
		if a == "--hide-shell":
			_set_shell(false)
		elif a.begins_with("--select="):
			_select(a.substr(9))
	if "--check" in args:
		_run_check()
	else:
		for a in args:
			if a.begins_with("--shot="):
				_take_shot(a.substr(7))


func _load_model() -> Node3D:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(ProjectSettings.globalize_path(GLB), state) != OK:
		return null
	return doc.generate_scene(state) as Node3D


func _collect(n: Node) -> void:
	if n is MeshInstance3D:
		parts[String(n.name)] = n
	for c in n.get_children():
		_collect(c)


func _build_environment() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.16, 0.18, 0.22)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.75, 0.8, 0.9)
	env.ambient_light_energy = 0.55
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)
	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-45, 35, 0)
	key.light_energy = 0.9
	key.shadow_enabled = true
	key.directional_shadow_max_distance = 3.0
	add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-20, -140, 0)
	fill.light_energy = 0.35
	add_child(fill)
	var ground := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(3, 3)
	ground.mesh = plane
	var gm := StandardMaterial3D.new()
	gm.albedo_color = Color(0.3, 0.31, 0.34)
	gm.roughness = 0.9
	ground.material_override = gm
	ground.position.y = -0.0005
	add_child(ground)
	cam = Camera3D.new()
	cam.near = 0.01
	cam.far = 20.0
	cam.fov = 40.0
	add_child(cam)
	hl_mat = StandardMaterial3D.new()
	hl_mat.albedo_color = Color(1.0, 0.7, 0.1, 0.45)
	hl_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	hl_mat.emission_enabled = true
	hl_mat.emission = Color(1.0, 0.6, 0.0)
	hl_mat.emission_energy_multiplier = 0.6


func _build_ui() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var panel := PanelContainer.new()
	panel.position = Vector2(12, 12)
	layer.add_child(panel)
	var box := VBoxContainer.new()
	panel.add_child(box)
	var title := Label.new()
	title.text = "JARVIS review viewer (S1.T03)\nCAD dimensions PROVISIONAL - not a fit drawing"
	box.add_child(title)
	chk_shell = CheckButton.new()
	chk_shell.text = "Outer shell"
	chk_shell.button_pressed = true
	chk_shell.toggled.connect(_set_shell)
	box.add_child(chk_shell)
	chk_int = CheckButton.new()
	chk_int.text = "Internal components"
	chk_int.button_pressed = true
	chk_int.toggled.connect(_set_internal)
	box.add_child(chk_int)
	var reset := Button.new()
	reset.text = "Reset view"
	reset.pressed.connect(_reset_view)
	box.add_child(reset)
	info = Label.new()
	info.text = "Click a part to identify it."
	info.custom_minimum_size = Vector2(300, 60)
	box.add_child(info)
	var help := Label.new()
	help.text = "Left-drag: orbit   Right-drag: pan   Wheel: zoom"
	box.add_child(help)


func _set_shell(on: bool) -> void:
	show_shell = on
	if chk_shell and chk_shell.button_pressed != on:
		chk_shell.set_pressed_no_signal(on)
	_apply_visibility()


func _set_internal(on: bool) -> void:
	show_internal = on
	if chk_int and chk_int.button_pressed != on:
		chk_int.set_pressed_no_signal(on)
	_apply_visibility()


func _is_visible_part(n: String) -> bool:
	return (n in EXTERIOR and show_shell) or (n in INTERNAL and show_internal)


func _apply_visibility() -> void:
	for n in parts:
		var vis := _is_visible_part(n)
		parts[n].visible = vis
		bodies[n].collision_layer = 1 if vis else 0
	if selected != "" and not _is_visible_part(selected):
		_select("")


func _reset_view() -> void:
	yaw = HOME.yaw
	pitch = HOME.pitch
	dist = HOME.dist
	target = HOME.target
	_update_camera()


func _update_camera() -> void:
	var off := Vector3(cos(pitch) * sin(yaw), sin(pitch), cos(pitch) * cos(yaw)) * dist
	cam.position = target + off
	cam.look_at(target, Vector3.UP)


func _part_aabb(n: String) -> AABB:
	var mi: MeshInstance3D = parts[n]
	return mi.global_transform * mi.get_aabb()


func _select(n: String) -> void:
	if selected != "" and parts.has(selected):
		parts[selected].material_overlay = null
	selected = n
	if n == "" or not parts.has(n):
		selected = ""
		if info:
			info.text = "Click a part to identify it."
		return
	parts[n].material_overlay = hl_mat
	var s := _part_aabb(n).size * 1000.0
	info.text = "Part: %s  (%s)\nW %.1f x H %.1f x D %.1f mm (bbox, PROVISIONAL)" % [
		n, "internal" if n in INTERNAL else "exterior", s.x, s.y, s.z]


func _pick(pos: Vector2) -> String:
	var from := cam.project_ray_origin(pos)
	var q := PhysicsRayQueryParameters3D.create(from, from + cam.project_ray_normal(pos) * 10.0)
	var hit := get_world_3d().direct_space_state.intersect_ray(q)
	var n := "" if hit.is_empty() else String(hit.collider.get_parent().name)
	_select(n)
	return n


func _unhandled_input(e: InputEvent) -> void:
	if e is InputEventMouseButton:
		match e.button_index:
			MOUSE_BUTTON_WHEEL_UP:
				dist = clampf(dist * 0.9, 0.15, 4.0)
				_update_camera()
			MOUSE_BUTTON_WHEEL_DOWN:
				dist = clampf(dist / 0.9, 0.15, 4.0)
				_update_camera()
			MOUSE_BUTTON_LEFT:
				if e.pressed:
					press_pos = e.position
					dragging = false
				elif not dragging:
					_pick(e.position)
	elif e is InputEventMouseMotion:
		if e.button_mask & MOUSE_BUTTON_MASK_LEFT:
			if e.position.distance_to(press_pos) > 4.0:
				dragging = true
			if dragging:
				yaw -= e.relative.x * 0.008
				pitch = clampf(pitch + e.relative.y * 0.008, -1.4, 1.4)
				_update_camera()
		elif e.button_mask & (MOUSE_BUTTON_MASK_RIGHT | MOUSE_BUTTON_MASK_MIDDLE):
			target += (-cam.global_transform.basis.x * e.relative.x + cam.global_transform.basis.y * e.relative.y) * dist * 0.0012
			_update_camera()


func _take_shot(path: String) -> void:
	for i in 8:
		await get_tree().process_frame
	get_viewport().get_texture().get_image().save_png(path)
	print("screenshot ", path)
	get_tree().quit(0)


func _run_check() -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	var errs: Array[String] = []
	for n in EXTERIOR + INTERNAL:
		if not parts.has(n):
			errs.append("missing part " + n)
	if errs.is_empty():
		# 1. CAD mm -> Godot m: shell envelope should be W200 x H280 x D160 mm (x, y, z in Godot axes)
		var bb := _part_aabb(ENVELOPE_PARTS[0])
		for n in ENVELOPE_PARTS:
			bb = bb.merge(_part_aabb(n))
		var want := Vector3(0.200, 0.280, 0.160)
		print("envelope mm: ", bb.size * 1000.0)
		var diff := (bb.size - want).abs()
		if maxf(diff.x, maxf(diff.y, diff.z)) > 0.005:
			errs.append("scale/envelope mismatch %s vs %s" % [bb.size, want])
		# 2. toggles
		_set_shell(false)
		for n in EXTERIOR:
			if parts[n].visible:
				errs.append(n + " still visible with shell off")
		for n in INTERNAL:
			if not parts[n].visible:
				errs.append(n + " hidden with internals on")
		# 3. picking: side view (+X), shell off -> click Pi_5 centre returns Pi_5
		yaw = PI / 2.0
		pitch = 0.0
		dist = 0.7
		target = Vector3(0, 0.03, 0)
		_update_camera()
		await get_tree().physics_frame
		var got := _pick(cam.unproject_position(_part_aabb("Pi_5").get_center()))
		if got != "Pi_5":
			errs.append("pick Pi_5 returned '%s'" % got)
		# shell on, front view: click Speaker_L grille centre
		_set_shell(true)
		yaw = 0.0
		pitch = 0.05
		dist = 0.9
		target = Vector3(0, 0.14, 0)
		_update_camera()
		await get_tree().physics_frame
		got = _pick(cam.unproject_position(_part_aabb("Speaker_L").get_center()))
		if got != "Speaker_L":
			errs.append("pick Speaker_L returned '%s'" % got)
		if selected != "Speaker_L" or not info.text.contains("Speaker_L"):
			errs.append("selection label not updated")
	print("CHECK RESULT: ", "FAIL " + "; ".join(errs) if errs else "PASS (%d parts)" % parts.size())
	get_tree().quit(1 if errs else 0)
