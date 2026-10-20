// JARVES dashboard: the privacy strip on Home (same states and words as the face, godot/face/privacy_strip.gd).
// Live from /api/runtime -> "privacy" (core/src/jarves/runtime/privacy_state.py); no Core = every chip "unknown".
// Mic chip: software mute. Camera chip: the Camera switch (turning it on asks here first). "What does he see?":
// polls /api/camera/preview (frames from the Core's RAM, shown in an <img>, never stored; only on this computer).
"use strict";

(function () {
  const STATES = {
    mic: { listening: ["מאזין", "Listening", "capture"], gated: ["מושהה", "Paused", "standby"],
      wake_only: ["רק מילת הפעלה", "Wake word only", "local"], muted: ["מושתק", "Muted", "off"],
      idle: ["סגור", "Off", "off"], unavailable: ["אין מיקרופון", "No mic", "none"] },
    camera: { photo: ["מצלם!", "Taking photo", "out"], preview: ["תצוגה פתוחה", "Preview on", "capture"],
      presence: ["דולקת (נוכחות)", "On (presence)", "capture"], off: ["סגורה", "Closed", "off"],
      disabled: ["כבויה", "Off", "off"], unavailable: ["אין מצלמה", "No camera", "none"] },
    speaker: { speaking: ["מדבר", "Speaking", "local"], quiet: ["שקט", "Quiet", "off"],
      unavailable: ["אין רמקול", "No speaker", "none"] },
    cloud_audio: { sending: ["קול נשלח לענן", "Audio to cloud", "out"], connected: ["ענן: לא שולח", "Cloud: not sending", "standby"],
      connecting: ["מתחבר לענן", "Connecting", "standby"], off: ["לא נשלח קול", "No audio sent", "off"] },
  };
  const NAMES = { mic: ["מיקרופון", "Microphone"], camera: ["מצלמה", "Camera"], speaker: ["רמקול", "Speaker"],
    cloud_audio: ["קול לענן", "Audio to the cloud"] };
  const UNKNOWN = ["לא ידוע", "Unknown", "unknown"];
  const PREVIEW_S = 20;
  const NS = "http://www.w3.org/2000/svg";
  const ICONS = {        // 24x24 outlines, drawn with currentColor
    mic: ["M9 4a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0z", "M5 11a7 7 0 0 0 14 0", "M12 18v3", "M8 21h8"],
    camera: ["M3 8h4l2-3h6l2 3h4v11H3z", "M12 10a3.5 3.5 0 1 0 0.01 0"],
    speaker: ["M4 9h4l5-4v14l-5-4H4z", "M16 9a4 4 0 0 1 0 6", "M18.5 6.5a7.5 7.5 0 0 1 0 11"],
    cloud_audio: ["M7 18h11a4 4 0 0 0 0-8 6 6 0 0 0-11.5 1.5A3.5 3.5 0 0 0 7 18z", "M12 16v-6", "M9.5 12.5 12 10l2.5 2.5"],
    preview: ["M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z", "M12 9.5a2.5 2.5 0 1 0 0.01 0"],
  };
  function icon(k, slash) {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ps-icon"); s.setAttribute("aria-hidden", "true");
    for (const d of ICONS[k].concat(slash ? ["M3 21 21 3"] : [])) {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      s.append(p);
    }
    return s;
  }
  let st = null, linked = false, he = JL.lang === "he", view = null;
  // the same states, compact, for the header on every page (shell.js)
  window.privacyChips = (privacy, live) => ["mic", "camera", "cloud_audio"].map((k) => {
    const v = live && privacy && privacy[k] && privacy[k].state;
    const e = (STATES[k] && STATES[k][v]) || UNKNOWN, lang = JL.lang === "he" ? 0 : 1;
    const name = NAMES[k][lang];
    return el("a", { href: "#/home", class: `ps-chip ps-mini ps-${e[2]}`, title: `${name}: ${e[lang]}`, "aria-label": `${name}: ${e[lang]}`, "data-k": k },
      icon(k, e[2] === "off" || e[2] === "none"), el("span", { class: "ps-mini-t" }, e[lang]));
  });
  const pv = { open: false, left: 0, poll: null, tick: null, msg: "", last: "" };

  function entry(k) { const v = linked && st && st[k] && st[k].state; return (STATES[k] && STATES[k][v]) || UNKNOWN; }
  function label(k) { return entry(k)[he ? 0 : 1]; }
  async function refresh() {
    try {
      const d = await api("/api/runtime");
      st = d.privacy || null; linked = !!(d.live && st);
      he = JL.lang === "he";                                   // the dashboard locale (i18n.js), not the Core's
    } catch (e) { st = null; linked = false; }
    draw();
  }
  async function toggleMic() {
    const s = linked && st.mic && st.mic.state;
    if (!s || s === "unavailable") return;
    try { await api("/api/runtime", { method: "POST", body: JSON.stringify({ mic_muted: s !== "muted" }) }); } catch (e) { pv.msg = e.message; }
    refresh();
  }
  async function toggleCamera() {
    const s = linked && st.camera && st.camera.state;
    if (!s || s === "unavailable") return;
    const on = s === "disabled";
    if (on && !confirm(he ? "להדליק את המצלמה?" : "Turn the camera on?")) return;     // the dashboard click is the approval
    try { await api("/api/features/camera", { method: "POST", body: JSON.stringify({ enabled: on }) }); } catch (e) { pv.msg = e.message; }
    setTimeout(refresh, 600);          // the Core picks the switch up within its next health check
  }
  function closePreview(send) {
    clearInterval(pv.poll); clearInterval(pv.tick); pv.poll = pv.tick = null;
    if (pv.open && send) api("/api/camera/preview", { method: "POST", body: JSON.stringify({ open: false }) }).catch(() => {});
    pv.open = false;
    const img = document.getElementById("psImg");
    if (img) img.removeAttribute("src");      // the picture is gone with the window
    draw();
  }
  function showFrame(r) {
    const img = document.getElementById("psImg");
    if (!img) return;
    if (r.frame && typeof r.frame.data === "string" && /^image\/(jpeg|png)$/.test(r.frame.mime)) img.src = `data:${r.frame.mime};base64,${r.frame.data}`;
    const cap = document.getElementById("psLast");
    pv.last = r.last_seen && typeof r.last_seen.text === "string" ? r.last_seen.text : "";
    if (cap) cap.textContent = pv.last || (he ? "עוד לא תיאר תמונה." : "Nothing described yet.");
  }
  async function openPreview() {
    if (pv.open) { closePreview(true); return; }
    pv.msg = "";
    let r;
    try { r = await api("/api/camera/preview", { method: "POST", body: JSON.stringify({ start: true }) }); }
    catch (e) { pv.msg = e.message; draw(); return; }
    if (!r.open) { pv.msg = r.reason || "closed"; draw(); return; }
    pv.open = true; pv.left = PREVIEW_S;
    draw(); showFrame(r);
    pv.poll = setInterval(async () => {
      if (!document.getElementById("psCard")) { closePreview(true); return; }   // left the Home page
      try { const x = await api("/api/camera/preview"); if (!x.open) { pv.msg = x.reason || ""; closePreview(false); } else showFrame(x); }
      catch (e) { pv.msg = e.message; closePreview(false); }
    }, 700);
    pv.tick = setInterval(() => { pv.left -= 1; if (pv.left <= 0) closePreview(true); else draw(); }, 1000);
  }

  function chip(k) {
    const e = entry(k), tone = e[2];
    const click = k === "mic" ? toggleMic : k === "camera" ? toggleCamera : null;
    const name = NAMES[k][he ? 0 : 1];
    return el(click ? "button" : "span", { type: click ? "button" : null, class: `ps-chip ps-${tone}`, onclick: click,
      title: `${name}: ${label(k)}` + (click ? (he ? " · לחיצה לשינוי" : " · click to change") : ""), "data-k": k,
      "aria-label": `${name}: ${label(k)}` }, icon(k, tone === "off" || tone === "none"), el("span", { dir: "auto" }, label(k)));
  }
  function draw() {
    const card = document.getElementById("psCard");
    if (!card) return;
    const key = JSON.stringify([linked && st ? { ...st, ts: 0 } : null, pv.open, pv.left, pv.msg]);
    if (card.dataset.key === key && card.firstChild) return;    // unchanged: keep the buttons (no lost clicks)
    card.dataset.key = key;
    const canPreview = pv.open || (linked && st.camera && st.camera.preview_available === true);
    const eye = el("button", { type: "button", class: "ps-chip " + (pv.open ? "ps-capture" : canPreview ? "ps-local" : "ps-none"),
      disabled: canPreview ? null : true, onclick: openPreview, "data-k": "preview",
      title: canPreview ? (he ? "התמונה נשארת במחשב ולא נשמרת" : "The picture stays on this computer and is never saved")
        : (he ? "המצלמה כבויה או לא מחוברת" : "The camera is off or not connected") },
      icon("preview", false), el("span", {}, pv.open ? (he ? "סגור תצוגה" : "Close preview") : (he ? "מה הוא רואה?" : "What does he see?")));
    const row = el("div", { class: "ps-row", dir: he ? "rtl" : "ltr" }, ["mic", "camera", "speaker", "cloud_audio"].map(chip), eye);
    const note = el("div", { class: "muted small" }, !linked ? (he ? "אין עדכון מג'ארביס: המצב לא ידוע." : "No update from JARVES: state unknown.")
      : (he ? "המצב האמיתי של החיישנים, מתעדכן כל 2 שניות." : "The real sensor state, updated every 2 seconds."), pv.msg ? " · " + pv.msg : "");
    // wake word: the last thing the local detector noticed (privacy_state.py "wake", shown for 2 minutes)
    const WAKE = { no_match: ["שמעתי דיבור, אבל לא את מילת ההפעלה: אמור \"Hey Jarvis\"", "Heard speech, no wake match: say \"Hey Jarvis\""],
      too_quiet: ["המיקרופון שקט מדי למילת ההפעלה: התקרב, הגבר את עוצמת הקלט או הרץ jarvis wake test", "Mic too quiet for the wake word: move closer, raise the input volume, or run jarvis wake test"],
      near_miss: ["כמעט: אמור \"Hey Jarvis\" קצת יותר ברור", "Close: say \"Hey Jarvis\" a bit clearer"],
      woke: ["מילת ההפעלה נשמעה", "Wake word heard"] };
    const wk = linked && st.wake && st.wake.note && WAKE[st.wake.note];
    const wakeLine = wk ? el("div", { class: "small ps-wake", id: "psWake", dir: "auto", "data-note": st.wake.note }, wk[he ? 0 : 1]) : null;
    const box = pv.open ? el("div", { class: "ps-preview" },
      el("div", { class: "ps-head", dir: he ? "rtl" : "ltr" }, el("span", { class: "ps-rec" }),
        el("b", {}, he ? "תצוגת מצלמה · נשאר במחשב" : "Camera preview · stays on this computer"), el("span", { class: "spacer" }),
        el("span", { class: "muted small" }, he ? `נסגר בעוד ${pv.left} שנ׳` : `Closes in ${pv.left} s`)),
      el("img", { id: "psImg", alt: he ? "תמונת המצלמה" : "Camera picture" }),
      el("div", { class: "muted small", dir: "auto" }, he ? "מה הוא תיאר לאחרונה:" : "What he last described:"),
      el("div", { id: "psLast", dir: "auto" }, pv.last || (he ? "עוד לא תיאר תמונה." : "Nothing described yet."))) : null;
    const keep = document.getElementById("psImg");
    const src = keep && keep.getAttribute("src");
    card.replaceChildren(...[el("h2", {}, he ? "מה פועל עכשיו" : "What is on now"), row, wakeLine, note, box].filter(Boolean));
    if (box && src) document.getElementById("psImg").src = src;
  }
  window.privacyStripCard = function () {
    clearInterval(view);
    const card = el("div", { class: "card", id: "psCard" });
    queueMicrotask(() => { draw(); refresh(); });
    view = setInterval(() => { if (document.getElementById("psCard")) refresh(); else { clearInterval(view); closePreview(true); } }, 2000);
    return card;
  };
})();
