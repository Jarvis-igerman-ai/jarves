extends Node
## Godot side of the Core link (core/src/jarves/link.py): localhost WebSocket + per-install token,
## automatic reconnect with backoff, truthful link state for the UI.

signal link_changed(connected: bool, detail: String)
signal message(type: String, payload: Dictionary)

const PROTOCOL := 1
var url := "ws://127.0.0.1:8765"
var ws := WebSocketPeer.new()
var connected := false
var detail := "not connected"
var _hello_sent := false
var _retry_at := 0.0
var _backoff := 1.0
var _was_open := false
var _active := true


func _ready() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--core-port="):
			url = "ws://127.0.0.1:%s" % a.substr(12)
	_connect()


static func state_dir() -> String:
	var h := OS.get_environment("JARVES_HOME")
	if h != "":
		return h
	if OS.get_name() == "Windows":
		return OS.get_environment("LOCALAPPDATA").path_join("jarves")
	var x := OS.get_environment("XDG_STATE_HOME")
	return (x if x != "" else OS.get_environment("HOME").path_join(".local/state")).path_join("jarves")


func _token() -> String:
	var f := FileAccess.open(state_dir().path_join("link_token"), FileAccess.READ)
	return f.get_as_text().strip_edges() if f else ""


func _set_link(c: bool, d: String) -> void:
	if c != connected or d != detail:
		connected = c
		detail = d
		link_changed.emit(c, d)


func _connect() -> void:
	ws = WebSocketPeer.new()
	_hello_sent = false
	_was_open = false
	var err := ws.connect_to_url(url)
	if err != OK:
		_schedule("cannot open %s (error %d)" % [url, err])


func _schedule(why: String) -> void:
	_set_link(false, "%s - retrying in %.0fs" % [why, _backoff])
	_retry_at = Time.get_ticks_msec() / 1000.0 + _backoff
	_backoff = minf(_backoff * 2.0, 5.0)


func send(type: String, payload: Dictionary = {}) -> bool:
	if not connected:
		return false
	return ws.send_text(JSON.stringify({"type": type, "payload": payload})) == OK


func stop() -> void:
	_active = false
	ws.close(1000, "face closing")


func _process(_d: float) -> void:
	if not _active:
		return
	if _retry_at > 0.0:
		if Time.get_ticks_msec() / 1000.0 >= _retry_at:
			_retry_at = 0.0
			_connect()
		return
	ws.poll()
	match ws.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			_was_open = true
			if not _hello_sent:
				var tok := _token()
				if tok == "":
					ws.close(1000, "no token")
					_schedule("Core token missing in %s (start the Core first)" % state_dir())
					return
				ws.send_text(JSON.stringify({"type": "hello", "payload": {"token": tok, "client": "godot",
						"protocol": PROTOCOL}}))
				_hello_sent = true
			while ws.get_available_packet_count() > 0:
				var parsed = JSON.parse_string(ws.get_packet().get_string_from_utf8())
				if typeof(parsed) != TYPE_DICTIONARY:
					continue
				var t: String = parsed.get("type", "")
				var p: Dictionary = parsed.get("payload", {}) if typeof(parsed.get("payload")) == TYPE_DICTIONARY else {}
				if t == "hello.ok":
					_backoff = 1.0
					_set_link(true, "connected to Core")
				message.emit(t, p)
		WebSocketPeer.STATE_CLOSED:
			var code := ws.get_close_code()
			var why := "Core not running" if not _was_open else "Core disconnected (code %d)" % code
			if code == 4401:
				why = "Core rejected the token (restart the Core and the face)"
			_schedule(why)
