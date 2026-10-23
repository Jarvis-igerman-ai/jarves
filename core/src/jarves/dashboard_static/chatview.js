// JARVES chat view: ONE implementation for the dashboard Chat page (#/chat) and the phone / remote-PC page (/m/).
// Streaming text chat with slash commands, one-line tool results (expandable, redacted by the server), inline approvals
// and a silent state. Plain DOM, no framework, no external request; every piece of text goes in via textContent.
//
// JarvesChat.mount(root, { api, tx, device }) -> { destroy() }
//   api.state(lang) / api.events(after, sig, lang) / api.send(body) / api.answer(id, approved, typed) / api.silent(on)
//   tx(he, en): the text in the page language;  device: true on a paired phone / PC (no speech, no typed codes)
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

  function mount(root, opts) {
    const { api, tx, device } = opts;
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
      placeholder: tx("כתבו הודעה או / לפקודות", "Type a message, or / for commands"), autocomplete: "off" });
    const sendBtn = h("button", { type: "submit", class: "primary jc-send" }, tx("שליחה", "Send"));
    const speakBox = device ? null : h("input", { type: "checkbox", id: uidp + "sp" });
    const silentBox = device ? null : h("input", { type: "checkbox", id: uidp + "si" });
    const cmds = h("div", { class: "jc-cmds", role: "group", "aria-label": tx("פקודות", "Commands") },
      ["/new", "/resume", "/undo", "/status", "/help"].map((c) => h("button", { type: "button", class: "jc-cmd", onclick: () => submit(c) }, c)));
    const form = h("form", { class: "jc-form", onsubmit: (ev) => { ev.preventDefault(); submit(input.value); } },
      h("label", { for: uidp + "in", class: "jc-sr" }, tx("הודעה ל-JARVES", "Message to JARVES")), input, sendBtn);
    sendBtn.disabled = true;
    const opts2 = device ? null : h("div", { class: "jc-opts" },
      h("label", { class: "jc-chk" }, silentBox, " ", tx("שקט (בלי קול, בלי צלצולים)", "Silent (no speech, no chimes)")),
      h("label", { class: "jc-chk", id: uidp + "spl" }, speakBox, " ", tx("להקריא את התשובה במחשב (רק כשלא שקט)", "Read the answer aloud on this computer (only when not silent)")));
    root.replaceChildren(h("div", { class: "jchat" }, h("div", { class: "jc-bar" }, status, opts2), h("div", { class: "jc-wrap" }, log, jump), apprBox, cmds, form));
    if (silentBox) silentBox.addEventListener("change", async () => {
      try { const r = await api.silent(silentBox.checked); paintSilent(r.silent); } catch (e) { note(e.message); }
    });
    const grow = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight + 2, 180) + "px"; sendBtn.disabled = !input.value.trim(); };
    input.addEventListener("input", grow);
    log.addEventListener("scroll", () => { jump.hidden = nearEnd(); });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); submit(input.value); }
    });

    function note(text, cls) { const d = h("div", { class: "jc-msg jc-notice " + (cls || ""), dir: "auto" }, text); log.append(d); scroll(); return d; }
    function nearEnd() { return log.scrollHeight - log.scrollTop - log.clientHeight < 80; }
    function scroll(force) { if (force === true || nearEnd()) { log.scrollTop = log.scrollHeight; jump.hidden = true; } else jump.hidden = false; }
    function paintSilent(s) {
      if (!s) return;
      const why = s.quiet_hours ? tx("שעות שקט", "quiet hours") : s.setting ? tx("מתג השקט", "silent switch") : "";
      status.replaceChildren(h("span", { class: "jc-pill " + (s.on ? "on" : "") }, s.on ? tx("שקט: ", "Silent: ") + why : tx("לא שקט", "Not silent")),
        ...(busy ? [h("span", { class: "jc-pill jc-writing" }, tx("כותב", "Writing"), h("span", { class: "jc-dots", "aria-hidden": "true" }, h("i"), h("i"), h("i")))] : []));
      if (silentBox) silentBox.checked = !!s.setting;
      if (speakBox) { speakBox.disabled = !!s.on || !(state && state.can_speak); if (s.on) speakBox.checked = false; }
    }
    function bubble(role, text, turn) {
      const d = h("div", { class: "jc-msg jc-" + role, dir: "auto" }, text);
      if (turn) d.dataset.turn = turn;
      log.append(d); scroll(); return d;
    }
    function toolLine(m) {
      const label = `${m.name} · ${m.ok ? tx("הצליח", "ok") : tx("נכשל", "failed")}${m.code && m.code !== "ok" ? " · " + m.code : ""}`;
      const d = h("details", { class: "jc-tool " + (m.ok ? "ok" : "bad") }, h("summary", { dir: "ltr" }, label),
        h("pre", { dir: "auto", class: "jc-detail" }, m.detail || tx("(אין פרטים)", "(no details)")));
      log.append(d); scroll();
    }
    function noticeText(m) { return lang() === "he" ? m.he : m.en; }
    function renderMessage(m) {
      if (m.role === "tool") return toolLine(m);
      if (m.role === "notice") return bubble("notice", noticeText(m));
      return bubble(m.role === "user" ? "user" : "assistant", m.text, m.turn);
    }
    function load(s) {
      state = s; log.replaceChildren(); turns.clear();
      (s.messages || []).forEach(renderMessage); scroll(true);
      after = s.cursor || 0; busy = s.busy || ""; phoneAp = s.phone_approvals !== false;
      paintSilent(s.silent); paintAppr(s.approvals || []);
    }
    function onEvent(e) {
      if (e.type === "user") { bubble("user", e.text, e.turn); scroll(true); }
      else if (e.type === "delta") {
        let b = turns.get(e.turn);
        if (!b) { b = bubble("assistant", "", e.turn); b.classList.add("streaming"); b.setAttribute("aria-busy", "true"); turns.set(e.turn, b); }
        b.textContent = e.text; scroll();
      } else if (e.type === "reply") {
        let b = turns.get(e.turn);
        const text = e.text || (e.ok ? "" : word(e.code, tx("משהו השתבש. ראו את היומן.", "Something went wrong. See the log.")));
        if (!b && text) b = bubble("assistant", "", e.turn);
        if (b) { b.textContent = text; b.classList.remove("streaming"); b.removeAttribute("aria-busy"); if (!e.ok) b.classList.add("err"); }
        turns.delete(e.turn);
      } else if (e.type === "tool") toolLine(e);
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

    async function submit(text) {
      text = String(text || "").trim();
      if (!text) return;
      const body = { text, id: uid(), lang: lang(), speak: !!(speakBox && speakBox.checked) };
      sendBtn.disabled = true;
      try {
        for (let i = 0; ; i++) {
          try { await api.send(body); break; }       // the same id on every resend: answered once
          catch (e) { if (e.status === undefined && i < 3) { await sleep(1200 * (i + 1)); continue; } throw e; }
        }
        input.value = ""; grow();
      } catch (e) { note(word(e.code, e.message), "err"); }
      grow(); input.focus();
    }

    async function loop() {
      while (alive) {
        try {
          const r = await api.events(after, sig, lang());
          if (!alive) return;
          phoneAp = r.phone_approvals !== false;
          if (r.resync) { load(await api.state(lang())); continue; }
          (r.events || []).forEach(onEvent);
          after = r.cursor; busy = r.busy || ""; paintSilent(r.silent);
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
    return { destroy() { alive = false; } };
  }

  window.JarvesChat = { mount, WORDS };
})();
