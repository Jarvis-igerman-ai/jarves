extends Control
## Selectable presentation (docs/APPEARANCE.md): "face" (the original shader face), "avatar2d", "avatar3d" or
## "hidden" (no renderer drawn). Switched at runtime by the Core message `appearance.set {mode}`; the Core, the
## voice and the link are never touched by a switch.
## ONE behaviour driver: face.gd keeps running in every mode (blinks, gaze, breathing, reactions, speech level,
## camera gaze) and each avatar draws face.pose(). So the expression / activity / speech-level / gaze contract
## is literally the same for all renderers, and an avatar can never blink or move more than the face.
## The activity pill (face_activity.gd) and the status dot stay visible in the avatar modes.

const MODES := ["face", "avatar2d", "avatar3d", "hidden"]
const Avatar2D := preload("res://face/avatar_2d.gd")
const Avatar3D := preload("res://face/avatar_3d.gd")
const FADE_IN := 0.45                       # s: calm cross-fade into the new presentation

var face: Control                           # face.gd, owned by the app (face_app.gd)
var mode := "face"
var avatar: Control                         # the current avatar renderer (null in face / hidden mode)
var switches := 0                           # applied switches (diagnostics / tests)
var _fade := 1.0
var _overlay: Control                       # status dot + camera glyph for the avatar modes


func setup(f: Control) -> void:
	face = f
	_overlay = Control.new()
	_overlay.name = "AvatarOverlay"
	_overlay.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_overlay.draw.connect(_draw_overlay)
	add_child(_overlay)


static func valid(m) -> bool:
	return typeof(m) == TYPE_STRING and m in MODES


func set_mode(m) -> bool:
	# unknown / malformed mode -> ignored (the current presentation stays); same mode -> no-op
	if not valid(m):
		return false
	if m == mode:
		return true
	if avatar != null:
		avatar.queue_free()         # avatars are built on demand and freed when left (Pi RAM/GPU)
		avatar = null
	mode = m
	switches += 1
	if mode == "avatar2d":
		avatar = Avatar2D.new()
	elif mode == "avatar3d":
		avatar = Avatar3D.new()
	if avatar != null:
		avatar.name = "Avatar"
		avatar.face = face
		add_child(avatar)
		move_child(avatar, 0)       # under the face node, so the activity pill draws on top
		avatar.apply_pose(face.pose())
	face.visible = mode != "hidden"
	face.set_canvas_visible(mode == "face")
	_fade = 0.0
	_layout_children()
	return true


func layout(rect: Rect2) -> void:
	position = Vector2.ZERO
	size = rect.position * 2.0 + rect.size
	face.position = rect.position
	face.size = rect.size
	_layout_children()


func _layout_children() -> void:
	if face == null:
		return
	for n in [avatar, _overlay]:
		if n != null:
			n.position = face.position
			n.size = face.size


func _process(delta: float) -> void:
	if _fade < 1.0:
		_fade = minf(_fade + delta / FADE_IN, 1.0)
	var a := _fade * _fade * (3.0 - 2.0 * _fade)
	if avatar != null:
		avatar.modulate.a = a
	elif mode == "face":
		face.get_child(0).modulate.a = a
	if _overlay != null:
		_overlay.visible = avatar != null
		_overlay.modulate.a = a
		_overlay.queue_redraw()


func _draw_overlay() -> void:
	# the same truthful status dot as the face shader (face.gd DOT / DOT_POS), small camera-on glyph upper-left
	if face == null:
		return
	var u: float = minf(_overlay.size.x, _overlay.size.y) * 0.5
	var c := _overlay.size * 0.5
	var col: Color = face.dot_color()
	var pulse := 1.0
	if face.link_state == "live":
		pulse = 0.75 + 0.25 * sin(face.t * TAU * 0.6)
	elif face.link_state == "connecting":
		pulse = 0.55 + 0.45 * (0.5 + 0.5 * sin(face.t * TAU * 0.4))
	var dp: Vector2 = c + face.DOT_POS * u
	draw_dot(dp, u, col, pulse, face.link_state == "muted")
	if face.camera_on:
		var cp := c + Vector2(-face.DOT_POS.x, face.DOT_POS.y) * u
		_overlay.draw_rect(Rect2(cp - Vector2(0.03, 0.02) * u, Vector2(0.06, 0.04) * u), Color(1, 1, 1, 0.75))
		_overlay.draw_circle(cp, 0.012 * u, Color(0.1, 0.1, 0.12))


func draw_dot(dp: Vector2, u: float, col: Color, pulse: float, slashed: bool) -> void:
	_overlay.draw_circle(dp, 0.05 * u, Color(col, 0.18 * pulse))
	_overlay.draw_circle(dp, 0.028 * u, Color(col, pulse))
	if slashed:
		_overlay.draw_line(dp + Vector2(-0.04, 0.04) * u, dp + Vector2(0.04, -0.04) * u, Color(0.1, 0.1, 0.12), 0.012 * u, true)
