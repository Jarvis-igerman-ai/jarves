extends Control
## JARVES animated face (shared by the 3D twin and the on-device face display, docs/FACE_RUNTIME.md).
## Procedural, resolution-independent drawing in the style of assets/JARVIS_visual_reference.png:
## glowing ring eyes with highlights, arched brows, small smile, on a dark round screen.
## States (face.set_state from the Core): idle, listening, thinking, speaking, happy, error.

const STATES := ["idle", "listening", "thinking", "speaking", "happy", "error"]
const BLUE := Color(0.42, 0.74, 1.0)
const RED := Color(1.0, 0.35, 0.3)

var state := "idle"
var t := 0.0
var _blink := 1.0          # 1 open .. 0 closed
var _next_blink := 2.5
var _blink_t := -1.0
# smoothed parameters
var _open := 1.0
var _look := Vector2.ZERO
var _brow := 0.0
var _mouth := 0.0          # 0 smile .. 1 open
var _col := BLUE
var _ring := 0.0           # outer screen ring intensity (listening)
var _happy := 0.0
var _rng := RandomNumberGenerator.new()


func set_state(s: String) -> void:
	if s in STATES:
		state = s
		queue_redraw()


func _process(delta: float) -> void:
	t += delta
	var target_open := 1.0
	var target_look := Vector2(sin(t * 0.37) * 0.15, sin(t * 0.23) * 0.08)
	var target_brow := 0.0
	var target_mouth := 0.0
	var target_col := BLUE
	var target_ring := 0.15
	var target_happy := 0.0
	match state:
		"listening":
			target_open = 1.12
			target_look = Vector2.ZERO
			target_brow = 0.35
			target_ring = 0.65 + 0.35 * sin(t * 5.0)
		"thinking":
			target_open = 0.72
			target_look = Vector2(0.55, -0.55)
			target_brow = -0.25
			target_ring = 0.3
		"speaking":
			target_mouth = 0.25 + 0.75 * absf(sin(t * 11.0) * sin(t * 4.3 + 1.0))
			target_ring = 0.35
		"happy":
			target_happy = 1.0
			target_brow = 0.5
		"error":
			target_col = RED
			target_open = 0.6
			target_brow = -0.6
			target_ring = 0.4 + 0.3 * sin(t * 8.0)
	var k := clampf(delta * 10.0, 0.0, 1.0)
	_open = lerpf(_open, target_open, k)
	_look = _look.lerp(target_look, k * 0.6)
	_brow = lerpf(_brow, target_brow, k)
	_mouth = lerpf(_mouth, target_mouth, clampf(delta * 18.0, 0.0, 1.0))
	_col = _col.lerp(target_col, k)
	_ring = lerpf(_ring, target_ring, k)
	_happy = lerpf(_happy, target_happy, k)
	# natural blinking (not while listening intently)
	if _blink_t >= 0.0:
		_blink_t += delta
		_blink = absf(cos(clampf(_blink_t / 0.16, 0.0, 1.0) * PI))
		if _blink_t > 0.16:
			_blink_t = -1.0
			_blink = 1.0
	elif t > _next_blink and state != "listening":
		_blink_t = 0.0
		_next_blink = t + _rng.randf_range(2.2, 5.5)
	queue_redraw()


func _glow_arc(c: Vector2, r: float, a0: float, a1: float, w: float, col: Color) -> void:
	for i in range(2, 0, -1):     # soft glow: wide faint passes under the core line
		draw_arc(c, r, a0, a1, 48, Color(col, 0.10 * (4 - i)), w * (1.0 + i * 0.9), true)
	draw_arc(c, r, a0, a1, 48, col, w, true)


func _glow_ring(c: Vector2, r: float, w: float, col: Color) -> void:
	for i in range(2, 0, -1):
		draw_circle(c, r, Color(col, 0.10 * (3 - i)), false, w * (1.0 + i * 0.9), true)
	draw_circle(c, r, col, false, w, true)


func _eye(c: Vector2, u: float, mirror: float) -> void:
	var r := u * 0.112
	var open := clampf(_open * _blink, 0.04, 1.3)
	if _happy > 0.5:              # ^ ^ eyes
		_glow_arc(c + Vector2(0, r * 0.25), r * 0.95, PI * 1.12, PI * 1.88, u * 0.022, _col)
		return
	draw_set_transform(c, 0.0, Vector2(1.0, open))
	_glow_ring(Vector2.ZERO, r, u * 0.019, _col)
	var p := _look * r * 0.38
	draw_circle(p, r * 0.62, Color(_col.darkened(0.55), 0.95))
	draw_circle(p, r * 0.42, Color(0.02, 0.04, 0.09))
	draw_circle(p + Vector2(-r * 0.22, -r * 0.24), r * 0.17, Color(1, 1, 1, 0.95))
	draw_circle(p + Vector2(r * 0.18, r * 0.20), r * 0.07, Color(1, 1, 1, 0.8))
	draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)
	# brow: short arc above the eye, tilted by state
	var bc := c + Vector2(r * 0.15 * mirror, -r * (1.45 + 0.25 * _brow))
	var tilt := -0.25 * _brow * mirror
	_glow_arc(bc, r * 0.75, PI * 1.25 + tilt, PI * 1.62 + tilt, u * 0.016, _col)


func _draw() -> void:
	var u: float = min(size.x, size.y)
	var c := size / 2.0
	draw_circle(c, u * 0.5, Color(0.0, 0.0, 0.0))
	draw_circle(c, u * 0.47, Color(0.02, 0.03, 0.06))
	if _ring > 0.01:
		_glow_ring(c, u * 0.485, u * 0.008, Color(_col, clampf(_ring, 0.0, 1.0)))
	var ex := u * 0.185
	var ey := -u * 0.04
	_eye(c + Vector2(-ex, ey), u, -1.0)
	_eye(c + Vector2(ex, ey), u, 1.0)
	var mc := c + Vector2(0, u * 0.13)
	if _mouth > 0.06:
		var h := u * 0.055 * _mouth
		draw_set_transform(mc, 0.0, Vector2(1.0, maxf(h / (u * 0.05), 0.05)))
		draw_circle(Vector2.ZERO, u * 0.05, Color(_col, 0.9))
		draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)
	else:
		_glow_arc(mc - Vector2(0, u * 0.045), u * 0.07, PI * 0.25, PI * 0.75, u * 0.016, _col)
	if state == "thinking":
		for i in 3:
			var a := t * 3.0 + i * TAU / 3.0
			draw_circle(c + Vector2(u * 0.3, -u * 0.28) + Vector2(cos(a), sin(a)) * u * 0.035, u * 0.011, Color(_col, 0.8))
	elif state == "error":
		draw_string(ThemeDB.fallback_font, c + Vector2(-u * 0.02, u * 0.33), "!", HORIZONTAL_ALIGNMENT_CENTER, -1,
				int(u * 0.09), _col)
