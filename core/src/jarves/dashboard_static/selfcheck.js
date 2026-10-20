// JARVES dashboard: Self-check page (owner request 20 Oct 2026, #17). Uses app.js helpers (el, api, render, header,
// pill, day, clock, route) and registers PAGES.selfcheck. Text goes in via textContent only.
"use strict";

(function () {
  const when = (ts) => ts ? `${day(ts)} ${clock(ts)}` : "—";
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const sevCls = { high: "fail", medium: "degraded", low: "plain" };
  const trendTx = { new: ["חדש", "new"], worse: ["החמיר", "worse"], same: ["כמו קודם", "same"] };
  const stateTx = { open: ["פתוח", "open"], accepted: ["התקבל", "accepted"], dismissed: ["נדחה", "dismissed"],
    snoozed: ["נדחה לאחר כך", "snoozed"] };
  const trigTx = { voice: ["קול", "voice"], cli: ["מסוף", "terminal"], dashboard: ["לוח בקרה", "dashboard"],
    schedule: ["שבועי", "weekly"], crash: ["אחרי עצירה לא מסודרת", "after an unclean stop"],
    restart: ["אחרי הפעלה", "after a start"], errors: ["אחרי רצף שגיאות", "after an error burst"], test: ["בדיקה", "test"] };
  const t2 = (pair) => pair ? tx(pair[0], pair[1]) : "";

  function findingCard(f, msg) {
    const decide = (op, extra) => async () => {
      try { await post("/api/selfcheck/decide", { fingerprint: f.fingerprint, op, ...(extra || {}) }); route(true); }
      catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
    };
    const buttons = el("div", { class: "row" });
    if (f.state === "open" || f.state === "snoozed") {
      buttons.append(
        el("button", { type: "button", class: "primary", onclick: decide("accept") },
          f.changes_setting ? tx("קבל (ישאל לאישור השינוי)", "Accept (asks before changing)") : tx("קבל", "Accept")),
        el("button", { type: "button", class: "ghost", onclick: decide("snooze", { days: 7 }) }, tx("הזכר בעוד שבוע", "Snooze 7 days")),
        el("button", { type: "button", class: "ghost", onclick: decide("dismiss") }, tx("דחה", "Dismiss")));
    } else {
      buttons.append(el("button", { type: "button", class: "ghost", onclick: decide("reopen") }, tx("פתח מחדש", "Reopen")));
    }
    if (f.proposal_id) {
      buttons.append(el("button", { type: "button", class: "primary", onclick: async () => {
        try { await post("/api/proposals/" + f.proposal_id, { op: "approve" }); msg.textContent = tx("השינוי הוחל (חל בהפעלה הבאה)", "Change applied (next start)"); route(true); }
        catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
      } }, tx("אשר את השינוי #", "Approve change #") + f.proposal_id));
    }
    const action = f.action || {};
    const link = action.type === "open" ? el("a", { href: action.page }, tx("פתח", "Open")) :
      action.type === "command" ? el("code", { dir: "ltr" }, action.command) : null;
    return el("div", { class: "issue card" },
      el("div", { class: "row" }, pill(sevCls[f.severity] || "plain", tx(f.severity_he, f.severity)),
        el("b", { dir: "auto" }, `${f.rank}. ${tx(f.title_he, f.title)}`),
        pill("plain", t2(trendTx[f.trend])), f.state !== "open" ? pill("plain", t2(stateTx[f.state])) : null),
      el("div", { class: "muted small" }, `${f.category} · ${tx("מאמץ", "effort")}: ${tx(f.effort_he, f.effort)}` +
        (f.count > 1 ? ` · ×${f.count}` : "")),
      (f.detail || f.detail_he) ? el("div", { class: "detail", dir: "auto" }, tx(f.detail_he, f.detail)) : null,
      f.cause ? el("div", { class: "small", dir: "auto" }, el("span", { class: "k" }, tx("סיבה משוערת: ", "Likely cause: ")), tx(f.cause_he, f.cause)) : null,
      el("div", { class: "small", dir: "auto" }, el("span", { class: "k" }, tx("תיקון מוצע: ", "Suggested fix: ")), tx(f.fix_he, f.fix), " ", link),
      f.evidence.length ? el("details", {}, el("summary", {}, tx("הוכחות", "Evidence") + ` (${f.evidence.length})`),
        el("div", { class: "evidence" }, f.evidence.map((e) => el("div", { dir: "ltr" }, e)))) : null,
      buttons);
  }

  async function pageSelfcheck(arg) {
    const d = arg ? { latest: await api("/api/selfcheck/" + arg), history: (await api("/api/selfcheck")).history }
      : await api("/api/selfcheck");
    const msg = el("span", { class: "muted small" });
    const aiBox = el("input", { type: "checkbox" });
    if (d.settings && d.settings["self_check.ai_pass"]) aiBox.checked = true;
    const run = el("button", { type: "button", class: "primary", onclick: async () => {
      run.disabled = true; run.textContent = tx("בודק…", "Checking…");
      try { const v = await post("/api/selfcheck/run", { ai: aiBox.checked }); location.hash = "#/selfcheck"; route(true); msg.textContent = tx(v.summary_he, v.summary); }
      catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; run.disabled = false; }
    } }, tx("בדוק עכשיו", "Check now"));
    const v = d.latest;
    const parts = [header(tx("בדיקה עצמית", "Self-check"),
      tx("ג'ארביס בודק את הרכיבים, היומן, ההגדרות והיכולות ומציע תיקונים. שינוי הגדרה רק אחרי אישור שלכם.",
        "JARVES checks components, logs, settings and features and suggests fixes. Settings change only after you approve."),
      el("label", { class: "small" }, aiBox, " ", tx("כולל בדיקה בעזרת המודל (עולה כסף)", "include AI review (costs money)")), run), msg];
    if (d.job) parts.push(el("div", { class: "muted small" }, tx("בדיקה קבועה: ", "Scheduled: ") + `${d.job.human} · ` +
      tx("הבאה: ", "next: ") + (d.job.next_run_local || "—") + " · ", el("a", { href: "#/jobs" }, tx("שינוי שעה", "change time"))));
    if (!v) {
      parts.push(el("div", { class: "card empty" }, tx("עוד לא רצה בדיקה עצמית.", "No self-check yet.")));
      render(parts); return;
    }
    const tr = v.trend;
    const ai = v.ai || {};
    parts.push(el("div", { class: "card" },
      el("div", { class: "row" }, el("b", {}, tx("ציון", "Score") + `: ${v.score}/100`),
        tr ? pill(tr.score_delta >= 0 ? "ok" : "fail", (tr.score_delta >= 0 ? "+" : "") + tr.score_delta + tx(" מול הבדיקה הקודמת", " vs previous")) : null,
        el("span", { class: "muted small" }, `#${v.id} · ${when(v.started)} · ${t2(trigTx[v.trigger])} · ${v.days} ${tx("ימים", "days")}`)),
      el("div", { dir: "auto" }, tx(v.summary_he, v.summary)),
      tr ? el("div", { class: "muted small" }, tx("חדשים", "new") + `: ${tr.new.length} · ` + tx("נשארו", "persisting") +
        `: ${tr.persisting.length} · ` + tx("נפתרו", "resolved") + `: ${tr.resolved.length}`) : null,
      el("div", { class: "muted small" }, ai.used ? tx(`בדיקת מודל: ${ai.reviewed} שיחות, עלות משוערת $${(ai.cost_usd || 0).toFixed(4)}`,
        `AI review: ${ai.reviewed} conversation(s), estimated cost $${(ai.cost_usd || 0).toFixed(4)}`)
        : tx("בדיקת מודל לא רצה: ", "AI review not run: ") + (ai.skipped || "")),
      el("div", { class: "row" }, el("button", { type: "button", class: "ghost", onclick: async () => {
        const t = await api(`/api/selfcheck/${v.id}/ticket`);
        ticket.hidden = false; ticket.value = "# " + t.title + "\n\n" + t.body;
      } }, tx("טיוטת כרטיס", "Ticket draft")))));
    const ticket = el("textarea", { rows: "14", dir: "ltr", readonly: true, style: "width:100%", hidden: true });
    parts.push(ticket);
    const shown = v.findings.filter((f) => f.state === "open" || f.state === "accepted");
    const hidden = v.findings.filter((f) => !(f.state === "open" || f.state === "accepted"));
    parts.push(shown.length ? shown.map((f) => findingCard(f, msg)) : el("div", { class: "card empty" }, tx("אין ממצאים פתוחים.", "No open findings.")));
    if (hidden.length) parts.push(el("details", {}, el("summary", {}, tx("נדחו / נדחו לאחר כך", "Dismissed / snoozed") + ` (${hidden.length})`),
      hidden.map((f) => findingCard(f, msg))));
    parts.push(el("div", { class: "card" }, el("h2", {}, tx("היסטוריה", "History")), el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, tx("מתי", "When")), el("th", {}, tx("הפעלה", "Trigger")),
        el("th", {}, tx("ציון", "Score")), el("th", {}, tx("ממצאים", "Findings")))),
      el("tbody", {}, (d.history || []).map((h) => el("tr", {},
        el("td", {}, el("a", { href: "#/selfcheck/" + h.id }, String(h.id))), el("td", {}, when(h.started)),
        el("td", {}, t2(trigTx[h.trigger])), el("td", {}, String(h.score ?? "—")),
        el("td", {}, `${h.counts.high || 0} / ${h.counts.medium || 0} / ${h.counts.low || 0}`)))))));
    render(parts);
  }

  Object.assign(PAGES, { selfcheck: pageSelfcheck });
})();
