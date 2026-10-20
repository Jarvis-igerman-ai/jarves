// SW-V4 G Recall page: search past conversations, notes and reports through the same index as the voice tool and
// `jarvis recall`, with filters (dates, speaker, kind of conversation, sources). Matched words are highlighted with
// text nodes (never HTML); recalled text is user content and is never translated (data-noi18n).
// Uses the helpers of app.js (el, api, render, header, pill, route) and pages.js (input, select, field, params,
// hashQuery, when).
"use strict";

const recallState = { q: "", since: "", until: "", speaker: "", conv: "", sources: "" };

function recallMarked(text, marks) {
  const out = [];
  let at = 0;
  for (const [a, b] of marks || []) {
    if (a < at || b > text.length) continue;
    if (a > at) out.push(text.slice(at, a));
    out.push(el("mark", {}, text.slice(a, b)));
    at = b;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

function recallWho(r) {
  if (r.source === "note") return tx("פתק", "note");
  if (r.source === "report") return tx("דוח", "report");
  if (r.source === "summary") return tx("סיכום שיחה", "conversation summary");
  if (r.source === "memory") return tx("זיכרון", "memory note");
  if (r.source === "tool") return tx("כלי", "tool");
  return r.role === "user" ? tx("אתה", "you") : "JARVES";
}

PAGES.recall = async function () {
  const s = recallState;
  const hq = hashQuery();
  const last = hq.last === "1" && !s.q;
  if (hq.q && !s.q) s.q = hq.q;
  const asked = s.q || last || s.since || s.until;
  const [d, st] = await Promise.all([
    asked ? api("/api/recall?" + params({ q: s.q, last: last ? "1" : "", since: s.since, until: s.until, speaker: s.speaker,
      conv: s.conv, sources: s.sources, limit: 30 })) : Promise.resolve(null),
    api("/api/recall/stats")]);
  if (d && last && d.question) s.q = d.question;
  const set = (k) => (ev) => { s[k] = ev.target.value; };
  const q = input({ type: "search", dir: "auto", placeholder: tx("מה אמרתי על הרופא?", "What did I say about the dentist?"),
    oninput: set("q"), style: "min-width:340px",
    "aria-label": tx("חיפוש", "Search") }, s.q);
  const go = () => { if (location.hash.includes("?")) location.hash = "#/recall"; else route(); };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const msg = el("span", { class: "muted small", role: "status" });
  const maint = (path, label) => el("button", { type: "button", class: "ghost", onclick: async (ev) => {
    ev.target.disabled = true; msg.textContent = tx("עובד…", "Working…");
    try { const r = await api(path, { method: "POST", body: "{}" }); msg.textContent = tx("בוצע", "Done") + " · " + JSON.stringify(r); }
    catch (e) { msg.textContent = e.message; }
    ev.target.disabled = false;
  } }, label);
  const filters = el("div", { class: "card filters" },
    field(tx("שאלה או מילים", "Question or words"), q),
    field(tx("מתאריך", "From"), input({ type: "text", placeholder: tx("2026-10-14 או בשבוע שעבר", "2026-10-14 or last week"), class: "narrow",
      dir: "auto", oninput: set("since") }, s.since)),
    field(tx("עד", "To"), input({ type: "text", placeholder: tx("תאריך או ביטוי", "date or phrase"), class: "narrow", dir: "auto",
      oninput: set("until") }, s.until)),
    field(tx("מי אמר", "Who said it"), select([["", tx("כולם", "anyone")], ["user", tx("אני", "me")], ["assistant", "JARVES"]], s.speaker,
      { onchange: set("speaker") })),
    field(tx("סוג שיחה", "Kind"), select([["", tx("קול והקלדה", "voice and typed")], ["voice", tx("קול", "voice")], ["typed", tx("הקלדה", "typed")]],
      s.conv, { onchange: set("conv") })),
    field(tx("מקורות", "Sources"), select([["", tx("הכול", "everything")], ["turn,tool,summary", tx("שיחות בלבד", "conversations only")],
      ["note", tx("פתקים", "notes")], ["report", tx("דוחות", "reports")], ["memory", tx("זיכרון", "memory notes")]], s.sources,
      { onchange: set("sources") })),
    el("div", { class: "row" }, el("button", { type: "button", class: "primary", onclick: go }, tx("חיפוש", "Search")),
      el("button", { type: "button", class: "ghost", onclick: () => { Object.assign(s, { q: "", since: "", until: "", speaker: "", conv: "", sources: "" }); go(); } },
        tx("ניקוי", "Clear"))));
  let body;
  if (d && d.enabled === false) {
    body = el("div", { class: "card" }, el("div", { class: "empty" }, tx("החיפוש בשיחות כבוי או לא זמין: ", "Conversation recall is off or unavailable: ") + (d.reason || "")),
      el("a", { href: "#/features" }, tx("עמוד התכונות", "Features page")));
  } else if (!d) {
    body = el("div", { class: "card" }, el("div", { class: "empty" }, tx("שאלו כמו בקול: \"מה אמרתי על הרופא?\", \"על מה דיברנו ביום שלישי?\"",
      "Ask as you would by voice: \"what did I say about the dentist?\", \"what did we talk about on Tuesday?\"")));
  } else {
    const items = d.results.map((r) => el("div", { class: "card", style: "margin-bottom:8px" },
      el("div", { class: "row", style: "gap:8px;flex-wrap:wrap" }, when(r.ts), pill(r.role === "user" ? "ok" : "plain", recallWho(r)),
        r.conv ? pill("plain", r.conv === "typed" ? tx("הקלדה", "typed") : tx("קול", "voice")) : null,
        r.private || r.third_party ? pill("medium", tx("פרטי", "private")) : null,
        r.hits > 1 ? el("span", { class: "muted small" }, `${r.hits} ${tx("התאמות", "matches")}`) : null,
        el("span", { class: "spacer" }),
        r.link ? el("a", { href: r.link }, r.source === "turn" || r.source === "tool" ? tx("לשיחה ←", "Open conversation →") : tx("פתיחה ←", "Open →")) : null),
      el("div", { dir: "auto", "data-noi18n": "", style: "margin-top:6px" }, recallMarked(r.snippet, r.marks)),
      r.also ? el("div", { dir: "auto", "data-noi18n": "", class: "small muted", style: "margin-top:4px" }, "… ", recallMarked(r.also.snippet, r.also.marks)) : null));
    body = [
      el("div", { class: "card", style: "margin-bottom:10px" }, el("b", {}, tx("התשובה הקצרה: ", "The short answer: ")),
        el("span", { dir: "auto", "data-noi18n": "" }, txh(d, "answer")),
        el("div", { class: "muted small", style: "margin-top:4px" },
          `${d.total} ${tx("תוצאות", "result(s)")} · ${d.took_ms} ms` + (d.range ? ` · ${tx("טווח", "range")}: ${d.range.phrase}` : "") +
          (d.terms.length ? ` · ${tx("מילים", "words")}: ${d.terms.join(", ")}` : ""))),
      items.length ? items : el("div", { class: "card" }, el("div", { class: "empty" }, tx("לא נמצא כלום. נסו מילה אחרת מהנושא או טווח זמן אחר.",
        "Nothing found. Try another word of the topic or another time range.")))];
  }
  const kinds = Object.entries(st.by_kind || {}).map(([k, n]) => `${k} ${n}`).join(" · ") || "—";
  render(header(tx("חיפוש בשיחות", "Recall"), tx("מה נאמר בשיחות קודמות, בפתקים ובדוחות. אותו אינדקס כמו בקול וב-jarvis recall.",
    "What was said in past conversations, notes and reports. The same index as the voice tool and jarvis recall.")),
    filters, body,
    el("div", { class: "card", style: "margin-top:12px" },
      el("div", { class: "small" }, `${tx("באינדקס", "Indexed")}: ${st.docs} (${kinds})` +
        (st.index_bytes ? ` · ${(st.index_bytes / 1048576).toFixed(1)} MB` : "") + (st.build_s !== null && st.build_s !== undefined ? ` · ${tx("בנייה אחרונה", "last build")} ${st.build_s} s` : "")),
      el("div", { class: "muted small" }, tx("פרופיל זה בלבד. מצב פרטיות: לא נשמר טקסט ולכן לא נכנס לאינדקס. אחרי 90 יום נשאר רק סיכום של כל שיחה.",
        "This profile only. Privacy mode: no text is kept, so nothing is indexed. After 90 days only a summary of each conversation stays.")),
      el("div", { class: "row", style: "margin-top:6px" }, maint("/api/recall/repair", tx("בדיקה ותיקון", "Check and repair")),
        maint("/api/recall/rebuild", tx("בנייה מחדש", "Rebuild")), msg)));
};
