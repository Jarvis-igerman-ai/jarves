// JARVES phone: Drive tab (V6, issue #218). Read-mostly: drive mode state and the "when you stop" list (tool names and
// a count, never text), the pre-drive briefing (the phone only ANSWERS "is anyone in the car?"), and a typed trip plan with
// Maps / Waze links. The phone cannot switch drive mode, approve, call or message anything here, and no location is read unless the owner opts in (phone_location, #339).
// Links are plain anchors the owner taps; this file never opens one. Text via textContent only; the token stays in app.js.
"use strict";
(function () {
  const T = {
    en: { intro: "Driving view. Big buttons, nothing starts by itself.", off: "The Drive screen is switched off on the computer.",
      mode: "Drive mode", on: "On", offm: "Off", readonly: "Shown here only. Switch it on the computer: jarvis drive on / off.",
      waiting: "Waiting for when you stop", none: "Nothing is waiting.", nolist: "Only the tool is shown, never the text.",
      brief: "Pre-drive briefing", ask: "Is anyone in the car with you?", alone: "I am alone", present: "Someone is with me",
      briefoff: "The pre-drive briefing is switched off on the computer.", held: "Your computer is set to keep passengers in mind, so the short version was used.",
      setting_present: "The computer is set to always assume a passenger.", empty: "Nothing to say right now.",
      trip: "Plan a trip", from: "From", to: "To", plan: "Plan trip", places: "Type both places. Your location is used only if you choose to share it below.",
      maps: "Open in Google Maps", waze: "Open in Waze", tap: "Opens in your Maps app only when you tap.",
      tripoff: "Trip planning is switched off on the computer.", failed: "Could not do that", loading: "Loading…", working: "Working…",
      mode_alone: "Full briefing", mode_present: "Short briefing (a passenger can hear)",
      acts: "Quick actions", actoff: "Drive Actions are switched off on the computer.", acthint: "These make a link. Nothing is dialed, sent or played until you tap it.",
      call: "Phone number", callgo: "Prepare call", navi: "Destination", navgo: "Prepare navigation", mus: "Song, artist or playlist", musgo: "Prepare music",
      tel: "Call", play: "Open in YouTube Music", tapact: "Opens in your phone's own app only when you tap.",
      loc: "My location (optional)", locoff: "Phone location is switched off on the computer (jarvis features on phone_location).",
      locdrive: "Turn drive mode on at the computer first. Location is only used while driving.",
      lochint: "Used only for this trip while drive mode is on. Rounded to about 100 m, kept in memory only, never saved.",
      locshare: "Share my location", locstop: "Stop sharing", locon: "Sharing. Stops when drive mode ends or you leave this tab.",
      where: "Where am I?", fromhere: "Plan from here", locdenied: "Location was not allowed on the phone, so nothing is shared.",
      locfail: "No position available, so nothing is shared.", locnone: "Phone has no location support here." },
    he: { intro: "תצוגת נהיגה. כפתורים גדולים, שום דבר לא מתחיל מעצמו.", off: "מסך הנהיגה כבוי במחשב.",
      mode: "מצב נהיגה", on: "דלוק", offm: "כבוי", readonly: "מוצג כאן בלבד. מדליקים ומכבים במחשב: jarvis drive on / off.",
      waiting: "ממתין לשתעצרו", none: "שום דבר לא ממתין.", nolist: "מוצג רק הכלי, אף פעם לא הטקסט.",
      brief: "תדריך לפני נסיעה", ask: "יש מישהו ברכב איתכם?", alone: "אני לבד", present: "יש מישהו איתי",
      briefoff: "התדריך לפני נסיעה כבוי במחשב.", held: "המחשב מוגדר להתחשב בנוסעים, ולכן נעשה שימוש בגרסה הקצרה.",
      setting_present: "המחשב מוגדר להניח תמיד שיש נוסע.", empty: "אין מה לומר כרגע.",
      trip: "תכנון נסיעה", from: "מאיפה", to: "לאן", plan: "תכננו נסיעה", places: "מקלידים את שני המקומות. המיקום שלכם משמש רק אם בחרתם לשתף אותו למטה.",
      maps: "פתיחה ב-Google Maps", waze: "פתיחה ב-Waze", tap: "נפתח באפליקציית המפות רק כשאתם לוחצים.",
      tripoff: "תכנון נסיעה כבוי במחשב.", failed: "לא ניתן לבצע", loading: "טוען…", working: "עובד…",
      mode_alone: "תדריך מלא", mode_present: "תדריך קצר (נוסע יכול לשמוע)",
      acts: "פעולות מהירות", actoff: "פעולות נהיגה כבויות במחשב.", acthint: "אלה יוצרים קישור. שום דבר לא מחויג, נשלח או מתנגן עד שתלחצו עליו.",
      call: "מספר טלפון", callgo: "הכינו שיחה", navi: "יעד", navgo: "הכינו ניווט", mus: "שיר, אמן או רשימה", musgo: "הכינו מוזיקה",
      tel: "התקשרו", play: "פתיחה ב-YouTube Music", tapact: "נפתח באפליקציה של הטלפון רק כשאתם לוחצים.",
      loc: "המיקום שלי (לא חובה)", locoff: "מיקום הטלפון כבוי במחשב (jarvis features on phone_location).",
      locdrive: "קודם מדליקים מצב נהיגה במחשב. המיקום משמש רק בזמן נהיגה.",
      lochint: "משמש רק לנסיעה הזו כשמצב נהיגה דלוק. מעוגל לכ-100 מטר, נשמר בזיכרון בלבד, אף פעם לא נשמר.",
      locshare: "שתפו את המיקום שלי", locstop: "הפסיקו לשתף", locon: "משתף. נפסק כשמצב נהיגה נגמר או כשעוזבים את הלשונית.",
      where: "איפה אני?", fromhere: "תכננו מכאן", locdenied: "המיקום לא אושר בטלפון, ולכן שום דבר לא משותף.",
      locfail: "אין מיקום זמין, ולכן שום דבר לא משותף.", locnone: "אין תמיכה במיקום בטלפון הזה." },
  };
  const LINKS = ["https://www.google.com/maps/dir/?", "https://waze.com/ul?"];
  const ACT_LINKS = ["tel:+", "https://www.google.com/maps/dir/?", "https://waze.com/ul?", "https://music.youtube.com/search?q="];
  const ACTS = [["call", "call", "callgo"], ["navigate", "navi", "navgo"], ["music", "mus", "musgo"]];
  let act = {}, actRes = null, watch = null, sharing = false, lastSend = 0, place = "";
  let root = null, cx = null, busy = false, state = null, brief = null, trip = null, note = "", from = "", to = "";
  const t = (k) => ((T[cx.lang()] || T.en)[k]) || T.en[k] || k;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const safe = (u) => typeof u === "string" && LINKS.some((p) => u.startsWith(p));

  async function load() {
    try { state = await cx.api("/api/m/drive"); note = ""; } catch (e) { note = e.message; state = null; }
    if (window.JarvesHandsFree && state) window.JarvesHandsFree.update(state);
    paint();
  }
  async function getBrief(answer) {
    if (busy) return; busy = true; note = t("working"); paint();
    try { brief = await cx.api("/api/m/drive/briefing" + (answer ? "?passenger=" + answer : "")); note = ""; }
    catch (e) { note = t("failed") + ": " + e.message; }
    busy = false; paint();
  }
  async function plan() {
    if (busy) return; busy = true; note = t("working"); paint();
    try { trip = await cx.post("/api/m/drive/journey", { origin: from, destination: to }); note = ""; }
    catch (e) { trip = null; note = t("failed") + ": " + e.message; }
    busy = false; paint();
  }
  async function doAct(kind) {
    if (busy) return; busy = true; note = t("working"); actRes = null; paint();
    try { actRes = { kind, ...(await cx.post("/api/m/drive/action", { kind, text: act[kind] || "" })) }; note = ""; }
    catch (e) { note = t("failed") + ": " + e.message; }
    busy = false; paint();
  }
  // Location (#339): the page asks, the phone OS asks the owner. Nothing runs unless the owner taps "Share"; every send is a rounded-by-the-
  // server fix, a failure or a denial means nothing is shared (fails closed).
  async function sendFix(p) {
    if (!sharing || Date.now() - lastSend < 15000) return; lastSend = Date.now();
    try { await cx.post("/api/m/drive/location", { lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy, ts: p.timestamp }); }
    catch (e) { stopShare(true); note = t("locfail"); paint(); }
  }
  function stopShare(quiet) {
    if (watch != null && navigator.geolocation) navigator.geolocation.clearWatch(watch);
    const was = sharing; watch = null; sharing = false; place = "";
    if (was) cx.post("/api/m/drive/location", { clear: true }).catch(() => {});
    if (!quiet) paint();
  }
  function startShare() {
    if (!navigator.geolocation) { note = t("locnone"); paint(); return; }
    sharing = true; lastSend = 0; note = ""; paint();
    watch = navigator.geolocation.watchPosition(sendFix, (e) => { stopShare(true); note = e && e.code === 1 ? t("locdenied") : t("locfail"); paint(); },
      { enableHighAccuracy: false, maximumAge: 10000, timeout: 20000 });
  }
  async function whereAmI() {
    if (busy) return; busy = true; note = t("working"); paint();
    try { const r = await cx.post("/api/m/drive/where", {}); place = r.place || ""; note = ""; }
    catch (e) { place = ""; note = t("failed") + ": " + e.message; }
    busy = false; paint();
  }
  async function planFromHere() {
    if (busy) return; busy = true; note = t("working"); paint();
    try { trip = await cx.post("/api/m/drive/journey", { origin: "here", destination: to }); note = ""; }
    catch (e) { trip = null; note = t("failed") + ": " + e.message; }
    busy = false; paint();
  }
  function locCard() {
    const c = card(t("loc"));
    if (!state.location_on) { c.appendChild(el("p", "muted", t("locoff"))); return; }
    if (!state.drive_mode) { c.appendChild(el("p", "muted", t("locdrive"))); if (sharing) stopShare(true); return; }
    c.appendChild(el("p", "muted", t("lochint")));
    if (!sharing) { c.appendChild(btn(t("locshare"), startShare, true)); return; }
    c.appendChild(el("p", "ok", t("locon")));
    c.appendChild(btn(t("where"), whereAmI, true));
    if (place) { const p = el("p", "", place); p.setAttribute("dir", "auto"); p.setAttribute("role", "status"); c.appendChild(p); }
    if (state.journey_on && to) c.appendChild(btn(t("fromhere"), planFromHere, false));
    c.appendChild(btn(t("locstop"), () => stopShare(false), false));
  }
  function btn(label, fn, primary) { const b = el("button", "big" + (primary ? " primary" : ""), label); b.type = "button"; b.addEventListener("click", fn); return b; }
  function card(title) { const c = el("section", "card drive-card"); c.appendChild(el("h2", "", title)); root.appendChild(c); return c; }
  function modeCard() {
    const c = card(t("mode")); const on = !!state.drive_mode;
    c.appendChild(el("p", "drive-state " + (on ? "ok" : "muted"), on ? t("on") : t("offm")));
    c.appendChild(el("p", "muted", t("readonly")));
    c.appendChild(el("h2", "", t("waiting") + ": " + state.waiting));
    if (!state.waiting) c.appendChild(el("p", "muted", t("none")));
    else {
      const ul = el("ul", "list"); (state.queue || []).forEach((q) => ul.appendChild(el("li", "", q.tool)));
      c.appendChild(ul); c.appendChild(el("p", "muted", t("nolist")));
    }
  }
  function briefCard() {
    const c = card(t("brief"));
    if (!state.briefing_on) { c.appendChild(el("p", "muted", t("briefoff"))); return; }
    const set = state.passenger_setting;
    c.appendChild(el("p", set === "present" ? "muted" : "", set === "present" ? t("setting_present") : t("ask")));
    const row = el("div", "col");
    if (set !== "present") row.appendChild(btn(t("alone"), () => getBrief("alone"), true));
    row.appendChild(btn(t("present"), () => getBrief("present"), set === "present"));
    c.appendChild(row);
    if (brief && !brief.ask) {
      c.appendChild(el("p", "muted", brief.mode === "alone" ? t("mode_alone") : t("mode_present")));
      if (brief.held_by_setting) c.appendChild(el("p", "muted", t("held")));
      const ul = el("ul", "list");
      (brief.sections || []).forEach((s) => { const li = el("li", "", s.text); li.setAttribute("dir", "auto"); ul.appendChild(li); });
      if (!(brief.sections || []).length) ul.appendChild(el("li", "", brief.text || t("empty")));
      c.appendChild(ul);
    }
  }
  function tripCard() {
    const c = card(t("trip"));
    if (!state.journey_on) { c.appendChild(el("p", "muted", t("tripoff"))); return; }
    c.appendChild(el("p", "muted", t("places")));
    [["from", from, (v) => { from = v; }], ["to", to, (v) => { to = v; }]].forEach(([k, val, set]) => {
      const lab = el("label"); lab.appendChild(el("span", "", t(k)));
      const inp = el("input"); inp.maxLength = 120; inp.value = val; inp.setAttribute("dir", "auto"); inp.setAttribute("autocomplete", "off");
      inp.setAttribute("aria-label", t(k)); inp.addEventListener("input", () => set(inp.value)); lab.appendChild(inp); c.appendChild(lab);
    });
    c.appendChild(btn(t("plan"), plan, true));
    if (trip) {
      const lines = (trip.lines && (trip.lines[cx.lang()] || trip.lines.en)) || [];
      const ul = el("ul", "list"); lines.forEach((l) => { const li = el("li", "", l); li.setAttribute("dir", "auto"); ul.appendChild(li); }); c.appendChild(ul);
      const row = el("div", "col");
      [["maps_url", "maps"], ["waze_url", "waze"]].forEach(([k, lab]) => {
        if (!safe(trip[k])) return;
        const a = el("a", "big btn", t(lab)); a.href = trip[k]; a.target = "_blank"; a.rel = "noopener noreferrer"; row.appendChild(a);
      });
      c.appendChild(row); c.appendChild(el("p", "muted", t("tap")));
    }
  }
  function actCard() {
    const c = card(t("acts"));
    if (!state.actions_on) { c.appendChild(el("p", "muted", t("actoff"))); return; }
    c.appendChild(el("p", "muted", t("acthint")));
    ACTS.forEach(([kind, lk, gk]) => {
      const lab = el("label"); lab.appendChild(el("span", "", t(lk)));
      const inp = el("input"); inp.maxLength = 120; inp.value = act[kind] || ""; inp.setAttribute("dir", "auto"); inp.setAttribute("autocomplete", "off");
      if (kind === "call") inp.setAttribute("inputmode", "tel");
      inp.setAttribute("aria-label", t(lk)); inp.addEventListener("input", () => { act[kind] = inp.value; }); lab.appendChild(inp); c.appendChild(lab);
      c.appendChild(btn(t(gk), () => doAct(kind), false));
      if (actRes && actRes.kind === kind) {
        if (!actRes.ok) { const p = el("p", "muted", (cx.lang() === "he" ? actRes.error_he : actRes.error) || t("failed")); p.setAttribute("role", "status"); c.appendChild(p); return; }
        const row = el("div", "col");
        (actRes.links || []).forEach((l) => {
          if (!(typeof l.url === "string" && ACT_LINKS.some((p) => l.url.startsWith(p)))) return;
          const a = el("a", "big btn", l.id === "tel" ? t("tel") : l.id === "maps" ? t("maps") : l.id === "waze" ? t("waze") : t("play"));
          a.href = l.url; a.rel = "noopener noreferrer"; if (l.id !== "tel") a.target = "_blank"; row.appendChild(a);
        });
        c.appendChild(row); c.appendChild(el("p", "muted", t("tapact")));
      }
    });
  }
  function paint() {
    if (!root) return;
    root.replaceChildren();
    root.appendChild(el("p", "muted", t("intro")));
    if (note) { const n = el("p", "muted", note); n.setAttribute("role", "status"); root.appendChild(n); }
    if (!state) return;
    if (!state.enabled) { root.appendChild(el("p", "", t("off"))); return; }
    modeCard(); briefCard(); tripCard(); locCard(); actCard();
  }
  const typing = () => document.activeElement && root.contains(document.activeElement) && document.activeElement.tagName === "INPUT";
  window.JarvesDrive = {
    mount(node, ctx) { root = node; cx = ctx; note = ""; brief = trip = actRes = null; act = {}; from = to = ""; place = ""; sharing = false; watch = null; root.replaceChildren(el("p", "muted", t("loading"))); load(); },
    refresh() { if (root && !busy && !typing()) load(); },
    unmount() { stopShare(true); root = null; },
    catalogs: T,
  };
})();
