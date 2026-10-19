extends Control
## Face display app (Mode B, docs/FACE_RUNTIME.md): the round face full-window + assistant controls.
## Same face.gd and Core link as the 3D twin. Args: --kiosk (hide controls; Space still talks),
## --state=<s> / --link=<ready|live|connecting|degraded|offline|error|muted> / --level=<0..1> (preview without
## the Core: the link no longer drives the face), --shot=<png> (screenshot then quit), --check (self-test),
## --low (cheaper face shader), --lively (old busier motion), --seed=<n> (repeatable motion),
## --seq=<png> [--seq-frames=8 --seq-span=12 | --seq-blink] (contact sheet of frames on a simulated clock, then quit),
## --appearance=<face|avatar2d|avatar3d|hidden> (start presentation; the Core's appearance.set overrides it),
## --privacy=<listening|wake|muted|speaking|photo|unavailable|preview|unknown>[_en] [--preview-demo] (privacy strip
## preview without a Core; the demo picture is a generated test pattern).

const Face := preload("res://face/face.gd")
const AssistantUI := preload("res://face/assistant_ui.gd")
const Appearance := preload("res://face/appearance.gd")

var face: Control
var appearance: Control          # face / avatar2d / avatar3d / hidden (appearance.gd); face.gd drives them all
var ui: PanelContainer
var _preview_level := -1.0


func _ready() -> void:
	var bg := ColorRect.new()
	bg.color = Color(0.0, 0.0, 0.0)
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(bg)
	face = Face.new()
	appearance = Appearance.new()
	appearance.name = "Appearance"
	appearance.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(appearance)
	appearance.add_child(face)
	appearance.setup(face)
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
		elif a.begins_with("--seed="):
			face.reseed(int(a.substr(7)))
		elif a.begins_with("--appearance="):
			appearance.set_mode(a.substr(13))
	if not preview:
		ui.face = face
		ui.appearance = appearance
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
		if a.begins_with("--privacy="):
			_privacy_demo(a.substr(10), "--preview-demo" in args)
	for a in args:
		if a.begins_with("--seq="):
			await _sequence(a.substr(6), args)
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
	appearance.layout(Rect2((s - Vector2(d, d)) / 2.0, Vector2(d, d)))


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


## --privacy=<scenario> [--preview-demo]: the privacy strip without a Core (screenshots). The preview picture is a
## generated test pattern, never a camera image.
const PRIVACY_DEMOS := {
	"listening": ["listening", "presence", "quiet", "sending"], "wake": ["wake_only", "disabled", "quiet", "off"],
	"muted": ["muted", "off", "quiet", "off"], "speaking": ["gated", "disabled", "speaking", "connected"],
	"photo": ["listening", "photo", "quiet", "sending"], "unavailable": ["unavailable", "unavailable", "unavailable", "off"],
	"preview": ["wake_only", "preview", "quiet", "off"],
}


static func privacy_payload(v: Array, loc := "he", last := "") -> Dictionary:
	return {"v": 1, "locale": loc, "mic": {"state": v[0]}, "camera": {"state": v[1], "preview_available":
		v[1] in ["off", "presence", "preview"], "last_seen": {"text": last, "ts": 0} if last != "" else null},
		"speaker": {"state": v[2]}, "cloud_audio": {"on": v[3] == "sending", "state": v[3]}}


static func test_frame(w := 320, h := 240, seq := 1) -> Dictionary:
	# synthetic test pattern (colour bars + a moving square) encoded like a Core preview frame
	var img := Image.create(w, h, false, Image.FORMAT_RGB8)
	var bars := [Color(0.75, 0.75, 0.75), Color(0.75, 0.75, 0), Color(0, 0.75, 0.75), Color(0, 0.75, 0),
		Color(0.75, 0, 0.75), Color(0.75, 0, 0), Color(0, 0, 0.75)]
	for i in bars.size():
		img.fill_rect(Rect2i(i * w / bars.size(), 0, w / bars.size() + 1, h), bars[i])
	img.fill_rect(Rect2i((seq * 17) % (w - 40), h / 2 - 20, 40, 40), Color.WHITE)
	return {"seq": seq, "mime": "image/png", "width": w, "height": h,
		"data": Marshalls.raw_to_base64(img.save_png_to_buffer())}


func _privacy_demo(which: String, with_preview: bool) -> void:
	ui.link.set_process(false)
	ui.strip.set_linked(which != "unknown")
	var loc := "en" if which.ends_with("_en") else "he"
	var key := which.trim_suffix("_en")
	if PRIVACY_DEMOS.has(key):
		var last := "זה ספל קפה לבן עם כיתוב כחול." if loc == "he" else "A white coffee mug with blue writing."
		ui._on_message("privacy.state", privacy_payload(PRIVACY_DEMOS[key], loc, last))
	if with_preview:
		ui._toggle_preview()
		ui._on_message("camera.preview_frame", test_frame())


func _privacy_checks(errs: Array) -> String:
	# privacy.state -> strip (truthful: unknown until a valid message, per-field validation, stale -> unknown,
	# link down -> unknown); clicks send the right message; the preview shows frames only while open.
	var s: Control = ui.strip
	s.set_linked(false)
	if s.get_parent() == null or not (s.get_parent() is CanvasLayer) or not s.visible:
		errs.append("privacy strip is not on its own always-visible layer")
	for k in s.ITEMS:
		if s.state_of(k) != "" or s.tone(k) != "unknown":
			errs.append("strip shows %s before any privacy.state" % k)
	s.set_linked(true)
	var seen_labels := {}
	for k in s.STATES:
		for st in s.STATES[k]:
			var v := {"mic": "idle", "camera": "off", "speaker": "quiet", "cloud_audio": "off"}
			v[k] = st
			ui._on_message("privacy.state", privacy_payload([v.mic, v.camera, v.speaker, v.cloud_audio]))
			if s.state_of(k) != st or s.label(k) == "" or s.label(k) == s.UNKNOWN[0]:
				errs.append("privacy.state %s=%s not shown (%s)" % [k, st, s.label(k)])
			seen_labels[k + ":" + s.label(k)] = true
	var n := 0
	for k in s.STATES:
		n += s.STATES[k].size()
	if seen_labels.size() != n:
		errs.append("two states share one label (%d of %d distinct)" % [seen_labels.size(), n])
	if s.tone_color("capture") == s.tone_color("off") or s.tone_color("out") == s.tone_color("local"):
		errs.append("strip tones not distinct")
	ui._on_message("privacy.state", privacy_payload(["listening", "presence", "quiet", "sending"], "en"))
	if s.label("mic") != "Listening" or s.label("cloud_audio") != "Audio to cloud" or s.tone("mic") != "capture":
		errs.append("English labels / tones wrong: %s %s" % [s.label("mic"), s.label("cloud_audio")])
	ui._on_message("privacy.state", privacy_payload(["listening", "presence", "quiet", "sending"]))
	if s.label("mic") != "מאזין":
		errs.append("Hebrew label wrong: %s" % s.label("mic"))
	# geometry: inside the window, above the eyes, every chip clickable
	var vr := get_viewport_rect()
	var big := vr.size.x >= 300.0              # --headless runs a 64 px window: geometry is checked in screenshots
	var gr := Rect2(s.global_position, s.size)
	if big and (not vr.encloses(gr) or gr.size.y < 20 or gr.end.y > vr.size.y * 0.2):
		errs.append("privacy strip outside the window / too low (%s in %s)" % [gr, vr])
	for k in s.ITEMS + ["preview"]:
		if s.chip_rect(k).size.x < s.size.y:
			errs.append("chip %s has no clickable area" % k)
	# malformed messages: each bad field becomes unknown, nothing crashes, nothing is guessed
	for bad in [{}, {"mic": "listening"}, {"mic": {"state": 5}}, {"camera": {"state": "nonsense"}},
			{"mic": null, "camera": [], "speaker": {"state": ["x"]}, "cloud_audio": "on"}, {"locale": 7},
			{"camera": {"state": "preview", "preview_available": "yes", "last_seen": {"text": 12}}},
			{"mic": {"state": "LISTENING"}}, {"cloud_audio": {"on": "true", "state": "nope"}}]:
		ui._on_message("privacy.state", bad)
		for k in s.ITEMS:
			var d = bad.get(k)
			var ok_field: bool = typeof(d) == TYPE_DICTIONARY and typeof(d.get("state")) == TYPE_STRING \
					and s.STATES[k].has(d.get("state"))
			if not ok_field and s.state_of(k) != "":
				errs.append("malformed %s accepted: %s -> %s" % [k, bad, s.state_of(k)])
	if s.preview_available:
		errs.append("preview_available accepted a non-bool")
	ui._on_message("privacy.state", {"cloud_audio": {"on": true, "state": "off"}})
	if s.state_of("cloud_audio") != "sending":
		errs.append("cloud_audio on=true not shown as sending")
	# stale and link down -> unknown
	ui._on_message("privacy.state", privacy_payload(["muted", "disabled", "quiet", "off"]))
	s._seen_at -= s.STALE_S + 1.0
	if s.state_of("mic") != "":
		errs.append("stale privacy.state still shown")
	ui._on_message("privacy.state", privacy_payload(["muted", "disabled", "quiet", "off"]))
	# clicks -> the right Core message (the strip itself waits for the Core's answer)
	ui._toggle_mic()
	if ui.last_sent != ["mic.set_mute", {"muted": false}] or s.state_of("mic") != "muted":
		errs.append("mic chip click sent %s" % [ui.last_sent])
	ui._toggle_camera()
	if ui.last_sent != ["camera.set_enabled", {"on": true}]:
		errs.append("camera chip click (disabled -> on) sent %s" % [ui.last_sent])
	ui._on_message("privacy.state", privacy_payload(["listening", "presence", "quiet", "sending"]))
	ui._toggle_mic()
	ui._toggle_camera()
	if ui.last_sent != ["camera.set_enabled", {"on": false}]:
		errs.append("camera chip click (on -> off) sent %s" % [ui.last_sent])
	ui._on_message("privacy.state", privacy_payload(["unavailable", "unavailable", "unavailable", "off"]))
	ui.last_sent = []
	ui._toggle_mic()
	ui._toggle_camera()
	if not ui.last_sent.is_empty():
		errs.append("click on an unavailable device sent %s" % [ui.last_sent])
	# preview: frames only while open; malformed frames ignored; refusal shown; auto close sends close
	var pv: PanelContainer = ui.preview
	ui._on_message("camera.preview_frame", test_frame())
	if pv.is_open or pv.picture.texture != null or pv.visible:
		errs.append("preview frame shown while the preview is closed")
	ui._on_message("privacy.state", privacy_payload(["wake_only", "off", "quiet", "off"], "he", "זה ספל."))
	if not s.preview_enabled():
		errs.append("preview button disabled although the camera is available")
	ui._toggle_preview()
	if not pv.is_open or ui.last_sent != ["camera.preview", {"open": true, "seconds": ui.PREVIEW_S}] or not s.preview_open:
		errs.append("preview button did not open / ask the Core: %s" % [ui.last_sent])
	for bad in [{}, {"data": 5, "mime": "image/png"}, {"data": "!!!notbase64!!!", "mime": "image/png", "seq": 9},
			{"data": test_frame().data, "mime": "image/gif"}, {"data": "QUJDREVGR0g=", "mime": "image/jpeg"},
			{"data": "a".repeat(500000), "mime": "image/png"}, {"data": test_frame().data, "mime": "image/jpeg"}]:
		ui._on_message("camera.preview_frame", bad)
	if pv.frames != 0 or pv.picture.texture != null:
		errs.append("malformed preview frame shown")
	ui._on_message("camera.preview_frame", test_frame(320, 240, 3))
	ui._on_message("camera.preview_frame", test_frame(320, 240, 2))       # older: dropped
	if pv.frames != 1 or pv.picture.texture == null or pv.picture.texture.get_width() != 320:
		errs.append("valid preview frame not shown (frames %d)" % pv.frames)
	var jimg := Image.create(160, 120, false, Image.FORMAT_RGB8)
	jimg.fill(Color(0.2, 0.5, 0.8))
	ui._on_message("camera.preview_frame", {"seq": 4, "mime": "image/jpeg", "width": 160, "height": 120,
		"data": Marshalls.raw_to_base64(jimg.save_jpg_to_buffer(0.7))})       # what the Core sends (OpenCV JPEG)
	if pv.frames != 2 or pv.picture.texture.get_width() != 160:
		errs.append("JPEG preview frame not shown")
	if pv._last.text != "זה ספל.":
		errs.append("last description caption missing: %s" % pv._last.text)
	var pr := Rect2(pv.global_position, pv.get_combined_minimum_size())
	if big and (not vr.encloses(pr) or pr.intersects(gr)):
		errs.append("preview window outside the screen / over the strip (%s, strip %s)" % [pr, gr])
	ui._on_message("camera.preview_state", {"open": false, "reason": "CAMERA_DISABLED"})
	if pv.picture.texture != null or not pv._status.text.contains("כבויה"):
		errs.append("preview refusal not shown")
	ui._process(5.0)
	if pv.is_open or pv.visible or ui.last_sent != ["camera.preview", {"open": false}] or s.preview_open:
		errs.append("preview does not close by itself after the refusal (%s)" % [ui.last_sent])
	ui._toggle_preview()
	ui._process(ui.PREVIEW_S + 0.5)
	if pv.is_open or ui.last_sent != ["camera.preview", {"open": false}]:
		errs.append("preview does not auto-close after %d s" % ui.PREVIEW_S)
	ui._toggle_preview()
	ui._on_link(false, "test")
	if pv.is_open or s.state_of("mic") != "" or s.preview_enabled():
		errs.append("link down does not close the preview / reset the strip")
	ui._on_link(true, "test")
	return "privacy strip: %d states with distinct labels, malformed/stale/offline -> unknown, preview open/close OK" % n


func _settle(frames := 40) -> void:
	for i in frames:
		face._process(1.0 / 60.0)


func _sequence(path: String, args: PackedStringArray) -> void:
	# contact sheet of the face on a simulated clock (exact times, independent of the real frame rate):
	# uniform frames over a span, or (--seq-blink) the phases of the first blink.
	var frames := 8
	var span := 12.0
	for a in args:
		if a.begins_with("--seq-frames="):
			frames = int(a.substr(13))
		elif a.begins_with("--seq-span="):
			span = float(a.substr(11))
	face.set_process(false)
	face.snap()
	var times: Array = []
	if "--seq-blink" in args:
		var n0: int = face.blink_count
		while face.blink_count == n0 and face.t < 30.0:
			face._process(1.0 / 240.0)
		var tb: float = face.t
		for dt in [-0.2, 0.02, 0.04, 0.055, 0.075, 0.1, 0.13, 0.2]:
			times.append(tb + dt)
		times[0] = face.t      # "before" frame: the blink has just started, use the frame right before instead
		face.t -= 1.0 / 240.0  # (the clock only runs forward; the first shot shows the lid as it starts to close)
	else:
		for i in frames:
			times.append(i * span / frames)
	var cols := 4
	var cell := 360
	var sheet := Image.create(cell * cols, cell * int(ceil(times.size() / float(cols))), false, Image.FORMAT_RGBA8)
	for i in times.size():
		while face.t + 1e-6 < times[i]:
			face._process(minf(1.0 / 240.0, times[i] - face.t))
		face._apply()
		await RenderingServer.frame_post_draw
		await RenderingServer.frame_post_draw
		var img := get_viewport().get_texture().get_image()
		img.convert(Image.FORMAT_RGBA8)
		img.resize(cell, cell, Image.INTERPOLATE_LANCZOS)
		sheet.blit_rect(img, Rect2i(0, 0, cell, cell), Vector2i((i % cols) * cell, (i / cols) * cell))
		print("seq frame %d: t=%.3f s blink=%.2f gaze=%.2f%% of radius" % [i, face.t, face._blink, face.gaze_radius() * 100.0])
	sheet.save_png(path)
	get_tree().quit(0)


func _simulate(f: Control, seconds: float, each_frame := Callable()) -> Dictionary:
	# drive a face on a simulated 60 fps clock and measure its motion (no real waiting)
	var dt := 1.0 / 60.0
	var blinks0: int = f.blink_count
	var max_gaze := 0.0
	var path := 0.0
	var y_min := 1e9
	var y_max := -1e9
	var prev: Vector2 = f._look * f.GAZE_TO_RADIUS
	for i in int(seconds / dt):
		if each_frame.is_valid():
			each_frame.call(f)
		f._process(dt)
		var p: Vector2 = f._look * f.GAZE_TO_RADIUS
		path += (p - prev).length()
		prev = p
		max_gaze = maxf(max_gaze, f.gaze_radius())
		var y: float = f._mat.get_shader_parameter("eye_y")
		y_min = minf(y_min, y)
		y_max = maxf(y_max, y)
	return {"blinks_per_min": (f.blink_count - blinks0) * 60.0 / seconds, "max_gaze": max_gaze,
		"gaze_path_per_min": path * 60.0 / seconds, "breath": (y_max - y_min) * 0.5}


func _reaction_checks(errs: Array) -> void:
	# face.emotion: each reaction is distinct, eases in, holds `ms`, eases back; a new one replaces it smoothly
	var f := _new_face(false, 31)
	_simulate(f, 1.0)
	var base: Dictionary = f._v.duplicate()
	var sig := {}
	for em in f.EMOTIONS:
		f.set_emotion(em, 1500)
		var m := _simulate(f, 1.0)
		var v: Dictionary = f._v
		sig[em] = [snappedf(v.smile, 0.05), snappedf(v.curve, 0.05), snappedf(v.open, 0.05), v.lid_tilt.snapped(Vector2(0.05, 0.05)),
			snappedf(v.mouth_w, 0.005), snappedf(f._fx.y, 0.1), snappedf(f._fx.z, 0.1)]
		if m.breath > 0.01:
			errs.append("%s bounces too much (%.2f%% r)" % [em, m.breath * 100.0])
		_simulate(f, 2.0)          # 1.5 s hold - 1 s elapsed + ease out
		if absf(f._v.smile - base.smile) > 0.05 or absf(f._v.curve - base.curve) > 0.05 or f.emotion != "":
			errs.append("%s does not ease back to the state" % em)
	for a in f.EMOTIONS:
		for b in f.EMOTIONS:
			if a < b and str(sig[a]) == str(sig[b]):
				errs.append("reactions %s and %s look identical" % [a, b])
	if sig.laugh[1] < 0.8 or sig.cry[5] < 0.8 or sig.love[6] < 0.8 or sig.surprised[2] < 1.15 or sig.sad[0] > -0.2:
		errs.append("reaction features missing (laugh crescent / tears / heart / wide eyes / sad frown): %s" % [sig])
	# replacing laugh with cry half-way must not jump (largest per-frame change of the mouth curve)
	f.set_emotion("laugh", 3000)
	_simulate(f, 1.0)
	var jump := [0.0, f._v.smile]
	f.set_emotion("cry", 3000)
	_simulate(f, 1.0, func(x: Control) -> void:
		jump[0] = maxf(jump[0], absf(x._v.smile - jump[1]))
		jump[1] = x._v.smile)
	if jump[0] > 0.15 or f._v.smile > -0.4:
		errs.append("reaction replacement jumps (%.2f per frame) or does not land (%.2f)" % [jump[0], f._v.smile])
	# asleep: no reactions
	f.set_link("offline")
	f.set_emotion("laugh", 3000)
	_simulate(f, 1.0)
	if f._v.curve > 0.05:
		errs.append("reaction shown while asleep")
	f.free()
	# the Core message reaches the face
	ui._on_link(true, "test")
	face.set_link("ready")
	ui._on_message("face.emotion", {"emotion": "surprised", "ms": 800})
	if face.emotion != "surprised" or absf(face._emo_len - 0.8) > 0.001:
		errs.append("face.emotion message not applied")
	ui._on_message("face.emotion", {"emotion": "nonsense"})
	_settle(120)
	if face.emotion != "":
		errs.append("unknown emotion / reaction end not handled")


func _gaze_checks(errs: Array) -> String:
	# face.set_gaze (camera): calm, capped, no jumps; holds, then returns to the idle look; thinking keeps its look
	var f := _new_face(false, 17)
	_simulate(f, 1.0)
	var feed := func(x: Control) -> void:
		if fmod(x.t, 0.2) < 1.0 / 60.0:
			x.set_gaze(1.0, -1.0, 0.9)          # person far to one side and up: the extreme target
	var step := [0.0, f._look * f.GAZE_TO_RADIUS]
	var watch := func(x: Control) -> void:
		feed.call(x)
		var p: Vector2 = x._look * x.GAZE_TO_RADIUS
		step[0] = maxf(step[0], (p - step[1]).length())
		step[1] = p
	_simulate(f, 3.0, watch)
	var off: Vector2 = (f._look - f._v.look) * f.GAZE_TO_RADIUS
	var cap: Vector2 = f.TRACK.max
	if not f.is_tracking() or off.x < cap.x * 0.6 or off.y > -cap.y * 0.6:
		errs.append("gaze does not turn toward the person (offset %s)" % off)
	if absf(off.x) > cap.x + 0.03 or absf(off.y) > cap.y + 0.03:
		errs.append("gaze offset %s exceeds the cap %s" % [off, cap])
	if step[0] > 0.012:
		errs.append("gaze jumps %.3f r per frame while tracking" % step[0])
	_simulate(f, 5.0)                             # no more updates: hold 2 s, then ease back
	var idle_r: float = f.gaze_radius()
	if f.is_tracking() or idle_r >= 0.04:
		errs.append("gaze does not return to idle after the person left (%.3f r)" % idle_r)
	f.set_gaze(1.0, 0.0, 0.9)
	f.set_gaze(0.0, 0.0, 0.0)                     # confidence 0 = stop now
	if f.is_tracking():
		errs.append("confidence 0 does not stop tracking")
	f.set_state("thinking")
	_simulate(f, 2.5, feed)
	if f._look.y > -0.5:
		errs.append("tracking overrides the thinking look")
	f.set_state("idle")
	f.set_link("offline")
	_simulate(f, 1.0, feed)
	if f.is_tracking():
		errs.append("tracking while asleep")
	f.free()
	# messages from the Core (malformed ones are ignored), camera indicator glyph
	face.set_link("ready")
	face.set_state("idle")
	ui._on_message("face.set_gaze", {"x": "left", "y": null, "confidence": "high"})
	ui._on_message("face.set_gaze", {"x": 0.5, "y": 0.2, "confidence": 1})
	if not face.is_tracking():
		errs.append("face.set_gaze message not applied")
	ui._on_message("face.set_gaze", {"x": 0, "y": 0, "confidence": 0})
	ui._on_message("camera.indicator", {"on": true, "flash": true})
	face._apply()
	if not face.camera_on or face._mat.get_shader_parameter("cam_on") < 0.5 or face._mat.get_shader_parameter("cam_flash") < 0.5:
		errs.append("camera indicator not shown")
	ui._on_message("camera.indicator", {"on": "yes"})
	ui._on_message("camera.indicator", {})
	face._apply()
	if face.camera_on or face._mat.get_shader_parameter("cam_on") > 0.0:
		errs.append("camera indicator stays on / accepts a non-bool")
	return "gaze tracking: offset %.1f%% / %.1f%% r (cap %.0f%% / %.0f%%), max step %.2f%% r/frame, idle again %.1f%% r" % [
		off.x * 100.0, off.y * 100.0, cap.x * 100.0, cap.y * 100.0, step[0] * 100.0, idle_r * 100.0]


func _activity_checks(errs: Array) -> void:
	# assistant.activity {kind, text, detail?} -> pill on the lower arc, fades in/out, Hebrew RTL, never when idle
	var pill: Control = face.activity
	face.set_link("ready")
	face.set_state("thinking")
	ui._on_message("assistant.activity", {"kind": "searching", "text": "מחפש ברשת"})
	_settle(30)
	if not pill.is_active() or pill.alpha < 0.9 or not pill._rtl or not pill.visible:
		errs.append("activity pill not shown (alpha %.2f, rtl %s)" % [pill.alpha, pill._rtl])
	ui._on_message("assistant.activity", {"kind": "done", "text": ""})
	_settle(120)
	if pill.is_active() or pill.alpha > 0.0 or pill.visible:
		errs.append("activity pill not cleared by done (alpha %.2f)" % pill.alpha)
	ui._on_message("assistant.activity", {"text": "Loading voice connection..."})      # legacy: panel only
	_settle(10)
	if pill.is_active():
		errs.append("legacy activity (no kind) shown on the face")
	ui._on_message("assistant.activity", {"kind": "thinking", "text": "חושב..."})
	ui._on_message("face.set_state", {"state": "idle"})
	_settle(120)
	if pill.is_active() or pill.alpha > 0.0:
		errs.append("text stays on an idle face")
	ui._on_message("assistant.activity", {"kind": "music", "text": "מנגן", "detail": "Bohemian Rhapsody - Queen"})
	ui._on_message("face.set_state", {"state": "speaking"})
	ui._on_message("face.set_state", {"state": "idle"})
	_settle(30)
	if not pill.is_active() or pill.detail == "":
		errs.append("music pill (title - artist) not kept while playing")
	ui._on_message("assistant.activity", {"kind": "music", "text": ""})
	_settle(120)
	if pill.is_active() or pill.alpha > 0.0:
		errs.append("empty text does not clear the pill")
	# geometry: the pill stays below the mouth and inside the round screen
	face.set_activity("music", "מנגן עכשיו", "שיר ארוך מאוד עם שם ארוך מאוד - אמן עם שם ארוך")
	pill.alpha = 1.0
	var g: Dictionary = pill.layout()
	var r: Rect2 = g.rect
	var s: float = face.size.y
	var top_unit := r.position.y / (s * 0.5) - 1.0
	var corners := [r.position, Vector2(r.end.x, r.position.y), r.end, Vector2(r.position.x, r.end.y)]
	for c in corners:
		if (c / (s * 0.5) - Vector2.ONE).length() > 0.97:
			errs.append("activity pill leaves the round screen")
			break
	if top_unit < 0.5:
		errs.append("activity pill too high (top %.2f, mouth at 0.36..0.5)" % top_unit)
	face.set_activity("done", "")
	_settle(120)


func _appearance_checks(errs: Array) -> String:
	# appearance.set: every presentation draws the SAME face.pose(); a switch never touches the face state;
	# malformed modes are ignored; hidden draws nothing; avatars blink/move exactly as calmly as the face.
	face.set_link("ready")
	face.set_state("happy")
	_settle(60)
	var canvas: Control = face.get_child(0)
	for m in appearance.MODES:
		ui._on_message("appearance.set", {"mode": m})
		await get_tree().process_frame
		if appearance.mode != m:
			errs.append("appearance.set %s not applied" % m)
		if face.state != "happy" or face.link_state != "ready":
			errs.append("switching to %s changed the face state/link" % m)
		var av: Control = appearance.avatar
		var ok := false
		match m:
			"face":
				ok = canvas.visible and face.visible and av == null
			"avatar2d":
				ok = av != null and av.get_script() == appearance.Avatar2D and not canvas.visible and face.visible
			"avatar3d":
				ok = av != null and av.get_script() == appearance.Avatar3D and not canvas.visible and face.visible
			"hidden":
				ok = av == null and not face.visible
		if not ok:
			errs.append("presentation %s not shown correctly" % m)
	ui._on_message("appearance.set", {"mode": "avatar2d"})
	face.set_activity("searching", "מחפש ברשת")
	_settle(30)
	if not face.activity.visible or face.activity.alpha < 0.9:
		errs.append("activity pill hidden in avatar mode")
	face.set_activity("done", "")
	var n0: int = appearance.switches
	for bad in [{"mode": 7}, {"mode": null}, {"mode": "AVATAR3D"}, {"mode": ["face"]}, {}, {"mode": "a".repeat(5000)},
			{"mode": {"x": 1}}, {"mode": 3.5}, {"mode": true}]:
		ui._on_message("appearance.set", bad)
	ui._on_message("appearance.set", {"mode": "avatar2d"})        # same mode again: no rebuild
	if appearance.mode != "avatar2d" or appearance.switches != n0:
		errs.append("malformed / repeated appearance.set changed the presentation")
	# the contract reaches both avatars: expressions, speech level, sleep, colour
	var f := _new_face(false, 7)
	var a2: Control = appearance.Avatar2D.new()
	var a3: Control = appearance.Avatar3D.new()
	var feed_both := func(x: Control) -> void:
		a2.apply_pose(x.pose())
		a3.apply_pose(x.pose())
	_simulate(f, 1.0)
	feed_both.call(f)
	var idle_eye: float = a3.eye_h
	f.set_state("happy")
	_simulate(f, 1.5)
	feed_both.call(f)
	if a2.mouth_curve < 0.8 or a3.eye_h > idle_eye * 0.9:
		errs.append("avatars do not show the happy expression (smile %.2f, eye %.2f vs %.2f)" % [a2.mouth_curve, a3.eye_h, idle_eye])
	f.set_state("speaking")
	_simulate(f, 1.0, func(x: Control) -> void: x.set_speech_level(0.8))
	feed_both.call(f)
	var talk := [a2.mouth_gap, a3.mouth_gap]
	_simulate(f, 0.6)
	feed_both.call(f)
	if talk[0] < 0.03 or talk[1] < 0.03 or a2.mouth_gap > 0.01 or a3.mouth_gap > 0.01:
		errs.append("avatar mouths do not follow the speech level (open %s, closed %.3f / %.3f)" % [talk, a2.mouth_gap, a3.mouth_gap])
	f.set_state("error")
	_simulate(f, 1.0)
	feed_both.call(f)
	if a3.eye_col.r < 0.9:
		errs.append("3D avatar ignores the expression colour")
	f.set_state("idle")
	f.set_link("offline")
	_simulate(f, 2.0)
	feed_both.call(f)
	if a2.eye_open > 0.1 or a3.eye_open > 0.1:
		errs.append("avatars do not sleep when offline")
	f.free()
	# motion budget: identical blink count to the driver, head motion tiny (the eyes do the looking)
	var g := _new_face(false, 11)
	var meas := {"b2": 0, "b3": 0, "open2": true, "open3": true, "head2": 0.0, "head3": 0.0}
	var watch := func(x: Control) -> void:
		feed_both.call(x)
		var o2: bool = a2.eye_open > 0.3
		var o3: bool = a3.eye_open > 0.3
		if meas.open2 and not o2:
			meas.b2 += 1
		if meas.open3 and not o3:
			meas.b3 += 1
		meas.open2 = o2
		meas.open3 = o3
		meas.head2 = maxf(meas.head2, a2.head_off.length())
		meas.head3 = maxf(meas.head3, a3.head_rot.length())
	var mo := _simulate(g, 120.0, watch)
	var driver: float = mo.blinks_per_min * 2.0
	if absf(meas.b2 - driver) > 1.0 or absf(meas.b3 - driver) > 1.0:
		errs.append("avatar blinks (%d / %d) differ from the face driver (%.0f)" % [meas.b2, meas.b3, driver])
	if mo.blinks_per_min < 8.0 or mo.blinks_per_min > 16.0:
		errs.append("avatar blink rate %.1f/min outside 8..16" % mo.blinks_per_min)
	if meas.head2 > 0.02 or meas.head3 > 0.06:
		errs.append("avatar head moves too much (2D %.1f%% r, 3D %.1f deg)" % [meas.head2 * 100.0, rad_to_deg(meas.head3)])
	g.free()
	a2.free()
	a3.free()
	ui._on_message("appearance.set", {"mode": "face"})
	face.set_state("idle")
	return "appearance: modes %s, avatar blinks %d/%d per 2 min (face %.0f), head motion 2D %.2f%% r / 3D %.2f deg" % [
		",".join(appearance.MODES), meas.b2, meas.b3, driver, meas.head2 * 100.0, rad_to_deg(meas.head3)]


func _new_face(lively: bool, seed_value: int, st := "idle") -> Control:
	var f: Control = Face.new()
	f.set_lively(lively)
	f.reseed(seed_value)
	f.set_link("ready")
	f.set_state(st)
	return f


func _motion_checks(errs: Array) -> String:
	# quantitative motion budget (docs: user feedback "moving too much, blinking too much, not natural")
	const MAX_GAZE := 0.04         # idle iris displacement < 4 % of the face radius
	const MAX_BREATH := 0.004      # breathing sway < 0.4 % of the face radius (amplitude)
	const MOUTH_CLOSE := 0.3       # mouth closed (< 0.1) within 300 ms after the audio stops
	var calm := {"blinks_per_min": 0.0, "max_gaze": 0.0, "gaze_path_per_min": 0.0, "breath": 0.0}
	var live := calm.duplicate()
	var seeds := [3, 11, 42]
	for sd in seeds:
		var fc := _new_face(false, sd)
		var mc := _simulate(fc, 120.0)
		var fl := _new_face(true, sd)
		var ml := _simulate(fl, 120.0)
		fc.free()
		fl.free()
		if mc.blinks_per_min < 8.0 or mc.blinks_per_min > 16.0:
			errs.append("idle blink rate %.1f/min outside 8..16 (seed %d)" % [mc.blinks_per_min, sd])
		if mc.max_gaze >= MAX_GAZE:
			errs.append("idle gaze %.1f%% of radius >= %.0f%%" % [mc.max_gaze * 100.0, MAX_GAZE * 100.0])
		if mc.breath >= MAX_BREATH:
			errs.append("breathing %.2f%% of radius >= %.1f%%" % [mc.breath * 100.0, MAX_BREATH * 100.0])
		for k in calm:
			calm[k] = maxf(calm[k], mc[k]) if k in ["max_gaze", "breath"] else calm[k] + mc[k] / seeds.size()
			live[k] = maxf(live[k], ml[k]) if k in ["max_gaze", "breath"] else live[k] + ml[k] / seeds.size()
	# --lively must measurably move more (proves the calm tuning is what makes the difference)
	if live.blinks_per_min < calm.blinks_per_min * 1.15 or live.gaze_path_per_min < calm.gaze_path_per_min * 2.0 \
			or live.max_gaze < calm.max_gaze * 1.3 or live.breath < calm.breath * 1.5:
		errs.append("--lively not measurably busier (calm %s, lively %s)" % [calm, live])
	# thinking: one look up-aside, then hold (no wandering)
	var ft := _new_face(false, 5)
	_simulate(ft, 2.0)
	ft.set_state("thinking")
	_simulate(ft, 2.5)
	var held := _simulate(ft, 10.0)
	if ft._look.y > -0.5 or held.gaze_path_per_min > 0.1:
		errs.append("thinking gaze does not settle and hold (path %.3f/min)" % held.gaze_path_per_min)
	ft.free()
	# speaking: 100 ms level blocks like the Core, then the audio stops; measure the mouth close time
	var close := {}
	for how in ["stale", "zero"]:
		var fs := _new_face(false, 9, "speaking")
		var blk := {"last": -1}
		var feed := func(f: Control) -> void:
			var b := floori(f.t / 0.1 + 1e-6)
			if b == blk.last:
				return
			blk.last = b
			if b < 10:
				f.set_speech_level(0.85 if b % 2 == 0 else 0.55)    # syllable-like 100 ms blocks
			elif how == "zero":
				f.set_speech_level(0.0)
		_simulate(fs, 1.0, feed)
		var top: float = fs._v.mouth_open
		var stop_t := 1.0          # the last voiced 100 ms block (sent at 0.9 s) has finished playing
		var closed_t := -1.0
		while fs.t < stop_t + 1.0:
			feed.call(fs)
			fs._process(1.0 / 60.0)
			if fs.t >= stop_t and fs._v.mouth_open < 0.1:
				closed_t = fs.t - stop_t
				break
		close[how] = closed_t
		if top < 0.3 or top > fs.cfg.mouth_max + 0.01:
			errs.append("mouth range %.2f outside 0.3..%.2f" % [top, fs.cfg.mouth_max])
		if closed_t < 0.0 or closed_t > MOUTH_CLOSE:
			errs.append("mouth closes %.0f ms after audio stops (%s), want <= 300" % [closed_t * 1000.0, how])
		fs.free()
	# listening: a noisy mic level must not make the rim flicker (low-pass) - compare frame-to-frame change
	var flick := {}
	for lv in [false, true]:
		var fm := _new_face(lv, 13, "listening")
		var rng := RandomNumberGenerator.new()
		rng.seed = 1
		var path := [0.0, 0.0, -1]
		var noisy := func(f: Control) -> void:
			var b := floori(f.t / 0.05 + 1e-6)
			if b != path[2]:
				path[2] = b
				f.set_mic_level(rng.randf_range(0.2, 0.8))
			path[0] += absf(f._v.ring - path[1])
			path[1] = f._v.ring
		_simulate(fm, 1.0, noisy)
		path[0] = 0.0
		_simulate(fm, 10.0, noisy)
		flick[lv] = path[0] / 10.0
		fm.free()
	if flick[false] > flick[true] * 0.5:
		errs.append("listening ring flickers (calm %.2f vs lively %.2f /s)" % [flick[false], flick[true]])
	return ("motion calm vs lively: blinks %.1f vs %.1f /min, max gaze %.1f%% vs %.1f%% r, gaze path %.1f%% vs %.1f%% r/min, "
		+ "breath %.2f%% vs %.2f%% r, mouth close %.0f ms (stale) / %.0f ms (zero), ring change %.2f vs %.2f /s") % [
		calm.blinks_per_min, live.blinks_per_min, calm.max_gaze * 100.0, live.max_gaze * 100.0,
		calm.gaze_path_per_min * 100.0, live.gaze_path_per_min * 100.0, calm.breath * 100.0, live.breath * 100.0,
		close.stale * 1000.0, close.zero * 1000.0, flick[false], flick[true]]


func _check() -> void:
	# headless self-test: every state animates without script errors, each expression is distinct,
	# the mouth only moves with real audio, the status dot is truthful, and no text is drawn on the face.
	var errs: Array = []
	var looks := {}
	# The self-test must not depend on a real Core link: on CI (Linux) the refused connection arrives at once and
	# flips the face to its offline/sleeping look mid-test. Stop the link polling; the dot cases below call
	# ui._on_link() themselves.
	ui.link.set_process(false)
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
	_settle(40)      # the rim is low-passed (no flicker), so give it ~0.7 s
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
	# the face has no Label nodes: the shader canvas + the activity pill (TextLine, hidden when idle)
	for n in face.get_children():
		if n is Label or n is RichTextLabel:
			errs.append("text node on the face")
	if face.get_child_count() != 2 or not (face.get_child(0).material is ShaderMaterial) \
			or face.activity == null or face.get_child(1) != face.activity:
		errs.append("face shader canvas / activity pill missing")
	_reaction_checks(errs)
	_activity_checks(errs)
	print(_gaze_checks(errs))
	print(await _appearance_checks(errs))
	print(_privacy_checks(errs))
	# sleeping face never blinks
	var fz := _new_face(false, 21)
	fz.set_link("offline")
	if _simulate(fz, 60.0).blinks_per_min > 0.0:
		errs.append("blinks while asleep")
	fz.free()
	var motion := _motion_checks(errs)
	print(motion)
	await get_tree().create_timer(0.5).timeout
	var link_ok: bool = ui.link.connected or ui.link.detail != ""
	var ok := errs.is_empty() and link_ok
	print("FACE CHECK RESULT: %s (states %d, links %d, link: %s) %s" % ["PASS" if ok else "FAIL", face.STATES.size(),
			face.LINKS.size(), ui.link.detail, "; ".join(errs)])
	get_tree().quit(0 if ok else 1)
