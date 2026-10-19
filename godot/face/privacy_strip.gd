extends Control
## Always-visible privacy / status strip (owner report 19 Oct: "I can't see if the mic or camera is on").
## Four chips - MIC, CAMERA, SPEAKER, CLOUD AUDIO - plus the "What does he see?" button, at the top of the face,
## avatar and room windows (assistant_ui.gd puts it on its own CanvasLayer, so --kiosk / Options never hide it).
## Driven ONLY by the Core message `privacy.state` (core/src/jarves/runtime/privacy_state.py, docs/FACE_RUNTIME.md):
## the strip never guesses. No link, no message yet, a malformed field or a state older than STALE_S -> "unknown".
## Colour-blind safe: Okabe-Ito colours, and every state ALSO differs in shape (filled = capturing, outline = local /
## standby, grey + slash = off) and in words (Hebrew, or English when the Core's locale is not Hebrew).
## Clicks: mic chip = software mute toggle, camera chip = camera switch (on asks on screen), eye = preview.

signal mic_pressed
signal camera_pressed
signal preview_pressed

const STALE_S := 12.0                   # the Core repeats privacy.state every 5 s; older = unknown
const ITEMS := ["mic", "camera", "speaker", "cloud_audio"]
const C_CAPTURE := Color("#E69F00")     # orange: capturing now (mic open to a conversation, camera sensor open)
const C_OUT := Color("#D55E00")         # vermillion: leaving the device (audio to the cloud, a photo)
const C_LOCAL := Color("#56B4E9")       # sky blue: on, this device only
const C_STANDBY := Color("#F0E442")     # yellow: connected / waiting, nothing captured
const C_OFF := Color(0.6, 0.62, 0.66)
const C_UNKNOWN := Color(0.92, 0.92, 0.92)
## state -> [he, en, tone]; tone: capture | out | local | standby | off | none (unavailable) | unknown
const STATES := {
	"mic": {"listening": ["מאזין", "Listening", "capture"], "gated": ["מושהה", "Paused", "standby"],
		"wake_only": ["רק מילת הפעלה", "Wake word only", "local"], "muted": ["מושתק", "Muted", "off"],
		"idle": ["סגור", "Off", "off"], "unavailable": ["אין מיקרופון", "No mic", "none"]},
	"camera": {"photo": ["מצלם!", "Taking photo", "out"], "preview": ["תצוגה פתוחה", "Preview on", "capture"],
		"presence": ["דולקת (נוכחות)", "On (presence)", "capture"], "off": ["סגורה", "Closed", "off"],
		"disabled": ["כבויה", "Off", "off"], "unavailable": ["אין מצלמה", "No camera", "none"]},
	"speaker": {"speaking": ["מדבר", "Speaking", "local"], "quiet": ["שקט", "Quiet", "off"],
		"unavailable": ["אין רמקול", "No speaker", "none"]},
	"cloud_audio": {"sending": ["קול נשלח לענן", "Audio to cloud", "out"],
		"connected": ["ענן: לא שולח", "Cloud: not sending", "standby"],
		"connecting": ["מתחבר לענן", "Connecting", "standby"], "off": ["לא נשלח קול", "No audio sent", "off"]},
}
const NAMES := {"mic": ["מיקרופון", "Microphone"], "camera": ["מצלמה", "Camera"], "speaker": ["רמקול", "Speaker"],
	"cloud_audio": ["קול לענן", "Audio to the cloud"]}
const HINTS := {
	"mic": ["לחיצה: השתקה / ביטול השתקה (תוכנה)", "Click: mute / unmute (software)"],
	"camera": ["לחיצה: הדלקה (באישור) / כיבוי", "Click: turn on (asks first) / off"],
	"speaker": ["", ""], "cloud_audio": ["", ""],
}
const UNKNOWN := ["לא ידוע", "Unknown", "unknown"]
const PREVIEW := [["מה הוא רואה?", "What does he see?"], ["סגור תצוגה", "Close preview"]]

var states := {"mic": "", "camera": "", "speaker": "", "cloud_audio": ""}    # "" = unknown
var locale := "he"
var linked := false
var preview_available := false
var preview_open := false
var last_seen_text := ""
var received := 0                       # valid privacy.state messages (tests)
var _seen_at := -1e9                    # seconds (ticks) of the last valid privacy.state
var _rects := {}                        # chip key -> Rect2 (clicks + tests)
var _font: SystemFont
var _lines := {}
var _t := 0.0
var _shaped_for := Vector2.ZERO
var avoid := Rect2()                    # screen rect of the assistant panel (set by assistant_ui.gd), never covered


func _init() -> void:
	name = "PrivacyStrip"
	mouse_filter = Control.MOUSE_FILTER_STOP
	_font = SystemFont.new()
	_font.font_names = PackedStringArray(["Segoe UI", "Noto Sans Hebrew", "Noto Sans", "Rubik", "Heebo",
		"DejaVu Sans", "Arial", "sans-serif"])
	_font.font_weight = 600
	_font.antialiasing = TextServer.FONT_ANTIALIASING_GRAY


static func _now() -> float:
	return Time.get_ticks_msec() / 1000.0


func apply(p: Dictionary) -> void:
	# privacy.state {v, locale, mic:{state}, camera:{state, preview_available, last_seen}, speaker:{state},
	# cloud_audio:{on, state}}. Each field is checked on its own; a bad one becomes unknown, never a guess.
	for k in ITEMS:
		var d = p.get(k)
		var st := ""
		if typeof(d) == TYPE_DICTIONARY and typeof(d.get("state")) == TYPE_STRING and STATES[k].has(d.get("state")):
			st = d.get("state")
		if k == "cloud_audio" and typeof(d) == TYPE_DICTIONARY and typeof(d.get("on")) == TYPE_BOOL and d.get("on"):
			st = "sending"                      # "on" wins over any other word (never under-report)
		states[k] = st
	var loc = p.get("locale")
	locale = "en" if typeof(loc) == TYPE_STRING and loc == "en" else "he"
	var cam = p.get("camera")
	preview_available = typeof(cam) == TYPE_DICTIONARY and typeof(cam.get("preview_available")) == TYPE_BOOL \
			and cam.get("preview_available")
	last_seen_text = ""
	if typeof(cam) == TYPE_DICTIONARY and typeof(cam.get("last_seen")) == TYPE_DICTIONARY:
		var tx = cam.get("last_seen").get("text")
		if typeof(tx) == TYPE_STRING:
			last_seen_text = tx.substr(0, 400)
	received += 1
	_seen_at = _now()
	_reshape()


func set_linked(on: bool) -> void:
	linked = on
	if not on:
		_seen_at = -1e9
		preview_available = false
	_reshape()


func set_preview_open(on: bool) -> void:
	preview_open = on
	_reshape()


func fresh() -> bool:
	return linked and _now() - _seen_at < STALE_S


func state_of(k: String) -> String:
	## the state the strip SHOWS ("" = unknown)
	return states.get(k, "") if fresh() else ""


func entry(k: String) -> Array:
	var st := state_of(k)
	return STATES[k][st] if st != "" else UNKNOWN


func label(k: String) -> String:
	var e := entry(k)
	return e[0] if locale == "he" else e[1]


func tone(k: String) -> String:
	return entry(k)[2]


static func tone_color(tn: String) -> Color:
	return {"capture": C_CAPTURE, "out": C_OUT, "local": C_LOCAL, "standby": C_STANDBY, "off": C_OFF,
		"none": C_OFF, "unknown": C_UNKNOWN}.get(tn, C_UNKNOWN)


func preview_enabled() -> bool:
	return preview_open or (fresh() and preview_available)


func preview_label() -> String:
	return PREVIEW[1 if preview_open else 0][0 if locale == "he" else 1]


func chip_rect(k: String) -> Rect2:
	return _rects.get(k, Rect2())


# ------------------------------------------------------------------ layout + drawing
func _unit() -> float:
	var vs := get_viewport_rect().size
	return clampf(minf(vs.x, vs.y) * 0.05, 26.0, 48.0)      # chip height in px


func _order() -> Array:
	var keys: Array = ITEMS.duplicate()
	keys.append("preview")
	if locale == "he":
		keys.reverse()                      # Hebrew reads right to left: the mic is the first chip on the right
	return keys


func _reshape() -> void:
	_shape_at(_unit())
	var avail := get_viewport_rect().size.x * 0.96
	if size.x > avail and size.x > 0.0:      # narrow window (round device screen): shrink to fit, never clip
		_shape_at(maxf(12.0, size.y * avail / size.x))


func _shape_at(h: float) -> void:
	var fs := int(h * 0.42)
	_lines.clear()
	for k in _order():
		var tl := TextLine.new()
		var words := preview_label() if k == "preview" else label(k)
		tl.direction = TextServer.DIRECTION_RTL if locale == "he" else TextServer.DIRECTION_LTR
		tl.add_string(words, _font, fs)
		_lines[k] = tl
	var gap := h * 0.18
	var x := 0.0
	_rects.clear()
	for k in _order():
		var w: float = h * 0.25 + h * 0.62 + h * 0.16 + _lines[k].get_size().x + h * 0.3
		_rects[k] = Rect2(x, 0, w, h)
		x += w + gap
	custom_minimum_size = Vector2(maxf(x - gap, 0.0), h)
	size = custom_minimum_size
	position = _place()
	queue_redraw()


func _place() -> Vector2:
	# top centre; beside the assistant panel (`avoid`) when they would overlap, else just below it
	var vs := get_viewport_rect().size
	var p := Vector2(round((vs.x - size.x) * 0.5), round(maxf(6.0, vs.y * 0.012)))
	if avoid.size.x <= 0.0 or not Rect2(p, size).grow(6.0).intersects(avoid):
		return p
	var right := Vector2(avoid.end.x + 10.0, p.y)
	if right.x + size.x <= vs.x - 6.0:
		return right
	var left := Vector2(avoid.position.x - 10.0 - size.x, p.y)
	if left.x >= 6.0:
		return left
	return Vector2(p.x, avoid.end.y + 8.0)


func _process(delta: float) -> void:
	_t += delta
	var vs := get_viewport_rect().size
	if vs != _shaped_for:
		_shaped_for = vs
		_reshape()
	elif position.distance_to(_place()) > 1.0:
		position = _place()
	queue_redraw()                          # pulse of the capturing chips + staleness


func _draw() -> void:
	var h := size.y
	for k in _order():
		var r: Rect2 = _rects.get(k, Rect2())
		if r.size.x <= 0.0:
			continue
		var tn := "local" if k == "preview" else tone(k)
		if k == "preview" and not preview_enabled():
			tn = "none"
		var col := tone_color(tn)
		var filled: bool = tn in ["capture", "out"] or (k == "preview" and preview_open)
		var sb := StyleBoxFlat.new()
		sb.set_corner_radius_all(int(h * 0.5))
		sb.bg_color = Color(col, 0.92) if filled else Color(0.04, 0.06, 0.1, 0.82)
		sb.border_color = Color(col, 1.0 if tn != "none" else 0.55)
		sb.set_border_width_all(maxi(2, int(h * 0.07)) if tn != "off" and tn != "none" else 1)
		if tn == "out":
			sb.border_color = Color(1, 1, 1, 0.9)      # double edge: leaves the device
		draw_style_box(sb, r)
		var fg := Color(0.05, 0.05, 0.07) if filled else Color(col, 1.0 if tn != "none" else 0.6)
		var ic := Vector2(r.position.x + h * 0.25 + h * 0.31, h * 0.5)
		var pulse := 1.0
		if tn in ["capture", "out"]:
			pulse = 0.8 + 0.2 * sin(_t * TAU * 0.8)
		_icon(k, ic, h * 0.62, Color(fg, fg.a * pulse))
		if tn in ["off", "none"] and k != "preview":
			draw_line(ic + Vector2(-0.3, 0.3) * h, ic + Vector2(0.3, -0.3) * h, Color(0.04, 0.06, 0.1), h * 0.11, true)
			draw_line(ic + Vector2(-0.3, 0.3) * h, ic + Vector2(0.3, -0.3) * h, fg, h * 0.05, true)
		var tl: TextLine = _lines[k]
		var tp := Vector2(r.position.x + h * 0.25 + h * 0.62 + h * 0.16, (h - tl.get_size().y) * 0.5)
		tl.draw(get_canvas_item(), tp, fg)


func _icon(k: String, c: Vector2, s: float, col: Color) -> void:
	var w := maxf(1.5, s * 0.09)
	match k:
		"mic":
			draw_rect(Rect2(c + Vector2(-0.13, -0.42) * s, Vector2(0.26, 0.5) * s), col, true)
			draw_circle(c + Vector2(0, -0.42) * s, 0.13 * s, col)
			draw_circle(c + Vector2(0, 0.08) * s, 0.13 * s, col)
			draw_arc(c + Vector2(0, -0.02) * s, 0.27 * s, 0.15, PI - 0.15, 16, col, w, true)
			draw_line(c + Vector2(0, 0.25) * s, c + Vector2(0, 0.42) * s, col, w, true)
			draw_line(c + Vector2(-0.16, 0.44) * s, c + Vector2(0.16, 0.44) * s, col, w, true)
		"camera":
			draw_rect(Rect2(c + Vector2(-0.42, -0.24) * s, Vector2(0.84, 0.58) * s), col, true)
			draw_rect(Rect2(c + Vector2(-0.16, -0.36) * s, Vector2(0.32, 0.14) * s), col, true)
			draw_circle(c + Vector2(0, 0.05) * s, 0.2 * s, Color(0.04, 0.06, 0.1))
			draw_circle(c + Vector2(0, 0.05) * s, 0.11 * s, col)
		"speaker":
			draw_colored_polygon(PackedVector2Array([c + Vector2(-0.4, -0.13) * s, c + Vector2(-0.2, -0.13) * s,
				c + Vector2(0.02, -0.36) * s, c + Vector2(0.02, 0.36) * s, c + Vector2(-0.2, 0.13) * s,
				c + Vector2(-0.4, 0.13) * s]), col)
			if state_of("speaker") == "speaking":
				draw_arc(c + Vector2(0.04, 0) * s, 0.18 * s, -0.9, 0.9, 10, col, w, true)
				draw_arc(c + Vector2(0.04, 0) * s, 0.34 * s, -0.9, 0.9, 12, col, w, true)
		"cloud_audio":
			draw_circle(c + Vector2(-0.18, 0.06) * s, 0.17 * s, col)
			draw_circle(c + Vector2(0.06, -0.06) * s, 0.24 * s, col)
			draw_circle(c + Vector2(0.27, 0.08) * s, 0.15 * s, col)
			draw_rect(Rect2(c + Vector2(-0.18, 0.06) * s, Vector2(0.45, 0.17) * s), col, true)
			if state_of("cloud_audio") == "sending":     # arrow up: out of the device
				var bg := Color(0.04, 0.06, 0.1) if col.v > 0.4 else Color.WHITE
				draw_line(c + Vector2(0.04, 0.2) * s, c + Vector2(0.04, -0.12) * s, bg, w, true)
				draw_line(c + Vector2(-0.07, -0.01) * s, c + Vector2(0.04, -0.13) * s, bg, w, true)
				draw_line(c + Vector2(0.15, -0.01) * s, c + Vector2(0.04, -0.13) * s, bg, w, true)
		"preview":
			draw_arc(c + Vector2(0, 0.3) * s, 0.5 * s, -PI * 0.8, -PI * 0.2, 16, col, w, true)
			draw_arc(c + Vector2(0, -0.3) * s, 0.5 * s, PI * 0.2, PI * 0.8, 16, col, w, true)
			draw_circle(c, 0.13 * s, col)


func _gui_input(ev: InputEvent) -> void:
	if ev is InputEventMouseButton and ev.pressed and ev.button_index == MOUSE_BUTTON_LEFT:
		var k := key_at(ev.position)
		if k == "mic":
			mic_pressed.emit()
		elif k == "camera":
			camera_pressed.emit()
		elif k == "preview" and preview_enabled():
			preview_pressed.emit()
		if k != "":
			accept_event()


func key_at(pos: Vector2) -> String:
	for k in _rects:
		if (_rects[k] as Rect2).has_point(pos):
			return k
	return ""


func _get_tooltip(at: Vector2) -> String:
	var k := key_at(at)
	if k == "":
		return ""
	var li := 0 if locale == "he" else 1
	if k == "preview":
		return ("התמונה נשארת במכשיר ולא נשמרת" if li == 0 else "The picture stays on this device and is never saved") \
			if preview_enabled() else ("המצלמה כבויה או לא מחוברת" if li == 0 else "The camera is off or not connected")
	var t: String = NAMES[k][li] + ": " + label(k)
	if not fresh():
		t += " - " + ("אין עדכון מג'ארביס" if li == 0 else "no update from JARVES")
	if HINTS[k][li] != "":
		t += "\n" + HINTS[k][li]
	return t
