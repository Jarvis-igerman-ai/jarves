// JARVES dashboard: Bug reports page (the issues registry, owner request 21 Oct 2026) and the header badge. Uses the
// app.js helpers (el, api, render, header, pill, day, clock, route) and registers PAGES.bugs. Text goes in via
// textContent only. The Report button first SHOWS the exact content-free text; sending asks for approval (popup).
"use strict";

(function () {
  const when = (ts) => ts ? `${day(ts)} ${clock(ts)}` : "—";
  const sevCls = { P1: "high", P2: "medium", P3: "low" };
  const stTx = { new: ["חדש", "New"], acknowledged: ["בטיפול", "Acknowledged"], fixed: ["תוקן", "Fixed"], ignored: ["מתעלמים", "Ignored"] };
  const filt = { status: "open", severity: "", area: "" };
  const t2 = (pair) => tx(pair[0], pair[1]);
  const statusText = (r) => r.status === "fixed" && r.closed ? tx(r.closed_he, r.closed)
    : t2(stTx[r.status]) + (r.status === "fixed" && r.fixed_in ? tx(` בגרסה ${r.fixed_in}`, ` in version ${r.fixed_in}`) : "");

  // ---------------------------------------------------------------- header badge (every page)
  let chip = null;
  async function badge() {
    if (document.hidden) return;
    try {
      const c = await api("/api/bugs/count");
      if (!chip) {
        chip = el("a", { href: "#/bugs", class: "schip plain", id: "bugChip", role: "button", "aria-haspopup": "dialog",
          onclick: (e) => { e.preventDefault(); togglePop(); } }, el("span", { class: "schip-t" }));
        const bar = document.getElementById("statusChips");
        if (bar && bar.parentNode) bar.parentNode.insertBefore(chip, bar.nextSibling);
      }
      chip.hidden = !c.open;
      chip.firstChild.textContent = tx(`דיווחי תקלות: ${c.open}`, `Bug reports: ${c.open}`);
      chip.className = "schip " + (c.urgent ? "bad" : c.open ? "warn" : "plain");     // red only for NEW unresolved P1/P2
      chip.title = tx(`${c.open} תקלות פתוחות, ${c.urgent || 0} חדשות וחמורות. לחצו להסבר`, `${c.open} open, ${c.urgent || 0} new and serious. Click for what this is`);
      if (pop && !pop.hidden) fillPop();
    } catch (e) { /* the page shows its own error */ }
  }
  // the popover: what the number is, in plain words (counts and titles only, never conversation text)
  let pop = null;
  async function fillPop() {
    const [c, d] = await Promise.all([api("/api/bugs/count"), api("/api/bugs?status=open")]);
    const seen = el("button", { type: "button", class: "ghost", id: "bugSeenAll", disabled: !c.new, onclick: async () => {
      await api("/api/bugs/seen", { method: "POST", body: "{}" }); await badge(); route(true); } }, tx("סמן הכל כנראה", "Mark all as seen"));
    pop.replaceChildren(
      el("strong", {}, tx("מה זה המספר?", "What is this number?")),
      el("p", { class: "small", dir: "auto" }, tx(
        "אלה כרטיסי אבחון מקומיים ש-JARVES פתח לעצמו במחשב הזה (בדיקה עצמית, כלים שנכשלים, קריסות). הם לא נשלחים לאף אחד. הכתום או האדום מציינים רק תקלות חדשות וחמורות שעוד לא ראיתם; ישנות נסגרות לבד אחרי 14 יום בלי הישנות.",
        "These are local self-diagnostic tickets JARVES opened for itself on this PC (self-test, failing tools, crashes). They are not sent to anyone. Red means only NEW serious (P1/P2) tickets you have not seen yet; old ones close by themselves after 14 days without recurring.")),
      el("div", { class: "small muted" }, tx(`${c.open} פתוחות · ${c.new} חדשות · ${c.urgent || 0} חדשות וחמורות · ${c.info || 0} מידע בלבד`,
        `${c.open} open · ${c.new} new · ${c.urgent || 0} new and serious · ${c.info || 0} information only`)),
      el("ul", { class: "small" }, d.issues.slice(0, 5).map((r) => el("li", { dir: "auto" }, `${r.severity} · ${tx(r.title_he || r.title, r.title)} · ×${r.evidence_count}`))),
      el("div", { class: "row" }, seen, el("a", { href: "#/bugs", onclick: () => { pop.hidden = true; } }, tx("לרשימה המלאה", "Open the full list"))));
  }
  function togglePop() {
    if (!pop) {
      pop = el("div", { class: "card", id: "bugPop", role: "dialog", "aria-label": tx("על דיווחי התקלות", "About bug reports"),
        style: "position:fixed;top:56px;inset-inline-end:16px;z-index:50;max-width:380px;box-shadow:0 8px 28px rgba(0,0,0,.35)", hidden: true });
      document.body.appendChild(pop);
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") pop.hidden = true; });
    }
    pop.hidden = !pop.hidden;
    if (!pop.hidden) fillPop().catch(() => { pop.textContent = tx("נכשל לטעון", "Could not load"); });
  }
  window.addEventListener("hashchange", () => { if (pop) pop.hidden = true; });
  window.addEventListener("hashchange", badge);
  if (window.JL && JL.onChange) JL.onChange(badge);
  setInterval(badge, 60000);
  setTimeout(badge, 800);

  // ---------------------------------------------------------------- list
  function chipBtn(label, on, fn) {
    return el("button", { type: "button", class: "ghost" + (on ? " active" : ""), "aria-pressed": on ? "true" : "false", onclick: fn }, label);
  }
  function filters(d) {
    const set = (k, v) => () => { filt[k] = v; route(true); };
    return el("div", { class: "card" },
      el("div", { class: "row", style: "flex-wrap:wrap" }, el("span", { class: "muted small" }, tx("מצב", "Status")),
        ...[["open", tx("פתוחות", "Open")], ["info", tx("מידע (לא תקלות)", "Information (not bugs)")], ["all", tx("הכל", "All")], ["fixed", tx("תוקנו", "Fixed")], ["ignored", tx("מתעלמים", "Ignored")]]
          .map(([v, l]) => chipBtn(l, filt.status === v, set("status", v)))),
      el("div", { class: "row", style: "flex-wrap:wrap" }, el("span", { class: "muted small" }, tx("חומרה", "Severity")),
        ...[["", tx("הכל", "All")], ["P1", "P1"], ["P2", "P2"], ["P3", "P3"]].map(([v, l]) => chipBtn(l, filt.severity === v, set("severity", v)))),
      el("div", { class: "row", style: "flex-wrap:wrap" }, el("span", { class: "muted small" }, tx("תחום", "Area")),
        chipBtn(tx("הכל", "All"), !filt.area, set("area", "")),
        ...d.areas.map((a) => chipBtn(a, filt.area === a, set("area", a)))));
  }
  function item(r) {
    return el("a", { class: "card", href: "#/bugs/" + r.id, style: "display:block;text-decoration:none;color:inherit" },
      el("div", { class: "row", style: "flex-wrap:wrap" }, pill(sevCls[r.severity], r.severity), el("strong", { dir: "auto" }, tx(r.title_he || r.title, r.title)),
        pill("plain", statusText(r))),
      r.meaning ? el("div", { class: "small", dir: "auto" }, tx(r.meaning_he || r.meaning, r.meaning)) : null,
      el("div", { class: "muted small", dir: "auto" }, `${r.id} · ${tx(r.area_label_he, r.area_label)} · ×${r.evidence_count} · ${when(r.last_seen)}`
        + (r.reported_url ? tx(" · דווח", " · reported") : r.bundle_saved ? tx(" · נשמר לצירוף", " · saved to attach") : "")));
  }
  async function pageList() {
    const q = new URLSearchParams();
    if (filt.status !== "all") q.set("status", filt.status);
    if (filt.severity) q.set("severity", filt.severity);
    if (filt.area) q.set("area", filt.area);
    const d = await api("/api/bugs?" + q.toString());
    render(header(tx("דיווחי תקלות", "Bug reports"),
      tx("בעיות ש-JARVES מצא בעצמו (בדיקה עצמית, מהירות, אבחון, כלים שנכשלים, קריסות, שגיאות בלוח הבקרה). הדיווח למפתח נשלח רק אחרי שתאשרו את הטקסט המדויק, ואין בו תוכן שיחות.",
        "Problems JARVES found in himself (self-test, speed, diagnostics, failing tools, crashes, dashboard errors). A report goes to the developer only after you approve the exact text, and it holds no conversation content.")),
    filters(d),
    d.issues.length ? d.issues.map(item) : el("div", { class: "card empty" }, tx("אין תקלות להצגה.", "No issues to show.")));
  }

  // ---------------------------------------------------------------- detail + report
  async function act(id, status, msg) {
    try { await api(`/api/bugs/${id}/status`, { method: "POST", body: JSON.stringify({ status }) }); badge(); route(true); }
    catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
  }
  async function send(id, box, msg) {
    msg.textContent = tx("ממתין לאישור שלכם בחלון האישורים…", "Waiting for your approval in the approvals popup…");
    try {
      const r = await api(`/api/bugs/${id}/report`, { method: "POST", body: "{}" });
      const text = { filed: tx("הדיווח נשלח למפתח: ", "Reported to the developer: "), commented: tx("נוספה הערה לדיווח קיים: ", "Added to the existing report: "),
        saved: tx("לא נשלח. נשמר בקובץ לצירוף ידני: ", "Not sent. Saved so you can attach it: "), declined: tx("לא אושר, שום דבר לא נשלח. ", "Not approved, nothing was sent. ") }[r.status] || "";
      msg.textContent = text + (r.issue_url || r.path || r.reason || "");
      badge();
    } catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + e.message; }
  }
  async function pageDetail(id) {
    const d = await api("/api/bugs/" + encodeURIComponent(id));
    const r = d.issue, msg = el("div", { class: "small", role: "status" });
    const bundleBox = el("div", { class: "card", hidden: true });
    const show = () => {
      bundleBox.hidden = false;
      bundleBox.replaceChildren(el("h2", {}, tx("זה הטקסט המדויק שיישלח", "This is the exact text that would be sent")),
        el("div", { class: "muted small" }, tx("אין בו תוכן שיחות, שמות, כתובות, נתיבים או מפתחות. ליעד: ", "No conversation text, names, addresses, paths or keys. To: ") + `github.com/${"Jarvis-igerman-ai/jarves"} · label ${"from-diagnostics"}`
          + (d.token ? "" : tx(" · אין אסימון GitHub במחשב: הדיווח יישמר בקובץ לצירוף ידני", " · no GitHub token here: the report is saved for you to attach"))),
        el("pre", { dir: "ltr", style: "white-space:pre-wrap;overflow-wrap:anywhere" }, d.bundle.title + "\n\n" + d.bundle.body),
        el("div", { class: "row" },
          el("button", { type: "button", class: "primary", onclick: () => send(id, bundleBox, msg) }, tx("שלחו (יבקש אישור)", "Send (asks for approval)")),
          el("button", { type: "button", class: "ghost", onclick: () => { bundleBox.hidden = true; } }, tx("ביטול", "Cancel"))));
    };
    const ev = Object.entries(r.evidence || {});
    render(header(tx("תקלה ", "Issue ") + r.id, tx(r.title_he || r.title, r.title), el("a", { href: "#/bugs" }, tx("כל הדיווחים", "All reports"))),
      el("div", { class: "card" },
        el("div", { class: "row", style: "flex-wrap:wrap" }, pill(sevCls[r.severity], r.severity), pill("plain", statusText(r)),
          el("span", { class: "muted small" }, `${tx(r.area_label_he, r.area_label)} · ×${r.evidence_count}`)),
        r.meaning ? el("div", {}, el("strong", {}, tx("מה זה אומר: ", "What it means: ")), tx(r.meaning_he || r.meaning, r.meaning)) : null,
        r.next_step ? el("div", {}, el("strong", {}, tx("הצעד הבא: ", "Next step: ")), tx(r.next_step_he || r.next_step, r.next_step)) : null,
        r.kind === "info" && r.hint ? el("div", { class: "muted small" }, tx(r.hint_he || r.hint, r.hint)) : null,
        el("div", { class: "small muted" }, tx("נראה לראשונה: ", "First seen: ") + when(r.first_seen) + tx(" · לאחרונה: ", " · last: ") + when(r.last_seen)
          + (r.versions.length ? tx(" · גרסאות: ", " · versions: ") + r.versions.join(", ") : "")),
        r.reported_url ? el("div", { class: "small" }, tx("דווח: ", "Reported: "), el("a", { href: r.reported_url, target: "_blank", rel: "noopener noreferrer" }, r.reported_url)) : null,
        r.bundle_saved ? el("div", { class: "small muted" }, tx("נשמר לצירוף: ", "Saved to attach: ") + r.bundle_saved) : null,
        el("div", { class: "row", style: "flex-wrap:wrap" },
          r.kind === "info" ? null : el("button", { type: "button", class: "primary", onclick: show }, tx("דווחו למפתח", "Report")),
          r.status === "new" && r.kind !== "info" ? el("button", { type: "button", class: "ghost", onclick: () => act(id, "acknowledged", msg) }, tx("ראיתי", "Acknowledge")) : null,
          r.status !== "ignored" ? el("button", { type: "button", class: r.kind === "info" ? "primary" : "ghost", onclick: () => act(id, "ignored", msg) },
            r.kind === "info" ? tx("התעלם מזה במחשב הזה", "Ignore this on this PC") : tx("התעלם", "Ignore")) : null,
          r.status !== "fixed" ? el("button", { type: "button", class: "ghost", onclick: () => act(id, "fixed", msg) }, tx("סמן כתוקן", "Mark fixed")) : null,
          r.status === "fixed" || r.status === "ignored" ? el("button", { type: "button", class: "ghost", onclick: () => act(id, "new", msg) }, tx("פתח מחדש", "Reopen")) : null),
        msg),
      el("div", { class: "card" }, el("h2", {}, tx("ראיות (בלי תוכן)", "Evidence (content-free)")),
        el("table", {}, el("tbody", {}, ev.length ? ev.map(([k, v]) => el("tr", {}, el("td", { dir: "ltr" }, k), el("td", { dir: "ltr" }, JSON.stringify(v))))
          : el("tr", {}, el("td", { class: "muted" }, tx("אין", "None")))))),
      bundleBox);
  }

  Object.assign(PAGES, { bugs: (arg) => (arg ? pageDetail(arg) : pageList()) });
})();
