extends Control
## Simple 2D avatar (appearance "avatar2d"): a stylized robot buddy drawn with canvas primitives only (no image
## assets, so it is cheap on the Pi and easy to restyle). It owns NO motion: every frame it draws face.gd's
## pose() (docs/APPEARANCE.md), so blinks, gaze, breathing, reactions and the speaking mouth are identical to the
## face and as calm. Swap the look by editing STYLE (colours / proportions) or replace this script entirely: the
## contract is only `face` (the behaviour driver) + `apply_pose(pose)`.

const STYLE := {
	"head_r": 0.7,                         # head radius (unit = half the square size)
	"head_col": Color(0.11, 0.14, 0.2), "head_hi": Color(1, 1, 1, 0.05), "rim_w": 0.018,
	"eye_x": 0.27, "eye_y": -0.06, "eye_rx": 0.12, "eye_ry": 0.15,
	"sclera": Color(0.9, 0.95, 1.0), "iris_r": 0.072, "pupil_r": 0.032, "pupil": Color(0.04, 0.06, 0.1),
	"brow": Color(0.72, 0.8, 0.9, 0.9), "brow_w": 0.022,
	"mouth_y": 0.3, "mouth_scale": 2.2, "mouth_ink": Color(0.03, 0.04, 0.07),
	"blush": Color(1.0, 0.45, 0.6), "tear": Color(0.55, 0.8, 1.0),
	"look_head": Vector2(0.012, 0.01),     # head parallax per look unit (tiny: the eyes do the looking)
	"look_iris": Vector2(0.06, 0.05),      # iris offset per look unit
	"breath_y": 0.004,
}

var face: Control                          # face.gd (behaviour driver); null in unit tests that feed poses
var p := {}                                # last pose drawn
# derived values (also read by the self-test)
var eye_open := 1.0                        # drawn eye height / full height
var head_off := Vector2.ZERO               # head offset in unit radii
var iris_off := Vector2.ZERO
var mouth_curve := 0.0                     # + = smile (corners up)
var mouth_gap := 0.0                       # mouth opening in unit radii


func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE


func _process(_delta: float) -> void:
	if face != null and visible:
		apply_pose(face.pose())


func apply_pose(pose: Dictionary) -> void:
	p = pose
	var look: Vector2 = pose.get("look", Vector2.ZERO)
	var bounce: float = -float(pose.get("laugh", 0.0)) * 0.009 * (0.5 - 0.5 * cos(TAU * 2.0 * float(pose.get("t", 0.0))))
	head_off = Vector2(look.x * STYLE.look_head.x, look.y * STYLE.look_head.y + STYLE.breath_y * float(pose.get("breath", 0.0)) + bounce)
	iris_off = Vector2(clampf(look.x * STYLE.look_iris.x, -0.05, 0.05), clampf(look.y * STYLE.look_iris.y, -0.06, 0.06))
	eye_open = clampf(float(pose.get("open", 1.0)) * (1.0 - float(pose.get("sleep", 0.0))), 0.0, 1.3)
	mouth_curve = float(pose.get("smile", 0.0))
	mouth_gap = float(pose.get("mouth_open", 0.0)) * 0.13
	queue_redraw()


func _ellipse(c: Vector2, r: Vector2, n := 28) -> PackedVector2Array:
	var pts := PackedVector2Array()
	for i in n:
		var a := TAU * i / n
		pts.append(c + Vector2(cos(a) * r.x, sin(a) * r.y))
	return pts


func _draw() -> void:
	if p.is_empty():
		return
	var u: float = minf(size.x, size.y) * 0.5
	var c := size * 0.5 + head_off * u
	var col: Color = p.get("col", Color(0.36, 0.78, 1.0))
	var bright: float = float(p.get("bright", 1.0))
	var asleep: bool = p.get("asleep", false)
	var ring: float = float(p.get("ring", 0.0))
	var hr: float = STYLE.head_r * u
	# antenna + soft light (listening rim / thinking comet become a calm glow here, no spinning)
	var tip := c + Vector2(0, -hr - 0.13 * u)
	draw_line(c + Vector2(0, -hr + 0.02 * u), tip, STYLE.head_col.lightened(0.25), 0.025 * u, true)
	var think: float = float(p.get("spin", 0.0)) * (0.5 + 0.5 * sin(float(p.get("t", 0.0)) * 1.2))
	draw_circle(tip, 0.05 * u, col * Color(1, 1, 1, 0.25 + 0.5 * clampf(ring + think, 0.0, 1.0)) * bright)
	draw_circle(tip, 0.03 * u, col.lerp(Color.WHITE, 0.3) * bright)
	# ears
	for sx in [-1.0, 1.0]:
		var ear := Rect2(c + Vector2(sx * hr - 0.05 * u, -0.12 * u), Vector2(0.1 * u, 0.24 * u))
		draw_rect(ear, STYLE.head_col.lightened(0.12))
		draw_rect(ear.grow(-0.02 * u), col * Color(1, 1, 1, 0.3 + 0.5 * clampf(ring, 0.0, 1.0)) * bright)
	# head, top highlight and accent rim
	draw_circle(c, hr, STYLE.head_col)
	draw_colored_polygon(_ellipse(c + Vector2(-0.18 * hr, -0.62 * hr), Vector2(0.38 * hr, 0.18 * hr)), STYLE.head_hi)
	draw_arc(c, hr, 0.0, TAU, 72, col * Color(1, 1, 1, 0.35 + 0.5 * clampf(ring, 0.0, 1.0)) * bright, STYLE.rim_w * u, true)
	# blush
	var blush: float = float(p.get("blush", 0.0))
	if blush > 0.01:
		for sx in [-1.0, 1.0]:
			draw_colored_polygon(_ellipse(c + Vector2(sx * 0.43 * u, 0.13 * u), Vector2(0.09, 0.05) * u),
					STYLE.blush * Color(1, 1, 1, 0.35 * blush))
	_draw_eyes(c, u, col, bright, asleep)
	_draw_mouth(c, u, col, bright)


func _draw_eyes(c: Vector2, u: float, col: Color, bright: float, asleep: bool) -> void:
	var curve: float = float(p.get("curve", 0.0))
	var lid_low: Vector2 = p.get("lid_low", Vector2.ZERO)
	var lid_up: Vector2 = p.get("lid_up", Vector2.ZERO)
	var tilt: Vector2 = p.get("lid_tilt", Vector2.ZERO)
	var iris_k: float = float(p.get("iris", 1.0))
	for i in 2:
		var sx := -1.0 if i == 0 else 1.0
		var e := c + Vector2(sx * STYLE.eye_x * u, STYLE.eye_y * u)
		var rx: float = STYLE.eye_rx * u
		var ry: float = STYLE.eye_ry * u * eye_open
		var line_col := STYLE.sclera * Color(1, 1, 1, bright)
		if asleep or ry < 0.018 * u:
			# closed: a soft downward arc
			draw_arc(e + Vector2(0, -0.02 * u), rx * 0.85, deg_to_rad(20), deg_to_rad(160), 16, line_col * Color(1, 1, 1, 0.7), 0.022 * u, true)
		elif curve > 0.5:
			# happy / laugh crescent ^ ^
			draw_arc(e + Vector2(0, 0.05 * u), rx * 0.9, deg_to_rad(200), deg_to_rad(340), 18, line_col, 0.03 * u, true)
		else:
			draw_colored_polygon(_ellipse(e, Vector2(rx, ry)), STYLE.sclera * Color(bright, bright, bright, 1.0))
			var io := Vector2(iris_off.x * u, iris_off.y * u)
			var ir: float = minf(STYLE.iris_r * u * maxf(iris_k, 0.3), ry * 0.95)
			draw_circle(e + io, ir, col * Color(bright, bright, bright, 1.0))
			draw_circle(e + io, minf(STYLE.pupil_r * u, ir * 0.6), STYLE.pupil)
			draw_circle(e + io + Vector2(-0.025, -0.03) * u, 0.016 * u, Color(1, 1, 1, 0.85))
			var lid_h: float = (lid_low.x if i == 0 else lid_low.y)
			if lid_h > 0.01:
				# lower lid rises (squint): head-coloured cover from below
				draw_colored_polygon(_ellipse(e + Vector2(0, ry * (2.0 - 2.0 * lid_h)), Vector2(rx * 1.2, ry)), STYLE.head_col)
		# brows: tilt < 0 raises the inner end (worried / sad), lid_up lowers the brow (thinking / error)
		var up: float = lid_up.x if i == 0 else lid_up.y
		var tl: float = tilt.x if i == 0 else tilt.y
		var by: float = (STYLE.eye_y - STYLE.eye_ry - 0.07 + up * 0.12) * u
		var inner := Vector2(-sx * 0.05 * u, by + tl * 0.05 * u)
		var outer := Vector2(sx * 0.11 * u, by - tl * 0.02 * u)
		draw_line(Vector2(c.x + sx * STYLE.eye_x * u, c.y) + inner, Vector2(c.x + sx * STYLE.eye_x * u, c.y) + outer,
				STYLE.brow * Color(1, 1, 1, bright * (0.35 + 0.65 * clampf(absf(tl) + up * 2.0, 0.0, 1.0))), STYLE.brow_w * u, true)
		# tears (cry): one small drop slides down below each eye
		var cry: float = float(p.get("cry", 0.0))
		if cry > 0.02:
			var ph := fmod(float(p.get("t", 0.0)) * 0.28 + i * 0.5, 1.0)
			draw_circle(e + Vector2(sx * 0.04 * u, ry + (0.03 + ph * 0.2) * u), 0.018 * u, STYLE.tear * Color(1, 1, 1, cry * (1.0 - ph)))
		# heart glints (love)
		var heart: float = float(p.get("heart", 0.0))
		if heart > 0.02:
			var h := e + Vector2(sx * 0.1 * u, -0.12 * u)
			var hc := STYLE.blush * Color(1, 1, 1, heart)
			draw_circle(h + Vector2(-0.012, 0) * u, 0.014 * u, hc)
			draw_circle(h + Vector2(0.012, 0) * u, 0.014 * u, hc)
			draw_colored_polygon(PackedVector2Array([h + Vector2(-0.026, 0.004) * u, h + Vector2(0.026, 0.004) * u, h + Vector2(0, 0.034) * u]), hc)


func _draw_mouth(c: Vector2, u: float, col: Color, bright: float) -> void:
	var w: float = float(p.get("mouth_w", 0.085)) * STYLE.mouth_scale * u
	var m := c + Vector2(float(p.get("mouth_x", 0.0)) * u, STYLE.mouth_y * u)
	var ink := col.lerp(Color.WHITE, 0.4) * Color(bright, bright, bright, 1.0)
	if p.get("asleep", false):
		draw_line(m + Vector2(-w * 0.4, 0), m + Vector2(w * 0.4, 0), ink * Color(1, 1, 1, 0.5), 0.02 * u, true)
		return
	if float(p.get("mouth_o", 0.0)) > 0.5:
		var r := (0.04 + mouth_gap * 0.25) * u
		draw_circle(m + Vector2(0, 0.02 * u), r, STYLE.mouth_ink)
		draw_arc(m + Vector2(0, 0.02 * u), r, 0.0, TAU, 24, ink, 0.018 * u, true)
		return
	var n := 16
	var top := PackedVector2Array()
	var bottom := PackedVector2Array()
	for i in n + 1:
		var x := -1.0 + 2.0 * i / n
		var bend := 1.0 - x * x
		top.append(m + Vector2(x * w, mouth_curve * 0.07 * u * bend))
		bottom.append(m + Vector2(x * w, mouth_curve * 0.07 * u * bend + mouth_gap * u * bend))
	if mouth_gap > 0.006:
		var poly := top.duplicate()
		for i in range(n, -1, -1):
			poly.append(bottom[i])
		draw_colored_polygon(poly, STYLE.mouth_ink)
		draw_polyline(bottom, ink, 0.016 * u, true)
	draw_polyline(top, ink, 0.022 * u, true)
