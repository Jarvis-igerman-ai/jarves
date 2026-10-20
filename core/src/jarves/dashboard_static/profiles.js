// Profiles (Issue #17, profiles.py): the profile chip + switcher in the top bar, the red TEST PROFILE banner, and
// the Profiles page (#/profiles: list, create / copy, make default, open its dashboard, delete with the name typed).
// Every profile has its OWN dashboard (own process, port and login): "open" starts that profile's dashboard, this
// page never shows another profile's data. Uses the app.js helpers (el, api, render, header, pill, route) and i18n.js.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  let last = null;

  function banner(d) {
    let b = $("profileBanner");
    if (!d || !d.test) { if (b) b.remove(); return; }
    if (!b) {
      b = el("div", { id: "profileBanner", class: "profile-banner", role: "status" });
      const content = document.querySelector(".content");
      if (content) content.prepend(b);
    }
    b.replaceChildren(el("b", {}, tx("פרופיל בדיקה", "TEST PROFILE")), " · ",
      tx("לא הנתונים האמיתיים שלך. זיכרון, שיחות, עלויות ותקציב נפרדים.", "not your real data. Separate memory, conversations, costs and budget."));
  }

  function chip(d) {
    const bar = $("appbar");
    if (!bar || !d) return;
    let wrap = $("profileChip");
    if (!wrap) {
      wrap = el("div", { id: "profileChip", class: "profile-chip" });
      const before = $("statusChips");
      before ? before.before(wrap) : bar.append(wrap);
    }
    const sel = el("select", { "aria-label": tx("פרופיל", "Profile"), "data-noi18n": "", onchange: async (ev) => {
      const name = ev.target.value;
      if (name === d.current) return;
      if (name === "__manage") { location.hash = "#/profiles"; ev.target.value = d.current; return; }
      ev.target.value = d.current;
      if (!confirm(tx(`לפתוח את לוח הבקרה של הפרופיל ${name}? הוא נפתח בלשונית חדשה, עם הכניסה שלו.`,
        `Open profile ${name}'s dashboard? It opens in a new tab with its own login.`))) return;
      try { await api(`/api/profiles/${encodeURIComponent(name)}/open`, { method: "POST", body: "{}" }); }
      catch (e) { alert(e.message); }
    } },
    (d.profiles || []).map((p) => el("option", { value: p.name }, p.name + (p.test ? " · TEST" : ""))),
    el("option", { value: "__manage" }, tx("ניהול פרופילים…", "Manage profiles…")));
    sel.value = d.current;
    wrap.classList.toggle("test", !!d.test);
    wrap.replaceChildren(el("span", { class: "schip-k" }, tx("פרופיל", "Profile")), sel);
  }

  async function refresh() {
    try { last = await api("/api/profiles"); } catch (e) { last = null; }
    banner(last);
    chip(last);
    return last;
  }
  window.profileState = () => last;

  async function pageProfiles() {
    const d = await refresh();
    if (!d) { render(el("div", { class: "card empty" }, tx("לא ניתן לטעון את הפרופילים.", "Could not load the profiles."))); return; }
    const msg = el("div", { class: "small", role: "status" });
    const show = (text, bad) => { msg.textContent = text; msg.className = "small " + (bad ? "bad" : "ok"); };
    const rows = d.profiles.map((p) => {
      const actions = [];
      if (p.name !== d.current) actions.push(el("button", { type: "button", class: "ghost", onclick: async () => {
        try { await api(`/api/profiles/${encodeURIComponent(p.name)}/open`, { method: "POST", body: "{}" });
          show(tx(`לוח הבקרה של ${p.name} נפתח בלשונית חדשה.`, `${p.name}'s dashboard opens in a new tab.`)); }
        catch (e) { show(e.message, true); } } }, tx("פתיחה", "Open")));
      if (!p.active) actions.push(el("button", { type: "button", class: "ghost", onclick: async () => {
        try { await api(`/api/profiles/${encodeURIComponent(p.name)}/use`, { method: "POST", body: "{}" }); route(true); }
        catch (e) { show(e.message, true); } } }, tx("ברירת מחדל", "Make default")));
      if (p.name !== "default" && p.name !== d.current) actions.push(el("button", { type: "button", class: "danger", onclick: async () => {
        const typed = prompt(tx(`מחיקה של ${p.name} מוחקת את כל הזיכרון, השיחות, ההגדרות, המפתחות וההתחברויות שלו, ואי אפשר לבטל. הקלידו את שם הפרופיל (${p.name}) כדי למחוק:`,
          `Deleting ${p.name} removes ALL its memory, conversations, settings, keys and sign-ins and cannot be undone. Type the profile name (${p.name}) to delete it:`));
        if (typed === null) return;
        try { await api(`/api/profiles/${encodeURIComponent(p.name)}/delete`, { method: "POST", body: JSON.stringify({ confirm: typed }) });
          route(true); }
        catch (e) { show(e.message, true); } } }, tx("מחיקה", "Delete")));
      return el("tr", {},
        el("td", { dir: "ltr" }, el("b", {}, p.name), p.current ? el("span", { class: "muted small" }, " · ", tx("כאן", "this one")) : null),
        el("td", {}, p.test ? pill("medium", tx("בדיקה", "test")) : tx("רגיל", "normal")),
        el("td", {}, p.running ? pill("ok", tx("פועל", "running")) : pill("plain", tx("עצור", "stopped"))),
        el("td", { dir: "ltr" }, String(p.port)),
        el("td", {}, p.active ? tx("כן", "yes") : ""),
        el("td", {}, actions));
    });
    const table = el("table", {},
      el("thead", {}, el("tr", {}, [tx("שם", "Name"), tx("סוג", "Kind"), tx("מצב", "State"), tx("פורט", "Port"),
        tx("ברירת מחדל", "Default"), ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows));
    const name = el("input", { type: "text", maxlength: "24", placeholder: "dana", dir: "ltr", "aria-label": tx("שם הפרופיל", "Profile name") });
    const from = el("select", { "aria-label": tx("להעתיק מ", "Copy from"), "data-noi18n": "" },
      el("option", { value: "" }, tx("פרופיל ריק", "empty profile")),
      d.profiles.map((p) => el("option", { value: p.name }, tx("להעתיק הגדרות מ-", "copy settings from ") + p.name)));
    const mem = el("input", { type: "checkbox", id: "profCopyMem" });
    const test = el("input", { type: "checkbox", id: "profTest" });
    const create = el("button", { type: "button", onclick: async () => {
      try {
        const r = await api("/api/profiles", { method: "POST", body: JSON.stringify({ name: name.value.trim(),
          from: from.value || null, copy_memory: mem.checked, test: test.checked || null }) });
        show(tx(`נוצר הפרופיל ${r.profile.name}. הפעלה: jarvis --profile ${r.profile.name}`, `Created ${r.profile.name}. Start it: jarvis --profile ${r.profile.name}`));
        name.value = "";
        setTimeout(() => route(true), 0);
      } catch (e) { show(e.message, true); }
    } }, tx("יצירה", "Create"));
    const form = el("div", { class: "card" }, el("h2", {}, tx("פרופיל חדש", "New profile")),
      el("div", { class: "row", style: "gap:8px;flex-wrap:wrap;align-items:center" }, name, from,
        el("label", { for: "profCopyMem", class: "small" }, mem, " ", tx("להעתיק גם זיכרון (USER.md, MEMORY.md, עובדות)", "also copy memory (USER.md, MEMORY.md, facts)")),
        el("label", { for: "profTest", class: "small" }, test, " ", tx("פרופיל בדיקה (לא מקוון)", "test profile (offline)")), create),
      el("div", { class: "muted small", style: "margin-top:6px" }, tx("אף פעם לא מועתק: ", "Never copied: "), d.never_copied.join(", ")),
      el("div", { class: "muted small" }, tx("מפתחות API מועתקים רק בטרמינל, כשמבקשים במפורש: ", "API keys are copied only in a terminal, when explicitly asked: "),
        el("code", {}, "jarvis profile create <name> --from <name> --copy-secrets")), msg);
    const cli = el("div", { class: "card small" }, el("h2", {}, tx("בטרמינל", "In a terminal")),
      el("ul", {}, Object.values(d.commands).map((c) => el("li", {}, el("code", {}, c)))),
      el("div", { class: "muted" }, tx("כל פרופיל רץ בנפרד, עם לוח בקרה, פורט וכניסה משלו. זו הפרדה של התוכנה: להפרדה מלאה בין אנשים צריך משתמש Windows נפרד.",
        "Each profile runs on its own, with its own dashboard, port and login. This is separation by the app: full separation between people needs separate Windows accounts.")));
    render(header(tx("פרופילים", "Profiles"), tx(`הפרופיל כאן: ${d.current}`, `This dashboard's profile: ${d.current}`)),
      el("div", { class: "card" }, table), form, cli);
  }

  const nav = document.getElementById("nav");
  if (nav && !nav.querySelector('[data-page="profiles"]')) {
    const grp = nav.querySelector('[data-group="system"]') || nav;
    grp.append(el("a", { href: "#/profiles", "data-page": "profiles" }, el("span", { "data-he": "פרופילים", "data-en": "Profiles" }, tx("פרופילים", "Profiles"))));
  }
  PAGES.profiles = pageProfiles;
  JL.onChange(() => { banner(last); chip(last); });
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
})();
