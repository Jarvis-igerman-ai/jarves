// SW-V4 F WhatsApp page (PC only): bridge status, link / unlink, mode, read and send allow-lists, rate counters, kill
// switch, retention, the inbox (UNTRUSTED text: plain textContent, never followed) and the send audit (hashes only).
// Uses the helpers of app.js / pages.js (el, api, render, header, pill, route, note, clock, day). Changes need the
// local session: a paired phone gets 403 from every write here.
"use strict";

PAGES.whatsapp = async function () {
  const d = await api("/api/whatsapp");
  const st = d.status || {};
  const msg = note();
  const post = async (path, body, okText) => {
    msg.textContent = tx("ממתין…", "Working…");
    try {
      const r = await api("/api/whatsapp/" + path, { method: "POST", body: JSON.stringify(body || {}) });
      msg.textContent = okText || tx("בוצע.", "Done.");
      if (path === "link" || (path === "setup" && r.step === "awaiting_scan")) showLink(r);
      else if (path === "setup" && r.ok === false) msg.textContent = tx(r.message_he, r.message);
      else route(true);
    } catch (e) { msg.textContent = tx("לא בוצע: ", "Not done: ") + e.message; }
  };
  const h = d.health || {};
  const hp = h.status === "ok" ? pill("ok", tx("מחובר", "connected")) : h.status === "disabled" ? pill("plain", tx("כבוי", "off"))
    : h.status === "degraded" ? pill("medium", tx("חלקי", "partial")) : pill("fail", tx("לא זמין", "unavailable"));
  const killed = !!st.killed;
  const missing = d.bridge_installed === false && !d.status;      // no bridge program and none running
  const cantRun = !d.local || missing;

  // ---- link (QR text or pairing code, refreshed until linked)
  const linkBox = el("pre", { class: "small", dir: "ltr", "data-noi18n": "", style: "line-height:1;font-size:7px;background:#fff;color:#000;display:none;padding:8px" });
  const showLink = async (r) => {
    linkBox.style.display = "block";
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      linkBox.textContent = r.pairing_code ? r.pairing_code : (r.qr_text || "");
      if (r.state === "linked") {
        msg.textContent = tx("מקושר. בטלפון יופיע JARVES bridge.", "Linked. The phone shows JARVES bridge.");
        if (st.mode !== "read" && st.mode !== "send" && window.confirm(tx("להפעיל עכשיו מצב קריאה בלבד? ג'ארביס יקרא ולא ישלח.", "Switch on read-only mode now? JARVES will read and never send.")))
          { try { await api("/api/whatsapp/mode", { method: "POST", body: JSON.stringify({ mode: "read" }) }); } catch (e) { msg.textContent = e.message; return; } }
        route(true); return;
      }
      if (r.state === "timeout" || r.state === "error") { msg.textContent = tx("הקישור לא הושלם. נסו שוב.", "Linking did not finish. Try again."); return; }
      await new Promise((res) => setTimeout(res, 1000));
      try { r = await api("/api/whatsapp/link"); } catch (e) { msg.textContent = e.message; return; }
    }
  };
  const phone = el("input", { type: "tel", dir: "ltr", placeholder: "+972…", style: "min-width:160px", "aria-label": "Phone number for a pairing code" });

  // ---- mode buttons
  const modeBtn = (m, label) => el("button", { type: "button", class: st.mode === m ? "primary" : "ghost", disabled: !d.local || st.mode === m,
    onclick: () => { if (m !== "send" || window.confirm(tx("להפעיל מצב שליחה? כל הודעה עדיין תאושר על המסך.", "Switch send mode on? Every message is still approved on the screen."))) post("mode", { mode: m }); } }, label);

  // ---- allow-lists
  const pol = d.policy || {};
  const listCard = (kind, title) => {
    const rows = (pol[kind + "_allow"] || []).map((c) => el("tr", {},
      el("td", { dir: "auto", "data-noi18n": "" }, c.name || "—"), el("td", { dir: "ltr", class: "small" }, c.number || c.id),
      el("td", {}, el("button", { type: "button", class: "ghost", disabled: !d.local, onclick: () => post("allow", { list: kind, op: "remove", chat: c.id }) }, tx("הסרה", "Remove")))));
    const inp = el("input", { type: "text", dir: "auto", placeholder: tx("שם או +מספר", "name or +number"), "aria-label": title });
    return el("div", { class: "card" }, el("b", {}, title),
      rows.length ? el("table", {}, el("tbody", {}, rows)) : el("div", { class: "empty small" }, kind === "send" ? tx("ריק: אף אחד (ברירת מחדל).", "Empty: nobody (the default).") : tx("ריק: כל צ'אט אחד-על-אחד נקרא, בלי קבוצות.", "Empty: every 1:1 chat is read, no groups.")),
      el("div", { class: "row" }, inp, el("button", { type: "button", disabled: !d.local, onclick: () => inp.value.trim() && post("allow", { list: kind, op: "add", chat: inp.value.trim() }) }, tx("הוספה", "Add"))));
  };

  // ---- inbox (untrusted)
  const msgs = el("div", {});
  const openChat = async (c) => {
    msgs.replaceChildren(el("div", { class: "muted small" }, tx("טוען…", "Loading…")));
    try {
      const r = await api("/api/whatsapp/messages?chat=" + encodeURIComponent(c.id));
      msgs.replaceChildren(el("div", { class: "small muted" }, tx("טקסט שכתבו אחרים: לא אמין. ג'ארביס לא פועל לפיו ולא פותח קישורים.", "Text written by others: untrusted. JARVES never acts on it and never opens links.")),
        ...r.messages.map((m) => el("div", { class: "card untrusted", style: "margin:4px 0" },
          el("div", { class: "small muted" }, `${m.time} · `, el("span", { dir: "auto", "data-noi18n": "" }, m.from)),
          el("div", { dir: "auto", "data-noi18n": "", style: "white-space:pre-wrap" }, m.kind === "text" ? m.text : m.media),
          m.hidden_chars_removed ? el("div", { class: "small" }, tx("הוסרו תווים נסתרים: ", "Hidden characters removed: ") + m.hidden_chars_removed.join(" ")) : null,
          m.has_link ? el("div", { class: "small" }, tx("יש קישור (לא נפתח).", "Contains a link (never opened).")) : null)));
    } catch (e) { msgs.replaceChildren(el("div", { class: "small" }, e.message)); }
  };
  const chats = (d.chats || []).map((c) => el("tr", { class: "click", onclick: () => openChat(c) },
    el("td", { dir: "auto", "data-noi18n": "" }, c.name || c.number || "—"), el("td", { class: "num" }, String(c.unread || 0)),
    el("td", { class: "small" }, c.last_ts ? `${day(c.last_ts)} ${clock(c.last_ts)}` : "—"),
    el("td", {}, c.can_send ? pill("ok", tx("שליחה מותרת", "may send")) : null)));

  const rate = st.rate || {};
  const lim = rate.limits || {};
  const ret = el("input", { type: "number", min: "1", max: "90", value: String(st.retention_days || 14), style: "width:70px", "aria-label": "Retention days" });
  if (!window.__waSetupAuto && /setup=1/.test(location.hash) && !cantRun && !st.linked) {   // from the Connect row
    window.__waSetupAuto = true;
    setTimeout(() => post("setup", {}, tx("סרקו את הקוד", "Scan the code")), 0);
  }
  if (!/setup=1/.test(location.hash)) window.__waSetupAuto = false;
  render(header(tx("וואטסאפ", "WhatsApp"), tx("גישה אמיתית דרך גשר נפרד במחשב. קודם קריאה בלבד; כל שליחה מאושרת על המסך.", "Real access through a separate bridge on this computer. Read-only first; every send is approved on the screen."),
      el("a", { href: "#/guide/whatsapp_bridge" }, tx("מדריך", "Guide"))),
    el("div", { class: "card" },
      el("div", { class: "row" }, el("b", {}, tx("מצב הגשר: ", "Bridge: ")), hp, el("span", { class: "small" }, h.detail || ""),
        killed ? pill("fail", tx("עצור", "KILLED")) : null),
      el("div", { class: "small muted" }, tx(d.tos_he, d.tos)),
      !d.local ? el("div", { class: "small" }, tx("את זה משנים רק במחשב עצמו.", "Changes are made only on the computer itself.")) : null,
      el("div", { class: "row", style: "margin-top:8px" },
        el("button", { type: "button", class: killed ? "ghost" : "danger", disabled: !d.local, onclick: () => post("kill", { on: killed ? false : true }) },
          killed ? tx("שחרור מתג החירום", "Release the kill switch") : tx("מתג חירום: לעצור הכול", "Kill switch: stop everything")),
        el("button", { type: "button", class: "ghost", disabled: cantRun, onclick: () => post("start", {}) }, tx("הפעלת הגשר", "Start the bridge")))),
    missing ? el("div", { class: "card", role: "alert" }, el("b", {}, tx("רכיב הגשר לא מותקן", "The bridge program is not installed")),
      el("div", { class: "small" }, tx(d.not_installed.he, d.not_installed.en))) : null,
    el("div", { class: "card" }, el("b", {}, tx("הגדרת וואטסאפ בלחיצה אחת", "Set up WhatsApp in one go")),
      el("div", { class: "small" }, tx("מפעיל את הגשר במידת הצורך ומציג קוד QR. את הסריקה בטלפון עושים אתם, ואחריה אפשר להפעיל קריאה בלבד. לא נשלחת שום הודעה.", "Starts the bridge if needed and shows a QR code. You scan it on your phone, then you can switch on read-only. No message is sent.")),
      el("button", { type: "button", class: "primary", disabled: cantRun || st.linked, onclick: () => post("setup", {}, tx("סרקו את הקוד", "Scan the code")) }, tx("הגדרת וואטסאפ", "Set up WhatsApp"))),
    el("h2", {}, tx("קישור", "Link")),
    el("div", { class: "card" },
      el("div", { class: "small" }, tx("בטלפון: וואטסאפ > הגדרות > מכשירים מקושרים > קישור מכשיר. ניתוק בטלפון עובד תמיד.", "On the phone: WhatsApp > Settings > Linked devices > Link a device. Unlinking on the phone always works.")),
      el("div", { class: "row" },
        el("button", { type: "button", class: "primary", disabled: cantRun || st.linked, onclick: () => post("link", phone.value.trim() ? { phone: phone.value.trim() } : {}, tx("סרקו את הקוד", "Scan the code")) }, tx("קישור (קוד QR)", "Link (QR code)")),
        phone, el("span", { class: "small muted" }, tx("או מספר לקוד צימוד", "or a number for a pairing code")),
        el("button", { type: "button", class: "ghost", disabled: !d.local || !st.linked, onclick: () => window.confirm(tx("לנתק ולמחוק את המידע של הגשר במחשב?", "Unlink and wipe the bridge store on this computer?")) && post("unlink", {}) }, tx("ניתוק", "Unlink"))),
      linkBox),
    el("h2", {}, tx("מצב", "Mode")),
    el("div", { class: "card" },
      el("div", { class: "row" }, modeBtn("off", tx("כבוי", "Off")), modeBtn("read", tx("קריאה בלבד", "Read only")), modeBtn("send", tx("שליחה", "Send"))),
      el("div", { class: "small" }, st.mode === "read" ? tx("ימים שנותרו עד שאפשר להציע מצב שליחה: ", "Days left before send mode can be offered: ") + String(st.watch_days_left ?? "—") : ""),
      el("div", { class: "small muted" }, tx("עד שמצב שליחה פועל, שולחים דרך פתיחת הצ'אט ואתם לוחצים שלח.", "Until send mode is on, sending opens the chat and you press send."))),
    el("h2", {}, tx("רשימות", "Lists")),
    listCard("read", tx("רשימת קריאה (צ'אטים נוספים)", "Read list (extra chats)")),
    listCard("send", tx("רשימת שליחה", "Send list")),
    el("h2", {}, tx("מגבלות ושמירה", "Limits and retention")),
    el("div", { class: "card" },
      el("div", { class: "small" }, tx("נשלחו בשעה האחרונה: ", "Sent in the last hour: ") + `${rate.hour ?? 0} / ${lim.per_hour ?? 10}`),
      el("div", { class: "small" }, tx("נשלחו היום: ", "Sent today: ") + `${rate.day ?? 0} / ${lim.per_day ?? 30}`),
      el("div", { class: "small" }, tx("אין שליחה בין 23:00 ל-07:00 בלי קוד מוקלד.", "No sends 23:00-07:00 without a typed code.")),
      el("div", { class: "row" }, el("span", { class: "small" }, tx("ימי שמירה: ", "Days kept: ")), ret,
        el("button", { type: "button", disabled: !d.local, onclick: () => post("retention", { days: parseInt(ret.value, 10) }) }, tx("שמירה", "Save")),
        el("button", { type: "button", class: "ghost", disabled: !d.local, onclick: () => post("retention", { keep_nothing: !st.keep_nothing }) },
          st.keep_nothing ? tx("לשמור הודעות שוב", "Keep messages again") : tx("לא לשמור כלום", "Keep nothing")),
        el("button", { type: "button", class: "ghost", disabled: !d.local, onclick: () => window.confirm(tx("למחוק את כל ההודעות השמורות?", "Delete every stored message?")) && post("purge", { confirm: true }) }, tx("מחיקת הודעות", "Delete messages")))),
    el("h2", {}, tx("תיבת הודעות", "Inbox")),
    el("div", { class: "card" }, chats.length ? el("table", {}, el("tbody", {}, chats)) : el("div", { class: "empty" }, tx("אין צ'אטים להצגה.", "No chats to show.")), msgs),
    el("h2", {}, tx("יומן שליחות", "Send audit")),
    el("div", { class: "card" }, (d.audit || []).length ? el("table", {}, el("tbody", {}, (d.audit || []).slice().reverse().map((a) => el("tr", {},
      el("td", { class: "small" }, `${day(a.ts)} ${clock(a.ts)}`), el("td", { dir: "auto", "data-noi18n": "" }, a.name || a.chat_hash),
      el("td", { class: "small" }, a.result), el("td", { class: "num" }, String(a.chars)))))) : el("div", { class: "empty" }, tx("עוד לא נשלח כלום.", "Nothing sent yet."))),
    msg);
};
