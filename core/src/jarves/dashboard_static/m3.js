// JARVES dashboard pages for WP-M3: Jobs, Reports, Notes, Files, Alerts, Autonomy, plus Timers and Lists (V4 P0.3).
// Uses the helpers of app.js (el, api, render, header, pill, day, clock, route) and registers its pages in PAGES
// (pages.js). SW-V4 K: every text is written once per language with tx(he, en) (the old English-only texts were
// half translated by the catalog), every control has a label, tables become cards on phones (table.stack).
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
  const msgSpan = () => el("span", { class: "muted small", role: "status" });
  async function act(msg, fn) {
    try { await fn(); route(true); } catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + JL.t(e.message); }   // server errors via the catalog
  }
  // destructive: the SERVER asks on the approval popup and this request waits for the owner's answer
  const WAITING = () => tx("ממתין לאישור על המסך שלך", "waiting for approval on your screen");
  const actApproved = (msg, fn) => { msg.textContent = WAITING(); return act(msg, fn); };
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  function download(name, text) {
    const a = el("a", { href: URL.createObjectURL(new Blob([text], { type: "text/markdown" })), download: name });
    document.body.append(a); a.click(); a.remove();
  }
  const STATUS = { active: ["פעיל", "active"], done: ["הסתיים", "done"], ok: ["תקין", "ok"], delivered: ["נמסר", "delivered"],
    applied: ["הוחל", "applied"], draft: ["טיוטה", "draft"], paused: ["מושהה", "paused"], pending: ["ממתין", "pending"],
    proposed: ["הוצע", "proposed"], skipped: ["דולג", "skipped"], partial: ["חלקי", "partial"], interrupted: ["נקטע", "interrupted"],
    failed: ["נכשל", "failed"], suppressed: ["הושתק", "suppressed"], expired: ["פג תוקף", "expired"], observed: ["נצפה", "observed"],
    rejected: ["נדחה", "rejected"], reverted: ["בוטל", "reverted"], denied: ["נדחה", "denied"], revoked: ["בוטל", "revoked"],
    dismissed: ["נסגר", "dismissed"], queued: ["בתור", "queued"], running: ["רץ", "running"] };
  const stText = (s) => { const p = STATUS[s]; return p ? tx(p[0], p[1]) : String(s || "—"); };
  const statusPill = (s) => pill({ active: "ok", done: "ok", ok: "ok", delivered: "ok", applied: "ok", draft: "plain",
    paused: "plain", pending: "degraded", proposed: "degraded", skipped: "degraded", partial: "degraded",
    interrupted: "fail", failed: "fail", suppressed: "plain", expired: "plain" }[s] || "plain", stText(s));
  const RISK = { low: ["נמוך", "low"], medium: ["בינוני", "medium"], high: ["גבוה", "high"] };
  const riskText = (r) => (RISK[r] ? tx(RISK[r][0], RISK[r][1]) : r);
  const PRIO = { urgent: ["דחוף", "urgent"], high: ["גבוה", "high"], normal: ["רגיל", "normal"], low: ["נמוך", "low"] };
  const CHAN = { speech: ["דיבור", "speech"], quiet: ["חלון שקט", "quiet popup"], popup: ["חלון שקט", "quiet popup"],
    os: ["התראת Windows", "Windows notification"], log: ["יומן בלבד", "log only"], telegram: ["טלגרם", "Telegram"], none: ["כלום", "nothing"] };
  const t2 = (map, k) => (map[k] ? tx(map[k][0], map[k][1]) : String(k || "—"));
  const field = (label, control) => el("label", { class: "field" }, label, control);
  const table = (heads, rows) => el("div", { class: "tscroll" }, el("table", { class: "stack" },
    el("thead", {}, el("tr", {}, heads.map((h) => el("th", {}, h)))), el("tbody", {}, rows)));

  // ------------------------------------------------------------- Jobs
  async function pageJobs() {
    const d = await api("/api/jobs");
    const msg = msgSpan();
    const whenIn = el("input", { type: "text", placeholder: tx("כל בוקר ב-8 / בכל יום שני ב-10", "every morning at 8 / every monday at 10"),
      dir: "auto", "aria-label": tx("מתי", "When") });
    const taskIn = el("input", { type: "text", placeholder: tx("מה לעשות (שאלת מחקר או טקסט תזכורת)", "What to do (research question or reminder text)"),
      dir: "auto", "aria-label": tx("מה לעשות", "What to do") });
    const kind = el("select", { "aria-label": tx("סוג", "Kind") }, el("option", { value: "research" }, tx("דוח מחקר", "research report")),
      el("option", { value: "reminder" }, tx("תזכורת", "reminder")));
    const preview = el("div", { class: "muted small", role: "status" });
    whenIn.addEventListener("change", async () => {
      try { const p = await post("/api/schedule/parse", { when: whenIn.value });
        preview.textContent = `${schedHuman(p.kind, p.spec, p.human)} · ${tx("הבאות", "next")}: ${p.next.map(schedStamp).join(" | ")}`; }
      catch (e) { preview.textContent = e.message; }
    });
    const form = el("div", { class: "card", style: "margin-bottom:12px" }, el("h2", {}, tx("משימה חדשה (תצוגה מקדימה, אחר כך אישור)", "New job (preview first, then approve)")),
      el("div", { class: "row jobs-form" }, whenIn, taskIn, kind,
        el("button", { type: "button", class: "primary", onclick: () => act(msg, () => post("/api/jobs", { when: whenIn.value, task: taskIn.value, action: kind.value })) }, tx("תצוגה מקדימה", "Preview")), msg),
      preview);
    const ACTION = { research: ["דוח מחקר", "research report"], reminder: ["תזכורת", "reminder"], briefing: ["תדריך", "briefing"], selfcheck: ["בדיקה עצמית", "self-check"] };
    const TRIG = { schedule: ["בזמן", "on time"], catchup: ["השלמה", "catch-up"], manual: ["ידני", "manual"], run_now: ["ידני", "manual"] };
    const rows = d.jobs.map((j) => {
      const m = msgSpan();
      const op = (o) => () => act(m, () => post("/api/jobs/" + j.id, { op: o }));
      const name = biName(j.name);
      const buttons = [
        j.status === "draft" ? el("button", { type: "button", class: "primary", onclick: op("approve") }, tx("אישור", "Approve")) : null,
        j.status === "active" ? el("button", { type: "button", onclick: op("pause") }, tx("השהיה", "Pause")) : null,
        j.status === "paused" ? el("button", { type: "button", onclick: op("resume") }, tx("המשך", "Resume")) : null,
        j.status !== "draft" ? el("button", { type: "button", onclick: op("run_now") }, tx("הרצה עכשיו", "Run now")) : null,
        el("button", { type: "button", class: "ghost", "data-user-label": "", "aria-label": tx("מחיקה", "Delete") + ": " + name, onclick: () => { m.textContent = WAITING(); op("delete")(); } }, tx("מחיקה", "Delete"))];
      const runs = (j.runs || []).slice(0, 5).map((r) => el("div", { class: "small muted" },
        `${when(r.scheduled_for)} · ${t2(TRIG, r.trigger)}${r.late ? " · " + tx("באיחור", "late") : ""}${r.missed ? ` · ${r.missed} ${tx("הוחמצו", "missed")}` : ""} · `, statusPill(r.state),
        r.error ? el("span", { dir: "auto" }, " " + r.error) : ""));
      return el("tr", {}, el("td", { "data-label": "Job" }, el("b", { dir: "auto" }, name), el("div", { class: "small", dir: "auto", "data-user": "" }, j.task),
          el("div", { class: "small muted" }, `${t2(ACTION, j.action)} · ${tx("כלים", "skills")}: ${j.skills.join(", ") || tx("אין", "none")} · ` +
            tx(`עד ${j.max_requests} בקשות AI להרצה · ${j.retries} ניסיונות חוזרים · עד ${Math.round(j.max_age_s / 3600)} שעות איחור`,
              `max ${j.max_requests} AI request(s)/run · retries ${j.retries} · max age ${Math.round(j.max_age_s / 3600)} h`))),
        el("td", { "data-label": "When" }, el("div", { dir: "auto" }, schedHuman(j.kind, j.spec, j.human)), el("div", { class: "small muted mono" }, j.spec + " · " + j.tz)),
        el("td", { "data-label": "Status" }, statusPill(j.status), el("div", { class: "small" }, j.next_run_local ? tx("הבאה: ", "next: ") + schedStamp(j.next_run_local) : (j.upcoming_local || []).map(schedStamp).join(" | "))),
        el("td", { "data-label": "Last run" }, j.last_outcome ? statusPill(j.last_outcome) : "—", el("div", { class: "small muted" }, schedStamp(j.last_run_local) || ""), runs),
        el("td", {}, el("div", { class: "row" }, buttons), m));
    });
    const tzIn = el("input", { type: "text", value: d.timezone, dir: "ltr", style: "width:170px", "aria-label": tx("אזור זמן ברירת מחדל", "Default time zone") });
    const tzMsg = msgSpan();
    const tzSave = el("button", { type: "button", onclick: () => act(tzMsg, () => post("/api/scheduler/settings", { timezone: tzIn.value })) }, tx("קביעת אזור", "Set zone"));
    // WP-L: the two other scheduler settings the guide explains (missed-job max age, what "workdays" means)
    const ageIn = el("input", { type: "number", min: "0", max: "168", step: "1", value: String(Math.round((d.max_age_s || 21600) / 3600)), style: "width:80px" });
    const wwIn = el("select", {}, el("option", { value: "0-4" }, tx("ימי חול = א׳-ה׳", "Workdays = Sun-Thu")), el("option", { value: "1-5" }, tx("ימי חול = ב׳-ו׳", "Workdays = Mon-Fri")));
    wwIn.value = d.workweek || "0-4";
    const setSave = el("button", { type: "button", onclick: () => act(tzMsg, () => post("/api/scheduler/settings", { max_age_s: Number(ageIn.value) * 3600, workweek: wwIn.value })) }, tx("שמירה", "Save"));
    const settings = el("div", { class: "card small", style: "margin-bottom:12px" }, el("h2", {}, tx("הגדרות", "Settings")),
      el("div", { class: "filters" }, field(tx("משימה שהוחמצה (המחשב כבוי) רצה אם היא לא ישנה יותר מ-(שעות)", "A missed job (computer off) still runs if not older than (hours)"), ageIn),
        field(tx("ימי חול", "Workdays"), wwIn), setSave, el("a", { href: "#/guide/scheduler" }, tx("מדריך", "Guide"))));
    render(header(tx("משימות קבועות", "Scheduled jobs"), JL.lang === "he" ? `תזכורות ודוחות מתוזמנים. עכשיו ${d.now_local}. הליבה מריצה משימות שהגיע זמנן (בדיקה לפחות כל 30 שניות); משימה שהוחמצה רצה פעם אחת אחרי ההפעלה אם היא לא ישנה מדי, ומסומנת כמאוחרת.` : `Scheduled reminders and reports. Now ${schedStamp(d.now_local)}. ${d.note}`, tzIn, tzSave, tzMsg), settings, form,
      el("div", { class: "card" }, rows.length ? table([tx("משימה", "Job"), tx("מתי", "When"), tx("מצב", "Status"), tx("הרצה אחרונה", "Last run"), ""], rows)
        : el("div", { class: "empty" }, tx("עוד אין משימות קבועות.", "No jobs yet."))));
  }

  // ------------------------------------------------------------- Reports
  async function pageReports(id) {
    const d = await api("/api/reports?period=this_week");
    const list = el("div", { class: "card list" }, d.reports.length ? d.reports.map((r) => el("a", { href: "#/reports/" + r.id, class: String(r.id) === id ? "active" : "" },
      el("span", { dir: "auto" }, r.title), " ", statusPill(r.status), r.late ? pill("degraded", tx("באיחור", "late")) : null))
      : el("div", { class: "empty" }, tx("אין דוחות השבוע.", "No reports this week.")));
    const body = el("div", { class: "card" }, el("div", { class: "muted" }, id ? tx("טוען…", "Loading…") : tx("בחרו דוח.", "Pick a report.")));
    render(header(tx("דוחות", "Reports"), tx("תוצאות שמורות של משימות קבועות. \"הקראה\" מקריאה את הטקסט השמור; שום דבר לא נוצר מחדש.",
      "Saved results of scheduled jobs. 'Read aloud' reads the saved text; nothing is regenerated.")), el("div", { class: "grid2" }, list, body));
    if (!id) return;
    const r = await api("/api/reports/" + id);
    const m = msgSpan();
    body.replaceChildren(el("div", { class: "row" }, el("b", { dir: "auto" }, r.title), el("span", { class: "spacer" }), m,
        el("button", { type: "button", class: "primary", onclick: () => act(m, async () => { const x = await post(`/api/reports/${r.id}`, { op: "speak" }); m.textContent = x.note; }) }, tx("הקראה", "Read aloud")),
        el("button", { type: "button", class: "ghost", onclick: () => actApproved(m, () => post(`/api/reports/${r.id}`, { op: "delete" })) }, tx("מחיקה", "Delete"))),
      el("div", { class: "small muted" }, `${schedStamp(r.created_local)} · ${stText(r.status)}${r.late ? " · " + tx("רץ באיחור (השלמה)", "ran late (catch-up)") : ""}${r.read_at ? " · " + tx("הוקרא ", "read ") + when(r.read_at) : ""}`),
      el("pre", { class: "file", dir: "auto" }, r.text),
      r.sources.length ? el("div", { class: "small" }, tx("מקורות: ", "Sources: "), r.sources.map((s) => s.query ? `${tx("חיפוש", "search")} "${s.query}"` : s.name).join(" · ")) : null);
  }

  // ------------------------------------------------------------- Notes
  async function pageNotes(arg) {
    const q = el("input", { type: "search", placeholder: tx("חיפוש בפתקים", "Search notes"), "aria-label": tx("חיפוש בפתקים", "Search notes"), dir: "auto", value: arg || "" });
    const d = await api("/api/notes?q=" + encodeURIComponent(arg || ""));
    q.addEventListener("change", () => { location.hash = "#/notes/" + encodeURIComponent(q.value); });
    const title = el("input", { type: "text", placeholder: tx("כותרת", "Title"), "aria-label": tx("כותרת", "Title"), dir: "auto" });
    const tags = el("input", { type: "text", placeholder: tx("תגיות, מופרדות בפסיק", "tags, comma separated"), "aria-label": tx("תגיות", "Tags"), dir: "auto" });
    const text = el("textarea", { class: "file", dir: "auto", style: "min-height:80px", "aria-label": tx("טקסט הפתק", "Note text") });
    const msg = msgSpan();
    const add = el("div", { class: "card", style: "margin-bottom:12px" }, el("h2", {}, tx("פתק חדש", "New note")),
      el("div", { class: "row", style: "gap:8px;margin:8px 0" }, title, tags), text,
      el("div", { class: "row", style: "margin-top:8px" }, el("button", { type: "button", class: "primary", onclick: () => act(msg, () => post("/api/notes", { title: title.value, text: text.value, tags: tags.value })) }, tx("שמירה", "Save")), msg));
    const cards = d.notes.map((n) => {
      const m = msgSpan();
      const ta = el("textarea", { class: "file", dir: "auto", style: "min-height:60px", "data-user-label": "", "aria-label": tx("טקסט הפתק", "Note text") + ": " + n.title }); ta.value = n.text;
      return el("div", { class: "card", style: "margin-bottom:10px" },
        el("div", { class: "row" }, el("b", { dir: "auto" }, n.title), " ", n.tags.map((t) => pill("plain", "#" + t)), el("span", { class: "spacer" }),
          el("span", { class: "small muted" }, `${tx("נוצר", "created")} ${when(n.created)} · ${tx("עודכן", "updated")} ${when(n.updated)}`)), ta,
        el("div", { class: "row", style: "margin-top:8px" }, el("button", { type: "button", onclick: () => act(m, () => post("/api/notes/" + n.id, { op: "edit", text: ta.value })) }, tx("שמירה", "Save")),
          el("button", { type: "button", onclick: async () => { const x = await post("/api/notes/" + n.id, { op: "export" }); download(x.filename, x.markdown); } }, tx("ייצוא ‎.md", "Export .md")),
          el("button", { type: "button", class: "ghost", onclick: () => actApproved(m, () => post("/api/notes/" + n.id, { op: "delete" })) }, tx("מחיקה", "Delete")), m));
    });
    render(header(tx("פתקים", "Notes"), tx("הפתקים שלך: חיפוש, עריכה וייצוא ל-Markdown.", "Your notes: searchable, editable, exportable as Markdown."), q,
      el("button", { type: "button", onclick: async () => { const x = await api("/api/notes/export"); download(x.filename, x.markdown); } }, tx("ייצוא הכול", "Export all"))),
      add, cards.length ? cards : el("div", { class: "card empty" }, tx("אין פתקים.", "No notes.")));
  }

  // ------------------------------------------------------------- Files
  async function pageFiles(path) {
    const d = await api("/api/files?path=" + encodeURIComponent(path || ""));
    const viewer = el("div", { class: "card" }, el("div", { class: "muted" }, tx("בחרו קובץ.", "Pick a file.")));
    const items = d.items.map((i) => el("tr", {}, el("td", {}, i.link ? el("span", { class: "muted" }, i.name + tx(" (קישור: לא נפתח)", " (link: not followed)")) : i.dir
      ? el("a", { href: "#/files/" + encodeURIComponent(i.path), dir: "auto" }, i.name + "/")
      : el("a", { href: "#", dir: "auto", onclick: async (ev) => { ev.preventDefault(); const f = await api("/api/files/read?path=" + encodeURIComponent(i.path));
          viewer.replaceChildren(el("div", { class: "row" }, el("b", { class: "mono" }, f.path), el("span", { class: "spacer" }),
            el("button", { type: "button", onclick: () => download(i.name, f.text) }, tx("הורדה", "Download"))), el("pre", { class: "file", dir: "auto" }, f.text)); } }, i.name)),
      el("td", { class: "num small" }, i.dir ? "" : i.bytes + " B"), el("td", { class: "small muted" }, when(i.modified))));
    const gpath = el("input", { type: "text", dir: "ltr", placeholder: "C:\\Users\\you\\Documents\\Shared", "aria-label": tx("נתיב התיקייה", "Folder path"), style: "min-width:280px" });
    const gmode = el("select", { "aria-label": tx("הרשאה", "Access") }, el("option", { value: "read" }, tx("קריאה בלבד", "read only")), el("option", { value: "readwrite" }, tx("קריאה וכתיבה", "read + write")));
    const gm = msgSpan();
    const MODE = { read: ["קריאה בלבד", "read only"], readwrite: ["קריאה וכתיבה", "read + write"] };
    const grants = d.grants.map((g) => {
      const m = msgSpan();
      const op = (o) => () => act(m, () => post("/api/files/grants/" + g.id, { op: o }));
      return el("tr", {}, el("td", { "data-label": "Grant" }, el("code", {}, `grant:${g.id}`)), el("td", { class: "small mono", "data-label": "Folder" }, g.path),
        el("td", { "data-label": "Access" }, t2(MODE, g.mode)), el("td", { "data-label": "Status" }, statusPill(g.status)),
        el("td", { class: "small muted", dir: "auto", "data-label": "Asked by" }, `${g.requested_by || ""} ${g.reason || ""}`),
        el("td", {}, g.status === "pending" ? [el("button", { type: "button", class: "primary", onclick: op("approve") }, tx("אישור", "Approve")), el("button", { type: "button", onclick: op("deny") }, tx("דחייה", "Deny"))]
          : g.status === "active" ? el("button", { type: "button", onclick: op("revoke") }, tx("ביטול ההרשאה", "Revoke")) : null, m));
    });
    const used = `${(d.usage_bytes / 1048576).toFixed(1)} / ${d.quota_mb} MB`;
    render(header(tx("הקבצים שלי", "My files"), tx(`סביבת העבודה הפרטית של JARVES; ${used} בשימוש. שום דבר כאן לא מורץ אף פעם.`,
      `JARVES's private workspace; ${used} used. Nothing here is ever run.`)),
      el("div", { class: "muted small mono", style: "margin:-12px 0 12px" }, d.root),
      el("div", { class: "grid2" }, el("div", { class: "card" }, el("b", { class: "mono" }, "/" + (d.path === "." ? "" : d.path)),
        el("table", { style: "margin-top:8px" }, el("tbody", {}, path ? el("tr", {}, el("td", {}, el("a", { href: "#/files" }, tx("↑ לראש", "↑ top")))) : null, items))), viewer),
      el("h2", {}, tx("תיקיות מורשות", "Folder grants")),
      el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;margin-bottom:8px" }, gpath, gmode,
        el("button", { type: "button", class: "primary", onclick: () => { if (confirm(tx(`לאפשר ל-JARVES ${gmode.value === "read" ? "לקרוא" : "לקרוא ולכתוב"} ב-${gpath.value}?`,
          `Let JARVES ${gmode.value === "read" ? "read" : "read and write"} ${gpath.value}?`))) act(gm, () => post("/api/files/grants", { path: gpath.value, mode: gmode.value })); } }, tx("מתן גישה לתיקייה", "Grant folder")), gm),
        grants.length ? table([tx("מזהה", "Grant"), tx("תיקייה", "Folder"), tx("גישה", "Access"), tx("מצב", "Status"), tx("ביקש", "Asked by"), ""], grants)
          : el("div", { class: "empty" }, tx("אין תיקיות מורשות. JARVES יכול רק לבקש; אתם מאשרים כאן.", "No folders granted. JARVES can only ask; you approve here."))));
  }

  // ------------------------------------------------------------- Alerts
  async function pageAlerts() {
    const d = await api("/api/alerts");
    const s = d.settings;
    const qs = el("input", { type: "time", value: s["alerts.quiet_start"], "aria-label": tx("תחילת שעות שקט", "Quiet hours start") });
    const qe = el("input", { type: "time", value: s["alerts.quiet_end"], "aria-label": tx("סוף שעות שקט", "Quiet hours end") });
    const qon = el("input", { type: "checkbox" }); qon.checked = s["alerts.quiet_enabled"];
    const urg = el("input", { type: "checkbox" }); urg.checked = s["alerts.urgent_in_quiet"];
    const mph = el("input", { type: "number", min: 1, max: 60, value: s["alerts.max_per_hour"], style: "width:70px" });
    const sel = {};
    const deliv = d.priorities.map((p) => { sel[p] = el("select", {}, d.channels.map((c) => el("option", { value: c, selected: s["alerts.delivery"][p] === c }, t2(CHAN, c))));
      return field(t2(PRIO, p), sel[p]); });
    const msg = msgSpan();
    const save = () => act(msg, () => post("/api/alerts/settings", { quiet_start: qs.value, quiet_end: qe.value, quiet_enabled: qon.checked,
      urgent_in_quiet: urg.checked, max_per_hour: Number(mph.value), delivery: Object.fromEntries(d.priorities.map((p) => [p, sel[p].value])) }));
    const list = (rows) => rows.length ? table([tx("מתי", "When"), tx("עדיפות", "Priority"), tx("מה", "What"), tx("מצב", "Status")],
      rows.map((a) => el("tr", {}, el("td", { class: "small muted nowrap", "data-label": "When" }, when(a.ts)),
        el("td", { "data-label": "Priority" }, pill(a.priority === "urgent" || a.priority === "high" ? "fail" : "plain", t2(PRIO, a.priority))),
        el("td", { dir: "auto", "data-label": "What" }, el("b", {}, txh(a, "title")), el("div", { class: "small" }, txh(a, "text").slice(0, 300))),
        el("td", { "data-label": "Status" }, statusPill(a.status), el("div", { class: "small muted" }, t2(CHAN, a.delivered_via || a.channel) + (a.reason ? " · " + a.reason : ""))))))
      : el("div", { class: "empty" }, tx("אין.", "None."));
    render(header(tx("התראות", "Alerts"), tx(`התראות יזומות: ${d.paused ? "מושהות" : "פועלות"}${d.quiet_now ? " · עכשיו שעות שקט" : ""}. חלון שקט = שורה שקטה על הפנים; דיבור = נאמר בקול.`,
      `Proactive alerts: ${d.paused ? "PAUSED" : "on"}${d.quiet_now ? " · quiet hours now" : ""}. Quiet popup = a silent line on the face; speech = said aloud.`),
      el("button", { type: "button", onclick: () => act(msg, () => post(d.paused ? "/api/alerts/resume" : "/api/alerts/pause", { minutes: 60 })) }, d.paused ? tx("המשך", "Resume") : tx("השהיה לשעה", "Pause 1 h")),
      el("button", { type: "button", onclick: () => act(msg, () => post("/api/alerts/test", {})) }, tx("התראת ניסיון", "Test alert"))),
      el("div", { class: "muted small", style: "margin:-12px 0 12px" }, txh(d, "os_notifications")),
      el("div", { class: "card", style: "margin-bottom:12px" }, el("h2", {}, tx("הגדרות", "Settings")),
        el("div", { class: "filters" }, el("label", { class: "check" }, qon, tx("שעות שקט", "quiet hours")), field(tx("מ-", "from"), qs), field(tx("עד", "to"), qe),
          el("label", { class: "check" }, urg, tx("דחוף מותר לדבר בשעות שקט", "urgent may speak in quiet hours")), field(tx("מקסימום לשעה", "max per hour"), mph)),
        el("div", { class: "small muted", style: "margin:4px 0" }, tx("איך כל עדיפות נמסרת:", "How each priority is delivered:")),
        el("div", { class: "filters" }, deliv), el("div", { class: "row" }, el("button", { type: "button", class: "primary", onclick: save }, tx("שמירה", "Save")), msg)),
      el("h2", {}, tx(`ממתינות (${d.pending.length})`, `Pending (${d.pending.length})`)), el("div", { class: "card" }, list(d.pending),
        d.pending.length ? el("button", { type: "button", onclick: () => act(msg, () => post("/api/alerts/dismiss_all", {})) }, tx("סגירת כולן", "Dismiss all")) : null),
      el("h2", {}, tx("אחרונות", "Recent")), el("div", { class: "card" }, list(d.recent)));
  }

  // ------------------------------------------------------------- Autonomy
  async function pageAutonomy() {
    const d = await api("/api/autonomy");
    const msg = msgSpan();
    const LEVEL = { suggest_and_ask: ["מציע ושואל (ברירת מחדל)", "suggest and ask (default)"], approved_low_risk: ["מחיל לבד רק שינויים בסיכון נמוך שסימנתם", "apply the low-risk changes you tick"],
      observe_only: ["רק רושם", "observe only"] };
    const level = el("select", { "aria-label": tx("רמת עצמאות", "Autonomy level") }, d.levels.map((l) => el("option", { value: l, selected: l === d.level }, t2(LEVEL, l))));
    const grants = d.appliers.filter((a) => a.risk === "low").map((a) => { const c = el("input", { type: "checkbox", value: a.id }); c.checked = d.grants.includes(a.id);
      return el("label", { class: "check" }, c, el("span", {}, el("span", { class: "mono" }, a.id), ": ", txh(a, "about"))); });
    const rows = d.proposals.map((p) => {
      const m = msgSpan();
      const op = (o) => () => act(m, () => post("/api/proposals/" + p.id, { op: o }));
      return el("tr", {}, el("td", { "data-label": "Proposal" }, el("b", { dir: "auto" }, txh(p, "title")), el("div", { class: "small", dir: "auto" }, txh(p, "detail")), p.result ? el("div", { class: "small muted", dir: "auto" }, p.result) : null),
        el("td", { "data-label": "Risk" }, pill(p.risk === "low" ? "ok" : "degraded", riskText(p.risk))), el("td", { "data-label": "Status" }, statusPill(p.status), el("div", { class: "small muted" }, p.decided_by || "")),
        el("td", {}, ["proposed", "observed", "failed"].includes(p.status) ? [el("button", { type: "button", class: "primary", onclick: op("approve") }, tx("אישור", "Approve")), el("button", { type: "button", onclick: op("reject") }, tx("דחייה", "Reject"))]
          : p.status === "applied" && p.revert ? el("button", { type: "button", onclick: op("revert") }, tx("ביטול השינוי", "Revert")) : null, m));
    });
    render(header(tx("עצמאות", "Autonomy"), tx("האבחון מציע שינויי כיול. \"מציע ושואל\" תמיד שואל; \"סיכון נמוך\" מחיל רק את השינויים שסימנתם; \"רק רושם\" לא משנה כלום.",
      "Diagnostics propose calibration changes. 'Suggest and ask' always asks; 'low risk' applies only the low-risk changes you tick; 'observe only' just records."),
      el("button", { type: "button", onclick: () => act(msg, () => post("/api/autonomy/sync", {})) }, tx("בדיקת אבחון עכשיו", "Check diagnostics now"))),
      el("div", { class: "card", style: "margin-bottom:12px" }, el("div", { class: "filters" }, field(tx("רמה", "Level"), level)),
        el("div", { style: "margin:8px 0" }, el("div", { class: "small muted" }, tx("מותר אוטומטית (רק ברמת \"סיכון נמוך\"):", "Allowed automatically (low-risk level only):")), grants),
        el("button", { type: "button", class: "primary", onclick: () => act(msg, () => post("/api/autonomy", { level: level.value,
          grants: [...document.querySelectorAll("#main input[type=checkbox]:checked")].map((c) => c.value) })) }, tx("שמירה", "Save")), msg),
      el("h2", {}, tx("הצעות", "Proposals")), el("div", { class: "card" }, rows.length ? table([tx("הצעה", "Proposal"), tx("סיכון", "Risk"), tx("מצב", "Status"), ""], rows)
        : el("div", { class: "empty" }, tx("אין הצעות.", "No proposals."))),
      el("h2", {}, tx("יומן ביקורת", "Audit log")), el("div", { class: "card" }, d.audit.length ? table([tx("מתי", "When"), tx("מי", "Who"), tx("פעולה", "Action"), tx("פרטים", "Details")],
        d.audit.slice(0, 50).map((a) => el("tr", {}, el("td", { class: "small muted nowrap", "data-label": "When" }, when(a.ts)), el("td", { "data-label": "Who" }, a.actor),
          el("td", { class: "mono small", "data-label": "Action" }, a.action), el("td", { class: "small mono", "data-label": "Details" }, `${a.target || ""} ${a.detail || ""}`))))
        : el("div", { class: "empty" }, tx("עוד אין רשומות.", "Nothing recorded yet."))));
  }

  // ------------------------------------------------------------- Timers & alarms (V4 P0.3)
  const leftText = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(r).padStart(2, "0"); };
  async function pageTimers() {
    const d = await api("/api/timers");
    const msg = msgSpan();
    const kind = el("select", { "aria-label": tx("סוג", "Kind") }, el("option", { value: "timer" }, tx("טיימר", "Timer")), el("option", { value: "alarm" }, tx("שעון מעורר", "Alarm")));
    const mins = el("input", { type: "number", min: "0", max: "1440", step: "any", placeholder: tx("דקות", "Minutes"), style: "width:110px", "aria-label": tx("דקות", "Minutes") });
    const at = el("input", { type: "time", "aria-label": tx("שעה", "Time") });
    const label = el("input", { type: "text", dir: "auto", maxlength: "80", placeholder: tx("תווית (לא חובה)", "Label (optional)"), style: "min-width:200px", "aria-label": tx("תווית", "Label") });
    const sync = () => { mins.hidden = kind.value === "alarm"; at.hidden = kind.value !== "alarm"; };
    kind.addEventListener("change", sync); sync();
    const send = () => act(msg, () => post("/api/timers", kind.value === "alarm"
      ? { kind: "alarm", at: at.value, label: label.value } : { kind: "timer", minutes: parseFloat(mins.value || "0"), label: label.value }));
    const add = el("div", { class: "card", style: "margin-bottom:12px" }, el("h2", {}, tx("טיימר או שעון מעורר חדש", "New timer or alarm")),
      el("div", { class: "row", style: "gap:8px;margin:8px 0;flex-wrap:wrap" }, kind, mins, at, label,
        el("button", { type: "button", class: "primary", onclick: send }, tx("התחל", "Start")), msg));
    const cells = [];
    const opBtn = (t, op, text, extra, cls) => el("button", { type: "button", class: cls || "ghost", "data-user-label": "", "aria-label": `${text}: ${t.label || (t.kind === "alarm" ? tx("שעון מעורר", "Alarm") : tx("טיימר", "Timer"))}`,
      onclick: () => act(msg, () => post("/api/timers/" + t.id, { op, ...(extra || {}) })) }, text);
    const editRow = (t) => {
      const lab = el("input", { type: "text", dir: "auto", maxlength: "80", value: t.label || "", "aria-label": tx("תווית", "Label") });
      const val = t.kind === "alarm" ? el("input", { type: "time", value: (t.at || "").slice(11, 16), "aria-label": tx("שעה", "Time") })
        : el("input", { type: "number", min: "0", max: "1440", step: "any", style: "width:90px", placeholder: tx("דקות", "Minutes"), "aria-label": tx("דקות", "Minutes") });
      return el("div", { class: "row small", style: "gap:6px;margin-top:6px" }, lab, val,
        el("button", { type: "button", onclick: () => act(msg, () => post("/api/timers/" + t.id, t.kind === "alarm"
          ? { op: "edit", label: lab.value, at: val.value } : { op: "edit", label: lab.value, minutes: val.value === "" ? null : parseFloat(val.value) })) }, tx("שמירה", "Save")));
    };
    const rows = d.timers.map((t) => {
      const ringing = t.state === "ringing";
      const left = el("td", { class: "num", "data-left": String(t.seconds_left), "data-label": "Left" }, ringing ? tx("מצלצל עכשיו", "Ringing now") : leftText(t.seconds_left));
      if (!ringing) cells.push(left);
      const edit = el("details", { class: "timer-edit" }, el("summary", {}, tx("עריכה", "Edit")), editRow(t));
      return el("tr", { class: ringing ? "ringing" : "" }, el("td", { "data-label": "Kind" }, pill(ringing ? "live" : "plain", t.kind === "alarm" ? tx("שעון מעורר", "Alarm") : tx("טיימר", "Timer"))),
        el("td", { dir: "auto", "data-label": "Label" }, t.label || "—"), el("td", { class: "small muted", "data-label": "At" }, t.kind === "alarm" ? (t.at || "").slice(11, 16) : ""), left,
        el("td", {}, el("div", { class: "row" }, opBtn(t, "snooze", tx("עוד 5 דקות", "Snooze 5 min"), { minutes: 5 }), opBtn(t, "cancel", tx("ביטול", "Cancel"))), edit));
    });
    const stText2 = { fired: tx("צלצל", "rang"), cancelled: tx("בוטל", "cancelled"), missed: tx("הוחמץ", "missed") };
    const recent = d.recent.map((r) => el("tr", {}, el("td", { "data-label": "Kind" }, r.kind === "alarm" ? tx("שעון מעורר", "Alarm") : tx("טיימר", "Timer")),
      el("td", { dir: "auto", "data-label": "Label" }, r.label || "—"), el("td", { class: "small muted", "data-label": "Status" }, stText2[r.status] || r.status),
      el("td", { class: "small muted", "data-label": "When" }, when(r.due)),
      el("td", {}, r.status === "fired" || r.status === "missed" ? opBtn(r, "snooze", tx("שוב בעוד 5 דקות", "Again in 5 min"), { minutes: 5 }) : null)));
    render(header(tx("טיימרים ושעונים מעוררים", "Timers & alarms"), tx("הליבה מצלצלת בקול, גם כשהחלון סגור.", "The Core rings them aloud, even when the window is closed.")),
      add, el("div", { class: "card" }, rows.length ? table([tx("סוג", "Kind"), tx("תווית", "Label"), tx("שעה", "Time"), tx("נשאר", "Left"), ""], rows)
        : el("div", { class: "empty" }, tx("אין טיימרים פעילים.", "No timers running."), el("div", { class: "small" }, tx("אפשר גם לבקש בקול: “טיימר לעשר דקות”.", "You can also ask aloud: “a ten minute timer”.")))),
      d.recent.length ? [el("h2", {}, tx("אחרונים", "Recent")), el("div", { class: "card" }, table([tx("סוג", "Kind"), tx("תווית", "Label"), tx("מצב", "Status"), tx("מתי", "When"), ""], recent))] : null,
      el("div", { class: "muted small", style: "margin-top:8px" }, tx("שינוי כאן נכנס לתוקף תוך שנייה.", "A change here applies within a second.")));
    const t0 = Date.now();
    timer = setInterval(() => {
      if (document.activeElement && document.activeElement.closest && document.activeElement.closest(".timer-edit")) return;   // editing
      const gone = (Date.now() - t0) / 1000;
      let done = false;
      for (const c of cells) { const s = Number(c.dataset.left) - gone; c.textContent = s > 0 ? leftText(s) : tx("מצלצל עכשיו", "Ringing now"); if (s <= -2) done = true; }
      if (done) route(true);
    }, 1000);
  }

  // ------------------------------------------------------------- Lists (V4 P0.3)
  async function pageLists(arg) {
    const all = await api("/api/lists");
    const name = arg || (all.lists[0] && all.lists[0].name) || "shopping";
    const d = await api("/api/lists?name=" + encodeURIComponent(name));
    const msg = msgSpan();
    const shown = (n) => n === "shopping" ? tx("קניות", "shopping") : n === "todo" ? tx("משימות", "todo") : n === "packing" ? tx("אריזה", "packing") : n;
    const names = el("nav", { class: "card list", "aria-label": tx("רשימות", "Lists") }, d.lists.map((l) => el("a", { href: "#/lists/" + encodeURIComponent(l.name), class: l.name === d.list ? "active" : "",
      "aria-current": l.name === d.list ? "page" : null },
      el("span", { dir: "auto" }, shown(l.name)), " ", el("span", { class: "muted small" }, tx(`${l.open} פתוחים`, `${l.open} open`)))),
      ["shopping", "todo"].filter((n) => !d.lists.some((l) => l.name === n)).map((n) => el("a", { href: "#/lists/" + n, class: n === d.list ? "active" : "" }, shown(n))));
    const item = el("input", { type: "text", dir: "auto", maxlength: "120", placeholder: tx("פריטים, מופרדים בפסיק", "Items, comma separated"), style: "min-width:220px;flex:1", "aria-label": tx("פריטים", "Items") });
    const addIt = () => act(msg, () => post("/api/lists", { list_name: d.list, items: item.value }));
    item.addEventListener("keydown", (e) => { if (e.key === "Enter") addIt(); });
    const other = el("input", { type: "text", dir: "auto", maxlength: "40", placeholder: tx("שם רשימה חדשה", "New list name"), "aria-label": tx("שם רשימה", "List name") });
    other.addEventListener("keydown", (e) => { if (e.key === "Enter" && other.value.trim()) location.hash = "#/lists/" + encodeURIComponent(other.value.trim()); });
    const items = (d.items || []).map((r) => el("div", { class: "row list-item" + (r.done ? " done" : "") },
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: r.done, disabled: r.done,
        onchange: () => act(msg, () => post("/api/lists/op", { op: "check", list_name: d.list, item: r.text })) }),
      el("span", { dir: "auto" }, r.text)), el("span", { class: "spacer" }),
      el("button", { type: "button", class: "ghost", "data-user-label": "", "aria-label": tx("הסרה", "Remove") + ": " + r.text, onclick: () => act(msg, () => post("/api/lists/op", { op: "remove", list_name: d.list, item: r.text })) }, tx("הסרה", "Remove"))));
    const doneN = (d.items || []).filter((r) => r.done).length, allN = (d.items || []).length;
    const clear = (onlyDone) => () => {
      const n = onlyDone ? doneN : allN;
      if (n)       // the server asks for the approval on the full popup and this request waits for the answer
        actApproved(msg, () => post("/api/lists/op", { op: "clear", list_name: d.list, only_done: onlyDone }));
    };
    render(header(tx("רשימות", "Lists"), tx("קניות ומשימות: מוסיפים, מסמנים, מסירים. ניקוי מבקש אישור.", "Shopping and to-do: add, tick off, remove. Clearing asks first.")),
      el("div", { class: "grid2" }, el("div", {}, names, el("div", { style: "margin-top:8px" }, other)),
        el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, el("h2", { dir: "auto", style: "margin:0" }, shown(d.list)), el("span", { class: "spacer" }), msg,
          el("button", { type: "button", disabled: !doneN, onclick: clear(true) }, tx("ניקוי מסומנים", "Clear ticked-off")),
          el("button", { type: "button", class: "ghost", disabled: !allN, onclick: clear(false) }, tx("ניקוי הכול", "Clear all"))),
          el("div", { class: "row", style: "gap:8px;margin:10px 0" }, item, el("button", { type: "button", class: "primary", onclick: addIt }, tx("הוספה", "Add"))),
          items.length ? items : el("div", { class: "empty" }, tx("הרשימה ריקה.", "This list is empty.")))));
  }

  Object.assign(PAGES, {          // pages.js registry; the app.js router checks it first
    timers: pageTimers, lists: pageLists,
    jobs: pageJobs, reports: pageReports, notes: pageNotes, files: pageFiles, alerts: pageAlerts, autonomy: pageAutonomy });
})();
