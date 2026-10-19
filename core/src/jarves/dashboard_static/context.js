// Conversation context (owner request, Issue #17: "in each conversation I need a way to see his context").
// Page #/context/<session>?tab=blocks|timeline|diff|why|tools|memory&turn=N&q=text&open=<block id>
// Also adds a "Context" button and "why?" links to every conversation page, and "Where this file was used" to the
// workspace file pages. Uses the helpers of app.js / pages.js (el, api, render, header, pill, tile, clock, day,
// diffView, hashQuery). Text goes in via text nodes only, never as HTML.
"use strict";

(function () {
  const TABS = [["blocks", "Blocks"], ["timeline", "Timeline"], ["diff", "Start vs end"], ["why", "Why did he say that?"],
    ["tools", "Tools"], ["memory", "Memory"]];
  const GROUPS = { front: "Voice model (GPT-Live) instructions", backend: "Backend model (delegation) instructions" };
  const KIND = { clock: "clock", workspace: "workspace", thinking: "silent note", task: "task result", progress: "progress",
    confirm: "approval", stall: "stall", note: "note", tool_result: "tool result", block_changed: "changed", end: "end" };
  const tok = (n) => "~" + (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n || 0)) + " tokens";
  const domId = (id) => "cx-" + String(id).replace(/[^\w-]/g, "-");
  const go = (sid, q) => { location.hash = "#/context/" + encodeURIComponent(sid) + "?" + new URLSearchParams(q).toString(); };

  // turn number (1-based position in the transcript) a moment belongs to: how many turns ended before it
  function turnNo(turns, ts) { let n = 0; for (const t of turns) { if (t.ts <= ts) n++; else break; } return n; }
  const atTurn = (n) => n === 0 ? "before the first turn" : `after turn ${n}`;

  function copyBtn(text) {
    const b = el("button", { type: "button", class: "ghost small" }, "Copy");
    b.addEventListener("click", async (ev) => {
      ev.preventDefault();
      try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; }
      catch (e) { b.textContent = "Copy failed"; }
      setTimeout(() => { b.textContent = "Copy"; }, 1500);
    });
    return b;
  }
  function textBox(text, chars, q) {
    if (text === null || text === undefined)
      return el("div", { class: "muted small" }, chars ? `(${chars} characters; text not kept: privacy mode)` : "(empty)");
    return el("div", {}, el("div", { class: "row" }, el("span", { class: "muted small" }, `${text.length} characters`),
      el("span", { class: "spacer" }), copyBtn(text)), el("pre", { dir: "auto" }, marked(text, q)));
  }
  function marked(text, q) {             // search hits highlighted (text nodes + <mark>, never HTML)
    if (!q) return text;
    const out = [], low = text.toLowerCase(), ql = q.toLowerCase();
    let i = 0;
    for (let j = low.indexOf(ql); j >= 0 && out.length < 400; j = low.indexOf(ql, i)) {
      out.push(text.slice(i, j), el("mark", { class: "cx-mark" }, text.slice(j, j + q.length)));
      i = j + q.length;
    }
    out.push(text.slice(i));
    return out;
  }
  const hit = (q, ...parts) => !q || parts.some((p) => p && String(p).toLowerCase().includes(q.toLowerCase()));

  function blockCard(b, maxTok, ctx) {
    const changed = b.changed_at ? el("span", { class: "cx-kind k-block_changed" },
      `v${b.version} · ${atTurn(turnNo(ctx.turns, b.changed_at))}`) : null;
    const det = el("details", { class: "cx-block" + (ctx.q && hit(ctx.q, b.text, b.label) ? " hit" : ""), id: domId(b.id) },
      el("summary", {},
        el("span", { class: "cx-label", title: b.id }, el("b", {}, b.label), " ", el("span", { class: "muted small" }, b.source || ""), " ", changed),
        el("span", { class: "cx-size", title: tok(b.tokens) }, el("span", { style: `width:${Math.max(2, Math.round(100 * (b.tokens || 0) / (maxTok || 1)))}%` })),
        el("span", { class: "cx-num" }, `${b.chars} ch · ${tok(b.tokens)} · #${b.hash}`)),
      el("div", { class: "cx-body" }, textBox(b.text, b.chars, ctx.q)));
    if ((ctx.q && hit(ctx.q, b.text)) || ctx.open === b.id) det.open = true;
    return det;
  }

  function tabBlocks(d, ctx) {
    const blocks = (ctx.view === "end" ? d.end_blocks : d.blocks).filter((b) => hit(ctx.q, b.text, b.label, b.id));
    const maxTok = Math.max(1, ...blocks.map((b) => b.tokens || 0));
    const toggle = el("div", { class: "row small" }, el("span", { class: "muted" }, "Show:"),
      ...[["start", "at the start"], ["end", "at the end (with changes)"]].map(([v, label]) => el("button", {
        type: "button", class: ctx.view === v ? "primary" : "ghost", onclick: () => go(ctx.sid, { ...ctx.query, view: v }) }, label)));
    const groups = Object.entries(GROUPS).map(([g, title]) => {
      const bs = blocks.filter((b) => b.group === g);
      if (!bs.length) return null;
      const sum = bs.reduce((a, b) => a + (b.tokens || 0), 0);
      return el("div", { class: "cx-group" }, el("h2", {}, title, " ", el("span", { class: "muted small" }, `${bs.length} blocks · ${tok(sum)}`)),
        bs.map((b) => blockCard(b, maxTok, ctx)));
    });
    return [toggle, el("div", { style: "height:8px" }), blocks.length ? groups : el("div", { class: "empty" }, "No block matches the search.")];
  }

  function evRow(e, ctx) {
    const text = e.text;
    const extra = [];
    if (e.data && e.data.tool) extra.push(el("div", { class: "muted small" }, `tool ${e.data.tool} · ${e.data.ok === false ? "failed" : "ok"}${e.data.code ? " · " + e.data.code : ""}`,
      e.data.args ? ` · args ${e.data.args}` : ""));
    if (e.kind === "end" && e.data) {
      const d = e.data;
      extra.push(el("div", { class: "small" }, `facts learned ${d.memory_added.length} · forgotten ${d.memory_removed.length} · edited ${d.memory_changed.length}` +
        (d.workspace_front_changed ? " · persona files changed" : "")));
    }
    return el("div", { class: "cx-ev" + (ctx.q && hit(ctx.q, text, e.label) ? " hit" : "") },
      el("span", { class: "cx-when", title: new Date(e.ts * 1000).toISOString() }, clock(e.ts)),
      el("span", { class: "cx-k" }, el("span", { class: "cx-kind k-" + e.kind }, KIND[e.kind] || e.kind)),
      el("div", { class: "cx-what" },
        el("details", { open: !!(ctx.q && hit(ctx.q, text)) }, el("summary", {}, e.label || e.kind, " ",
          el("span", { class: "muted small" }, `${atTurn(turnNo(ctx.turns, e.ts))} · ${e.chars} ch · ${tok(e.tokens)}`)),
          extra, text !== null && text !== undefined ? el("pre", { dir: "auto" }, marked(text, ctx.q))
            : e.chars ? el("div", { class: "muted small" }, "(text not kept: privacy mode)") : null)));
  }

  function tabTimeline(d, ctx) {
    const items = d.events.map((e) => ({ ts: e.ts, e }));
    ctx.turns.forEach((t, i) => items.push({ ts: t.ts, t, n: i + 1 }));
    items.sort((a, b) => a.ts - b.ts);
    const changes = d.events.filter((e) => ["workspace", "block_changed", "task", "end"].includes(e.kind));
    const summary = el("div", { class: "card" }, el("b", {}, "Changed since the start"),
      changes.length ? el("ul", {}, changes.map((e) => el("li", {}, `${atTurn(turnNo(ctx.turns, e.ts))} (${clock(e.ts)}): ${e.label}`)))
        : el("div", { class: "muted small" }, "Nothing: his instructions stayed as they were at the start; only the notes below were added."));
    const rows = items.filter((x) => x.t ? hit(ctx.q, x.t.text) : hit(ctx.q, x.e.text, x.e.label)).map((x) => x.t
      ? el("div", { class: "cx-ev turn" }, el("span", { class: "cx-when" }, clock(x.ts)),
        el("span", { class: "cx-k" }, el("b", {}, `turn ${x.n}`)),
        el("div", { class: "cx-what", dir: "auto" }, `${x.t.role === "user" ? "You" : "JARVES"}: `,
          textOf(x.t) === null ? `(${x.t.chars} characters, privacy mode)` : marked(x.t.text, ctx.q),
          x.t.role === "assistant" ? el("a", { class: "cx-why-link", href: "#/context/" + encodeURIComponent(ctx.sid) + "?tab=why&turn=" + x.n }, "why?") : null))
      : evRow(x.e, ctx));
    return [summary, el("div", { style: "height:10px" }), el("div", { class: "card" },
      el("div", { class: "muted small" }, `Started ${clock(d.started)} with ${tok(d.start_tokens)} of instructions; ${d.events.filter((e) => e.kind !== "end").length} pieces of context were added during the conversation (${tok(d.total_tokens - d.start_tokens)}).`),
      el("div", { class: "cx-tl" }, rows.length ? rows : el("div", { class: "empty" }, "Nothing matches the search.")))];
  }

  function factList(facts) {
    return facts.length ? el("ul", {}, facts.map((f) => el("li", { dir: "auto" }, `#${f.id} `, f.text === null ? `(${f.chars} characters, privacy mode)` : f.text,
      el("span", { class: "muted small" }, ` · ${f.source || "?"}${f.confidence !== undefined && f.confidence !== null ? " · " + f.confidence : ""}`)))) : el("div", { class: "muted small" }, "none");
  }
  function tabDiff(d) {
    const df = d.diff;
    const blocks = df.blocks.length ? df.blocks.map((b) => el("div", { class: "card", style: "margin-bottom:10px" },
      el("div", { class: "row" }, el("b", {}, b.label || b.id), pill(b.change === "added" ? "low" : "medium", b.change),
        b.at ? el("span", { class: "muted small" }, `at ${clock(b.at)}`) : null, el("span", { class: "spacer" }),
        el("span", { class: "muted small" }, `${b.chars[0]} → ${b.chars[1]} characters`)),
      b.lines ? diffView(b.lines.join("\n")) : el("div", { class: "muted small" }, b.change === "added" ? "Added during the conversation (see Blocks: at the end)." : "Text not kept (privacy mode): only the hash changed.")))
      : el("div", { class: "card muted" }, "No instruction block changed during this conversation.");
    return [blocks, el("div", { class: "card" }, el("h2", {}, "Memory and persona during the conversation"),
      el("div", { class: "muted small" }, df.applies_next),
      el("h3", {}, "Facts learned"), factList(df.memory_added || []),
      el("h3", {}, "Facts forgotten"), factList(df.memory_removed || []),
      el("h3", {}, "Facts edited"), factList(df.memory_changed || []),
      el("div", { class: "small", style: "margin-top:8px" }, df.workspace_front_changed === null || df.workspace_front_changed === undefined ? "Persona files: not checked (conversation not finished)."
        : df.workspace_front_changed ? "Persona files (SOUL / IDENTITY / USER) changed during the conversation." : "Persona files did not change."))];
  }

  function words(s) { return new Set(String(s || "").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((w) => w.length >= 3)); }
  function tabWhy(d, ctx) {
    const turns = ctx.turns;
    const answers = turns.map((t, i) => ({ t, n: i + 1 })).filter((x) => x.t.role === "assistant");
    if (!answers.length) return el("div", { class: "card empty" }, "JARVES said nothing in this conversation yet.");
    let pick = answers.find((x) => x.n >= Number(ctx.query.turn || 0)) || answers[answers.length - 1];
    if (!ctx.query.turn) pick = answers[answers.length - 1];
    const sel = el("select", { onchange: (ev) => go(ctx.sid, { ...ctx.query, tab: "why", turn: ev.target.value }) },
      answers.map((x) => el("option", { value: String(x.n) }, `turn ${x.n} · ${clock(x.t.ts)} · ${(x.t.text || `(${x.t.chars} characters)`).slice(0, 60)}`)));
    sel.value = String(pick.n);
    const t = pick.t;
    const prevUser = [...turns.slice(0, pick.n - 1)].reverse().find((x) => x.role === "user");
    const from = prevUser ? prevUser.ts - 1 : d.started;
    const notes = d.events.filter((e) => e.ts >= from && e.ts <= t.ts + 1 && !["clock", "end"].includes(e.kind));
    const clockNote = [...d.events].reverse().find((e) => e.kind === "clock" && e.ts <= t.ts);
    const standing = d.events.filter((e) => e.ts < from && ["workspace", "task", "block_changed"].includes(e.kind));
    const tools = (ctx.sess.tools || []).filter((x) => x.ts >= from && x.ts <= t.ts + 1);
    const said = words(t.text), asked = words(prevUser && prevUser.text);
    const mem = (d.memory || []).map((m) => {
      const w = words(m.text); let s = 0;
      for (const x of w) if (said.has(x) || asked.has(x)) s++;
      return { m, s };
    }).filter((x) => x.s >= 2 || (x.s >= 1 && words(x.m.text).size <= 3)).sort((a, b) => b.s - a.s);
    const blocksAt = d.end_blocks.map((b) => b.changed_at && b.changed_at > t.ts ? (d.blocks.find((x) => x.id === b.id) || null) : b).filter(Boolean);
    const link = (b) => el("a", { href: "#/context/" + encodeURIComponent(ctx.sid) + "?tab=blocks&open=" + encodeURIComponent(b.id) + (b.changed_at && b.changed_at <= t.ts ? "&view=end" : "") }, b.label);
    return el("div", { class: "cx-why" },
      el("div", { class: "card cx-col" }, el("div", { class: "row" }, el("b", {}, "Answer"), sel),
        prevUser ? el("p", { dir: "auto" }, el("b", {}, "You: "), prevUser.text === null ? `(${prevUser.chars} characters, privacy mode)` : prevUser.text) : null,
        el("p", { dir: "auto" }, el("b", {}, "JARVES: "), t.text === null ? `(${t.chars} characters, privacy mode)` : t.text)),
      el("div", { class: "card cx-col" }, el("h2", {}, "What reached him for this answer"),
        notes.length ? el("div", { class: "cx-tl" }, notes.map((e) => evRow(e, ctx))) : el("div", { class: "muted small" }, "No tool result or note: he answered from his instructions and the conversation so far."),
        tools.length ? [el("h3", {}, "Tools called (transcript)"), el("ul", {}, tools.map((x) => el("li", {}, `${x.name} ${x.ok === 0 ? "✗ " + (x.code || "failed") : x.ok === 1 ? "✓" : "…"} `,
          el("span", { class: "muted small" }, x.args || ""))))] : null),
      el("div", { class: "card cx-col" }, el("h2", {}, "Possibly from memory"),
        mem.length ? el("ul", {}, mem.slice(0, 8).map((x) => el("li", { dir: "auto" }, `#${x.m.id} ${x.m.text}`, el("span", { class: "muted small" }, ` · ${x.m.source} · in ${x.m.targets.join(", ")} · ${x.s} shared words`))))
          : el("div", { class: "muted small" }, (d.memory || []).length ? "No remembered fact shares words with this exchange." : "No remembered facts were in his instructions.")),
      el("div", { class: "card cx-col" }, el("h2", {}, "Standing context at that moment"),
        el("ul", {}, blocksAt.map((b) => el("li", {}, link(b), el("span", { class: "muted small" }, ` · ${GROUPS[b.group] ? b.group : ""} · ${tok(b.tokens)}${b.changed_at && b.changed_at <= t.ts ? " · changed " + atTurn(turnNo(turns, b.changed_at)) : ""}`)))),
        clockNote ? el("div", { class: "small" }, "Latest clock note: ", el("span", { dir: "auto" }, clockNote.text || `(${clockNote.chars} characters)`)) : null,
        standing.length ? [el("h3", {}, "Earlier notes still in the conversation"), el("div", { class: "cx-tl" }, standing.map((e) => evRow(e, ctx)))] : null),
      el("div", { class: "muted small" }, "A guide, not a proof: the model decides by itself. These are the inputs it had at that moment."));
  }

  function tabTools(d, ctx) {
    const rows = (d.tools || []).filter((x) => hit(ctx.q, x.name, x.skill, x.why_not));
    rows.sort((a, b) => (b.offered - a.offered) || a.name.localeCompare(b.name));
    return el("div", { class: "card" }, el("div", { class: "muted small" }, `${rows.filter((x) => x.offered).length} tools offered to the backend, ${rows.filter((x) => !x.offered).length} left out.`),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Tool"), el("th", {}, "Skill"), el("th", {}, "Permission"), el("th", {}, "Offered"), el("th", {}, "Why not / policy"))),
        el("tbody", {}, rows.map((x) => el("tr", {}, el("td", { class: "mono small" }, x.name), el("td", { class: "small" }, x.skill), el("td", { class: "small" }, x.permission || "—"),
          el("td", {}, x.offered ? pill("ok", "yes") : pill("plain", "no")), el("td", { class: "small" }, x.why_not || x.policy || ""))))));
  }

  function tabMemory(d, ctx) {
    const rows = (d.memory || []).filter((m) => hit(ctx.q, m.text, m.source));
    return el("div", { class: "card" }, el("div", { class: "muted small" }, "Remembered facts that were really in his instructions at the start (front = the voice model, backend = the delegated model)."),
      rows.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, "Fact"), el("th", {}, "Source"), el("th", { class: "num" }, "Confidence"), el("th", {}, "In"), el("th", {}, "Learned in"))),
        el("tbody", {}, rows.map((m) => el("tr", {}, el("td", { class: "mono small" }, String(m.id)),
          el("td", { dir: "auto" }, m.text === null ? `(${m.chars} characters, privacy mode)` : marked(m.text, ctx.q)),
          el("td", { class: "small" }, m.source || ""), el("td", { class: "num small" }, m.confidence === null || m.confidence === undefined ? "" : String(m.confidence)),
          el("td", { class: "small" }, (m.targets || []).join(", ")),
          el("td", { class: "small" }, m.learned_in ? el("a", { href: "#/session/" + encodeURIComponent(m.learned_in) }, "conversation") : "you asked")))))
        : el("div", { class: "empty" }, "No remembered fact was in his instructions."));
  }

  async function exportJson(sid, btn) {
    try {
      const d = await api("/api/session/" + encodeURIComponent(sid) + "/context/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(d, null, 1)], { type: "application/json" }));
      const a = el("a", { href: url, download: `jarves-context-${sid}.json` });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      btn.textContent = "Exported (redacted)";
    } catch (e) { btn.textContent = "Export failed: " + e.message; }
  }

  async function pageContext(sid) {
    const query = hashQuery();
    const [d, sess] = await Promise.all([api("/api/session/" + encodeURIComponent(sid) + "/context"), api("/api/session/" + encodeURIComponent(sid))]);
    const back = el("a", { href: "#/session/" + encodeURIComponent(sid) }, "← transcript");
    if (!d.captured) {
      render(header("Context", sid, back), el("div", { class: "card empty" }, d.why));
      return;
    }
    const turns = (sess.turns || []).slice().sort((a, b) => a.ts - b.ts || a.id - b.id);
    const ctx = { sid, d, sess, turns, query, q: query.q || "", tab: query.tab || "blocks", view: query.view || "start", open: query.open || "" };
    const m = d.meta || {};
    const exp = el("button", { type: "button" }, "Export (redacted JSON)");
    exp.addEventListener("click", () => exportJson(sid, exp));
    const search = el("input", { type: "search", placeholder: "Search in his context…", dir: "auto" });
    search.value = ctx.q;
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") go(sid, { ...query, q: search.value }); });
    const tabs = el("div", { class: "cx-tabs", role: "tablist" }, TABS.map(([k, label]) => el("button", { type: "button", role: "tab", class: k === ctx.tab ? "on" : "",
      "aria-selected": k === ctx.tab ? "true" : "false", onclick: () => go(sid, { ...query, tab: k }) }, label)));
    const body = { blocks: tabBlocks, timeline: tabTimeline, diff: tabDiff, why: tabWhy, tools: tabTools, memory: tabMemory }[ctx.tab] || tabBlocks;
    const model = d.kind === "typed" ? `typed chat · ${m.backend_model || "?"}` : `${m.model || "?"} (${m.voice || "?"}) → ${m.backend_model || "?"}`;
    render(
      header("His context", `${day(d.started)} ${clock(d.started)} · ${d.kind === "typed" ? "typed conversation" : "voice conversation"}`, exp, back),
      d.private ? el("div", { class: "card small" }, pill("medium", "privacy mode"), " Captured with privacy on: names, sizes and hashes only, no text.") : null,
      el("div", { class: "tiles" },
        tile("At the start", tok(d.start_tokens), `${d.blocks.length} blocks · estimate`),
        tile("At the end", tok(d.total_tokens), `${d.events.filter((e) => e.kind !== "end").length} pieces added`),
        tile("Models", model, [m.reasoning && m.reasoning.effort ? "effort " + m.reasoning.effort : null, m.service_tier ? "tier " + m.service_tier : null].filter(Boolean).join(" · ")),
        tile("Tools offered", String((d.tools || []).filter((x) => x.offered).length), `${(d.tools || []).filter((x) => !x.offered).length} left out`),
        tile("Facts in prompt", String((d.memory || []).length), "remembered facts")),
      tabs, el("div", { class: "cx-search" }, search, el("button", { type: "button", onclick: () => go(sid, { ...query, q: search.value }) }, "Search"),
        ctx.q ? el("button", { type: "button", class: "ghost", onclick: () => go(sid, { ...query, q: "" }) }, "Clear") : null),
      body(d, ctx), el("div", { class: "muted small", style: "margin-top:10px" }, d.token_note));
    if (ctx.open) {
      const node = document.getElementById(domId(ctx.open));
      if (node) { node.open = true; node.classList.add("flash"); node.scrollIntoView({ block: "center" }); }
    }
  }
  PAGES.context = pageContext;

  // ---------------------------------------------------------------- hooks on other pages (no edits there)
  const sessionPage = PAGES.session;
  if (sessionPage) PAGES.session = async function (id, refresh) {
    await sessionPage(id, refresh);
    const head = $main.querySelector(".row");
    if (head && !head.querySelector(".cx-open")) {
      head.insertBefore(el("a", { class: "cx-open button-link", href: "#/context/" + encodeURIComponent(id) }, "Context"), head.querySelector("a"));
    }
    $main.querySelectorAll(".chat .msg").forEach((m, i) => {
      if (m.classList.contains("assistant") && !m.querySelector(".cx-why-link")) {
        const meta = m.querySelector(".meta");
        if (meta) meta.append(el("a", { class: "cx-why-link", href: "#/context/" + encodeURIComponent(id) + "?tab=why&turn=" + (i + 1) }, "why?"));
      }
    });
  };
  const workspacePage = PAGES.workspace;
  if (workspacePage) PAGES.workspace = async function (name) {
    await workspacePage(name);
    const file = name || (($main.querySelector(".list a.active") || {}).textContent || "").match(/^[A-Z]+\.md/)?.[0];
    if (!file) return;
    let d;
    try { d = await api("/api/context/usage?file=" + encodeURIComponent(file)); } catch (e) { return; }
    let prev = null;
    const rows = d.conversations.slice().reverse().map((r) => {
      const h = r.used.map((u) => u.hash).join("/");
      const changed = prev !== null && h !== prev; prev = h;
      return { r, changed };
    }).reverse();
    $main.append(el("div", { class: "card", style: "margin-top:12px" }, el("h2", {}, "Where this file was used"),
      rows.length ? el("table", {}, el("tbody", {}, rows.map(({ r, changed }) => el("tr", {},
        el("td", {}, r.session_id ? el("a", { href: "#/context/" + encodeURIComponent(r.session_id) + "?tab=blocks" }, `${day(r.started)} ${clock(r.started)}`) : `${day(r.started)} ${clock(r.started)}`),
        el("td", { class: "small" }, r.kind), el("td", { class: "small" }, r.used.map((u) => `${u.group} ${u.chars} ch`).join(", ")),
        el("td", { class: "mono small" }, r.used.map((u) => "#" + u.hash).join(" ")),
        el("td", {}, changed ? pill("medium", "new version") : null)))))
        : el("div", { class: "muted small" }, "Not seen in any recorded conversation yet (or the file is empty / not injected).")));
  };
})();
