extends PanelContainer
## "What does he see?" window: the camera picture as the Core captures it, so the user can check what the camera sees.
## Frames come ONLY while this window is open (`camera.preview_frame` {seq, mime, width, height, data: base64};
## core/src/jarves/camera/service.py: small, low fps, local link only). They are decoded into a texture in RAM and
## never saved; closing drops the texture. A thick orange frame + a red dot say "the camera is on" the whole time.
## Closes by itself after `seconds` (default 20) or on the close button / the strip's eye button.
## Below the picture: "What he last described" = the last camera_describe answer (privacy.state camera.last_seen).

signal close_requested(reason: String)

const C_FRAME := Color("#E69F00")       # same orange as a capturing chip on the strip
const C_DOT := Color("#D55E00")
const MAX_B64 := 400000                 # larger frames are ignored (the Core sends ~10-30 kB)
const MAX_SIDE := 1280
const TEXTS := {
	"title": ["תצוגת מצלמה · נשאר במכשיר", "Camera preview · stays on this device"],
	"waiting": ["ממתין לתמונה...", "Waiting for the picture..."],
	"closes": ["נסגר בעוד %d שנ׳", "Closes in %d s"],
	"last": ["מה הוא תיאר לאחרונה:", "What he last described:"],
	"none": ["עוד לא תיאר תמונה.", "Nothing described yet."],
	"close": ["סגור", "Close"],
	"CAMERA_DISABLED": ["המצלמה כבויה. הדליקו אותה בלחיצה על סמל המצלמה.", "The camera is off. Click the camera icon to turn it on."],
	"CAMERA_UNAVAILABLE": ["אין מצלמה מחוברת.", "No camera is connected."],
	"PREVIEW_DISABLED": ["תצוגת המצלמה כבויה בלוח הבקרה.", "The camera preview is switched off in the dashboard."],
	"camera_off": ["המצלמה כובתה.", "The camera was turned off."],
	"timeout": ["התצוגה נסגרה.", "The preview closed."],
	"offline": ["אין חיבור לג'ארביס.", "Not connected to JARVES."],
}

var is_open := false
var frames := 0                          # frames shown since opening (tests)
var last_seq := -1
var reason := ""
var locale := "he"
var seconds := 20.0
var _left := 0.0
var picture: TextureRect
var _title: Label
var _count: Label
var _status: Label
var _last_head: Label
var _last: Label
var _close: Button
var _dot: ColorRect
var _head: HBoxContainer
var _b64 := RegEx.create_from_string("^[A-Za-z0-9+/]+={0,2}$")


func _init() -> void:
	name = "CameraPreview"
	visible = false
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.03, 0.04, 0.07, 0.96)
	sb.border_color = C_FRAME
	sb.set_border_width_all(6)
	sb.set_corner_radius_all(12)
	sb.set_content_margin_all(12)
	add_theme_stylebox_override("panel", sb)
	var box := VBoxContainer.new()
	add_child(box)
	_head = HBoxContainer.new()
	_head.add_theme_constant_override("separation", 10)
	box.add_child(_head)
	var head := _head
	_dot = ColorRect.new()
	_dot.color = C_DOT
	_dot.custom_minimum_size = Vector2(14, 14)
	_dot.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(_dot)
	_title = Label.new()
	_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(_title)
	_count = Label.new()
	_count.modulate = Color(1, 1, 1, 0.75)
	head.add_child(_count)
	_close = Button.new()
	_close.pressed.connect(func(): close_requested.emit("closed"))
	head.add_child(_close)
	picture = TextureRect.new()
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	picture.custom_minimum_size = Vector2(320, 240)
	box.add_child(picture)
	_status = Label.new()
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status.custom_minimum_size = Vector2(320, 0)
	box.add_child(_status)
	_last_head = Label.new()
	_last_head.modulate = Color(1, 1, 1, 0.65)
	box.add_child(_last_head)
	_last = Label.new()
	_last.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_last.custom_minimum_size = Vector2(320, 0)
	box.add_child(_last)
	_texts()


func tr2(k: String) -> String:
	return TEXTS[k][0 if locale == "he" else 1]


static func _rtl(s: String) -> bool:
	for ch in s:
		var c := ch.unicode_at(0)
		if c >= 0x0590 and c <= 0x05FF:
			return true
	return false


func _dir(l: Label) -> void:
	var rtl := _rtl(l.text) or (l.text == "" and locale == "he")
	l.text_direction = Control.TEXT_DIRECTION_RTL if rtl else Control.TEXT_DIRECTION_LTR
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT if rtl else HORIZONTAL_ALIGNMENT_LEFT


func _texts() -> void:
	# Hebrew: the header runs right to left (dot + title on the right, close button on the left)
	_head.layout_direction = Control.LAYOUT_DIRECTION_RTL if locale == "he" else Control.LAYOUT_DIRECTION_LTR
	_title.text = tr2("title")
	_close.text = tr2("close")
	_last_head.text = tr2("last")
	if _last.text == "":
		_last.text = tr2("none")
	for l in [_title, _status, _last_head, _last]:
		_dir(l)


func set_locale(loc: String) -> void:
	if loc != locale:
		locale = loc
		_last.text = ""
		_texts()


func set_last_seen(text: String) -> void:
	_last.text = text if text != "" else tr2("none")
	_dir(_last)


func open_preview(secs := 20.0) -> void:
	seconds = clampf(secs, 3.0, 60.0)
	_left = seconds
	is_open = true
	visible = true
	frames = 0
	last_seq = -1
	reason = ""
	picture.texture = null
	_status.text = tr2("waiting")
	_status.visible = true
	_dir(_status)
	_layout()


func close_preview(why := "") -> void:
	is_open = false
	visible = false
	picture.texture = null                 # the picture is gone with the window (RAM only, never saved)
	reason = why


func apply_state(p: Dictionary) -> void:
	# camera.preview_state {open, reason?}: the Core refused or ended it -> show why, keep the window a moment
	if not is_open:
		return
	if typeof(p.get("open")) == TYPE_BOOL and p.get("open") == false:
		var r = p.get("reason")
		reason = r if typeof(r) == TYPE_STRING else "closed"
		picture.texture = null
		_status.text = tr2(reason) if TEXTS.has(reason) else tr2("timeout")
		_status.visible = true
		_dir(_status)
		_left = minf(_left, 4.0)           # read the reason, then the window goes away by itself


func apply_frame(p: Dictionary) -> bool:
	# a frame is shown only while the window is open (never an unrequested picture); malformed ones are ignored
	if not is_open or reason != "":
		return false
	var data = p.get("data")
	var mime = p.get("mime")
	var seq = p.get("seq")
	if typeof(data) != TYPE_STRING or data.length() == 0 or data.length() > MAX_B64:
		return false
	if typeof(mime) != TYPE_STRING or not (mime in ["image/jpeg", "image/png"]):
		return false
	if (typeof(seq) == TYPE_INT or typeof(seq) == TYPE_FLOAT) and int(seq) <= last_seq:
		return false                        # stale / repeated frame
	if data.length() % 4 != 0 or _b64.search(data) == null:
		return false                        # not base64: never handed to the decoder (no engine error)
	var raw := Marshalls.base64_to_raw(data)
	if raw.size() < 8:
		return false
	var jpeg := raw[0] == 0xFF and raw[1] == 0xD8 and raw[2] == 0xFF
	var png := raw[0] == 0x89 and raw[1] == 0x50 and raw[2] == 0x4E and raw[3] == 0x47
	if (mime == "image/jpeg" and not jpeg) or (mime == "image/png" and not png):
		return false
	var img := Image.new()
	var err := img.load_jpg_from_buffer(raw) if mime == "image/jpeg" else img.load_png_from_buffer(raw)
	if err != OK or img.get_width() < 1 or img.get_width() > MAX_SIDE or img.get_height() > MAX_SIDE:
		return false
	picture.texture = ImageTexture.create_from_image(img)
	if typeof(seq) == TYPE_INT or typeof(seq) == TYPE_FLOAT:
		last_seq = int(seq)
	frames += 1
	_status.text = ""
	_status.visible = false                 # no empty line between the picture and the caption
	return true


func tick(delta: float) -> bool:
	## counts down; true when the window should close now (the owner sends camera.preview {open: false})
	if not is_open:
		return false
	_left -= delta
	_count.text = tr2("closes") % ceili(maxf(_left, 0.0))
	_dot.modulate.a = 0.55 + 0.45 * absf(sin(Time.get_ticks_msec() / 1000.0 * PI))
	return _left <= 0.0


func _layout() -> void:
	var vs := get_viewport_rect().size
	var w: float = clampf(minf(vs.x * 0.6, vs.y * 0.75), 340.0, 720.0)
	picture.custom_minimum_size = Vector2(w - 24.0, (w - 24.0) * 0.75)
	_status.custom_minimum_size.x = w - 24.0
	_last.custom_minimum_size.x = w - 24.0
	reset_size()
	position = ((vs - get_combined_minimum_size()) * 0.5).round()
	position.y = maxf(position.y, vs.y * 0.09)


func _process(_d: float) -> void:
	if visible:
		var vs := get_viewport_rect().size
		var want := ((vs - get_combined_minimum_size()) * 0.5).round()
		want.y = maxf(want.y, vs.y * 0.09)
		if position.distance_to(want) > 2.0:
			_layout()
