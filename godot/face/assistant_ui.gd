extends PanelContainer
## Assistant controls shared by the 3D twin (main.gd) and the face display (face_app.gd).
## Talk (hold Space / button), software mute, typed questions, captions, Core status and
## confirmation prompts. All behaviour lives in the Python Core; this is only UI + the link.

const CoreLink := preload("res://face/core_link.gd")

var link: Node
var face: Control                 # face.gd instance to drive (set by the owner)
var status_lbl: Label
var caption: Label
var activity: Label              # big, plain-language "what is JARVES doing now"
var level_bar: ProgressBar       # live microphone level while listening
var _level_seen := 0.0
var entry: LineEdit
var talk_btn: Button
var mute_chk: CheckButton
var confirm: ConfirmationDialog
var _confirm_id := ""
var _talk_held := false
var last_status := {}


func _ready() -> void:
	link = CoreLink.new()
	link.name = "CoreLink"
	add_child(link)
	link.link_changed.connect(_on_link)
	link.message.connect(_on_message)
	var box := VBoxContainer.new()
	add_child(box)
	var title := Label.new()
	title.text = "JARVES assistant"
	box.add_child(title)
	status_lbl = Label.new()
	status_lbl.text = "Core: connecting..."
	status_lbl.custom_minimum_size = Vector2(330, 0)
	status_lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(status_lbl)
	activity = Label.new()
	activity.text = "Connecting to JARVES..."
	activity.add_theme_font_size_override("font_size", 22)
	activity.custom_minimum_size = Vector2(330, 0)
	activity.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	activity.text_direction = Control.TEXT_DIRECTION_AUTO
	box.add_child(activity)
	level_bar = ProgressBar.new()
	level_bar.max_value = 1.0
	level_bar.show_percentage = false
	level_bar.custom_minimum_size = Vector2(330, 10)
	level_bar.tooltip_text = "Microphone level"
	box.add_child(level_bar)
	caption = Label.new()
	caption.custom_minimum_size = Vector2(330, 60)
	caption.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	caption.text_direction = Control.TEXT_DIRECTION_AUTO
	box.add_child(caption)
	var row := HBoxContainer.new()
	box.add_child(row)
	talk_btn = Button.new()
	talk_btn.text = "Talk (hold Space)"
	talk_btn.button_down.connect(func(): _talk(true))
	talk_btn.button_up.connect(func(): _talk(false))
	row.add_child(talk_btn)
	mute_chk = CheckButton.new()
	mute_chk.text = "Mic mute (software)"
	mute_chk.toggled.connect(func(on): link.send("mic.set_mute", {"muted": on}))
	row.add_child(mute_chk)
	var row2 := HBoxContainer.new()
	box.add_child(row2)
	entry = LineEdit.new()
	entry.placeholder_text = "Type to JARVES (Hebrew or English)..."
	entry.custom_minimum_size = Vector2(260, 0)
	entry.text_submitted.connect(_send_text)
	row2.add_child(entry)
	var send := Button.new()
	send.text = "Send"
	send.pressed.connect(func(): _send_text(entry.text))
	row2.add_child(send)
	confirm = ConfirmationDialog.new()
	confirm.confirmed.connect(func(): _answer(true))
	confirm.canceled.connect(func(): _answer(false))
	add_child(confirm)
	_set_controls(false)


func _set_controls(on: bool) -> void:
	talk_btn.disabled = not on
	mute_chk.disabled = not on
	entry.editable = on


func _talk(down: bool) -> void:
	if down == _talk_held:
		return
	_talk_held = down
	link.send("talk.down" if down else "talk.up")


func _send_text(t: String) -> void:
	t = t.strip_edges()
	if t == "" or not link.connected:
		return
	link.send("user.text", {"text": t})
	caption.text = "You: " + t
	entry.text = ""


func _answer(ok: bool) -> void:
	if _confirm_id != "":
		link.send("confirm.reply", {"id": _confirm_id, "approved": ok})
		_confirm_id = ""


func _unhandled_input(e: InputEvent) -> void:
	if e is InputEventKey and e.keycode == KEY_SPACE and not e.echo and not entry.has_focus():
		_talk(e.pressed)
		get_viewport().set_input_as_handled()


func _on_link(connected: bool, detail: String) -> void:
	_set_controls(connected)
	if not connected:
		status_lbl.text = "Core: " + detail
		activity.text = "JARVES is not running: " + detail
		if face:
			face.set_state("idle")


func _on_message(t: String, p: Dictionary) -> void:
	match t:
		"face.set_state":
			if face:
				face.set_state(p.get("state", "idle"))
			_show_activity(str(p.get("state", "idle")))
		"assistant.activity":
			activity.text = str(p.get("text", ""))
		"mic.level":
			level_bar.value = float(p.get("level", 0.0))
			_level_seen = Time.get_ticks_msec()
		"assistant.text":
			caption.text = "JARVES: " + str(p.get("text", ""))
		"user.transcript":
			caption.text = "You: " + str(p.get("text", ""))
		"error":
			caption.text = "! %s: %s %s" % [p.get("code", ""), p.get("message", ""), p.get("action", "")]
			activity.text = "Problem: %s. %s" % [p.get("message", ""), p.get("action", "")]
		"confirm.request":
			_confirm_id = str(p.get("id", ""))
			confirm.dialog_text = str(p.get("prompt", "Confirm?"))
			confirm.popup_centered()
		"status":
			last_status = p
			_show_status(p)


const ACTIVITY := {
	"idle": "Ready. Say \"Hey Jarvis\", hold Space, or type.",
	"listening": "Listening... speak now",
	"thinking": "Thinking...",
	"speaking": "Speaking",
	"happy": "Done",
	"error": "Something went wrong (see below)",
}


func _show_activity(state: String) -> void:
	if state == "idle" and last_status.get("voice_mode", "") in ["live", "realtime"] and last_status.get("live_session", false):
		activity.text = "Conversation on: just talk. Tap Talk to end."
	else:
		activity.text = ACTIVITY.get(state, state)
	if state != "listening" and Time.get_ticks_msec() - _level_seen > 500:
		level_bar.value = 0.0


func _show_status(p: Dictionary) -> void:
	var lines := ["Core: %s   voice: %s%s" % [p.get("core", "?"), p.get("voice_mode", "?"),
			"   (mic muted)" if p.get("mic_muted", false) else ""]]
	var comps: Dictionary = p.get("components", {})
	for k in comps:
		var c: Dictionary = comps[k]
		if c.get("status", "") in ["unavailable", "degraded"]:
			lines.append("- %s: %s" % [k, c.get("detail", "")])
	status_lbl.text = "\n".join(lines)
	mute_chk.set_pressed_no_signal(p.get("mic_muted", false))
