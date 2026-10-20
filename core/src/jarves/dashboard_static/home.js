// JARVES dashboard, SW-V2 WP-K: Home (simple overview), Connections (Google, step by step) and the privacy switch
// in the top bar. SW-V3 P0.2: one language at a time (tx(he, en), i18n.js), Hebrew first. Text only via textContent.
"use strict";

const bi = (he, en, cls) => el("span", { class: cls || "", dir: JL.dir() }, tx(he, en));
const two = (o, k) => txo(o, k);                                   // {k_he, k_en} from the API
const lampOf = (s) => ({ ok: "ok", warn: "warn", off: "off", bad: "bad", unknown: "off" }[s] || "off");
const pillOf = (s, text) => pill({ ok: "ok", warn: "medium", off: "plain", unknown: "plain", fail: "fail" }[s] || "plain", text);
async function post(path, body) { return api(path, { method: "POST", body: JSON.stringify(body || {}) }); }

// ---------------------------------------------------------------- privacy (top bar + Home tile)
let privacyState = null;
async function togglePrivacy(msg) {
  const to = !privacyState.privacy;
  try {
    privacyState = await post("/api/privacy", { privacy: to });
    if (msg) msg.textContent = privacyState.saved_in_config ? tx("נשמר", "Saved")
      : tx("שונה עכשיו; config.toml לא נשמר: ", "Changed now; config.toml not saved: ") + privacyState.config_error;
  } catch (e) { if (msg) msg.textContent = tx("לא שונה: ", "Not changed: ") + e.message; }
  await renderTopbar();
  refreshOverview();
  if ((location.hash || "#/home").startsWith("#/home")) route(true);
}
function privacyButton(msg, compact) {
  const on = privacyState.privacy;
  return el("button", { type: "button", class: "switch priv-btn", id: compact ? "privacyBtn" : null, "aria-pressed": on ? "true" : "false",
    title: on ? tx("מה שנאמר לא נשמר. לחיצה: לשמור טקסט מלא (לפיתוח)", "What is said is not kept. Click to keep full text (development)")
      : tx("הטקסט המלא נשמר (לפיתוח). לחיצה: להפסיק לשמור", "Full text is kept (development). Click to stop keeping what is said"),
    onclick: () => togglePrivacy(msg) },
  compact ? tx(on ? "פרטיות: פועלת" : "פרטיות: כבויה", on ? "Privacy: on" : "Privacy: off")
    : tx(on ? "פרטיות פועלת" : "פרטיות כבויה", on ? "Privacy ON" : "Privacy OFF"));
}
async function renderTopbar() {
  const bar = document.getElementById("topbar");
  try { privacyState = await api("/api/privacy"); } catch (e) { bar.replaceChildren(); return; }
  const msg = el("span", { class: "muted small" });
  // SW-V4 B: development mode is said plainly on every page (the server sends the text, empty when privacy is on)
  const banner = privacyState.privacy ? null : el("span", { class: "priv-banner small", role: "status", id: "privacyBanner" },
    tx(privacyState.banner_he, privacyState.banner_en));
  bar.replaceChildren(el("span", { class: "priv" }, privacyButton(msg, true)), ...(banner ? [banner] : []), msg);
}
function privacyCard(p) {
  const msg = el("div", { class: "muted small" });
  const oneWay = (label, path, question) => el("button", { type: "button", class: "danger", onclick: async () => {
    msg.textContent = tx("ממתין לאישור על המסך שלך", "waiting for approval on your screen");     // the server asks on the popup
    try {
      const r = await post(path, { confirm: true });
      msg.textContent = tx("בוצע", "Done") + (r.deleted !== undefined ? ` (${r.deleted} ${tx("שיחות", "conversations")})` : ` (${r.turns} ${tx("שורות", "lines")})`);
      route(true);
    } catch (e) { msg.textContent = tx("לא בוצע: ", "Not done: ") + e.message; }
  } }, label);
  return el("div", { class: "card", id: "privacy" }, el("h2", {}, tx("פרטיות", "Privacy")),
    el("div", { class: "big-status" }, el("span", { class: "lamp " + (p.privacy ? "ok" : "warn") }),
      el("div", {}, el("div", { class: "title" }, tx(p.label_he, p.label_en)))),
    el("div", { class: "small" }, tx(p.explain_he, p.explain_en)),
    el("div", { class: "actions" }, privacyButton(msg),
      oneWay(tx("הסתרת ההיסטוריה", "Mask existing history"), "/api/privacy/mask-history",
        tx("למחוק את המילים מכל השיחות והיומנים השמורים? אורכים, זמנים ועלויות נשארים.",
          "Remove the words from ALL saved conversations and log files? Lengths, times and costs stay.")),
      oneWay(tx("מחיקת כל השיחות השמורות", "Delete all stored conversations"), "/api/privacy/delete-conversations",
        tx("למחוק את כל השיחות השמורות (העלויות נשארות)?", "Delete ALL stored conversations (costs stay)?"))), msg);
}

// ---------------------------------------------------------------- Home
PAGES.home = async function () {
  const d = await api("/api/home");
  privacyState = d.privacy;
  const lc = d.last_conversation;
  const okConn = d.connections.filter((c) => c.state === "ok").length;
  const tiles = el("div", { class: "tiles" },
    tile(tx("מחובר", "Connected"), `${okConn} / ${d.connections.length}`,
      d.connections.filter((c) => c.state !== "ok").map((c) => `${two(c, "title").split(" (")[0]}: ${two(c, "detail")}`).join(" · ") || tx("הכל", "everything")),
    tile(tx("יכולות פועלות", "Features on"), String(d.features.on.length), tx(`${d.features.off.length} כבויות או לא זמינות`, `${d.features.off.length} off or not available`)),
    tile(tx("עלות היום", "Today's cost"), usd(d.today.cost), tx(`${d.today.conversations} שיחות · ${d.today.voice_minutes} דק׳ קול`,
      `${d.today.conversations} conversations · ${d.today.voice_minutes} voice min`), true),
    tile(tx("בעיות פתוחות", "Open issues"), String(d.issues.open), d.issues.high ? tx(`${d.issues.high} חשובות`, `${d.issues.high} important`) : tx("אין חשובות", "none important")),
    tile(tx("שיחה אחרונה", "Last talk"), lc ? clock(lc.started).slice(0, 5) : "—",
      lc ? `${day(lc.started)} · ${secs(lc.duration_s)} · ` + tx(`${lc.user_turns} דברים שאמרת${lc.typed ? " (בהקלדה)" : ""}`,
        `${lc.user_turns} things you said${lc.typed ? " (typed)" : ""}`) : tx("עוד אין שיחות", "no conversations yet")));
  const next = el("div", { class: "card next" }, el("span", { class: "label" }, tx("מה עכשיו:", "What to do next:")),
    el("span", {}, tx(d.next.he, d.next.en)), el("span", { class: "spacer" }),
    d.next.link ? el("a", { class: "button-link", href: d.next.link }, tx("קדימה ←", "Go →")) : null);
  const conns = el("div", { class: "card" }, el("h2", {}, tx("חיבורים", "Connections")),
    d.connections.map((c) => el("div", { class: "conn" }, pillOf(c.state, c.state === "ok" ? tx("תקין", "OK") : c.state === "warn" ? tx("לבדוק", "check") : tx("כבוי", "off")),
      el("div", {}, el("b", {}, two(c, "title")),
        el("div", { class: "small" }, two(c, "detail"),
          c.days_left !== null && c.days_left !== undefined ? el("span", { class: "muted" }, tx(` · חיבור מחדש בעוד כ-${c.days_left} ימים`, ` · reconnect in ~${c.days_left} days`)) : null)),
      el("span", { class: "spacer" }), el("a", { href: c.link }, tx("פתיחה ←", "Open →")))));
  const feats = el("div", { class: "card" }, el("h2", {}, tx("מה פועל", "What is on")),
    el("div", { class: "chips" }, d.features.on.map((f) => el("span", { class: "chip-on" }, two(f, "title")))),
    el("div", { class: "small muted", style: "margin-top:10px" }, tx("כבוי או לא זמין", "Off or not available")),
    el("div", { class: "chips" }, d.features.off.map((f) => el("span", { class: "chip-off", title: txh(f, "reason"), dir: "auto" }, two(f, "title")))),
    el("div", { class: "actions" }, el("a", { class: "button-link", href: "#/features" }, tx("שינוי", "Change"))));
  const last = lc ? el("div", { class: "card" }, el("h2", {}, tx("שיחה אחרונה", "Last conversation")),
    el("div", { dir: "auto" }, lc.first_user || tx("(לא נאמר כלום)", "(nothing said)")),
    el("div", { class: "muted small" }, `${day(lc.started)} ${clock(lc.started)} · ${secs(lc.duration_s)} · ${usd(lc.cost)}` + (lc.issues ? tx(` · ${lc.issues} בעיות`, ` · ${lc.issues} issue(s)`) : "")),
    el("div", { class: "actions" }, el("a", { class: "button-link", href: "#/session/" + encodeURIComponent(lc.id) }, tx("פתיחה", "Open")))) : null;
  const setup = typeof window.guidesSetupCard === "function" ? await window.guidesSetupCard() : null;   // WP-L checklist
  const strip = typeof window.privacyStripCard === "function" ? window.privacyStripCard() : null;     // strip.js
  render(el("div", { class: "big-status hero" }, el("span", { class: "lamp " + lampOf(d.status) }),
    el("div", {}, el("h1", { class: "title" }, tx(d.status_he, d.status_en)),
      el("div", { class: "muted" }, d.live ? tx("מדבר עכשיו", "talking now") : tx("סקירה של JARVES במבט אחד", "JARVES at a glance")))),
    strip, next, setup, tiles, el("div", { class: "grid-home" }, conns, feats), el("div", { class: "grid-home" }, privacyCard(d.privacy), last));
  timer = setInterval(() => route(true), 15000);
};

// ---------------------------------------------------------------- Connections (Google)
const CHECK = { ok: ["ok", "עובד", "works"], fail: ["fail", "נכשל", "failed"], not_run: ["plain", "עוד לא נבדק", "not checked yet"],
  skipped: ["plain", "לא בשימוש", "not used"] };
const checkPill = (k) => { const c = CHECK[k] || CHECK.not_run; return pill(c[0], tx(c[1], c[2])); };
PAGES.connections = async function () {
  const g = await api("/api/google/setup");
  const cn = await api("/api/connect");                      // the same checklist as `jarvis connect --status`
  const cmsg = el("div", { class: "small", dir: "auto" });
  const guided = el("div", { class: "card", id: "guided-setup" }, el("h2", {}, tx("הגדרה מודרכת של כל החשבונות", "Guided setup of every account")),
    el("div", { class: "muted small" }, tx("אותה בדיקה כמו בפקודה ", "The same check as the command "), el("span", { class: "mono" }, cn.command + " --status")),
    el("div", { class: "checks" }, cn.items.map((i) => [
      el("div", {}, tx(i.title_he, i.title_en)),
      el("div", {}, i.state === "connected" ? pill("ok", tx("מחובר", "connected")) : i.state === "attention" ? pill("medium", tx("דורש טיפול", "needs attention"))
        : pill("plain", tx("חסר", "missing")), " ", el("span", { class: "muted small" }, tx(i.detail_he, i.detail_en)),
        i.state !== "connected" && i.fix_en ? el("div", { class: "small" }, tx(i.fix_he, i.fix_en)) : null)])),
    el("div", { class: "actions" }, el("button", { type: "button", class: "primary", onclick: async () => {
      try { const r = await post("/api/connect/run"); cmsg.textContent = tx(r.message_he, r.message_en); }
      catch (e) { cmsg.textContent = e.message; }
    } }, tx("הפעלת הגדרה מודרכת", "Run guided setup")), cmsg));
  const msg = el("div", { class: "small", dir: "auto" });
  const act = async (fn) => { try { await fn(); } catch (e) { msg.textContent = e.message; } };
  const start = el("button", { type: "button", class: "primary big", disabled: g.login.running || null, onclick: () => act(async () => {
    try { await post("/api/google/start"); msg.textContent = tx("נפתח דפדפן: בוחרים חשבון, Continue, מסמנים הכל.", "A browser window opens: pick your account, Continue, tick every box."); }
    catch (e) { msg.textContent = e.message + " · " + tx("עקבו אחרי השלבים למטה, או הריצו: ", "Follow the steps below, or run: ") + g.start_command; }
    setTimeout(() => route(true), 1500);
  }) }, g.login.running ? tx("מחכה ל-Google…", "Waiting for Google…") : g.state === "needs_reconnect" ? tx("חיבור מחדש", "Reconnect")
    : g.signed_in ? tx("התחברות שוב", "Sign in again") : g.app_client ? tx("חיבור Google", "Connect Google") : tx("התחלה", "Start"));
  const test = el("button", { type: "button", disabled: !g.signed_in || null, onclick: () => act(async () => {
    msg.textContent = tx("בודק…", "Checking…");
    const r = await post("/api/google/test");
    msg.textContent = r.ok ? tx("עובד: היומן והמייל עונים.", "Works: calendar and mail answer.") : (r.fix ? tx(r.fix.he, r.fix.en) : tx("נכשל", "Failed"));
    route(true);
  }) }, tx("בדיקה", "Test"));
  const disc = el("button", { type: "button", class: "danger", disabled: !(g.signed_in || g.needs_reconnect) || null, onclick: () => act(async () => {
    msg.textContent = tx("ממתין לאישור על המסך שלך", "waiting for approval on your screen");     // the server asks on the popup
    await post("/api/google/disconnect"); msg.textContent = tx("נותק", "Disconnected"); route(true);
  }) }, tx("ניתוק", "Disconnect"));
  const row = (label, value) => [el("div", {}, label), el("div", {}, value)];
  const yes = (v, a, b) => v ? pill("ok", a) : pill("plain", b);
  const checks = el("div", { class: "checks" },
    g.app_client ? [] : row(tx("קובץ מפתח מ-Google (client_secret…json)", "Key file from Google (client_secret…json)"), yes(g.client_configured, tx("קיים", "here"), tx("חסר", "missing"))),
    row(tx("מחובר", "Signed in"), g.signed_in ? pill("ok", tx("כן", "yes")) : g.needs_reconnect ? pill("medium", tx("פג תוקף: להתחבר מחדש", "expired: reconnect")) : pill("plain", tx("לא", "no"))),
    row(tx("בדיקת יומן", "Calendar check"), checkPill(g.check.calendar)),
    row(tx("בדיקת מייל", "Mail check"), checkPill(g.check.gmail)),
    row(tx("שליחת מיילים", "Sending mail"), g.gmail_send ? el("a", { href: "#/features" }, tx("Google מאפשר טיוטות; המתג נמצא ביכולות (כבוי כברירת מחדל)", "Google allows drafts; the on/off switch is on Features (off by default)"))
      : el("span", { class: "muted" }, tx("לא התבקש", "not asked for"))),
    g.app_client ? [] : row(tx("חיבור מחדש שבועי", "Weekly reconnect"), g.testing_days_left !== null ? tx(`בעוד כ-${g.testing_days_left} ימים (מצב Testing)`, `in about ${g.testing_days_left} days (Testing mode)`) : "—"),
    row(tx("בעיה אחרונה", "Last problem"), g.last_error ? el("span", { class: "mono" }, g.last_error) : el("span", { class: "muted" }, tx("אין", "none"))));
  const fix = g.fix ? el("div", { class: "card fix" }, el("b", {}, tx("איך מתקנים", "How to fix")), el("div", {}, tx(g.fix.he, g.fix.en)),
    g.fix.url ? el("div", { class: "actions" }, el("a", { class: "button-link", href: g.fix.url, target: "_blank", rel: "noopener noreferrer" }, tx("פתיחת העמוד ב-Google Cloud ←", "Open the page in Google Cloud →"))) : null) : null;
  const steps = el("ol", { class: "steps" }, g.steps.filter((s) => !(g.calendar_only && s.gmail)).map((s, i) => {
    const smsg = el("span", { class: "muted small" });
    const clicks = (s["clicks_" + JL.lang] || (JL.lang === "he" && s.clicks_he) || (s.clicks_en || []).map((c) => JL.t(c)));
    return el("li", { class: "step" + (s.done ? " done" : "") }, el("span", { class: "num" }, s.done ? "✓" : String(i + 1)),
      el("div", {}, el("b", {}, txo(s, "title")),
        s.done ? null : el("ul", {}, clicks.map((c) => el("li", {}, c))),
        el("div", { class: "actions" },
          el("a", { class: "button-link", href: s.url, target: "_blank", rel: "noopener noreferrer" }, tx("פתיחה ב-Google Cloud ←", "Open in Google Cloud →")),
          s.copy ? el("button", { type: "button", onclick: async () => { try { await navigator.clipboard.writeText(s.copy); smsg.textContent = tx("הועתק", "Copied"); } catch (e) { smsg.textContent = tx("העתקה: ", "Copy: ") + s.copy; } } },
            s.id === "scopes" ? tx("העתקת הרשאות", "Copy permissions") : tx("העתקת “", "Copy “") + s.copy + "”") : null,
          s.id === "audience" ? el("span", { class: "muted small" }, tx("הדביקו את כתובת ה-Gmail שלכם", "paste your own Gmail address")) : null,
          el("button", { type: "button", class: "ghost", onclick: () => act(async () => { await post("/api/google/step/" + s.id, { done: !s.done }); route(true); }) },
            s.done ? tx("סימון כלא בוצע", "Mark not done") : tx("סיימתי", "Mark done")), smsg)));
  }));
  const stateText = { connected: ["מחובר", "connected"], needs_reconnect: ["צריך לחבר מחדש", "needs reconnect"], ready_to_sign_in: ["מוכן להתחברות", "ready to sign in"] }[g.state] || ["לא מחובר", "not connected"];
  render(header(tx("חיבורים", "Connections"), tx("מחברים את JARVES ליומן ולמייל שלך ב-Google.", "Connect JARVES to your Google Calendar and Gmail.")),
    el("div", { class: "big-status" }, el("span", { class: "lamp " + ({ connected: "ok", needs_reconnect: "warn" }[g.state] || "off") }),
      el("div", {}, el("div", { class: "title" }, "Google: " + tx(stateText[0], stateText[1])),
        el("div", { class: "muted small" }, g.scopes.length ? tx("מותר: ", "Allowed: ") + g.scopes.join(", ")
          : g.needs_reconnect ? tx("אין צורך להגדיר שוב: רק להתחבר עוד פעם.", "Nothing to set up again: just sign in once more.") : tx("עוד אין הרשאות", "No permissions yet")))),
    el("div", { class: "card next" }, el("span", { class: "label" }, tx("מה עכשיו:", "What to do next:")), el("span", {}, tx(g.next_he, g.next_en))),
    el("div", { class: "grid-home" },
      el("div", { class: "card" }, el("h2", {}, tx("מצב", "Status")), checks, el("div", { class: "actions" }, start, test, disc), msg),
      el("div", { class: "card" }, el("h2", {}, tx("מה זה?", "What is this?")), el("div", {}, tx(g.explain_he, g.explain_en)),
        g.app_client ? null : el("div", { class: "small", style: "margin-top:10px" }, tx("הכי פשוט: במחשב הזה מריצים ", "Easiest: on this computer run "), el("span", { class: "mono" }, g.start_command),
          tx(". הפקודה פותחת כל עמוד, מעתיקה מה להדביק, מוצאת לבד את הקובץ שהורד, מתחברת ובודקת הכל.",
            ". It opens each page, copies what to paste, picks up the downloaded file by itself, signs in and checks everything."),
          g.gcloud_installed ? tx(" (gcloud מותקן: הוא יכול גם ליצור את הפרויקט.)", " (gcloud is installed: it can also create the project for you.)") : ""))),
    guided,
    fix,
    g.app_client ? null : el("details", { class: "card", open: !g.steps.every((s) => s.done || (g.calendar_only && s.gmail)) || null },
      el("summary", {}, el("b", {}, tx("השלבים ב-Google Cloud (פעם אחת, כ-10 דקות)", "The steps in Google Cloud (one time, ~10 minutes)"))),
      el("div", { class: "muted small", style: "margin:10px 0" }, tx("JARVES לא רואה את העמודים האלה, אז מסמנים כל שלב בעצמכם (שורת הפקודה מסמנת בשבילכם). הקובץ של השלב האחרון נאסף מתיקיית ההורדות כשלוחצים התחלה.",
        "JARVES cannot see these pages, so tick each step yourself (the command line ticks them for you). The last step's file is picked up from Downloads when you press Start.")),
      steps));
  if (g.login.running) timer = setInterval(() => route(true), 2000);
};

renderTopbar();
setInterval(renderTopbar, 15000);
