// Setup and tour (#/setup, #/setup/<topic>), redesigned for #152: ONE card at a time, 3 short lines, 3 big answers.
// Every answer except Later finishes the topic, and that is SAVED on the server (wizard.json, versioned per release);
// finished topics leave the queue and wait in a collapsed "Done (n)" list with an "Again" button.
// Speed: one GET /api/wizard (local state only) paints the page; answers update the page at once and are saved in the
// background (in order); the slow parts (connection state, switches, "Do it for me") load only when "More" is opened.
// Keys: Enter = Works (focus starts there), 1 / 2 / 3 = Works / Did not work / Later. A failure stays a LOCAL bug entry;
// reporting it on GitHub happens only from the Bug reports page, after the owner reviews and confirms it.
// Server: jarves/wizard.py. Uses the app.js helpers (el, api, render, header, pill, route, tx). Text via textContent only.
"use strict";

(function () {
  const L = (he, en) => tx(he, en);
  const T = (o, k) => txo(o, k);
  const MARK = { works: "✓", fails: "✗", skipped: "–", seen: "✓" };
  const HOW = { works: ["עובד", "works"], fails: ["לא עבד", "did not work"], skipped: ["לא בשבילי", "not for me"], seen: ["סומן", "done"] };
  const S = { d: null, cur: "", chain: Promise.resolve(), failing: false, msg: null };

  async function post(path, body) { return api(path, { method: "POST", body: JSON.stringify(body || {}) }); }
  const byId = (id) => S.d.topics.find((t) => t.id === id);
  const onPage = () => /^#\/setup(\/|$)/.test(location.hash) || location.hash === "#/setup";

  // ------------------------------------------------------------------ local state (mirrors wizard.py)
  function counts() {
    const d = S.d;
    const finished = d.topics.filter((t) => !t.left && t.done && t.done.version === d.release).length;
    const total = d.left.length + finished;
    return { left: d.left.length, finished, total, percent: total ? Math.round(100 * finished / total) : 100 };
  }
  // Save in the background, strictly in order; a failure shows "Not saved" with Retry (the page never lies about it).
  function save(path, body, label) {
    S.chain = S.chain.then(() => post(path, body)).then(() => null, (e) => {
      setMsg(el("span", {}, L(`לא נשמר (${label}): ${e.message} `, `Not saved (${label}): ${e.message} `),
        el("button", { type: "button", onclick: () => { setMsg(null); save(path, body, label); } }, L("לנסות שוב", "Retry"))));
    });
    return S.chain;
  }
  function setMsg(node) {
    S.msg = node;
    const box = document.getElementById("tour-msg");
    if (box) box.replaceChildren(...(node ? [node] : []));
  }

  function answer(id, how, note) {
    const d = S.d, t = byId(id);
    if (!t) return;
    const i = d.left.indexOf(id);
    setMsg(null);
    if (how === "later") {
      if (i >= 0) d.left.splice(i, 1);
      d.left.push(id);
      t.status = "later";
      save(`/api/wizard/topic/${id}/choice`, { choice: "later" }, L(t.he, t.en));
    } else {
      if (i >= 0) d.left.splice(i, 1);
      t.left = false;
      t.status = how;
      t.done = { how, version: d.release };
      if (!d.done.includes(id)) d.done.push(id);
      save(`/api/wizard/topic/${id}/done`, { how, note: note || "" }, L(t.he, t.en));
      if (how === "fails") setMsg(el("span", {}, L("נשמר כ\"לא עבד\" ודיווח באג מקומי נוצר. ", "Saved as \"did not work\" and a local bug entry was made. "),
        el("a", { href: "#/issues" }, L("לדווח ב-GitHub (תראו הכול לפני שליחה)", "Report it on GitHub (you review it first)"))));
    }
    const nx = d.left.length ? (d.left[i >= 0 && i < d.left.length ? i : 0]) : "";
    show(how === "later" && d.left.length === 1 ? id : nx);
  }

  function show(id) {
    S.cur = id || "";
    S.failing = false;
    history.replaceState(null, "", "#/setup" + (S.cur ? "/" + S.cur : ""));   // no hashchange: no refetch, no wait
    paint();
    const b = document.getElementById("tour-works");
    if (b) b.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ the card
  function line(labelHe, labelEn, he, en) {
    return he || en ? el("li", {}, el("span", { class: "tour-k" }, L(labelHe, labelEn)), el("span", { class: "tour-v" }, L(he, en))) : null;
  }

  function card(t) {
    const d = S.d;
    const pos = d.left.indexOf(t.id);
    const c = counts();
    const tag = t.again === "retest" ? L("בדיקה חוזרת אחרי תיקון", "Retest after a fix")
      : t.again === "changed" ? L("השתנה בגרסה הזו", "Changed in this version") : null;
    const failBox = el("div", { class: "tour-fail", hidden: !S.failing || null });
    const answers = el("div", { class: "tour-answers", role: "group", "aria-label": L("התשובה שלכם", "Your answer") },
      el("button", { type: "button", class: "primary tour-big", id: "tour-works", onclick: () => answer(t.id, "works") },
        el("span", {}, L("✓ עובד", "✓ Works")), el("kbd", { "aria-hidden": "true" }, "1")),
      el("button", { type: "button", class: "tour-big", id: "tour-fails", onclick: () => openFail(t, failBox) },
        el("span", {}, L("✗ לא עבד", "✗ Did not work")), el("kbd", { "aria-hidden": "true" }, "2")),
      el("button", { type: "button", class: "tour-big", id: "tour-later", onclick: () => answer(t.id, "later") },
        el("span", {}, L("אחר כך", "Later")), el("kbd", { "aria-hidden": "true" }, "3")));
    const doneNote = t.done && !t.left ? el("div", { class: "small" }, pill(t.done.how === "fails" ? "degraded" : "ok", L(...HOW[t.done.how])),
      " ", L("כבר הושלם. אפשר לענות שוב כדי לשנות.", "Already done. Answer again to change it.")) : null;
    const more = el("details", { class: "tour-more" }, el("summary", {}, L("עוד: הגדרה, בדיקה אוטומטית ומדריך", "More: set up, automatic check and guide")));
    more.addEventListener("toggle", () => { if (more.open && !more.dataset.loaded) { more.dataset.loaded = "1"; loadMore(t, more); } });
    return el("section", { class: "card tour-card", "aria-labelledby": "tour-title" },
      el("div", { class: "small muted tour-meta" },
        pos >= 0 ? L(`נושא ${c.finished + 1} מתוך ${c.total}`, `Topic ${c.finished + 1} of ${c.total}`) : L("נושא שהושלם", "A finished topic"),
        t.optional ? L(" · לא חובה", " · optional") : "", tag ? " · " : "", tag),
      el("h2", { id: "tour-title" }, L(t.he, t.en)),
      el("ul", { class: "tour-lines" },
        line("מה זה עושה", "What it does", t.do_he, t.do_en),
        line("לנסות", "Try it", t.say_he, t.say_en),
        line("מה אמור לקרות", "What should happen", t.expect_he, t.expect_en)),
      doneNote, answers, failBox,
      t.optional ? el("button", { type: "button", class: "ghost small tour-skip", onclick: () => answer(t.id, "skipped") }, L("לא בשבילי, לדלג", "Not for me, skip")) : null,
      more);
  }

  function openFail(t, box) {
    S.failing = true;
    const note = el("input", { type: "text", maxlength: "300", id: "tour-note", autocomplete: "off",
      placeholder: L("מה לא עבד? (לא חובה)", "What did not work? (optional)"), "aria-label": L("מה לא עבד (לא חובה)", "What did not work (optional)") });
    const done = () => answer(t.id, "fails", note.value);
    note.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); done(); }
      if (ev.key === "Escape") { ev.preventDefault(); S.failing = false; box.hidden = true; document.getElementById("tour-fails").focus(); }
    });
    box.replaceChildren(note, el("button", { type: "button", class: "primary", onclick: done }, L("לשמור", "Save")),
      el("div", { class: "small muted" }, L("נשמר רק במחשב הזה. שום דבר לא נשלח בלי אישור שלכם.", "Stays on this PC. Nothing is sent without your OK.")));
    box.hidden = false;
    note.focus();
  }

  // "More": the slow parts, loaded only now (connection state, switches, keys, the safe check, the learn links).
  async function loadMore(t, more) {
    const body = el("div", { class: "tour-more-body", role: "status", "aria-live": "polite" }, L("טוען…", "Loading…"));
    more.append(body);
    let v;
    try { v = await api("/api/wizard/topic/" + t.id); } catch (e) { body.textContent = e.message; return; }
    const out = el("div", { class: "tour-out small", role: "status", "aria-live": "polite" });
    const kids = [];
    if (v.reusable) kids.push(el("div", { class: "tour-actions" }, pill("ok", L("נמצא חיבור שעובד", "A working connection was found")), " ", L(v.connect.detail_he, v.connect.detail_en)));
    if (v.features.length) {
      kids.push(el("div", { class: "small" }, L("מתגים: ", "Switches: "), v.features.map((f) => `${T(f, "title")} (${f.enabled ? L("פועל", "on") : L("כבוי", "off")})`).join(", ")));
      if (!v.all_on) kids.push(el("button", { type: "button", onclick: async () => {
        if (v.high && !confirm(L("זו יכולת רגישה. להפעיל?", "This is a sensitive feature. Turn it on?"))) return;
        try { await post(`/api/wizard/topic/${t.id}/switch`, { confirmed: true }); out.textContent = L("הופעל.", "Turned on."); }
        catch (e) { out.textContent = L("לא שונה: ", "Not changed: ") + e.message; } } }, L("להפעיל", "Turn on")));
    }
    for (const s of v.key_fields) {
      const inp = el("input", { type: "password", autocomplete: "off", spellcheck: "false", id: "sec-" + s.field, "aria-label": L(s.he, s.en) });
      kids.push(el("div", { class: "tour-secret" }, el("label", { for: "sec-" + s.field }, L(s.he, s.en)), inp,
        el("button", { type: "button", onclick: async () => {
          try { await post("/api/wizard/secret/" + s.field, { value: inp.value }); inp.value = ""; out.textContent = L("נשמר במאגר הסודות.", "Saved to the secret store."); }
          catch (e) { inp.value = ""; out.textContent = L("לא נשמר: ", "Not saved: ") + e.message; } } }, L("לשמור", "Save"))));
    }
    if (v.connect && !v.key_fields.length && !v.reusable) kids.push(el("button", { type: "button", onclick: async () => {
      try { const r = await post("/api/connect/run", { id: v.connect.id }); out.textContent = L(r.message_he, r.message_en); } catch (e) { out.textContent = e.message; } } },
      L("לפתוח את ההדרכה לחיבור", "Open the guided connection")));
    kids.push(el("button", { type: "button", onclick: async () => {
      out.textContent = L("בודק…", "Checking…");
      try {
        const r = await post(`/api/wizard/topic/${t.id}/test`);
        out.replaceChildren(pill(r.ok ? "ok" : "degraded", r.ok ? L("הבדיקה עברה", "Check passed") : L("הבדיקה לא עברה", "Check failed")),
          r.parts.map((p) => el("div", {}, (p.ok ? "✓ " : "✗ ") + L(p.he, p.en))));
      } catch (e) { out.textContent = e.message; } } }, L("תעשה את זה בשבילי (בדיקה בטוחה)", "Do it for me (safe check)")));
    const c = v.card || {};
    const learn = el("ul", { class: "tour-learn small" },
      (c.say || []).length ? el("li", {}, L("עוד דוגמאות: ", "More examples: "), c.say.map((s) => L(s.he, s.en)).join(" · ")) : null,
      el("li", {}, L("בלוח הבקרה: ", "In the dashboard: "), el("a", { href: t.page }, L("לפתוח את העמוד", "Open the page"))),
      el("li", {}, L("בטרמינל: ", "In a terminal: "), el("code", {}, t.cli)),
      c.guide ? el("li", {}, L("מדריך: ", "Guide: "), el("a", { href: "#/guide/" + c.guide }, L(c.guide_title_he, c.guide_title_en))) : null);
    body.replaceChildren(el("div", { class: "tour-actions" }, kids), out, learn,
      el("div", { class: "small muted" }, L("מפתחות נשמרים רק במאגר הסודות, לעולם לא בקובץ ההתקדמות.", "Keys go only to the secret store, never into the progress file.")));
  }

  // ------------------------------------------------------------------ lists and tools
  function leftList() {
    const d = S.d;
    const rest = d.left.filter((id) => id !== S.cur);
    if (!rest.length) return null;
    return el("details", { class: "tour-left" }, el("summary", {}, L(`עוד נשאר (${rest.length})`, `Still left (${rest.length})`)),
      el("div", { class: "tour-chips" }, rest.map((id) => { const t = byId(id); return el("button", { type: "button", class: "ghost tour-chip", onclick: () => show(id) },
        L(t.he, t.en) + (t.status === "later" ? L(" (אחר כך)", " (later)") : "")); })));
  }

  function doneList() {
    const d = S.d;
    if (!d.done.length) return null;
    const rows = d.done.map(byId).filter(Boolean).map((t) => el("li", {},
      el("span", { class: "tour-mark", "aria-hidden": "true" }, MARK[t.done.how] || "✓"),
      el("button", { type: "button", class: "ghost tour-open", onclick: () => show(t.id) }, L(t.he, t.en)),
      el("span", { class: "small muted" }, L(...(HOW[t.done.how] || HOW.seen))),
      el("button", { type: "button", class: "small", "aria-label": L(`שוב: ${t.he}`, `Again: ${t.en}`), onclick: async () => {
        try { await S.chain; await post(`/api/wizard/topic/${t.id}/again`); S.d = await api("/api/wizard"); show(t.id); } catch (e) { setMsg(document.createTextNode(e.message)); } } },
        L("שוב", "Again"))));
    return el("details", { class: "tour-done" }, el("summary", {}, L(`הושלמו (${d.done.length})`, `Done (${d.done.length})`)), el("ul", {}, rows));
  }

  function tools() {
    const d = S.d, c = counts();
    const out = el("div", { class: "small", role: "status", "aria-live": "polite" });
    const auto = d.automated ? L(`בדיקות אוטומטיות: ${d.automated.passed} עברו, ${d.automated.failed.length} נכשלו`, `Automated checks: ${d.automated.passed} passed, ${d.automated.failed.length} failed`)
      : L("הבדיקות האוטומטיות עוד לא רצו", "The automated checks have not run yet");
    return el("details", { class: "tour-tools" }, el("summary", {}, L("כלים: בדיקות אוטומטיות וגיליון אישור", "Tools: automated checks and sign-off sheet")),
      el("div", { class: "small" }, auto), out,
      el("div", { class: "tour-actions" },
        el("button", { type: "button", onclick: async () => { out.textContent = L("בודק הכול…", "Checking everything…"); try { d.automated = await post("/api/wizard/automated"); paint(); } catch (e) { out.textContent = e.message; } } }, L("להריץ את הבדיקות האוטומטיות", "Run the automated checks")),
        el("button", { type: "button", onclick: async () => { try { await S.chain; const r = await post("/api/wizard/signoff", {}); out.textContent = L(`נכתב ${r.file}: ${r.open.length} שורות פתוחות`, `Written ${r.file}: ${r.open.length} rows open`); } catch (e) { out.textContent = e.message; } } }, L("לכתוב את גיליון האישור", "Write the sign-off sheet")),
        d.mode === "baseline" && !c.left ? el("button", { type: "button", onclick: async () => { await S.chain; await post("/api/wizard/control", { op: "finish_baseline" }); S.d = await api("/api/wizard"); paint(); } }, L("לסיים את הבדיקה הראשונה", "Finish the first full check")) : null,
        el("button", { type: "button", onclick: async () => {
          if (!confirm(L("להחזיר את כל הנושאים לרשימה?", "Put every topic back into the list?"))) return;
          await S.chain; await post("/api/wizard/control", { op: "reset" }); S.d = await api("/api/wizard"); show(S.d.next); } }, L("להתחיל מחדש", "Start over"))),
      el("div", { class: "small muted" }, L("גם בטרמינל: jarvis tour --left, ‏--done, ‏--again. ", "Also in a terminal: jarvis tour --left, --done, --again. "), el("a", { href: "#/addons" }, L("הגדרה ותוספים", "Setup & add-ons"))));
  }

  function paint() {
    const d = S.d, c = counts();
    const t = S.cur ? byId(S.cur) : null;
    const sub = c.left ? L(`נשארו ${c.left} · גרסה ${d.release}`, `${c.left} left · version ${d.release}`)
      : L(`הכול הושלם לגרסה ${d.release}`, `All done for version ${d.release}`);
    const bar = el("div", { class: "tour-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(c.percent),
      "aria-label": L(`התקדמות: ${c.finished} מתוך ${c.total}`, `Progress: ${c.finished} of ${c.total}`) }, el("div", { style: `width:${c.percent}%` }));
    const main = t ? card(t) : el("section", { class: "card tour-card tour-alldone" },
      el("h2", {}, L("✓ אין מה לעשות כאן כרגע", "✓ Nothing left to do here")),
      el("div", {}, L("כל הנושאים של הגרסה הזו הושלמו. נושא שישתנה בגרסה הבאה יחזור לכאן לבד.", "Every topic of this version is done. A topic that changes in a later version comes back here by itself.")));
    render(header(L("הגדרה וסיור", "Setup and tour"), sub), bar,
      el("div", { id: "tour-msg", class: "small tour-msg", role: "status", "aria-live": "polite" }, S.msg),
      main, leftList(), doneList(), tools());
  }

  // Enter = Works, 1/2/3 = Works / Did not work / Later; never while typing, never with a modifier key.
  function onKey(ev) {
    if (!onPage() || !S.d || !S.cur || S.failing || ev.ctrlKey || ev.altKey || ev.metaKey || ev.isComposing) return;
    const tg = ev.target;
    if (tg && tg.closest && tg.closest("input, textarea, select, [contenteditable], details.tour-more, details.tour-tools")) return;
    const typing = tg && tg.closest && tg.closest("button, a, summary");
    const k = ev.key;
    if (k === "1" || (k === "Enter" && !typing)) { ev.preventDefault(); answer(S.cur, "works"); }
    else if (k === "2") { ev.preventDefault(); document.getElementById("tour-fails").click(); }
    else if (k === "3") { ev.preventDefault(); answer(S.cur, "later"); }
  }
  let keysOn = false;

  async function pageTour(arg, refresh) {
    if (refresh === true && S.d && document.getElementById("tour-title")) return;   // a stray live refresh never wipes a half-typed note
    S.d = await api("/api/wizard");
    S.msg = null;
    if (!keysOn) { document.addEventListener("keydown", onKey); keysOn = true; }
    const known = arg && S.d.topics.some((t) => t.id === arg);
    show(known ? arg : S.d.next);
  }
  PAGES.setup = pageTour;
})();
