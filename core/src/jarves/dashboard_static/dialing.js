// SW-V4 H Calls page: your saved contacts with a Call button, and a box for a number you type yourself.
// "Call" never dials: it reads the number back, asks for your approval (popup / this dashboard), and only then opens
// Phone Link (or the default dialer) so YOU press Call. Numbers on screen are masked; contact names are user content
// and are never translated (data-noi18n). Uses the helpers of app.js (el, api, render, header, pill) and pages.js.
"use strict";

PAGES.calls = async function () {
  const d = await api("/api/dialing");
  const msg = el("div", { class: "small", role: "status", "aria-live": "polite", style: "margin-top:8px" });
  const say = (r) => { msg.textContent = tx(r.message_he || r.code || "", r.message || r.code || ""); };
  const call = async (payload, btn) => {
    if (btn) btn.disabled = true;
    msg.textContent = tx("ממתין לאישור שלך על המסך…", "Waiting for your approval on the screen…");
    try {
      const r = await api("/api/dialing/call", { method: "POST", body: JSON.stringify(payload) });
      say(r);
    } catch (e) { msg.textContent = e.message; }
    if (btn) btn.disabled = false;
  };
  if (!d.enabled) {
    render(header(tx("שיחות טלפון", "Phone calls"), tx("חיוג מסייע דרך Phone Link: אתם לוחצים חיוג.", "Assisted dialing through Phone Link: you press Call.")),
      el("div", { class: "card" }, el("div", { class: "empty" }, tx("חיוג מסייע כבוי: ", "Assisted dialing is off: ") + (d.reason || "")),
        el("a", { href: "#/features" }, tx("עמוד התכונות", "Features page"))));
    return;
  }
  const h = d.handler || {};
  const app = h.kind === "none" ? tx("אין אפליקציה לחיוג (tel:)", "No app handles dialing (tel:)") : (h.app || "—");
  const rows = d.contacts.map((c) => el("tr", {},
    el("td", { dir: "auto", "data-noi18n": "" }, c.name),
    el("td", { class: "small muted", dir: "ltr", "data-noi18n": "" }, c.numbers.map((n) => `${n.label} ${n.masked}`).join(" · ")),
    el("td", { class: "small muted" }, c.numbers.map((n) => n.source).filter((v, i, a) => a.indexOf(v) === i).join(", ")),
    el("td", {}, el("button", { type: "button", class: "primary", "aria-label": tx("התקשר ל", "Call ") + c.name,
      onclick: (ev) => call({ name: c.name }, ev.target) }, tx("התקשר", "Call")))));
  const number = el("input", { type: "tel", dir: "ltr", inputmode: "tel", placeholder: "054-123-4567", "aria-label": tx("מספר טלפון", "Phone number"),
    style: "min-width:200px" });
  const who = el("input", { type: "text", dir: "auto", placeholder: tx("שם (לא חובה)", "Name (optional)"), "aria-label": tx("שם", "Name") });
  render(header(tx("שיחות טלפון", "Phone calls"), tx("ג'ארביס לא מחייג בעצמו: הוא פותח את החייגן ואתם לוחצים חיוג.", "JARVES never places the call: it opens the dialer and you press Call.")),
    el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" },
      el("b", {}, tx("אפליקציית החיוג: ", "Dialer app: ")), el("span", {}, app),
      h.phone_link === "not_installed" || h.phone_link === "no_data" ? pill("medium", tx("Phone Link לא מחובר", "Phone Link not set up")) : null)),
    el("div", { class: "card", style: "margin-top:10px" },
      d.contacts.length ? el("table", { class: "table" },
        el("thead", {}, el("tr", {}, el("th", {}, tx("שם", "Name")), el("th", {}, tx("מספר", "Number")), el("th", {}, tx("מקור", "Source")), el("th", {}, ""))),
        el("tbody", {}, rows)) :
        el("div", { class: "empty" }, tx("אין אנשי קשר עם מספר. אמרו לג'ארביס מספר פעם אחת, או הקלידו כאן.", "No contacts with a number yet. Tell JARVES a number once, or type one here."))),
    el("div", { class: "card", style: "margin-top:10px" }, el("b", {}, tx("מספר שאני מקליד בעצמי", "A number I type myself")),
      el("div", { class: "row", style: "gap:8px;flex-wrap:wrap;margin-top:6px" }, who, number,
        el("button", { type: "button", class: "primary", onclick: (ev) => call({ number: number.value, name: who.value }, ev.target) }, tx("התקשר", "Call"))),
      el("div", { class: "muted small", style: "margin-top:6px" }, tx("מספרים ממייל, מאתר או מהודעה לא מחויגים אוטומטית: הקלידו אותם בעצמכם.",
        "Numbers from an e-mail, a web page or a message are never dialled for you: type them yourself."))),
    msg);
};
