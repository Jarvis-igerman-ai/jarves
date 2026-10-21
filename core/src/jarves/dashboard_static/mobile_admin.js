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
    const sw = el("button", { type: "button", class: "switch", "aria-pressed": d.phone_approvals ? "true" : "false", onclick: async () => {
      if (!d.phone_approvals && !confirm(tx("לאפשר לטלפון לאשר פעולות? בקשות עם קוד עדיין יאושרו רק במחשב.", "Let phones approve actions? Requests that need a typed code are still approved on the computer only."))) return;
      await api("/api/mobile/settings", { method: "POST", body: JSON.stringify({ phone_approvals: !d.phone_approvals }) }); route(true);
    } }, d.phone_approvals ? tx("פועל", "On") : tx("כבוי", "Off"));
    const appr = el("div", { class: "card" }, el("h2", {}, tx("אישורים מהטלפון", "Approvals from the phone")), sw,
      el("div", { class: "muted small" }, tx("כבוי כברירת מחדל: האישורים נענים בחלון במחשב.", "Off by default: approvals are answered in the popup on this computer.")));
    const devs = d.devices.map((x) => el("tr", {}, el("td", {}, x.name), el("td", { class: "small" }, x.id),
      el("td", { class: "small" }, x.last_seen ? `${day(x.last_seen)} ${clock(x.last_seen)}` : "—"), el("td", { class: "small" }, x.last_ip || ""),
      el("td", {}, x.revoked ? pill("plain", tx("בוטל", "revoked")) : el("button", { type: "button", onclick: async () => {
        await api(`/api/mobile/devices/${x.id}/revoke`, { method: "POST", body: "{}" }); route(true);
      } }, tx("ביטול גישה", "Revoke")))));
    const audit = a.entries.slice(0, 100).map((x) => el("tr", {}, el("td", { class: "small" }, `${day(x.ts)} ${clock(x.ts)}`),
      el("td", {}, x.event), el("td", { class: "small" }, [x.method, x.path, x.status, x.device, x.ip, x.reason].filter(Boolean).join(" · "))));
    render(header(tx("טלפון", "Phone"), tx("דיבור עם JARVES מהטלפון. אין שרת בענן: הטלפון מדבר ישירות עם המחשב הזה.", "Talk to JARVES from your phone. No cloud server: the phone talks directly to this computer.")),
      status, el("div", { style: "height:12px" }), appr, el("h2", {}, tx("טלפונים מצומדים", "Paired phones")),
      el("div", { class: "card m4-scroll" }, devs.length ? el("table", {}, el("thead", {}, el("tr", {}, ["", "Id", "", "", ""].map((h) => el("th", {}, h)))), el("tbody", {}, devs))
        : el("div", { class: "empty" }, tx("אין טלפונים מצומדים.", "No paired phones."))),
      el("h2", {}, tx("יומן ביקורת (ללא תוכן הודעות)", "Audit log (no message text)")),
      el("div", { class: "card m4-scroll" }, audit.length ? el("table", {}, el("tbody", {}, audit)) : el("div", { class: "empty" }, tx("עוד לא נרשם דבר.", "Nothing logged yet."))));
  }
  Object.assign(PAGES, { phone: pagePhone });
})();
