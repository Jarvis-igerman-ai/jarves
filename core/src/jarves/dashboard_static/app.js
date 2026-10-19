// JARVES dashboard (no framework, no external requests). All text goes in via textContent, never innerHTML.
"use strict";

const $main = document.getElementById("main");
let timer = null;           // live refresh of the current page
let overview = null;

// ---------------------------------------------------------------- helpers
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;        // CSSOM: allowed by the strict CSP (no inline style attr)
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false)
    e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts,
    headers: { "Content-Type": "application/json", ...(opts.method && opts.method !== "GET" ? { "X-Jarves": "1" } : {}) } });
  const data = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
const usd = (v, d = 3) => v === null || v === undefined ? "—" : "$" + Number(v).toFixed(v >= 1 ? 2 : d);
const secs = (s) => s === null || s === undefined ? "—" : s < 60 ? `${s.toFixed(s < 10 ? 1 : 0)} s`
  : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
const dt = (ts) => new Date(ts * 1000);
const clock = (ts) => dt(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const day = (ts) => dt(ts).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
const est = () => el("span", { class: "est", title: "Estimate from [pricing]; your OpenAI bill is the truth" }, "estimate");
const pill = (cls, text) => el("span", { class: "pill " + cls }, text);
const textOf = (t) => t.text !== null && t.text !== undefined ? t.text : null;
const header = (title, sub, ...right) => el("div", { class: "row" },
  el("div", {}, el("h1", {}, title), sub ? el("div", { class: "sub" }, sub) : null), el("div", { class: "spacer" }), ...right);
function render(...nodes) { $main.replaceChildren(...nodes.flat(2).filter((n) => n !== null && n !== undefined && n !== false)); }
function fail(e) { render(el("div", { class: "card empty" }, "Could not load: " + e.message)); }

const tip = document.getElementById("tip");
function hoverTip(node, lines) {
  node.addEventListener("mousemove", (ev) => {
    tip.replaceChildren(...lines.map((l) => el("div", {}, l)));
    tip.hidden = false;
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = (ev.clientY + 14) + "px";
  });
  node.addEventListener("mouseleave", () => { tip.hidden = true; });
}

// ---------------------------------------------------------------- theme
const THEMES = ["auto", "light", "dark"];
function applyTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  document.getElementById("themeBtn").textContent = "Theme: " + t;
}
let theme = "auto";
try { theme = localStorage.getItem("jarves-theme") || "auto"; } catch (e) { /* storage blocked */ }
applyTheme(theme);
document.getElementById("themeBtn").addEventListener("click", () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  try { localStorage.setItem("jarves-theme", theme); } catch (e) { /* ignore */ }
  applyTheme(theme);
});

// ---------------------------------------------------------------- overview (side bar)
async function refreshOverview() {
  try {
    overview = await api("/api/overview");
    const dot = document.getElementById("coreDot");
    dot.className = "dot " + (overview.live ? "live" : overview.core.state === "ready" ? "ok"
      : overview.core.state === "degraded" ? "bad" : "");
    dot.title = overview.live ? "conversation in progress" : "Core: " + (overview.core.state || "unknown");
    document.getElementById("issueCount").textContent = overview.issues.open || "";
    document.getElementById("footInfo").textContent =
      `v${overview.version} · ${overview.sessions} conversations` + (overview.privacy ? " · privacy on" : "");
  } catch (e) { /* page shows its own error */ }
}

// ---------------------------------------------------------------- conversations
async function pageConversations() {
  const [list] = await Promise.all([api("/api/sessions?limit=200"), refreshOverview()]);
  const today = overview && overview.today;
  const tiles = el("div", { class: "tiles" },
    tile("Today", today ? usd(today.total) : "$0", today ? `${today.sessions} conversations · ${today.voice_minutes} voice min` : "no conversations", true),
    tile("Last 7 days", usd((overview.week || []).reduce((a, d) => a + d.total, 0)),
      `${(overview.week || []).reduce((a, d) => a + d.sessions, 0)} conversations`, true),
    tile("Open issues", String(overview.issues.open), overview.issues.high ? `${overview.issues.high} high` : "none high"));
  const live = overview.live ? el("a", { class: "card live-banner", href: "#/session/" + encodeURIComponent(overview.live.id) },
    pill("live", "Live now"), el("span", {}, `${overview.live.user_turns} things said by you · ${secs(overview.live.duration_s)} so far`),
    el("span", { class: "spacer" }), el("span", {}, "Open live view →")) : null;
  const rows = list.map((s) => el("tr", { class: "click", onclick: () => { location.hash = "#/session/" + encodeURIComponent(s.id); } },
    el("td", {}, day(s.started), el("div", { class: "muted small" }, clock(s.started))),
    el("td", {}, s.first_user ? el("span", { dir: "auto" }, s.first_user.length > 70 ? s.first_user.slice(0, 70) + "…" : s.first_user)
      : el("span", { class: "muted" }, "(nothing said)"), s.live ? [" ", pill("live", "live")] : null),
    el("td", { class: "num" }, secs(s.duration_s)),
    el("td", { class: "num" }, `${s.user_turns} / ${s.assistant_turns}`),
    el("td", { class: "num" }, s.tools ? `${s.tools}${s.tool_failures ? ` (${s.tool_failures} failed)` : ""}` : "—"),
    el("td", { class: "num" }, usd(s.cost.total)),
    el("td", {}, s.issues ? pill("medium", `${s.issues}`) : el("span", { class: "muted" }, "—"))));
  render(header("Conversations", "Every live voice conversation: what you said, what JARVES said, tools, timings and cost."),
    live, tiles,
    el("div", { class: "card" }, list.length ? el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "When"), el("th", {}, "First thing you said"), el("th", { class: "num" }, "Length"),
        el("th", { class: "num" }, "Turns you / him"), el("th", { class: "num" }, "Tools"), el("th", { class: "num" }, "Cost"),
        el("th", {}, "Issues"))), el("tbody", {}, rows))
      : el("div", { class: "empty" }, "No conversations recorded yet. They appear here as soon as you talk to JARVES.")));
  if (overview.live) timer = setInterval(() => route(true), 5000);
}
function tile(label, value, hint, estimate) {
  return el("div", { class: "card tile" }, el("div", { class: "label" }, label, estimate ? est() : null),
    el("div", { class: "value" }, value), el("div", { class: "hint" }, hint));
}

async function pageSession(id, keepScroll) {
  const d = await api("/api/session/" + encodeURIComponent(id));
  const s = d.session;
  const items = [];
  for (const t of d.turns) items.push({ ts: t.ts, kind: "turn", t });
  for (const t of d.tools) items.push({ ts: t.ts, kind: "tool", t });
  for (const t of d.delegations) items.push({ ts: t.ts, kind: "backend", t });
  for (const e of d.events) if (!["LIVE_BACKEND_DONE", "LIVE_STARTED", "VOICE_ENDED", "LIVE_ENDED", "CORE_READY",
    "CORE_STOPPED"].includes(e.code)) items.push({ ts: e.ts, kind: "event", t: e });
  items.sort((a, b) => a.ts - b.ts);
  const chat = el("div", { class: "chat" });
  chat.append(el("div", { class: "sys" }, `Started ${day(s.started)} ${clock(s.started)}`,
    s.model ? el("span", { class: "chip" }, `${s.model} → ${s.backend_model || "?"}${s.tier ? " · " + s.tier : ""}`) : null));
  const gapShow = Math.max(8, d.thresholds.dead_air_s * 1.6);
  let prev = s.started;
  let lastRole = null;
  let sysRow = null;
  for (const it of items) {
    const gap = it.ts - prev;
    if (gap >= gapShow) {
      const bad = lastRole === "user" || gap >= d.thresholds.stall_s;
      chat.append(el("div", { class: "gap" + (bad ? " bad" : "") }, `${secs(gap)} ${bad ? "silence while waiting" : "quiet"}`));
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
        el("div", { class: "meta" }, clock(t.ts), wait)));
      lastRole = t.role;
      sysRow = null;
      continue;
    }
    if (!sysRow) { sysRow = el("div", { class: "sys" }); chat.append(sysRow); }
    if (it.kind === "tool") {
      const t = it.t;
      const failed = t.ok === 0, pending = t.ok === null && t.done_ts === null;
      const c = el("span", { class: "chip" + (failed ? " fail" : t.duration_s > 5 ? " slow" : "") },
        `${t.name} ${failed ? "✗ " + (t.code || "failed") : pending ? "…" : "✓"}${t.duration_s !== null ? " · " + secs(t.duration_s) : ""}`);
      hoverTip(c, [`tool ${t.name}`, `called ${clock(t.ts)}`, t.done_ts ? `result ${clock(t.done_ts)} (${t.code || ""})` : "no result logged",
        t.args ? "args: " + t.args : ""].filter(Boolean));
      sysRow.append(c);
    } else if (it.kind === "backend") {
      const t = it.t;
      const c = el("span", { class: "chip" + (t.status !== "completed" ? " fail" : t.duration_s > 10 ? " slow" : "") },
        `backend ${t.status || ""} · ${secs(t.duration_s)}`);
      hoverTip(c, [`${t.model || "backend"}${t.tier ? " (" + t.tier + ")" : ""}`, `finished ${clock(t.ts)} after ${secs(t.duration_s)}`,
        t.input_tokens !== null ? `${t.input_tokens} in / ${t.output_tokens} out tokens` : "tokens not logged (cost estimated)"]);
      sysRow.append(c);
    } else {
      const e = it.t;
      const c = el("span", { class: "chip" + (e.level === "ERROR" ? " fail" : e.level === "WARNING" ? " slow" : "") },
        e.code.replace(/^LIVE_/, "").toLowerCase().replace(/_/g, " "));
      hoverTip(c, [`${clock(e.ts)} ${e.level} ${e.code}`, e.msg]);
      sysRow.append(c);
    }
  }
  if (s.ended) chat.append(el("div", { class: "sys" }, `Ended ${clock(s.ended)}${s.end_reason ? " (" + s.end_reason + ")" : ""}`));
  else if (s.live) chat.append(el("div", { class: "sys" }, pill("live", "live — updates every 2 s")));
  const c = s.cost;
  render(
    header(s.live ? "Live conversation" : "Conversation", `${day(s.started)} ${clock(s.started)} · ${secs(s.duration_s)}`,
      el("a", { href: "#/conversations" }, "← all conversations")),
    el("div", { class: "tiles" },
      tile("Cost", usd(c.total), `voice ${usd(c.voice)} + backend ${usd(c.backend)}`, true),
      tile("Voice", `${c.voice_minutes} min`, s.model || ""),
      tile("Backend calls", String(c.backend_responses), c.estimated_tokens ? "token use estimated" : "token use logged"),
      tile("Turns", `${s.user_turns} / ${s.assistant_turns}`, "you / JARVES"),
      tile("Tools", String(s.tools), s.tool_failures ? `${s.tool_failures} failed` : "no failures")),
    d.issues.length ? el("div", { class: "card" }, el("h2", {}, "Issues found in this conversation"),
      el("div", { class: "issue-list" }, d.issues.map((i) => el("div", { class: "row" }, pill(i.severity, i.severity),
        el("span", { dir: "ltr" }, i.title), el("span", { class: "muted small" }, i.kind)))),
      el("div", { class: "small", style: "margin-top:8px" }, el("a", { href: "#/issues" }, "Evidence and actions on the Issues page →"))) : null,
    el("h2", {}, "Transcript"), el("div", { class: "card" }, chat));
  if (s.live) timer = setInterval(() => route(true), 2000);
  if (keepScroll === true) window.scrollTo(0, document.body.scrollHeight);
}

// ---------------------------------------------------------------- costs
async function pageCosts() {
  const d = await api("/api/costs?days=30");
  const byDay = Object.fromEntries(d.days.map((x) => [x.day, x]));
  const days = [];                                  // every one of the last 30 days, zero when nothing happened
  for (let i = 29; i >= 0; i--) {
    const key = new Date(Date.now() - i * 86400000).toLocaleDateString("sv");
    days.push(byDay[key] || { day: key, sessions: 0, voice_minutes: 0, voice: 0, backend: 0, text: 0, total: 0 });
  }
  const total = (n) => d.days.slice(0, n).reduce((a, x) => a + x.total, 0);
  const max = Math.max(0.0001, ...days.map((x) => x.total));
  const chart = el("div", { class: "chart", role: "img", "aria-label": "Daily estimated cost, last 30 days (table below)" });
  const labels = el("div", { class: "xlabels" });
  for (const x of days) {
    const bar = el("div", { class: "bar" });
    const segs = [["voice", "var(--series-1)"], ["backend", "var(--series-2)"], ["text", "var(--series-3)"]];
    for (const [k, color] of segs) if (x[k] > 0)
      bar.append(el("div", { class: "seg", style: `height:${(x[k] / max) * 100}%;background:${color}` }));
    hoverTip(bar, [x.day, `total ${usd(x.total)} (estimate)`, `voice ${usd(x.voice)} · ${x.voice_minutes} min`,
      `backend ${usd(x.backend)}`, `typed ${usd(x.text)}`, `${x.sessions} conversations`]);
    chart.append(bar);
    labels.append(el("span", {}, days.indexOf(x) % 5 === 4 || days.indexOf(x) === days.length - 1 ? x.day.slice(5) : ""));
  }
  const legend = el("div", { class: "legend" },
    el("span", {}, el("i", { style: "background:var(--series-1)" }), "Voice (GPT-Live minutes)"),
    el("span", {}, el("i", { style: "background:var(--series-2)" }), "Backend (tokens + search)"),
    el("span", {}, el("i", { style: "background:var(--series-3)" }), "Typed answers"));
  const prices = d.pricing;
  render(header("Costs", "Estimated spending per day and per conversation."),
    el("div", { class: "tiles" }, tile("Today", usd(d.days[0] && d.days[0].day === new Date().toLocaleDateString("sv") ? d.days[0].total : 0), "", true),
      tile("Last 7 days", usd(total(7)), "", true), tile("Last 30 days", usd(total(30)), `${d.sessions.length} conversations`, true)),
    el("div", { class: "card" }, el("h2", { style: "margin-top:0" }, "Per day"),
      d.days.length ? [chart, labels, legend] : el("div", { class: "empty" }, "No costs recorded yet.")),
    el("h2", {}, "Per day (table)"),
    el("div", { class: "card" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Day"), el("th", { class: "num" }, "Conversations"),
      el("th", { class: "num" }, "Voice min"), el("th", { class: "num" }, "Voice"), el("th", { class: "num" }, "Backend"),
      el("th", { class: "num" }, "Typed"), el("th", { class: "num" }, "Total"))),
      el("tbody", {}, d.days.map((x) => el("tr", {}, el("td", {}, x.day), el("td", { class: "num" }, x.sessions),
        el("td", { class: "num" }, x.voice_minutes), el("td", { class: "num" }, usd(x.voice)), el("td", { class: "num" }, usd(x.backend)),
        el("td", { class: "num" }, usd(x.text)), el("td", { class: "num" }, el("b", {}, usd(x.total)))))))),
    el("h2", {}, "Per conversation"),
    el("div", { class: "card" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "When"), el("th", {}, "Models"),
      el("th", { class: "num" }, "Voice min"), el("th", { class: "num" }, "Backend calls"), el("th", { class: "num" }, "Voice"),
      el("th", { class: "num" }, "Backend"), el("th", { class: "num" }, "Total"))),
      el("tbody", {}, d.sessions.map((s) => el("tr", { class: "click", onclick: () => { location.hash = "#/session/" + encodeURIComponent(s.id); } },
        el("td", {}, `${day(s.started)} ${clock(s.started)}`), el("td", { class: "small" }, `${s.model || "?"} → ${s.backend_model || "?"}${s.tier ? " (" + s.tier + ")" : ""}`),
        el("td", { class: "num" }, s.voice_minutes), el("td", { class: "num" }, s.backend_responses + (s.estimated_tokens ? "*" : "")),
        el("td", { class: "num" }, usd(s.voice)), el("td", { class: "num" }, usd(s.backend, 4)), el("td", { class: "num" }, el("b", {}, usd(s.total)))))))),
    el("h2", {}, "Prices used"),
    el("div", { class: "card" },
      el("div", { class: "kv" },
        ...Object.entries(prices.voice_per_minute).flatMap(([m, p]) => [el("div", { class: "k" }, m), el("div", { class: "v" }, `$${p} per voice minute`)]),
        ...Object.entries(prices.models).flatMap(([m, p]) => [el("div", { class: "k" }, m),
          el("div", { class: "v" }, `$${p.input} in / $${p.cached_input} cached / $${p.output} out per 1M tokens`)]),
        el("div", { class: "k" }, "service tiers"), el("div", { class: "v" }, Object.entries(prices.tier_multiplier).map(([k, v]) => `${k} ×${v}`).join(", ")),
        el("div", { class: "k" }, "web search"), el("div", { class: "v" }, `$${prices.web_search_per_call} per call`)),
      el("ul", { class: "muted small" }, d.notes.map((n) => el("li", {}, n))), el("div", { class: "muted small" }, "* token use estimated")));
}

// ---------------------------------------------------------------- issues
function issueRow(i) {
  const ev = (() => { try { return JSON.parse(i.evidence || "{}"); } catch (e) { return {}; } })();
  const evNodes = [];
  for (const [k, v] of Object.entries(ev)) {
    if (k === "session" || v === null || (Array.isArray(v) && !v.length)) continue;
    const val = Array.isArray(v) ? v.map((x) => el("div", { dir: "auto" }, String(x)))
      : typeof v === "object" ? el("div", {}, JSON.stringify(v)) : el("span", { dir: "auto" }, String(v));
    evNodes.push(el("div", {}, el("span", { class: "k" }, k.replace(/_/g, " ") + ": "), val));
  }
  const actions = el("div", { class: "row" });
  if (i.status === "open") {
    actions.append(el("button", { type: "button", onclick: () => setIssue(i.id, "resolved") }, "Resolved"),
      el("button", { type: "button", class: "ghost", onclick: () => setIssue(i.id, "ignored") }, "Ignore"));
  } else actions.append(el("button", { type: "button", onclick: () => setIssue(i.id, "open") }, "Reopen"));
  return el("div", { class: "issue card" },
    pill(i.severity, i.severity),
    el("div", {}, el("div", { class: "title", dir: "ltr" }, i.title),
      el("div", { class: "muted small" }, `${i.kind} · seen in ${i.count} conversation${i.count > 1 ? "s" : ""} · last ${day(i.last_seen)} ${clock(i.last_seen)}`,
        ev.session && !String(ev.session).startsWith("day:") ? [" · ", el("a", { href: "#/session/" + encodeURIComponent(ev.session) }, "open conversation")] : null)),
    actions,
    i.detail ? el("div", { class: "detail", dir: "ltr" }, i.detail) : null,
    evNodes.length ? el("div", { class: "evidence" }, evNodes) : null);
}
async function setIssue(id, status) {
  await api("/api/issues/" + id, { method: "POST", body: JSON.stringify({ status }) });
  route();
}
async function pageIssues(status) {
  status = status || "open";
  const list = await api("/api/issues?status=" + status);
  const tabs = el("div", { class: "row" }, ["open", "resolved", "ignored", "all"].map((s) =>
    el("a", { href: "#/issues/" + s, class: "pill plain" + (s === status ? " ok" : "") }, s)));
  const run = el("button", { type: "button", class: "primary", onclick: async () => {
    run.disabled = true; run.textContent = "Checking…";
    const r = await api("/api/diagnose", { method: "POST", body: "{}" });
    run.textContent = `Done: ${r.found} findings`; setTimeout(route, 600);
  } }, "Run self-diagnosis now");
  render(header("Issues", "Problems JARVES found in his own conversations (rules run after every conversation).", run),
    tabs, el("div", { style: "height:12px" }),
    list.length ? list.map(issueRow) : el("div", { class: "card empty" }, status === "open" ? "No open issues." : "Nothing here."));
  refreshOverview();
}

// ---------------------------------------------------------------- memory, skills, workspace, settings
async function pageMemory() {
  const d = await api("/api/memory");
  render(header("Memory", "What JARVES remembers about you (only what you asked him to keep)." +
      (d.editable ? "" : " Editing from here arrives with the memory update; for now say \"forget …\" or run `jarvis memory delete <id>`.")),
    el("div", { class: "card" }, d.items.length ? el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, "Memory"), el("th", {}, "Saved"))),
      el("tbody", {}, d.items.map((m) => el("tr", {}, el("td", { class: "muted" }, m.id),
        el("td", { dir: "auto" }, m.text), el("td", { class: "muted small" }, (m.updated || m.created || "").replace("T", " ").slice(0, 16))))))
      : el("div", { class: "empty" }, d.error || "Nothing remembered yet.")));
}
async function pageSkills() {
  const d = await api("/api/skills");
  const cards = d.skills.map((s) => el("div", { class: "card", style: "margin-bottom:12px" },
    el("div", { class: "row" }, el("b", {}, s.skill), s.health ? pill(s.health, s.health) : null,
      el("span", { class: "muted small", dir: "auto" }, s.detail)),
    s.action ? el("div", { class: "muted small" }, "Try: " + s.action) : null,
    el("table", { style: "margin-top:8px" }, el("thead", {}, el("tr", {}, el("th", {}, "Tool"), el("th", { class: "num" }, "Calls (30 d)"),
      el("th", { class: "num" }, "Failed"), el("th", { class: "num" }, "Avg time"), el("th", {}, "Last used"))),
      el("tbody", {}, s.tools.map((t) => el("tr", {}, el("td", {}, el("code", {}, t.name)), el("td", { class: "num" }, t.n || 0),
        el("td", { class: "num" }, t.fails ? el("span", { class: "pill fail" }, t.fails) : "0"),
        el("td", { class: "num" }, t.avg_s ? secs(t.avg_s) : "—"), el("td", { class: "muted small" }, t.last ? `${day(t.last)} ${clock(t.last)}` : "never")))))));
  const comps = Object.entries(d.components || {}).map(([k, c]) => el("tr", {}, el("td", {}, k), el("td", {}, pill(c.status, c.status)),
    el("td", { class: "small", dir: "auto" }, c.detail), el("td", { class: "small muted" }, c.action)));
  render(header("Skills & tools", "What JARVES can do, how often each tool was used and whether it works."),
    cards.length ? cards : el("div", { class: "card empty" }, "No skills recorded yet (they are listed when the Core starts)."),
    el("h2", {}, "Component health" + (d.snapshot_age_s !== null && d.snapshot_age_s !== undefined ? ` (snapshot ${secs(d.snapshot_age_s)} old, Core ${d.core || "?"})` : "")),
    el("div", { class: "card" }, comps.length ? el("table", {}, el("tbody", {}, comps)) : el("div", { class: "empty" }, "No health snapshot yet.")));
}
async function pageWorkspace(name) {
  const d = await api("/api/workspace");
  name = name || (d.files[0] && d.files[0].name);
  const list = el("div", { class: "card list" }, d.files.map((f) => el("a", { href: "#/workspace/" + encodeURIComponent(f.name),
    class: f.name === name ? "active" : "" }, f.name, f.exists === false ? el("span", { class: "muted small" }, " (not created)") : null)));
  const body = el("div", { class: "card" }, el("div", { class: "muted" }, "Loading…"));
  render(header("Workspace", "JARVES's own files: who he is, who you are and how he works. " +
      (d.writable ? "Edit and save here." : `Read-only (${d.provider}).`)), el("div", { class: "grid2" }, list, body));
  if (!name) { body.replaceChildren(el("div", { class: "empty" }, "No files.")); return; }
  const f = await api("/api/workspace/" + encodeURIComponent(name));
  if (f.writable) {
    const ta = el("textarea", { class: "file", dir: "auto" }); ta.value = f.text;
    const msg = el("span", { class: "muted small" });
    body.replaceChildren(el("div", { class: "row" }, el("b", {}, name), el("span", { class: "spacer" }), msg,
      el("button", { type: "button", class: "primary", onclick: async () => {
        try { await api("/api/workspace/" + encodeURIComponent(name), { method: "PUT", body: JSON.stringify({ text: ta.value }) }); msg.textContent = "Saved"; }
        catch (e) { msg.textContent = "Not saved: " + e.message; }
      } }, "Save")), el("div", { style: "height:10px" }), ta);
  } else body.replaceChildren(el("b", {}, name), el("div", { style: "height:10px" }),
    f.text ? el("pre", { class: "file", dir: "auto" }, f.text) : el("div", { class: "empty" }, "This file does not exist yet."));
}
async function pageSettings() {
  const d = await api("/api/settings");
  const sections = Object.entries(d.config).map(([sec, v]) => {
    const rows = typeof v === "object" && v !== null
      ? Object.entries(v).flatMap(([k, x]) => [el("div", { class: "k" }, k), el("div", { class: "v" }, typeof x === "object" ? JSON.stringify(x) : String(x))])
      : [el("div", { class: "k" }, sec), el("div", { class: "v" }, String(v))];
    return el("div", { class: "card", style: "margin-bottom:12px" }, el("b", {}, typeof v === "object" ? `[${sec}]` : "general"),
      el("div", { class: "kv", style: "margin-top:8px" }, rows));
  });
  render(header("Settings", `Read-only view of the settings in effect. ${d.hint}`), el("div", { class: "muted small", style: "margin-bottom:12px" }, `Data folder: ${d.home}`), sections);
}

// ---------------------------------------------------------------- router
async function route(refresh) {
  clearInterval(timer); timer = null;
  const [page, arg] = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  for (const a of document.querySelectorAll("nav a"))
    a.classList.toggle("active", a.dataset.page === (page === "session" ? "conversations" : page || "conversations"));
  try {
    if (page === "session") await pageSession(arg, refresh === true && (window.innerHeight + window.scrollY >= document.body.scrollHeight - 40));
    else if (page === "costs") await pageCosts();
    else if (page === "issues") await pageIssues(arg);
    else if (page === "memory") await pageMemory();
    else if (page === "skills") await pageSkills();
    else if (page === "workspace") await pageWorkspace(arg);
    else if (page === "settings") await pageSettings();
    else await pageConversations();
  } catch (e) { if (refresh !== true) fail(e); }
}
window.addEventListener("hashchange", () => route());
refreshOverview().then(() => route());
setInterval(refreshOverview, 15000);
