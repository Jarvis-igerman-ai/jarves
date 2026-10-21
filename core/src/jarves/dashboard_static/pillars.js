// V5 pillars (software slice): the Goals page and the Learned preferences page. Same stores as the voice tools and
// the CLI (`jarvis goals`, `jarvis prefs`). User text (goal titles, steps, preferences, the owner's own words) is
// shown as data with dir="auto" and is never translated. Uses the helpers of app.js (el, api, render, header, pill,
// route) and registers in PAGES (pages.js).
"use strict";

(function () {
  const msgSpan = () => el("span", { class: "muted small", role: "status" });
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  async function act(msg, fn) {
    try { await fn(); route(true); } catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + JL.t(e.message); }
  }
  const WAITING = () => tx("ממתין לאישור על המסך שלך", "waiting for approval on your screen");
  const STATUS = { active: ["פעיל", "Active"], paused: ["מושהה", "Paused"], done: ["הושלם", "Completed"], dropped: ["בוטל", "Dropped"] };
  const stText = (s) => { const p = STATUS[s]; return p ? tx(p[0], p[1]) : String(s); };
  const bar = (pct) => el("div", { class: "goal-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct),
    "aria-label": tx("התקדמות", "Progress"), style: "height:8px;background:var(--line,#ccc);border-radius:4px;overflow:hidden;min-width:120px;flex:1" },
    el("div", { style: "height:100%;width:" + pct + "%;background:var(--ok,#2a7)" }));

  // ------------------------------------------------------------- Goals
  async function pageGoals() {
    const d = await api("/api/goals");
    const msg = msgSpan();
    const title = el("input", { type: "text", dir: "auto", maxlength: "120", style: "min-width:220px;flex:1", "aria-label": tx("הגדרת יעד", "New goal"),
      placeholder: tx("יעד חדש, למשל: לרוץ 10 ק\"מ", "New goal, for example: run a 10k") });
    const by = el("input", { type: "date", "aria-label": tx("תאריך יעד", "Target date") });
    const steps = el("input", { type: "text", dir: "auto", style: "min-width:220px;flex:1", "aria-label": tx("צעדים", "Steps"),
      placeholder: tx("צעדים, מופרדים בפסיק (לא חובה)", "Steps, comma separated (optional)") });
    const add = () => title.value.trim() && act(msg, () => post("/api/goals", { title: title.value, target_date: by.value, steps: steps.value }));
    title.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
    const checkin = el("div", { class: "card", hidden: true, "aria-live": "polite" });
    const doCheckin = async () => {
      try {
        const c = await post("/api/goals/checkin");
        checkin.hidden = false;
        checkin.replaceChildren(el("h2", {}, tx("סיכום התקדמות", "Check-in")),
          c.goals.length ? c.goals.map((g) => el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" },
            el("b", { dir: "auto" }, g.title), el("span", {}, g.progress + "%"),
            g.overdue ? pill("fail", tx("עבר התאריך", "Past its date")) : null,
            g.stalled ? pill("degraded", tx("בלי התקדמות", "No movement")) : null,
            g.next_steps.length ? el("span", { class: "muted small", dir: "auto" }, tx("הבא: ", "Next: ") + g.next_steps[0]) : null))
            : el("div", { class: "empty" }, tx("אין יעדים פעילים.", "No active goals.")));
      } catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + JL.t(e.message); }
    };
    const card = (g) => {
      const open = g.status === "active" || g.status === "paused";
      const stepEl = (s) => el("div", { class: "row", style: "gap:8px" },
        el("label", { class: "check" }, el("input", { type: "checkbox", checked: s.done,
          onchange: (e) => act(msg, () => post("/api/goals/op", { op: "step_set", step_id: s.id, done: e.target.checked })) }),
          el("span", { dir: "auto" }, s.text)),
        s.outward ? pill("degraded", tx("נוגע בעולם החיצון", "Outward-facing")) : null, el("span", { class: "spacer" }),
        el("button", { type: "button", class: "ghost", "aria-label": tx("הסרת צעד", "Remove step") + ": " + s.text,
          onclick: () => act(msg, () => post("/api/goals/op", { op: "step_remove", step_id: s.id })) }, tx("הסרה", "Remove")));
      const newStep = el("input", { type: "text", dir: "auto", maxlength: "160", "aria-label": tx("צעד חדש", "New step"), placeholder: tx("צעד חדש", "New step") });
      const outward = el("input", { type: "checkbox", "aria-label": tx("נוגע באנשים אחרים או בעולם החיצון", "Involves other people or the outside world") });
      const addStep = () => newStep.value.trim() && act(msg, () => post("/api/goals/op", { op: "step_add", id: g.id, text: newStep.value, outward: outward.checked }));
      newStep.addEventListener("keydown", (e) => { if (e.key === "Enter") addStep(); });
      const setStatus = (s) => () => act(msg, () => post("/api/goals/op", { op: "status", id: g.id, status: s }));
      const rename = () => {
        const t = window.prompt(tx("שם היעד", "Goal title"), g.title);
        if (t && t.trim()) act(msg, () => post("/api/goals/op", { op: "update", id: g.id, title: t }));
      };
      return el("div", { class: "card" },
        el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, el("h2", { dir: "auto", style: "margin:0" }, g.title),
          pill(g.status === "done" ? "ok" : "plain", stText(g.status)), el("span", { class: "spacer" }),
          g.target_date ? el("span", { class: "muted small" }, tx("עד ", "By ") + g.target_date) : null),
        el("div", { class: "row", style: "gap:8px;margin:8px 0" }, bar(g.progress), el("span", {}, g.progress + "%")),
        g.details ? el("p", { dir: "auto", class: "muted" }, g.details) : null,
        g.steps.map(stepEl),
        open ? el("div", { class: "row", style: "gap:8px;flex-wrap:wrap;margin-top:8px" }, newStep,
          el("label", { class: "check" }, outward, el("span", { class: "small" }, tx("נוגע בעולם החיצון", "Outward-facing"))),
          el("button", { type: "button", onclick: addStep }, tx("הוספת צעד", "Add step"))) : null,
        el("div", { class: "row", style: "gap:8px;flex-wrap:wrap;margin-top:8px" },
          open && !g.steps.length ? el("label", {}, el("span", { class: "small" }, tx("אחוז התקדמות", "Percent")), " ",
            el("input", { type: "number", min: "0", max: "100", value: String(g.percent), style: "width:5em",
              onchange: (e) => act(msg, () => post("/api/goals/op", { op: "update", id: g.id, percent: Number(e.target.value) })) })) : null,
          el("button", { type: "button", class: "ghost", onclick: rename }, tx("שינוי שם", "Rename")),
          g.status === "active" ? el("button", { type: "button", class: "ghost", onclick: setStatus("paused") }, tx("השהיה", "Pause")) : null,
          g.status !== "active" ? el("button", { type: "button", class: "ghost", onclick: setStatus("active") }, tx("חידוש", "Resume")) : null,
          open ? el("button", { type: "button", onclick: setStatus("done") }, tx("סימון כהושלם", "Mark completed")) : null,
          open ? el("button", { type: "button", class: "ghost", onclick: setStatus("dropped") }, tx("ביטול היעד", "Drop goal")) : null,
          el("span", { class: "spacer" }),
          el("button", { type: "button", class: "ghost", onclick: () => {         // the SERVER asks on the approval popup
            msg.textContent = WAITING();
            act(msg, () => post("/api/goals/op", { op: "delete", id: g.id }));
          } }, tx("מחיקה", "Delete"))));
    };
    render(header(tx("יעדים", "Goals"), tx("מגדירים יעד, עוקבים אחרי הצעדים. JARVES לא מבצע צעדים בעצמו.",
        "Set a goal and track its steps. JARVES does not carry out steps on its own.")),
      el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, title, by, steps,
        el("button", { type: "button", class: "primary", onclick: add }, tx("הוספת יעד", "Add goal")),
        el("button", { type: "button", class: "ghost", onclick: doCheckin }, tx("סיכום התקדמות", "Check-in")), msg)),
      checkin,
      d.goals.length ? d.goals.map(card) : el("div", { class: "empty" }, tx("עוד אין יעדים.", "No goals yet.")));
  }

  // ------------------------------------------------------------- Learned preferences
  async function pagePreferences() {
    const d = await api("/api/prefs");
    const msg = msgSpan();
    const op = (id, o, extra) => act(msg, () => post("/api/prefs/op", Object.assign({ op: o, id: id }, extra || {})));
    const text = el("input", { type: "text", dir: "auto", maxlength: "200", style: "min-width:260px;flex:1", "aria-label": tx("העדפה חדשה", "New preference"),
      placeholder: tx("העדפה שתרצה שאזכור", "A preference you want remembered") });
    const topic = el("select", { "aria-label": tx("נושא", "Topic") }, d.topics.map((t) => el("option", { value: t }, topicText(t))));
    const add = () => text.value.trim() && act(msg, () => post("/api/prefs", { text: text.value, topic: topic.value }));
    text.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
    const edit = (p) => () => {
      const t = window.prompt(tx("ניסוח ההעדפה", "Preference wording"), p.text);
      if (t && t.trim()) op(p.id, "edit", { text: t });
    };
    const row = (p, buttons) => el("div", { class: "card" },
      el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, el("b", { dir: "auto" }, p.text), pill("plain", topicText(p.topic)),
        p.stale ? pill("degraded", tx("לא אושרה מחדש זמן רב", "Not reaffirmed for a long time")) : null),
      p.evidence ? el("div", { class: "muted small", dir: "auto" }, tx("מה שאמרת: ", "What you said: ") + p.evidence) : null,
      el("div", { class: "row", style: "gap:8px;flex-wrap:wrap;margin-top:6px" }, buttons));
    const btn = (label, fn, cls) => el("button", { type: "button", class: cls || "ghost", onclick: fn }, label);
    const proposed = d.proposed.map((p) => row(p, [btn(tx("אישור, לזכור", "Confirm, remember it"), () => op(p.id, "confirm"), "primary"),
      btn(tx("עריכה", "Edit"), edit(p)), btn(tx("לא לזכור", "Do not remember"), () => op(p.id, "decline"))]));
    const items = d.items.map((p) => row(p, [btn(tx("עריכה", "Edit"), edit(p)),
      p.stale ? btn(tx("אישור מחדש", "Reaffirm"), () => op(p.id, "reaffirm"), "primary") : null,
      btn(tx("הסרה", "Remove"), () => op(p.id, "remove"))]));
    const declined = d.declined.map((p) => row(p, [btn(tx("הסרה", "Remove"), () => op(p.id, "remove"))]));
    render(header(tx("העדפות נלמדות", "Learned preferences"), tx("רק מה שאמרת ואישרת. הכול גלוי, ניתן לעריכה ולהסרה.",
        "Only what you said and confirmed. Everything is visible, editable and removable.")),
      el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, text, topic,
        el("button", { type: "button", class: "primary", onclick: add }, tx("שמירה", "Save")), msg)),
      el("h2", {}, tx("ממתינות לאישור שלך", "Waiting for your confirmation")),
      proposed.length ? proposed : el("div", { class: "empty" }, tx("אין הצעות ממתינות.", "No pending suggestions.")),
      el("h2", {}, tx("נזכרות", "Remembered")),
      items.length ? items : el("div", { class: "empty" }, tx("עוד לא נשמרו העדפות.", "No preferences saved yet.")),
      declined.length ? [el("h2", {}, tx("נדחו (כדי לא לשאול שוב)", "Declined (so it is not asked again)")), declined] : null);
  }
  const TOPICS = { voice: ["קול", "Voice"], schedule: ["לוח זמנים", "Schedule"], food: ["אוכל", "Food"], music: ["מוזיקה", "Music"],
    communication: ["תקשורת", "Communication"], other: ["אחר", "Other"] };
  function topicText(t) { const p = TOPICS[t] || TOPICS.other; return tx(p[0], p[1]); }

  Object.assign(PAGES, { goals: pageGoals, preferences: pagePreferences });
})();
