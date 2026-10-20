// JARVES dashboard pages for WP-M3: Jobs, Reports, Notes, Files, Alerts, Autonomy. Uses the helpers of app.js
// (el, api, render, header, pill, day, clock, route) and registers its pages in PAGES (pages.js).
"use strict";

(function () {
  // The Core words schedules ("בכל יום ראשון ב-10:00") and stamps ("יום א׳ 25.10.2026 10:00") in Hebrew. Every other UI
  // language gets the same facts in English (Campaign D: Hebrew text and bidi-mangled dates in the English UI).
  const DAYS_EN = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const LETTER = { "א": 0, "ב": 1, "ג": 2, "ד": 3, "ה": 4, "ו": 5, "ש": 6 };
  window.schedStamp = (s) => (JL.lang === "he" || !s) ? s
    : String(s).replace(/יום ([א-ש])['׳]/g, (m, l) => DAYS_EN[LETTER[l]].slice(0, 3));
  window.schedHuman = (kind, spec, he) => {
    if (JL.lang === "he") return he;
    try {
      if (kind === "once") { const d = new Date(spec); const p = (n) => String(n).padStart(2, "0");
        return `once, ${DAYS_EN[d.getDay()].slice(0, 3)} ${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} at ${p(d.getHours())}:${p(d.getMinutes())}`; }
      const f = String(spec).trim().split(/\s+/); if (f.length !== 5) return he;
      const num = (x) => /^\d+$/.test(x) ? parseInt(x, 10) : null, p = (n) => String(n).padStart(2, "0");
      const [mi, hr, dom, mon, dow] = f, m = num(mi), h = num(hr);
      const at = m !== null && h !== null ? ` at ${p(h)}:${p(m)}` : "";
      if (m !== null && h !== null && dom === "*" && mon === "*") {
        if (dow === "*") return "every day" + at;
        if (dow === "0-4") return "Sunday to Thursday" + at;
        if (dow === "1-5") return "Monday to Friday" + at;
        if (/^[\d,]+$/.test(dow)) return "every " + dow.split(",").map((x) => DAYS_EN[+x % 7]).join(" and ") + at;
      }
      if (/^\*\/\d+$/.test(mi) && f.slice(1).every((x) => x === "*")) return `every ${mi.slice(2)} minutes`;
      if (m !== null && f.slice(1).every((x) => x === "*")) return m === 0 ? "every full hour" : `every hour, ${m} minutes past`;
      if (/^\*\/\d+$/.test(hr) && m !== null && dom === "*" && mon === "*" && dow === "*") return `every ${hr.slice(2)} hours`;
      return "cron: " + spec;
    } catch (e) { return he; }
  };
  // "Hebrew / English" names written by the Core (e.g. the weekly self-check job): show the part for the UI language
  const biName = (n) => { const m = /^([^A-Za-z]*[֐-׿][^A-Za-z]*?)\s+\/\s+([A-Za-z].*)$/.exec(String(n || "")); return m ? (JL.lang === "he" ? m[1] : m[2]) : n; };
  const when = (ts) => ts ? `${day(ts)} ${clock(ts)}` : "—";
  const msgSpan = () => el("span", { class: "muted small" });
  async function act(msg, fn) {
    try { await fn(); route(true); } catch (e) { msg.textContent = "Failed: " + e.message; }
  }
  // destructive: the SERVER asks on the approval popup and this request waits for the owner's answer
  const WAITING = () => tx("ממתין לאישור על המסך שלך", "waiting for approval on your screen");
  const actApproved = (msg, fn) => { msg.textContent = WAITING(); return act(msg, fn); };
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  function download(name, text) {
    const a = el("a", { href: URL.createObjectURL(new Blob([text], { type: "text/markdown" })), download: name });
    document.body.append(a); a.click(); a.remove();
  }
  const statusPill = (s) => pill({ active: "ok", done: "ok", ok: "ok", delivered: "ok", applied: "ok", draft: "plain",
    paused: "plain", pending: "degraded", proposed: "degraded", skipped: "degraded", partial: "degraded",
    interrupted: "fail", failed: "fail", suppressed: "plain", expired: "plain" }[s] || "plain", s);

  // ------------------------------------------------------------- Jobs
  async function pageJobs() {
    const d = await api("/api/jobs");
    const msg = msgSpan();
    const whenIn = el("input", { type: "text", placeholder: "כל בוקר ב-8 / every monday at 10 / 0 8 * * *", dir: "auto", style: "min-width:260px" });
    const taskIn = el("input", { type: "text", placeholder: "What to do (research question or reminder text)", dir: "auto", style: "min-width:320px" });
    const kind = el("select", {}, el("option", { value: "research" }, "research report"), el("option", { value: "reminder" }, "reminder"));
    const preview = el("div", { class: "muted small" });
    whenIn.addEventListener("change", async () => {
      try { const p = await post("/api/schedule/parse", { when: whenIn.value }); preview.textContent = `${schedHuman(p.kind, p.spec, p.human)} · next: ${p.next.map(schedStamp).join(" | ")}`; }
      catch (e) { preview.textContent = e.message; }
    });
    const form = el("div", { class: "card", style: "margin-bottom:12px" }, el("b", {}, "New job (preview first, then approve)"),
      el("div", { class: "row", style: "margin-top:8px;flex-wrap:wrap;gap:8px" }, whenIn, taskIn, kind,
        el("button", { type: "button", class: "primary", onclick: () => act(msg, () => post("/api/jobs", { when: whenIn.value, task: taskIn.value, action: kind.value })) }, "Preview"), msg),
      preview);
    const rows = d.jobs.map((j) => {
      const m = msgSpan();
      const op = (o) => () => act(m, () => post("/api/jobs/" + j.id, { op: o }));
      const buttons = [
        j.status === "draft" ? el("button", { type: "button", class: "primary", onclick: op("approve") }, "Approve") : null,
        j.status === "active" ? el("button", { type: "button", onclick: op("pause") }, "Pause") : null,
        j.status === "paused" ? el("button", { type: "button", onclick: op("resume") }, "Resume") : null,
        j.status !== "draft" ? el("button", { type: "button", onclick: op("run_now") }, "Run now") : null,
        el("button", { type: "button", class: "ghost", onclick: () => { msg.textContent = WAITING(); op("delete")(); } }, "Delete")];
      const runs = (j.runs || []).slice(0, 5).map((r) => el("div", { class: "small muted" },
        `${when(r.scheduled_for)} · ${r.trigger}${r.late ? " · late" : ""}${r.missed ? ` · ${r.missed} missed` : ""} · `, statusPill(r.state),
        r.error ? ` ${r.error}` : ""));
      return el("tr", {}, el("td", {}, el("b", { dir: "auto" }, biName(j.name)), el("div", { class: "small", dir: "auto" }, j.task),
          el("div", { class: "small muted" }, `${j.action} · skills: ${j.skills.join(", ") || "none"} · max ${j.max_requests} AI request(s)/run · retries ${j.retries} · max age ${Math.round(j.max_age_s / 3600)} h`)),
        el("td", {}, el("div", { dir: "auto" }, schedHuman(j.kind, j.spec, j.human)), el("div", { class: "small muted", dir: "ltr" }, j.spec + " · " + j.tz)),
        el("td", {}, statusPill(j.status), el("div", { class: "small" }, j.next_run_local ? "next: " + schedStamp(j.next_run_local) : (j.upcoming_local || []).map(schedStamp).join(" | "))),
        el("td", {}, j.last_outcome ? statusPill(j.last_outcome) : "—", el("div", { class: "small muted" }, schedStamp(j.last_run_local) || ""), runs),
        el("td", {}, buttons, m));
    });
    const tzIn = el("input", { type: "text", value: d.timezone, style: "width:150px", "aria-label": "Default time zone" });
    const tzMsg = msgSpan();
    const tzSave = el("button", { type: "button", onclick: () => act(tzMsg, () => post("/api/scheduler/settings", { timezone: tzIn.value })) }, "Set zone");
    // WP-L: the two other scheduler settings the guide explains (missed-job max age, what "ימי חול" means)
    const ageIn = el("input", { type: "number", min: "0", max: "168", step: "1", value: String(Math.round((d.max_age_s || 21600) / 3600)), style: "width:70px", "aria-label": "Missed job max age (hours)" });
    const wwIn = el("select", { "aria-label": "Workdays (ימי חול)" }, el("option", { value: "0-4" }, "ימי חול = Sun-Thu"), el("option", { value: "1-5" }, "ימי חול = Mon-Fri"));
    wwIn.value = d.workweek || "0-4";
    const setSave = el("button", { type: "button", onclick: () => act(tzMsg, () => post("/api/scheduler/settings", { max_age_s: Number(ageIn.value) * 3600, workweek: wwIn.value })) }, "Save");
    const settings = el("div", { class: "card small", style: "margin-bottom:12px" }, el("b", {}, "Settings "),
      "A missed job (computer off) runs once if not older than ", ageIn, " hours · ", wwIn, " ", setSave,
      " ", el("a", { href: "#/guide/scheduler" }, "Guide"));
    render(header("Jobs", JL.lang === "he" ? `תזכורות ודוחות מתוזמנים. עכשיו ${d.now_local}. הליבה מריצה משימות שהגיע זמנן (בדיקה לפחות כל 30 שניות); משימה שהוחמצה רצה פעם אחת אחרי ההפעלה אם היא לא ישנה מדי, ומסומנת כמאוחרת.` : `Scheduled reminders and reports. Now ${schedStamp(d.now_local)}. ${d.note}`, tzIn, tzSave, tzMsg), settings, form,
      el("div", { class: "card" }, rows.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Job"), el("th", {}, "When"),
        el("th", {}, "Status"), el("th", {}, "Last run"), el("th", {}, ""))), el("tbody", {}, rows)) : el("div", { class: "empty" }, "No jobs yet.")));
  }

  // ------------------------------------------------------------- Reports
  async function pageReports(id) {
    const d = await api("/api/reports?period=this_week");
    const list = el("div", { class: "card list" }, d.reports.length ? d.reports.map((r) => el("a", { href: "#/reports/" + r.id, class: String(r.id) === id ? "active" : "" },
      el("span", { dir: "auto" }, r.title), " ", statusPill(r.status), r.late ? pill("degraded", "late") : null)) : el("div", { class: "empty" }, "No reports this week."));
    const body = el("div", { class: "card" }, el("div", { class: "muted" }, id ? "Loading…" : "Pick a report."));
    render(header("Reports", "Saved results of scheduled jobs. 'Read aloud' reads the saved text; nothing is regenerated."), el("div", { class: "grid2" }, list, body));
    if (!id) return;
    const r = await api("/api/reports/" + id);
    const m = msgSpan();
    body.replaceChildren(el("div", { class: "row" }, el("b", { dir: "auto" }, r.title), el("span", { class: "spacer" }), m,
        el("button", { type: "button", class: "primary", onclick: () => act(m, async () => { const x = await post(`/api/reports/${r.id}`, { op: "speak" }); m.textContent = x.note; }) }, "Read aloud"),
        el("button", { type: "button", class: "ghost", onclick: () => actApproved(m, () => post(`/api/reports/${r.id}`, { op: "delete" })) }, "Delete")),
      el("div", { class: "small muted" }, `${schedStamp(r.created_local)} · ${r.status}${r.late ? " · ran late (catch-up)" : ""}${r.read_at ? " · read " + when(r.read_at) : ""}`),
      el("pre", { class: "file", dir: "auto" }, r.text),
      r.sources.length ? el("div", { class: "small" }, "Sources: ", r.sources.map((s) => s.query ? `search "${s.query}"` : s.name).join(" · ")) : null);
  }

  // ------------------------------------------------------------- Notes
  async function pageNotes(arg) {
    const q = el("input", { type: "search", placeholder: "Search notes", dir: "auto", value: arg || "" });
    const d = await api("/api/notes?q=" + encodeURIComponent(arg || ""));
    q.addEventListener("change", () => { location.hash = "#/notes/" + encodeURIComponent(q.value); });
    const title = el("input", { type: "text", placeholder: "Title", dir: "auto" });
    const tags = el("input", { type: "text", placeholder: "tags, comma separated", dir: "auto" });
    const text = el("textarea", { class: "file", dir: "auto", style: "min-height:80px" });
    const msg = msgSpan();
    const add = el("div", { class: "card", style: "margin-bottom:12px" }, el("b", {}, "New note"),
      el("div", { class: "row", style: "gap:8px;margin:8px 0" }, title, tags), text,
      el("div", { class: "row" }, el("button", { type: "button", class: "primary", onclick: () => act(msg, () => post("/api/notes", { title: title.value, text: text.value, tags: tags.value })) }, "Save"), msg));
    const cards = d.notes.map((n) => {
      const m = msgSpan();
      const ta = el("textarea", { class: "file", dir: "auto", style: "min-height:60px" }); ta.value = n.text;
      return el("div", { class: "card", style: "margin-bottom:10px" },
        el("div", { class: "row" }, el("b", { dir: "auto" }, n.title), " ", n.tags.map((t) => pill("plain", "#" + t)), el("span", { class: "spacer" }),
          el("span", { class: "small muted" }, `created ${when(n.created)} · updated ${when(n.updated)}`)), ta,
        el("div", { class: "row" }, el("button", { type: "button", onclick: () => act(m, () => post("/api/notes/" + n.id, { op: "edit", text: ta.value })) }, "Save"),
          el("button", { type: "button", onclick: async () => { const x = await post("/api/notes/" + n.id, { op: "export" }); download(x.filename, x.markdown); } }, "Export .md"),
          el("button", { type: "button", class: "ghost", onclick: () => actApproved(m, () => post("/api/notes/" + n.id, { op: "delete" })) }, "Delete"), m));
    });
    render(header("Notes", "Your notes: searchable, editable, exportable as Markdown.", q,
      el("button", { type: "button", onclick: async () => { const x = await api("/api/notes/export"); download(x.filename, x.markdown); } }, "Export all")),
      add, cards.length ? cards : el("div", { class: "card empty" }, "No notes."));
  }

  // ------------------------------------------------------------- Files
  async function pageFiles(path) {
    const d = await api("/api/files?path=" + encodeURIComponent(path || ""));
    const viewer = el("div", { class: "card" }, el("div", { class: "muted" }, "Pick a file."));
    const items = d.items.map((i) => el("tr", {}, el("td", {}, i.link ? el("span", { class: "muted" }, i.name + " (link: not followed)") : i.dir
      ? el("a", { href: "#/files/" + encodeURIComponent(i.path) }, i.name + "/")
      : el("a", { href: "#", onclick: async (ev) => { ev.preventDefault(); const f = await api("/api/files/read?path=" + encodeURIComponent(i.path));
          viewer.replaceChildren(el("div", { class: "row" }, el("b", {}, f.path), el("span", { class: "spacer" }),
            el("button", { type: "button", onclick: () => download(i.name, f.text) }, "Download")), el("pre", { class: "file", dir: "auto" }, f.text)); } }, i.name)),
      el("td", { class: "num small" }, i.dir ? "" : i.bytes + " B"), el("td", { class: "small muted" }, when(i.modified))));
    const gpath = el("input", { type: "text", placeholder: "C:\\Users\\you\\Documents\\Shared", style: "min-width:320px" });
    const gmode = el("select", {}, el("option", { value: "read" }, "read only"), el("option", { value: "readwrite" }, "read + write"));
    const gm = msgSpan();
    const grants = d.grants.map((g) => {
      const m = msgSpan();
      const op = (o) => () => act(m, () => post("/api/files/grants/" + g.id, { op: o }));
      return el("tr", {}, el("td", {}, el("code", {}, `grant:${g.id}`)), el("td", { class: "small" }, g.path), el("td", {}, g.mode), el("td", {}, statusPill(g.status)),
        el("td", { class: "small muted" }, `${g.requested_by || ""} ${g.reason || ""}`),
        el("td", {}, g.status === "pending" ? [el("button", { type: "button", class: "primary", onclick: op("approve") }, "Approve"), el("button", { type: "button", onclick: op("deny") }, "Deny")]
          : g.status === "active" ? el("button", { type: "button", onclick: op("revoke") }, "Revoke") : null, m));
    });
    render(header("Files", `JARVES's private workspace (${d.root}); ${(d.usage_bytes / 1048576).toFixed(1)} of ${d.quota_mb} MB used. Nothing here is ever run.`),
      el("div", { class: "grid2" }, el("div", { class: "card" }, el("b", {}, "/" + (d.path === "." ? "" : d.path)),
        el("table", { style: "margin-top:8px" }, el("tbody", {}, path ? el("tr", {}, el("td", {}, el("a", { href: "#/files" }, "↑ top"))) : null, items))), viewer),
      el("h2", {}, "Folder grants"),
      el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;margin-bottom:8px" }, gpath, gmode,
        el("button", { type: "button", class: "primary", onclick: () => { if (confirm(`Let JARVES ${gmode.value === "read" ? "read" : "read and write"} ${gpath.value}?`)) act(gm, () => post("/api/files/grants", { path: gpath.value, mode: gmode.value })); } }, "Grant folder"), gm),
        grants.length ? el("table", {}, el("tbody", {}, grants)) : el("div", { class: "empty" }, "No folders granted. JARVES can only ask; you approve here.")));
  }

  // ------------------------------------------------------------- Alerts
  async function pageAlerts() {
    const d = await api("/api/alerts");
    const s = d.settings;
    const qs = el("input", { type: "time", value: s["alerts.quiet_start"] });
    const qe = el("input", { type: "time", value: s["alerts.quiet_end"] });
    const qon = el("input", { type: "checkbox" }); qon.checked = s["alerts.quiet_enabled"];
    const urg = el("input", { type: "checkbox" }); urg.checked = s["alerts.urgent_in_quiet"];
    const mph = el("input", { type: "number", min: 1, max: 60, value: s["alerts.max_per_hour"], style: "width:60px" });
    const sel = {};
    const deliv = d.priorities.map((p) => { sel[p] = el("select", {}, d.channels.map((c) => el("option", { value: c, selected: s["alerts.delivery"][p] === c }, c)));
      return el("label", { class: "small" }, p + " ", sel[p]); });
    const msg = msgSpan();
    const save = () => act(msg, () => post("/api/alerts/settings", { quiet_start: qs.value, quiet_end: qe.value, quiet_enabled: qon.checked,
      urgent_in_quiet: urg.checked, max_per_hour: Number(mph.value), delivery: Object.fromEntries(d.priorities.map((p) => [p, sel[p].value])) }));
    const list = (rows) => rows.length ? el("table", {}, el("tbody", {}, rows.map((a) => el("tr", {}, el("td", { class: "small muted" }, when(a.ts)),
      el("td", {}, pill(a.priority === "urgent" || a.priority === "high" ? "fail" : "plain", a.priority)), el("td", { dir: "auto" }, el("b", {}, a.title), el("div", { class: "small" }, a.text.slice(0, 300))),
      el("td", {}, statusPill(a.status), el("div", { class: "small muted" }, a.delivered_via || a.reason || a.channel || "")))))) : el("div", { class: "empty" }, "None.");
    render(header("Alerts", `Proactive alerts: ${d.paused ? "PAUSED" : "on"}${d.quiet_now ? " · quiet hours now" : ""}. Quiet popup = a silent line on the face; speech = said aloud. ${d.os_notifications}.`,
      el("button", { type: "button", onclick: () => act(msg, () => post(d.paused ? "/api/alerts/resume" : "/api/alerts/pause", { minutes: 60 })) }, d.paused ? "Resume" : "Pause 1 h"),
      el("button", { type: "button", onclick: () => act(msg, () => post("/api/alerts/test", {})) }, "Test alert")),
      el("div", { class: "card", style: "margin-bottom:12px" }, el("b", {}, "Settings"),
        el("div", { class: "row", style: "gap:12px;flex-wrap:wrap;margin-top:8px" }, el("label", { class: "small" }, qon, " quiet hours "), qs, "–", qe,
          el("label", { class: "small" }, urg, " urgent may speak in quiet hours"), el("label", { class: "small" }, "max/hour ", mph)),
        el("div", { class: "row", style: "gap:12px;margin-top:8px" }, "Delivery:", deliv), el("div", { class: "row" }, el("button", { type: "button", class: "primary", onclick: save }, "Save"), msg)),
      el("h2", {}, `Pending (${d.pending.length})`), el("div", { class: "card" }, list(d.pending),
        d.pending.length ? el("button", { type: "button", onclick: () => act(msg, () => post("/api/alerts/dismiss_all", {})) }, "Dismiss all") : null),
      el("h2", {}, "Recent"), el("div", { class: "card" }, list(d.recent)));
  }

  // ------------------------------------------------------------- Autonomy
  async function pageAutonomy() {
    const d = await api("/api/autonomy");
    const msg = msgSpan();
    const level = el("select", {}, d.levels.map((l) => el("option", { value: l, selected: l === d.level }, l)));
    const grants = d.appliers.filter((a) => a.risk === "low").map((a) => { const c = el("input", { type: "checkbox", value: a.id }); c.checked = d.grants.includes(a.id);
      return el("label", { class: "small" }, c, ` ${a.id}: ${a.about}`); });
    const rows = d.proposals.map((p) => {
      const m = msgSpan();
      const op = (o) => () => act(m, () => post("/api/proposals/" + p.id, { op: o }));
      return el("tr", {}, el("td", {}, el("b", { dir: "auto" }, p.title), el("div", { class: "small", dir: "auto" }, p.detail), p.result ? el("div", { class: "small muted" }, p.result) : null),
        el("td", {}, pill(p.risk === "low" ? "ok" : "degraded", p.risk)), el("td", {}, statusPill(p.status), el("div", { class: "small muted" }, p.decided_by || "")),
        el("td", {}, ["proposed", "observed", "failed"].includes(p.status) ? [el("button", { type: "button", class: "primary", onclick: op("approve") }, "Approve"), el("button", { type: "button", onclick: op("reject") }, "Reject")]
          : p.status === "applied" && p.revert ? el("button", { type: "button", onclick: op("revert") }, "Revert") : null, m));
    });
    render(header("Autonomy", "Diagnostics propose calibration changes. suggest_and_ask (default) always asks; approved_low_risk applies only the low-risk changes you tick; observe_only just records.",
      el("button", { type: "button", onclick: () => act(msg, () => post("/api/autonomy/sync", {})) }, "Check diagnostics now")),
      el("div", { class: "card", style: "margin-bottom:12px" }, el("div", { class: "row", style: "gap:12px" }, "Level ", level),
        el("div", { style: "margin:8px 0" }, "Allowed automatically (approved_low_risk only):", grants.map((g) => el("div", {}, g))),
        el("button", { type: "button", class: "primary", onclick: () => act(msg, () => post("/api/autonomy", { level: level.value,
          grants: [...document.querySelectorAll("#main input[type=checkbox]:checked")].map((c) => c.value) })) }, "Save"), msg),
      el("h2", {}, "Proposals"), el("div", { class: "card" }, rows.length ? el("table", {}, el("tbody", {}, rows)) : el("div", { class: "empty" }, "No proposals.")),
      el("h2", {}, "Audit log"), el("div", { class: "card" }, el("table", {}, el("tbody", {}, d.audit.slice(0, 50).map((a) => el("tr", {},
        el("td", { class: "small muted" }, when(a.ts)), el("td", {}, a.actor), el("td", {}, a.action), el("td", { class: "small" }, `${a.target || ""} ${a.detail || ""}`)))))));
  }

  // ------------------------------------------------------------- Timers & alarms (V4 P0.3)
  const leftText = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(r).padStart(2, "0"); };
  async function pageTimers() {
    const d = await api("/api/timers");
    const msg = msgSpan();
    const kind = el("select", {}, el("option", { value: "timer" }, tx("טיימר", "Timer")), el("option", { value: "alarm" }, tx("שעון מעורר", "Alarm")));
    const mins = el("input", { type: "number", min: "0", max: "1440", step: "any", placeholder: tx("דקות", "Minutes"), style: "width:110px", "aria-label": tx("דקות", "Minutes") });
    const at = el("input", { type: "time", "aria-label": tx("שעה", "Time") });
    const label = el("input", { type: "text", dir: "auto", maxlength: "80", placeholder: tx("תווית (לא חובה)", "Label (optional)"), style: "min-width:200px", "aria-label": tx("תווית", "Label") });
    const sync = () => { mins.hidden = kind.value === "alarm"; at.hidden = kind.value !== "alarm"; };
    kind.addEventListener("change", sync); sync();
    const send = () => act(msg, () => post("/api/timers", kind.value === "alarm"
      ? { kind: "alarm", at: at.value, label: label.value } : { kind: "timer", minutes: parseFloat(mins.value || "0"), label: label.value }));
    const add = el("div", { class: "card", style: "margin-bottom:12px" }, el("b", {}, tx("טיימר או שעון מעורר חדש", "New timer or alarm")),
      el("div", { class: "row", style: "gap:8px;margin:8px 0;flex-wrap:wrap" }, kind, mins, at, label,
        el("button", { type: "button", class: "primary", onclick: send }, tx("התחל", "Start")), msg));
    const cells = [];
    const rows = d.timers.map((t) => {
      const left = el("td", { class: "small", "data-left": String(t.seconds_left) }, leftText(t.seconds_left));
      cells.push(left);
      return el("tr", {}, el("td", {}, pill("plain", t.kind === "alarm" ? tx("שעון מעורר", "Alarm") : tx("טיימר", "Timer"))),
        el("td", { dir: "auto" }, t.label || "—"), el("td", { class: "small muted" }, t.kind === "alarm" ? t.at : ""), left,
        el("td", {}, el("button", { type: "button", class: "ghost", onclick: () => act(msg, () => post("/api/timers/" + t.id, { op: "cancel" })) }, tx("ביטול", "Cancel"))));
    });
    const stText = { fired: tx("צלצל", "rang"), cancelled: tx("בוטל", "cancelled"), missed: tx("הוחמץ", "missed") };
    const recent = d.recent.map((r) => el("tr", {}, el("td", {}, r.kind === "alarm" ? tx("שעון מעורר", "Alarm") : tx("טיימר", "Timer")),
      el("td", { dir: "auto" }, r.label || "—"), el("td", { class: "small muted" }, stText[r.status] || r.status), el("td", { class: "small muted" }, when(r.due))));
    render(header(tx("טיימרים ושעונים מעוררים", "Timers & alarms"), tx("הליבה מצלצלת בקול, גם כשהחלון סגור.", "The Core rings them aloud, even when the window is closed.")),
      add, el("div", { class: "card" }, rows.length ? el("table", {}, el("tbody", {}, rows)) : el("div", { class: "empty" }, tx("אין טיימרים פעילים.", "No timers running."))),
      d.recent.length ? [el("h2", {}, tx("אחרונים", "Recent")), el("div", { class: "card" }, el("table", {}, el("tbody", {}, recent)))] : null,
      el("div", { class: "muted small", style: "margin-top:8px" }, tx("שינוי כאן נכנס לתוקף תוך שנייה.", "A change here applies within a second.")));
    const t0 = Date.now();
    timer = setInterval(() => {
      const gone = (Date.now() - t0) / 1000;
      let done = false;
      for (const c of cells) { const s = Number(c.dataset.left) - gone; c.textContent = leftText(s); if (s <= -2) done = true; }
      if (done) route(true);
    }, 1000);
  }

  // ------------------------------------------------------------- Lists (V4 P0.3)
  async function pageLists(arg) {
    const all = await api("/api/lists");
    const name = arg || (all.lists[0] && all.lists[0].name) || "shopping";
    const d = await api("/api/lists?name=" + encodeURIComponent(name));
    const msg = msgSpan();
    const names = el("div", { class: "card list" }, d.lists.map((l) => el("a", { href: "#/lists/" + encodeURIComponent(l.name), class: l.name === d.list ? "active" : "" },
      el("span", { dir: "auto" }, l.name), " ", el("span", { class: "muted small" }, `${l.open}`))),
      ["shopping", "todo"].filter((n) => !d.lists.some((l) => l.name === n)).map((n) => el("a", { href: "#/lists/" + n, class: n === d.list ? "active" : "" },
        n === "shopping" ? tx("קניות", "shopping") : tx("משימות", "todo"))));
    const item = el("input", { type: "text", dir: "auto", maxlength: "120", placeholder: tx("פריטים, מופרדים בפסיק", "Items, comma separated"), style: "min-width:260px", "aria-label": tx("פריטים", "Items") });
    const addIt = () => act(msg, () => post("/api/lists", { list_name: d.list, items: item.value }));
    item.addEventListener("keydown", (e) => { if (e.key === "Enter") addIt(); });
    const other = el("input", { type: "text", dir: "auto", maxlength: "40", placeholder: tx("שם רשימה חדשה", "New list name"), "aria-label": tx("שם רשימה", "List name") });
    other.addEventListener("keydown", (e) => { if (e.key === "Enter" && other.value.trim()) location.hash = "#/lists/" + encodeURIComponent(other.value.trim()); });
    const items = (d.items || []).map((r) => el("div", { class: "row", style: "gap:8px;align-items:center;padding:4px 0" },
      el("input", { type: "checkbox", checked: r.done, disabled: r.done, "aria-label": r.text,
        onchange: () => act(msg, () => post("/api/lists/op", { op: "check", list_name: d.list, item: r.text })) }),
      el("span", { dir: "auto", style: r.done ? "text-decoration:line-through;opacity:.6" : "" }, r.text), el("span", { class: "spacer" }),
      el("button", { type: "button", class: "ghost", onclick: () => act(msg, () => post("/api/lists/op", { op: "remove", list_name: d.list, item: r.text })) }, tx("הסרה", "Remove"))));
    const doneN = (d.items || []).filter((r) => r.done).length, allN = (d.items || []).length;
    const clear = (onlyDone) => () => {
      const n = onlyDone ? doneN : allN;
      if (n)       // the server asks for the approval on the full popup and this request waits for the answer
        actApproved(msg, () => post("/api/lists/op", { op: "clear", list_name: d.list, only_done: onlyDone }));
    };
    render(header(tx("רשימות", "Lists"), tx("קניות ומשימות: מוסיפים, מסמנים, מסירים. ניקוי מבקש אישור.", "Shopping and to-do: add, tick off, remove. Clearing asks first.")),
      el("div", { class: "grid2" }, el("div", {}, names, el("div", { style: "margin-top:8px" }, other)),
        el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, el("b", { dir: "auto" }, d.list), el("span", { class: "spacer" }), msg,
          el("button", { type: "button", disabled: !doneN, onclick: clear(true) }, tx("ניקוי מסומנים", "Clear ticked-off")),
          el("button", { type: "button", class: "ghost", disabled: !allN, onclick: clear(false) }, tx("ניקוי הכול", "Clear all"))),
          el("div", { class: "row", style: "gap:8px;margin:10px 0" }, item, el("button", { type: "button", class: "primary", onclick: addIt }, tx("הוספה", "Add"))),
          items.length ? items : el("div", { class: "empty" }, tx("הרשימה ריקה.", "This list is empty.")))));
  }

  Object.assign(PAGES, {          // pages.js registry; the app.js router checks it first
    timers: pageTimers, lists: pageLists,
    jobs: pageJobs, reports: pageReports, notes: pageNotes, files: pageFiles, alerts: pageAlerts, autonomy: pageAutonomy });
})();
