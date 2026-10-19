// WP-M4 dashboard pages: Features capability matrix, Identity & voice, Calendar, Devices (LAN pairing + audit).
// Uses app.js helpers (el, api, render, header, pill, day, clock). All text via textContent, never innerHTML.
"use strict";

(function () {
  const STATUS_LABEL = {
    verified_on_device: "verified on device", implemented_unverified: "built, not verified", planned: "planned",
    blocked: "blocked", not_applicable: "n/a",
  };
  const STATUS_CLASS = { verified_on_device: "ok", implemented_unverified: "medium", planned: "low", blocked: "high", not_applicable: "plain" };
  const statusPill = (t) => t ? pill(STATUS_CLASS[t.status] || "plain", STATUS_LABEL[t.status] || t.status) : "—";
  const field = (label, input) => el("label", { class: "m4-field" }, el("span", { class: "muted small" }, label), input);
  const input = (attrs) => el("input", { type: "text", ...attrs });

  // the session's CSRF token (paired phones need it on every write; harmless on this computer)
  api("/api/session").then((s) => { window.JARVES_CSRF = s.csrf || ""; }).catch(() => {});

  // ------------------------------------------------------------------ Features: capability matrix
  const FILTERS = [["all", "All"], ["enabled", "Enabled"], ["not_ready_on_pi", "Not ready on Pi"], ["needs_attention", "Needs attention"]];
  async function pageFeatures(filter) {
    filter = FILTERS.some(([k]) => k === filter) ? filter : "all";
    const d = await api("/api/features");
    const keep = (f) => filter === "enabled" ? f.enabled : filter === "not_ready_on_pi" ? !f.pi_ready
      : filter === "needs_attention" ? f.needs_attention : true;
    const list = d.features.filter(keep);
    const tabs = el("div", { class: "row m4-tabs", role: "tablist" }, FILTERS.map(([k, label]) =>
      el("a", { href: "#/features/" + k, class: "pill plain" + (k === filter ? " ok" : ""), role: "tab",
        "aria-selected": k === filter ? "true" : "false" },
        label, " ", el("span", { class: "muted" }, String(d.features.filter((f) => k === "all" || (k === "enabled" ? f.enabled
          : k === "not_ready_on_pi" ? !f.pi_ready : f.needs_attention)).length)))));
    const rows = list.map((f) => {
      const msg = el("span", { class: "muted small" });
      const sw = el("button", { type: "button", class: "switch", "aria-pressed": f.enabled ? "true" : "false",
        "aria-label": `${f.title_en}: ${f.enabled ? "on" : "off"}`, onclick: async () => {
          if (!f.enabled && f.risk === "high" && !confirm(`Turn on ${f.title_en}? This is a high-risk feature.`)) return;
          try { await api("/api/features/" + encodeURIComponent(f.id), { method: "POST", body: JSON.stringify({ enabled: !f.enabled }) }); route(true); }
          catch (e) { msg.textContent = "Not changed: " + e.message; }
        } }, f.enabled ? "On" : "Off");
      const w = f.targets.windows, p = f.targets.linux_arm64_pi;
      const blockers = [w.blocker ? "Windows: " + w.blocker : null, p.blocker ? "Pi: " + p.blocker : null,
        ...(f.matrix_problems || []).map((x) => "matrix: " + x)].filter(Boolean);
      const health = f.health === "ok" ? pill("ok", "active") : f.health === "off" ? pill("plain", "off") : pill("unavailable", "unavailable");
      return el("tr", { class: f.needs_attention ? "m4-attn" : null },
        el("td", { "data-label": "Feature" }, el("b", {}, f.title_en), " ", el("span", { class: "muted", dir: "auto" }, f.title_he),
          el("div", { class: "muted small" }, f.about),
          f.requested ? el("div", { class: "small" }, "JARVES was asked to turn this on: approve here.") : null),
        el("td", { "data-label": "Enabled" }, sw, msg),
        el("td", { "data-label": "Health" }, health, f.reason ? el("div", { class: "small", dir: "auto" }, f.reason) : null),
        el("td", { "data-label": "Settings", class: "small" }, f.settings || "—"),
        el("td", { "data-label": "Permissions", class: "small" }, (f.permissions || []).join(", "), " ", pill(f.risk, f.risk)),
        el("td", { "data-label": "Windows" }, statusPill(w), w.evidence ? el("div", { class: "muted small" }, w.evidence) : null),
        el("td", { "data-label": "Pi" }, statusPill(p)),
        el("td", { "data-label": "Blocker", class: "small" }, blockers.length ? blockers.map((b) => el("div", {}, b)) : "—"),
        el("td", { "data-label": "Roadmap" }, p.roadmap_issue ? el("a", { href: p.roadmap_issue, target: "_blank", rel: "noopener noreferrer" },
          "#" + p.roadmap_issue.split("/").pop()) : "—"));
    });
    render(header("Features", "What JARVES may do, whether it works now, and its real status on Windows and on the Raspberry Pi. "
        + "'Built, not verified' means it was never run on that device. " + d.note),
      tabs, el("div", { style: "height:12px" }),
      el("div", { class: "card m4-scroll" }, rows.length ? el("table", { class: "m4-matrix" },
        el("thead", {}, el("tr", {}, ["Feature", "Enabled", "Health", "Settings", "Permissions", "Windows", "Pi", "Blocker", "Roadmap"]
          .map((h) => el("th", {}, h)))), el("tbody", {}, rows)) : el("div", { class: "empty" }, "Nothing in this filter.")),
      el("div", { class: "muted small", style: "margin-top:8px" }, "Last checked: ",
        [...new Set(d.features.map((f) => f.last_checked))].join(", "), " · Pi roadmap: ",
        el("a", { href: d.pi_roadmap, target: "_blank", rel: "noopener noreferrer" }, d.pi_roadmap)));
  }

  // ------------------------------------------------------------------ Identity & voice
  async function pageIdentity() {
    const d = await api("/api/identity");
    const nameMsg = el("span", { class: "muted small" });
    const disp = input({ value: d.display_name, maxlength: "40", "aria-label": "Display name" });
    const spoken = input({ value: d.spoken_name, maxlength: "40", dir: "auto", "aria-label": "Spoken name" });
    const names = el("div", { class: "card" }, el("h2", {}, "Name"),
      el("div", { class: "m4-form" }, field("Display name (written)", disp), field("Spoken name (how he says it)", spoken),
        el("button", { type: "button", class: "primary", onclick: async () => {
          try { await api("/api/identity/names", { method: "POST", body: JSON.stringify({ display_name: disp.value, spoken_name: spoken.value }) });
            nameMsg.textContent = "Saved: used from the next conversation."; }
          catch (e) { nameMsg.textContent = "Not saved: " + e.message; }
        } }, "Save name"), nameMsg),
      el("div", { class: "muted small" }, `Changing the name does not change the wake phrase: he still wakes on "${d.wake.phrase}".`));

    const w = d.wake, cap = w.capability;
    const wakeMsg = el("div", { class: "muted small" });
    const models = w.models.map((m) => el("tr", {},
      el("td", {}, el("b", {}, m.phrase), el("div", { class: "muted small" }, m.name)),
      el("td", { class: "small" }, m.eval ? `recall ${Math.round(m.eval.recall * 100)} % · ${m.eval.false_wakes} false wakes in ${Math.round(m.eval.hours * 60)} min (synthetic)` : "—"),
      el("td", { class: "small" }, m.threshold ?? "—"),
      el("td", {}, w.model === m.path ? pill("ok", "in use") : el("button", { type: "button", onclick: async () => {
        try { await api("/api/identity/wake", { method: "POST", body: JSON.stringify({ model: m.path }) }); wakeMsg.textContent = "Saved: restart JARVES to use it."; route(true); }
        catch (e) { wakeMsg.textContent = "Not changed: " + e.message; }
      } }, "Use"))));
    const wake = el("div", { class: "card" }, el("h2", {}, "Wake phrase"),
      el("div", { class: "kv" },
        el("div", { class: "k" }, "current phrase"), el("div", { class: "v" }, `${w.phrase} (${w.builtin ? "built-in model" : "trained on this device"})`),
        el("div", { class: "k" }, "Hey Jarvis: Windows"), el("div", { class: "v" }, statusPill(cap.targets.windows), " ", cap.targets.windows.evidence || ""),
        el("div", { class: "k" }, "Hey Jarvis: Pi"), el("div", { class: "v" }, statusPill(cap.targets.linux_arm64_pi), " ", cap.targets.linux_arm64_pi.blocker || ""),
        el("div", { class: "k" }, "custom phrase: Windows"), el("div", { class: "v" }, statusPill(cap.custom.windows), " ", cap.custom.windows.blocker || ""),
        el("div", { class: "k" }, "custom phrase: Pi"), el("div", { class: "v" }, statusPill(cap.custom.linux_arm64_pi), " ", cap.custom.linux_arm64_pi.blocker || "",
          cap.custom.linux_arm64_pi.roadmap_issue ? [" ", el("a", { href: cap.custom.linux_arm64_pi.roadmap_issue, target: "_blank", rel: "noopener noreferrer" }, "roadmap")] : null)),
      w.missing ? el("div", { class: "small" }, `The chosen model ${w.missing} is missing: using Hey Jarvis.`) : null,
      el("p", { class: "small" }, "A new phrase: run ", el("code", {}, w.train_command),
        " on this computer (free, local, about 3-5 minutes), then test it with your voice: ", el("code", {}, "jarvis wake test --model <file>"),
        " and count false wakes over 30 minutes of normal room sound: ", el("code", {}, "jarvis wake test --model <file> --false-wakes 30"), "."),
      models.length ? el("table", {}, el("thead", {}, el("tr", {}, ["Trained phrase", "Check", "Threshold", ""].map((h) => el("th", {}, h)))),
        el("tbody", {}, models)) : el("div", { class: "muted small" }, "No phrase trained yet."),
      w.builtin ? null : el("button", { type: "button", onclick: async () => {
        try { await api("/api/identity/wake", { method: "POST", body: JSON.stringify({ model: "hey_jarvis" }) }); route(true); }
        catch (e) { wakeMsg.textContent = e.message; }
      } }, "Back to Hey Jarvis"), wakeMsg);

    const vMsg = el("div", { class: "muted small" });
    const choose = async (v, approve) => {
      try {
        await api("/api/identity/voice", { method: "POST", body: JSON.stringify({ voice: v.id, approve_cost: approve === true }) });
        vMsg.textContent = `Voice set to ${v.voice}: heard from the next conversation.`; route(true);
      } catch (e) {
        if (!approve && /approv|switching/i.test(e.message) && confirm(`${e.message}\n\nApprove this change?`)) return choose(v, true);
        vMsg.textContent = "Not changed: " + e.message;
      }
    };
    const voiceRows = d.voices.map((v) => el("tr", {},
      el("td", {}, el("b", {}, v.voice), " ", d.voice.current === v.id ? pill("ok", "in use") : null),
      el("td", { class: "small" }, `${v.provider} ${v.model}`), el("td", { class: "small" }, v.about),
      el("td", { class: "num small" }, v.usd_per_minute != null ? `$${v.usd_per_minute}/min ${v.cost_kind === "estimate" ? "(est.)" : ""}` : "unknown"),
      el("td", { class: "small muted" }, "not available"),
      el("td", {}, d.voice.current === v.id ? null : el("button", { type: "button", onclick: () => choose(v) },
        v.mode === d.voice.mode ? "Use" : "Use (needs approval)"))));
    const voices = el("div", { class: "card" }, el("h2", {}, "Voice"),
      el("div", { class: "muted small" }, d.voices[0] ? d.voices[0].preview_note : "", " Rules: same model = ", d.rules.same_model,
        "; another model = ", d.rules.other_model, ". You can also say \"תחליף קול\"."),
      d.voice.notes.length ? el("div", { class: "small" }, d.voice.notes.join(" ")) : null,
      el("div", { class: "m4-scroll" }, el("table", {}, el("thead", {}, el("tr", {}, ["Voice", "Provider / model", "Accent / style", "Cost", "Preview", ""].map((h) => el("th", {}, h)))),
        el("tbody", {}, voiceRows))), vMsg);
    render(header("Identity & voice", "His name, the phrase that wakes him and the voice he speaks with. Three separate settings."),
      names, el("div", { style: "height:12px" }), wake, el("div", { style: "height:12px" }), voices);
  }

  // ------------------------------------------------------------------ Calendar
  async function pageCalendar() {
    const d = await api("/api/calendar");
    const g = d.google;
    const status = el("div", { class: "card" }, el("h2", {}, "Google"),
      el("div", { class: "kv" },
        el("div", { class: "k" }, "connected"), el("div", { class: "v" }, g.connected ? "yes" : (g.message_he || "no")),
        el("div", { class: "k" }, "read calendars"), el("div", { class: "v" }, g.calendar ? "allowed" : "not granted"),
        el("div", { class: "k" }, "add / change events"), el("div", { class: "v" }, d.write_enabled ? "on (every change is read back and approved)"
          : d.write_switch ? "switch on, but Google permission missing: jarvis google login --calendar-write" : "off (Features: calendar_write)"),
        el("div", { class: "k" }, "delete events"), el("div", { class: "v" }, d.deletion)),
      d.error ? el("div", { class: "small", dir: "auto" }, d.error) : null);
    if (!g.calendar) { render(header("Calendar", "Google Calendar: read, add and change events. Never delete."), status); return; }
    const out = el("div", { class: "card", hidden: true });
    const showPlan = (p) => {
      out.hidden = false;
      const msg = el("div", { class: "muted small" });
      out.replaceChildren(el("h2", {}, "Check before saving"), el("div", { dir: "auto" }, p.readback),
        el("div", { class: "row", style: "margin-top:10px" },
          el("button", { type: "button", class: "primary", onclick: async () => {
            try { const r = await api("/api/calendar/commit", { method: "POST", body: JSON.stringify({ preview_id: p.preview_id }) });
              out.replaceChildren(el("h2", {}, "Saved in Google Calendar"), el("div", { dir: "auto" }, r.message)); }
            catch (e) { msg.textContent = "Not saved: " + e.message; }
          } }, "Yes, save"), el("button", { type: "button", class: "ghost", onclick: () => { out.hidden = true; } }, "Cancel")), msg);
    };
    const preview = async (body, msg) => {
      try { showPlan(await api("/api/calendar/preview", { method: "POST", body: JSON.stringify(body) })); msg.textContent = ""; }
      catch (e) { msg.textContent = e.message; }
    };
    const cals = d.calendars.filter((c) => c.access === "owner" || c.access === "writer");
    const f = { title: input({ "aria-label": "Title", dir: "auto" }), day: input({ value: "tomorrow", "aria-label": "Day" }),
      start: input({ placeholder: "HH:MM", "aria-label": "Start" }), end: input({ placeholder: "HH:MM", "aria-label": "End" }),
      cal: el("select", { "aria-label": "Calendar" }, cals.map((c) => el("option", { value: c.primary ? "primary" : c.name }, c.name + (c.primary ? " (main)" : "")))),
      rec: el("select", { "aria-label": "Repeat" }, ["none", "daily", "weekly", "monthly", "yearly"].map((r) => el("option", { value: r }, r))),
      count: input({ placeholder: "times", "aria-label": "Repeat count" }), rem: input({ placeholder: "minutes before", "aria-label": "Reminder" }) };
    const cMsg = el("div", { class: "muted small" });
    const create = el("div", { class: "card" }, el("h2", {}, "Add an event"),
      el("div", { class: "m4-form" }, field("Title", f.title), field("Day (today / tomorrow / 2026-11-03)", f.day), field("Start", f.start),
        field("End", f.end), field("Calendar", f.cal), field("Repeat", f.rec), field("Times", f.count), field("Reminder", f.rem),
        el("button", { type: "button", class: "primary", disabled: !d.write_enabled, onclick: () => preview({ op: "create", title: f.title.value,
          day: f.day.value, start: f.start.value || null, end: f.end.value || null, calendar: f.cal.value, recurrence: f.rec.value,
          count: f.count.value ? Number(f.count.value) : null, reminder_minutes: f.rem.value ? [Number(f.rem.value)] : [] }, cMsg) }, "Preview")), cMsg);
    const evRows = d.events.map((e) => {
      const m = el("div", { class: "muted small" });
      const scope = el("select", { "aria-label": "Which occurrences" }, el("option", { value: "" }, "choose…"),
        el("option", { value: "this" }, "only this time"), el("option", { value: "series" }, "the whole series"));
      const t = input({ placeholder: "new title", dir: "auto", "aria-label": "New title" }), s = input({ placeholder: "new start HH:MM", "aria-label": "New start" });
      const edit = el("div", { class: "m4-form", hidden: true }, t, s, e.recurring ? scope : null,
        el("button", { type: "button", onclick: () => preview({ op: "update", handle: e.handle, title: t.value || null, start: s.value || null,
          scope: e.recurring ? (scope.value || null) : null }, m) }, "Preview change"), m);
      return el("tr", {}, el("td", { class: "small" }, e.when), el("td", { dir: "auto" }, e.title, e.recurring ? [" ", pill("plain", "repeats")] : null, edit),
        el("td", { class: "small" }, e.calendar),
        el("td", {}, d.write_enabled ? el("button", { type: "button", onclick: () => { edit.hidden = !edit.hidden; } }, "Change") : null));
    });
    render(header("Calendar", "Google Calendar: read, add and change events. JARVES never deletes events (no button, no voice command)."),
      status, el("div", { style: "height:12px" }), out, el("div", { style: "height:12px" }), create,
      el("h2", {}, "Next 7 days"), el("div", { class: "card m4-scroll" }, evRows.length ? el("table", {}, el("tbody", {}, evRows)) : el("div", { class: "empty" }, "Nothing in the next 7 days.")));
  }

  // ------------------------------------------------------------------ Devices (LAN pairing) + audit
  async function pageDevices() {
    const sess = await api("/api/session");
    if (sess.kind === "device") {
      const m = el("div", { class: "muted small" });
      render(header("This device", `Paired as "${sess.name}". Only the computer itself can pair other devices or read the audit log.`),
        el("div", { class: "card" }, el("button", { type: "button", onclick: async () => {
          try { await api(`/api/devices/${sess.device}/revoke`, { method: "POST", body: "{}" }); m.textContent = "Un-paired. Reload to confirm."; }
          catch (e) { m.textContent = e.message; }
        } }, "Un-pair this device"), m));
      return;
    }
    const [d, a] = await Promise.all([api("/api/devices"), api("/api/audit")]);
    const codeBox = el("div", { class: "m4-code", "aria-live": "polite" });
    const lan = el("div", { class: "card" }, el("h2", {}, "Home network"),
      d.lan ? [el("div", {}, "On: phones on your home Wi-Fi can open ", el("code", {}, d.pair_url), " and pair with a code. Never reachable from the internet."),
        el("div", { class: "row", style: "margin-top:10px" }, el("button", { type: "button", class: "primary", onclick: async () => {
          try { const c = await api("/api/devices/pair-code", { method: "POST", body: "{}" });
            codeBox.replaceChildren(el("div", { class: "muted small" }, "Pairing code (5 minutes, one use):"), el("div", { class: "m4-big" }, c.code.replace(/(\d{4})(\d{4})/, "$1 $2")),
              el("div", { class: "muted small" }, "On the phone: " + c.pair_url)); }
          catch (e) { codeBox.textContent = e.message; }
        } }, "Pair a phone")), codeBox]
        : el("div", {}, "Off: this computer only. To use your phone: ", el("code", {}, d.how), "."),
      el("div", { class: "muted small", style: "margin-top:8px" }, "Plain HTTP on your home network: only pair on a Wi-Fi you trust."));
    const devs = d.devices.map((x) => el("tr", {}, el("td", {}, x.name), el("td", { class: "small" }, x.id),
      el("td", { class: "small" }, x.last_seen ? `${day(x.last_seen)} ${clock(x.last_seen)}` : "—"), el("td", { class: "small" }, x.last_ip || ""),
      el("td", {}, x.revoked ? pill("plain", "revoked") : el("button", { type: "button", onclick: async () => {
        await api(`/api/devices/${x.id}/revoke`, { method: "POST", body: "{}" }); route(true);
      } }, "Revoke"))));
    const audit = a.entries.slice(0, 100).map((x) => el("tr", {}, el("td", { class: "small" }, `${day(x.ts)} ${clock(x.ts)}`),
      el("td", {}, x.event), el("td", { class: "small" }, [x.principal, x.method, x.path, x.status, x.device, x.ip, x.reason].filter(Boolean).join(" · "))));
    render(header("Devices", "Who may open this dashboard, and a log of every change made through it."),
      lan, el("h2", {}, "Paired devices"),
      el("div", { class: "card m4-scroll" }, devs.length ? el("table", {}, el("thead", {}, el("tr", {}, ["Name", "Id", "Last seen", "Address", ""].map((h) => el("th", {}, h)))), el("tbody", {}, devs))
        : el("div", { class: "empty" }, "No paired devices.")),
      el("h2", {}, "Audit log (changes and approvals)"),
      el("div", { class: "card m4-scroll" }, audit.length ? el("table", {}, el("tbody", {}, audit)) : el("div", { class: "empty" }, "Nothing logged yet.")));
  }

  window.JARVES_PAGES = Object.assign(window.JARVES_PAGES || {}, {
    features: pageFeatures, identity: pageIdentity, calendar: pageCalendar, devices: pageDevices,
  });
})();
