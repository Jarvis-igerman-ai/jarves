// Projects page (Work group): the project manager. A project picker with status and done/total, the task board of
// the chosen project grouped by status, a task detail panel with the timeline (deep dive) and the owner's actions
// (plan: your own list or instruction, a suggestion, several agents consulted, or one lead agent with its own
// sub-agents; approve plan, pause / resume, add / record done / update / split / cancel / retry task, note,
// ask / change, ship plan). Same
// operations as `jarvis projects` and the voice tools (/api/projects/...). Agent and task text is untrusted:
// text nodes only (never innerHTML), never translated (data-noi18n), dir="auto". JARVES never pushes or merges.
// Uses the helpers of app.js (el, api, render, header, pill, tile, route) and pages.js (input, select, field, note, when).
"use strict";

const projLang = () => (window.JL && window.JL.lang === "he" ? "he" : "en");
const projGroups = () => [
  ["proposed", ["proposed"], tx("מוצעות", "Proposed")],
  ["ready", ["ready", "todo", "pending", "queued"], tx("מוכנות", "Ready")],
  ["running", ["running"], tx("רצות", "Running")],
  ["blocked", ["blocked", "failed"], tx("חסומות / נכשלו", "Blocked / failed")],
  ["done", ["done"], tx("הושלמו", "Done")]];
const projStateCls = (s) => ({ active: "ok", paused: "medium", archived: "plain", done: "ok", running: "ok", failed: "high",
  blocked: "high", proposed: "medium" }[s] || "plain");

function projAge(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return tx("עכשיו", "now");
  if (s < 5400) return Math.round(s / 60) + tx(" דק׳", " min");
  if (s < 129600) return Math.round(s / 3600) + tx(" שע׳", " h");
  return Math.round(s / 86400) + tx(" ימים", " d");
}

const projCsv = (v) => String(v || "").split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);

async function projOp(box, body, then) {
  box.textContent = tx("שולח…", "Sending…");
  try {
    const r = await api("/api/projects/op", { method: "POST", body: JSON.stringify(body) });
    box.replaceChildren(el("span", { dir: "auto", "data-noi18n": "" }, (r && (r.text || r.error)) || ""),
      r && r.commands ? el("pre", { dir: "ltr", "data-noi18n": "", class: "small", style: "white-space:pre-wrap" }, r.commands.join("\n")) : null);
    if (r && r.ok !== false && !r.error && then) then(r);
    return r;
  } catch (e) { box.textContent = e.message; }
}

function projTaskCard(t, sel, pick) {
  const extra = [t.runner ? t.runner : null, projAge(t.updated), t.attempts ? tx("ניסיונות: ", "attempts: ") + t.attempts : null].filter(Boolean).join(" · ");
  return el("div", { class: "card", style: "margin-bottom:8px;padding:10px 12px" + (sel ? ";outline:2px solid var(--accent, currentColor)" : "") },
    el("div", { class: "row", style: "gap:6px" },
      el("button", { type: "button", class: "ghost", "aria-pressed": sel ? "true" : "false", onclick: pick,
        style: "text-align:start;flex:1;padding:2px 4px", dir: "auto", "data-noi18n": "" }, `#${t.seq} ${t.title}`)),
    el("div", { class: "small muted", dir: "auto", "data-noi18n": "" }, extra),
    t.reason ? el("div", { class: "small", dir: "auto", "data-noi18n": "" }, t.reason) : null,
    t.handoff ? el("div", { class: "small muted", dir: "auto", "data-noi18n": "", style: "white-space:pre-wrap" }, String(t.handoff).slice(0, 240)) : null);
}

function projDetail(pid, seq, t, box, deep) {
  const title = input({ type: "text", dir: "auto", "aria-label": tx("כותרת", "Title"), style: "min-width:260px" }, t.title);
  const spec = el("textarea", { dir: "auto", rows: "3", style: "width:100%", "aria-label": tx("פירוט", "Spec") });
  const scope = input({ type: "text", dir: "ltr", "aria-label": tx("היקף (נתיבים)", "Scope (paths)") }, (t.scope || []).join(", "));
  const deps = input({ type: "text", dir: "ltr", class: "narrow", "aria-label": tx("תלוי ב-", "Depends on") }, (t.deps || []).join(", "));
  const noteT = input({ type: "text", dir: "auto", style: "min-width:260px", "aria-label": tx("הערה", "Note") });
  const talkT = input({ type: "text", dir: "auto", style: "min-width:260px", "aria-label": tx("הודעה לסוכן", "Message to the agent") });
  const mode = select([["ask", tx("שאלה (קריאה בלבד)", "Ask (read-only)")], ["do", tx("בקשת שינוי", "Ask to change")]], "ask", { "aria-label": tx("מצב", "Mode") });
  const base = { project: pid, task: seq };
  const go = (op, extra) => () => projOp(box, Object.assign({ op }, base, extra()), () => setTimeout(() => route(true), 400));
  const splitHow = input({ type: "text", dir: "auto", style: "min-width:260px", "aria-label": tx("איך לפצל (לא חובה)", "How to split (optional)") });
  const splitWho = input({ type: "text", dir: "ltr", "aria-label": tx("להתייעץ עם (סוכנים)", "Consult (agents)"), placeholder: "claude, codex" });
  const tl = (deep.timeline || []).slice().reverse().map((e) => el("tr", {}, el("td", { class: "small" }, when(e.ts)),
    el("td", { class: "small", "data-noi18n": "" }, e.who || ""), el("td", { class: "small", "data-noi18n": "" }, e.kind || ""),
    el("td", { dir: "auto", "data-noi18n": "", class: "small", style: "white-space:pre-wrap" }, e.text || "")));
  return el("section", { class: "card", id: "projDetail", tabindex: "-1", "aria-label": tx("פרטי משימה", "Task details") },
    el("div", { class: "row" }, el("h3", { style: "margin:0", dir: "auto", "data-noi18n": "" }, `#${t.seq} ${t.title}`), pill(projStateCls(t.status), t.status_text || t.status),
      el("span", { class: "spacer" }),
      el("button", { type: "button", class: "ghost", onclick: () => { location.hash = "#/projects?p=" + encodeURIComponent(pid); } }, tx("סגירה", "Close"))),
    t.handoff ? el("pre", { dir: "auto", "data-noi18n": "", class: "small", style: "white-space:pre-wrap" }, String(t.handoff)) : null,
    (deep.depends_on || []).length ? el("div", { class: "small muted", "data-noi18n": "" }, tx("תלוי ב: ", "Depends on: ") + deep.depends_on.map((d) => (d && d.seq ? "#" + d.seq : "#" + d)).join(", ")) : null,
    el("div", { class: "filters" }, field(tx("כותרת", "Title"), title), field(tx("היקף (נתיבים)", "Scope (paths)"), scope), field(tx("תלוי ב-", "Depends on"), deps)),
    field(tx("פירוט (ריק = ללא שינוי)", "Spec (empty = unchanged)"), spec),
    el("div", { class: "row", style: "margin:8px 0" },
      el("button", { type: "button", class: "primary", onclick: go("update_task", () => {
        const x = { title: title.value, scope: projCsv(scope.value), deps: projCsv(deps.value).map(Number).filter((n) => !isNaN(n)) };
        if (spec.value.trim()) x.spec = spec.value; return x; }) }, tx("עדכון", "Update")),
      el("button", { type: "button", class: "ghost", onclick: go("retry_task", () => ({})) }, tx("ניסיון חוזר", "Retry")),
      el("button", { type: "button", class: "ghost", onclick: go("cancel_task", () => ({})) }, tx("ביטול משימה", "Cancel task"))),
    el("div", { class: "filters" }, field(tx("איך לפצל (לא חובה)", "How to split (optional)"), splitHow),
      field(tx("להתייעץ עם (סוכנים)", "Consult (agents)"), splitWho),
      el("button", { type: "button", class: "ghost", onclick: go("split_task", () => ({ how: splitHow.value, consult: projCsv(splitWho.value) })) },
        tx("פיצול למשימות קטנות", "Split into smaller tasks"))),
    el("div", { class: "filters" }, field(tx("הערה", "Note"), noteT),
      el("button", { type: "button", class: "ghost", onclick: go("note", () => ({ text: noteT.value })) }, tx("הוספת הערה", "Add note"))),
    el("div", { class: "filters" }, field(tx("הודעה לסוכן", "Message to the agent"), talkT), field(tx("מצב", "Mode"), mode),
      el("button", { type: "button", class: "primary", onclick: go("talk", () => ({ text: talkT.value, mode: mode.value, lang: projLang() })) }, tx("שליחה לסוכן", "Send to agent"))),
    el("h4", {}, tx("ציר זמן", "Timeline")),
    tl.length ? el("table", {}, el("tbody", {}, tl)) : el("div", { class: "empty" }, "—"));
}

PAGES.projects = async function () {
  const q = hashQuery();
  const d = await api("/api/projects");
  const guide = el("a", { href: "#/guide/projects" }, tx("מדריך", "Guide"));
  if (d.enabled === false) {
    render(header(tx("פרויקטים", "Projects"), tx("מנהל הפרויקטים", "The project manager"), guide),
      el("div", { class: "card" }, el("div", { class: "empty" }, tx("מנהל הפרויקטים כבוי: ", "The project manager is off: ") + (d.reason || "")),
        el("div", { class: "small" }, tx("הדליקו אותו בעמוד התכונות, ואז במחשב: jarvis projects open <תיקייה>",
          "Switch it on on the Features page, then on the PC: jarvis projects open <folder>")),
        el("a", { href: "#/features" }, tx("עמוד התכונות", "Features page"))));
    return;
  }
  const projects = d.projects || [];
  const lead = el("p", { class: "muted small" }, tx("פתיחה (open) וארכוב (archive) של פרויקט זמינים גם מהמחשב: jarvis projects open / archive. ג'רוויס אף פעם לא דוחף (push) ולא ממזג (merge) - זו ההחלטה שלך.",
    "Open and archive of a project are also available from the PC: jarvis projects open / archive. JARVES never pushes or merges - that is your decision."));
  const head = header(tx("פרויקטים", "Projects"), tx("תכנון, ביצוע ומעקב אחר משימות בפרויקטי git, עם אישור שלך על המסך.",
    "Plan, run and follow tasks in git projects, with your approval on screen."), guide);
  if (!projects.length) {
    render(head, lead, el("div", { class: "card" }, el("div", { class: "empty" }, tx("אין עדיין פרויקטים. במחשב: jarvis projects open <תיקייה>",
      "No projects yet. On the PC: jarvis projects open <folder>"))));
    return;
  }
  const cur = projects.find((p) => p.id === q.p || p.name === q.p) || projects[0];
  const pick = select(projects.map((p) => [p.id, `${p.name} (${p.status}, ${(p.counts || {}).done || 0}/${p.total})`]), cur.id, { "aria-label": tx("בחירת פרויקט", "Choose project") });
  pick.addEventListener("change", () => { location.hash = "#/projects?p=" + encodeURIComponent(pick.value); });
  const box = el("div", { class: "small", role: "status", "aria-live": "polite", style: "margin:8px 0" });
  const st = await api(`/api/projects/status?project=${encodeURIComponent(cur.id)}&lang=${projLang()}`);
  const tasks = (st && st.tasks) || [];
  const selSeq = q.t ? Number(q.t) : null;
  const selTask = tasks.find((t) => t.seq === selSeq);
  const list = el("table", {}, el("thead", {}, el("tr", {}, [tx("פרויקט", "Project"), tx("מצב", "Status"), tx("הושלמו", "Done"), tx("ענף", "Branch")].map((h) => el("th", {}, h)))),
    el("tbody", {}, projects.map((p) => el("tr", { class: "click" + (p.id === cur.id ? " active" : ""), tabindex: "0",
      onclick: () => { location.hash = "#/projects?p=" + encodeURIComponent(p.id); },
      onkeydown: (e) => { if (e.key === "Enter") location.hash = "#/projects?p=" + encodeURIComponent(p.id); } },
    el("td", { dir: "auto", "data-noi18n": "" }, p.name), el("td", {}, pill(projStateCls(p.status), p.status)),
    el("td", { class: "num" }, `${(p.counts || {}).done || 0}/${p.total}`), el("td", { dir: "ltr", "data-noi18n": "", class: "small" }, p.branch || "—")))));
  const grouped = new Set();
  const cols = projGroups().map(([key, sts, label]) => {
    const items = tasks.filter((t) => sts.includes(t.status));
    items.forEach((t) => grouped.add(t.seq));
    return el("section", { "aria-label": label }, el("h4", { style: "margin:0 0 6px" }, `${label} (${items.length})`),
      items.length ? items.map((t) => projTaskCard(t, t.seq === selSeq, () => { location.hash = `#/projects?p=${encodeURIComponent(cur.id)}&t=${t.seq}`; })) : el("div", { class: "muted small" }, "—"));
  });
  const goal = el("textarea", { dir: "auto", rows: "4", style: "width:100%", "aria-label": tx("יעד או רשימת משימות ב-JSON", "Goal or task list as JSON"),
    placeholder: tx("יעד בטקסט חופשי, או רשימת JSON: [{\"title\": \"...\", \"spec\": \"...\"}]", "A goal in plain words, or a JSON list: [{\"title\": \"...\", \"spec\": \"...\"}]") });
  const how = input({ type: "text", dir: "auto", style: "min-width:280px", "aria-label": tx("איך לפצל (לא חובה)", "How to split (optional)"),
    placeholder: tx("למשל: משימה לכל מודול, בדיקות בסוף", "e.g. one task per module, tests last") });
  const strat = select([["split", tx("ג'ארביס מפצל למשימות קטנות", "JARVES splits into small tasks")],
    ["delegate", tx("סוכן מוביל אחד עם תת-סוכנים משלו", "One lead agent with its own sub-agents")]], "split", { "aria-label": tx("אסטרטגיה", "Strategy") });
  const consult = input({ type: "text", dir: "ltr", "aria-label": tx("להתייעץ עם (סוכנים)", "Consult (agents)"), placeholder: "claude, codex, opencode" });
  const agentI = input({ type: "text", dir: "ltr", class: "narrow", "aria-label": tx("סוכן", "Agent"), placeholder: "claude" });
  const planGo = () => {
    const v = goal.value.trim();
    let body = { op: "plan", project: cur.id, how: how.value, strategy: strat.value, agent: agentI.value.trim(), consult: projCsv(consult.value) };
    if (v.startsWith("[")) { try { body.tasks = JSON.parse(v); } catch (e) { box.textContent = tx("JSON לא תקין: ", "Invalid JSON: ") + e.message; return; } } else body.goal = v;
    projOp(box, body, () => setTimeout(() => route(true), 400));
  };
  const refresh = () => setTimeout(() => route(true), 400);
  const simple = (op, extra) => () => projOp(box, Object.assign({ op, project: cur.id }, extra || {}), refresh);
  const nt = input({ type: "text", dir: "auto", style: "min-width:220px", "aria-label": tx("כותרת משימה", "Task title") });
  const ns = el("textarea", { dir: "auto", rows: "2", style: "width:100%", "aria-label": tx("פירוט משימה", "Task spec") });
  const nsc = input({ type: "text", dir: "ltr", "aria-label": tx("היקף (נתיבים)", "Scope (paths)"), placeholder: "src/, tests/" });
  const nd = input({ type: "text", dir: "ltr", class: "narrow", "aria-label": tx("תלוי ב-", "Depends on"), placeholder: "1, 2" });
  const nteam = el("input", { type: "checkbox", "aria-label": tx("סוכן עם תת-סוכנים", "Agent with sub-agents") });
  const ndone = el("input", { type: "checkbox", "aria-label": tx("כבר בוצע (לתיעוד)", "Already done (record it)") });
  const addGo = () => projOp(box, { op: "add_task", project: cur.id, title: nt.value, spec: ns.value, scope: projCsv(nsc.value),
    deps: projCsv(nd.value).map(Number).filter((n) => !isNaN(n)), mode: nteam.checked ? "team" : "solo", done: ndone.checked,
    handoff: ndone.checked ? ns.value : "" }, refresh);
  const paused = cur.status === "paused";
  const deepBox = el("div", {});
  // who works now: JARVES's task runs and your own Claude Code / Codex sessions in this folder
  const sess = (st && st.sessions) || [];
  const runningNow = tasks.filter((t) => t.status === "running");
  const agentWho = input({ type: "text", dir: "ltr", class: "narrow", "aria-label": tx("סוכן", "Agent"), placeholder: "claude" });
  const agentMsg = input({ type: "text", dir: "auto", style: "min-width:240px", "aria-label": tx("הודעה לסוכן", "Message to the agent") });
  const agentOp = (op, extra) => () => projOp(box, Object.assign({ op, project: cur.id, agent: agentWho.value.trim() }, extra || {}), refresh);
  const nowCard = el("div", { class: "card" }, el("b", {}, tx("עובדים עכשיו: ", "Working now: ")),
    el("span", { dir: "auto", "data-noi18n": "" }, runningNow.map((t) => `#${t.seq} ${t.runner || ""}${t.mode === "team" ? " +" : ""}`)
      .concat(sess.map((x) => `${x.provider} (${tx("שלך", "yours")}, ${x.state_text || x.state}, ${x.age || ""})`)).join(" · ") || "—"),
    el("div", { class: "filters", style: "margin-top:8px" }, field(tx("סוכן", "Agent"), agentWho), field(tx("הודעה לסוכן", "Message to the agent"), agentMsg),
      el("button", { type: "button", class: "ghost", onclick: () => projOp(box, { op: "tell_agent", project: cur.id, agent: agentWho.value.trim(), text: agentMsg.value }, refresh) }, tx("שליחה לסוכן", "Send to agent")),
      el("button", { type: "button", class: "ghost", onclick: agentOp("more_agents") }, tx("עוד סוכנים", "More agents")),
      el("button", { type: "button", class: "ghost", onclick: agentOp("more_agents", { fewer: true }) }, tx("פחות סוכנים", "Fewer agents")),
      el("button", { type: "button", class: "ghost", onclick: agentOp("stop_agent") }, tx("עצירת הסוכן", "Stop the agent"))));
  render(head, lead,
    el("div", { class: "card filters" }, field(tx("פרויקט", "Project"), pick),
      el("div", { class: "row" },
        paused || cur.status !== "active" ? el("button", { type: "button", class: "primary", onclick: simple("resume") }, tx("המשך", "Resume"))
          : el("button", { type: "button", class: "ghost", onclick: simple("pause") }, tx("השהיה", "Pause")),
        el("button", { type: "button", class: "ghost", onclick: simple("approve_plan") }, tx("אישור התוכנית", "Approve plan")),
        el("button", { type: "button", class: "ghost", onclick: simple("ship_plan") }, tx("תוכנית שילוב (Ship plan)", "Ship plan")),
        el("button", { type: "button", class: "ghost", onclick: () => route(true) }, tx("רענון", "Refresh")))),
    el("div", { class: "card" }, el("b", {}, tx("תקציר: ", "Summary: ")), el("span", { dir: "auto", "data-noi18n": "" }, (st && st.text) || "—")),
    nowCard,
    box,
    el("div", { class: "card" }, list),
    el("h3", {}, tx("לוח משימות", "Task board")),
    el("div", { style: "display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;align-items:start" }, cols),
    deepBox,
    el("h3", {}, tx("תכנון", "Plan")),
    el("div", { class: "card" }, goal, el("div", { class: "filters", style: "margin-top:8px" },
      field(tx("אסטרטגיה", "Strategy"), strat), field(tx("איך לפצל (לא חובה)", "How to split (optional)"), how),
      field(tx("להתייעץ עם (סוכנים)", "Consult (agents)"), consult), field(tx("סוכן", "Agent"), agentI)),
      el("div", { class: "row", style: "margin-top:8px" },
      el("button", { type: "button", class: "primary", onclick: planGo }, tx("תכנון", "Plan")))),
    el("h3", {}, tx("הוספת משימה", "Add task")),
    el("div", { class: "card" }, el("div", { class: "filters" }, field(tx("כותרת משימה", "Task title"), nt), field(tx("היקף (נתיבים)", "Scope (paths)"), nsc), field(tx("תלוי ב-", "Depends on"), nd)),
      field(tx("פירוט משימה", "Task spec"), ns),
      el("div", { class: "row" }, field(tx("סוכן עם תת-סוכנים", "Agent with sub-agents"), nteam),
        field(tx("כבר בוצע (לתיעוד)", "Already done (record it)"), ndone)),
      el("div", { class: "row", style: "margin-top:8px" },
        el("button", { type: "button", class: "primary", onclick: addGo }, tx("הוספה", "Add")))));
  if (selTask) {
    try {
      const dp = await api(`/api/projects/deep?project=${encodeURIComponent(cur.id)}&task=${selTask.seq}&lang=${projLang()}`);
      deepBox.replaceChildren(projDetail(cur.id, selTask.seq, selTask, box, dp || {}));
      const dd = document.getElementById("projDetail"); if (dd) dd.focus();
    } catch (e) { box.textContent = e.message; }
  }
};
