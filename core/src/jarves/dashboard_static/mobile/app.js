// JARVES phone app (V5): a PWA served by the Core. English + Hebrew. No framework; text via textContent only.
// The bearer token lives in localStorage on this phone; it is never put in a URL.
"use strict";

(function () {
  const KEY = "jarves_m_token", LANG = "jarves_m_lang";
  const T = {
    en: { pair_title: "Pair this phone", pair_help: "On the computer open the JARVES dashboard, go to Phone and press Pair a phone. Scan the QR code, or type the 8-digit code here.",
      code: "Pairing code", name: "Name for this phone", pair: "Pair", pair_net: "Works on your home network only. Nothing here is reachable from the internet.",
      tab_home: "This phone", tab_chat: "Chat", tab_voice: "Voice", tab_approvals: "Approvals", tab_plans: "Plans", tab_prefs: "Learned", tab_drive: "Drive", tab_browser: "Browser", tab_health: "Health & speed", send: "Send", type_here: "Type a message",
      waiting: "Waiting for approval", pages_you_have: "Pages on this device", no_tests: "No self-test or speed test has run yet.", chat_off: "Chat is not running on the computer.",
      hold: "Hold to talk", release: "Release to send", unpair: "Un-pair this phone", online: "connected", offline: "computer unreachable",
      pairing: "Pairing…", bad_code: "Wrong or expired code. Make a new one on the computer.", unpaired: "This phone is no longer paired. Pair again.",
      thinking: "…", err_chat: "JARVES could not answer right now.", voice_tls: "Voice needs a secure (https) connection. On the computer run: jarvis dashboard --mobile --tls-selfsigned. Text chat works without it.",
      voice_ready: "Press and hold the button, speak, then release.", voice_nomic: "Microphone permission was denied or is unavailable.",
      heard: "You said", nothing: "Nothing is waiting for approval.", pc_only: "Answer on the computer.", approve: "Approve", deny: "Deny",
      typed_pc: "Needs the code shown on the computer.", sec: "s", core: "Core", tls: "Secure connection", yes: "yes", no: "no (text only)",
      busy: "The previous message is still being answered. Wait a moment.", phone_ap_on: "Phone approvals are on.", phone_ap_off: "Phone approvals are off: approvals are answered on the computer.", sending: "Sending…",
      banner: "The computer is unreachable. Nothing is sent until it is back; type the message again then.",
      note: "Note", goals: "Goals", pause: "Pause", resume: "Resume", drop: "Drop", snooze: "Snooze notes 1 h", stop_notes: "Stop notes",
      sig_snoozed: "Notes snoozed for one hour.", sig_stopped: "Notes stopped. Turn them on again on the computer.", from_pc: "computer", no_goals: "No active goals.",
      trust_note: "This connection is not trusted (the computer uses its own unverified certificate), so this page cannot be installed as an app. Optional fix: on the computer open the guide \u201cTrusted phone connection\u201d.",
      trust_note_ca: "This phone does not trust the connection yet, so the app cannot be installed. Download the JARVES certificate below and install it yourself: Settings > Security > Install a certificate > CA certificate. The guide on the computer has the steps: \u201cTrusted phone connection\u201d.",
      trust_dl: "Download the certificate", trust_dl_err: "Could not download the certificate. Try again, or copy the file from the computer." },
    he: { pair_title: "צימוד הטלפון", pair_help: "במחשב פתחו את לוח הבקרה של JARVES, עברו אל טלפון ולחצו על צימוד טלפון. סרקו את קוד ה-QR או הקלידו כאן את הקוד בן 8 הספרות.",
      code: "קוד צימוד", name: "שם לטלפון", pair: "לצמד", pair_net: "עובד ברשת הביתית בלבד. שום דבר כאן לא נגיש מהאינטרנט.",
      tab_home: "הטלפון הזה", tab_chat: "שיחה", tab_voice: "קול", tab_approvals: "אישורים", tab_plans: "תוכניות", tab_prefs: "למדתי", tab_drive: "נהיגה", tab_browser: "דפדפן", tab_health: "תקינות ומהירות", send: "שליחה", type_here: "כתבו הודעה",
      waiting: "ממתינים לאישור", pages_you_have: "דפים במכשיר הזה", no_tests: "עוד לא רצה בדיקה עצמית או בדיקת מהירות.", chat_off: "השיחה לא פועלת במחשב.",
      hold: "לחצו והחזיקו לדיבור", release: "שחררו לשליחה", unpair: "ניתוק הטלפון", online: "מחובר", offline: "המחשב לא זמין",
      pairing: "מצמיד…", bad_code: "קוד שגוי או שפג תוקפו. הפיקו קוד חדש במחשב.", unpaired: "הטלפון כבר לא מצומד. צמדו מחדש.",
      thinking: "…", err_chat: "JARVES לא הצליח לענות כרגע.", voice_tls: "קול דורש חיבור מאובטח (https). במחשב הריצו: jarvis dashboard --mobile --tls-selfsigned. שיחה בכתב עובדת גם בלי זה.",
      voice_ready: "לחצו והחזיקו את הכפתור, דברו ושחררו.", voice_nomic: "הרשאת המיקרופון נדחתה או לא זמינה.",
      heard: "אמרתם", nothing: "שום דבר לא ממתין לאישור.", pc_only: "עונים במחשב.", approve: "אישור", deny: "דחייה",
      typed_pc: "דורש את הקוד שמוצג במחשב.", sec: "ש׳", core: "ליבה", tls: "חיבור מאובטח", yes: "כן", no: "לא (טקסט בלבד)",
      busy: "ההודעה הקודמת עדיין בטיפול. המתינו רגע.", phone_ap_on: "אישורים מהטלפון פעילים.", phone_ap_off: "אישורים מהטלפון כבויים: עונים על אישורים במחשב.", sending: "שולח…",
      banner: "המחשב לא זמין. שום דבר לא נשלח עד שהוא חוזר; כתבו את ההודעה שוב אז.",
      note: "הודעה", goals: "יעדים", pause: "השהיה", resume: "המשך", drop: "ויתור", snooze: "השתקת הודעות לשעה", stop_notes: "עצירת הודעות",
      sig_snoozed: "ההודעות הושתקו לשעה.", sig_stopped: "ההודעות נעצרו. מדליקים שוב במחשב.", from_pc: "מחשב", no_goals: "אין יעדים פעילים.",
      trust_note: "החיבור לא מהימן (המחשב משתמש באישור משלו שלא אומת), ולכן אי אפשר להתקין את העמוד כאפליקציה. תיקון אופציונלי: במחשב פתחו את המדריך \u201cחיבור מהימן לטלפון\u201d.",
      trust_note_ca: "הטלפון עוד לא סומך על החיבור, ולכן אי אפשר להתקין את האפליקציה. הורידו למטה את האישור של JARVES והתקינו אותו בעצמכם: הגדרות > אבטחה > התקנת אישור > אישור CA. השלבים במדריך במחשב: \u201cחיבור מהימן לטלפון\u201d.",
      trust_dl: "הורדת האישור", trust_dl_err: "לא הצלחנו להוריד את האישור. נסו שוב, או העבירו את הקובץ מהמחשב." },
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
    if (opts.raw && r.ok) return r.blob();                       // a file download (the public CA certificate)
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { forget(); const u = new Error(t("unpaired")); u.status = 401; throw u; }
    if (!r.ok) { const e = new Error((lang === "he" && d.error_he) || d.error || ("HTTP " + r.status)); e.status = r.status; e.code = d.code; throw e; }
    return d;
  }
  const post = (p, obj) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj || {}) });

  function forget() { unmountChat(); if (window.JarvesAudio) window.JarvesAudio.unmount(); if (window.JarvesPlans) window.JarvesPlans.unmount(); if (window.JarvesPrefs) window.JarvesPrefs.unmount(); if (window.JarvesBrowser) window.JarvesBrowser.unmount(); browserOn = false; token = ""; me = null; try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ } show(); }

  function show() {
    $("pairView").hidden = !!token; $("appView").hidden = !token;
    clearInterval(timer);
    if (token) { load(); timer = setInterval(tick, 4000); }
    else { const m = /(?:^|[#&])c=(\d{6,9})/.exec(location.hash); if (m) { $("code").value = m[1]; history.replaceState(null, "", location.pathname); } }
  }
  async function load() { try { me = await api("/api/m/me"); } catch (e) { return; } await checkBrowser(); info(); allowTabs(); mountIndicator(); tick(); voiceNote(); }
  function mountIndicator() { if (window.JarvesAudio && $("audioBar")) window.JarvesAudio.mount({ audio: $("audioBar"), notices: $("noticeBox") }, { api, post, lang: () => lang }); }
  function info() { if (me) $("meInfo").textContent = `${me.name} · ${me.phone_approvals ? t("phone_ap_on") : t("phone_ap_off")}`; trustNote(); }

  // ---- #513: a text-only hint when the connection is not trusted (the app cannot install). JARVES installs nothing on the
  // phone: the owner downloads the PUBLIC CA certificate here (over the paired, token-checked connection) and installs it himself.
  let swOk = null;                                   // null: not known yet; false: the browser refused the service worker
  function trustNote() {
    const n = $("trustNote"), b = $("trustCa"), tr = (me && me.trust) || null;
    const untrusted = !!tr && (!tr.trusted_source || swOk === false);
    n.hidden = !untrusted; b.hidden = !(untrusted && tr.ca_download);
    if (untrusted) n.textContent = tr.ca_download ? t("trust_note_ca") : t("trust_note");
  }
  $("trustCa").addEventListener("click", async () => {
    const b = $("trustCa"); b.disabled = true;
    try {
      const url = URL.createObjectURL(await api("/api/m/trust/ca", { raw: true }));
      const a = document.createElement("a"); a.href = url; a.download = "jarves-local-ca.crt"; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (e) { $("trustNote").textContent = t("trust_dl_err"); }
    b.disabled = false;
  });

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
  let tab = "chat", chatView = null;
  const TABS = { home: "home", chat: "chat", voice: "chat", approvals: "approvals", plans: "scope:tasks", prefs: "scope:prefs", drive: "scope:drive", browser: "scope:browser", health: "health" };
  const allowed = (n) => (n === "browser" && !browserOn ? false : TABS[n].startsWith("scope:") ? ((me && me.scopes) || []).includes(TABS[n].slice(6)) : ((me && me.pages) || []).includes(TABS[n]));
  function setTab(name) {
    tab = name;
    document.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.tab === tab)));
    Object.keys(TABS).forEach((n) => { $("tab-" + n).hidden = n !== tab; });
    if (tab === "chat") mountChat();
    if (tab === "plans") mountPlans();
    if (tab === "prefs") mountPrefs();
    if (tab === "browser") mountBrowser();
    if (tab === "drive") mountDrive();
    else if (window.JarvesHandsFree) window.JarvesHandsFree.unmount();      // hands-free Drive voice never runs behind another tab
    tick();
  }
  function allowTabs() {
    const pages = (me && me.pages) || [];
    Object.keys(TABS).forEach((n) => { $("tabbtn-" + n).hidden = !allowed(n); });
    if (!allowed(tab)) setTab(pages.includes("chat") ? "chat" : pages.includes("home") ? "home" : (pages[0] ? Object.keys(TABS).find((n) => TABS[n] === pages[0]) : "home"));
    else setTab(tab);
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
  // The composer (redesign #511): attachments placeholder + push-to-talk mic beside Send (chatview.js `phone` option)
  let micBtn = null;
  function composer() {
    const S = window.JarvesShell, ic = (n) => (S ? S.icon(n) : document.createTextNode(n));
    const attach = document.createElement("button"); attach.type = "button"; attach.className = "jc-icon jc-attach";
    attach.setAttribute("aria-label", tx("צירוף קובץ", "Attach a file")); attach.title = attach.getAttribute("aria-label"); attach.appendChild(ic("plus"));
    attach.addEventListener("click", () => { if (S) S.toast(tx("צירוף קבצים עוד לא זמין בטלפון.", "Attaching files is not available on the phone yet.")); });
    micBtn = document.createElement("button"); micBtn.type = "button"; micBtn.className = "jc-icon jc-mic";
    micBtn.setAttribute("aria-label", t("hold")); micBtn.title = t("hold"); micBtn.appendChild(ic("mic"));
    micBtn.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (!(window.isSecureContext && me && me.voice)) { if (S) S.toast(t("voice_tls")); return; }
      startRec("chat");
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach((n) => micBtn.addEventListener(n, () => { if (rec && rec.target === "chat") stopRec(); }));
    micBtn.addEventListener("contextmenu", (e) => e.preventDefault());
    return { start: [attach], end: [micBtn] };
  }
  function mountChat() {
    if (chatView || !window.JarvesChat) return;
    const S = window.JarvesShell;
    chatView = window.JarvesChat.mount($("chatRoot"), { api: chatApi, tx, device: true,
      phone: { icon: S ? S.icon : null, hint: S ? S.hint : null, ...composer(), onEvent: (e) => { if (S) S.onChatEvent(e); } } });
  }
  function mountPlans() { if (window.JarvesPlans) window.JarvesPlans.mount($("plansRoot"), { api, post, lang: () => lang }); }
  function mountBrowser() { if (window.JarvesBrowser) window.JarvesBrowser.mount($("browserRoot"), { api, post, lang: () => lang }); }
  // The Browser tab shows only when the owner gave this phone the `browser` scope AND the computer says the phone view is on
  // (GET /api/m/browser enabled). A refusal (403: no scope, or the view is off) hides it without a word.
  let browserOn = false;
  async function checkBrowser() {
    const was = browserOn;
    if (!(me && (me.scopes || []).includes("browser"))) browserOn = false;
    else {
      try { const s = await api("/api/m/browser"); browserOn = !!s.enabled; if (window.JarvesBrowser) window.JarvesBrowser.status(s); }
      catch (e) { if (e.status) browserOn = false; }                // offline: keep what was known
    }
    return was !== browserOn;
  }
  function mountPrefs() { if (window.JarvesPrefs) window.JarvesPrefs.mount($("prefsRoot"), { api, post, lang: () => lang }); }
  function mountDrive() {
    if (window.JarvesHandsFree) window.JarvesHandsFree.mount($("hfRoot"), { api, lang: () => lang });
    if (window.JarvesDrive) window.JarvesDrive.mount($("driveRoot"), { api, post, lang: () => lang });
  }
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
  function recUi(target, on) {                                              // the button that is recording shows it
    if (target === "chat") { if (micBtn) { micBtn.classList.toggle("rec", on); micBtn.setAttribute("aria-label", on ? t("release") : t("hold")); micBtn.setAttribute("aria-pressed", String(on)); } return; }
    $("ptt").classList.toggle("rec", on); $("ptt").textContent = on ? t("release") : t("hold");
  }
  async function startRec(target) {
    target = target === "chat" ? "chat" : "voice";
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
      src.connect(proc); proc.connect(ctx.destination); rec = { stream, ctx, proc, src, chunks, target };
      recUi(target, true);
    } catch (e) {
      rec = null; closeAll(); recUi(target, false);
      if (window.JarvesAudio) window.JarvesAudio.phoneMic(false);
      if (target === "chat") { if (window.JarvesShell) window.JarvesShell.toast(t("voice_nomic")); } else $("voiceOut").textContent = t("voice_nomic");
    }
  }
  function discardRec() {                                                    // close the microphone and send nothing
    if (!rec) return; const r = rec; rec = null;
    r.proc.disconnect(); r.src.disconnect(); r.stream.getTracks().forEach((x) => x.stop()); r.ctx.close();
    recUi(r.target, false); if (r.target !== "chat") $("voiceOut").textContent = "";
    if (window.JarvesAudio) window.JarvesAudio.phoneMic(false);
  }
  async function stopRec() {
    if (!rec) return; const r = rec; rec = null;
    r.proc.disconnect(); r.src.disconnect(); r.stream.getTracks().forEach((x) => x.stop()); const rate = r.ctx.sampleRate; r.ctx.close();
    recUi(r.target, false);
    if (window.JarvesAudio) window.JarvesAudio.phoneMic(false);
    if (!r.chunks.length) return;
    const inChat = r.target === "chat" && chatView;
    if (inChat) { if (window.JarvesShell) window.JarvesShell.toast(t("sending")); } else $("voiceOut").textContent = t("sending");
    try {
      const d = await api("/api/m/voice", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav(down(r.chunks, rate, 16000), 16000) });
      if (inChat) { if (d.transcript) chatView.local("user", d.transcript); if (d.reply) chatView.local("assistant", d.reply); }
      else $("voiceOut").textContent = d.transcript ? `${t("heard")}: ${d.transcript}\n\n${d.reply}` : (d.reply || "");
    } catch (e) { if (inChat) { if (window.JarvesShell) window.JarvesShell.toast(e.message); } else $("voiceOut").textContent = e.message; }
  }
  const ptt = $("ptt");
  ptt.addEventListener("pointerdown", (e) => { e.preventDefault(); startRec("voice"); });
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
      if (me.scopes && me.scopes.includes("browser") && await checkBrowser()) allowTabs();
      if (tab === "home") renderHome(await api("/api/m/home"));
      if (tab === "health") renderHealth(await api("/api/m/health"));
    } catch (e) { /* the connection pill already says why */ }
  }
  function li(parent, text, cls) { const l = document.createElement("li"); const s = document.createElement("span"); s.className = "grow"; s.textContent = text; l.appendChild(s); parent.appendChild(l); return l; }
  function renderApprovals(a) {
    $("apprNote").textContent = a.phone_approvals ? t("phone_ap_on") : t("phone_ap_off");
    if (me && !!me.phone_approvals !== !!a.phone_approvals) { me.phone_approvals = !!a.phone_approvals; info(); }   // the "This phone" line follows the switch
    const ul = $("apprList"); ul.replaceChildren();
    if (!a.pending.length) { li(ul, t("nothing")); return; }
    a.pending.forEach((p) => {                        // one card per request: title, risk + time left, a note, the answers
      const row = li(ul, p.title); row.classList.add("appr", "risk-" + (p.risk || "low"));
      const meta = document.createElement("div"); meta.className = "appr-meta";
      const chip = (text, cls) => { const c = document.createElement("span"); c.className = "chip " + (cls || ""); c.textContent = text; meta.appendChild(c); };
      if (p.risk) chip(tx("סיכון ", "Risk ") + ({ low: tx("נמוך", "low"), medium: tx("בינוני", "medium"), high: tx("גבוה", "high") }[p.risk] || p.risk), "risk");
      chip(`${p.seconds_left} ${t("sec")}`, "time");
      row.appendChild(meta);
      if (!a.phone_approvals) return;
      const acts = document.createElement("div"); acts.className = "appr-acts";
      const mk = (label, ok, primary) => { const b = document.createElement("button"); b.type = "button"; b.textContent = label; if (primary) b.className = "primary";
        b.addEventListener("click", async () => { b.disabled = true; try { await post("/api/m/approvals/" + p.id, { approved: ok, id: "appr-" + p.id + (ok ? "-yes" : "-no") }); } catch (e) { row.firstChild.textContent = e.message; } tick(); }); acts.appendChild(b); };
      const why = (text) => { const s = document.createElement("span"); s.className = "muted appr-why"; s.textContent = text; row.appendChild(s); };
      if (p.typed) { why(t("typed_pc")); mk(t("deny"), false, false); }
      else if (p.pc_only) { why(t("pc_only")); mk(t("deny"), false, false); }
      else { mk(t("deny"), false, false); mk(t("approve"), true, true); }
      row.appendChild(acts);
    });
  }
  // health values and component names come from the computer in English: say them in Hebrew too (unknown ones stay as sent)
  const HV = { running: "רצה", stopped: "עצורה", ok: "תקין", down: "לא פעיל", off: "כבוי", on: "פועל", idle: "לא פעיל", error: "שגיאה", unknown: "לא ידוע", ready: "מוכן" };
  const HK = { audio: "שמע", core: "ליבה", model: "מודל", voice: "קול", wake: "מילת הפעלה", tts: "הקראה", stt: "תמלול", bridge: "גשר" };
  const hv = (v) => (lang === "he" && HV[String(v).toLowerCase()]) || v;
  const hk = (k) => (lang === "he" && HK[k]) || (k.charAt(0).toUpperCase() + k.slice(1));
  function renderHealth(s) {
    const ul = $("statusList"); ul.replaceChildren();
    const st = (row, v) => { const x = String(v).toLowerCase(); row.classList.add(/^(ok|running|ready|yes|true|on|pass(ed)?)$/.test(x) ? "st-ok"
      : /(fail|error|down|unreach|stopped|missing|bad)/.test(x) ? "st-bad" : "st-idle"); return row; };
    st(li(ul, `${t("core")}: ${hv(s.core)}`), s.core); st(li(ul, `${t("tls")}: ${s.tls ? t("yes") : t("no")}`), s.tls ? "ok" : "idle");
    Object.keys(s.components || {}).sort().forEach((k) => st(li(ul, `${hk(k)}: ${hv(s.components[k])}`), s.components[k]));
    const tl = $("testList"); tl.replaceChildren();
    const kinds = Object.keys(s.tests || {});
    if (!kinds.length) li(tl, t("no_tests"));
    kinds.forEach((k) => li(tl, s.tests[k][lang === "he" ? "he" : "en"]));
  }
  function renderHome(s) {
    const ul = $("homeList"); ul.replaceChildren();
    li(ul, `${t("core")}: ${hv(s.core)}`); li(ul, `${tx("ממתינים לאישור", "Waiting for approval")}: ${s.waiting}`);
    li(ul, `${t("pages_you_have")}: ${s.pages.map((x) => ({ home: tx("בית", "Home"), chat: tx("שיחה", "Chat"), approvals: tx("אישורים", "Approvals"), health: tx("תקינות ומהירות", "Health & speed") }[x] || x)).join(", ")}`);
  }
  $("unpair").addEventListener("click", async () => { try { await post("/api/m/unpair"); } catch (e) { /* already gone */ } forget(); });
  $("lang").addEventListener("click", () => { lang = lang === "he" ? "en" : "he"; try { localStorage.setItem(LANG, lang); } catch (e) { /* ignore */ } applyLang(); if (window.JarvesShell) window.JarvesShell.repaint(); if (window.JarvesAudio) window.JarvesAudio.repaint(); voiceNote(); info(); unmountChat(); if (tab === "chat") mountChat(); if (tab === "plans") mountPlans(); if (tab === "prefs") mountPrefs(); if (tab === "drive") mountDrive(); if (tab === "browser") mountBrowser(); tick(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) discardRec(); tick(); });      // never keep the mic open in the background
  window.addEventListener("online", tick);
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/m/sw.js", { scope: "/m/" }).then(() => { swOk = true; trustNote(); }).catch(() => { swOk = false; trustNote(); });
  else swOk = false;
  if (window.JarvesShell) window.JarvesShell.init({ tx, go: setTab, chat: () => chatView });
  applyLang(); show();
})();
