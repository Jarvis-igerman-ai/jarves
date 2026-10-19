// SW-V2 WP-L: Guides. One page per feature (what it is, live state and why, steps with buttons, what to say, Test
// now, cost/privacy, problems, Windows/Pi status), the index, the Home "What should I do first?" card and a
// "Guide" link on every feature's own page. Data: /api/guides (guides/*.toml + live features.status()).
// Uses the app.js helpers (el, api, render, header, pill) and registers in PAGES (pages.js). Text via textContent.
"use strict";

(function () {
  let lang = "he";
  try { lang = localStorage.getItem("jarves-guide-lang") || "he"; } catch (e) { /* storage blocked */ }
  const T = (o, k) => (lang === "he" ? o[k + "_he"] : o[k + "_en"]) || o[k + "_en"] || o[k + "_he"] || "";
  const H = (o) => (lang === "he" ? o.he : o.en) || o.en || o.he || "";      // steps / problems: {he, en}
  const dirOf = () => (lang === "he" ? "rtl" : "ltr");
  const STATE = {
    on: ["ok", "פועל", "On"], setup: ["unavailable", "צריך הגדרה", "Setup needed"], off: ["plain", "כבוי", "Off"],
    partly: ["medium", "חלקית", "Partly"], planned: ["plain", "עוד לא קיים", "Not built yet"],
    missing: ["plain", "לא בגרסה הזאת", "Not in this version"], info: ["plain", "מידע", "Info"] };
  const CATS = { start: ["להתחלה", "Getting started"], voice: ["קול", "Voice"], everyday: ["יום-יום", "Everyday"],
    google: ["גוגל", "Google"], memory: ["זיכרון ואופי", "Memory and personality"], camera: ["מצלמה", "Camera"],
    costs: ["עלויות", "Costs"], system: ["מערכת", "System"], privacy: ["פרטיות", "Privacy"], other: ["אחר", "Other"] };
  const PLAT = { verified_on_device: ["ok", "verified"], implemented_unverified: ["medium", "not yet verified"],
    planned: ["low", "planned"], blocked: ["fail", "blocked"], not_applicable: ["plain", "n/a"] };
  const L = (he, en) => (lang === "he" ? he : en);
  const statePill = (s) => { const x = STATE[s] || STATE.info; return pill(x[0], L(x[1], x[2])); };

  function langToggle() {
    return el("button", { type: "button", class: "ghost", onclick: () => {
      lang = lang === "he" ? "en" : "he";
      try { localStorage.setItem("jarves-guide-lang", lang); } catch (e) { /* ignore */ }
      route(true);
    } }, lang === "he" ? "English" : "עברית");
  }

  // ------------------------------------------------------------- index
  async function pageGuides() {
    const d = await api("/api/guides");
    const byCat = {};
    for (const g of d.guides) (byCat[g.category] = byCat[g.category] || []).push(g);
    const cards = Object.entries(byCat).map(([cat, gs]) => el("div", { class: "card guide-cat" },
      el("h2", { dir: dirOf() }, L(...(CATS[cat] || CATS.other))),
      el("div", { class: "guide-grid" }, gs.map((g) => el("a", { class: "guide-tile" + (g.setup_needed ? " need" : ""), href: "#/guide/" + g.id },
        el("div", { class: "row" }, el("b", { dir: dirOf() }, T(g, "title")), el("span", { class: "spacer" }), statePill(g.state)),
        el("div", { class: "small muted", dir: dirOf() }, T(g, "what")),
        g.reason && g.state !== "on" ? el("div", { class: "small", dir: "auto" }, g.reason) : null)))));
    const h = d.help || {};
    const snoozed = Object.keys(h.snoozed || {}).concat(h.dismissed || []);
    const help = el("div", { class: "card small guide-sec" }, el("b", {}, L("הצעות עזרה", "Setup suggestions")), " ",
      h.proactive_help === false ? pill("plain", L("כבוי", "off")) : pill("ok", L("פועל", "on")), " ",
      el("span", { class: "muted", dir: dirOf() }, L("ג'ארביס מציע לכל היותר דבר אחד בשיחה, ואותו דבר לכל היותר פעם בשבוע. \"תפסיק להציע\" מכבה.",
        "JARVES suggests at most one thing per conversation, the same one at most once a week. \"Stop suggesting\" turns it off.")),
      snoozed.length ? el("div", { style: "margin-top:6px" }, L("נדחו: ", "Snoozed: "), snoozed.join(", "), " ",
        el("button", { type: "button", class: "ghost", onclick: async () => { await api("/api/guides-help", { method: "POST", body: JSON.stringify({ op: "reset" }) }); route(true); } }, L("אפס", "Reset"))) : null);
    render(header(L("מדריכים", "Guides"), L(`מדריך פשוט לכל יכולת. ${d.setup_needed} צריכים הגדרה.`, `A simple guide for every feature. ${d.setup_needed} need setup.`), langToggle()),
      checklistCard(d.checklist), help, cards);
  }

  function checklistCard(items) {
    const done = items.filter((c) => c.done).length;
    return el("div", { class: "card checklist" }, el("div", { class: "row" },
      el("b", { dir: dirOf() }, L("מה לעשות קודם?", "What should I do first?")), el("span", { class: "spacer" }),
      el("span", { class: "muted small" }, `${done} / ${items.length}`)),
      el("ol", { dir: dirOf() }, items.map((c) => el("li", { class: c.done ? "done" : "" },
        el("a", { href: "#/guide/" + c.id }, T(c, "title")), " ", c.done ? pill("ok", L("בוצע", "done")) : statePill(c.state),
        !c.done ? el("div", { class: "small muted", dir: dirOf() }, T(c, "first_step")) : null))));
  }

  // ------------------------------------------------------------- one guide
  async function pageGuide(id) {
    const g = await api("/api/guides/" + encodeURIComponent(id));
    const msg = el("div", { class: "small", "aria-live": "polite" });
    const testOut = el("div", { class: "test-out", "aria-live": "polite" });
    const runTest = async () => {
      testOut.replaceChildren(el("span", { class: "muted" }, L("בודק…", "Checking…")));
      try { const r = await api(`/api/guides/${g.id}/test`, { method: "POST", body: "{}" });
        testOut.replaceChildren(pill(r.ok ? "ok" : "fail", r.ok ? L("עובד", "works") : L("עוד לא", "not yet")), " ", el("span", { dir: dirOf() }, lang === "he" ? r.he : r.en));
      } catch (e) { testOut.textContent = e.message; }
    };
    const switchFeature = async (fid, on, risk, title) => {
      if (on && risk === "high" && !confirm(L(`להפעיל את ${title}? זו יכולת בסיכון גבוה.`, `Turn on ${title}? This is a high-risk feature.`))) return;
      try { await api("/api/features/" + encodeURIComponent(fid), { method: "POST", body: JSON.stringify({ enabled: on }) }); route(true); }
      catch (e) { msg.textContent = e.message; }
    };
    const rowOf = (fid) => g.feature_rows.find((r) => r.id === fid);
    const actionBtn = (s) => {
      const [kind, arg] = [s.action.split(":")[0], s.action.slice(s.action.indexOf(":") + 1)];
      if (kind === "feature_on") {
        const r = rowOf(arg);
        if (!r) return el("span", { class: "muted small" }, L("(לא בגרסה הזאת)", "(not in this version)"));
        return el("button", { type: "button", class: r.enabled ? "" : "primary", disabled: r.enabled ? true : null,
          onclick: () => switchFeature(arg, true, r.risk, T(r, "title")) }, r.enabled ? L("דולק", "On") : L("הפעל", "Turn on"));
      }
      if (kind === "open") return el("a", { class: "button-link", href: arg }, L("פתח", "Open"));
      if (kind === "command") return el("span", {}, el("code", { dir: "ltr" }, arg), " ",
        el("button", { type: "button", class: "ghost", onclick: async (ev) => {
          try { await navigator.clipboard.writeText(arg); ev.target.textContent = L("הועתק", "Copied"); } catch (e) { ev.target.textContent = L("העתיקו ידנית", "Copy it by hand"); }
        } }, L("העתק", "Copy")));
      if (kind === "test") return el("button", { type: "button", class: "primary", onclick: runTest }, L("בדוק עכשיו", "Test now"));
      if (kind === "set_budget") return el("button", { type: "button", class: "primary", onclick: async () => {
        try { const r = await api(`/api/guides/${g.id}/step/${s.n}`, { method: "POST", body: "{}" }); msg.textContent = L(`נקבע: $${r.api_monthly_budget_usd} לחודש`, `Set: $${r.api_monthly_budget_usd} a month`); route(true); }
        catch (e) { msg.textContent = e.message; } } }, L(`קבע $${arg}`, `Set $${arg}`));
      return null;
    };
    const steps = el("ol", { class: "steps", dir: dirOf() }, g.steps.map((s) => el("li", { class: s.done === true ? "done" : "" },
      el("div", { dir: dirOf() }, H(s)),
      el("div", { class: "step-actions" }, actionBtn(s), s.done === true ? pill("ok", L("בוצע", "done")) : s.done === false ? pill("plain", L("עוד לא", "not yet")) : null))));
    const feats = g.feature_rows.length ? el("table", {}, el("tbody", {}, g.feature_rows.map((r) => el("tr", {},
      el("td", {}, el("button", { type: "button", class: "switch", "aria-pressed": r.enabled ? "true" : "false", "aria-label": `${r.title_en}: ${r.enabled ? "on" : "off"}`,
        onclick: () => switchFeature(r.id, !r.enabled, r.risk, T(r, "title")) }, r.enabled ? "On" : "Off")),
      el("td", {}, el("b", { dir: dirOf() }, T(r, "title")), " ", pill(r.risk, r.risk)),
      el("td", {}, r.active ? pill("ok", L("פועל", "active")) : !r.enabled ? pill("plain", L("כבוי", "off")) : pill("unavailable", L("לא זמין", "unavailable")),
        r.reason && !r.active ? el("div", { class: "small", dir: "auto" }, r.reason) : null))))) : null;
    const plat = (t) => { if (!t) return "—"; const p = PLAT[t.status] || ["plain", t.status];
      return el("div", {}, pill(p[0], p[1]), t.evidence ? el("div", { class: "muted small" }, t.evidence) : null,
        t.blocker ? el("div", { class: "small" }, "Blocker: " + t.blocker) : null,
        t.roadmap_issue ? el("a", { class: "small", href: t.roadmap_issue, target: "_blank", rel: "noopener" }, "roadmap issue") : null); };
    const section = (title, ...body) => el("div", { class: "card guide-sec" }, el("h2", { dir: dirOf() }, title), ...body);
    render(header(T(g, "title"), null, el("a", { href: "#/guides" }, L("כל המדריכים ←", "← all guides")), langToggle()),
      el("div", { class: "card guide-head" }, el("div", { class: "row" }, statePill(g.state),
        g.reason && g.state !== "on" ? el("span", { dir: "auto" }, g.reason) : null),
        el("p", { class: "lead", dir: dirOf() }, T(g, "what")), feats, msg),
      section(L("איך מפעילים", "How to turn it on"), steps),
      g.say.length ? section(L("מה להגיד לו", "What to say"), el("ul", { class: "say" }, g.say.map((s) =>
        el("li", {}, el("span", { dir: "rtl", lang: "he" }, `“${s.he}”`), el("span", { class: "muted", dir: "ltr" }, ` · “${s.en}”`))))) : null,
      el("div", { class: "grid2 guide-pair" },
        section(L("מה לצפות", "What to expect"), el("p", { dir: dirOf() }, T(g, "expect"))),
        section(L("לבדוק שזה עובד", "Check it works"), el("p", { class: "small", dir: dirOf() }, T(g, "test")),
          g.test ? el("button", { type: "button", class: "primary", onclick: runTest }, L("בדוק עכשיו", "Test now")) : null, testOut)),
      el("div", { class: "grid2 guide-pair" },
        section(L("עלות", "Cost"), el("p", { dir: dirOf() }, T(g, "cost"))),
        section(L("פרטיות", "Privacy"), el("p", { dir: dirOf() }, T(g, "privacy")))),
      g.problems.length ? section(L("בעיות נפוצות", "Common problems"), el("dl", { class: "problems", dir: dirOf() }, g.problems.flatMap((p) =>
        [el("dt", {}, H(p)), el("dd", {}, lang === "he" ? p.fix_he : p.fix_en)]))) : null,
      section(L("Windows ו-Raspberry Pi", "Windows and Raspberry Pi"), el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Windows"), el("th", {}, "Raspberry Pi"))),
        el("tbody", {}, el("tr", {}, el("td", {}, plat(g.platforms.windows)), el("td", {}, plat(g.platforms.pi)))))),
      g.links.length ? el("div", { class: "card small guide-sec" }, L("ראו גם: ", "See also: "), g.links.map((l, i) => [i ? " · " : "", el("a", { href: "#/guide/" + l.id }, T(l, "title"))])) : null);
  }

  async function pageGuideFor(fid) {           // Features page row link -> the guide that covers that feature
    const d = await api("/api/guides");
    const g = d.guides.find((x) => x.features.includes(fid));
    location.replace("#/" + (g ? "guide/" + g.id : "guides"));
  }

  // Home card (called by app.js pageHome when present): the same checklist, short.
  window.guidesSetupCard = async function () {
    try { const d = await api("/api/guides"); return checklistCard(d.checklist); } catch (e) { return null; }
  };

  // "Guide" link on each feature's own page (pages listed in the guide data), added after the page renders.
  let pageMap = null;
  async function addGuideLink() {
    const page = location.hash.replace(/^#\/?/, "").split(/[/?]/)[0] || "home";
    if (page === "guide" || page === "guides") return;
    if (pageMap === null) {
      pageMap = {};
      try { for (const g of (await api("/api/guides")).guides) for (const p of g.pages || []) pageMap[p] = g; } catch (e) { return; }
    }
    const g = pageMap[page];
    const head = document.querySelector("#main > .row");
    if (!g || !head || head.querySelector(".guide-link")) return;
    head.append(el("a", { class: "button-link guide-link", href: "#/guide/" + g.id, title: g.title_en }, L("מדריך", "Guide")));
  }
  new MutationObserver(() => { addGuideLink(); }).observe(document.getElementById("main"), { childList: true });

  // nav entry (index.html is shared with other packages; add it here once)
  const nav = document.getElementById("nav");
  const badge = el("span", { class: "count", id: "guideCount", title: "features that need setup" });
  if (nav && !nav.querySelector('[data-page="guides"]')) {
    const a = el("a", { href: "#/guides", "data-page": "guides" }, "Guides ", badge);
    const after = nav.querySelector('[data-page="features"]');
    after ? after.after(a) : nav.append(a);
  }
  async function refreshBadge() {     // "Setup needed" count next to Guides in the side bar
    try { const d = await api("/api/guides"); badge.textContent = d.setup_needed || ""; } catch (e) { /* page shows errors */ }
  }
  refreshBadge();
  setInterval(refreshBadge, 30000);

  PAGES.guides = pageGuides;
  PAGES.guide = pageGuide;
  PAGES["guide-for"] = pageGuideFor;
})();
