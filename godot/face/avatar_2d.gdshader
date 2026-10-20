shader_type canvas_item;
render_mode unshaded;
// 2D avatar (appearance "avatar2d"): a soft-shaded robot buddy in one SDF pass (no textures), cheap on a Pi 5.
// avatar_2d.gd sets every value from face.gd's pose(); this only draws. Units: p in avatar radii (half the
// shorter side), y down, centre = (0, 0). Layers back to front: halo, antenna, ears, head shell, face plate,
// blush, eyes (+ happy arcs / hearts / tears), brows, mouth. Output is straight alpha over the black screen.

uniform vec2 rect_px = vec2(720.0);
uniform float t = 0.0;
uniform vec3 accent : source_color = vec3(0.36, 0.78, 1.0);
uniform float bright = 1.0;
uniform vec2 head_off = vec2(0.0);
uniform float breath = 0.0;
uniform vec2 iris_off = vec2(0.0);
uniform float eye_open = 1.0;
uniform vec2 lid_low = vec2(0.0);
uniform vec2 lid_up = vec2(0.0);
uniform vec2 lid_tilt = vec2(0.0);
uniform float curve = 0.0;
uniform float iris_k = 1.0;
uniform float smile = 0.0;
uniform float mouth_gap = 0.0;
uniform float mouth_w = 0.085;
uniform float mouth_x = 0.0;
uniform float mouth_o = 0.0;
uniform float blush = 0.0;
uniform float cry = 0.0;
uniform float heart = 0.0;
uniform float ring = 0.0;
uniform float think = 0.0;
uniform float asleep = 0.0;
// style knobs (avatar_2d.gd STYLE pushes these)
uniform float head_r = 0.68;
uniform vec3 shell_lo : source_color = vec3(0.09, 0.11, 0.18);
uniform vec3 shell_hi : source_color = vec3(0.33, 0.42, 0.58);
uniform vec3 plate : source_color = vec3(0.035, 0.05, 0.09);
uniform vec2 plate_r = vec2(0.53, 0.41);
uniform float plate_y = 0.05;
uniform vec2 eye_pos = vec2(0.235, -0.02);
uniform vec2 eye_r = vec2(0.118, 0.142);
uniform float iris_r = 0.078;
uniform float pupil_r = 0.036;
uniform vec3 sclera : source_color = vec3(0.95, 0.97, 1.0);
uniform vec3 blush_col : source_color = vec3(1.0, 0.48, 0.62);
uniform vec3 tongue : source_color = vec3(0.98, 0.5, 0.58);
uniform vec3 heart_col : source_color = vec3(1.0, 0.36, 0.52);
uniform float mouth_y = 0.25;
uniform float mouth_scale = 2.0;
uniform float relax_low = 0.08;          // resting lower lid: a soft, friendly look instead of a stare
uniform float plate_round = 0.36;

// anti-aliased coverage of a signed distance (1.4 px wide edge)
float fill(float d) { return clamp(0.5 - d * 0.5 * min(rect_px.x, rect_px.y) / 1.4, 0.0, 1.0); }
float soft(float d, float k) { return 1.0 - smoothstep(-k, k, d); }
float sd_ellipse(vec2 p, vec2 r) { return (length(p / r) - 1.0) * min(r.x, r.y); }
float sd_capsule(vec2 p, vec2 a, vec2 b, float r) {
	vec2 pa = p - a, ba = b - a;
	float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
	return length(pa - ba * h) - r;
}
float sd_rbox(vec2 p, vec2 b, float r) {
	vec2 q = abs(p) - b + r;
	return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
float sd_heart(vec2 p) {   // iq's heart, point up -> flip so it reads right with y down; unit ~1 tall
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
}vec4 over(vec4 base, vec3 c, float a) { return base * (1.0 - a) + vec4(c, 1.0) * a; }

void fragment() {
	float u = 0.5 * min(rect_px.x, rect_px.y);
	vec2 p = (UV * rect_px - 0.5 * rect_px) / u - head_off;
	p.y *= 1.0 - 0.004 * breath;           // breathing: a tiny vertical swell, nothing else moves on its own
	vec4 o = vec4(0.0);
	float hr = head_r;
	float listen = clamp(ring, 0.0, 1.0);
	float r = length(p);
	// soft accent halo behind the head (stronger while listening)
	o = over(o, accent, (0.05 + 0.18 * listen) * exp(-max(r - hr, 0.0) * 14.0) * step(hr - 0.01, r) * bright);
	// antenna: stalk + glowing bulb that bobs with the breath
	vec2 tip = vec2(0.0, -hr - 0.115 - 0.004 * breath);
	float stalk = sd_capsule(p, vec2(0.0, -hr + 0.02), tip, 0.014);
	o = over(o, mix(shell_lo, shell_hi, 0.55), fill(stalk));
	float bulb_glow = clamp(0.35 + 0.6 * listen + think * (0.45 + 0.35 * sin(t * 1.3)), 0.0, 1.2);
	o = over(o, accent, 0.5 * bulb_glow * exp(-max(length(p - tip) - 0.045, 0.0) * 22.0) * bright);
	float bd = length(p - tip) - 0.042;
	vec3 bulb = mix(accent * 0.75, mix(accent, vec3(1.0), 0.65), smoothstep(0.04, -0.04, (p - tip).y + (p - tip).x));
	o = over(o, bulb * (0.6 + 0.4 * bulb_glow) * bright, fill(bd));
	// ear pods with a glow strip
	for (int i = 0; i < 2; i++) {
		float sx = i == 0 ? -1.0 : 1.0;
		vec2 q = p - vec2(sx * (hr + 0.01), -0.02);
		float ed = sd_rbox(q, vec2(0.06, 0.12), 0.045);
		vec3 ec = mix(shell_lo, shell_hi, 0.35 + 0.4 * smoothstep(0.1, -0.1, q.y));
		o = over(o, ec, fill(ed));
		float sd = sd_rbox(q - vec2(sx * 0.03, 0.0), vec2(0.014, 0.075), 0.014);
		o = over(o, accent * bright, fill(sd) * (0.35 + 0.65 * listen));
	}
	// head shell: soft sphere shading (wrapped light from the upper left) + accent rim light
	float hd = r - hr;
	vec3 n = vec3(p / hr, sqrt(max(1.0 - dot(p, p) / (hr * hr), 0.0)));
	float lam = dot(n, normalize(vec3(-0.45, -0.65, 0.62))) * 0.5 + 0.5;
	vec3 shell = mix(shell_lo, shell_hi, pow(lam, 1.6));
	shell += accent * pow(1.0 - n.z, 3.0) * (0.22 + 0.35 * listen);
	shell += vec3(1.0) * 0.1 * soft(sd_ellipse(p - vec2(-0.25, -0.43), vec2(0.17, 0.09)), 0.07);
	o = over(o, shell * bright, fill(hd));
	// face plate: dark glass with a soft bevel, inner vignette and a curved reflection
	vec2 pp = p - vec2(0.0, plate_y);
	float pd = sd_rbox(pp, plate_r, plate_round);
	o = over(o, mix(shell_lo, shell_hi, 0.75) * bright, fill(pd - 0.012) * 0.6);
	vec3 glass = mix(plate * 1.9, plate, smoothstep(-0.35, 0.25, pp.y));
	glass = mix(glass, plate * 0.55, smoothstep(-0.14, 0.0, pd));
	o = over(o, glass, fill(pd));
	float refl = soft(sd_bend(pp - vec2(-0.08, 0.0), 0.3, -0.05, -plate_r.y + 0.07, 0.014), 0.02) * smoothstep(0.25, -0.1, pp.x);
	o = over(o, vec3(1.0), 0.07 * refl * fill(pd));
	// blush
	for (int i = 0; i < 2; i++) {
		float sx = i == 0 ? -1.0 : 1.0;
		vec2 q = (p - vec2(sx * 0.35, 0.15)) / vec2(0.085, 0.05);
		o = over(o, blush_col, 0.7 * blush * exp(-dot(q, q) * 1.4));
	}
	// eyes
	float happy = smoothstep(0.35, 0.8, curve) * (1.0 - asleep);
	float hw = smoothstep(0.25, 0.75, heart);
	float closed = max(1.0 - smoothstep(0.1, 0.22, eye_open), asleep);
	for (int i = 0; i < 2; i++) {
		float sx = i == 0 ? -1.0 : 1.0;
		float up = i == 0 ? lid_up.x : lid_up.y;
		float tl = i == 0 ? lid_tilt.x : lid_tilt.y;
		float low = i == 0 ? lid_low.x : lid_low.y;
		vec2 e = vec2(sx * eye_pos.x, eye_pos.y) + iris_off * 0.25;
		vec2 q = p - e;
		float qo = q.x * sx;                 // + toward the outer corner
		float ry = eye_r.y * max(eye_open, 0.02);
		vec2 er = vec2(eye_r.x * (1.0 + 0.04 * max(eye_open - 1.0, 0.0)), ry);
		float d_eye = sd_ellipse(q, er);
		// upper lid: drops with lid_up, slants down to the outer corner when worried (tilt < 0)
		float lid_y = -ry + up * 1.1 * ry - tl * 0.42 * (qo + eye_r.x) * 0.5 + max(-tl, 0.0) * 0.012;
		float d_lid = lid_y - q.y;
		// lower lid: an arched cover rising from below (squint / smile)
		float relax = relax_low * (1.0 - clamp((eye_open - 1.0) * 6.0, 0.0, 1.0));    // wide eyes: no resting lid
		float d_low = -sd_ellipse(q - vec2(0.0, ry * (2.05 - 2.0 * (low + relax))), vec2(er.x * 1.5, ry));
		float d_vis = max(max(d_eye, d_lid), d_low);
		float open_a = fill(d_vis) * (1.0 - happy) * (1.0 - hw) * (1.0 - closed);
		vec3 ec = mix(sclera * 0.78, sclera, smoothstep(lid_y - 0.0, lid_y + 0.05, q.y));   // lid shadow
		ec = mix(ec, sclera * 0.85, smoothstep(-0.02, 0.0, d_eye));                          // soft rim
		vec2 iq = q - iris_off;
		float ir = min(iris_r * max(iris_k, 0.35), ry * 0.98);
		float id = length(iq) - ir;
		vec3 ic = mix(accent * 0.55, mix(accent, vec3(1.0), 0.35), smoothstep(-ir, ir * 0.9, iq.y));
		ic = mix(ic, accent * 0.4, smoothstep(-0.009, 0.0, id));                              // soft iris edge
		ec = mix(ec, ic * bright, fill(id));
		ec = mix(ec, vec3(0.03, 0.04, 0.08), fill(length(iq) - min(pupil_r, ir * 0.55)));
		ec = mix(ec, vec3(1.0), fill(length(iq - vec2(-0.028, -0.032)) - 0.021 * min(1.0, ir / iris_r)));
		ec = mix(ec, vec3(1.0), 0.8 * fill(length(iq - vec2(0.024, 0.026)) - 0.009));
		// wet eyes when crying: a bright water line along the lower lid
		ec = mix(ec, vec3(0.75, 0.9, 1.0), cry * 0.7 * soft(abs(d_eye + 0.012) - 0.006, 0.006) * step(0.0, q.y));
		o = over(o, ec * mix(1.0, bright, 0.5), open_a);
		// lid line: a soft dark edge on the cut so a lowered lid reads as a lid
		float lid_edge = soft(abs(d_lid) - 0.007, 0.005) * fill(d_eye - 0.004) * step(d_low, 0.0);
		float lid_k = clamp(up * 4.0 + abs(tl) * 1.5, 0.0, 1.0) * (1.0 - happy) * (1.0 - hw) * (1.0 - closed);
		o = over(o, plate * 0.4, lid_edge * lid_k);
		// happy / laughing crescent  ^ ^
		float arc = sd_bend(q - vec2(0.0, 0.045), eye_r.x * 0.92, -0.085, 0.0, 0.026);
		o = over(o, sclera * bright, fill(arc) * happy * (1.0 - hw));
		// closed (sleep / mid-blink): a calm downward arc
		float cl = sd_bend(q - vec2(0.0, -0.005), eye_r.x * 0.85, 0.035, 0.0, 0.016);
		o = over(o, sclera * (0.45 + 0.55 * bright), fill(cl) * closed * 0.85);
		// love: hearts instead of eyes
		float hd2 = sd_heart((q - vec2(0.0, 0.015)) / 0.2) * 0.2;
		vec3 hc = mix(heart_col * 0.8, mix(heart_col, vec3(1.0), 0.35), smoothstep(0.08, -0.1, q.y));
		hc = mix(hc, vec3(1.0), 0.85 * fill(length(q - vec2(-0.04, -0.035)) - 0.018));
		o = over(o, hc * bright, fill(hd2) * hw * (1.0 - closed));
		// tears: one drop slides down from the outer corner, plus a glossy streak
		if (cry > 0.02) {
			float ph = fract(t * 0.28 + float(i) * 0.5);
			vec2 dp = q - vec2(sx * 0.07, ry * 0.7 + 0.03 + ph * 0.2);
			float drop = min(length(dp) - 0.02, sd_capsule(dp, vec2(0.0, 0.0), vec2(0.0, -0.035), 0.004 + 0.012 * smoothstep(-0.035, 0.0, dp.y)));
			vec3 tc = mix(vec3(0.62, 0.86, 1.0), vec3(1.0), fill(length(dp - vec2(-0.006, -0.004)) - 0.006));
			o = over(o, tc, fill(drop) * cry * (1.0 - 0.7 * ph));
		}
		// brows: rounded, softly arched; tilt < 0 raises the inner end (worried), lid_up lowers it
		float by = eye_pos.y - eye_r.y - 0.075 + up * 0.09 - max(eye_open - 1.0, 0.0) * 0.3 - 0.008 * float(happy > 0.5);
		vec2 bi = vec2(sx * (eye_pos.x - 0.06), by + tl * 0.055);
		vec2 bo = vec2(sx * (eye_pos.x + 0.075), by - tl * 0.022 + 0.008);
		vec2 bm = (bi + bo) * 0.5 + vec2(0.0, -0.014);
		float bd2 = min(sd_capsule(p, bi, bm, 0.016), sd_capsule(p, bm, bo, 0.014));
		float brow_a = (0.45 + 0.55 * clamp(abs(tl) * 1.4 + up * 2.5 + max(eye_open - 1.05, 0.0) * 4.0, 0.0, 1.0)) * (1.0 - 0.6 * asleep);
		o = over(o, mix(shell_hi, vec3(0.85, 0.9, 1.0), 0.5) * bright, fill(bd2) * brow_a);
	}
	// mouth
	vec2 m = vec2(mouth_x, mouth_y);
	vec2 q = p - m;
	float w = mouth_w * mouth_scale;
	float a = smile * 0.075;                        // y = c + a(1 - x^2/w^2): a > 0 -> middle lower = smile
	float tremble = cry * 0.005 * sin(t * 34.0 + q.x * 40.0);
	float c0 = -a * 0.5 + tremble;
	vec3 ink = mix(accent, vec3(1.0), 0.5) * bright;
	float xn = clamp(q.x / w, -1.0, 1.0);
	float bend = 1.0 - xn * xn;
	float yt = c0 + a * bend;
	float yb = yt + mouth_gap * bend;
	float inside = step(abs(q.x), w) * step(yt, q.y) * step(q.y, yb);
	float open_m = smoothstep(0.004, 0.02, mouth_gap) * (1.0 - mouth_o) * (1.0 - asleep);
	float d_in = max(max(abs(q.x) - w, yt - q.y), q.y - yb);
	vec3 mouth_in = vec3(0.12, 0.04, 0.08);
	float tg = sd_ellipse(q - vec2(0.0, yb - 0.005), vec2(w * 0.5, max(mouth_gap * 0.55, 0.004)));
	mouth_in = mix(mouth_in, tongue * (0.6 + 0.4 * bright), fill(max(tg, d_in)));
	o = over(o, mouth_in, fill(d_in) * open_m);
	float top = sd_bend(q, w, a, c0, 0.017 - 0.006 * asleep);
	float bot = sd_bend(q, w, mouth_gap * 1.0 + a, c0, 0.013);
	o = over(o, ink * (1.0 - 0.45 * asleep), fill(top) * (1.0 - mouth_o));
	o = over(o, ink, fill(bot) * open_m * step(0.0, q.y - yt + 0.02) * smoothstep(0.004, 0.03, mouth_gap));
	// surprised "o"
	vec2 orad = vec2(0.042 + mouth_gap * 0.12, 0.05 + mouth_gap * 0.25);
	float od = sd_ellipse(q - vec2(0.0, 0.015), orad);
	o = over(o, mouth_in, fill(od) * mouth_o);
	o = over(o, ink, soft(abs(od) - 0.012, 0.004) * mouth_o);
	COLOR = vec4(o.rgb / max(o.a, 1e-4), o.a);
}
