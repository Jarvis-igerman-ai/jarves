// ---- Browser tab (V7 B4, issue #399): the browser pilot's live view on the phone, with the owner's takeover. A tab module like
// plans.js (window.JarvesBrowser.mount(root, {api, post, lang})); Mirrors liveViewCard() in dashboard_static/browser.js: one poll in flight, an epoch guard so a
// frame asked before a button press cannot undo it, "busy" keeps the view, the Continue button follows the SERVER state, give
// back = the assistant is PAUSED, a separate confirmed step lets it continue. The grant and what the owner types live only in
// this closure: never in the page, a URL, a log or storage. The picture is an <img> with a data: URL; text via textContent only.
"use strict";
(function () {
  const T = {
    en: { intro: "Watch the assistant on its local test page. Only you can take over; while you do, the assistant is locked out.",
      watch: "Watch", take: "Take over", give: "Give back", cont: "Let the assistant continue", stop: "Stop", send: "Send",
      type_label: "Type into the page", type_ph: "Text for the page", show: "Show what I type", key_enter: "Enter", key_tab: "Tab",
      key_enter_l: "Press Enter on the page", key_tab_l: "Press Tab on the page", up: "Scroll up", down: "Scroll down",
      picture: "Live picture of the test page", empty: "The picture of the page appears here.", tap_hint: "Tap the picture to click there.",
      no_pilot: "Nothing is running yet. Start the browser pilot on the computer, then press Watch.",
      st_off: "Not watching", st_AGENT: "The assistant is working", st_HUMAN: "You are in control", st_PAUSED: "The assistant is paused", st_STOPPED: "Stopped",
      ends: "Ends in {n} s", busy: "The page is busy, trying again.", sent: "Sent to the page.", starting: "Opening the view…",
      mine_ok: "You are in control. The assistant is locked out.",
      back_ok: "Control is back and the assistant is paused. It can read the page only after you let it continue.",
      ask_cont: "The assistant will be able to read the page as it is now (a logged-in account, for instance). Continue?",
      cont_ok: "The assistant continues. It never saw what you typed.", cont_not: "Not resumed: the session is now: ",
      stopped: "Stopped. The session is closed.", ended: "The view ended. Press Watch to see it again.", expired: "The view expired. Press Watch to see it again.",
      private: "What you type goes to the page only. It is not saved, not logged and never shown to the assistant.",
      tls: "This needs the secure (https) phone link.", off: "The browser view is off on the computer.", rate: "Too many requests. Wait a moment.",
      takeover_off: "Taking over is off on the computer: you can watch and stop.", cant_take: "The session cannot be taken over right now.",
      pilot_stopped: "The session was stopped.", pilot_cap: "The session reached its limit and was stopped.", test_only: "Only local test pages can be shown.",
      pilot_off: "The browser pilot is off on the computer.", frame_fail: "The page could not be captured; the view ended.", failed: "That did not work." },
    he: { intro: "צפו בסוכן עובד על דף הבדיקה המקומי שלו. רק אתם יכולים לקחת שליטה, ובזמן הזה הסוכן נעול בחוץ.",
      watch: "צפייה", take: "קחו שליטה", give: "החזירו שליטה", cont: "אפשרו לסוכן להמשיך", stop: "עצירה", send: "שליחה",
      type_label: "הקלדה בדף", type_ph: "טקסט לדף", show: "הצגת מה שמוקלד", key_enter: "Enter", key_tab: "Tab",
      key_enter_l: "לחיצה על מקש Enter בדף", key_tab_l: "לחיצה על מקש Tab בדף", up: "גלילה למעלה", down: "גלילה למטה",
      picture: "תמונה חיה של דף הבדיקה", empty: "תמונת הדף תופיע כאן.", tap_hint: "הקישו על התמונה כדי ללחוץ שם.",
      no_pilot: "עוד שום דבר לא רץ. הפעילו את טייס הדפדפן במחשב ואז לחצו על צפייה.",
      st_off: "לא בצפייה", st_AGENT: "הסוכן עובד", st_HUMAN: "השליטה אצלכם", st_PAUSED: "הסוכן מושהה", st_STOPPED: "נעצר",
      ends: "מסתיים בעוד {n} ש׳", busy: "הדף עסוק, מנסה שוב.", sent: "נשלח לדף.", starting: "פותח את התצוגה…",
      mine_ok: "השליטה אצלכם. הסוכן נעול בחוץ.",
      back_ok: "השליטה הוחזרה והסוכן מושהה. הוא יוכל לקרוא את הדף רק אחרי שתאפשרו לו להמשיך.",
      ask_cont: "הסוכן יוכל לקרוא את הדף כפי שהוא עכשיו (למשל חשבון מחובר). להמשיך?",
      cont_ok: "הסוכן ממשיך. הוא לא ראה את מה שהקלדתם.", cont_not: "לא הופעל. מצב הסשן עכשיו: ",
      stopped: "נעצר. הסשן נסגר.", ended: "הצפייה הסתיימה. לחצו על צפייה כדי לראות שוב.", expired: "תוקף הצפייה פג. לחצו על צפייה כדי לראות שוב.",
      private: "מה שאתם מקלידים מגיע לדף בלבד. הוא לא נשמר, לא נרשם ולא מוצג לסוכן.",
      tls: "צריך את הקישור המאובטח (https) לטלפון.", off: "תצוגת הדפדפן כבויה במחשב.", rate: "יותר מדי בקשות. המתינו רגע.",
      takeover_off: "לקיחת שליטה כבויה במחשב: אפשר לצפות ולעצור.", cant_take: "אי אפשר לקחת שליטה על הסשן כרגע.",
      pilot_stopped: "הסשן נעצר.", pilot_cap: "הסשן הגיע למגבלה שלו ונעצר.", test_only: "אפשר להציג רק דפי בדיקה מקומיים.",
      pilot_off: "טייס הדפדפן כבוי במחשב.", frame_fail: "לא הצלחנו לצלם את הדף; הצפייה הסתיימה.", failed: "הפעולה לא הצליחה." },
  };
  const ERR = { PHONE_BROWSER_NO_SESSION: "no_pilot", PHONE_BROWSER_NO_TLS: "tls", PHONE_BROWSER_OFF: "off", PHONE_BROWSER_RATE: "rate",
    LIVEVIEW_OFF: "off", LIVEVIEW_EXPIRED: "expired", LIVEVIEW_TOKEN: "ended", LIVEVIEW_TAKEOVER_OFF: "takeover_off", LIVEVIEW_TAKEOVER: "cant_take",
    LIVEVIEW_FRAME: "frame_fail", BROWSER_PILOT_STOPPED: "pilot_stopped", BROWSER_PILOT_CAP: "pilot_cap", BROWSER_PILOT_VIEW_TEST_SITE: "test_only",
    BROWSER_PILOT_OFF: "pilot_off", BROWSER_PILOT_BUSY: "busy", LIVEVIEW_BUSY: "busy" };
  let root = null, cx = null, ui = null;
  // the view's state: the grant and the last picture stay in this closure only (never the DOM text, a URL or storage)
  const st = { status: null, grant: "", timer: 0, inflight: false, epoch: 0, srv: "", src: "", left: null, wait: false, msg: "", acting: false, show: false };
  const t = (k) => ((T[cx ? cx.lang() : "en"] || T.en)[k]) || T.en[k] || k;
  const he = () => !!cx && cx.lang() === "he";
  const err = (e) => (ERR[e && e.code] ? t(ERR[e.code]) : (he() ? t("failed") : ((e && e.message) || t("failed"))));
  const mine = () => !!st.grant && st.srv === "HUMAN";
  const visible = () => !!root && root.isConnected && !root.closest("[hidden]") && !document.hidden;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const btn = (label, fn, cls) => { const b = el("button", cls || "", label); b.type = "button"; b.addEventListener("click", fn); return b; };

  function end(text) {
    clearInterval(st.timer); st.timer = 0; st.grant = ""; st.srv = ""; st.src = ""; st.left = null; st.wait = false; st.msg = text || "";
    if (ui) { ui.img.removeAttribute("src"); ui.box.value = ""; }
    sync();
  }
  async function poll() {
    if (!st.grant || st.inflight || !visible()) return;            // one request at a time; a hidden tab or page asks for nothing
    st.inflight = true;
    const g = st.grant, ep = st.epoch;                               // a frame asked BEFORE a button press must not undo it
    try {
      const r = await cx.post("/api/m/browser/frame", { grant: g });
      if (g !== st.grant) return;                                    // stopped or started again meanwhile
      st.src = "data:image/png;base64," + r.png_b64; st.left = r.expires_in_s; st.wait = false;
      if (ep === st.epoch) st.srv = r.state;                         // the buttons follow the SERVER state (also after a reload)
      if (ui) ui.img.src = st.src;
      sync();
    } catch (e) {
      if (g !== st.grant) return;
      if (!e.status || e.status === 429) { st.wait = e.status === 429; sync(); return; }    // busy or a Wi-Fi blip: keep the view
      end(err(e));
    } finally { st.inflight = false; }
  }
  async function watch() {
    if (st.acting) return;
    st.acting = true; st.msg = t("starting"); sync();
    try {
      const r = await cx.post("/api/m/browser/start", {});
      st.grant = r.grant; st.srv = r.state; st.left = r.expires_in_s; st.msg = ""; st.epoch++;
      clearInterval(st.timer); st.timer = setInterval(poll, Math.max(1000, r.interval_ms || 1000));
    } catch (e) { st.acting = false; end(err(e)); return; }
    st.acting = false; sync(); poll();
  }
  async function act(path, body, ok) {                               // takeover / give back / continue: the epoch brackets the call
    if (st.acting || !st.grant) return null;
    st.acting = true; sync();
    let r = null;
    try { st.epoch++; r = await cx.post(path, { grant: st.grant, ...body }); st.epoch++; if (r && r.state) st.srv = r.state; st.msg = ok ? t(ok) : ""; }
    catch (e) { st.msg = err(e); }
    st.acting = false; sync();
    return r;
  }
  const take = () => act("/api/m/browser/takeover", {}, "mine_ok");
  const give = () => act("/api/m/browser/return", {}, "back_ok");
  async function cont() {
    if (!window.confirm(t("ask_cont"))) return;
    const r = await act("/api/m/browser/return", { resume: true }, "");
    if (r) st.msg = r.state === "AGENT" ? t("cont_ok") : t("cont_not") + t("st_" + r.state);
    sync();
  }
  async function stop() {
    if (st.acting) return;
    st.acting = true; sync();
    try { await cx.post("/api/m/browser/stop", {}); st.acting = false; if (st.status) st.status.session = false; end(t("stopped")); }
    catch (e) { st.acting = false; st.msg = err(e); sync(); }
  }
  async function send(kind, data) {
    if (!mine()) return;
    try { await cx.post("/api/m/browser/input", { grant: st.grant, kind, data }); st.msg = t("sent"); }
    catch (e) { st.msg = err(e); }
    sync();
  }
  function typeIt() {
    const v = ui.box.value; ui.box.value = "";                       // cleared at once; the text is never kept or shown
    if (v) send("type", { text: v });
  }
  function tap(ev) {
    const img = ui.img;
    if (!mine() || !img.naturalWidth) return;
    const r = img.getBoundingClientRect();
    const x = Math.round((ev.clientX - r.left) * img.naturalWidth / r.width), y = Math.round((ev.clientY - r.top) * img.naturalHeight / r.height);
    send("click", { x: Math.max(0, Math.min(img.naturalWidth - 1, x)), y: Math.max(0, Math.min(img.naturalHeight - 1, y)) });
  }

  function build() {
    if (!root) return;
    const u = {};
    u.state = el("span", "pill bv-state"); u.left = el("span", "muted bv-left");
    const head = el("div", "bv-head"); head.append(u.state, u.left);
    u.img = el("img", "bv-img"); u.img.alt = t("picture"); u.img.draggable = false; u.img.decoding = "async";
    u.img.addEventListener("click", tap);
    u.empty = el("div", "bv-empty", t("empty"));
    u.frame = el("div", "bv-frame"); u.frame.append(u.img, u.empty);
    u.hint = el("p", "muted bv-hint", t("tap_hint"));
    u.msg = el("p", "bv-msg"); u.msg.setAttribute("role", "status"); u.msg.setAttribute("aria-live", "polite");
    // the owner's controls (only while the session is his)
    u.box = el("input", "bv-box"); u.box.type = "password"; u.box.id = "bvType"; u.box.maxLength = 500;
    u.box.autocomplete = "off"; u.box.spellcheck = false; u.box.setAttribute("autocapitalize", "off"); u.box.setAttribute("autocorrect", "off");
    u.box.setAttribute("enterkeyhint", "send"); u.box.placeholder = t("type_ph"); u.box.dir = "auto";
    u.box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); typeIt(); } });
    const lab = el("label", "bv-label", t("type_label")); lab.htmlFor = "bvType";
    u.send = btn(t("send"), typeIt, "primary bv-send");
    const row = el("div", "bv-type"); row.append(u.box, u.send);
    u.show = btn(t("show"), () => { st.show = !st.show; sync(); }, "bv-show");
    const key = (k, label) => { const b = btn(t("key_" + k), () => send("key", { key: label }), "bv-key"); b.setAttribute("aria-label", t("key_" + k + "_l")); b.dir = "ltr"; return b; };
    const keys = el("div", "bv-keys");
    keys.append(key("enter", "Enter"), key("tab", "Tab"), btn(t("up"), () => send("scroll", { dy: -400 }), "bv-key"), btn(t("down"), () => send("scroll", { dy: 400 }), "bv-key"));
    u.give = btn(t("give"), give, "bv-give");
    u.ctl = el("div", "bv-ctl"); u.ctl.append(lab, row, u.show, keys, u.give, el("p", "muted bv-private", t("private")));
    // the main actions
    u.watch = btn(t("watch"), watch, "primary"); u.take = btn(t("take"), take, "primary"); u.cont = btn(t("cont"), cont, "primary bv-cont");
    u.stop = btn(t("stop"), stop, "bv-stop");
    const acts = el("div", "bv-acts"); acts.append(u.watch, u.cont, u.take, u.stop);           // paused: Continue first, Take over again the quiet one
    u.noPilot = el("p", "muted bv-note", t("no_pilot")); u.noTake = el("p", "muted bv-note", t("takeover_off"));
    const card = el("div", "card bv-card"); card.append(head, u.frame, u.hint, u.msg, u.ctl, acts, u.noPilot, u.noTake);
    root.replaceChildren(el("p", "muted bv-intro", t("intro")), card);
    ui = u;
    if (st.src) u.img.src = st.src;
    sync();
  }
  function sync() {
    if (!ui) return;
    const watching = !!st.grant, s = st.status || {}, my = mine();
    ui.state.textContent = watching && st.srv ? t("st_" + st.srv) : t("st_off");
    ui.state.className = "pill bv-state" + (watching ? " on s-" + String(st.srv).toLowerCase() : "");
    ui.left.textContent = !watching ? "" : st.wait ? t("busy") : (st.left != null ? t("ends").replace("{n}", String(st.left)) : "");
    const pic = watching && !!st.src;
    ui.img.hidden = !pic; ui.empty.hidden = pic;
    ui.frame.classList.toggle("mine", my);
    ui.hint.hidden = !(my && pic);
    ui.msg.textContent = st.msg; ui.msg.hidden = !st.msg;
    ui.ctl.hidden = !my;
    ui.box.type = st.show ? "text" : "password";
    ui.show.setAttribute("aria-pressed", String(st.show));
    ui.watch.hidden = watching; ui.watch.disabled = st.acting;
    ui.take.hidden = !(watching && s.takeover && (st.srv === "AGENT" || st.srv === "PAUSED"));
    ui.cont.hidden = !(watching && st.srv === "PAUSED"); ui.cont.disabled = st.acting || st.srv !== "PAUSED";
    if (!ui.cont.hidden) ui.take.className = "";                    // paused: continuing is the main step, taking over again the quiet one
    else ui.take.className = "primary";
    ui.stop.hidden = !(watching || s.session); ui.stop.disabled = st.acting;
    [ui.take, ui.give, ui.send].forEach((b) => { b.disabled = st.acting; });
    ui.noPilot.hidden = watching || !st.status || !!s.session;
    ui.noTake.hidden = !(watching && st.status && !s.takeover);
  }
  window.JarvesBrowser = {
    mount(node, ctx) { root = node; cx = ctx; build(); if (st.grant) poll(); },
    status(s) { st.status = s || null; if (st.grant && s && (!s.session || !s.enabled)) end(t("ended")); else sync(); },   // from GET /api/m/browser
    unmount() { end(""); root = null; ui = null; },                  // un-paired: the grant is dropped at once
    catalogs: T,
  };
})();
