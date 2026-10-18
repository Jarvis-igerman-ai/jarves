extends Control
## JARVES animated face (shared by the 3D twin and the on-device face display, docs/FACE_RUNTIME.md).
## Rendered by ONE canvas shader (face.gdshader: SDF eyes, lids, mouth, rim, status dot); this script owns the
## behaviour: per-state expression targets, frame-rate independent smoothing, blinks, saccades, breathing.
## States (face.set_state from the Core): idle, listening, thinking, speaking, happy, error.
## Link (status dot, set_link): ready, live, connecting, degraded, offline, error, muted. "offline" = sleep look.
## No text is ever drawn on the face. `--low` drops the wide glow / glass passes for slow GPUs.
## `--lively` restores the earlier, busier motion (LIVELY below) for side-by-side comparison.
## All motion runs on the face clock `t` (advanced only by _process), so tests can simulate time exactly.

# ---------------------------------------------------------------------------------------------- MOTION TUNING
# Every timing / amplitude of the face motion lives here. CALM is the default: modelled on a real person at rest
# (blink every 4-7 s, ~150 ms, gaze mostly holds, breathing barely visible). LIVELY = the previous values.
# Units: seconds; "look" units drive the eye/iris offset (1 look = ~11 % of the face radius at the iris,
# see GAZE_TO_RADIUS); "rate" values are exponential-smoothing rates in 1/s (time constant = 1/rate).
const CALM := {
	# blinks
	"blink_every": Vector2(4.0, 7.0),       # random interval between blinks (s)
	"blink_close": 0.055,                   # quick close (s)
	"blink_open": 0.095,                    # slightly slower open (s) -> ~150 ms per blink
	"double_blink": 0.1,                    # chance that a blink is a double blink
	"double_gap": 0.09,                     # pause between the two blinks of a double (s)
	"blink_min_gap": 1.8,                   # never two (non-double) blinks closer than this (s)
	"blink_after_saccade": 0.2,             # chance a gaze shift is followed by a blink
	"blink_state_every": {"listening": 1.9, "thinking": 1.5},    # interval multiplier (attentive = fewer blinks)
	"blink_state_len": {"listening": 1.2, "thinking": 1.35},     # duration multiplier (slower, softer blink)
	# gaze
	"saccade_every": Vector2(3.0, 8.0),     # random hold time between saccades (s)
	"saccade_time": 0.16,                   # eased (in-out) saccade duration (s)
	"saccade_center": 0.4,                  # chance a saccade returns to straight ahead
	"saccade_amp": {"idle": Vector2(0.25, 0.15), "speaking": Vector2(0.1, 0.06)},   # max offset per state (look)
	"micro_amp": Vector2.ZERO,              # random micro-saccade jumps (look) - off: they read as jitter
	"micro_every": Vector2(0.25, 0.8),
	"drift_amp": 0.025,                     # smooth, slow fixational drift (look), no randomness per frame
	"drift_hz": Vector2(0.07, 0.11),
	"look_rate": 4.0,                       # state gaze change (e.g. thinking look-up-aside), one slow move
	"think_wander": 0.0,                    # thinking: continuous circling of the look-up target (look)
	# breathing / idle sway (applied to openness, glow, eye height, mouth height)
	"breath_period": 5.5,
	"breath_open": 0.006, "breath_glow": 0.025, "breath_y": 0.003, "breath_mouth": 0.002,
	"sleep_pulse": 0.04, "sleep_period": 6.5,                      # sleeping brightness breathing
	# state-change "pop" (anticipation squash)
	"pop_in": 0.1, "pop_out": 0.36, "pop_open": 0.04, "pop_w": 0.015, "pop_back": 0.6,   # back = overshoot
	"expr_rate": 7.0,                       # expression (lids, mouth shape, colour) smoothing
	# speaking: mouth follows the speaker level (envelope follower, attack/release time constants)
	"mouth_attack": 0.04, "mouth_release": 0.1, "mouth_gain": 1.0, "mouth_max": 0.6,
	"mouth_rate": 40.0,                     # extra shader-side smoothing of the mouth opening
	"speech_stale": 0.18,                   # no level update for this long -> audio stopped (Core sends 100 ms blocks)
	# listening: rim reacts to the mic level, low-passed so it never flickers
	"mic_attack": 0.12, "mic_release": 0.35, "mic_stale": 0.6, "ring_gain": 1.1, "ring_rate": 7.0,
	# rim / comet / error (shader time scales)
	"wave_amt": 0.1, "wave_speed": 0.5,     # rim halo shimmer
	"spin_speed": 1.2,                      # thinking comet, rad/s (~5 s per sweep)
	"error_pulse": 0.08, "error_hz": 0.2,   # error rim pulse
	"tear_thresh": 0.97,                    # error glitch tears (higher = rarer)
	"dot_speed": 0.6,                       # status-dot pulse/breathe speed
}
const LIVELY := {
	"blink_every": Vector2(2.5, 6.0), "blink_close": 0.072, "blink_open": 0.118, "double_blink": 0.18,
	"double_gap": 0.12, "blink_min_gap": 0.0, "blink_after_saccade": 0.0,
	"blink_state_every": {"listening": 1.6}, "blink_state_len": {},
	"saccade_every": Vector2(0.8, 3.2), "saccade_time": 0.12, "saccade_center": 0.35,
	"saccade_amp": {"idle": Vector2(0.5, 0.28), "speaking": Vector2(0.18, 0.1), "thinking": Vector2(0.12, 0.08)},
	"micro_amp": Vector2(0.03, 0.025), "micro_every": Vector2(0.25, 0.8), "drift_amp": 0.0,
	"drift_hz": Vector2(0.07, 0.11), "look_rate": 16.0, "think_wander": 0.1,
	"breath_period": 5.03, "breath_open": 0.012, "breath_glow": 0.05, "breath_y": 0.006, "breath_mouth": 0.004,
	"sleep_pulse": 0.12, "sleep_period": 5.7,
	"pop_in": 0.08, "pop_out": 0.32, "pop_open": 0.07, "pop_w": 0.03, "pop_back": 1.70158, "expr_rate": 9.0,
	"mouth_attack": 0.031, "mouth_release": 0.071, "mouth_gain": 1.0, "mouth_max": 1.0, "mouth_rate": 22.0,
	"speech_stale": 0.25,
	"mic_attack": 0.05, "mic_release": 0.167, "mic_stale": 0.6, "ring_gain": 1.6, "ring_rate": 22.0,
	"wave_amt": 0.22, "wave_speed": 1.0, "spin_speed": 2.6, "error_pulse": 0.25, "error_hz": 0.64,
	"tear_thresh": 0.86, "dot_speed": 1.0,
}
## Transient emotional reactions (face.emotion {emotion, ms}); same for CALM and --lively. They ease in, hold for
## `ms`, then ease back to the current state; a new reaction replaces the running one by cross-fading.
const REACT := {
	"ease_in": 0.25, "ease_out": 0.7,       # s (the expression smoothing adds ~0.15 s on top)
	"replace_time": 0.45,                   # cross-fade when a new reaction replaces a running one
	"default_ms": 3500,
	"laugh_bounce": 0.009, "laugh_hz": 2.0,  # gentle chuckle bounce: <1 % of the radius, 2 per second
	"cry_tremble": 0.003, "cry_hz": 5.5,     # small lip tremble
	"tear_hz": 0.28,                         # one tear slides down roughly every 3.5 s per eye
}
## look units -> displacement of the iris centre in face-radius units (eye shift + iris shift inside the eye)
const GAZE_TO_RADIUS := Vector2(0.065 + 0.3 * 0.165, 0.05 + 0.22 * 0.23)
# ------------------------------------------------------------------------------------------------------------

const STATES := ["idle", "listening", "thinking", "speaking", "happy", "error"]
const LINKS := ["ready", "live", "connecting", "degraded", "offline", "error", "muted"]
const EMOTIONS := ["laugh", "cry", "surprised", "love", "sad"]
const SHADER := preload("res://face/face.gdshader")
const ActivityPill := preload("res://face/face_activity.gd")
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

var cfg: Dictionary = CALM
var lively := false
var state := "idle"
var link_state := "offline"
var low_quality := false
var t := 0.0                        # face clock (s)
var blink_count := 0                # closures started (tests / diagnostics)
var _pop := 0.0                     # squash on every state change (anticipation)
var _pop_t0 := -100.0
var _speech_level := 0.0
var _speech_seen := -1000.0
var _mic_level := 0.0
var _mic_seen := -1000.0
var _mouth := 0.0                   # smoothed mouth opening, 0 when there is no speaker audio
var _mic := 0.0
var _blink := 1.0                   # 1 open .. 0 closed
var _blink_t := -1.0
var _blink_len := 1.0
var _last_blink := -100.0
var _next_blink := 2.0
var _double_blink := false
var _gaze := Vector2.ZERO           # current saccade offset (eased)
var _gaze_from := Vector2.ZERO
var _gaze_to := Vector2.ZERO
var _gaze_t0 := -100.0
var _micro := Vector2.ZERO
var _next_saccade := 1.5
var _next_micro := 0.3
var _drift_ph := Vector2.ZERO
var _look := Vector2.ZERO           # final gaze sent to the shader (state look + saccade + drift)
var _v := {}                        # smoothed shader values
var _mat: ShaderMaterial
var _rng := RandomNumberGenerator.new()
var emotion := ""                   # running reaction ("" = none)
var _emo_t0 := -100.0
var _emo_len := 0.0                 # s; <= 0 = hold (previews)
var _emo_prev := ""                 # reaction being replaced (cross-faded out)
var _emo_prev_w := 0.0
var activity: Control               # face_activity.gd pill on the lower arc
var _fx := Vector3.ZERO             # smoothed reaction extras: x laugh bounce, y tears, z heart glints


func _init() -> void:
	var args := OS.get_cmdline_user_args()
	low_quality = "--low" in args
	var canvas := ColorRect.new()
	canvas.name = "FaceCanvas"
	canvas.mouse_filter = Control.MOUSE_FILTER_IGNORE
	canvas.set_anchors_preset(Control.PRESET_FULL_RECT)
	_mat = ShaderMaterial.new()
	_mat.shader = SHADER
	canvas.material = _mat
	add_child(canvas)
	activity = ActivityPill.new()
	add_child(activity)
	_drift_ph = Vector2(_rng.randf() * TAU, _rng.randf() * TAU)
	set_lively("--lively" in args)
	_v = _targets()
	_apply()


func _ready() -> void:
	# preview flags (twin: main.gd, display: face_app.gd); the Core overrides them once connected
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--face-emotion=") or a.begins_with("--emotion="):
			set_emotion(a.get_slice("=", 1), 0)
		elif a.begins_with("--face-activity=") or a.begins_with("--activity="):
			# --activity=kind|text|detail
			var parts := a.substr(a.find("=") + 1).split("|")
			var smp: Array = ActivityPill.SAMPLES.get(parts[0], [parts[0], ""])
			set_activity(parts[0], parts[1] if parts.size() > 1 else smp[0], parts[2] if parts.size() > 2 else smp[1])
			activity.alpha = 1.0


func set_emotion(e: String, ms := -1) -> void:
	# transient reaction; ms < 0 -> default length, ms == 0 -> hold (previews); "" / unknown -> ease out now
	if not (e in EMOTIONS):
		if emotion != "":
			_emo_len = maxf(t - _emo_t0, REACT.ease_in)
		return
	_emo_prev = emotion
	_emo_prev_w = _emo_weight()
	emotion = e
	_emo_t0 = t
	_emo_len = (REACT.default_ms if ms < 0 else ms) / 1000.0


func set_activity(kind: String, text: String, detail := "") -> void:
	activity.set_activity(kind, text, detail)


func _emo_weight() -> float:
	if emotion == "" or is_asleep():
		return 0.0
	var dt := t - _emo_t0
	var w_in := smoothstep(0.0, REACT.replace_time if _emo_prev_w > 0.0 else REACT.ease_in, dt)
	if _emo_len <= 0.0:
		return w_in
	return w_in * (1.0 - smoothstep(_emo_len, _emo_len + REACT.ease_out, dt))


func _emotion_targets(e: Dictionary) -> Dictionary:
	# expression of the running reaction, built on top of the state expression
	var r := e.duplicate()
	match emotion:
		"laugh":   # squinted ^ ^ eyes, open grin, rosy cheeks
			r.col = HAPPY; r.lid_low = Vector2(0.34, 0.34); r.curve = 1.0; r.iris = 0.3; r.lid_up = Vector2.ZERO
			r.lid_tilt = Vector2.ZERO; r.smile = 1.0; r.mouth_w = 0.135; r.mouth_x = 0.0
			r.mouth_open = maxf(0.62, _mouth); r.blush = 0.7; r.glow = 1.2; r.ambient = 1.3; r.look = Vector2.ZERO
		"cry":     # worried lids, wet downcast eyes, trembling frown, tears
			r.col = Color(0.42, 0.72, 1.0); r.lid_up = Vector2(0.2, 0.2); r.lid_tilt = Vector2(-0.7, -0.7)
			r.lid_low = Vector2(0.14, 0.14); r.curve = 0.0; r.smile = -0.7; r.mouth_w = 0.075; r.mouth_x = 0.0
			r.mouth_open = maxf(0.12, _mouth); r.look = Vector2(0.0, 0.3); r.open = 0.92; r.blush = 0.0
			r.glow = 0.9; r.ambient = 0.85
		"sad":     # mild: a little worried, softer, looking down
			r.lid_up = Vector2(0.14, 0.14); r.lid_tilt = Vector2(-0.45, -0.45); r.lid_low = Vector2(0.06, 0.06)
			r.curve = 0.0; r.smile = -0.4; r.mouth_w = 0.07; r.look = Vector2(0.0, 0.32); r.open = 0.94
			r.glow = 0.9; r.ambient = 0.85; r.blush = 0.0
		"surprised":   # wide eyes, small "o"
			r.open = 1.22; r.w = 1.06; r.lid_up = Vector2.ZERO; r.lid_tilt = Vector2(-0.12, -0.12); r.curve = 0.0
			r.lid_low = Vector2.ZERO; r.smile = 0.0; r.mouth_w = 0.05; r.mouth_x = 0.0; r.mouth_o = 1.0
			r.mouth_open = maxf(0.6, _mouth); r.look = Vector2.ZERO; r.glow = 1.2; r.iris = 1.0
		"love":    # soft happy eyes, warm blush, heart glints
			r.lid_low = Vector2(0.2, 0.2); r.curve = 0.0; r.lid_up = Vector2.ZERO; r.lid_tilt = Vector2.ZERO
			r.smile = 0.85; r.mouth_w = 0.1; r.blush = 1.0; r.glow = 1.15; r.ambient = 1.25; r.iris = 1.0
			r.look = Vector2.ZERO
	return r


func reseed(seed_value: int) -> void:
	# repeatable motion (previews / tests): same seed -> same blinks and saccades
	_rng.seed = seed_value
	_drift_ph = Vector2(_rng.randf() * TAU, _rng.randf() * TAU)
	_next_blink = t + _rng.randf_range(1.0, 3.0)


func set_lively(on: bool) -> void:
	lively = on
	cfg = LIVELY if on else CALM


func set_state(s: String) -> void:
	if s in STATES and s != state:
		state = s
		_pop_t0 = t
		if s == "idle" and activity.kind != "music":
			activity.set_activity("done", "")     # the turn is over: no text on an idle face (music may stay)
		_next_saccade = t + (0.4 if lively else _rng.randf_range(0.6, 1.2))
		if not lively and cfg.saccade_amp.get(s, Vector2.ZERO) == Vector2.ZERO:
			_start_saccade(Vector2.ZERO)      # attentive states: eyes come back to the person


func set_link(value: String) -> void:
	if value in LINKS:
		link_state = value


func set_speech_level(level: float) -> void:
	_speech_level = clampf(level, 0.0, 1.0)
	_speech_seen = t


func set_mic_level(level: float) -> void:
	_mic_level = clampf(level, 0.0, 1.0)
	_mic_seen = t


func snap() -> void:
	# jump straight to the current expression (previews/screenshots; live use always transitions)
	_v = _targets()
	_look = _v.look
	var w := _emo_weight()
	_fx = Vector3(w if emotion == "laugh" else 0.0, w if emotion == "cry" else 0.0, w if emotion == "love" else 0.0)
	_apply()


func is_asleep() -> bool:
	return link_state == "offline"


func dot_color() -> Color:
	return DOT[link_state]


func gaze_radius() -> float:
	# current iris displacement from the rest position, in face-radius units (excludes deliberate state looks)
	return ((_look - _v.look) * GAZE_TO_RADIUS).length()


func _targets() -> Dictionary:
	# expression targets for the current state (all smoothed in _process -> transitions are always continuous)
	var e := {
		"col": BLUE, "bright": 1.0, "glow": 1.0, "open": 1.0, "w": 1.0, "lid_up": Vector2.ZERO,
		"lid_tilt": Vector2.ZERO, "lid_low": Vector2.ZERO, "curve": 0.0, "sleep": 0.0, "iris": 1.0,
		"smile": 0.55, "mouth_open": 0.0, "mouth_w": 0.085, "mouth_x": 0.0, "mouth_a": 1.0, "ring": 0.0,
		"ring_col": BLUE, "spin": 0.0, "blush": 0.0, "jitter": 0.0, "look": Vector2.ZERO, "ambient": 1.0, "mouth_o": 0.0,
	}
	match state:
		"listening":
			e.open = 1.1; e.w = 1.04; e.lid_low = Vector2.ZERO; e.smile = 0.3; e.mouth_w = 0.07
			e.ring = 0.32 + _mic * cfg.ring_gain; e.glow = 1.15 + _mic * 0.4; e.ambient = 1.3
		"thinking":
			e.open = 0.92; e.lid_up = Vector2(0.17, 0.06); e.lid_tilt = Vector2(-0.15, 0.1)
			e.lid_low = Vector2(0.18, 0.05); e.smile = -0.15; e.mouth_w = 0.055; e.mouth_x = 0.035
			e.look = Vector2(0.62, -0.62) + Vector2(sin(t * 0.7), cos(t * 0.5)) * cfg.think_wander; e.spin = 1.0
		"speaking":
			e.smile = 0.3; e.mouth_w = 0.09 + _mouth * 0.02; e.mouth_open = _mouth
			e.lid_low = Vector2(0.04, 0.04); e.glow = 1.0 + _mouth * 0.3; e.ring = 0.08 + _mouth * 0.2
		"happy":
			e.col = HAPPY; e.open = 1.0; e.lid_low = Vector2(0.3, 0.3); e.curve = 1.0; e.iris = 0.35
			e.smile = 1.0; e.mouth_w = 0.13; e.mouth_open = 0.5; e.blush = 1.0; e.glow = 1.2; e.ambient = 1.3
		"error":
			e.col = RED; e.ring_col = RED; e.open = 0.9; e.lid_up = Vector2(0.2, 0.2); e.lid_tilt = Vector2(-0.75, -0.75)
			e.lid_low = Vector2(0.08, 0.08); e.smile = -0.75; e.mouth_w = 0.07; e.look = Vector2(0.0, 0.3)
			e.ring = 0.35 + cfg.error_pulse * sin(t * TAU * cfg.error_hz); e.jitter = 1.0
	if is_asleep():
		e.col = SLEEP; e.bright = 0.55 + cfg.sleep_pulse * sin(t * TAU / cfg.sleep_period); e.glow = 0.6
		e.sleep = 1.0; e.iris = 0.0; e.smile = 0.15; e.mouth_w = 0.04; e.mouth_open = 0.0; e.mouth_a = 0.0
		e.ring = 0.0; e.spin = 0.0; e.blush = 0.0; e.jitter = 0.0; e.look = Vector2(0.0, 0.25); e.ambient = 0.5
	var w := _emo_weight()
	var pw := _emo_prev_w * (1.0 - smoothstep(0.0, REACT.replace_time, t - _emo_t0)) if not is_asleep() else 0.0
	if pw > 0.0:          # cross-fade: the old reaction hands over directly to the new one
		var cur := emotion
		emotion = _emo_prev
		var rp := _emotion_targets(e)
		emotion = cur
		for key in e:
			var a = e[key]
			e[key] = a.lerp(rp[key], pw / maxf(1.0 - w, 1e-3) if w < 0.999 else 0.0) if (a is Vector2 or a is Color) \
					else lerpf(a, rp[key], pw / maxf(1.0 - w, 1e-3) if w < 0.999 else 0.0)
	elif _emo_prev_w > 0.0:
		_emo_prev_w = 0.0
	if w > 0.0:
		var r := _emotion_targets(e)
		for key in e:
			var a = e[key]
			e[key] = a.lerp(r[key], w) if (a is Vector2 or a is Color) else lerpf(a, r[key], w)
	return e


static func _follow(cur: float, target: float, attack: float, release: float, delta: float) -> float:
	# one-pole envelope follower with separate attack / release time constants (s)
	var tau := attack if target > cur else release
	return lerpf(cur, target, 1.0 - exp(-delta / maxf(tau, 1e-4)))


func _process(delta: float) -> void:
	t += delta
	# mouth follows the real speaker level only; no audio -> closed (no fake flapping), limited range
	var speech := 0.0
	if state == "speaking" and t - _speech_seen < cfg.speech_stale:
		speech = minf(_speech_level * cfg.mouth_gain, cfg.mouth_max)
	_mouth = _follow(_mouth, speech, cfg.mouth_attack, cfg.mouth_release, delta)
	var mic := _mic_level if t - _mic_seen < cfg.mic_stale else 0.0
	_mic = _follow(_mic, mic, cfg.mic_attack, cfg.mic_release, delta)
	_update_pop()
	_update_eyes(delta)
	var e := _targets()
	var k := 1.0 - exp(-delta * cfg.expr_rate)
	for key in e:
		var target = e[key]
		var rate := k
		if key == "look":
			rate = 1.0 - exp(-delta * cfg.look_rate)
		elif key == "mouth_open":
			rate = 1.0 - exp(-delta * cfg.mouth_rate)
		elif key == "ring":
			rate = 1.0 - exp(-delta * cfg.ring_rate)
		elif key in ["glow", "bright"]:
			rate = 1.0 - exp(-delta * 22.0)
		_v[key] = _v[key].lerp(target, rate) if (target is Vector2 or target is Color) else lerpf(_v[key], target, rate)
	# final gaze: deliberate state look + eased saccade + slow drift (micro jumps only in --lively)
	var off: Vector2 = _gaze + _micro + cfg.drift_amp * Vector2(sin(TAU * cfg.drift_hz.x * t + _drift_ph.x),
			sin(TAU * cfg.drift_hz.y * t + _drift_ph.y))
	var target_look: Vector2 = _v.look + off * (0.0 if is_asleep() else 1.0)
	_look = target_look if not lively else _look.lerp(target_look, 1.0 - exp(-delta * 16.0))
	if emotion != "" and _emo_len > 0.0 and t - _emo_t0 > _emo_len + REACT.ease_out:
		emotion = ""
	# reaction-only extras, faded with the same smoothing as the expression
	var k2 := 1.0 - exp(-delta * cfg.expr_rate)
	var w := _emo_weight()
	_fx = _fx.lerp(Vector3(w if emotion == "laugh" else 0.0, w if emotion == "cry" else 0.0,
			w if emotion == "love" else 0.0), k2)
	activity.accent = _v.col
	activity.step(delta, t, is_asleep())
	_apply()


func _update_pop() -> void:
	# short squash on a state change: ease up, then back-ease down with a small (configurable) overshoot
	var dt := t - _pop_t0
	if dt < cfg.pop_in:
		_pop = sin(dt / cfg.pop_in * PI * 0.5)
	elif dt < cfg.pop_in + cfg.pop_out:
		var x: float = (dt - cfg.pop_in) / cfg.pop_out - 1.0
		var s: float = cfg.pop_back
		_pop = -((s + 1.0) * x * x * x + s * x * x)     # 1 - back_ease_out(x + 1)
	else:
		_pop = 0.0


func _start_saccade(to: Vector2) -> void:
	_gaze_from = _gaze
	_gaze_to = to
	_gaze_t0 = t


func _start_blink() -> void:
	_blink_t = 0.0
	_last_blink = t
	blink_count += 1
	_blink_len = cfg.blink_state_len.get(state, 1.0)


func _update_eyes(delta: float) -> void:
	# blinks: quick close, slightly slower open; rare double blink; fewer/slower in attentive states
	if _blink_t >= 0.0:
		_blink_t += delta
		var c: float = cfg.blink_close * _blink_len
		var d: float = c + cfg.blink_open * _blink_len
		if _blink_t < c:
			_blink = cos(_blink_t / c * PI * 0.5)                        # ease into the closed lid
		else:
			var x := clampf((_blink_t - c) / (d - c), 0.0, 1.0)
			_blink = x * x * (3.0 - 2.0 * x)                              # soft open
		if _blink_t > d:
			_blink_t = -1.0
			_blink = 1.0
			if _double_blink:
				_double_blink = false
				_next_blink = t + cfg.double_gap
	elif t > _next_blink and not is_asleep():
		var second := t - _last_blink < 0.5            # the second half of a double blink never doubles again
		_start_blink()
		_double_blink = not second and _rng.randf() < cfg.double_blink
		var r: Vector2 = cfg.blink_every
		_next_blink = t + _rng.randf_range(r.x, r.y) * cfg.blink_state_every.get(state, 1.0)
	# gaze: hold still, then a small eased saccade; attentive states keep eye contact
	if t > _next_saccade:
		var a: Vector2 = cfg.saccade_amp.get(state, Vector2.ZERO)
		var to := Vector2.ZERO
		if _rng.randf() >= cfg.saccade_center:
			to = Vector2(_rng.randf_range(-a.x, a.x), _rng.randf_range(-a.y, a.y))
		var r: Vector2 = cfg.saccade_every
		_next_saccade = t + _rng.randf_range(r.x, r.y)
		if a != Vector2.ZERO or _gaze_to != Vector2.ZERO:
			_start_saccade(to)
			# a blink often accompanies a gaze shift (only if one is not due soon anyway)
			if _blink_t < 0.0 and not is_asleep() and _rng.randf() < cfg.blink_after_saccade \
					and t - _last_blink > cfg.blink_min_gap and _next_blink - t > 1.0 and state in ["idle", "speaking"]:
				_next_blink = t + cfg.saccade_time * 0.6
	var sx := clampf((t - _gaze_t0) / cfg.saccade_time, 0.0, 1.0)
	_gaze = _gaze_from.lerp(_gaze_to, sx * sx * (3.0 - 2.0 * sx))        # eased in and out, then hold
	if cfg.micro_amp != Vector2.ZERO and t > _next_micro:
		var m: Vector2 = cfg.micro_amp
		_micro = Vector2(_rng.randf_range(-m.x, m.x), _rng.randf_range(-m.y, m.y))
		var r2: Vector2 = cfg.micro_every
		_next_micro = t + _rng.randf_range(r2.x, r2.y)


func breath() -> float:
	return sin(t * TAU / cfg.breath_period)


func _apply() -> void:
	var b := breath()
	var o: float = _v.open * _blink * (1.0 + cfg.breath_open * b + cfg.pop_open * _pop)
	var w: float = _v.w * (1.0 - cfg.pop_w * _pop)
	var bounce: float = -_fx.x * REACT.laugh_bounce * (0.5 - 0.5 * cos(TAU * REACT.laugh_hz * t))
	var tremble: float = _fx.y * REACT.cry_tremble * sin(TAU * REACT.cry_hz * t)
	var m := _mat
	m.set_shader_parameter("t", t)
	m.set_shader_parameter("low_quality", low_quality)
	m.set_shader_parameter("eye_col", _v.col)
	m.set_shader_parameter("bright", _v.bright)
	m.set_shader_parameter("glow", _v.glow * (1.0 + cfg.breath_glow * b))
	m.set_shader_parameter("eye_half", Vector2(0.165 * w, 0.23))
	m.set_shader_parameter("eye_y", -0.1 + cfg.breath_y * b + bounce)
	m.set_shader_parameter("look", _look)
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
	m.set_shader_parameter("mouth_pos", Vector2(_v.mouth_x, 0.36 + cfg.breath_mouth * b + bounce * 1.3 + tremble))
	m.set_shader_parameter("mouth_alpha", _v.mouth_a)
	m.set_shader_parameter("ambient", _v.ambient)
	m.set_shader_parameter("ring", maxf(_v.ring, 0.0))
	m.set_shader_parameter("ring_col", _v.ring_col)
	m.set_shader_parameter("spin", _v.spin)
	m.set_shader_parameter("blush", _v.blush)
	m.set_shader_parameter("jitter", _v.jitter)
	m.set_shader_parameter("cry", _fx.y)
	m.set_shader_parameter("tear_hz", REACT.tear_hz)
	m.set_shader_parameter("heart", _fx.z)
	m.set_shader_parameter("mouth_o", _v.mouth_o)
	m.set_shader_parameter("wave_amt", cfg.wave_amt)
	m.set_shader_parameter("wave_speed", cfg.wave_speed)
	m.set_shader_parameter("spin_speed", cfg.spin_speed)
	m.set_shader_parameter("tear_thresh", cfg.tear_thresh)
	m.set_shader_parameter("dot_speed", cfg.dot_speed)
	m.set_shader_parameter("dot_pos", DOT_POS)
	m.set_shader_parameter("dot_col", DOT[link_state])
	m.set_shader_parameter("dot_pulse", 1.0 if link_state == "live" else 0.0)
	m.set_shader_parameter("dot_breathe", 1.0 if link_state == "connecting" else 0.0)
	m.set_shader_parameter("dot_slash", 1.0 if link_state == "muted" else 0.0)
