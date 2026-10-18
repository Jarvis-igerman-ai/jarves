extends Control
## Face display app (Mode B, docs/FACE_RUNTIME.md): the round face full-window + assistant controls.
## Same face.gd and Core link as the 3D twin. Args: --kiosk (hide controls; Space still talks),
## --state=<s> / --link=<ready|live|connecting|degraded|offline|error|muted> / --level=<0..1> (preview without
## the Core: the link no longer drives the face), --shot=<png> (screenshot then quit), --check (self-test),
## --low (cheaper face shader), --lively (old busier motion), --seed=<n> (repeatable motion),
## --seq=<png> [--seq-frames=8 --seq-span=12 | --seq-blink] (contact sheet of frames on a simulated clock, then quit).

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
		elif a.begins_with("--seed="):
			face.reseed(int(a.substr(7)))
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
	# the face itself contains no text/labels: only the shader canvas
	for n in face.get_children():
		if n is Label or n is RichTextLabel:
			errs.append("text node on the face")
	if face.get_child_count() != 1 or not (face.get_child(0).material is ShaderMaterial):
		errs.append("face shader canvas missing")
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
