// JARVES dashboard: Health & speed page (self-test and speed test, owner request 21 Oct 2026). Uses app.js helpers
// (el, api, render, header, pill, day, clock, route) and registers PAGES.speed. Text goes in via textContent only.
// Both tests are read-only; "Run now" starts one and the page reloads with the new result. Sparklines are inline SVG.
"use strict";

(function () {
  const when = (ts) => ts ? `${day(ts)} ${clock(ts)}` : "—";
  const cls = { pass: "ok", warn: "degraded", fail: "fail", skip: "plain", "n/a": "plain" };
  const stTx = { pass: ["תקין", "OK"], warn: ["אזהרה", "Warning"], fail: ["נכשל", "Failed"], skip: ["דולג", "Skipped"], "n/a": ["אין נתון", "No data"] };
  const t2 = (pair) => pair ? tx(pair[0], pair[1]) : "";
  const SVG = "http://www.w3.org/2000/svg";

  function spark(values, status) {
    const w = 90, h = 22, svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("width", w); svg.setAttribute("height", h); svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("role", "img"); svg.setAttribute("class", "spark spark-" + (cls[status] || "plain"));
    if (values.length < 2) return svg;
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.map((v, i) => `${(i / (values.length - 1) * (w - 4) + 2).toFixed(1)},${(h - 3 - (v - lo) / span * (h - 6)).toFixed(1)}`);
    const line = document.createElementNS(SVG, "polyline");
    line.setAttribute("points", pts.join(" ")); line.setAttribute("fill", "none");
    line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-width", "1.5");
    const last = pts[pts.length - 1].split(","), dot = document.createElementNS(SVG, "circle");
    dot.setAttribute("cx", last[0]); dot.setAttribute("cy", last[1]); dot.setAttribute("r", "2.2"); dot.setAttribute("fill", "currentColor");
    svg.append(line, dot);
    return svg;
  }

  function strip(rows) {
    return el("span", { class: "row", role: "img", "aria-label": tx("היסטוריית בדיקות עצמיות", "Self-test history") },
      rows.map((r) => el("span", { class: "pill " + (cls[r.status] || "plain"), title: when(r.ts) }, " ")));
  }

  function card(title, ...kids) { return el("div", { class: "card" }, el("h2", {}, title), ...kids); }

  async function runNow(kind, deep, msg) {
    msg.textContent = tx("רץ עכשיו…", "Running…");
    try { await api("/api/selfspeed/run", { method: "POST", body: JSON.stringify({ kind, deep: !!deep }) }); route(true); }
    catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
  }

  function selfCard(d, msg) {
    const r = d.latest.selftest;
    const buttons = el("div", { class: "row" },
      el("button", { type: "button", class: "primary", onclick: () => runNow("selftest", false, msg) }, tx("הרץ עכשיו", "Run now")),
      el("button", { type: "button", class: "ghost", title: tx("מפעיל ליבה זמנית, טוען את מודל מילת ההפעלה ובודק התקני שמע (בלי להקליט ובלי לדבר)", "Starts a temporary Core, loads the wake model and checks audio devices (nothing recorded or spoken)"),
        onclick: () => runNow("selftest", true, msg) }, tx("הרץ בדיקה מעמיקה", "Run deep test")));
    if (!r) return card(tx("בדיקה עצמית", "Self-test"), el("div", { class: "muted small" }, tx("עוד לא רצה בדיקה עצמית.", "No self-test yet.")), buttons);
    return card(tx("בדיקה עצמית", "Self-test"),
      el("div", { class: "row" }, pill(cls[r.status], t2(stTx[r.status])), el("span", { dir: "auto" }, tx(d.summary_he.selftest, d.summary.selftest)),
        el("span", { class: "muted small" }, `${when(r.ts)} · ${Math.round(r.ms)} ms`)),
      strip(d.selftest_spark), buttons,
      el("table", {}, el("tbody", {}, r.checks.map((c) => el("tr", {}, el("td", {}, pill(cls[c.status], t2(stTx[c.status]))),
        el("td", {}, tx(c.title_he, c.title)), el("td", { dir: "auto", class: "small" }, c.detail),
        el("td", { dir: "auto", class: "small muted" }, c.status === "warn" || c.status === "fail" ? c.fix : ""))))));
  }

  function speedCard(d, msg) {
    const r = d.latest.speedtest;
    const button = el("div", { class: "row" },
      el("button", { type: "button", class: "primary", onclick: () => runNow("speedtest", false, msg) }, tx("הרץ עכשיו", "Run now")));
    if (!r) return card(tx("בדיקת מהירות", "Speed test"), el("div", { class: "muted small" }, tx("עוד לא רצה בדיקת מהירות.", "No speed test yet.")), button);
    return card(tx("בדיקת מהירות", "Speed test"),
      el("div", { class: "row" }, pill(cls[r.status], t2(stTx[r.status])), el("span", { dir: "auto" }, tx(d.summary_he.speedtest, d.summary.speedtest)),
        el("span", { class: "muted small" }, `${when(r.ts)} · ${Math.round(r.ms / 1000)} s · ${r.class}`)),
      button,
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, tx("מדד", "Metric")), el("th", {}, tx("ערך", "Value")), el("th", {}, tx("תקציב", "Budget")),
        el("th", {}, tx("מול 5 קודמות", "vs last 5")), el("th", {}, tx("מגמה", "Trend")), el("th", {}, tx("מצב", "Status")))),
        el("tbody", {}, r.metrics.map((m) => el("tr", {}, el("td", { dir: "ltr" }, m.name),
          el("td", { dir: "ltr" }, m.value === null ? "—" : `${m.value} ${m.unit}`), el("td", { dir: "ltr", class: "small" }, m.max === null ? "—" : String(m.max)),
          el("td", { dir: "ltr", class: "small" }, m.trend_pct === null ? "—" : `${m.trend_pct > 0 ? "+" : ""}${m.trend_pct} %`),
          el("td", {}, spark(d.spark[m.name] || [], m.status)),
          el("td", {}, pill(cls[m.status], t2(stTx[m.status])), m.detail ? el("span", { class: "small muted", dir: "auto" }, " " + m.detail) : null))))));
  }

  async function pageSpeed() {
    const d = await api("/api/selfspeed");
    const msg = el("span", { class: "muted small", role: "status" });
    render(header(tx("תקינות ומהירות", "Health & speed"),
      tx("ג'ארביס בודק את ההתקנה שלו ואת המהירות שלו, בחינם ובלי לשנות כלום. אפשר גם לבקש בקול.",
        "JARVES checks his own installation and his speed, for free and without changing anything. You can also ask by voice."),
      el("a", { href: "#/health" }, tx("תקינות", "Health"))), msg,
      selfCard(d, msg), speedCard(d, msg),
      card(tx("לוח זמנים", "Schedule"), el("div", { class: "small" },
        tx("בדיקה עצמית כל יום ב-08:15, בדיקת מהירות פעם בשבוע (ראשון 13:00). לא בשעות השקט ולא באמצע שיחה: ריצה שדולגה נרשמת ביומן ולא נדחית.",
          "Self-test daily at 08:15, speed test weekly (Sunday 13:00). Never in quiet hours or in the middle of a conversation: a skipped run is logged, not queued."),
        el("span", { dir: "ltr" }, ` ${d.schedule.quiet[0]}-${d.schedule.quiet[1]}`))));
  }

  Object.assign(PAGES, { speed: pageSpeed });
})();
