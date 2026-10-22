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
      el("div", { class: "card" },
        el("label", { class: "check" }, el("input", { type: "checkbox", checked: d.in_prompt,
          onchange: (e) => act(msg, () => api("/api/features/prefs_in_prompt", { method: "POST", body: JSON.stringify({ enabled: e.target.checked }) })) }),
          el("span", {}, tx("להשתמש בהעדפות המאושרות בכל שיחה", "Use my confirmed preferences in every conversation"))),
        el("p", { class: "muted small" }, tx("רק העדפות שאישרת, בגודל קטן וקבוע. תקף מהשיחה הבאה. כך זה נשלח:", "Only preferences you confirmed, within a small fixed size. Applies from the next conversation. This is what is sent:")),
        el("pre", { dir: "auto", class: "small" }, d.prompt_text || tx("(אין מה לשלוח)", "(nothing to send)"))),
      el("h2", {}, tx("ממתינות לאישור שלך", "Waiting for your confirmation")),
      proposed.length ? proposed : el("div", { class: "empty" }, tx("אין הצעות ממתינות.", "No pending suggestions.")),
      el("h2", {}, tx("נזכרות", "Remembered")),
      items.length ? items : el("div", { class: "empty" }, tx("עוד לא נשמרו העדפות.", "No preferences saved yet.")),
      declined.length ? [el("h2", {}, tx("נדחו (כדי לא לשאול שוב)", "Declined (so it is not asked again)")), declined] : null);
  }
  const TOPICS = { voice: ["קול", "Voice"], schedule: ["לוח זמנים", "Schedule"], food: ["אוכל", "Food"], music: ["מוזיקה", "Music"],
    communication: ["תקשורת", "Communication"], other: ["אחר", "Other"] };
  function topicText(t) { const p = TOPICS[t] || TOPICS.other; return tx(p[0], p[1]); }

  // ------------------------------------------------------------- Proactive offers
  async function pageProactive() {
    const d = await api("/api/proactive");
    const msg = msgSpan();
    const MODE = { on_request: tx("רק לפי בקשה (ברירת מחדל)", "Only when I ask (default)"), in_conversation: tx("הצעה אחת בתוך שיחה שכבר התחלתי", "One offer inside a conversation I already started") };
    const mode = el("select", { "aria-label": tx("מצב", "Mode"), onchange: (e) => act(msg, () => post("/api/proactive/op", { op: "mode", mode: e.target.value })) },
      d.modes.map((m) => el("option", { value: m, selected: m === d.mode }, MODE[m])));
    render(header(tx("הצעות יזומות", "Proactive offers"), tx("JARVES לא מדבר מעצמו מחוץ לשיחה ולא בשעות שקט (22:00-07:00).",
        "JARVES never speaks on its own outside a conversation, and never in quiet hours (22:00-07:00).")),
      el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, el("label", {}, tx("מצב: ", "Mode: "), mode), msg,
        d.quiet_now ? pill("plain", tx("שעות שקט עכשיו", "Quiet hours now")) : null)),
      d.items.length ? d.items.map((i) => el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" },
        el("b", { dir: "auto" }, i.text), pill("plain", i.kind === "goal_overdue" ? tx("עבר התאריך", "Past its date") : tx("בלי התקדמות", "No movement")),
        i.state === "offered" ? pill("degraded", tx("הוצע בשיחה", "Offered in a conversation")) : null, el("span", { class: "spacer" }),
        el("button", { type: "button", class: "ghost", onclick: () => act(msg, () => post("/api/proactive/op", { op: "dismiss", id: i.id })) }, tx("סגירה", "Dismiss")))))
        : el("div", { class: "empty" }, tx("אין מה להציע כרגע.", "Nothing to mention right now.")));
  }

  // ------------------------------------------------------------- Development drafts (local only)
  async function pageDevDrafts() {
    const d = await api("/api/dev/drafts");
    const msg = msgSpan();
    const title = el("input", { type: "text", dir: "auto", maxlength: "100", style: "min-width:220px;flex:1", "aria-label": tx("כותרת", "Title"), placeholder: tx("רעיון או כרטיס חדש", "New idea or ticket") });
    const body = el("textarea", { dir: "auto", maxlength: "1500", rows: "3", style: "width:100%", "aria-label": tx("פרטים", "Details"), placeholder: tx("פרטים (לא חובה)", "Details (optional)") });
    const kind = el("select", { "aria-label": tx("סוג", "Kind") }, d.kinds.map((k) => el("option", { value: k }, k === "ticket" ? tx("כרטיס", "Ticket") : tx("הצעה", "Suggestion"))));
    const add = () => title.value.trim() && act(msg, () => post("/api/dev/drafts", { title: title.value, body: body.value, kind: kind.value }));
    const file = (i) => async () => {
      msg.textContent = WAITING();
      try { const r = await post("/api/health/ticket/file", { id: i.id }); msg.textContent = r.status === "draft" ? JL.t(r.reason || "") : r.status; route(true); }
      catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + JL.t(e.message); }
    };
    render(header(tx("טיוטות פיתוח", "Development drafts"), tx("רעיונות וכרטיסים נשמרים כאן, מקומית בלבד. שום דבר לא נשלח ל-GitHub בלי אישור שלך על המסך.",
        "Ideas and tickets are kept here, locally only. Nothing is posted to GitHub without your approval on the screen.")),
      el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, title, kind, el("button", { type: "button", class: "primary", onclick: add }, tx("שמירה כטיוטה", "Save as draft")), msg), body),
      d.drafts.length ? d.drafts.map((i) => el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" },
        el("b", { dir: "auto" }, i.title), pill("plain", i.severity === "ticket" ? tx("כרטיס", "Ticket") : tx("הצעה", "Suggestion")),
        pill(i.status === "draft" ? "plain" : "ok", i.status === "draft" ? tx("טיוטה מקומית", "Local draft") : i.status), el("span", { class: "spacer" }),
        i.status === "draft" && d.repo_set ? el("button", { type: "button", class: "ghost", onclick: file(i) }, tx("פרסום ב-GitHub (מבקש אישור)", "Post to GitHub (asks first)")) : null,
        el("button", { type: "button", class: "ghost", onclick: () => act(msg, () => post("/api/dev/op", { op: "discard", id: i.id })) }, tx("מחיקה", "Discard"))),
        el("pre", { dir: "auto", class: "small", style: "white-space:pre-wrap" }, i.body)))
        : el("div", { class: "empty" }, tx("אין טיוטות.", "No drafts.")),
      d.repo_set ? null : el("p", { class: "muted small" }, tx("לא הוגדר מאגר פרטי (health.ticket_repo), לכן אי אפשר לפרסם; הטיוטות נשארות מקומיות.",
        "No private repository is set (health.ticket_repo), so posting is not possible; drafts stay local.")));
  }

  // ------------------------------------------------------------- Development status (read-only; changes ask first)
  async function pageDevelopment() {
    const d = await api("/api/dev/status");
    const msg = msgSpan();
    const STATE = { ci_failed: tx("CI נכשל", "CI failed"), ci_running: tx("CI רץ", "CI running"), green: tx("ירוק וממתין", "Green, waiting"),
      draft: tx("טיוטה", "Draft"), no_ci: tx("אין CI", "No CI yet"), open: tx("פתוח", "Open"), in_progress: tx("בעבודה", "In progress"), stuck: tx("תקוע", "Stuck") };
    const BAD = { ci_failed: 1, stuck: 1 };
    const ask = (p) => async () => {
      msg.textContent = WAITING();
      try { const r = await post("/api/dev/status/apply", { id: p.id }); msg.textContent = r.status === "done" ? tx("בוצע", "Done") : tx("לא בוצע (לא אושר)", "Not done (not approved)"); route(true); }
      catch (e) { msg.textContent = tx("נכשל: ", "Failed: ") + JL.t(e.message); }
    };
    const row = (i) => el("tr", {}, el("td", { dir: "auto" }, (i.type === "pr" ? "PR #" : tx("נושא #", "Issue #")) + i.number),
      el("td", { dir: "auto" }, i.title),
      el("td", {}, pill(BAD[i.state] ? "degraded" : i.state === "green" ? "ok" : "plain", STATE[i.state] || i.state)),
      el("td", { dir: "auto" }, i.blocker || "-"),
      el("td", { dir: "auto" }, tx(i.suggestion.text_he, i.suggestion.text_en),
        i.proposals.map((p) => el("div", {}, el("button", { type: "button", class: "ghost", title: p.command, onclick: ask(p) }, tx(p.text_he, p.text_en) + " (" + tx("מבקש אישור", "asks first") + ")"),
          el("code", { dir: "ltr", class: "small" }, " " + p.command)))));
    render(header(tx("פיתוח", "Development"), tx("מה הסוכנים, בקשות המשיכה וה-CI של JARVES עושים. הדף קורא בלבד; כל שינוי ב-GitHub מציג את הפקודה המדויקת ומבקש אישור על המסך.",
        "What the agents, pull requests and CI of JARVES are doing. This page only reads; any change on GitHub shows the exact command and asks on the screen.")),
      el("div", { class: "card" }, el("b", { dir: "auto" }, tx(d.sentence_he, d.sentence_en)), " ", msg,
        d.notes.length ? el("p", { class: "muted small" }, tx("הערות: ", "Notes: ") + d.notes.join(", ")) : null),
      d.items.length ? el("div", { class: "card", style: "overflow-x:auto" }, el("table", { class: "table" },
        el("thead", {}, el("tr", {}, ["", tx("כותרת", "Title"), tx("מצב", "State"), tx("חסם", "Blocker"), tx("הצעה לצעד הבא", "Suggested next step")].map((h) => el("th", {}, h)))),
        el("tbody", {}, d.items.map(row)))) : el("div", { class: "empty" }, tx("אין בקשות משיכה או נושאים פתוחים (או ש-GitHub לא נקרא).", "No open pull requests or issues (or GitHub could not be read).")),
      el("h3", {}, tx("סוכנים", "Agents")),
      d.agents.length ? d.agents.map((a) => el("div", { class: "card" }, el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" },
        el("b", { dir: "ltr" }, a.provider + (a.project ? " / " + a.project : "")), el("span", { dir: "ltr", class: "muted small" }, a.branch),
        pill(a.health === "stuck" || a.health === "idle_long" || a.health === "blocked" ? "degraded" : a.health === "finished" ? "ok" : "plain", a.health),
        el("span", { class: "muted small" }, Math.round(a.age_s / 60) + " " + tx("דק'", "min")))))
        : el("div", { class: "empty" }, tx("אין סוכנים פעילים.", "No active agents.")));
  }

  // ------------------------------------------------------------- Goal plans (the executor)
  const PLAN_STATE = { draft: ["טיוטה, ממתינה לאישורך", "Draft, waiting for your approval"], ready: ["מאושרת, מתחילה", "Approved, starting"],
    running: ["רצה", "Running"], paused: ["מושהית", "Paused"], blocked: ["חסומה, צריכה אותך", "Blocked, needs you"], done: ["הושלמה", "Done"],
    failed: ["נכשלה", "Failed"], cancelled: ["בוטלה", "Cancelled"] };
  const STEP_STATE = { pending: ["ממתין", "Pending"], running: ["רץ", "Running"], done: ["בוצע", "Done"], failed: ["נכשל", "Failed"],
    blocked: ["חסום", "Blocked"], unknown: ["לא ברור אם בוצע", "May have run"], skipped: ["דולג", "Skipped"] };
  const pillFor = (st) => st === "done" ? "ok" : (st === "failed" || st === "blocked" || st === "unknown" ? "fail" : "plain");
  async function pageGoalPlans() {
    const d = await api("/api/goal-plans");
    const msg = msgSpan();
    const op = (o, id, extra) => () => act(msg, () => post("/api/goal-plans/op", Object.assign({ op: o, id: id }, extra || {})));
    const btn = (label, fn, cls) => el("button", { type: "button", class: cls || "ghost", onclick: fn }, label);
    const stepEl = (p, s) => el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" },
      el("b", {}, String(s.idx + 1) + "."), el("code", { dir: "ltr" }, s.tool), pill(pillFor(s.status), tx(STEP_STATE[s.status][0], STEP_STATE[s.status][1])),
      s.consequential ? pill("degraded", tx("שואל לפני ביצוע", "Asks first")) : null,
      s.why ? el("span", { class: "muted small", dir: "auto" }, s.why) : null,
      s.evidence ? el("span", { class: "small", dir: "auto" }, tx("עדות: ", "Evidence: ") + s.evidence) : null,
      s.error ? el("span", { class: "small", dir: "auto" }, s.error) : null, el("span", { class: "spacer" }),
      (s.status === "failed" || s.status === "blocked" || s.status === "unknown") && p.state !== "cancelled"
        ? btn(tx("ניסיון חוזר", "Retry"), op("retry", p.id, { step: s.idx })) : null,
      (s.status === "failed" || s.status === "blocked" || s.status === "unknown" || s.status === "pending") && p.state !== "done" && p.state !== "cancelled"
        ? btn(tx("דילוג", "Skip"), op("skip", p.id, { step: s.idx })) : null);
    const card = (p) => {
      const live = p.state === "ready" || p.state === "running";
      return el("div", { class: "card" },
        el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, el("h2", { dir: "auto", style: "margin:0" }, p.title),
          pill(pillFor(p.state), tx(PLAN_STATE[p.state][0], PLAN_STATE[p.state][1])), el("span", { class: "spacer" }),
          el("span", { class: "muted small" }, tx("זמן מרבי ", "Time cap ") + Math.round(p.timeout_s / 60) + tx(" דק׳, בקשות AI ", " min, AI requests ") + p.max_ai)),
        el("div", { class: "row", style: "gap:8px;margin:8px 0" }, bar(p.progress), el("span", {}, p.progress + "%")),
        p.note ? el("p", { dir: "auto", class: "muted", role: "status" }, p.note) : null,
        p.steps.map((s) => stepEl(p, s)),
        p.result ? el("p", { dir: "auto" }, el("b", {}, tx("תוצאה: ", "Result: ")), p.result) : null,
        el("div", { class: "row", style: "gap:8px;flex-wrap:wrap;margin-top:8px" },
          p.state === "draft" ? btn(tx("אישור והרצה (מבקש אישור על המסך)", "Approve and run (asks on your screen)"), () => {
            msg.textContent = WAITING(); act(msg, () => post("/api/goal-plans/op", { op: "approve", id: p.id }));
          }, "primary") : null,
          live ? btn(tx("השהיה", "Pause"), op("pause", p.id)) : null,
          p.state === "paused" || p.state === "blocked" ? btn(tx("המשך", "Resume"), op("resume", p.id), "primary") : null,
          p.state === "failed" ? btn(tx("ניסיון חוזר", "Retry"), op("retry", p.id)) : null,
          p.state !== "done" && p.state !== "failed" && p.state !== "cancelled" ? btn(tx("ביטול", "Cancel"), op("cancel", p.id)) : null,
          el("span", { class: "spacer" }),
          !live ? btn(tx("מחיקה", "Delete"), op("delete", p.id)) : null));
    };
    render(header(tx("תוכניות ליעדים", "Goal plans"), tx("תוכנית היא רשימת צעדים קצרה של כלים קיימים. היא רצה רק אחרי שאישרת אותה, ושואלת לפני כל צעד ששינה משהו.",
        "A plan is a short list of steps using existing tools. It runs only after you approve it and asks before every step that changes something.")),
      msg, d.plans.length ? d.plans.map(card) : el("div", { class: "empty" }, tx("אין תוכניות. אמרו: \"תתכנן איך ל...\" או: jarvis goals plan new.", "No plans. Say: \"plan how to ...\" or run: jarvis goals plan new.")));
  }

  Object.assign(PAGES, { development: pageDevelopment, goals: pageGoals, preferences: pagePreferences, proactive: pageProactive, devdrafts: pageDevDrafts, goalplans: pageGoalPlans });
})();
