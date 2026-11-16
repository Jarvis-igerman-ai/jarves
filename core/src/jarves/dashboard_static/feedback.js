// JARVES dashboard: Feedback page (JAR-230, beta, off by default). "That was wrong" saves the LAST exchange (what you
// said, what JARVES answered, the tool names) as one redacted local case; the list shows saved cases and can delete one.
// Nothing is sent anywhere. Local computer only. Text goes in via textContent (el helper).
"use strict";

(function () {
  const st = { active: false, cases: [], err: "", msg: "" };

  async function load() {
    st.err = "";
    try {
      const r = await api("/api/feedback");
      st.active = !!r.active;
      st.cases = r.cases || [];
    } catch (e) { st.err = e.message; st.cases = []; }
  }

  async function wrong(note) {
    st.msg = "";
    try {
      const r = await api("/api/feedback/wrong", { method: "POST", body: JSON.stringify({ note }) });
      st.msg = tx(`נשמר כמשוב (${r.case.id}). שום דבר לא נשלח.`, `Saved as feedback (${r.case.id}). Nothing was sent.`);
    } catch (e) {                // only "no exchange" (404) gets that text; refused/off/network show the error (R7)
      st.msg = e.message === "FEEDBACK_NO_EXCHANGE" ? tx("לא נמצאה שיחה אחרונה לשמור. אולי היסטוריית השיחות כבויה.",
        "No last exchange to save. The conversation history may be off.") : e.message;
    }
    await load(); draw();
  }

  async function remove(id) {
    try { await api(`/api/feedback/${id}/delete`, { method: "POST", body: "{}" }); } catch (e) { st.err = e.message; }
    await load(); draw();
  }

  function caseCard(c) {
    return el("div", { class: "card", dir: "auto" },
      el("div", { class: "row" }, el("b", { dir: "ltr" }, c.id), el("span", { class: "muted small" }, ` ${c.saved || ""} · ${c.via || ""}`),
        c.exchange_at ? el("span", { class: "muted small" }, tx(` · השיחה מ-${c.exchange_at.replace("T", " ")}`,
          ` · exchange from ${c.exchange_at.replace("T", " ")}`)) : null,
        el("button", { type: "button", class: "ghost", "aria-label": tx(`מחיקת ${c.id}`, `Delete ${c.id}`), onclick: () => remove(c.id) },
          tx("מחיקה", "Delete"))),
      el("div", {}, el("span", { class: "muted" }, tx("אמרתם: ", "You said: ")), c.say || "—"),
      el("div", {}, el("span", { class: "muted" }, tx("JARVES ענה: ", "JARVES answered: ")), c.reply || "—"),
      c.tools && c.tools.length ? el("div", { class: "small muted", dir: "ltr" }, c.tools.join(", ")) : null,
      c.note ? el("div", { class: "small" }, tx("הערה: ", "Note: "), c.note) : null);
  }

  function draw() {
    const note = el("input", { type: "text", maxlength: "300", id: "fbNote", "aria-label": tx("מה היה לא נכון (רשות)", "What was wrong (optional)"),
      placeholder: tx("מה היה לא נכון (רשות)", "What was wrong (optional)") });
    const body = st.err ? el("div", { class: "card", role: "alert" }, st.err)
      : !st.active ? el("div", { class: "empty", role: "status" }, tx(
        "המשוב כבוי (בטא). הפעילו בדף היכולות: קודם יכולות בטא, ואז המתג “wrong_feedback”. או בטרמינל: “jarvis features on wrong_feedback”.",
        "Feedback is off (beta): turn it on in Features: first beta features, then the wrong_feedback switch. Or in a terminal: jarvis features on wrong_feedback."))
        : el("div", {},
          el("div", { class: "row" }, el("label", { class: "field" }, note),
            el("button", { type: "button", class: "primary", id: "fbWrong", onclick: () => wrong(note.value) },
              tx("זה היה לא נכון: שמרו את השיחה האחרונה", "That was wrong: save the last exchange"))),
          st.msg ? el("div", { class: "card small", role: "status" }, st.msg) : null,
          st.cases.length ? st.cases.map(caseCard) : el("div", { class: "empty" }, tx("עוד לא נשמר משוב.", "No feedback saved yet.")));
    render(header(tx("משוב", "Feedback"), tx(
      "כשתשובה הייתה לא נכונה: לחצו על הכפתור, או אמרו “ג׳רוויס, זה היה לא נכון”. נשמרת השיחה האחרונה, מושחרת, במחשב בלבד.",
      "When an answer was wrong: press the button, or say “Jarvis, that was wrong”. The last exchange is saved, redacted, on this PC only.")), body);
  }

  async function pageFeedback() { st.msg = ""; await load(); draw(); }
  Object.assign(PAGES, { feedback: pageFeedback });
})();
