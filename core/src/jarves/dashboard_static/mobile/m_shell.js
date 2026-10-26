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
  };
  function icon(name) {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("width", "22"); s.setAttribute("height", "22");
    s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false"); s.setAttribute("class", "ic ic-" + name);
    (PATHS[name] || []).forEach((d) => { const p = document.createElementNS(NS, "path"); p.setAttribute("d", d); s.appendChild(p); });
    return s;
  }
  const $ = (id) => document.getElementById(id);
  let cfg = null, lastFocus = null, toastTimer = 0, waitingList = false;

  function setIcon(id, name) { const b = $(id); if (b && !b.querySelector("svg")) b.prepend(icon(name)); }
  function label(id, he, en) { const b = $(id); if (b) { const t = cfg.tx(he, en); b.setAttribute("aria-label", t); b.title = t; } }
  function paintLabels() {
    label("menuBtn", "תפריט", "Menu"); label("newChat", "שיחה חדשה", "New chat"); label("drawerClose", "סגירת התפריט", "Close the menu");
    const h = $("histHead"); if (h) h.textContent = cfg.tx("שיחות אחרונות", "Recent conversations");
    const l = $("histLoad"); if (l) l.lastChild.textContent = cfg.tx("הצגת שיחות אחרונות", "Show recent conversations");
    const n = $("drawerNew"); if (n) n.lastChild.textContent = cfg.tx("שיחה חדשה", "New chat");
  }
  function open() {
    const d = $("drawer"); if (!d || !d.hidden) return;
    lastFocus = document.activeElement; d.hidden = false; $("menuBtn").setAttribute("aria-expanded", "true");
    document.body.classList.add("drawer-open");
    const sel = d.querySelector("[aria-selected=true]:not([hidden])") || $("drawerClose"); sel.focus();
  }
  function close() {
    const d = $("drawer"); if (!d || d.hidden) return;
    d.hidden = true; $("menuBtn").setAttribute("aria-expanded", "false"); document.body.classList.remove("drawer-open");
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
    list.forEach((s) => {
      const li = document.createElement("li"), b = document.createElement("button");
      b.type = "button"; b.className = "hist-item"; b.dir = "auto";
      const t = document.createElement("span"); t.className = "hist-t"; t.dir = "auto"; t.textContent = s.first || cfg.tx("(פרטי)", "(private)");
      const m = document.createElement("span"); m.className = "hist-m";
      let when = "";
      try { when = new Date(s.started * 1000).toLocaleDateString(document.documentElement.lang || undefined, { day: "numeric", month: "short" }); } catch (e) { /* no date */ }
      m.textContent = [when, cfg.tx(`${s.turns} הודעות`, `${s.turns} messages`)].filter(Boolean).join(" · ");
      b.append(icon("chat"), t, m);
      b.addEventListener("click", () => { close(); chatCmd("/resume " + s.n); });
      li.appendChild(b); ul.appendChild(li);
    });
  }
  function onChatEvent(e) {
    if (e && e.type === "notice" && e.command === "resume" && Array.isArray(e.sessions)) { waitingList = false; paintHistory(e.sessions); }
  }
  function init(c) {
    cfg = c;
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
  }
  window.JarvesShell = { icon, init, open, close, toast, onChatEvent, repaint: () => { if (cfg) paintLabels(); }, get waiting() { return waitingList; } };
})();
