// JARVES phone: Goal plans (V5, issue #184). Read-only steps and status; the phone can pause, cancel, resume or retry a plan (#340).
// Approving, skipping and deleting stay on the computer (#200). Text via textContent only.
"use strict";
(function () {
  const T = {
    en: { intro: "Plans JARVES made for your goals. Starting or repeating a step is always done on the computer; here you can look, pause, resume, retry or cancel.",
      progress: "Progress", empty: "No plans yet.", off: "Goal plans are switched off on the computer.", loading: "Loading…", pause: "Pause", resume: "Resume", retry: "Retry", yes_retry: "Yes, retry", ask_retry: "Run the failed steps again? A step that changes something still asks for your approval on the computer.", resumed_ok: "Plan resumed.", retried_ok: "Plan will try again.", cancel: "Cancel plan", yes: "Yes, cancel", no: "Keep",
      ask: "Cancel this plan? Steps that did not run will be skipped.", step: "Steps", done_word: "done", pc: "Approve, skip or delete on the computer.", failed: "Could not do that",
      paused_ok: "Plan paused.", cancelled_ok: "Plan cancelled.", needs_approval: "Waiting for your approval on the computer.", changes: "changes something",
      s_draft: "waiting for approval", s_ready: "ready", s_running: "running", s_paused: "paused", s_blocked: "needs you", s_done: "done", s_failed: "failed", s_cancelled: "cancelled",
      t_pending: "pending", t_running: "running", t_done: "done", t_failed: "failed", t_blocked: "blocked", t_unknown: "unknown: check on the computer", t_skipped: "skipped" },
    he: { intro: "תוכניות ש-JARVES הכין ליעדים שלכם. התחלה או חזרה על שלב נעשות תמיד במחשב; כאן אפשר לצפות, להשהות, לחדש, לנסות שוב או לבטל.",
      progress: "התקדמות", empty: "אין תוכניות עדיין.", off: "תוכניות ליעדים כבויות במחשב.", loading: "טוען…", pause: "השהיה", resume: "המשך", retry: "ניסיון חוזר", yes_retry: "כן, לנסות שוב", ask_retry: "להריץ שוב את השלבים שנכשלו? שלב שמשנה משהו עדיין ישאל לאישור שלכם במחשב.", resumed_ok: "התוכנית חודשה.", retried_ok: "התוכנית תנסה שוב.", cancel: "ביטול תוכנית", yes: "כן, לבטל", no: "להשאיר",
      ask: "לבטל את התוכנית? שלבים שלא רצו ידולגו.", step: "שלבים", done_word: "הושלמו", pc: "אישור, דילוג ומחיקה נעשים במחשב.", failed: "לא ניתן לבצע",
      paused_ok: "התוכנית הושהתה.", cancelled_ok: "התוכנית בוטלה.", needs_approval: "ממתינה לאישור שלכם במחשב.", changes: "משנה משהו",
      s_draft: "ממתינה לאישור", s_ready: "מוכנה", s_running: "רצה", s_paused: "מושהית", s_blocked: "צריכה אתכם", s_done: "הושלמה", s_failed: "נכשלה", s_cancelled: "בוטלה",
      t_pending: "ממתין", t_running: "רץ", t_done: "הושלם", t_failed: "נכשל", t_blocked: "חסום", t_unknown: "לא ידוע: בדקו במחשב", t_skipped: "דולג" },
  };
  const newId = () => "plan-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  let root = null, cx = null, busy = false, asking = null, note = "", last = null; const open = new Set();
  const t = (k) => ((T[cx.lang()] || T.en)[k]) || T.en[k] || k;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  async function load() {
    let d; try { d = await cx.api("/api/m/plans"); } catch (e) { note = e.message; paint(null); return; }
    last = d; paint(d);
  }
  async function op(body, ok) {
    if (busy) return; busy = true;
    try { await cx.post("/api/m/plans/op", { ...body, id: newId() }); note = t(ok); asking = null; }
    catch (e) { note = t("failed") + ": " + e.message; asking = null; }
    busy = false; load();
  }
  function btn(label, fn, primary) { const b = el("button", primary ? "primary" : "", label); b.type = "button"; b.addEventListener("click", fn); return b; }
  function card(ul, p) {
    const li = el("li", "plan"), box = el("div", "grow");
    const head = el("div", "plan-head"); head.append(el("strong", "plan-title", p.title), el("span", "pill", t("s_" + p.state)));
    box.appendChild(head);
    const total = p.steps.length, done = p.steps.filter((s) => s.status === "done" || s.status === "skipped").length;
    const bar = el("progress"); bar.max = 100; bar.value = p.progress; bar.setAttribute("aria-label", t("progress") + " " + p.progress + "%"); box.appendChild(bar);
    box.appendChild(el("div", "muted", `${p.progress}% · ${done}/${total} ${t("done_word")}`));
    if (p.state === "draft") box.appendChild(el("div", "muted", t("needs_approval")));
    else if (p.needs_pc) box.appendChild(el("div", "muted", t("pc")));
    if (p.note) box.appendChild(el("div", "muted plan-note", p.note));
    const det = el("details"); if (open.has(p.id)) det.open = true;
    det.addEventListener("toggle", () => { if (det.open) open.add(p.id); else open.delete(p.id); });
    det.appendChild(el("summary", "", `${t("step")} (${total})`));
    const ol = el("ol", "plan-steps");
    p.steps.forEach((s) => {
      const si = el("li"); si.appendChild(el("span", "plan-step-tool", s.tool + " "));
      si.appendChild(el("span", "pill " + (s.status === "done" ? "ok" : (s.status === "failed" || s.status === "unknown" ? "bad" : "")), t("t_" + s.status)));
      if (s.consequential) si.appendChild(el("span", "muted", " · " + t("changes")));
      if (s.why) si.appendChild(el("div", "muted plan-why", s.why));
      if (s.error) si.appendChild(el("div", "plan-err", s.error));
      ol.appendChild(si);
    });
    det.appendChild(ol); box.appendChild(det);
    li.appendChild(box);
    const acts = el("div", "row wrap");
    if (asking && asking.id === p.id) {
      const retry = asking.op === "retry";
      box.appendChild(el("div", "", t(retry ? "ask_retry" : "ask")));
      acts.append(btn(t(retry ? "yes_retry" : "yes"), () => op({ op: asking.op, plan: p.id }, retry ? "retried_ok" : "cancelled_ok"), true), btn(t("no"), () => { asking = null; paint(last); }));
    } else {
      if (p.can.includes("pause")) acts.appendChild(btn(t("pause"), () => op({ op: "pause", plan: p.id }, "paused_ok")));
      if (p.can.includes("resume")) acts.appendChild(btn(t("resume"), () => op({ op: "resume", plan: p.id }, "resumed_ok")));
      if (p.can.includes("retry")) acts.appendChild(btn(t("retry"), () => { asking = { id: p.id, op: "retry" }; paint(last); }));
      if (p.can.includes("cancel")) acts.appendChild(btn(t("cancel"), () => { asking = { id: p.id, op: "cancel" }; paint(last); }));
    }
    li.appendChild(acts); ul.appendChild(li);
  }
  function paint(d) {
    if (!root) return;
    root.replaceChildren();
    root.appendChild(el("p", "muted", t("intro")));
    if (note) { const n = el("p", "muted", note); n.setAttribute("role", "status"); root.appendChild(n); }
    if (!d) return;
    if (!d.enabled) root.appendChild(el("p", "muted", t("off")));
    if (!d.plans.length) { root.appendChild(el("p", "", t("empty"))); return; }
    const ul = el("ul", "list"); d.plans.forEach((p) => card(ul, p)); root.appendChild(ul);
  }
  window.JarvesPlans = {
    mount(node, ctx) { root = node; cx = ctx; note = ""; asking = null; root.replaceChildren(el("p", "muted", t("loading"))); load(); },
    refresh() { if (root && !asking && !busy) load(); },
    unmount() { root = null; },
    catalogs: T,
  };
})();
