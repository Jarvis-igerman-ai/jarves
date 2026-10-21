// JARVES phone app (V5): a PWA served by the Core. English + Hebrew. No framework; text via textContent only.
// The bearer token lives in localStorage on this phone; it is never put in a URL.
"use strict";
(function () {
  const KEY = "jarves_m_token", LANG = "jarves_m_lang";
  const T = {
    en: { pair_title: "Pair this phone", pair_help: "On the computer open the JARVES dashboard, go to Phone and press Pair a phone. Scan the QR code, or type the 8-digit code here.",
      code: "Pairing code", name: "Name for this phone", pair: "Pair", pair_net: "Works on your home network only. Nothing here is reachable from the internet.",
      tab_chat: "Chat", tab_voice: "Voice", tab_approvals: "Approvals", tab_status: "Status", send: "Send", type_here: "Type a message",
      hold: "Hold to talk", release: "Release to send", unpair: "Un-pair this phone", online: "connected", offline: "computer unreachable",
      pairing: "Pairing…", bad_code: "Wrong or expired code. Make a new one on the computer.", unpaired: "This phone is no longer paired. Pair again.",
      thinking: "…", err_chat: "JARVES could not answer right now.", voice_tls: "Voice needs a secure (https) connection. On the computer run: jarvis dashboard --mobile --tls-selfsigned. Text chat works without it.",
      voice_ready: "Press and hold the button, speak, then release.", voice_nomic: "Microphone permission was denied or is unavailable.",
      heard: "You said", nothing: "Nothing is waiting for approval.", pc_only: "Answer on the computer.", approve: "Approve", deny: "Deny",
      typed_pc: "Needs the code shown on the computer.", sec: "s", core: "Core", tls: "Secure connection", yes: "yes", no: "no (text only)",
      phone_ap_on: "Phone approvals are on.", phone_ap_off: "Phone approvals are off: approvals are answered on the computer.", sending: "Sending…" },
    he: { pair_title: "צימוד הטלפון", pair_help: "במחשב פתחו את לוח הבקרה של JARVES, עברו אל טלפון ולחצו על צימוד טלפון. סרקו את קוד ה-QR או הקלידו כאן את הקוד בן 8 הספרות.",
      code: "קוד צימוד", name: "שם לטלפון", pair: "לצמד", pair_net: "עובד ברשת הביתית בלבד. שום דבר כאן לא נגיש מהאינטרנט.",
      tab_chat: "שיחה", tab_voice: "קול", tab_approvals: "אישורים", tab_status: "מצב", send: "שליחה", type_here: "כתבו הודעה",
      hold: "לחצו והחזיקו לדיבור", release: "שחררו לשליחה", unpair: "ניתוק הטלפון", online: "מחובר", offline: "המחשב לא זמין",
      pairing: "מצמיד…", bad_code: "קוד שגוי או שפג תוקפו. הפיקו קוד חדש במחשב.", unpaired: "הטלפון כבר לא מצומד. צמדו מחדש.",
      thinking: "…", err_chat: "JARVES לא הצליח לענות כרגע.", voice_tls: "קול דורש חיבור מאובטח (https). במחשב הריצו: jarvis dashboard --mobile --tls-selfsigned. שיחה בכתב עובדת גם בלי זה.",
      voice_ready: "לחצו והחזיקו את הכפתור, דברו ושחררו.", voice_nomic: "הרשאת המיקרופון נדחתה או לא זמינה.",
      heard: "אמרתם", nothing: "שום דבר לא ממתין לאישור.", pc_only: "עונים במחשב.", approve: "אישור", deny: "דחייה",
      typed_pc: "דורש את הקוד שמוצג במחשב.", sec: "ש׳", core: "ליבה", tls: "חיבור מאובטח", yes: "כן", no: "לא (טקסט בלבד)",
      phone_ap_on: "אישורים מהטלפון פעילים.", phone_ap_off: "אישורים מהטלפון כבויים: עונים על אישורים במחשב.", sending: "שולח…" },
  };
  const $ = (id) => document.getElementById(id);
  let lang = "en";
  try { lang = localStorage.getItem(LANG) || ((navigator.language || "en").startsWith("he") ? "he" : "en"); } catch (e) { /* storage blocked */ }
  const t = (k) => (T[lang] && T[lang][k]) || T.en[k] || k;
  function applyLang() {
    document.documentElement.lang = lang; document.documentElement.dir = lang === "he" ? "rtl" : "ltr";
    document.querySelectorAll("[data-t]").forEach((n) => { const c = n.querySelector(".badge"); if (c) { n.firstChild.textContent = t(n.dataset.t) + " "; } else n.textContent = t(n.dataset.t); });
    document.querySelectorAll("[data-ph]").forEach((n) => { n.placeholder = t(n.dataset.ph); });
    setConn(connected);
  }
  let token = ""; try { token = localStorage.getItem(KEY) || ""; } catch (e) { /* storage blocked */ }
  let me = null, connected = true, timer = 0;

  function setConn(ok) { connected = ok; const c = $("conn"); c.textContent = ok ? t("online") : t("offline"); c.className = "pill " + (ok ? "ok" : "bad"); }

  async function api(path, opts = {}) {
    const headers = { "X-Jarves": "1", ...(opts.headers || {}) };
    if (token) headers.Authorization = "Bearer " + token;
    let r;
    try { r = await fetch(path, { method: opts.method || "GET", headers, body: opts.body, cache: "no-store" }); }
    catch (e) { setConn(false); throw new Error(t("offline")); }
    setConn(true);
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { forget(); throw new Error(t("unpaired")); }
    if (!r.ok) { const e = new Error(d.error || ("HTTP " + r.status)); e.status = r.status; e.code = d.code; throw e; }
    return d;
  }
  const post = (p, obj) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj || {}) });

  function forget() { token = ""; me = null; try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ } show(); }

  function show() {
    $("pairView").hidden = !!token; $("appView").hidden = !token;
    clearInterval(timer);
    if (token) { load(); timer = setInterval(tick, 4000); }
    else { const m = /(?:^|[#&])c=(\d{6,9})/.exec(location.hash); if (m) { $("code").value = m[1]; history.replaceState(null, "", location.pathname); } }
  }
  async function load() { try { me = await api("/api/m/me"); } catch (e) { return; } info(); tick(); voiceNote(); }
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

  // ---- tabs
  let tab = "chat";
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    tab = b.dataset.tab;
    document.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    ["chat", "voice", "approvals", "status"].forEach((n) => { $("tab-" + n).hidden = n !== tab; });
    tick();
  }));

  // ---- chat
  function bubble(text, cls) { const d = document.createElement("div"); d.className = "msg " + (cls || ""); d.textContent = text; $("log").appendChild(d); d.scrollIntoView({ block: "end" }); return d; }
  $("chatForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const box = $("chatText"), text = box.value.trim(); if (!text) return;
    box.value = ""; bubble(text, "me"); const wait = bubble(t("thinking"));
    try { const d = await post("/api/m/chat", { text }); wait.textContent = d.reply; if (!d.ok) wait.classList.add("err"); }
    catch (e) { wait.textContent = e.status === 429 || e.status === 503 || e.status === 502 ? e.message : (e.message || t("err_chat")); wait.classList.add("err"); }
  });

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
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const ctx = new (window.AudioContext || window.webkitAudioContext)(); const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1); const chunks = [];
      proc.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      src.connect(proc); proc.connect(ctx.destination); rec = { stream, ctx, proc, src, chunks };
      $("ptt").classList.add("rec"); $("ptt").textContent = t("release");
    } catch (e) { $("voiceOut").textContent = t("voice_nomic"); }
  }
  async function stopRec() {
    if (!rec) return; const r = rec; rec = null;
    r.proc.disconnect(); r.src.disconnect(); r.stream.getTracks().forEach((x) => x.stop()); const rate = r.ctx.sampleRate; r.ctx.close();
    $("ptt").classList.remove("rec"); $("ptt").textContent = t("hold");
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
  async function tick() {
    if (!token || document.hidden) return;
    try {
      const a = await api("/api/m/approvals"); const n = a.pending.length, badge = $("apprCount");
      badge.hidden = !n; badge.textContent = String(n);
      if (tab === "approvals") renderApprovals(a);
      if (tab === "status") renderStatus(await api("/api/m/status"));
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
        b.addEventListener("click", async () => { b.disabled = true; try { await post("/api/m/approvals/" + p.id, { approved: ok }); } catch (e) { row.firstChild.textContent = e.message; } tick(); }); row.appendChild(b); };
      if (p.typed) { const s = document.createElement("span"); s.className = "muted"; s.textContent = t("typed_pc"); row.appendChild(s); mk(t("deny"), false, false); }
      else { mk(t("approve"), true, true); mk(t("deny"), false, false); }
    });
  }
  function renderStatus(s) {
    const ul = $("statusList"); ul.replaceChildren();
    li(ul, `${t("core")}: ${s.core}`); li(ul, `${t("tls")}: ${s.tls ? t("yes") : t("no")}`);
    Object.keys(s.components || {}).sort().forEach((k) => li(ul, `${k}: ${s.components[k]}`));
  }
  $("unpair").addEventListener("click", async () => { try { await post("/api/m/unpair"); } catch (e) { /* already gone */ } forget(); });
  $("lang").addEventListener("click", () => { lang = lang === "he" ? "en" : "he"; try { localStorage.setItem(LANG, lang); } catch (e) { /* ignore */ } applyLang(); voiceNote(); info(); tick(); });
  document.addEventListener("visibilitychange", tick); window.addEventListener("online", tick);
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/m/sw.js", { scope: "/m/" }).catch(() => {});
  applyLang(); show();
})();
