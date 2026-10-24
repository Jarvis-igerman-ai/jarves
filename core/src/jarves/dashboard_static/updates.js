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
  if (nav && !nav.querySelector('[data-page="signoff"]')) {
    nav.insertBefore(el("a", { href: "#/signoff", "data-page": "signoff" }, "Release sign-off"), settings || null);
  }
})();

// The Core reports the update-check problem as an English sentence; show it in the page language (Hebrew page => Hebrew text).
function updateError(e) {
  const m = String(e || "");
  if (m.startsWith("releases not reachable on GitHub")) {
    return tx("אי אפשר להגיע לגרסאות ב-GitHub (אין שם גרסאות שפורסמו, או שהגעתם למגבלת הבקשות). מתקינים חתומים מתפרסמים ב-github.com/igalgerman/jarvis-releases/releases, או שמגדירים [update] base_url.", m);
  }
  if (m.startsWith("no connection to GitHub")) {
    return tx("אין חיבור ל-GitHub כרגע. בדיקת העדכון לא משפיעה על שום דבר אחר.", m);
  }
  if (m.startsWith("GitHub answered HTTP")) {
    return tx("GitHub החזיר שגיאה בבדיקת העדכון: " + m.replace("GitHub answered ", ""), m);
  }
  return m;
}

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

  const isBeta = d.channel === "beta";
  const chan = el("button", { type: "button", class: "ghost", "aria-pressed": isBeta ? "true" : "false", onclick: async (ev) => {
    busy(ev.currentTarget, true);
    try { await post("/api/updates/channel", { channel: isBeta ? "stable" : "beta" }); await post("/api/updates/check"); route(true); }
    catch (e) { out.textContent = tx("הערוץ לא הוחלף: ", "Channel not changed: ") + e.message; }
    busy(ev.currentTarget, false);
  } }, isBeta ? "Switch to the stable channel" : "Switch to the beta channel");
  const au = await api("/api/updates/auto");
  const setAuto = (on) => async (ev) => {
    busy(ev.currentTarget, true);
    try { await post("/api/updates/auto", { enabled: on }); route(true); }
    catch (e) { out.textContent = tx("לא שונה: ", "Not changed: ") + e.message; }
    busy(ev.currentTarget, false);
  };
  const answer = (choice) => async (ev) => {
    busy(ev.currentTarget, true);
    try { await post("/api/updates/auto/answer", { choice }); route(true); }
    catch (e) { out.textContent = e.message; }
    busy(ev.currentTarget, false);
  };
  const pick = (channel, auto) => async (ev) => {
    busy(ev.currentTarget, true);
    try { await post("/api/updates/auto/channel", { channel, auto }); route(true); }
    catch (e) { out.textContent = tx("הערוץ לא נבחר: ", "Channel not chosen: ") + e.message; }
    busy(ev.currentTarget, false);
  };
  const autoLine = au.enabled
    ? (au.default_from_channel === "beta" ? "Automatic updates: on (beta default)" : "Automatic updates: on")
    : "Automatic updates: off";
  const when = (ts) => el("code", {}, new Date(ts * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }));
  const NEXT = { check: "Next update check", install: "Install at a quiet moment, not before",
    quiet_moment: "Waiting for a quiet moment (never in a conversation)" };
  const nextRow = au.enabled && au.next_kind && NEXT[au.next_kind]
    ? [el("div", { class: "k" }, "Next"), el("div", { class: "v" }, NEXT[au.next_kind],
        au.next_kind === "quiet_moment" ? "" : " ", au.next_kind === "quiet_moment" ? "" : when(au.next_at))] : [];
  const lr = au.last_result || null;
  const lastRow = lr && lr.version
    ? [el("div", { class: "k" }, "Last result"), el("div", { class: "v" },
        lr.ok ? "Updated to" : "Not installed, nothing changed:", " ", el("code", {}, lr.version), " ",
        lr.ok ? "" : el("code", {}, lr.code || ""), " ", when(lr.ts))] : [];
  const note = au.notice && au.notice.kind !== "choose" ? el("div", { class: "small", style: "margin-top:8px" }, tx(au.notice.text_he, au.notice.text_en)) : null;
  const autoCard = el("div", { class: "card" }, el("h2", {}, "Automatic updates"),
    el("div", { class: "kv" },
      el("div", { class: "k" }, "Status"), el("div", { class: "v" }, autoLine, " ", au.enabled
        ? el("button", { type: "button", class: "ghost", onclick: setAuto(false) }, "Turn off")
        : el("button", { type: "button", class: "ghost", onclick: setAuto(true) }, "Turn on")),
      el("div", { class: "k" }, "Now"), el("div", { class: "v" }, tx(au.message_he, au.message_en),
        au.busy && au.busy.length ? " (" + au.busy.join(", ") + ")" : ""), ...nextRow, ...lastRow),
    au.enabled && au.target ? el("div", { class: "row", style: "margin-top:10px" },
      el("button", { type: "button", class: "primary", onclick: answer("now") }, "Update now"),
      el("button", { type: "button", class: "ghost", onclick: answer("later") }, "Later"),
      el("button", { type: "button", class: "ghost", onclick: answer("off") }, "Turn off automatic updates")) : null,
    note,
    el("div", { class: "muted small", style: "margin-top:6px" },
      "Installs only a newer signed version of your channel, only at a quiet moment (not in a conversation, an approval or a coding-agent task), after a backup. It is on by default only on the beta channel. Command line: jarvis update auto on|off|status."));
  const choose = au.channel_chosen ? null : el("div", { class: "card" }, el("h2", {}, "Which update channel?"),
    el("div", { class: "small" }, tx("יציב: רק גרסאות שנבדקו (ברירת מחדל, בלי עדכון אוטומטי). בטא: כל בנייה ירוקה מוקדם, עם עדכון אוטומטי כברירת מחדל. עד שתבחר נשאר יציב ושום דבר לא מותקן לבד.",
      "Stable: only tested releases (default, no automatic updates). Beta: every green build early, with automatic updates on by default. Until you choose you stay on stable and nothing installs by itself.")),
    el("div", { class: "row", style: "margin-top:10px" },
      el("button", { type: "button", class: "primary", onclick: pick("stable", false) }, "Stable, no automatic updates"),
      el("button", { type: "button", class: "ghost", onclick: pick("stable", true) }, "Stable with automatic updates"),
      el("button", { type: "button", class: "ghost", onclick: pick("beta", true) }, "Beta with automatic updates"),
      el("button", { type: "button", class: "ghost", onclick: pick("beta", false) }, "Beta, no automatic updates")));
  const latest = d.latest ? (d.latest.display || d.latest.version) : null;
  render(header("Updates", "Install, update and go back to the previous version. JARVES asks before it installs anything and keeps your data.",
      check),
    out,
    d.error ? el("div", { class: "card" }, pill("degraded", tx("בדיקה לא זמינה", "Check unavailable")), " ", el("span", { class: "small" }, updateError(d.error))) : null,
    el("div", { class: "card" }, el("div", { class: "kv" },
      el("div", { class: "k" }, "Current version"), el("div", { class: "v" }, el("code", {}, d.beta_build || d.current), d.beta_build ? " " : "", d.beta_build ? pill("degraded", "BETA") : ""),
      el("div", { class: "k" }, "Update channel"),
      el("div", { class: "v" }, el("code", {}, isBeta ? "beta" : "stable"), " ", chan,
        el("div", { class: "muted small" }, isBeta
          ? tx("בטא: כל בנייה ירוקה של main מגיעה אליך, חתומה. לחזרה לגרסה קודמת: כפתור החזרה למטה.", "Beta: every green main build reaches you, signed. To go back a version use the Roll back button below.")
          : tx("יציב: רק גרסאות שנבדקו ידנית ואושרו.", "Stable: only releases that were tested by hand and signed off."))),
      el("div", { class: "k" }, "Latest available"),
      el("div", { class: "v" }, latest ? el("code", {}, latest) : pill("ok", "Up to date")),
      el("div", { class: "k" }, "Last checked"), el("div", { class: "v" }, d.checked_at || "—")),
      install ? el("div", { class: "row", style: "margin-top:10px" }, install) : null),
    choose, autoCard,
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

// Release sign-off page: the owner's manual test sheet for one stable version (docs/reviews/MANUAL_TEST_<version>.md).
PAGES.signoff = async function () {
  const params = new URLSearchParams((location.hash.split("?")[1]) || "");
  const out = el("div", { class: "muted small", role: "status", "aria-live": "polite" });
  const input = el("input", { type: "text", id: "signoff-version", value: params.get("version") || "", placeholder: "4.1.0", "aria-label": tx("גרסה יציבה", "Stable version") });
  const open = el("button", { type: "button", class: "primary", onclick: () => { location.hash = "#/signoff?version=" + encodeURIComponent(input.value.trim()); } }, "Open sheet");
  const version = params.get("version");
  const head = header("Release sign-off", "Test the latest beta by hand, record each result, then sign off. A stable release is refused without it.");
  if (!version) {
    render(head, el("div", { class: "card" }, el("div", { class: "row" }, el("label", { for: "signoff-version" }, "Stable version"), input, open),
      el("div", { class: "muted small" }, "Create the sheet first: python tools/manual_test_sheet.py --version X.Y.Z (docs/RELEASING.md).")));
    return;
  }
  let d;
  try { d = await api("/api/release/signoff?version=" + encodeURIComponent(version)); }
  catch (e) { render(head, el("div", { class: "card" }, pill("degraded", tx("אין גיליון", "No sheet")), " ", el("span", { class: "small" }, e.message))); return; }
  const post = (body) => api("/api/release/signoff", { method: "POST", body: JSON.stringify({ version, ...body }) });
  const rows = d.rows.map((r) => {
    const note = el("input", { type: "text", value: r.note || "", "aria-label": tx("הערה ל-", "Note for ") + r.id });
    const sel = el("select", { "aria-label": tx("תוצאה ל-", "Result for ") + r.id }, ...["pending", "pass", "fail", "blocked", "accepted-blocked"].map((v) => el("option", { value: v, selected: v === r.result }, v)));
    const save = el("button", { type: "button", class: "ghost", onclick: async () => {
      try { await post({ row: r.id, result: sel.value, note: note.value }); route(true); } catch (e) { out.textContent = e.message; }
    } }, "Save");
    return el("tr", {}, el("td", {}, r.id), el("td", {}, r.area), el("td", {}, r.todo), el("td", {}, r.expected), el("td", {}, sel), el("td", {}, note), el("td", {}, save));
  });
  const finish = el("button", { type: "button", class: "primary", disabled: d.open.length > 0, onclick: async () => {
    try { await post({ finish: true }); route(true); } catch (e) { out.textContent = tx("לא נחתם: ", "Not signed: ") + e.message; }
  } }, "Sign off");
  render(head, out,
    el("div", { class: "card" }, el("div", { class: "kv" },
      el("div", { class: "k" }, "Version"), el("div", { class: "v" }, el("code", {}, d.version)),
      el("div", { class: "k" }, "Signed"), el("div", { class: "v" }, d.signed ? pill("ok", tx("נחתם", "Signed")) : pill("degraded", tx("לא נחתם", "Not signed"))),
      el("div", { class: "k" }, "Tested beta"), el("div", { class: "v" }, d.beta || "—"),
      el("div", { class: "k" }, "Commit"), el("div", { class: "v" }, d.commit ? el("code", {}, d.commit.slice(0, 12)) : "—"),
      el("div", { class: "k" }, "Date"), el("div", { class: "v" }, d.date || "—"))),
    el("table", { class: "table" }, el("thead", {}, el("tr", {}, ...["ID", "Area", "What to do", "Expected", "Result", "Note", ""].map((h) => el("th", { scope: "col" }, h)))), el("tbody", {}, ...rows)),
    el("div", { class: "row", style: "margin-top:10px" }, finish),
    el("div", { class: "muted small" }, "Sign off records the beta you have installed (version and commit) and today's date. Same from the command line: jarvis release signoff."));
};
