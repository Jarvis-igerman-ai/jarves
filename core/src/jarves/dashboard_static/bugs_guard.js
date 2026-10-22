// JARVES dashboard: reports an uncaught script error to the issues registry (Bug reports page) so the developer can
// fix it in the next version. Loaded first and not deferred, so it also sees errors while the other scripts load.
// CONTENT-FREE by construction: only the error class name ("TypeError"), the script file name, line, column and the
// page id are sent. The error message and the stack text (which can contain what was typed) are never read or sent.
// At most 5 reports per page load; the server rate-limits again.
"use strict";

(function () {
  let sent = 0;
  const MAX = 5;
  const page = () => (location.hash.replace(/^#\/?/, "").split(/[\/?]/)[0] || "home").toLowerCase().replace(/[^a-z0-9_\-]/g, "").slice(0, 30);
  const cls = (n) => (/^[A-Za-z]{1,40}$/.test(String(n)) ? String(n) : "Error");
  function send(name, file, line, col) {
    if (sent >= MAX || !/^[a-z0-9_.\-]{1,40}\.js$/.test(file)) return;
    sent += 1;
    try {
      fetch("/api/bugs/client-error", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Jarves": "1", "X-CSRF-Token": window.JARVES_CSRF || "" },
        body: JSON.stringify({ name: cls(name), file, line: line | 0, col: col | 0, page: page() }),
      }).catch(() => {});
    } catch (e) { /* reporting must never raise */ }
  }
  window.addEventListener("error", (ev) => {
    const m = String(ev.filename || "").match(/\/([a-z0-9_.\-]+\.js)(?:\?.*)?$/);
    if (m) send(ev.error && ev.error.name, m[1], ev.lineno, ev.colno);
  });
  window.addEventListener("unhandledrejection", (ev) => {
    const r = ev.reason;
    const m = String((r && r.stack) || "").match(/\/([a-z0-9_.\-]+\.js):(\d+):(\d+)/);
    if (m) send(r && r.name, m[1], +m[2], +m[3]);
  });
})();
