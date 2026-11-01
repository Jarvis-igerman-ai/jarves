/* JARVES workflow: the "Health" view (watch the watcher). Read-only: GET /api/health, and /api/timeline only for
   the ready-work count in the stop banner. A separate file because the strict CSP allows no inline scripts.
   ?demo=1 draws a clearly labelled built-in sample when the service has no data yet; nothing is ever sent. */
"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const TZ = "Asia/Jerusalem";
  const LAYERS = ["supervisor", "autopilot", "wrapper", "manager", "core", "bridge"];
  // Locale part for this view (page language: <html lang>, Hebrew by default).
  const I18N = {
    he: {heading: "שכבות המפעל", headingNote: "כל שכבה שומרת על זו שמתחתיה. נקודה ירוקה: יש דופק.",
      incidents: "תקלות פתוחות", noIncidents: "אין תקלות פתוחות.",
      layers: {supervisor: "מפקח", autopilot: "טייס אוטומטי", wrapper: "עוטף claude-j", manager: "מנהל", core: "ליבה", bridge: "גשר סוכנים"},
      status: {ok: "פועל", stale: "אין דופק", dead: "עצר"}, unknown: "לא ידוע",
      justNow: "נראה לפני רגע", minAgo: n => `נראה לפני ${n} דק׳`, hourAgo: n => `נראה לפני ${n} שע׳`, dayAgo: n => `נראה לפני ${n} ימים`, never: "טרם נראה",
      minutes: n => `${n} דק׳`, hours: n => n === 1 ? "שעה" : `${n} שעות`, days: n => n === 1 ? "יום" : `${n} ימים`,
      stopped: (at, dur) => `המפעל עצר מ־${at} — ${dur}.`, ready: n => `עבודה ממתינה: ${n}`,
      incidentOpen: (at, dur) => `תקלה פתוחה מ־${at} — ${dur}.`, since: at => `מאז ${at}`, openTask: id => `משימת התפעול ${id}`,
      loading: "טוען את מצב השכבות…", empty: "אין עדיין נתוני דופק", emptyHint: "השכבות יופיעו כאן אחרי שידווחו דופק ראשון.",
      missing: "השירות המקומי עוד לא מדווח בריאות", missingHint: "אחרי עדכון השירות המקומי המצב יופיע כאן. אפשר לראות דוגמה עם ‎?demo=1‎ בכתובת.",
      failed: s => `לא ניתן לטעון את מצב השכבות (${s}). ננסה שוב בעוד חצי דקה.`, updated: at => `עודכן ${at}`,
      demo: "נתוני הדגמה בלבד — זה לא המצב האמיתי של המפעל."},
    en: {heading: "Factory layers", headingNote: "Each layer watches the one below it. A green dot means a heartbeat was seen.",
      incidents: "Open incidents", noIncidents: "No open incidents.",
      layers: {supervisor: "Supervisor", autopilot: "Autopilot", wrapper: "claude-j wrapper", manager: "Manager", core: "Core", bridge: "Agent bridge"},
      status: {ok: "Running", stale: "No heartbeat", dead: "Stopped"}, unknown: "Unknown",
      justNow: "Seen just now", minAgo: n => `Seen ${n} min ago`, hourAgo: n => `Seen ${n} h ago`, dayAgo: n => `Seen ${n} days ago`, never: "Never seen",
      minutes: n => `${n} min`, hours: n => n === 1 ? "1 hour" : `${n} hours`, days: n => n === 1 ? "1 day" : `${n} days`,
      stopped: (at, dur) => `The factory stopped at ${at} — ${dur}.`, ready: n => `${n} ready tasks waiting`,
      incidentOpen: (at, dur) => `Incident open since ${at} — ${dur}.`, since: at => `Since ${at}`, openTask: id => `Ops task ${id}`,
      loading: "Loading layer health…", empty: "No heartbeat data yet", emptyHint: "Layers appear here after their first heartbeat.",
      missing: "The local service does not report health yet", missingHint: "After the local service is updated the state appears here. Add ?demo=1 to the address to see a sample.",
      failed: s => `Could not load layer health (${s}). Retrying in half a minute.`, updated: at => `Updated ${at}`,
      demo: "Demo data only — this is not the real factory state."},
  };
  const lang = String(document.documentElement.lang || "he").toLowerCase().startsWith("en") ? "en" : "he";
  const L = I18N[lang];
  const DEMO = new URLSearchParams(location.search).get("demo") === "1";
  const clock = new Intl.DateTimeFormat(lang === "en" ? "en-GB" : "he-IL", {timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
  const dayClock = new Intl.DateTimeFormat(lang === "en" ? "en-GB" : "he-IL", {timeZone: TZ, weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
  let health = null, readyWork = null, skew = 0, failure = "", missing = false, loaded = false, busy = false;

  const num = value => (typeof value === "number" && Number.isFinite(value) ? value : null);
  const serverNow = () => Date.now() / 1000 + skew;
  const at = epoch => (serverNow() - epoch > 20 * 3600 ? dayClock : clock).format(new Date(epoch * 1000));

  function duration(seconds) {
    const s = Math.max(0, seconds);
    if (s < 3600) return L.minutes(Math.max(1, Math.round(s / 60)));
    if (s < 48 * 3600) return L.hours(Math.round(s / 3600));
    return L.days(Math.round(s / 86400));
  }

  function seen(epoch) {
    if (epoch === null) return L.never;
    const s = Math.max(0, serverNow() - epoch);
    if (s < 60) return L.justNow;
    if (s < 3600) return L.minAgo(Math.floor(s / 60));
    if (s < 48 * 3600) return L.hourAgo(Math.floor(s / 3600));
    return L.dayAgo(Math.floor(s / 86400));
  }

  // Built-in sample for ?demo=1, relative to now so the live clock moves; never mixed with real data.
  function demoHealth() {
    const now = Math.floor(Date.now() / 1000), stop = now - 13 * 3600 - 7 * 60;
    const beat = (layer, status, age, state, note = "") => ({layer, status, state, last_beat_at: now - age, age_s: age, note});
    return {now, layers: [beat("supervisor", "ok", 14, "watching"), beat("autopilot", "dead", now - stop, "stopped", "database is locked; loop exited"),
      beat("wrapper", "stale", 7 * 60, "idle", "no claude-j session open"), beat("manager", "ok", 50, "waiting_owner"),
      beat("core", "ok", 6, "running"), beat("bridge", "ok", 33, "connected")],
      incidents: [{key: "factory-stopped", kind: "factory_stopped", since: stop, summary: "Autopilot stopped after a database lock; 41 tasks are ready.", task_id: "OPS-FACTORY-STALL"}]};
  }

  async function get(path) {
    const response = await fetch(path, {credentials: "same-origin", cache: "no-store"});
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  async function load() {
    if (busy) return;
    busy = true;
    try {
      let result = null;
      try { result = await get("/api/health"); failure = ""; }
      catch (error) { failure = error.message || "?"; }
      missing = !failure && result === null;
      if (!result && DEMO) { result = demoHealth(); missing = false; failure = ""; }
      if (result && typeof result === "object") {
        health = {layers: Array.isArray(result.layers) ? result.layers : [], incidents: Array.isArray(result.incidents) ? result.incidents : []};
        skew = num(result.now) !== null ? result.now - Date.now() / 1000 : 0;
        readyWork = null;
        if (stopInfo()) {
          const line = DEMO && health.incidents.some(i => i.key === "factory-stopped") ? {gaps: [{ready_work: 41, end: serverNow()}]}
            : await get("/api/timeline?hours=24").catch(() => null);
          const open = (line?.gaps || []).filter(g => num(g.ready_work) !== null && (num(g.end) === null || g.end >= serverNow() - 300));
          if (open.length) readyWork = Math.max(...open.map(g => g.ready_work));
        }
      } else if (!failure) health = null;
      loaded = true;
      render();
    } finally { busy = false; }
  }

  function layerName(layer) { return L.layers[layer] || String(layer || L.unknown); }

  function stopInfo() {
    if (!health) return null;
    const dead = health.layers.filter(l => l.status === "dead" && num(l.last_beat_at) !== null).map(l => l.last_beat_at);
    if (dead.length) return {kind: "stopped", since: Math.min(...dead)};
    const open = health.incidents.filter(i => num(i.since) !== null).map(i => i.since);
    if (open.length) return {kind: "incident", since: Math.min(...open)};
    if (health.incidents.length || health.layers.some(l => l.status === "dead")) return {kind: "incident", since: null};
    return null;
  }

  function banner() {
    const info = stopInfo();
    if (!info) return "";
    const text = info.since === null ? L.incidents
      : (info.kind === "stopped" ? L.stopped : L.incidentOpen)(at(info.since), duration(serverNow() - info.since));
    const ready = readyWork !== null ? ` <b>${esc(L.ready(readyWork))}</b>` : "";
    return `<div class="hl-banner" id="health-banner"><span class="hl-banner-dot" aria-hidden="true"></span><p><strong>${esc(text)}</strong>${ready}</p></div>`;
  }

  function rows() {
    const order = layer => { const i = LAYERS.indexOf(layer); return i < 0 ? LAYERS.length : i; };
    const list = health.layers.slice().sort((a, b) => order(a.layer) - order(b.layer));
    if (!list.length) return `<div class="empty-state compact"><h3>${esc(L.empty)}</h3><p>${esc(L.emptyHint)}</p></div>`;
    return `<ul class="hl-layers">${list.map(l => {
      const status = ["ok", "stale", "dead"].includes(l.status) ? l.status : "unknown";
      const beat = num(l.last_beat_at);
      return `<li class="hl-row hl-${status}"><span class="hl-dot" aria-hidden="true"></span>`
        + `<span class="hl-main"><span class="hl-name">${esc(layerName(l.layer))}</span><span class="hl-code" dir="ltr">${esc(l.layer)}</span>`
        + (l.note ? `<span class="hl-note" dir="auto">${esc(l.note)}</span>` : "") + `</span>`
        + `<span class="hl-side"><span class="hl-status">${esc(L.status[status] || L.unknown)}</span>`
        + `<span class="hl-age" data-beat="${beat === null ? "" : esc(beat)}">${esc(seen(beat))}</span>`
        + (l.state ? `<span class="hl-state" dir="ltr">${esc(l.state)}</span>` : "") + `</span></li>`;
    }).join("")}</ul>`;
  }

  function incidents() {
    if (!health.incidents.length) return `<p class="hl-quiet">${esc(L.noIncidents)}</p>`;
    return `<ul class="hl-incidents">${health.incidents.map(i => {
      const since = num(i.since);
      return `<li class="hl-incident"><span class="hl-kind" dir="ltr">${esc(i.kind || i.key)}</span>`
        + `<span class="hl-incident-main"><span class="hl-incident-summary" dir="auto">${esc(i.summary || i.key)}</span>`
        + `<span class="hl-incident-meta">${since === null ? "" : `<span>${esc(L.since(at(since)))} · ${esc(duration(serverNow() - since))}</span>`}`
        + (i.task_id ? `<button type="button" class="text-link" data-task="${esc(i.task_id)}">${esc(L.openTask(i.task_id))} <span aria-hidden="true">←</span></button>` : "")
        + `</span></span></li>`;
    }).join("")}</ul>`;
  }

  function render() {
    const root = $("#health-root");
    if (!root) return;
    const demo = DEMO && health ? `<p class="demo-notice" role="note">${esc(L.demo)}</p>` : "";
    let body;
    if (!loaded) body = `<p class="hl-quiet">${esc(L.loading)}</p>`;
    else if (!health && missing) body = `<div class="empty-state"><h3>${esc(L.missing)}</h3><p>${esc(L.missingHint)}</p></div>`;
    else if (!health) body = `<div class="empty-state"><h3>${esc(L.empty)}</h3><p>${esc(failure ? L.failed(failure) : L.emptyHint)}</p></div>`;
    else body = banner()
      + `<section class="panel hl-panel" aria-labelledby="health-layers-title"><div class="panel-heading"><div><span class="section-index">01</span><h2 id="health-layers-title">${esc(L.heading)}</h2></div>`
      + `<span class="small-label" id="health-updated">${esc(L.updated(clock.format(new Date())))}</span></div><p class="hl-intro">${esc(L.headingNote)}</p>${rows()}</section>`
      + `<section class="panel hl-panel" aria-labelledby="health-incidents-title"><div class="panel-heading"><div><span class="section-index">02</span><h2 id="health-incidents-title">${esc(L.incidents)}</h2></div>`
      + `<span class="today-count${health.incidents.length ? " warn" : ""}">${health.incidents.length || ""}</span></div>${incidents()}</section>`
      + (failure ? `<p class="hl-quiet" role="status">${esc(L.failed(failure))}</p>` : "");
    const focusedTask = document.activeElement?.closest?.("#health-root [data-task]")?.dataset.task;
    root.innerHTML = demo + body;
    if (focusedTask) [...root.querySelectorAll("[data-task]")].find(b => b.dataset.task === focusedTask)?.focus({preventScroll: true});
  }

  // Live "last seen" text without a refetch: only the age labels and the banner duration change.
  function tick() {
    document.querySelectorAll("#health-root .hl-age").forEach(node => {
      const text = seen(node.dataset.beat === "" ? null : Number(node.dataset.beat));
      if (node.textContent !== text) node.textContent = text;
    });
    const old = $("#health-banner");
    if (old) {
      const fresh = document.createElement("div");
      fresh.innerHTML = banner();
      if (fresh.firstElementChild && fresh.firstElementChild.innerHTML !== old.innerHTML) old.innerHTML = fresh.firstElementChild.innerHTML;
    }
  }

  const visible = () => location.hash === "#health" && !document.hidden;
  window.addEventListener("hashchange", () => { if (visible()) { render(); load(); } });
  document.addEventListener("visibilitychange", () => { if (visible()) load(); });
  setInterval(() => { if (visible()) tick(); }, 15000);
  setInterval(() => { if (visible()) load(); }, 30000);
  if (location.hash === "#health") load();
})();
