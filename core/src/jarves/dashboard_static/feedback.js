// JAR-230 Feedback page: one tap saves the last exchange (redacted, local) as "that was wrong"; lists the last 7 days.
// Uses the helpers of app.js (el, api, render, header, fail, day) and tx (i18n.js). Saved texts go in via textContent only.
"use strict";

PAGES.feedback = async function () {
  let d;
  try { d = await api("/api/feedback"); } catch (e) { fail(e); return; }
  const note = el("input", { id: "fb-note", "aria-label": tx("הערה (לא חובה)", "Note (optional)"), placeholder: tx("הערה (לא חובה)", "Note (optional)") });
  const msg = el("span", { class: "muted", role: "status" });
  const save = async () => {
    try { await api("/api/feedback/wrong", { method: "POST", body: JSON.stringify({ note: note.value }) }); PAGES.feedback(); }
    catch (e) { msg.textContent = e.message; }
  };
  const rows = d.items.slice().reverse().map((it) => el("li", { "data-noi18n": "" },
    day(it.ts) + ": " + it.user + " → " + it.jarves + (it.note ? " (" + it.note + ")" : "")));
  render(header(tx("משוב", "Feedback"), tx("השיחה האחרונה נשמרת במחשב הזה בלבד, בלי פרטים אישיים.", "The last exchange is saved on this PC only, without personal details.")),
    el("div", { class: "card" }, el("div", { class: "row" }, note,
      el("button", { onclick: save }, tx("זה היה לא נכון", "That was wrong")), msg)),
    el("div", { class: "card" }, el("p", {}, tx(d.count + " פריטים ב-7 הימים האחרונים", d.count + " items in the last 7 days")),
      rows.length ? el("ul", {}, ...rows) : null));
};
