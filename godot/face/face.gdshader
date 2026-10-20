shader_type canvas_item;
render_mode unshaded;
// JARVES face: one full-screen SDF pass (eyes, lids, mouth, rim, status dot, glass). No textures, no
// post-processing, so it stays cheap on a Raspberry Pi 5. face.gd owns every animated value; this only draws.
// Coordinates: p in [-1, 1], y down, the round screen is the unit disc.

uniform float t = 0.0;
uniform bool low_quality = false;
uniform vec3 eye_col : source_color = vec3(0.36, 0.78, 1.0);
uniform float bright = 1.0;            // overall eye brightness (sleep dims it)
uniform float glow = 1.0;              // eye/mouth halo strength
uniform vec2 eye_half = vec2(0.15, 0.19);
uniform float eye_gap = 0.37;
uniform float eye_y = -0.1;
uniform vec2 look = vec2(0.0);         // gaze, roughly -1..1
uniform vec2 open = vec2(1.0);         // vertical openness per eye (L, R), blink included
uniform vec2 lid_up = vec2(0.0);       // upper lid drop 0..1 (L, R)
uniform vec2 lid_tilt = vec2(0.0);     // + inner corner down (stern), - inner corner up (worried)
uniform vec2 lid_low = vec2(0.0);      // lower lid rise 0..1 (L, R)
uniform float lid_curve = 0.0;         // arches the lower lid -> happy crescent
uniform float sleep = 0.0;             // 1 = closed sleeping eyes
uniform float iris = 1.0;              // inner iris ring / depth amount
uniform float mouth_smile = 0.4;       // + smile, - frown
uniform float mouth_open = 0.0;        // 0..1
uniform float mouth_w = 0.1;
uniform vec2 mouth_pos = vec2(0.0, 0.34);
uniform float mouth_alpha = 1.0;
uniform float ambient = 1.0;
uniform float ring = 0.0;              // rim halo (listening / error)
uniform vec3 ring_col : source_color = vec3(0.36, 0.78, 1.0);
uniform float spin = 0.0;              // thinking comet on the rim
uniform float blush = 0.0;
uniform float jitter = 0.0;            // error glitch
uniform vec3 dot_col : source_color = vec3(0.3, 0.95, 0.55);
uniform vec2 dot_pos = vec2(0.0, 0.84);
uniform float dot_pulse = 0.0;         // expanding ring (live conversation)
uniform float dot_breathe = 0.0;       // slow brightness breathing (connecting)
uniform float dot_slash = 0.0;         // muted: hollow ring + slash
uniform float cam_on = 0.0;            // camera indicator (software: Camera Module 3 has no LED)
uniform float cam_flash = 0.0;         // 1 -> 0 over 0.6 s after a photo was taken
// motion speeds / amounts (face.gd CALM / LIVELY tuning block)
uniform float wave_amt = 0.1;          // rim halo shimmer depth
uniform float wave_speed = 0.5;        // rim halo shimmer speed
uniform float spin_speed = 1.2;        // thinking comet, rad/s
uniform float tear_thresh = 0.97;      // error glitch: higher = rarer tears
uniform float dot_speed = 0.6;         // status-dot pulse / breathe speed
// transient reactions (face.gd REACT)
uniform float cry = 0.0;               // 0..1: tears sliding down from the outer lower lids
uniform float tear_hz = 0.28;          // tear cycles per second per eye
uniform float mouth_o = 0.0;           // 0..1: blend the mouth into a small round "o" (surprised)
uniform float heart = 0.0;             // 0..1: heart-shaped glints in the eyes (love)

float sd_rbox(vec2 p, vec2 b, float r) {
	vec2 q = abs(p) - b + r;
	return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

float dot2(vec2 v) { return dot(v, v); }

// heart (Inigo Quilez), y up, tip at the origin, about 1 unit tall
float sd_heart(vec2 p) {
	p.x = abs(p.x);
	if (p.y + p.x > 1.0) return sqrt(dot2(p - vec2(0.25, 0.75))) - sqrt(2.0) / 4.0;
	return sqrt(min(dot2(p - vec2(0.0, 1.0)), dot2(p - 0.5 * max(p.x + p.y, 0.0)))) * sign(p.x - p.y);
}

// a falling drop: round bottom, tapered top (y down)
float sd_drop(vec2 q, float r) {
	q.y *= q.y < 0.0 ? 0.5 : 1.0;
	return length(q) - r;
}

float sd_segment(vec2 p, vec2 a, vec2 b) {
	vec2 pa = p - a, ba = b - a;
	return length(pa - ba * clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0));
}

// signed distance of one eye; q is local to the eye centre with +x towards the nose
float eye_sdf(vec2 q, vec2 hs, float op, float lu, float tilt, float ll) {
	float h = max(hs.y * op, 0.012);
	float d = sd_rbox(q, vec2(hs.x, h), min(hs.x * 0.92, h));
	float xn = q.x / hs.x;
	// upper lid: a straight line, tilted per emotion
	float k = tilt * hs.y * 0.55 / hs.x;
	float yu = -h + 2.0 * h * lu + k * q.x;
	d = max(d, (yu - q.y) / sqrt(1.0 + k * k));
	// lower lid: rises and arches into a crescent when happy
	float dline = q.y - (h - 2.0 * h * ll);
	float rc = hs.x * 1.0;
	float yc = -h + min(hs.x * 0.92, h) + 0.62 * hs.y;
	float dcirc = rc - length(q - vec2(0.0, yc));        // happy: a round cheek pushes up -> "^" crescent
	d = max(d, mix(dline, max(dcirc, q.y - yc), lid_curve));
	// sleeping: a relaxed closed-lid arc
	float xc = clamp(q.x, -hs.x * 0.9, hs.x * 0.9);
	float xs = xc / (hs.x * 0.9);
	float ys = hs.y * 0.18 * (1.0 - xs * xs) - hs.y * 0.05;
	float ds = length(vec2(q.x - xc, q.y - ys)) - 0.016;
	return mix(d, ds, sleep);
}

vec3 eye_shade(vec2 q, vec2 hs, float d) {
	vec2 n = q / hs;
	vec2 ic = vec2(0.0, 0.12) + look * vec2(0.3, 0.22);
	vec2 dq = (n - ic) * vec2(1.0, 0.8);
	float rr = length(dq);
	// luminous body: deep saturated colour at the top (lid shadow), brighter towards the bottom
	vec3 c = eye_col * (0.42 + 0.38 * smoothstep(-1.0, 0.8, n.y));
	// iris: a soft luminous disc with fine radial fibres and a faint limbal ring -> depth, not a cartoon pupil
	float fib = 0.5 + 0.5 * sin(atan(dq.y, dq.x) * 18.0 + sin(rr * 14.0) * 1.5);
	float disc = smoothstep(0.78, 0.5, rr);
	vec3 irisc = eye_col * (0.9 + 0.045 * fib * smoothstep(0.2, 0.6, rr)) + vec3(0.05, 0.06, 0.08);
	c = mix(c, irisc, disc * iris);
	c += eye_col * exp(-(rr - 0.74) * (rr - 0.74) * 160.0) * 0.35 * iris;      // limbal ring
	c = mix(c, eye_col * 0.32, smoothstep(0.3, 0.1, rr) * 0.55 * iris);       // deep pupil well
	c += mix(eye_col, vec3(1.0), 0.6) * exp(-rr * rr * 30.0) * 0.12 * iris;  // light deep inside
	c = mix(c, eye_col * 1.05 + vec3(0.08), lid_curve * 0.7);                // happy crescent: even, bright
	// inner rim: crisp bright edge, like light caught in a glass lens
	c += mix(eye_col, vec3(1.0), 0.45) * exp(min(d, 0.0) * 80.0) * 0.55;
	// specular highlights (fixed light from upper left, slight parallax against the gaze)
	vec2 hp = n - vec2(-0.34, -0.5) + look * 0.06;
	float s1 = smoothstep(1.0, 0.45, length(hp / vec2(0.3, 0.17)));
	float s2 = smoothstep(1.0, 0.3, length((n - vec2(0.36, 0.42) + look * 0.04) / 0.075));
	if (heart > 0.001) {
		vec2 hq = (n - vec2(-0.3, -0.36) + look * 0.06) * vec2(1.0, hs.y / hs.x) / 0.42;
		float hm = smoothstep(0.06, -0.06, sd_heart(vec2(hq.x, 0.5 - hq.y) * 1.0));
		s1 = mix(s1, hm, heart);
		c = mix(c, vec3(1.0, 0.84, 0.92), clamp(s1 * 0.85, 0.0, 1.0) * heart);
	}
	c = mix(c, vec3(1.0), clamp(s1 * 0.75 * (1.0 - heart) + s2 * 0.7, 0.0, 1.0) * max(iris, 0.5));
	return c;
}

float mouth_sdf(vec2 p) {
	vec2 q = p - mouth_pos;
	float t0 = 0.016;
	float w = mouth_w - t0;
	float xc = clamp(q.x, -w, w);
	float s = 1.0 - (xc / w) * (xc / w);
	float centre = mouth_smile * 0.055 * s;
	float top = centre - mouth_open * 0.02 * sqrt(s);
	float bot = centre + mouth_open * 0.105 * sqrt(s);
	float mid = (top + bot) * 0.5;
	float half_h = (bot - top) * 0.5;
	float slope = mouth_smile * 0.11 * xc / (w * w);
	float by = abs(q.y - mid) - half_h;
	// first-order distance correction along the curve only (keeps the halo even, no streaks off the ends)
	by /= mix(sqrt(1.0 + slope * slope), 1.0, clamp(abs(by) * 10.0 + max(abs(q.x) - w, 0.0) * 20.0, 0.0, 1.0));
	vec2 b = vec2(abs(q.x) - w, by);
	float d = length(max(b, 0.0)) + min(max(b.x, b.y), 0.0) - t0;
	if (mouth_o > 0.001) {   // surprised: a small round "o" (ellipse, first-order distance)
		vec2 ro = vec2(0.042, 0.052);
		vec2 qo = (q - vec2(0.0, 0.014)) / ro;
		float dO = (length(qo) - 1.0) * length(qo) / max(length(qo / ro), 1e-3);
		d = mix(d, dO, mouth_o);
	}
	return d;
}

void fragment() {
	vec2 p = UV * 2.0 - 1.0;
	float aa = max(fwidth(p.x), 1e-4) * 1.2;
	float r = length(p);
	// error glitch: rare thin horizontal tears
	float tear = step(tear_thresh, fract(sin(floor(t * 9.0) * 91.7) * 4375.85));
	p.x += jitter * tear * 0.018 * sin(p.y * 60.0 + t * 40.0);

	// --- screen: deep navy, lighter in the middle, vignette towards the bezel
	vec3 col = mix(vec3(0.032, 0.05, 0.088), vec3(0.004, 0.007, 0.014), smoothstep(0.05, 1.0, r));
	col *= 1.0 - 0.45 * smoothstep(0.7, 1.0, r);
	// ambient light the eyes throw onto the glass
	float amb = exp(-dot((p - vec2(0.0, eye_y + 0.04)) * vec2(0.9, 1.5), (p - vec2(0.0, eye_y + 0.04)) * vec2(0.9, 1.5)) * 2.2);
	col += eye_col * amb * 0.085 * ambient * bright;

	// --- eyes
	float glow_acc = 0.0;
	vec3 eyes = vec3(0.0);
	float eye_a = 0.0;
	vec2 offs = look * vec2(0.065, 0.05);
	for (int i = 0; i < 2; i++) {
		float side = i == 0 ? -1.0 : 1.0;
		vec2 c = vec2(side * eye_gap, eye_y) + offs;
		vec2 q = p - c;
		vec2 qi = vec2(-q.x * side, q.y);       // +x towards the nose
		float d = eye_sdf(qi, eye_half, open[i], lid_up[i], lid_tilt[i], lid_low[i]);
		float a = smoothstep(aa, -aa, d);
		vec3 sh = mix(eye_shade(q, eye_half, d), eye_col * 0.9, sleep);
		eyes = mix(eyes, sh, a);
		eye_a = max(eye_a, a);
		float dd = max(d, 0.0);
		glow_acc += exp(-dd * 26.0) * 0.5 + (low_quality ? 0.0 : exp(-dd * 7.0) * 0.22);
	}

	// --- mouth
	float md = mouth_sdf(p);
	float ma = smoothstep(aa, -aa, md) * mouth_alpha;
	float inner = smoothstep(-0.008, -0.03, md) * smoothstep(0.05, 0.35, mouth_open);
	vec3 mouth = mix(eye_col * 1.05 + vec3(0.08), eye_col * 0.22 + vec3(0.01, 0.015, 0.03), inner);
	float mdd = max(md, 0.0);
	glow_acc += (exp(-mdd * 30.0) * 0.45 + (low_quality ? 0.0 : exp(-mdd * 9.0) * 0.12)) * mouth_alpha;

	col += eye_col * glow_acc * glow * bright * 0.55;

	// happy cheeks
	if (blush > 0.001) {
		for (int i = 0; i < 2; i++) {
			float side = i == 0 ? -1.0 : 1.0;
			vec2 bq = (p - vec2(side * (eye_gap + 0.05), eye_y + eye_half.y + 0.1)) / vec2(0.11, 0.065);
			col += vec3(1.0, 0.36, 0.55) * exp(-dot(bq, bq) * 1.6) * 0.32 * blush;
		}
	}

	col = mix(col, eyes * bright, eye_a);
	col = mix(col, mouth * bright, ma);

	// --- tears (cry): a drop slides from the outer lower lid down the cheek, with a soft shine and a wet trail
	if (cry > 0.001) {
		for (int i = 0; i < 2; i++) {
			float side = i == 0 ? -1.0 : 1.0;
			float ph = fract(t * tear_hz + (i == 0 ? 0.0 : 0.47));
			vec2 st = vec2(side * (eye_gap + eye_half.x * 0.55), eye_y + eye_half.y * 0.72) + offs;
			float fall = ph * ph * 0.3;                     // gravity: starts slow, speeds up
			vec2 tp = st + vec2(side * fall * 0.06, fall);
			float ta = smoothstep(0.0, 0.18, ph) * (1.0 - smoothstep(0.7, 1.0, ph)) * cry;
			float r0 = 0.019 * (0.6 + 0.4 * smoothstep(0.0, 0.25, ph));
			float td = sd_drop(p - tp, r0);
			float trail = sd_segment(p, st, tp - vec2(0.0, r0)) - 0.005;
			col += eye_col * exp(-max(td, 0.0) * 60.0) * 0.35 * ta;
			col = mix(col, eye_col * 0.6 + vec3(0.06, 0.1, 0.14), smoothstep(aa, -aa, trail) * 0.25 * ta);
			vec3 dc = mix(eye_col * 0.75 + vec3(0.05), eye_col * 1.1 + vec3(0.15), smoothstep(-r0, r0, p.y - tp.y));
			dc = mix(dc, vec3(1.0), smoothstep(0.008, 0.0, length(p - tp - vec2(-0.006, -0.004))) * 0.9);
			col = mix(col, dc, smoothstep(aa, -aa, td) * ta);
		}
	}

	// --- rim halo (listening reacts to the mic, error pulses) and the thinking comet
	float ang = atan(p.y, p.x);
	float rd = abs(r - 0.935);
	float wave = 1.0 - wave_amt + wave_amt * sin(ang * 3.0 + t * 1.7 * wave_speed) * sin(ang * 2.0 - t * 1.1 * wave_speed);
	col += ring_col * ring * wave * (exp(-rd * 90.0) * 0.75 + (low_quality ? 0.0 : exp(-rd * 16.0) * 0.28));
	if (spin > 0.001) {
		float head = t * spin_speed;
		float lag = mod(head - ang, 6.28318);
		float tail = exp(-lag * 1.3) + 0.35 * exp(-mod(head + 3.14159 - ang, 6.28318) * 2.2);
		col += eye_col * spin * tail * (exp(-rd * 120.0) * 1.1 + exp(-rd * 25.0) * 0.25);
	}

	// --- status dot
	vec2 dq = p - dot_pos;
	float dl = length(dq);
	float dr = 0.03;
	float breathe = 1.0 - dot_breathe * (0.45 + 0.45 * sin(t * 3.2 * dot_speed));
	float dfill = dl - dr;
	float dring = abs(dl - dr * 0.86) - dr * 0.24;
	vec2 sd = vec2(0.7071, -0.7071) * dr * 1.45;
	float dslash = sd_segment(dq, -sd, sd) - 0.0055;
	float dshape = mix(dfill, min(dring, dslash), dot_slash);
	float dhalo = max(dshape, 0.0);
	col += dot_col * breathe * (exp(-dhalo * 60.0) * 0.55 + exp(-dhalo * 18.0) * 0.18);
	if (dot_slash > 0.5) {   // dark gap either side of the slash so it reads as "crossed out"
		float gap = sd_segment(dq, -sd, sd) - 0.012;
		col *= mix(1.0, 0.25, smoothstep(aa, -aa, gap) * step(dl, dr * 1.25));
	}
	if (dot_pulse > 0.001) {
		float ph = fract(t * 0.75 * dot_speed);
		float pr = dr + ph * 0.075;
		col += dot_col * dot_pulse * (1.0 - ph) * (exp(-abs(dl - pr) * 140.0) * 0.9);
	}
	vec3 dc = mix(dot_col * 1.1 + vec3(0.12), vec3(1.0), smoothstep(dr * 0.75, 0.0, dl) * 0.35 * (1.0 - dot_slash));
	col = mix(col, dc * breathe, smoothstep(aa, -aa, dshape));

	// --- camera indicator (upper-left, mirrors the status dot): a small warm lens ring + pupil while the camera
	// is ON; a brief bright pulse when a photo is taken. Absent (exactly nothing drawn) when the camera is off.
	if (cam_on > 0.001 || cam_flash > 0.001) {
		vec2 cq = p - vec2(-dot_pos.x, dot_pos.y);
		float cl = length(cq);
		float cring = abs(cl - 0.024) - 0.0055;
		float cshape = min(cring, cl - 0.009);
		vec3 ccol = vec3(1.0, 0.72, 0.32) * (0.85 + 0.6 * cam_flash);
		col += ccol * max(cam_on, cam_flash) * exp(-max(cshape, 0.0) * 55.0) * 0.35;
		col = mix(col, ccol, smoothstep(aa, -aa, cshape) * max(cam_on, cam_flash));
		col += vec3(1.0, 0.9, 0.75) * cam_flash * exp(-cl * 9.0) * 0.5;
	}

	// --- glass: faint curved reflection and a thin bezel edge
	if (!low_quality) {
		vec2 gq = (p - vec2(-0.38, -0.62)) / vec2(0.62, 0.3);
		float refl = exp(-dot(gq, gq) * 2.0) * smoothstep(0.9, 0.4, r);
		col += vec3(0.6, 0.75, 1.0) * refl * 0.035;
	}
	col += vec3(0.25, 0.4, 0.6) * exp(-abs(r - 0.985) * 220.0) * 0.12;
	float disc = smoothstep(1.0, 1.0 - aa * 1.5, r);
	COLOR = vec4(col, disc);
}
