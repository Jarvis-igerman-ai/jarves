// JARVES dashboard: Self-management page (owner request 21 Oct 2026). Uses app.js helpers (el, api, render, header,
// pill, route). Text goes in via textContent only. NOTHING on this page changes anything without the approvals flow:
// every button asks on the popup / dashboard banner first (BEFORE / AFTER), is backed up and can be undone from the
// History list. Secrets are never typed here: "Connect" opens the guided window (hidden input).
"use strict";

(function () {
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const stTx = { connected: ["מחובר", "connected"], missing: ["לא מחובר", "not connected"],
    attention: ["דורש טיפול", "needs attention"], on: ["דולק", "on"], off: ["כבוי", "off"],
    unavailable: ["לא זמין", "unavailable"] };
  const stCls = { connected: "ok", on: "ok", attention: "degraded", unavailable: "degraded", missing: "plain", off: "plain" };
  const t2 = (pair) => pair ? tx(pair[0], pair[1]) : "";
  const when = (ts) => ts ? new Date(ts * 1000).toLocaleString(window.JL ? JL.locale() : []) : "—";
  const card = (title, ...kids) => el("div", { class: "card" }, el("h2", {}, title), ...kids);

  function mkStatus() {
    const msg = el("div", { class: "small", role: "status", "aria-live": "polite" });
    const show = (text, bad) => { msg.textContent = text; msg.className = "small " + (bad ? "bad" : "ok"); };
    return { msg, show };
  }

  // one change: preview the BEFORE / AFTER, then ask (the popup shows the same text), then reload
  async function change(body, ui, done) {
    try {
      const pv = await post("/api/selfmanage/preview", body);
      ui.show(tx("ממתין לאישור שלך על המסך (חלון או באנר): ", "Waiting for your approval on the screen (popup or banner): ") + pv.summary);
      const r = await post("/api/selfmanage/change", body);
      ui.show(r.message);
      if (done) done(); else route(true);
    } catch (e) { ui.show(e.message, true); }
  }

  function connectionsCard(ui) {
    const box = el("div", {}, el("div", { class: "muted" }, tx("בודק…", "Checking…")));
    const load = async () => {
      try {
        const d = await api("/api/selfmanage/connections");
        box.replaceChildren(...d.connections.map((c) => el("div", { class: "conn" },
          pill(stCls[c.state] || "plain", t2(stTx[c.state]) || c.state), " ",
          el("b", {}, tx(c.title_he || c.id, c.title_en || c.id)), " ",
          el("span", { class: "muted small" }, tx(c.detail_he || "", c.detail_en || "")),
          c.state !== "connected" && c.fix_en ? el("div", { class: "small" }, tx(c.fix_he || "", c.fix_en)) : null,
          c.state !== "connected" && c.id !== "check" ? el("button", { type: "button", class: "ghost", onclick: async () => {
            try { const r = await post("/api/selfmanage/connect", { which: c.id }); ui.show(r.message); }
            catch (e) { ui.show(e.message, true); } } }, tx("חיבור / תיקון", "Connect / fix")) : null)));
      } catch (e) { box.replaceChildren(el("div", { class: "bad" }, e.message)); }
    };
    load();
    return card(tx("חיבורים ותוצאות בדיקה", "Connections and test results"), box,
      el("div", { class: "row" }, el("button", { type: "button", class: "ghost", onclick: load }, tx("בדוק שוב", "Check again")),
        el("span", { class: "muted small" }, tx("מפתחות ואסימונים מוקלדים בחלון ההנחיה בלבד (הקלדה מוסתרת), לא כאן.",
          "Keys and tokens are typed in the guided window only (hidden), never here."))));
  }

  function featuresCard(d, ui) {
    return card(tx("יכולות", "Features"), el("div", { class: "muted small" }, tx(
      "מתגי הרשאה ובטיחות אינם ניתנים לשינוי מכאן (עמוד התכונות).", "Permission and safety switches cannot be changed here (Features page).")),
      d.features.map((f) => el("div", { class: "conn" }, pill(stCls[f.state] || "plain", t2(stTx[f.state]) || f.state), " ",
        el("b", {}, tx(f.title_he, f.title_en)), " ", el("span", { class: "muted small" }, txh(f, "reason") || ""),
        f.protected ? el("span", { class: "muted small" }, " · " + tx("מוגן", "protected")) :
          el("button", { type: "button", class: "ghost", onclick: () => change(
            { what: "feature", target: f.id, value: f.state === "off" ? "on" : "off" }, ui) },
          f.state === "off" ? tx("הפעל", "Turn on") : tx("כבה", "Turn off")))));
  }

  function skillsCard(d, ui) {
    const g = d.skills.generated;
    return card(tx("כלים", "Skills"),
      el("div", { class: "small" }, tx("מובנים: ", "Built in: "), d.skills.built_in.map((s) => s.id).join(", ") || "—"),
      g.length ? g.map((s) => el("div", { class: "conn" }, pill(s.enabled ? "ok" : "plain", s.enabled ? tx("דולק", "on") : tx("כבוי", "off")),
        " ", el("b", {}, s.title), " ", el("button", { type: "button", class: "ghost", onclick: () => change(
          { what: "skill", target: s.id, value: s.enabled ? "off" : "on" }, ui) }, s.enabled ? tx("כבה", "Turn off") : tx("הפעל", "Turn on"))))
        : el("div", { class: "muted small" }, tx("אין כלים שנוצרו.", "No generated skills.")));
  }

  function filesCard(d, ui) {
    const names = d.files.filter((f) => !f.generated).map((f) => f.name);
    let cur = names.includes("USER.md") ? "USER.md" : names[0];
    const sel = el("select", { "aria-label": tx("קובץ", "File"), onchange: (ev) => { cur = ev.target.value; fill(); } },
      d.files.map((f) => el("option", { value: f.name }, f.name + (f.generated ? " (" + tx("נוצר אוטומטית, קריאה בלבד", "generated, read only") + ")" : f.editable ? "" : " (" + tx("לא ניתן לשינוי מכאן", "not editable here") + ")"))));
    const ta = el("textarea", { rows: "12", dir: "auto", "data-noi18n": "", "aria-label": tx("תוכן הקובץ", "File text"), style: "width:100%;font-family:monospace" });
    const info = el("div", { class: "muted small" });
    const save = el("button", { type: "button", class: "primary", onclick: () => change(
      { what: "file", target: sel.value, mode: "replace_file", value: ta.value }, ui) }, tx("שמור (מבקש אישור)", "Save (asks for approval)"));
    function fill() {
      sel.value = cur;
      const f = d.files.find((x) => x.name === cur);
      ta.value = d.file_text[cur] !== undefined ? d.file_text[cur] : "";
      ta.readOnly = !(f && f.editable);
      save.disabled = ta.readOnly;
      info.textContent = f ? `${f.title} · ${f.chars} ${tx("תווים", "characters")} · ${f.versions} ${tx("גרסאות שמורות", "saved versions")}` : "";
    }
    fill();
    return card(tx("קבצי אישיות וכללים", "Persona and rule files"), sel, info, ta, el("div", { class: "row" }, save));
  }

  function memoryCard(d, ui) {
    const inp = el("input", { type: "text", maxlength: "400", dir: "auto", "data-noi18n": "", "aria-label": tx("זיכרון חדש", "New memory"), style: "min-width:320px" });
    return card(tx("זיכרון", "Memory") + ` (${d.memory.count})`,
      el("div", { class: "row" }, inp, el("button", { type: "button", class: "ghost", onclick: () => change(
        { what: "memory_add", value: inp.value }, ui) }, tx("זכור (מבקש אישור)", "Remember (asks for approval)"))),
      d.memory.items.map((m) => el("div", { class: "conn" }, el("span", { dir: "auto", "data-noi18n": "" }, m.text), " ",
        el("button", { type: "button", class: "ghost", onclick: () => change({ what: "memory_remove", target: String(m.id) }, ui) },
          tx("שכח (מבקש אישור)", "Forget (asks for approval)")))));
  }

  function improveCard(d) {
    return card(tx("מה אפשר לשפר", "What could be improved"),
      d.improve.length ? d.improve.map((i) => el("div", { class: "small" }, "• " + tx(i.he, i.en)))
        : el("div", { class: "muted small" }, tx("לא נמצא כלום.", "Nothing found.")));
  }

  function historyCard(d, ui) {
    return card(tx("היסטוריית שינויים", "History of changes"),
      d.history.length ? d.history.map((h) => el("div", { class: "conn" },
        el("span", { class: "muted small" }, when(h.ts)), " ", el("b", {}, h.summary), " ",
        el("span", { class: "muted small" }, h.by), h.undone ? pill("plain", tx("בוטל", "undone")) : null,
        h.undoable && !h.undone ? el("button", { type: "button", class: "ghost", onclick: async () => {
          try { ui.show(tx("ממתין לאישור שלך על המסך…", "Waiting for your approval on the screen…"));
            const r = await post("/api/selfmanage/undo", { id: h.id }); ui.show(r.message); route(true); }
          catch (e) { ui.show(e.message, true); } } }, tx("ביטול השינוי (מבקש אישור)", "Undo (asks for approval)")) : null))
        : el("div", { class: "muted small" }, tx("עוד לא בוצעו שינויים.", "No changes yet.")));
  }

  PAGES.selfmanage = async function () {
    const d = await api("/api/selfmanage");
    const ui = mkStatus();
    render(header(tx("ניהול עצמי", "Self-management"),
      tx("כל שינוי מבקש את אישורך עם 'לפני' ו'אחרי', נשמר לו גיבוי ואפשר לבטל אותו. אפשר גם בקול: 'בטל את זה'.",
        "Every change asks for your approval with BEFORE and AFTER, is backed up and can be undone. Also by voice: 'undo that'.")),
      ui.msg, connectionsCard(ui), improveCard(d), featuresCard(d, ui), skillsCard(d, ui), filesCard(d, ui), memoryCard(d, ui),
      historyCard(d, ui));
  };
})();
