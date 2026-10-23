// JARVES dashboard: Approvals (V3, polished in SW-V4 K). A banner at the top of EVERY page while JARVES waits for
// the owner's approval, and the #/approvals page: the pending requests (with the question he may say aloud), the
// decision history with filters. Data: GET /api/approvals (approvals.py); answers: POST /api/approvals/<id>
// {approved, typed}. The dashboard click IS the owner's answer; typed requests need the code.
// The banner sits in the page flow above the content (not fixed): it never covers the page, also on phones.
"use strict";

(function () {
  const RISK = { low: ["נמוך", "low"], medium: ["בינוני", "medium"], high: ["גבוה", "high"] };
  const DECISION = { approved: ["אושר", "approved"], denied: ["נדחה", "denied"], timeout: ["פג תוקף (נדחה)", "timed out (denied)"],
    cancelled: ["בוטל", "cancelled"], pending: ["ממתין", "waiting"] };
  const CHANNEL = { voice: ["בקול", "voice"], dashboard: ["לוח הבקרה", "dashboard"], popup: ["חלון אישור", "approval window"],
    terminal: ["מסוף", "terminal"], cli: ["מסוף", "terminal"], telegram: ["טלגרם", "Telegram"], "": ["—", "—"] };
  const KIND = { send: ["שליחה", "send"], message: ["הודעה", "message"], tool: ["כלי", "tool"], shell: ["פקודה", "command"],
    install: ["התקנה", "install"], calendar: ["יומן", "calendar"], feature: ["יכולת", "feature"], camera: ["מצלמה", "camera"],
    schedule: ["תזמון", "schedule"], profile: ["פרופיל", "profile"], email: ["מייל", "e-mail"] };
  const t2 = (map, k) => { const p = map[k]; return p ? tx(p[0], p[1]) : String(k || "—"); };
  // "auto-deny in 59 min" instead of "3600 s"
  const left = (s) => s >= 120 ? tx(`נדחה אוטומטית בעוד ${Math.round(s / 60)} דק׳`, `auto-deny in ${Math.round(s / 60)} min`)
    : tx(`נדחה אוטומטית בעוד ${s} שנ׳`, `auto-deny in ${s} s`);
  // privacy mode keeps only the length of a title ("<21 chars>"): say so in words
  const masked = (s) => { const m = /^<(\d+) chars>$/.exec(String(s || "")); return m ? tx(`(מוסתר בפרטיות, ${m[1]} תווים)`, `(hidden by privacy, ${m[1]} characters)`) : s; };
  let last = "", busy = false, msg = "";
  const filt = { decision: "", q: "" };

  function card(a, compact) {
    const typed = el("input", { type: "text", inputmode: "numeric", maxlength: "6", class: "ap-code",
      "aria-label": tx("קוד אישור", "Approval code"), placeholder: a.typed_code || "" });
    const send = async (approved) => {
      busy = true; msg = "";
      try { await api("/api/approvals/" + encodeURIComponent(a.id), { method: "POST",
        body: JSON.stringify({ approved, typed: a.typed ? typed.value.trim() : "" }) }); }
      catch (e) { msg = e.message; }
      busy = false; last = ""; refresh();
    };
    const r = RISK[a.risk] || RISK.high;
    const spoken = tx(a.spoken_he, a.spoken_en);
    const details = Object.entries(a.details || {}).map(([k, v]) =>
      [el("dt", {}, k), el("dd", {}, el("code", { dir: "ltr" }, String(v)))]);
    return el("div", { class: `ap-card ap-${a.risk}`, role: "alertdialog", "aria-labelledby": "apt-" + a.id + (compact ? "b" : "p") },
      el("div", { class: "ap-head" }, el("b", {}, tx("JARVES מבקש אישור", "JARVES asks for approval")),
        el("span", { class: "pill ap-risk " + (a.risk === "high" ? "high" : a.risk === "medium" ? "medium" : "low") }, tx("סיכון", "Risk") + ": " + tx(r[0], r[1])),
        el("span", { class: "muted small" }, left(a.seconds_left))),
      el("div", { class: "ap-title", dir: "auto", "data-user": "", id: "apt-" + a.id + (compact ? "b" : "p") }, a.title),
      a.prompt && a.prompt !== a.title ? el("div", { dir: "auto", class: "ap-prompt", "data-user": "" }, a.prompt) : null,
      a.why ? el("div", { class: "small ap-why" }, el("span", { class: "muted" }, tx("למה (במילים של JARVES): ", "Why (JARVES's words): ")),
        el("span", { dir: "auto", "data-user": "" }, a.why)) : null,
      !compact && spoken ? el("div", { class: "small" }, el("span", { class: "muted" }, tx("מה הוא יגיד בקול: ", "What he says aloud: ")), spoken) : null,
      !compact && details.length ? el("dl", { class: "kvlist small" }, details.flat()) : null,
      a.typed ? el("label", { class: "ap-typed" }, tx(`פעולה מסוכנת: הקלידו ${a.typed_code} כדי לאשר `, `Dangerous: type ${a.typed_code} to approve `), typed) : null,
      el("div", { class: "row" },
        el("button", { type: "button", class: "primary", disabled: busy ? true : null, onclick: () => send(true) }, tx("אישור", "Approve")),
        el("button", { type: "button", disabled: busy ? true : null, onclick: () => send(false) }, tx("דחייה", "Deny")),
        compact ? el("a", { href: "#/approvals", class: "small" }, tx("פרטים", "Details")) : null));
  }

  function banner(pending) {
    let b = document.getElementById("apBanner");
    if (!pending.length) { if (b) b.remove(); return; }
    if (!b) {
      b = el("div", { id: "apBanner", class: "ap-banner", "aria-live": "assertive" });
      const main = document.getElementById("main");
      main.parentNode.insertBefore(b, main);                       // in the flow above the page content
    }
    if (location.hash.startsWith("#/approvals") || location.hash.startsWith("#/chat")) { b.replaceChildren(); b.hidden = true; return; }   // the chat shows them inline
    b.hidden = false;
    b.replaceChildren(...pending.map((a) => card(a, true)), ...(msg ? [el("div", { class: "small", role: "alert" }, msg)] : []));
  }

  async function refresh() {
    let d;
    try { d = await api("/api/approvals"); } catch (e) { return; }
    const badge = document.getElementById("apCount");                 // sidebar: how many requests wait for you
    if (badge) { const n = d.pending.length; badge.textContent = n ? String(n) : ""; badge.title = tx(`${n} ממתינים לאישור`, `${n} waiting for your approval`); }
    const key = JSON.stringify([d.pending.map((a) => [a.id, Math.round(a.seconds_left / 30)]), d.history.length, msg, busy,
      window.JL && JL.lang, location.hash, filt]);
    if (key === last) return;
    last = key;
    const act = document.activeElement;
    const typing = act && (act.classList.contains("ap-code") || act.closest && act.closest(".ap-filters"));
    if (!typing) banner(d.pending);
    const page = document.getElementById("apPage");
    if (page && !typing) draw(page, d);
  }

  function filters() {
    const sel = el("select", { "aria-label": tx("סינון לפי החלטה", "Filter by decision"), onchange: () => { filt.decision = sel.value; last = ""; refresh(); } },
      el("option", { value: "" }, tx("כל ההחלטות", "All decisions")),
      ...["approved", "denied", "timeout", "cancelled"].map((k) => el("option", { value: k }, t2(DECISION, k))));
    sel.value = filt.decision;
    const q = el("input", { type: "search", placeholder: tx("חיפוש בהיסטוריה…", "Search the history…"), "aria-label": tx("חיפוש בהיסטוריה", "Search the history"),
      dir: "auto", value: filt.q, oninput: () => { filt.q = q.value; clearTimeout(q._t); q._t = setTimeout(() => { last = ""; refresh(); }, 250); } });
    return el("div", { class: "row ap-filters" }, sel, q);
  }

  function draw(page, d) {
    const hist = (d.history || []).filter((h) => (!filt.decision || h.decision === filt.decision)
      && (!filt.q || `${h.title} ${h.kind} ${h.channel}`.toLowerCase().includes(filt.q.toLowerCase())));
    const rows = hist.map((h) => el("tr", {},
      el("td", { class: "small nowrap", "data-label": "Time" }, `${day(h.ts)} ${clock(h.ts)}`),
      el("td", { "data-label": "Decision" }, pill(h.decision === "approved" ? "ok" : h.decision === "denied" ? "high" : "plain", t2(DECISION, h.decision))),
      el("td", { "data-label": "Where" }, t2(CHANNEL, h.channel)),
      el("td", { "data-label": "Kind" }, t2(KIND, h.kind)),
      el("td", { "data-label": "Risk" }, t2(RISK, h.risk)),
      el("td", { dir: "auto", "data-label": "What", "data-user": "" }, masked(h.title))));
    const focusFilter = page.querySelector(".ap-filters input") === document.activeElement;
    page.replaceChildren(
      el("div", { class: "card" }, el("h2", {}, tx("ממתין לאישור שלך", "Waiting for your approval") + (d.pending.length ? ` (${d.pending.length})` : "")),
        d.pending.length ? d.pending.map((a) => card(a, false)) : el("div", { class: "empty" }, tx("אין בקשות ממתינות.", "Nothing is waiting.")),
        msg ? el("div", { class: "small", role: "alert" }, msg) : null),
      el("div", { class: "card" }, el("h2", {}, tx("החלטות אחרונות", "Recent decisions")), filters(),
        rows.length ? el("div", { class: "tscroll" }, el("table", { class: "stack" }, el("thead", {}, el("tr", {}, ...[tx("זמן", "Time"), tx("החלטה", "Decision"),
          tx("איפה", "Where"), tx("סוג", "Kind"), tx("סיכון", "Risk"), tx("מה", "What")].map((x) => el("th", {}, x)))), el("tbody", {}, rows)))
          : el("div", { class: "empty" }, (d.history || []).length ? tx("אין החלטות שמתאימות לסינון.", "No decision matches the filter.")
            : tx("עוד אין החלטות.", "No decisions yet."))));
    if (focusFilter) page.querySelector(".ap-filters input").focus();
  }

  async function pageApprovals() {
    render(header(tx("אישורים", "Approvals"), tx("כל מה ש-JARVES ביקש לאשר: מאשרים או דוחים כאן. בלי תשובה = נדחה.",
      "Everything JARVES asked you to approve: approve or deny here. No answer = denied.")), el("div", { id: "apPage" }, el("div", { class: "muted" }, tx("טוען…", "Loading…"))));
    last = "";
    await refresh();
  }
  if (typeof PAGES !== "undefined") Object.assign(PAGES, { approvals: pageApprovals });
  setInterval(refresh, 2000);
  window.addEventListener("hashchange", () => { last = ""; refresh(); });
  document.addEventListener("DOMContentLoaded", refresh);
})();
