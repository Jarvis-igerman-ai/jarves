// JARVES phone: hands-free "Drive voice" (V6, issue #219). OFF unless the owner switched `phone_hands_free` on at the computer.
// HONEST SCOPE: a phone web page can use the microphone only while it is open, visible and the screen is on. So this is a FOREGROUND
// session. It never listens in the background and has no wake word. It keeps the screen awake (Screen Wake Lock) and ends, with the
// microphone closed and a plain message, when the page is hidden, the screen lock is lost, the audio route (Bluetooth car audio)
// disappears, or a time limit passes. Push-to-talk (Voice tab) stays the default. Text via textContent only; the token stays in app.js.
"use strict";
(function () {
  const T = {
    en: { h: "Drive voice", intro: "Hands-free while this page is open and the screen is on. It never listens in the background.", start: "Start Drive voice", end: "End",
      off: "Hands-free Drive voice is switched off on the computer.", s_idle: "Ready", s_listening: "Listening", s_hearing: "Hearing you", s_thinking: "Thinking", s_speaking: "Speaking (say stop)",
      s_ended: "Ended", waiting: "Waiting for when you stop", nowake: "This phone did not allow keeping the screen on. If the screen locks the session ends.",
      secure: "Voice needs a secure (https) connection.", nomic: "Microphone permission was denied or is unavailable.", failed: "Could not start",
      e_route_lost: "The car audio disconnected, so hands-free ended and the microphone is closed. Use push-to-talk.",
      e_wake_lock_lost: "The screen lock was lost, so the session ended. The phone does not listen in the background.",
      e_hidden: "The page left the screen, so the session ended. The phone does not listen in the background.",
      e_mic_lost: "The microphone went away, so the session ended.", e_idle: "Quiet for too long, so the session ended. The microphone is closed.",
      e_time: "Time limit reached, so the session ended. The microphone is closed.", e_user: "Session ended. The microphone is closed.",
      e_drive_off: "Drive mode is off, so the session ended. The microphone is closed.", e_error: "Something went wrong, so the session ended. The microphone is closed.", busy: "Busy: wait a moment." },
    he: { h: "קול נהיגה", intro: "ללא ידיים כל עוד העמוד פתוח והמסך דלוק. אף פעם לא מקשיב ברקע.", start: "התחילו קול נהיגה", end: "סיום",
      off: "קול נהיגה ללא ידיים כבוי במחשב.", s_idle: "מוכן", s_listening: "מקשיב", s_hearing: "שומע אתכם", s_thinking: "חושב", s_speaking: "מדבר (אמרו עצור)",
      s_ended: "הסתיים", waiting: "ממתין לשתעצרו", nowake: "הטלפון לא אישר להשאיר את המסך דלוק. אם המסך ננעל השיחה תסתיים.",
      secure: "קול דורש חיבור מאובטח (https).", nomic: "הרשאת המיקרופון נדחתה או לא זמינה.", failed: "לא ניתן להתחיל",
      e_route_lost: "חיבור השמע לרכב נותק, ולכן השיחה ללא ידיים הסתיימה והמיקרופון סגור. השתמשו בלחצן הדיבור.",
      e_wake_lock_lost: "נעילת המסך אבדה, ולכן השיחה הסתיימה. הטלפון לא מקשיב ברקע.",
      e_hidden: "העמוד לא על המסך, ולכן השיחה הסתיימה. הטלפון לא מקשיב ברקע.",
      e_mic_lost: "המיקרופון נעלם, ולכן השיחה הסתיימה.", e_idle: "שקט יותר מדי זמן, ולכן השיחה הסתיימה. המיקרופון סגור.",
      e_time: "הגעתם לזמן המרבי, ולכן השיחה הסתיימה. המיקרופון סגור.", e_user: "השיחה הסתיימה. המיקרופון סגור.",
      e_drive_off: "מצב הנהיגה כבוי, ולכן השיחה הסתיימה. המיקרופון סגור.", e_error: "משהו השתבש, ולכן השיחה הסתיימה. המיקרופון סגור.", busy: "עסוק: חכו רגע." },
  };
  const ECHO_MS = 1500;                                  // speech that starts this soon after the phone spoke may be its own echo
  const START_CHUNKS = 2, BARGE_CHUNKS = 3, MAX_UTTER_MS = 20000, MIN_UTTER_MS = 350, MAX_ERRORS = 3;
  const cfg = { threshold: 0.02, silenceMs: 900 };                    // voice-activity tuning (tests shorten the silence window)
  let root = null, cx = null, S = null, shown = null;
  const t = (k) => ((T[cx ? cx.lang() : "en"] || T.en)[k]) || T.en[k] || k;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  // Pure helper (also used by the tests): did the audio route we were using disappear?
  function routeLost(before, after, usedId) {
    const ins = (after || []).filter((d) => d.kind === "audioinput"), outs = (after || []).filter((d) => d.kind === "audiooutput");
    const wasOuts = (before || []).filter((d) => d.kind === "audiooutput").length;
    if (usedId && ins.length && !ins.some((d) => d.deviceId === usedId)) return true;
    return !ins.length || outs.length < wasOuts;
  }
  function wavOf(chunks, rate) {                       // 16 kHz mono 16-bit WAV (same format as push-to-talk)
    let n = 0; chunks.forEach((c) => { n += c.length; }); const all = new Float32Array(n); let o = 0; chunks.forEach((c) => { all.set(c, o); o += c.length; });
    const len = Math.floor(all.length * 16000 / rate), out = new DataView(new ArrayBuffer(44 + len * 2)); const w = (p, s) => { for (let i = 0; i < s.length; i++) out.setUint8(p + i, s.charCodeAt(i)); };
    w(0, "RIFF"); out.setUint32(4, 36 + len * 2, true); w(8, "WAVEfmt "); out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
    out.setUint32(24, 16000, true); out.setUint32(28, 32000, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true); w(36, "data"); out.setUint32(40, len * 2, true);
    for (let i = 0; i < len; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, all[Math.floor(i * rate / 16000)])) * 32767, true);
    return out.buffer;
  }
  function paint() {
    if (!root) return; root.replaceChildren();
    const c = el("div", "card"); c.appendChild(el("h2", "", t("h"))); root.appendChild(c);
    if (!shown || !shown.hands_free_on) { c.appendChild(el("p", "muted", t("off"))); return; }
    c.appendChild(el("p", "muted", t("intro")));
    const live = S && !S.over;
    const st = el("p", "hf-state", live ? t("s_" + S.state) : (S && S.msg ? t("s_ended") : t("s_idle"))); st.setAttribute("role", "status"); st.setAttribute("aria-live", "assertive"); st.dataset.state = live ? S.state : "idle";
    c.appendChild(st);
    if (S && S.msg) { const m = el("p", "", S.msg); m.setAttribute("role", "status"); m.setAttribute("dir", "auto"); c.appendChild(m); }
    if (S && S.say) { const m = el("p", "muted", S.say); m.setAttribute("dir", "auto"); c.appendChild(m); }
    if (live && S.waiting) c.appendChild(el("p", "muted", t("waiting") + ": " + S.waiting));
    const b = el("button", "big", live ? t("end") : t("start")); b.type = "button"; b.id = "hfBtn"; b.addEventListener("click", () => (live ? finish("user", "e_user") : start())); c.appendChild(b);
  }
  function set(state) { if (S && !S.over) { S.state = state; paint(); } }
  async function finish(reason, key, text) {          // closes EVERYTHING first, then tells the computer why
    if (!S || S.over) return; const s = S; s.over = true; s.msg = text || t(key || "e_user");
    release(s);
    paint();
    tell(reason);
  }
  function tell(reason) {
    return cx.api("/api/m/drive/voice/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) }).catch(() => { /* the page is already closed */ });
  }
  function release(s) {                               // mic, audio graph, wake lock, listeners: idempotent, safe on a half-started session
    try { clearInterval(s.timer); } catch (e) { /* ignore */ }
    try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) { /* ignore */ }
    try { s.proc && (s.proc.onaudioprocess = null, s.proc.disconnect()); s.src && s.src.disconnect(); } catch (e) { /* ignore */ }
    try { s.stream && s.stream.getTracks().forEach((x) => { x.onended = null; x.stop(); }); } catch (e) { /* ignore */ }
    try { s.ctx && s.ctx.close(); } catch (e) { /* ignore */ }
    try { s.lock && (s.lock.onrelease = null, s.lock.release()); } catch (e) { /* ignore */ }
    document.removeEventListener("visibilitychange", s.onvis);
    try { navigator.mediaDevices.removeEventListener("devicechange", s.ondev); } catch (e) { /* ignore */ }
    if (window.JarvesAudio) window.JarvesAudio.phoneMic(false);
  }
  async function start() {
    if (S && !S.over) return;
    S = { state: "listening", over: false, msg: "", say: "", waiting: 0, errors: 0 }; const s = S;
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { s.over = true; s.msg = t("secure"); paint(); return; }
    try {
      const d = await cx.api("/api/m/drive/voice/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      s.maxMs = (d.max_s || 1800) * 1000; s.idleMs = (d.idle_s || 90) * 1000; s.t0 = s.last = Date.now();
    } catch (e) { s.over = true; s.msg = t("failed") + ": " + e.message; paint(); return; }
    if (s.over) { tell("user"); return; }               // ended (tab left, page hidden) while the computer answered
    try {
      s.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const tr = s.stream.getAudioTracks()[0]; s.used = tr && tr.getSettings ? tr.getSettings().deviceId : "";
      if (s.over) { release(s); tell("user"); return; }  // ended during the permission prompt: close the mic at once
      s.before = await navigator.mediaDevices.enumerateDevices().catch(() => []);
      if (s.over) { release(s); tell("user"); return; }
      tr.onended = () => finish("mic_lost", "e_mic_lost");
      s.ondev = async () => { const after = await navigator.mediaDevices.enumerateDevices().catch(() => []); if (routeLost(s.before, after, s.used)) finish("route_lost", "e_route_lost"); else s.before = after; };
      navigator.mediaDevices.addEventListener("devicechange", s.ondev);
      s.ctx = new (window.AudioContext || window.webkitAudioContext)(); s.src = s.ctx.createMediaStreamSource(s.stream);
      s.proc = s.ctx.createScriptProcessor(2048, 1, 1); s.proc.onaudioprocess = (e) => onChunk(s, new Float32Array(e.inputBuffer.getChannelData(0)));
      s.src.connect(s.proc); s.proc.connect(s.ctx.destination);
    } catch (e) { s.msg = t("nomic"); s.over = true; release(s); paint(); cx.api("/api/m/drive/voice/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "mic_lost" }) }).catch(() => {}); return; }
    if (window.JarvesAudio) window.JarvesAudio.phoneMic(true, { stop: () => finish("user", "e_user"), discard: () => finish("user", "e_user"), live: () => !!s.stream && s.stream.getTracks().some((x) => x.readyState === "live") });
    try {                                              // keep the screen on; a lost lock while visible ends the session
      if (navigator.wakeLock) { s.lock = await navigator.wakeLock.request("screen"); s.lock.onrelease = () => { if (!document.hidden) finish("wake_lock_lost", "e_wake_lock_lost"); }; } else s.say = t("nowake");
    } catch (e) { s.say = t("nowake"); }
    if (s.over) { release(s); tell("user"); return; }
    s.onvis = () => { if (document.hidden) finish("hidden", "e_hidden"); }; document.addEventListener("visibilitychange", s.onvis);
    if (document.hidden) return finish("hidden", "e_hidden");          // hidden before the listener existed
    s.timer = setInterval(() => { const n = Date.now(); if (n - s.t0 > s.maxMs) finish("user", "e_time"); else if (n - s.last > s.idleMs && s.state !== "thinking" && s.state !== "speaking") finish("user", "e_idle"); }, 1000);
    paint();
  }
  function onChunk(s, f) {
    if (s.over) return; let sum = 0; for (let i = 0; i < f.length; i++) sum += f[i] * f[i]; const loud = Math.sqrt(sum / f.length) > cfg.threshold, now = Date.now();
    if (s.state === "thinking") return;
    if (s.state === "speaking") {                      // barge-in: the owner talking over the answer stops it and is heard
      s.loud = loud ? (s.loud || 0) + 1 : 0; if (s.loud < BARGE_CHUNKS) return; stopSpeech(); s.loud = 0; s.cap = null; s.spokeAt = now; set("listening");
    }
    if (!s.cap) { s.pre = (s.pre || []).concat([f]).slice(-3); s.loud = loud ? (s.loud || 0) + 1 : 0; if (s.loud >= START_CHUNKS) { s.cap = { chunks: s.pre.slice(), t0: now, quiet: now, barge: now - (s.spokeAt || 0) < ECHO_MS }; s.last = now; set("hearing"); } return; }
    s.cap.chunks.push(f); if (loud) s.cap.quiet = now;
    if (now - s.cap.quiet > cfg.silenceMs || now - s.cap.t0 > MAX_UTTER_MS) { const c = s.cap; s.cap = null; s.loud = 0; if (now - c.t0 < MIN_UTTER_MS) { set("listening"); return; } turn(s, c.chunks, c.barge); }
  }
  function stopSpeech() { try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) { /* ignore */ } }
  async function turn(s, chunks, barge) {
    set("thinking");
    try {
      const d = await cx.api("/api/m/drive/voice/turn" + (barge ? "?barge=1" : ""), { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wavOf(chunks, s.ctx.sampleRate) });
      if (s.over) return; s.errors = 0; s.waiting = d.waiting != null ? d.waiting : s.waiting;
      if (d.action === "end") return finish("user", "e_user");
      if (d.action === "speak" && d.reply) { s.last = Date.now(); s.say = d.reply; set("speaking"); return speak(s, d.reply); }
      if (d.action === "stop_speech") stopSpeech();
      s.last = Date.now(); set("listening");
    } catch (e) {
      if (s.over) return; s.errors++;
      if (e.status === 409 && e.code !== "BUSY") return finish("user", "e_error", e.message);   // the computer ended it: show ITS reason (drive off, time, restart)
      if (e.status === 429 || e.status === 409) { s.say = t("busy"); return set("listening"); }
      if (s.errors >= MAX_ERRORS) return finish("user", "e_error");
      s.say = e.message; set("listening");
    }
  }
  function speak(s, text) {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) return set("listening");
    const u = new SpeechSynthesisUtterance(text); u.lang = cx.lang() === "he" ? "he-IL" : "en-US";
    u.onend = u.onerror = () => { s.spokeAt = Date.now(); if (!s.over && s.state === "speaking") { s.last = Date.now(); set("listening"); } };
    window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
  }
  window.JarvesHandsFree = {
    mount(node, ctx) { root = node; cx = ctx; if (!S || S.over) { S = null; shown = null; } paint(); },
    update(st) { shown = st; if (!S || S.over) paint(); else if (!st.hands_free_on || !st.drive_mode) finish("user", "e_drive_off"); },
    unmount() { if (S && !S.over) finish("user", "e_user"); root = null; },
    routeLost, cfg, catalogs: T,
  };
})();
