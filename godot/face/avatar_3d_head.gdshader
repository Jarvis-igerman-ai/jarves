shader_type spatial;
render_mode diffuse_lambert_wrap, specular_schlick_ggx;
// 3D avatar head (appearance "avatar3d"): ONE smooth sphere whose shader paints a soft matte shell and a dark
// glass visor with glowing eye panels + mouth (SDFs on the front of the sphere, no textures). Stylized on purpose:
// no skin, no realistic eyeballs, no pupils that stare; the expression lives in simple light shapes.
// avatar_3d.gd sets every value from face.gd's pose(). Face coordinates f: x right, y DOWN, unit = head radius.

uniform float t = 0.0;
uniform vec3 accent : source_color = vec3(0.36, 0.78, 1.0);
uniform float bright = 1.0;
uniform float glow = 1.0;
uniform vec2 look = vec2(0.0);
uniform float eye_open = 1.0;
uniform vec2 lid_low = vec2(0.0);
uniform vec2 lid_up = vec2(0.0);
uniform vec2 lid_tilt = vec2(0.0);
uniform float curve = 0.0;
uniform float smile = 0.0;
uniform float mouth_gap = 0.0;
uniform float mouth_w = 0.085;
uniform float mouth_x = 0.0;
uniform float mouth_o = 0.0;
uniform float blush = 0.0;
uniform float cry = 0.0;
uniform float heart = 0.0;
uniform float ring = 0.0;
uniform float asleep = 0.0;
// style knobs (avatar_3d.gd STYLE pushes these)
uniform vec3 shell : source_color = vec3(0.86, 0.88, 0.93);
uniform vec3 visor : source_color = vec3(0.025, 0.035, 0.06);
uniform vec2 visor_r = vec2(0.72, 0.54);
uniform float visor_y = 0.02;
uniform vec2 eye_pos = vec2(0.27, -0.06);
uniform vec2 eye_half = vec2(0.095, 0.15);
uniform float look_shift = 0.05;
uniform float eye_energy = 2.2;
uniform float mouth_y = 0.27;
uniform float mouth_scale = 1.9;
uniform vec3 blush_col : source_color = vec3(1.0, 0.45, 0.62);
uniform vec3 heart_col : source_color = vec3(1.0, 0.38, 0.55);
uniform float relax_low = 0.06;

varying vec3 lp;

void vertex() {
	lp = VERTEX;   // model space; the mesh is a unit-radius sphere scaled by the node
}

float sd_ellipse(vec2 p, vec2 r) { return (length(p / r) - 1.0) * min(r.x, r.y); }
float sd_rbox(vec2 p, vec2 b, float r) {
	vec2 q = abs(p) - b + r;
	return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
float sd_capsule(vec2 p, vec2 a, vec2 b, float r) {
	vec2 pa = p - a, ba = b - a;
	float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
	return length(pa - ba * h) - r;
}
float sd_heart(vec2 p) {
	p.y = -p.y + 0.5;
	p.x = abs(p.x);
	if (p.y + p.x > 1.0) return sqrt(dot(p - vec2(0.25, 0.75), p - vec2(0.25, 0.75))) - sqrt(2.0) / 4.0;
	return sqrt(min(dot(p - vec2(0.0, 1.0), p - vec2(0.0, 1.0)),
			dot(p - 0.5 * max(p.x + p.y, 0.0), p - 0.5 * max(p.x + p.y, 0.0)))) * sign(p.x - p.y);
}
// stroke along y = c + a * (1 - (x/w)^2) for |x| <= w, round caps (smiles, happy arcs, closed lids); exact
// distance to an 8-segment polyline, so the field stays continuous (no seams at the ends of steep arcs)
float sd_bend(vec2 p, float w, float a, float c, float r) {
	float d = 1e3;
	vec2 prev = vec2(-w, c);
	for (int i = 1; i <= 8; i++) {
		float x = -1.0 + 2.0 * float(i) / 8.0;
		vec2 cur = vec2(x * w, c + a * (1.0 - x * x));
		d = min(d, sd_capsule(p, prev, cur, 0.0));
		prev = cur;
	}
	return d - r;
}float cover(float d) { float w = max(fwidth(d), 1e-4); return clamp(0.5 - d / w, 0.0, 1.0); }
float halo(float d, float k) { return exp(-max(d, 0.0) * k); }

void fragment() {
	vec3 n = normalize(lp);
	vec2 f = vec2(n.x, -n.y);
	float front = smoothstep(0.15, 0.3, n.z);
	// visor: a rounded super-ellipse on the front
	vec2 vq = (f - vec2(0.0, visor_y)) / visor_r;
	float vv = pow(pow(abs(vq.x), 2.8) + pow(abs(vq.y), 2.8), 1.0 / 2.8);
	float dv = (vv - 1.0) * min(visor_r.x, visor_r.y);
	float in_visor = cover(dv) * front;
	vec3 em = vec3(0.0);
	// features (only drawn inside the visor)
	float happy = smoothstep(0.35, 0.8, curve) * (1.0 - asleep);
	float hw = smoothstep(0.25, 0.75, heart) * (1.0 - asleep);
	float closed = max(1.0 - smoothstep(0.1, 0.22, eye_open), asleep);
	float lit = bright * glow;
	vec2 sh = clamp(look * look_shift, vec2(-0.06), vec2(0.06));
	float eyes = 0.0;
	float eyes_halo = 0.0;
	float glints = 0.0;
	float pink = 0.0;
	vec3 tears = vec3(0.0);
	for (int i = 0; i < 2; i++) {
		float sx = i == 0 ? -1.0 : 1.0;
		float up = i == 0 ? lid_up.x : lid_up.y;
		float tl = i == 0 ? lid_tilt.x : lid_tilt.y;
		float low = i == 0 ? lid_low.x : lid_low.y;
		vec2 q = f - vec2(sx * eye_pos.x, eye_pos.y) - sh;
		float qo = q.x * sx;
		float hy = eye_half.y * max(eye_open, 0.02);
		vec2 hb = vec2(eye_half.x * (1.0 + 0.06 * max(eye_open - 1.0, 0.0)), hy);
		float d_eye = sd_rbox(q, hb, min(hb.x, hb.y) * 0.98);
		float lid_y = -hy + up * 1.0 * hy - tl * 0.45 * (qo + hb.x) * 0.5 + max(-tl, 0.0) * 0.01;
		float d_lid = lid_y - q.y;
		float relax = relax_low * (1.0 - clamp((eye_open - 1.0) * 6.0, 0.0, 1.0));    // wide eyes: no resting lid
		float d_low = -sd_ellipse(q - vec2(0.0, hy * (2.1 - 2.0 * (low + relax))), vec2(hb.x * 1.7, hy));
		float d_vis = max(max(d_eye, d_lid), d_low);
		float k_open = (1.0 - happy) * (1.0 - hw) * (1.0 - closed);
		float arc = sd_bend(q - vec2(0.0, 0.05), eye_half.x * 1.05, -0.095, 0.0, 0.032);
		float cl = sd_bend(q - vec2(0.0, 0.0), eye_half.x * 1.0, 0.04, 0.0, 0.02);
		float hd = sd_heart((q - vec2(0.0, 0.01)) / 0.25) * 0.25;
		eyes += cover(d_vis) * k_open + cover(arc) * happy * (1.0 - hw) + cover(cl) * closed * 0.55;
		eyes_halo += halo(max(d_eye, d_lid), 26.0) * (1.0 - 0.6 * low) * k_open + halo(arc, 26.0) * happy * (1.0 - hw);
		pink += cover(hd) * hw + 0.5 * halo(hd, 24.0) * hw;
		// a soft glint keeps the panels alive (not a staring pupil)
		vec2 gq = q - vec2(-0.035, -hy * 0.45);
		glints += cover(sd_ellipse(gq, vec2(0.022, 0.03) * min(1.0, eye_open))) * k_open * step(0.0, -d_vis)
				+ cover(length(q - vec2(-0.05, -0.04)) - 0.022) * hw;
		// brows: glowing dashes that appear only when the face is expressive (worried / stern / surprised)
		float by = eye_pos.y - eye_half.y - 0.08 + up * 0.08 - max(eye_open - 1.0, 0.0) * 0.28;
		vec2 bi = vec2(sx * (eye_pos.x - 0.07), by + tl * 0.06) + sh;
		vec2 bo = vec2(sx * (eye_pos.x + 0.07), by - tl * 0.02) + sh;
		float bd = sd_capsule(f, bi, bo, 0.016);
		float brow_a = clamp(abs(tl) * 1.6 + up * 3.0 + max(eye_open - 1.06, 0.0) * 5.0, 0.0, 1.0) * (1.0 - asleep);
		eyes += cover(bd) * brow_a * 0.6;
		// blush glow on the visor
		vec2 bq = (f - vec2(sx * 0.44, 0.16)) / vec2(0.1, 0.055);
		pink += blush * 0.6 * exp(-dot(bq, bq) * 1.5);
		// tears
		if (cry > 0.02) {
			float ph = fract(t * 0.28 + float(i) * 0.5);
			vec2 dp = q - vec2(sx * 0.07, hy * 0.8 + 0.04 + ph * 0.18);
			float drop = min(length(dp) - 0.022, sd_capsule(dp, vec2(0.0), vec2(0.0, -0.04), 0.004 + 0.014 * smoothstep(-0.04, 0.0, dp.y)));
			tears += vec3(0.5, 0.8, 1.0) * (cover(drop) + 0.3 * halo(drop, 30.0)) * cry * (1.0 - 0.7 * ph);
		}
	}
	// mouth
	vec2 q = f - vec2(mouth_x * 0.9, mouth_y);
	float w = mouth_w * mouth_scale;
	float a = smile * 0.07;
	float c0 = -a * 0.5 + cry * 0.005 * sin(t * 34.0 + q.x * 40.0);
	float xn = clamp(q.x / w, -1.0, 1.0);
	float bend = 1.0 - xn * xn;
	float yt = c0 + a * bend;
	float yb = yt + mouth_gap * bend;
	float open_m = smoothstep(0.004, 0.02, mouth_gap) * (1.0 - mouth_o) * (1.0 - asleep);
	float d_in = max(max(abs(q.x) - w, yt - q.y), q.y - yb);
	float top = sd_bend(q, w, a, c0, 0.02);
	float bot = sd_bend(q, w, a + mouth_gap, c0, 0.016);
	vec2 orad = vec2(0.04 + mouth_gap * 0.15, 0.048 + mouth_gap * 0.3);
	float od = sd_ellipse(q - vec2(0.0, 0.015), orad);
	float mouth = cover(top) * (1.0 - mouth_o) * (1.0 - 0.5 * asleep) + cover(bot) * open_m
			+ cover(abs(od) - 0.014) * mouth_o;
	float mouth_fill = cover(d_in) * open_m * 0.22 + cover(od) * mouth_o * 0.22;
	float mouth_halo = halo(top, 30.0) * (1.0 - mouth_o) + halo(abs(od) - 0.014, 30.0) * mouth_o;
	vec3 ink = mix(accent, vec3(1.0), 0.35);
	em += accent * eye_energy * lit * eyes * in_visor;
	em += accent * 0.45 * lit * eyes_halo * in_visor;
	em += vec3(1.0) * 1.6 * lit * glints * in_visor;
	em += heart_col * 1.8 * bright * pink * in_visor;
	em += tears * 1.6 * in_visor;
	em += ink * 1.6 * lit * mouth * in_visor + ink * 0.35 * lit * mouth_halo * in_visor + accent * 0.9 * lit * mouth_fill * in_visor;
	// visor rim: a thin accent seam that brightens while listening
	em += accent * (0.25 + 0.9 * clamp(ring, 0.0, 1.0)) * bright * cover(abs(dv + 0.008) - 0.006) * front;
	// shell: matte, slightly warm, a faint top-to-bottom tint; visor: dark glossy glass
	vec3 sh_col = shell * mix(0.86, 1.0, smoothstep(-0.8, 0.6, n.y));
	vec3 glass = visor * (1.0 + 0.8 * smoothstep(0.2, -0.6, f.y));
	ALBEDO = mix(sh_col, glass, in_visor);
	ROUGHNESS = mix(0.55, 0.32, in_visor);
	SPECULAR = mix(0.35, 0.18, in_visor);
	RIM = mix(0.35, 0.0, in_visor);
	RIM_TINT = 0.6;
	EMISSION = em;
}
