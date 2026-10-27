// JARVES dashboard: Model Lab page (V7 M4, #418). READ ONLY: lists archived runs and compares models (quality, speed,
// cost). It never starts a run (that is `jarvis modellab run`) and never shows or reads a key. Free and local models
// only by default; the paid ones appear only when the box is ticked. Text goes in via textContent (el helper).
"use strict";

(function () {
  const st = { runs: null, a: "", b: "", paid: false, data: null, err: "" };
  const LABEL = { free: ["ok", "חינמי", "Free"], local: ["ok", "מקומי", "Local"], paid: ["medium", "בתשלום", "Paid"],
    unknown: ["plain", "לא ידוע", "Unknown"] };
  const pct = (q) => q === null || q === undefined ? "—" : Math.round(q * 100) + "%";
  const secs = (v) => v === null || v === undefined ? "—" : v + " s";

  async function load() {
    st.err = "";
    try {
      if (!st.runs) {
        st.runs = (await api("/api/modellab/runs")).runs;
        st.a = st.a || (st.runs[0] ? st.runs[0].id : "");
      }
      const ids = [st.a, st.b].filter(Boolean).join(",");
      st.data = ids ? await api(`/api/modellab/${st.paid ? "all" : "compare"}/${ids}`) : null;
    } catch (e) { st.err = e.message; st.data = null; }
  }

  function runSelect(label, value, none, onchange) {
    const s = el("select", { "aria-label": label, onchange: (e) => onchange(e.target.value) },
      none ? el("option", { value: "" }, tx("בלי השוואה", "No comparison")) : null,
      st.runs.map((r) => el("option", { value: r.id }, `${r.id} (${r.models.length})`)));
    s.value = value;
    return el("label", { class: "field" }, el("span", {}, label), s);
  }

  function table() {
    const d = st.data, ids = d.run_ids;
    const head = el("tr", {}, el("th", { scope: "col" }, tx("מודל", "Model")), el("th", { scope: "col" }, tx("סוג", "Type")),
      ids.map((i) => [tx("איכות", "Quality"), tx("מהירות", "Speed"), tx("עלות", "Cost"), tx("עלות לתשובה מקובלת", "Cost per accepted")]
        .map((h) => el("th", { scope: "col", class: "num" }, ids.length > 1 ? `${h} (${i})` : h))));
    const rows = d.models.map((m) => {
      const l = LABEL[m.label] || LABEL.unknown;
      return el("tr", {}, el("td", { dir: "ltr" }, el("b", {}, m.model)), el("td", {}, pill(l[0], tx(l[1], l[2]))),
        ids.map((i) => {
          const r = m.runs[i];
          if (!r) return [1, 2, 3, 4].map(() => el("td", { class: "num" }, "—"));
          const none = r.ran === 0;
          return [el("td", { class: "num" }, none ? tx("דולג", "Skipped") : pct(r.quality)),
            el("td", { class: "num" }, secs(r.avg_latency_s)), el("td", { class: "num" }, usd(r.cost_usd)),
            el("td", { class: "num" }, usd(r.cost_per_accepted_usd))];
        }));
    });
    return el("table", {}, el("caption", { class: "muted small" }, tx("כל מודל בכל הרצה שנבחרה", "Each model in each selected run")),
      el("thead", {}, head), el("tbody", {}, rows));
  }

  function draw() {
    const rep = st.data;
    const paid = el("input", { type: "checkbox", id: "mlPaid", onchange: async (e) => { st.paid = e.target.checked; await load(); draw(); } });
    paid.checked = st.paid;
    const body = st.err ? el("div", { class: "card", role: "alert" }, st.err)
      : !st.runs || !st.runs.length ? el("div", { class: "empty" }, tx(
        "עוד אין הרצות. מעבדת המודלים רצה רק כשאתם מתחילים אותה: בטרמינל הריצו את "jarvis modellab run". כאן רואים רק תוצאות.",
        "No runs yet. The Model Lab runs only when you start it: in a terminal run jarvis modellab run. This page only shows results."))
        : el("div", {}, el("div", { class: "row" }, runSelect(tx("הרצה", "Run"), st.a, false, async (v) => { st.a = v; await load(); draw(); }),
          runSelect(tx("השוואה להרצה", "Compare with run"), st.b, true, async (v) => { st.b = v; await load(); draw(); }),
          el("label", { class: "field" }, paid, el("span", {}, " " + tx("הצג גם מודלים בתשלום", "Also show paid models")))),
        rep && rep.models.length ? el("div", { class: "card", style: "overflow-x:auto" }, table())
          : el("div", { class: "empty" }, tx("אין מודלים חינמיים או מקומיים בהרצה הזו. אפשר להציג גם בתשלום.",
            "No free or local models in this run. You can also show paid ones.")));
    render(header(tx("מעבדת מודלים", "Model Lab"), tx(
      "איכות, מהירות ועלות לכל מודל, ממערך משימות ציבורי וקבוע. רק קריאה: שום דבר לא רץ מהדף הזה, ולא מוצג מפתח.",
      "Quality, speed and cost per model on a fixed public task set. Read only: nothing runs from this page and no key is shown.")),
      el("div", { class: "card small", dir: "auto", role: "note" }, tx(
        "ברירת מחדל: מודלים חינמיים ומקומיים בלבד. הרצה בתשלום דורשת תקרה שאתם קובעים, עד 2 דולר.",
        "Default: free and local models only. A paid run needs a cap you set, at most 2 USD.")), body);
  }

  async function pageModelLab() { st.runs = null; await load(); draw(); }
  Object.assign(PAGES, { modellab: pageModelLab });
})();
