// JARVES dashboard locale layer (SW-V3 P0.2c, issue #17): N languages, ENGLISH by default, RTL-correct.
//
// Locales: every language is one catalog file in locales/<code>.js that calls JARVES_LOCALE({...}) with its meta
// (code, name, dir, bcp47) and a "dict" keyed by the ENGLISH source text (gettext style). Adding a language is
// that one file plus its <script> tag in index.html (the registry: script order = picker order). en.js lists every
// source key; tests require he.js to have all of them and report coverage for the others (missing keys fall back
// to English, never to an empty string).
//
// The choice: an explicit saved choice (localStorage "jarves-lang", set only by the picker) wins, else ENGLISH.
// The browser's language never picks one. "#/page?lang=xx" forces one for captures.
//
// Pages call tx(he, en) (Hebrew written inline, the English is the catalog key for every other locale),
// txh/txo(obj, key) for server texts with <key>_<locale> siblings, and JL.t(english). Older English-only pages are
// translated by the catalog on new DOM nodes. User content (dir="auto"), code, logs and file text are never touched.
// Text only via textContent / node.data, never innerHTML. Loaded after the catalogs and before app.js.
"use strict";

(function () {
  const KEY = "jarves-lang", DEFAULT = "en";
  const CAT = window.JARVES_CATALOGS || {};
  // the registry: English is built in (its catalog only lists the keys); the others come from their catalog files
  const LOC = { en: { code: "en", name: "English", english: "English", dir: "ltr", bcp47: "en-GB", dict: {}, rules: [] } };
  for (const c of Object.keys(CAT)) LOC[c] = Object.assign({ dict: {}, rules: [], dir: "ltr", bcp47: c }, LOC[c] || {}, CAT[c], { code: c });
  if (CAT.en) LOC.en.dict = CAT.en.dict || {};
  const ORDER = Object.keys(LOC);                 // en first, then script order of the catalog files

  let lang = DEFAULT;
  try { const s = localStorage.getItem(KEY); if (s && LOC[s]) lang = s; } catch (e) { /* storage blocked: English */ }
  const forced = new URLSearchParams(location.hash.split("?")[1] || "").get("lang");
  if (forced && LOC[forced]) lang = forced;

  const HE = /[֐-׿]/, LAT = /[A-Za-z]/, RTL_CHARS = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
  const FSI = "⁨", PDI = "⁩";
  const SCRIPT = { he: HE };   // text already in the target script is left alone (one entry per non-Latin locale)
  const compiled = {};
  function cat(code) {
    if (compiled[code]) return compiled[code];
    const L = LOC[code] || LOC.en;
    const prefix = Object.keys(L.dict).filter((k) => /[: ]$/.test(k)).sort((a, b) => b.length - a.length);
    return (compiled[code] = { dict: L.dict, prefix, rules: (L.rules || []).map(([p, to]) => [new RegExp(p), to]) });
  }
  const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  function lookup(t, code) {
    const c = cat(code);
    if (own(c.dict, t)) return c.dict[t];
    for (const [rx, to] of c.rules) if (rx.test(t)) return t.replace(rx, to);
    for (const p of c.prefix) if (t.length > p.length && t.startsWith(p)) return c.dict[p] + t.slice(p.length);
    return null;
  }
  const rtl = () => (LOC[lang] || LOC.en).dir === "rtl";
  // an English fallback inside a right-to-left page: "next →" points backwards there, so the arrows are mirrored
  // (catalog texts are written with the right arrows already; code / chips are LTR islands and never reach here)
  const flip = (s) => s.replace(/[←→]/g, (a) => (a === "→" ? "←" : "→"));
  // "English · עברית" (either order): keep the parts of the chosen language (Hebrew, else English) and neutral ones
  function pick(t) {
    if (t.indexOf(" · ") < 0 && t.indexOf("·") !== 0) return t;
    const parts = t.split(/\s*·\s*/);
    const kind = (p) => (HE.test(p) ? "he" : LAT.test(p) ? "en" : "");
    const kinds = parts.map(kind);
    if (!(kinds.includes("he") && kinds.includes("en"))) return t;
    const want = lang === "he" ? "he" : "en";
    return parts.filter((p, i) => p && (kinds[i] === want || kinds[i] === "")).join(" · ");
  }
  function tr(t, noFlip) {
    if (typeof t !== "string" || !t) return t;
    const r = pick(t);
    const exact = lang !== "en" && own(cat(lang).dict, r) ? cat(lang).dict[r] : null;
    if (exact !== null) return exact;                        // also a mixed text ("... ל/ו/ב ...") has its catalog entry
    const sc = SCRIPT[lang];
    if (sc && sc.test(r)) return r;
    if (lang !== "en" && HE.test(r)) return r;               // Hebrew-only text with no English source
    const v = lookup(r, lang);
    if (v !== null) return v;
    return rtl() && !noFlip ? flip(r) : r;
  }
  const SKIP = "code,pre,textarea,script,style,.mono,[data-noi18n],[dir=auto],.kv .v,.chip,.k,kbd,samp";
  const ATTRS = ["title", "aria-label", "placeholder", "alt"];
  const isolated = new Set();                                // elements with an isolated English text child
  // An English sentence split over several nodes (text, link, text) cannot be isolated piece by piece: the pieces
  // would line up right-to-left. When an element has no RTL letter at all, it becomes one LTR paragraph instead.
  function settle() {
    for (const p of Array.from(isolated)) {
      isolated.delete(p);
      if (!p.isConnected || p.hasAttribute("dir") || p.childNodes.length < 2 || RTL_CHARS.test(p.textContent)) continue;
      for (const c of p.childNodes) {
        if (c.nodeType === 3 && c.data.indexOf(FSI) >= 0) { c.data = c.data.split(FSI).join("").split(PDI).join(""); done.set(c, c.data); }
      }
      p.dir = "ltr";
    }
  }
  const done = new WeakMap();                                // text node -> the text we wrote (never translated twice)
  function trNode(n) {
    if (n.nodeType === 3) {
      const p = n.parentElement;
      if (!p || p.closest(SKIP) || done.get(n) === n.data) return;
      const s = n.data, t = s.trim();
      if (!t || !LAT.test(t) && t.indexOf("·") < 0) return;
      let r = tr(t);
      // English left in a right-to-left page (no catalog entry yet, server text): isolate it as one LTR run so its
      // punctuation stays at its end ("first?" not "?first"); text with any RTL letter keeps the page direction
      if (rtl() && !RTL_CHARS.test(r) && r.indexOf(FSI) < 0) { r = FSI + r + PDI; isolated.add(p); }
      if (r !== t) n.data = s.slice(0, s.indexOf(t)) + r + s.slice(s.indexOf(t) + t.length);
      done.set(n, n.data);
      return;
    }
    if (n.nodeType !== 1) return;
    // phone card labels: CSS selectors key on data-label (English), the visible label is data-label-t (also on a
    // dir=auto cell that holds user content: the label is ours)
    const dl0 = n.getAttribute("data-label");
    if (dl0) n.setAttribute("data-label-t", tr(dl0, true));
    if (n.closest(SKIP)) {
      // a dir="auto" input holds the user's words, but its placeholder is ours ("Search what was said…")
      const ph = /^(INPUT|TEXTAREA)$/.test(n.tagName) && !n.parentElement?.closest(SKIP) && n.getAttribute("placeholder");
      if (ph && LAT.test(ph)) n.setAttribute("placeholder", tr(ph.trim(), true));
      return;
    }
    for (const a of ATTRS) {
      const v = n.getAttribute(a);
      if (v && LAT.test(v)) { const r = tr(v.trim(), true); if (r !== v.trim()) n.setAttribute(a, r); }
    }
    for (const c of Array.from(n.childNodes)) trNode(c);
  }

  function applyDoc() {
    const L = LOC[lang] || LOC.en, root = document.documentElement;
    root.lang = L.code;
    root.dir = L.dir;
    for (const e of document.querySelectorAll("[data-en]")) {
      e.textContent = lang === "he" && e.dataset.he ? e.dataset.he : e.dataset[lang] || tr(e.dataset.en);
    }
  }

  const listeners = [], pickers = new Set();
  window.JL = {
    get lang() { return lang; },
    DEFAULT,
    locales: () => ORDER.map((c) => ({ code: c, name: LOC[c].name, english: LOC[c].english || LOC[c].name, dir: LOC[c].dir })),
    dir: () => (LOC[lang] || LOC.en).dir,
    rtl,
    // the base direction of one of OUR texts (server detail, search title): RTL when it has any RTL letter
    // ("Pear Desktop לא רץ" is a Hebrew sentence), else LTR. User content keeps dir="auto" instead.
    textDir: (s) => (RTL_CHARS.test(String(s || "")) ? "rtl" : "ltr"),
    locale: () => (LOC[lang] || LOC.en).bcp47,
    t: (s) => tr(s),
    translate: (n) => { trNode(n); settle(); },
    onChange: (fn) => listeners.push(fn),
    // {he: "...", en: "...", ar?: "..."} objects (guide steps, setup clicks): this locale, else the English via the catalog
    pick: (o) => (o ? o[lang] || (lang === "he" ? o.he : "") || tr(o.en) || o.he || "" : ""),
    set(l) {
      lang = LOC[l] ? l : DEFAULT;
      try { localStorage.setItem(KEY, lang); } catch (e) { /* ignore */ }
      applyDoc();
      for (const s of Array.from(pickers)) {
        if (!s.isConnected) { pickers.delete(s); continue; }      // pages re-render: forget the old pickers
        s.value = lang; s.setAttribute("aria-label", tx("שפה", "Language") + " / Language");
      }
      for (const fn of listeners) try { fn(lang); } catch (e) { /* a listener must not break the switch */ }
    },
    // the language picker: a native <select> (keyboard, screen readers and phones get the platform control)
    picker(cls) {
      const s = document.createElement("select");
      s.className = cls || "lang-pick";
      s.setAttribute("data-noi18n", "");
      s.setAttribute("aria-label", tx("שפה", "Language") + " / Language");
      for (const x of window.JL.locales()) {
        const o = document.createElement("option");
        o.value = x.code; o.textContent = x.name; o.lang = x.code; o.dir = x.dir;
        if (x.code === lang) o.selected = true;
        s.append(o);
      }
      s.addEventListener("change", () => window.JL.set(s.value));
      pickers.add(s);
      return s;
    },
  };
  // Hebrew written inline (he), English source (en): the English is also the catalog key for every other locale
  window.tx = (he, en) => (lang === "he" ? (he || en) : lang === "en" ? (en || he) : en ? tr(en) : he);
  // Server text with locale siblings (<key>_he, later <key>_ar ...): this locale's sibling, else the English
  // <key> (or <key>_en) through the client catalog. Only for our own fixed texts; user content, logs and code are
  // never given a sibling.
  function srv(o, k, en) {
    if (!o) return "";
    const mine = o[k + "_" + lang];
    if (mine) return mine;
    if (lang === "he") return o[k + "_he"] || (en ? tr(en, true) : "");      // no server sibling: the client catalog
    return (en ? (lang === "en" ? en : tr(en)) : "") || o[k + "_he"] || "";
  }
  // <key>_en is the English DISPLAY form when the source holds a model-facing Hebrew hint (D-11), so it wins
  window.txh = (o, k) => srv(o, k, o && (o[k + "_en"] || o[k]));
  window.txo = (o, k) => srv(o, k, o && o[k + "_en"]);

  applyDoc();
  function start() {
    applyDoc();
    trNode(document.body);
    settle();
    new MutationObserver((ms) => {
      for (const m of ms) for (const n of m.addedNodes) trNode(n);
      settle();
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
