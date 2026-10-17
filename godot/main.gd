extends Node3D
# JARVES review viewer (S1.T03R2 Pebble A). Loads the CAD-derived GLB (metres, part names = CAD names) and
# assets/JARVIS_manifest.json (part -> category + CAD bbox in mm) + assets/finishes.json (PROVISIONAL finishes).
# Left-drag orbit | right/middle-drag pan | wheel zoom | click selects a part.
# CLI (after "--"):  --check  (print PASS/FAIL, exit code)  |  --shot=<png> [--hide-shell] [--select=<Part>]
#                    [--show-reserved] [--finish=<key>] [--view=front|side|rear|three_quarter|top|led_closeup]
#                    [--exploded | --exploded=<0..1>] [--blank-led]

const GLB := "res://assets/JARVIS.glb"
const MANIFEST := "res://assets/JARVIS_manifest.json"
const FINISHES := "res://assets/finishes.json"
const LAYERS := {"exterior": "Outer shell", "structure": "Structure (neck / spine / chassis)",
	"component": "Components (envelopes)", "reserved": "Reserved volumes (SSD, cable)"}
const HOME := {"yaw": 0.55, "pitch": 0.22, "dist": 1.05, "target": Vector3(0, 0.15, 0)}
# view -> [yaw, pitch, dist, target]
const VIEWS := {"front": [0.0, 0.08, 1.05, Vector3(0, 0.15, 0)], "side": [PI / 2.0, 0.08, 1.05, Vector3(0, 0.15, 0)],
	"rear": [PI, 0.08, 1.05, Vector3(0, 0.15, 0)], "three_quarter": [0.55, 0.22, 1.05, Vector3(0, 0.15, 0)],
	"top": [0.0, 1.5, 1.05, Vector3(0, 0.15, 0)], "led_closeup": [0.55, 0.35, 0.45, Vector3(0, 0.075, 0)]}
const EXPLODE_VIEW := {"target": Vector3(0, 0.38, 0), "dist": 1.5}
const EXPLODE_LIFT := 0.10  # m, global +Y lift per unit factor so downward offsets stay above the ground
const LED_RING := ["LED_Diffuser_Front", "LED_Diffuser_Back", "LED_Strip"]

var parts := {}      # name -> MeshInstance3D
var bodies := {}     # name -> StaticBody3D (click collision)
var cat := {}        # name -> category
var manifest := {}
var fin := {}
var finish_key := ""
var shown := {"exterior": true, "structure": true, "component": true, "reserved": false}
var checks := {}
var finish_btn: OptionButton
var cam: Camera3D
var yaw: float = HOME.yaw
var pitch: float = HOME.pitch
var dist: float = HOME.dist
var target: Vector3 = HOME.target
var selected := ""
var info: Label
var press_pos := Vector2.ZERO
var dragging := false
var hl_mat: StandardMaterial3D
var blank_led := false
var explode_factor := 0.0
var exploded := false
var orig_pos := {}   # name -> original global origin
var blank_chk: CheckButton
var explode_chk: CheckButton
var explode_slider: HSlider


func _ready() -> void:
	manifest = _json(MANIFEST)
	fin = _json(FINISHES)
	_build_environment()
	var model := _load_model()
	if model == null or manifest.is_empty() or fin.is_empty():
		push_error("Could not load GLB / manifest / finishes")
		get_tree().quit(2)
		return
	add_child(model)
	_collect(model)
	for n in parts:
		cat[n] = manifest.parts[n].category if manifest.parts.has(n) else "exterior"
		parts[n].create_trimesh_collision()
		bodies[n] = parts[n].get_child(parts[n].get_child_count() - 1)
	for n in parts:
		orig_pos[n] = parts[n].global_transform.origin
	_build_ui()
	_apply_finish(fin.default)
	_apply_visibility()
	_update_camera()
	var args := OS.get_cmdline_user_args()
	var view := ""
	for a in args:
		if a == "--hide-shell":
			_set_layer(false, "exterior")
		elif a == "--show-reserved":
			_set_layer(true, "reserved")
		elif a.begins_with("--select="):
			_select(a.substr(9))
		elif a.begins_with("--finish="):
			_apply_finish(a.substr(9))
		elif a == "--blank-led":
			_set_blank(true)
		elif a == "--exploded":
			_set_explode(true, 1.0)
		elif a.begins_with("--exploded="):
			_set_explode(true, float(a.substr(11)))
		elif a.begins_with("--view="):
			view = a.substr(7)
	if view != "":
		_set_view(view)
	if "--check" in args:
		_run_check()
	else:
		for a in args:
			if a.begins_with("--shot="):
				_take_shot(a.substr(7))


func _json(path: String) -> Dictionary:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return {}
	var d = JSON.parse_string(f.get_as_text())
	return d if d is Dictionary else {}


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
	env.background_color = Color(0.17, 0.18, 0.21)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.78, 0.8, 0.86)
	env.ambient_light_energy = 0.5
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.glow_enabled = true
	env.glow_intensity = 0.4
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)
	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-42, 30, 0)
	key.light_energy = 0.95
	key.shadow_enabled = true
	key.directional_shadow_max_distance = 3.0
	add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-15, -130, 0)
	fill.light_energy = 0.35
	add_child(fill)
	var rim := DirectionalLight3D.new()
	rim.rotation_degrees = Vector3(-30, 180, 0)
	rim.light_energy = 0.4
	add_child(rim)
	var ground := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(3, 3)
	ground.mesh = plane
	var gm := StandardMaterial3D.new()
	gm.albedo_color = Color(0.33, 0.33, 0.35)
	gm.roughness = 0.85
	ground.material_override = gm
	ground.position.y = -0.0005
	add_child(ground)
	cam = Camera3D.new()
	cam.near = 0.01
	cam.far = 20.0
	cam.fov = 35.0
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
	title.text = "JARVES Pebble A - visual review (S1.T03R2)\nShapes from CAD; sizes & finishes PROVISIONAL"
	box.add_child(title)
	for c in LAYERS:
		var chk := CheckButton.new()
		chk.text = LAYERS[c]
		chk.button_pressed = shown[c]
		chk.toggled.connect(_set_layer.bind(c))
		box.add_child(chk)
		checks[c] = chk
	finish_btn = OptionButton.new()
	for k in fin.finishes:
		finish_btn.add_item(fin.finishes[k].label)
		finish_btn.set_item_metadata(finish_btn.item_count - 1, k)
	finish_btn.item_selected.connect(func(i): _apply_finish(finish_btn.get_item_metadata(i)))
	box.add_child(finish_btn)
	blank_chk = CheckButton.new()
	blank_chk.text = "Blank cover instead of LED ring"
	blank_chk.toggled.connect(_set_blank)
	box.add_child(blank_chk)
	explode_chk = CheckButton.new()
	explode_chk.text = "Exploded view"
	explode_chk.toggled.connect(func(on): _set_explode(on, explode_slider.value if on else 0.0, true))
	box.add_child(explode_chk)
	explode_slider = HSlider.new()
	explode_slider.min_value = 0.0
	explode_slider.max_value = 1.0
	explode_slider.step = 0.01
	explode_slider.value = 1.0
	explode_slider.custom_minimum_size = Vector2(300, 20)
	explode_slider.value_changed.connect(func(v): if exploded: _set_explode(true, v))
	box.add_child(explode_slider)
	var views := HBoxContainer.new()
	for v in VIEWS:
		var b := Button.new()
		b.text = v.replace("_", " ").capitalize()
		b.pressed.connect(_set_view.bind(v))
		views.add_child(b)
	box.add_child(views)
	info = Label.new()
	info.text = "Click a part to identify it."
	info.custom_minimum_size = Vector2(330, 60)
	box.add_child(info)
	var help := Label.new()
	help.text = "Left-drag: orbit   Right-drag: pan   Wheel: zoom"
	box.add_child(help)


func _set_layer(on: bool, c: String) -> void:
	shown[c] = on
	if checks.has(c) and checks[c].button_pressed != on:
		checks[c].set_pressed_no_signal(on)
	_apply_visibility()


func _part_visible(n: String) -> bool:
	var c: String = cat[n]
	if c == "alternate":
		return blank_led and shown["exterior"]
	if blank_led and n in LED_RING:
		return false
	return shown.get(c, true)


func _set_blank(on: bool) -> void:
	blank_led = on
	if blank_chk and blank_chk.button_pressed != on:
		blank_chk.set_pressed_no_signal(on)
	_apply_visibility()


func _set_explode(on: bool, f: float = 1.0, set_camera: bool = false) -> void:
	exploded = on
	explode_factor = clampf(f, 0.0, 1.0) if on else 0.0
	for n in parts:
		var e: Array = manifest.parts[n].get("explode_mm", [0, 0, 0]) if manifest.parts.has(n) else [0, 0, 0]
		var d := Vector3(e[0], e[2], -e[1]) / 1000.0 * explode_factor + Vector3(0, EXPLODE_LIFT * explode_factor, 0)
		var gt: Transform3D = parts[n].global_transform
		gt.origin = orig_pos[n] + d
		parts[n].global_transform = gt
	if explode_chk and explode_chk.button_pressed != on:
		explode_chk.set_pressed_no_signal(on)
	if explode_slider and on and not is_equal_approx(explode_slider.value, explode_factor):
		explode_slider.set_value_no_signal(explode_factor)
	if on or set_camera:
		target = EXPLODE_VIEW.target if on else HOME.target
		dist = EXPLODE_VIEW.dist if on else HOME.dist
		_update_camera()


func _apply_visibility() -> void:
	for n in parts:
		var vis: bool = _part_visible(n)
		parts[n].visible = vis
		bodies[n].collision_layer = 1 if vis else 0
	if selected != "" and not parts[selected].visible:
		_select("")


func _apply_finish(key: String) -> void:
	if not fin.finishes.has(key):
		push_error("unknown finish " + key)
		return
	finish_key = key
	var f: Dictionary = fin.finishes[key]
	for n in parts:
		if not fin.roles.has(n):
			continue
		var s: Dictionary = f[fin.roles[n]]
		var m := StandardMaterial3D.new()
		m.albedo_color = Color(s.color[0], s.color[1], s.color[2])
		m.roughness = s.get("rough", 0.5)
		m.metallic = s.get("metal", 0.0)
		if s.get("coat", 0.0) > 0.0:
			m.clearcoat_enabled = true
			m.clearcoat = s.coat
		if s.has("emit"):
			m.emission_enabled = true
			m.emission = Color(s.emit[0], s.emit[1], s.emit[2])
			m.emission_energy_multiplier = s.get("strength", 5) * 0.4
		if s.get("alpha", 1.0) < 1.0:
			m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			m.albedo_color.a = s.alpha
		parts[n].material_override = m
	if finish_btn:
		for i in finish_btn.item_count:
			if finish_btn.get_item_metadata(i) == key:
				finish_btn.select(i)


func _set_view(v: String) -> void:
	if VIEWS.has(v):
		yaw = VIEWS[v][0]
		pitch = VIEWS[v][1]
		dist = VIEWS[v][2]
		target = VIEWS[v][3]
		if exploded and v != "led_closeup":
			dist = EXPLODE_VIEW.dist
			target = EXPLODE_VIEW.target
		_update_camera()


func _update_camera() -> void:
	var off := Vector3(cos(pitch) * sin(yaw), sin(pitch), cos(pitch) * cos(yaw)) * dist
	cam.position = target + off
	cam.look_at(target, Vector3.UP)


func _part_aabb(n: String) -> AABB:
	var mi: MeshInstance3D = parts[n]
	return mi.global_transform * mi.get_aabb()


func _cad_aabb(n: String) -> AABB:  # manifest bbox (CAD mm, Z up, front -Y) -> Godot metres (Y up, front +Z)
	var b: Array = manifest.parts[n].bbox_mm
	var lo := Vector3(b[0], b[2], -b[4]) / 1000.0
	var hi := Vector3(b[3], b[5], -b[1]) / 1000.0
	return AABB(lo, hi - lo)


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
	info.text = "Part: %s  (%s)\nW %.1f x H %.1f x D %.1f mm (bbox, PROVISIONAL)" % [n, cat[n], s.x, s.y, s.z]


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
	for i in 10:
		await get_tree().process_frame
	get_viewport().get_texture().get_image().save_png(path)
	print("screenshot ", path)
	get_tree().quit(0)


func _pick_center(n: String, y: float, p: float, d: float) -> String:
	yaw = y
	pitch = p
	dist = d
	target = _part_aabb(n).get_center()
	_update_camera()
	await get_tree().physics_frame
	return _pick(cam.unproject_position(_part_aabb(n).get_center()))


func _run_check() -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	var errs: Array[String] = []
	# 1. every manifest part present; CAD mm -> Godot m bbox match per part (tessellation tolerance 1.5 mm)
	var worst := 0.0
	for n in manifest.parts:
		if not parts.has(n):
			errs.append("missing part " + n)
			continue
		var a := _part_aabb(n)
		var b := _cad_aabb(n)
		var dev := maxf((a.position - b.position).abs()[(a.position - b.position).abs().max_axis_index()],
			(a.end - b.end).abs()[(a.end - b.end).abs().max_axis_index()])
		worst = maxf(worst, dev)
		if dev > 0.0015:
			errs.append("%s bbox off by %.1f mm" % [n, dev * 1000.0])
	print("parts: %d in GLB, %d in manifest; worst bbox deviation %.2f mm" % [parts.size(), manifest.parts.size(), worst * 1000.0])
	if errs.is_empty():
		var ext: AABB = _part_aabb("Head_Back")
		for n in parts:
			if cat[n] == "exterior":
				ext = ext.merge(_part_aabb(n))
		print("exterior envelope mm (W x H x D): ", ext.size * 1000.0)
		# 2. layer toggles
		_set_layer(false, "exterior")
		for n in parts:
			if parts[n].visible != (cat[n] != "exterior" and cat[n] != "alternate" and shown[cat[n]]):
				errs.append(n + " visibility wrong with shell off")
		await _check_new(errs)
		# 3. picking through the stack: shell off, side view -> Pi_5
		var got: String = await _pick_center("Pi_5", PI / 2.0, 0.0, 0.8)
		if got != "Pi_5":
			errs.append("pick Pi_5 returned '%s'" % got)
		# shell on, front view -> fabric band and face cover
		_set_layer(true, "exterior")
		got = await _pick_center("Base_Band", 0.0, 0.0, 1.0)
		if got != "Base_Band":
			errs.append("pick Base_Band returned '%s'" % got)
		got = await _pick_center("Display_Panel", 0.0, 0.0, 1.0)
		if got != "Face_Cover":
			errs.append("pick face returned '%s'" % got)
		if selected != "Face_Cover" or not info.text.contains("Face_Cover"):
			errs.append("selection label not updated")
		# 4. finish switch applies the palette from finishes.json
		for k in fin.finishes:
			_apply_finish(k)
			var want: Array = fin.finishes[k][fin.roles["Base_Shell"]].color
			var m: StandardMaterial3D = parts["Base_Shell"].material_override
			if m == null or not m.albedo_color.is_equal_approx(Color(want[0], want[1], want[2])):
				errs.append("finish %s not applied" % k)
	print("CHECK RESULT: ", "FAIL " + "; ".join(errs) if errs else "PASS (%d parts, %d finishes)" % [parts.size(), fin.finishes.size()])
	get_tree().quit(1 if errs else 0)


func _check_new(errs: Array[String]) -> void:
	# a. alternates hidden by default, LED diffusers visible; blank cover swaps them
	_set_layer(true, "exterior")
	for n in ["LED_Blank_Front", "LED_Blank_Back"]:
		if parts[n].visible:
			errs.append(n + " visible by default")
	for n in LED_RING:
		if not parts[n].visible:
			errs.append(n + " hidden by default")
	_set_blank(true)
	for n in ["LED_Blank_Front", "LED_Blank_Back"]:
		if not parts[n].visible:
			errs.append(n + " not shown with blank cover")
	for n in LED_RING:
		if parts[n].visible:
			errs.append(n + " still visible with blank cover")
	_set_blank(false)
	if parts["LED_Blank_Front"].visible or not parts["LED_Diffuser_Front"].visible:
		errs.append("blank cover toggle off did not restore")
	# b. explode
	var c0: Vector3 = _part_aabb("Head_Back").get_center()
	var e: Array = manifest.parts["Head_Back"].explode_mm
	var want := Vector3(e[0], e[2], -e[1]) / 1000.0 + Vector3(0, EXPLODE_LIFT, 0)
	_set_explode(true, 1.0)
	var d1: float = (_part_aabb("Head_Back").get_center() - c0 - want).length()
	if d1 > 0.001:
		errs.append("explode factor 1 Head_Back off by %.2f mm" % (d1 * 1000.0))
	# c. picking in exploded state
	await get_tree().physics_frame
	await get_tree().physics_frame
	for n in ["Mic_Cap", "Head_Back"]:
		var got: String = await _pick_center(n, 0.55, 0.3, 1.2)
		if got != n:
			errs.append("exploded pick %s returned '%s'" % [n, got])
	_set_explode(false, 0.0)
	var d0: float = (_part_aabb("Head_Back").get_center() - c0).length()
	if d0 > 0.0001:
		errs.append("explode factor 0 not restored (%.3f mm)" % (d0 * 1000.0))
	await get_tree().physics_frame
	await get_tree().physics_frame
	print("explode err %.3f mm, restore err %.4f mm" % [d1 * 1000.0, d0 * 1000.0])
	_set_layer(false, "exterior")
