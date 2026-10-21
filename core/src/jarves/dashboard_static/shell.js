// JARVES dashboard shell (SW-V3 P0.2): grouped navigation (drawer on phones), global search, the language picker
// and the status chips shown on every page (Core, this device, Raspberry Pi readiness, microphone / camera / audio
// to the cloud). Loaded last; uses the app.js helpers (el, api, route) and i18n.js (JL, tx). Text via textContent.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const side = $("side"), scrim = $("scrim"), menuBtn = $("menuBtn"), input = $("gsearch"), box = $("gresults");

  // ---------------------------------------------------------------- language
  function labels() {
    side.setAttribute("aria-label", tx("ניווט", "Navigation"));
    menuBtn.setAttribute("aria-label", tx("תפריט", "Menu"));
    $("sideClose").setAttribute("aria-label", tx("סגירת התפריט", "Close menu"));
    input.placeholder = tx("חיפוש עמודים, יכולות, מדריכים…", "Search pages, features, guides…");
    input.setAttribute("aria-label", tx("חיפוש בלוח הבקרה", "Search the dashboard"));
    document.title = tx("JARVES · לוח בקרה", "JARVES · Dashboard");
  }
  $("langSlot").replaceChildren(JL.picker("lang-pick"));         // the language picker (every locale in i18n.js)
  JL.onChange(() => {
    labels();
    if (typeof applyTheme === "function") applyTheme(theme);      // app.js
    extra = null;                                                  // search titles follow the language
    if (typeof renderTopbar === "function") renderTopbar();         // home.js privacy switch
    refreshOverview();
    chips();
    route();
  });
  labels();

  // ---------------------------------------------------------------- navigation drawer (phones / narrow windows)
  function drawer(open) {
    document.body.classList.toggle("nav-open", open);
    scrim.hidden = !open;
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { const a = side.querySelector("nav a.active") || side.querySelector("nav a"); if (a) a.focus(); }
  }
  menuBtn.addEventListener("click", () => drawer(!document.body.classList.contains("nav-open")));
  $("sideClose").addEventListener("click", () => { drawer(false); menuBtn.focus(); });
  scrim.addEventListener("click", () => drawer(false));
  $("nav").addEventListener("click", (ev) => { if (ev.target.closest("a")) drawer(false); });
  window.addEventListener("hashchange", () => {
    drawer(false);
    if (typeof show === "function") show(false);
    const a = side.querySelector("nav a.active");
    if (a && a.scrollIntoView) a.scrollIntoView({ block: "nearest" });
  });

  // ---------------------------------------------------------------- global search
  const KEYWORDS = {        // extra words people search for (both languages)
    home: "status overview dashboard מצב סקירה ראשי", runtime: "mic mute window show hide autostart quit stop wake tray מיקרופון השתקה חלון הפעלה עצירה מגש",
    connections: "google gmail calendar sign in oauth גוגל ג׳ימייל התחברות", devices: "phone lan pair wifi audit טלפון הצמדה רשת ביקורת",
    conversations: "transcript chat history talk תמליל היסטוריה צ׳אט", recall: "recall search what did i say remember find חיפוש מה אמרתי מה דיברנו שיחות קודמות", agents: "coding agents claude code codex sessions worktree approvals סוכני קוד קלוד קודקס סשנים פרויקט", issues: "problems errors diagnosis תקלות שגיאות אבחון",
    selfcheck: "self check health suggestions improvements בדיקה עצמית הצעות שיפור",
    updates: "update upgrade rollback version previous installer עדכון שדרוג חזרה לגרסה קודמת מתקין",
    speed: "health speed self test speed test slow broken benchmark performance תקינות ומהירות בדיקה עצמית בדיקת מהירות איטי שבור ביצועים",
    health: "health diagnostics anomalies canary preflight ai review repair ticket תקינות אבחון חריגות בדיקת לילה בודק תיקון",
    tasks: "research background מחקר רקע", calls: "call phone dial dialer phone link contact התקשר חיוג טלפון שיחה איש קשר", logs: "log errors events debug יומן שגיאות אירועים",
    calendar: "events meeting אירועים פגישה", jobs: "schedule reminder cron מתוזמן תזכורת", reports: "briefing morning תדריך בוקר",
    timers: "timer alarm countdown wake clock טיימר שעון מעורר התראה זמן", lists: "shopping todo groceries tasks קניות משימות רשימה סופר",
    alerts: "notifications התראות", notes: "markdown memo רשימות", files: "folders documents תיקיות מסמכים",
    identity: "name wake word voice שם מילת הפעלה קול", appearance: "face avatar theme פנים אווטאר",
    memory: "facts remember forget עובדות לזכור לשכוח", workspace: "persona soul user identity אישיות",
    setup: "install add-ons wizard התקנה תוספים אשף", features: "switches enable disable pi windows מתגים הפעלה כיבוי",
    skills: "tools health כלים תקינות", newskills: "generated skills proposals כלים חדשים הצעות", autonomy: "permissions approve הרשאות אישור",
    usage: "quota limits chatgpt openai api budget מכסה מגבלות תקציב", costs: "price spend money dollars מחיר הוצאה כסף",
    browser: "web site chrome playwright allow-list אתר אינטרנט דפדפן רשימת היתר", whatsapp: "whatsapp messages inbox link qr kill send וואטסאפ הודעות קישור שליחה מתג חירום", guides: "help how to עזרה איך", settings: "config toml configuration תצורה", backup: "restore export שחזור ייצוא",
    profiles: "profile users user test switch פרופיל משתמשים משתמש בדיקה החלפה",
  };
  const norm = (s) => String(s || "").toLowerCase().replace(/[֑-ׇ]/g, "").replace(/[׳']/g, "'");
  let extra = null, hits = [], sel = -1;
  function pages() {
    return Array.from(document.querySelectorAll("#nav a[data-page]")).map((a) => {
      const span = a.querySelector("[data-he]"), group = a.closest(".nav-group");
      const gh = group && group.querySelector(".nav-h");
      return { title: span ? span.textContent : a.textContent, where: gh ? gh.textContent : "", href: a.getAttribute("href"),
        hay: norm([span && span.dataset.he, span && span.dataset.en, KEYWORDS[a.dataset.page], a.dataset.page].join(" ")) };
    });
  }
  async function loadExtra() {
    if (extra) return extra;
    extra = [];
    const [f, g] = await Promise.all([api("/api/features").catch(() => null), api("/api/guides").catch(() => null)]);
    for (const x of (f && f.features) || []) extra.push({ title: txo(x, "title"), where: tx("יכולת", "Feature"),
      href: "#/guide-for/" + encodeURIComponent(x.id), hay: norm([x.title_he, x.title_en, x.id, x.about, x.about_he].join(" ")) });
    for (const x of (g && g.guides) || []) extra.push({ title: txo(x, "title"), where: tx("מדריך", "Guide"),
      href: "#/guide/" + encodeURIComponent(x.id), hay: norm([x.title_he, x.title_en, x.id].join(" ")) });
    return extra;
  }
  function show(open) { box.hidden = !open; input.setAttribute("aria-expanded", open ? "true" : "false"); }
  function go(h) {
    show(false); input.value = ""; input.blur();
    if (h.action) h.action(); else location.hash = h.href;
  }
  async function search() {
    const q = norm(input.value.trim());
    if (!q) { show(false); return; }
    const words = q.split(/\s+/);
    const all = pages().concat(await loadExtra());
    const seen = new Set();
    hits = all.filter((x) => words.every((w) => x.hay.includes(w) || norm(x.title).includes(w)))
      .filter((x) => !seen.has(x.href) && seen.add(x.href)).slice(0, 8);
    const raw = input.value.trim();
    hits.push({ title: tx(`חיפוש בשיחות: “${raw}”`, `Search conversations for “${raw}”`), where: tx("שיחות", "Conversations"),
      action: () => { if (typeof convState !== "undefined") { convState.q = raw; convState.page = 1; } location.hash = "#/conversations"; route(); } });
    hits.push({ title: tx(`חיפוש ביומן המערכת: “${raw}”`, `Search the system log for “${raw}”`), where: tx("יומן מערכת", "System log"),
      action: () => { if (typeof logState !== "undefined") logState.q = raw; location.hash = "#/logs"; route(); } });
    sel = hits.length > 2 ? 0 : -1;
    draw();
  }
  function draw() {
    box.replaceChildren(...hits.map((h, i) => el("a", { href: h.href || "#", role: "option", id: "gr" + i, class: "gr" + (i === sel ? " sel" : ""),
      "aria-selected": i === sel ? "true" : "false", "data-noi18n": "",
      onclick: (ev) => { ev.preventDefault(); go(h); } }, el("span", { class: "gr-t", dir: JL.textDir(h.title) }, h.title), el("span", { class: "gr-w muted small" }, h.where))));
    if (sel >= 0) input.setAttribute("aria-activedescendant", "gr" + sel); else input.removeAttribute("aria-activedescendant");
    show(hits.length > 0);
  }
  input.addEventListener("input", search);
  input.addEventListener("focus", () => { loadExtra(); if (input.value.trim()) search(); });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      if (!hits.length) return;
      ev.preventDefault();
      sel = (sel + (ev.key === "ArrowDown" ? 1 : -1) + hits.length) % hits.length; draw();
    } else if (ev.key === "Enter" && hits.length) { ev.preventDefault(); go(hits[Math.max(0, sel)]); }
    else if (ev.key === "Escape") { show(false); input.blur(); }
  });
  document.addEventListener("click", (ev) => { if (!ev.target.closest(".search")) show(false); });
  document.addEventListener("keydown", (ev) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if ((ev.key === "/" && !typing) || (ev.key.toLowerCase() === "k" && (ev.ctrlKey || ev.metaKey))) { ev.preventDefault(); input.focus(); input.select(); }
    else if (ev.key === "Escape" && document.body.classList.contains("nav-open")) { drawer(false); menuBtn.focus(); }
  });

  // ---------------------------------------------------------------- status chips (every page)
  let rt = null, piNotReady = null, piTotal = null, lastPi = 0;
  function deviceText(h) {
    if (!h || !h.os) return tx("מכשיר לא ידוע", "Unknown device");
    const arm = /^(aarch64|arm64|armv\d)/i.test(h.machine || "");
    if (h.os === "Linux" && arm) return "Linux ARM64 (Pi)";
    return h.os;
  }
  function chips() {
    const bar = $("statusChips");
    const r = rt && rt.runtime, live = !!(rt && rt.live), state = r && r.core && r.core.state;
    const running = live && state === "running";
    const core = el("a", { href: "#/runtime", class: "schip " + (running ? "ok" : rt ? "bad" : "unk"),
      title: tx("הליבה של JARVES (התהליך שרץ ברקע)", "The JARVES Core (the background process)") },
    el("span", { class: "dot " + (running ? "ok" : rt ? "bad" : "") }),
    el("span", { class: "schip-t" }, running ? tx("פועל", "Running") : rt ? tx("עצור", "Stopped") : tx("לא ידוע", "Unknown")));
    const host = rt && rt.host;
    const dev = el("a", { href: "#/devices", class: "schip plain", title: tx("המחשב שעליו JARVES רץ", "The computer JARVES runs on")
        + (host && host.os ? `: ${host.os} ${host.machine || ""}` : "") },
      el("span", { class: "schip-k" }, tx("מכשיר", "Device")), el("span", { class: "schip-t", dir: "ltr" }, deviceText(rt && rt.host)));
    const pi = el("a", { href: "#/features/not_ready_on_pi", class: "schip " + (piNotReady === null ? "unk" : piNotReady ? "warn" : "ok"),
      title: tx("כמה יכולות עוד לא מוכנות ל-Raspberry Pi (מאומת / מתוכנן / חסום לכל יכולת)",
        "How many features are not ready on the Raspberry Pi yet (verified / planned / blocked per feature)") },
    el("span", { class: "schip-t" }, "Pi: " + (piNotReady === null ? tx("לא ידוע", "Unknown")
      : piNotReady ? tx(`${piTotal - piNotReady}/${piTotal} מוכנות`, `${piTotal - piNotReady}/${piTotal} ready`) : tx("הכל מוכן", "all ready"))));
    const priv = typeof window.privacyChips === "function" ? window.privacyChips(rt && rt.privacy, live) : [];
    bar.replaceChildren(core, dev, pi, ...priv);
    fadeCue(bar);
  }
  // a sideways scroller shows a fade at the edge where more chips are hidden (D-17); RTL scrollLeft is negative
  function fadeCue(s) {
    if (!s) return;
    s.dataset.fade = "1";
    const upd = () => {
      const max = s.scrollWidth - s.clientWidth, pos = Math.abs(s.scrollLeft);
      s.classList.toggle("fade-end", max > 2 && pos < max - 2);
      s.classList.toggle("fade-start", max > 2 && pos > 2);
    };
    if (!s._fade) { s._fade = true; s.addEventListener("scroll", upd, { passive: true }); window.addEventListener("resize", upd); }
    upd();
  }
  window.fadeCue = fadeCue;
  async function poll() {
    if (document.hidden) return;
    try { rt = await api("/api/runtime"); } catch (e) { rt = null; }
    if (Date.now() - lastPi > 60000) {
      lastPi = Date.now();
      try {
        const f = await api("/api/features");
        piTotal = f.features.length; piNotReady = f.features.filter((x) => !x.pi_ready).length;
      } catch (e) { piNotReady = null; }
    }
    chips();
  }
  // ---------------------------------------------------------------- clickable table rows: keyboard (Campaign D)
  // pages build `tr.click` rows with an onclick only (mouse); make them reachable with Tab and activate with Enter / Space
  const main = $("main");
  function rowsKeyboard() {
    for (const r of main.querySelectorAll("tr.click:not([tabindex])")) { r.tabIndex = 0; r.setAttribute("role", "link"); }
  }
  new MutationObserver(rowsKeyboard).observe(main, { childList: true, subtree: true });
  rowsKeyboard();
  main.addEventListener("keydown", (ev) => {
    const r = ev.target.closest && ev.target.closest("tr.click");
    if (r && r === ev.target && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); r.click(); }
  });
  window.shellOverview = () => {};          // hook for app.js refreshOverview (kept for later use)
  chips();
  poll();
  setInterval(poll, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
})();
