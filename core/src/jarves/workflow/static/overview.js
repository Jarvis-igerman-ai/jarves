/* JARVES workflow Overview (owner redesign): one factory status bar, "done today", theme and shell state.
   Read-only: it renders from the state app.js already fetched (the "workflow:render" event) and reads only GET
   /api/health (the verdict) and GET /api/report (the morning report). The only thing it stores is the viewer's
   theme choice, in guarded browser storage. */
"use strict";

(() => {
  const root = document.documentElement;
  const THEME_KEY = "jarves.workflow.theme";
  // Runs before first paint (the script is not deferred), so a saved light/dark choice never flashes.
  let savedTheme = null;
  try { savedTheme = localStorage.getItem(THEME_KEY); } catch (_) { /* storage blocked: follow the system */ }
  if (savedTheme === "light" || savedTheme === "dark") root.dataset.theme = savedTheme;

  const EN = String(root.lang || "he").toLowerCase().startsWith("en");
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const VIEWS = new Set(["overview", "roadmap", "questions", "work", "trace", "health", "timeline"]);
  // Page language: <html lang>, Hebrew by default. Each number keeps its own label; nothing is added together.
  const T = EN ? {
    allWorking: n => `Everything is working: ${n === 1 ? "1 worker is active" : `${n} workers are active`}`,
    needsYou: (q, n) => `Needs you: ${q === 1 ? "1 question" : `${q} questions`}${n ? ` · ${n === 1 ? "1 worker is active" : `${n} workers are active`}` : ""}`,
    stoppedFor: (d, why) => `Stopped for ${d}: ${why}`, stopped: why => `The factory is not working: ${why}`,
    whyAnswers: "waiting for your answers", whyQueued: n => `${n} tasks queued, no active worker`, whyEmpty: "nothing is queued",
    supervisorDown: "The supervisor is not running: no new work starts", noData: t => t ? `No fresh data since ${t}` : "No fresh data yet",
    loading: "Loading the factory status", supOk: "The supervisor is watching.", updated: a => `Updated ${a}`,
    dur: {m: n => `${n} min`, h: n => `${n} h`, d: n => n === 1 ? "1 day" : `${n} days`},
    factsLabel: "Pipeline", fQuestions: "Waiting for you", fQueued: "Queued", fReview: "In review", fMerged: "Merged today",
    doneMerged: "Merged", doneVerified: "Verified", doneShipped: "Shipped",
    doneEmpty: "Nothing was finished yet today.", doneNote: "Each number is counted on its own, by the task's last update today.",
    alerts: n => n === 1 ? "1 alert" : `${n} alerts`, themeLight: "Light theme", themeDark: "Dark theme",
    reportLoading: "Loading the report", reportFailed: "The report is not available now.",
    // Same codes as observability.VERDICT_TEXT (the server also sends the English reason).
    v: {working: d => `Everything is working: ${d.active} worker(s) active`, supervisor_dead: () => "The supervisor is not running: no new work starts",
      supervisor_late: () => "The supervisor heartbeat is late", no_data: () => "No heartbeat data yet", no_core: () => "The Core is not reachable (NO_CORE): no new work starts",
      usage_stop: d => `Stopped by usage: ${d.why}`, idle_ready: d => `Idle with ${d.ready} ready task(s) and no active worker`,
      owner_wait: d => `Waiting for your answers only (${d.questions})`, nothing_queued: () => "Nothing is queued"},
    static: {needs: "Needs you", all_questions: "All questions", running: "Working now", blocked: "Stuck", done_today: "Done today",
      versions: "Progress by version", shipped: "Shipped", verified: "Verified", merged: "Merged",
      more: "More details: team, active tasks, events and alerts", report: "Morning report"},
  } : {
    allWorking: n => `הכול עובד: ${n === 1 ? "עובד אחד פעיל" : `${n} עובדים פעילים`}`,
    needsYou: (q, n) => `צריך אותך: ${q === 1 ? "שאלה אחת" : `${q} שאלות`}${n ? ` · ${n === 1 ? "עובד אחד פעיל" : `${n} עובדים פעילים`}` : ""}`,
    stoppedFor: (d, why) => `עצר מזה ${d}: ${why}`, stopped: why => `המפעל לא עובד: ${why}`,
    whyAnswers: "מחכה לתשובות שלך", whyQueued: n => `${n} משימות בתור ואין עובד פעיל`, whyEmpty: "אין משימות בתור",
    supervisorDown: "המשגיח לא פעיל: עבודה חדשה לא מתחילה", noData: t => t ? `אין נתונים טריים מאז ${t}` : "עדיין אין נתונים טריים",
    loading: "טוען את מצב המפעל", supOk: "המשגיח שומר על העבודה.", updated: a => `עודכן ${a}`,
    dur: {m: n => `${n} דק׳`, h: n => `${n} שע׳`, d: n => n === 1 ? "יום" : `${n} ימים`},
    factsLabel: "צנרת העבודה", fQuestions: "ממתין לך", fQueued: "בתור", fReview: "בבדיקה", fMerged: "מוזגו היום",
    doneMerged: "מוזגו", doneVerified: "נבדקו", doneShipped: "שוחררו",
    doneEmpty: "עוד לא הושלם דבר היום.", doneNote: "כל מספר נספר בנפרד, לפי העדכון האחרון של המשימה היום.",
    alerts: n => n === 1 ? "התראה אחת" : `${n} התראות`, themeLight: "ערכה בהירה", themeDark: "ערכה כהה",
    reportLoading: "טוען את הדוח", reportFailed: "הדוח לא זמין כרגע.",
    v: {working: d => `הכול עובד: ${d.active === 1 ? "עובד אחד פעיל" : `${d.active} עובדים פעילים`}`, supervisor_dead: () => "המשגיח לא פעיל: עבודה חדשה לא מתחילה",
      supervisor_late: () => "פעימת המשגיח מאחרת", no_data: () => "עדיין אין נתוני דופק", no_core: () => "הליבה לא זמינה (NO_CORE): עבודה חדשה לא מתחילה",
      usage_stop: d => `נעצר בגלל שימוש: ${d.why}`, idle_ready: d => `בטלה: ${d.ready} משימות מוכנות ואין עובד פעיל`,
      owner_wait: d => `מחכה רק לתשובות שלך (${d.questions})`, nothing_queued: () => "אין משימות בתור"},
    static: {needs: "צריך אותך", all_questions: "לכל השאלות", running: "עובד עכשיו", blocked: "תקוע", done_today: "הושלם היום",
      versions: "התקדמות לפי גרסה", shipped: "שוחרר", verified: "נבדק", merged: "מוזג",
      more: "עוד פרטים: הצוות, משימות פעילות, אירועים והתראות"},
  };
  const signatures = {};

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const seconds = value => {
    if (value === null || value === undefined || value === "") return 0;
    const n = typeof value === "number" ? value : Number.isFinite(Number(value)) ? Number(value) : Date.parse(value) / 1000;
    return Number.isFinite(n) ? (n > 1e12 ? n / 1000 : n) : 0;
  };
  const clock = ts => {
    if (!ts) return "";
    const d = new Date(ts * 1000), today = new Date().toDateString() === d.toDateString();
    return d.toLocaleString(EN ? "en-GB" : "he-IL", today ? {hour: "2-digit", minute: "2-digit"} : {day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit"});
  };
  function put(id, html) {
    if (signatures[id] === html) return;
    signatures[id] = html;
    $(id).innerHTML = html;
  }

  const duration = sec => sec < 3600 ? T.dur.m(Math.max(1, Math.floor(sec / 60))) : sec < 86400 ? T.dur.h(Math.floor(sec / 3600)) : T.dur.d(Math.floor(sec / 86400));

  // The verdict is coloured by the WORST state. A gap or stale data is "unknown", never green.
  // A worker is active when it holds running work and reported recently (same freshness rule as app.js).
  function factory(data, now = Date.now() / 1000) {
    const tasks = new Map(data.tasks.map(task => [task.id, task]));
    const fresh = agent => seconds(agent.heartbeat_at) > now - 120 || seconds(agent.tool_started_at) > now - 900;
    const active = data.agents.filter(agent => agent.status !== "offline" && fresh(agent)
      && (["running", "claimed"].includes(agent.status) || tasks.get(agent.task_id)?.state === "running")).length;
    const supervisor = data.supervisor || {};
    const supBeat = seconds(supervisor.heartbeat_at);
    const supOk = Boolean(supBeat && now - supBeat < 120 && ["running", "watching"].includes(supervisor.status || "running"));
    const last = Math.max(0, ...data.agents.map(agent => Math.max(seconds(agent.heartbeat_at), seconds(agent.progress_at))));
    const observed = seconds(data.observed_at);
    const dataAge = observed ? Math.max(0, now - observed) : Infinity;
    const count = state => data.tasks.filter(task => task.state === state).length;
    const questions = data.questions.filter(q => ["pending", "deferred", "open"].includes(q.status || "pending")).length;
    // Queued counts only tasks that may run, the same rule as the "Working now" block.
    const queued = data.tasks.filter(task => task.state === "queued" && task.execution_allowed !== false).length;
    const verdict = dataAge > 180 ? "unknown" : !supOk ? "bad" : !active ? "stopped" : questions ? "needs" : "ok";
    const tone = {unknown: "unknown", bad: "bad", stopped: "attention", needs: "attention", ok: "ok"}[verdict];
    const freshness = dataAge > 180 ? "bad" : dataAge > 60 ? "attention" : "ok";
    return {active, supOk, last, observed, verdict, tone, freshness, questions, blocked: count("blocked"), review: count("review"), queued};
  }

  function verdictText(f, now = Date.now() / 1000) {
    const why = f.questions ? T.whyAnswers : f.queued ? T.whyQueued(f.queued) : T.whyEmpty;
    return {unknown: () => T.noData(clock(f.observed)), bad: () => T.supervisorDown, ok: () => T.allWorking(f.active),
      needs: () => T.needsYou(f.questions, f.active),
      stopped: () => f.last ? T.stoppedFor(duration(now - f.last), why) : T.stopped(why)}[f.verdict]();
  }

  // The one trusted verdict comes from GET /api/health (observability.verdict). Without it (old service, fetch
  // failed, or older than 2 min) the local logic above is used unchanged.
  const REMOTE_TONE = {working: "ok", idle_with_work: "attention", owner_wait: "attention", stopped: "bad", unknown: "unknown"};
  let remote = null, remoteAt = 0, remoteBusy = false;
  async function loadVerdict() {
    if (remoteBusy || typeof fetch !== "function") return;
    remoteBusy = true;
    try {
      const response = await fetch("/api/health", {credentials: "same-origin", cache: "no-store"});
      const body = response.ok ? await response.json() : null;
      remote = body && body.verdict && REMOTE_TONE[body.verdict.verdict] ? body.verdict : null;
      remoteAt = Date.now() / 1000;
    } catch (_) { remote = null; } finally { remoteBusy = false; }
  }
  function remoteVerdict(now = Date.now() / 1000) {
    if (!remote || now - remoteAt > 120) return null;
    const d = remote.detail || {};
    let text = (T.v[d.code] || (() => remote.reason))(d);
    if (remote.verdict !== "working" && remote.since_min >= 1) text += ` · ${T.dur.m(remote.since_min).replace(/^/, EN ? "for " : "מזה ")}`;
    return {tone: REMOTE_TONE[remote.verdict], text, kind: remote.verdict};
  }

  function renderStatus(data, age, complete) {
    const f = factory(data);
    const rv = remoteVerdict();
    const title = rv ? rv.text : verdictText(f);
    if (rv) f.tone = rv.tone;
    $("#ov-status").dataset.tone = f.tone;
    $("#ov-status-icon").textContent = {ok: "✓", attention: "!", bad: "✕", unknown: "?"}[f.tone];
    // The one polite live region: written only when the sentence changes, never on every refresh.
    if ($("#ov-status-title").textContent !== title) $("#ov-status-title").textContent = title;
    $("#ov-status-detail").textContent = (rv ? rv.kind === "working" : f.verdict === "ok" || f.verdict === "needs") ? T.supOk : "";
    const freshEl = $("#ov-fresh");
    freshEl.textContent = T.updated(age(data.observed_at));
    freshEl.dataset.age = f.freshness;
    const merged = doneToday(data, complete).merged;
    const fact = (label, n, tone, href) => {
      const inner = `<span class="ov-fact-n">${n}</span><span class="ov-fact-label">${esc(label)}</span>`;
      return `<li class="ov-fact${n && tone ? ` ${tone}` : ""}"><a href="${href}">${inner}</a></li>`;
    };
    put("#ov-facts", fact(T.fQuestions, f.questions, "attention", "#questions") + fact(T.fQueued, f.queued, "", "#roadmap")
      + fact(T.fReview, f.review, "", "#work") + fact(T.fMerged, merged, "done", "#work"));
    $("#ov-facts").setAttribute("aria-label", T.factsLabel);
  }

  // Done today: three separate counts of required-or-not tasks whose last update is today. Never summed.
  function doneToday(data, complete, now = Date.now()) {
    const midnight = new Date(now); midnight.setHours(0, 0, 0, 0);
    const today = data.tasks.filter(task => seconds(task.updated_at) >= midnight.getTime() / 1000);
    const verifiedState = task => ["verified", "done"].includes(task.state);
    return {
      merged: today.filter(task => task.implementation === "merged" || verifiedState(task)).length,
      verified: today.filter(verifiedState).length,
      shipped: today.filter(task => complete(task)).length,
    };
  }

  function renderDone(data, complete) {
    const d = doneToday(data, complete);
    const tile = (n, label, cls) => `<li class="ov-done-tile ${cls}"><span class="ov-done-n">${n}</span><span class="ov-done-label">${esc(label)}</span></li>`;
    const tiles = `<ul class="ov-done-tiles">${tile(d.merged, T.doneMerged, "merged")}${tile(d.verified, T.doneVerified, "verified")}${tile(d.shipped, T.doneShipped, "shipped")}</ul>`;
    put("#ov-done", (d.merged || d.verified || d.shipped ? tiles : `<p class="today-empty">${esc(T.doneEmpty)}</p>`) + `<p class="ov-note">${esc(T.doneNote)}</p>`);
    const alerts = (data.alerts || []).length;
    $("#ov-more-count").textContent = alerts ? `· ${T.alerts(alerts)}` : "";
  }

  function setView() {
    const name = location.hash.replace(/^#/, "").split("&")[0];
    document.body.dataset.view = VIEWS.has(name) ? name : "overview";
  }

  function effectiveTheme() {
    return root.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  }
  function syncThemeButton() {
    const button = $("#theme-toggle");
    if (!button) return;
    const light = effectiveTheme() === "light";
    $(".theme-label", button).textContent = light ? T.themeDark : T.themeLight;
    button.setAttribute("aria-pressed", String(light));
  }

  let last = null;
  document.addEventListener("workflow:render", event => {
    const {data, complete = () => false, age = () => ""} = event.detail || {};
    if (!data || !Array.isArray(data.tasks) || !Array.isArray(data.agents) || !Array.isArray(data.questions)) return;
    last = {data, complete, age};
    renderStatus(data, age, complete);
    renderDone(data, complete);
  });
  // app.js re-renders only when the state changes; freshness ("stopped since", "updated") still moves with the clock.
  setInterval(() => {
    if (document.hidden) return;
    loadVerdict().then(() => { if (last) renderStatus(last.data, last.age, last.complete); });
  }, 15000);
  // Morning report: read-only GET /api/report, fetched when the block is opened.
  async function loadReport() {
    const box = $("#ov-report-text");
    box.textContent = T.reportLoading;
    try {
      const response = await fetch("/api/report", {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error(String(response.status));
      box.textContent = (await response.json()).he || "";
    } catch (_) { box.textContent = T.reportFailed; }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (EN) document.querySelectorAll("[data-i18n]").forEach(el => { const text = T.static[el.dataset.i18n]; if (text) el.textContent = text; });
    $("#ov-status-title").textContent = T.loading;
    setView();
    loadVerdict();
    $("#ov-report")?.addEventListener("toggle", event => { if (event.target.open) loadReport(); });
    window.addEventListener("hashchange", setView);
    syncThemeButton();
    matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", syncThemeButton);
    $("#theme-toggle")?.addEventListener("click", () => {
      const next = effectiveTheme() === "light" ? "dark" : "light";
      root.dataset.theme = next;
      try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* remembered for this page only */ }
      syncThemeButton();
    });
  });
})();
