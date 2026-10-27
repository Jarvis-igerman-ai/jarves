// SW-V4 E Coding agents page (Activity group): the owner's Claude Code / Codex sessions per project (provider,
// state, last progress, age, blocker, next step, cost), the portfolio digest, waiting agent approvals, a start form,
// and one page per session (timeline, ask / tell, continue / pause / stop / resume). Same operations as
// `jarvis agents` and the voice tools (/api/agents/...). Agent text is untrusted user content: text nodes only,
// never translated (data-noi18n), dir="auto".
// Uses the helpers of app.js (el, api, render, header, pill, route, timer) and pages.js (input, select, field, note).
"use strict";

const agentsLang = () => (window.JL && window.JL.lang === "he" ? "he" : "en");
const agentStateCls = (s) => ({ RUNNING: "ok", IDLE: "plain", WAITING_APPROVAL: "high", WAITING_INPUT: "medium",
  PAUSED: "medium", STOPPED: "plain", DISCONNECTED: "medium", DISCOVERED: "plain", UNKNOWN: "plain" }[s] || "plain");
const agentProvider = (p) => (p === "codex" ? "Codex" : "Claude Code");

function agentAttach(a) {
  return { managed: tx("הופעל ע\"י ג'רוויס", "started by JARVES"), live_channel: tx("ערוץ חי", "live channel"),
    plugin: tx("תוסף: בעצירה הבאה", "plugin: at the next stop"), observe_only: tx("צפייה בלבד", "observe only"),
    UNSUPPORTED_ATTACH: tx("צפייה בלבד (אין חיבור)", "observe only (cannot attach)") }[a] || a;
}

function agentResult(box, r) {
  box.replaceChildren(el("div", { dir: "auto", "data-noi18n": "" }, (r && r.text) || ""),
    r && r.candidates && r.candidates.length ? el("div", { class: "small muted" }, tx("מתאימים: ", "Matching: "),
      r.candidates.map((c) => el("a", { href: "#/agent/" + encodeURIComponent(c.id), style: "margin-inline-end:8px" },
        `${agentProvider(c.provider)} · ${c.project}`))) : null);
}

async function agentPost(path, body, box) {
  box.textContent = tx("שולח…", "Sending…");
  try { agentResult(box, await api(path, { method: "POST", body: JSON.stringify(body) })); }
  catch (e) { box.textContent = e.message; }
}

// G6 (#413): what blocks the agents' pull requests. Hidden when the switch is off or the API is not there. Text nodes only.
async function agentsPrCard(on) {
  if (!on) return null;                                          // the agents answer says whether the switch exists and is on
  let r;
  try { r = await api(`/api/github/work?lang=${agentsLang()}`); } catch (e) { return null; }
  if (!r || !r.enabled) return null;
  const rows = (r.rows || []).map((x) => el("tr", {},
    el("td", { dir: "auto", "data-noi18n": "" }, x.branch),
    el("td", {}, pill(x.state === "ready" ? "ok" : x.state === "checks_failing" ? "fail" : "plain", x.state.replace("_", " "))),
    el("td", { dir: "auto", "data-noi18n": "", class: "small" }, x.next)));
  return el("div", { class: "card", id: "agents-prs" }, el("h3", {}, tx("בקשות המיזוג של הסוכנים", "Agents' pull requests")),
    rows.length ? el("table", {}, el("thead", {}, el("tr", {}, ["ענף", "מצב", "הצעד הבא"].map((h, i) => el("th", {}, tx(h, ["Branch", "State", "Next step"][i]))))),
      el("tbody", {}, rows)) : el("div", { class: "empty" }, r.text || tx("אין סשן עם ענף.", "No session with a branch.")));
}

PAGES.agents = async function () {
  const lang = agentsLang();
  const [d, ap] = await Promise.all([api(`/api/agents?digest=1&lang=${lang}`), api("/api/agents/approvals")]);
  const prCard = d.enabled === false ? null : await agentsPrCard(d.pr_card);
  if (d.enabled === false) {
    render(header(tx("סוכני קוד", "Coding agents"), tx("Claude Code ו-Codex", "Claude Code and Codex")),
      el("div", { class: "card" }, el("div", { class: "empty" }, tx("מנהל סוכני הקוד כבוי: ", "The coding-agent orchestrator is off: ") + (d.reason || "")),
        el("div", { class: "small" }, tx("הדליקו אותו בעמוד התכונות, ואז: jarvis agents projects add <תיקייה> ו-jarvis agents install-plugin claude",
          "Switch it on on the Features page, then: jarvis agents projects add <folder> and jarvis agents install-plugin claude")),
        el("a", { href: "#/features" }, tx("עמוד התכונות", "Features page"))));
    return;
  }
  const rows = (d.sessions || []).map((s) => el("tr", { class: "click", onclick: () => { location.hash = "#/agent/" + encodeURIComponent(s.id); } },
    el("td", {}, agentProvider(s.provider)),
    el("td", { dir: "auto", "data-noi18n": "" }, s.project),
    el("td", {}, pill(agentStateCls(s.state), s.state_text), s.last_known ? el("span", { class: "muted small" }, " " + tx("(ידוע אחרון)", "(last known)")) : null),
    el("td", { class: "small" }, s.age),
    el("td", { dir: "auto", "data-noi18n": "", class: "small" }, s.blocker || s.waiting_for || "—"),
    el("td", { dir: "auto", "data-noi18n": "", class: "small" }, s.next_step || (s.untrusted_activity || "").slice(0, 80) || "—"),
    el("td", { class: "num small" }, s.cost_usd ? `$${s.cost_usd.toFixed(3)}` : (s.tokens ? `${s.tokens} tok` : "—")),
    el("td", { class: "small muted" }, agentAttach(s.attach))));
  const table = rows.length ? el("table", {}, el("thead", {}, el("tr", {},
    ["Agent", "Project", "State", "Last progress", "Blocker", "Next step", "Cost", "Connection"].map((h, i) =>
      el("th", {}, tx(["סוכן", "פרויקט", "מצב", "התקדמות אחרונה", "חוסם", "הצעד הבא", "עלות", "חיבור"][i], h))))), el("tbody", {}, rows))
    : el("div", { class: "empty" }, (d.projects || []).length
      ? tx("אין כרגע סשנים בפרויקטים המותרים. הפעילו Claude Code או Codex באחד מהם, או התחילו סשן חדש כאן.",
        "No sessions in the allowed projects right now. Start Claude Code or Codex in one of them, or start a new session here.")
      : tx("עוד אין פרויקטים מותרים. במחשב: jarvis agents projects add C:\\work\\jarvis --name jarvis, ואז jarvis agents install-plugin claude.",
        "No allowed projects yet. On the PC: jarvis agents projects add C:\\work\\jarvis --name jarvis, then jarvis agents install-plugin claude."));
  const waiting = (ap.approvals || []).map((x) => {
    const code = input({ type: "text", class: "narrow", placeholder: tx("קוד", "code"), "aria-label": tx("קוד", "code") });
    const msg = note();
    const decide = (yes) => async () => {
      msg.textContent = tx("שולח…", "Sending…");
      try { const r = await api(`/api/agents/approvals/${encodeURIComponent(x.request_id)}`, { method: "POST", body: JSON.stringify({ approved: yes, code: code.value }) });
        msg.textContent = r.text || ""; setTimeout(route, 600); }
      catch (e) { msg.textContent = e.message; }
    };
    return el("div", { class: "card", style: "margin-bottom:8px" },
      el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, pill(x.risk === "high" ? "high" : "medium", x.risk || ""),
        el("b", {}, `${agentProvider(x.provider)} · ${x.tool || ""}`), el("span", { class: "spacer" }),
        x.typed ? code : null,
        el("button", { type: "button", class: "primary", onclick: decide(true) }, tx("אישור", "Approve")),
        el("button", { type: "button", class: "ghost", onclick: decide(false) }, tx("דחייה", "Deny")), msg),
      el("pre", { dir: "ltr", "data-noi18n": "", class: "small" }, x.command || ""));
  });
  const prov = select([["claude", "Claude Code"], ["codex", "Codex"]], "claude");
  const proj = select((d.projects || []).map((p) => [p, p]), (d.projects || [])[0] || "");
  const task = input({ type: "text", dir: "auto", placeholder: tx("המשימה", "The task"), style: "min-width:320px", "aria-label": tx("המשימה", "The task") });
  const here = input({ type: "checkbox", "aria-label": tx("בתיקיית הפרויקט עצמה", "In the project folder itself") });
  const startMsg = note();
  const start = el("div", { class: "card filters" },
    field(tx("סוכן", "Agent"), prov), field(tx("פרויקט", "Project"), proj), field(tx("משימה", "Task"), task),
    el("label", { class: "field" }, el("span", {}, tx("בלי worktree חדש", "No new worktree")), here),
    el("div", { class: "row" }, el("button", { type: "button", class: "primary", disabled: !(d.projects || []).length, onclick: () =>
      agentPost("/api/agents/start", { provider: prov.value, project: proj.value, task: task.value, here: here.checked }, startMsg) },
    tx("התחלת סשן (אישור על המסך)", "Start a session (approve on screen)")), startMsg));
  const dg = d.digest || {};
  render(header(tx("סוכני קוד", "Coding agents"), tx("הסשנים של Claude Code ו-Codex בפרויקטים המותרים, גם כאלה שהפעלת בעצמך. אותן פעולות כמו בקול וב-jarvis agents.",
    "Claude Code and Codex sessions in your allowed projects, also ones you started yourself. The same operations as by voice and jarvis agents."),
  el("a", { href: "#/guide/agent_orchestrator" }, tx("מדריך", "Guide"))),
  el("div", { class: "card", style: "margin-bottom:10px" }, el("b", {}, tx("תקציר: ", "Digest: ")), el("span", { dir: "auto", "data-noi18n": "" }, dg.text || "—"),
    d.hidden_outside_allowlist ? el("div", { class: "muted small" }, `${d.hidden_outside_allowlist} ` + tx("סשנים נוספים מחוץ לרשימת המותרים", "more session(s) outside the allow-list")) : null),
  waiting.length ? [el("h3", {}, tx("ממתינים לאישור שלך", "Waiting for your approval")), waiting] : null,
  el("div", { class: "card" }, table),
  prCard,
  el("h3", {}, tx("סשן חדש", "New session")), start);
  timer = setInterval(() => route(true), 10000);
};

PAGES.agent = async function (id) {
  const lang = agentsLang();
  const d = await api(`/api/agents/session/${encodeURIComponent(id)}?lang=${lang}`);
  if (!d.ok) { render(header(tx("סשן", "Session"), ""), el("div", { class: "card" }, el("div", { class: "empty" }, d.text || ""), el("a", { href: "#/agents" }, tx("כל הסשנים ←", "← all sessions")))); return; }
  const s = d.session;
  const box = el("div", { class: "small", role: "status" });
  const q = input({ type: "text", dir: "auto", placeholder: tx("מה חוסם אותך?", "What is blocking you?"), style: "min-width:320px", "aria-label": tx("שאלה", "Question") });
  const t = input({ type: "text", dir: "auto", placeholder: tx("תריץ את הבדיקות הממוקדות", "Run the targeted tests"), style: "min-width:320px", "aria-label": tx("הוראה", "Instruction") });
  const act = (a) => el("button", { type: "button", class: "ghost", onclick: () => agentPost("/api/agents/action", { ref: s.id, action: a }, box) },
    { continue: tx("המשך", "Continue"), pause: tx("השהיה", "Pause"), interrupt: tx("עצירת התור", "Interrupt"), stop: tx("סיום (אישור)", "Stop (approve)"), resume: tx("המשך דרך ג'רוויס (אישור)", "Resume under JARVES (approve)") }[a]);
  const acts = ["continue", "pause"].concat(s.capabilities.includes("interrupt") ? ["interrupt", "stop"] : [])
    .concat(s.capabilities.includes("resume") ? ["resume"] : []);
  const timeline = (d.events || []).slice().reverse().map((e) => el("tr", {}, el("td", { class: "small" }, when(e.ts)),
    el("td", { class: "small" }, e.kind), el("td", { dir: "auto", "data-noi18n": "", class: "small" }, e.summary || "")));
  render(header(`${agentProvider(s.provider)} · ${s.project}`, `${s.cwd} · ${s.state_text} · ${s.age}`, el("a", { href: "#/agents" }, tx("כל הסשנים ←", "← all sessions"))),
    el("div", { class: "tiles" },
      tile(tx("מצב", "State"), s.state_text, s.last_known ? tx("ידוע אחרון", "last known") : s.status_source),
      tile(tx("חיבור", "Connection"), agentAttach(s.attach), s.capabilities.join(", ")),
      tile(tx("עלות (הערכה)", "Cost (estimate)"), s.cost_usd ? `$${s.cost_usd.toFixed(3)}` : "—", s.auth === "subscription" ? tx("מנוי", "subscription") : s.auth),
      tile(tx("טוקנים", "Tokens"), s.tokens ? String(s.tokens) : "—", "")),
    el("div", { class: "card" }, el("b", {}, tx("הדיווח האחרון שלו: ", "Its last report: ")), el("span", { dir: "auto", "data-noi18n": "" }, s.untrusted_summary || "—"),
      s.blocker ? el("div", { dir: "auto", "data-noi18n": "" }, tx("חוסם: ", "Blocker: ") + s.blocker) : null,
      s.next_step ? el("div", { dir: "auto", "data-noi18n": "" }, tx("הצעד הבא: ", "Next step: ") + s.next_step) : null,
      (s.files || []).length ? el("div", { class: "small muted", "data-noi18n": "" }, tx("קבצים: ", "Files: ") + s.files.join(", ")) : null),
    el("div", { class: "card filters" },
      field(tx("לשאול", "Ask"), q), el("button", { type: "button", class: "primary", onclick: () => agentPost("/api/agents/ask", { ref: s.id, question: q.value, wait_s: 20 }, box) }, tx("שאלה", "Ask")),
      field(tx("להגיד", "Tell"), t), el("button", { type: "button", class: "primary", onclick: () => agentPost("/api/agents/tell", { ref: s.id, text: t.value }, box) }, tx("שליחה", "Send")),
      el("div", { class: "row" }, acts.map(act)), box),
    el("h3", {}, tx("ציר זמן", "Timeline")),
    el("div", { class: "card" }, timeline.length ? el("table", {}, el("tbody", {}, timeline)) : el("div", { class: "empty" }, "—")));
  if (["RUNNING", "WAITING_APPROVAL", "WAITING_INPUT"].includes(s.state)) timer = setInterval(() => route(true), 5000);
};
