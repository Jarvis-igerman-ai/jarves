extends Node3D
## JARVES room simulation (user request): a ~4 x 4 m furnished study/living room around the digital twin.
## Everything is procedural (primitive meshes + StandardMaterial3D + generated textures): no downloaded assets.
## Coordinates follow main.gd: metres, Y up, device front = +Z, device stands at the origin. The desk is placed so
## its top surface is exactly at `desk_top` (= the device's lowest point), the floor 0.75 m below.
## Pi 5 budget: ~150 extra draw calls, a few k triangles, ONE shadowed light at a time (sun by day, pendant at
## night), no reflection probe; SSAO/glow off with set_quality(false) (--low); SDFGI/SSIL only with set_gi (--gi).

const ROOM_W := 4.0          # x extent (m)
const ROOM_D := 4.0          # z extent (m)
const ROOM_H := 2.6          # floor to ceiling
const DESK_H := 0.75         # desk top above the floor
const X0 := -1.6             # interior faces of the walls (device at x = 0, desk against the back wall)
const X1 := X0 + ROOM_W
const Z0 := -0.55
const Z1 := Z0 + ROOM_D
const T := 0.12              # wall thickness
const WIN := {"z0": -0.1, "z1": 1.4, "sill": 0.85, "top": 2.25}  # window in the left wall (x = X0), heights above floor

var desk_top := 0.0
var F := -0.75               # floor y
var C := 1.85                # ceiling y
var evening := false
var hq := true
var sun: DirectionalLight3D
var day_fill: OmniLight3D
var pendant: OmniLight3D
var floor_lamp: OmniLight3D
var bulb_mat: StandardMaterial3D
var shade_mat: StandardMaterial3D
var env_day: Environment
var env_eve: Environment
var bodies: Array[StaticBody3D] = []
var desk_top_mesh: MeshInstance3D
var _rng := RandomNumberGenerator.new()


func build(top_y: float) -> void:
	name = "Room"
	desk_top = top_y
	F = top_y - DESK_H
	C = F + ROOM_H
	_rng.seed = 7
	_build_shell()
	_build_window()
	_build_outside()
	_build_desk()
	_build_props()
	_build_lights()
	_build_envs()
	for b in bodies:
		b.collision_layer = 2
		b.collision_mask = 0
	# the single shadowed omni (pendant) gets a full atlas quadrant instead of a quarter of one
	var vp := get_viewport()
	if vp:
		vp.positional_shadow_atlas_size = 4096
		vp.positional_shadow_atlas_quad_0 = Viewport.SHADOW_ATLAS_QUADRANT_SUBDIV_1


# ---------------------------------------------------------------- materials / textures
func _mat(col: Color, rough := 0.8, metal := 0.0) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = col
	m.roughness = rough
	m.metallic = metal
	return m


func _tri(m: StandardMaterial3D, tile_m: float) -> StandardMaterial3D:  # world-space triplanar mapping, tile size in m
	m.uv1_triplanar = true
	m.uv1_world_triplanar = true
	m.uv1_scale = Vector3.ONE / tile_m
	return m


func _noise_tex(freq: float, col_a: Color, col_b: Color, seed: int, normal := false, bump := 2.0) -> NoiseTexture2D:
	var n := FastNoiseLite.new()
	n.seed = seed
	n.frequency = freq
	n.fractal_octaves = 4
	var t := NoiseTexture2D.new()
	t.width = 256
	t.height = 256
	t.seamless = true
	t.noise = n
	if normal:
		t.as_normal_map = true
		t.bump_strength = bump
	else:
		var g := Gradient.new()
		g.set_color(0, col_a)
		g.set_color(1, col_b)
		t.color_ramp = g
	return t


# Seamless plank / grain texture. Grain noise is sampled on a cylinder so it wraps in x; rows end on a gap so it
# wraps in y. Returns [albedo, normal].
func _wood(px: int, rows: int, base: Color, gaps: bool, seed: int) -> Array:
	var n := FastNoiseLite.new()
	n.seed = seed
	n.noise_type = FastNoiseLite.TYPE_SIMPLEX
	n.frequency = 0.05
	n.fractal_octaves = 3
	var alb := Image.create(px, px, false, Image.FORMAT_RGB8)
	var hgt := Image.create(px, px, false, Image.FORMAT_RGB8)
	var row_h := px / rows
	var tones := []
	var offs := []
	for r in rows:
		tones.append([_rng.randf_range(0.82, 1.12), _rng.randf_range(0.82, 1.12)])
		offs.append(_rng.randi_range(0, px - 1))
	var R := px * 0.12 / TAU
	for y in px:
		var r := y / row_h
		var gy := y % row_h
		for x in px:
			var xx: int = (x + offs[r]) % px
			var plank: int = (x + offs[r]) / (px / 2) % 2
			var a := float(x) / px * TAU
			var g := n.get_noise_3d(cos(a) * R, sin(a) * R, y * 1.6 + plank * 91.0 + r * 37.0)
			var fib := n.get_noise_3d(cos(a) * R * 8.0, sin(a) * R * 8.0, y * 9.0)
			var k: float = tones[r][plank] * (0.9 + 0.13 * g + 0.04 * fib)
			var h := 1.0
			if gaps and (gy < 1 or xx % (px / 2) < 1):
				k *= 0.42
				h = 0.0
			alb.set_pixel(x, y, Color(base.r * k, base.g * k, base.b * k))
			var hv := h * (0.92 + 0.08 * g)
			hgt.set_pixel(x, y, Color(hv, hv, hv))
	hgt.bump_map_to_normal_map(3.0 if gaps else 1.0)
	alb.generate_mipmaps()
	hgt.generate_mipmaps()
	return [ImageTexture.create_from_image(alb), ImageTexture.create_from_image(hgt)]


func _rug_tex() -> ImageTexture:
	var px := 256
	var n := FastNoiseLite.new()
	n.seed = 3
	n.frequency = 0.35
	var img := Image.create(px, px, false, Image.FORMAT_RGB8)
	var field := Color(0.56, 0.5, 0.43)
	var band := Color(0.32, 0.26, 0.22)
	var edge := Color(0.78, 0.74, 0.67)
	for y in px:
		for x in px:
			var d := mini(mini(x, px - 1 - x), mini(y, px - 1 - y))
			var c := field
			if d < 10:
				c = band
			elif d < 16:
				c = edge
			elif d < 22:
				c = band
			elif (x + y) % 32 < 2 or absi(x - y) % 32 < 2:
				c = field.lerp(band, 0.35)  # faint diamond lattice
			var k := 0.9 + 0.1 * n.get_noise_2d(x, y)
			img.set_pixel(x, y, Color(c.r * k, c.g * k, c.b * k))
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


func _art_tex() -> ImageTexture:  # abstract landscape print for the wall frame
	var w := 192
	var h := 128
	var img := Image.create(w, h, false, Image.FORMAT_RGB8)
	var n := FastNoiseLite.new()
	n.seed = 11
	n.frequency = 0.02
	for y in h:
		for x in w:
			var sky := Color(0.86, 0.8, 0.7).lerp(Color(0.62, 0.72, 0.8), 1.0 - float(y) / h)
			var hill1 := h * 0.55 + 14.0 * n.get_noise_1d(x)
			var hill2 := h * 0.72 + 9.0 * n.get_noise_1d(x + 400.0)
			var c := sky
			if y > hill2:
				c = Color(0.27, 0.33, 0.3)
			elif y > hill1:
				c = Color(0.48, 0.55, 0.5)
			if Vector2(x - w * 0.7, y - h * 0.32).length() < 11.0:
				c = Color(0.93, 0.72, 0.5)
			img.set_pixel(x, y, c)
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


# ---------------------------------------------------------------- mesh helpers
func _add(mesh: Mesh, pos: Vector3, mat: Material, nm := "", parent: Node3D = null, solid := false, rot := Vector3.ZERO) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = mat
	mi.position = pos
	mi.rotation = rot
	if nm != "":
		mi.name = nm
	(parent if parent else self).add_child(mi)
	if solid:
		mi.create_trimesh_collision()
		bodies.append(mi.get_child(mi.get_child_count() - 1))
	return mi


func _box(size: Vector3, pos: Vector3, mat: Material, nm := "", parent: Node3D = null, solid := false, rot := Vector3.ZERO) -> MeshInstance3D:
	var b := BoxMesh.new()
	b.size = size
	return _add(b, pos, mat, nm, parent, solid, rot)


func _cyl(rt: float, rb: float, h: float, pos: Vector3, mat: Material, nm := "", parent: Node3D = null, segs := 24, solid := false) -> MeshInstance3D:
	var c := CylinderMesh.new()
	c.top_radius = rt
	c.bottom_radius = rb
	c.height = h
	c.radial_segments = segs
	c.rings = 1
	return _add(c, pos, mat, nm, parent, solid)


func _box_span(lo: Vector3, hi: Vector3, mat: Material, nm := "", solid := true) -> MeshInstance3D:
	return _box(hi - lo, (lo + hi) * 0.5, mat, nm, null, solid)


# ---------------------------------------------------------------- room shell
func _build_shell() -> void:
	var fl: Array = _wood(512, 16, Color(0.62, 0.45, 0.3), true, 21)  # 2.4 m tile: 15 cm x 1.2 m oak planks
	var floor_m := _tri(_mat(Color(1, 1, 1), 0.55), 2.4)
	floor_m.albedo_texture = fl[0]
	floor_m.normal_enabled = true
	floor_m.normal_texture = fl[1]
	floor_m.normal_scale = 0.6
	floor_m.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	_box_span(Vector3(X0 - T, F - 0.04, Z0 - T), Vector3(X1 + T, F, Z1 + T), floor_m, "Floor")
	var wall_m := _tri(_mat(Color(0.9, 0.87, 0.82), 0.92), 1.5)
	wall_m.normal_enabled = true
	wall_m.normal_texture = _noise_tex(0.06, Color.BLACK, Color.WHITE, 5, true, 1.2)
	wall_m.normal_scale = 0.25
	var ceil_m := _mat(Color(0.93, 0.93, 0.92), 0.95)
	_box_span(Vector3(X0 - T, C, Z0 - T), Vector3(X1 + T, C + 0.1, Z1 + T), ceil_m, "Ceiling")
	_box_span(Vector3(X0 - T, F, Z0 - T), Vector3(X1 + T, C, Z0), wall_m, "Wall_Back")
	_box_span(Vector3(X0 - T, F, Z1), Vector3(X1 + T, C, Z1 + T), wall_m, "Wall_Front")
	_box_span(Vector3(X1, F, Z0), Vector3(X1 + T, C, Z1), wall_m, "Wall_Right")
	# left wall with the window opening
	var sill := F + WIN.sill
	var top := F + WIN.top
	_box_span(Vector3(X0 - T, F, Z0), Vector3(X0, sill, Z1), wall_m, "Wall_Left_Low")
	_box_span(Vector3(X0 - T, top, Z0), Vector3(X0, C, Z1), wall_m, "Wall_Left_High")
	_box_span(Vector3(X0 - T, sill, Z0), Vector3(X0, top, WIN.z0), wall_m, "Wall_Left_A")
	_box_span(Vector3(X0 - T, sill, WIN.z1), Vector3(X0, top, Z1), wall_m, "Wall_Left_B")
	# skirting boards + ceiling cove
	var trim := _mat(Color(0.95, 0.95, 0.93), 0.5)
	var sk := 0.09
	_box_span(Vector3(X0, F, Z0), Vector3(X1, F + sk, Z0 + 0.015), trim, "", false)
	_box_span(Vector3(X0, F, Z1 - 0.015), Vector3(X1, F + sk, Z1), trim, "", false)
	_box_span(Vector3(X0, F, Z0), Vector3(X0 + 0.015, F + sk, Z1), trim, "", false)
	_box_span(Vector3(X1 - 0.015, F, Z0), Vector3(X1, F + sk, Z1), trim, "", false)
	# door in the right wall
	var door_m := _mat(Color(0.93, 0.92, 0.89), 0.45)
	var dz := 2.55
	_box(Vector3(0.03, 2.04, 0.84), Vector3(X1 - 0.015, F + 1.02, dz), door_m, "Door", null, true)
	_box(Vector3(0.05, 2.1, 0.06), Vector3(X1 - 0.02, F + 1.05, dz - 0.45), trim)
	_box(Vector3(0.05, 2.1, 0.06), Vector3(X1 - 0.02, F + 1.05, dz + 0.45), trim)
	_box(Vector3(0.05, 0.06, 0.96), Vector3(X1 - 0.02, F + 2.1, dz), trim)
	var steel := _mat(Color(0.75, 0.75, 0.74), 0.3, 1.0)
	_box(Vector3(0.06, 0.02, 0.13), Vector3(X1 - 0.06, F + 1.0, dz - 0.3), steel)


func _build_window() -> void:
	var frame := _mat(Color(0.96, 0.96, 0.95), 0.4)
	var sill := F + WIN.sill
	var top := F + WIN.top
	var zc: float = (WIN.z0 + WIN.z1) * 0.5
	var wz: float = WIN.z1 - WIN.z0
	var wy: float = top - sill
	var fx := X0 - T * 0.5
	var f := 0.06
	_box(Vector3(0.08, f, wz), Vector3(fx, sill + f * 0.5, zc), frame, "Window_Frame")
	_box(Vector3(0.08, f, wz), Vector3(fx, top - f * 0.5, zc), frame)
	_box(Vector3(0.08, wy, f), Vector3(fx, (sill + top) * 0.5, WIN.z0 + f * 0.5), frame)
	_box(Vector3(0.08, wy, f), Vector3(fx, (sill + top) * 0.5, WIN.z1 - f * 0.5), frame)
	_box(Vector3(0.07, wy, 0.05), Vector3(fx, (sill + top) * 0.5, zc), frame)          # mullion
	_box(Vector3(0.07, 0.04, wz), Vector3(fx, sill + wy * 0.72, zc), frame)            # transom
	# interior sill board
	_box(Vector3(0.2, 0.03, wz + 0.12), Vector3(X0 + 0.04, sill - 0.015, zc), _mat(Color(0.95, 0.95, 0.94), 0.35), "Window_Sill", null, true)
	var glass := _mat(Color(0.85, 0.92, 0.95, 0.07), 0.04)
	glass.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	glass.metallic_specular = 0.9
	var g := _box(Vector3(0.008, wy - 0.1, wz - 0.1), Vector3(fx, (sill + top) * 0.5, zc), glass, "Window_Glass")
	g.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	# light linen curtains either side
	var cur := _mat(Color(0.9, 0.87, 0.8), 0.95)
	cur.normal_enabled = true
	cur.normal_texture = _noise_tex(0.02, Color.BLACK, Color.WHITE, 9, true, 6.0)
	cur.uv1_scale = Vector3(6, 1, 1)
	cur.cull_mode = BaseMaterial3D.CULL_DISABLED
	var ch := top + 0.2 - (F + 0.02)
	for s in [-1, 1]:
		var cz: float = (WIN.z0 - 0.2) if s < 0 else (WIN.z1 + 0.2)
		_add(_curtain_mesh(0.42, ch, 0.035), Vector3(X0 + 0.11, F + 0.02, cz), cur, "Curtain")
	_cyl(0.012, 0.012, wz + 1.1, Vector3(X0 + 0.1, top + 0.2, zc), steel_mat(), "", null, 8).rotation = Vector3(PI / 2, 0, 0)


func _curtain_mesh(w: float, h: float, amp: float) -> ArrayMesh:
	# pleated drape: a vertical sheet in the YZ plane with sine folds along z (x = fold depth), slightly wider at the hem
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var nz := 40
	var ny := 4
	for j in ny:
		for i in nz:
			var quad := []
			for c in [[i, j], [i + 1, j], [i + 1, j + 1], [i, j + 1]]:
				var u: float = float(c[0]) / nz
				var v: float = float(c[1]) / ny
				var spread := 1.0 + 0.15 * (1.0 - v)
				var z := (u - 0.5) * w * spread
				var x := amp * sin(u * TAU * 4.5 + v * 0.6) * (0.8 + 0.2 * (1.0 - v))
				quad.append([Vector3(x, v * h, z), Vector2(u, 1.0 - v)])
			for k in [0, 1, 2, 0, 2, 3]:
				st.set_uv(quad[k][1])
				st.add_vertex(quad[k][0])
	st.index()
	st.generate_normals()
	st.generate_tangents()
	return st.commit()


func _leaf_mesh(w: float, h: float, bend: float) -> ArrayMesh:
	# sword-shaped leaf (snake plant): lens outline, folded V cross-section, bends outwards (+z) towards the tip
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var n := 8
	var rows := []
	for j in n + 1:
		var v := float(j) / n
		var half := w * 0.5 * sin(PI * clampf(v * 0.85 + 0.12, 0.0, 1.0)) * (1.0 - v * 0.25)
		var z := bend * v * v
		rows.append([Vector3(-half, v * h, z + half * 0.25), Vector3(0, v * h, z), Vector3(half, v * h, z + half * 0.25)])
	for j in n:
		for k in 2:
			var a: Vector3 = rows[j][k]
			var b: Vector3 = rows[j][k + 1]
			var c: Vector3 = rows[j + 1][k + 1]
			var d: Vector3 = rows[j + 1][k]
			for p in [a, b, c, a, c, d]:
				st.add_vertex(p)
	st.index()
	st.generate_normals()
	return st.commit()


func steel_mat() -> StandardMaterial3D:
	return _mat(Color(0.3, 0.3, 0.32), 0.35, 1.0)


func _build_outside() -> void:
	var out := Node3D.new()
	out.name = "Outside"
	add_child(out)
	var grass := _mat(Color(1, 1, 1), 0.95)
	grass.albedo_texture = _noise_tex(0.05, Color(0.36, 0.48, 0.22), Color(0.5, 0.6, 0.3), 4)
	grass.uv1_scale = Vector3(30, 30, 1)
	var p := PlaneMesh.new()
	p.size = Vector2(80, 80)
	_add(p, Vector3(-40 + X0, F - 0.4, 1.0), grass, "Lawn", out)
	var bark := _mat(Color(0.3, 0.24, 0.19), 0.9)
	var leaf := _mat(Color(1, 1, 1), 0.85)
	leaf.albedo_texture = _noise_tex(0.08, Color(0.16, 0.26, 0.12), Color(0.3, 0.42, 0.2), 6)
	var trees := [Vector3(-9, 0, -3.0), Vector3(-12, 0, 2.5), Vector3(-7.5, 0, 4.5), Vector3(-15, 0, -1.0), Vector3(-10, 0, 7.5)]
	for tp in trees:
		var h := _rng.randf_range(3.5, 5.5)
		_cyl(0.12, 0.18, h, Vector3(tp.x, F - 0.4 + h * 0.5, tp.z), bark, "", out, 8)
		for k in 3:
			var s := SphereMesh.new()
			var r := _rng.randf_range(1.2, 1.9)
			s.radius = r
			s.height = r * 1.7
			s.radial_segments = 16
			s.rings = 8
			_add(s, Vector3(tp.x + _rng.randf_range(-0.6, 0.6), F - 0.4 + h + _rng.randf_range(-0.3, 0.8), tp.z + _rng.randf_range(-0.6, 0.6)), leaf, "", out)
	var hedge := _mat(Color(1, 1, 1), 0.9)
	hedge.albedo_texture = leaf.albedo_texture
	_box(Vector3(0.9, 1.3, 16.0), Vector3(-5.5, F - 0.4 + 0.65, 1.0), hedge, "Hedge", out)
	for c in out.get_children():
		if c is GeometryInstance3D:
			c.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF  # keep the sun's shadow budget for the room


# ---------------------------------------------------------------- furniture
func _build_desk() -> void:
	var oak: Array = _wood(256, 4, Color(0.74, 0.56, 0.38), false, 33)
	var top_m := _tri(_mat(Color(1, 1, 1), 0.42), 0.9)
	top_m.albedo_texture = oak[0]
	top_m.normal_enabled = true
	top_m.normal_texture = oak[1]
	top_m.normal_scale = 0.3
	top_m.clearcoat_enabled = true
	top_m.clearcoat = 0.25
	top_m.clearcoat_roughness = 0.4
	var dx0 := -0.55
	var dx1 := 0.75
	var dz0 := Z0 + 0.02
	var dz1 := 0.22
	desk_top_mesh = _box_span(Vector3(dx0, desk_top - 0.03, dz0), Vector3(dx1, desk_top, dz1), top_m, "Desk_Top")
	var leg := _mat(Color(0.12, 0.12, 0.13), 0.45, 0.6)
	for lx in [dx0 + 0.05, dx1 - 0.05]:
		for lz in [dz0 + 0.05, dz1 - 0.05]:
			_box_span(Vector3(lx - 0.02, F, lz - 0.02), Vector3(lx + 0.02, desk_top - 0.03, lz + 0.02), leg, "Desk_Leg")
	_box_span(Vector3(dx0 + 0.05, desk_top - 0.09, dz0 + 0.04), Vector3(dx1 - 0.05, desk_top - 0.03, dz0 + 0.06), leg, "Desk_Rail", false)
	# desk items: closed laptop, mug, notebook + pen, small speaker-free zone around the device
	var alu := _mat(Color(0.42, 0.43, 0.45), 0.4, 0.85)
	var lap := _box(Vector3(0.31, 0.016, 0.215), Vector3(0.45, desk_top + 0.008, -0.2), alu, "Laptop", null, true)
	lap.rotation.y = 0.12
	var mug_m := _mat(Color(0.85, 0.82, 0.76), 0.25)
	_cyl(0.042, 0.04, 0.095, Vector3(-0.38, desk_top + 0.0475, -0.05), mug_m, "Mug", null, 40, true)
	var coffee := _mat(Color(0.12, 0.07, 0.04), 0.15)
	_cyl(0.037, 0.037, 0.002, Vector3(-0.38, desk_top + 0.085, -0.05), coffee, "", null, 20)
	var handle := TorusMesh.new()
	handle.inner_radius = 0.022
	handle.outer_radius = 0.03
	handle.rings = 24
	handle.ring_segments = 10
	_add(handle, Vector3(-0.425, desk_top + 0.05, -0.05), mug_m, "", null, false, Vector3(PI / 2, 0, 0))
	var note := _box(Vector3(0.15, 0.012, 0.21), Vector3(0.36, desk_top + 0.006, 0.08), _mat(Color(0.18, 0.24, 0.32), 0.7), "Notebook")
	note.rotation.y = -0.25
	var pen := _cyl(0.004, 0.004, 0.14, Vector3(0.45, desk_top + 0.004, 0.09), _mat(Color(0.1, 0.1, 0.1), 0.3, 0.4), "", null, 8)
	pen.rotation = Vector3(PI / 2, 0.4, 0)
	# wall art above the desk
	var frame := _mat(Color(0.12, 0.1, 0.09), 0.5)
	_box(Vector3(0.72, 0.5, 0.025), Vector3(0.1, desk_top + 0.75, Z0 + 0.0125), frame, "Picture")
	var art := _mat(Color(1, 1, 1), 0.8)
	art.albedo_texture = _art_tex()
	var q := QuadMesh.new()
	q.size = Vector2(0.6, 0.4)
	_add(q, Vector3(0.1, desk_top + 0.75, Z0 + 0.0255), art)


func _build_props() -> void:
	# --- wooden chair, pulled out and turned towards the room
	var chair := Node3D.new()
	chair.name = "Chair"
	add_child(chair)
	chair.position = Vector3(0.95, F, 0.62)
	chair.rotation.y = -0.6
	var ash := _mat(Color(0.58, 0.44, 0.31), 0.55)
	var seat_m := _mat(Color(0.28, 0.3, 0.33), 0.9)
	for lx in [-0.2, 0.2]:
		_box(Vector3(0.035, 0.45, 0.035), Vector3(lx, 0.225, 0.19), ash, "", chair)
		_box(Vector3(0.035, 0.88, 0.035), Vector3(lx, 0.44, -0.19), ash, "", chair, false, Vector3(-0.06, 0, 0))
	_box(Vector3(0.44, 0.04, 0.42), Vector3(0, 0.46, 0), ash, "Chair_Seat", chair, true)
	_box(Vector3(0.4, 0.035, 0.38), Vector3(0, 0.495, 0.01), seat_m, "", chair)
	_box(Vector3(0.42, 0.12, 0.025), Vector3(0, 0.8, -0.215), ash, "", chair, false, Vector3(-0.06, 0, 0))
	_box(Vector3(0.42, 0.03, 0.025), Vector3(0, 0.62, -0.205), ash, "", chair, false, Vector3(-0.06, 0, 0))
	# --- bookshelf against the back wall, right of the desk
	var shelf := Node3D.new()
	shelf.name = "Bookshelf"
	add_child(shelf)
	shelf.position = Vector3(1.55, F, Z0 + 0.16)
	var wood := _mat(Color(0.93, 0.92, 0.9), 0.5)
	var sw := 0.84
	var sh := 1.86
	_box(Vector3(0.02, sh, 0.3), Vector3(-sw * 0.5, sh * 0.5, 0), wood, "Shelf_Side", shelf, true)
	_box(Vector3(0.02, sh, 0.3), Vector3(sw * 0.5, sh * 0.5, 0), wood, "", shelf, true)
	_box(Vector3(sw, sh, 0.01), Vector3(0, sh * 0.5, -0.145), wood, "", shelf)
	var levels := [0.06, 0.48, 0.9, 1.32, sh - 0.01]
	var palette := [Color(0.55, 0.16, 0.13), Color(0.16, 0.25, 0.38), Color(0.82, 0.78, 0.68), Color(0.2, 0.32, 0.25),
		Color(0.85, 0.6, 0.25), Color(0.12, 0.12, 0.14), Color(0.6, 0.55, 0.5), Color(0.38, 0.22, 0.3)]
	for i in levels.size():
		_box(Vector3(sw - 0.02, 0.022, 0.29), Vector3(0, levels[i], 0), wood, "", shelf)
		if i == levels.size() - 1:
			break
		var x := -sw * 0.5 + 0.03
		var limit := sw * 0.5 - (0.03 if i % 2 == 0 else 0.3)
		while x < limit:
			var bw := _rng.randf_range(0.022, 0.045)
			var bh := _rng.randf_range(0.2, 0.3)
			var bd := _rng.randf_range(0.17, 0.23)
			var bm := _mat(palette[_rng.randi() % palette.size()].darkened(_rng.randf_range(0.0, 0.25)), 0.75)
			_box(Vector3(bw, bh, bd), Vector3(x + bw * 0.5, levels[i] + 0.011 + bh * 0.5, 0.02), bm, "", shelf)
			x += bw + 0.003
		if i % 2 == 1:  # a lying stack + a small ceramic vase on alternate shelves
			var vm := _mat(Color(0.7, 0.75, 0.72), 0.3)
			_cyl(0.035, 0.05, 0.16, Vector3(sw * 0.5 - 0.15, levels[i] + 0.091, 0.0), vm, "", shelf, 16)
	# --- snake plant in the window corner
	var plant := Node3D.new()
	plant.name = "Plant"
	add_child(plant)
	plant.position = Vector3(X0 + 0.32, F, Z0 + 0.3)
	var pot := _mat(Color(0.78, 0.76, 0.72), 0.6)
	_cyl(0.16, 0.13, 0.36, Vector3(0, 0.18, 0), pot, "Plant_Pot", plant, 24, true)
	_cyl(0.15, 0.15, 0.01, Vector3(0, 0.34, 0), _mat(Color(0.18, 0.13, 0.09), 1.0), "", plant, 16)
	var leaf_mats := []
	for i in 4:
		var lm := _mat(Color(0.09, 0.2, 0.1).lerp(Color(0.3, 0.42, 0.18), i / 3.0), 0.5)
		lm.cull_mode = BaseMaterial3D.CULL_DISABLED
		leaf_mats.append(lm)
	for i in 26:
		var h := _rng.randf_range(0.35, 0.9)
		var a := _rng.randf() * TAU
		var r := _rng.randf_range(0.0, 0.1)
		var tilt := _rng.randf_range(0.04, 0.3) * (1.0 - h)   # short outer leaves lean out more
		var leafm := _add(_leaf_mesh(_rng.randf_range(0.045, 0.075), h, _rng.randf_range(0.04, 0.12)), Vector3(cos(a) * r, 0.33, sin(a) * r),
			leaf_mats[_rng.randi() % 4], "", plant)
		leafm.rotation = Vector3(0, -a + PI / 2, 0)
		leafm.rotate_object_local(Vector3.RIGHT, tilt + 0.05)
	# --- rug in the middle of the room
	var rug := _mat(Color(1, 1, 1), 1.0)
	rug.albedo_texture = _rug_tex()
	_box(Vector3(2.0, 0.012, 1.4), Vector3(0.35, F + 0.006, 1.75), rug, "Rug", null, true)
	# --- sofa against the front wall (the "sofa view" sits here)
	var sofa := Node3D.new()
	sofa.name = "Sofa"
	add_child(sofa)
	sofa.position = Vector3(0.3, F, Z1 - 0.48)
	var fab := _mat(Color(1, 1, 1), 1.0)
	fab.albedo_texture = _noise_tex(0.4, Color(0.36, 0.4, 0.44), Color(0.44, 0.48, 0.52), 8)
	fab.uv1_scale = Vector3(4, 4, 1)
	_box(Vector3(2.0, 0.28, 0.9), Vector3(0, 0.2, 0), fab, "Sofa_Base", sofa, true)
	_box(Vector3(2.0, 0.5, 0.2), Vector3(0, 0.55, 0.35), fab, "", sofa, true)
	for sx in [-0.9, 0.9]:
		_box(Vector3(0.2, 0.32, 0.9), Vector3(sx, 0.5, 0), fab, "", sofa)
	for sx in [-0.4, 0.4]:
		_box(Vector3(0.78, 0.13, 0.66), Vector3(sx, 0.405, -0.08), fab, "", sofa)
	var feet := _mat(Color(0.2, 0.15, 0.1), 0.6)
	for sx in [-0.95, 0.95]:
		for sz in [-0.4, 0.4]:
			_box(Vector3(0.04, 0.06, 0.04), Vector3(sx, 0.03, sz), feet, "", sofa)
	# --- floor lamp next to the sofa
	var lamp := Node3D.new()
	lamp.name = "Floor_Lamp"
	add_child(lamp)
	lamp.position = Vector3(X0 + 0.45, F, Z1 - 0.45)
	var blk := _mat(Color(0.1, 0.1, 0.1), 0.4, 0.7)
	_cyl(0.14, 0.15, 0.025, Vector3(0, 0.0125, 0), blk, "", lamp)
	_cyl(0.012, 0.012, 1.45, Vector3(0, 0.74, 0), blk, "", lamp, 8)
	shade_mat = _mat(Color(0.95, 0.9, 0.8), 0.95)
	shade_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	shade_mat.emission_enabled = true
	shade_mat.emission = Color(1.0, 0.78, 0.5)
	shade_mat.emission_energy_multiplier = 0.0
	var shade := CylinderMesh.new()
	shade.top_radius = 0.16
	shade.bottom_radius = 0.21
	shade.height = 0.28
	shade.cap_top = false
	shade.cap_bottom = false
	shade.radial_segments = 32
	var sm := _add(shade, Vector3(0, 1.5, 0), shade_mat, "Lamp_Shade", lamp)
	sm.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	# --- ceiling pendant above the room centre
	var pend := Node3D.new()
	pend.name = "Pendant"
	add_child(pend)
	pend.position = Vector3(0.35, C, 1.2)
	_cyl(0.004, 0.004, 0.62, Vector3(0, -0.31, 0), blk, "", pend, 6)
	_cyl(0.05, 0.05, 0.02, Vector3(0, -0.01, 0), blk, "", pend, 16)
	var pm := _mat(Color(0.16, 0.18, 0.17), 0.5, 0.3)
	pm.cull_mode = BaseMaterial3D.CULL_DISABLED
	var cone := CylinderMesh.new()
	cone.top_radius = 0.05
	cone.bottom_radius = 0.19
	cone.height = 0.2
	cone.cap_bottom = false
	cone.radial_segments = 32
	_add(cone, Vector3(0, -0.72, 0), pm, "Pendant_Shade", pend)
	bulb_mat = _mat(Color(1, 0.95, 0.85), 0.2)
	bulb_mat.emission_enabled = true
	bulb_mat.emission = Color(1.0, 0.8, 0.55)
	var bulb := SphereMesh.new()
	bulb.radius = 0.04
	bulb.height = 0.08
	var bm2 := _add(bulb, Vector3(0, -0.77, 0), bulb_mat, "Pendant_Bulb", pend)
	bm2.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF


# ---------------------------------------------------------------- lights / environment
func _build_lights() -> void:
	sun = DirectionalLight3D.new()
	sun.name = "Sun"
	var el := deg_to_rad(33.0)
	var dir := Vector3(cos(el) * 0.92, -sin(el), -cos(el) * 0.39).normalized()   # in through the left window
	sun.basis = Basis.looking_at(dir, Vector3.UP)
	sun.light_color = Color(1.0, 0.95, 0.86)
	sun.light_energy = 2.6
	sun.light_angular_distance = 0.6
	sun.shadow_enabled = true
	sun.shadow_bias = 0.03
	sun.shadow_blur = 1.5
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_2_SPLITS
	sun.directional_shadow_max_distance = 9.0
	add_child(sun)
	day_fill = OmniLight3D.new()   # skylight bouncing in through the window (no shadow)
	day_fill.name = "Window_Fill"
	day_fill.position = Vector3(X0 + 0.9, F + 1.6, 0.7)
	day_fill.light_color = Color(1.0, 0.97, 0.92)
	day_fill.light_energy = 0.9
	day_fill.omni_range = 5.0
	day_fill.omni_attenuation = 0.8
	day_fill.light_specular = 0.2
	add_child(day_fill)
	pendant = OmniLight3D.new()    # evening key light (the one shadowed light at night)
	pendant.name = "Pendant_Light"
	pendant.position = Vector3(0.35, C - 0.8, 1.2)
	pendant.light_color = Color(1.0, 0.82, 0.62)
	pendant.light_energy = 2.2
	pendant.omni_range = 6.0
	pendant.omni_attenuation = 1.0
	pendant.shadow_enabled = true
	pendant.shadow_bias = 0.08
	pendant.shadow_normal_bias = 2.5
	pendant.shadow_blur = 2.0
	pendant.omni_shadow_mode = OmniLight3D.SHADOW_CUBE
	pendant.light_size = 0.05
	add_child(pendant)
	floor_lamp = OmniLight3D.new()
	floor_lamp.name = "Floor_Lamp_Light"
	floor_lamp.position = Vector3(X0 + 0.45, F + 1.5, Z1 - 0.45)
	floor_lamp.light_color = Color(1.0, 0.78, 0.55)
	floor_lamp.light_energy = 1.4
	floor_lamp.omni_range = 4.5
	floor_lamp.omni_attenuation = 1.2
	add_child(floor_lamp)
	# No ReflectionProbe: measured ~570 MB extra VRAM on the dev iGPU for little visible gain; sky reflections are
	# kept weak instead (reflected_light_source = sky, low ambient sky share).


func _env(evening_: bool) -> Environment:
	var sky_m := ProceduralSkyMaterial.new()
	if evening_:
		sky_m.sky_top_color = Color(0.03, 0.05, 0.12)
		sky_m.sky_horizon_color = Color(0.22, 0.16, 0.2)
		sky_m.ground_horizon_color = Color(0.08, 0.07, 0.08)
		sky_m.ground_bottom_color = Color(0.02, 0.02, 0.03)
		sky_m.sky_energy_multiplier = 0.5
	else:
		sky_m.sky_top_color = Color(0.3, 0.5, 0.82)
		sky_m.sky_horizon_color = Color(0.72, 0.8, 0.9)
		sky_m.ground_horizon_color = Color(0.55, 0.6, 0.55)
		sky_m.ground_bottom_color = Color(0.2, 0.24, 0.18)
		sky_m.sky_energy_multiplier = 1.2
	var sky := Sky.new()
	sky.sky_material = sky_m
	sky.radiance_size = Sky.RADIANCE_SIZE_128
	var e := Environment.new()
	e.background_mode = Environment.BG_SKY
	e.sky = sky
	e.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	e.ambient_light_sky_contribution = 0.55   # indoors most ambient is warm wall/floor bounce, not raw blue sky
	e.ambient_light_color = Color(0.3, 0.26, 0.24) if evening_ else Color(0.95, 0.88, 0.8)
	e.ambient_light_energy = 0.07 if evening_ else 0.45
	e.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	e.tonemap_mode = Environment.TONE_MAPPER_AGX
	e.tonemap_exposure = 1.25 if evening_ else 1.1
	e.ssao_enabled = hq
	e.ssao_radius = 0.8
	e.ssao_intensity = 1.6
	e.ssao_power = 1.4
	e.ssil_enabled = false
	e.ssil_intensity = 0.8
	e.ssil_radius = 2.0
	e.glow_enabled = hq
	e.glow_intensity = 0.35
	e.glow_bloom = 0.02
	e.glow_hdr_threshold = 1.2
	e.adjustment_enabled = true
	e.adjustment_saturation = 1.05
	return e


func _build_envs() -> void:
	env_day = _env(false)
	env_eve = _env(true)
	set_evening(false)


func current_env() -> Environment:
	return env_eve if evening else env_day


func set_evening(on: bool) -> void:
	evening = on
	sun.visible = not on
	day_fill.visible = not on
	pendant.visible = on
	floor_lamp.visible = on
	bulb_mat.emission_energy_multiplier = 6.0 if on else 0.0
	shade_mat.emission_energy_multiplier = 0.9 if on else 0.0


func set_quality(high: bool) -> void:   # false = Pi profile (--low): no SSAO/glow, cheaper shadows
	hq = high
	for e in [env_day, env_eve]:
		if e:
			e.ssao_enabled = high
			e.glow_enabled = high
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_2_SPLITS if high else DirectionalLight3D.SHADOW_ORTHOGONAL
	sun.shadow_blur = 1.5 if high else 0.5
	pendant.omni_shadow_mode = OmniLight3D.SHADOW_CUBE if high else OmniLight3D.SHADOW_DUAL_PARABOLOID
	pendant.light_size = 0.05 if high else 0.0
	if RenderingServer.get_current_rendering_method() == "gl_compatibility":
		sun.light_energy = 1.5   # the GL renderer has no HDR headroom: the sun patch would clip to white


func set_gi(on: bool) -> void:  # optional SDFGI + SSIL bounce light (--gi, desktop only; off by default and on the Pi)
	for e in [env_day, env_eve]:
		e.sdfgi_enabled = on
		e.ssil_enabled = on
		e.sdfgi_use_occlusion = true
		e.sdfgi_cascades = 2
		e.sdfgi_min_cell_size = 0.1
	if on:
		for e in [env_day, env_eve]:
			e.ambient_light_energy *= 0.35


func set_active(on: bool) -> void:
	visible = on
	for b in bodies:
		b.collision_layer = 2 if on else 0


# Point clamp so the orbit camera never leaves the room.
func clamp_inside(p: Vector3) -> Vector3:
	return Vector3(clampf(p.x, X0 + 0.08, X1 - 0.08), clampf(p.y, F + 0.15, C - 0.1), clampf(p.z, Z0 + 0.08, Z1 - 0.08))
