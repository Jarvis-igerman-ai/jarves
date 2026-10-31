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
  let connected = false;
  let lastGoodAt = 0;
  let requestInFlight = null;
  let refreshAgain = false;
  let stream = null;
  let toastTimer;
  let dialogTaskId = null;
  let questionsSignature = "";
  let lastPresenceRefresh = 0;
  const drafts = new Map();
  views.trace = ["פעולות והיסטוריה", "מהתוכנית עד לפעולה", "סוכן, סשן וקריאת כלי — עכשיו ובהיסטוריה שנשמרה.", "DEVELOPMENT / ACTION HISTORY"];
  let traceRows = [], traceBefore = null, traceLive = true, traceBusy = false, traceUntil = Date.now() / 1000;

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
        if (response.status === 401 || response.status === 403) throw new Error(detail || "נדרש לפתוח את הקישור המאומת שהשירות המקומי הציג.");
        throw new Error(detail || result.message || `הבקשה לא הושלמה (${response.status}).`);
      }
      return result;
    } finally { clearTimeout(timeout); }
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
    $$("#dispatch, #add-task, #owner-present, #owner-away").forEach(button => { button.disabled = !ok; });
    if (!ok && !data) {
      $("#metrics").innerHTML = "";
      $("#agents").innerHTML = empty("סביבת העבודה עדיין לא מחוברת", "המסך לא ממציא סוכנים או התקדמות. אחרי חיבור לשירות המקומי יופיע כאן המצב המדווח.");
      $("#active-tasks").innerHTML = empty("אין מידע זמין", "ממתינים לחיבור מאומת לשירות.", true);
      $("#events").innerHTML = "";
      $("#roadmap").innerHTML = empty("התוכנית תופיע אחרי החיבור", "המשימות נשמרות בשירות המקומי. אין כאן תוכנית לדוגמה שמוצגת כמצב חי.");
      $("#questions").innerHTML = empty("ממתינים לחיבור", "שאלות ותשובות נשמרות בשירות המקומי.");
    }
  }

  async function refresh() {
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
      } catch (error) { connectionState(false, error.name === "AbortError" ? "השירות לא ענה בזמן." : error.message); }
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
    const good = fresh && !["offline", "stale", "failed", "stopped"].includes(status);
    $("#watchdog-indicator").innerHTML = `<span class="status-dot ${good ? "good" : "warn"}" aria-hidden="true"></span><span>${data.source?.mode === "demo" ? "משגיח — הדגמה בלבד" : good ? "המשגיח החיצוני פעיל" : heartbeat ? "אין עדכון טרי מהמשגיח" : "המשגיח טרם התחבר"}</span>`;
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

  function renderMetrics() {
    const tasks = data.tasks;
    const required = tasks.filter(isRequired);
    const done = required.filter(complete).length;
    const running = data.agents.filter(agent => agent.status === "running").length;
    const waiting = tasks.filter(task => task.state === "waiting_owner").length;
    const active = tasks.filter(task => ["running", "review"].includes(task.state)).length;
    const ready = tasks.filter(task => task.ready === true).length;
    const pending = data.questions.filter(question => ["pending", "deferred", "open"].includes(question.status || "pending")).length;
    $("#questions-count").textContent = pending;
    const cards = [
      {label: "סוכנים בעבודה", symbol: "◈", value: running, detail: `${data.agents.length} נרשמו`, foot: data.agents.length ? "לפי דיווחי הסוכנים" : "טרם נרשמו סוכנים", cls: ""},
      {label: "משימות פעילות", symbol: "↗", value: active, detail: `${ready} זמינות להקצאה`, foot: "בפיתוח ובבדיקה", cls: ""},
      {label: "ממתין להחלטה שלך", symbol: "◇", value: pending, detail: `${waiting} משימות`, foot: "נשאר בתמונה, לא מדולג", cls: "amber"},
      {label: "דרישות חובה שהושלמו", symbol: "✓", value: done, detail: `מתוך ${required.length}`, foot: tasks.length ? `${tasks.length} פריטים בהיקף הכולל; אופציונליים מחוץ למכנה` : "ממתין למיפוי התוכנית", cls: "done", progress: required.length ? done / required.length * 100 : 0}
    ];
    $("#metrics").innerHTML = cards.map(card => `<div class="metric ${card.cls}"><div class="metric-label"><span>${esc(card.label)}</span><span class="metric-symbol" aria-hidden="true">${card.symbol}</span></div><div class="metric-value">${card.value}<small>${esc(card.detail)}</small></div><p class="metric-foot">${esc(card.foot)}</p>${card.progress !== undefined ? `<div class="metric-bar" aria-hidden="true"><progress max="100" value="${card.progress}"></progress></div>` : ""}</div>`).join("");
  }

  function renderAgents() {
    $("#agent-summary").textContent = `${data.agents.length} סוכנים רשומים`;
    $("#agents").innerHTML = data.agents.length ? data.agents.map((agent, i) => {
      const task = data.tasks.find(item => item.id === agent.task_id);
      const symbols = {bugs: "⌘", features: "◇", release: "✓", ops: "◫", manager: "◎", security: "⌁"};
      return `<article class="agent-card ${esc(agent.status)}"><div class="agent-head"><span class="agent-avatar" aria-hidden="true">${symbols[agent.lane] || "◈"}</span><div><h3 class="agent-name">${esc(agent.label || agent.id || `סוכן ${i + 1}`)}</h3><p class="agent-lane">${esc(laneNames[agent.lane] || agent.lane || "טרם שויך תחום")}</p></div>${statusPill(agent.status)}</div>${task ? `<button type="button" class="agent-link" data-task="${esc(task.id)}"><p class="agent-work">${esc(task.title)}</p><p class="agent-task-id">${esc(task.id)}</p></button>` : `<p class="agent-work no-task">${agent.status === "idle" ? "פנוי למשימה הבאה" : "לא דווחה משימה פעילה"}</p>`}<p class="agent-note">${esc(agent.note || "")}</p><div class="agent-footer"><span>אות חיים ${esc(age(agent.heartbeat_at))}</span><span>התקדמות ${esc(age(agent.progress_at))}</span></div></article>`;
    }).join("") : empty("הצוות עוד לא התחבר", "המנהל ותתי־הסוכנים של Claude Code יופיעו כאן אחרי דיווח מה־hooks המקומיים. לא מוצגים סוכנים משוערים או מחוברים למראית עין.");
  }

  function renderAlerts() {
    const list = data.alerts || [];
    const section = $("#alerts-section");
    section.hidden = list.length === 0;
    section.innerHTML = list.map(alert => `<div class="alert"><span class="alert-symbol" aria-hidden="true">△</span><p>${esc(alert.summary)}</p>${alert.task_id ? `<button type="button" class="text-link" data-task="${esc(alert.task_id)}">למשימה ←</button>` : ""}</div>`).join("");
  }

  function renderActive() {
    const work = data.tasks.filter(task => ["running", "review", "waiting_owner", "blocked"].includes(task.state)).sort((a, b) => (a.priority ?? 2) - (b.priority ?? 2)).slice(0, 7);
    $("#active-tasks").innerHTML = work.length ? work.map(task => `<button type="button" class="work-row" data-task="${esc(task.id)}"><span class="work-state-icon ${["waiting_owner", "blocked"].includes(task.state) ? "amber" : ""}" aria-hidden="true">${task.state === "waiting_owner" ? "◇" : task.state === "blocked" ? "!" : task.state === "review" ? "◌" : "↗"}</span><span class="work-row-main"><span class="work-row-title">${esc(task.title)}</span><span class="work-row-meta"><span class="mono">${esc(task.id)}</span><span dir="ltr">${esc(task.version || "ללא גרסה")}</span><span>${esc(stateNames[task.state] || task.state)}</span></span></span><span class="priority ${(task.priority ?? 2) < 2 ? "high" : ""}">P${esc(task.priority ?? 2)}</span></button>`).join("") : empty("אין משימות פעילות כרגע", data.tasks.length ? "משימות התור והחסימות זמינות בתוכנית המלאה. הקצאת עבודה תחבר בין סוכן פנוי למשימה מתאימה." : "הוסף משימה או ייבא את תוכנית העבודה כדי להתחיל.", true);
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
    $("#roadmap-context").innerHTML = data.source?.scope_note ? `<details class="scope-note"><summary>היקף התוכנית והמקור</summary><p dir="auto">${esc(data.source.scope_note)}</p><p>סגירת Issue או מיזוג PR מוצגים בנפרד מהוכחת תרחיש. פריט שטרם מופה נשאר לא ידוע.</p></details>` : "";
    const search = $("#task-search").value.trim().toLocaleLowerCase("he-IL");
    let tasks = data.tasks.filter(task => !search || `${task.title} ${task.id} ${task.version}`.toLocaleLowerCase("he-IL").includes(search));
    if (filter === "active") tasks = tasks.filter(task => ["running", "review"].includes(task.state));
    if (filter === "waiting") tasks = tasks.filter(task => ["waiting_owner", "blocked", "queued"].includes(task.state));
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
      return `<section class="version-section"><div class="version-heading"><div class="version-title"><h2>${esc(version)}</h2><p>${esc(versionDescriptions[version] || "משימות משויכות")}<span class="version-scope-count">${fullGroup.length} פריטים בסך הכול${extraCount ? ` · ${extraCount} אופציונליים או נדחו` : ""}</span></p></div><div class="version-completion"><span>${done} / ${requiredGroup.length} חובה הושלמו</span><progress class="version-progress" aria-label="השלמת דרישות חובה של ${esc(version)}" value="${done}" max="${Math.max(1, requiredGroup.length)}"></progress></div></div>${group.sort((a, b) => (a.priority ?? 2) - (b.priority ?? 2)).map(task => {
        const doneTask = complete(task);
        const icon = doneTask ? "✓" : task.state === "running" ? "↗" : ["waiting_owner", "blocked"].includes(task.state) ? "◇" : "";
        const iconClass = doneTask ? "complete" : task.state === "running" ? "running" : ["waiting_owner", "blocked"].includes(task.state) ? "waiting" : "";
        const agent = data.agents.find(item => item.id === task.agent_id);
        const source = sourceSummary(task);
        return `<button type="button" class="roadmap-row" data-task="${esc(task.id)}"><span class="task-check ${iconClass}" aria-hidden="true">${icon}</span><span><span class="roadmap-task-title">${esc(task.title)}</span><span class="task-progress-fields">${taskFields(task)}</span>${source ? `<span class="source-summary">${esc(source)}</span>` : ""}</span><span class="task-row-end">${scopeLabel(task) ? `<span class="pill scope-pill">${esc(scopeLabel(task))}</span>` : ""}${task.execution_allowed === false && task.state === "queued" ? `<span class="state-pill gray">למיפוי ואימות</span>` : statusPill(task.state)}<span class="task-row-agent">${esc(agent?.label || task.agent_id || task.id)}</span></span></button>`;
      }).join("")}</section>`;
    }).join("");
  }

  function stashDrafts() {
    $$(".question-form").forEach(form => {
      const selected = $("input[type=radio]:checked", form);
      drafts.set(form.dataset.question, {text: $("textarea", form).value, choice: selected?.value || ""});
    });
  }

  function renderQuestions() {
    const signature = JSON.stringify(data.questions.map(question => [question, data.tasks.find(task => task.id === question.task_id)?.title]));
    if (signature === questionsSignature) return;
    questionsSignature = signature;
    const focused = document.activeElement;
    const activeDraft = focused?.matches(".question-answer") ? {id: focused.id, start: focused.selectionStart, end: focused.selectionEnd} : null;
    stashDrafts();
    const pending = data.questions.filter(question => !["answered", "resolved", "cancelled"].includes(question.status || "pending"));
    const history = data.questions.filter(question => ["answered", "resolved"].includes(question.status)).slice(-5).reverse();
    const renderQuestion = question => {
      const task = data.tasks.find(item => item.id === question.task_id);
      const draft = drafts.get(question.id) || {};
      const choices = (question.choices || []).map(choice => typeof choice === "string" ? {id: choice, label: choice} : choice);
      const answered = ["answered", "resolved"].includes(question.status);
      const id = String(question.id);
      return `<article class="question-card${answered ? " answered" : ""}" id="question-${esc(id)}"><div class="question-meta">${statusPill(question.status || "pending")}<span>${esc(age(question.created_at))}</span><span class="mono">${esc(id)}</span></div><h3>${esc(question.title)}</h3>${question.context ? `<p class="question-context">${esc(question.context)}</p>` : ""}${task ? `<p class="question-task">משויך ל־${esc(task.title)} · <span class="mono">${esc(task.id)}</span></p>` : ""}${answered ? `<p class="question-answer-history">${esc(question.answer || "התשובה נשמרה")}</p>` : `<form class="question-form" data-question="${esc(id)}"><fieldset class="question-choices" ${choices.length ? "" : "hidden"}><legend class="sr-only">תשובות אפשריות ל־${esc(question.title)}</legend>${choices.map((choice, index) => `<label class="choice"><input type="radio" name="answer-${esc(id)}" value="${esc(choice.label || choice.id)}" ${draft.choice === (choice.label || choice.id) ? "checked" : ""}><span>${esc(choice.label || choice.id)}</span><span class="choice-index" aria-hidden="true">${index + 1}</span></label>`).join("")}</fieldset><label class="sr-only" for="answer-${esc(id)}">תשובה חופשית ל־${esc(question.title)}</label><textarea class="question-answer" id="answer-${esc(id)}" rows="2" maxlength="4000" placeholder="או כתוב תשובה משלך…">${esc(draft.text || "")}</textarea><div class="question-actions"><button class="button primary" type="submit">שמור תשובה <span aria-hidden="true">←</span></button><button class="button secondary" type="button" data-defer="${esc(id)}">לא עכשיו</button><span class="question-helper">Ctrl + Enter לשמירה</span></div><p class="form-error" role="alert" hidden></p></form>`}</article>`;
    };
    $("#questions").innerHTML = (pending.length ? pending.map(renderQuestion).join("") : empty("אין כרגע שאלות שממתינות לך", "כשסוכן ייתקל בהחלטה שדורשת אותך, היא תישמר כאן עם המשימה וההקשר. שאר העבודה יכולה להמשיך.")) + (history.length ? `<h3 class="questions-history-title">תשובות אחרונות</h3>${history.map(renderQuestion).join("")}` : "") + `<p class="planning-note">כאן עונים על שאלות תכנון. אישורי הרשאות, תשלומים, שליחה או שחרור יציב נשארים במנגנוני האישור הקיימים.</p>`;
    if (activeDraft) {
      const restored = document.getElementById(activeDraft.id);
      if (restored) { restored.focus({preventScroll: true}); restored.setSelectionRange(activeDraft.start, activeDraft.end); }
    }
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
    dialogTaskId = task.id;
    $("#task-dialog-title").textContent = task.title;
    const acceptance = task.acceptance || [];
    const fields = [["מימוש", implementationLabel(task)], ["אימות", validationNames[task.validation] || task.validation || "טרם נבדק"], ["הפצה", deliveryNames[task.delivery] || task.delivery || "טרם הופץ"], ["אימות נדרש", validationNames[task.required_validation] || task.required_validation || "לא הוגדר"]];
    const agent = data.agents.find(item => item.id === task.agent_id);
    $("#task-dialog-body").innerHTML = `<div class="detail-meta">${statusPill(task.state)}<span class="pill mono">${esc(task.id)}</span><span class="pill" dir="ltr">${esc(task.version || "ללא גרסה")}</span><span class="pill">${esc(laneNames[task.lane] || task.lane || "לא שויך")}</span><span class="pill mono">P${esc(task.priority ?? 2)}</span>${scopeLabel(task) ? `<span class="pill scope-pill">${esc(scopeLabel(task))}</span>` : ""}</div>${task.blocker ? `<div class="detail-blocker"><b>מה ממתין:</b> ${esc(task.blocker)}</div>` : ""}<div class="detail-stages">${fields.map(([label, value]) => `<div class="detail-stage">${label}<strong>${esc(value)}</strong></div>`).join("")}</div><h3 class="detail-heading">גרסת הקוד והתוצר שנבדק</h3><p class="detail-list">קוד: <span class="mono">${esc(task.code_revision || "טרם זוהה")}</span><br>תוצר: ${task.artifact_ref ? evidenceHTML(task.artifact_ref) : "טרם זוהה"}</p>${sourceRecords(task)}<h3 class="detail-heading">תנאי קבלה והוכחות</h3>${acceptance.length ? `<ul class="acceptance-list">${acceptance.map(item => { const passed = criterionPassed(task, item); return `<li><span class="task-check ${passed ? "complete" : ""}" aria-hidden="true">${passed ? "✓" : ""}</span><div class="acceptance-copy">${esc(item.label || item.id)}${item.evidence ? `<br>${evidenceHTML(item.evidence)}${!passed && item.passed === true ? `<br><span class="detail-list">הראיה אינה משויכת לגרסת הקוד הנוכחית</span>` : ""}` : `<br><span class="detail-list">טרם צורפה ראיה</span>`}</div></li>`; }).join("")}</ul>` : `<p class="detail-list">עדיין לא הוגדרו תנאי קבלה. אין להסיק שהמשימה הושלמה.</p>`}<h3 class="detail-heading">בעלות ותלויות</h3><p class="detail-list">סוכן: ${esc(agent?.label || task.agent_id || "טרם הוקצה")}<br>תלויות: ${esc((task.depends_on || []).join(", ") || "אין תלויות מדווחות")}<br>משאבים: ${esc((task.resources || []).join(", ") || "אין משאב בלעדי מדווח")}<br>ביצוע: ${task.execution_allowed === false ? "רשומת דרישה למיפוי; טרם הופעלה להקצאת עבודה" : "זמינה להקצאה בכפוף לתלויות ולמשאבים"}<br>עדכון אחרון: ${esc(timestamp(task.updated_at))}</p>`;
    const historyButton = document.createElement("button");
    historyButton.type = "button"; historyButton.className = "button secondary";
    historyButton.dataset.taskHistory = task.id; historyButton.textContent = "פעולות והיסטוריה של המשימה ←";
    $("#task-dialog-body").append(historyButton);
    if (reopen && !$("#task-dialog").open) $("#task-dialog").showModal();
  }

  function render() {
    renderMetrics(); renderAlerts(); renderAgents(); renderActive(); renderEvents(); renderRoadmap(); renderQuestions();
    const selected = $("#trace-agent").value;
    const options = '<option value="">כל הסוכנים</option>' + data.agents.map(a => `<option value="${esc(a.id)}">${a.parent_id ? "↳ " : ""}${esc(a.label || a.id)}${a.requested_model ? " · " + esc(a.requested_model) : ""}</option>`).join("");
    if ($("#trace-agent").innerHTML !== options) { $("#trace-agent").innerHTML = options; $("#trace-agent").value = selected; }
    $$(".agent-card", $("#agents")).forEach((card, i) => {
      const button = document.createElement("button"); button.type = "button"; button.className = "text-link agent-history";
      button.textContent = "ציר פעולות והיסטוריה ←"; button.dataset.agentHistory = data.agents[i].id; card.append(button);
    });
    if (view === "trace" && traceLive) loadTrace();
    if (dialogTaskId && $("#task-dialog").open) openTask(dialogTaskId, false);
  }

  function traceFilters() {
    return {agent_id: $("#trace-agent").value, task_id: $("#trace-task").value.trim(),
      since: traceUntil - Number($("#trace-range").value), until: traceUntil};
  }

  async function loadTrace(older = false) {
    if (traceBusy || !connected) return;
    traceBusy = true;
    try {
      if (!older && traceLive) traceUntil = Date.now() / 1000;
      const query = new URLSearchParams({...traceFilters(), ...(older && traceBefore ? {before: traceBefore} : {})});
      const result = await api("/api/trace?" + query);
      traceRows = older ? [...traceRows, ...result.events] : result.events;
      traceBefore = result.before;
      $("#trace-mode").value = result.settings.mode;
      const actor = data?.agents.find(a => a.id === $("#trace-agent").value);
      const parent = data?.agents.find(a => a.id === actor?.parent_id);
      const children = actor ? data.agents.filter(a => a.parent_id === actor.id) : [];
      $("#trace-relations").innerHTML = actor ? `<p>${esc(actor.label || actor.id)}${actor.requested_model ? ` · מודל מבוקש: <span dir="ltr">${esc(actor.requested_model)}</span>` : ""}</p>${parent ? `<button type="button" class="text-link" data-agent-history="${esc(parent.id)}">בסשן של ${esc(parent.label || parent.id)} ←</button>` : ""}${children.map(a => `<button type="button" class="text-link" data-agent-history="${esc(a.id)}">↳ ${esc(a.label || a.id)}</button>`).join(" ")}` : '<p class="detail-list">בחרו סוכן כדי לרדת לקשרים המדווחים ולפעולות שלו.</p>';
      $("#trace-coverage").textContent = `נשמרים עד ${result.settings.retention_days} ימים / ${result.settings.max_events} אירועים. מוצגים רק אירועים שנקלטו; תיעוד מתאמים חיצוניים עשוי להיות חלקי. סודות מסוננים, אך ייצוא דורש בדיקה שלך.`;
      $("#trace-more").hidden = !result.more;
      $("#trace-events").innerHTML = traceRows.length ? traceRows.map(row => {
        const actor = data?.agents.find(a => a.id === row.agent_id);
        const timing = row.duration_ms === null ? "" : ` · ${Math.round(row.duration_ms)} ms`;
        return `<button type="button" class="trace-row" data-trace-id="${row.id}"><span class="trace-index" dir="ltr">#${row.id}</span><span><b>${esc(row.tool || row.event)}</b><small>${esc(actor?.label || row.agent_id)} · ${esc(row.task_id || "ללא שיוך משימה")}</small></span><span class="trace-end">${esc(row.status)}${esc(timing)}<small>${esc(timestamp(row.ts))}</small></span></button>`;
      }).join("") : empty("אין אירועים בטווח הנבחר", "תיעוד מתחיל בחיבור בפועל. היסטוריה שלא נשמרה אינה משוחזרת בדיעבד.", true);
    } catch (error) { toast(error.message, true); }
    finally { traceBusy = false; }
  }

  function setTraceLive(value) {
    traceLive = value; $("#trace-live").setAttribute("aria-pressed", String(value));
    $("#trace-live").textContent = value ? "עדכון חי פעיל" : "היסטוריה קפואה";
  }

  async function renewPresence(event) {
    if (!event.isTrusted || !connected || document.hidden || data?.owner?.mode !== "present" || Date.now() - lastPresenceRefresh < 60000) return;
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
    button.disabled = true;
    try {
      await api(path, body);
      await refresh();
      if (message) toast(message);
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = !connected; }
  }

  $("#owner-present").addEventListener("click", event => mutate(event.currentTarget, "/api/owner", {mode: "present"}, "סימנת שאתה כאן. השאלות שממתינות מרוכזות בתור ההחלטות."));
  $("#owner-away").addEventListener("click", event => mutate(event.currentTarget, "/api/owner", {mode: "away"}, "השאלות נשמרות. עבודה שאינה תלויה בתשובה שלך יכולה להמשיך."));
  $("#dispatch").addEventListener("click", event => mutate(event.currentTarget, "/api/dispatch", {}, "ההקצאה נבדקה. משימות זמינות יוקצו לסוכנים מתאימים."));
  $("#add-task").addEventListener("click", () => { $("#create-error").hidden = true; $("#create-dialog").showModal(); });
  $$(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  $("#task-dialog").addEventListener("close", () => { dialogTaskId = null; });
  document.addEventListener("click", event => {
    const taskHistory = event.target.closest("[data-task-history]");
    if (taskHistory) {
      $("#task-dialog").close(); $("#trace-task").value = taskHistory.dataset.taskHistory;
      $("#trace-agent").value = ""; location.hash = "trace"; loadTrace();
    }
    const actorHistory = event.target.closest("[data-agent-history]");
    if (actorHistory) { $("#trace-agent").value = actorHistory.dataset.agentHistory; $("#trace-task").value = ""; location.hash = "trace"; loadTrace(); }
    const traceButton = event.target.closest("[data-trace-id]");
    if (traceButton) {
      const row = traceRows.find(r => String(r.id) === traceButton.dataset.traceId);
      if (row) { $("#trace-dialog-body").textContent = JSON.stringify(row, null, 2); $("#trace-dialog").showModal(); }
    }
    const taskButton = event.target.closest("[data-task]");
    if (taskButton) openTask(taskButton.dataset.task);
    const filterButton = event.target.closest("[data-filter]");
    if (filterButton) {
      filter = filterButton.dataset.filter;
      $$("[data-filter]").forEach(button => { button.classList.toggle("active", button === filterButton); button.setAttribute("aria-pressed", String(button === filterButton)); });
      renderRoadmap();
    }
    const deferButton = event.target.closest("[data-defer]");
    if (deferButton) mutate(deferButton, `/api/questions/${encodeURIComponent(deferButton.dataset.defer)}/defer`, {}, "השאלה נשמרה להמשך. המשימה לא נעלמה מהתוכנית.");
  });
  $("#task-search").addEventListener("input", renderRoadmap);
  $("#trace-refresh").addEventListener("click", () => loadTrace());
  $("#trace-more").addEventListener("click", () => { setTraceLive(false); loadTrace(true); });
  $("#trace-live").addEventListener("click", () => { setTraceLive(!traceLive); if (traceLive) loadTrace(); });
  ["#trace-agent", "#trace-range", "#trace-task"].forEach(id => $(id).addEventListener("change", () => { traceUntil = Date.now() / 1000; loadTrace(); }));
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
    const answer = $("textarea", form).value.trim() || $("input:checked", form)?.value;
    const error = $(".form-error", form);
    if (!answer) { error.textContent = "בחר תשובה או כתוב תשובה משלך."; error.hidden = false; return; }
    const button = $("button[type=submit]", form);
    button.disabled = true;
    error.hidden = true;
    try {
      await api(`/api/questions/${encodeURIComponent(form.dataset.question)}/answer`, {answer});
      drafts.delete(form.dataset.question);
      form.reset();
      await refresh();
      toast("התשובה נשמרה. המנהל יוכל להמשיך מההחלטה הזאת.");
    } catch (err) { error.textContent = err.message; error.hidden = false; button.disabled = false; }
  });
  $("#questions").addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      const form = event.target.closest(".question-form");
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
    const button = $("button[type=submit]", form);
    button.disabled = true;
    error.hidden = true;
    try {
      await api("/api/tasks", {title: String(values.get("title")).trim(), version: values.get("version"), lane: values.get("lane"), priority: Number(values.get("priority")), required_validation: values.get("required_validation"), acceptance});
      form.reset();
      $("#create-dialog").close();
      await refresh();
      location.hash = "roadmap";
      toast("המשימה נוספה לתוכנית עם תנאי הקבלה שלה.");
    } catch (err) { error.textContent = err.message; error.hidden = false; }
    finally { button.disabled = false; }
  });
  window.addEventListener("hashchange", switchView);
  window.addEventListener("online", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  document.addEventListener("pointerdown", renewPresence, {passive: true});
  document.addEventListener("keydown", renewPresence, {passive: true});

  function connectStream() {
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
      catch (error) { connectionState(false, error.message); switchView(); return; }
    }
    switchView();
    await refresh();
    connectStream();
    setInterval(() => { if (!document.hidden) refresh(); }, 12000);
    setInterval(() => { renderLiveStatus(); if (lastGoodAt && Date.now() - lastGoodAt > 45000) connectionState(false, "הנתונים לא עודכנו בזמן האחרון."); }, 10000);
  }
  start();
})();
