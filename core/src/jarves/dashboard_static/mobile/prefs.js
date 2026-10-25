// JARVES phone: Learned preferences page (V5, issue #183). Same data and rules as the dashboard page; the phone can
// confirm, correct (edit), decline and forget, never add. Text via textContent only; the token stays in app.js.
"use strict";
(function () {
  const T = {
    en: { intro: "What JARVES learned about you. Only what you confirm is used in conversations.", empty: "Nothing learned yet.",
      confirmed: "Confirmed", proposed: "Waiting for your answer", declined: "Declined (kept so it is not asked again)",
      confirm: "Confirm", decline: "Decline", forget: "Forget", edit: "Correct", save: "Save", cancel: "Cancel", again: "Still true",
      stale: "Not re-confirmed for a long time: not used until you say it is still true.", conflict: "Looks like another confirmed preference",
      said: "You said", off: "Using preferences in conversations is switched off on the computer.", 
      saved: "Saved.", gone: "Forgotten.", failed: "Could not do that", loading: "Loading…", label: "Corrected text", ask: "Forget this preference?", yes: "Yes, forget" },
    he: { intro: "מה ש-JARVES למד עליכם. רק מה שאישרתם משמש בשיחות.", empty: "עוד לא נלמד כלום.",
      confirmed: "מאושר", proposed: "ממתין לתשובה שלכם", declined: "נדחה (נשמר כדי שלא ישאלו שוב)",
      confirm: "אישור", decline: "דחייה", forget: "שכחו", edit: "תיקון", save: "שמירה", cancel: "ביטול", again: "עדיין נכון",
      stale: "לא אושר מחדש זמן רב: לא בשימוש עד שתאמרו שזה עדיין נכון.", conflict: "דומה להעדפה מאושרת אחרת",
      said: "אמרתם", off: "השימוש בהעדפות בשיחות כבוי במחשב.", 
      saved: "נשמר.", gone: "נשכח.", failed: "לא ניתן לבצע", loading: "טוען…", label: "הטקסט המתוקן", ask: "לשכוח את ההעדפה הזאת?", yes: "כן, לשכוח" },
  };
  const newId = () => "pref-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  let root = null, cx = null, busy = false, editing = 0, asking = 0, note = "";
  const t = (k) => ((T[cx.lang()] || T.en)[k]) || T.en[k] || k;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  async function load() {
    let d; try { d = await cx.api("/api/m/prefs"); } catch (e) { note = e.message; paint(null); return; }
    paint(d);
  }
  async function op(body, ok) {
    if (busy) return; busy = true;
    try { await cx.post("/api/m/prefs/op", { ...body, id: newId() }); note = t(ok); editing = asking = 0; }
    catch (e) { note = t("failed") + ": " + e.message; }
    busy = false; load();
  }
  function btn(label, fn, primary) { const b = el("button", primary ? "primary" : "", label); b.type = "button"; b.addEventListener("click", fn); return b; }
  function row(ul, p, d, kind) {
    const li = el("li", "pref"), box = el("div", "grow");
    const txt = el("div", "", p.text); txt.className = "pref-text"; box.appendChild(txt);
    if (p.evidence && kind === "proposed") box.appendChild(el("div", "muted", t("said") + ": " + p.evidence));
    if (p.stale) box.appendChild(el("div", "muted", t("stale")));
    li.appendChild(box);
    const acts = el("div", "row wrap");
    if (editing === p.id) {
      const lab = el("label"); const sp = el("span", "", t("label")); const inp = el("input"); inp.maxLength = 200; inp.value = p.text; inp.setAttribute("aria-label", t("label"));
      lab.append(sp, inp); li.appendChild(lab);
      acts.append(btn(t("save"), () => op({ op: "edit", pref: p.id, text: inp.value }, "saved"), true), btn(t("cancel"), () => { editing = 0; paint(d); }));
    } else if (asking === p.id) {
      box.appendChild(el("div", "", t("ask")));
      acts.append(btn(t("yes"), () => op({ op: "remove", pref: p.id }, "gone"), true), btn(t("cancel"), () => { asking = 0; paint(d); }));
    } else {
      if (kind !== "confirmed") acts.appendChild(btn(t("confirm"), () => op({ op: "confirm", pref: p.id }, "saved"), true));
      if (kind === "proposed") acts.appendChild(btn(t("decline"), () => op({ op: "decline", pref: p.id }, "saved")));
      if (kind === "confirmed" && p.stale) acts.appendChild(btn(t("again"), () => op({ op: "reaffirm", pref: p.id }, "saved"), true));
      if (kind !== "declined") acts.appendChild(btn(t("edit"), () => { editing = p.id; paint(d); }));
      acts.appendChild(btn(t("forget"), () => { asking = p.id; paint(d); }));
    }
    li.appendChild(acts); ul.appendChild(li);
  }
  function paint(d) {
    if (!root) return;
    root.replaceChildren();
    root.appendChild(el("p", "muted", t("intro")));
    if (note) { const n = el("p", "muted", note); n.setAttribute("role", "status"); root.appendChild(n); }
    if (!d) return;
    if (!d.in_prompt) root.appendChild(el("p", "muted", t("off")));
    let any = false;
    [["proposed", d.proposed], ["confirmed", d.items], ["declined", d.declined]].forEach(([kind, rows]) => {
      if (!(rows || []).length) return; any = true;
      root.appendChild(el("h2", "", t(kind)));
      const ul = el("ul", "list"); rows.forEach((p) => row(ul, p, d, kind)); root.appendChild(ul);
    });
    if (!any) root.appendChild(el("p", "", t("empty")));
  }
  window.JarvesPrefs = {
    mount(node, ctx) { root = node; cx = ctx; note = ""; editing = asking = 0; root.replaceChildren(el("p", "muted", t("loading"))); load(); },
    refresh() { if (root && !editing && !asking && !busy) load(); },
    unmount() { root = null; },
    catalogs: T,
  };
})();
