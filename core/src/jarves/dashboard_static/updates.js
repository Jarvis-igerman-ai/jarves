// SW-V4 L Updates page: current version, the signed update check, install after YOU press the button, and the Roll back
// button (previous version + the data from before the update; asks for approval on the screen). Uses the helpers of app.js
// (el, api, render, header, pill, route, tx) and registers PAGES.updates (pages.js). Text goes in via textContent only.
"use strict";

(function () {
  const nav = document.getElementById("nav");
  const settings = nav && nav.querySelector('[data-page="settings"]');
  if (nav && !nav.querySelector('[data-page="updates"]')) {
    nav.insertBefore(el("a", { href: "#/updates", "data-page": "updates" }, "Updates"), settings || null);
  }
})();

PAGES.updates = async function () {
  const d = await api("/api/updates");
  const rb = d.rollback || {};
  const out = el("div", { class: "muted small", role: "status", "aria-live": "polite" });
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const busy = (b, on) => { b.disabled = on; };

  const check = el("button", { type: "button", class: "ghost", onclick: async (ev) => {
    busy(ev.currentTarget, true); out.textContent = tx("בודק…", "Checking…");
    try { await post("/api/updates/check"); route(true); } catch (e) { out.textContent = tx("הבדיקה נכשלה: ", "Check failed: ") + e.message; }
    busy(ev.currentTarget, false);
  } }, "Check now");

  const install = d.available ? el("button", { type: "button", class: "primary", onclick: async (ev) => {
    busy(ev.currentTarget, true);
    out.textContent = tx("מוריד ובודק את החתימה, ה-SHA256 והגודל…", "Downloading and checking the signature, SHA256 and size…");
    try {
      const r = await post("/api/updates/install", { approve: true, silent: true });
      out.textContent = r.note;
    } catch (e) { out.textContent = tx("לא הותקן (הגרסה הנוכחית ממשיכה לעבוד): ", "Not installed (the current version keeps working): ") + e.message; }
    busy(ev.currentTarget, false);
  } }, "Install update") : null;

  const rollback = el("button", { type: "button", class: "ghost", disabled: !rb.possible, onclick: async (ev) => {
    busy(ev.currentTarget, true);
    out.textContent = tx("ממתין לאישור שלך על המסך…", "Waiting for your approval on the screen…");
    try {
      const r = await post("/api/updates/rollback", { approve: true });
      out.textContent = r.note;
    } catch (e) { out.textContent = tx("לא בוצעה חזרה: ", "Not rolled back: ") + e.message; }
    busy(ev.currentTarget, false);
  } }, "Roll back to the previous version");

  const latest = d.latest ? d.latest.version : null;
  render(header("Updates", "Install, update and go back to the previous version. JARVES asks before it installs anything and keeps your data.",
      check),
    out,
    d.error ? el("div", { class: "card" }, pill("degraded", tx("בדיקה לא זמינה", "Check unavailable")), " ", el("span", { class: "small" }, d.error)) : null,
    el("div", { class: "card" }, el("div", { class: "kv" },
      el("div", { class: "k" }, "Current version"), el("div", { class: "v" }, el("code", {}, d.current)),
      el("div", { class: "k" }, "Latest available"),
      el("div", { class: "v" }, latest ? el("code", {}, latest) : pill("ok", "Up to date")),
      el("div", { class: "k" }, "Last checked"), el("div", { class: "v" }, d.checked_at || "—")),
      install ? el("div", { class: "row", style: "margin-top:10px" }, install) : null),
    el("h2", {}, "Go back"),
    el("div", { class: "card" },
      rb.possible
        ? el("div", { class: "small" }, tx("הגרסה הקודמת: ", "Previous version: "), el("code", {}, rb.version),
          rb.installer_kept ? "" : tx(" (תורד מהשרת החתום)", " (downloaded from the signed release folder)"))
        : el("div", { class: "empty" }, "Nothing to roll back to: no previous version is kept yet."),
      el("div", { class: "row", style: "margin-top:10px" }, rollback),
      el("div", { class: "muted small", style: "margin-top:6px" },
        "Rolling back stops JARVES, restores your data from before the update and installs the previous version. Anything you changed after the update is kept in a safety backup. Also from the command line: jarvis update --rollback.")),
    el("div", { class: "muted small", style: "margin-top:10px" },
      "The installer is not code-signed yet. Windows SmartScreen may say Windows protected your PC: choose More info, then Run anyway."),
    el("div", { class: "muted small" }, "Starting JARVES when you sign in stays off unless you turn it on (jarvis autostart on)."));
};
