// Release notes page: every version from CHANGELOG.md, newest first; pick a version to read what changed.
// Uses the helpers of app.js (el, api, render, header, pill, route, tx) and registers PAGES.releasenotes (pages.js).
// Text goes in via textContent only (the notes are data, never HTML). The note texts are the English CHANGELOG
// (data-noi18n: not part of the translated UI); the kind tags, the picker and the labels are translated.
"use strict";

PAGES.releasenotes = async function () {
  const params = new URLSearchParams((location.hash.split("?")[1]) || "");
  const want = params.get("version") || "";
  let d;
  try { d = await api("/api/release-notes" + (want ? "?version=" + encodeURIComponent(want) : "")); }
  catch (e) { render(header("Release notes", "What changed in each version."), el("div", { class: "card" }, e.message)); return; }
  const sel = d.selected;
  const pick = el("select", { id: "rn-version", "aria-label": tx("גרסה", "Version"), onchange: (ev) => { location.hash = "#/releasenotes?version=" + encodeURIComponent(ev.target.value); } },
    ...d.versions.map((v) => el("option", { value: v.version, selected: sel && v.version === sel.version }, v.version + (v.status ? " (" + v.status + ")" : ""))));
  const kindHe = { Added: "נוסף", Changed: "שונה", Fixed: "תוקן", Docs: "תיעוד", Version: "גרסה", Security: "אבטחה", Removed: "הוסר", Note: "הערה" };
  const items = sel ? sel.items.map((it) => el("li", {},
    pill(it.kind === "Fixed" ? "ok" : "info", tx(kindHe[it.kind] || it.kind, it.kind) + (it.scope ? " (" + it.scope + ")" : "")), " ",
    el("span", { "data-noi18n": "" }, it.text))) : [];
  render(header("Release notes", "What changed in each version."),
    el("div", { class: "card" },
      el("div", { class: "row" }, el("label", { for: "rn-version" }, "Version"), pick,
        d.current && sel && sel.version.indexOf(d.current) === 0 ? pill("ok", tx("הגרסה שלך", "Your version")) : null),
      sel ? (sel.intro ? el("p", { "data-noi18n": "" }, sel.intro) : null) : el("p", { class: "muted" }, "No release notes found in this install."),
      items.length ? el("ul", {}, ...items) : null));
};
