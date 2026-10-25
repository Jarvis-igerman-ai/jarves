// JARVES phone app (V5): a PWA served by the Core. English + Hebrew. No framework; text via textContent only.
// The bearer token lives in localStorage on this phone; it is never put in a URL.
"use strict";
(function () {
  const KEY = "jarves_m_token", LANG = "jarves_m_lang";
  const T = {
    en: { pair_title: "Pair this phone", pair_help: "On the computer open the JARVES dashboard, go to Phone and press Pair a phone. Scan the QR code, or type the 8-digit code here.",
      code: "Pairing code", name: "Name for this phone", pair: "Pair", pair_net: "Works on your home network only. Nothing here is reachable from the internet.",
      tab_home: "Home", tab_chat: "Chat", tab_voice: "Voice", tab_approvals: "Approvals", tab_plans: "Plans", tab_prefs: "Learned", tab_drive: "Drive", tab_health: "Health & speed", send: "Send", type_here: "Type a message",
      waiting: "Waiting for approval", pages_you_have: "Pages on this device", no_tests: "No self-test or speed test has run yet.", chat_off: "Chat is not running on the computer.",
      hold: "Hold to talk", release: "Release to send", unpair: "Un-pair this phone", online: "connected", offline: "computer unreachable",
      pairing: "Pairing…", bad_code: "Wrong or expired code. Make a new one on the computer.", unpaired: "This phone is no longer paired. Pair again.",
      thinking: "…", err_chat: "JARVES could not answer right now.", voice_tls: "Voice needs a secure (https) connection. On the computer run: jarvis dashboard --mobile --tls-selfsigned. Text chat works without it.",
      voice_ready: "Press and hold the button, speak, then release.", voice_nomic: "Microphone permission was denied or is unavailable.",
      heard: "You said", nothing: "Nothing is waiting for approval.", pc_only: "Answer on the computer.", approve: "Approve", deny: "Deny",
      typed_pc: "Needs the code shown on the computer.", sec: "s", core: "Core", tls: "Secure connection", yes: "yes", no: "no (text only)",
      busy: "The previous message is still being answered. Wait a moment.", phone_ap_on: "Phone approvals are on.", phone_ap_off: "Phone approvals are off: approvals are answered on the computer.", sending: "Sending…",
      banner: "The computer is unreachable. Nothing is sent until it is back; type the message again then.",
      not_sent: "Not sent", waiting: "Waiting for the connection…", send_again: "Send again", stale: "Waited too long while offline. Send it again if you still want it.",
      note: "Note", goals: "Goals", pause: "Pause", resume: "Resume", drop: "Drop", snooze: "Snooze notes 1 h", stop_notes: "Stop notes",
      sig_snoozed: "Notes snoozed for one hour.", sig_stopped: "Notes stopped. Turn them on again on the computer.", from_pc: "computer", no_goals: "No active goals." },
    he: { pair_title: "צימוד הטלפון", pair_help: "במחשב פתחו את לוח הבקרה של JARVES, עברו אל טלפון ולחצו על צימוד טלפון. סרקו את קוד ה-QR או הקלידו כאן את הקוד בן 8 הספרות.",
      code: "קוד צימוד", name: "שם לטלפון", pair: "לצמד", pair_net: "עובד ברשת הביתית בלבד. שום דבר כאן לא נגיש מהאינטרנט.",
      tab_home: "בית", tab_chat: "שיחה", tab_voice: "קול", tab_approvals: "אישורים", tab_plans: "תוכניות", tab_prefs: "למדתי", tab_drive: "נהיגה", tab_health: "תקינות ומהירות", send: "שליחה", type_here: "כתבו הודעה",
      waiting: "ממתינים לאישור", pages_you_have: "דפים במכשיר הזה", no_tests: "עוד לא רצה בדיקה עצמית או בדיקת מהירות.", chat_off: "השיחה לא פועלת במחשב.",
      hold: "לחצו והחזיקו לדיבור", release: "שחררו לשליחה", unpair: "ניתוק הטלפון", online: "מחובר", offline: "המחשב לא זמין",
      pairing: "מצמיד…", bad_code: "קוד שגוי או שפג תוקפו. הפיקו קוד חדש במחשב.", unpaired: "הטלפון כבר לא מצומד. צמדו מחדש.",
      thinking: "…", err_chat: "JARVES לא הצליח לענות כרגע.", voice_tls: "קול דורש חיבור מאובטח (https). במחשב הריצו: jarvis dashboard --mobile --tls-selfsigned. שיחה בכתב עובדת גם בלי זה.",
      voice_ready: "לחצו והחזיקו את הכפתור, דברו ושחררו.", voice_nomic: "הרשאת המיקרופון נדחתה או לא זמינה.",
      heard: "אמרתם", nothing: "שום דבר לא ממתין לאישור.", pc_only: "עונים במחשב.", approve: "אישור", deny: "דחייה",
      typed_pc: "דורש את הקוד שמוצג במחשב.", sec: "ש׳", core: "ליבה", tls: "חיבור מאובטח", yes: "כן", no: "לא (טקסט בלבד)",
      busy: "ההודעה הקודמת עדיין בטיפול. המתינו רגע.", phone_ap_on: "אישורים מהטלפון פעילים.", phone_ap_off: "אישורים מהטלפון כבויים: עונים על אישורים במחשב.", sending: "שולח…",
      banner: "המחשב לא זמין. שום דבר לא נשלח עד שהוא חוזר; כתבו את ההודעה שוב אז.",
      not_sent: "לא נשלחה", waiting: "ממתין לחיבור…", send_again: "לשלוח שוב", stale: "ההודעה חיכתה יותר מדי זמן בלי חיבור. שלחו שוב אם עדיין רוצים.",
      note: "הודעה", goals: "יעדים", pause: "השהיה", resume: "המשך", drop: "ויתור", snooze: "השתקת הודעות לשעה", stop_notes: "עצירת הודעות",
      sig_snoozed: "ההודעות הושתקו לשעה.", sig_stopped: "ההודעות נעצרו. מדליקים שוב במחשב.", from_pc: "מחשב", no_goals: "אין יעדים פעילים." },
  };
  const $ = (id) => document.getElementById(id);
  const newId = () => "c-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);      // 8-64 chars: the server's command id
  let lang = "en";
  try { lang = localStorage.getItem(LANG) || ((navigator.language || "en").startsWith("he") ? "he" : "en"); } catch (e) { /* storage blocked */ }
  const t = (k) => (T[lang] && T[lang][k]) || T.en[k] || k;
  function applyLang() {
    document.documentElement.lang = lang; document.documentElement.dir = lang === "he" ? "rtl" : "ltr";
    document.querySelectorAll("[data-t]").forEach((n) => { const c = n.querySelector(".badge"); if (c) { n.firstChild.textContent = t(n.dataset.t) + " "; } else n.textContent = t(n.dataset.t); });
    document.querySelectorAll("[data-ph]").forEach((n) => { n.placeholder = t(n.dataset.ph); });
    const lb = $("lang"); lb.textContent = lang === "he" ? "EN" : "עב"; lb.lang = lang === "he" ? "en" : "he"; lb.setAttribute("aria-label", lang === "he" ? "English" : "עברית");
    setConn(connected);
  }
  let token = ""; try { token = localStorage.getItem(KEY) || ""; } catch (e) { /* storage blocked */ }
  let me = null, connected = true, timer = 0;

  function setConn(ok) {
    const was = connected; connected = ok; const c = $("conn"); c.textContent = ok ? t("online") : t("offline"); c.className = "pill " + (ok ? "ok" : "bad");
    const b = $("banner"); b.hidden = ok || !token; b.textContent = ok ? "" : t("banner");
    if (ok && !was) syncNow();                      // back online: catch up with the one conversation on the PC
  }

  async function api(path, opts = {}) {
    const headers = { "X-Jarves": "1", ...(opts.headers || {}) };
    if (token) headers.Authorization = "Bearer " + token;
    let r;
    try { r = await fetch(path, { method: opts.method || "GET", headers, body: opts.body, cache: "no-store" }); }
    catch (e) { setConn(false); throw new Error(t("offline")); }
    setConn(true);
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { forget(); const u = new Error(t("unpaired")); u.status = 401; throw u; }
    if (!r.ok) { const e = new Error(d.error || ("HTTP " + r.status)); e.status = r.status; e.code = d.code; throw e; }
    return d;
  }
  const post = (p, obj) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj || {}) });

  function forget() { unmountChat(); if (window.JarvesAudio) window.JarvesAudio.unmount(); if (window.JarvesPlans) window.JarvesPlans.unmount(); if (window.JarvesPrefs) window.JarvesPrefs.unmount(); token = ""; me = null; try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ } show(); }

  function show() {
    $("pairView").hidden = !!token; $("appView").hidden = !token;
    clearInterval(timer);
    if (token) { load(); timer = setInterval(tick, 4000); }
    else { const m = /(?:^|[#&])c=(\d{6,9})/.exec(location.hash); if (m) { $("code").value = m[1]; history.replaceState(null, "", location.pathname); } }
  }
  async function load() { try { me = await api("/api/m/me"); } catch (e) { return; } info(); allowTabs(); mountIndicator(); tick(); voiceNote(); }
  function mountIndicator() { if (window.JarvesAudio && $("audioBar")) window.JarvesAudio.mount({ audio: $("audioBar"), notices: $("noticeBox") }, { api, post, lang: () => lang }); }
  function info() { if (me) $("meInfo").textContent = `${me.name} · ${me.phone_approvals ? t("phone_ap_on") : t("phone_ap_off")}`; }

  // ---- pairing
  $("pairForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("pairMsg"); msg.textContent = t("pairing");
    try {
      const d = await post("/api/m/pair", { code: $("code").value, name: $("name").value });
      token = d.token; try { localStorage.setItem(KEY, token); } catch (e) { /* storage blocked: stays for this tab only */ }
      msg.textContent = ""; show();
    } catch (e) { msg.textContent = e.status === 403 ? t("bad_code") : e.message; }
  });

  // ---- tabs: only the pages the owner gave this device (the server refuses the others anyway: PAGE_NOT_ALLOWED)
  let tab = "home", chatView = null;
  const TABS = { home: "home", chat: "chat", voice: "chat", approvals: "approvals", plans: "scope:tasks", prefs: "scope:prefs", drive: "scope:drive", health: "health" };
  const allowed = (n) => (TABS[n].startsWith("scope:") ? ((me && me.scopes) || []).includes(TABS[n].slice(6)) : ((me && me.pages) || []).includes(TABS[n]));
  function setTab(name) {
    tab = name;
    document.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.tab === tab)));
    Object.keys(TABS).forEach((n) => { $("tab-" + n).hidden = n !== tab; });
    if (tab === "chat") mountChat();
    if (tab === "plans") mountPlans();
    if (tab === "prefs") mountPrefs();
    if (tab === "drive") mountDrive();
    tick();
  }
  function allowTabs() {
    const pages = (me && me.pages) || [];
    Object.keys(TABS).forEach((n) => { $("tabbtn-" + n).hidden = !allowed(n); });
    if (!allowed(tab)) setTab(pages.includes("home") ? "home" : (pages[0] ? Object.keys(TABS).find((n) => TABS[n] === pages[0]) : "home"));
  }
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  // ---- chat: the shared chat view (chatview.js), streaming, slash commands, inline approvals
  const tx = (he, en) => (lang === "he" ? he : en);
  const chatApi = {
    state: (l) => api("/api/m/chat/state?lang=" + l),
    events: (after, sig, l) => api("/api/m/chat/events?after=" + after + "&sig=" + encodeURIComponent(sig) + "&lang=" + l + "&wait=20"),
    send: (body) => post("/api/m/chat/send", body),
    answer: (id, approved, typed) => post("/api/m/approvals/" + id, { approved }),
    silent: () => Promise.reject(new Error("computer only")),
  };
  function mountChat() {
    if (chatView || !window.JarvesChat) return;
    chatView = window.JarvesChat.mount($("chatRoot"), { api: chatApi, tx, device: true });
  }
  function mountPlans() { if (window.JarvesPlans) window.JarvesPlans.mount($("plansRoot"), { api, post, lang: () => lang }); }
  function mountPrefs() { if (window.JarvesPrefs) window.JarvesPrefs.mount($("prefsRoot"), { api, post, lang: () => lang }); }
  function mountDrive() { if (window.JarvesDrive) window.JarvesDrive.mount($("driveRoot"), { api, post, lang: () => lang }); }
  function unmountChat() { if (chatView) { chatView.destroy(); chatView = null; } }

  // ---- voice (push to talk): record, encode 16 kHz mono WAV, send
  function voiceNote() {
    const ok = window.isSecureContext && me && me.voice;
    $("voiceNote").textContent = ok ? t("voice_ready") : t("voice_tls"); $("ptt").disabled = !ok;
  }
  let rec = null;
  function wav(samples, rate) {
    const out = new DataView(new ArrayBuffer(44 + samples.length * 2)); const w = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); out.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVEfmt "); out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
    out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true); w(36, "data"); out.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 32767, true);
    return out.buffer;
  }
  function down(chunks, from, to) {
    let n = 0; chunks.forEach((c) => { n += c.length; }); const all = new Float32Array(n); let o = 0; chunks.forEach((c) => { all.set(c, o); o += c.length; });
    const ratio = from / to, len = Math.floor(all.length / ratio), res = new Float32Array(len);
    for (let i = 0; i < len; i++) res[i] = all[Math.floor(i * ratio)];
    return res;
  }
  async function startRec() {
    if (rec) return;
    let stream = null, ctx = null;
    const closeAll = () => { try { if (stream) stream.getTracks().forEach((x) => x.stop()); } catch (e) { /* ignore */ } try { if (ctx) ctx.close(); } catch (e) { /* ignore */ } };
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      // the strip follows the real track state from the moment the stream is open, even if the setup below fails
      const live = () => !!stream && stream.getTracks().some((x) => x.readyState === "live");
      if (window.JarvesAudio) window.JarvesAudio.phoneMic(true, { stop: stopRec, discard: discardRec, live });
      ctx = new (window.AudioContext || window.webkitAudioContext)(); const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1); const chunks = [];
      proc.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      src.connect(proc); proc.connect(ctx.destination); rec = { stream, ctx, proc, src, chunks };
      $("ptt").classList.add("rec"); $("ptt").textContent = t("release");
    } catch (e) {
      rec = null; closeAll(); $("ptt").classList.remove("rec"); $("ptt").textContent = t("hold");
      if (window.JarvesAudio) window.JarvesAudio.phoneMic(false);
      $("voiceOut").textContent = t("voice_nomic");
    }
  }
  function discardRec() {                                                    // close the microphone and send nothing
    if (!rec) return; const r = rec; rec = null;
    r.proc.disconnect(); r.src.disconnect(); r.stream.getTracks().forEach((x) => x.stop()); r.ctx.close();
    $("ptt").classList.remove("rec"); $("ptt").textContent = t("hold"); $("voiceOut").textContent = "";
    if (window.JarvesAudio) window.JarvesAudio.phoneMic(false);
  }
  async function stopRec() {
    if (!rec) return; const r = rec; rec = null;
    r.proc.disconnect(); r.src.disconnect(); r.stream.getTracks().forEach((x) => x.stop()); const rate = r.ctx.sampleRate; r.ctx.close();
    $("ptt").classList.remove("rec"); $("ptt").textContent = t("hold");
    if (window.JarvesAudio) window.JarvesAudio.phoneMic(false);
    if (!r.chunks.length) return;
    $("voiceOut").textContent = t("sending");
    try {
      const d = await api("/api/m/voice", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav(down(r.chunks, rate, 16000), 16000) });
      $("voiceOut").textContent = d.transcript ? `${t("heard")}: ${d.transcript}\n\n${d.reply}` : (d.reply || "");
    } catch (e) { $("voiceOut").textContent = e.message; }
  }
  const ptt = $("ptt");
  ptt.addEventListener("pointerdown", (e) => { e.preventDefault(); startRec(); });
  ["pointerup", "pointercancel", "pointerleave"].forEach((n) => ptt.addEventListener(n, () => stopRec()));

  // ---- approvals + status polling
  function bubble(text, cls) { const d = document.createElement("div"); d.className = "msg " + (cls || ""); d.textContent = text; $("noteLog").appendChild(d); return d; }
  // ---- sync: the PC holds the one conversation; this phone only reads it and acknowledges notes
  let seq = 0; const mine = new Set(), shown = new Set();
  async function syncNow() {
    if (!token || document.hidden) return;
    let s; try { s = await api("/api/m/sync?since=" + seq); } catch (e) { return; }
    if (s.head < seq) seq = 0;                                  // the PC was restarted with a fresh journal
    const first = seq === 0;
    (s.turns || []).forEach((x) => { seq = Math.max(seq, x.seq); });          // the chat itself is drawn by the shared chat view
    (s.notices || []).forEach(async (n) => {
      if (!shown.has(n.id)) { shown.add(n.id); if (window.JarvesAudio) window.JarvesAudio.notice(n); else { const b = bubble(t("note") + ": " + n.text, "note"); b.setAttribute("role", "status"); } }
      try { await post("/api/m/notices/" + n.id + "/ack"); } catch (e) { /* shown again next sync, never twice on screen */ }
    });
    if (window.JarvesAudio) window.JarvesAudio.signals(s.signals);
    renderGoals(s);
  }
  function renderGoals(s) {
    const on = !!(s.signals || (me && me.scopes && me.scopes.includes("tasks")));
    $("goalsHead").hidden = $("goalList").hidden = $("sigBox").hidden = !on; if (!on) return;
    const ul = $("goalList"); ul.replaceChildren();
    if (!(s.goals || []).length) li(ul, t("no_goals"));
    (s.goals || []).forEach((g) => {
      const row = li(ul, `${g.title} (${g.percent == null ? 0 : g.percent}%)`);
      const act = (label, status) => { const b = document.createElement("button"); b.type = "button"; b.textContent = label;
        b.addEventListener("click", async () => { b.disabled = true; try { await post("/api/m/goals/op", { op: "status", goal: g.id, status, id: newId() }); } catch (e) { row.firstChild.textContent = e.message; } syncNow(); }); row.appendChild(b); };
      if (g.status === "paused") act(t("resume"), "active"); else act(t("pause"), "paused");
      act(t("drop"), "dropped");
    });
  }
  const sig = (op, key) => async () => { try { await post("/api/m/signals", { op, minutes: 60, id: newId() }); $("sigNote").textContent = t(key); } catch (e) { $("sigNote").textContent = e.message; } };
  $("sigSnooze").addEventListener("click", sig("snooze", "sig_snoozed")); $("sigStop").addEventListener("click", sig("stop", "sig_stopped"));
  async function tick() {
    if (!token || document.hidden) return;
    if (!me) { load(); return; }                      // opened while the computer was unreachable: try again
    syncNow();
    if (window.JarvesAudio) window.JarvesAudio.refresh();
    try {
      if (me.pages.includes("approvals")) {
        const a = await api("/api/m/approvals"); const n = a.pending.length, badge = $("apprCount");
        badge.hidden = !n; badge.textContent = String(n);
        if (tab === "approvals") renderApprovals(a);
      }
      if (tab === "plans" && window.JarvesPlans) window.JarvesPlans.refresh();
      if (tab === "prefs" && window.JarvesPrefs) window.JarvesPrefs.refresh();
      if (tab === "drive" && window.JarvesDrive) window.JarvesDrive.refresh();
      if (tab === "home") renderHome(await api("/api/m/home"));
      if (tab === "health") renderHealth(await api("/api/m/health"));
    } catch (e) { /* the connection pill already says why */ }
  }
  function li(parent, text, cls) { const l = document.createElement("li"); const s = document.createElement("span"); s.className = "grow"; s.textContent = text; l.appendChild(s); parent.appendChild(l); return l; }
  function renderApprovals(a) {
    $("apprNote").textContent = a.phone_approvals ? t("phone_ap_on") : t("phone_ap_off");
    const ul = $("apprList"); ul.replaceChildren();
    if (!a.pending.length) { li(ul, t("nothing")); return; }
    a.pending.forEach((p) => {
      const row = li(ul, `${p.title} (${p.seconds_left}${t("sec")})`);
      if (!a.phone_approvals) return;
      const mk = (label, ok, primary) => { const b = document.createElement("button"); b.type = "button"; b.textContent = label; if (primary) b.className = "primary";
        b.addEventListener("click", async () => { b.disabled = true; try { await post("/api/m/approvals/" + p.id, { approved: ok, id: "appr-" + p.id + (ok ? "-yes" : "-no") }); } catch (e) { row.firstChild.textContent = e.message; } tick(); }); row.appendChild(b); };
      if (p.typed) { const s = document.createElement("span"); s.className = "muted"; s.textContent = t("typed_pc"); row.appendChild(s); mk(t("deny"), false, false); }
      else if (p.pc_only) { const s = document.createElement("span"); s.className = "muted"; s.textContent = t("pc_only"); row.appendChild(s); mk(t("deny"), false, false); }
      else { mk(t("approve"), true, true); mk(t("deny"), false, false); }
    });
  }
  function renderHealth(s) {
    const ul = $("statusList"); ul.replaceChildren();
    li(ul, `${t("core")}: ${s.core}`); li(ul, `${t("tls")}: ${s.tls ? t("yes") : t("no")}`);
    Object.keys(s.components || {}).sort().forEach((k) => li(ul, `${k}: ${s.components[k]}`));
    const tl = $("testList"); tl.replaceChildren();
    const kinds = Object.keys(s.tests || {});
    if (!kinds.length) li(tl, t("no_tests"));
    kinds.forEach((k) => li(tl, s.tests[k][lang === "he" ? "he" : "en"]));
  }
  function renderHome(s) {
    const ul = $("homeList"); ul.replaceChildren();
    li(ul, `${t("core")}: ${s.core}`); li(ul, `${t("waiting")}: ${s.waiting}`);
    li(ul, `${t("pages_you_have")}: ${s.pages.map((x) => ({ home: tx("בית", "Home"), chat: tx("שיחה", "Chat"), approvals: tx("אישורים", "Approvals"), health: tx("תקינות ומהירות", "Health & speed") }[x] || x)).join(", ")}`);
  }
  $("unpair").addEventListener("click", async () => { try { await post("/api/m/unpair"); } catch (e) { /* already gone */ } forget(); });
  $("lang").addEventListener("click", () => { lang = lang === "he" ? "en" : "he"; try { localStorage.setItem(LANG, lang); } catch (e) { /* ignore */ } applyLang(); if (window.JarvesAudio) window.JarvesAudio.repaint(); voiceNote(); info(); unmountChat(); if (tab === "chat") mountChat(); if (tab === "plans") mountPlans(); if (tab === "prefs") mountPrefs(); if (tab === "drive") mountDrive(); tick(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) discardRec(); tick(); });      // never keep the mic open in the background
  window.addEventListener("online", tick);
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/m/sw.js", { scope: "/m/" }).catch(() => {});
  applyLang(); show();
})();
