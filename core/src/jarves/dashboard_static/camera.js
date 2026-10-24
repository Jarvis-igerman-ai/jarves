// JARVES dashboard: Camera page (V4.5 workstream D). One section for the camera: honest state, device, privacy line,
// last request, a device probe vs a frame test, and a timed local preview with a countdown and a Stop button.
// Uses app.js helpers (el, api, render, header, pill, clock, day, route). Text goes in via textContent only; the
// preview picture lives in one <img> and is removed when the preview stops. Nothing here sends a picture anywhere.
"use strict";

(function () {
  const PREVIEW_S = 20;
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const ST = {
    off: ["plain", "כבויה", "Camera off", "המצלמה כבויה. החיישן סגור.", "The camera is switched off. The sensor is closed."],
    available: ["ok", "זמינה", "Available", "המצלמה זמינה אבל לא פועלת. החיישן סגור ונפתח רק כשמבקשים.",
      "The camera is available but not running. The sensor is closed and opens only when you ask."],
    previewing: ["degraded", "תצוגה מקומית", "Previewing", "תצוגה מקומית פתוחה. התמונה נשארת במחשב.",
      "A local preview is open. The picture stays on this computer."],
    capturing: ["degraded", "מצלמת עכשיו", "Capturing", "מצלמת תמונה אחת עכשיו.", "Taking one picture now."],
    sending: ["high", "שולח ל-AI", "Sending to AI", "תמונה אחת נשלחת עכשיו לספק ה-AI.", "One picture is being sent to the AI provider now."],
    busy: ["degraded", "עסוקה", "Busy", "המצלמה עסוקה בבקשה אחרת.", "The camera is busy with another request."],
    error: ["fail", "שגיאה", "Error", "יש בעיה במצלמה. ראו מה לעשות למטה.", "There is a problem with the camera. See what to do below."],
  };
  const KIND = { probe: ["בדיקת מכשיר", "Device check"], frame: ["בדיקת תמונה", "Frame test"],
    describe: ["תיאור תמונה", "Describe"], read_text: ["קריאת טקסט", "Read text"] };
  const HINT = { too_dark: ["חשוך מדי, הוסיפו אור.", "Too dark, add light."],
    maybe_blurred: ["אולי לא בפוקוס, נקו את העדשה.", "Maybe out of focus, clean the lens."] };
  const REFUSAL = { CORE_STOPPED: ["JARVES לא רץ. הפעילו אותו מהכפתור למעלה.", "JARVES is not running. Start it with the button at the top."],
    CAMERA_UNAVAILABLE: ["אין מצלמה זמינה.", "No camera is available."], CAMERA_DISABLED: ["המצלמה כבויה.", "The camera is switched off."],
    PREVIEW_DISABLED: ["תצוגת המצלמה כבויה בדף היכולות.", "The camera preview is switched off in Features."] };
  const pv = { open: false, left: 0, poll: null, tick: null, msg: "" };
  let view = null, testOut = null, busyBtn = "", err = "";

  const t2 = (p) => tx(p[0], p[1]);
  const card = (title, ...kids) => el("div", { class: "card" }, el("h2", {}, title), ...kids);
  const line = (...kids) => el("div", { class: "small", dir: "auto", style: "margin:4px 0" }, ...kids);

  function errBox(e) {
    return el("div", { class: "card", role: "alert", style: "border-inline-start:5px solid var(--critical)" },
      el("strong", {}, tx(e.he, e.en)), el("div", {}, tx(e.action_he, e.action_en)),
      e.detail ? el("div", { class: "muted small", dir: "ltr" }, e.detail) : null);
  }

  function resultLine(r) {
    if (!r) return tx("עוד לא הייתה בקשה.", "No request yet.");
    const k = KIND[r.kind] ? tx(KIND[r.kind][0], KIND[r.kind][1]) : r.kind;
    const when = `${day(r.ts)} ${clock(r.ts)}`;
    const out = r.ok ? tx("הצליח", "Worked") : (r.error ? tx(r.error.he, r.error.en) : r.code);
    const where = r.sent_to_provider ? tx(" · נשלחה תמונה אחת לספק AI", " · one picture was sent to the AI provider")
      : tx(" · נשאר במחשב", " · stayed on this computer");
    return `${k}: ${out} · ${when}${where}`;
  }

  function testResult(t) {
    if (!t) return null;
    const bits = [];
    if (t.mode === "probe") {
      bits.push(t.ok ? tx("נמצאה מצלמה. לא נפתח חיישן ולא צולמה תמונה.", "A camera is listed. No sensor was opened and no picture was taken.")
        : tx("לא נמצאה מצלמה. לא נפתח חיישן.", "No camera was found. No sensor was opened."));
      if (t.detail) bits.push(t.detail);
    } else if (t.ok) {
      bits.push(tx(`תמונת בדיקה התקבלה: ${t.width}x${t.height} תוך ${t.ms} מ"ש. נמדדה ונמחקה: לא נשמרה ולא נשלחה.`,
        `A test picture arrived: ${t.width}x${t.height} in ${t.ms} ms. Measured and dropped: not saved and not sent.`));
      for (const h of t.hints || []) bits.push(t2(HINT[h]));
      if (!(t.hints || []).length) bits.push(tx("התמונה נראית תקינה (בהירות ופוקוס).", "The picture looks fine (light and focus)."));
      bits.push(t.sensor_closed ? tx("החיישן נסגר.", "The sensor is closed again.") : "");
    } else {
      bits.push(tx(t.he, t.en), tx(t.action_he, t.action_en));
    }
    return el("div", { class: t.ok === false ? "card" : "muted", role: "status", "aria-live": "polite" }, bits.filter(Boolean).map((b) => line(b)));
  }

  async function runTest(mode) {
    busyBtn = mode; err = ""; draw();
    try { const r = await post("/api/camera/test", { mode }); testOut = r.test; view = r; }
    catch (e) { testOut = null; err = e.message; }
    busyBtn = ""; draw();
  }

  function stopPreview(send) {
    clearInterval(pv.poll); clearInterval(pv.tick); pv.poll = pv.tick = null;
    if (pv.open && send) api("/api/camera/preview", { method: "POST", body: JSON.stringify({ open: false }) }).catch(() => {});
    pv.open = false;
    const img = document.getElementById("camImg");
    if (img) img.removeAttribute("src");            // the picture is gone with the preview
    refresh().catch(() => {});
  }
  function showFrame(r) {
    const img = document.getElementById("camImg");
    if (img && r.frame && typeof r.frame.data === "string" && /^image\/(jpeg|png)$/.test(r.frame.mime))
      img.src = `data:${r.frame.mime};base64,${r.frame.data}`;
  }
  async function startPreview() {
    pv.msg = ""; err = "";
    let r;
    try { r = await api("/api/camera/preview", { method: "POST", body: JSON.stringify({ start: true }) }); }
    catch (e) { err = e.message; draw(); return; }
    if (!r.open) { pv.msg = r.reason || "closed"; draw(); return; }
    pv.open = true; pv.left = PREVIEW_S; draw(); showFrame(r);
    pv.poll = setInterval(async () => {
      if (!document.getElementById("camCard")) { stopPreview(true); return; }       // left the page: close it
      try { const x = await api("/api/camera/preview"); if (!x.open) { pv.msg = x.reason || ""; stopPreview(false); } else showFrame(x); }
      catch (e) { err = e.message; stopPreview(false); }
    }, 700);
    pv.tick = setInterval(() => { pv.left -= 1; if (pv.left <= 0) stopPreview(true); else draw(); }, 1000);
  }

  function draw() {
    const $m = document.getElementById("camCard");
    if (!$m || !view) return;
    const v = view, st = ST[pv.open ? "previewing" : v.state] || ST.off, state = pv.open ? "previewing" : v.state;
    const local = v.data_flow === "local_only";
    const tests = card(tx("בדיקת מצלמה", "Test camera"),
      line(tx("שתי בדיקות נפרדות: בדיקת מכשיר רק מחפשת מצלמה. בדיקת תמונה פותחת את החיישן לרגע וקולטת תמונה אחת.",
        "Two separate checks: Check device only looks for a camera. Test a frame opens the sensor for a moment and reads one picture.")),
      el("div", { class: "row", style: "flex-wrap:wrap;gap:8px" },
        el("button", { type: "button", class: "ghost", style: "min-height:44px", disabled: busyBtn ? true : null, onclick: () => runTest("probe") },
          busyBtn === "probe" ? tx("בודק…", "Checking…") : tx("בדיקת מכשיר (לא פותח כלום)", "Check device (opens nothing)")),
        el("button", { type: "button", class: "primary", style: "min-height:44px",
          disabled: busyBtn || !v.core_running || !v.enabled || pv.open ? true : null, onclick: () => runTest("frame") },
          busyBtn === "frame" ? tx("מצלם…", "Taking one…") : tx("בדיקת תמונה (תמונה אחת, נשארת כאן)", "Test a frame (one picture, stays here)"))),
      line(tx("תמונות הבדיקה נשארות במחשב: לא נשמרות, לא נשלחות ולא מוצגות.", "Test pictures stay on this computer: not saved, not sent and not shown.")),
      !v.enabled ? line(tx("בדיקת תמונה דורשת להדליק את מתג \"מצלמה\" בדף היכולות.", "Test a frame needs the Camera switch on in Features.")) : null,
      testResult(testOut));
    const refusal = v.preview && v.preview.refusal;
    const preview = card(tx("תצוגה מקומית", "Local preview"),
      line(tx(`תצוגה חיה למשך ${PREVIEW_S} שניות כדי לכוון את המצלמה. התמונה נשארת במחשב ולא נשמרת.`,
        `A live view for ${PREVIEW_S} seconds to aim the camera. The picture stays on this computer and is never saved.`)),
      pv.open ? el("div", { class: "ps-preview" },
        el("div", { class: "ps-head", dir: JL.dir() }, el("span", { class: "ps-rec" }),
          el("strong", { id: "camCount", "aria-live": "off" }, tx(`נסגרת בעוד ${pv.left} שנ'`, `Closes in ${pv.left} s`))),
        el("img", { id: "camImg", alt: tx("תצוגה חיה מהמצלמה", "Live camera view") }),
        el("button", { type: "button", class: "primary", style: "min-height:44px;width:100%", onclick: () => stopPreview(true) },
          tx("עצור", "Stop")))
        : el("button", { type: "button", class: "primary", style: "min-height:44px", disabled: v.preview && v.preview.can_start && !busyBtn ? null : true,
          onclick: startPreview }, tx("הצג תצוגה מקומית", "Start local preview")),
      !pv.open && refusal && REFUSAL[refusal] ? line(t2(REFUSAL[refusal])) : null,
      pv.msg && REFUSAL[pv.msg] ? line(t2(REFUSAL[pv.msg])) : null);
    const e = v.error;
    const prov = v.provider || {};
    $m.replaceChildren(
      el("div", { class: "card" },
        el("div", { class: "row", style: "align-items:center;gap:10px;flex-wrap:wrap" },
          pill(st[0], tx(st[1], st[2])), el("strong", { dir: "auto" }, tx(st[3], st[4]))),
        line(el("strong", {}, tx("מכשיר: ", "Device: ")), v.device && v.device.detail ? v.device.detail : "—"),
        line(el("strong", {}, tx("חיישן: ", "Sensor: ")), v.sensor_open ? tx("פתוח עכשיו", "open now") : tx("סגור", "closed")),
        line(el("strong", {}, tx("תמונה: ", "Picture: ")), local
          ? tx("נשארת במחשב הזה בלבד (מקומי)", "stays on this computer only (local)")
          : tx("נשלחת עכשיו לספק AI", "being sent to the AI provider now")),
        line(el("strong", {}, tx("בקשה אחרונה: ", "Last request: ")), resultLine(v.last_result)),
        line(tx(`ספק AI מקבל תמונה אחת רק כשמבקשים מ-JARVES להסתכל ("מה אתה רואה"). היום: ${prov.photos_today || 0} מתוך ${prov.daily_limit || 0}.`,
          `The AI provider gets one picture only when you ask JARVES to look ("what do you see"). Today: ${prov.photos_today || 0} of ${prov.daily_limit || 0}.`)),
        v.core_running === false ? line(tx("JARVES לא רץ, לכן זו בדיקה בלי לפתוח את החיישן.", "JARVES is not running, so this is a check that opens no sensor.")) : null),
      e ? errBox(e) : (v.last_result && !v.last_result.ok && v.last_result.error ? errBox(v.last_result.error) : null),
      err ? el("div", { class: "card", role: "alert" }, err) : null,
      tests, preview,
      line(tx("מילות הפעלה: \"מה אתה רואה\", \"מה אני מחזיק\", \"תקרא את זה\". החיישן נפתח רק אז.",
        "Say: \"what do you see\", \"what am I holding\", \"read this\". The sensor opens only then.")));
  }

  async function refresh() {
    try { view = await api("/api/camera/status"); err = ""; } catch (e) { err = e.message; view = view || { state: "off", device: {}, preview: {}, provider: {} }; }
    draw();
  }

  async function pageCamera() {
    render(header(tx("מצלמה", "Camera"), tx("מצב אמיתי של המצלמה, בדיקות ותצוגה מקומית", "The real camera state, tests and a local preview")),
      el("div", { id: "camCard" }));
    await refresh();
    timer = setInterval(() => { if (!pv.open && !busyBtn) refresh(); }, 3000);
  }

  Object.assign(PAGES, { camera: pageCamera });
})();
