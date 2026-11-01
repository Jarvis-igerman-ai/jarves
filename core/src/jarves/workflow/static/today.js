/* JARVES workflow "Today" blocks of the Overview (#732, owner redesign): what needs the owner, what runs now and
   for how long, what is stuck and since when, version progress.
   Read-only: it renders from the state app.js already fetched (the "workflow:render" event) and never answers,
   approves or changes anything. Answer buttons open the existing question form. */
"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const EN = String(document.documentElement.lang || "he").toLowerCase().startsWith("en");
  const versions = ["V4.5.1", "V5", "V6", "V7"];
  const OPEN = new Set(["pending", "deferred", "open"]);
  const unblockers = {you: "אתה", ci: "CI", hardware: "חומרה", team: "הצוות", unknown: "לא צוין"};
  const unblockersEn = {you: "You", ci: "CI", hardware: "Hardware", team: "Team", unknown: "Unknown"};
  // Page language: <html lang>, Hebrew by default. Technical ids stay in Latin script in both.
  const T = EN ? {
    lanes: {bugs: "Bugs", release: "Release", features: "Features", ops: "Infra", security: "Security", manager: "Manager"},
    general: "General", minutes: n => `~${n} min`, deferred: "Saved for later", blocks: "Blocks:", related: "Related to:",
    recommended: "Recommended:", ifSkip: "If you do not answer:", answer: "Answer", answerLabel: t => `Answer: ${t}`,
    more: n => n === 1 ? "1 more question" : `${n} more questions`,
    nothing: "Nothing is waiting for you.", nothingMore: "The team keeps working; a new question will show up here.",
    started: a => `started ${a}`, noBeat: a => `no progress since ${a}`, noWorker: q => `No worker is running right now · ${q === 1 ? "1 task" : `${q} tasks`} queued`,
    earlier: n => `Earlier sessions: ${n}`, moreTeam: n => `${n} more in the team list. `, archived: n => `${n} archived.`,
    offline: "The worker disconnected mid-task", stalled: "The worker stalled without progress", twice: "Stalled twice; needs a decision",
    refused: "Autopilot could not start a worker", noReason: "No reason recorded", since: a => `since ${a}`,
    hint: "The tag shows who can unblock it.", moreBlocked: n => `${n} more in the plan`, noBlocked: "Nothing is stuck.",
    bar: r => `${r.version}: ${r.merged} merged, ${r.verified} verified, ${r.shipped} shipped of ${r.total} required. Open in the plan`,
    merged: "merged", verified: "verified", shipped: "shipped", of: n => `of ${n}`, who: unblockersEn,
  } : {
    lanes: {bugs: "באגים", release: "שחרור", features: "פיצ'רים", ops: "תשתית", security: "אבטחה", manager: "ניהול"},
    general: "כללי", minutes: n => `כ־${n} דק׳`, deferred: "נשמר להמשך", blocks: "חוסם:", related: "קשור ל:",
    recommended: "מומלץ:", ifSkip: "אם לא תענה:", answer: "לענות", answerLabel: t => `לענות על: ${t}`,
    more: n => n === 1 ? "ועוד שאלה אחת" : `${n} שאלות נוספות`,
    nothing: "שום דבר לא מחכה לך.", nothingMore: "הצוות ממשיך לעבוד; שאלה חדשה תופיע כאן.",
    started: a => `התחיל ${a}`, noBeat: a => `ללא התקדמות מאז ${a}`, noWorker: q => `שום עובד לא רץ כרגע · ${q === 1 ? "משימה אחת" : `${q} משימות`} בתור`,
    earlier: n => `סשנים קודמים: ${n}`, moreTeam: n => `ועוד ${n} ברשימת הצוות. `, archived: n => `${n} בארכיון.`,
    offline: "העובד התנתק באמצע העבודה", stalled: "העובד נתקע בלי התקדמות", twice: "נתקע פעמיים; צריך החלטה",
    refused: "המנוע האוטומטי לא הצליח להתחיל עובד", noReason: "לא נרשמה סיבה", since: a => `מאז ${a}`,
    hint: "התג מראה מי יכול לשחרר.", moreBlocked: n => `ועוד ${n} בתוכנית`, noBlocked: "שום דבר לא תקוע.",
    bar: r => `${r.version}: ${r.merged} מוזגו, ${r.verified} נבדקו, ${r.shipped} שוחררו מתוך ${r.total} חובה. פתח בתוכנית`,
    merged: "מוזגו", verified: "נבדקו", shipped: "שוחררו", of: n => `מתוך ${n}`, who: unblockers,
  };
  const signatures = {};
  let helpers = {age: () => "", complete: () => false};

  const seconds = value => { const n = typeof value === "number" ? value : Date.parse(value) / 1000; return Number.isFinite(n) ? (n > 1e12 ? n / 1000 : n) : 0; };
  // Clock time for "since": today shows HH:MM, older shows day/month too.
  const clock = value => {
    const ts = seconds(value);
    if (!ts) return "";
    const d = new Date(ts * 1000), today = new Date().toDateString() === d.toDateString();
    return d.toLocaleString(EN ? "en-GB" : "he-IL", today ? {hour: "2-digit", minute: "2-digit"} : {day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit"});
  };

  // Re-render a block only when its content changed, so the aria-live list is announced once per change.
  function put(id, html) {
    if (signatures[id] === html) return;
    signatures[id] = html;
    $(id).innerHTML = html;
  }

  function needsYou(data) {
    const tasks = new Map(data.tasks.map(task => [task.id, task]));
    const open = data.questions.filter(q => OPEN.has(q.status || "pending"))
      // Oldest waiting first (owner research): the longest wait is the most urgent; ties by the blocked task's priority.
      .sort((a, b) => seconds(a.created_at) - seconds(b.created_at)
        || (tasks.get(a.task_id)?.priority ?? 2) - (tasks.get(b.task_id)?.priority ?? 2));
    $("#today-needs-count").textContent = open.length ? String(open.length) : "";
    // Shown only when something waits; the verdict bar already says "nothing needs you" calmly.
    $("#today-needs").closest(".today-block").hidden = !open.length;
    const cards = open.slice(0, 3).map(q => {
      const task = tasks.get(q.task_id);
      const recommended = (q.choices || []).find(choice => typeof choice === "object" && choice.recommended);
      const facts = [
        task?.version ? `<span class="pill" dir="ltr">${esc(task.version)}</span>` : "",
        q.minutes ? `<span class="pill amber">${esc(T.minutes(q.minutes))}</span>` : "",
        q.status === "deferred" ? `<span class="pill">${esc(T.deferred)}</span>` : "",
      ].join("");
      return `<li class="today-question"><div class="today-question-facts">${facts}<span class="today-age">${esc(helpers.age(q.created_at))}</span></div>`
        + `<h3 dir="auto">${esc(q.title)}</h3>`
        + (task ? `<p class="today-line"><span class="today-key">${esc(q.blocks === false ? T.related : T.blocks)}</span> <span dir="auto">${esc(task.title)}</span></p>` : "")
        + (recommended ? `<p class="today-line"><span class="today-key">${esc(T.recommended)}</span> <span dir="auto">${esc(recommended.label || recommended.id)}</span></p>` : "")
        + (q.safe_default ? `<p class="today-line today-default"><span class="today-key">${esc(T.ifSkip)}</span> <span dir="auto">${esc(q.safe_default)}</span></p>` : "")
        + `<a class="button primary today-answer" href="#questions" data-today-question="${esc(q.id)}" aria-label="${esc(T.answerLabel(q.title))}">${esc(T.answer)} <span aria-hidden="true" class="dir-arrow">←</span></a></li>`;
    }).join("");
    const more = open.length > 3 ? `<a class="text-link today-more" href="#questions">${esc(T.more(open.length - 3))} <span aria-hidden="true" class="dir-arrow">←</span></a>` : "";
    put("#today-needs", open.length ? `<ul class="today-questions">${cards}</ul>${more}`
      : `<p class="today-empty"><span class="status-dot good" aria-hidden="true"></span><b>${esc(T.nothing)}</b> ${esc(T.nothingMore)}</p>`);
  }

  function running(data) {
    const now = Date.now() / 1000;
    const tasks = new Map(data.tasks.map(task => [task.id, task]));
    const beatOf = agent => Math.max(seconds(agent.heartbeat_at), seconds(agent.tool_started_at) && now - seconds(agent.tool_started_at) < 900 ? now : 0);
    // Stalled rows float to the top (they need attention); then the most recent progress.
    const live = data.agents.filter(agent => agent.status !== "offline" && tasks.get(agent.task_id)?.state === "running")
      .sort((a, b) => (now - beatOf(b) >= 120) - (now - beatOf(a) >= 120) || seconds(b.progress_at) - seconds(a.progress_at));
    const earlier = data.agents.filter(agent => agent.status === "offline")
      .sort((a, b) => seconds(b.heartbeat_at) - seconds(a.heartbeat_at));
    const archived = (data.agent_counts || {}).archived || 0;
    $("#today-running-count").textContent = live.length ? String(live.length) : "";
    const rows = live.map(agent => {
      const task = tasks.get(agent.task_id);
      const beat = beatOf(agent);
      const fresh = beat && now - beat < 120;
      const model = agent.observed_model || agent.requested_model;
      // "How long": the task's last state change (claim) is the best start time the state carries.
      const started = task.updated_at ? `<span>${esc(T.started(helpers.age(task.updated_at)))}</span>` : "";
      return `<li><button type="button" class="today-row" data-task="${esc(task.id)}"><span class="status-dot ${fresh ? "good today-pulse" : "warn"}" aria-hidden="true"></span>`
        + `<span class="today-row-main"><span class="today-row-title" dir="auto">${esc(task.title)}</span>`
        + `<span class="today-row-meta"><span class="today-chip">${esc(T.lanes[agent.lane] || agent.lane || T.general)}</span><span dir="auto">${esc(agent.label || agent.id)}</span>${model ? `<span dir="ltr">${esc(model)}</span>` : ""}`
        + `${started}${fresh ? "" : `<span class="today-warn"><span aria-hidden="true">⚠ </span>${esc(T.noBeat(clock(beat)))}</span>`}</span></span></button></li>`;
    }).join("");
    const queued = data.tasks.filter(task => task.state === "queued" && task.execution_allowed !== false).length;
    const list = rows ? `<ul class="today-list">${rows}</ul>` : `<p class="today-empty">${esc(T.noWorker(queued))}</p>`;
    const history = earlier.length || archived ? `<details class="today-earlier"><summary>${esc(T.earlier(earlier.length + archived))}</summary><ul>${earlier.slice(0, 8).map(agent => `<li><span dir="auto">${esc(agent.label || agent.id)}</span><span>${esc(helpers.age(agent.heartbeat_at))}</span></li>`).join("")}</ul>${earlier.length > 8 || archived ? `<p>${earlier.length > 8 ? esc(T.moreTeam(earlier.length - 8)) : ""}${archived ? esc(T.archived(archived)) : ""}</p>` : ""}</details>` : "";
    const open = $("#today-running details")?.open;
    put("#today-running", list + history);
    if (open) $("#today-running details")?.setAttribute("open", "");
  }

  // Who can move a blocked task, from the fields a task carries. Unknown stays unknown.
  function blockedReason(task) {
    const text = String(task.blocker || "");
    const reason = {worker_offline: "worker_offline", worker_stalled: "worker_stalled"}[text]
      || (/stalled twice/i.test(text) ? "twice" : /^Autopilot start refused/i.test(text) ? "refused" : "");
    let who = "unknown";
    if (/owner|decision|answer|approv|החלטה|אישור/i.test(text)) who = "you";
    else if (task.required_validation === "device" || /device|phone|hardware|mic|speaker|car\b|raspberry|pi ?5|טלפון|מכשיר|רכב/i.test(text + " " + (task.resources || []).join(" "))) who = "hardware";
    else if (/\bci\b|check|test|pipeline|build|merge queue/i.test(text)) who = "ci";
    else if (/^worker_|^Autopilot/i.test(text)) who = "team";
    return {reason, raw: reason ? "" : text, who};
  }
  const reasonText = {worker_offline: () => T.offline, worker_stalled: () => T.stalled, twice: () => T.twice, refused: () => T.refused};

  function blocked(data) {
    // Most important first, then the longest stuck.
    const list = data.tasks.filter(task => task.state === "blocked")
      .sort((a, b) => (a.priority ?? 2) - (b.priority ?? 2) || seconds(a.updated_at) - seconds(b.updated_at));
    $("#today-blocked-count").textContent = list.length ? String(list.length) : "";
    const rows = list.slice(0, 6).map(task => {
      const info = blockedReason(task);
      const why = info.reason ? esc(reasonText[info.reason]()) : info.raw ? `<span dir="auto">${esc(info.raw)}</span>` : esc(T.noReason);
      const since = task.updated_at ? `<span class="today-since">${esc(T.since(clock(task.updated_at)))} · ${esc(helpers.age(task.updated_at))}</span>` : "";
      return `<li><button type="button" class="today-row" data-task="${esc(task.id)}"><span class="today-who today-who-${info.who}">${esc(T.who[info.who])}</span>`
        + `<span class="today-row-main"><span class="today-row-title" dir="auto">${esc(task.title)}</span><span class="today-row-meta"><span>${why}</span>${since}${task.version ? `<span dir="ltr">${esc(task.version)}</span>` : ""}</span></span></button></li>`;
    }).join("");
    const more = list.length > 6 ? `<a class="text-link today-more" href="#roadmap">${esc(T.moreBlocked(list.length - 6))} <span aria-hidden="true" class="dir-arrow">←</span></a>` : "";
    put("#today-blocked", list.length ? `<p class="today-hint">${esc(T.hint)}</p><ul class="today-list">${rows}</ul>${more}` : `<p class="today-empty"><span class="status-dot good" aria-hidden="true"></span>${esc(T.noBlocked)}</p>`);
  }

  function roadmap(data) {
    const rows = versions.map(version => {
      const required = data.tasks.filter(task => task.version === version && (!task.scope || task.scope === "required"));
      const shipped = required.filter(task => helpers.complete(task)).length;
      const verified = required.filter(task => helpers.complete(task) || ["verified", "done"].includes(task.state)).length;
      const merged = required.filter(task => helpers.complete(task) || ["verified", "done"].includes(task.state) || task.implementation === "merged").length;
      return {version, total: required.length, shipped, verified, merged};
    });
    put("#today-roadmap", `<ul class="today-versions">${rows.map(row => `<li><a class="today-version" href="#roadmap" data-version="${esc(row.version)}" aria-label="${esc(T.bar(row))}">`
      + `<span class="today-version-name" dir="ltr">${esc(row.version)}</span>`
      + `<span class="today-bar" aria-hidden="true" data-total="${row.total}"><span class="seg shipped" data-n="${row.shipped}"></span><span class="seg verified" data-n="${row.verified - row.shipped}"></span><span class="seg merged" data-n="${row.merged - row.verified}"></span></span>`
      + `<span class="today-version-text" aria-hidden="true"><span><b>${row.merged}</b> ${esc(T.merged)}</span><span><b>${row.verified}</b> ${esc(T.verified)}</span><span><b>${row.shipped}</b> ${esc(T.shipped)}</span><span class="today-of">${esc(T.of(row.total))}</span></span></a></li>`).join("")}</ul>`);
    // Widths through the CSSOM: the strict CSP forbids style attributes in markup.
    document.querySelectorAll("#today-roadmap .today-bar").forEach(bar => {
      const total = Number(bar.dataset.total) || 1;
      bar.querySelectorAll(".seg").forEach(seg => { seg.style.inlineSize = `${Math.min(100, Number(seg.dataset.n) / total * 100)}%`; });
    });
  }

  document.addEventListener("workflow:render", event => {
    const {data, ...rest} = event.detail || {};
    if (!data || !Array.isArray(data.tasks)) return;
    helpers = {...helpers, ...rest};
    $("#today").hidden = false;
    needsYou(data); running(data); blocked(data); roadmap(data);
  });

  // The answer button opens the existing question form for that question; it never sends an answer itself.
  document.addEventListener("click", event => {
    const link = event.target.closest("[data-today-question]");
    if (!link) return;
    event.preventDefault();
    const id = link.dataset.todayQuestion;
    const reveal = () => requestAnimationFrame(() => {
      const card = document.getElementById(`question-${id}`);
      if (!card) return;
      card.classList.add("today-target");
      card.scrollIntoView({block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
      (card.querySelector(".question-form input, .question-form textarea") || card).focus({preventScroll: true});
      setTimeout(() => card.classList.remove("today-target"), 2400);
    });
    if (location.hash === "#questions") reveal();
    else { window.addEventListener("hashchange", reveal, {once: true}); location.hash = "questions"; }
  });
})();
