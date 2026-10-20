// SW-V3 P1.2 Browser page: the owner's allow-list of sites JARVES may fully control (default empty), the rules and
// what is open right now. Uses the helpers of app.js (el, api, render, header, pill, route) and platformCell (m2.js).
"use strict";

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
    "aria-label": "Site to allow" });
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
    el("h2", {}, tx("אתרים עם שליטה מלאה", "Full-control sites") + ` (${d.sites.length})`),
    el("div", { class: "card" },
      rows.length ? el("table", {}, el("tbody", {}, rows))
        : el("div", { class: "empty" }, "Empty: every site is read-only + ask. Add only sites you trust JARVES to click and type on."),
      el("div", { class: "row", style: "margin-top:10px" }, input, add), msg,
      el("div", { class: "muted small", style: "margin-top:6px" }, "Also from the command line: jarvis browser allow <site> / jarvis browser remove <site>. Purchases, payments and passwords still ask on the screen every time.")));
};
