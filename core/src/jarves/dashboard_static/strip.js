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
  let st = null, linked = false, view = null;
  // the same states, compact, for the header on every page (shell.js)
  // The mic chip is a button that opens the mic mode menu (below); the others link to Home.
  let hdr = { privacy: null, live: false };
  window.privacyChips = (privacy, live) => {
    hdr = { privacy, live };
    if (mm.menu) mmFill();                         // an open menu follows the live state
    return ["mic", "camera", "cloud_audio"].map((k) => {
      const v = live && privacy && privacy[k] && privacy[k].state;
      const e = (STATES[k] && STATES[k][v]) || UNKNOWN, t = tx(e[0], e[1]);
      const name = tx(NAMES[k][0], NAMES[k][1]);
      const kids = [icon(k, e[2] === "off" || e[2] === "none"), el("span", { class: "ps-mini-t" }, t)];
      const common = { class: `ps-chip ps-mini ps-${e[2]}`, "data-k": k };
      if (k !== "mic") return el("a", { href: "#/home", ...common, title: `${name}: ${t}`, "aria-label": `${name}: ${t}` }, ...kids);
      const hint = `${name}: ${t}. ${tx("לחצו לשינוי מצב", "Click to change the mode")}`;
      return el("button", { type: "button", ...common, id: "micPill", title: hint, "aria-label": hint, "aria-haspopup": "menu",
        "aria-expanded": mm.menu ? "true" : "false", onclick: mmToggle, onkeydown: mmPillKey }, ...kids);
    });
  };
  // ---- mic mode menu: Muted / Wake word only / Live, applied at once through POST /api/runtime (same as the tray, `jarvis mic`, `jarvis talk`)
  const MODES = [
    { id: "muted", t: ["מושתק", "Muted"], d: ["המיקרופון כבוי בתוכנה", "The microphone is off (software mute)"], body: { mic_muted: true } },
    { id: "wake_only", t: ["רק מילת הפעלה", "Wake word only"], d: ["רק \"Hey Jarvis\" נשמע, במכשיר הזה", "Only \"Hey Jarvis\" is heard, on this device"], body: { mic_muted: false, talk: "stop" } },
    { id: "live", t: ["חי", "Live"], d: ["שיחה פתוחה, בלי מילת הפעלה", "A conversation is open, no wake word needed"], body: { mic_muted: false, talk: "start" } },
  ];
  const mm = { menu: null, busy: false, msg: "", err: false };
  function modeNow() {
    const s = hdr.live && hdr.privacy && hdr.privacy.mic && hdr.privacy.mic.state;
    if (s === "muted") return "muted";
    if (s === "listening" || s === "gated") return "live";
    if (s === "wake_only" || s === "idle") return "wake_only";
    return "";
  }
  function mmFill() {
    const m = mm.menu; if (!m) return;
    const now = modeNow(), focusId = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.mode;
    const items = MODES.map((x) => el("button", { type: "button", role: "menuitemradio", class: "mm-item", "data-mode": x.id,
      "aria-checked": now === x.id ? "true" : "false", disabled: !hdr.live ? true : null, onclick: () => mmApply(x) },
    el("span", { class: "mm-mark", "aria-hidden": "true" }, now === x.id ? "✓" : ""),
    el("span", { class: "mm-txt" }, el("b", {}, tx(x.t[0], x.t[1])), el("small", {}, tx(x.d[0], x.d[1])))));
    const note = !hdr.live ? tx("JARVES עצור: הפעילו אותו כדי לשנות את מצב המיקרופון.", "JARVES is stopped: start it to change the microphone mode.") : mm.msg;
    m.replaceChildren(...items, el("div", { class: "mm-msg" + (mm.err ? " err" : ""), role: mm.err ? "alert" : "status", "aria-live": "polite" }, note));
    if (focusId) { const f = m.querySelector(`[data-mode="${focusId}"]`); if (f && !f.disabled) f.focus(); }
  }
  async function mmApply(x) {
    if (mm.busy || !hdr.live) return;
    mm.busy = true; mm.err = false; mm.msg = tx("מחליף…", "Switching…"); mmFill();
    try {
      const r = await api("/api/runtime", { method: "POST", body: JSON.stringify(x.body) });
      const talk = r && r.applied && r.applied.talk;
      if (x.id === "live" && talk && talk.ok === false) { mm.err = true; mm.msg = talk.message || tx("לא ניתן לפתוח שיחה", "Could not open a conversation"); }
      else mm.msg = tx("המיקרופון: ", "Microphone: ") + tx(x.t[0], x.t[1]);
    } catch (e) { mm.err = true; mm.msg = tx("לא שונה: ", "Not changed: ") + e.message; }
    mm.busy = false;
    if (typeof window.shellPoll === "function") await window.shellPoll();
    mmFill();
  }
  function mmPos() {
    const b = document.getElementById("micPill"), m = mm.menu; if (!b || !m) return;
    const r = b.getBoundingClientRect(), w = m.offsetWidth, rtl = document.documentElement.dir === "rtl";
    let left = rtl ? r.right - w : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    m.style.left = left + "px"; m.style.top = Math.max(8, Math.min(r.bottom + 6, window.innerHeight - 8 - m.offsetHeight)) + "px";
  }
  function mmClose(refocus) {
    if (!mm.menu) return;
    mm.menu.remove(); mm.menu = null; mm.msg = ""; mm.err = false;
    document.removeEventListener("mousedown", mmOutside, true); window.removeEventListener("resize", mmPos);
    const b = document.getElementById("micPill");
    if (b) { b.setAttribute("aria-expanded", "false"); if (refocus) b.focus(); }
  }
  function mmOutside(ev) { if (mm.menu && !mm.menu.contains(ev.target) && !ev.target.closest("#micPill")) mmClose(false); }
  function mmToggle() {
    if (mm.menu) return mmClose(true);
    mm.menu = el("div", { class: "mm-menu", id: "micMenu", role: "menu", "aria-label": tx("מצב מיקרופון", "Microphone mode"), onkeydown: mmKey });
    document.body.append(mm.menu);
    mmFill(); mmPos();
    document.getElementById("micPill").setAttribute("aria-expanded", "true");
    document.addEventListener("mousedown", mmOutside, true); window.addEventListener("resize", mmPos);
    const f = mm.menu.querySelector('[aria-checked="true"]:not([disabled])') || mm.menu.querySelector(".mm-item:not([disabled])");
    if (f) f.focus();
  }
  function mmPillKey(ev) { if (ev.key === "ArrowDown" && !mm.menu) { ev.preventDefault(); mmToggle(); } }
  function mmKey(ev) {
    const items = [...mm.menu.querySelectorAll(".mm-item:not([disabled])")], i = items.indexOf(document.activeElement);
    if (ev.key === "Escape") { ev.preventDefault(); mmClose(true); }
    else if (ev.key === "Tab") mmClose(false);
    else if (items.length && ["ArrowDown", "ArrowUp", "Home", "End"].includes(ev.key)) {
      ev.preventDefault();
      const n = ev.key === "Home" ? 0 : ev.key === "End" ? items.length - 1 : (i + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[n].focus();
    }
  }
  const pv = { open: false, left: 0, poll: null, tick: null, msg: "", last: "" };

  function entry(k) { const v = linked && st && st[k] && st[k].state; return (STATES[k] && STATES[k][v]) || UNKNOWN; }
  function label(k) { const e = entry(k); return tx(e[0], e[1]); }
  async function refresh() {
    try {
      const d = await api("/api/runtime");
      st = d.privacy || null; linked = !!(d.live && st);
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
    if (on && !confirm(tx("להדליק את המצלמה?", "Turn the camera on?"))) return;     // the dashboard click is the approval
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
    if (cap) cap.textContent = pv.last || (tx("עוד לא תיאר תמונה.", "Nothing described yet."));
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
    const name = tx(NAMES[k][0], NAMES[k][1]);
    return el(click ? "button" : "span", { type: click ? "button" : null, class: `ps-chip ps-${tone}`, onclick: click,
      title: `${name}: ${label(k)}` + (click ? (tx(" · לחיצה לשינוי", " · click to change")) : ""), "data-k": k,
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
      title: canPreview ? (tx("התמונה נשארת במחשב ולא נשמרת", "The picture stays on this computer and is never saved"))
        : (tx("המצלמה כבויה או לא מחוברת", "The camera is off or not connected")) },
      icon("preview", false), el("span", {}, pv.open ? (tx("סגור תצוגה", "Close preview")) : (tx("מה הוא רואה?", "What does he see?"))));
    const row = el("div", { class: "ps-row", dir: JL.dir() }, ["mic", "camera", "speaker", "cloud_audio"].map(chip), eye);
    const note = el("div", { class: "muted small" }, !linked ? (tx("אין עדכון מג'ארביס: המצב לא ידוע.", "No update from JARVES: state unknown."))
      : (tx("המצב האמיתי של החיישנים, מתעדכן כל 2 שניות.", "The real sensor state, updated every 2 seconds.")), pv.msg ? " · " + pv.msg : "");
    // wake word: the last thing the local detector noticed (privacy_state.py "wake", shown for 2 minutes)
    const WAKE = { no_match: ["שמעתי דיבור, אבל לא את מילת ההפעלה: אמור \"Hey Jarvis\"", "Heard speech, no wake match: say \"Hey Jarvis\""],
      too_quiet: ["המיקרופון שקט מדי למילת ההפעלה: התקרב, הגבר את עוצמת הקלט או הרץ jarvis wake test", "Mic too quiet for the wake word: move closer, raise the input volume, or run jarvis wake test"],
      near_miss: ["כמעט: אמור \"Hey Jarvis\" קצת יותר ברור", "Close: say \"Hey Jarvis\" a bit clearer"],
      woke: ["מילת ההפעלה נשמעה", "Wake word heard"] };
    const wk = linked && st.wake && st.wake.note && WAKE[st.wake.note];
    const wakeLine = wk ? el("div", { class: "small ps-wake", id: "psWake", dir: "auto", "data-note": st.wake.note }, tx(wk[0], wk[1])) : null;
    const box = pv.open ? el("div", { class: "ps-preview" },
      el("div", { class: "ps-head", dir: JL.dir() }, el("span", { class: "ps-rec" }),
        el("b", {}, tx("תצוגת מצלמה · נשאר במחשב", "Camera preview · stays on this computer")), el("span", { class: "spacer" }),
        el("span", { class: "muted small" }, tx(`נסגר בעוד ${pv.left} שנ׳`, `Closes in ${pv.left} s`))),
      el("img", { id: "psImg", alt: tx("תמונת המצלמה", "Camera picture") }),
      el("div", { class: "muted small", dir: "auto" }, tx("מה הוא תיאר לאחרונה:", "What he last described:")),
      el("div", { id: "psLast", dir: "auto" }, pv.last || (tx("עוד לא תיאר תמונה.", "Nothing described yet.")))) : null;
    const keep = document.getElementById("psImg");
    const src = keep && keep.getAttribute("src");
    card.replaceChildren(...[el("h2", {}, tx("מה פועל עכשיו", "What is on now")), row, wakeLine, note, box].filter(Boolean));
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
