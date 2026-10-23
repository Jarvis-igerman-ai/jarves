extends Node3D
# JARVES review viewer (S1.T03R2 Pebble A). Loads the CAD-derived GLB (metres, part names = CAD names) and
# assets/JARVIS_manifest.json (part -> category + CAD bbox in mm) + assets/finishes.json (PROVISIONAL finishes).
# Left-drag orbit | right/middle-drag pan | wheel zoom | click selects a part.
# CLI (after "--"):  --check  (print PASS/FAIL, exit code)  |  --shot=<png> [--hide-shell] [--select=<Part>]
#                    [--show-reserved] [--finish=<key>] [--view=front|side|rear|three_quarter|top|led_closeup]
#                    [--exploded | --exploded=<0..1>] [--led-ring]  (optional LED ring instead of the default blank)
#   Assembly images: --step=<1..N> | --part=<Name> | --overview  (UI hidden; combine with --shot=); --list-parts=<kind,...> prints PARTLIST lines
#   Room simulation (default view): --studio | --room | --evening | --cam=desk|sofa|closeup | --low | --gi | --hide-ui
#   Keys: R room/studio  N day/evening  C camera preset  H hide UI

const GLB := "res://assets/JARVIS.glb"
const MANIFEST := "res://assets/JARVIS_manifest.json"
const FINISHES := "res://assets/finishes.json"
const LAYERS := {"exterior": "Outer shell", "structure": "Structure (neck column / tray / retainer)",
	"component": "Components (envelopes)", "reserved": "Reserved volumes (SSD, cable)"}
const HOME := {"yaw": 0.55, "pitch": 0.22, "dist": 1.05, "target": Vector3(0, 0.15, 0)}
# view -> [yaw, pitch, dist, target]
const VIEWS := {"front": [0.0, 0.08, 1.05, Vector3(0, 0.15, 0)], "side": [PI / 2.0, 0.08, 1.05, Vector3(0, 0.15, 0)],
	"rear": [PI, 0.08, 1.05, Vector3(0, 0.15, 0)], "three_quarter": [0.55, 0.22, 1.05, Vector3(0, 0.15, 0)],
	"top": [0.0, 1.5, 1.05, Vector3(0, 0.15, 0)], "led_closeup": [0.55, 0.35, 0.45, Vector3(0, 0.075, 0)]}
const EXPLODE_VIEW := {"target": Vector3(0, 0.38, 0), "dist": 1.5}
const EXPLODE_LIFT := 0.10  # m, global +Y lift per unit factor so downward offsets stay above the ground
const LED_RING := ["LED_Diffuser_Front", "LED_Diffuser_Back", "LED_Strip", "LED_Connector"]
const BLANKS := ["LED_Blank_Front", "LED_Blank_Back"]
const STATIC_FACE := ["Face_Eye_L", "Face_Eye_R", "Face_Smile"]   # Blender placeholder, replaced by the live face
const FaceScript := preload("res://face/face.gd")
const Appearance := preload("res://face/appearance.gd")
const AssistantUI := preload("res://face/assistant_ui.gd")
const RoomScript := preload("res://room/room.gd")
# room camera presets around the device (target = device middle): [yaw, pitch, dist, fov]
# desk: standing person (eye 1.6 m above the floor) ~1.6 m away; sofa: seated eye ~1.15 m, 3 m away; closeup: 0.6 m
const ROOM_TARGET := Vector3(0, 0.15, 0)
const ROOM_CAMS := {"conversation": [0.30, 0.16, 0.9, 42.0], "desk": [0.42, 0.41, 1.75, 52.0], "sofa": [0.05, 0.083, 3.0, 48.0], "closeup": [0.38, 0.12, 0.62, 35.0]}
# CLI flags that ask for the plain inspection (studio) view unless --room is also given
const STUDIO_FLAGS := ["--studio", "--hide-shell", "--show-reserved", "--overview"]
const STUDIO_PREFIXES := ["--view=", "--exploded", "--step=", "--part="]

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
var ground: MeshInstance3D
var ui_layer: CanvasLayer
var extras: Array = []   # step/overview helper nodes (arrows, labels), freed on re-apply
var step_mode := false
var leader_layer: CanvasLayer
var label_px: Array = []  # screen positions of placed Label3Ds (overlap avoidance)
var face: Control         # live face (face/face.gd) rendered onto the display glass
var face_quad: MeshInstance3D
var inspection_panel: PanelContainer
var assistant: PanelContainer
var room: Node3D           # room/room.gd
var room_on := false
var room_cam := "conversation"
var studio: Node3D         # studio lights + grey ground
var we: WorldEnvironment
var env_studio: Environment
var face_vp: SubViewport
var appearance: Control
var room_chk: CheckButton


func _ready() -> void:
	manifest = _json(MANIFEST)
	fin = _json(FINISHES)
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--list-parts="):  # print "PARTLIST <pid> <Name>" for the given kinds, then quit
			var kinds := a.substr(13).split(",")
			for n in manifest.get("parts", {}):
				var p: Dictionary = manifest.parts[n]
				if p.get("pid", "") != "" and p.get("kind", "") in kinds:
					print("PARTLIST %s %s" % [p.pid, n])
			get_tree().quit(0)
			return
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
	var args := OS.get_cmdline_user_args()
	room = RoomScript.new()
	add_child(room)
	room.build(_device_aabb().position.y)   # desk top = lowest point of the device as exported
	if "--low" in args:
		room.set_quality(false)
	if "--gi" in args:
		room.set_gi(true)
	_build_ui()
	_build_face()
	_apply_finish(fin.default)
	_apply_visibility()
	_update_camera()
	var want_room := true
	for a in args:
		if a in STUDIO_FLAGS:
			want_room = false
		for pre in STUDIO_PREFIXES:
			if a.begins_with(pre):
				want_room = false
	if "--room" in args:
		want_room = true
	if "--evening" in args:
		room.set_evening(true)
	for a in args:
		if a.begins_with("--cam=") and ROOM_CAMS.has(a.substr(6)):
			room_cam = a.substr(6)
	_set_room(want_room)
	if "--hide-ui" in args:
		ui_layer.visible = false
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
		elif a == "--led-ring":
			_set_blank(true)
		elif a == "--exploded":
			_set_explode(true, 1.0)
		elif a.begins_with("--exploded="):
			_set_explode(true, float(a.substr(11)))
		elif a.begins_with("--view="):
			view = a.substr(7)
	if view != "":
		_set_view(view)
	for a in args:
		if a.begins_with("--step="):
			_apply_step(clampi(int(a.substr(7)), 1, manifest.steps.size()))
		elif a.begins_with("--part="):
			_apply_part(a.substr(7))
		elif a == "--overview":
			_apply_overview()
	if "--check" in args:
		_run_check()
	elif "--bench" in args:
		_bench()
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
	env_studio = env
	we = WorldEnvironment.new()
	we.environment = env
	add_child(we)
	studio = Node3D.new()
	studio.name = "Studio"
	add_child(studio)
	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-42, 30, 0)
	key.light_energy = 0.95
	key.shadow_enabled = true
	key.directional_shadow_max_distance = 3.0
	studio.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-15, -130, 0)
	fill.light_energy = 0.35
	studio.add_child(fill)
	var rim := DirectionalLight3D.new()
	rim.rotation_degrees = Vector3(-30, 180, 0)
	rim.light_energy = 0.4
	studio.add_child(rim)
	ground = MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(3, 3)
	ground.mesh = plane
	var gm := StandardMaterial3D.new()
	gm.albedo_color = Color(0.33, 0.33, 0.35)
	gm.roughness = 0.85
	ground.material_override = gm
	ground.position.y = -0.0005
	studio.add_child(ground)
	cam = Camera3D.new()
	cam.near = 0.01
	cam.far = 200.0
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
	ui_layer = layer
	add_child(layer)
	var panel := PanelContainer.new()
	inspection_panel = panel
	panel.visible = false
	panel.position = Vector2(12, 12)
	panel.theme = AssistantUI.make_ui_theme(18)
	layer.add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 6)
	panel.add_child(box)
	var title := Label.new()
	title.text = "Model inspector"
	title.add_theme_font_size_override("font_size", 22)
	box.add_child(title)
	var prov := Label.new()
	prov.text = "Pebble A - R5b review. Sizes and finishes are provisional."
	prov.modulate = Color(1, 1, 1, 0.6)
	box.add_child(prov)
	for c in LAYERS:
		var chk := CheckButton.new()
		chk.text = LAYERS[c]
		chk.button_pressed = shown[c]
		chk.toggled.connect(_set_layer.bind(c))
		box.add_child(chk)
		checks[c] = chk
	var more := VBoxContainer.new()
	more.visible = false
	finish_btn = OptionButton.new()
	for k in fin.finishes:
		finish_btn.add_item(fin.finishes[k].label)
		finish_btn.set_item_metadata(finish_btn.item_count - 1, k)
	finish_btn.item_selected.connect(func(i): _apply_finish(finish_btn.get_item_metadata(i)))
	more.add_child(finish_btn)
	blank_chk = CheckButton.new()
	blank_chk.text = "Optional LED ring fitted (default: blank ring)"
	blank_chk.toggled.connect(_set_blank)
	more.add_child(blank_chk)
	explode_chk = CheckButton.new()
	explode_chk.text = "Exploded view"
	explode_chk.toggled.connect(func(on): _set_explode(on, explode_slider.value if on else 0.0, true))
	more.add_child(explode_chk)
	explode_slider = HSlider.new()
	explode_slider.min_value = 0.0
	explode_slider.max_value = 1.0
	explode_slider.step = 0.01
	explode_slider.value = 1.0
	explode_slider.custom_minimum_size = Vector2(380, 24)
	explode_slider.value_changed.connect(func(v): if exploded: _set_explode(true, v))
	more.add_child(explode_slider)
	var more_btn := CheckButton.new()
	more_btn.text = "More: finish, LED ring, exploded view"
	more_btn.toggled.connect(func(on): more.visible = on)
	box.add_child(more_btn)
	box.add_child(more)
	var views := HFlowContainer.new()
	for v in VIEWS:
		var b := Button.new()
		b.text = v.replace("_", " ").capitalize()
		b.pressed.connect(_set_view.bind(v))
		views.add_child(b)
	box.add_child(views)
	var rbox := HFlowContainer.new()
	room_chk = CheckButton.new()
	room_chk.text = "Room (R)"
	room_chk.toggled.connect(_set_room)
	rbox.add_child(room_chk)
	var eve := Button.new()
	eve.text = "Day / evening (N)"
	eve.pressed.connect(_toggle_evening)
	rbox.add_child(eve)
	var cb := Button.new()
	cb.text = "Camera (C)"
	cb.pressed.connect(_next_room_cam)
	rbox.add_child(cb)
	box.add_child(rbox)
	info = Label.new()
	info.text = "Click a part to identify it."
	info.custom_minimum_size = Vector2(380, 0)
	box.add_child(info)
	var help := Label.new()
	help.text = "Drag: orbit · Right-drag: pan · Wheel: zoom · H: hide panels"
	help.modulate = Color(1, 1, 1, 0.6)
	box.add_child(help)


func _build_face() -> void:
	# live animated face on the display glass + assistant controls (Core link); same face as face_app.tscn
	var vp := SubViewport.new()
	vp.size = Vector2i(720, 720)
	vp.transparent_bg = true
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	face_vp = vp
	face = FaceScript.new()
	face.size = Vector2(720, 720)
	appearance = Appearance.new()
	vp.add_child(appearance)
	appearance.add_child(face)
	appearance.setup(face)
	appearance.layout(Rect2(Vector2.ZERO, Vector2(720, 720)))
	add_child(vp)
	if parts.has("Display_Panel"):
		var bb := _cad_aabb("Display_Panel")
		var q := QuadMesh.new()
		q.size = Vector2(0.132, 0.132)       # = CAD face opening (r66)
		var m := StandardMaterial3D.new()
		m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		m.albedo_texture = vp.get_texture()
		face_quad = MeshInstance3D.new()
		face_quad.name = "LiveFace"
		face_quad.mesh = q
		face_quad.material_override = m
		parts["Display_Panel"].add_child(face_quad)   # follows the exploded view
		var ctr := bb.get_center()
		face_quad.global_transform = Transform3D(Basis.IDENTITY, Vector3(ctr.x, ctr.y, bb.end.z + 0.0008))  # facing +Z (front), ignores GLB node rotation
	for a in OS.get_cmdline_user_args():   # face preview without a Core (screenshots); the Core overrides both
		if a.begins_with("--face-state="):
			face.set_state(a.substr(13))
		elif a.begins_with("--face-link="):
			face.set_link(a.substr(12))
		elif a.begins_with("--appearance="):
			appearance.set_mode(a.substr(13))
	face.snap()
	if "--no-core" in OS.get_cmdline_user_args() and not "--check" in OS.get_cmdline_user_args():
		return
	assistant = AssistantUI.new()
	assistant.face = face
	assistant.appearance = appearance
	ui_layer.add_child(assistant)
	assistant.inspector_chk.visible = true
	assistant.inspector_toggled.connect(func(on): inspection_panel.visible = on)
	assistant.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT, Control.PRESET_MODE_MINSIZE, 12)


func _device_aabb() -> AABB:  # outer shell envelope at the assembled position (metres)
	var a := AABB()
	var first := true
	for n in parts:
		if cat.get(n, "exterior") != "exterior":
			continue
		var gt: Transform3D = parts[n].global_transform
		if orig_pos.has(n):
			gt.origin = orig_pos[n]
		var b: AABB = gt * parts[n].get_aabb()
		a = b if first else a.merge(b)
		first = false
	return a


func _set_room(on: bool) -> void:
	room_on = on
	room.set_active(on)
	studio.visible = not on
	we.environment = room.current_env() if on else env_studio
	if room_chk and room_chk.button_pressed != on:
		room_chk.set_pressed_no_signal(on)
	if on:
		_apply_room_cam(room_cam)
	else:
		cam.fov = 35.0
		yaw = HOME.yaw
		pitch = HOME.pitch
		dist = HOME.dist
		target = HOME.target
		_update_camera()


func _apply_room_cam(k: String) -> void:
	room_cam = k
	var c: Array = ROOM_CAMS[k]
	yaw = c[0]
	pitch = c[1]
	dist = c[2]
	cam.fov = c[3]
	target = ROOM_TARGET
	_update_camera()


func _next_room_cam() -> void:
	var keys := ROOM_CAMS.keys()
	if not room_on:
		_set_room(true)
		return
	_apply_room_cam(keys[(keys.find(room_cam) + 1) % keys.size()])


func _toggle_evening() -> void:
	room.set_evening(not room.evening)
	if not room_on:
		_set_room(true)
	we.environment = room.current_env()


func _set_layer(on: bool, c: String) -> void:
	shown[c] = on
	if checks.has(c) and checks[c].button_pressed != on:
		checks[c].set_pressed_no_signal(on)
	_apply_visibility()


func _part_visible(n: String) -> bool:
	var c: String = cat[n]
	if n in STATIC_FACE and face_quad != null:
		return false
	if c == "alternate":
		return blank_led and shown["exterior"]
	if blank_led and n in BLANKS:
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
	if room_on:
		cam.position = room.clamp_inside(cam.position)   # orbit/zoom never leaves the room
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
	if n != "" and not parts.has(n) and info:
		info.text = "Room: %s (not part of the device)" % n.replace("_", " ")
	return n


func _unhandled_input(e: InputEvent) -> void:
	if e is InputEventKey and e.pressed and not e.echo:
		match e.keycode:
			KEY_R:
				_set_room(not room_on)
			KEY_N:
				_toggle_evening()
			KEY_C:
				_next_room_cam()
			KEY_H:
				ui_layer.visible = not ui_layer.visible
		return
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


func _bench() -> void:  # --bench: render 300 frames, print fps + scene cost, quit (for the Pi 5 budget)
	Engine.max_fps = 0
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	for i in 60:
		await get_tree().process_frame
	var t0 := Time.get_ticks_usec()
	for i in 300:
		await get_tree().process_frame
	var fps := 300.0 / ((Time.get_ticks_usec() - t0) / 1e6)
	print("BENCH room=%s fps=%.1f draw_calls=%d primitives=%d objects=%d video_mem_MB=%.0f" % [room_on, fps,
		Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME), Performance.get_monitor(Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME),
		Performance.get_monitor(Performance.RENDER_TOTAL_OBJECTS_IN_FRAME), Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / 1048576.0])
	get_tree().quit(0)


func _take_shot(path: String) -> void:
	for i in 10:
		await get_tree().process_frame
	get_viewport().get_texture().get_image().save_png(path)
	print("screenshot ", path)
	get_tree().quit(0)


const STEP_PULL := 0.6      # fraction of explode_mm used for the parts of the current step
const STEP_LIFT := 0.06     # m, upward offset added to the pulled-out parts (also the fallback when explode ~ 0)
const ARROW_COLOR := Color(1.0, 0.45, 0.0)
const LEGEND := "P = printed     B = bought     E = electronics (placeholder)"


func _kind(n: String) -> String:
	return manifest.parts[n].get("kind", "") if manifest.parts.has(n) else ""


func _pid(n: String) -> String:
	return manifest.parts[n].get("pid", "") if manifest.parts.has(n) else ""


func _hidden_in_instructions(n: String) -> bool:
	return _kind(n) in ["reserved", "alternate"] or cat[n] in ["reserved", "alternate"]


func _clear_extras() -> void:
	for e in extras:
		if is_instance_valid(e):
			e.queue_free()
	extras.clear()
	leader_layer = null


func _move_part(n: String, d: Vector3) -> void:
	var gt: Transform3D = parts[n].global_transform
	gt.origin = orig_pos[n] + d
	parts[n].global_transform = gt


func _reset_scene_state() -> void:
	_set_room(false)   # assembly images always use the plain studio view
	_clear_extras()
	_apply_finish(finish_key if finish_key != "" else fin.default)
	for n in parts:
		_move_part(n, Vector3.ZERO)
		parts[n].visible = true
		bodies[n].collision_layer = 0
	ground.visible = true
	ground.scale = Vector3(40, 1, 40)
	label_px.clear()
	step_mode = true
	if ui_layer:
		ui_layer.visible = false
	_select("")


func _step_offset(n: String) -> Vector3:
	var e: Array = manifest.parts[n].get("explode_mm", [0, 0, 0])
	var o := Vector3(e[0], e[2], -e[1]) / 1000.0 * STEP_PULL + Vector3(0, STEP_LIFT, 0)
	return o if o.length() >= 0.05 else Vector3(0, 0.08, 0)  # keep a visible gap/arrow when explode ~ cancels the lift


func _fade(n: String) -> void:
	var m = parts[n].material_override
	if m is StandardMaterial3D:
		var f: StandardMaterial3D = m.duplicate()
		var a: float = f.albedo_color.a
		f.albedo_color = f.albedo_color.lerp(Color(0.82, 0.83, 0.86), 0.45)
		f.albedo_color.a = a
		f.emission_enabled = false
		parts[n].material_override = f


func _apply_step(sn: int) -> void:
	_reset_scene_state()
	var frame: Array = []
	var fresh: Array = []
	for n in parts:
		var s: int = int(manifest.parts[n].get("step", 1)) if manifest.parts.has(n) else 99
		var vis: bool = not _hidden_in_instructions(n) and s <= sn
		parts[n].visible = vis
		if not vis:
			continue
		if s == sn:
			_move_part(n, _step_offset(n))
			fresh.append(n)
		else:
			_fade(n)
		if sn < 10 or s == sn or _part_aabb(n).end.y > 0.105:
			frame.append(_part_aabb(n))
	var d := _fit_camera(frame, 0.6, 0.38)
	for n in fresh:
		if _pid(n) == "":
			continue  # face details ride along with the display, no label/arrow
		var pulled_c: Vector3 = _part_aabb(n).get_center()
		var off := _step_offset(n)
		_add_arrow(pulled_c, pulled_c - off, d)
		_add_label(_pid(n), pulled_c, d, true)
	var txt: String = str(manifest.get("steps", {}).get(str(sn), ""))
	_overlay("Step %d / %d" % [sn, manifest.steps.size()], txt, LEGEND)


func _apply_part(pn: String) -> void:
	if not parts.has(pn):
		push_error("unknown part " + pn)
		return
	_reset_scene_state()
	ground.visible = false
	for n in parts:
		parts[n].visible = (n == pn)
	_fit_camera([_part_aabb(pn)], 0.6, 0.35)
	var p: Dictionary = manifest.parts[pn]
	var b: Array = p.bbox_mm
	var body := "%.1f x %.1f x %.1f mm  (W x D x H)   kind: %s\n%s" % [b[3] - b[0], b[4] - b[1], b[5] - b[2], p.get("kind", ""), p.get("note", "")]
	_overlay("%s  %s" % [p.get("pid", ""), pn], body, "")


func _apply_overview() -> void:
	_reset_scene_state()
	for n in parts:
		parts[n].visible = not _hidden_in_instructions(n)
	_set_explode(true, 1.0)
	var frame: Array = []
	for n in parts:
		if parts[n].visible:
			frame.append(_part_aabb(n))
	var d := _fit_camera(frame, 0.55, 0.3, 1.3)
	for n in parts:
		if parts[n].visible and _pid(n) != "":
			_add_label(_pid(n), _part_aabb(n).get_center(), d, false, 0.6)
	_overlay("JARVES Pebble A - part numbers", "", LEGEND)


# Place the camera on a yaw/pitch 3/4 view and pull back until every AABB corner is inside the safe screen area
# (top ~20% is reserved for the overlay text). Returns the distance.
func _fit_camera(boxes: Array, y: float, p: float, margin: float = 1.0) -> float:
	var all: AABB = boxes[0]
	for b in boxes:
		all = all.merge(b)
	yaw = y
	pitch = p
	target = all.get_center()
	var vs := get_viewport().get_visible_rect().size
	var tv := tan(deg_to_rad(cam.fov) * 0.5)
	var th := tv * vs.x / vs.y
	var d := 0.25
	while d < 8.0:
		dist = d
		_update_camera()
		var inv := cam.global_transform.affine_inverse()
		var ok := true
		for b in boxes:
			for i in 8:
				var c: Vector3 = inv * b.get_endpoint(i)
				var z := -c.z
				if z <= 0.02 or absf(c.x / (z * th)) > 0.9 or absf(c.y / (z * tv)) > 0.66:
					ok = false
					break
			if not ok:
				break
		if ok:
			break
		d *= 1.02
	d *= margin
	dist = d
	_update_camera()
	target += cam.global_transform.basis.y * (0.17 * d * tv)  # content moves down, leaving room for the title
	_update_camera()
	return d


# Find a free screen spot for a label (optionally starting to the right of its part), nearest-first, avoiding earlier
# labels; draws a leader line when the label ends up away from its part.
func _declutter(pos: Vector3, side: bool, sc: float) -> Vector3:
	var depth := (pos - cam.global_position).dot(-cam.global_transform.basis.z)
	var home := cam.unproject_position(pos)
	var vs := get_viewport().get_visible_rect().size
	var start := home + (Vector2(90.0 * sc, 0) if side else Vector2.ZERO)
	var bw := 150.0 * sc
	var bh := 78.0 * sc
	var offs: Array = []
	for ix in range(-8, 9):
		for iy in range(-8, 9):
			offs.append(Vector2(ix * bw * 0.55, iy * bh * 0.8))
	offs.sort_custom(func(a, b): return a.length_squared() < b.length_squared())
	var sp := start
	for o in offs:
		var c: Vector2 = start + o
		if c.y < 100.0 or c.y > vs.y - 60.0 or c.x < 40.0 or c.x > vs.x - 90.0:
			continue
		var clash := false
		for q in label_px:
			if absf(q.x - c.x) < bw and absf(q.y - c.y) < bh:
				clash = true
				break
		if not clash:
			sp = c
			break
	label_px.append(sp)
	if sp.distance_to(home) > 40.0 * sc:
		if leader_layer == null or not is_instance_valid(leader_layer):
			leader_layer = CanvasLayer.new()
			leader_layer.layer = 4
			add_child(leader_layer)
			extras.append(leader_layer)
		var ln2 := Line2D.new()
		ln2.points = PackedVector2Array([home, sp])
		ln2.width = 3.0
		ln2.default_color = Color(1.0, 0.95, 0.5)
		leader_layer.add_child(ln2)
		var dot := Polygon2D.new()
		var pts := PackedVector2Array()
		for i in 12:
			pts.append(home + Vector2(cos(i * TAU / 12.0), sin(i * TAU / 12.0)) * 5.0)
		dot.polygon = pts
		dot.color = Color(1.0, 0.95, 0.5)
		leader_layer.add_child(dot)
	return cam.project_position(sp, depth)


func _add_label(text: String, pos: Vector3, d: float, side: bool = false, sc: float = 1.0) -> void:
	var l := Label3D.new()
	l.text = text
	l.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	l.no_depth_test = true
	l.font_size = 96
	l.pixel_size = d * 0.00055 * sc
	l.outline_size = 28
	l.outline_modulate = Color(0, 0, 0)
	l.modulate = Color(1.0, 0.95, 0.5)
	l.render_priority = 20
	l.outline_render_priority = 19
	l.position = _declutter(pos, side, sc)
	add_child(l)
	extras.append(l)


func _arrow_mat() -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.albedo_color = ARROW_COLOR
	m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	m.no_depth_test = true
	m.render_priority = 10
	return m


func _add_arrow(from: Vector3, to: Vector3, d: float) -> void:
	var v := to - from
	var ln := v.length()
	if ln < 0.005:
		return
	var dir := v / ln
	var r := d * 0.006
	var head := minf(ln * 0.45, r * 5.0)
	var ref := Vector3.RIGHT if absf(dir.dot(Vector3.RIGHT)) < 0.9 else Vector3.FORWARD
	var bx := ref.cross(dir).normalized()
	var bs := Basis(bx, dir, bx.cross(dir))
	var mat := _arrow_mat()
	var shaft := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.top_radius = r
	cm.bottom_radius = r
	cm.height = ln - head
	cm.radial_segments = 16
	shaft.mesh = cm
	shaft.material_override = mat
	shaft.transform = Transform3D(bs, from + dir * (ln - head) * 0.5)
	add_child(shaft)
	var cone := MeshInstance3D.new()
	var cn := CylinderMesh.new()
	cn.top_radius = 0.0
	cn.bottom_radius = r * 2.6
	cn.height = head
	cn.radial_segments = 20
	cone.mesh = cn
	cone.material_override = mat
	cone.transform = Transform3D(bs, to - dir * head * 0.5)
	add_child(cone)
	extras.append(shaft)
	extras.append(cone)


func _overlay(title: String, body: String, legend: String) -> void:
	var layer := CanvasLayer.new()
	layer.layer = 5
	add_child(layer)
	extras.append(layer)
	var vs := get_viewport().get_visible_rect().size
	var t := _ov_label(title, 56, Vector2(24, 12), vs.x - 48)
	layer.add_child(t)
	if body != "":
		layer.add_child(_ov_label(body, 26, Vector2(26, 82), vs.x - 52))
	if legend != "":
		layer.add_child(_ov_label(legend, 22, Vector2(26, vs.y - 40), vs.x - 52))


func _ov_label(text: String, fs: int, pos: Vector2, w: float) -> Label:
	var l := Label.new()
	l.text = text
	l.position = pos
	l.custom_minimum_size = Vector2(w, 0)
	l.size = Vector2(w, 10)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.add_theme_font_size_override("font_size", fs)
	l.add_theme_color_override("font_color", Color(1, 1, 1))
	l.add_theme_color_override("font_outline_color", Color(0, 0, 0))
	l.add_theme_constant_override("outline_size", maxi(4, fs / 6))
	return l


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
	await _check_room(errs)   # room simulation (default view); leaves the viewer in studio mode for the checks below
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
		got = await _pick_center("Base_Shell_Band", PI / 2.0, 0.0, 1.0)  # side: front has the grille
		if got != "Base_Shell_Band":
			errs.append("pick Base_Shell_Band returned '%s'" % got)
		got = await _pick_center("Display_Panel", 0.0, 0.0, 1.0)
		if got != "Display_Panel":
			errs.append("pick face returned '%s'" % got)
		if selected != "Display_Panel" or not info.text.contains("Display_Panel"):
			errs.append("selection label not updated")
		# 4. finish switch applies the palette from finishes.json
		for k in fin.finishes:
			_apply_finish(k)
			var want: Array = fin.finishes[k][fin.roles["Base_Shell"]].color
			var m: StandardMaterial3D = parts["Base_Shell"].material_override
			if m == null or not m.albedo_color.is_equal_approx(Color(want[0], want[1], want[2])):
				errs.append("finish %s not applied" % k)
			# 5. assembly step mode (last: leaves the scene in step state)
			_apply_step(7)
			var n_lbl := 0
			for e in extras:
				if e is Label3D:
					n_lbl += 1
			var want_lbl := 0
			for n in parts:
				var s: int = int(manifest.parts[n].get("step", 1))
				var want_vis: bool = s <= 7 and not _hidden_in_instructions(n)
				if parts[n].visible != want_vis:
					errs.append("step 7: %s visible=%s, expected %s" % [n, parts[n].visible, want_vis])
				if want_vis and s == 7 and _pid(n) != "":
					want_lbl += 1
			if n_lbl != want_lbl or want_lbl == 0:
				errs.append("step 7: %d labels, expected %d" % [n_lbl, want_lbl])
	print("CHECK RESULT: ", "FAIL " + "; ".join(errs) if errs else "PASS (%d parts, %d finishes)" % [parts.size(), fin.finishes.size()])
	get_tree().quit(1 if errs else 0)


func _check_room(errs: Array[String]) -> void:
	# a. the room is the default view and has its main pieces
	if room == null or not room_on or not room.visible:
		errs.append("room is not the default view")
		return
	for nm in ["Floor", "Ceiling", "Wall_Back", "Wall_Left_Low", "Window_Glass", "Desk_Top", "Chair", "Bookshelf", "Plant",
			"Rug", "Sofa", "Pendant", "Sun", "Pendant_Light"]:
		if room.find_child(nm, true, false) == null:
			errs.append("room: missing " + nm)
	# b. device stands on the desk top (+-1 cm) and inside its footprint; desk ~75 cm above the floor
	var dev := _device_aabb()
	var dm: MeshInstance3D = room.desk_top_mesh
	var desk: AABB = dm.global_transform * dm.get_aabb()
	var fm: MeshInstance3D = room.find_child("Floor", true, false)
	var floor_y: float = (fm.global_transform * fm.get_aabb()).end.y
	var gap := dev.position.y - desk.end.y
	if absf(gap) > 0.01:
		errs.append("device bottom %.1f mm from the desk top" % (gap * 1000.0))
	if dev.position.x < desk.position.x or dev.end.x > desk.end.x or dev.position.z < desk.position.z or dev.end.z > desk.end.z:
		errs.append("device footprint not on the desk")
	if absf(desk.end.y - floor_y - 0.75) > 0.03:
		errs.append("desk height %.0f mm" % ((desk.end.y - floor_y) * 1000.0))
	print("room: desk top %.0f mm above floor, device gap %.2f mm" % [(desk.end.y - floor_y) * 1000.0, gap * 1000.0])
	# c. the live face renders through its SubViewport and every camera preset sees it unobstructed
	if face_quad == null or face_vp == null or face_vp.render_target_update_mode != SubViewport.UPDATE_ALWAYS \
			or (face_quad.material_override as StandardMaterial3D).albedo_texture != face_vp.get_texture():
		errs.append("live face SubViewport not wired to the display")
	# The room must apply the same presentation messages as the standalone face window.
	if assistant == null or assistant.appearance != appearance:
		errs.append("room appearance receiver missing")
	else:
		for mode in appearance.MODES:
			assistant._on_message("appearance.set", {"mode": mode})
			if appearance.mode != mode or (mode in ["avatar2d", "avatar3d"] and appearance.avatar == null):
				errs.append("room appearance not applied: " + mode)
		assistant._on_message("appearance.set", {"mode": "face"})
		assistant.strip.locale = "en"
		assistant._on_message("status", {"mic_muted": true})
		if assistant.options_box.visible or assistant.mute_chk.get_parent() == assistant.options_box \
				or not assistant.mute_chk.visible or assistant.mute_chk.text != "Unmute microphone":
			errs.append("room microphone recovery hidden or mislabeled")
		assistant.mute_chk.toggled.emit(false)
		if assistant.last_sent != ["mic.set_mute", {"muted": false}]:
			errs.append("room unmute button did not request microphone recovery")
	await get_tree().physics_frame
	var vs := get_viewport().get_visible_rect().size
	for k in ROOM_CAMS:
		_apply_room_cam(k)
		await get_tree().physics_frame
		await get_tree().physics_frame
		var fc := face_quad.global_position
		var cp := cam.global_position
		var eye_y := cp.y - floor_y
		if cam.is_position_behind(fc):
			errs.append("cam %s: face behind the camera" % k)
			continue
		var sp := cam.unproject_position(fc)
		var edge := cam.unproject_position(fc + face_quad.global_basis.x * 0.066)
		if sp.x < vs.x * 0.05 or sp.x > vs.x * 0.95 or sp.y < vs.y * 0.05 or sp.y > vs.y * 0.95:
			errs.append("cam %s: face off screen at %s" % [k, sp])
		if (edge - sp).length() * 2.0 < vs.y * 0.03:
			errs.append("cam %s: face too small on screen" % k)
		if (cp - fc).normalized().dot(face_quad.global_basis.z) < 0.5:
			errs.append("cam %s: face not turned to the camera" % k)
		var q := PhysicsRayQueryParameters3D.create(cp, fc + (fc - cp).normalized() * 0.02)
		var hit := get_world_3d().direct_space_state.intersect_ray(q)
		var by := "" if hit.is_empty() else String(hit.collider.get_parent().name)
		if by != "Display_Panel":
			errs.append("cam %s: view of the face blocked by '%s'" % [k, by])
		print("room cam %s: eye %.2f m above floor, %.2f m from face, face %.1f%% of screen height, ray hits %s" % [k, eye_y, cp.distance_to(fc), (edge - sp).length() * 200.0 / vs.y, by])
	_apply_room_cam("desk")
	if absf(cam.global_position.y - floor_y - 1.6) > 0.1 or cam.global_position.distance_to(face_quad.global_position) > 2.0:
		errs.append("desk view is not at standing eye height ~1.5-2 m from the device")
	# d. day / evening lighting
	room.set_evening(true)
	we.environment = room.current_env()
	if room.sun.visible or not room.pendant.visible or not room.pendant.shadow_enabled or we.environment != room.env_eve:
		errs.append("evening lighting not applied")
	room.set_evening(false)
	we.environment = room.current_env()
	if not room.sun.visible or room.pendant.visible or we.environment != room.env_day:
		errs.append("day lighting not restored")
	# e. studio toggle hides the room and its collision
	_set_room(false)
	if room.visible or not studio.visible or we.environment != env_studio:
		errs.append("studio toggle failed")
	for b in room.bodies:
		if b.collision_layer != 0:
			errs.append("room collision still active in studio")
			break
	await get_tree().physics_frame


func _check_new(errs: Array[String]) -> void:
	# a. default = blank ring visible, optional LED ring hidden; the LED toggle swaps them
	_set_layer(true, "exterior")
	for n in BLANKS:
		if not parts[n].visible:
			errs.append(n + " hidden by default")
	for n in LED_RING:
		if parts[n].visible:
			errs.append(n + " visible by default")
	_set_blank(true)
	for n in BLANKS:
		if parts[n].visible:
			errs.append(n + " still visible with the LED ring fitted")
	for n in ["LED_Diffuser_Front", "LED_Diffuser_Back"]:
		if not parts[n].visible:
			errs.append(n + " not shown with the LED ring fitted")
	_set_blank(false)
	if not parts["LED_Blank_Front"].visible or parts["LED_Diffuser_Front"].visible:
		errs.append("LED toggle off did not restore the blank ring")
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
	for pk in [["Head_Front", PI / 2.0], ["Head_Back", PI]]:
		var n: String = pk[0]
		var got: String = await _pick_center(n, pk[1], 0.3, 1.2)
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
