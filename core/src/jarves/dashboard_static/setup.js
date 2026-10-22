// Setup & add-ons (#/addons; #/setup is the Setup and tour wizard, wizard.js): the dashboard face of `jarvis setup` (setup_wizard.py). Same groups, same
// recommendations and availability as the CLI wizard (GET /api/setup); the switches are the Features switches
// (POST /api/features/<id>), names / wake phrase / voice link to the Identity & voice page, and every add-on links
// to its Guide. Nothing is duplicated here. Uses the app.js helpers (el, api, render, header, pill, route).
"use strict";

(function () {
  let lang = JL.lang;                                  // one dashboard locale (i18n.js)
  JL.onChange((l) => { lang = l; });
  const L = (he, en) => tx(he, en);
  const T = (o, k) => txo(o, k);                       // <key>_<locale> with the English fallback (i18n.js)
  const dir = () => JL.dir();

  async function pageSetup() {
    const d = await api("/api/setup");
    const top = el("div", { class: "card", dir: dir() },
      el("div", {}, L("אותה הגדרה כמו ", "The same setup as "), el("code", {}, "jarvis setup"),
        L(". כל שינוי כאן נשמר מיד ואפשר לשנות שוב בכל זמן.", ". Every change here is saved at once and can be changed again any time.")),
      el("div", { class: "small", style: "margin-top:6px" },
        el("a", { href: "#/identity" }, L("שם, מילת הפעלה וקול", "Name, wake phrase and voice")), " · ",
        el("a", { href: "#/connections" }, L("חיבורים (OpenAI, ChatGPT, גוגל)", "Connections (OpenAI, ChatGPT, Google)")), " · ",
        el("a", { href: "#/guides" }, L("כל המדריכים", "All guides")), " · ",
        el("a", { href: "#/features" }, L("טבלת יכולות מלאה", "Full capability matrix"))),
      el("div", { class: "small muted", style: "margin-top:6px" }, L("חיבור: ", "Connection: "), d.connection,
        d.finished_at ? "" : L(" · ההגדרה עוד לא הושלמה", " · setup not finished yet")));
    const cards = d.groups.map((g) => el("div", { class: "card" },
      el("h2", { dir: dir() }, T(g, "title")),
      el("div", {}, g.features.map((f) => {
        const msg = el("span", { class: "muted small" });
        const sw = el("button", { type: "button", class: "switch", "aria-pressed": f.enabled ? "true" : "false",
          "aria-label": switchLabel(txo(f, "title"), f.enabled), onclick: async () => {
            if (!f.enabled && f.risk === "high" && !confirm(L(`להפעיל את ${f.title_he}? זו יכולת רגישה.`, `Turn on ${f.title_en}? This is a sensitive feature.`))) return;
            try { await api("/api/features/" + encodeURIComponent(f.id), { method: "POST", body: JSON.stringify({ enabled: !f.enabled }) }); route(true); }
            catch (e) { msg.textContent = L("לא שונה: ", "Not changed: ") + e.message; }
          } }, f.enabled ? L("פועל", "On") : L("כבוי", "Off"));
        const avail = f.available ? pill("ok", L("זמין", "available"))
          : pill("unavailable", L("לא זמין", "not available"));
        const h = f.hint || {};
        const why = !f.available ? el("div", { class: "small", dir: JL.textDir(T({ x_he: h.he, x_en: h.en }, "x") || txh(f, "reason")) }, T({ x_he: h.he, x_en: h.en }, "x") || txh(f, "reason"),
          h.link ? el("span", {}, " ", el("a", { href: h.link, target: "_blank", rel: "noopener noreferrer" }, L("הורדה", "download"))) : null) : null;
        const notes = [T(f, "cost"), T(f, "privacy")].filter(Boolean);
        return el("div", { class: "row", style: "align-items:flex-start;gap:12px;padding:8px 0;border-bottom:1px solid var(--line)" },
          el("div", { style: "flex:1", dir: dir() },
            el("b", {}, txo(f, "title")), " ", pill(f.risk, ({ low: L("סיכון נמוך", "low risk"), medium: L("סיכון בינוני", "medium risk"), high: L("סיכון גבוה", "high risk") })[f.risk] || f.risk), " ", avail,
            f.enabled !== f.recommended ? el("span", { class: "small muted" }, " · ", L("מומלץ: ", "recommended: "), f.recommended ? L("פועל", "on") : L("כבוי", "off")) : null,
            el("div", { class: "muted small" }, txh(f, "about")),
            notes.length ? el("div", { class: "small" }, notes.join(" · ")) : null, why,
            f.guide ? el("div", { class: "small" }, el("a", { href: "#/guide/" + f.guide, "aria-label": L("מדריך", "Guide") + ": " + txo(f, "title") }, L("מדריך", "Guide"))) : null),
          el("div", {}, sw, msg));
      }))));
    const next = d.next_steps.length ? el("div", { class: "card", dir: dir() }, el("h2", {}, L("מה לעשות עכשיו", "What to do next")),
      el("ul", {}, d.next_steps.map((s) => el("li", {}, el("a", { href: s.dashboard }, T(s, "title")), ": ", T(s, "step"),
        s.reason ? el("span", { class: "muted small" }, " (" + txh(s, "reason") + ")") : null)))) : null;
    render(header(L("הגדרה ותוספים", "Setup & add-ons"),
      L("כל התוספים, מקובצים: מה פועל, מה זמין, ומה צריך כדי שיעבוד.", "Every add-on, grouped: what is on, what is available, and what it needs.")),   // language: the picker in the top bar
      top, next, cards);
  }

  // nav entry (index.html is shared; add it once, before Features)
  const nav = document.getElementById("nav");
  if (nav && !nav.querySelector('[data-page="addons"]')) {
    const a = el("a", { href: "#/addons", "data-page": "addons" }, el("span", {}, "Setup & add-ons ", el("small", { class: "he" }, "הגדרה ותוספים")));
    const before = nav.querySelector('[data-page="features"]');
    before ? before.before(a) : nav.append(a);
  }
  PAGES.addons = pageSetup;
})();
