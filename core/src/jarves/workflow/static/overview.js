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
    needsYou: (q, n, shown) => `Needs you: ${q === 1 ? "1 question" : shown && shown < q ? `${shown} of ${q} questions` : `${q} questions`}${n ? ` · ${n === 1 ? "1 worker is active" : `${n} workers are active`}` : ""}`,
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
      more: "More details: team, active tasks, events and alerts", report: "Morning report",
      now_title: "What each worker is doing now", now_loading: "Loading worker activity", restarts_title: "Supervisor stops and starts",
      restarts_more: "Full timeline", manager_title: "Manager log", manager_note: "Last 20 actions: kind and time only"},
    // #789: Overview "now" panels (GET /api/now, read-only metadata).
    now: {
      noWorkers: "No worker is running right now.", noTask: "No task claimed", noTrace: "No recorded action yet",
      failed: "This data is not available now. It refreshes by itself.", ago: a => `${a} ago`, just: "just now",
      more: (shown, total) => `Showing ${shown} of ${total}`, traceOff: "Action recording is off, so there is no last action.",
      act: {PreToolUse: t => `Running ${t}`, PostToolUse: t => `Finished ${t}`, PostToolUseFailure: t => `${t} failed`,
        Stop: () => "Stopped and waiting", SubagentStop: () => "Worker finished", SubagentStart: () => "Worker started",
        SessionStart: () => "Session started", SessionEnd: () => "Session ended", Notification: () => "Notification",
        UserPromptSubmit: () => "New instruction received"},
      other: e => `Event ${e}`, tool: "a tool", ms: n => n >= 1000 ? `${Math.round(n / 1000)} s` : `${n} ms`,
      noManager: "No manager session is known yet.", noActions: "The manager has no recorded actions yet.",
      stripLabel: (s, p, g) => `Last 24 hours: ${s} starts, ${p} stops, ${g} silent gaps`,
      start: "Started", stop: "Stopped", status: "Status change", gap: d => `Silent for ${d}`,
      stripEmpty: "No stop or start in the last 24 hours.", from: "24 h ago", to: "Now"},
  } : {
    allWorking: n => `הכול עובד: ${n === 1 ? "עובד אחד פעיל" : `${n} עובדים פעילים`}`,
    needsYou: (q, n, shown) => `צריך אותך: ${q === 1 ? "שאלה אחת" : shown && shown < q ? `${shown} מתוך ${q} שאלות` : `${q} שאלות`}${n ? ` · ${n === 1 ? "עובד אחד פעיל" : `${n} עובדים פעילים`}` : ""}`,
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
      more: "עוד פרטים: הצוות, משימות פעילות, אירועים והתראות",
      now_title: "מה כל עובד עושה עכשיו", now_loading: "טוען את פעילות העובדים", restarts_title: "עצירות והפעלות של המשגיח",
      restarts_more: "לציר הזמן המלא", manager_title: "יומן המנהל", manager_note: "20 הפעולות האחרונות: סוג וזמן בלבד"},
    now: {
      noWorkers: "אין עובד פעיל כרגע.", noTask: "בלי משימה רשומה", noTrace: "עדיין לא נרשמה פעולה",
      failed: "הנתונים האלה לא זמינים כרגע. הם יתעדכנו מעצמם.", ago: a => `לפני ${a}`, just: "עכשיו",
      more: (shown, total) => `מוצגים ${shown} מתוך ${total}`, traceOff: "רישום הפעולות כבוי, לכן אין פעולה אחרונה.",
      act: {PreToolUse: t => `מריץ ${t}`, PostToolUse: t => `סיים ${t}`, PostToolUseFailure: t => `${t} נכשל`,
        Stop: () => "עצר וממתין", SubagentStop: () => "עובד סיים", SubagentStart: () => "עובד התחיל",
        SessionStart: () => "שיחה התחילה", SessionEnd: () => "שיחה הסתיימה", Notification: () => "התראה",
        UserPromptSubmit: () => "התקבלה הנחיה חדשה"},
      other: e => `אירוע ${e}`, tool: "כלי", ms: n => n >= 1000 ? `${Math.round(n / 1000)} שנ׳` : `${n} מ״ש`,
      noManager: "עדיין לא ידוע מי המנהל.", noActions: "עדיין לא נרשמו פעולות של המנהל.",
      stripLabel: (s, p, g) => `24 השעות האחרונות: ${s} הפעלות, ${p} עצירות, ${g} פערי שקט`,
      start: "הופעל", stop: "נעצר", status: "שינוי מצב", gap: d => `שקט במשך ${d}`,
      stripEmpty: "לא היו עצירות או הפעלות ב־24 השעות האחרונות.", from: "לפני 24 שע׳", to: "עכשיו"},
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
    return {active, supOk, last, observed, verdict, tone, freshness, questions, shown: data.questions_view ? data.questions_view.shown_open : 0, blocked: count("blocked"), review: count("review"), queued};
  }

  function verdictText(f, now = Date.now() / 1000) {
    const why = f.questions ? T.whyAnswers : f.queued ? T.whyQueued(f.queued) : T.whyEmpty;
    return {unknown: () => T.noData(clock(f.observed)), bad: () => T.supervisorDown, ok: () => T.allWorking(f.active),
      needs: () => T.needsYou(f.questions, f.active, f.shown),
      stopped: () => f.last ? T.stoppedFor(duration(now - f.last), why) : T.stopped(why)}[f.verdict]();
  }

  // The one trusted verdict comes from GET /api/health (observability.verdict). Without it (old service, fetch
  // failed, or older than 2 min) the local logic above is used unchanged.
  const REMOTE_TONE = {working: "ok", idle_with_work: "attention", owner_wait: "attention", stopped: "bad", unknown: "unknown"};
  let remote = null, remoteAt = 0, remoteBusy = false, remoteFocus = null;
  async function loadVerdict() {
    if (remoteBusy || typeof fetch !== "function") return;
    remoteBusy = true;
    try {
      const response = await fetch("/api/health", {credentials: "same-origin", cache: "no-store"});
      const body = response.ok ? await response.json() : null;
      remote = body && body.verdict && REMOTE_TONE[body.verdict.verdict] ? body.verdict : null;
      remoteFocus = body && body.focus && typeof body.focus.he === "string" ? body.focus : null;
      remoteAt = Date.now() / 1000;
    } catch (_) { remote = null; remoteFocus = null; } finally { remoteBusy = false; }
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
    // One short focus line from the same /api/health answer (workflow/focus.py): e.g. "מיקוד: מיזוג — 9 PR פתוחים".
    const focusEl = $("#ov-focus");
    const focusText = remoteFocus && Date.now() / 1000 - remoteAt <= 120 ? (EN ? remoteFocus.en : remoteFocus.he) : "";
    focusEl.hidden = !focusText;
    if (focusEl.textContent !== focusText) focusEl.textContent = focusText;
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

  // ---- #789: what each worker is doing now, supervisor stop/start strip and the manager log (GET /api/now) ----
  const N = T.now;
  const ago = (ts, now) => {
    const sec = Math.max(0, now - seconds(ts));
    return sec < 60 ? N.just : N.ago(duration(sec));
  };
  function actionText(a) {
    const fn = N.act[a.event];
    return fn ? fn(a.tool || N.tool) : N.other(a.event || "?");
  }
  function workerItem(w, now, mode) {
    const task = w.task_id ? `<span class="ov-worker-task" dir="auto"><b>${esc(w.task_id)}</b>${w.task_title ? ` · ${esc(w.task_title)}` : ""}</span>`
      : `<span class="ov-worker-task muted">${esc(N.noTask)}</span>`;
    const a = w.last;
    const line = a ? `<span class="ov-worker-line${a.status === "failed" ? " bad" : ""}"><span dir="auto">${esc(actionText(a))}</span> · <time>${esc(ago(a.ts, now))}</time>${a.hint ? ` · <span dir="auto">${esc(a.hint)}</span>` : ""}</span>`
      : `<span class="ov-worker-line muted">${esc(mode === "off" ? N.traceOff : N.noTrace)}</span>`;
    return `<li class="ov-worker"><span class="ov-worker-name" dir="auto">${esc(w.label)}</span>${task}${line}</li>`;
  }
  function renderWorkers(view) {
    const list = view.workers || [];
    $("#ov-now-count").textContent = view.workers_total ? String(view.workers_total) : "";
    let html = list.length ? list.map(w => workerItem(w, view.now, view.trace_mode)).join("") : `<li class="today-empty">${esc(N.noWorkers)}</li>`;
    if (view.workers_total > list.length) html += `<li class="ov-note">${esc(N.more(list.length, view.workers_total))}</li>`;
    put("#ov-workers", html);
  }
  function renderStrip(view) {
    const r = view.restarts || {events: [], gaps: []}, span = Math.max(1, view.now - view.since);
    const x = ts => { const p = Math.min(1000, Math.max(0, (ts - view.since) / span * 1000)); return EN ? p : 1000 - p; };
    const label = N.stripLabel(r.starts || 0, r.stops || 0, (r.gaps || []).length);
    if (!r.events.length && !r.gaps.length) { put("#ov-strip", `<p class="today-empty">${esc(N.stripEmpty)}</p>`); return; }
    const gaps = r.gaps.map(g => { const a = x(g.start), b = x(g.end); return `<rect class="ov-strip-gap" x="${Math.min(a, b).toFixed(1)}" y="2" width="${Math.max(3, Math.abs(b - a)).toFixed(1)}" height="20"></rect>`; }).join("");
    const marks = r.events.map(e => `<rect class="ov-strip-${e.kind}" x="${(x(e.ts) - 2).toFixed(1)}" y="0" width="4" height="24"></rect>`).join("");
    const svg = `<svg class="ov-strip-svg" viewBox="0 0 1000 24" preserveAspectRatio="none" role="img" aria-label="${esc(label)}"><rect class="ov-strip-bg" x="0" y="8" width="1000" height="8"></rect>${gaps}${marks}</svg>`;
    const axis = `<div class="ov-strip-axis" aria-hidden="true"><span>${esc(N.from)}</span><span>${esc(N.to)}</span></div>`;
    const rows = [...r.events.map(e => ({ts: e.ts, kind: e.kind, text: N[e.kind] || e.kind, title: e.summary})),
      ...r.gaps.map(g => ({ts: g.start, kind: "gap", text: N.gap(duration(g.seconds)), title: ""}))]
      .sort((a, b) => b.ts - a.ts).slice(0, 6);
    const list = `<ol class="ov-strip-list">${rows.map(e => `<li class="ov-strip-item ${e.kind}"${e.title ? ` title="${esc(e.title)}"` : ""}><time>${esc(clock(e.ts))}</time> <span dir="auto">${esc(e.text)}</span></li>`).join("")}</ol>`;
    put("#ov-strip", `<p class="ov-note">${esc(label)}</p>${svg}${axis}${list}`);
  }
  function renderManager(view) {
    const m = view.manager;
    if (!m) { put("#ov-manager-log", `<p class="today-empty">${esc(N.noManager)}</p>`); return; }
    if (!m.actions.length) { put("#ov-manager-log", `<p class="today-empty">${esc(view.trace_mode === "off" ? N.traceOff : N.noActions)}</p>`); return; }
    put("#ov-manager-log", `<ol class="ov-log">${m.actions.map(a => `<li class="ov-log-row${a.status === "failed" ? " bad" : ""}"><time>${esc(clock(a.ts))}</time><span dir="auto">${esc(actionText(a))}</span>${a.duration_ms != null ? `<span class="ov-log-ms">${esc(N.ms(a.duration_ms))}</span>` : ""}</li>`).join("")}</ol>`);
  }
  const NOW_BOXES = ["#ov-workers", "#ov-strip", "#ov-manager-log"];
  let nowBusy = false;
  async function loadNow() {
    if (nowBusy || typeof fetch !== "function" || !$("#ov-workers")) return;
    nowBusy = true;
    try {
      const response = await fetch("/api/now", {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error(String(response.status));
      const view = await response.json();
      if (!view || !Array.isArray(view.workers)) throw new Error("shape");
      renderWorkers(view); renderStrip(view); renderManager(view);
    } catch (_) {
      // Keep the last good panels; only an empty (first) load shows the error line.
      NOW_BOXES.forEach(id => { if (!signatures[id]) put(id, `<p class="today-empty">${esc(N.failed)}</p>`); });
    } finally {
      NOW_BOXES.forEach(id => $(id)?.setAttribute("aria-busy", "false"));
      nowBusy = false;
    }
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
    // The first render means the private session is ready: load the "now" panels at once instead of waiting 15 s.
    if (!last) loadNow();
    last = {data, complete, age};
    renderStatus(data, age, complete);
    renderDone(data, complete);
  });
  // app.js re-renders only when the state changes; freshness ("stopped since", "updated") still moves with the clock.
  setInterval(() => {
    if (document.hidden) return;
    loadVerdict().then(() => { if (last) renderStatus(last.data, last.age, last.complete); });
    if (document.body.dataset.view === "overview") loadNow();
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
    loadNow();
    $("#ov-report")?.addEventListener("toggle", event => { if (event.target.open) loadReport(); });
    window.addEventListener("hashchange", () => { setView(); if (document.body.dataset.view === "overview") loadNow(); });
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
