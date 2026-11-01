/* JARVES workflow: the "Blocked" view. One page that answers: what is blocked, why, since when, who must act and
   what unblocks it. Read-only: GET /api/blocked (observability.blocked_view). Commands are shown as text with a
   copy button; nothing is ever executed or sent from this page. A separate file because the strict CSP allows no
   inline scripts. ?demo=1 draws a clearly labelled built-in sample when the service has no data yet. */
"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const TZ = "Asia/Jerusalem";
  const CAUSES = ["owner", "worker_gone", "dependency", "resource", "pr", "other"];
  const TONE = {owner: "attention", worker_gone: "bad", dependency: "idle", resource: "busy", pr: "attention", other: "unknown"};
  const ICON = {owner: "◆", worker_gone: "✕", dependency: "⋯", resource: "■", pr: "⑂", other: "?"};
  // Locale part for this view (page language: <html lang>, Hebrew by default). One language per string.
  const I18N = {
    he: {
      cause: {owner: "ממתין לך", worker_gone: "עובד נעלם", dependency: "תלות", resource: "משאב תפוס", pr: "PR חסום", other: "אחר"},
      meaning: {owner: "שאלה פתוחה שלך עוצרת את המשימה. התשובה משחררת אותה.",
        worker_gone: "העובד הפסיק לדווח באמצע. הנעילות שלו נשארות עד שהמנהל משחרר.",
        dependency: "מחכה שמשימה אחרת תאומת קודם.", resource: "משאב בלעדי תפוס בידי משימה אחרת.",
        pr: "בקשת מיזוג שלא יכולה להתמזג עכשיו: התנגשות, בדיקה אדומה, סקירה ממתינה או בעלים שנעלם.",
        other: "סיבה אחרת. טקסט החסימה מוצג כמו שהוא."},
      part: {owner: n => n === 1 ? "אחד מחכה לך" : `${n} מחכים לך`, worker_gone: n => n === 1 ? "עובד אחד נעלם" : `${n} עובדים שנעלמו`,
        dependency: n => n === 1 ? "תלות אחת" : `${n} תלויות`, resource: n => n === 1 ? "משאב תפוס אחד" : `${n} משאבים תפוסים`,
        pr: n => n === 1 ? "PR חסום אחד" : `${n} PR חסומים`, other: n => n === 1 ? "אחד מסיבה אחרת" : `${n} מסיבה אחרת`},
      total: n => n === 1 ? "חסום אחד" : `${n} חסומים`,
      next: {answer: "לענות על השאלה בלשונית ההחלטות", recover: "המנהל בודק את נקודת השמירה ומשחרר את המשימה",
        release_claim: "המנהל משחרר את הבעלות הישנה", wait_dep: "אין מה לעשות עכשיו: תתחיל כשהתלות תאומת",
        wait_resource: "אין מה לעשות עכשיו: תתחיל כשהמשאב יתפנה", pr_conflict: "המנהל מעדכן את הענף מ־main ופותר התנגשויות",
        pr_red: "המנהל קורא את הבדיקה שנכשלה ודוחף תיקון", pr_review: "מחכה לסקירה עצמאית של Opus",
        pr_owned: "המנהל משחרר את הבעלות הישנה על ה־PR", inspect: "המנהל קורא את סיבת החסימה ומחליט"},
      reason: {pr_conflict: "התנגשות", pr_red: "בדיקה אדומה", pr_review: "סקירה ממתינה", pr_owned: "בעלים נעלם"},
      who: "מי פועל", step: "הצעד הבא", since: "ממתי", blocker: "סיבה", locks: "נעילות שנשארו", waiting: "מחכה ל־",
      you: "אתה", manager: "המנהל", review: "סוקר Opus", agent: "העובד", openQuestion: "לשאלה", openPr: "פתח ב־GitHub",
      copy: "העתק", copied: "הועתק", copyFailed: "סמנו והעתיקו ידנית", copyLabel: c => `העתק את הפקודה — ${c}`,
      ago: {now: "עכשיו", m: n => `לפני ${n} דק׳`, h: n => `לפני ${n} שע׳`, d: n => n === 1 ? "לפני יום" : `לפני ${n} ימים`},
      clearTitle: "אין חסומים. הכול זורם.", clearHint: "כשמשהו ייחסם הוא יופיע כאן עם הסיבה, ממתי ומי צריך לפעול.",
      ledger: n => n === 1 ? "משימה אחת במצב חסום בלדג׳ר. הדף מראה גם מה מחכה לך, לתלות, למשאב או ל־PR." : `${n} משימות במצב חסום בלדג׳ר. הדף מראה גם מה מחכה לך, לתלות, למשאב או ל־PR.`,
      prsMissing: "אין תמונת GitHub עדכנית, ולכן בקשות מיזוג לא נבדקו כאן.", readOnly: "קריאה בלבד: פקודות מוצגות להעתקה, שום דבר לא מופעל מהדף.",
      jump: "קפיצה לפי סיבה", updated: at => `עודכן ${at}`, loading: "טוען את רשימת החסומים…",
      missing: "השירות המקומי עוד לא מדווח חסומים", missingHint: "אחרי עדכון השירות המקומי הרשימה תופיע כאן. אפשר לראות דוגמה עם ‎?demo=1‎ בכתובת.",
      failed: s => `לא ניתן לטעון את רשימת החסומים (${s}). ננסה שוב בעוד חצי דקה.`,
      demo: "נתוני הדגמה בלבד — זה לא המצב האמיתי של המפעל."},
    en: {
      cause: {owner: "Waiting for you", worker_gone: "Worker gone", dependency: "Dependency", resource: "Resource busy", pr: "Blocked PR", other: "Other"},
      meaning: {owner: "An open question of yours holds this task. Your answer releases it.",
        worker_gone: "The worker stopped reporting mid-task. Its locks stay held until the manager releases them.",
        dependency: "Waits for another task to be verified first.", resource: "An exclusive resource is held by another task.",
        pr: "A pull request that cannot merge now (conflicts, red checks, a pending review or a stale owner).",
        other: "Another reason. The raw blocker text is shown as is."},
      part: {owner: n => `${n} waiting for you`, worker_gone: n => n === 1 ? "1 worker gone" : `${n} workers gone`,
        dependency: n => n === 1 ? "1 dependency" : `${n} dependencies`, resource: n => n === 1 ? "1 resource busy" : `${n} resources busy`,
        pr: n => n === 1 ? "1 blocked PR" : `${n} blocked PRs`, other: n => `${n} other`},
      total: n => `${n} blocked`,
      next: {answer: "Answer the question in the Decisions tab", recover: "The manager checks the checkpoint, then recovers the task",
        release_claim: "The manager releases the stale claim", wait_dep: "Nothing to do now; it starts when the dependency is verified",
        wait_resource: "Nothing to do now; it starts when the resource is free", pr_conflict: "The manager updates the branch from main and resolves the conflicts",
        pr_red: "The manager reads the failed check and pushes a fix", pr_review: "Waits for the independent Opus review",
        pr_owned: "The manager releases the stale claim on this PR", inspect: "The manager reads the blocker and decides"},
      reason: {pr_conflict: "Conflicts", pr_red: "Red checks", pr_review: "Review pending", pr_owned: "Owner gone"},
      who: "Who acts", step: "Next step", since: "Since", blocker: "Reason", locks: "Locks still held", waiting: "Waiting on ",
      you: "You", manager: "The manager", review: "Opus reviewer", agent: "Agent", openQuestion: "Open the question", openPr: "Open on GitHub",
      copy: "Copy", copied: "Copied", copyFailed: "Select and copy by hand", copyLabel: c => `Copy the command — ${c}`,
      ago: {now: "just now", m: n => `${n} min ago`, h: n => `${n} h ago`, d: n => n === 1 ? "1 day ago" : `${n} days ago`},
      clearTitle: "Nothing is blocked. Work is flowing.", clearHint: "When something blocks it shows up here with the reason, since when and who must act.",
      ledger: n => `${n} task(s) in the blocked state in the ledger. This page also shows what waits for you, a dependency, a resource or a PR.`,
      prsMissing: "No fresh GitHub snapshot, so pull requests were not checked here.", readOnly: "Read-only: commands are shown to copy; nothing runs from this page.",
      jump: "Jump to a cause", updated: at => `Updated ${at}`, loading: "Loading the blocked list…",
      missing: "The local service does not report blocked work yet", missingHint: "After the local service is updated the list appears here. Add ?demo=1 to the address to see a sample.",
      failed: s => `Could not load the blocked list (${s}). Retrying in half a minute.`,
      demo: "Demo data only — this is not the real factory state."},
  };
  const lang = String(document.documentElement.lang || "he").toLowerCase().startsWith("en") ? "en" : "he";
  const L = I18N[lang];
  const DEMO = new URLSearchParams(location.search).get("demo") === "1";
  const locale = lang === "en" ? "en-GB" : "he-IL";
  const clock = new Intl.DateTimeFormat(locale, {timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
  const dayClock = new Intl.DateTimeFormat(locale, {timeZone: TZ, day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
  let view = null, failure = "", missing = false, loaded = false, busy = false, skew = 0, polls = 0;
  const signatures = {};

  const num = value => (typeof value === "number" && Number.isFinite(value) ? value : null);
  const serverNow = () => Date.now() / 1000 + skew;
  const absolute = epoch => (serverNow() - epoch > 20 * 3600 ? dayClock : clock).format(new Date(epoch * 1000));
  function ago(epoch) {
    const s = Math.max(0, serverNow() - epoch);
    if (s < 60) return L.ago.now;
    if (s < 3600) return L.ago.m(Math.floor(s / 60));
    if (s < 48 * 3600) return L.ago.h(Math.floor(s / 3600));
    return L.ago.d(Math.floor(s / 86400));
  }
  const id = value => `<bdi class="mono" dir="ltr">${esc(value)}</bdi>`;
  function githubURL(value) {
    try {
      const url = new URL(String(value));
      return url.protocol === "https:" && url.hostname === "github.com" && !url.username && !url.password ? url.href : "";
    } catch (_) { return ""; }
  }

  // Built-in sample for ?demo=1, relative to now; never mixed with real data.
  function demoView() {
    const now = Math.floor(Date.now() / 1000);
    const row = (cause, kind, ident, title, age, who, next, extra = {}) => ({cause, kind, id: ident, title, since: now - age, who, next_code: next,
      commands: [], blocker: "", question_id: "", agent_id: "", locks: [], waiting_on: [], reasons: [], url: "", ...extra});
    const rows = [
      row("owner", "task", "V5-PHONE-PAIR", "Pair the phone with the PC", 3 * 3600, "owner", "answer", {question_id: "q-phone-pair"}),
      row("worker_gone", "task", "BUG-812", "Wake word misses the first syllable", 5 * 3600 + 600, "manager", "recover",
        {blocker: "worker_offline", agent_id: "worker-7", locks: ["windows-gui", "file:core/src/jarves/wake"], commands: ["workflow_console.py recover --task BUG-812"]}),
      row("worker_gone", "task", "OPS-SYNC", "Nightly sync stalls on big repos", 26 * 3600, "manager", "recover",
        {blocker: "Native session stopped before a ledger handoff; manager must inspect the result/checkpoint", commands: ["workflow_console.py recover --task OPS-SYNC"]}),
      row("dependency", "task", "V5-CAR-MODE", "Car mode voice loop", 50 * 60, "task:V5-PHONE-PAIR", "wait_dep", {waiting_on: ["V5-PHONE-PAIR"]}),
      row("resource", "task", "BUG-820", "Tray icon flickers", 12 * 60, "task:BUG-812", "wait_resource", {waiting_on: ["windows-gui"]}),
      row("pr", "pr", "791", "Installer: silent upgrade keeps settings", 7 * 3600, "manager", "pr_conflict", {reasons: ["pr_conflict", "pr_review"]}),
      row("other", "task", "REL-46", "Beta 4.6 sign-off sheet", 2 * 86400, "manager", "inspect", {blocker: "phone sheet missing; owner travels until Sunday"}),
    ];
    const groups = CAUSES.map(cause => ({cause, rows: rows.filter(r => r.cause === cause)})).map(g => ({...g, count: g.rows.length}));
    return {now, total: rows.length, groups, tasks_in_blocked_state: 3, prs: {state: "ok"}};
  }

  async function get(path) {
    const response = await fetch(path, {credentials: "same-origin", cache: "no-store"});
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  function setBadge() {
    const badge = $("#blocked-count");
    if (!badge) return;
    const n = view ? view.total : 0;
    badge.textContent = String(n);
    badge.hidden = !n;
  }

  async function load() {
    if (busy) return;
    busy = true;
    try {
      let result = null;
      try { result = await get("/api/blocked"); failure = ""; }
      catch (error) { failure = error.message || "?"; }
      missing = !failure && result === null;
      if (!result && DEMO) { result = demoView(); missing = false; failure = ""; }
      if (result && typeof result === "object" && Array.isArray(result.groups)) {
        view = result;
        skew = num(result.now) !== null ? result.now - Date.now() / 1000 : 0;
      } else if (!failure) view = null;
      loaded = true;
      setBadge();
      if (visible()) render();
    } finally { busy = false; }
  }

  function summary() {
    if (!view || !view.total) return L.clearTitle;
    const parts = CAUSES.map(cause => [cause, (view.groups.find(g => g.cause === cause) || {}).count || 0])
      .filter(([, n]) => n).map(([cause, n]) => L.part[cause](n));
    return `${L.total(view.total)}: ${parts.join(", ")}`;
  }

  function who(value) {
    const text = String(value || "");
    if (text === "owner") return {tone: "attention", html: esc(L.you)};
    if (text === "manager") return {tone: "busy", html: esc(L.manager)};
    if (text === "review") return {tone: "done", html: esc(L.review)};
    if (text.startsWith("task:")) return {tone: "idle", html: `${esc(L.waiting)}${id(text.slice(5))}`};
    if (text.startsWith("agent:")) return {tone: "bad", html: `${esc(L.agent)} ${id(text.slice(6))}`};
    return {tone: "unknown", html: esc(text)};
  }

  function rowHTML(r) {
    const since = num(r.since);
    const actor = who(r.who);
    const ref = r.kind === "pr" ? id(`#${r.id}`) : r.kind === "task" ? `<button type="button" class="text-link bk-id" data-task="${esc(r.id)}">${id(r.id)}</button>` : id(r.id);
    const reasons = (Array.isArray(r.reasons) ? r.reasons : []).filter(x => L.reason[x])
      .map(x => `<li class="bk-chip">${esc(L.reason[x])}</li>`).join("");
    const waiting = (Array.isArray(r.waiting_on) ? r.waiting_on : []).map(w => r.cause === "dependency"
      ? `<button type="button" class="text-link" data-task="${esc(w)}">${id(w)}</button>` : id(w)).join(" ");
    const locks = (Array.isArray(r.locks) ? r.locks : []).map(id).join(" ");
    const commands = (Array.isArray(r.commands) ? r.commands : []).map(c => `<div class="bk-cmd"><code dir="ltr">${esc(c)}</code>`
      + `<button type="button" class="button secondary bk-copy" data-copy="${esc(c)}" aria-label="${esc(L.copyLabel(c))}">${esc(L.copy)}</button></div>`).join("");
    const url = githubURL(r.url);
    const links = (r.question_id ? `<a class="text-link" href="#questions" data-question="${esc(r.question_id)}">${esc(L.openQuestion)} ${id(r.question_id)} <span aria-hidden="true" class="dir-arrow">←</span></a>` : "")
      + (url ? `<a class="text-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(L.openPr)} <span aria-hidden="true" class="dir-arrow">↗</span></a>` : "");
    return `<li class="bk-row"><div class="bk-row-head"><span class="bk-title" dir="auto">${esc(r.title || r.id)}</span>${ref}</div>`
      + (reasons ? `<ul class="bk-chips">${reasons}</ul>` : "")
      + `<dl class="bk-facts">`
      + `<div><dt>${esc(L.since)}</dt><dd>${since === null ? "—" : `<time datetime="${esc(new Date(since * 1000).toISOString())}" data-since="${esc(since)}">${esc(ago(since))}</time> <span class="bk-abs">· ${esc(absolute(since))}</span>`}</dd></div>`
      + `<div><dt>${esc(L.who)}</dt><dd><span class="bk-who" data-tone="${actor.tone}">${actor.html}</span>${waiting && r.cause !== "dependency" ? ` <span class="bk-waiting">${waiting}</span>` : ""}</dd></div>`
      + `<div><dt>${esc(L.step)}</dt><dd>${esc(L.next[r.next_code] || r.next || "")}${waiting && r.cause === "dependency" ? ` <span class="bk-waiting">${waiting}</span>` : ""}</dd></div>`
      + (r.blocker && ["other", "worker_gone"].includes(r.cause) ? `<div><dt>${esc(L.blocker)}</dt><dd><q class="bk-blocker" dir="auto">${esc(r.blocker)}</q></dd></div>` : "")
      + (locks ? `<div><dt>${esc(L.locks)}</dt><dd class="bk-locks">${locks}</dd></div>` : "")
      + `</dl>${commands}${links ? `<div class="bk-links">${links}</div>` : ""}</li>`;
  }

  function groupHTML(g, index) {
    const cause = CAUSES.includes(g.cause) ? g.cause : "other";
    const rows = Array.isArray(g.rows) ? g.rows : [];
    return `<section class="panel bk-group" id="blocked-${cause}" data-tone="${TONE[cause]}" aria-labelledby="blocked-${cause}-title">`
      + `<div class="panel-heading bk-group-head"><div><span class="bk-icon" aria-hidden="true">${ICON[cause]}</span>`
      + `<h2 id="blocked-${cause}-title" tabindex="-1">${esc(L.cause[cause])}</h2><span class="today-count${TONE[cause] === "attention" || TONE[cause] === "bad" ? " warn" : ""}">${rows.length}</span></div>`
      + `<span class="section-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span></div>`
      + `<p class="bk-meaning">${esc(L.meaning[cause])}</p><ul class="bk-rows">${rows.map(rowHTML).join("")}</ul></section>`;
  }

  // The summary bar (with the view's one polite live region) is built once and only updated, so a screen reader
  // hears the sentence when it changes, never on every refresh.
  function shell(root) {
    if ($("#blocked-summary")) return;
    root.innerHTML = `<div id="blocked-demo"></div><section class="bk-summary" id="blocked-bar" data-tone="unknown" aria-labelledby="blocked-summary">`
      + `<span class="bk-summary-icon" id="blocked-icon" aria-hidden="true">…</span><div class="bk-summary-main">`
      + `<h2 id="blocked-summary" aria-live="polite"></h2><div id="blocked-notes"></div></div></section><div id="blocked-body"></div>`;
  }

  function render() {
    const root = $("#blocked-root");
    if (!root) return;
    shell(root);
    const set = (sel, html) => { if (signatures[sel] !== html) { signatures[sel] = html; $(sel).innerHTML = html; } };
    set("#blocked-demo", DEMO && view ? `<p class="demo-notice" role="note">${esc(L.demo)}</p>` : "");
    let title, tone = "unknown", icon = "…", notes = "", body = "";
    if (!loaded) title = L.loading;
    else if (!view && missing) { title = L.missing; notes = `<p class="bk-note">${esc(L.missingHint)}</p>`; }
    else if (!view) { title = L.failed(failure); tone = "bad"; icon = "✕"; }
    else {
      const groups = view.groups.filter(g => g && CAUSES.includes(g.cause) && (g.count || (Array.isArray(g.rows) && g.rows.length)));
      const owner = (view.groups.find(g => g.cause === "owner") || {}).count || 0;
      const clear = !view.total;
      title = summary();
      tone = clear ? "ok" : owner ? "attention" : "bad";
      icon = clear ? "✓" : "!";
      notes = (clear ? [L.clearHint] : [view.tasks_in_blocked_state ? L.ledger(view.tasks_in_blocked_state) : "",
        view.prs && view.prs.state !== "ok" ? L.prsMissing : "", L.readOnly]).filter(Boolean).map(t => `<p class="bk-note">${esc(t)}</p>`).join("")
        + `<p class="bk-updated">${esc(L.updated(clock.format(new Date())))}</p>`;
      const jump = groups.length > 1 ? `<div class="bk-jump" role="group" aria-label="${esc(L.jump)}">${groups.map(g => `<button type="button" class="filter bk-jump-btn" data-jump="${esc(g.cause)}" data-tone="${TONE[g.cause]}"><span>${esc(L.cause[g.cause])}</span> <b>${esc(g.count)}</b></button>`).join("")}</div>` : "";
      body = jump + groups.map(groupHTML).join("") + (failure ? `<p class="hl-quiet" role="status">${esc(L.failed(failure))}</p>` : "");
    }
    const bar = $("#blocked-bar");
    bar.dataset.tone = tone;
    $("#blocked-icon").textContent = icon;
    const live = $("#blocked-summary");
    if (live.textContent !== title) live.textContent = title;
    set("#blocked-notes", notes);
    const focused = document.activeElement?.closest?.("#blocked-body [data-copy], #blocked-body [data-task], #blocked-body [data-jump]");
    const key = focused ? (focused.dataset.copy || focused.dataset.task || focused.dataset.jump) : null;
    set("#blocked-body", body);
    if (key && !root.contains(focused)) [...root.querySelectorAll("[data-copy], [data-task], [data-jump]")].find(b => (b.dataset.copy || b.dataset.task || b.dataset.jump) === key)?.focus({preventScroll: true});
  }

  async function copy(button) {
    const text = button.dataset.copy || "";
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (_) { ok = false; }
    if (!ok) {
      const code = button.parentElement?.querySelector("code");
      if (code) { const range = document.createRange(); range.selectNodeContents(code); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range); }
    }
    button.textContent = ok ? `${L.copied} ✓` : L.copyFailed;
    setTimeout(() => { button.textContent = L.copy; }, 2000);
  }

  // Live "since" text without a refetch.
  function tick() {
    document.querySelectorAll("#blocked-root time[data-since]").forEach(node => {
      const text = ago(Number(node.dataset.since));
      if (node.textContent !== text) node.textContent = text;
    });
  }

  document.addEventListener("click", event => {
    const target = event.target.closest?.("#blocked-root [data-copy], #blocked-root [data-jump], #blocked-root [data-question]");
    if (!target) return;
    if (target.dataset.copy !== undefined) copy(target);
    else if (target.dataset.jump) { const g = $(`#blocked-${CSS.escape(target.dataset.jump)}`); g?.scrollIntoView({block: "start"}); g?.querySelector("h2")?.focus?.(); }
    else if (target.dataset.question) {
      const qid = target.dataset.question;
      // app.js switches to the Decisions tab on the hash change; then the question card is brought into view.
      setTimeout(() => document.getElementById(`question-${qid}`)?.scrollIntoView({block: "center"}), 150);
    }
  });

  const visible = () => location.hash === "#blocked" && !document.hidden;
  window.addEventListener("hashchange", () => { if (visible()) { render(); load(); } });
  document.addEventListener("visibilitychange", () => { if (visible()) load(); });
  setInterval(() => { if (visible()) tick(); }, 15000);
  // The nav badge stays current on every tab (cheap GET); the page itself refreshes every 30 s while open.
  setInterval(() => { polls += 1; if (!document.hidden && (visible() || polls % 2 === 0)) load(); }, 30000);
  load();
})();
