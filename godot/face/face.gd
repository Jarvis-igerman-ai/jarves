extends Control
## JARVES animated face (shared by the 3D twin and the on-device face display, docs/FACE_RUNTIME.md).
## Rendered by ONE canvas shader (face.gdshader: SDF eyes, lids, mouth, rim, status dot); this script owns the
## behaviour: per-state expression targets, frame-rate independent smoothing, blinks, saccades, breathing.
## States (face.set_state from the Core): idle, listening, thinking, speaking, happy, error.
## Link (status dot, set_link): ready, live, connecting, degraded, offline, error, muted. "offline" = sleep look.
## No text is ever drawn on the face. `--low` drops the wide glow / glass passes for slow GPUs.

const STATES := ["idle", "listening", "thinking", "speaking", "happy", "error"]
const LINKS := ["ready", "live", "connecting", "degraded", "offline", "error", "muted"]
const SHADER := preload("res://face/face.gdshader")
const BLUE := Color(0.36, 0.78, 1.0)
const HAPPY := Color(0.42, 0.88, 1.0)
const RED := Color(1.0, 0.36, 0.28)
const SLEEP := Color(0.38, 0.5, 0.7)
## Status dot: on the upper-right edge of the screen, like a watch/phone status light (unit-disc coords, y down).
## Not bottom-centre (reads as a chin) and not 12 o'clock (the bezel already has a physical LED there).
const DOT_POS := Vector2(0.625, -0.625)
const DOT := {
	"ready": Color(0.22, 0.95, 0.5), "live": Color(0.3, 0.62, 1.0), "connecting": Color(1.0, 0.7, 0.2),
	"degraded": Color(1.0, 0.7, 0.2), "offline": Color(1.0, 0.27, 0.25), "error": Color(1.0, 0.27, 0.25),
	"muted": Color(0.62, 0.65, 0.7),
}

var state := "idle"
var link_state := "offline"
var low_quality := false
var t := 0.0
var _pop := 0.0                     # tweened squash on every state change (anticipation)
var _speech_level := 0.0
var _speech_seen := -1000
var _mic_level := 0.0
var _mic_seen := -1000
var _mouth := 0.0                   # smoothed mouth opening, 0 when there is no speaker audio
var _mic := 0.0
var _blink := 1.0                   # 1 open .. 0 closed
var _blink_t := -1.0
var _next_blink := 2.0
var _double_blink := false
var _gaze := Vector2.ZERO           # saccade target offset
var _micro := Vector2.ZERO
var _next_saccade := 1.0
var _next_micro := 0.3
var _v := {}                        # smoothed shader values
var _mat: ShaderMaterial
var _rng := RandomNumberGenerator.new()


func _init() -> void:
	low_quality = "--low" in OS.get_cmdline_user_args()
	var canvas := ColorRect.new()
	canvas.name = "FaceCanvas"
	canvas.mouse_filter = Control.MOUSE_FILTER_IGNORE
	canvas.set_anchors_preset(Control.PRESET_FULL_RECT)
	_mat = ShaderMaterial.new()
	_mat.shader = SHADER
	canvas.material = _mat
	add_child(canvas)
	_v = _targets()
	_apply()


func set_state(s: String) -> void:
	if s in STATES and s != state:
		state = s
		if is_inside_tree():
			var tw := create_tween()
			tw.tween_property(self, "_pop", 1.0, 0.08).set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_OUT)
			tw.tween_property(self, "_pop", 0.0, 0.32).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
		_next_saccade = t + 0.4


func set_link(value: String) -> void:
	if value in LINKS:
		link_state = value


func set_speech_level(level: float) -> void:
	_speech_level = clampf(level, 0.0, 1.0)
	_speech_seen = Time.get_ticks_msec()


func set_mic_level(level: float) -> void:
	_mic_level = clampf(level, 0.0, 1.0)
	_mic_seen = Time.get_ticks_msec()


func snap() -> void:
	# jump straight to the current expression (previews/screenshots; live use always transitions)
	_v = _targets()
	_apply()


func is_asleep() -> bool:
	return link_state == "offline"


func dot_color() -> Color:
	return DOT[link_state]


func _targets() -> Dictionary:
	# expression targets for the current state (all smoothed in _process -> transitions are always continuous)
	var e := {
		"col": BLUE, "bright": 1.0, "glow": 1.0, "open": 1.0, "w": 1.0, "lid_up": Vector2.ZERO,
		"lid_tilt": Vector2.ZERO, "lid_low": Vector2.ZERO, "curve": 0.0, "sleep": 0.0, "iris": 1.0,
		"smile": 0.55, "mouth_open": 0.0, "mouth_w": 0.085, "mouth_x": 0.0, "mouth_a": 1.0, "ring": 0.0,
		"ring_col": BLUE, "spin": 0.0, "blush": 0.0, "jitter": 0.0, "look": Vector2.ZERO, "ambient": 1.0,
	}
	match state:
		"listening":
			e.open = 1.1; e.w = 1.04; e.lid_low = Vector2.ZERO; e.smile = 0.3; e.mouth_w = 0.07
			e.ring = 0.32 + _mic * 1.6; e.glow = 1.15 + _mic * 0.5; e.ambient = 1.3
		"thinking":
			e.open = 0.92; e.lid_up = Vector2(0.17, 0.06); e.lid_tilt = Vector2(-0.15, 0.1)
			e.lid_low = Vector2(0.18, 0.05); e.smile = -0.15; e.mouth_w = 0.055; e.mouth_x = 0.035
			e.look = Vector2(0.62, -0.62) + Vector2(sin(t * 0.7), cos(t * 0.5)) * 0.1; e.spin = 1.0
		"speaking":
			e.smile = 0.3; e.mouth_w = 0.09 + _mouth * 0.02; e.mouth_open = _mouth
			e.lid_low = Vector2(0.04, 0.04); e.glow = 1.0 + _mouth * 0.35; e.ring = 0.08 + _mouth * 0.25
		"happy":
			e.col = HAPPY; e.open = 1.0; e.lid_low = Vector2(0.3, 0.3); e.curve = 1.0; e.iris = 0.35
			e.smile = 1.0; e.mouth_w = 0.13; e.mouth_open = 0.5; e.blush = 1.0; e.glow = 1.2; e.ambient = 1.3
		"error":
			e.col = RED; e.ring_col = RED; e.open = 0.9; e.lid_up = Vector2(0.2, 0.2); e.lid_tilt = Vector2(-0.75, -0.75)
			e.lid_low = Vector2(0.08, 0.08); e.smile = -0.75; e.mouth_w = 0.07; e.look = Vector2(0.0, 0.3)
			e.ring = 0.35 + 0.25 * sin(t * 4.0); e.jitter = 1.0
	if is_asleep():
		e.col = SLEEP; e.bright = 0.55 + 0.12 * sin(t * 1.1); e.glow = 0.6; e.sleep = 1.0; e.iris = 0.0
		e.smile = 0.15; e.mouth_w = 0.04; e.mouth_open = 0.0; e.mouth_a = 0.0; e.ring = 0.0; e.spin = 0.0
		e.blush = 0.0; e.jitter = 0.0; e.look = Vector2(0.0, 0.25); e.ambient = 0.5
	return e


func _process(delta: float) -> void:
	t += delta
	var now := Time.get_ticks_msec()
	# mouth follows the real speaker level only; no audio -> closed (no fake flapping)
	var speech := _speech_level if state == "speaking" and now - _speech_seen < 250 else 0.0
	_mouth = lerpf(_mouth, speech, 1.0 - exp(-delta * (32.0 if speech > _mouth else 14.0)))
	var mic := _mic_level if now - _mic_seen < 600 else 0.0
	_mic = lerpf(_mic, mic, 1.0 - exp(-delta * (20.0 if mic > _mic else 6.0)))
	_update_eyes(delta)
	var e := _targets()
	var k := 1.0 - exp(-delta * 9.0)
	for key in e:
		var target = e[key]
		var rate := k
		if key == "look":
			rate = 1.0 - exp(-delta * 16.0)
			target = target + (_gaze + _micro) * (0.0 if is_asleep() else 1.0)
		elif key in ["mouth_open", "ring", "glow", "bright"]:
			rate = 1.0 - exp(-delta * 22.0)
		_v[key] = _v[key].lerp(target, rate) if (target is Vector2 or target is Color) else lerpf(_v[key], target, rate)
	_apply()


func _update_eyes(delta: float) -> void:
	# blinks: 2.5-6 s apart, sometimes a double blink; asymmetric (fast close, slower open)
	if _blink_t >= 0.0:
		_blink_t += delta
		var d := 0.19
		var x := clampf(_blink_t / d, 0.0, 1.0)
		_blink = 1.0 - (sin(x / 0.38 * PI * 0.5) if x < 0.38 else cos((x - 0.38) / 0.62 * PI * 0.5))
		if _blink_t > d:
			_blink_t = -1.0
			_blink = 1.0
			if _double_blink:
				_double_blink = false
				_next_blink = t + 0.12
	elif t > _next_blink and not is_asleep():
		_blink_t = 0.0
		_double_blink = _rng.randf() < 0.18
		_next_blink = t + _rng.randf_range(2.5, 6.0) * (1.6 if state == "listening" else 1.0)
	# gaze: saccades (quick jumps, then hold) + micro-saccades; attentive states keep eye contact
	if t > _next_saccade:
		var amp := {"idle": Vector2(0.5, 0.28), "speaking": Vector2(0.18, 0.1), "thinking": Vector2(0.12, 0.08)}
		var a: Vector2 = amp.get(state, Vector2.ZERO)
		_gaze = Vector2.ZERO if _rng.randf() < 0.35 else Vector2(_rng.randf_range(-a.x, a.x), _rng.randf_range(-a.y, a.y))
		_next_saccade = t + _rng.randf_range(0.8, 3.2)
	if t > _next_micro:
		_micro = Vector2(_rng.randf_range(-0.03, 0.03), _rng.randf_range(-0.025, 0.025))
		_next_micro = t + _rng.randf_range(0.25, 0.8)


func _apply() -> void:
	var breath := sin(t * 1.25)                     # ~5 s breathing cycle
	var o: float = _v.open * _blink * (1.0 + 0.012 * breath + 0.07 * _pop)
	var w: float = _v.w * (1.0 - 0.03 * _pop)
	var m := _mat
	m.set_shader_parameter("t", t)
	m.set_shader_parameter("low_quality", low_quality)
	m.set_shader_parameter("eye_col", _v.col)
	m.set_shader_parameter("bright", _v.bright)
	m.set_shader_parameter("glow", _v.glow * (1.0 + 0.05 * breath))
	m.set_shader_parameter("eye_half", Vector2(0.165 * w, 0.23))
	m.set_shader_parameter("eye_y", -0.1 + 0.006 * breath)
	m.set_shader_parameter("look", _v.look)
	m.set_shader_parameter("open", Vector2(o, o))
	m.set_shader_parameter("lid_up", _v.lid_up)
	m.set_shader_parameter("lid_tilt", _v.lid_tilt)
	m.set_shader_parameter("lid_low", _v.lid_low)
	m.set_shader_parameter("lid_curve", _v.curve)
	m.set_shader_parameter("sleep", _v.sleep)
	m.set_shader_parameter("iris", _v.iris)
	m.set_shader_parameter("mouth_smile", _v.smile)
	m.set_shader_parameter("mouth_open", _v.mouth_open)
	m.set_shader_parameter("mouth_w", _v.mouth_w)
	m.set_shader_parameter("mouth_pos", Vector2(_v.mouth_x, 0.36 + 0.004 * breath))
	m.set_shader_parameter("mouth_alpha", _v.mouth_a)
	m.set_shader_parameter("ambient", _v.ambient)
	m.set_shader_parameter("ring", maxf(_v.ring, 0.0))
	m.set_shader_parameter("ring_col", _v.ring_col)
	m.set_shader_parameter("spin", _v.spin)
	m.set_shader_parameter("blush", _v.blush)
	m.set_shader_parameter("jitter", _v.jitter)
	m.set_shader_parameter("dot_pos", DOT_POS)
	m.set_shader_parameter("dot_col", DOT[link_state])
	m.set_shader_parameter("dot_pulse", 1.0 if link_state == "live" else 0.0)
	m.set_shader_parameter("dot_breathe", 1.0 if link_state == "connecting" else 0.0)
	m.set_shader_parameter("dot_slash", 1.0 if link_state == "muted" else 0.0)
