extends Control
## "What is JARVES doing" pill on the lower arc of the round face (below the mouth, never over the eyes).
## A vector icon + a few words (+ optional detail line, e.g. song - artist). Soft fade in/out, Hebrew RTL.
## Driven by face.gd: set_activity(kind, text, detail) from the Core `assistant.activity {kind, text, detail?}`.
## Text is drawn with TextLine (BiDi-shaped, ellipsis when too long) - no Label nodes on the face.
## Sizes are fractions of the face size S, chosen for a ~13 cm round screen read at 1-3 m:
## the icon (large, simple) carries the meaning at 3 m, the words are readable from ~1-1.5 m.

const KINDS := ["thinking", "searching", "reading", "music", "time", "memory", "tool", "connecting"]
## preview samples (--activity=<kind> without text); the Core sends its own words
const SAMPLES := {
	"thinking": ["חושב...", ""], "searching": ["מחפש ברשת", ""], "reading": ["קורא את הדף", ""],
	"music": ["מנגן עכשיו", "Bohemian Rhapsody · Queen"], "time": ["בודק את השעה", ""],
	"memory": ["נזכר...", ""], "tool": ["מפעיל כלי", ""], "connecting": ["מתחבר...", ""],
}
const TEXT_SIZE := 0.058        # main words, x S  (42 px at 720)
const DETAIL_SIZE := 0.037      # detail line, x S (27 px at 720)
const ICON := 0.066             # icon box, x S
const CENTER_Y := 0.62          # pill centre, unit-disc y (mouth sits at 0.36, laughing grin reaches ~0.5)
const BG := Color(0.025, 0.05, 0.095, 0.8)
const FG := Color(0.9, 0.96, 1.0)
const FADE_IN := 0.16           # s (time constant)
const FADE_OUT := 0.3
const STALE_S := 45.0           # safety: a non-music activity the Core forgot to clear fades out after this

var kind := ""
var text := ""
var detail := ""
var accent := Color(0.36, 0.78, 1.0)
var alpha := 0.0                # current opacity 0..1 (tests)
var _shown_at := -100.0
var _t := 0.0
var _font: SystemFont
var _line := TextLine.new()
var _dline := TextLine.new()
var _rtl := false
var _draw_kind := ""         # kept while fading out after a clear


func _init() -> void:
	name = "ActivityPill"
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_anchors_preset(Control.PRESET_FULL_RECT)
	_font = SystemFont.new()
	_font.font_names = PackedStringArray(["Segoe UI", "Noto Sans Hebrew", "Noto Sans", "Rubik", "Heebo",
		"DejaVu Sans", "Arial", "sans-serif"])
	_font.font_weight = 600
	_font.antialiasing = TextServer.FONT_ANTIALIASING_GRAY
	visible = false


func set_activity(k: String, words: String, more := "") -> void:
	if k == "done" or words.strip_edges() == "" or not (k in KINDS):
		kind = ""                  # clear: fades out with the last content still drawn
		return
	if alpha > 0.05 and (k != kind or words != text):
		alpha = minf(alpha, 0.35)  # content swap while visible: brief soft dip, then back up
	kind = k
	_draw_kind = k
	text = words.strip_edges()
	detail = more.strip_edges()
	_shown_at = _t
	_rtl = _is_rtl(text + detail)
	_shape()


func is_active() -> bool:
	return kind != ""


func step(delta: float, face_t: float, asleep: bool) -> void:
	_t = face_t
	if kind != "" and kind != "music" and _t - _shown_at > STALE_S:
		kind = ""
	var target := 1.0 if (kind != "" and not asleep) else 0.0
	var tau := FADE_IN if target > alpha else FADE_OUT
	alpha = lerpf(alpha, target, 1.0 - exp(-delta / tau))
	if target == 0.0 and alpha < 0.01:
		alpha = 0.0
	visible = alpha > 0.0
	if visible:
		queue_redraw()


static func _is_rtl(s: String) -> bool:
	for ch in s:
		var c := ch.unicode_at(0)
		if (c >= 0x0590 and c <= 0x05FF) or (c >= 0x0600 and c <= 0x06FF) or (c >= 0xFB1D and c <= 0xFB4F):
			return true
	return false


func _shape() -> void:
	var s := size.y
	if s < 32.0:
		return             # not laid out yet; NOTIFICATION_RESIZED shapes it
	for pair in [[_line, text, TEXT_SIZE], [_dline, detail, DETAIL_SIZE]]:
		var tl: TextLine = pair[0]
		tl.clear()
		# each line is shaped in its own direction (a Latin song title inside a Hebrew pill stays LTR)
		tl.direction = TextServer.DIRECTION_RTL if _is_rtl(pair[1]) else TextServer.DIRECTION_LTR
		tl.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		tl.add_string(pair[1], _font, int(round(s * pair[2])))
	_line.alignment = HORIZONTAL_ALIGNMENT_RIGHT if _rtl else HORIZONTAL_ALIGNMENT_LEFT
	_dline.alignment = _line.alignment


func _notification(what: int) -> void:
	if what == NOTIFICATION_RESIZED and text != "":
		_shape()


func layout() -> Dictionary:
	# pill geometry for the current content (also used by the self-test): rect, icon centre, text origin
	var s := size.y
	var two := detail != ""
	var ic := s * ICON
	var pad_x := s * 0.032
	var pad_y := s * 0.016
	var gap := s * 0.022
	var lh := s * TEXT_SIZE * 1.2
	var dh := s * DETAIL_SIZE * 1.2 if two else 0.0
	var h := maxf(lh + dh + pad_y * 2.0, ic + pad_y * 2.0)
	var cy := s * 0.5 * (1.0 + CENTER_Y + (0.04 if two else 0.0)) + (1.0 - alpha) * s * 0.012
	# widest pill that stays inside the round screen at its lower edge
	var yb := (cy + h * 0.5) / (s * 0.5) - 1.0
	var max_w := 2.0 * sqrt(maxf(1.0 - yb * yb, 0.05)) * s * 0.5 * 0.9
	var max_text := max_w - pad_x * 2.0 - ic - gap
	_line.width = -1
	_dline.width = -1
	var tw := minf(maxf(_line.get_size().x, _dline.get_size().x if two else 0.0), max_text)
	_line.width = tw
	_dline.width = tw
	var w := pad_x * 2.0 + ic + gap + tw
	var r := Rect2(s * 0.5 - w * 0.5, cy - h * 0.5, w, h)
	# reading order: icon first -> on the right for Hebrew, on the left otherwise
	var icon_c := Vector2(r.end.x - pad_x - ic * 0.5 if _rtl else r.position.x + pad_x + ic * 0.5, cy)
	var tx := r.position.x + pad_x if _rtl else r.position.x + pad_x + ic + gap
	return {"rect": r, "icon": icon_c, "ic": ic, "tx": tx, "ty": cy - (lh + dh) * 0.5, "lh": lh, "dh": dh}


func _draw() -> void:
	if text == "" or alpha <= 0.0:
		return
	var s := size.y
	var g := layout()
	var r: Rect2 = g.rect
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(BG, BG.a * alpha)
	sb.set_corner_radius_all(int(r.size.y * 0.5))
	sb.set_border_width_all(maxi(1, int(round(s * 0.0028))))
	sb.border_color = Color(accent, 0.38 * alpha)
	sb.shadow_color = Color(accent, 0.16 * alpha)
	sb.shadow_size = int(s * 0.022)
	sb.anti_aliasing = true
	draw_style_box(sb, r)
	var tx: float = g.tx
	var ty: float = g.ty
	var lh: float = g.lh
	var dh: float = g.dh
	_line.draw(get_canvas_item(), Vector2(tx, ty + (lh - _line.get_size().y) * 0.5), Color(FG, alpha))
	if detail != "":
		_dline.draw(get_canvas_item(), Vector2(tx, ty + lh + (dh - _dline.get_size().y) * 0.5), Color(FG, 0.66 * alpha))
	_icon(_draw_kind, g.icon, g.ic, Color(accent.lightened(0.15), alpha))

func _icon(k: String, c: Vector2, ic: float, col: Color) -> void:
	var lw := maxf(2.0, ic * 0.085)
	var u := ic * 0.5        # half box
	match k:
		"thinking":
			# three dots, a slow gentle sequential glow (1.6 s cycle) - the only moving part of the pill
			for i in 3:
				var a := 0.4 + 0.6 * (0.5 + 0.5 * sin(TAU * (_t / 1.6 - i * 0.18)))
				draw_circle(c + Vector2((i - 1) * u * 0.62, 0.0), u * 0.17, Color(col, col.a * a))
		"searching":
			var mc := c + Vector2(-u * 0.14, -u * 0.14)
			draw_arc(mc, u * 0.5, 0.0, TAU, 40, col, lw, true)
			draw_line(mc + Vector2(u, u) * 0.5 * 0.7071, c + Vector2(u, u) * 0.78, col, lw * 1.25, true)
		"reading":
			var top := c + Vector2(0.0, -u * 0.52)
			var bot := c + Vector2(0.0, u * 0.62)
			for sd in [-1.0, 1.0]:
				draw_polyline(PackedVector2Array([top, c + Vector2(sd * u * 0.88, -u * 0.68),
					c + Vector2(sd * u * 0.88, u * 0.46), bot]), col, lw, true)
			draw_line(top, bot, col, lw * 0.8, true)
		"music":
			var h1 := c + Vector2(-u * 0.5, u * 0.5)
			var h2 := c + Vector2(u * 0.52, u * 0.32)
			for hd in [h1, h2]:
				_ellipse(hd, Vector2(u * 0.27, u * 0.2), col)
			var s1 := h1 + Vector2(u * 0.24, -u * 0.04)
			var s2 := h2 + Vector2(u * 0.24, -u * 0.04)
			var e1 := s1 + Vector2(0.0, -u * 1.05)
			var e2 := s2 + Vector2(0.0, -u * 1.05)
			draw_line(s1, e1 - Vector2(0, lw * 0.3), col, lw * 0.8, true)
			draw_line(s2, e2 - Vector2(0, lw * 0.3), col, lw * 0.8, true)
			draw_colored_polygon(PackedVector2Array([e1, e2, e2 + Vector2(0, u * 0.24), e1 + Vector2(0, u * 0.24)]), col)
		"time":
			draw_arc(c, u * 0.82, 0.0, TAU, 48, col, lw, true)
			var now := Time.get_time_dict_from_system()   # the hands show the real time
			var mn: float = now.minute
			var hr: float = fmod(float(now.hour), 12.0) + mn / 60.0
			var ah := hr / 12.0 * TAU - PI * 0.5
			var am := mn / 60.0 * TAU - PI * 0.5
			draw_line(c, c + Vector2(cos(ah), sin(ah)) * u * 0.4, col, lw, true)
			draw_line(c, c + Vector2(cos(am), sin(am)) * u * 0.6, col, lw * 0.8, true)
			draw_circle(c, lw * 0.7, col)
		"memory":
			# a soft four-point sparkle ("recalling")
			var pts := PackedVector2Array()
			for i in 8:
				var ang := i * PI / 4.0 - PI * 0.5
				var rr := u * (0.9 if i % 2 == 0 else 0.26)
				pts.append(c + Vector2(cos(ang), sin(ang)) * rr)
			draw_colored_polygon(pts, col)
			draw_circle(c + Vector2(u * 0.62, -u * 0.62), u * 0.12, col)
		"connecting":
			# a signal: three arcs that light up one after another (1.2 s cycle) above a dot
			draw_circle(c + Vector2(0.0, u * 0.62), lw * 0.9, col)
			for i in 3:
				var rr := u * (0.42 + 0.34 * i)
				var a := 0.3 + 0.7 * clampf(sin(TAU * (_t / 1.2 - i * 0.16)) * 0.5 + 0.5, 0.0, 1.0)
				draw_arc(c + Vector2(0.0, u * 0.62), rr, -PI * 0.78, -PI * 0.22, 20, Color(col, col.a * a), lw, true)
		"tool":
			draw_arc(c, u * 0.38, 0.0, TAU, 32, col, lw, true)
			for i in 8:
				var ang := i * TAU / 8.0
				var d := Vector2(cos(ang), sin(ang))
				draw_line(c + d * u * 0.55, c + d * u * 0.82, col, lw * 1.25, true)


func _ellipse(c: Vector2, r: Vector2, col: Color) -> void:
	var pts := PackedVector2Array()
	for i in 20:
		var a := i * TAU / 20.0
		var p := Vector2(cos(a) * r.x, sin(a) * r.y).rotated(-0.35)
		pts.append(c + p)
	draw_colored_polygon(pts, col)
