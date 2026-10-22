// Setup and tour (#/setup, #/setup/<topic>): ONE wizard, topic by topic: SET UP -> TEST -> LEARN (owner issues #58/#60/#61).
// Server side: jarves/wizard.py (GET /api/wizard, /api/wizard/topic/<id>, POST .../choice|switch|test|result|learned,
// /api/wizard/secret/<field>, /api/wizard/automated, /api/wizard/signoff). Secrets go only through hidden inputs to the
// secret store. Uses the app.js helpers (el, api, render, header, pill, route, tx). Text goes in via textContent only.
"use strict";

(function () {
  const L = (he, en) => tx(he, en);
  const T = (o, k) => txo(o, k);
  const MARK = { done: "✓", skipped: "–", later: "…", setup: "◐", todo: "○" };
  const STATUS = { done: ["הושלם", "done"], skipped: ["דולג", "skipped"], later: ["אחר כך", "later"], setup: ["הוגדר, צריך בדיקה", "set up, needs a test"], todo: ["לא הוגדר", "not set"] };
  let listOpen = false;

  async function post(path, body) { return api(path, { method: "POST", body: JSON.stringify(body || {}) }); }

  function topicList(d, cur) {
    const items = d.topics.map((t) => el("li", {}, el("button", { type: "button", class: "ghost", "aria-current": t.id === cur ? "true" : null,
      onclick: () => { listOpen = false; location.hash = "#/setup/" + t.id; } },
      el("span", { class: "tour-mark", "aria-hidden": "true" }, MARK[t.status]), el("span", {}, L(t.he, t.en)),
      el("span", { class: "muted small" }, L(...STATUS[t.status])))));
    const more = d.collapsed.length ? el("li", {}, el("details", {}, el("summary", { class: "small muted" },
      L(`${d.collapsed.length} נושאים שכבר אומתו`, `${d.collapsed.length} topics already verified`)),
      d.collapsed.map((c) => el("button", { type: "button", class: "ghost", onclick: () => { location.hash = "#/setup/" + c.id; } }, L(c.he, c.en))))) : null;
    return el("ul", { class: "tour-list" + (listOpen ? "" : " closed"), id: "tour-list", "aria-label": L("נושאים", "Topics") }, items, more);
  }

  function setupStage(t, refresh) {
    const out = el("div", { class: "tour-out small", role: "status", "aria-live": "polite" });
    const panel = el("div", {});
    const choose = async (c) => { try { await post(`/api/wizard/topic/${t.id}/choice`, { choice: c }); refresh(c === "done" ? null : "next"); } catch (e) { out.textContent = e.message; } };
    const btns = el("div", { class: "tour-actions" },
      el("button", { type: "button", class: "primary", onclick: () => open() }, L("להגדיר", "Set up")),
      el("button", { type: "button", onclick: () => choose("skipped") }, L("לדלג", "Skip")),
      el("button", { type: "button", onclick: () => choose("later") }, L("אחר כך", "Later")));
    function open() {
      const kids = [];
      if (t.features.length) {
        kids.push(el("div", { class: "small" }, L("מתגים: ", "Switches: "), t.features.map((f) => `${T(f, "title")} (${f.enabled ? L("פועל", "on") : L("כבוי", "off")})`).join(", ")));
        kids.push(el("button", { type: "button", class: "primary", onclick: async () => {
          if (t.high && !confirm(L("זו יכולת רגישה. להפעיל?", "This is a sensitive feature. Turn it on?"))) return;
          try { await post(`/api/wizard/topic/${t.id}/switch`, { confirmed: true }); await post(`/api/wizard/topic/${t.id}/choice`, { choice: "done" }); refresh(); }
          catch (e) { out.textContent = L("לא שונה: ", "Not changed: ") + e.message; } } }, L("להפעיל את המתגים", "Turn the switches on")));
      }
      for (const s of t.secrets) {
        const inp = el("input", { type: "password", autocomplete: "off", spellcheck: "false", id: "sec-" + s.field, "aria-label": L(s.he, s.en) });
        kids.push(el("div", { class: "tour-secret" }, el("label", { for: "sec-" + s.field }, L(s.he, s.en)), inp,
          el("button", { type: "button", onclick: async () => {
            try { await post("/api/wizard/secret/" + s.field, { value: inp.value }); inp.value = ""; await post(`/api/wizard/topic/${t.id}/choice`, { choice: "done" }); refresh(); }
            catch (e) { inp.value = ""; out.textContent = L("לא נשמר: ", "Not saved: ") + e.message; } } }, L("לשמור", "Save"))));
      }
      if (t.connect && !t.secrets.length) {
        kids.push(el("button", { type: "button", onclick: async () => {
          try { const r = await post("/api/connect/run"); out.textContent = L(r.message_he, r.message_en); } catch (e) { out.textContent = e.message; } } },
          L("לפתוח את ההדרכה לחיבור", "Open the guided connection")));
        kids.push(el("button", { type: "button", onclick: () => choose("done") }, L("סיימתי", "I finished")));
      }
      if (!t.features.length && !t.secrets.length && !t.connect) kids.push(el("button", { type: "button", onclick: () => choose("done") }, L("אין מה להגדיר, להמשיך", "Nothing to set up, continue")));
      kids.push(el("div", { class: "small muted" }, L("מפתחות וטוקנים נשמרים רק במאגר הסודות, לעולם לא בקובץ ההתקדמות.", "Keys and tokens go only to the secret store, never into the progress file.")));
      panel.replaceChildren(...kids);
    }
    const reuse = t.reusable ? el("div", { class: "tour-actions" }, pill("ok", L("נמצא חיבור שעובד", "A working connection was found")), " ",
      L(t.connect.detail_he, t.connect.detail_en), el("button", { type: "button", class: "primary", onclick: () => choose("done") }, L("להשתמש בו", "Keep it"))) : null;
    return el("section", { class: "card tour-stage" }, el("h2", {}, L("1. הגדרה", "1. Set up")),
      el("div", {}, L(t.gives_he, t.gives_en)), t.setup ? el("div", { class: "small" }, pill(t.setup === "done" ? "ok" : "degraded", L(...STATUS[t.status]))) : null,
      reuse, btns, panel, out);
  }

  function testStage(t, refresh) {
    const out = el("div", { class: "tour-out small", role: "status", "aria-live": "polite" });
    const note = el("input", { type: "text", maxlength: "300", "aria-label": L("מה לא עבד", "What did not work"), placeholder: L("מה לא עבד? (לא חובה)", "What did not work? (optional)") });
    const record = async (r) => {
      try {
        const x = await post(`/api/wizard/topic/${t.id}/result`, { result: r, note: note.value });
        if (x.bug) { out.textContent = L("נרשם דיווח באג עם אבחון בלי תוכן.", "A bug report with content-free diagnostics was added."); return; }
        refresh(r === "fails" ? null : "next");
      } catch (e) { out.textContent = e.message; }
    };
    const run = el("button", { type: "button", onclick: async () => {
      out.textContent = L("בודק...", "Checking...");
      try {
        const r = await post(`/api/wizard/topic/${t.id}/test`);
        out.replaceChildren(pill(r.ok ? "ok" : "degraded", r.ok ? L("הבדיקה עברה", "Check passed") : L("הבדיקה לא עברה", "Check failed")),
          r.parts.map((p) => el("div", {}, (p.ok ? "✓ " : "✗ ") + L(p.he, p.en))));
      } catch (e) { out.textContent = e.message; } } }, L("תעשה את זה בשבילי", "Do it for me"));
    const names = { works: L("עובד", "Works"), fails: L("לא עובד", "Does not work"), skip: L("דולג", "Skipped") };
    return el("section", { class: "card tour-stage" }, el("h2", {}, L("2. בדיקה", "2. Test")),
      el("div", { class: "small muted" }, L("בדיקה בטוחה במחשב בלבד. אתם מחליטים אם זה עובד.", "A safe local check only. You decide whether it works.")),
      el("div", { class: "tour-actions" }, run), out,
      el("div", { class: "tour-actions" },
        el("button", { type: "button", class: "primary", onclick: () => record("works") }, L("עובד", "Works")),
        el("button", { type: "button", onclick: () => record("fails") }, L("לא עובד", "Does not work")),
        el("button", { type: "button", onclick: () => record("skip") }, L("לדלג", "Skip"))), note,
      t.test ? el("div", { class: "small" }, L("תוצאה שנרשמה: ", "Recorded: "), names[t.test],
        t.verified_in ? L(` (אומת בגרסה ${t.verified_in}, ${t.verified_on})`, ` (verified in ${t.verified_in} on ${t.verified_on})`) : "") : null);
  }

  function learnStage(t, refresh) {
    const c = t.card;
    return el("section", { class: "card tour-stage tour-card" }, el("h2", {}, L("3. להכיר (30 שניות)", "3. Learn (30 seconds)")),
      el("dl", {},
        el("dt", {}, L("מה זה עושה", "What it does")), el("dd", {}, L(c.gives_he, c.gives_en)),
        c.say.length ? [el("dt", {}, L("מה להגיד", "What to say")), el("dd", {}, el("ul", { class: "say" }, c.say.map((s) => el("li", {}, L(s.he, s.en)))))] : null,
        c.expect_en ? [el("dt", {}, L("מה אמור לקרות", "What to expect")), el("dd", {}, L(c.expect_he, c.expect_en))] : null,
        el("dt", {}, L("איפה בלוח הבקרה", "Where in the dashboard")), el("dd", {}, el("a", { href: c.page }, c.page)),
        el("dt", {}, L("בשורת הפקודה", "On the command line")), el("dd", {}, el("code", {}, c.cli)),
        c.guide ? [el("dt", {}, L("מדריך", "Guide")), el("dd", {}, el("a", { href: "#/guide/" + c.guide }, L(c.guide_title_he, c.guide_title_en)))] : null),
      el("div", { class: "tour-actions" },
        el("a", { href: c.page }, L("לנסות עכשיו", "Try it now")),
        el("button", { type: "button", class: "primary", onclick: async () => { await post(`/api/wizard/topic/${t.id}/learned`); refresh("next"); } }, L("הבנתי, הלאה", "Got it, next"))));
  }

  async function pageTour(arg) {
    const d = await api("/api/wizard");
    const all = d.topics.concat(d.collapsed);
    const cur = (arg && all.some((t) => t.id === arg)) ? arg : (d.next || (d.topics[0] && d.topics[0].id));
    const refresh = async (next) => {
      if (next === "next") {
        const nd = await api("/api/wizard");
        const i = nd.topics.findIndex((t) => t.id === cur);
        const rest = nd.topics.slice(i + 1);
        const nx = rest.find((t) => t.status !== "done" && t.status !== "skipped") || rest[0];
        if (nx) { location.hash = "#/setup/" + nx.id; return; }
      }
      route(true);
    };
    const autoOut = el("div", { class: "small", role: "status", "aria-live": "polite" });
    const auto = d.automated ? L(`בדיקות אוטומטיות: ${d.automated.passed} עברו, ${d.automated.failed.length} נכשלו`, `Automated checks: ${d.automated.passed} passed, ${d.automated.failed.length} failed`) : L("בדיקות אוטומטיות עוד לא רצו", "Automated checks have not run yet");
    const left = el("div", {}, el("button", { type: "button", class: "ghost tour-list-toggle", "aria-controls": "tour-list", "aria-expanded": listOpen ? "true" : "false",
      onclick: () => { listOpen = !listOpen; document.getElementById("tour-list").classList.toggle("closed", !listOpen); } }, L("רשימת נושאים", "Topic list")), topicList(d, cur));
    const t = cur ? await api("/api/wizard/topic/" + cur) : null;
    const right = el("div", {}, t ? [el("h2", {}, L(t.he, t.en) + (t.optional ? L(" (לא חובה)", " (optional)") : "")),
      setupStage(t, refresh), testStage(t, refresh), learnStage(t, refresh)] : el("div", { class: "card" }, L("אין נושאים.", "No topics.")));
    const bar = el("div", {}, el("div", { class: "small" }, L(`${d.finished} מתוך ${d.total} נושאים (${d.percent}%)`, `${d.finished} of ${d.total} topics (${d.percent}%)`)),
      el("div", { class: "tour-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(d.percent), "aria-label": L("התקדמות", "Progress") }, el("div", { style: `width:${d.percent}%` })));
    const foot = el("div", { class: "card" },
      el("div", { class: "small" }, auto), autoOut,
      el("div", { class: "tour-actions" },
        el("button", { type: "button", onclick: async () => { autoOut.textContent = L("בודק הכול...", "Checking everything..."); try { await post("/api/wizard/automated"); route(true); } catch (e) { autoOut.textContent = e.message; } } }, L("להריץ את הבדיקות האוטומטיות", "Run the automated checks")),
        el("button", { type: "button", onclick: async () => { try { const r = await post("/api/wizard/signoff", {}); autoOut.textContent = L(`נכתב ${r.file}: ${r.open.length} שורות פתוחות`, `Written ${r.file}: ${r.open.length} rows open`); } catch (e) { autoOut.textContent = e.message; } } }, L("לכתוב את גיליון האישור", "Write the sign-off sheet")),
        d.mode === "baseline" && d.finished === d.total ? el("button", { type: "button", class: "primary", onclick: async () => { await post("/api/wizard/control", { op: "finish_baseline" }); route(true); } }, L("לסיים את הבדיקה הראשונה", "Finish the first full check")) : null),
      el("div", { class: "small muted" }, L("אפשר לעצור ולהמשיך בכל זמן; ההתקדמות נשמרת. ", "You can stop and resume any time; progress is saved. "), el("a", { href: "#/addons" }, L("הגדרה ותוספים", "Setup & add-ons"))));
    render(header(L("הגדרה וסיור", "Setup and tour"), d.mode === "baseline"
      ? L("בדיקה ראשונה של הכול, נושא אחרי נושא. אפשר לדלג על כל נושא.", "The first full check, topic by topic. Every topic can be skipped.")
      : L("מה חדש ומה השתנה, ובדיקה קצרה כללית.", "What is new and changed, plus a short overall check.")), bar,
      el("div", { class: "tour" }, left, right), foot);
  }
  PAGES.setup = pageTour;
})();
