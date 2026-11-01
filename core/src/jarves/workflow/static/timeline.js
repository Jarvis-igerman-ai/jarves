/* JARVES workflow: the "Timeline" view. Read-only: GET /api/timeline?hours=N. Swim lanes per layer, hatched stop
   bands, the pipeline feed and a copy-as-text block in the same layout as `workflow_console.py timeline`.
   Pure SVG drawn in real pixels (no libraries, no inline styles: positions go through the CSSOM).
   ?demo=1 draws a clearly labelled built-in sample when the service has no data yet. */
"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const TZ = "Asia/Jerusalem";
  const LAYERS = ["supervisor", "autopilot", "wrapper", "manager", "core", "bridge"];
  const RANGES = [6, 24, 168];
  // Locale part for this view (page language: <html lang>, Hebrew by default).
  const I18N = {
    he: {chart: "ציר זמן לפי שכבה", range: "טווח זמן", ranges: {6: "6 שעות", 24: "24 שעות", 168: "7 ימים"},
      layers: {supervisor: "מפקח", autopilot: "טייס אוטומטי", wrapper: "עוטף claude-j", manager: "מנהל", core: "ליבה", bridge: "גשר סוכנים"},
      status: {ok: "פועל", stale: "אין דופק", dead: "עצר"}, legendGap: "המפעל עצר", now: "עכשיו",
      stops: "עצירות בטווח", noStops: "לא היו עצירות בטווח הזה.",
      gap: (layer, from, to, dur, ready) => `${layer}: עצר ⁦${from}–${to}⁩ (${dur}), עבודה ממתינה: ${ready}`, gapShort: dur => `עצר ${dur}`,
      segment: (layer, state, from, to, dur) => `${layer}: ${state}, ⁦${from}–${to}⁩ (${dur})`, mark: (layer, from, text) => `${layer}, ${from}: ⁨${text}⁩`,
      pipeline: "תנועת משימות", allLanes: "כל התחומים", laneFilter: "סינון לפי תחום", noPipeline: "אין תנועת משימות בטווח הזה.",
      lanes: {bugs: "תיקון באגים", release: "בדיקות ושחרור", features: "פיצ'רים", ops: "תשתית ותהליך", security: "אבטחה", manager: "ניהול"},
      states: {queued: "בתור", running: "בעבודה", waiting_owner: "ממתין לך", blocked: "חסום", review: "בבדיקה", merged: "מוזג", verified: "מאומת", done: "הושלם", deferred: "נשמר להמשך"},
      arrow: "←", shown: (n, total) => `מוצגות ${n} מתוך ${total}`, copy: "העתק כטקסט לדיווח באג", copied: "הועתק. אפשר להדביק בדיווח.",
      copyBlocked: "ההעתקה נחסמה. הטקסט מסומן למטה: העתיקו ידנית.", copyLabel: "ציר הזמן כטקסט",
      minutes: n => `${n} דק׳`, hours: n => `${n} שע׳`, days: n => `${n} ימים`,
      loading: "טוען את ציר הזמן…", empty: "עדיין אין נתונים לטווח הזה", emptyHint: "ציר הזמן מתמלא כשהשכבות מדווחות דופק.",
      missing: "השירות המקומי עוד לא מתעד ציר זמן", missingHint: "אחרי עדכון השירות המקומי ההיסטוריה תופיע כאן. אפשר לראות דוגמה עם ‎?demo=1‎ בכתובת.",
      failed: s => `לא ניתן לטעון את ציר הזמן (${s}).`, demo: "נתוני הדגמה בלבד — זה לא המצב האמיתי של המפעל."},
    en: {chart: "Timeline by layer", range: "Time range", ranges: {6: "6 hours", 24: "24 hours", 168: "7 days"},
      layers: {supervisor: "Supervisor", autopilot: "Autopilot", wrapper: "claude-j wrapper", manager: "Manager", core: "Core", bridge: "Agent bridge"},
      status: {ok: "Running", stale: "No heartbeat", dead: "Stopped"}, legendGap: "Factory stopped", now: "Now",
      stops: "Stops in range", noStops: "No stops in this range.",
      gap: (layer, from, to, dur, ready) => `${layer}: stopped ${from}–${to} (${dur}), ${ready} ready tasks waiting`, gapShort: dur => `stopped ${dur}`,
      segment: (layer, state, from, to, dur) => `${layer}: ${state}, ${from}–${to} (${dur})`, mark: (layer, from, text) => `${layer}, ${from}: ${text}`,
      pipeline: "Task movement", allLanes: "All lanes", laneFilter: "Filter by lane", noPipeline: "No task movement in this range.",
      lanes: {bugs: "Bug fixes", release: "Testing and release", features: "Features", ops: "Infrastructure and process", security: "Security", manager: "Management"},
      states: {queued: "Queued", running: "Running", waiting_owner: "Waiting for you", blocked: "Blocked", review: "In review", merged: "Merged", verified: "Verified", done: "Done", deferred: "Deferred"},
      arrow: "→", shown: (n, total) => `Showing ${n} of ${total}`, copy: "Copy as text for a bug report", copied: "Copied. Paste it into the report.",
      copyBlocked: "Copy was blocked. The text is selected below; copy it by hand.", copyLabel: "Timeline as text",
      minutes: n => `${n} min`, hours: n => `${n} h`, days: n => `${n} days`,
      loading: "Loading the timeline…", empty: "No data for this range yet", emptyHint: "The timeline fills in as layers report heartbeats.",
      missing: "The local service does not record a timeline yet", missingHint: "After the local service is updated the history appears here. Add ?demo=1 to the address to see a sample.",
      failed: s => `Could not load the timeline (${s}).`, demo: "Demo data only — this is not the real factory state."},
  };
  const lang = String(document.documentElement.lang || "he").toLowerCase().startsWith("en") ? "en" : "he";
  const L = I18N[lang];
  const DEMO = new URLSearchParams(location.search).get("demo") === "1";
  const fmt = options => new Intl.DateTimeFormat(lang === "en" ? "en-GB" : "he-IL", {timeZone: TZ, hourCycle: "h23", ...options});
  const clock = fmt({hour: "2-digit", minute: "2-digit"});
  const dayClock = fmt({weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit"});
  const dayOnly = fmt({weekday: "short", day: "numeric", month: "numeric"});
  let hours = 24, line = null, failure = "", missing = false, loaded = false, busy = false, lane = "", rangeNow = Date.now() / 1000, observer = null;

  const num = value => (typeof value === "number" && Number.isFinite(value) ? value : null);
  const list = value => (Array.isArray(value) ? value : []);
  const layerName = layer => L.layers[layer] || String(layer ?? "?");
  const stateName = state => L.status[state] || L.states[state] || String(state ?? "?");
  const dayKey = fmt({year: "numeric", month: "numeric", day: "numeric"});
  // Clock time alone when the moment is today in Israel; weekday and date otherwise.
  const when = epoch => (hours > 24 || dayKey.format(new Date(epoch * 1000)) !== dayKey.format(new Date()) ? dayClock : clock).format(new Date(epoch * 1000));
  function duration(seconds) {
    const s = Math.max(0, seconds);
    if (s < 3600) return L.minutes(Math.max(1, Math.round(s / 60)));
    if (s < 72 * 3600) return L.hours(Math.round(s / 360) / 10);
    return L.days(Math.round(s / 8640) / 10);
  }

  // --- Copy-as-text: English, Israel time, the same layout `workflow_console.py timeline` prints. ---
  const stamp = epoch => {
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"})
      .formatToParts(new Date(epoch * 1000)).map(part => [part.type, part.value]));
    return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
  };
  const hrs = seconds => `${(Math.max(0, seconds) / 3600).toFixed(1)}h`;
  function timelineText(data, rangeHours, now) {
    const since = now - rangeHours * 3600;
    const inRange = item => (num(item.end) ?? now) >= since;
    const out = [`JARVES timeline: last ${rangeHours}h, Asia/Jerusalem, generated ${stamp(now)}`, "", "STOPS"];
    const gaps = list(data.gaps).filter(inRange).sort((a, b) => a.start - b.start);
    if (!gaps.length) out.push("  none");
    gaps.forEach(g => out.push(`  ${stamp(g.start)} -> ${stamp(num(g.end) ?? now)}  ${String(g.layer).padEnd(10)}  stopped ${hrs(num(g.seconds) ?? (num(g.end) ?? now) - g.start)}  ready_work=${num(g.ready_work) ?? "?"}`));
    out.push("", "LAYERS");
    const rank = l => (LAYERS.includes(l) ? LAYERS.indexOf(l) : LAYERS.length);
    const segs = list(data.segments).filter(inRange).sort((a, b) => rank(a.layer) - rank(b.layer) || a.start - b.start);
    if (!segs.length) out.push("  none");
    segs.forEach(s => out.push(`  ${stamp(Math.max(s.start, since))} -> ${stamp(num(s.end) ?? now)}  ${String(s.layer).padEnd(10)}  ${s.state}`));
    out.push("", "MARKS");
    const marks = list(data.marks).filter(m => m.ts >= since).sort((a, b) => a.ts - b.ts);
    if (!marks.length) out.push("  none");
    marks.forEach(m => out.push(`  ${stamp(m.ts)}  ${String(m.layer).padEnd(10)}  ${m.kind}: ${m.summary ?? ""}`));
    out.push("", "PIPELINE (newest first)");
    const moves = list(data.pipeline).filter(p => p.ts >= since).sort((a, b) => b.ts - a.ts);
    if (!moves.length) out.push("  none");
    moves.forEach(p => out.push(`  ${stamp(p.ts)}  ${p.task_id}  ${p.from_state ?? "-"} -> ${p.to_state}  [${p.lane ?? "-"}]  ${p.agent || "-"}  ${p.title ?? ""}`));
    return out.join("\n");
  }

  // --- Built-in sample for ?demo=1 (7 days, relative to now). ---
  function demoTimeline() {
    const now = Math.floor(Date.now() / 1000), from = now - 168 * 3600, stop = now - 13 * 3600 - 7 * 60;
    const old = [now - 20 * 3600, now - 19 * 3600 - 12 * 60];
    const segments = [], seg = (layer, state, start, end) => segments.push({layer, state, start, end});
    seg("supervisor", "ok", from, now - 3 * 86400); seg("supervisor", "stale", now - 3 * 86400, now - 3 * 86400 + 1800); seg("supervisor", "ok", now - 3 * 86400 + 1800, now);
    seg("autopilot", "ok", from, old[0]); seg("autopilot", "dead", old[0], old[1]); seg("autopilot", "ok", old[1], stop - 1200); seg("autopilot", "stale", stop - 1200, stop); seg("autopilot", "dead", stop, now);
    seg("wrapper", "ok", from, now - 5 * 3600); seg("wrapper", "stale", now - 5 * 3600, now - 4 * 3600); seg("wrapper", "ok", now - 4 * 3600, now - 420); seg("wrapper", "stale", now - 420, now);
    seg("manager", "ok", from, old[0] + 600); seg("manager", "stale", old[0] + 600, old[1]); seg("manager", "ok", old[1], now);
    seg("core", "ok", from, now); seg("bridge", "ok", from, now - 2 * 3600); seg("bridge", "stale", now - 2 * 3600, now - 2 * 3600 + 900); seg("bridge", "ok", now - 2 * 3600 + 900, now);
    const moves = [["V451-HEALTH-TAB", "Health and timeline dashboard tabs", "features", "review", "merged", "worker_ui"],
      ["V451-TODAY", "Today first screen", "features", "running", "review", "worker_ui"], ["BUG-760", "Supervisor exits on database lock", "bugs", "queued", "running", "worker_a"],
      ["OPS-FACTORY-STALL", "Factory stopped: autopilot loop exited", "ops", null, "queued", "supervisor"], ["BUG-744", "Stop hook retry loses the reason", "bugs", "review", "merged", "merger"],
      ["REL-451-SHEET", "Owner sign-off sheet for 4.5.1", "release", "queued", "waiting_owner", "manager"], ["BUG-731", "Trace pagination skips a row", "bugs", "running", "review", "worker_b"]];
    const pipeline = moves.map((m, i) => ({ts: now - (i < 3 ? 600 + i * 1500 : 13 * 3600 + i * 2100), task_id: m[0], title: m[1], lane: m[2], from_state: m[3], to_state: m[4], agent: m[5]}));
    return {segments, pipeline,
      marks: [{ts: stop, kind: "dead", layer: "autopilot", summary: "heartbeat lost after database is locked"}, {ts: old[0], kind: "dead", layer: "autopilot", summary: "loop exited"},
        {ts: old[1], kind: "restart", layer: "supervisor", summary: "restarted autopilot"}, {ts: now - 4 * 3600, kind: "restart", layer: "wrapper", summary: "claude-j session reopened"}],
      gaps: [{layer: "autopilot", start: stop, end: now, seconds: now - stop, ready_work: 41}, {layer: "autopilot", start: old[0], end: old[1], seconds: old[1] - old[0], ready_work: 6}]};
  }

  async function load() {
    if (busy) return;
    busy = true;
    try {
      let result = null;
      failure = "";
      try {
        const response = await fetch(`/api/timeline?hours=${hours}`, {credentials: "same-origin", cache: "no-store"});
        if (response.status !== 404) {
          if (!response.ok) throw new Error(String(response.status));
          result = await response.json();
        }
      } catch (error) { failure = error.message || "?"; }
      missing = !failure && result === null;
      if (!result && DEMO) { result = demoTimeline(); missing = false; failure = ""; }
      if (result && typeof result === "object") line = {segments: list(result.segments), marks: list(result.marks), gaps: list(result.gaps), pipeline: list(result.pipeline)};
      else if (!failure) line = null;
      rangeNow = Date.now() / 1000;
      loaded = true;
      render();
    } finally { busy = false; }
  }

  // --- Chart ---
  function chart(width) {
    const t1 = rangeNow, t0 = t1 - hours * 3600, laneH = 26, laneGap = 10, top = 24, axis = 24;
    const layers = LAYERS.concat([...new Set(line.segments.map(s => s.layer))].filter(l => !LAYERS.includes(l)));
    const height = top + layers.length * (laneH + laneGap) + axis;
    const x = t => Math.round(Math.min(width, Math.max(0, (t - t0) / (t1 - t0) * width)) * 10) / 10;
    const y = layer => top + layers.indexOf(layer) * (laneH + laneGap);
    const tips = [];
    const tip = text => { tips.push(text); return `data-tip="${tips.length - 1}" tabindex="0" aria-label="${esc(text)}"`; };
    const step = {6: 3600, 24: 3 * 3600, 168: 86400}[hours];
    const ticks = [];
    // Ticks on whole Israel-time hours/days: walk in the step and keep marks where the local clock is round.
    for (let t = Math.ceil(t0 / 900) * 900; t <= t1; t += 900) {
      const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).formatToParts(new Date(t * 1000)).map(q => [q.type, Number(q.value)]));
      if (p.minute === 0 && (step === 86400 ? p.hour === 0 : p.hour % (step / 3600) === 0)) ticks.push(t);
    }
    let svg = `<svg class="tl-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="group" aria-label="${esc(L.chart)}">`
      + `<defs><pattern id="tl-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect class="tl-hatch-bg" width="6" height="6"></rect><line class="tl-hatch-line" x1="0" y1="0" x2="0" y2="6"></line></pattern></defs>`;
    svg += ticks.map(t => `<line class="tl-grid" x1="${x(t)}" x2="${x(t)}" y1="${top - 4}" y2="${height - axis}"></line>`
      + `<text class="tl-tick" x="${x(t)}" y="${height - 8}" text-anchor="${x(t) < 18 ? "start" : x(t) > width - 18 ? "end" : "middle"}">${esc(step === 86400 ? dayOnly.format(new Date(t * 1000)) : clock.format(new Date(t * 1000)))}</text>`).join("");
    svg += layers.map(l => `<rect class="tl-lane" x="0" y="${y(l)}" width="${width}" height="${laneH}" rx="4"></rect>`).join("");
    line.segments.filter(s => layers.includes(s.layer) && num(s.start) !== null && (num(s.end) ?? t1) > t0).forEach(s => {
      const a = x(s.start), b = x(num(s.end) ?? t1), state = ["ok", "stale", "dead"].includes(s.state) ? s.state : "unknown";
      svg += `<rect class="tl-seg tl-${state}" x="${a}" y="${y(s.layer) + 4}" width="${Math.max(2, b - a)}" height="${laneH - 8}" rx="3" `
        + `${tip(L.segment(layerName(s.layer), stateName(s.state), when(s.start), when(num(s.end) ?? t1), duration((num(s.end) ?? t1) - s.start)))}></rect>`;
    });
    const gaps = line.gaps.filter(g => num(g.start) !== null && (num(g.end) ?? t1) > t0);
    gaps.forEach(g => {
      const a = x(g.start), b = x(num(g.end) ?? t1), row = layers.includes(g.layer) ? y(g.layer) : top, w = Math.max(3, b - a);
      const text = L.gap(layerName(g.layer), when(g.start), when(num(g.end) ?? t1), duration(num(g.seconds) ?? (num(g.end) ?? t1) - g.start), num(g.ready_work) ?? "?");
      svg += `<rect class="tl-gap-band" x="${a}" y="${top - 4}" width="${w}" height="${height - axis - top + 4}"></rect>`
        + `<rect class="tl-gap" x="${a}" y="${row}" width="${w}" height="${laneH}" fill="url(#tl-hatch)" ${tip(text)}></rect>`
        + `<line class="tl-gap-edge" x1="${a}" x2="${a + w}" y1="${top - 10}" y2="${top - 10}"></line>`
        + (w > 64 ? `<text class="tl-gap-label" x="${a + 4}" y="${top - 14}">${esc(L.gapShort(duration(num(g.seconds) ?? (num(g.end) ?? t1) - g.start)))}</text>` : "");
    });
    line.marks.filter(m => num(m.ts) !== null && m.ts >= t0 && layers.includes(m.layer)).forEach(m => {
      const cx = x(m.ts), cy = y(m.layer) + 2;
      svg += `<path class="tl-mark tl-mark-${m.kind === "dead" ? "dead" : "info"}" d="M${cx} ${cy - 5} L${cx + 5} ${cy} L${cx} ${cy + 5} L${cx - 5} ${cy} Z" `
        + `${tip(L.mark(layerName(m.layer), when(m.ts), `${m.kind}: ${m.summary ?? ""}`))}></path>`;
    });
    svg += `<line class="tl-now" x1="${width - 1}" x2="${width - 1}" y1="${top - 4}" y2="${height - axis}"></line></svg>`;
    const labels = layers.map(l => `<li class="tl-label" data-layer="${esc(l)}">${esc(layerName(l))}</li>`).join("");
    return {svg, labels, tips, height, top, laneH, laneGap};
  }

  function drawChart() {
    const box = $("#tl-plot");
    if (!box || !line) return;
    const width = Math.max(240, Math.floor(box.clientWidth));
    if (!box.clientWidth) return;
    const drawn = chart(width);
    box.innerHTML = drawn.svg;
    box.tips = drawn.tips;
    const labels = $("#tl-labels");
    labels.innerHTML = drawn.labels;
    // Lane labels line up with the SVG rows; offsets through the CSSOM (CSP: no style attributes in markup).
    labels.style.paddingBlockStart = `${drawn.top}px`;
    labels.querySelectorAll(".tl-label").forEach(li => { li.style.blockSize = `${drawn.laneH}px`; li.style.marginBlockEnd = `${drawn.laneGap}px`; });
  }

  function showTip(target) {
    const box = $("#tl-plot"), tipNode = $("#tl-tip");
    const text = box?.tips?.[Number(target.dataset.tip)];
    if (!tipNode || text === undefined) return;
    tipNode.textContent = text;
    tipNode.hidden = false;
    const r = target.getBoundingClientRect(), host = $("#tl-chart").getBoundingClientRect();
    const left = Math.min(Math.max(0, r.left - host.left + r.width / 2 - tipNode.offsetWidth / 2), host.width - tipNode.offsetWidth);
    tipNode.style.left = `${Math.max(0, left)}px`;
    tipNode.style.top = `${Math.max(0, r.top - host.top - tipNode.offsetHeight - 8)}px`;
  }
  const hideTip = () => { const t = $("#tl-tip"); if (t) t.hidden = true; };

  function stops() {
    const t0 = rangeNow - hours * 3600;
    const gaps = line.gaps.filter(g => num(g.start) !== null && (num(g.end) ?? rangeNow) > t0).sort((a, b) => b.start - a.start);
    if (!gaps.length) return `<p class="hl-quiet">${esc(L.noStops)}</p>`;
    return `<ul class="tl-stops">${gaps.map(g => `<li><span class="tl-stop-swatch" aria-hidden="true"></span><span>${esc(L.gap(layerName(g.layer), when(g.start), when(num(g.end) ?? rangeNow), duration(num(g.seconds) ?? (num(g.end) ?? rangeNow) - g.start), num(g.ready_work) ?? "?"))}</span></li>`).join("")}</ul>`;
  }

  function pipeline() {
    const t0 = rangeNow - hours * 3600;
    const all = line.pipeline.filter(p => num(p.ts) !== null && p.ts >= t0).sort((a, b) => b.ts - a.ts);
    const lanes = [...new Set(all.map(p => p.lane).filter(Boolean))];
    if (lane && !lanes.includes(lane)) lane = "";
    const rows = all.filter(p => !lane || p.lane === lane);
    const chips = `<div class="filter-group tl-lanes" role="group" aria-label="${esc(L.laneFilter)}">`
      + [["", L.allLanes], ...lanes.map(l => [l, L.lanes[l] || l])].map(([value, label]) => `<button type="button" class="filter${value === lane ? " active" : ""}" data-tl-lane="${esc(value)}" aria-pressed="${value === lane}">${esc(label)}</button>`).join("") + `</div>`;
    const shown = rows.slice(0, 200);
    const items = shown.map(p => `<li class="tl-move"><time class="tl-move-time" datetime="${esc(new Date(p.ts * 1000).toISOString())}">${esc(when(p.ts))}</time>`
      + `<span class="tl-move-main"><span class="tl-move-title" dir="auto">${esc(p.title || p.task_id)}</span>`
      + `<span class="tl-move-meta"><button type="button" class="text-link" data-task="${esc(p.task_id)}" dir="ltr">${esc(p.task_id)}</button>`
      + `<span class="tl-move-states"><span>${esc(p.from_state ? stateName(p.from_state) : "—")}</span><span aria-hidden="true"> ${L.arrow} </span><b class="tl-to tl-to-${esc(p.to_state)}">${esc(stateName(p.to_state))}</b></span>`
      + (p.lane ? `<span>${esc(L.lanes[p.lane] || p.lane)}</span>` : "") + (p.agent ? `<span dir="ltr">${esc(p.agent)}</span>` : "") + `</span></span></li>`).join("");
    return chips + (rows.length ? `<ol class="tl-feed">${items}</ol>${rows.length > shown.length ? `<p class="hl-quiet">${esc(L.shown(shown.length, rows.length))}</p>` : ""}` : `<p class="hl-quiet">${esc(L.noPipeline)}</p>`);
  }

  function render() {
    const root = $("#timeline-root");
    if (!root) return;
    const range = `<div class="tl-toolbar"><div class="filter-group" role="group" aria-label="${esc(L.range)}">${RANGES.map(h => `<button type="button" class="filter${h === hours ? " active" : ""}" data-tl-range="${h}" aria-pressed="${h === hours}">${esc(L.ranges[h])}</button>`).join("")}</div>`
      + `<button type="button" class="button secondary" id="tl-copy"${line ? "" : " disabled"}><span aria-hidden="true">⧉</span> ${esc(L.copy)}</button></div>`
      + `<p id="tl-copy-status" class="tl-copy-status" role="status" aria-live="polite"></p><textarea id="tl-copy-text" class="retro-prompt tl-copy-text" rows="8" readonly hidden aria-label="${esc(L.copyLabel)}" dir="ltr"></textarea>`;
    let body;
    if (!loaded) body = `<p class="hl-quiet">${esc(L.loading)}</p>`;
    else if (!line && missing) body = `<div class="empty-state"><h3>${esc(L.missing)}</h3><p>${esc(L.missingHint)}</p></div>`;
    else if (!line) body = `<div class="empty-state"><h3>${esc(L.empty)}</h3><p>${esc(failure ? L.failed(failure) : L.emptyHint)}</p></div>`;
    else if (!line.segments.length && !line.gaps.length && !line.pipeline.length && !line.marks.length) body = `<div class="empty-state"><h3>${esc(L.empty)}</h3><p>${esc(L.emptyHint)}</p></div>`;
    else body = `<section class="panel tl-panel" aria-labelledby="tl-chart-title"><div class="panel-heading"><div><span class="section-index">01</span><h2 id="tl-chart-title">${esc(L.chart)}</h2></div>`
      + `<span class="tl-legend" aria-hidden="true"><i class="tl-key tl-ok"></i>${esc(L.status.ok)}<i class="tl-key tl-stale"></i>${esc(L.status.stale)}<i class="tl-key tl-dead"></i>${esc(L.status.dead)}<i class="tl-key tl-key-gap"></i>${esc(L.legendGap)}</span></div>`
      + `<div class="tl-chart" id="tl-chart"><ol class="tl-labels" id="tl-labels"></ol><div class="tl-plot" id="tl-plot" dir="ltr"></div><div id="tl-tip" class="tl-tip" role="tooltip" hidden></div></div>`
      + `<h3 class="tl-sub">${esc(L.stops)}</h3>${stops()}</section>`
      + `<section class="panel tl-panel" aria-labelledby="tl-feed-title"><div class="panel-heading"><div><span class="section-index">02</span><h2 id="tl-feed-title">${esc(L.pipeline)}</h2></div></div><div class="tl-feed-wrap">${pipeline()}</div></section>`
      + (failure ? `<p class="hl-quiet" role="status">${esc(L.failed(failure))}</p>` : "");
    const demo = DEMO && line ? `<p class="demo-notice" role="note">${esc(L.demo)}</p>` : "";
    const focus = document.activeElement;
    const keep = focus?.closest?.("#timeline-root") ? ["data-tl-range", "data-tl-lane", "data-task"].map(k => [k, focus.getAttribute(k)]).find(([, v]) => v !== null) : null;
    root.innerHTML = demo + range + body;
    drawChart();
    if (keep) [...root.querySelectorAll(`[${keep[0]}]`)].find(el => el.getAttribute(keep[0]) === keep[1])?.focus({preventScroll: true});
    else if (focus?.id === "tl-copy") $("#tl-copy")?.focus({preventScroll: true});
    if (!observer && "ResizeObserver" in window) {
      let last = 0;
      observer = new ResizeObserver(entries => { const w = Math.floor(entries[0].contentRect.width); if (w && w !== last) { last = w; drawChart(); } });
    }
    const plot = $("#tl-plot");
    if (observer && plot) { observer.disconnect(); observer.observe(plot); }
  }

  async function copy() {
    if (!line) return;
    const text = timelineText(line, hours, Date.now() / 1000);
    const status = $("#tl-copy-status"), area = $("#tl-copy-text");
    // A clipboard permission prompt can leave the promise pending: fall back to the selectable text after 2 s.
    const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 2000));
    try { await Promise.race([navigator.clipboard.writeText(text), timeout]); status.textContent = L.copied; area.hidden = true; }
    catch (_) { area.value = text; area.hidden = false; area.focus(); area.select(); status.textContent = L.copyBlocked; }
  }

  document.addEventListener("click", event => {
    if (!event.target.closest?.("#timeline-root")) return;
    const range = event.target.closest("[data-tl-range]");
    if (range) { hours = Number(range.dataset.tlRange); render(); load(); return; }
    const chip = event.target.closest("[data-tl-lane]");
    if (chip) { lane = chip.dataset.tlLane; render(); return; }
    if (event.target.closest("#tl-copy")) copy();
  });
  ["pointerover", "focusin"].forEach(type => document.addEventListener(type, event => { const t = event.target.closest?.("#tl-plot [data-tip]"); if (t) showTip(t); }));
  ["pointerout", "focusout"].forEach(type => document.addEventListener(type, event => { if (event.target.closest?.("#tl-plot [data-tip]")) hideTip(); }));
  document.addEventListener("keydown", event => { if (event.key === "Escape") hideTip(); });

  const visible = () => location.hash === "#timeline" && !document.hidden;
  window.addEventListener("hashchange", () => { if (visible()) { render(); load(); } });
  document.addEventListener("visibilitychange", () => { if (visible()) load(); });
  setInterval(() => { if (visible()) load(); }, 60000);
  if (location.hash === "#timeline") load();
})();
