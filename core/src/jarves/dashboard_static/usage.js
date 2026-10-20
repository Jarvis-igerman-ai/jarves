// SW-V2 M5 dashboard pages: Usage (meters, thresholds) and Backup (list, verify, restore drill, retention), plus the
// compact usage strip at the bottom of every page. Uses app.js helpers (el, api, header, pill, render, tile...).
// Registers its pages in PAGES (pages.js; the app.js router checks it first), so it adds no edits to other pages.
"use strict";

(function () {
  const css = document.createElement("link");
  css.rel = "stylesheet"; css.href = "/static/usage.css";
  document.head.append(css);

  // nav entries (before Settings)
  const nav = document.getElementById("nav");
  const settings = nav && nav.querySelector('[data-page="settings"]');
  for (const [page, label] of [["usage", "Usage"], ["backup", "Backup"]]) {
    if (!nav || nav.querySelector(`[data-page="${page}"]`)) continue;
    const a = el("a", { href: "#/" + page, "data-page": page }, label);
    nav.insertBefore(a, settings || null);
  }

  const CAT = {
    provider_authoritative: ["ok", "from the provider"], app_local_counter: ["plain", "JARVES counter"],
    estimate: ["medium", "estimate"], unavailable: ["unavailable", "not reported"],
  };
  const catPill = (c) => pill((CAT[c] || ["plain"])[0], (CAT[c] || [, c])[1]);
  const when = (ts) => ts ? `${day(ts)} ${clock(ts)}` : "Unknown";

  function bar(m) {
    const b = el("div", { class: "ubar", role: "img", "aria-label": m.percent === null ? "percent unknown" : `${m.percent}% used` });
    if (m.percent === null) { b.classList.add("unknown"); return b; }
    const p = Math.min(100, m.percent);
    b.append(el("i", { class: m.percent >= 100 ? "crit" : m.percent >= 90 ? "high" : m.percent >= 75 ? "warn" : "",
      style: `width:${p}%` }));
    return b;
  }
  function headline(m) {
    if (m.state === "limit_reached") return "Limit reached";
    if (m.percent !== null) return m.percent_text;
    return m.used === null ? "Unknown" : m.used_text;
  }

  // ---------------------------------------------------------------- footer strip (every page)
  const strip = el("footer", { class: "ustrip", "aria-label": "Usage" });
  document.body.append(strip);
  async function refreshStrip() {
    let d;
    try { d = await api("/api/usage"); } catch (e) { strip.replaceChildren(el("span", { class: "muted small" }, "Usage: not available")); return; }
    const part = (label, f) => {
      const m = f.meter, x = f.extra;
      const node = el("a", { href: "#/usage", class: "upart" + (m.state === "limit_reached" ? " reached" : "") },
        el("b", {}, label), bar(m), el("span", { class: "uval" }, headline(m)), catPill(m.category),
        x ? el("span", { class: "muted small" }, `${txh(x, "title")}: ${x.used_text}${x.limit !== null ? " / " + x.limit_text : ""}`) : null);
      hoverTip(node, [txh(m, "title"), `${m.used_text} of ${m.limit_text} (${m.percent_text})`, `source: ${txh(m, "source")}`,
        `window: ${txh(m, "window") || m.window_text} · reset: ${m.reset_text}`, txh(m, "note")]);
      return node;
    };
    // SW-V3 P0.2: today's estimated JARVES spend next to the provider meters (from /api/overview, app.js)
    const t = typeof overview !== "undefined" && overview && overview.today;
    const cost = el("a", { href: "#/costs", class: "upart ucost" }, el("b", {}, tx("היום", "Today")),
      el("span", { class: "uval" }, t ? usd(t.total) : tx("לא ידוע", "Unknown")), est());
    hoverTip(cost, [tx("הוצאה משוערת היום (לפי [pricing])", "Estimated spend today (from [pricing])"),
      t ? tx(`${t.sessions} שיחות · ${t.voice_minutes} דק׳ קול`, `${t.sessions} conversations · ${t.voice_minutes} voice min`) : ""]);
    strip.replaceChildren(part(tx("מנוי ChatGPT", "ChatGPT plan"), d.footer.chatgpt_plan), part("OpenAI API", d.footer.openai_api), cost);
  }
  refreshStrip();
  setTimeout(refreshStrip, 1500);                 // once /api/overview has answered (today's cost)
  setInterval(refreshStrip, 60000);
  JL.onChange(refreshStrip);

  // ---------------------------------------------------------------- Usage page
  async function pageUsage() {
    const d = await api("/api/usage");
    const groups = [["chatgpt_plan", "ChatGPT plan"], ["openai_api", "OpenAI API"], ["jarves_local", "JARVES daily limits"]];
    const cards = groups.map(([g, title]) => el("div", { class: "card", style: "margin-bottom:12px" }, el("h2", {}, title),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Meter"), el("th", {}, "Used / limit"), el("th", {}, ""),
        el("th", {}, "Window / reset"), el("th", {}, "Source"))),
        el("tbody", {}, d.meters.filter((m) => m.provider === g).map((m) => el("tr", {},
          el("td", {}, el("b", {}, txh(m, "title")), el("div", { class: "muted small" }, `${txh(m, "metric")} (${m.unit})`),
            m.state === "limit_reached" ? pill("fail", "limit reached") : null, m.state === "stale" ? pill("medium", "stale") : null),
          el("td", { class: "num" }, `${m.used_text} / ${m.limit_text}`, m.limit !== null ? el("div", { class: "small" }, m.percent_text) : null,
            m.limit_source ? el("div", { class: "muted small" }, "limit: " + txh(m, "limit_source")) : null),
          el("td", {}, bar(m)),
          el("td", { class: "small" }, txh(m, "window") || m.window_text, el("div", { class: "muted" }, "reset: " + m.reset_text)),
          el("td", { class: "small" }, catPill(m.category), el("div", { class: "muted" }, txh(m, "source")),
            el("div", { class: "muted" }, `confidence ${m.confidence} · ${m.scope.replace("_", " ")} · checked ${when(m.last_verified)}`),
            m.note ? el("div", { class: "muted" }, txh(m, "note")) : null)))))));
    const s = d.settings;
    const th = el("input", { type: "text", value: s.thresholds.join(", "), "aria-label": "Alert thresholds in percent", size: 14 });
    const bud = el("input", { type: "number", min: "0", step: "1", value: s.api_monthly_budget_usd ?? "", placeholder: "none",
      "aria-label": "Monthly API budget in dollars" });
    const costs = el("select", { "aria-label": "Costs API" }, ["auto", "on", "off"].map((v) => {
      const o = el("option", { value: v }, v); if (v === s.costs_api) o.selected = true; return o; }));
    const msg = el("span", { class: "muted small" });
    const save = el("button", { type: "button", class: "primary", onclick: async () => {
      try {
        await api("/api/usage/settings", { method: "POST", body: JSON.stringify({ thresholds: th.value,
          api_monthly_budget_usd: bud.value === "" ? null : Number(bud.value), costs_api: costs.value }) });
        msg.textContent = "Saved"; route(true); refreshStrip();
      } catch (e) { msg.textContent = "Not saved: " + e.message; }
    } }, "Save");
    const alerts = d.alerts.length ? el("table", {}, el("tbody", {}, d.alerts.map((a) => el("tr", {},
      el("td", {}, when(a.ts)), el("td", {}, a.meter), el("td", { class: "num" }, a.threshold + "%"),
      el("td", { class: "muted small" }, "window " + a.window)))))
      : el("div", { class: "empty" }, "No threshold reached yet.");
    render(header("Usage", "What is used and what is left — and where each number comes from. Unknown means the provider does not tell JARVES."),
      cards,
      el("h2", {}, "Alerts"),
      el("div", { class: "card" }, el("div", { class: "kv" },
        el("div", { class: "k" }, "Thresholds (%)"), el("div", { class: "v" }, th, el("span", { class: "muted small" }, " once per meter and window")),
        el("div", { class: "k" }, "API budget per month ($)"), el("div", { class: "v" }, bud,
          el("span", { class: "muted small" }, " your own number: OpenAI does not expose the project budget")),
        el("div", { class: "k" }, "Costs API"), el("div", { class: "v" }, costs, el("span", { class: "muted small" },
          d.costs_api.key_present ? " admin key present (read-only use)" : " needs OPENAI_ADMIN_KEY in secrets.env"))),
        el("div", { class: "row", style: "margin-top:10px" }, save, msg), el("h2", {}, "Recent alerts"), alerts),
      el("ul", { class: "muted small" }, d.notes.map((n, i) => el("li", {}, (d["notes_" + JL.lang] && d["notes_" + JL.lang][i]) || JL.t(n)))));
  }

  // ---------------------------------------------------------------- Backup page
  async function pageBackup() {
    const d = await api("/api/backup");
    const out = el("div", { class: "muted small", "aria-live": "polite" });
    const act = (label, path, name, cls) => el("button", { type: "button", class: cls || "ghost", onclick: async (ev) => {
      const b = ev.currentTarget; b.disabled = true; out.textContent = label + "…";
      try {
        const r = await api(path, { method: "POST", body: JSON.stringify(name ? { name } : {}) });
        out.textContent = path.endsWith("/test") ? `Restore test ${r.ok ? "PASSED" : "FAILED"}: ${r.files || 0} files, ` +
          `${r.memories ?? "?"} memories, ${r.conversations ?? "?"} conversations${r.error ? " — " + r.error : ""}`
          : path.endsWith("/verify") ? `${r.name}: ${r.ok ? "OK" : "FAILED " + r.errors.join("; ")} (${r.files} files)`
          : `Backup written and verified: ${r.name}`;
        if (!path.endsWith("/test") && !path.endsWith("/verify")) setTimeout(() => route(true), 400);
      } catch (e) { out.textContent = label + " failed: " + e.message; }
      b.disabled = false;
    } }, label);
    const rows = d.backups.map((b) => el("tr", {}, el("td", {}, el("code", {}, b.name)), el("td", {}, pill(b.kind === "auto" ? "plain" : "ok", b.kind)),
      el("td", { class: "num" }, (b.size / 1024).toFixed(0) + " KB"), el("td", {}, when(b.ts)),
      el("td", {}, act("Verify", "/api/backup/verify", b.name), " ", act("Test restore", "/api/backup/test", b.name))));
    const s = d.settings;
    const daily = el("input", { type: "number", min: "1", max: "365", value: s.daily, "aria-label": "Daily backups kept" });
    const weekly = el("input", { type: "number", min: "0", max: "365", value: s.weekly, "aria-label": "Weekly backups kept" });
    const auto = el("input", { type: "checkbox", "aria-label": "Automatic daily backup" }); auto.checked = s.auto;
    const faces = el("input", { type: "checkbox", "aria-label": "Include face recognition data" }); faces.checked = s.include_faces;
    const msg = el("span", { class: "muted small" });
    const st = d.state || {};
    render(header("Backup", "Automatic local backups of settings, memory, notes and history. Never your keys or sign-in tokens.",
        act("Back up now", "/api/backup", null, "primary")),
      out,
      el("div", { class: "tiles", style: "margin-top:12px" },
        tile("Last backup", st.last ? when(st.last.ts) : "never", st.last ? st.last.kind : ""),
        tile("Backups kept", String(d.backups.length), d.folder),
        tile("Last restore test", st.last_restore_test ? (st.last_restore_test.ok ? "passed" : "FAILED") : "never",
          st.last_restore_test ? when(st.last_restore_test.ts) : "run one below")),
      el("div", { class: "card" }, rows.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Backup"), el("th", {}, "Kind"),
        el("th", { class: "num" }, "Size"), el("th", {}, "When"), el("th", {}, ""))), el("tbody", {}, rows))
        : el("div", { class: "empty" }, "No backups yet. The first automatic one runs a few minutes after JARVES starts.")),
      el("h2", {}, "Keep"),
      el("div", { class: "card" }, el("div", { class: "kv" },
        el("div", { class: "k" }, "Automatic daily backup"), el("div", { class: "v" }, auto),
        el("div", { class: "k" }, "Daily backups"), el("div", { class: "v" }, daily),
        el("div", { class: "k" }, "Weekly backups"), el("div", { class: "v" }, weekly),
        el("div", { class: "k" }, "Include face data"), el("div", { class: "v" }, faces, el("span", { class: "muted small" }, " biometric; off by default"))),
        el("div", { class: "row", style: "margin-top:10px" }, el("button", { type: "button", class: "primary", onclick: async () => {
          try {
            await api("/api/backup/settings", { method: "POST", body: JSON.stringify({ daily: Number(daily.value),
              weekly: Number(weekly.value), auto: auto.checked, include_faces: faces.checked }) });
            msg.textContent = "Saved";
          } catch (e) { msg.textContent = "Not saved: " + e.message; }
        } }, "Save"), msg)),
      el("h2", {}, "If something goes wrong"),
      el("div", { class: "card" }, el("ol", {}, d.guidance.map((g, i) => el("li", {}, (d["guidance_" + JL.lang] && d["guidance_" + JL.lang][i]) || JL.t(g)))),
        d.core_running ? el("div", { class: "small" }, "JARVES is running: a real restore needs it stopped first (the test restore above is always safe).") : null));
  }

  PAGES.usage = pageUsage;                      // pages.js registry, checked first by the app.js router
  PAGES.backup = pageBackup;
})();
