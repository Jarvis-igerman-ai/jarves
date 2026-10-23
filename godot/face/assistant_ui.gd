extends PanelContainer
## Assistant controls shared by the 3D twin (main.gd) and the face display (face_app.gd).
## Voice-first status, optional software mute, captions, Core status and
## confirmation prompts. All behaviour lives in the Python Core; this is only UI + the link.

const CoreLink := preload("res://face/core_link.gd")
const FaceScript := preload("res://face/face.gd")
const PrivacyStrip := preload("res://face/privacy_strip.gd")
const CameraPreview := preload("res://face/camera_preview.gd")
const PREVIEW_S := 20.0           # "What does he see?" closes by itself after this

var link: Node
var face: Control                 # face.gd instance to drive (set by the owner)
var appearance: Control           # appearance.gd (face_app only; the 3D twin keeps the face on its glass)
var status_lbl: Label
var caption: Label
var who: Label                   # "JARVES" / "You" kept OUT of the caption so Hebrew lays out right-to-left
var activity: Label              # big, plain-language "what is JARVES doing now"
var level_bar: ProgressBar       # live microphone level while listening
var _level_seen := 0.0
signal inspector_toggled(on: bool)   # "Model inspector" (3D twin review panel); the face display has none
const UI_FONT := 20               # readable on a desk monitor; the default 16 px was too small
var options_box: VBoxContainer
var inspector_chk: CheckButton
var legend_lbl: Label
var pages := {}                   # settings "tree": page name -> VBoxContainer (root = the list)
var main_view: Array = []         # hidden while Settings is open
var connection: Label
var _state := "idle"
var _linked := false
var mute_chk: CheckButton
var dash_btn: Button
var confirm: ConfirmationDialog
var _confirm_id := ""
var last_status := {}
var _closing := false
var _close_ack := false
var strip: Control                # privacy_strip.gd: always visible (own CanvasLayer: kiosk / Options never hide it)
var preview: PanelContainer       # camera_preview.gd: "What does he see?"
var last_sent := []               # [type, payload] of the last message sent by the strip / preview (tests)
var title_lbl: Label              # "JARVES assistant" (+ " · profile <name>" for a non-default profile)
var profile_banner: Label         # red "TEST PROFILE" banner (Core status.profile.test), own CanvasLayer: always visible
var profile_name := "default"


func _ready() -> void:
	get_tree().auto_accept_quit = false
	link = CoreLink.new()
	link.name = "CoreLink"
	add_child(link)
	link.link_changed.connect(_on_link)
	link.message.connect(_on_message)
	theme = _make_theme()
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 10)
	add_child(box)
	var title := Label.new()
	title.text = "JARVES assistant"
	title.add_theme_font_size_override("font_size", UI_FONT + 4)
	title_lbl = title
	box.add_child(title)
	connection = Label.new()
	connection.text = "Connecting to Core..."
	box.add_child(connection)
	var toggle := Button.new()
	toggle.text = "Options"
	toggle.toggle_mode = true
	toggle.toggled.connect(_on_options)
	box.add_child(toggle)
	options_box = VBoxContainer.new()
	options_box.add_theme_constant_override("separation", 8)
	options_box.visible = false
	box.add_child(options_box)
	status_lbl = Label.new()
	status_lbl.text = "Core: connecting..."
	status_lbl.custom_minimum_size = Vector2(400, 0)
	status_lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	options_box.add_child(status_lbl)
	var legend := Label.new()
	legend_lbl = legend
	legend.text = LINK_LEGEND
	legend.modulate = Color(1, 1, 1, 0.6)
	legend.custom_minimum_size = Vector2(400, 0)
	legend.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	options_box.add_child(legend)
	activity = Label.new()
	activity.text = "Connecting to JARVES..."
	activity.add_theme_font_size_override("font_size", UI_FONT + 6)
	activity.custom_minimum_size = Vector2(400, 0)
	activity.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	activity.text_direction = Control.TEXT_DIRECTION_AUTO
	box.add_child(activity)
	level_bar = ProgressBar.new()
	level_bar.max_value = 1.0
	level_bar.show_percentage = false
	level_bar.custom_minimum_size = Vector2(400, 12)
	level_bar.tooltip_text = "Microphone level"
	options_box.add_child(level_bar)
	who = Label.new()
	who.modulate = Color(1, 1, 1, 0.6)
	box.add_child(who)
	caption = Label.new()
	caption.custom_minimum_size = Vector2(400, 0)
	caption.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	caption.text_direction = Control.TEXT_DIRECTION_AUTO
	box.add_child(caption)
	mute_chk = CheckButton.new()
	mute_chk.text = "Mic mute (software)"
	mute_chk.toggled.connect(func(on): link.send("mic.set_mute", {"muted": on}))
	options_box.add_child(mute_chk)
	# opens the local web dashboard (conversations, logs, costs, memory, settings); the Core opens the browser
	dash_btn = Button.new()
	dash_btn.text = "Open dashboard"
	dash_btn.tooltip_text = "Conversations, logs, costs, memory and settings in your browser"
	dash_btn.pressed.connect(func(): link.send("ui.open_dashboard"))
	options_box.add_child(dash_btn)
	inspector_chk = CheckButton.new()
	inspector_chk.text = "Model inspector"
	inspector_chk.visible = false     # main.gd (3D twin) turns it on; the face display has no inspector
	inspector_chk.toggled.connect(func(on): inspector_toggled.emit(on))
	options_box.add_child(inspector_chk)
	_build_menu()
	confirm = ConfirmationDialog.new()
	confirm.confirmed.connect(func(): _answer(true))
	confirm.canceled.connect(func(): _answer(false))
	add_child(confirm)
	var layer := CanvasLayer.new()
	layer.name = "PrivacyLayer"
	layer.layer = 5
	add_child(layer)
	strip = PrivacyStrip.new()
	strip.tooltip_text = "JARVES"
	strip.mic_pressed.connect(_toggle_mic)
	strip.camera_pressed.connect(_toggle_camera)
	strip.preview_pressed.connect(_toggle_preview)
	layer.add_child(strip)
	preview = CameraPreview.new()
	preview.close_requested.connect(func(_why): _close_preview())
	layer.add_child(preview)
	profile_banner = Label.new()
	profile_banner.name = "ProfileBanner"
	profile_banner.text = TEST_BANNER
	profile_banner.visible = false
	profile_banner.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	profile_banner.add_theme_color_override("font_color", Color.WHITE)
	profile_banner.add_theme_font_size_override("font_size", 20)
	var banner_bg := StyleBoxFlat.new()
	banner_bg.bg_color = Color(0.72, 0.08, 0.08, 0.92)
	banner_bg.content_margin_top = 4
	banner_bg.content_margin_bottom = 4
	profile_banner.add_theme_stylebox_override("normal", banner_bg)
	profile_banner.set_anchors_preset(Control.PRESET_TOP_WIDE)
	profile_banner.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.add_child(profile_banner)
	_set_controls(false)


## One calm look for the whole panel: bigger text, dark rounded card, roomy padding, large touch-friendly buttons.
static func make_ui_theme(font := 20) -> Theme:
	var t := Theme.new()
	t.default_font_size = font
	var card := StyleBoxFlat.new()
	card.bg_color = Color(0.07, 0.08, 0.11, 0.88)
	card.set_corner_radius_all(14)
	card.set_content_margin_all(18)
	t.set_stylebox("panel", "PanelContainer", card)
	for kind in ["normal", "hover", "pressed"]:
		var b := StyleBoxFlat.new()
		b.bg_color = {"normal": Color(0.17, 0.19, 0.25), "hover": Color(0.23, 0.26, 0.34), "pressed": Color(0.18, 0.36, 0.62)}[kind]
		b.set_corner_radius_all(10)
		b.content_margin_left = 14
		b.content_margin_right = 14
		b.content_margin_top = 9
		b.content_margin_bottom = 9
		t.set_stylebox(kind, "Button", b)
	return t


func _make_theme() -> Theme:
	return make_ui_theme(UI_FONT)


## Settings as a tree (like phone settings): a short list of rows; each row opens one small page with a Back row.
func _build_menu() -> void:
	for w in [activity, who, caption]:
		main_view.append(w)
	var take := func(w: Control, page: VBoxContainer) -> void:
		w.get_parent().remove_child(w)
		page.add_child(w)
	var root := _page("root", "")
	for spec in [["Status", "status"], ["Microphone", "mic"], ["Dashboard", "dashboard"], ["Help", "help"]]:
		var row := Button.new()
		row.text = "%s    ›" % spec[0]
		row.alignment = HORIZONTAL_ALIGNMENT_LEFT
		row.pressed.connect(_open_page.bind(spec[1]))
		root.add_child(row)
	inspector_chk.get_parent().remove_child(inspector_chk)
	root.add_child(inspector_chk)
	take.call(status_lbl, _page("status", "Status"))
	var mic := _page("mic", "Microphone")
	take.call(mute_chk, mic)
	take.call(level_bar, mic)
	take.call(dash_btn, _page("dashboard", "Dashboard"))
	take.call(legend_lbl, _page("help", "What the dot means"))
	_open_page("root")


func _page(key: String, title: String) -> VBoxContainer:
	var pg := VBoxContainer.new()
	pg.add_theme_constant_override("separation", 8)
	if key != "root":
		var back := Button.new()
		back.text = "‹    Back"
		back.alignment = HORIZONTAL_ALIGNMENT_LEFT
		back.pressed.connect(_open_page.bind("root"))
		pg.add_child(back)
		var t := Label.new()
		t.text = title
		t.add_theme_font_size_override("font_size", UI_FONT + 4)
		pg.add_child(t)
	pg.visible = false
	options_box.add_child(pg)
	pages[key] = pg
	return pg


func _open_page(key: String) -> void:
	for k in pages:
		pages[k].visible = k == key


func _on_options(on: bool) -> void:
	options_box.visible = on
	for w in main_view:
		w.visible = not on
	if on:
		_open_page("root")


func _process(delta: float) -> void:
	if preview and preview.tick(delta):
		_close_preview()
	if strip:                               # the strip never covers this panel (hidden in --kiosk: alpha 0)
		strip.avoid = get_global_rect() if visible and modulate.a > 0.05 else Rect2()


func _send(t: String, p: Dictionary = {}) -> void:
	last_sent = [t, p]
	link.send(t, p)


func _toggle_mic() -> void:
	# software mute toggle; the strip changes only when the Core's privacy.state says so (never optimistic)
	var st: String = strip.state_of("mic")
	if st == "" or st == "unavailable":
		return
	_send("mic.set_mute", {"muted": st != "muted"})


func _toggle_camera() -> void:
	# off at once; on = the Core asks on this screen first (confirm.request) and needs a connected camera
	var st: String = strip.state_of("camera")
	if st == "" or st == "unavailable":
		return
	_send("camera.set_enabled", {"on": st == "disabled"})


func _toggle_preview() -> void:
	if preview.is_open:
		_close_preview()
		return
	preview.set_locale(strip.locale)
	preview.set_last_seen(strip.last_seen_text)
	preview.open_preview(PREVIEW_S)
	strip.set_preview_open(true)
	_send("camera.preview", {"open": true, "seconds": PREVIEW_S})


func _close_preview() -> void:
	var was: bool = preview.is_open
	preview.close_preview("closed")
	strip.set_preview_open(false)
	if was:
		_send("camera.preview", {"open": false})


func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_CLOSE_REQUEST and not _closing:
		_close_desktop()


func _close_desktop() -> void:
	_closing = true
	_set_controls(false)
	if link and link.connected:
		link.send("desktop.close")
		var deadline := Time.get_ticks_msec() + 1500
		while not _close_ack and link.connected and Time.get_ticks_msec() < deadline:
			await get_tree().process_frame
	if link:
		link.stop()
	get_tree().quit(0)


func _set_controls(on: bool) -> void:
	mute_chk.disabled = not on
	dash_btn.disabled = not on


func _answer(ok: bool) -> void:
	if _confirm_id != "":
		link.send("confirm.reply", {"id": _confirm_id, "approved": ok})
		_confirm_id = ""


func _on_link(connected: bool, detail: String) -> void:
	_linked = connected
	_set_controls(connected)
	strip.set_linked(connected)
	if not connected:
		preview.close_preview("offline")      # no frames without the Core; nothing kept
		strip.set_preview_open(false)
		status_lbl.text = "Core: " + detail
		activity.text = "Not connected"
		if face:
			face.set_state("idle")
			face.set_link("offline")
			face.set_activity("done", "")
			face.set_emotion("")
		connection.text = "● " + LINK_CAPTION["offline"]
		connection.modulate = face_link_color("offline")
	else:
		_show_activity(_state)


## Defensive readers: a malformed message must never break the face. The Core sends clean data, but a protocol
## hiccup, a version mismatch or a bug must not take the display down (tests/test_face_protocol_godot.py).
static func _str(v, d := "") -> String:
	if v == null:
		return d
	return v if typeof(v) == TYPE_STRING else str(v)


static func _num(v, d := 0.0) -> float:
	if typeof(v) != TYPE_INT and typeof(v) != TYPE_FLOAT:
		return d
	var f := float(v)
	return d if (is_nan(f) or is_inf(f)) else f


static func _bool(v) -> bool:
	return typeof(v) == TYPE_BOOL and v


static func _dict(v) -> Dictionary:
	return v if typeof(v) == TYPE_DICTIONARY else {}


func _on_message(t: String, p: Dictionary) -> void:
	if t == "desktop.closed":
		_close_ack = true
		return
	if t == "display.close":
		# the Core hides the window ("hide yourself", tray, dashboard, Core quitting); the Core itself keeps running
		if not _closing:
			_close_desktop()
		return
	match t:
		"face.set_state":
			var st := _str(p.get("state"), "idle")
			if face:
				face.set_state(st)
			_show_activity(st)
		"face.emotion":
			# transient reaction {emotion: laugh|cry|surprised|love|sad, ms}; eases back to the current state
			if face:
				face.set_emotion(_str(p.get("emotion")), clampi(int(_num(p.get("ms"), -1.0)), -1, 60000))
		"face.set_gaze":
			# camera: where the person is {x, y: -1..1, confidence: 0..1}; confidence 0 = stop tracking
			if face:
				face.set_gaze(_num(p.get("x")), _num(p.get("y")), clampf(_num(p.get("confidence")), 0.0, 1.0))
		"appearance.set":
			# {mode: face|avatar2d|avatar3d|hidden}; anything else is ignored (the current presentation stays)
			if appearance:
				appearance.set_mode(p.get("mode"))
		"privacy.state":
			# {mic, camera, speaker, cloud_audio}: the always-visible strip (privacy_strip.gd checks every field)
			strip.apply(p)
			if preview.is_open:
				preview.set_last_seen(strip.last_seen_text)
		"camera.preview_frame":
			preview.apply_frame(p)                # ignored unless the window is open
		"camera.preview_state":
			preview.apply_state(p)
		"camera.enabled":
			# {on, active, reason}: answer to the camera chip; the strip itself follows privacy.state
			if typeof(p.get("on")) == TYPE_BOOL and not p.get("on") and _str(p.get("reason")) != "":
				caption.text = "Camera: " + _str(p.get("reason")).substr(0, 120)
		"camera.indicator":
			# {on, flash}: the camera is streaming / a photo was just taken (software indicator)
			if face:
				face.set_camera(_bool(p.get("on")), _bool(p.get("flash")))
		"assistant.activity":
			# {kind?, text, detail?}: with a kind it is also shown on the face (pill on the lower arc);
			# kind "done" or an empty text clears it. Without a kind it stays a panel-only message.
			var words := _str(p.get("text")).substr(0, 200)
			if face and p.has("kind"):
				face.set_activity(_str(p.get("kind")), words, _str(p.get("detail")).substr(0, 200))
			activity.text = words
			if activity.text == "":
				activity.text = ACTIVITY.get(_state, _state)
			if activity.text.contains("Loading voice"):
				connection.text = "● " + LINK_CAPTION["connecting"]
				connection.modulate = face_link_color("connecting")
				if face:
					face.set_link("connecting")
		"speaker.level":
			if face:
				face.set_speech_level(clampf(_num(p.get("level")), 0.0, 1.0))
		"mic.level":
			level_bar.value = clampf(_num(p.get("level")), 0.0, 1.0)
			_level_seen = Time.get_ticks_msec()
			if face:
				face.set_mic_level(level_bar.value)
		"assistant.text":
			_say("JARVES", _str(p.get("text")).substr(0, 1500))
		"user.transcript":
			_say("You (heard)", _str(p.get("text")).substr(0, 1500))
		"error":
			caption.text = "! %s: %s %s" % [_str(p.get("code")), _str(p.get("message")), _str(p.get("action"))]
			activity.text = "Problem: %s. %s" % [_str(p.get("message")), _str(p.get("action"))]
		"confirm.request":
			_confirm_id = _str(p.get("id"))
			confirm.dialog_text = _str(p.get("prompt"), "Confirm?")
			confirm.popup_centered()
		"confirm.cancel":
			# answered elsewhere (approval popup, dashboard, voice) or expired: close the dialog without replying
			if _str(p.get("id")) == _confirm_id:
				_confirm_id = ""
				confirm.hide()
		"status":
			last_status = p
			show_profile(_dict(p.get("profile")))
			_show_status(p)
		"dashboard.opened":
			# {url, browser}: the Core opened it in the browser; without a browser show where it runs
			var where := _str(p.get("url")).substr(0, 200)
			var opened: bool = typeof(p.get("browser")) == TYPE_BOOL and p.get("browser")
			caption.text = "Dashboard opened in your browser" if opened else "Dashboard running at " + where


const TEST_BANNER := "TEST PROFILE · not your real data"


## Profile from the Core `status` snapshot (profiles.info): a non-default name goes into the panel title, a test
## profile shows the red banner. Missing / malformed = the default look (older Cores send no profile).
func show_profile(pr: Dictionary) -> void:
	var pname := _str(pr.get("name"), "default").substr(0, 24)
	profile_name = pname if pname != "" else "default"
	title_lbl.text = "JARVES assistant" if profile_name == "default" else "JARVES assistant · profile " + profile_name
	profile_banner.visible = _bool(pr.get("test"))


const ACTIVITY := {
	"idle": "Ready",
	"listening": "Listening · speak now",
	"thinking": "Thinking...",
	"speaking": "Speaking",
	"happy": "Done",
	"error": "Problem · open Options for details",
}


## Status dot on the face (face.gd DOT colours). Captions live only in this panel, never on the face.
const LINK_CAPTION := {
	"ready": "Ready · voice link up", "live": "Live conversation connected",
	"connecting": "Connecting to voice · please wait", "degraded": "Voice degraded · open Options",
	"offline": "Offline · Core disconnected", "error": "Problem · open Options for details",
	"muted": "Microphone muted (software)",
}
const LINK_LEGEND := "Face dot: green = ready · blue pulse = live conversation · amber = connecting/degraded · red = offline/error · grey slashed = mic muted"


static func link_state_for(linked: bool, st: Dictionary, face_state: String) -> String:
	# one truthful link state from the Core `status` snapshot (core/src/jarves/app.py snapshot())
	if not linked:
		return "offline"
	if st.get("mic_muted", false):
		return "muted"
	if st.get("live_session", false):
		return "live"
	if st.get("voice_connecting", false) or st.is_empty():
		return "connecting"
	if face_state == "error":
		return "error"
	var comps := _dict(st.get("components"))
	var voice := str(_dict(comps.get("voice")).get("status", ""))
	var wake := str(_dict(comps.get("wake_word")).get("status", ""))
	if voice in ["unavailable", "degraded"] or wake in ["unavailable", "degraded"]:
		return "degraded"
	if voice == "ok" or (wake == "ok" and st.get("wake_listening", false)):
		return "ready"
	return "degraded"


static func face_link_color(ls: String) -> Color:
	return FaceScript.DOT.get(ls, Color.WHITE)


func _show_activity(state: String) -> void:
	_state = state
	var ls := link_state_for(_linked, last_status, state)
	connection.text = "● " + LINK_CAPTION[ls]
	connection.modulate = face_link_color(ls)
	activity.text = ACTIVITY.get(state, state) if ls in ["live", "ready"] else LINK_CAPTION[ls]
	if face:
		face.set_link(ls)
	if state != "listening" and Time.get_ticks_msec() - _level_seen > 500:
		level_bar.value = 0.0


func _say(speaker: String, text: String) -> void:
	who.text = speaker
	caption.text = text
	# QA 18 Oct: TEXT_DIRECTION_AUTO still laid Hebrew out left-to-right, so pick the direction explicitly
	var rtl := false
	for ch in text:
		var c := ch.unicode_at(0)
		if c >= 0x0590 and c <= 0x05FF:
			rtl = true
			break
	caption.text_direction = Control.TEXT_DIRECTION_RTL if rtl else Control.TEXT_DIRECTION_LTR
	caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT if rtl else HORIZONTAL_ALIGNMENT_LEFT


const FRIENDLY := {"ai": "AI answers", "voice": "Voice", "speech_in": "Speech recognition", "speech_out": "Voice output",
	"mic": "Microphone", "audio": "Audio device", "music": "Music", "wake_word": "\"Hey Jarvis\"", "face_link": "Face link"}


func _show_status(p: Dictionary) -> void:
	var lines := ["Core: %s   voice: %s%s" % [p.get("core", "?"), p.get("voice_mode", "?"),
			"   (mic muted)" if p.get("mic_muted", false) else ""]]
	var comps := _dict(p.get("components"))
	for k in comps:
		var c := _dict(comps[k])
		if c.get("status", "") in ["unavailable", "degraded"]:
			# one short line per problem, with what to do; full detail stays in `jarvis status`
			var act: String = str(c.get("action", ""))
			lines.append("- %s: %s" % [FRIENDLY.get(k, k), act if act != "" else str(c.get("detail", "")).substr(0, 60)])
	status_lbl.text = "\n".join(lines)
	mute_chk.set_pressed_no_signal(bool(p.get("mic_muted", false)))

	_show_activity(_str(p.get("face_state"), _state))
