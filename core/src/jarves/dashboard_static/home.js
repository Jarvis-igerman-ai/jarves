// JARVES dashboard, SW-V2 WP-K: Home (simple overview), Connections (Google, step by step) and the privacy switch
// in the top bar. Plain Hebrew + English words, one "what to do next" hint. Text only via textContent (el()).
"use strict";

const bi = (he, en, cls) => el("span", { class: cls || "" }, el("span", { dir: "rtl" }, he), " ", el("span", { class: "muted" }, "· " + en));
const lampOf = (s) => ({ ok: "ok", warn: "warn", off: "off", bad: "bad", unknown: "off" }[s] || "off");
const pillOf = (s, text) => pill({ ok: "ok", warn: "medium", off: "plain", unknown: "plain", fail: "fail" }[s] || "plain", text);
async function post(path, body) { return api(path, { method: "POST", body: JSON.stringify(body || {}) }); }

// ---------------------------------------------------------------- privacy (top bar + Home tile)
let privacyState = null;
async function togglePrivacy(msg) {
  const to = !privacyState.privacy;
  try {
    privacyState = await post("/api/privacy", { privacy: to });
    if (msg) msg.textContent = privacyState.saved_in_config ? "Saved · נשמר" : "Changed now; config.toml not saved: " + privacyState.config_error;
  } catch (e) { if (msg) msg.textContent = "Not changed: " + e.message; }
  await renderTopbar();
  refreshOverview();
  if ((location.hash || "#/home").startsWith("#/home")) route(true);
}
function privacyButton(msg) {
  return el("button", { type: "button", class: "switch", id: "privacyBtn", "aria-pressed": privacyState.privacy ? "true" : "false",
    title: privacyState.privacy ? "Click to keep full text (development)" : "Click to stop keeping what is said",
    onclick: () => togglePrivacy(msg) }, privacyState.privacy ? "Privacy ON · פרטיות פועלת" : "Privacy OFF · פרטיות כבויה");
}
async function renderTopbar() {
  const bar = document.getElementById("topbar");
  try { privacyState = await api("/api/privacy"); } catch (e) { bar.replaceChildren(); return; }
  const msg = el("span", { class: "muted small" });
  bar.replaceChildren(el("span", { class: "priv" }, privacyButton(msg),
    el("span", { class: "small", dir: "auto" }, privacyState.privacy ? "מה שנאמר לא נשמר" : "הטקסט המלא נשמר (לפיתוח)"),
    el("span", { class: "muted small" }, privacyState.privacy ? "· what is said is not kept" : "· full text is kept (development)")),
    msg, el("span", { class: "spacer" }), el("a", { href: "#/home" }, "Home · בית"), el("a", { href: "#/connections" }, "Connections · חיבורים"));
}
function privacyCard(p) {
  const msg = el("div", { class: "muted small" });
  const oneWay = (label, path, question) => el("button", { type: "button", class: "danger", onclick: async () => {
    if (!confirm(question + "\n\nThis cannot be undone. · אי אפשר לבטל.")) return;
    try { const r = await post(path, { confirm: true }); msg.textContent = "Done · בוצע" + (r.deleted !== undefined ? ` (${r.deleted} conversations)` : ` (${r.turns} lines)`); route(true); }
    catch (e) { msg.textContent = "Not done: " + e.message; }
  } }, label);
  return el("div", { class: "card", id: "privacy" }, el("h2", {}, "Privacy · פרטיות"),
    el("div", { class: "big-status" }, el("span", { class: "lamp " + (p.privacy ? "ok" : "warn") }),
      el("div", {}, el("div", { class: "title", dir: "rtl" }, p.label_he), el("div", { class: "muted" }, p.label_en))),
    el("div", { class: "small", dir: "rtl" }, p.explain_he), el("div", { class: "small muted" }, p.explain_en),
    el("div", { class: "actions" }, privacyButton(msg),
      oneWay("Mask existing history · הסתר היסטוריה", "/api/privacy/mask-history",
        "Remove the words from ALL saved conversations and log files? Lengths, times and costs stay.\nלמחוק את המילים מכל השיחות והיומנים השמורים?"),
      oneWay("Delete all stored conversations · מחק שיחות", "/api/privacy/delete-conversations",
        "Delete ALL stored conversations (costs stay)?\nלמחוק את כל השיחות השמורות?")), msg);
}

// ---------------------------------------------------------------- Home
PAGES.home = async function () {
  const d = await api("/api/home");
  privacyState = d.privacy;
  const lc = d.last_conversation;
  const okConn = d.connections.filter((c) => c.state === "ok").length;
  const tiles = el("div", { class: "tiles" },
    tile("Connected · מחובר", `${okConn} / ${d.connections.length}`, d.connections.filter((c) => c.state !== "ok").map((c) => `${c.title_en.split(" (")[0]}: ${c.detail_en}`).join(" · ") || "everything"),
    tile("Features on · פועל", String(d.features.on.length), `${d.features.off.length} off or not available`),
    tile("Today's cost · היום", usd(d.today.cost), `${d.today.conversations} conversations · ${d.today.voice_minutes} voice min`, true),
    tile("Open issues · בעיות", String(d.issues.open), d.issues.high ? `${d.issues.high} important` : "none important"),
    tile("Last talk · שיחה אחרונה", lc ? clock(lc.started).slice(0, 5) : "—",
      lc ? `${day(lc.started)} · ${secs(lc.duration_s)} · ${lc.user_turns} things you said${lc.typed ? " (typed)" : ""}` : "no conversations yet"));
  const next = el("div", { class: "card next" }, el("span", { class: "label" }, "What to do next · מה עכשיו:"),
    el("span", { dir: "rtl" }, d.next.he), el("span", { class: "muted" }, d.next.en), el("span", { class: "spacer" }),
    d.next.link ? el("a", { class: "button-link", href: d.next.link }, "Go · קדימה →") : null);
  const conns = el("div", { class: "card" }, el("h2", {}, "Connections · חיבורים"),
    d.connections.map((c) => el("div", { class: "conn" }, pillOf(c.state, c.state === "ok" ? "OK" : c.state === "warn" ? "check" : "off"),
      el("div", {}, el("b", {}, c.title_en), " ", el("span", { class: "muted", dir: "rtl" }, c.title_he),
        el("div", { class: "small" }, el("span", { dir: "rtl" }, c.detail_he), el("span", { class: "muted" }, " · " + c.detail_en),
          c.days_left !== null && c.days_left !== undefined ? el("span", { class: "muted" }, ` · reconnect in ~${c.days_left} days`) : null)),
      el("span", { class: "spacer" }), el("a", { href: c.link }, "Open →"))));
  const feats = el("div", { class: "card" }, el("h2", {}, "What is on · מה פועל"),
    el("div", { class: "chips" }, d.features.on.map((f) => el("span", { class: "chip-on", title: f.title_he }, f.title_en))),
    el("div", { class: "small muted", style: "margin-top:10px" }, "Off or not available · כבוי או לא זמין"),
    el("div", { class: "chips" }, d.features.off.map((f) => el("span", { class: "chip-off", title: f.reason, dir: "auto" }, f.title_en))),
    el("div", { class: "actions" }, el("a", { class: "button-link", href: "#/features" }, "Change · לשנות")));
  const last = lc ? el("div", { class: "card" }, el("h2", {}, "Last conversation · שיחה אחרונה"),
    el("div", { dir: "auto" }, lc.first_user || "(nothing said)"),
    el("div", { class: "muted small" }, `${day(lc.started)} ${clock(lc.started)} · ${secs(lc.duration_s)} · ${usd(lc.cost)}` + (lc.issues ? ` · ${lc.issues} issue(s)` : "")),
    el("div", { class: "actions" }, el("a", { class: "button-link", href: "#/session/" + encodeURIComponent(lc.id) }, "Open · לפתוח"))) : null;
  const setup = typeof window.guidesSetupCard === "function" ? await window.guidesSetupCard() : null;   // WP-L checklist
  render(el("div", { class: "big-status" }, el("span", { class: "lamp " + lampOf(d.status) }),
    el("div", {}, el("div", { class: "title", dir: "rtl" }, d.status_he), el("div", { class: "muted" }, d.status_en + (d.live ? " · talking now" : "")))),
    next, setup, tiles, el("div", { class: "grid-home" }, conns, feats), el("div", { class: "grid-home" }, privacyCard(d.privacy), last));
  timer = setInterval(() => route(true), 15000);
};

// ---------------------------------------------------------------- Connections (Google)
const CHECK = { ok: ["ok", "works · עובד"], fail: ["fail", "failed · נכשל"], not_run: ["plain", "not checked yet"], skipped: ["plain", "not used"] };
PAGES.connections = async function () {
  const g = await api("/api/google/setup");
  const msg = el("div", { class: "small", dir: "auto" });
  const act = async (fn) => { try { await fn(); } catch (e) { msg.textContent = e.message; } };
  const start = el("button", { type: "button", class: "primary big", disabled: g.login.running || null, onclick: () => act(async () => {
    try { await post("/api/google/start"); msg.textContent = "A browser window opens: pick your account, Continue, tick every box. · נפתח דפדפן: בוחרים חשבון, Continue, מסמנים הכל."; }
    catch (e) { msg.textContent = e.message + " · " + "Follow the steps below, or run: " + g.start_command; }
    setTimeout(() => route(true), 1500);
  }) }, g.login.running ? "Waiting for Google… · מחכה" : g.state === "needs_reconnect" ? "Reconnect · חבר מחדש" : g.signed_in ? "Sign in again · התחבר שוב" : g.app_client ? "Connect Google · חיבור גוגל" : "Start · התחל");
  const test = el("button", { type: "button", disabled: !g.signed_in || null, onclick: () => act(async () => {
    msg.textContent = "Checking… · בודק";
    const r = await post("/api/google/test");
    msg.textContent = r.ok ? "Works: calendar and mail answer. · עובד." : (r.fix ? r.fix.he + " · " + r.fix.en : "Failed");
    route(true);
  }) }, "Test · בדיקה");
  const disc = el("button", { type: "button", class: "danger", disabled: !(g.signed_in || g.needs_reconnect) || null, onclick: () => act(async () => {
    if (!confirm("Disconnect Google? JARVES forgets the keys and Google revokes them.\nלנתק את גוגל?")) return;
    await post("/api/google/disconnect"); msg.textContent = "Disconnected · נותק"; route(true);
  }) }, "Disconnect · נתק");
  const row = (label, value) => [el("div", {}, label), el("div", {}, value)];
  const yes = (v, a, b) => v ? pill("ok", a) : pill("plain", b);
  const checks = el("div", { class: "checks" },
    g.app_client ? [] : row("Key file from Google (client_secret…json) · קובץ מפתח", yes(g.client_configured, "here", "missing")),
    row("Signed in · מחובר", g.signed_in ? pill("ok", "yes") : g.needs_reconnect ? pill("medium", "expired: reconnect") : pill("plain", "no")),
    row("Calendar check · בדיקת יומן", pill(...CHECK[g.check.calendar] || CHECK.not_run)),
    row("Mail check · בדיקת מייל", pill(...CHECK[g.check.gmail] || CHECK.not_run)),
    row("Sending mail · שליחת מיילים", g.gmail_send ? el("a", { href: "#/features" }, "Google allows drafts; the on/off switch is on Features (off by default)") : el("span", { class: "muted" }, "not asked for")),
    g.app_client ? [] : row("Weekly reconnect · חיבור מחדש", g.testing_days_left !== null ? `in about ${g.testing_days_left} days (Testing mode)` : "—"),
    row("Last problem · בעיה אחרונה", g.last_error ? el("span", { class: "mono" }, g.last_error) : el("span", { class: "muted" }, "none")));
  const fix = g.fix ? el("div", { class: "card fix" }, el("b", {}, "How to fix · איך מתקנים"), el("div", { dir: "rtl" }, g.fix.he), el("div", { class: "muted" }, g.fix.en),
    g.fix.url ? el("div", { class: "actions" }, el("a", { class: "button-link", href: g.fix.url, target: "_blank", rel: "noopener noreferrer" }, "Open the page in Google Cloud →")) : null) : null;
  const steps = el("ol", { class: "steps" }, g.steps.filter((s) => !(g.calendar_only && s.gmail)).map((s, i) => {
    const smsg = el("span", { class: "muted small" });
    return el("li", { class: "step" + (s.done ? " done" : "") }, el("span", { class: "num" }, s.done ? "✓" : String(i + 1)),
      el("div", {}, el("b", { dir: "rtl" }, s.title_he), " ", el("span", { class: "muted" }, "· " + s.title_en),
        s.done ? null : el("ul", {}, s.clicks_en.map((c, k) => el("li", {}, el("span", { dir: "rtl" }, s.clicks_he[k] || ""), el("span", { class: "muted" }, " · " + c)))),
        el("div", { class: "actions" },
          el("a", { class: "button-link", href: s.url, target: "_blank", rel: "noopener noreferrer" }, "Open in Google Cloud →"),
          s.copy ? el("button", { type: "button", onclick: async () => { try { await navigator.clipboard.writeText(s.copy); smsg.textContent = "Copied · הועתק"; } catch (e) { smsg.textContent = "Copy: " + s.copy; } } }, s.id === "scopes" ? "Copy permissions" : "Copy “" + s.copy + "”") : null,
          s.id === "audience" ? el("span", { class: "muted small" }, "paste your own Gmail address") : null,
          el("button", { type: "button", class: "ghost", onclick: () => act(async () => { await post("/api/google/step/" + s.id, { done: !s.done }); route(true); }) }, s.done ? "Mark not done" : "Mark done · סיימתי"), smsg)));
  }));
  render(header("Connections · חיבורים", "Connect JARVES to your Google Calendar and Gmail. · מחברים את JARVES ליומן ולמייל שלך."),
    el("div", { class: "big-status" }, el("span", { class: "lamp " + ({ connected: "ok", needs_reconnect: "warn" }[g.state] || "off") }),
      el("div", {}, el("div", { class: "title" }, "Google · גוגל: " + ({ connected: "connected · מחובר", needs_reconnect: "needs reconnect · צריך לחבר מחדש", ready_to_sign_in: "ready to sign in · מוכן" }[g.state] || "not connected · לא מחובר")),
        el("div", { class: "muted small" }, g.scopes.length ? "Allowed: " + g.scopes.join(", ") : g.needs_reconnect ? "Nothing to set up again: just sign in once more." : "No permissions yet"))),
    el("div", { class: "card next" }, el("span", { class: "label" }, "What to do next · מה עכשיו:"), el("span", { dir: "rtl" }, g.next_he), el("span", { class: "muted" }, g.next_en)),
    el("div", { class: "grid-home" },
      el("div", { class: "card" }, el("h2", {}, "Status · מצב"), checks, el("div", { class: "actions" }, start, test, disc), msg),
      el("div", { class: "card" }, el("h2", {}, "What is this? · מה זה?"), el("div", { dir: "rtl" }, g.explain_he), el("div", { class: "muted small", style: "margin-top:6px" }, g.explain_en),
        g.app_client ? null : el("div", { class: "small", style: "margin-top:10px" }, "Easiest: on this computer run ", el("span", { class: "mono" }, g.start_command),
          ". It opens each page, copies what to paste, picks up the downloaded file by itself, signs in and checks everything.",
          g.gcloud_installed ? " (gcloud is installed: it can also create the project for you.)" : ""))),
    fix,
    g.app_client ? null : el("details", { class: "card", open: !g.steps.every((s) => s.done || (g.calendar_only && s.gmail)) || null },
      el("summary", {}, el("b", {}, "The steps in Google Cloud (one time, ~10 minutes) · השלבים בגוגל קלאוד")),
      el("div", { class: "muted small", style: "margin:10px 0" }, "JARVES cannot see these pages, so tick each step yourself (the command line ticks them for you). The last step's file is picked up from Downloads when you press Start."),
      steps));
  if (g.login.running) timer = setInterval(() => route(true), 2000);
};

renderTopbar();
setInterval(renderTopbar, 15000);
