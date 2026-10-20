extends Control
## Lightweight boot screen: no room or GLB is loaded before actual Core/voice readiness.
const CoreLink := preload("res://face/core_link.gd")
var link: Node
var progress: ProgressBar
var detail: Label
var _poll := 0.0
var _opening := false


func _ready() -> void:
	var bg := ColorRect.new()
	bg.color = Color(0.035, 0.05, 0.085)
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(bg)
	var center := CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(center)
	var box := VBoxContainer.new()
	box.custom_minimum_size = Vector2(520, 0)
	box.add_theme_constant_override("separation", 18)
	center.add_child(box)
	var title := Label.new()
	title.text = "JARVES"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.add_theme_font_size_override("font_size", 38)
	box.add_child(title)
	detail = Label.new()
	detail.text = "Loading JARVES..."
	detail.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	detail.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(detail)
	progress = ProgressBar.new()
	progress.custom_minimum_size = Vector2(520, 22)
	progress.value = 5
	box.add_child(progress)
	link = CoreLink.new()
	link.client_name = "cli"
	link.message.connect(_message)
	add_child(link)
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--loading-shot="):
			await get_tree().process_frame
			await get_tree().process_frame
			get_viewport().get_texture().get_image().save_png(arg.substr(15))
			get_tree().quit()


func _process(delta: float) -> void:
	_poll -= delta
	if _poll <= 0 and link and link.connected and not _opening:
		_poll = 0.25
		link.send("status.request")


func _message(type: String, payload: Dictionary) -> void:
	if type != "status" or _opening:
		return
	var startup: Dictionary = payload.get("startup", {})
	progress.value = startup.get("progress", 5)
	detail.text = startup.get("detail", "Waiting for JARVES readiness...")
	if startup.get("ready", false):
		if "--loading-check" in OS.get_cmdline_user_args():
			print("LOADING CHECK RESULT: PASS (read-only status reached readiness)")
			get_tree().quit()
			return
		_opening = true
		detail.text = "Voice ready. Loading the room..."
		progress.value = 95
		await get_tree().process_frame
		link.stop()
		get_tree().change_scene_to_file("res://main.tscn")
