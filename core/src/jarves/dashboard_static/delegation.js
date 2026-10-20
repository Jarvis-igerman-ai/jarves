// SW-V4 D (#14): the strict delegation budget on the Costs page: today's delegated spend (used / held by live
// reservations / cap), the mode and the fallback-ladder step, and the open reservations, refreshed every 2 s while
// the page is open (GET /api/delegation). Uses the helpers of app.js (el, api, usd, tile, pill) and tx (i18n.js).
"use strict";

(function () {
  const orig = PAGES.costs;
  function card() {
    const box = el("div", { class: "card", id: "deleg-card", "aria-live": "polite" });
    const fill = (d) => {
      const stepTxt = { client: tx("בצד הלקוח (תקרה קשיחה)", "client (strict cap)"),
        server: tx("בצד השרת (ספירה בלבד)", "server (counted, not strictly capped)"),
        text_only: tx("טקסט בלבד, בלי חיפושים", "text only, no lookups") }[d.step] || d.step;
      const c = d.counters || {};
      box.replaceChildren(
        el("h2", { style: "margin-top:0" }, tx("חיפושים בשיחה: תקציב קשיח", "Spoken lookups: strict budget")),
        el("div", { class: "muted small" }, tx("כל חיפוש נשמר בתקציב לפני שהוא נשלח; בקשה שלא נכנסת לא נשלחת אף פעם.",
          "Every lookup is reserved before it is sent; a request that does not fit is never sent.")),
        el("div", { class: "tiles" },
          tile(tx("נוצל היום", "Used today"), usd(d.used_usd, 4), "", true),
          tile(tx("שמור עכשיו", "Held now"), usd(d.held_usd, 4), "", true),
          tile(tx("תקרה יומית", "Daily cap"), usd(d.limit_usd, 2), tx("לשאלה: ", "Per question: ") + usd(d.caps.turn_usd, 2)),
          tile(tx("נשאר", "Left"), usd(d.remaining_usd, 4), tx("בקשות היום: ", "Requests today: ") + `${d.requests_today} / ${d.requests_limit}`)),
        el("div", { class: "kv" },
          el("div", { class: "k" }, tx("מצב", "Mode")), el("div", { class: "v" }, d.mode === "client" ? pill("ok", stepTxt) : pill("plain", stepTxt)),
          el("div", { class: "k" }, tx("גיבוי", "Fallback")), el("div", { class: "v" }, d.fallback + (d.ladder_reason ? " · " + d.ladder_reason : "")),
          el("div", { class: "k" }, tx("נענו / נחסמו לפני שליחה", "Answered / refused before sending")),
          el("div", { class: "v" }, `${c.completed || 0} / ${c.cap_refused || 0}`),
          el("div", { class: "k" }, tx("בוטלו / חרגו מזמן", "Cancelled / timeouts")), el("div", { class: "v" }, `${c.cancelled || 0} / ${c.timeouts || 0}`)),
        el("h3", {}, tx("הזמנות פתוחות", "Open reservations")),
        d.reservations.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Kind"), el("th", { class: "num" }, tx("סכום", "Amount")),
          el("th", { class: "num" }, tx("תהליך", "Process")), el("th", { class: "num" }, tx("גיל", "Age")))),
          el("tbody", {}, d.reservations.map((r) => el("tr", {}, el("td", {}, el("code", {}, r.kind)),
            el("td", { class: "num" }, r.kind === "delegation_usd_micros" ? usd(r.n / 1e6, 4) : String(r.n)),
            el("td", { class: "num" }, String(r.pid)), el("td", { class: "num" }, `${Math.round(r.age_s)} s`)))))
          : el("div", { class: "empty" }, tx("אין הזמנות פתוחות.", "No open reservations.")));
    };
    const load = async () => {
      if (!document.body.contains(box) && box.dataset.started) return false;
      box.dataset.started = "1";
      try { fill(await api("/api/delegation")); } catch (e) { box.replaceChildren(el("div", { class: "empty" }, e.message)); }
      return true;
    };
    load();
    const iv = setInterval(async () => { if (!(await load())) clearInterval(iv); }, 2000);
    return box;
  }
  PAGES.costs = async function () {
    await orig.apply(this, arguments);
    const h = document.querySelector("#main .row");
    if (h && !document.getElementById("deleg-card")) h.after(card());
  };
})();
