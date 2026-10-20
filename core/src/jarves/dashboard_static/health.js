// JARVES dashboard: Health page (SW-V4 package O, Self-Diagnostics 2.0, #17). Uses app.js helpers (el, api, render,
// header, pill, day, clock, route) and registers PAGES.health. Text goes in via textContent only. Nothing on this
// page changes anything without the approvals flow: repairs and ticket filing ask on screen first.
"use strict";

(function () {
  const when = (ts) => ts ? `${day(ts)} ${clock(ts)}` : "—";
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const sevCls = { high: "fail", medium: "degraded", low: "plain" };
  const sevTx = { high: ["גבוהה", "high"], medium: ["בינונית", "medium"], low: ["נמוכה", "low"] };
  const stTx = { ok: ["תקין", "ok"], degraded: ["פועל חלקית", "degraded"], unavailable: ["לא זמין", "unavailable"],
    disabled: ["כבוי", "disabled"], unknown: ["לא ידוע", "unknown"] };
  const stateTx = { open: ["פתוח", "open"], accepted: ["התקבל", "accepted"], dismissed: ["נדחה", "dismissed"],
    snoozed: ["נדחה לאחר כך", "snoozed"], stale: ["ממתין יותר משבוע", "stale"], seen: ["נבדק", "seen"] };
  const pfCls = { green: "ok", amber: "degraded", red: "fail", skipped: "plain" };
  const cnCls = { PASS: "ok", FAIL: "fail", BLOCKED: "plain" };
  const t2 = (pair) => pair ? tx(pair[0], pair[1]) : "";
  const money = (v) => "$" + Number(v || 0).toFixed(4);

  function card(title, ...kids) {
    return el("div", { class: "card" }, el("h2", {}, title), ...kids);
  }

  function findingCard(f, msg) {
    const decide = (op, extra) => async () => {
      try { await post("/api/health/decide", { fingerprint: f.fingerprint, op, source: f.source, ...(extra || {}) }); route(true); }
      catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
    };
    const buttons = el("div", { class: "row" });
    if (f.state === "open" || f.state === "snoozed") {
      buttons.append(
        el("button", { type: "button", class: "primary", onclick: decide("accept") }, tx("קבל", "Accept")),
        el("button", { type: "button", class: "ghost", onclick: decide("snooze", { days: 7 }) }, tx("הזכר בעוד שבוע", "Snooze 7 days")),
        el("button", { type: "button", class: "ghost", onclick: decide("dismiss") }, tx("דחה", "Dismiss")));
    } else {
      buttons.append(el("button", { type: "button", class: "ghost", onclick: decide("reopen") }, tx("פתח מחדש", "Reopen")));
    }
    const action = f.action || {};
    if (action.type === "remedy") {
      buttons.append(el("button", { type: "button", class: "ghost", onclick: () => remedy(action.remedy, f.fingerprint, msg) },
        tx("תיקון (ישאל לאישור)", "Repair (asks first)")));
    }
    if (f.source !== "selfcheck") {
      buttons.append(el("button", { type: "button", class: "ghost", onclick: async () => {
        try { const t = await post("/api/health/ticket", { fingerprint: f.fingerprint }); msg.textContent = tx("נוצרה טיוטת כרטיס #", "Ticket draft #") + t.id; route(true); }
        catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
      } }, tx("טיוטת כרטיס", "Ticket draft")));
    }
    const link = action.type === "open" ? el("a", { href: action.page }, tx("פתח", "Open")) :
      action.type === "command" ? el("code", { dir: "ltr" }, action.command) : null;
    const hints = (f.hints || []).map((h) => el("div", { dir: "ltr", class: "small" },
      el("a", { href: h.url, target: "_blank", rel: "noopener" }, `${h.file}:${h.line}`), ` ${h.function || ""} ${h.commit || ""}`,
      h.guide ? el("a", { href: "#/guide/" + h.guide }, " " + tx("מדריך", "Guide")) : null));
    return el("div", { class: "issue card" },
      el("div", { class: "row" }, pill(sevCls[f.severity] || "plain", t2(sevTx[f.severity])),
        el("b", { dir: "auto" }, `${f.rank}. ${tx(f.title_he, f.title)}`),
        f.state !== "open" ? pill("plain", t2(stateTx[f.state])) : null,
        f.source === "selfcheck" ? pill("plain", tx("בדיקה עצמית", "Self-check")) : null),
      el("div", { class: "muted small" }, `${f.category} · ×${f.count || 1}`),
      (f.detail || f.detail_he) ? el("div", { class: "detail", dir: "auto" }, tx(f.detail_he, f.detail)) : null,
      f.fix ? el("div", { class: "small", dir: "auto" }, el("span", { class: "k" }, tx("תיקון מוצע: ", "Suggested fix: ")), tx(f.fix_he, f.fix), " ", link) : null,
      hints.length ? el("details", {}, el("summary", {}, tx("מיקום בקוד", "Code location")), hints) : null,
      (f.evidence || []).length ? el("details", {}, el("summary", {}, tx("הוכחות", "Evidence") + ` (${f.evidence.length})`),
        el("div", { class: "evidence" }, f.evidence.map((e) => el("div", { dir: "ltr" }, e)))) : null,
      buttons);
  }

  async function remedy(id, finding, msg, feature) {
    msg.textContent = tx("ממתין לאישור על המסך…", "Waiting for your approval on screen…");
    try {
      const r = await post("/api/health/remediate", { remedy: id, finding, feature });
      msg.textContent = `${id}: ${r.status} ${r.postcheck || r.result || r.precheck || ""}`;
      route(true);
    } catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
  }

  function trendView(trend) {
    if (!trend || !trend.length) return el("div", { class: "muted small" }, tx("אין עדיין מגמה (נאסף כל שעה).", "No trend yet (recorded every hour)."));
    const w = 300, h = 40, n = trend.length;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("width", w); svg.setAttribute("height", h);
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", tx("מגמת ציון 30 יום", "30-day score trend"));
    const pts = trend.map((p, i) => `${n === 1 ? w / 2 : (i * w) / (n - 1)},${h - (p.score / 100) * (h - 4) - 2}`).join(" ");
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", pts); line.setAttribute("fill", "none"); line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-width", "2");
    svg.append(line);
    return el("div", { class: "row" }, svg, el("span", { class: "muted small" }, `${trend[0].score} → ${trend[n - 1].score}`));
  }

  function aiCard(d, msg) {
    const s = d.ai_settings || {};
    const v = d.ai_review;
    const every = el("select", { "aria-label": tx("כל כמה ימים", "Every how many days") },
      ["off", "1", "2", "3", "7"].map((o) => el("option", { value: o }, o === "off" ? tx("כבוי", "off") : o)));
    every.value = s["health.ai_review_enabled"] ? String(s["health.ai_review_every_days"]) : "off";
    every.onchange = async () => {
      try { await post("/api/health/settings", { "health.ai_review_every_days": every.value }); route(true); }
      catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
    };
    const pv = el("div", {});
    const previewBtn = el("button", { type: "button", class: "ghost", onclick: async () => {
      try {
        const p = await api("/api/health/ai/preview");
        pv.replaceChildren(
          el("div", {}, tx("מה יישלח: ", "What would be sent: ") + `${p.items.length} · ` + tx("עלות משוערת עד ", "estimate up to ") + money(p.est_usd)
            + ` (${tx("תקרה", "cap")} ${money(p.cap_usd)})` + (p.would_skip ? " · " + tx("ידלג: אין שום דבר חדש ($0)", "would skip: nothing new ($0)") : "")),
          el("ul", {}, p.items.slice(0, 12).map((i) => el("li", { dir: "ltr" }, `[${i.severity}] ${i.kind}: ${i.title}`))),
          p.would_skip ? null : el("button", { type: "button", class: "primary", onclick: async () => {
            msg.textContent = tx("בודק…", "Reviewing…");
            try { const r = await post("/api/health/ai/run", { confirm: true, est_usd: p.est_usd }); msg.textContent = `${r.status} ${money(r.cost_usd)}`; route(true); }
            catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
          } }, tx("הרץ עכשיו (עד העלות המשוערת)", "Run now (up to the estimate)")));
      } catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
    } }, tx("מה יישלח ובכמה", "What would be sent and its cost"));
    const items = v && v.items_list ? v.items_list : [];
    const decide = (it, op, extra) => async () => {
      try { await post("/api/health/ai/decide", { id: it.id, op, ...(extra || {}) }); route(true); }
      catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
    };
    return card(tx("בדיקת AI סלקטיבית", "Selective AI review"),
      v && v.ready ? el("div", { class: "row" }, pill("degraded", tx("דוח מוכן", "AI review ready")), `${items.length} · ${money(v.cost_usd)}`) : null,
      el("div", { class: "row" }, el("label", { class: "small" }, tx("כל כמה ימים: ", "Every N days: "), every),
        el("span", { class: "muted small" }, tx("תקרה לבדיקה ", "per-run cap ") + money(s["health.ai_review_run_cap_usd"]) + " · "
          + tx("לחודש ", "per month ") + money(s["health.ai_review_month_cap_usd"]) + " · " + tx("החודש ", "this month ") + money(s.month_spent_usd)
          + " · " + (s["health.ai_review_model"] || "")), previewBtn),
      el("details", {}, el("summary", {}, tx("מה הבודק לעולם לא רואה", "What the reviewer never sees")),
        el("ul", {}, ((document.documentElement.lang === "he" ? s.never_shown_he : s.never_shown) || []).map((x) => el("li", {}, x)))),
      pv,
      v ? el("div", { class: "muted small" }, `#${v.id} · ${when(v.started)} · ${v.status}` + (v.reason ? ` (${v.reason})` : "")
        + ` · ${tx("לפני: ", "estimate: ")}${money(v.est_usd)} · ${tx("בפועל: ", "actual: ")}${money(v.cost_usd)}`) :
        el("div", { class: "muted small" }, tx("הבודק עוד לא רץ.", "The reviewer has not run yet.")),
      items.map((it) => el("div", { class: "issue card" },
        el("div", { class: "row" }, pill(sevCls[it.severity] || "plain", t2(sevTx[it.severity])), el("b", { dir: "auto" }, `${it.rank}. ${it.title}`),
          pill("plain", t2(stateTx[it.state]))),
        it.evidence ? el("div", { class: "small", dir: "auto" }, el("span", { class: "k" }, tx("הוכחה: ", "Evidence: ")), it.evidence) : null,
        it.hypothesis ? el("div", { class: "small", dir: "auto" }, el("span", { class: "k" }, tx("השערה: ", "Hypothesis: ")), it.hypothesis) : null,
        it.fix ? el("div", { class: "small", dir: "auto" }, el("span", { class: "k" }, tx("תיקון מוצע: ", "Suggested fix: ")), it.fix) : null,
        el("div", { class: "row" },
          el("button", { type: "button", class: "primary", onclick: decide(it, "accept") }, tx("קבל (שום דבר לא מוחל לבד)", "Accept (nothing is applied by itself)")),
          el("button", { type: "button", class: "ghost", onclick: decide(it, "snooze", { days: 7 }) }, tx("הזכר בעוד שבוע", "Snooze 7 days")),
          el("button", { type: "button", class: "ghost", onclick: decide(it, "dismiss") }, tx("דחה", "Dismiss"))))));
  }

  async function pageHealth() {
    const d = await api("/api/health");
    const msg = el("span", { class: "muted small", role: "status" });
    const parts = [header(tx("תקינות", "Health"),
      tx("ג'ארביס עוקב אחרי עצמו בחינם, מוצא חריגות ומראה איפה בקוד. תיקון רק אחרי אישור שלכם.",
        "JARVES watches himself for free, finds anomalies and shows where in the code. Repairs only after you approve."),
      el("a", { href: "#/selfcheck" }, tx("בדיקה עצמית מלאה", "Full self-check"))), msg];
    parts.push(el("div", { class: "card" },
      el("div", { class: "row" }, el("b", {}, tx("ציון", "Score") + `: ${d.score}/100`), pill(d.open ? "degraded" : "ok", `${d.open} ` + tx("פתוחים", "open"))),
      el("div", { dir: "auto" }, tx(d.summary_he, d.summary)), trendView(d.trend)));
    parts.push(card(tx("רכיבים", "Components"), d.tiles.length ? el("div", { class: "row" }, d.tiles.map((t) =>
      el("span", { class: "pill " + ({ ok: "ok", degraded: "degraded", unavailable: "fail" }[t.status] || "plain"), title: t.detail },
        `${t.name}: ${t2(stTx[t.status] || stTx.unknown)}`))) : el("div", { class: "muted small" }, tx("ג'ארביס לא פועל כרגע.", "JARVES is not running now."))));
    const open = d.findings.filter((f) => f.state === "open" || f.state === "accepted");
    const other = d.findings.filter((f) => !(f.state === "open" || f.state === "accepted"));
    parts.push(card(tx("ממצאים", "Findings"), open.length ? open.map((f) => findingCard(f, msg)) : el("div", { class: "muted" }, tx("אין ממצאים פתוחים.", "No open findings.")),
      other.length ? el("details", {}, el("summary", {}, tx("נדחו / נדחו לאחר כך", "Dismissed / snoozed") + ` (${other.length})`), other.map((f) => findingCard(f, msg))) : null));
    const vr = d.version_report;
    parts.push(card(tx("דוח גרסה", "Version report"), vr ? el("div", {},
      el("div", {}, `${vr.previous} → ${vr.version} (${vr.commit || "—"}) · ${vr.hours_compared} h`),
      vr.items.length ? el("ul", {}, vr.items.map((i) => el("li", { dir: "ltr" }, i.code ? `${i.kind} ${i.code} ${i.ratio || ""}×` : `${i.kind} ${i.metric || ""} ${i.ratio || ""}×`)))
        : el("div", { class: "muted small" }, tx("אין נסיגה מול הגרסה הקודמת.", "No regression against the previous version.")))
      : el("div", { class: "muted small" }, tx("אין עדכון חדש להשוואה.", "No recent update to compare."))));
    const pf = d.preflight;
    parts.push(card(tx("בדיקת הפעלה", "Start-up check"), pf ? el("div", {},
      el("div", { class: "muted small" }, `${when(pf.ts)} · ${Math.round(pf.ms)} ms`),
      el("table", {}, el("tbody", {}, pf.items.map((i) => el("tr", {}, el("td", {}, pill(pfCls[i.status] || "plain", i.status)),
        el("td", {}, tx(i.title_he, i.title)), el("td", { dir: "ltr", class: "small" }, i.detail)))))) :
      el("div", { class: "muted small" }, tx("עוד לא רצה בדיקת הפעלה.", "No start-up check yet."))));
    parts.push(card(tx("בדיקות לילה", "Nightly canaries"), (d.canary || []).length ? el("div", {},
      el("div", { class: "muted small" }, `${when(d.canary[0].ts)} · ${d.canary[0].profile}`),
      el("table", {}, el("tbody", {}, d.canary[0].results.map((r) => el("tr", {}, el("td", {}, pill(cnCls[r.status] || "plain", r.status)),
        el("td", {}, r.name), el("td", { class: "small" }, `${Math.round(r.ms)} ms`), el("td", { dir: "ltr", class: "small" }, r.detail))))))
      : el("div", { class: "muted small" }, tx("עוד לא רצו בדיקות לילה.", "No nightly canary yet."))));
    parts.push(aiCard(d, msg));
    parts.push(card(tx("תיקונים (תמיד באישור)", "Repairs (always with approval)"),
      el("div", { class: "row" }, d.remedies.map((r) => el("button", { type: "button", class: "ghost", title: tx(r.dry_run_he, r.dry_run),
        onclick: () => remedy(r.id, "", msg) }, tx(r.title_he, r.title)))),
      (d.remediations || []).length ? el("table", {}, el("tbody", {}, d.remediations.slice(0, 10).map((r) => el("tr", {},
        el("td", {}, when(r.ts)), el("td", {}, r.remedy), el("td", {}, r.status), el("td", { class: "small", dir: "ltr" }, r.postcheck || r.result || r.precheck || ""))))) : null));
    parts.push(card(tx("טיוטות כרטיסים", "Ticket drafts"), (d.tickets || []).length ? d.tickets.map((t) => el("details", {},
      el("summary", {}, `#${t.id} ${t.title} · ${t.status}`), el("pre", { dir: "ltr" }, t.body),
      t.status === "draft" ? el("button", { type: "button", class: "ghost", onclick: async () => {
        msg.textContent = tx("ממתין לאישור על המסך…", "Waiting for your approval on screen…");
        try { const r = await post("/api/health/ticket/file", { id: t.id }); msg.textContent = `${r.status} ${r.issue_url || r.reason || ""}`; route(true); }
        catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
      } }, tx("שלח ל-GitHub (ישאל קודם, רק אם הוגדר מאגר)", "File on GitHub (asks first, only if a repo is set)")) : null))
      : el("div", { class: "muted small" }, tx("אין טיוטות.", "No drafts."))));
    render(parts);
  }

  Object.assign(PAGES, { health: pageHealth });
})();
