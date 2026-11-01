// Release sign-off, step by step (#60, feature `signoff_steps`, beta, OFF by default). The SAME sheet and API as the
// table view (updates.js, /api/release/signoff): one owner check per screen, resume at the first open check, the rows CI
// already proved shown as one summary. Records only the button the owner presses (Works = pass, Does not work = fail
// with his note, Cannot test now = blocked, Accept as untestable = accepted-blocked with HIS typed reason). Nothing is
// ever recorded in bulk or on his behalf; signing is the existing finish call and the server refuses it while a row is
// open. Text goes in via textContent only (el helper).
"use strict";

(function () {
  const tablePage = PAGES.signoff;
  if (typeof tablePage !== "function") return;
  const BIG = "min-height:52px;font-size:1.1rem;flex:1 1 140px";

  PAGES.signoff = async function () {
    const params = new URLSearchParams((location.hash.split("?")[1]) || "");
    const version = params.get("version");
    if (!version || params.get("view") === "table") return tablePage();
    let d;
    try { d = await api("/api/release/signoff?version=" + encodeURIComponent(version)); }
    catch (_e) { return tablePage(); }                                   // the table view explains a missing sheet
    if (!d.steps_view) {
      await tablePage();
      $main.append(el("div", { class: "muted small", style: "margin-top:10px" },
        tx("תצוגת צעד אחר צעד כבויה (בטא). להפעלה: jarvis features on signoff_steps",
           "The step-by-step view is off (beta). Turn it on: jarvis features on signoff_steps")));
      return;
    }
    const go = (extra) => {                                             // a changed hash re-routes by itself (app.js)
      const h = "#/signoff?version=" + encodeURIComponent(version) + (extra || "");
      if (location.hash === h) route(true); else location.hash = h;
    };
    const rows = Object.fromEntries(d.rows.map((r) => [r.id, r]));
    const steps = d.steps.map((id) => rows[id]);
    const auto = d.rows.filter((r) => r.auto);
    const out = el("div", { class: "muted small", role: "status", "aria-live": "polite" });
    const post = (body) => api("/api/release/signoff", { method: "POST", body: JSON.stringify({ version, ...body }) });
    const head = header(tx("אישור גרסה ", "Release sign-off ") + d.version,
      tx("בדיקה אחת בכל מסך. נרשם רק מה שאתם לוחצים.", "One check per screen. Only what you press is recorded."),
      el("a", { href: "#/signoff?version=" + encodeURIComponent(version) + "&view=table", class: "small" }, tx("תצוגת טבלה", "Table view")));
    const build = el("div", { class: "muted small" }, tx("בטא מותקנת: ", "Installed beta: ") + (d.installed || tx("לא ידוע", "unknown")));
    const autoCard = auto.length ? el("div", { class: "card" },
      el("strong", {}, tx(`בדיקות שהמערכת כבר אישרה: ${auto.length}`, `Checks the system already passed: ${auto.length}`)),
      el("div", { class: "muted small", dir: "auto" }, auto[0].note)) : null;

    const want = params.get("step");                                     // none = resume at the first open check
    if (!steps.length || want === "end" || (!want && !d.next)) return renderEnd();
    let idx = steps.findIndex((s) => s.id === want);
    if (idx < 0) idx = Math.max(0, steps.findIndex((s) => s.id === d.next));

    const r = steps[idx];
    const label = { pending: tx("ממתין", "pending"), pass: tx("עובד", "works"), fail: tx("לא עובד", "does not work"),
      blocked: tx("לא נבדק", "not tested"), "accepted-blocked": tx("אושר כלא ניתן לבדיקה", "accepted as untestable") };
    const note = el("textarea", { rows: "3", style: "width:100%", "aria-label": tx("מה לא עבד", "What did not work") });
    const reason = el("input", { type: "text", style: "width:100%", "aria-label": tx("למה אי אפשר לבדוק", "Why it cannot be tested") });
    const after = () => {
      const nextOpen = steps.slice(idx + 1).find((s) => s.result === "pending");
      go(nextOpen ? "&step=" + encodeURIComponent(nextOpen.id) : "&step=end");
    };
    const record = async (result, text) => {
      try { await post({ row: r.id, result, note: text || "" }); after(); }
      catch (e) { out.textContent = e.message; }
    };
    const failBox = el("div", { class: "card", hidden: true },
      el("label", {}, tx("מה קרה? (משפט אחד)", "What happened? (one sentence)")), note,
      el("button", { type: "button", class: "primary", onclick: () => {
        if (!note.value.trim()) { out.textContent = tx("כתבו משפט קצר על מה שלא עבד.", "Write one short sentence about what did not work."); return; }
        record("fail", note.value);
      } }, tx("שמירה", "Save")));
    const acceptBox = el("div", { class: "card", hidden: true },
      el("label", {}, tx("למה אי אפשר לבדוק את זה? (נרשם בגיליון)", "Why can this not be tested? (written in the sheet)")), reason,
      el("button", { type: "button", onclick: () => {
        if (!reason.value.trim()) { out.textContent = tx("צריך סיבה כדי לאשר.", "A reason is needed to accept."); return; }
        record("accepted-blocked", reason.value);
      } }, tx("אישור כלא ניתן לבדיקה", "Accept as untestable")));
    const prog = el("progress", { value: String(idx + 1), max: String(steps.length), style: "width:100%",
      "aria-label": tx(`צעד ${idx + 1} מתוך ${steps.length}`, `Step ${idx + 1} of ${steps.length}`) });
    render(head, build, out, idx === 0 ? autoCard : null,
      el("div", { class: "card" },
        el("div", { class: "row" }, el("strong", {}, tx(`צעד ${idx + 1} מתוך ${steps.length}`, `Step ${idx + 1} of ${steps.length}`)),
          el("div", { class: "spacer" }), pill(r.result === "pass" ? "ok" : r.result === "pending" ? "" : "degraded", label[r.result] || r.result)),
        prog,
        el("div", { class: "muted small", dir: "auto" }, r.id + " · " + r.area),
        el("h2", {}, tx("מה לעשות", "What to do")), el("div", { style: "font-size:1.15rem", dir: "auto" }, r.todo),
        el("h2", {}, tx("מה אמור לקרות", "What should happen")), el("div", { style: "font-size:1.15rem", dir: "auto" }, r.expected),
        r.note ? el("div", { class: "muted small" }, tx("הערה: ", "Note: "), el("span", { dir: "auto" }, r.note)) : null,
        el("div", { class: "row", style: "flex-wrap:wrap;gap:8px;margin-top:12px" },
          el("button", { type: "button", class: "primary", style: BIG, onclick: () => record("pass", "") }, tx("עובד", "Works")),
          el("button", { type: "button", style: BIG, onclick: () => { failBox.hidden = false; note.focus(); } }, tx("לא עובד", "Does not work")),
          el("button", { type: "button", class: "ghost", style: BIG, onclick: () => record("blocked", "") }, tx("אי אפשר לבדוק עכשיו", "Cannot test now"))),
        el("button", { type: "button", class: "ghost small", onclick: () => { acceptBox.hidden = false; reason.focus(); } },
          tx("לא ניתן לבדיקה בכלל? אשרו עם סיבה", "Cannot be tested at all? Accept with a reason"))),
      failBox, acceptBox,
      el("div", { class: "row", style: "gap:8px;margin-top:10px" },
        el("button", { type: "button", class: "ghost", disabled: idx === 0, onclick: () => go("&step=" + encodeURIComponent(steps[idx - 1].id)) }, tx("חזרה", "Back")),
        el("div", { class: "spacer" }),
        el("button", { type: "button", class: "ghost", onclick: () => go(idx + 1 < steps.length ? "&step=" + encodeURIComponent(steps[idx + 1].id) : "&step=end") }, tx("הבא", "Next"))));

    function renderEnd() {
      const failed = steps.filter((s) => s.result === "fail");
      const counts = steps.reduce((m, s) => (m[s.result] = (m[s.result] || 0) + 1, m), {});
      const finish = el("button", { type: "button", class: "primary", style: BIG, disabled: d.open.length > 0 || d.signed, onclick: async () => {
        try { await post({ finish: true }); route(true); } catch (e) { out.textContent = tx("לא נחתם: ", "Not signed: ") + e.message; }
      } }, tx("לחתום על הגרסה", "Sign off this release"));
      render(head, build, out, autoCard,
        el("div", { class: "card" },
          el("h2", {}, tx("סיכום", "Summary")),
          el("div", {}, tx(`עובד: ${counts.pass || 0} · לא עובד: ${counts.fail || 0} · לא נבדק: ${(counts.blocked || 0) + (counts.pending || 0)} · אושר כלא ניתן לבדיקה: ${counts["accepted-blocked"] || 0}`,
            `Works: ${counts.pass || 0} · Does not work: ${counts.fail || 0} · Not tested: ${(counts.blocked || 0) + (counts.pending || 0)} · Accepted as untestable: ${counts["accepted-blocked"] || 0}`)),
          d.signed ? pill("ok", tx("נחתם", "Signed")) : null,
          ...steps.filter((s) => s.result !== "pass" && s.result !== "accepted-blocked").map((s) =>
            el("div", { dir: "auto" }, el("a", { href: "#/signoff?version=" + encodeURIComponent(version) + "&step=" + encodeURIComponent(s.id) }, s.id + ": " + s.todo))),
          failed.length ? el("div", { class: "small", style: "margin-top:8px" },
            tx("משהו לא עבד: הגרסה היציבה חסומה. דווחו בעמוד ", "Something did not work: the stable release is blocked. Report it on the "),
            el("a", { href: "#/bugs" }, tx("דיווחי תקלות", "Bug reports")), ".") : null,
          el("div", { class: "row", style: "margin-top:12px" }, finish),
          el("div", { class: "muted small" }, tx("החתימה רושמת את הבטא המותקנת, הקומיט שלה ואת התאריך. השרת מסרב כל עוד שורה פתוחה.",
            "Signing records the installed beta, its commit and the date. The server refuses while any row is open."))),
        steps.length ? el("button", { type: "button", class: "ghost", onclick: () => go("&step=" + encodeURIComponent(steps[steps.length - 1].id)) }, tx("חזרה", "Back")) : null);
    }
  };
})();
