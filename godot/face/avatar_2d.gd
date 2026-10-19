extends Control
## 2D avatar (appearance "avatar2d"): a soft-shaded robot buddy drawn by ONE SDF shader (avatar_2d.gdshader, no
## image assets, so it is cheap on the Pi and easy to restyle). It owns NO motion: every frame it draws face.gd's
## pose() (docs/APPEARANCE.md), so blinks, gaze, breathing, reactions and the speaking mouth are identical to the
## face and as calm. The only "idle life" is what the driver already has (breath swell + antenna bob, slow gaze).
## Swap the look by editing STYLE (colours / proportions, pushed to the shader) or replace this script entirely:
## the contract is only `face` (the behaviour driver) + `apply_pose(pose)`.

const SHADER := preload("res://face/avatar_2d.gdshader")
const STYLE := {
	"head_r": 0.68,                                        # head radius (unit = half the shorter side)
	"shell_lo": Color(0.09, 0.11, 0.18), "shell_hi": Color(0.33, 0.42, 0.58),   # shell shadow / lit colour
	"plate": Color(0.035, 0.05, 0.09), "plate_r": Vector2(0.53, 0.41), "plate_y": 0.05,   # dark glass face plate
	"eye_pos": Vector2(0.235, -0.02), "eye_r": Vector2(0.118, 0.142),   # eye centre (x mirrored) / radii
	"iris_r": 0.088, "pupil_r": 0.045, "sclera": Color(0.95, 0.97, 1.0),
	"blush_col": Color(1.0, 0.48, 0.62), "tongue": Color(0.98, 0.5, 0.58), "heart_col": Color(1.0, 0.36, 0.52),
	"mouth_y": 0.25, "mouth_scale": 2.0,                   # mouth height / width multiplier on pose.mouth_w
	"relax_low": 0.08, "plate_round": 0.36,              # resting lower lid (soft look, no stare) / plate corners
	"look_head": Vector2(0.012, 0.01),     # head parallax per look unit (tiny: the eyes do the looking)
	"look_iris": Vector2(0.06, 0.05),      # iris offset per look unit
	"breath_y": 0.004,
}

var face: Control                          # face.gd (behaviour driver); null in unit tests that feed poses
var p := {}                                # last pose drawn
var mat: ShaderMaterial
# derived values (also read by the self-test)
var eye_open := 1.0                        # drawn eye height / full height
var head_off := Vector2.ZERO               # head offset in unit radii
var iris_off := Vector2.ZERO
var mouth_curve := 0.0                     # + = smile (corners up)
var mouth_gap := 0.0                       # mouth opening in unit radii


func _init() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	mat = ShaderMaterial.new()
	mat.shader = SHADER
	for k in ["head_r", "shell_lo", "shell_hi", "plate", "plate_r", "plate_y", "eye_pos", "eye_r", "iris_r",
			"pupil_r", "sclera", "blush_col", "tongue", "heart_col", "mouth_y", "mouth_scale", "relax_low", "plate_round"]:
		mat.set_shader_parameter(k, STYLE[k])
	var canvas := ColorRect.new()
	canvas.mouse_filter = Control.MOUSE_FILTER_IGNORE
	canvas.set_anchors_preset(Control.PRESET_FULL_RECT)
	canvas.material = mat
	add_child(canvas)


func _process(_delta: float) -> void:
	if face != null and visible:
		apply_pose(face.pose())


func apply_pose(pose: Dictionary) -> void:
	p = pose
	var look: Vector2 = pose.get("look", Vector2.ZERO)
	var t: float = float(pose.get("t", 0.0))
	var bounce: float = -float(pose.get("laugh", 0.0)) * 0.009 * (0.5 - 0.5 * cos(TAU * 2.0 * t))
	head_off = Vector2(look.x * STYLE.look_head.x, look.y * STYLE.look_head.y + STYLE.breath_y * float(pose.get("breath", 0.0)) + bounce)
	iris_off = Vector2(clampf(look.x * STYLE.look_iris.x, -0.05, 0.05), clampf(look.y * STYLE.look_iris.y, -0.06, 0.06))
	eye_open = clampf(float(pose.get("open", 1.0)) * (1.0 - float(pose.get("sleep", 0.0))), 0.0, 1.3)
	mouth_curve = float(pose.get("smile", 0.0))
	mouth_gap = float(pose.get("mouth_open", 0.0)) * 0.13
	var asleep: bool = pose.get("asleep", false)
	var v := {
		"rect_px": size if size.x > 1.0 else Vector2(720, 720), "t": t, "accent": pose.get("col", Color(0.36, 0.78, 1.0)),
		"bright": float(pose.get("bright", 1.0)), "head_off": head_off, "breath": float(pose.get("breath", 0.0)),
		"iris_off": iris_off, "eye_open": eye_open, "lid_low": pose.get("lid_low", Vector2.ZERO),
		"lid_up": pose.get("lid_up", Vector2.ZERO), "lid_tilt": pose.get("lid_tilt", Vector2.ZERO),
		"curve": float(pose.get("curve", 0.0)), "iris_k": float(pose.get("iris", 1.0)), "smile": mouth_curve,
		"mouth_gap": mouth_gap, "mouth_w": float(pose.get("mouth_w", 0.085)), "mouth_x": float(pose.get("mouth_x", 0.0)),
		"mouth_o": clampf(float(pose.get("mouth_o", 0.0)), 0.0, 1.0), "blush": float(pose.get("blush", 0.0)),
		"cry": float(pose.get("cry", 0.0)), "heart": float(pose.get("heart", 0.0)), "ring": float(pose.get("ring", 0.0)),
		"think": float(pose.get("spin", 0.0)), "asleep": 1.0 if asleep else 0.0,
	}
	for k in v:
		mat.set_shader_parameter(k, v[k])
