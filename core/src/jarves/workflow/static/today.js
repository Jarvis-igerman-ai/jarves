/* JARVES workflow "Today" first screen (#732): what needs the owner, what runs, what is stuck, version progress.
   Read-only: it renders from the state app.js already fetched (the "workflow:render" event) and never answers,
   approves or changes anything. Answer buttons open the existing question form. */
"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const versions = ["V4.5.1", "V5", "V6", "V7"];
  const laneNames = {bugs: "באגים", release: "שחרור", features: "פיצ'רים", ops: "תשתית", security: "אבטחה", manager: "ניהול"};
  const OPEN = new Set(["pending", "deferred", "open"]);
  const unblockers = {you: "אתה", ci: "CI", hardware: "חומרה", team: "הצוות", unknown: "לא צוין"};
  const signatures = {};
  let helpers = {age: () => "", complete: () => false};

  const seconds = value => { const n = typeof value === "number" ? value : Date.parse(value) / 1000; return Number.isFinite(n) ? (n > 1e12 ? n / 1000 : n) : 0; };
  const plural = (n, one, many) => n === 1 ? one : `${n} ${many}`;

  // Re-render a block only when its content changed, so the aria-live list is announced once per change.
  function put(id, html) {
    if (signatures[id] === html) return;
    signatures[id] = html;
    $(id).innerHTML = html;
  }

  function needsYou(data) {
    const tasks = new Map(data.tasks.map(task => [task.id, task]));
    const open = data.questions.filter(q => OPEN.has(q.status || "pending"))
      // Blocking questions first, then the blocked task's priority, then the oldest.
      .sort((a, b) => (b.blocks !== false) - (a.blocks !== false)
        || (tasks.get(a.task_id)?.priority ?? 2) - (tasks.get(b.task_id)?.priority ?? 2)
        || seconds(a.created_at) - seconds(b.created_at));
    $("#today-needs-count").textContent = open.length ? String(open.length) : "";
    const cards = open.slice(0, 3).map(q => {
      const task = tasks.get(q.task_id);
      const recommended = (q.choices || []).find(choice => typeof choice === "object" && choice.recommended);
      const facts = [
        task?.version ? `<span class="pill" dir="ltr">${esc(task.version)}</span>` : "",
        q.minutes ? `<span class="pill amber">כ־${esc(q.minutes)} דק׳</span>` : "",
        q.status === "deferred" ? `<span class="pill">נשמר להמשך</span>` : "",
      ].join("");
      return `<li class="today-question"><div class="today-question-facts">${facts}<span class="today-age">${esc(helpers.age(q.created_at))}</span></div>`
        + `<h3 dir="auto">${esc(q.title)}</h3>`
        + (task ? `<p class="today-line"><span class="today-key">${q.blocks === false ? "קשור ל:" : "חוסם:"}</span> <span dir="auto">${esc(task.title)}</span></p>` : "")
        + (recommended ? `<p class="today-line"><span class="today-key">מומלץ:</span> <span dir="auto">${esc(recommended.label || recommended.id)}</span></p>` : "")
        + (q.safe_default ? `<p class="today-line today-default"><span class="today-key">אם לא תענה:</span> <span dir="auto">${esc(q.safe_default)}</span></p>` : "")
        + `<a class="button primary today-answer" href="#questions" data-today-question="${esc(q.id)}" aria-label="${esc(`לענות על: ${q.title}`)}">לענות <span aria-hidden="true">←</span></a></li>`;
    }).join("");
    const more = open.length > 3 ? `<a class="text-link today-more" href="#questions">${plural(open.length - 3, "ועוד שאלה אחת", "שאלות נוספות")} <span aria-hidden="true">←</span></a>` : "";
    put("#today-needs", open.length ? `<ul class="today-questions">${cards}</ul>${more}`
      : `<p class="today-empty"><span class="status-dot good" aria-hidden="true"></span><b>שום דבר לא מחכה לך.</b> הצוות ממשיך לעבוד; שאלה חדשה תופיע כאן.</p>`);
  }

  function running(data) {
    const now = Date.now() / 1000;
    const tasks = new Map(data.tasks.map(task => [task.id, task]));
    const live = data.agents.filter(agent => agent.status !== "offline" && tasks.get(agent.task_id)?.state === "running")
      .sort((a, b) => seconds(b.progress_at) - seconds(a.progress_at));
    const earlier = data.agents.filter(agent => agent.status === "offline")
      .sort((a, b) => seconds(b.heartbeat_at) - seconds(a.heartbeat_at));
    const archived = (data.agent_counts || {}).archived || 0;
    $("#today-running-count").textContent = live.length ? String(live.length) : "";
    const rows = live.map(agent => {
      const task = tasks.get(agent.task_id);
      const beat = Math.max(seconds(agent.heartbeat_at), seconds(agent.tool_started_at) && now - seconds(agent.tool_started_at) < 900 ? now : 0);
      const fresh = beat && now - beat < 120;
      const model = agent.observed_model || agent.requested_model;
      return `<li><button type="button" class="today-row" data-task="${esc(task.id)}"><span class="status-dot ${fresh ? "good today-pulse" : "warn"}" aria-hidden="true"></span>`
        + `<span class="today-row-main"><span class="today-row-title" dir="auto">${esc(task.title)}</span>`
        + `<span class="today-row-meta"><span class="today-chip">${esc(laneNames[agent.lane] || agent.lane || "כללי")}</span><span dir="auto">${esc(agent.label || agent.id)}</span>${model ? `<span dir="ltr">${esc(model)}</span>` : ""}`
        + `<span>${fresh ? `התקדמות ${esc(helpers.age(agent.progress_at))}` : `<span class="today-warn">אין עדכון ${esc(helpers.age(agent.heartbeat_at))}</span>`}</span></span></span></button></li>`;
    }).join("");
    const queued = data.tasks.filter(task => task.state === "queued" && task.execution_allowed !== false).length;
    const list = rows ? `<ul class="today-list">${rows}</ul>` : `<p class="today-empty">שום עובד לא רץ כרגע · ${plural(queued, "משימה אחת בתור", "בתור")}</p>`;
    const history = earlier.length || archived ? `<details class="today-earlier"><summary>סשנים קודמים: ${earlier.length + archived}</summary><ul>${earlier.slice(0, 8).map(agent => `<li><span dir="auto">${esc(agent.label || agent.id)}</span><span>${esc(helpers.age(agent.heartbeat_at))}</span></li>`).join("")}</ul>${earlier.length > 8 || archived ? `<p>${earlier.length > 8 ? `ועוד ${earlier.length - 8} ברשימת הצוות. ` : ""}${archived ? `${archived} בארכיון.` : ""}</p>` : ""}</details>` : "";
    const open = $("#today-running details")?.open;
    put("#today-running", list + history);
    if (open) $("#today-running details")?.setAttribute("open", "");
  }

  // Who can move a blocked task, from the fields a task carries. Unknown stays unknown.
  function blockedReason(task) {
    const text = String(task.blocker || "");
    const reason = {worker_offline: "העובד התנתק באמצע העבודה", worker_stalled: "העובד נתקע בלי התקדמות"}[text]
      || (/stalled twice/i.test(text) ? "נתקע פעמיים; צריך החלטה" : /^Autopilot start refused/i.test(text) ? "המנוע האוטומטי לא הצליח להתחיל עובד" : "");
    let who = "unknown";
    if (/owner|decision|answer|approv|החלטה|אישור/i.test(text)) who = "you";
    else if (task.required_validation === "device" || /device|phone|hardware|mic|speaker|car\b|raspberry|pi ?5|טלפון|מכשיר|רכב/i.test(text + " " + (task.resources || []).join(" "))) who = "hardware";
    else if (/\bci\b|check|test|pipeline|build|merge queue/i.test(text)) who = "ci";
    else if (/^worker_|^Autopilot/i.test(text)) who = "team";
    return {reason, raw: reason ? "" : text, who};
  }

  function blocked(data) {
    const list = data.tasks.filter(task => task.state === "blocked").sort((a, b) => (a.priority ?? 2) - (b.priority ?? 2));
    $("#today-blocked-count").textContent = list.length ? String(list.length) : "";
    const rows = list.slice(0, 6).map(task => {
      const info = blockedReason(task);
      const why = info.reason ? esc(info.reason) : info.raw ? `<span dir="auto">${esc(info.raw)}</span>` : "לא נרשמה סיבה";
      return `<li><button type="button" class="today-row" data-task="${esc(task.id)}"><span class="today-who today-who-${info.who}">${esc(unblockers[info.who])}</span>`
        + `<span class="today-row-main"><span class="today-row-title" dir="auto">${esc(task.title)}</span><span class="today-row-meta"><span>${why}</span>${task.version ? `<span dir="ltr">${esc(task.version)}</span>` : ""}</span></span></button></li>`;
    }).join("");
    const more = list.length > 6 ? `<a class="text-link today-more" href="#roadmap">ועוד ${list.length - 6} בתוכנית <span aria-hidden="true">←</span></a>` : "";
    put("#today-blocked", list.length ? `<p class="today-hint">התג מראה מי יכול לשחרר.</p><ul class="today-list">${rows}</ul>${more}` : `<p class="today-empty">אין משימות חסומות.</p>`);
  }

  function roadmap(data) {
    const rows = versions.map(version => {
      const required = data.tasks.filter(task => task.version === version && (!task.scope || task.scope === "required"));
      const shipped = required.filter(task => helpers.complete(task)).length;
      const verified = required.filter(task => helpers.complete(task) || ["verified", "done"].includes(task.state)).length;
      const merged = required.filter(task => helpers.complete(task) || ["verified", "done"].includes(task.state) || task.implementation === "merged").length;
      return {version, total: required.length, shipped, verified, merged};
    });
    put("#today-roadmap", `<ul class="today-versions">${rows.map(row => `<li><a class="today-version" href="#roadmap" data-version="${esc(row.version)}" aria-label="${esc(`${row.version}: ${row.merged} מוזגו, ${row.verified} נבדקו, ${row.shipped} שוחררו מתוך ${row.total} חובה. פתח בתוכנית`)}">`
      + `<span class="today-version-name" dir="ltr">${esc(row.version)}</span>`
      + `<span class="today-bar" aria-hidden="true" data-total="${row.total}"><span class="seg shipped" data-n="${row.shipped}"></span><span class="seg verified" data-n="${row.verified - row.shipped}"></span><span class="seg merged" data-n="${row.merged - row.verified}"></span></span>`
      + `<span class="today-version-text" aria-hidden="true"><span><b>${row.merged}</b> מוזגו</span><span><b>${row.verified}</b> נבדקו</span><span><b>${row.shipped}</b> שוחררו</span><span class="today-of">מתוך ${row.total}</span></span></a></li>`).join("")}</ul>`);
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

  // "לענות" opens the existing question form for that question; it never sends an answer itself.
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
