// V5 "Phone" page: pair a phone (QR + one-time code), list/revoke phones, phone approvals switch, audit.
// Uses app.js helpers (el, api, render, header, pill, day, clock) and tx(he, en). All text via textContent.
"use strict";

(function () {
  const NS = "http://www.w3.org/2000/svg";
  function qrSvg(rows) {
    const n = rows.length, q = 4, svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${n + 2 * q} ${n + 2 * q}`); svg.setAttribute("width", "220"); svg.setAttribute("height", "220");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "QR code");
    const bg = document.createElementNS(NS, "rect"); bg.setAttribute("width", String(n + 2 * q)); bg.setAttribute("height", String(n + 2 * q)); bg.setAttribute("fill", "#fff"); svg.appendChild(bg);
    const path = document.createElementNS(NS, "path"); let d = "";
    rows.forEach((r, y) => { for (let x = 0; x < n; x++) if (r[x] === "1") d += `M${x + q} ${y + q}h1v1h-1z`; });
    path.setAttribute("d", d); path.setAttribute("fill", "#000"); svg.appendChild(path);
    return svg;
  }
  function tailnetCard(d, box) {
    const t = d.tailnet || {};
    const stateText = { ready: tx("מוכן", "ready"), "not-installed": tx("לא מותקן", "not installed"),
      "not-connected": tx("מותקן אך לא מחובר", "installed but not connected"), unused: tx("לא בשימוש", "not in use") }[t.state] || t.state;
    const kids = [el("h2", {}, tx("Tailscale (גישה מחוץ לבית)", "Tailscale (away from home)")),
      el("div", { id: "tailnetState" }, tx("מצב: ", "Status: "), el("b", {}, stateText),
        t.ip ? el("span", { class: "small" }, ` · ${t.ip}${t.name ? " · " + t.name : ""}`) : "")];
    if (t.present && t.url) {
      kids.push(el("div", { id: "tailnetUrl" }, tx("כתובת בטלפון: ", "Address on the phone: "), el("code", {}, t.url)));
      kids.push(el("div", { class: "muted small" }, t.tls_source === "tailscale"
        ? tx("חיבור מאובטח עם תעודה של Tailscale: קול זמין.", "Secure connection with a Tailscale certificate: voice is available.")
        : tx("ללא תעודת Tailscale: משתמשים בתעודה עצמית או בלי TLS (קול דורש TLS).", "No Tailscale certificate: self-signed or no TLS (voice needs TLS).")));
      if (d.enabled) kids.push(el("div", { class: "row", style: "margin-top:10px" }, el("button", { type: "button", id: "tailnetPair", onclick: async () => {
        try {
          const c = await api("/api/mobile/code", { method: "POST", body: "{}" });
          box.replaceChildren(el("div", { class: "muted small" }, tx("קוד צימוד דרך Tailscale (5 דקות, שימוש אחד):", "Pairing code over Tailscale (5 minutes, one use):")),
            el("div", { class: "m4-big" }, c.code.replace(/(\d{4})(\d{4})/, "$1 $2")), c.tailnet_qr ? qrSvg(c.tailnet_qr) : "",
            el("div", { class: "muted small" }, (c.tailnet_url || c.url).split("#")[0]));
        } catch (e) { box.textContent = e.message; }
      } }, tx("צימוד דרך Tailscale", "Pair over Tailscale"))));
      const only = el("button", { type: "button", class: "switch", id: "tailnetOnly", "aria-pressed": d.bind_tailnet_only ? "true" : "false", onclick: async () => {
        await api("/api/mobile/settings", { method: "POST", body: JSON.stringify({ bind_tailnet_only: !d.bind_tailnet_only }) }); route(true);
      } }, d.bind_tailnet_only ? tx("פועל", "On") : tx("כבוי", "Off"));
      kids.push(el("div", { style: "margin-top:10px" }, tx("האזנה לכתובת Tailscale בלבד (לא לרשת הבית): ", "Listen on the Tailscale address only (not the home network): "), only,
        el("div", { class: "muted small" }, tx("נכנס לתוקף בהפעלה הבאה של JARVES.", "Applies the next time JARVES starts."))));
    } else {
      kids.push(el("div", { class: "muted small", id: "tailnetHelp" }, t.state === "not-connected"
        ? tx("Tailscale מותקן אך לא מחובר. פתחו אותו והתחברו בעצמכם; JARVES לא מתחבר בשבילכם.", "Tailscale is installed but not connected. Open it and sign in yourself; JARVES never signs in for you.")
        : tx("אופציונלי: התקינו Tailscale במחשב ובטלפון והתחברו לאותו חשבון. JARVES רק מזהה אותו ולא מתקין דבר. מדריך: docs/PHONE_ACCESS_TAILSCALE.md", "Optional: install Tailscale on this computer and the phone and sign in to the same account. JARVES only detects it and never installs anything. Guide: docs/PHONE_ACCESS_TAILSCALE.md")));
    }
    return el("div", { class: "card", id: "tailnetCard" }, ...kids);
  }
  async function pagePhone() {
    const d = await api("/api/mobile"), a = await api("/api/mobile/audit");
    const box = el("div", { class: "m4-code", "aria-live": "polite" });
    const status = el("div", { class: "card" }, el("h2", {}, tx("גישה מהטלפון", "Phone access")),
      d.enabled ? [
        el("div", {}, tx("פעיל: טלפונים ברשת הביתית יכולים להצטמד. ", "On: phones on your home network can pair. "),
          d.reachable_from_phone ? el("code", {}, `${d.tls ? "https" : "http"}://${d.host}:${d.port}/m/`) : tx("(נגיש מהמחשב הזה בלבד)", "(this computer only)")),
        el("div", { class: "muted small" }, d.tls ? tx("חיבור מאובטח: קול זמין.", "Secure connection: voice is available.")
          : tx("ללא TLS: שיחה בכתב, אישורים ומצב בלבד. לקול: jarvis dashboard --mobile --tls-selfsigned", "No TLS: chat, approvals and status only. For voice: jarvis dashboard --mobile --tls-selfsigned")),
        el("div", { class: "muted small" }, tx("מתחיל יחד עם JARVES. כיבוי המתג עוצר את הגישה מהטלפון מיד.", "Starts with JARVES. Switching it off stops phone access at once.")),
        el("div", { class: "row", style: "margin-top:10px" }, el("button", { type: "button", class: "primary", onclick: async () => {
          try {
            const c = await api("/api/mobile/code", { method: "POST", body: "{}" });
            box.replaceChildren(el("div", { class: "muted small" }, tx("קוד צימוד (5 דקות, שימוש אחד):", "Pairing code (5 minutes, one use):")),
              el("div", { class: "m4-big" }, c.code.replace(/(\d{4})(\d{4})/, "$1 $2")), qrSvg(c.qr), el("div", { class: "muted small" }, c.url.split("#")[0]));
          } catch (e) { box.textContent = e.message; }
        } }, tx("צימוד טלפון", "Pair a phone"))), box]
        : el("div", {}, tx("כבוי: המחשב הזה בלבד. להפעלה: ", "Off: this computer only. To use your phone: "), el("code", {}, d.how)));
    const sw = el("button", { type: "button", class: "switch", id: "phoneApprovalsSwitch", "aria-pressed": d.phone_approvals ? "true" : "false", onclick: async () => {
      if (!d.phone_approvals && !confirm(tx("אזהרה: טלפון אבוד או גנוב עם גישה מצומדת יוכל לאשר פעולות בכל רמת סיכון, חוץ מבקשות שדורשות קוד מוקלד (אלה נשארות במחשב בלבד). אפשר לנתק את כל הטלפונים בלחיצה אחת. להפעיל?",
        "Warning: a lost or stolen phone that is still paired could approve actions of ANY risk level, except requests that need a typed code (those stay on the computer only). You can revoke every phone with one click. Turn it on?"))) return;
      await api("/api/mobile/settings", { method: "POST", body: JSON.stringify({ phone_approvals: !d.phone_approvals }) }); route(true);
    } }, d.phone_approvals ? tx("פועל", "On") : tx("כבוי", "Off"));
    const appr = el("div", { class: "card" }, el("h2", {}, tx("אישורים מהטלפון", "Approvals from the phone")), sw,
      el("div", { class: "muted small" }, tx("כבוי כברירת מחדל: האישורים נענים בחלון במחשב.", "Off by default: approvals are answered in the popup on this computer.")),
      el("div", { class: "warn small", id: "phoneApprovalsWarning" }, tx("כשהמתג פועל, טלפון מצומד יכול לאשר כל רמת סיכון, חוץ מבקשות עם קוד מוקלד. טלפון אבוד = סכנה: נתקו אותו מיד.",
        "When on, a paired phone can approve any risk level except typed-code requests. A lost phone is a danger: revoke it at once.")),
      el("div", { class: "row", style: "margin-top:8px" }, el("button", { type: "button", class: "danger", id: "revokeAll", onclick: async () => {
        if (!confirm(tx("לנתק את כל הטלפונים המצומדים עכשיו?", "Revoke ALL paired phones now?"))) return;
        await api("/api/mobile/revoke-all", { method: "POST", body: "{}" }); route(true);
      } }, tx("ניתוק כל הטלפונים", "Revoke all phones"))));
    const PG = { home: ["בית", "Home"], chat: ["שיחה", "Chat"], approvals: ["אישורים", "Approvals"], health: ["תקינות ומהירות", "Health & speed"] };
    const pagesOf = (x) => el("fieldset", { class: "m4-pages" }, el("legend", { class: "small" }, tx("דפים מותרים", "Allowed pages")),
      Object.keys(PG).map((p) => el("label", { class: "small" }, el("input", { type: "checkbox", checked: (x.pages || []).includes(p) ? true : null,
        disabled: x.revoked ? true : null, onchange: async (ev) => {
          const now = Object.keys(PG).filter((q) => (q === p ? ev.target.checked : (x.pages || []).includes(q)));
          try { await api(`/api/mobile/devices/${x.id}/pages`, { method: "POST", body: JSON.stringify({ pages: now }) }); } catch (e) { ev.target.checked = !ev.target.checked; }
          route(true);
        } }), " ", tx(PG[p][0], PG[p][1]))),
      el("div", { class: "muted small" }, tx("הגדרות, סודות, קבצים וניהול מכשירים תמיד רק במחשב.", "Settings, secrets, files and device management are always computer-only.")));
    const SC = { tasks: ["יעדים והשתקת התראות", "Goals and notice snooze/stop"], prefs: ["העדפות שנלמדו (צפייה, תיקון, מחיקה)", "Learned preferences (view, correct, forget)"] };
    const scopesOf = (x) => el("fieldset", { class: "m4-pages" }, el("legend", { class: "small" }, tx("הרשאות נוספות (כבויות כברירת מחדל)", "Extra permissions (off by default)")),
      Object.keys(SC).map((k) => el("label", { class: "small" }, el("input", { type: "checkbox", "data-scope": k, checked: (x.scopes || []).includes(k) ? true : null,
        disabled: x.revoked ? true : null, onchange: async (ev) => {
          const now = (x.scopes || []).filter((q) => q !== k).concat(ev.target.checked ? [k] : []);
          try { await api(`/api/mobile/devices/${x.id}/scopes`, { method: "POST", body: JSON.stringify({ scopes: now }) }); } catch (e) { ev.target.checked = !ev.target.checked; }
          route(true);
        } }), " ", tx(SC[k][0], SC[k][1]))));
    const devs = d.devices.map((x) => el("tr", {}, el("td", {}, x.name), el("td", { class: "small" }, x.id),
      el("td", {}, pagesOf(x), scopesOf(x)),
      el("td", { class: "small" }, x.last_seen ? `${day(x.last_seen)} ${clock(x.last_seen)}` : "—"), el("td", { class: "small" }, x.last_ip || ""),
      el("td", {}, x.revoked ? pill("plain", tx("בוטל", "revoked")) : el("button", { type: "button", onclick: async () => {
        await api(`/api/mobile/devices/${x.id}/revoke`, { method: "POST", body: "{}" }); route(true);
      } }, tx("ביטול גישה", "Revoke")))));
    const audit = a.entries.slice(0, 100).map((x) => el("tr", {}, el("td", { class: "small" }, `${day(x.ts)} ${clock(x.ts)}`),
      el("td", {}, x.event), el("td", { class: "small" }, [x.method, x.path, x.status, x.device, x.ip, x.reason].filter(Boolean).join(" · "))));
    render(header(tx("טלפון", "Phone"), tx("דיבור עם JARVES מהטלפון. אין שרת בענן: הטלפון מדבר ישירות עם המחשב הזה.", "Talk to JARVES from your phone. No cloud server: the phone talks directly to this computer.")),
      status, el("div", { style: "height:12px" }), tailnetCard(d, box), el("div", { style: "height:12px" }), appr, el("h2", {}, tx("טלפונים מצומדים", "Paired phones")),
      el("div", { class: "card m4-scroll" }, devs.length ? el("table", {}, el("thead", {}, el("tr", {}, ["", "Id", "", "", "", ""].map((h) => el("th", {}, h)))), el("tbody", {}, devs))
        : el("div", { class: "empty" }, tx("אין טלפונים מצומדים.", "No paired phones."))),
      el("h2", {}, tx("יומן ביקורת (ללא תוכן הודעות)", "Audit log (no message text)")),
      el("div", { class: "card m4-scroll" }, audit.length ? el("table", {}, el("tbody", {}, audit)) : el("div", { class: "empty" }, tx("עוד לא נרשם דבר.", "Nothing logged yet."))));
  }
  Object.assign(PAGES, { phone: pagePhone });
})();
