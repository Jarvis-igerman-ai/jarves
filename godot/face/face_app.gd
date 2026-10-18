extends Control
## Face display app (Mode B, docs/FACE_RUNTIME.md): the round face full-window + assistant controls.
## Same face.gd and Core link as the 3D twin. Args: --kiosk (hide controls; Space still talks),
## --state=<s> / --link=<ready|live|connecting|degraded|offline|error|muted> / --level=<0..1> (preview without
## the Core: the link no longer drives the face), --shot=<png> (screenshot then quit), --check (self-test),
## --low (cheaper face shader).

const Face := preload("res://face/face.gd")
const AssistantUI := preload("res://face/assistant_ui.gd")

var face: Control
var ui: PanelContainer
var _preview_level := -1.0


func _ready() -> void:
	var bg := ColorRect.new()
	bg.color = Color(0.0, 0.0, 0.0)
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(bg)
	face = Face.new()
	add_child(face)
	ui = AssistantUI.new()
	ui.position = Vector2(12, 12)
	var args := OS.get_cmdline_user_args()
	var preview := false
	var link_given := false
	for a in args:
		if a == "--kiosk":
			ui.modulate.a = 0.0
		elif a.begins_with("--state="):
			face.set_state(a.substr(8))
			preview = true
		elif a.begins_with("--link="):
			face.set_link(a.substr(7))
			preview = true
			link_given = true
		elif a.begins_with("--level="):
			_preview_level = float(a.substr(8))
	if not preview:
		ui.face = face
	if preview and not link_given:
		face.set_link("ready")
	add_child(ui)
	get_viewport().size_changed.connect(_layout)
	_layout()
	if "--check" in args:
		ui.face = face
		_check()
		return
	if "--e2e" in args or "--e2e-close" in args:
		ui.face = face
		_e2e()
		return
	for a in args:
		if a.begins_with("--shot="):
			await get_tree().create_timer(1.6).timeout
			get_viewport().get_texture().get_image().save_png(a.substr(7))
			get_tree().quit(0)


func _process(_delta: float) -> void:
	if _preview_level >= 0.0:
		# preview only: a speech-like envelope so the speaking/listening shots show real level-driven motion
		var lv := _preview_level * (0.75 + 0.25 * sin(face.t * 9.0))
		face.set_speech_level(lv)
		face.set_mic_level(lv)


func _layout() -> void:
	var s := get_viewport_rect().size
	var d: float = min(s.x, s.y)
	face.size = Vector2(d, d)
	face.position = (s - face.size) / 2.0


func _e2e() -> void:
	# real Godot <-> real Core round trip: connect, ask, expect a reply and the face state sequence
	var states: Array = []
	var replies: Array = []
	ui.link.message.connect(func(t, p):
		if t == "face.set_state":
			states.append(p.get("state"))
		elif t == "assistant.text" and p.get("code", "") != "GREETING":
			replies.append(p))
	var t0 := Time.get_ticks_msec()
	while not ui.link.connected and Time.get_ticks_msec() - t0 < 15000:
		await get_tree().process_frame
	if not ui.link.connected:
		print("E2E RESULT: FAIL (not connected: %s)" % ui.link.detail)
		get_tree().quit(1)
		return
	if "--e2e-close" in OS.get_cmdline_user_args():
		# Exercise the same notification as the OS window close button.
		ui.notification(NOTIFICATION_WM_CLOSE_REQUEST)
		return
	ui.link.send("user.text", {"text": "מה השעה?"})
	t0 = Time.get_ticks_msec()
	while (replies.is_empty() or states.is_empty() or states[-1] != "idle") and Time.get_ticks_msec() - t0 < 20000:
		await get_tree().process_frame
	var ok: bool = not replies.is_empty() and "thinking" in states and face.state == "idle"
	print("E2E RESULT: %s (reply: %s; face states: %s)" % ["PASS" if ok else "FAIL",
			replies[0].get("text", "") if replies.size() else "-", ",".join(states)])
	get_tree().quit(0 if ok else 1)


func _settle(frames := 40) -> void:
	for i in frames:
		face._process(1.0 / 60.0)


func _check() -> void:
	# headless self-test: every state animates without script errors, each expression is distinct,
	# the mouth only moves with real audio, the status dot is truthful, and no text is drawn on the face.
	var errs: Array = []
	var looks := {}
	face.set_link("ready")
	for st in face.STATES:
		face.set_state(st)
		for i in 5:
			await get_tree().process_frame
		_settle()
		if face.state != st:
			errs.append("state %s not applied" % st)
		var v: Dictionary = face._v
		looks[st] = [v.col, v.lid_low, v.lid_up, v.lid_tilt, v.smile, v.ring, v.spin, v.curve]
	for a in face.STATES:
		for b in face.STATES:
			if a < b and str(looks[a]) == str(looks[b]):
				errs.append("states %s and %s look identical" % [a, b])
	if looks["happy"][7] < 0.5 or looks["thinking"][6] < 0.5 or looks["error"][0].r < 0.9:
		errs.append("happy crescent / thinking comet / error colour missing")
	# Silence must keep the mouth closed even if a stale speaking state remains.
	face.set_state("speaking")
	face.set_speech_level(0.0)
	_settle()
	if face._mouth > 0.01:
		errs.append("mouth open without audio")
	face.set_speech_level(0.8)
	_settle(10)
	if face._mouth < 0.5:
		errs.append("mouth does not follow speaker level")
	face._speech_seen = -1000
	_settle()
	if face._mouth > 0.01 or ui.options_box.visible:
		errs.append("mouth stays open after audio stops")
	# listening rim reacts to the mic level
	face.set_state("listening")
	face.set_mic_level(0.0)
	_settle()
	var quiet: float = face._v.ring
	face.set_mic_level(0.9)
	_settle(10)
	if face._v.ring < quiet + 0.5:
		errs.append("listening ring ignores mic level")
	# status dot from the Core `status` message
	ui._on_link(true, "test")
	var cases := [
		[{}, "connecting"],
		[{"components": {"voice": {"status": "ok"}, "wake_word": {"status": "ok"}}, "wake_listening": true}, "ready"],
		[{"live_session": true, "face_state": "listening"}, "live"],
		[{"voice_connecting": true}, "connecting"],
		[{"components": {"voice": {"status": "degraded"}}}, "degraded"],
		[{"components": {"voice": {"status": "ok"}}, "face_state": "error"}, "error"],
		[{"mic_muted": true, "live_session": true}, "muted"],
	]
	for c in cases:
		ui._on_message("status", c[0])
		if face.link_state != c[1]:
			errs.append("status %s -> dot %s, want %s" % [c[0], face.link_state, c[1]])
		elif ui.connection.text.contains("Hey Jarvis") or ui.connection.modulate != face.dot_color():
			errs.append("panel caption for %s wrong" % c[1])
	var colours := {}
	for ls in face.LINKS:
		face.set_link(ls)
		colours[str(face.dot_color())] = true
	if colours.size() < 5:
		errs.append("dot colours not distinct")
	ui._on_link(false, "test disconnect")
	_settle()
	if face.link_state != "offline" or not face.is_asleep() or face._v.sleep < 0.9:
		errs.append("offline does not put the face to sleep")
	face.set_state(face.STATES[-1])
	face.set_state("not-a-state")
	if face.state != face.STATES[-1]:
		errs.append("invalid state accepted")
	# the face itself contains no text/labels: only the shader canvas
	for n in face.get_children():
		if n is Label or n is RichTextLabel:
			errs.append("text node on the face")
	if face.get_child_count() != 1 or not (face.get_child(0).material is ShaderMaterial):
		errs.append("face shader canvas missing")
	await get_tree().create_timer(0.5).timeout
	var link_ok: bool = ui.link.connected or ui.link.detail != ""
	var ok := errs.is_empty() and link_ok
	print("FACE CHECK RESULT: %s (states %d, links %d, link: %s) %s" % ["PASS" if ok else "FAIL", face.STATES.size(),
			face.LINKS.size(), ui.link.detail, "; ".join(errs)])
	get_tree().quit(0 if ok else 1)
