extends Control
## Face display app (Mode B, docs/FACE_RUNTIME.md): the round face full-window + assistant controls.
## Same face.gd and Core link as the 3D twin. Args: --kiosk (hide controls; Space still talks),
## --state=<s> (preview a state without the Core), --shot=<png> (screenshot then quit), --check (self-test).

const Face := preload("res://face/face.gd")
const AssistantUI := preload("res://face/assistant_ui.gd")

var face: Control
var ui: PanelContainer


func _ready() -> void:
	var bg := ColorRect.new()
	bg.color = Color(0.03, 0.03, 0.05)
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(bg)
	face = Face.new()
	add_child(face)
	ui = AssistantUI.new()
	ui.face = face
	ui.position = Vector2(12, 12)
	add_child(ui)
	get_viewport().size_changed.connect(_layout)
	_layout()
	var args := OS.get_cmdline_user_args()
	for a in args:
		if a == "--kiosk":
			ui.modulate.a = 0.0
		elif a.begins_with("--state="):
			face.set_state(a.substr(8))
	if "--check" in args:
		_check()
		return
	if "--e2e" in args or "--e2e-close" in args:
		_e2e()
		return
	for a in args:
		if a.begins_with("--shot="):
			await get_tree().create_timer(1.0).timeout
			get_viewport().get_texture().get_image().save_png(a.substr(7))
			get_tree().quit(0)


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


func _check() -> void:
	# headless self-test: every state animates without script errors; link reports a truthful state
	var ok := true
	for st in face.STATES:
		face.set_state(st)
		for i in 5:
			await get_tree().process_frame
		if face.state != st:
			ok = false
	# Silence must keep the mouth closed even if a stale speaking state remains.
	face.set_state("speaking")
	face.set_speech_level(0.0)
	face._process(1.0)
	ok = ok and face._mouth < 0.01
	face.set_speech_level(0.8)
	face._process(1.0)
	ok = ok and face._mouth > 0.5
	face._speech_seen = -1000
	face._process(1.0)
	ok = ok and face._mouth < 0.01 and not ui.options_box.visible
	ui._on_link(true, "test")
	ui._on_message("status", {"live_session": true, "face_state": "listening"})
	ok = ok and ui.connection.text == "Voice connected" and face.connection == "LISTENING"
	ui._on_link(false, "test disconnect")
	ok = ok and face.connection == "OFFLINE"
	face.set_state(face.STATES[-1])
	face.set_state("not-a-state")
	ok = ok and face.state == face.STATES[-1]
	await get_tree().create_timer(0.5).timeout
	var link_ok: bool = ui.link.connected or ui.link.detail != ""
	print("FACE CHECK RESULT: %s (states %d, link: %s)" % ["PASS" if ok and link_ok else "FAIL", face.STATES.size(),
			ui.link.detail])
	get_tree().quit(0 if ok and link_ok else 1)
