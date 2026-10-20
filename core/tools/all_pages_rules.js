// SW-V4 K (issue #17): DOM rules for tools/all_pages_check.py, evaluated in the real browser on every page.
// Simple, local, axe-style checks (no external service, no axe download): accessible names, alt text, duplicate
// ids, positive tabindex, focusable content inside aria-hidden, contrast AA, mouse-only controls, phone target
// size, link names that repeat for different targets, lang/dir, direction of Hebrew/English blocks, elements that
// stick out of the viewport, leftover "Loading..." / "undefined" texts, and the translation inventory (English
// multi-word strings on a Hebrew page, Hebrew on an English page; logs, code and user content excluded).
// Called as  page.evaluate(RULES, {lang, width, reduced})  -> {issues: [{rule, sev, msg, where}], stats: {...}}
(opts) => {
  const out = [], stats = {};
  const lang = opts.lang, rtlPage = lang === "he";
  const HE = /[֐-׿]/, LAT = /[A-Za-z]/;
  const add = (rule, sev, msg, node) => out.push({ rule, sev, msg: String(msg).slice(0, 300), where: where(node) });
  function where(n) {
    if (!n || n.nodeType !== 1) return "";
    const parts = [];
    for (let e = n; e && e.nodeType === 1 && parts.length < 4; e = e.parentElement) {
      let s = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(s + "#" + e.id); break; }
      if (e.classList.length) s += "." + Array.from(e.classList).slice(0, 2).join(".");
      parts.unshift(s);
    }
    return parts.join(">");
  }
  const visible = (e) => {
    if (!e || !e.getClientRects().length) return false;
    const cs = getComputedStyle(e);
    if (cs.visibility === "hidden" || cs.display === "none" || parseFloat(cs.opacity) === 0) return false;
    return !e.closest("[hidden],[aria-hidden=true]");      // the closed phone drawer is visibility: hidden (inherited)
  };

  // ------------------------------------------------------------------ document
  const root = document.documentElement;
  if (root.lang !== lang) add("html-lang", "serious", `html lang=${root.lang}, want ${lang}`, root);
  if (root.dir !== (rtlPage ? "rtl" : "ltr")) add("html-dir", "serious", `html dir=${root.dir}`, root);
  const main = document.getElementById("main");
  if (main && !main.querySelector("h1")) add("page-has-heading-one", "moderate", "no h1 in main", main);

  // ------------------------------------------------------------------ ids, tabindex, aria-hidden
  const ids = {};
  for (const e of document.querySelectorAll("[id]")) (ids[e.id] = ids[e.id] || []).push(e);
  for (const [id, list] of Object.entries(ids)) if (list.length > 1) add("duplicate-id", "serious", `id "${id}" x${list.length}`, list[1]);
  for (const e of document.querySelectorAll("[tabindex]")) if (+e.getAttribute("tabindex") > 0) add("tabindex", "serious", "positive tabindex", e);
  const FOCUSABLE = "a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex='-1'])";
  for (const h of document.querySelectorAll("[aria-hidden=true]"))
    for (const f of h.querySelectorAll(FOCUSABLE)) if (f.getClientRects().length) add("aria-hidden-focus", "serious", "focusable inside aria-hidden", f);

  // ------------------------------------------------------------------ accessible names
  function name(e) {
    const al = (e.getAttribute("aria-label") || "").trim();
    if (al) return al;
    const lb = e.getAttribute("aria-labelledby");
    if (lb) { const t = lb.split(/\s+/).map((i) => document.getElementById(i)?.innerText || "").join(" ").trim(); if (t) return t; }
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.tagName)) {
      if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l && l.innerText.trim()) return l.innerText.trim(); }
      const wrap = e.closest("label"); if (wrap && wrap.innerText.trim()) return wrap.innerText.trim();
      if (/^(button|submit|reset)$/.test(e.type) && e.value) return e.value;
      if (e.placeholder) return e.placeholder;
      return (e.getAttribute("title") || "").trim();
    }
    const t = (e.innerText || e.textContent || "").trim();
    if (t) return t;
    const img = e.querySelector("img[alt]"); if (img && img.alt.trim()) return img.alt.trim();
    const svgT = e.querySelector("svg title"); if (svgT && svgT.textContent.trim()) return svgT.textContent.trim();
    return (e.getAttribute("title") || "").trim();
  }
  const controls = Array.from(document.querySelectorAll("a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=switch],[role=tab]")).filter(visible);
  stats.controls = controls.length;
  for (const c of controls) if (!name(c)) add(/^(INPUT|SELECT|TEXTAREA)$/.test(c.tagName) ? "label" : "control-name", "critical", `${c.tagName.toLowerCase()} has no accessible name`, c);
  for (const i of document.querySelectorAll("img")) if (visible(i) && !i.hasAttribute("alt")) add("image-alt", "critical", "img without alt", i);
  // icon-only buttons: one or two symbol characters with no aria-label are read as "x" / "check mark"
  for (const c of controls) {
    if (c.tagName !== "BUTTON" && c.getAttribute("role") !== "button") continue;
    const t = (c.innerText || "").trim();
    if (t && t.length <= 2 && !/[\p{L}\p{N}]/u.test(t) && !c.getAttribute("aria-label") && !c.getAttribute("title"))
      add("icon-button-name", "serious", `icon button "${t}" without aria-label`, c);
  }
  // links with the same name that go to different places (D-14: 54 links all called "conversation")
  const byName = {};
  for (const a of controls) if (a.tagName === "A" && a.closest("#main")) {
    const n = name(a).toLowerCase(); (byName[n] = byName[n] || new Set()).add(a.getAttribute("href"));
  }
  for (const [n, hrefs] of Object.entries(byName)) if (hrefs.size > 2) add("identical-links", "moderate", `${hrefs.size} links named "${n}" go to different places`, main);

  // ------------------------------------------------------------------ mouse-only controls, target size
  const NATIVE = /^(A|BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY|LABEL|OPTION|DETAILS)$/;
  for (const e of document.querySelectorAll("#main *, .appbar *")) {
    if (NATIVE.test(e.tagName) || e.hasAttribute("tabindex") || !visible(e)) continue;
    if (getComputedStyle(e).cursor !== "pointer") continue;
    if (e.parentElement && getComputedStyle(e.parentElement).cursor === "pointer") continue;   // the parent is reported
    if (e.closest("a,button,label,summary,[tabindex]")) continue;
    add("mouse-only", "serious", `clickable ${e.tagName.toLowerCase()} not reachable by keyboard`, e);
  }
  if (opts.width <= 480) for (const c of controls) {
    if (!/^(BUTTON|SELECT|INPUT)$/.test(c.tagName) && c.getAttribute("role") !== "button") continue;
    if (c.type === "checkbox" || c.type === "radio") continue;
    const r = c.getBoundingClientRect();
    if (r.width < 24 || r.height < 24) add("target-size", "serious", `target ${Math.round(r.width)}x${Math.round(r.height)} px`, c);
  }

  // ------------------------------------------------------------------ contrast AA
  const parse = (s) => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const over = (top, bot) => { const a = top[3]; return [top[0] * a + bot[0] * (1 - a), top[1] * a + bot[1] * (1 - a), top[2] * a + bot[2] * (1 - a), 1]; };
  function bgOf(e) {
    const layers = [];
    for (let x = e; x; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.backgroundImage && cs.backgroundImage !== "none") return null;          // gradient / image: unknown
      const c = parse(cs.backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let bg = [255, 255, 255, 1];
    for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
    return bg;
  }
  const seen = new Set();
  let checked = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const e = t.parentElement;
    if (!e || !t.data.trim() || seen.has(e) || !visible(e)) continue;
    seen.add(e);
    if (e.closest("button:disabled,[disabled],[aria-disabled=true],option,.chart,svg")) continue;   // WCAG: inactive controls are exempt
    const cs = getComputedStyle(e);
    let fg = parse(cs.color); if (!fg) continue;
    let op = 1; for (let x = e; x; x = x.parentElement) op *= parseFloat(getComputedStyle(x).opacity);
    const bg = bgOf(e); if (!bg) continue;
    fg = over([fg[0], fg[1], fg[2], fg[3] * op], bg);
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const px = parseFloat(cs.fontSize), bold = +cs.fontWeight >= 700;
    const need = px >= 24 || (bold && px >= 18.66) ? 3 : 4.5;
    checked++;
    if (ratio < need - 0.01) add("color-contrast", "serious", `${ratio.toFixed(2)}:1 < ${need} "${t.data.trim().slice(0, 40)}" ${cs.color} on rgb(${bg.slice(0, 3).map(Math.round)})`, e);
  }
  stats.contrastChecked = checked;

  // ------------------------------------------------------------------ direction of text blocks
  const BLOCK = /^(block|flex|grid|list-item|table-cell|inline-block|inline-flex)$/;
  for (const e of document.querySelectorAll("#main *")) {
    if (!visible(e) || !BLOCK.test(getComputedStyle(e).display)) continue;
    if (e.closest("code,pre,.mono,textarea,.chip,.chart,.xlabels,.num,kbd,samp,input,select,table.log,[data-noi18n],.kv .v,.kv .k")) continue;
    let own = ""; for (const c of e.childNodes) if (c.nodeType === 3) own += c.data;
    own = own.trim(); if (own.length < 3) continue;
    const cs = getComputedStyle(e);
    if (rtlPage && HE.test(own) && !LAT.test(own.slice(0, 1)) && cs.direction === "ltr" && !e.closest("[dir=auto]"))
      add("direction", "serious", `Hebrew text laid out left-to-right: "${own.slice(0, 40)}"`, e);
    if (rtlPage && HE.test(own) && cs.textAlign === "left" && cs.direction === "rtl")
      add("direction", "moderate", `Hebrew text aligned left: "${own.slice(0, 40)}"`, e);
    if (!rtlPage && !HE.test(own) && cs.direction === "rtl")
      add("direction", "serious", `English text laid out right-to-left: "${own.slice(0, 40)}"`, e);
  }

  // ------------------------------------------------------------------ elements outside the viewport (no scroller)
  const W = innerWidth;
  const scrolls = (x) => { const o = getComputedStyle(x).overflowX; return o === "auto" || o === "scroll"; };
  let clipped = 0;
  for (const e of document.querySelectorAll("#main *, .appbar *")) {
    if (clipped > 12) break;
    if (!visible(e)) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 2 || (r.right <= W + 1 && r.left >= -1)) continue;
    let ok = false;
    for (let x = e.parentElement; x && x !== document.body; x = x.parentElement) if (scrolls(x)) { ok = true; break; }
    if (ok || getComputedStyle(e).position === "fixed") continue;
    const p = e.parentElement; if (p) { const pr = p.getBoundingClientRect(); if (pr.right > W + 1 || pr.left < -1) continue; }   // report the outermost
    clipped++;
    add("off-screen", "serious", `sticks out of the ${W}px viewport (${Math.round(r.left)}..${Math.round(r.right)})`, e);
  }
  // horizontal scrollers with no visible cue are flagged where they hide content (D-17 header chips)
  for (const s of document.querySelectorAll(".appbar *")) {
    if (!visible(s) || !scrolls(s) || s.scrollWidth <= s.clientWidth + 2) continue;
    if (!s.classList.contains("fade") && !s.dataset.fade) add("hidden-scroll", "moderate", `content scrolls sideways with no cue (${s.scrollWidth}>${s.clientWidth})`, s);
  }

  // ------------------------------------------------------------------ leftover texts
  const mainText = main ? main.innerText : "";
  if (/^\s*(Loading…|Loading\.\.\.|טוען…)\s*$/.test(mainText)) add("loading-stuck", "serious", "page still says Loading", main);
  const bad = mainText.match(/\b(undefined|NaN|\[object Object\])\b|\bnull\b(?![-_])/);
  if (bad) add("bad-text", "serious", `"${bad[0]}" shown to the user`, main);

  // ------------------------------------------------------------------ translation inventory
  const SKIP = "code,pre,textarea,script,style,.mono,[data-noi18n],[dir=auto],kbd,samp,.chip,table.log,.kv .v,.kv .k,svg,option,.log-msg,[data-user],.ap-prompt,.chart,.xlabels";
  // product, service and technical names stay English in Hebrew text (glossary decision, docs/reviews/SWV4_K_DASHBOARD.md)
  const KEEP = /\b(JARVES|Jarves|JARVIS|Google(?: Calendar| Contacts| Cloud| Console)?|Gmail|Telegram|Instagram|WhatsApp|Pear(?: Desktop)?|OpenAI|ChatGPT|GPT[-\w.]*|gpt[-\w.]*|Claude(?: Code)?|Codex|Home Assistant|Windows(?: Hello)?|Raspberry Pi|Pi \d|Pi|Edge|Chrome|Chromium|YouTube(?: Music)?|Spotify|Wi-?Fi|Bluetooth|USB|LAN|API|CLI|URL|PDF|OAuth|Playwright|Godot|Phone Link|OpenCV|Whisper|Vosk|Piper|Porcupine|openWakeWord|Silero|WinRT|SAPI|PortAudio|JSON|TOML|SQLite|PIN|AI|OK|SMS|Wake|hey jarves|Hey Jarves|Realtime|Live|Opus|Sonnet|Haiku|Fable|Microsoft|Apple|Samsung|Galaxy|iPhone|Android|Linux|macOS|Python|Node|Go|Git|GitHub|PowerShell|Pebble|Inno Setup|Oref|Pikud HaOref|DPAPI|TTS|STT|VAD|Ollama|Llama|Gemini|Tavily|Brave|Bing|DuckDuckGo|Vesper|Marin|Cedar|Alloy|Ash|Ballad|Coral|Echo|Sage|Shimmer|Verse|MCP|SDK|CPU|GPU|RAM|NPU|HTTP|HTTPS|IP|DNS|UTC|ID|IDs|UI|CSV|OK)\b/g;
  // a Hebrew sentence may name an English UI label in quotes or brackets ("Create client", (Desktop app)): that is
  // deliberate; a run of 3+ English words outside them is a half translation. A text with no Hebrew: 2+ words.
  function englishRun(s) {
    const mixed = HE.test(s);
    if (mixed) s = s.replace(/"[^"]*"|“[^”]*”|'[^']*'|`[^`]*`|\([^)]*\)|«[^»]*»/g, " ");
    s = s.replace(/https?:\/\/\S+|\S+@\S+|\S*[\\/]\S*|\S*[_=<>{}$#]\S*|\b\w+\.\w+\S*|\S*\d\S*/g, " ").replace(KEEP, " ");
    const m = s.match(mixed ? /[A-Za-z][a-z'’]+(?:[ \-–,]+[A-Za-z][a-z'’]+){2,}/ : /[A-Za-z][a-z'’]+(?:[ \-–,]+[A-Za-z][a-z'’]+)+/);
    return m ? m[0] : null;
  }
  const leftovers = new Map(), hebrewOnEn = new Map();
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let t = tw.nextNode(); t; t = tw.nextNode()) {
    const e = t.parentElement, s = t.data.trim();
    if (!e || !s || e.closest(SKIP) || !visible(e)) continue;
    if (rtlPage) { const r = englishRun(s); if (r) leftovers.set(s.slice(0, 400), where(e)); }
    else if (HE.test(s)) hebrewOnEn.set(s.slice(0, 400), where(e));
  }
  for (const e of document.querySelectorAll("[aria-label],[title],[placeholder]")) {
    if (e.closest(SKIP) && !/^(INPUT|TEXTAREA)$/.test(e.tagName)) continue;
    if (!e.getClientRects().length) continue;
    for (const a of ["aria-label", "title", "placeholder"]) {
      const v = (e.getAttribute(a) || "").trim(); if (!v) continue;
      if (rtlPage) { if (englishRun(v)) leftovers.set(`[${a}] ${v.slice(0, 300)}`, where(e)); }
      else if (HE.test(v) && !/ \/ Language$/.test(v)) hebrewOnEn.set(`[${a}] ${v.slice(0, 300)}`, where(e));
    }
  }
  for (const [s, w] of leftovers) out.push({ rule: "untranslated", sev: "serious", msg: s, where: w });
  for (const [s, w] of hebrewOnEn) out.push({ rule: "hebrew-on-english", sev: "serious", msg: s, where: w });
  stats.untranslated = leftovers.size; stats.hebrewOnEnglish = hebrewOnEn.size;

  // ------------------------------------------------------------------ reduced motion
  if (opts.reduced && document.getAnimations) {
    for (const a of document.getAnimations()) {
      const t = a.effect && a.effect.getTiming ? a.effect.getTiming() : {};
      if (a.playState === "running" && (t.iterations === Infinity || (t.duration || 0) > 300))
        add("reduced-motion", "serious", `animation runs although reduced motion is set (${a.animationName || a.constructor.name})`, a.effect && a.effect.target);
    }
  }
  stats.issues = out.length;
  return { issues: out, stats };
}
