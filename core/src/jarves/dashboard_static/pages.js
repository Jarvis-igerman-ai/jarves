// JARVES dashboard pages added in SW-V2 WP-H: searchable conversations, full transcript, raw view, logs, tasks,
// costs with web searches, memory / workspace / settings editing. Uses the helpers of app.js (el, api, render, ...)
// and registers its pages in PAGES, which the router in app.js checks first. Text only via textContent.
"use strict";

const PAGES = {};
const utc = (ts) => dt(ts).toISOString().slice(11, 19) + " UTC";
const utcFull = (ts) => dt(ts).toISOString().replace("T", " ").slice(0, 19) + " UTC";
// local time with UTC next to it (the log is written in UTC)
const when = (ts) => el("span", { title: utcFull(ts) }, `${day(ts)} ${clock(ts)}`, el("span", { class: "muted small" }, " · " + utc(ts)));
const params = (o) => Object.entries(o).filter(([, v]) => v !== "" && v !== null && v !== undefined && v !== false)
  .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v === true ? "1" : v)}`).join("&");
const hashQuery = () => Object.fromEntries(new URLSearchParams(location.hash.split("?")[1] || ""));
function input(attrs, value) { const i = el("input", attrs); if (value !== undefined && value !== null) i.value = value; return i; }
function select(options, value, attrs) {
  const s = el("select", attrs || {}, options.map(([v, label]) => el("option", { value: v }, label)));
  s.value = value === undefined || value === null ? "" : String(value);
  return s;
}
function field(label, control) { return el("label", { class: "field" }, el("span", {}, label), control); }
function note(cls) { return el("span", { class: "muted small " + (cls || "") }); }
function diffView(text) {
  if (!text) return el("div", { class: "muted small" }, "No changes.");
  return el("pre", { class: "diff" }, text.split("\n").map((l) =>
    el("div", { class: l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : "" }, l || " ")));
}
function download(href, label) { return el("a", { href, download: "", class: "button-link" }, label); }

// ---------------------------------------------------------------- conversations (search, filter, sort, pages)
const convState = { q: "", from: "", to: "", tool: "", kind: "", issues: false, errors: false, min_cost: "", min_min: "",
  max_min: "", sort: "started", dir: "desc", page: 1, per: 50 };
PAGES.conversations = async function () {
  const s = convState;
  const query = { q: s.q, from: s.from, to: s.to, tool: s.tool, kind: s.kind, issues: s.issues, errors: s.errors,
    min_cost: s.min_cost, min_dur: s.min_min ? Number(s.min_min) * 60 : "", max_dur: s.max_min ? Number(s.max_min) * 60 : "",
    sort: s.sort, dir: s.dir, page: s.page, per: s.per };
  const [d] = await Promise.all([api("/api/sessions?" + params(query)), refreshOverview()]);
  const today = overview && overview.today;
  const tiles = el("div", { class: "tiles" },
    tile("Today", today ? usd(today.total) : "$0", today ? `${today.sessions} conversations · ${today.voice_minutes} voice min` : "no conversations", true),
    tile("Last 7 days", usd((overview.week || []).reduce((a, x) => a + x.total, 0)),
      `${(overview.week || []).reduce((a, x) => a + x.sessions, 0)} conversations`, true),
    tile("Open issues", String(overview.issues.open), overview.issues.high ? `${overview.issues.high} high` : "none high"),
    tile("Matching", String(d.total), `${usd(d.totals.cost)} · ${secs(d.totals.duration_s)}`, true));
  const live = overview.live ? el("a", { class: "card live-banner", href: "#/session/" + encodeURIComponent(overview.live.id) },
    pill("live", "Live now"), el("span", {}, `${overview.live.user_turns} things said by you · ${secs(overview.live.duration_s)} so far`),
    el("span", { class: "spacer" }), el("span", {}, "Open live view →")) : null;
  const set = (k) => (ev) => { s[k] = ev.target.type === "checkbox" ? ev.target.checked : ev.target.value; s.page = 1; };
  const q = input({ type: "search", placeholder: "Search what was said…", dir: "auto", oninput: set("q") }, s.q);
  const go = () => route();
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const filters = el("div", { class: "card filters" },
    field("Text", q),
    field("From", input({ type: "date", onchange: set("from") }, s.from)), field("To", input({ type: "date", onchange: set("to") }, s.to)),
    field("Tool used", select([["", "any"], ...d.tools.map((t) => [t, t])], s.tool, { onchange: set("tool") })),
    field("Kind", select([["", "voice and typed"], ["voice", "voice"], ["typed", "typed"]], s.kind, { onchange: set("kind") })),
    field("Cost at least $", input({ type: "number", step: "0.01", min: "0", class: "narrow", oninput: set("min_cost") }, s.min_cost)),
    field("Length min–max (min)", el("span", { class: "row" }, input({ type: "number", min: "0", class: "narrow", oninput: set("min_min") }, s.min_min),
      input({ type: "number", min: "0", class: "narrow", oninput: set("max_min") }, s.max_min))),
    el("label", { class: "check" }, input({ type: "checkbox", onchange: set("issues"), checked: s.issues }), "has issues"),
    el("label", { class: "check" }, input({ type: "checkbox", onchange: set("errors"), checked: s.errors }), "errors only"),
    field("Sort", el("span", { class: "row" }, select([["started", "time"], ["cost", "cost"], ["duration", "length"], ["turns", "turns"],
      ["issues", "issues"], ["tools", "tools"]], s.sort, { onchange: set("sort") }),
      select([["desc", "↓"], ["asc", "↑"]], s.dir, { onchange: set("dir") }))),
    el("div", { class: "row" }, el("button", { type: "button", class: "primary", onclick: go }, "Apply"),
      el("button", { type: "button", class: "ghost", onclick: () => { Object.assign(s, { q: "", from: "", to: "", tool: "", kind: "", issues: false,
        errors: false, min_cost: "", min_min: "", max_min: "", page: 1 }); go(); } }, "Clear")));
  const rows = d.items.map((x) => el("tr", { class: "click", onclick: () => { location.hash = "#/session/" + encodeURIComponent(x.id); } },
    el("td", {}, day(x.started), el("div", { class: "muted small" }, `${clock(x.started)} · ${utc(x.started)}`)),
    el("td", {}, pill(x.typed ? "plain" : "ok", x.typed ? "typed" : "voice")),
    el("td", {}, x.first_user ? el("span", { dir: "auto" }, x.first_user.length > 70 ? x.first_user.slice(0, 70) + "…" : x.first_user)
      : el("span", { class: "muted" }, "(nothing said)"), x.live ? [" ", pill("live", "live")] : null),
    el("td", { class: "num" }, secs(x.duration_s)),
    el("td", { class: "num" }, `${x.user_turns} / ${x.assistant_turns}`),
    el("td", { class: "num" }, x.tools ? `${x.tools}${x.tool_failures ? ` (${x.tool_failures} failed)` : ""}` : "—"),
    el("td", { class: "num" }, usd(x.cost.total)),
    el("td", {}, x.errors ? pill("high", `${x.errors} err`) : null, x.issues ? pill("medium", `${x.issues}`) : (x.errors ? null : el("span", { class: "muted" }, "—")))))
    .flatMap((tr, i) => {        // SW-V4 G: the text filter goes through the recall index; show where it matched
      const r = d.items[i].recall;
      return r ? [tr, el("tr", { class: "click", onclick: () => { location.hash = "#/session/" + encodeURIComponent(d.items[i].id); } },
        el("td", { colspan: "8", class: "small", dir: "auto", "data-noi18n": "" }, el("span", { class: "muted" }, `${clock(r.ts)} · `),
          typeof recallMarked === "function" ? recallMarked(r.snippet, r.marks) : r.snippet))] : [tr];
    });
  const pager = el("div", { class: "row pager" },
    el("button", { type: "button", disabled: d.page <= 1, onclick: () => { s.page -= 1; go(); } }, "← Newer"),
    el("span", { class: "muted small" }, `page ${d.page} of ${d.pages} · ${d.total} conversations`),
    el("button", { type: "button", disabled: d.page >= d.pages, onclick: () => { s.page += 1; go(); } }, "Older →"),
    el("span", { class: "spacer" }),
    field("Per page", select([["25", "25"], ["50", "50"], ["100", "100"]], s.per, { onchange: (e) => { s.per = Number(e.target.value); s.page = 1; go(); } })));
  render(header("Conversations", "Every conversation, voice and typed: what you said, what JARVES said, tools, timings and cost. Times are local, with UTC."),
    live, tiles, filters,
    el("div", { class: "card" }, d.items.length ? el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "When"), el("th", {}, "Kind"), el("th", {}, "First thing you said"), el("th", { class: "num" }, "Length"),
        el("th", { class: "num" }, "Turns you / him"), el("th", { class: "num" }, "Tools"), el("th", { class: "num" }, "Cost"),
        el("th", {}, "Issues"))), el("tbody", {}, rows))
      : el("div", { class: "empty" }, d.total === 0 && !s.q && !s.tool ? "No conversations recorded yet. They appear here as soon as you talk to JARVES." : "No conversation matches these filters."),
    pager));
  if (overview.live) timer = setInterval(() => route(true), 5000);
};

// ---------------------------------------------------------------- one conversation: full transcript
PAGES.session = async function (id, refresh) {
  const keepScroll = refresh === true && (window.innerHeight + window.scrollY >= document.body.scrollHeight - 40);
  const d = await api("/api/session/" + encodeURIComponent(id));
  const s = d.session;
  const items = [];
  for (const t of d.turns) items.push({ ts: t.ts, kind: "turn", t });
  for (const t of d.tools) items.push({ ts: t.ts, kind: "tool", t });
  for (const t of d.delegations) items.push({ ts: t.ts, kind: "backend", t });
  for (const t of d.tasks || []) items.push({ ts: typeof t.created === "number" ? t.created : Date.parse(t.created) / 1000, kind: "task", t });
  for (const e of d.events) if (!/_BACKEND_DONE$/.test(e.code) && !["LIVE_STARTED", "VOICE_ENDED", "LIVE_ENDED", "CORE_READY",
    "CORE_STOPPED"].includes(e.code)) items.push({ ts: e.ts, kind: "event", t: e });
  items.sort((a, b) => a.ts - b.ts);
  const chat = el("div", { class: "chat" });
  chat.append(el("div", { class: "sys" }, "Started ", when(s.started),
    s.model ? el("span", { class: "chip" }, `${s.model} → ${s.backend_model || "?"}${s.tier ? " · " + s.tier : ""}`) : null));
  const gapShow = Math.max(8, d.thresholds.dead_air_s * 1.6);
  let prev = s.started, lastRole = null, sysRow = null;
  for (const it of items) {
    const gap = it.ts - prev;
    if (gap >= gapShow) {
      const bad = lastRole === "user" || gap >= d.thresholds.stall_s;
      chat.append(el("div", { class: "gap" + (bad ? " bad" : "") }, `${secs(gap)} ${bad ? "silence while waiting (dead air)" : "quiet"}`));
      sysRow = null;
    }
    prev = it.ts;
    if (it.kind === "turn") {
      const t = it.t, text = textOf(t);
      const wait = t.wait_s !== null && t.wait_s !== undefined
        ? el("span", { class: "wait" + (t.wait_s > d.thresholds.dead_air_s ? " bad" : ""), title: "silence from the end of your speech to his first audio" },
          `reply after ${t.wait_s.toFixed(1)} s`) : null;
      chat.append(el("div", { class: "msg " + t.role },
        el("div", { class: "who" }, t.role === "user" ? "You" : "JARVES"),
        el("div", { class: "text" + (text === null ? " private" : ""), dir: "auto" }, text === null ? `(${t.chars} characters, privacy mode)` : text),
        el("div", { class: "meta" }, el("span", { title: utcFull(t.ts) }, `${clock(t.ts)} · ${utc(t.ts)}`), wait)));
      lastRole = t.role; sysRow = null;
      continue;
    }
    if (!sysRow) { sysRow = el("div", { class: "sys" }); chat.append(sysRow); }
    if (it.kind === "tool") {
      const t = it.t;
      const failed = t.ok === 0, pending = t.ok === null && t.done_ts === null;
      sysRow.append(el("details", { class: "chip-d" + (failed ? " fail" : t.duration_s > 5 ? " slow" : "") },
        el("summary", {}, `${t.name} ${failed ? "✗ " + (t.code || "failed") : pending ? "… no result" : "✓ " + (t.code || "")}${t.duration_s !== null ? " · " + secs(t.duration_s) : ""}`),
        el("div", { class: "small" }, `called ${clock(t.ts)}${t.done_ts ? ` · result ${clock(t.done_ts)}` : ""}${t.origin ? " · " + t.origin : ""}`),
        t.args ? el("pre", { class: "args", dir: "auto" }, t.args) : el("div", { class: "muted small" }, "no arguments logged")));
    } else if (it.kind === "backend") {
      const t = it.t;
      const toks = t.input_tokens !== null && t.input_tokens !== undefined;
      sysRow.append(el("details", { class: "chip-d" + (["completed"].includes(t.status) ? (t.duration_s > 10 ? " slow" : "") : " fail") },
        el("summary", {}, `${t.kind === "review" ? "AI review" : t.kind === "task" ? "task backend" : "backend"} ${t.status || ""} · ${secs(t.duration_s)}${t.web_searches ? ` · ${t.web_searches} search${t.web_searches > 1 ? "es" : ""}` : ""}`),
        el("div", { class: "small" }, `${t.model || "backend"}${t.tier ? " (" + t.tier + ")" : ""} · ${clock(t.ts)}`),
        el("div", { class: "small" }, toks ? `${t.input_tokens} in${t.cached_tokens ? ` (${t.cached_tokens} cached)` : ""} / ${t.output_tokens} out tokens` : "tokens not logged (cost estimated)")));
    } else if (it.kind === "task") {
      const t = it.t;
      sysRow.append(el("details", { class: "chip-d" + (t.status === "done" ? "" : " fail") },
        el("summary", {}, `background task ${t.status} · ${secs(t.duration_s)}`),
        el("div", { class: "small", dir: "auto" }, t.description || ""),
        t.result ? el("div", { class: "small", dir: "auto" }, "Result: " + t.result) : null,
        t.error ? el("div", { class: "small" }, "Error: " + t.error) : null));
    } else {
      const e = it.t;
      const c = el("span", { class: "chip" + (e.level === "ERROR" ? " fail" : e.level === "WARNING" ? " slow" : "") },
        e.code.replace(/^LIVE_/, "").toLowerCase().replace(/_/g, " "));
      hoverTip(c, [`${clock(e.ts)} · ${utc(e.ts)} ${e.level} ${e.code}`, e.msg]);
      sysRow.append(c);
    }
  }
  if (s.ended) chat.append(el("div", { class: "sys" }, "Ended ", when(s.ended), s.end_reason ? ` (${s.end_reason})` : ""));
  else if (s.live) chat.append(el("div", { class: "sys" }, pill("live", "live — updates every 2 s")));
  const c = s.cost;
  const reviewMsg = note();
  const review = d.review.done ? pill("ok", "AI review done") : d.review.skipped
    ? el("span", { class: "muted small", title: d.review.skipped }, "AI review: " + d.review.skipped)
    : el("button", { type: "button", onclick: async (ev) => {
      ev.target.disabled = true; reviewMsg.textContent = "Reviewing…";
      try { const r = await api(`/api/session/${encodeURIComponent(s.id)}/review`, { method: "POST", body: "{}" }); reviewMsg.textContent = `${r.found} finding(s)`; setTimeout(route, 700); }
      catch (e) { reviewMsg.textContent = e.message; }
    } }, "AI review now");
  const delMsg = note();          // SW-V4 G: one conversation (and its recall index rows), after the owner's approval
  const del = s.live ? null : el("button", { type: "button", class: "ghost", onclick: async (ev) => {
    if (!window.confirm(tx("למחוק את השיחה הזאת? אי אפשר לבטל.", "Delete this conversation? This cannot be undone."))) return;
    ev.target.disabled = true; delMsg.textContent = tx("ממתין לאישור על המסך…", "Waiting for your approval on the screen…");
    try { await api(`/api/session/${encodeURIComponent(s.id)}/delete`, { method: "POST", body: JSON.stringify({ confirm: true }) }); location.hash = "#/conversations"; }
    catch (e) { delMsg.textContent = e.message; ev.target.disabled = false; }
  } }, tx("מחיקת השיחה", "Delete conversation"));
  render(
    header(s.live ? "Live conversation" : (s.typed ? "Typed conversation" : "Conversation"), `${day(s.started)} ${clock(s.started)} (${utc(s.started)}) · ${secs(s.duration_s)}`,
      el("a", { href: "#/logs?conv=" + encodeURIComponent(s.id) }, "Log lines"),
      el("a", { href: "#/raw/" + encodeURIComponent(s.id) }, "Raw JSON"), del, delMsg,
      el("a", { href: "#/conversations" }, "← all conversations")),
    el("div", { class: "tiles" },
      tile("Cost", usd(c.total), `voice ${usd(c.voice)} + backend ${usd(c.backend, 4)}`, true),
      tile("Voice", `${c.voice_minutes} min`, s.model || (s.typed ? "typed" : "")),
      tile("Backend calls", String(c.backend_responses), c.estimated_tokens ? "some token use estimated" : "exact token use"),
      tile("Web searches", String(c.web_searches || 0), usd(c.web_search_usd || 0, 3)),
      tile("Turns", `${s.user_turns} / ${s.assistant_turns}`, "you / JARVES"),
      tile("Tools", String(s.tools), s.tool_failures ? `${s.tool_failures} failed` : "no failures")),
    el("div", { class: "card" }, el("div", { class: "row" }, el("h2", { style: "margin:0" }, "Issues found"), el("span", { class: "spacer" }), review, reviewMsg),
      d.issues.length ? el("div", { class: "issue-list", style: "margin-top:8px" }, d.issues.map((i) => el("div", { class: "row" }, pill(i.severity, JL.t(i.severity)),
        el("span", { dir: JL.textDir(txh(i, "title")) }, txh(i, "title")), el("span", { class: "muted small" }, i.kind)))) : el("div", { class: "muted small" }, "None."),
      d.issues.length ? el("div", { class: "small", style: "margin-top:8px" }, el("a", { href: "#/issues" }, "Evidence and actions on the Issues page →")) : null),
    el("h2", {}, "Transcript"), el("div", { class: "card" }, chat));
  if (s.live) timer = setInterval(() => route(true), 2000);
  if (keepScroll) window.scrollTo(0, document.body.scrollHeight);
};

PAGES.raw = async function (id) {
  const d = await api("/api/session/" + encodeURIComponent(id) + "/raw");
  const blob = JSON.stringify(d, null, 1);
  render(header("Raw conversation data", id, el("a", { href: "#/session/" + encodeURIComponent(id) }, "← transcript"),
    el("a", { href: "#/logs?conv=" + encodeURIComponent(id) }, "Log lines")),
    el("div", { class: "card" }, el("pre", { class: "file raw", dir: "ltr" }, blob)));
};

// ---------------------------------------------------------------- logs
const logState = { level: "", module: "", code: "", exclude: "", since: "", until: "", q: "", session: "", file: "", noisy: false, follow: false, limit: 300 };
let logFacets = null;
PAGES.logs = async function (_, refresh) {
  const hq = hashQuery();
  const s = logState;
  if (!refresh && hq.conv !== undefined) s.conv = hq.conv;
  if (!refresh && hq.conv === undefined) s.conv = "";
  if (!logFacets) logFacets = await api("/api/logs/facets").catch(() => ({ modules: {}, prefixes: {}, levels: [] }));
  const toIso = (v) => v ? new Date(v).toISOString() : "";
  const query = { level: s.level, module: s.module, code: s.code, exclude: s.exclude, since: toIso(s.since), until: toIso(s.until),
    q: s.q, session: s.session, file: s.file, noisy: s.noisy, limit: s.limit, conv: s.conv };
  const d = await api("/api/logs?" + params(query));
  const set = (k) => (ev) => { s[k] = ev.target.type === "checkbox" ? ev.target.checked : ev.target.value; };
  const go = () => route();
  const codeIn = input({ list: "codeList", placeholder: "LIVE_* , TASK_*, TOOL_RESULT", oninput: set("code") }, s.code);
  const qIn = input({ type: "search", placeholder: "free text", dir: "auto", oninput: set("q") }, s.q);
  for (const i of [codeIn, qIn]) i.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const filters = el("div", { class: "card filters" },
    field("Level at least", select([["", "all"], ...["VERBOSE", "DEBUG", "INFO", "WARNING", "ERROR"].map((l) => [l, l])], s.level, { onchange: set("level") })),
    field("Module", select([["", "all"], ...Object.entries(logFacets.modules || {}).map(([m, n]) => [m, `${m} (${n})`])], s.module, { onchange: set("module") })),
    field("Event code", codeIn),
    el("datalist", { id: "codeList" }, Object.keys(logFacets.prefixes || {}).concat(Object.keys(logFacets.codes || {})).map((c) => el("option", { value: c }))),
    field("Hide codes", input({ placeholder: "e.g. LINK_*", oninput: set("exclude") }, s.exclude)),
    field("From", input({ type: "datetime-local", step: "1", onchange: set("since") }, s.since)),
    field("To", input({ type: "datetime-local", step: "1", onchange: set("until") }, s.until)),
    field("Text", qIn),
    field("Core session", input({ placeholder: "8-char id", class: "narrow", oninput: set("session") }, s.session)),
    field("File", select([["", "all files"], ...(d.files || []).map((f) => [f.name, `${f.name} (${Math.round(f.size / 1024)} KB)`])], s.file, { onchange: set("file") })),
    el("label", { class: "check" }, input({ type: "checkbox", onchange: set("noisy"), checked: s.noisy }), "show noisy codes"),
    el("label", { class: "check" }, input({ type: "checkbox", checked: s.follow, onchange: (e) => { s.follow = e.target.checked; go(); } }), "follow (refresh every 3 s)"),
    el("div", { class: "row" }, el("button", { type: "button", class: "primary", onclick: go }, "Apply"),
      el("button", { type: "button", class: "ghost", onclick: () => { Object.assign(s, { level: "", module: "", code: "", exclude: "", since: "", until: "", q: "", session: "", file: "", noisy: false, conv: "" }); location.hash = "#/logs"; go(); } }, "Clear"),
      download("/export/logs.jsonl?" + params({ ...query, limit: 50000 }), "Download filtered (.jsonl)")));
  const table = el("table", { class: "log" }, el("thead", {}, el("tr", {}, el("th", {}, "Time (local · UTC)"), el("th", {}, "Level"),
    el("th", {}, "Module"), el("th", {}, "Code"), el("th", {}, "Message"))), el("tbody", {}, d.lines.flatMap(logRow)));
  const more = d.more ? el("button", { type: "button", onclick: async (ev) => {
    ev.target.disabled = true;
    const n = await api("/api/logs?" + params({ ...query, before: d.cursor }));
    table.tBodies[0].append(...n.lines.flatMap(logRow));
    d.cursor = n.cursor; ev.target.disabled = !n.more; ev.target.textContent = n.more ? "Load older" : "No more";
  } }, "Load older") : null;
  render(header("Logs", "jarves.log and its rotated files, newest first (read-only). " + (d.privacy ? "Privacy mode: what was said is masked." : "Secrets and keys are always masked.")),
    s.conv ? el("div", { class: "card row", style: "margin-bottom:12px" }, "Showing the log lines of one conversation. ",
      el("a", { href: "#/session/" + encodeURIComponent(s.conv) }, "Open its transcript"), el("span", { class: "spacer" }),
      el("a", { href: "#/logs", onclick: () => { s.conv = ""; } }, "Show all")) : null,
    filters, el("div", { class: "muted small", style: "margin:8px 0" }, `${d.matched} matching line${d.matched === 1 ? "" : "s"}${d.more ? `, showing the newest ${d.lines.length}` : ""}`),
    el("div", { class: "card" }, d.lines.length ? [el("div", { class: "tscroll" }, table), more] : el("div", { class: "empty" }, "No log lines match.")));
  if (s.follow) timer = setInterval(() => route(true), 3000);
};
const LOG_SKIP = new Set(["ts", "ts_utc", "ref", "level", "component", "code", "msg", "cid", "session", "conv"]);
function logRow(l) {
  const extra = Object.entries(l).filter(([k]) => !LOG_SKIP.has(k));
  const summary = extra.slice(0, 4).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ");
  const detail = el("tr", { class: "detail", hidden: true }, el("td", { colspan: "5" },
    el("div", { class: "row small" }, el("span", { class: "muted" }, `${l.ref} · cid ${l.cid || "-"} · session ${l.session || "-"}`),
      l.conv ? el("a", { href: "#/session/" + encodeURIComponent(l.conv), "aria-label": `${tx("פתיחת השיחה", "Open conversation")} ${l.conv} · ${clock(l.ts)}` }, "open conversation →") : null),
    el("pre", { class: "args", dir: "ltr" }, JSON.stringify(Object.fromEntries(Object.entries(l).filter(([k]) => k !== "conv")), null, 1))));
  const row = el("tr", { class: "click lvl-" + String(l.level || "").toLowerCase(), onclick: () => { detail.hidden = !detail.hidden; } },
    el("td", { class: "mono nowrap" }, clock(l.ts), el("div", { class: "muted small" }, `${utc(l.ts)} · ${day(l.ts)}`)),
    el("td", {}, pill(l.level === "ERROR" ? "high" : l.level === "WARNING" ? "medium" : "plain", l.level || "")),
    el("td", { class: "small", "data-noi18n": "" }, l.component || ""),   // a module name, never translated
    // D-14: every conversation link names its conversation and time for screen readers (inside .mono: no catalog pass)
    el("td", { class: "mono small" }, l.code || "", l.conv ? el("div", {}, el("a", { href: "#/session/" + encodeURIComponent(l.conv), onclick: (e) => e.stopPropagation(),
      "aria-label": `${tx("פתיחת השיחה", "Open conversation")} ${l.conv} · ${clock(l.ts)}` }, tx("שיחה", "conversation"))) : null),
    el("td", { dir: "auto" }, l.msg || "", summary ? el("div", { class: "muted small mono", dir: "ltr" }, summary.length > 220 ? summary.slice(0, 220) + "…" : summary) : null));
  return [row, detail];
}

// ---------------------------------------------------------------- background tasks
PAGES.tasks = async function () {
  const d = await api("/api/tasks?limit=200");
  render(header("Background tasks", "Research JARVES ran in the background while you kept talking (from tasks.jsonl)."),
    el("div", { class: "tiles" }, ...Object.entries(d.counts).map(([k, v]) => tile(k, String(v), ""))),
    el("div", { class: "card" }, d.items.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Started"), el("th", {}, "Task"),
      el("th", {}, "Status"), el("th", { class: "num" }, "Took"), el("th", { class: "num" }, "Attempts"), el("th", {}, "Searches"))),
      el("tbody", {}, d.items.flatMap((t) => {
        const ts = typeof t.created === "number" ? t.created : Date.parse(t.created) / 1000;
        const detail = el("tr", { class: "detail", hidden: true }, el("td", { colspan: "6" },
          t.result ? el("div", { dir: "auto" }, el("b", {}, "Result: "), t.result) : null,
          t.error ? el("div", {}, el("b", {}, "Error: "), t.error) : null,
          el("div", { class: "muted small" }, `announced: ${t.announced ? "yes" : "no"} · usage: ${JSON.stringify(t.usage || {})}`)));
        return [el("tr", { class: "click", onclick: () => { detail.hidden = !detail.hidden; } },
          el("td", {}, isNaN(ts) ? "—" : when(ts)), el("td", { dir: "auto" }, t.description || ""),
          el("td", {}, pill(t.status === "done" ? "ok" : t.status === "cancelled" ? "plain" : "high", t.status)),
          el("td", { class: "num" }, secs(t.duration_s)), el("td", { class: "num" }, t.attempts || 1),
          el("td", { class: "small", dir: "auto" }, Array.isArray(t.queries) ? t.queries.join(" · ") : (t.queries || ""))), detail];
      }))) : el("div", { class: "empty" }, "No background tasks yet. Ask JARVES for a longer research and it runs here.")));
};

// ---------------------------------------------------------------- costs (with web searches and backend kinds)
PAGES.costs = async function () {
  const d = await api("/api/costs?days=30");
  const byDay = Object.fromEntries(d.days.map((x) => [x.day, x]));
  const days = [];
  for (let i = 29; i >= 0; i--) {
    const key = new Date(Date.now() - i * 86400000).toLocaleDateString("sv");
    days.push(byDay[key] || { day: key, sessions: 0, voice_minutes: 0, voice: 0, backend: 0, text: 0, total: 0, web_searches: 0 });
  }
  const total = (n) => d.days.filter((x) => (Date.now() - Date.parse(x.day)) / 86400000 < n).reduce((a, x) => a + x.total, 0);
  const max = Math.max(0.0001, ...days.map((x) => x.total));
  const chart = el("div", { class: "chart", role: "img", "aria-label": JL.t("Daily cost, last 30 days (table below)") });
  const labels = el("div", { class: "xlabels" });
  days.forEach((x, i) => {
    const bar = el("div", { class: "bar" });
    for (const [k, color] of [["voice", "var(--series-1)"], ["backend", "var(--series-2)"], ["text", "var(--series-3)"]]) if (x[k] > 0)
      bar.append(el("div", { class: "seg", style: `height:${(x[k] / max) * 100}%;background:${color}` }));
    hoverTip(bar, [x.day, `total ${usd(x.total)}`, `voice ${usd(x.voice)} · ${x.voice_minutes} min`, `backend ${usd(x.backend)}`,
      `typed ${usd(x.text)}`, `${x.web_searches || 0} web searches`, `${x.sessions} conversations`]);
    chart.append(bar);
    labels.append(el("span", {}, i % 5 === 4 || i === days.length - 1 ? x.day.slice(5) : ""));
  });
  const p = d.pricing;
  const kinds = { voice: "spoken lookups", retry: "stall retries", task: "background tasks", text: "typed answers", review: "AI reviews" };
  render(header("Costs", "Spending per day and per conversation, from the exact model, tier, tokens and web searches each request reports."),
    el("div", { class: "tiles" }, tile("Today", usd(byDay[new Date().toLocaleDateString("sv")] ? byDay[new Date().toLocaleDateString("sv")].total : 0), "", true),
      tile("Last 7 days", usd(total(7)), "", true), tile("Last 30 days", usd(total(30)), `${d.sessions.length} conversations`, true),
      tile("Web searches (30 d)", String(d.days.reduce((a, x) => a + (x.web_searches || 0), 0)), `$${p.web_search_per_call * 1000} per 1,000`, true)),
    el("div", { class: "card" }, el("h2", { style: "margin-top:0" }, "Per day"),
      d.days.length ? [chart, labels, el("div", { class: "legend" },
        el("span", {}, el("i", { style: "background:var(--series-1)" }), "Voice (GPT-Live minutes)"),
        el("span", {}, el("i", { style: "background:var(--series-2)" }), "Backend (tokens + web search)"),
        el("span", {}, el("i", { style: "background:var(--series-3)" }), "Typed answers"))] : el("div", { class: "empty" }, "No costs recorded yet.")),
    el("h2", {}, "Backend requests by kind (30 days)"),
    el("div", { class: "card" }, d.backend_by_kind.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Kind"), el("th", { class: "num" }, "Requests"),
      el("th", { class: "num" }, "Web searches"), el("th", { class: "num" }, "Usage not logged"))),
      el("tbody", {}, d.backend_by_kind.map((k) => el("tr", {}, el("td", {}, kinds[k.kind] || k.kind), el("td", { class: "num" }, k.n),
        el("td", { class: "num" }, k.searches), el("td", { class: "num" }, k.estimated ? k.estimated + "*" : "0")))))
      : el("div", { class: "empty" }, "No backend requests yet.")),
    el("h2", {}, "Per day (table)"),
    el("div", { class: "card" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Day"), el("th", { class: "num" }, "Conversations"),
      el("th", { class: "num" }, "Voice min"), el("th", { class: "num" }, "Voice"), el("th", { class: "num" }, "Backend"),
      el("th", { class: "num" }, "Typed"), el("th", { class: "num" }, "Searches"), el("th", { class: "num" }, "Total"))),
      el("tbody", {}, d.days.map((x) => el("tr", {}, el("td", {}, x.day), el("td", { class: "num" }, x.sessions),
        el("td", { class: "num" }, x.voice_minutes), el("td", { class: "num" }, usd(x.voice)), el("td", { class: "num" }, usd(x.backend, 4)),
        el("td", { class: "num" }, usd(x.text, 4)), el("td", { class: "num" }, x.web_searches || 0), el("td", { class: "num" }, el("b", {}, usd(x.total)))))))),
    el("h2", {}, "Per conversation"),
    el("div", { class: "card" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "When"), el("th", {}, "Models"),
      el("th", { class: "num" }, "Voice min"), el("th", { class: "num" }, "Backend calls"), el("th", { class: "num" }, "Searches"),
      el("th", { class: "num" }, "Voice"), el("th", { class: "num" }, "Backend"), el("th", { class: "num" }, "Total"))),
      el("tbody", {}, d.sessions.map((s) => el("tr", { class: "click", onclick: () => { location.hash = "#/session/" + encodeURIComponent(s.id); } },
        el("td", {}, when(s.started)), el("td", { class: "small" }, `${s.kind === "typed" ? "typed" : (s.model || "?")} → ${s.backend_model || "?"}${s.tier ? " (" + s.tier + ")" : ""}`),
        el("td", { class: "num" }, s.voice_minutes), el("td", { class: "num" }, s.backend_responses + (s.estimated_tokens ? "*" : "")),
        el("td", { class: "num" }, s.web_searches || 0), el("td", { class: "num" }, usd(s.voice)), el("td", { class: "num" }, usd(s.backend, 4)),
        el("td", { class: "num" }, el("b", {}, usd(s.total)))))))),
    el("h2", {}, "Prices used"),
    el("div", { class: "card" }, el("div", { class: "kv" },
      ...Object.entries(p.voice_per_minute).flatMap(([m, v]) => [el("div", { class: "k" }, m), el("div", { class: "v" }, `$${v} per voice minute`)]),
      ...Object.entries(p.models).flatMap(([m, v]) => [el("div", { class: "k" }, m), el("div", { class: "v" }, `$${v.input} in / $${v.cached_input} cached / $${v.output} out per 1M tokens`)]),
      el("div", { class: "k" }, "service tiers"), el("div", { class: "v" }, Object.entries(p.tier_multiplier).map(([k, v]) => `${k} ×${v}`).join(", ")),
      el("div", { class: "k" }, "web search"), el("div", { class: "v" }, `$${p.web_search_per_call} per call ($${p.web_search_per_call * 1000} per 1,000)`)),
      el("ul", { class: "muted small" }, d.notes.map((n) => el("li", {}, n))), el("div", { class: "muted small" }, "* token use not logged: estimated")));
};

// ---------------------------------------------------------------- memory (edit, delete, forget, undo)
PAGES.memory = async function () {
  const d = await api("/api/memory");
  const msg = note();
  const act = async (fn, okText) => { try { await fn(); msg.textContent = okText; route(); } catch (e) { msg.textContent = "Not done: " + e.message; } };
  const call = (path, method, body) => api(path, { method, body: JSON.stringify(body || {}) });
  const addIn = input({ placeholder: "Something JARVES should remember about you", dir: "auto", maxlength: "500" });
  const add = el("div", { class: "row" }, addIn, el("button", { type: "button", class: "primary", onclick: () => act(() => call("/api/memory", "POST", { text: addIn.value }), "Saved") }, "Add"));
  const rows = d.items.map((m) => {
    const text = el("td", { dir: "auto" }, m.text);
    const edit = el("button", { type: "button", class: "ghost", onclick: () => {
      const i = input({ dir: "auto", maxlength: "500", class: "wide" }, m.text);
      text.replaceChildren(i, el("div", { class: "row" }, el("button", { type: "button", class: "primary", onclick: () => act(() => call("/api/memory/" + m.id, "PUT", { text: i.value }), "Saved") }, "Save"),
        el("button", { type: "button", class: "ghost", onclick: () => route() }, "Cancel")));
      i.focus();
    } }, "Edit");
    const del = el("button", { type: "button", class: "ghost", onclick: () => act(() => call("/api/memory/" + m.id, "DELETE"), "Deleted (undo is available)") }, "Delete");
    return el("tr", {}, el("td", { class: "muted" }, m.id), text,
      el("td", {}, pill(m.source === "auto" ? "plain" : "ok", m.source === "auto" ? `learned ${Math.round((m.confidence || 0) * 100)}%` : "you asked")),
      el("td", { class: "muted small" }, (m.updated || m.created || "").replace("T", " ").slice(0, 16)), el("td", {}, el("div", { class: "row" }, edit, del)));
  });
  const groups = d.auto_sessions.map((g) => el("div", { class: "row" }, el("span", { class: "mono small" }, g.session), el("span", { class: "muted small" }, `${g.facts} fact(s) learned`),
    el("button", { type: "button", class: "ghost", onclick: () => act(() => call("/api/memory/forget-session", "POST", { session: g.session }), "Forgotten (undo is available)") }, "Forget this conversation's facts")));
  const cur = d.curator || {};
  render(header("Memory", "What JARVES remembers about you: what you asked him to keep, and what he learned from conversations.",
    d.undo ? el("button", { type: "button", onclick: () => act(() => call("/api/memory/undo", "POST"), "Undone") }, "Undo " + d.undo) : null, msg),
    el("div", { class: "card" }, add, el("div", { class: "muted small", style: "margin-top:6px" },
      tx(`למידה משיחות: ${cur["curator.auto"] ? JL.t(String(cur["curator.auto"].value)) : "?"} · בעזרת המודל: ${cur["curator.llm"] ? JL.t(String(cur["curator.llm"].value)) : "?"} · `,
        `Learning from conversations: ${cur["curator.auto"] ? cur["curator.auto"].value : "?"} · with the AI model: ${cur["curator.llm"] ? cur["curator.llm"].value : "?"} · `),
      el("a", { href: "#/features" }, "switch on the Features page"))),
    el("div", { style: "height:12px" }),
    el("div", { class: "card" }, d.items.length ? el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, "Memory"), el("th", {}, "Source"), el("th", {}, "Saved"), el("th", {}, ""))),
      el("tbody", {}, rows)) : el("div", { class: "empty" }, d.error || "Nothing remembered yet.")),
    groups.length ? [el("h2", {}, "Learned automatically, per conversation"), el("div", { class: "card issue-list" }, groups)] : null);
};

// ---------------------------------------------------------------- workspace files (edit, diff, history, undo)
PAGES.workspace = async function (name) {
  const d = await api("/api/workspace");
  name = name || (d.files[0] && d.files[0].name);
  const list = el("div", { class: "card list" }, d.files.map((f) => el("a", { href: "#/workspace/" + encodeURIComponent(f.name), class: f.name === name ? "active" : "" },
    f.name, el("div", { class: "muted small" }, JL.t(f.title) + " · " + (f.generated ? tx("נוצר אוטומטית", "generated") : f.agent_edit ? tx("JARVES רשאי לערוך", "JARVES may edit") : tx("רק אתם", "you only"))))),
    d.notes.length ? [el("div", { class: "muted small", style: "margin:10px 10px 4px" }, "Daily notes"),
      ...d.notes.map((n) => el("a", { href: "#/note/" + n.day }, n.day))] : null);
  const body = el("div", { class: "card" }, el("div", { class: "muted" }, "Loading…"));
  render(header("Workspace", "JARVES's own files: who he is, who you are and how he works. Every save keeps the previous version."), el("div", { class: "grid2" }, list, body));
  if (!name) { body.replaceChildren(el("div", { class: "empty" }, "No files.")); return; }
  const f = await api("/api/workspace/" + encodeURIComponent(name));
  const msg = note();
  const out = el("div");
  const ta = el("textarea", { class: "file", dir: "auto", readonly: !f.writable, "aria-label": tx("תוכן הקובץ ", "File text ") + name }); ta.value = f.text;
  const send = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const base = "/api/workspace/" + encodeURIComponent(name);
  const buttons = f.writable ? [
    el("button", { type: "button", onclick: async () => { try { out.replaceChildren(el("h2", {}, "Changes (not saved yet)"), diffView((await send(base + "/preview", { text: ta.value })).diff)); } catch (e) { msg.textContent = e.message; } } }, "Preview changes"),
    el("button", { type: "button", class: "primary", onclick: async () => {
      try { const r = await api(base, { method: "PUT", body: JSON.stringify({ text: ta.value }) }); msg.textContent = r.changed ? "Saved" : "No change"; if (r.changed) { await PAGES.workspace(name); } }
      catch (e) { msg.textContent = "Not saved: " + e.message; }
    } }, "Save"),
    el("button", { type: "button", disabled: !f.history.length, onclick: async () => { try { await send(base + "/undo"); await PAGES.workspace(name); } catch (e) { msg.textContent = e.message; } } }, "Undo last change")] : [];
  // D-16: one card per version on phones (table.stack), so the date no longer wraps over three lines
  const hist = f.history.length ? el("table", { class: "stack" }, el("tbody", {}, f.history.slice(0, 20).map((h) => el("tr", {},
    el("td", { class: "mono small", "data-label": "Version" }, h.version), el("td", { class: "muted small nowrap", "data-label": "Saved" }, h.saved.replace("T", " ")), el("td", { class: "num small", "data-label": "Size" }, `${h.chars} B`),
    el("td", {}, el("div", { class: "row" }, el("button", { type: "button", class: "ghost", onclick: async () => {
      const v = await api(base + "/version/" + encodeURIComponent(h.version)); out.replaceChildren(el("h2", {}, `Going back to ${h.version} would change:`), diffView(v.diff));
    } }, "Compare"), f.writable ? el("button", { type: "button", class: "ghost", onclick: async () => { try { await send(base + "/restore", { version: h.version }); await PAGES.workspace(name); } catch (e) { msg.textContent = e.message; } } }, "Restore") : null)))))) : el("div", { class: "muted small" }, "No earlier versions yet.");
  body.replaceChildren(el("div", { class: "row" }, el("b", {}, name), el("span", { class: "muted small" }, JL.t(f.title) + tx(" · בשימוש ב: ", " · used in: ") + (f.inject.join(", ") || tx("שום מקום", "nothing"))),
    el("span", { class: "spacer" }), msg, ...buttons), el("div", { style: "height:10px" }), ta, out,
    el("h2", {}, "Earlier versions"), hist);
};
PAGES.note = async function (dayName) {
  const n = await api("/api/workspace-note/" + encodeURIComponent(dayName));
  render(header("Daily note " + n.day, "What JARVES learned that day (written by the memory curator).", el("a", { href: "#/workspace" }, "← workspace")),
    el("div", { class: "card" }, el("pre", { class: "file", dir: "auto" }, n.text)));
};

// ---------------------------------------------------------------- settings (allowlist, validated, backed up)
PAGES.settings = async function () {
  const d = await api("/api/settings");
  const msg = note();
  const changed = {};
  const groups = {};
  for (const e of d.editable) (groups[e.group] = groups[e.group] || []).push(e);
  const control = (e) => {
    const cur = e.file !== null && e.file !== undefined ? e.file : e.running;
    const lab = { "aria-label": txh(e, "label") || e.key };       // K: a screen reader names the control by its row
    const mark = (v) => { changed[e.key] = v; save.disabled = false; };
    if (e.type === "bool") return input({ type: "checkbox", checked: !!cur, ...lab, onchange: (ev) => mark(ev.target.checked) });
    if (e.type === "choice") return select(e.choices.map((c) => [c, c === "" ? tx("(ברירת המחדל של החשבון)", "(account default)") : c]), cur, { ...lab, onchange: (ev) => mark(ev.target.value) });
    const a = { type: e.type === "str" ? "text" : "number", class: "narrow", ...lab, oninput: (ev) => mark(ev.target.value) };
    if (e.type !== "str") { a.step = e.type === "int" ? "1" : "any"; if (e.min !== undefined) a.min = e.min; if (e.max !== undefined) a.max = e.max; }
    return input(a, cur);
  };
  const save = el("button", { type: "button", class: "primary", disabled: true, onclick: async () => {
    try { const r = await api("/api/settings", { method: "PUT", body: JSON.stringify({ values: changed }) });
      msg.textContent = `Saved ${Object.keys(r.changed).length} value(s) to config.toml${r.backup ? " (backup " + r.backup + ")" : ""}. They apply when JARVES starts next.`; save.disabled = true; undo.disabled = false; }
    catch (e) { msg.textContent = "Not saved: " + e.message; }
  } }, "Save changes");
  const undo = el("button", { type: "button", disabled: !d.backups.length, onclick: async () => {
    try { const r = await api("/api/settings/undo", { method: "POST", body: "{}" }); msg.textContent = "Restored " + r.restored; setTimeout(route, 900); }
    catch (e) { msg.textContent = e.message; }
  } }, "Undo last save");
  const cards = Object.entries(groups).map(([g, list]) => el("div", { class: "card", style: "margin-bottom:12px" }, el("b", {}, txh(list[0], "group")),
    el("table", { style: "margin-top:6px" }, el("tbody", {}, list.map((e) => el("tr", {}, el("td", {}, txh(e, "label"), el("div", { class: "muted small mono" }, e.key)),
      el("td", {}, control(e)), el("td", { class: "muted small" }, `${tx("ברירת מחדל", "default")} ${e.default === "" ? '""' : e.default}` + (e.min !== undefined ? ` · ${e.min}–${e.max ?? "∞"}` : "") +
        (e.file !== null && e.file !== undefined && e.file !== e.running ? tx(` · פועל עכשיו: ${e.running} (עד הפעלה מחדש)`, ` · running now: ${e.running} (until restart)`) : ""))))))));
  const rt = d.runtime || {};
  const voice = rt["voice.voice"];
  const rtMsg = note();
  const voiceSel = voice ? select(voice.choices.map((c) => [c, c]), voice.value, { "aria-label": tx("קול", "Voice"), onchange: async (ev) => {
    try { await api("/api/settings/runtime", { method: "POST", body: JSON.stringify({ key: "voice.voice", value: ev.target.value }) }); rtMsg.textContent = "Saved: the next conversation uses this voice."; }
    catch (e) { rtMsg.textContent = e.message; }
  } }) : null;
  const full = el("details", { class: "card" }, el("summary", {}, "All settings in effect (read-only)"),
    el("div", { class: "kv", style: "margin-top:8px" }, Object.entries(d.config).flatMap(([sec, v]) => typeof v === "object" && v !== null
      ? Object.entries(v).flatMap(([k, x]) => [el("div", { class: "k" }, `${sec}.${k}`), el("div", { class: "v" }, typeof x === "object" ? JSON.stringify(x) : String(x))])
      : [el("div", { class: "k" }, sec), el("div", { class: "v" }, String(v))])));
  render(header("Settings", "Change JARVES's non-secret settings. They are checked before saving, the old config.toml is backed up, and most apply when JARVES starts next.", undo, save),
    msg, d.error ? el("div", { class: "card", style: "margin-bottom:12px" }, "config.toml cannot be edited here: " + d.error) : null,
    el("div", { class: "card", style: "margin-bottom:12px" }, el("b", {}, tx("הגדרה וסיור", "Setup and tour")), el("div", { class: "small" },
      tx("להגדיר, לבדוק וללמוד כל נושא, צעד אחר צעד: ", "Set up, test and learn every topic, step by step: "), el("a", { href: "#/setup" }, tx("פתיחת הגדרה וסיור", "Open Setup and tour")))),
    el("div", { class: "card", style: "margin-bottom:12px" }, el("b", {}, "Switches"), el("div", { class: "small" },
      "Turning features on or off (camera, music, web search, background tasks, AI review, learning from conversations, greeting, chimes…) is on the ",
      el("a", { href: "#/features" }, "Features page"), ". They apply at once or at the next conversation.")),
    voiceSel ? el("div", { class: "card", style: "margin-bottom:12px" }, el("div", { class: "row" }, el("b", {}, "Voice"), voiceSel, rtMsg),
      el("div", { class: "muted small" }, "GPT-Live voice; a new voice starts with the next conversation.")) : null,
    cards, full, el("div", { class: "muted small", style: "margin-top:12px" }, tx(`קובץ: ${d.config_file} · תיקיית הנתונים: ${d.home} · מפתחות API נשמרים ב-secrets.env ואף פעם לא מוצגים או נערכים כאן.`, `File: ${d.config_file} · data folder: ${d.home} · API keys live in secrets.env and are never shown or edited here.`)));
};
