// JARVES phone app shell (redesign #511): icons, the side drawer (pages + recent conversations), toasts.
// Plain DOM, no framework, no external request; text via textContent only; SVG icons built with createElementNS.
// Used by app.js: JarvesShell.init({ tx, go, chat }) once, JarvesShell.onChatEvent(e) for every chat event.
"use strict";
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const PATHS = {
    menu: ["M4 7h16", "M4 12h16", "M4 17h10"],
    close: ["M6 6l12 12", "M18 6L6 18"],
    edit: ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
    send: ["M12 19V5", "M6 11l6-6 6 6"],
    mic: ["M12 4a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V7a3 3 0 0 0-3-3z", "M6 11a6 6 0 0 0 12 0", "M12 17v3"],
    plus: ["M12 5v14", "M5 12h14"],
    copy: ["M9 9h10v10H9z", "M5 15V5h10"],
    chat: ["M5 5h14v10H9l-4 4z"],
    check: ["M5 12l5 5L19 7"],
    history: ["M4 12a8 8 0 1 0 2.3-5.6", "M4 4v4h4", "M12 8v4l3 2"],
    shield: ["M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z", "M9 12l2 2 4-4"],
    list: ["M9 6h11", "M9 12h11", "M9 18h11", "M4 6h.01", "M4 12h.01", "M4 18h.01"],
    bulb: ["M9 18h6", "M10 21h4", "M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"],
    car: ["M5 16V12l2-5h10l2 5v4", "M3 16h18v2H3z", "M7 19v1", "M17 19v1", "M7.5 13h.01", "M16.5 13h.01"],
    wave: ["M4 10v4", "M8 7v10", "M12 4v16", "M16 7v10", "M20 10v4"],
    pulse: ["M3 12h4l2-6 4 12 2-6h6"],
    phone: ["M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z", "M11 18h2"],
    info: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M12 11v5", "M12 8h.01"],
    lock: ["M6 11h12v9H6z", "M8 11V8a4 4 0 0 1 8 0v3"],
    globe: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M3 12h18", "M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9", "M12 3c-2.5 2.6-3.8 5.6-3.8 9s1.3 6.4 3.8 9"],
  };
  const TAB_ICONS = { chat: "chat", approvals: "shield", plans: "list", prefs: "bulb", drive: "car", browser: "globe", voice: "wave", health: "pulse", home: "phone" };
  function icon(name) {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("width", "22"); s.setAttribute("height", "22");
    s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false"); s.setAttribute("class", "ic ic-" + name);
    (PATHS[name] || []).forEach((d) => { const p = document.createElementNS(NS, "path"); p.setAttribute("d", d); s.appendChild(p); });
    return s;
  }
  const $ = (id) => document.getElementById(id);
  let cfg = null, lastFocus = null, toastTimer = 0, waitingList = false, closing = 0;
  const calm = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function setIcon(id, name) { const b = $(id); if (b && !b.querySelector("svg")) b.prepend(icon(name)); }
  function label(id, he, en) { const b = $(id); if (b) { const t = cfg.tx(he, en); b.setAttribute("aria-label", t); b.title = t; } }
  function paintLabels() {
    label("menuBtn", "תפריט", "Menu"); label("newChat", "שיחה חדשה", "New chat"); label("drawerClose", "סגירת התפריט", "Close the menu");
    const h = $("histHead"); if (h) h.textContent = cfg.tx("שיחות אחרונות", "Recent conversations");
    const l = $("histLoad"); if (l) l.lastChild.textContent = cfg.tx("הצגת שיחות אחרונות", "Show recent conversations");
    const n = $("drawerNew"); if (n) n.lastChild.textContent = cfg.tx("שיחה חדשה", "New chat");
  }
  function open() {
    const d = $("drawer"); if (!d) return;
    if (closing) { clearTimeout(closing); closing = 0; d.classList.remove("closing"); }
    if (!d.hidden) return;
    lastFocus = document.activeElement; d.hidden = false; $("menuBtn").setAttribute("aria-expanded", "true");
    document.body.classList.add("drawer-open");
    const sel = d.querySelector("[aria-selected=true]:not([hidden])") || $("drawerClose"); sel.focus();
  }
  function close() {
    const d = $("drawer"); if (!d || d.hidden || closing) return;
    $("menuBtn").setAttribute("aria-expanded", "false"); document.body.classList.remove("drawer-open");
    const done = () => { closing = 0; d.classList.remove("closing"); d.hidden = true; };
    if (calm()) done(); else { d.classList.add("closing"); closing = setTimeout(done, 160); }     // slide out, then hide
    const f = lastFocus && document.contains(lastFocus) && !lastFocus.closest("#drawer") ? lastFocus : $("menuBtn");
    if (f && f.offsetParent !== null) f.focus();
  }
  function toast(text) {
    const t = $("toast"); if (!t) return;
    t.textContent = text; t.hidden = false; clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; t.textContent = ""; }, 4000);
  }
  function chatCmd(text) {
    const c = cfg.chat(); cfg.go("chat");
    const run = () => { const v = cfg.chat(); if (v) v.submit(text); };
    if (c) run(); else setTimeout(run, 300);              // the chat view mounts on the first visit
  }
  // Recent conversations: the answer to /resume carries the list (chat_engine._resume -> notice.sessions). No new API.
  function paintHistory(list) {
    const ul = $("histList"); if (!ul) return;
    ul.replaceChildren();
    if (!list.length) { const li = document.createElement("li"); li.className = "muted"; li.textContent = cfg.tx("אין שיחות קודמות.", "No earlier conversations."); ul.appendChild(li); return; }
    // grouped by day like ChatGPT: Today / Yesterday / Previous 7 days / Earlier (the phone's own clock and language)
    const day = (ts) => { const d = new Date(ts * 1000); d.setHours(0, 0, 0, 0); return d.getTime(); };
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const group = (s) => { if (!s.started) return 3; const n = Math.round((today.getTime() - day(s.started)) / 864e5); return n <= 0 ? 0 : n === 1 ? 1 : n < 8 ? 2 : 3; };
    const names = [cfg.tx("היום", "Today"), cfg.tx("אתמול", "Yesterday"), cfg.tx("7 הימים האחרונים", "Previous 7 days"), cfg.tx("קודם", "Earlier")];
    let last = -1;
    list.forEach((s) => {
      const g = group(s);
      if (g !== last) { last = g; const hl = document.createElement("li"); hl.className = "hist-day"; hl.textContent = names[g]; ul.appendChild(hl); }
      const li = document.createElement("li"), b = document.createElement("button");
      b.type = "button"; b.className = "hist-item"; b.dir = "auto";
      const t = document.createElement("span"); t.className = "hist-t"; t.dir = "auto"; t.textContent = s.first || cfg.tx("(פרטי)", "(private)");
      const m = document.createElement("span"); m.className = "hist-m";
      let when = "";
      try { when = g < 2 ? new Date(s.started * 1000).toLocaleTimeString(document.documentElement.lang || undefined, { hour: "2-digit", minute: "2-digit" })
        : new Date(s.started * 1000).toLocaleDateString(document.documentElement.lang || undefined, { day: "numeric", month: "short" }); } catch (e) { /* no date */ }
      m.textContent = [when, cfg.tx(`${s.turns} הודעות`, `${s.turns} messages`)].filter(Boolean).join(" · ");
      b.append(icon("chat"), t, m);
      b.addEventListener("click", () => { close(); chatCmd("/resume " + s.n); });
      li.appendChild(b); ul.appendChild(li);
    });
  }
  function onChatEvent(e) {
    if (e && e.type === "notice" && e.command === "resume" && Array.isArray(e.sessions)) { waitingList = false; paintHistory(e.sessions); }
  }
  // First-run hint in the empty chat (client only, dismissible, remembered on this phone): a trusted connection first (voice
  // and the full app need it), else adding JARVES to the home screen. "How" opens This phone, where the install help lives.
  const HINT = "jarves_m_hint_off";
  function hint() {
    let off = false; try { off = localStorage.getItem(HINT) === "1"; } catch (e) { /* storage blocked: show it */ }
    const standalone = window.matchMedia && window.matchMedia("(display-mode: standalone)").matches;
    if (off || (standalone && window.isSecureContext)) return null;
    const secure = window.isSecureContext;
    const box = document.createElement("div"); box.className = "m-hint"; box.setAttribute("role", "note");
    const tx = document.createElement("div"); tx.className = "m-hint-t";
    const h = document.createElement("strong"); h.textContent = secure ? cfg.tx("JARVES כמו אפליקציה", "JARVES as an app") : cfg.tx("חיבור לא מאובטח", "Connection not secure");
    const p = document.createElement("span"); p.dir = "auto";
    p.textContent = secure ? cfg.tx("הוסיפו את JARVES למסך הבית: נפתח במסך מלא, בלי שורת כתובת.", "Add JARVES to your home screen: it opens full screen, without the address bar.")
      : cfg.tx("שיחה בכתב עובדת. קול והתקנה צריכים חיבור מאובטח (https).", "Text chat works. Voice and installing need a secure (https) connection.");
    tx.append(h, p);
    const how = document.createElement("button"); how.type = "button"; how.className = "m-hint-go"; how.textContent = cfg.tx("איך?", "How?");
    how.addEventListener("click", () => cfg.go("home"));
    const x = document.createElement("button"); x.type = "button"; x.className = "icon-btn m-hint-x"; x.appendChild(icon("close"));
    x.setAttribute("aria-label", cfg.tx("סגירת הטיפ", "Close the tip"));
    x.addEventListener("click", () => { try { localStorage.setItem(HINT, "1"); } catch (e) { /* this time only */ } box.remove(); });
    box.append(icon(secure ? "phone" : "lock"), tx, how, x);
    return box;
  }
  function init(c) {
    cfg = c;
    document.querySelectorAll("#drawer [data-tab]").forEach((b) => { if (!b.querySelector("svg") && TAB_ICONS[b.dataset.tab]) b.prepend(icon(TAB_ICONS[b.dataset.tab])); });
    setIcon("menuBtn", "menu"); setIcon("newChat", "edit"); setIcon("drawerClose", "close");
    const hl = $("histLoad"); if (hl && !hl.querySelector("svg")) hl.append(icon("history"), document.createTextNode(""));
    const dn = $("drawerNew"); if (dn && !dn.querySelector("svg")) dn.append(icon("edit"), document.createTextNode(""));
    paintLabels();
    $("menuBtn").addEventListener("click", open);
    $("drawerClose").addEventListener("click", close);
    $("drawerScrim").addEventListener("click", close);
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") close(); });
    document.querySelectorAll("#drawer [data-tab]").forEach((b) => b.addEventListener("click", close));
    const fresh = () => { close(); chatCmd("/new"); };
    $("newChat").addEventListener("click", fresh); $("drawerNew").addEventListener("click", fresh);
    hl.addEventListener("click", () => { waitingList = true; close(); chatCmd("/resume"); });
    watchKeyboard();
  }
  // The on-screen keyboard: while it is open (a text field has focus and the visible height shrank by a quarter) the root gets
  // the class "kb": the quiet idle strip and the empty-state decoration step aside so the conversation keeps its room, and the
  // newest message stays in view above the composer. The strip still shows whenever something listens (.hot).
  function watchKeyboard() {
    const vv = window.visualViewport; if (!vv) return;
    let tall = vv.height;
    const typing = () => { const a = document.activeElement; return !!a && (a.tagName === "TEXTAREA" || (a.tagName === "INPUT" && !/^(button|checkbox|radio|submit)$/.test(a.type))); };
    const check = () => {
      if (!typing()) tall = Math.max(tall, vv.height);
      const kb = typing() && vv.height < tall * 0.75, was = document.documentElement.classList.contains("kb");
      document.documentElement.classList.toggle("kb", kb);
      if (kb && !was) { const log = document.querySelector("#tab-chat .jc-log"); if (log) requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; }); }
    };
    vv.addEventListener("resize", check);
    window.addEventListener("orientationchange", () => { tall = 0; setTimeout(() => { tall = vv.height; check(); }, 400); });
    document.addEventListener("focusin", () => setTimeout(check, 250)); document.addEventListener("focusout", () => setTimeout(check, 250));
  }
  window.JarvesShell = { icon, init, open, close, toast, onChatEvent, hint, repaint: () => { if (cfg) paintLabels(); }, get waiting() { return waitingList; } };
})();
