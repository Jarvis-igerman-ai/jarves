// JARVES phone: microphone / cloud-audio indicator and proactive-notice controls (V5, issue #185).
// The strip always says, in words, what is listening: this phone's microphone (known only here), the computer's microphone
// and whether audio is going to the cloud (read from the computer, "unknown" when it cannot be read). Notices stay on screen
// with Snooze / Stop notes / Dismiss. Text via textContent only.
"use strict";
(function () {
  const T = {
    en: { phone_mic: "Phone microphone", comp_mic: "Computer microphone", cloud: "Cloud audio", on: "ON: recording", off: "off", unknown: "unknown",
      listening: "listening", gated: "on, silent while JARVES speaks", wake_only: "wake word only (on the computer)", muted: "muted", idle: "idle", unavailable: "not available",
      c_sending: "SENDING audio to the cloud", c_connected: "session open, only silence is sent", c_connecting: "connecting", c_off: "off",
      local_note: "Audio from this phone goes to the computer only, is understood there on the device, is not sent to the cloud and is not stored.",
      stop_send: "Stop and send", discard: "Discard", notice: "Note", snooze: "Snooze notes 1 h", stop_notes: "Stop notes", dismiss: "Dismiss",
      sig_snoozed: "Notes snoozed for one hour.", sig_stopped: "Notes stopped. Turn them on again on the computer.", sig_state_snoozed: "Notes are snoozed", sig_state_stopped: "Notes are stopped (turn on on the computer)",
      failed: "Could not do that", strip: "Microphone and cloud audio" },
    he: { phone_mic: "מיקרופון הטלפון", comp_mic: "מיקרופון המחשב", cloud: "שמע לענן", on: "פועל: מקליט", off: "כבוי", unknown: "לא ידוע",
      listening: "מאזין", gated: "פועל, שקט בזמן שהעוזר מדבר", wake_only: "מילת הפעלה בלבד (במחשב)", muted: "מושתק", idle: "לא פעיל", unavailable: "לא זמין",
      c_sending: "שולח שמע לענן", c_connected: "שיחה פתוחה, נשלחת רק שתיקה", c_connecting: "מתחבר", c_off: "כבוי",
      local_note: "שמע מהטלפון הזה עובר רק למחשב, מובן שם על המכשיר, לא נשלח לענן ולא נשמר.",
      stop_send: "עצירה ושליחה", discard: "ביטול ההקלטה", notice: "הודעה", snooze: "השתקת הודעות לשעה", stop_notes: "עצירת הודעות", dismiss: "סגירה",
      sig_snoozed: "ההודעות הושתקו לשעה.", sig_stopped: "ההודעות נעצרו. מדליקים שוב במחשב.", sig_state_snoozed: "ההודעות מושתקות", sig_state_stopped: "ההודעות נעצרו (מדליקים במחשב)",
      failed: "לא ניתן לבצע", strip: "מיקרופון ושמע לענן" },
  };
  const newId = () => "sig-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  let box = null, nbox = null, cx = null, phone = null, comp = null, sig = null, note = "", seen = new Set();
  const t = (k) => ((T[cx.lang()] || T.en)[k]) || T.en[k] || k;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const btn = (label, fn, primary) => { const b = el("button", primary ? "primary" : "", label); b.type = "button"; b.addEventListener("click", fn); return b; };

  function line(label, value, hot) {
    const r = el("div", "audio-row" + (hot ? " hot" : ""));
    r.append(el("span", "audio-dot", hot ? "●" : "○"), el("span", "audio-label", label + ": "), el("strong", "audio-val", value));
    return r;
  }
  function paintStrip() {
    if (!box) return;
    box.replaceChildren();
    const c = comp || { known: false };
    const mic = c.known === false ? "unknown" : c.mic, cloud = c.known === false ? "unknown" : c.cloud;
    const hot = !!phone || cloud === "sending" || mic === "listening";
    box.className = "audio" + (hot ? " hot" : "");
    box.setAttribute("aria-label", t("strip"));
    box.appendChild(line(t("phone_mic"), phone ? t("on") : t("off"), !!phone));
    box.appendChild(line(t("comp_mic"), t(mic), mic === "listening"));
    box.appendChild(line(t("cloud"), cloud === "unknown" ? t("unknown") : t("c_" + cloud), cloud === "sending"));
    if (sig && sig.stopped) box.appendChild(el("div", "muted", t("sig_state_stopped")));
    else if (sig && sig.snoozed_until) box.appendChild(el("div", "muted", t("sig_state_snoozed")));
    if (phone) {
      const row = el("div", "row wrap");
      row.append(btn(t("stop_send"), () => phone.stop(), true), btn(t("discard"), () => phone.discard()));
      box.appendChild(row);
    }
    box.appendChild(el("div", "muted audio-note", t("local_note")));
  }
  async function control(op, key, card) {
    try { await cx.post("/api/m/signals", { op, minutes: 60, id: newId() }); note = t(key); if (op === "stop" && card) card.remove(); sig = { ...(sig || {}), stopped: op === "stop", snoozed_until: op === "snooze" ? 1 : (sig && sig.snoozed_until) }; }
    catch (e) { note = t("failed") + ": " + e.message; }
    paintNote(); paintStrip();
  }
  function paintNote() { const n = nbox && nbox.querySelector(".notice-status"); if (n) n.textContent = note; }
  function addNotice(n) {
    if (!nbox || seen.has(n.id)) return; seen.add(n.id);
    let st = nbox.querySelector(".notice-status");
    if (!st) { st = el("p", "muted notice-status"); st.setAttribute("role", "status"); nbox.appendChild(st); }
    const card = el("div", "notice"); card.setAttribute("role", "status");
    card.appendChild(el("div", "notice-text", t("notice") + ": " + n.text));
    const row = el("div", "row wrap");
    if (n.kind === "signal") row.append(btn(t("snooze"), () => control("snooze", "sig_snoozed", card)), btn(t("stop_notes"), () => control("stop", "sig_stopped", card)));
    row.appendChild(btn(t("dismiss"), () => card.remove()));
    card.appendChild(row); nbox.insertBefore(card, st);
  }
  async function refresh() {
    if (!cx) return;
    try { const d = await cx.api("/api/m/audio"); comp = d.computer; } catch (e) { comp = { known: false }; }
    paintStrip();
  }
  window.JarvesAudio = {
    mount(nodes, ctx) { box = nodes.audio; nbox = nodes.notices; cx = ctx; box.hidden = false; paintStrip(); refresh(); },
    phoneMic(on, handlers) { phone = on ? handlers : null; paintStrip(); },
    notice: addNotice,
    signals(s) { sig = s || null; paintStrip(); },
    refresh,
    repaint() { paintStrip(); },
    unmount() { if (box) { box.replaceChildren(); box.hidden = true; } if (nbox) nbox.replaceChildren(); box = nbox = cx = null; phone = comp = sig = null; seen = new Set(); },
    catalogs: T,
  };
})();
