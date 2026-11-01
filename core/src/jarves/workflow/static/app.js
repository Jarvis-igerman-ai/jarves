/* JARVES workflow UI. No embedded sample state: every visible record comes from /api/state. */
"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const stateNames = {queued: "בתור", running: "בעבודה", waiting_owner: "ממתין לך", blocked: "חסום", review: "בבדיקה", verified: "מאומת", done: "הושלם", idle: "פנוי", offline: "מנותק", stale: "אין עדכון", pending: "ממתין", deferred: "נשמר להמשך", answered: "נענה"};
  const laneNames = {bugs: "תיקון באגים", release: "בדיקות ושחרור", features: "פיצ'רים", ops: "תשתית ותהליך", security: "אבטחה", manager: "ניהול"};
  const implNames = {planned: "טרם התחיל", in_progress: "בפיתוח", merged: "מוזג"};
  const validationNames = {not_run: "טרם נבדק", automated: "אוטומטי", app: "תרחיש במוצר", device: "מכשיר אמיתי"};
  const deliveryNames = {none: "טרם הופץ", candidate: "מועמד", beta: "בטא", stable: "יציבה"};
  const versionDescriptions = {"V4.5.1": "ייצוב, תיקונים ותהליך שחרור", V5: "אותו JARVES, גם בטלפון", V6: "קול, נסיעות ושימוש ברכב", V7: "Android, דפדפן וכלי פיתוח"};
  const versions = ["V4.5.1", "V5", "V6", "V7"];
  const validationRank = {not_run: 0, automated: 1, app: 2, device: 3};
  const views = {overview: ["תמונת מצב", "העבודה, בתמונה אחת", "מי עובד, מה התקדם ומה צריך אותך — בלי לאבד את ההמשך.", "DEVELOPMENT / LIVE OVERVIEW"], roadmap: ["התוכנית המלאה", "כל הדרך קדימה", "מה מומש, מה נבדק ומה שוחרר — כל שלב עם ההוכחה שלו.", "DEVELOPMENT / FULL ROADMAP"], questions: ["ממתין להחלטה", "התשובות שמקדמות", "כל השאלות במקום אחד. תשובה עכשיו, או שמירה להמשך בלי לאבד משימה.", "DEVELOPMENT / DECISIONS"]};
  let data = null;
  let view = "overview";
  let filter = "all";
  let roadmapVersion = "all";
  const expandedVersions = new Map();
  let connected = false;
  let lastGoodAt = 0;
  let requestInFlight = null;
  let refreshAgain = false;
  let stream = null;
  let toastTimer;
  let dialogTaskId = null, dialogKey = "";
  const busyButtons = new Set();
  let questionsSignature = "";
  let lastPresenceRefresh = 0;
  const DRAFTS_KEY = "jarves-workflow-question-drafts";
  const drafts = (() => { try { const saved = JSON.parse(localStorage.getItem(DRAFTS_KEY) || "[]"); return new Map(Array.isArray(saved) ? saved : []); } catch { return new Map(); } })();
  const GENERAL = "__general";
  // One-click "Get more info": a fixed follow-up the manager recognises and answers with `workflow reply`.
  const INFO_TEXT = "Explain this question: why am I asked, what each option does, what you recommend, and what happens if I skip it.";
  const INFO_IDLE_S = 180;
  // Console locale part for the question explanation labels (page language: <html lang>, Hebrew by default).
  const I18N = {
    he: {moreInfo: "קבל עוד מידע", moreInfoTitle: "המנהל יסביר: למה שואלים, מה כל אפשרות עושה, מה הוא ממליץ ומה קורה אם מדלגים",
      waiting: n => n < 1 ? "נשלח למנהל · ממתין לתשובה (פחות מדקה)" : `נשלח למנהל · ממתין לתשובה כבר ${n} דק׳`,
      idle: "המנהל כנראה במנוחה. עברו לחלון של Claude, או המתינו לאירוע הבא של עובד. ההודעה נשמרה ותימסר בסיום הצעד הבא שלו.",
      replied: "המנהל הסביר. התשובה מופיעה בשרשור למטה.", sent: "הבקשה נשמרה ותימסר למנהל בסיום הצעד הנוכחי שלו. שום דבר לא אושר.",
      why: "למה שואלים", steps: "מה אתה עושה", noSteps: "אין עדיין צעדים כתובים. לחצו על ״קבל עוד מידע״ והמנהל יכתוב אותם.",
      minutes: "זמן", about: n => `כ־${n} דק׳`, skip: "אם תדלג", recommended: "ההמלצה", details: "פרטים מקוריים", link: "פרטים ↗"},
    en: {moreInfo: "Get more info", moreInfoTitle: "The manager explains why you are asked, what each option does, what it recommends and what happens if you skip",
      waiting: n => n < 1 ? "Sent, waiting for the manager (under a minute)" : `Sent, waiting for the manager since ${n} min`,
      idle: "The manager is idle. Switch to the Claude window or wait for the next worker event. The message is saved and is delivered at its next step.",
      replied: "The manager explained. The reply is in the thread below.", sent: "Saved; the manager gets it when its current step ends. Nothing was approved.",
      why: "Why", steps: "What you do", noSteps: "No written steps yet. Click Get more info and the manager writes them.",
      minutes: "Minutes", about: n => `About ${n} min`, skip: "If you skip it", recommended: "Recommended", details: "Details", link: "Details ↗"},
  };
  const L = I18N[String(document.documentElement.lang || "he").toLowerCase().startsWith("en") ? "en" : "he"];
  views.work = ["באגים, מיזוגים ומדיניות", "מה פתוח ומה מותר לעשות", "באגים פתוחים ובקשות מיזוג יחד, והמדיניות שהמנוע האוטומטי פועל לפיה. שינוי מדיניות: jarvis workflow autopilot set.", "DEVELOPMENT / WORK AND POLICY"];
  views.trace = ["פעולות והיסטוריה", "מהתוכנית עד לפעולה", "סוכן, סשן וקריאת כלי — עכשיו ובהיסטוריה שנשמרה.", "DEVELOPMENT / ACTION HISTORY"];
  let traceRows = [], traceBefore = null, traceLive = true, traceBusy = false, traceUntil = Date.now() / 1000, traceRenderedKey = "", traceRefreshPending = false;

  function seconds(value) {
    if (typeof value === "number") return value > 1e12 ? value / 1000 : value;
    if (typeof value === "string" && value) {
      const n = Number(value);
      if (Number.isFinite(n)) return n > 1e12 ? n / 1000 : n;
      const d = Date.parse(value);
      return Number.isFinite(d) ? d / 1000 : 0;
    }
    return 0;
  }

  function age(value) {
    const ts = seconds(value);
    if (!ts) return "טרם דווח";
    const delta = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (delta < 10) return "עכשיו";
    if (delta < 60) return `לפני ${delta} שנ׳`;
    if (delta < 3600) return `לפני ${Math.floor(delta / 60)} דק׳`;
    if (delta < 86400) return `לפני ${Math.floor(delta / 3600)} שע׳`;
    return `לפני ${Math.floor(delta / 86400)} ימים`;
  }

  function timestamp(value) {
    const ts = seconds(value);
    return ts ? new Date(ts * 1000).toLocaleString("he-IL", {day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit"}) : "טרם דווח";
  }

  function safeURL(value) {
    try {
      const url = new URL(String(value));
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : "";
    } catch { return ""; }
  }

  function complete(task) {
    if (task.state !== "done") return false;
    const required = validationRank[task.required_validation] ?? Infinity;
    const actual = validationRank[task.validation] ?? 0;
    return Boolean(task.code_revision && task.artifact_ref) && ["beta", "stable"].includes(task.delivery) && actual >= required && (task.acceptance || []).length > 0 && task.acceptance.every(item => criterionPassed(task, item));
  }

  function criterionPassed(task, item) {
    return item.passed === true && Boolean(item.evidence) && Boolean(task.code_revision) && item.code_revision === task.code_revision;
  }

  function isRequired(task) { return !task.scope || task.scope === "required"; }
  // What is working now comes first (owner, #730): running, waiting for you, blocked, review, queued, then finished.
  const stateRank = {running: 0, waiting_owner: 1, blocked: 2, review: 3, queued: 4, verified: 5, done: 6};
  const versionRank = version => { const i = versions.indexOf(version); return i < 0 ? versions.length : i; };
  function taskOrder(a, b) {
    return (stateRank[a.state] ?? 4) - (stateRank[b.state] ?? 4) || (a.priority ?? 2) - (b.priority ?? 2)
      || versionRank(a.version) - versionRank(b.version) || String(a.id).localeCompare(String(b.id));
  }

  function scopeLabel(task) {
    return {optional: "אופציונלי", deferred: "נדחה להמשך", out_of_scope: "מחוץ להיקף"}[task.scope] || (isRequired(task) ? "" : "היקף טרם סווג");
  }

  function statusPill(status) {
    const tone = ["waiting_owner", "blocked", "deferred", "pending"].includes(status) ? " amber" : ["offline", "stale"].includes(status) ? " red" : ["queued", "idle", "review"].includes(status) ? " gray" : "";
    return `<span class="state-pill${tone}"><span class="status-dot" aria-hidden="true"></span>${esc(stateNames[status] || status || "לא ידוע")}</span>`;
  }

  function empty(title, body, compact = false, code = "") {
    return `<div class="empty-state${compact ? " compact" : ""}"><span class="empty-icon" aria-hidden="true">◌</span><h3>${esc(title)}</h3><p>${esc(body)}</p>${code ? `<code>${esc(code)}</code>` : ""}</div>`;
  }

  function toast(message, error = false) {
    const element = $("#toast");
    element.textContent = message;
    element.classList.toggle("error", error);
    element.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { element.hidden = true; }, error ? 9000 : 4500);
  }

  // #741: a 401/403 means the private session is gone. Stop everything, drop stale data and show ONE notice, never zeros.
  const OPEN_COMMAND = "jarvis workflow open";
  let ended = false;
  function sessionEnded() {
    if (ended) return;
    ended = true;
    data = null;
    try { stream?.close(); } catch (_) { /* already closed */ }
    $(".workspace")?.setAttribute("hidden", "");
    $("#toast")?.setAttribute("hidden", "");
    const page = document.createElement("div");
    page.id = "session-ended";
    page.className = "session-ended";
    page.setAttribute("role", "alert");
    page.innerHTML = `<div class="session-ended-card"><span class="empty-icon" aria-hidden="true">◌</span><h1>הסשן הפרטי הסתיים</h1><p>החיבור הפרטי למרכז העבודה פג, למשל אחרי הפעלה מחדש של המשגיח. כדי להמשיך, הריצו את הפקודה הבאה בטרמינל או פתחו את הקישור ש־claude-j הדפיס.</p><div class="session-ended-cmd"><code dir="auto">${esc(OPEN_COMMAND)}</code><button type="button" class="button primary" id="session-ended-copy">העתקת הפקודה</button></div><p class="session-ended-note" id="session-ended-note" aria-live="polite"></p></div>`;
    document.body.appendChild(page);
    $("#session-ended-copy").addEventListener("click", async () => {
      const note = $("#session-ended-note");
      try { await navigator.clipboard.writeText(OPEN_COMMAND); note.textContent = "הפקודה הועתקה."; }
      catch (_) { note.textContent = "ההעתקה נחסמה. סמנו את הפקודה והעתיקו ידנית."; }
    });
    $("#session-ended-copy").focus();
  }

  async function api(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const options = {credentials: "same-origin", cache: "no-store", signal: controller.signal};
      if (body !== undefined) {
        options.method = "POST";
        options.headers = {"Content-Type": "application/json", "X-Workflow-Request": "1"};
        options.body = JSON.stringify(body);
      }
      const response = await fetch(path, options);
      let result;
      try { result = await response.json(); } catch { result = {}; }
      if (!response.ok || result.ok === false) {
        const detail = typeof result.error === "string" ? result.error : result.error?.message;
        if (response.status === 401 || response.status === 403) {
          sessionEnded();
          const ended = new Error(detail || "נדרש לפתוח את הקישור המאומת שהשירות המקומי הציג.");
          ended.sessionEnded = true;
          throw ended;
        }
        throw new Error(detail || result.message || `הבקשה לא הושלמה (${response.status}).`);
      }
      return result;
    } finally { clearTimeout(timeout); }
  }

  function syncControls() {
    // Offline disables mutating controls; buttons with a request in flight stay disabled.
    $$("#dispatch, #add-task, #owner-present, #owner-away, .question-form button[type=submit], .ask-more-form button[type=submit], [data-defer], [data-reopen], [data-more-info], #create-task-form button[type=submit]").forEach(button => { button.disabled = !connected || busyButtons.has(button) || button.dataset.waiting === "1"; });
  }

  function connectionState(ok, error = "") {
    connected = ok;
    const indicator = $("#connection");
    indicator.classList.toggle("offline", !ok);
    indicator.innerHTML = `<span class="status-dot ${ok ? "good" : "warn"}" aria-hidden="true"></span>${ok ? "מחובר לשירות" : "החיבור נותק"}`;
    const banner = $("#connection-banner");
    banner.hidden = ok;
    banner.textContent = ok ? "" : `${error || "לא ניתן להגיע לשירות המקומי."}${data ? " הנתונים המוצגים הם העדכון האחרון שהתקבל; השלמות חדשות לא יוצגו עד לחיבור מחדש." : " הפעל את השירות ופתח את הקישור המאומת שהוא מציג."}`;
    $("#agents-live-label").innerHTML = `<span class="status-dot ${ok ? "good" : "warn"}" aria-hidden="true"></span>${ok ? "מצב מדווח" : "נתונים אחרונים"}`;
    syncControls();
    $$("#trace-agent, #trace-task, #trace-range, #trace-from, #trace-to, #trace-mode").forEach(field => { field.disabled = !ok; });
    updateTraceControls();
    if (!ok && !data) {
      $("#metrics").innerHTML = "";
      $("#agents-offline-wrap").innerHTML = "";
      $("#agents").innerHTML = empty("סביבת העבודה עדיין לא מחוברת", "המסך לא ממציא סוכנים או התקדמות. אחרי חיבור לשירות המקומי יופיע כאן המצב המדווח.");
      $("#active-tasks").innerHTML = empty("אין מידע זמין", "ממתינים לחיבור מאומת לשירות.", true);
      $("#events").innerHTML = "";
      $("#roadmap").innerHTML = empty("התוכנית תופיע אחרי החיבור", "המשימות נשמרות בשירות המקומי. אין כאן תוכנית לדוגמה שמוצגת כמצב חי.");
      $("#questions").innerHTML = empty("ממתינים לחיבור", "שאלות ותשובות נשמרות בשירות המקומי.");
    }
  }

  async function refresh() {
    if (ended) return;
    if (requestInFlight) { refreshAgain = true; return requestInFlight; }
    requestInFlight = (async () => {
      try {
        const next = await api("/api/state");
        if (!Array.isArray(next.tasks) || !Array.isArray(next.agents) || !Array.isArray(next.questions)) throw new Error("התקבל עדכון בפורמט לא מוכר.");
        const changed = !data || next.revision !== data.revision || !connected;
        data = next;
        lastGoodAt = Date.now();
        connectionState(true);
        renderLiveStatus();
        if (changed) render();
      } catch (error) { if (!error.sessionEnded) connectionState(false, error.name === "AbortError" ? "השירות לא ענה בזמן." : error.message); }
      finally {
        requestInFlight = null;
        if (refreshAgain) { refreshAgain = false; queueMicrotask(refresh); }
      }
    })();
    return requestInFlight;
  }

  function renderLiveStatus() {
    if (!data) return;
    $("#last-update").textContent = `עדכון ${age(data.observed_at)}`;
    $("#last-update").dateTime = seconds(data.observed_at) ? new Date(seconds(data.observed_at) * 1000).toISOString() : "";
    const supervisor = data.supervisor || {};
    const heartbeat = seconds(supervisor.heartbeat_at);
    const fresh = Boolean(heartbeat && Date.now() / 1000 - heartbeat < 120);
    const status = supervisor.status || (fresh ? "running" : "unknown");
    const good = fresh && ["running", "watching"].includes(status);
    $("#watchdog-indicator").innerHTML = `<span class="status-dot ${good ? "good" : "warn"}" aria-hidden="true"></span><span>${data.source?.mode === "demo" ? "משגיח — הדגמה בלבד" : good ? "המשגיח החיצוני פעיל" : heartbeat && status === "degraded" ? "המשגיח פועל במצב מוגבל" : heartbeat ? "אין עדכון טרי מהמשגיח" : "המשגיח טרם התחבר"}</span>`;
    const owner = data.owner || {};
    const present = owner.effective_mode === "present";
    $("#owner-present").setAttribute("aria-pressed", String(present));
    $("#owner-away").setAttribute("aria-pressed", String(!present));
    $("#owner-title").textContent = present ? "אתה כאן. אפשר לקבל החלטות ולהתקדם." : "הצוות ממשיך. מה שצריך אותך נשמר.";
    $("#owner-description").textContent = present ? "השאלות מרוכזות בתור ההחלטות; תשובה לא נחשבת לאישור פעולה חיצונית." : "משימה שממתינה לך נשארת גלויה, ועבודה עצמאית יכולה להמשיך.";
    const source = data.source || {};
    const unmapped = Array.isArray(source.unmapped) ? source.unmapped.length : Number(source.unmapped) || 0;
    const sourceBanner = $("#source-banner");
    const oldSource = source.mode === "github" && seconds(source.synced_at) && Date.now() / 1000 - seconds(source.synced_at) > 900;
    const notices = [];
    if (source.mode === "demo") notices.push("סביבת הדגמה — הנתונים כאן נועדו לבדוק את התהליך ואינם מצב הפיתוח האמיתי של JARVES.");
    if (source.error) notices.push(`סנכרון המקור אינו זמין. מוצג המידע האחרון שהתקבל${source.synced_at ? ` (${age(source.synced_at)})` : ""}; אין להסיק ממנו מצב עדכני של GitHub.`);
    else if (oldSource) notices.push(`נתוני GitHub לא סונכרנו לאחרונה (${age(source.synced_at)}). מצב הסוכנים המקומי מוצג בנפרד.`);
    if (source.truncated) notices.push("המקור הגיע למגבלת הסנכרון; הרשימה עשויה להיות חלקית.");
    if (unmapped) notices.push(`מיפוי התוכנית עדיין לא מלא: ${unmapped} פריטים דורשים שיוך או בירור. אין להסיק מכאן שהגרסאות הושלמו.`);
    if (source.mode === "scope") notices.push("היקף התוכנית נטען. מצב הקוד והבדיקות עדיין דורש מיפוי מול GitHub והראיות; דרישות התוכנית אינן הוראות ביצוע אוטומטיות.");
    sourceBanner.hidden = !notices.length;
    sourceBanner.textContent = notices.join(" ");
    const parts = [];
    if (source.sha) parts.push(`מקור ${String(source.sha).slice(0, 8)}`);
    if (source.synced_at) parts.push(`סונכרן ${age(source.synced_at)}`);
    if (source.mode === "demo") parts.push("הדגמה");
    else if (source.mode) parts.push(source.mode === "github" ? "GitHub" : source.mode === "scope" ? "היקף תוכנית" : "נתונים מקומיים");
    $("#source-detail").textContent = parts.join(" · ") || "המצב מוצג מהשירות המקומי בלבד";
  }

  function agentStale(agent) {
    const beat = seconds(agent.heartbeat_at);
    const tool = seconds(agent.tool_started_at);
    if (tool && Date.now() / 1000 - tool < 900) return false;
    return Boolean(agent.status === "running" && (!beat || Date.now() / 1000 - beat > 120));
  }

  // #732 S1 count truth: workers = live running+claimed agents; running and in-review tasks are never added together;
  // required progress is merged / verified / shipped, not one flat "done".
  function renderMetrics() {
    const tasks = data.tasks;
    const required = tasks.filter(isRequired);
    const merged = required.filter(task => task.implementation === "merged" || ["verified", "done"].includes(task.state)).length;
    const verified = required.filter(task => ["verified", "done"].includes(task.state)).length;
    const shipped = required.filter(complete).length;
    // same source as the "now" strip: agents holding a running/claimed task, plus live running/claimed agents with no task
    const holders = new Set(tasks.filter(task => ["running", "claimed"].includes(task.state) && task.agent_id).map(task => task.agent_id));
    const workers = holders.size + data.agents.filter(agent => ["running", "claimed"].includes(agent.status) && !agentStale(agent) && !holders.has(agent.id) && !agent.task_id).length;
    const waiting = tasks.filter(task => task.state === "waiting_owner").length;
    const running = tasks.filter(task => task.state === "running").length;
    const inReview = tasks.filter(task => task.state === "review").length;
    const ready = tasks.filter(task => task.ready === true).length;
    const problems = (data.alerts || []).length;
    const pending = data.questions.filter(question => ["pending", "deferred", "open"].includes(question.status || "pending")).length;
    $("#questions-count").textContent = pending;
    const total = required.length;
    const split = [["מוזגו", merged], ["אומתו", verified], ["נשלחו", shipped]];
    const cards = [
      {label: "סוכנים בעבודה", symbol: "◈", value: workers, detail: `${(data.agent_counts || {}).live ?? data.agents.length} סשנים חיים`, foot: problems ? (problems === 1 ? "בעיה אחת פתוחה" : `${problems} בעיות פתוחות`) : "הכול תקין", cls: problems ? "amber" : ""},
      {label: "משימות בריצה", symbol: "↗", value: running, detail: `${inReview} בבדיקה`, foot: `${ready} זמינות להקצאה`, cls: ""},
      {label: "ממתין להחלטה שלך", symbol: "◇", value: pending, detail: waiting === 1 ? "משימה אחת" : `${waiting} משימות`, foot: "נשאר בתמונה, לא מדולג", cls: "amber"},
      {label: "דרישות חובה: נשלחו", symbol: "✓", value: shipped, detail: `מתוך ${total}`, foot: total ? split.map(([name, n]) => `${name} ${n}`).join(" · ") : "ממתין למיפוי התוכנית", cls: "done", split: total ? split.map(([name, n]) => [name, n / total * 100]) : null}
    ];
    const bar = card => card.split ? `<div class="metric-bar split" aria-hidden="true">${card.split.map(([name, pct]) => `<progress max="100" value="${pct}" title="${esc(name)}"></progress>`).join("")}</div>` : "";
    $("#metrics").innerHTML = cards.map((card, index) => `<a href="#${["overview", "roadmap", "questions", "roadmap"][index]}" data-overview-metric="${index}" class="metric ${card.cls}"><div class="metric-label"><span>${esc(card.label)}</span><span class="metric-symbol" aria-hidden="true">${card.symbol}</span></div><div class="metric-value">${card.value}<small dir="auto">${esc(card.detail)}</small></div><p class="metric-foot" dir="auto">${esc(card.foot)}</p>${bar(card)}</a>`).join("");
  }

  // #730: live workers first; offline ones behind a remembered toggle; archived ones only counted.
  const OFFLINE_KEY = "jarves.workflow.showOffline";
  let showOffline = false;
  try { showOffline = localStorage.getItem(OFFLINE_KEY) === "1"; } catch (_) { /* storage blocked: default hidden */ }
  const liveRank = {running: 0, blocked: 1, waiting_owner: 1, idle: 2};
  const lastSeen = agent => Math.max(seconds(agent.heartbeat_at) || 0, seconds(agent.progress_at) || 0);
  const countText = (n, one, many) => n === 1 ? one : `${n} ${many}`;

  function agentModel(agent) {
    const asked = agent.requested_model, seen = agent.observed_model;
    if (!asked && !seen) return "";
    const value = seen && asked && seen !== asked ? `${asked} → ${seen}` : (seen || asked);
    return `<p class="agent-model"><span>${seen ? "מודל" : "מודל מבוקש"}</span> <span dir="ltr">${esc(value)}</span></p>`;
  }

  function renderAgents() {
    const all = data.agents;
    const counts = data.agent_counts || {};
    const live = all.filter(a => a.status !== "offline")
      .sort((a, b) => (liveRank[a.status] ?? 3) - (liveRank[b.status] ?? 3) || lastSeen(b) - lastSeen(a));
    const offline = all.filter(a => a.status === "offline").sort((a, b) => lastSeen(b) - lastSeen(a));
    const archived = counts.archived || 0;
    const parts = [countText(live.length, "סוכן פעיל אחד", "פעילים")];
    if (offline.length) parts.push(countText(offline.length, "מנותק אחד", "מנותקים"));
    if (archived) parts.push(`${archived} בארכיון`);
    $("#agent-summary").textContent = all.length || archived ? parts.join(" · ") : "אין סוכנים רשומים";
    const symbols = {bugs: "⌘", features: "◇", release: "✓", ops: "◫", manager: "◎", security: "⌁"};
    $("#agents").innerHTML = live.length ? live.map((agent, i) => {
      const task = data.tasks.find(item => item.id === agent.task_id);
      return `<article class="agent-card ${esc(agent.status)}" data-agent-id="${esc(agent.id)}"><div class="agent-head"><span class="agent-avatar" aria-hidden="true">${symbols[agent.lane] || "◈"}</span><div><h3 class="agent-name">${esc(agent.label || agent.id || `סוכן ${i + 1}`)}</h3><p class="agent-lane">${esc(laneNames[agent.lane] || agent.lane || "טרם שויך תחום")}</p></div>${statusPill(agentStale(agent) ? "stale" : agent.status)}</div>${task ? `<button type="button" class="agent-link" data-task="${esc(task.id)}"><p class="agent-work">${esc(task.title)}</p><p class="agent-task-id">${esc(task.id)}</p></button>` : `<p class="agent-work no-task">${agent.status === "idle" ? "פנוי למשימה הבאה" : "לא דווחה משימה פעילה"}</p>`}${agentModel(agent)}<p class="agent-note">${esc(agent.note || "")}</p><div class="agent-footer"><span>אירוע אחרון ${esc(age(agent.heartbeat_at))}</span><span>התקדמות ${esc(age(agent.progress_at))}</span></div></article>`;
    }).join("") : all.length || archived
      ? empty("אין סוכן פעיל כרגע", "סוכנים שסיימו או התנתקו מרוכזים ברשימה המקופלת שמתחת.", true)
      : empty("הצוות עוד לא התחבר", "המנהל ותתי־הסוכנים של Claude Code יופיעו כאן אחרי דיווח מה־hooks המקומיים. לא מוצגים סוכנים משוערים או מחוברים למראית עין.");
    $$(".agent-card", $("#agents")).forEach(card => {
      const button = document.createElement("button"); button.type = "button"; button.className = "text-link agent-history";
      button.textContent = "ציר פעולות והיסטוריה ←"; button.dataset.agentHistory = card.dataset.agentId; card.append(button);
    });
    const wrap = $("#agents-offline-wrap");
    if (!offline.length && !archived) { wrap.innerHTML = ""; return; }
    const hours = Math.round((counts.retention_s || 86400) / 3600);
    const toggle = offline.length ? `<button type="button" class="text-link agents-offline-toggle" id="agents-offline-toggle" aria-expanded="${showOffline}" aria-controls="agents-offline"><span aria-hidden="true">${showOffline ? "▾" : "▸"}</span>${showOffline ? "הסתר" : "הצג"} ${countText(offline.length, "מנותק אחד", "מנותקים")}</button>` : "";
    const note = archived ? `<span class="small-label agents-archive-note">${archived} בארכיון (מנותקים מעל ${hours} שע׳ ושאריות בדיקה)</span>` : "";
    const list = offline.length ? `<ul class="agent-offline-list" id="agents-offline"${showOffline ? "" : " hidden"}>${offline.map(agent => `<li><button type="button" class="agent-offline-row" data-agent-history="${esc(agent.id)}"><span class="agent-offline-name">${esc(agent.label || agent.id)}</span><span class="agent-offline-lane">${esc(laneNames[agent.lane] || agent.lane || "")}</span><span class="agent-offline-age">${esc(age(lastSeen(agent)))}</span></button></li>`).join("")}</ul>` : "";
    wrap.innerHTML = `<div class="agents-offline-bar">${toggle}${note}</div>${list}`;
  }

  function renderWork() {
    const ap = data.autopilot || {policy: {lanes: {}}, work: []};
    const policy = ap.policy || {lanes: {}};
    const lane = name => (policy.lanes || {})[name] || {};
    $("#work-count").textContent = (ap.work || []).length;
    const state = policy.kill ? "כבוי בכפתור חירום" : policy.enabled ? "פעיל" : "כבוי (ברירת מחדל)";
    $("#autopilot-policy").innerHTML = `<h2>מדיניות המנוע האוטומטי</h2>${ap.error ? `<p class="form-error" role="alert">${esc(ap.error)}</p>` : ""}<p class="detail-list">מצב: <b>${esc(state)}</b> · גרסאות מאושרות: <span dir="ltr">${esc((policy.authorized_versions || []).join(", "))}</span> · סדר גרסאות: <span dir="ltr">${esc((policy.versions || []).join(" ← "))}</span></p><p class="detail-list">עובדים: באגים ${esc(lane("bugs").workers)}, פיצ'רים ${esc(lane("features").workers)} · מקסימום במקביל ${esc(policy.max_concurrent)} · התחלות בשעה ${esc(policy.max_starts_per_hour)}</p><p class="detail-list">תוויות באג: <span dir="ltr">${esc((policy.bug_labels || []).join(", "))}</span> · עצירה: <span dir="ltr">jarvis workflow autopilot stop</span></p>`;
    const rows = [...(ap.work || [])].sort((a, b) => (stateRank[a.task_state] ?? 4) - (stateRank[b.task_state] ?? 4) || (a.priority ?? 2) - (b.priority ?? 2)).map(w => `<a class="work-row" href="${esc(safeURL(w.url))}" target="_blank" rel="noopener noreferrer"><span class="work-state-icon ${w.priority < 2 ? "amber" : ""}" aria-hidden="true">${w.kind === "pr" ? "⇄" : "!"}</span><span class="work-row-main"><span class="work-row-title">${w.kind === "pr" ? "בקשת מיזוג" : "באג"} #${esc(w.number)} · ${esc(w.title)}</span><span class="work-row-meta"><span dir="ltr">${esc((w.labels || []).join(", "))}</span>${w.task_id ? `<span class="mono">${esc(w.task_id)}</span><span>${esc(stateNames[w.task_state] || w.task_state)}</span>` : "<span>ללא משימה</span>"}${w.kind === "pr" ? "<span>בדיקות: לא ידוע</span>" : ""}</span></span></a>`).join("");
    $("#work-list").innerHTML = `<h2>באגים פתוחים ובקשות מיזוג</h2>${rows || empty("אין פריטים פתוחים בתמונה האחרונה", ap.sync_error || "הרשימה מתעדכנת בסנכרון הקריאה מ־GitHub.", true)}`;
  }

  function renderAlerts() {
    const list = data.alerts || [];
    const section = $("#alerts-section");
    section.hidden = list.length === 0;
    section.innerHTML = list.map(alert => `<div class="alert"><span class="alert-symbol" aria-hidden="true">△</span><p>${esc(alert.summary)}</p>${alert.task_id ? `<button type="button" class="text-link" data-task="${esc(alert.task_id)}">למשימה ←</button>` : ""}</div>`).join("");
  }

  function renderActive() {
    const work = data.tasks.filter(task => ["running", "review", "waiting_owner", "blocked"].includes(task.state)).sort(taskOrder).slice(0, 7);
    $("#active-tasks").innerHTML = work.length ? work.map(task => `<button type="button" class="work-row" data-task="${esc(task.id)}"><span class="work-state-icon ${["waiting_owner", "blocked"].includes(task.state) ? "amber" : ""}" aria-hidden="true">${task.state === "waiting_owner" ? "◇" : task.state === "blocked" ? "!" : task.state === "review" ? "◌" : "↗"}</span><span class="work-row-main"><span class="work-row-title">${esc(task.title)}</span><span class="work-row-meta"><span class="mono">${esc(task.id)}</span><span dir="ltr">${esc(task.version || "ללא גרסה")}</span><span>${esc(stateNames[task.state] || task.state)}</span></span></span><span class="priority ${(task.priority ?? 2) < 2 ? "high" : ""}">P${esc(task.priority ?? 2)}</span></button>`).join("") : empty("אין משימות פעילות כרגע", data.tasks.length ? "משימות התור והחסימות זמינות בתוכנית המלאה. הכפתור 'בדוק הקצאה' משבץ רק סשנים שחוברו עם הקצאה אוטומטית. סוכני Claude רגילים לוקחים משימות דרך המנהל." : "הוסף משימה או ייבא את תוכנית העבודה כדי להתחיל.", true);
  }

  function renderEvents() {
    const events = [...(data.events || [])].sort((a, b) => (Number(b.id) || seconds(b.ts)) - (Number(a.id) || seconds(a.ts))).slice(0, 7);
    $("#events").innerHTML = events.length ? events.map(event => `<li><span class="event-point ${/question|wait|alert|block/.test(event.kind || "") ? "amber" : ""}" aria-hidden="true"></span><div class="event-body"><p>${esc(event.summary)}</p><time title="${esc(timestamp(event.ts))}">${esc(age(event.ts))}</time></div></li>`).join("") : `<li><div class="event-body"><p>האירוע הבא יופיע כאן ברגע שידווח. אין פעילות לדוגמה.</p></div></li>`;
  }

  function taskFields(task) {
    const acceptance = task.acceptance || [];
    const passed = acceptance.filter(item => criterionPassed(task, item)).length;
    const required = validationRank[task.required_validation] ?? Infinity;
    const actual = validationRank[task.validation] ?? 0;
    const fields = [
      ["קוד", implementationLabel(task), task.implementation === "merged"],
      ["אימות", validationNames[task.validation] || task.validation || "לא נבדק", Boolean(task.code_revision) && actual >= required],
      ["הפצה", deliveryNames[task.delivery] || task.delivery || "טרם הופץ", task.delivery === "stable"],
      ["קבלה", acceptance.length ? `${passed}/${acceptance.length}` : "לא הוגדרה", acceptance.length > 0 && passed === acceptance.length]
    ];
    return fields.map(([label, value, ok]) => `<span class="progress-field ${ok ? "passed" : ""}"><i aria-hidden="true">${ok ? "✓" : "·"}</i>${label}: <strong>${esc(value)}</strong></span>`).join("");
  }

  function implementationLabel(task) {
    return task.implementation === "planned" && task.execution_allowed === false ? "טרם מופה" : implNames[task.implementation] || task.implementation || "לא ידוע";
  }

  function sourceSummary(task) {
    const issues = task.source_issues || [];
    const prs = task.source_prs || [];
    const closed = issues.filter(item => item.state === "closed").length;
    const merged = prs.filter(item => item.merged_at).length;
    const open = prs.filter(item => item.state === "open").length;
    const parts = [];
    if (issues.length) parts.push(`${closed}/${issues.length} Issues סגורים`);
    if (merged) parts.push(`${merged} PR מוזגו`);
    if (open) parts.push(`${open} PR פתוחים`);
    if (!parts.length && task.execution_allowed === false) parts.push("מצב המקור טרם מופה");
    return parts.length ? `מקור: ${parts.join(" · ")}${task.validation === "not_run" ? " · אימות טרם תועד" : ""}` : "";
  }

  function mappingLabel(value) {
    return {"No granular issue mapped": "עדיין לא שויך Issue ממוקד לדרישה הזאת.", "Missing GitHub metadata; state remains unknown": "חסר מידע מ־GitHub. המצב נשאר לא ידוע.", "Issue/PR metadata is not installed-app verification": "מצב Issues ו־PRs מתאר את המקור; הוא אינו הוכחת בדיקה של האפליקציה המותקנת."}[value] || value;
  }

  function renderRoadmap() {
    if (!data) return;
    // Keep the owner's open/closed choice across live refreshes.
    const scopeOpen = Boolean($("#roadmap-context details.scope-note")?.open);
    $("#roadmap-context").innerHTML = data.source?.scope_note ? `<details class="scope-note"${scopeOpen ? " open" : ""}><summary>היקף התוכנית והמקור</summary><p dir="auto">${esc(data.source.scope_note)}</p><p>סגירת Issue או מיזוג PR מוצגים בנפרד מהוכחת תרחיש. פריט שטרם מופה נשאר לא ידוע.</p></details>` : "";
    const versionChoices = ["all", ...versions, ...new Set(data.tasks.map(t => t.version || "ללא גרסה").filter(v => !versions.includes(v)))];
    $("#version-nav").innerHTML = versionChoices.map(v => {
      const count = v === "all" ? data.tasks.length : data.tasks.filter(t => (t.version || "ללא גרסה") === v).length;
      return `<button type="button" class="version-filter${roadmapVersion === v ? " active" : ""}" data-version="${esc(v)}" aria-pressed="${roadmapVersion === v}"><span dir="auto">${esc(v === "all" ? "כל הגרסאות" : v)}</span><span class="version-count">${count}</span></button>`;
    }).join("");
    const search = $("#task-search").value.trim().toLocaleLowerCase("he-IL");
    let tasks = data.tasks.filter(task => !search || `${task.title} ${task.id} ${task.version}`.toLocaleLowerCase("he-IL").includes(search));
    if (roadmapVersion !== "all") tasks = tasks.filter(t => (t.version || "ללא גרסה") === roadmapVersion);
    if (filter === "queued") tasks = tasks.filter(t => t.state === "queued");
    if (filter === "active") tasks = tasks.filter(task => ["running", "review"].includes(task.state));
    if (filter === "waiting") tasks = tasks.filter(task => ["waiting_owner", "blocked"].includes(task.state));
    if (filter === "done") tasks = tasks.filter(complete);
    if (!tasks.length) {
      $("#roadmap").innerHTML = empty(data.tasks.length ? "אין משימות שמתאימות לסינון" : "התוכנית המלאה עדיין לא מופתה", data.tasks.length ? "נסה לבחור סינון אחר או לשנות את החיפוש." : "הוסף משימות או ייבא דרישות. כל סעיף יופיע עם סטטוס קוד, אימות, הפצה ותנאי קבלה — בלי להסיק שמיזוג הוא השלמה.");
      return;
    }
    const groups = [...versions, ...new Set(tasks.map(task => task.version || "ללא גרסה").filter(version => !versions.includes(version)))];
    $("#roadmap").innerHTML = groups.map(version => {
      const group = tasks.filter(task => (task.version || "ללא גרסה") === version);
      if (!group.length) return "";
      const fullGroup = data.tasks.filter(task => (task.version || "ללא גרסה") === version);
      const requiredGroup = fullGroup.filter(isRequired);
      const done = requiredGroup.filter(complete).length;
      const extraCount = fullGroup.length - requiredGroup.length;
      const expanded = search || filter !== "all" || roadmapVersion !== "all" || (expandedVersions.get(version) ?? version === versions[0]);
      return `<details class="version-section" ${expanded ? "open" : ""}><summary class="version-heading" data-version-toggle="${esc(version)}"><span class="version-title"><span class="version-number" role="heading" aria-level="2">${esc(version)}</span><span class="version-description">${esc(versionDescriptions[version] || "משימות משויכות")}<span class="version-scope-count">${fullGroup.length} פריטים בסך הכול${extraCount ? ` · ${extraCount} אופציונליים או נדחו` : ""}</span></span></span><span class="version-completion"><span>${done} / ${requiredGroup.length} חובה הושלמו</span><progress class="version-progress" aria-label="השלמת דרישות חובה של ${esc(version)}" value="${done}" max="${Math.max(1, requiredGroup.length)}"></progress><span class="version-chevron" aria-hidden="true">⌄</span></span></summary>${(() => { const sorted = group.sort(taskOrder); const open = sorted.filter(t => !complete(t)); const closed = sorted.filter(complete); return [open, closed]; })().map((part, partIndex) => !part.length ? "" : (partIndex ? `<details class="done-group"${filter === "done" ? " open" : ""}><summary class="text-link">${part.length === 1 ? "משימה אחת הושלמה" : `${part.length} משימות הושלמו`}</summary>` : "") + part.map(task => {
        const doneTask = complete(task);
        const icon = doneTask ? "✓" : task.state === "running" ? "↗" : ["waiting_owner", "blocked"].includes(task.state) ? "◇" : "";
        const iconClass = doneTask ? "complete" : task.state === "running" ? "running" : ["waiting_owner", "blocked"].includes(task.state) ? "waiting" : "";
        const agent = data.agents.find(item => item.id === task.agent_id);
        const source = sourceSummary(task);
        return `<button type="button" class="roadmap-row" data-task="${esc(task.id)}"><span class="task-check ${iconClass}" aria-hidden="true">${icon}</span><span><span class="roadmap-task-title">${esc(task.title)}</span><span class="task-progress-fields">${taskFields(task)}</span>${source ? `<span class="source-summary">${esc(source)}</span>` : ""}</span><span class="task-row-end">${scopeLabel(task) ? `<span class="pill scope-pill">${esc(scopeLabel(task))}</span>` : ""}${task.execution_allowed === false && task.state === "queued" ? `<span class="state-pill gray">למיפוי ואימות</span>` : statusPill(task.state)}<span class="task-row-agent">${esc(agent?.label || task.agent_id || task.id)}</span></span></button>`;
      }).join("") + (partIndex && part.length ? "</details>" : "")).join("")}</details>`;
    }).join("");
  }

  function stashDrafts() {
    $$(".question-form").forEach(form => {
      const picked = $$("input[type=radio]:checked, input[type=checkbox]:checked", form).map(input => input.value);
      drafts.set(form.dataset.question, {...(drafts.get(form.dataset.question) || {}), text: $("textarea", form).value, choice: picked});
    });
    $$(".ask-more-form").forEach(form => {
      drafts.set(form.dataset.followup, {...(drafts.get(form.dataset.followup) || {}), more: $("textarea", form).value});
    });
    for (const [key, value] of drafts) if (!value.text && !value.more && !(value.choice || []).length) drafts.delete(key);
    try { localStorage.setItem(DRAFTS_KEY, JSON.stringify([...drafts].slice(-50))); } catch { /* drafts stay in memory */ }
  }

  function clearDraft(key, field) {
    const draft = drafts.get(key);
    if (draft) { if (field) draft[field] = ""; else drafts.delete(key); }
    try { localStorage.setItem(DRAFTS_KEY, JSON.stringify([...drafts].slice(-50))); } catch { /* drafts stay in memory */ }
  }

  function followupThread(items) {
    if (!items.length) return "";
    return `<ol class="followup-thread" aria-label="שאלות המשך ותשובות המנהל">${items.map(item => `<li class="followup-item"><p class="followup-ask"><span class="followup-who">${item.channel === "dashboard" || item.channel === "terminal" ? `אתה · ${item.channel === "terminal" ? "טרמינל" : "דשבורד"}` : "ערוץ לא מאומת"}</span><span class="followup-text" dir="auto">${esc(item.text)}</span><time title="${esc(timestamp(item.created_at))}">${esc(age(item.created_at))}</time></p>${item.reply ? `<p class="followup-reply"><span class="followup-who">המנהל</span><span class="followup-text" dir="auto">${esc(item.reply)}</span><time title="${esc(timestamp(item.replied_at))}">${esc(age(item.replied_at))}</time></p>` : `<p class="followup-wait" role="status">${item.status === "delivered" ? "נמסר למנהל, ממתין לתשובה" : "נשמר, יימסר למנהל בסיום הצעד הנוכחי שלו"}</p>`}</li>`).join("")}</ol>`;
  }

  function askMoreForm(key, label, placeholder) {
    const draft = drafts.get(key) || {};
    const field = `more-${key}`;
    return `<form class="ask-more-form" data-followup="${esc(key)}"><label class="ask-more-label" for="${esc(field)}">${esc(label)}</label><textarea class="ask-more-text" id="${esc(field)}" rows="2" maxlength="2000" placeholder="${esc(placeholder)}">${esc(draft.more || "")}</textarea><div class="question-actions"><button class="button secondary" type="submit">שלח למנהל <span aria-hidden="true">←</span></button><span class="question-helper">Ctrl + Enter לשליחה · השאלה נשארת פתוחה עד שתענה</span></div><p class="form-error" role="alert" hidden></p></form>`;
  }

  function infoState(followups, id) {
    const asks = followups.filter(item => item.question_id === id && item.text === INFO_TEXT);
    const last = asks[asks.length - 1];
    if (!last) return null;
    if (last.reply) return {replied: true};
    const waited = Math.max(0, Date.now() / 1000 - seconds(last.created_at));
    return {waiting: true, minutes: Math.floor(waited / 60), idle: waited >= INFO_IDLE_S};
  }

  function infoStatusHTML(state) {
    if (!state) return "";
    if (state.replied) return `<p class="info-status replied" role="status">${esc(L.replied)}</p>`;
    return `<p class="info-status" role="status">${esc(L.waiting(state.minutes))}</p>${state.idle ? `<p class="info-idle">${esc(L.idle)}</p>` : ""}`;
  }

  function explainHTML(question, task, recommended) {
    const link = safeURL(question.link);
    const whyBits = [question.why ? `<span dir="auto">${esc(question.why)}</span>` : "", task?.version ? `<span class="pill">${esc(task.version)}</span>` : "", link ? `<a class="evidence-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(L.link)}</a>` : ""].filter(Boolean).join(" ");
    const steps = Array.isArray(question.steps) ? question.steps.filter(Boolean) : [];
    const rows = [];
    if (whyBits) rows.push([L.why, whyBits]);
    if (steps.length) rows.push([L.steps, `<ol class="question-steps" dir="auto">${steps.map(step => `<li dir="auto">${esc(step)}</li>`).join("")}</ol>`]);
    else if (question.kind === "owner_gate") rows.push([L.steps, `<span>${esc(L.noSteps)}</span>`]);
    if (question.minutes) rows.push([L.minutes, `<span class="pill amber">${esc(L.about(question.minutes))}</span>`]);
    if (question.skip_effect) rows.push([L.skip, `<span dir="auto">${esc(question.skip_effect)}</span>`]);
    if (recommended || question.recommendation) rows.push([L.recommended, `<span dir="auto">${recommended ? `<b>${esc(recommended.label)}</b>` : ""}${recommended && question.recommendation ? " · " : ""}${question.recommendation ? esc(question.recommendation) : ""}</span>`]);
    const list = rows.length ? `<dl class="question-explain">${rows.map(([label, value]) => `<div class="explain-row"><dt>${esc(label)}</dt><dd>${value}</dd></div>`).join("")}</dl>` : "";
    const details = question.details ? `<details class="question-details"><summary>${esc(L.details)}</summary><p dir="auto">${esc(question.details)}</p></details>` : "";
    return list + details;
  }

  function renderQuestions() {
    const followups = Array.isArray(data.followups) ? data.followups : [];
    const infoTick = followups.some(item => item.text === INFO_TEXT && !item.reply) ? Math.floor(Date.now() / 60000) : 0;
    const signature = JSON.stringify([data.questions.map(question => [question, data.tasks.find(task => task.id === question.task_id)?.title]), followups, infoTick]);
    if (signature === questionsSignature) return;
    questionsSignature = signature;
    const focused = document.activeElement;
    const activeDraft = focused?.matches(".question-answer, .ask-more-text") ? {id: focused.id, start: focused.selectionStart, end: focused.selectionEnd} : null;
    stashDrafts();
    const pending = data.questions.filter(question => !["answered", "resolved", "cancelled"].includes(question.status || "pending"));
    $("#question-progress").textContent = pending.length ? `${pending.length === 1 ? "שאלה אחת ממתינה" : `${pending.length} שאלות ממתינות`} · אפשר לענות ברצף. טיוטות נשמרות גם אחרי רענון.` : "כל ההחלטות שנענו נשמרות בהמשך.";
    const history = data.questions.filter(question => ["answered", "resolved"].includes(question.status)).reverse()
    const shownHistory = history.filter((question, index) => index < 5 || question.needs_owner_confirmation);
    const renderQuestion = question => {
      const task = data.tasks.find(item => item.id === question.task_id);
      const draft = drafts.get(question.id) || {};
      const picks = Array.isArray(draft.choice) ? draft.choice : draft.choice ? [draft.choice] : [];
      const choices = (question.choices || []).map(choice => typeof choice === "string" ? {id: choice, label: choice} : choice);
      const answered = ["answered", "resolved"].includes(question.status);
      const id = String(question.id);
      const kind = question.multi ? "checkbox" : "radio";
      const recommended = choices.find(choice => choice.recommended);
      const explain = explainHTML(question, task, answered ? null : recommended);
      const info = answered ? null : infoState(followups, id);
      const thread = followupThread(followups.filter(item => item.question_id === id));
      const options = choices.map((choice, index) => { const value = choice.label || choice.id; return `<label class="choice${choice.recommended ? " recommended" : ""}"><input type="${kind}" name="answer-${esc(id)}" value="${esc(value)}" ${picks.includes(value) ? "checked" : ""}><span class="choice-text"><span class="choice-label" dir="auto">${esc(value)}${choice.recommended ? ` <span class="pill recommended-badge">מומלץ</span>` : ""}</span>${choice.description ? `<span class="choice-description" dir="auto">${esc(choice.description)}</span>` : ""}</span><span class="choice-index" aria-hidden="true">${index + 1}</span></label>`; }).join("");
      return `<article class="question-card${answered ? " answered" : ""}" id="question-${esc(id)}"><div class="question-meta">${statusPill(question.status || "pending")}<span>${esc(age(question.created_at))}</span><span class="mono">${esc(id)}</span></div><h3 dir="auto">${esc(question.title)}</h3>${question.context ? `<p class="question-context" dir="auto">${esc(question.context)}</p>` : ""}${explain}${task ? `<button type="button" class="question-task text-link" data-task="${esc(task.id)}">למשימה: ${esc(task.title)} · <span class="mono">${esc(task.id)}</span> ←</button>` : ""}${answered ? `<p class="question-answer-history">${esc(question.answer || "התשובה נשמרה")}</p><p class="question-provenance">נענה על ידי: ${esc(question.answered_by || "unknown")} · ערוץ: ${esc(question.channel || "unknown")}${question.needs_owner_confirmation ? ` <span class="pill amber needs-confirmation">דרוש אישור שלך: התשובה לא ניתנה מהדשבורד או מהטרמינל שלך</span> <button class="button secondary" type="button" data-reopen="${esc(id)}">פתח מחדש</button>` : ""}</p>${thread}` : `<form class="question-form" data-question="${esc(id)}"><fieldset class="question-choices" ${choices.length ? "" : "hidden"}><legend class="sr-only">${question.multi ? "אפשר לבחור כמה תשובות" : "תשובות אפשריות"} ל־${esc(question.title)}</legend>${options}</fieldset><label class="sr-only" for="answer-${esc(id)}">תשובה אחרת או הערה ל־${esc(question.title)}</label><textarea class="question-answer" id="answer-${esc(id)}" rows="2" maxlength="4000" placeholder="${choices.length ? "אחר, או הערה לבחירה…" : "כתוב את התשובה שלך…"}">${esc(draft.text || "")}</textarea><p class="question-default">${question.safe_default ? `אם לא תענה: <span dir="auto">${esc(question.safe_default)}</span>. ` : ""}שום דבר לא מאושר מתוך שתיקה.</p><div class="question-actions"><button class="button primary" type="submit">שמור תשובה <span aria-hidden="true">←</span></button><button class="button secondary more-info" type="button" data-more-info="${esc(id)}" data-waiting="${info?.waiting ? "1" : "0"}" title="${esc(L.moreInfoTitle)}" ${info?.waiting ? "disabled" : ""}><span aria-hidden="true">?</span> ${esc(L.moreInfo)}</button><button class="button secondary" type="button" data-defer="${esc(id)}">לא עכשיו</button><span class="question-helper">Ctrl + Enter לשמירה</span></div>${infoStatusHTML(info)}<p class="form-error" role="alert" hidden></p></form>${thread}${askMoreForm(id, "שאל עוד את המנהל", "למשל: למה זה נחוץ? מה האפשרויות? תבדוק קודם את…")}`}</article>`;
    };
    const general = `<section class="question-card general-ask" aria-label="שאלה כללית למנהל"><h3>שאלה כללית למנהל</h3>${followupThread(followups.filter(item => !item.question_id))}${askMoreForm(GENERAL, "מה תרצה לשאול או לבקש?", "למשל: מה מצב הגרסה? תבדוק את…")}</section>`;
    $("#questions").innerHTML = (pending.length ? pending.map(renderQuestion).join("") : empty("אין כרגע שאלות שממתינות לך", "כשסוכן ייתקל בהחלטה שדורשת אותך, היא תישמר כאן עם המשימה וההקשר. שאר העבודה יכולה להמשיך.")) + general + (shownHistory.length ? `<h3 class="questions-history-title">תשובות אחרונות</h3>${shownHistory.map(renderQuestion).join("")}` : "") + `<p class="planning-note">כאן עונים על שאלות תכנון. אישורי הרשאות, תשלומים, שליחה או שחרור יציב נשארים במנגנוני האישור הקיימים.</p>`;
    if (activeDraft) {
      const restored = document.getElementById(activeDraft.id);
      if (restored) { restored.focus({preventScroll: true}); restored.setSelectionRange(activeDraft.start, activeDraft.end); }
    }
    syncControls();
  }

  function evidenceHTML(value) {
    const values = Array.isArray(value) ? value : value ? [value] : [];
    return values.map(item => {
      const raw = typeof item === "object" && item ? item.url || item.link || item.summary || JSON.stringify(item) : String(item);
      const label = typeof item === "object" && item ? item.label || item.summary || raw : raw;
      const url = safeURL(raw);
      return url ? `<a class="evidence-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>` : `<span class="detail-list">${esc(label)}</span>`;
    }).join("<br>");
  }

  function sourceRecords(task) {
    const issueRows = (task.source_issues || []).map(item => {
      const url = safeURL(item.url);
      const title = `#${item.number} · ${item.title || "Issue"}`;
      return `<div class="source-record"><div>${url ? `<a class="evidence-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)} ↗</a>` : esc(title)}</div><span class="pill ${item.state === "open" ? "amber" : ""}">${item.state === "closed" ? "Issue סגור" : item.state === "open" ? "Issue פתוח" : "מצב לא ידוע"}</span></div>`;
    });
    const prRows = (task.source_prs || []).map(item => {
      const url = safeURL(item.url);
      const title = `PR #${item.number} · ${item.title || ""}`;
      return `<div class="source-record"><div>${url ? `<a class="evidence-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)} ↗</a>` : esc(title)}</div><span class="pill ${item.state === "open" ? "amber" : ""}">${item.merged_at ? "PR מוזג" : item.state === "open" ? "PR פתוח" : item.state === "closed" ? "נסגר ללא מיזוג" : "מצב לא ידוע"}</span></div>`;
    });
    const sourceURL = safeURL(task.source_url);
    const paths = (task.source_paths || []).map(path => `<span class="source-path" dir="ltr">${esc(typeof path === "string" ? path : JSON.stringify(path))}</span>`).join("");
    return `<h3 class="detail-heading">מצב המקור — לא הוכחת בדיקה</h3>${issueRows.join("")}${prRows.join("")}${!issueRows.length && !prRows.length ? `<p class="detail-list">לא סונכרנו Issues או PRs לדרישה הזאת. אין בכך הוכחה שאין מימוש.</p>` : ""}${sourceURL ? `<p class="detail-list"><a class="evidence-link" href="${esc(sourceURL)}" target="_blank" rel="noopener noreferrer">למקור הדרישה ↗</a></p>` : ""}${paths ? `<div class="source-paths">${paths}</div>` : ""}${task.mapping_note ? `<p class="detail-list">${esc(mappingLabel(task.mapping_note))}</p>` : ""}`;
  }

  function openTask(id, reopen = true) {
    const task = data?.tasks.find(item => item.id === id);
    if (!task) return;
    const key = JSON.stringify([task, (a => a ? [a.id, a.label] : null)(data.agents.find(item => item.id === task.agent_id)), data.questions.filter(q => q.task_id === task.id)]);
    if (!reopen && dialogTaskId === task.id && key === dialogKey) return; // unchanged: keep open <details> and focus
    dialogTaskId = task.id; dialogKey = key;
    $("#task-dialog-title").textContent = task.title;
    const acceptance = task.acceptance || [];
    const fields = [["מימוש", implementationLabel(task)], ["אימות", validationNames[task.validation] || task.validation || "טרם נבדק"], ["הפצה", deliveryNames[task.delivery] || task.delivery || "טרם הופץ"], ["אימות נדרש", validationNames[task.required_validation] || task.required_validation || "לא הוגדר"]];
    const agent = data.agents.find(item => item.id === task.agent_id);
    $("#task-dialog-body").innerHTML = `<div class="detail-meta">${statusPill(task.state)}<span class="pill mono">${esc(task.id)}</span><span class="pill" dir="ltr">${esc(task.version || "ללא גרסה")}</span><span class="pill">${esc(laneNames[task.lane] || task.lane || "לא שויך")}</span><span class="pill mono">P${esc(task.priority ?? 2)}</span>${scopeLabel(task) ? `<span class="pill scope-pill">${esc(scopeLabel(task))}</span>` : ""}</div>${task.blocker ? `<div class="detail-blocker"><b>מה ממתין:</b> ${esc(task.blocker)}</div>` : ""}<div class="detail-stages">${fields.map(([label, value]) => `<div class="detail-stage">${label}<strong>${esc(value)}</strong></div>`).join("")}</div><h3 class="detail-heading">גרסת הקוד והתוצר שנבדק</h3><p class="detail-list">קוד: <span class="mono">${esc(task.code_revision || "טרם זוהה")}</span><br>תוצר: ${task.artifact_ref ? evidenceHTML(task.artifact_ref) : "טרם זוהה"}</p><details class="source-disclosure"><summary>קישורי GitHub ומקור הדרישה</summary>${sourceRecords(task)}</details><h3 class="detail-heading">תנאי קבלה והוכחות</h3>${acceptance.length ? `<ul class="acceptance-list">${acceptance.map(item => { const passed = criterionPassed(task, item); return `<li><span class="task-check ${passed ? "complete" : ""}" aria-hidden="true">${passed ? "✓" : ""}</span><div class="acceptance-copy">${esc(item.label || item.id)}${item.evidence ? `<br>${evidenceHTML(item.evidence)}${!passed && item.passed === true ? `<br><span class="detail-list">הראיה אינה משויכת לגרסת הקוד הנוכחית</span>` : ""}` : `<br><span class="detail-list">טרם צורפה ראיה</span>`}</div></li>`; }).join("")}</ul>` : `<p class="detail-list">עדיין לא הוגדרו תנאי קבלה. אין להסיק שהמשימה הושלמה.</p>`}<h3 class="detail-heading">בעלות ותלויות</h3><p class="detail-list">סוכן: ${agent ? `<button type="button" class="text-link" data-agent-history="${esc(agent.id)}">${esc(agent.label || agent.id)} ←</button>` : esc(task.agent_id || "טרם הוקצה")}<br>תלויות: ${esc((task.depends_on || []).join(", ") || "אין תלויות מדווחות")}<br>משאבים: ${esc((task.resources || []).join(", ") || "אין משאב בלעדי מדווח")}<br>ביצוע: ${task.execution_allowed === false ? "רשומת דרישה למיפוי; טרם הופעלה להקצאת עבודה" : "זמינה להקצאה בכפוף לתלויות ולמשאבים"}<br>עדכון אחרון: ${esc(timestamp(task.updated_at))}</p>`;
    const historyButton = document.createElement("button");
    historyButton.type = "button"; historyButton.className = "button secondary";
    historyButton.dataset.taskHistory = task.id; historyButton.textContent = "פעולות והיסטוריה של המשימה ←";
    $("#task-dialog-body").append(historyButton);
    if (reopen && !$("#task-dialog").open) $("#task-dialog").showModal();
  }

  function render() {
    const focused = document.activeElement;
    const focusKey = ["data-task", "data-agent-history", "data-task-history", "data-version", "data-version-toggle", "data-filter", "data-overview-metric"].find(k => focused?.hasAttribute(k));
    const focusValue = focusKey && focused.getAttribute(focusKey);
    renderMetrics(); renderAlerts(); renderAgents(); renderActive(); renderEvents(); renderRoadmap(); renderQuestions(); renderWork();
    // #732: today.js draws the "Today" rows (incl. the former Now strip) from the same state; read-only.
    document.dispatchEvent(new CustomEvent("workflow:render", {detail: {data, complete, age}}));
    const selected = $("#trace-agent").value;
    const options = '<option value="">כל הסוכנים</option>' + data.agents.map(a => `<option value="${esc(a.id)}">${a.parent_id ? "↳ " : ""}${esc(a.label || a.id)}${a.requested_model ? " · " + esc(a.requested_model) : ""}</option>`).join("");
    if ($("#trace-agent").innerHTML !== options) { $("#trace-agent").innerHTML = options; $("#trace-agent").value = selected; }
    if (view === "trace" && traceLive) loadTrace();
    if (dialogTaskId && $("#task-dialog").open) openTask(dialogTaskId, false);
    syncControls();
    if (focusKey && !focused.isConnected) $$(`[${focusKey}]`).find(el => el.getAttribute(focusKey) === focusValue)?.focus({preventScroll: true});
  }

  function traceSelectionKey() {
    return JSON.stringify([$("#trace-agent").value, $("#trace-task").value.trim(), $("#trace-range").value, $("#trace-from").value, $("#trace-to").value]);
  }

  function traceFilters() {
    const custom = $("#trace-range").value === "custom";
    const since = custom ? new Date($("#trace-from").value).getTime() / 1000 : traceUntil - Number($("#trace-range").value);
    const until = custom ? new Date($("#trace-to").value).getTime() / 1000 + 0.999 : traceUntil;
    if (!Number.isFinite(since) || !Number.isFinite(until) || since > until) throw new Error("בחרו טווח תקין: זמן ההתחלה צריך להיות לפני זמן הסיום.");
    return {agent_id: $("#trace-agent").value, task_id: $("#trace-task").value.trim(), since, until};
  }

  function eventName(row) {
    return {PreToolUse: "הכלי התחיל", PostToolUse: "הכלי הסתיים", PostToolUseFailure: "הכלי נכשל", SessionStart: "הסשן התחיל", SessionEnd: "הסשן הסתיים", SubagentStart: "סוכן משנה התחיל", SubagentStop: "סוכן משנה עצר", Stop: "הסוכן עצר", UserPromptSubmit: "התקבלה בקשה", Notification: "התראה", TelemetryGap: "פער בתיעוד"}[row.event] || stateNames[row.status] || "אירוע מתועד";
  }

  function setFeedback(text) {
    const node = $("#trace-feedback");
    if (node.textContent !== text) node.textContent = text;
  }

  function updateTraceControls() {
    $$("#trace-refresh, #trace-more, #trace-retro").forEach(button => { button.disabled = !connected || traceBusy; });
  }

  async function loadTrace(older = false) {
    if (!connected) return;
    if (traceBusy) { if (!older && traceLive) traceRefreshPending = true; return; }
    const selection = traceSelectionKey();
    traceBusy = true;
    updateTraceControls();
    try {
      if (!older && traceLive) traceUntil = Date.now() / 1000;
      const filters = traceFilters();
      if (selection !== traceRenderedKey) {
        traceRows = []; traceBefore = null;
        $("#trace-events").innerHTML = empty("טוען את הפעולות שבחרת", "ממתין לתיעוד מהשירות המקומי.", true);
        $("#trace-more").hidden = true;
      }
      const query = new URLSearchParams({...filters, ...(older && traceBefore ? {before: traceBefore} : {})});
      const result = await api("/api/trace?" + query);
      if (selection !== traceSelectionKey()) return;
      traceRows = older ? [...traceRows, ...result.events] : result.events;
      traceBefore = result.before; traceRenderedKey = selection;
      $("#trace-mode").value = result.settings.mode;
      const actor = data?.agents.find(a => a.id === $("#trace-agent").value);
      const parent = data?.agents.find(a => a.id === actor?.parent_id);
      const children = actor ? data.agents.filter(a => a.parent_id === actor.id) : [];
      const relations = actor ? `<p>${esc(actor.label || actor.id)}${actor.requested_model ? ` · מודל מבוקש: <span dir="ltr">${esc(actor.requested_model)}</span>` : ""}</p>${parent ? `<button type="button" class="text-link" data-agent-history="${esc(parent.id)}">בסשן של ${esc(parent.label || parent.id)} ←</button>` : ""}${children.map(a => `<button type="button" class="text-link" data-agent-history="${esc(a.id)}">↳ ${esc(a.label || a.id)}</button>`).join(" ")}` : '<p class="detail-list">בחרו סוכן כדי לראות את הקשרים המדווחים ואת הפעולות שלו.</p>';
      if ($("#trace-relations").innerHTML !== relations) $("#trace-relations").innerHTML = relations;
      $("#trace-coverage").textContent = `נשמרים עד ${result.settings.retention_days} ימים / ${result.settings.max_events.toLocaleString("he-IL")} אירועים. מוצג רק תיעוד שנקלט; כיסוי מתאמים חיצוניים עשוי להיות חלקי. בדקו מידע רגיש לפני שיתוף ייצוא.`;
      setFeedback(`${traceRows.length} ${traceRows.length === 1 ? "פעולה מוצגת" : "פעולות מוצגות"}${result.more ? " · אפשר לטעון פעולות קודמות" : ""} · ${traceLive ? "עדכון חי" : "תצוגה קפואה לניתוח"}`);
      $("#trace-more").hidden = !result.more;
      $("#trace-events").innerHTML = traceRows.length ? traceRows.map(row => {
        const actor = data?.agents.find(a => a.id === row.agent_id);
        const timing = row.duration_ms == null ? "" : ` · ${Math.round(row.duration_ms)} ms`;
        const failed = row.status === "failed" || row.event === "PostToolUseFailure";
        return `<button type="button" class="trace-row${failed ? " trace-failed" : ""}" data-trace-id="${row.id}"><span class="trace-index" dir="ltr">#${row.id}</span><span><b>${esc(row.tool || eventName(row))}</b><small>${esc(actor?.label || row.agent_id)} · ${esc(row.task_id || "ללא שיוך משימה")}</small></span><span class="trace-end">${esc(eventName(row))}${esc(timing)}<small>${esc(timestamp(row.ts))}</small></span><span class="trace-open" aria-hidden="true">←</span></button>`;
      }).join("") : empty("אין פעולות בטווח הנבחר", "נסו סוכן או תקופה אחרים. היסטוריה שלא נשמרה אינה משוחזרת בדיעבד.", true);
    } catch (error) {
      setFeedback(error.message);
      if (selection !== traceRenderedKey) { traceRows = []; $("#trace-events").innerHTML = empty("הפעולות לא נטענו", error.message, true); }
    } finally {
      traceBusy = false;
      updateTraceControls();
      // A paused view must also apply a selection made during an in-flight request.
      const followUp = selection !== traceSelectionKey() || (traceRefreshPending && traceLive);
      traceRefreshPending = false;
      if (followUp && view === "trace") loadTrace();
    }
  }

  function setTraceLive(value) {
    if (value && $("#trace-range").value === "custom") { $("#trace-range").value = "86400"; $("#trace-dates").hidden = true; }
    traceLive = value; $("#trace-live").setAttribute("aria-pressed", String(value));
    $("#trace-live").textContent = value ? "עדכון חי פעיל" : "היסטוריה קפואה";
  }

  function openTrace(row) {
    const actor = data?.agents.find(a => a.id === row.agent_id);
    const task = data?.tasks.find(t => t.id === row.task_id);
    const parent = data?.agents.find(a => a.id === row.parent_id);
    const metadataOnly = row.data?.captured === "metadata only";
    const fields = [["סוכן", actor?.label || row.agent_id], ["כלי", row.tool || "אירוע סשן"], ["זמן", new Date(row.ts * 1000).toLocaleString("he-IL")], ["משך שנמדד", row.duration_ms == null ? "לא זמין לאירוע הזה" : `${Math.round(row.duration_ms)} ms`]];
    $("#trace-dialog-title").textContent = `${row.tool || "פעולת סוכן"} · ${eventName(row)}`;
    $("#trace-dialog-body").innerHTML = `<p class="action-caption">פעולה #${row.id} · תוצאה של כלי אינה הוכחת השלמת משימה.</p><div class="action-facts">${fields.map(([label, value]) => `<div><span>${esc(label)}</span><strong dir="auto">${esc(value)}</strong></div>`).join("")}</div>${task ? `<button type="button" class="button secondary" data-task="${esc(task.id)}">למשימה: ${esc(task.title)} ←</button>` : `<p class="detail-list">משימה: ${esc(row.task_id || "לא דווח שיוך")}</p>`}${parent ? `<p class="detail-list">שיוך מדווח לסשן של ${esc(parent.label || parent.id)}</p>` : ""}${actor?.requested_model ? `<p class="detail-list">מודל מבוקש: <span dir="ltr">${esc(actor.requested_model)}</span> · אינו הוכחת מודל אפקטיבי</p>` : ""}<p class="action-capture">${metadataOnly ? "נשמר מידע תמציתי בלבד. קלט ופלט של כלים לא הוקלטו באירוע הזה." : "מוצגים הפרטים שנקלטו, לאחר סינון וקיצור אפשרי."}</p><div id="action-details"></div><details class="raw-event"><summary>פרטים טכניים מלאים של האירוע שנשמר</summary><pre class="trace-json" dir="ltr" id="trace-raw-json"></pre></details>`;
    for (const [key, label] of [["tool_input", "קלט לכלי"], ["tool_response", "תוצאה שהתקבלה"], ["error", "שגיאה"], ["summary", "תקציר שדווח"]]) {
      if (row.data?.[key] === undefined) continue;
      const section = document.createElement("section"); section.className = "action-detail";
      const heading = document.createElement("h3"); heading.textContent = label;
      const value = document.createElement("pre"); value.dir = "auto";
      value.textContent = typeof row.data[key] === "string" ? row.data[key] : JSON.stringify(row.data[key], null, 2);
      section.append(heading, value); $("#action-details").append(section);
    }
    $("#trace-raw-json").textContent = JSON.stringify(row, null, 2);
    $("#trace-dialog").showModal();
  }

  async function renewPresence(event) {
    if (!event.isTrusted || !connected || document.hidden || data?.owner?.effective_mode !== "present" || event.target.closest?.("#owner-present, #owner-away") || Date.now() - lastPresenceRefresh < 60000) return;
    lastPresenceRefresh = Date.now();
    try { await api("/api/owner", {mode: "present"}); }
    catch { /* Presence is never assumed from polling or a failed activity update. */ }
  }

  function switchView() {
    const requested = location.hash.replace(/^#/, "");
    view = views[requested] ? requested : "overview";
    $$(".view").forEach(section => { section.hidden = section.id !== `view-${view}`; });
    $$(".nav-link").forEach(link => {
      const active = link.dataset.view === view;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
    const [name, title, description, eyebrow] = views[view];
    $("#breadcrumb-current").textContent = name;
    $("#page-title").innerHTML = `${esc(title)}<span>.</span>`;
    $("#page-description").textContent = description;
    $("#page-eyebrow").textContent = eyebrow;
    document.title = `${name} — JARVES`;
    if (view === "trace") loadTrace();
  }

  async function mutate(button, path, body, message) {
    button.disabled = true; busyButtons.add(button);
    try {
      const result = await api(path, body);
      await refresh();
      if (message) toast(typeof message === "function" ? message(result) : message);
    } catch (error) { toast(error.message, true); }
    finally { busyButtons.delete(button); button.disabled = !connected; }
  }

  $("#owner-present").addEventListener("click", event => mutate(event.currentTarget, "/api/owner", {mode: "present"}, "סימנת שאתה כאן. השאלות שממתינות מרוכזות בתור ההחלטות."));
  $("#owner-away").addEventListener("click", event => mutate(event.currentTarget, "/api/owner", {mode: "away"}, "השאלות נשמרות. עבודה שאינה תלויה בתשובה שלך יכולה להמשיך."));
  $("#dispatch").addEventListener("click", event => mutate(event.currentTarget, "/api/dispatch", {}, result => {
    const list = result?.result?.assignments || [];
    const unconfirmed = list.filter(item => item.status === "delivery_unconfirmed").length;
    const sent = list.length - unconfirmed;
    if (!list.length) return "לא הוקצתה עבודה: אין סשן מחובר עם הקצאה אוטומטית שפנוי ויש לו משימה זמינה. סוכני Claude רגילים לוקחים משימות דרך המנהל.";
    return `${sent} הקצאות נשלחו${unconfirmed ? `; ${unconfirmed} ללא אישור מסירה — בדוק את הסשן המקורי לפני ניסיון חוזר` : ""}.`;
  }));
  $("#add-task").addEventListener("click", () => { $("#create-error").hidden = true; $("#create-dialog").showModal(); });
  $$(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  $("#task-dialog").addEventListener("close", () => { dialogTaskId = null; });
  document.addEventListener("click", event => {
    if (event.target.closest("#agents-offline-toggle")) {
      showOffline = !showOffline;
      try { localStorage.setItem(OFFLINE_KEY, showOffline ? "1" : "0"); } catch (_) { /* remembered for this page only */ }
      renderAgents();
      $("#agents-offline-toggle")?.focus({preventScroll: true});
      return;
    }
    const taskHistory = event.target.closest("[data-task-history]");
    if (taskHistory) {
      $("#task-dialog").close(); $("#trace-task").value = taskHistory.dataset.taskHistory;
      $("#trace-agent").value = ""; location.hash = "trace"; loadTrace();
    }
    const version = event.target.closest("[data-version]");
    if (version) { roadmapVersion = version.dataset.version; renderRoadmap(); }
    const versionToggle = event.target.closest("[data-version-toggle]");
    if (versionToggle) expandedVersions.set(versionToggle.dataset.versionToggle, !versionToggle.parentElement.open);
    const metric = event.target.closest("[data-overview-metric]");
    if (metric) {
      const index = Number(metric.dataset.overviewMetric);
      if (index === 1 || index === 3) { roadmapVersion = "all"; $("#task-search").value = ""; filter = index === 1 ? "active" : "done"; $$("[data-filter]").forEach(b => { b.classList.toggle("active", b.dataset.filter === filter); b.setAttribute("aria-pressed", String(b.dataset.filter === filter)); }); renderRoadmap(); }
      if (index === 0) $("#agents").scrollIntoView({block: "start"});
    }
    const actorHistory = event.target.closest("[data-agent-history]");
    if (actorHistory) { $$("dialog[open]").forEach(d => d.close()); $("#trace-agent").value = actorHistory.dataset.agentHistory; $("#trace-task").value = ""; location.hash = "trace"; loadTrace(); }
    const traceButton = event.target.closest("[data-trace-id]");
    if (traceButton) {
      const row = traceRows.find(r => String(r.id) === traceButton.dataset.traceId);
      if (row) openTrace(row);
    }
    const taskButton = event.target.closest("[data-task]");
    if (taskButton) { if ($("#trace-dialog").open) $("#trace-dialog").close(); openTask(taskButton.dataset.task); }
    const filterButton = event.target.closest("[data-filter]");
    if (filterButton) {
      filter = filterButton.dataset.filter;
      $$("[data-filter]").forEach(button => { button.classList.toggle("active", button === filterButton); button.setAttribute("aria-pressed", String(button === filterButton)); });
      renderRoadmap();
    }
    const infoButton = event.target.closest("[data-more-info]");
    if (infoButton) {
      if (infoButton.dataset.waiting !== "1") mutate(infoButton, `/api/questions/${encodeURIComponent(infoButton.dataset.moreInfo)}/ask-more`, {text: INFO_TEXT}, L.sent);
      return;
    }
    const reopenButton = event.target.closest("[data-reopen]");
    if (reopenButton) { mutate(reopenButton, `/api/questions/${encodeURIComponent(reopenButton.dataset.reopen)}/reopen`, {}, "השאלה נפתחה מחדש וממתינה לתשובה שלך."); return; }
    const deferButton = event.target.closest("[data-defer]");
    if (deferButton) mutate(deferButton, `/api/questions/${encodeURIComponent(deferButton.dataset.defer)}/defer`, {}, "השאלה נשמרה להמשך. המשימה לא נעלמה מהתוכנית.");
  });
  $("#task-search").addEventListener("input", renderRoadmap);
  $("#trace-refresh").addEventListener("click", () => { if ($("#trace-range").value !== "custom") traceUntil = Date.now() / 1000; loadTrace(); });
  $("#trace-more").addEventListener("click", () => { setTraceLive(false); loadTrace(true); });
  $("#trace-live").addEventListener("click", () => { setTraceLive(!traceLive); if (traceLive) loadTrace(); });
  ["#trace-agent", "#trace-task", "#trace-from", "#trace-to"].forEach(id => $(id).addEventListener("change", () => { traceUntil = Date.now() / 1000; loadTrace(); }));
  $("#trace-range").addEventListener("change", () => {
    const custom = $("#trace-range").value === "custom";
    $("#trace-dates").hidden = !custom;
    if (custom) {
      const localInput = ts => new Date(ts - new Date(ts).getTimezoneOffset() * 60000).toISOString().slice(0, 19);
      if (!$("#trace-from").value) $("#trace-from").value = localInput(Date.now() - 3600000);
      if (!$("#trace-to").value) $("#trace-to").value = localInput(Date.now());
      setTraceLive(false);
    }
    traceUntil = Date.now() / 1000; loadTrace();
  });
  $("#trace-copy").addEventListener("click", async () => {
    const field = $("#trace-retro-prompt");
    try { await navigator.clipboard.writeText(field.value); toast("הבקשה הועתקה. הדביקו אותה בסוכן שבו /retro מותקן."); }
    catch { field.focus(); field.select(); toast("הבקשה מסומנת. אפשר להעתיק אותה עם Ctrl+C או דרך תפריט ההעתקה."); }
  });
  $("#trace-mode").addEventListener("change", async event => {
    try { await api("/api/trace/settings", {mode: event.target.value}); await loadTrace(); }
    catch (error) { toast(error.message, true); }
  });
  $("#trace-retro").addEventListener("click", async event => {
    const button = event.currentTarget; button.disabled = true;
    try {
      const result = await api("/api/trace/retro", traceFilters());
      $("#trace-retro-prompt").value = result.result.prompt;
      $("#trace-handoff").hidden = false;
      toast(result.result.truncated ? "הוכן ייצוא חלקי; כדאי לצמצם את הטווח." : "הוכנה בקשה לרטרו. לא הופעל סוכן ולא שונו כללים.");
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });
  $("#questions").addEventListener("submit", async event => {
    const form = event.target.closest(".question-form");
    if (!form) return;
    event.preventDefault();
    const typed = $("textarea", form).value.trim(), picked = $$("input:checked", form).map(input => input.value).join(", ");
    const answer = typed && picked ? `${picked} — ${typed}` : typed || picked;
    const error = $(".form-error", form);
    if (!answer) { error.textContent = "בחר תשובה או כתוב תשובה משלך."; error.hidden = false; return; }
    if (!connected) { error.textContent = "אין חיבור לשירות; התשובה לא נשלחה."; error.hidden = false; return; }
    const button = $("button[type=submit]", form);
    button.disabled = true; busyButtons.add(button);
    error.hidden = true;
    try {
      await api(`/api/questions/${encodeURIComponent(form.dataset.question)}/answer`, {answer});
      $("textarea", form).value = "";
      $$("input:checked", form).forEach(input => { input.checked = false; });
      clearDraft(form.dataset.question);
      await refresh();
      toast("התשובה נשמרה. המנהל יוכל להמשיך מההחלטה הזאת.");
      $(".question-form")?.querySelector("input[type=radio], textarea")?.focus();
    } catch (err) { error.textContent = err.message; error.hidden = false; }
    finally { busyButtons.delete(button); button.disabled = !connected; }
  });
  $("#questions").addEventListener("input", stashDrafts);
  $("#questions").addEventListener("change", stashDrafts);
  $("#questions").addEventListener("submit", async event => {
    const form = event.target.closest(".ask-more-form");
    if (!form) return;
    event.preventDefault();
    const text = $("textarea", form).value.trim(), key = form.dataset.followup;
    const error = $(".form-error", form);
    if (!text) { error.textContent = "כתוב מה תרצה לשאול."; error.hidden = false; return; }
    if (!connected) { error.textContent = "אין חיבור לשירות; השאלה לא נשלחה."; error.hidden = false; return; }
    const button = $("button[type=submit]", form);
    button.disabled = true; busyButtons.add(button);
    error.hidden = true;
    try {
      await api(key === GENERAL ? "/api/followups" : `/api/questions/${encodeURIComponent(key)}/ask-more`, {text});
      $("textarea", form).value = "";  // reset() would restore the rendered draft text
      clearDraft(key, "more");
      await refresh();
      toast("השאלה נשמרה ותימסר למנהל בסיום הצעד הנוכחי שלו. שום דבר לא אושר.");
    } catch (err) { error.textContent = err.message; error.hidden = false; }
    finally { busyButtons.delete(button); button.disabled = !connected; }
  });
  $("#questions").addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      const form = event.target.closest(".question-form, .ask-more-form");
      if (form) { event.preventDefault(); form.requestSubmit(); }
    }
  });
  $("#create-task-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const acceptance = String(values.get("acceptance") || "").split("\n").map(value => value.trim()).filter(Boolean).map((label, index) => ({id: `A${index + 1}`, label}));
    const error = $("#create-error");
    if (!acceptance.length) { error.textContent = "הוסף לפחות תנאי קבלה אחד."; error.hidden = false; return; }
    if (!connected) { error.textContent = "אין חיבור לשירות; המשימה לא נשמרה."; error.hidden = false; return; }
    const button = $("button[type=submit]", form);
    button.disabled = true; busyButtons.add(button);
    error.hidden = true;
    try {
      await api("/api/tasks", {title: String(values.get("title")).trim(), version: values.get("version"), lane: values.get("lane"), priority: Number(values.get("priority")), required_validation: values.get("required_validation"), acceptance});
      form.reset();
      $("#create-dialog").close();
      roadmapVersion = String(values.get("version")); filter = "all"; $("#task-search").value = "";
      $$("[data-filter]").forEach(b => { b.classList.toggle("active", b.dataset.filter === "all"); b.setAttribute("aria-pressed", String(b.dataset.filter === "all")); });
      await refresh();
      renderRoadmap(); // The state stream may have rendered before these local filters changed.
      location.hash = "roadmap";
      toast("המשימה נוספה לתוכנית עם תנאי הקבלה שלה.");
    } catch (err) { error.textContent = err.message; error.hidden = false; }
    finally { busyButtons.delete(button); button.disabled = !connected; }
  });
  window.addEventListener("hashchange", switchView);
  window.addEventListener("online", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  document.addEventListener("pointerdown", renewPresence, {passive: true});
  document.addEventListener("keydown", renewPresence, {passive: true});

  function connectStream() {
    if (ended) return;
    if (!("EventSource" in window)) return;
    stream = new EventSource("/api/events", {withCredentials: true});
    stream.addEventListener("change", () => refresh());
    stream.addEventListener("open", () => refresh());
    stream.addEventListener("error", () => {
      if (Date.now() - lastGoodAt > 15000) connectionState(false, "זרם העדכונים נותק; מנסה להתחבר מחדש.");
    });
    window.addEventListener("beforeunload", () => stream?.close(), {once: true});
  }

  async function start() {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const token = fragment.get("token");
    if (token) {
      history.replaceState(null, "", `${location.pathname}${location.search}#overview`);
      try { await api("/api/session", {token}); }
      catch (error) { if (error.sessionEnded) return; connectionState(false, error.message); switchView(); return; }
    }
    switchView();
    await refresh();
    if (ended) return;
    connectStream();
    setInterval(() => { if (!document.hidden && !ended) refresh(); }, 12000);
    setInterval(() => { if (data && !document.hidden && !ended) renderQuestions(); }, 20000);
    setInterval(() => { if (ended) return; renderLiveStatus(); if (lastGoodAt && Date.now() - lastGoodAt > 45000) connectionState(false, "הנתונים לא עודכנו בזמן האחרון."); }, 10000);
  }
  start();
})();
