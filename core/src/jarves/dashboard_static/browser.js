// SW-V3 P1.2 Browser page: the owner's allow-list of sites JARVES may fully control (default empty), the rules and
// what is open right now. Uses the helpers of app.js (el, api, render, header, pill, route) and platformCell (m2.js).
"use strict";

// B2 (#397): read-only live view of the pilot's local test page. Frames are polled with POST (the token never goes in a URL);
// the image is set from a data URL built here, nothing from the page is parsed as HTML. No input is ever sent to the page.
function liveViewCard() {
  const img = el("img", { alt: tx("תצוגה חיה של דף הבדיקה", "Live view of the test page"), style: "max-width:100%;display:none;border:1px solid currentColor" });
  const msg = el("div", { class: "small", role: "status", "aria-live": "polite" });
  const clockEl = el("div", { class: "small muted" });               // the expiry line; never overwrites what a button said
  let epoch = 0;
  let token = "", timer = 0;
  let end = (text) => { clearInterval(timer); timer = 0; token = ""; start.disabled = false; msg.textContent = text || ""; };
  const poll = async () => {
    if (!token) return;
    if (!document.body.contains(img)) { end(""); return; }       // the owner left this page: stop polling (no hidden loop)
    if (document.hidden) return;                                   // a hidden tab asks for nothing
    try {
      const ep = epoch;                                            // a frame asked BEFORE a button press must not undo it
      const r = await api("/api/browser/live/frame", { method: "POST", body: JSON.stringify({ grant: token }) });
      img.src = "data:image/png;base64," + r.png_b64; img.style.display = "";
      if (ep === epoch && typeof setMine === "function" && (r.state === "HUMAN") !== mine.on) setMine(r.state === "HUMAN");
      clockEl.textContent = tx("פג בעוד ", "Expires in ") + r.expires_in_s + " s";
    } catch (e) { img.style.display = "none"; end(e.message); }
  };
  const start = el("button", { type: "button", class: "primary", onclick: async () => {
    start.disabled = true; msg.textContent = "";
    try {
      const r = await api("/api/browser/live/start", { method: "POST", body: "{}" });
      token = r.grant; takeBtn.disabled = false; if (r.state === "HUMAN") setMine(true); await poll();
      if (token) timer = setInterval(poll, Math.max(1000, r.interval_ms || 1000));
    } catch (e) { end(e.message); }
  } }, tx("תצוגה חיה", "Live view"));
  const stop = el("button", { type: "button", onclick: async () => {
    try { await api("/api/browser/live/stop", { method: "POST", body: "{}" }); img.style.display = "none"; end(tx("נעצר. הסשן נסגר.", "Stopped. The session is closed.")); }
    catch (e) { end(e.message); }
  } }, tx("עצירה", "Stop"));
  // B3 (#398): the OWNER can take over the same session. While it is yours the agent is locked out; what you type goes to the page and
  // nowhere else (not logged, not stored here, never shown to the assistant). Local test pages only.
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify({ grant: token, ...body }) });
  const mine = { on: false };
  const typeBox = el("input", { type: "password", autocomplete: "off", spellcheck: "false", "aria-label": tx("טקסט להקלדה בדף", "Text to type into the page"), disabled: true });
  const secret = el("input", { type: "checkbox", id: "live-secret", disabled: true, onchange: () => { typeBox.type = secret.checked ? "text" : "password"; } });
  const send = async (kind, data) => {
    if (!token || !mine.on) return;
    try { await post("/api/browser/live/input", { kind, data }); msg.textContent = tx("נשלח לדף.", "Sent to the page."); }
    catch (e) { msg.textContent = e.message; }
  };
  const typeBtn = el("button", { type: "button", disabled: true, onclick: async () => {
    const t = typeBox.value; typeBox.value = ""; if (t) await send("type", { text: t }); } }, tx("הקלדה בדף", "Type into the page"));
  const keyBtns = ["Enter", "Tab"].map((k) => el("button", { type: "button", disabled: true, "data-key": k, onclick: () => send("key", { key: k }) }, k));
  const driveBtns = [typeBtn, ...keyBtns];
  const setMine = (on) => { mine.on = on; takeBtn.disabled = on || !token; giveBtn.disabled = !on; typeBox.disabled = secret.disabled = !on; driveBtns.forEach((b) => { b.disabled = !on; });
    img.style.cursor = on ? "crosshair" : ""; };
  img.addEventListener("click", (ev) => {
    if (!mine.on || !img.naturalWidth) return;
    const r = img.getBoundingClientRect();
    send("click", { x: Math.round((ev.clientX - r.left) * img.naturalWidth / r.width), y: Math.round((ev.clientY - r.top) * img.naturalHeight / r.height) });
  });
  const takeBtn = el("button", { type: "button", disabled: true, onclick: async () => {
    try { epoch++; await post("/api/browser/live/takeover", {}); setMine(true); msg.textContent = tx("השליטה אצלכם. הסוכן נעול בחוץ.", "You are in control. The assistant is locked out."); }
    catch (e) { msg.textContent = e.message; } } }, tx("קחו שליטה", "Take over"));
  const giveBtn = el("button", { type: "button", disabled: true, onclick: async () => {
    try { epoch++; await post("/api/browser/live/return", {}); setMine(false); contBtn.disabled = false;
      msg.textContent = tx("השליטה הוחזרה והסוכן מושהה. הוא יוכל לקרוא את הדף רק אחרי שתאשרו.", "Control is back and the assistant is paused. It can read the page only after you let it continue."); }
    catch (e) { msg.textContent = e.message; } } }, tx("החזירו (הסוכן מושהה)", "Give back (assistant paused)"));
  const contBtn = el("button", { type: "button", disabled: true, onclick: async () => {
    if (!window.confirm(tx("הסוכן יוכל לקרוא את הדף כפי שהוא עכשיו (למשל חשבון מחובר). להמשיך?", "The assistant will be able to read the page as it is now (a logged-in account, for instance). Continue?"))) return;
    try { epoch++; await post("/api/browser/live/return", { resume: true }); contBtn.disabled = true; msg.textContent = tx("הסוכן ממשיך. הוא לא ראה מה שהקלדתם.", "The assistant continues. It never saw what you typed."); }
    catch (e) { msg.textContent = e.message; } } }, tx("אפשרו לסוכן להמשיך", "Let the assistant continue"));
  const baseEnd = end;
  end = (text) => { setMine(false); baseEnd(text); takeBtn.disabled = true; };
  return el("div", { class: "card", id: "live-view" }, el("h2", {}, tx("תצוגה חיה (בטא)", "Live view (beta)")),
    el("div", { class: "small muted" }, tx("רק דפי בדיקה מקומיים. הצפייה היא לקריאה בלבד; רק אתם יכולים לקחת שליטה, והסוכן נעול בחוץ בזמן זה.", "Local test pages only. Viewing is read only; only you can take over, and the assistant is locked out while you do.")),
    el("div", { class: "row" }, start, takeBtn, giveBtn, contBtn, stop), msg, clockEl, img,
    el("div", { class: "row" }, typeBox, el("label", { for: "live-secret", class: "small" }, secret, " ", tx("הצגת מה שמוקלד", "Show what I type")), typeBtn, ...keyBtns));
}

PAGES.browser = async function () {
  const d = await api("/api/browser");
  const f = d.feature;
  const msg = el("span", { class: "muted small", role: "status" });
  const change = async (action, site) => {
    try {
      await api("/api/browser/sites", { method: "POST", body: JSON.stringify({ action, site }) });
      route(true);
    } catch (e) { msg.textContent = tx("לא שונה: ", "Not changed: ") + e.message; }
  };
  const input = el("input", { type: "text", placeholder: "example.com", dir: "ltr", style: "min-width:240px",
    "aria-label": JL.t("Site to allow") });
  const add = el("button", { type: "button", class: "primary", onclick: () => {
    const v = input.value.trim();
    if (v) change("add", v);
  } }, "Allow full control");
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") add.click(); });
  const rows = d.sites.map((s) => el("tr", {},
    el("td", {}, el("code", {}, s), el("span", { class: "muted small" }, "  + subdomains")),
    el("td", {}, el("button", { type: "button", class: "ghost", onclick: () => change("remove", s) }, "Remove"))));
  const state = f.active ? pill("ok", "on") : !f.enabled ? pill("plain", "off") : pill("fail", "unavailable");
  const act = d.activity || {};
  render(header("Browser", "Web pages JARVES opens for you. Full control only on the sites below; every other site is read-only and opens only after you approve it on the screen."),
    el("div", { class: "card" },
      el("div", { class: "row" }, el("b", {}, "Switch: "), state,
        f.reason && !f.active ? el("span", { class: "small" }, f.reason) : null,
        el("a", { href: "#/features", class: "small" }, "Features page"), el("a", { href: "#/guide/browser", class: "small" }, "Guide")),
      el("div", { class: "small", style: "margin-top:6px" }, "Now: ", act.detail || "not running (no live Core status)"),
      el("ul", { class: "small" }, d.rules.map((r) => el("li", {}, r))),
      el("div", { class: "muted small" }, tx("אף פעם לא גולש (חשבונות AI ועמודי מכסה): ", "Never browsed (AI account / quota pages): ") + d.blocked_hosts.join(", ")),
      el("div", { class: "row", style: "margin-top:8px" }, el("span", { class: "small" }, "Windows: "), platformCell(f.targets.windows),
        el("span", { class: "small" }, "Raspberry Pi: "), platformCell(f.targets.linux_arm64_pi))),
    d.live_view ? liveViewCard() : null,
    el("h2", {}, tx("אתרים עם שליטה מלאה", "Full-control sites") + ` (${d.sites.length})`),
    el("div", { class: "card" },
      rows.length ? el("table", {}, el("tbody", {}, rows))
        : el("div", { class: "empty" }, "Empty: every site is read-only + ask. Add only sites you trust JARVES to click and type on."),
      el("div", { class: "row", style: "margin-top:10px" }, input, add), msg,
      el("div", { class: "muted small", style: "margin-top:6px" }, "Also from the command line: jarvis browser allow <site> / jarvis browser remove <site>. Purchases, payments and passwords still ask on the screen every time.")));
};
