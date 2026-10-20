// JARVES dashboard: Approvals (V3). A banner on EVERY page while JARVES waits for the owner's approval, and the
// #/approvals page with the decision history. Data: GET /api/approvals (approvals.py); answers: POST
// /api/approvals/<id> {approved, typed}. The dashboard click IS the owner's answer; typed requests need the code.
"use strict";

(function () {
  const he = () => !window.JL || JL.lang === "he";
  const T = (h, e) => (he() ? h : e);
  const RISK = { low: ["נמוך", "low"], medium: ["בינוני", "medium"], high: ["גבוה", "high"] };
  let last = "", busy = false, msg = "";

  function card(a, compact) {
    const typed = el("input", { type: "text", inputmode: "numeric", maxlength: "6", class: "ap-code",
      "aria-label": T("קוד אישור", "Approval code"), placeholder: a.typed_code || "" });
    const send = async (approved) => {
      busy = true; msg = "";
      try { await api("/api/approvals/" + encodeURIComponent(a.id), { method: "POST",
        body: JSON.stringify({ approved, typed: a.typed ? typed.value.trim() : "" }) }); }
      catch (e) { msg = e.message; }
      busy = false; last = ""; refresh();
    };
    const r = RISK[a.risk] || RISK.high;
    const details = Object.entries(a.details || {}).map(([k, v]) =>
      el("div", { class: "ap-row" }, el("span", { class: "muted small" }, k + ": "), el("code", { dir: "ltr" }, String(v))));
    return el("div", { class: `ap-card ap-${a.risk}`, role: "alertdialog", "aria-label": T("בקשת אישור", "Approval request") },
      el("div", { class: "ap-head" }, el("b", {}, T("JARVES מבקש אישור", "JARVES asks for approval")),
        el("span", { class: "pill ap-risk" }, T("סיכון ", "risk ") + r[he() ? 0 : 1]),
        el("span", { class: "muted small" }, T(`נדחה אוטומטית בעוד ${a.seconds_left} שנ׳`, `auto-deny in ${a.seconds_left} s`))),
      el("div", { class: "ap-title", dir: "auto" }, a.title),
      a.prompt && a.prompt !== a.title ? el("div", { dir: "auto", class: "ap-prompt" }, a.prompt) : null,
      a.why ? el("div", { class: "small", dir: "auto" }, el("span", { class: "muted" }, T("למה (במילים של JARVES): ", "Why (JARVES's words): ")), a.why) : null,
      compact ? null : details,
      a.typed ? el("div", { class: "ap-typed" }, T(`פעולה מסוכנת: הקלד ${a.typed_code} כדי לאשר `, `Dangerous: type ${a.typed_code} to approve `), typed) : null,
      el("div", { class: "row" },
        el("button", { type: "button", class: "primary", disabled: busy ? true : null, onclick: () => send(true) }, T("אשר", "Approve")),
        el("button", { type: "button", disabled: busy ? true : null, onclick: () => send(false) }, T("דחה", "Deny")),
        compact ? el("a", { href: "#/approvals", class: "small" }, T("פרטים", "Details")) : null));
  }

  function banner(pending) {
    let b = document.getElementById("apBanner");
    if (!pending.length) { if (b) b.remove(); return; }
    if (!b) { b = el("div", { id: "apBanner", class: "ap-banner", "aria-live": "assertive" }); document.body.append(b); }
    if (location.hash.startsWith("#/approvals")) { b.replaceChildren(); return; }
    b.replaceChildren(...pending.map((a) => card(a, true)), msg ? el("div", { class: "small" }, msg) : null);
  }

  async function refresh() {
    let d;
    try { d = await api("/api/approvals"); } catch (e) { return; }
    const key = JSON.stringify([d.pending.map((a) => [a.id, a.seconds_left]), msg, busy, window.JL && JL.lang]);
    if (key === last) return;
    last = key;
    const typing = document.activeElement && document.activeElement.classList.contains("ap-code");
    if (!typing) banner(d.pending);
    const page = document.getElementById("apPage");
    if (page && !typing) draw(page, d);
  }

  function draw(page, d) {
    const rows = (d.history || []).map((h) => el("tr", {},
      el("td", { class: "small" }, new Date(h.ts * 1000).toLocaleString()), el("td", {}, h.decision), el("td", {}, h.channel),
      el("td", {}, h.kind), el("td", {}, (RISK[h.risk] || ["", h.risk])[he() ? 0 : 1]), el("td", { dir: "auto" }, h.title)));
    page.replaceChildren(
      el("div", { class: "card" }, el("h2", {}, T("ממתין לאישור שלך", "Waiting for your approval")),
        d.pending.length ? d.pending.map((a) => card(a, false)) : el("div", { class: "muted" }, T("אין בקשות ממתינות.", "Nothing is waiting.")),
        msg ? el("div", { class: "small" }, msg) : null),
      el("div", { class: "card" }, el("h2", {}, T("החלטות אחרונות", "Recent decisions")),
        rows.length ? el("table", { class: "table" }, el("thead", {}, el("tr", {}, ...[T("זמן", "Time"), T("החלטה", "Decision"),
          T("איפה", "Where"), T("סוג", "Kind"), T("סיכון", "Risk"), T("מה", "What")].map((x) => el("th", {}, x)))), el("tbody", {}, rows))
          : el("div", { class: "muted" }, T("עוד אין החלטות.", "No decisions yet."))));
  }

  async function pageApprovals() {
    render(header(T("אישורים", "Approvals"), T("כל מה ש-JARVES ביקש לאשר: אשר או דחה כאן. בלי תשובה = נדחה.",
      "Everything JARVES asked you to approve: approve or deny here. No answer = denied.")), el("div", { id: "apPage" }));
    last = "";
    await refresh();
  }
  if (typeof PAGES !== "undefined") Object.assign(PAGES, { approvals: pageApprovals });
  setInterval(refresh, 2000);
  document.addEventListener("DOMContentLoaded", refresh);
})();
