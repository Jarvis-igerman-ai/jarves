// JARVES dashboard: Chat page (#/chat; `jarvis web` opens it). The same silent text conversation as `jarvis chat`, with
// streaming answers, slash commands, one-line tool results and inline approvals (chatview.js does the work).
// Uses app.js helpers (el, api, render, header) and tx(he, en). Text goes in via textContent only.
"use strict";

(function () {
  let view = null;
  const q = (o) => Object.entries(o).filter(([, v]) => v !== "" && v !== null && v !== undefined).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const post = async (path, body) => {
    const r = await fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Jarves": "1",
      "X-CSRF-Token": window.JARVES_CSRF || "" }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(d.error || r.statusText); e.status = r.status; e.code = d.code; throw e; }
    return d;
  };
  const get = async (path) => {
    const r = await fetch(path, { credentials: "same-origin", cache: "no-store" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(d.error || r.statusText); e.status = r.status; e.code = d.code; throw e; }
    return d;
  };
  const chatApi = {
    state: (lang) => get("/api/chat/state?" + q({ lang })),
    events: (after, sig, lang) => get("/api/chat/events?" + q({ after, sig, lang, wait: 20 })),
    send: (body) => post("/api/chat/send", body),
    answer: (id, approved, typed) => post("/api/approvals/" + encodeURIComponent(id), { approved, typed }),
    silent: (on) => post("/api/chat/silent", { on }),
  };

  function destroy() { if (view) { view.destroy(); view = null; } }
  window.addEventListener("hashchange", () => { if (!location.hash.startsWith("#/chat")) destroy(); });

  async function pageChat() {
    destroy();
    const holder = el("div", { id: "chatHolder" });
    render(header(tx("שיחה", "Chat"),
      tx("אותה שיחה כמו ב-jarvis chat ובשיחה הקולית: אותה היסטוריה, אותם כלים ואותם אישורים. כתבו / לפקודות.",
        "The same conversation as jarvis chat and the voice session: the same history, tools and approvals. Type / for commands."),
      el("a", { href: "#/approvals" }, tx("אישורים", "Approvals"))), holder);
    view = window.JarvesChat.mount(holder, { api: chatApi, tx, device: false });
  }
  Object.assign(PAGES, { chat: pageChat });
})();
