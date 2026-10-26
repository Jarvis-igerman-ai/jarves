// JARVES chat view: ONE implementation for the dashboard Chat page (#/chat) and the phone / remote-PC page (/m/).
// Streaming text chat with slash commands, one-line tool results (expandable, redacted by the server), inline approvals
// and a silent state. Plain DOM, no framework, no external request; every piece of text goes in via textContent.
//
// JarvesChat.mount(root, { api, tx, device }) -> { destroy() }
//   api.state(lang) / api.events(after, sig, lang) / api.send(body) / api.answer(id, approved, typed) / api.silent(on)
//   tx(he, en): the text in the page language;  device: true on a paired phone / PC (no speech, no typed codes)
//   phone (optional, the phone app only): { icon(name) -> svg node, start: [nodes], end: [nodes], onEvent(e) }: icon send
//     button, extra composer controls (attach, mic), a thinking row, Copy under each answer and the raw events (history).
//   returns { destroy(), submit(text), local(role, text) }
// Messages are rendered from server events only (the server echoes a sent message), so a second device or the terminal
// chat shows the same conversation. Message ids are reused on a resend: the computer answers once.
"use strict";

(function () {
  const h = (tag, attrs, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat(Infinity)) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
    return e;
  };
  const uid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2, 12));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const WORDS = {
    CHAT_BUSY: ["התשובה הקודמת עדיין נכתבת. המתינו רגע.", "The previous answer is still being written. Wait a moment."],
    UI_ACTION_STALE: ["הרשימה הזאת כבר נשלחה.", "This list was already sent."],
    UI_ACTION_EXPIRED: ["הרשימה פגה. בקשו רשימה חדשה.", "This list has expired. Ask for a new one."],
    UI_ACTION_UNKNOWN: ["הרשימה לא מוכרת (אולי אחרי הפעלה מחדש). בקשו רשימה חדשה.", "This list is not known (maybe after a restart). Ask for a new one."],
    UI_ACTION_INVALID: ["הבחירה לא תקינה.", "That selection is not valid."],
    UI_ACTION_TOO_MANY: ["אפשר לשלוח עד 10 פריטים בבת אחת.", "You can send at most 10 items at once."],
    UI_ACTION_RATE: ["יותר מדי לחיצות. נסו עוד דקה.", "Too many clicks. Try again in a minute."],
    UI_ACTION_FORBIDDEN: ["את זה אפשר לעשות רק בדשבורד של המחשב עצמו.", "This can only be done in the dashboard on the computer itself."],
    CHAT_UNAVAILABLE: ["השיחה לא פועלת כאן. הריצו jarvis web או הפעילו את JARVES.", "Chat is not running here. Run jarvis web or start JARVES."],
    CHAT_FEATURE_OFF: ["השיחה כבויה: jarvis features on web_chat", "Chat is switched off: jarvis features on web_chat"],
    CHAT_TOO_LONG: ["ההודעה ארוכה מדי.", "The message is too long."],
    PAGE_NOT_ALLOWED: ["למכשיר הזה לא ניתן הדף הזה. אפשר לאשר במחשב.", "This device was not given this page. Allow it on the computer."],
    PAGE_PC_ONLY: ["את זה אפשר לעשות רק במחשב עצמו.", "This can only be done on the computer itself."],
    APPROVAL_ALREADY_ANSWERED: ["כבר נענה במקום אחר.", "Already answered somewhere else."],
    APPROVAL_CODE_NEEDED: ["צריך להקליד את הקוד שמוצג.", "Type the code that is shown."],
    TYPED_PC_ONLY: ["הבקשה הזאת דורשת את הקוד במחשב.", "This request needs the code on the computer."],
    PHONE_APPROVALS_OFF: ["אישורים מהטלפון כבויים: עונים במחשב.", "Phone approvals are off: answer on the computer."],
    RATE_LIMITED: ["יותר מדי הודעות. האטו קצת.", "Too many messages. Slow down."],
  };

  // Small, safe markdown for answers: paragraphs, #/##/### headings, -/* and 1. lists, > quotes, ``` code blocks,
  // `code`, **bold**, *italic*, [text](http/https link). Everything is built with DOM calls: HTML in an answer is shown as text.
  const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\s][^*\n]*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/;
  function inline(text) {
    return text.split(INLINE).map((t, i) => {
      if (i % 2 === 0) return t;
      if (t[0] === "`") return h("code", { class: "jc-ic" }, t.slice(1, -1));
      if (t.startsWith("**")) return h("strong", {}, t.slice(2, -2));
      if (t[0] === "*") return h("em", {}, t.slice(1, -1));
      const m = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(t);
      return m ? h("a", { href: m[2], target: "_blank", rel: "noopener noreferrer" }, m[1]) : t;
    });
  }
  function codeBlock(code, lang, tx) {
    const btn = h("button", { type: "button", class: "jc-copy", onclick: async () => {
      try { await navigator.clipboard.writeText(code); btn.textContent = tx("הועתק", "Copied"); } catch (e) { btn.textContent = tx("ההעתקה נכשלה", "Copy failed"); }
      setTimeout(() => { btn.textContent = tx("העתקה", "Copy"); }, 1500);
    } }, tx("העתקה", "Copy"));
    return h("div", { class: "jc-code-block", dir: "ltr" }, h("div", { class: "jc-code-head" }, h("span", {}, lang || "code"), btn), h("pre", {}, h("code", {}, code)));
  }
  function markdown(text, tx) {
    const out = [], lines = String(text).replace(/\r\n?/g, "\n").split("\n");
    let i = 0, para = [];
    const flush = () => { if (para.length) { out.push(h("p", { dir: "auto" }, inline(para.join("\n")))); para = []; } };
    while (i < lines.length) {
      const ln = lines[i];
      const fence = /^\s*```\s*([\w+-]*)\s*$/.exec(ln);
      if (fence) {                                        // an unfinished fence (still streaming) shows what is there so far
        flush(); const body = []; i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++; out.push(codeBlock(body.join("\n"), fence[1], tx)); continue;
      }
      const hd = /^(#{1,3})\s+(.*)$/.exec(ln);
      if (hd) { flush(); out.push(h("div", { class: "jc-h jc-h" + hd[1].length, dir: "auto" }, inline(hd[2]))); i++; continue; }
      if (/^\s*([-*\u2022]|\d+[.)])\s+/.test(ln)) {
        flush(); const ordered = /^\s*\d/.test(ln), items = [];
        while (i < lines.length && /^\s*([-*\u2022]|\d+[.)])\s+/.test(lines[i])) items.push(h("li", { dir: "auto" }, inline(lines[i++].replace(/^\s*([-*\u2022]|\d+[.)])\s+/, ""))));
        out.push(h(ordered ? "ol" : "ul", {}, items)); continue;
      }
      if (/^>\s?/.test(ln)) {
        flush(); const q = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
        out.push(h("blockquote", { dir: "auto" }, inline(q.join("\n")))); continue;
      }
      if (!ln.trim()) { flush(); i++; continue; }
      para.push(ln); i++;
    }
    flush();
    return out;
  }

  function mount(root, opts) {
    const { api, tx, device } = opts;
    const phone = opts.phone || null;
    const lang = () => ((document.documentElement.lang || "en").startsWith("he") ? "he" : "en");
    const word = (code, fallback) => (WORDS[code] ? tx(WORDS[code][0], WORDS[code][1]) : fallback);
    let alive = true, after = 0, sig = "", busy = "", state = null, phoneAp = true;
    const turns = new Map();              // turn id -> the assistant bubble being streamed
    const uidp = "jc" + Math.random().toString(36).slice(2, 7);

    const log = h("div", { class: "jc-log", role: "log", "aria-live": "polite", "aria-relevant": "additions text",
      "aria-label": tx("השיחה", "Conversation"), tabindex: "0",
      "data-empty": tx("שלום, אני JARVES. במה אפשר לעזור? כתבו הודעה או / לפקודות.", "Hi, I am JARVES. How can I help? Type a message, or / for commands.") });
    const jump = h("button", { type: "button", class: "jc-jump", hidden: true, "aria-label": tx("לגלול להודעה האחרונה", "Scroll to the latest message"),
      onclick: () => scroll(true) }, "\u2193");
    const apprBox = h("div", { class: "jc-appr", "aria-live": "assertive" });
    const status = h("div", { class: "jc-status", role: "status" });
    const input = h("textarea", { id: uidp + "in", class: "jc-in", rows: "1", dir: "auto", maxlength: "4000", enterkeyhint: "send",
      placeholder: opts.phone ? tx("כתבו הודעה", "Message JARVES") : tx("כתבו הודעה או / לפקודות", "Type a message, or / for commands"), autocomplete: "off" });
    const sendBtn = phone && phone.icon
      ? h("button", { type: "submit", class: "primary jc-send jc-icon", "aria-label": tx("שליחה", "Send"), title: tx("שליחה", "Send") }, phone.icon("send"))
      : h("button", { type: "submit", class: "primary jc-send" }, tx("שליחה", "Send"));
    const speakBox = device ? null : h("input", { type: "checkbox", id: uidp + "sp" });
    const silentBox = device ? null : h("input", { type: "checkbox", id: uidp + "si" });
    const cmds = h("div", { class: "jc-cmds", role: "group", "aria-label": tx("פקודות", "Commands") },
      ["/new", "/resume", "/undo", "/status", "/help"].map((c) => h("button", { type: "button", class: "jc-cmd", onclick: () => submit(c) }, c)));
    const form = h("form", { class: "jc-form", onsubmit: (ev) => { ev.preventDefault(); submit(input.value); } },
      h("label", { for: uidp + "in", class: "jc-sr" }, tx("הודעה ל-JARVES", "Message to JARVES")),
      ...((phone && phone.start) || []), input, ...((phone && phone.end) || []), sendBtn);
    sendBtn.disabled = true;
    const opts2 = device ? null : h("div", { class: "jc-opts" },
      h("label", { class: "jc-chk" }, silentBox, " ", tx("שקט (בלי קול, בלי צלצולים)", "Silent (no speech, no chimes)")),
      h("label", { class: "jc-chk", id: uidp + "spl" }, speakBox, " ", tx("להקריא את התשובה במחשב (רק כשלא שקט)", "Read the answer aloud on this computer (only when not silent)")));
    const pop = h("div", { class: "jc-pop", role: "listbox", hidden: true, "aria-label": tx("פקודות", "Commands") });
    let popItems = [], popSel = 0;
    const closePop = () => { pop.hidden = true; popItems = []; pop.replaceChildren(); input.removeAttribute("aria-activedescendant"); };
    const pick = (c) => { closePop(); input.value = c + " "; input.focus(); grow(); };
    function paintPop() {
      const v = input.value;
      const all = ((state && state.commands) || []).map((c) => ({ name: c.name, text: lang() === "he" ? c.he : c.en }));
      popItems = /^\/\S*$/.test(v) ? all.filter((c) => c.name.startsWith(v.toLowerCase())) : [];
      if (!popItems.length) { closePop(); return; }
      popSel = Math.min(popSel, popItems.length - 1);
      pop.replaceChildren(...popItems.map((c, i) => h("div", { id: uidp + "o" + i, role: "option", class: "jc-opt" + (i === popSel ? " sel" : ""),
        "aria-selected": i === popSel ? "true" : "false", onmousedown: (ev) => { ev.preventDefault(); pick(c.name); } },
        h("b", { dir: "ltr" }, c.name), h("span", { class: "jc-small", dir: "auto" }, c.text))));
      input.setAttribute("aria-activedescendant", uidp + "o" + popSel);
      pop.hidden = false;
    }
    // phone: a ChatGPT-like empty state (heading, an optional hint from the page, the main commands with the server's own
    // descriptions) and skeleton lines until the first state arrives. Both are client only.
    const empty = phone ? h("div", { class: "jc-empty" }) : null;
    function paintEmpty() {
      if (!empty) return;
      const want = ["/status", "/resume", "/help"], all = (state && state.commands) || [];
      const sugg = want.map((n) => all.find((c) => c.name === n) || { name: n });
      const say = (c) => (lang() === "he" ? c.he : c.en) || "";
      empty.replaceChildren(h("div", { class: "jc-mark", "aria-hidden": "true" }),
        h("h2", { class: "jc-empty-h", dir: "auto" }, tx("במה אפשר לעזור?", "How can I help?")),
        (typeof phone.hint === "function" && phone.hint()) || null,
        h("div", { class: "jc-sugg", role: "group", "aria-label": tx("פקודות", "Commands") }, sugg.map((c) =>
          h("button", { type: "button", class: "jc-sug", onclick: () => submit(c.name) }, h("b", { dir: "ltr" }, c.name),
            say(c) ? h("span", { dir: "auto" }, say(c)) : null))),
        h("p", { class: "jc-empty-tip", dir: "auto" }, tx("הקלידו / לכל הפקודות", "Type / for all commands")));
    }
    root.replaceChildren(h("div", { class: "jchat" + (phone ? " jc-phone" : "") }, h("div", { class: "jc-bar" }, status, opts2), h("div", { class: "jc-wrap" }, log, jump, empty), apprBox, cmds, h("div", { class: "jc-formwrap" }, pop, form)));
    if (phone) { log.append(...[0, 1, 2].map((i) => h("div", { class: "jc-skel jc-skel" + i, "aria-hidden": "true" }))); paintEmpty(); }
    if (silentBox) silentBox.addEventListener("change", async () => {
      try { const r = await api.silent(silentBox.checked); paintSilent(r.silent); } catch (e) { note(e.message); }
    });
    const grow = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight + 2, 180) + "px"; sendBtn.disabled = !input.value.trim(); };
    input.addEventListener("input", () => { grow(); popSel = 0; paintPop(); });
    input.addEventListener("blur", () => setTimeout(closePop, 120));
    // follow the newest text while the reader is at the end (also while a long answer streams into a short log, e.g. with the
    // phone keyboard open); stop following once they scroll up, until they come back down or press the jump button
    let stick = true;
    log.addEventListener("scroll", () => { stick = nearEnd(); jump.hidden = stick; });
    if (window.ResizeObserver) new ResizeObserver(() => { if (stick) log.scrollTop = log.scrollHeight; }).observe(log);
    input.addEventListener("keydown", (ev) => {
      if (!pop.hidden && popItems.length) {
        if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); popSel = (popSel + (ev.key === "ArrowDown" ? 1 : popItems.length - 1)) % popItems.length; paintPop(); return; }
        if (ev.key === "Tab" || (ev.key === "Enter" && !ev.shiftKey && input.value !== popItems[popSel].name)) { ev.preventDefault(); pick(popItems[popSel].name); return; }
        if (ev.key === "Escape") { ev.preventDefault(); closePop(); return; }
      }
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); submit(input.value); }
    });

    // phone: a "thinking" row from the send until the first streamed text, the answer or a notice (client only)
    let pending = false;
    const think = phone ? h("div", { class: "jc-think", role: "status" }, h("span", { class: "jc-dots", "aria-hidden": "true" }, h("i"), h("i"), h("i")),
      h("span", {}, tx("JARVES חושב", "JARVES is thinking"))) : null;
    function paintThink() {
      if (!think) return;
      const on = (pending || !!busy) && turns.size === 0;
      if (on) { if (think.parentNode !== log || log.lastChild !== think) { log.append(think); scroll(); } } else think.remove();
    }
    function addCopy(d, text) {         // phone: a Copy button under a finished answer (the clipboard of this phone only)
      if (!phone || !text || d.nextSibling && d.nextSibling.classList && d.nextSibling.classList.contains("jc-acts")) return;
      const label = tx("העתקה", "Copy");
      const ic = (n) => (phone.icon ? phone.icon(n) : label);
      const b = h("button", { type: "button", class: "jc-act", "aria-label": label, title: label }, ic("copy"));
      b.addEventListener("click", async () => {
        let ok = true; try { await navigator.clipboard.writeText(text); } catch (e) { ok = false; }
        b.setAttribute("aria-label", ok ? tx("הועתק", "Copied") : tx("ההעתקה נכשלה", "Copy failed")); b.classList.toggle("done", ok);
        if (ok && phone.icon) b.replaceChildren(ic("check"));
        setTimeout(() => { b.setAttribute("aria-label", label); b.classList.remove("done"); if (phone.icon) b.replaceChildren(ic("copy")); }, 1500);
      });
      const when = d.dataset.time ? h("span", { class: "jc-time", title: d.title || null }, d.dataset.time) : null;
      if (when) d.classList.add("has-acts");
      d.after(h("div", { class: "jc-acts" }, b, when)); scroll();
    }
    function note(text, cls) { const d = h("div", { class: "jc-msg jc-notice " + (cls || ""), dir: "auto" }, text); log.append(d); scroll(); return d; }
    function nearEnd() { return log.scrollHeight - log.scrollTop - log.clientHeight < 80; }
    function scroll(force) { if (force === true || stick || nearEnd()) { stick = true; log.scrollTop = log.scrollHeight; jump.hidden = true; } else jump.hidden = false; }
    function paintSilent(s) {
      if (!s) return;
      const why = s.quiet_hours ? tx("שעות שקט", "quiet hours") : s.setting ? tx("מתג השקט", "silent switch") : "";
      status.replaceChildren(h("span", { class: "jc-pill " + (s.on ? "on" : "") }, s.on ? tx("שקט: ", "Silent: ") + why : tx("לא שקט", "Not silent")),
        ...(busy ? [h("span", { class: "jc-pill jc-writing" }, tx("כותב", "Writing"), h("span", { class: "jc-dots", "aria-hidden": "true" }, h("i"), h("i"), h("i")))] : []));
      if (silentBox) silentBox.checked = !!s.setting;
      if (speakBox) { speakBox.disabled = !!s.on || !(state && state.can_speak); if (s.on) speakBox.checked = false; }
    }
    function setText(d, text) {
      if (d.classList.contains("jc-assistant")) d.replaceChildren(...markdown(text, tx)); else d.textContent = text;
    }
    // Time of a message: shown small under it (CSS, from data-time); the full date and time on hover. Messages without
    // a time (a resumed older conversation) show none.
    function stamp(d, ts) {
      if (!ts) return;
      try {
        const t = new Date(ts * 1000), now = new Date();
        const hm = t.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
        d.dataset.time = t.toDateString() === now.toDateString() ? hm : t.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + hm;
        d.title = t.toLocaleString();
      } catch (e) { /* no time shown */ }
    }
    function bubble(role, text, turn, ts) {
      const d = h("div", { class: "jc-msg jc-" + role, dir: "auto" });
      setText(d, text); stamp(d, ts);
      if (turn) d.dataset.turn = turn;
      log.append(d);
      if (think && think.parentNode === log) log.append(think);
      scroll(); return d;
    }
    function displayBlock(m) {      // #363: read-only owner text from a tool result; text nodes only, one RTL-safe line each
      const d = h("div", { class: "jc-msg jc-display", dir: "auto" });
      String(m.text || "").split(String.fromCharCode(10)).forEach((ln) => d.append(h("div", { dir: "auto", style: "white-space:pre-wrap" }, ln)));
      log.append(d); scroll(true);
    }
    // #444 (S1): owner-facing UI block from the server (text / checklist). Text nodes only, no HTML parsing; ids are
    // server-assigned. S2: a checklist that names a next step (b.on) gets a send button; the click posts ONLY the block id and
    // the ticked item ids to the computer, which checks them against its own record and asks for approval before any write.
    const blockUis = new Map();
    function setBlockState(id, state) {
      const u = blockUis.get(id); if (!u) return;
      const open = state === "open";
      u.btn.disabled = !open; u.boxes.forEach((x) => { x.disabled = !open; });
      u.msg.textContent = state === "used" ? tx("נשלח. ממתין לאישור שלכם.", "Sent. Waiting for your approval.")
        : state === "expired" ? tx("הרשימה פגה. בקשו רשימה חדשה.", "This list has expired. Ask for a new one.") : "";
    }
    // #446 (S3): candidate tasks from messages, one card at a time. Add / Edit / Skip, then ONE send with the ids, the owner's
    // own edited titles and the skipped ids; the computer checks them against its record and asks for ONE approval. Text nodes only.
    function cardsView(b, m, wrap) {
      const cards = Array.isArray(b.cards) ? b.cards : [], dec = cards.map((c) => (c.skip ? "skip" : "")), edits = {};
      let at = 0, editing = false, locked = false, drawn = false;
      const body = h("div"), status = h("div", { class: "jc-small", role: "status", "aria-live": "polite" });
      const msg = h("div", { class: "jc-small", role: "alert" });
      const title = (i) => edits[cards[i].id] || cards[i].title;
      const btn = (label, fn, cls) => h("button", { type: "button", class: cls || "", disabled: locked, onclick: fn }, label);
      const decide = (v) => { dec[at] = v; at += 1; editing = false; draw(); };
      const send = async () => {
        const ids = (v) => cards.filter((_c, i) => dec[i] === v).map((c) => c.id);
        const sent = { block: b.id, selected: ids("add"), lang: lang() };
        const ed = {}; sent.selected.forEach((id) => { if (edits[id]) ed[id] = edits[id]; });
        if (Object.keys(ed).length) sent.edits = ed;
        if (ids("skip").length) sent.skipped = ids("skip");
        if (!sent.selected.length && !sent.skipped) { msg.textContent = tx("לא נבחר כלום.", "Nothing is chosen."); return; }
        locked = true; msg.textContent = ""; draw();
        try { await api.uiAction(sent); setBlockState(b.id, "used"); }
        catch (e) { locked = false; draw(); msg.textContent = word(e.code, e.message || tx("הבקשה נדחתה.", "The request was refused.")); }
      };
      function draw() {
        body.replaceChildren();
        if (at < cards.length) {
          const c = cards[at], meta = [c.due ? tx("לתאריך ", "due ") + c.due : "", c.person || ""].filter(Boolean).join(" · ");
          const src = c.ref || c.when ? tx("מתוך וואטסאפ", "from WhatsApp") + [c.when, c.ref].filter(Boolean).map((x) => " · " + x).join("") : "";
          body.append(h("div", { class: "jc-small" }, tx(`כרטיס ${at + 1} מתוך ${cards.length} · משימה אפשרית, לא מאושרת`,
            `Card ${at + 1} of ${cards.length} · possible task, not confirmed`)));
          if (editing) {
            const inp = h("input", { type: "text", maxlength: "120", value: title(at), dir: "auto", "aria-label": tx("שם המשימה", "Task title"), class: "jc-card-in",
              onkeydown: (ev) => { if (ev.key === "Escape" || ev.key === "Enter") ev.preventDefault(); if (ev.key === "Escape") { editing = false; draw(); } else if (ev.key === "Enter") save(); } });
            const save = () => { const v = inp.value.replace(/\s+/g, " ").trim(); if (!v) return; edits[c.id] = v.slice(0, 120); decide("add"); };
            body.append(inp, h("div", { class: "jc-row jc-cardbtns" }, btn(tx("שמרו והוסיפו", "Save and add"), save, "primary"),
              btn(tx("ביטול", "Cancel"), () => { editing = false; draw(); })));
            if (drawn) inp.focus();
          } else {
            body.append(...[h("div", { class: "jc-card-t", dir: "auto" }, title(at)), meta ? h("div", { class: "jc-small", dir: "auto" }, meta) : null,
              src ? h("div", { class: "jc-small", dir: "auto" }, src) : null,
              c.skip ? h("div", { class: "jc-small", dir: "auto" }, tx("נראית כמו משימה שכבר קיימת: מסומנת לדילוג.", "Looks like a task that already exists: set to Skip.")) : null,
              dec[at] ? h("div", { class: "jc-small" }, dec[at] === "add" ? tx("נבחר: להוסיף", "Chosen: add") : tx("נבחר: לדלג", "Chosen: skip")) : null,
              h("div", { class: "jc-row jc-cardbtns" }, btn(tx("הוסיפו", "Add"), () => decide("add"), "primary"),
                btn(tx("ערכו", "Edit"), () => { editing = true; draw(); }), btn(tx("דלגו", "Skip"), () => decide("skip")),
                at > 0 ? btn(tx("הקודם", "Back"), () => { at -= 1; draw(); }) : null, dec[at] ? btn(tx("הבא", "Next"), () => { at += 1; draw(); }) : null)].filter(Boolean));
          }
        } else {
          const adds = cards.map((_c, i) => i).filter((i) => dec[i] === "add");
          const fs = h("fieldset", { class: "jc-fs", tabindex: "-1" }, h("legend", { dir: "auto" }, tx(`${adds.length} להוספה, ${dec.filter((x) => x === "skip").length} לדילוג`,
            `${adds.length} to add, ${dec.filter((x) => x === "skip").length} skipped`)));
          adds.forEach((i, n) => fs.append(h("div", { dir: "auto" }, `${n + 1}) ${title(i)}`)));
          body.append(fs, h("div", { class: "jc-small" }, tx("אחרי השליחה תקבלו אישור אחד עם הכותרות המדויקות.", "After you send, you get ONE approval with the exact titles.")),
            h("div", { class: "jc-row jc-cardbtns" }, btn(tx("שלחו את הבחירה", "Send my choices"), send, "primary"), btn(tx("חזרה לכרטיסים", "Back to the cards"), () => { at = 0; draw(); })));
        }
        status.textContent = at < cards.length ? "" : tx("סיימתם את הכרטיסים.", "All cards done.");
        if (drawn && !editing) { const f = at < cards.length ? body.querySelector("button") : body.querySelector("fieldset"); if (f) f.focus(); }
        drawn = true;
      }
      wrap.append(h("b", { dir: "auto" }, b.title || tx("משימות אפשריות", "Possible tasks")), body, status, msg); draw();
      blockUis.set(b.id, { btn: { set disabled(v) { locked = !!v; draw(); } }, boxes: [], msg });
      if (m.state && m.state !== "open") setBlockState(b.id, m.state);
    }
    function blockView(m) {
      const b = m.block;
      if (!b || b.v !== 1 || (b.kind !== "text" && b.kind !== "checklist" && b.kind !== "cards")) return;     // unknown block: dropped, text stays
      const wrap = h("div", { class: "jc-msg jc-block", dir: b.dir === "rtl" || b.dir === "ltr" ? b.dir : "auto", "data-kind": b.kind });
      if (b.kind === "cards") {
        cardsView(b, m, wrap);
      } else if (b.kind === "text") {
        if (b.title) wrap.append(h("b", { dir: "auto" }, b.title));
        String(b.text || "").split(String.fromCharCode(10)).forEach((ln) => wrap.append(h("div", { dir: "auto", style: "white-space:pre-wrap" }, ln)));
      } else {
        const items = Array.isArray(b.items) ? b.items : [];
        const status = h("div", { class: "jc-small", role: "status", "aria-live": "polite" });
        const paint = () => { status.textContent = tx(`${wrap.querySelectorAll("input:checked").length} מתוך ${items.length} סומנו`,
          `${wrap.querySelectorAll("input:checked").length} of ${items.length} marked`); };
        const fs = h("fieldset", { class: "jc-fs" }, h("legend", { dir: "auto" }, b.title || tx("רשימה", "List")));
        items.forEach((it) => {
          const id = uidp + "c" + b.id + it.id;
          const box = h("input", { type: "checkbox", id, onchange: paint });
          box.checked = !!it.checked;
          fs.append(h("label", { class: "jc-chk-row", for: id, dir: "auto" }, box,
            h("span", { dir: "auto" }, it.label, it.detail ? h("small", { class: "jc-small", dir: "auto" }, it.detail) : null)));
        });
        wrap.append(fs, status); paint();
        if (b.on && typeof api.uiAction === "function" && !device) {
          const boxes = Array.from(fs.querySelectorAll("input"));
          const msg = h("div", { class: "jc-small", role: "alert" });
          const btn = h("button", { type: "button", class: "primary", onclick: async () => {
            const selected = items.filter((_it, n) => boxes[n].checked).map((it) => it.id);
            if (!selected.length) { msg.textContent = tx("לא סומן כלום.", "Nothing is ticked."); return; }
            btn.disabled = true; msg.textContent = "";
            try { await api.uiAction({ block: b.id, selected, lang: lang() }); setBlockState(b.id, "used"); }
            catch (e) { setBlockState(b.id, "open"); msg.textContent = word(e.code, e.message || tx("הבקשה נדחתה.", "The request was refused.")); }
          } }, tx("שלחו את הסימון", "Send my selection"));
          wrap.append(h("div", { class: "jc-row" }, btn), msg);
          blockUis.set(b.id, { btn, boxes, msg });
          if (m.state && m.state !== "open") setBlockState(b.id, m.state);
        }
      }
      log.append(wrap); scroll(true);
    }
    function toolLine(m) {
      const safety = !m.ok && m.code === "TOOL_UNTRUSTED_TURN";
      const label = `${m.name} · ${m.ok ? tx("הצליח", "ok") : safety ? tx("דולג (כלל בטיחות)", "skipped (safety rule)") : tx("נכשל", "failed")}${m.code && m.code !== "ok" ? " · " + m.code : ""}`;
      const d = h("details", { class: "jc-tool " + (m.ok || safety ? "ok" : "bad") }, h("summary", { dir: "ltr" }, label),
        h("pre", { dir: "auto", class: "jc-detail" }, m.detail || tx("(אין פרטים)", "(no details)")));
      log.append(d); scroll();
    }
    function noticeText(m) { return lang() === "he" ? m.he : m.en; }
    function renderMessage(m) {
      if (m.role === "tool") return toolLine(m);
      if (m.role === "display") return displayBlock(m);
      if (m.role === "ui_block") return blockView(m);
      if (m.role === "notice") return bubble("notice", noticeText(m));
      const d = bubble(m.role === "user" ? "user" : "assistant", m.text, m.turn, m.ts);
      if (m.role !== "user") addCopy(d, m.text);
      return d;
    }
    function load(s) {
      state = s; log.replaceChildren(); turns.clear(); blockUis.clear();
      (s.messages || []).forEach(renderMessage); scroll(true);
      after = s.cursor || 0; busy = s.busy || ""; phoneAp = s.phone_approvals !== false; paintEmpty();
      paintSilent(s.silent); paintAppr(s.approvals || []); paintThink();
    }
    function onEvent(e) {
      if (phone && typeof phone.onEvent === "function") { try { phone.onEvent(e); } catch (x) { /* the page's own hook */ } }
      if (["delta", "reply", "notice", "error", "reset"].includes(e.type)) pending = false;
      if (e.type === "user") { bubble("user", e.text, e.turn, e.ts); scroll(true); }
      else if (e.type === "delta") {
        let b = turns.get(e.turn);
        if (!b) { b = bubble("assistant", "", e.turn, e.ts); b.classList.add("streaming"); b.setAttribute("aria-busy", "true"); turns.set(e.turn, b); }
        setText(b, e.text); scroll();
      } else if (e.type === "reply") {
        let b = turns.get(e.turn);
        const text = e.text || (e.ok ? "" : word(e.code, tx("משהו השתבש. ראו את היומן.", "Something went wrong. See the log.")));
        if (!b && text) b = bubble("assistant", "", e.turn, e.ts);
        if (b) { setText(b, text); b.classList.remove("streaming"); b.removeAttribute("aria-busy"); if (!e.ok) b.classList.add("err"); else addCopy(b, text); }
        turns.delete(e.turn);
      } else if (e.type === "tool") toolLine(e);
      else if (e.type === "display") displayBlock(e);
      else if (e.type === "ui_block") blockView(e);
      else if (e.type === "ui_state") setBlockState(e.block, e.state);
      else if (e.type === "notice") bubble("notice", noticeText(e));
      else if (e.type === "reset") api.state(lang()).then(load).catch(() => {});
      else if (e.type === "error") note(word(e.code, e.message || e.code), "err");
    }
    function paintAppr(list) {
      apprBox.replaceChildren(...list.map(card));
    }
    function card(a) {
      const typed = h("input", { type: "text", inputmode: "numeric", maxlength: "6", class: "jc-code", autocomplete: "off",
        "aria-label": tx("קוד אישור", "Approval code") });
      const msg = h("div", { class: "jc-small", role: "alert" });
      const answer = async (ok) => {
        try { await api.answer(a.id, ok, ok && a.typed ? typed.value.trim() : ""); }
        catch (e) { msg.textContent = word(e.code, e.message); }
      };
      const title = uidp + "t" + a.id;
      const cantApprove = device && (!phoneAp || a.typed);
      return h("div", { class: "jc-card risk-" + a.risk, role: "group", "aria-labelledby": title },
        h("div", { class: "jc-chead" }, h("b", { id: title }, a.headline),
          h("span", { class: "jc-pill" }, tx("סיכון: ", "Risk: ") + ({ low: tx("נמוך", "low"), medium: tx("בינוני", "medium"), high: tx("גבוה", "high") })[a.risk]),
          h("span", { class: "jc-small" }, tx(`נדחה אוטומטית בעוד ${a.seconds_left} שנ׳`, `auto-deny in ${a.seconds_left} s`))),
        h("div", { dir: "auto", class: "jc-sentence" }, a.sentence),
        a.rows.length ? h("details", { class: "jc-rows" }, h("summary", {}, tx("פרטים", "Details")),
          h("dl", {}, a.rows.flatMap((r) => [h("dt", {}, r.label), h("dd", { dir: "auto", class: r.mono ? "mono" : "" }, r.value)]))) : null,
        a.typed && !device ? h("label", { class: "jc-chk" }, tx(`פעולה מסוכנת: הקלידו ${a.code} כדי לאשר `, `Dangerous: type ${a.code} to approve `), typed) : null,
        a.typed && device ? h("div", { class: "jc-small" }, word("TYPED_PC_ONLY")) : null,
        device && !phoneAp ? h("div", { class: "jc-small" }, word("PHONE_APPROVALS_OFF")) : null,
        h("div", { class: "jc-row" },
          cantApprove ? null : h("button", { type: "button", class: "primary", onclick: () => answer(true) }, tx("אישור", "Approve")),
          device && !phoneAp ? null : h("button", { type: "button", onclick: () => answer(false) }, tx("דחייה", "Deny"))), msg);
    }

    async function submit(text, again) {
      text = String(text || "").trim();
      if (!text) return;
      const body = { text, id: again || uid(), lang: lang(), speak: !!(speakBox && speakBox.checked) };
      sendBtn.disabled = true;
      try {
        for (let i = 0; ; i++) {
          try { await api.send(body); break; }       // the same id on every resend: answered once
          catch (e) { if (e.status === undefined && i < 3) { await sleep(1200 * (i + 1)); continue; } throw e; }
        }
        if (!again || input.value.trim() === text) input.value = "";
        grow(); pending = true; paintThink();
      } catch (e) {
        const n = note(word(e.code, e.message), "err");
        if (phone) {                         // per-message Retry: the same id, so the computer answers once
          n.append(h("div", { class: "jc-row" }, h("span", { class: "jc-small", dir: "auto" }, "“" + (text.length > 60 ? text.slice(0, 60) + "…" : text) + "”"),
            h("button", { type: "button", class: "jc-retry", onclick: () => { n.remove(); submit(text, body.id); } }, tx("לנסות שוב", "Retry"))));
          if (input.value.trim() === text) { input.value = ""; grow(); }
        }
      }
      grow(); if (!phone || !again) input.focus();
    }

    async function loop() {
      while (alive) {
        try {
          const r = await api.events(after, sig, lang());
          if (!alive) return;
          phoneAp = r.phone_approvals !== false;
          if (r.resync) { load(await api.state(lang())); continue; }
          (r.events || []).forEach(onEvent);
          after = r.cursor; busy = r.busy || ""; paintSilent(r.silent); paintThink();
          const ids = r.approvals.map((a) => a.id).join(",");
          if (ids !== sig) paintAppr(r.approvals);            // not while the same cards stay: a typed code is not lost
          sig = ids;
          if ((r.events || []).length) await sleep(250);       // batches a burst of streamed text into one poll
        } catch (e) { if (!alive) return; await sleep(2500); }
      }
    }

    (async () => {
      try {
        const s = await api.state(lang());
        if (!s.available) {
          root.replaceChildren(h("div", { class: "jchat" }, h("p", { class: "jc-notice", dir: "auto" }, word("CHAT_UNAVAILABLE"))));
          return;
        }
        load(s); sig = (s.approvals || []).map((a) => a.id).join(","); loop();
      } catch (e) {
        root.replaceChildren(h("div", { class: "jchat" }, h("p", { class: "jc-notice err", role: "alert", dir: "auto" }, word(e.code, e.message))));
      }
    })();
    function local(role, text) {         // phone voice: the push-to-talk exchange (it is not a chat event), drawn like a message
      const d = bubble(role === "user" ? "user" : "assistant", text); d.classList.add("jc-local");
      if (role !== "user") addCopy(d, text);
      return d;
    }
    return { destroy() { alive = false; }, submit: (t) => submit(t), local };
  }

  window.JarvesChat = { mount, WORDS };
})();
