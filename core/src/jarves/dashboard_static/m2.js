// SW-V2 M2 pages: Appearance (face / 2D / 3D avatar / hidden) and New Skills (review -> approve -> grant -> enable).
// Uses the helpers of app.js (el, api, render, header, pill) and registers in PAGES (pages.js). Text via textContent only.
"use strict";

const PLATFORM = { verified_on_device: "verified", implemented_unverified: "not yet verified", planned: "planned",
  blocked: "blocked", not_applicable: "n/a" };
function platformCell(t) {
  if (!t) return el("span", { class: "muted small" }, "—");
  const cls = t.status === "verified_on_device" ? "ok" : t.status === "blocked" ? "fail" : t.status === "planned" ? "plain" : "medium";
  return el("div", {}, pill(cls, PLATFORM[t.status] || t.status),
    t.evidence ? el("div", { class: "muted small" }, txh(t, "evidence")) : null,
    t.blocker ? el("div", { class: "small" }, tx("חסם: ", "Blocker: ") + txh(t, "blocker")) : null,
    t.roadmap_issue ? el("a", { class: "small", href: t.roadmap_issue, target: "_blank", rel: "noopener" }, "roadmap issue") : null);
}

PAGES.appearance = async function () {
  const d = await api("/api/appearance");
  const msg = el("span", { class: "muted small" });
  const rows = d.modes.map((m) => el("tr", {},
    el("td", {}, el("button", { type: "button", class: m.id === d.mode ? "primary" : "", "aria-pressed": m.id === d.mode ? "true" : "false",
      onclick: async () => {
        try { await api("/api/appearance", { method: "POST", body: JSON.stringify({ mode: m.id }) }); msg.textContent = "Switched; the screen follows within a second."; route(true); }
        catch (e) { msg.textContent = "Not changed: " + e.message; }
      } }, m.id === d.mode ? "In use" : "Use")),
    el("td", {}, el("b", {}, txo(m, "title"))),
    el("td", {}, platformCell(m.targets.windows)), el("td", {}, platformCell(m.targets.linux_arm64_pi))));
  render(header(tx("מראה", "Appearance"), tx("איך JARVES נראה על המסך. ", "How JARVES looks on the screen. ") + d.note
      + tx(" אפשר גם לומר \"תחליף לאווטאר תלת מימד\".", " You can also say \"תחליף לאווטאר תלת מימד\".")),
    el("div", { class: "card" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, ""), el("th", {}, "Presentation"),
      el("th", {}, "Windows"), el("th", {}, "Raspberry Pi"))), el("tbody", {}, rows)), el("div", { style: "height:8px" }), msg));
};

function capLabel(c) { return c === "storage" ? "own small local storage" : c.startsWith("network:") ? "network: " + c.slice(8) : c; }

PAGES.newskills = async function (arg) {
  const d = await api("/api/skills/generated");
  const msg = el("span", { class: "muted small" });
  const act = async (id, what, body, okText) => {
    try { await api(`/api/skills/generated/${encodeURIComponent(id)}/${what}`, { method: "POST", body: JSON.stringify(body || {}) }); msg.textContent = okText; route(true); }
    catch (e) { msg.textContent = "Not done: " + e.message; }
  };
  const drafts = d.drafts.map((x) => {
    const r = x.report || {};
    const box = el("div", { class: "card", style: "margin-bottom:12px" },
      el("div", { class: "row" }, el("b", {}, r.title || x.id), r.ok ? pill("ok", "validation passed") : pill("fail", "validation failed"),
        r.upgrade_of ? pill("plain", `new version of v${r.upgrade_of}`) : null,
        el("span", { class: "muted small" }, `proposed by ${(x.meta || {}).proposed_by || "?"}`)),
      el("div", { dir: "auto", style: "margin-top:6px" }, r.summary_he || ""), el("div", { class: "muted small" }, r.summary_en || ""),
      r.has_code ? el("div", { class: "small", style: "margin-top:6px;color:#b45309" },
        "⚠ This Skill includes Python code. It runs in a restricted child process (no OS modules, no file/network except what you grant), but on Windows the code is NOT OS-sandboxed: approve only code you understand.") : null,
      (r.errors || []).length ? el("ul", { class: "small" }, r.errors.map((e) => el("li", {}, e))) : null,
      (r.warnings || []).length ? el("ul", { class: "small muted" }, r.warnings.map((e) => el("li", {}, "Warning: " + e))) : null);
    const files = el("div", {});
    const show = el("button", { type: "button", class: "ghost", onclick: async () => {
      const f = await api("/api/skills/generated/" + encodeURIComponent(x.id));
      files.replaceChildren(el("h3", {}, "SKILL.md"), el("pre", { class: "file", dir: "auto" }, f.skill_md),
        f.skill_py ? [el("h3", {}, "skill.py"), el("pre", { class: "file" }, f.skill_py)] : el("div", { class: "muted small" }, "No code: instructions only."));
    } }, "Review files");
    box.append(el("div", { class: "row", style: "margin-top:8px" }, show,
      el("button", { type: "button", class: "primary", disabled: !r.ok, onclick: () => {
        if (confirm(`Install "${r.title || x.id}"? It installs DISABLED; you grant and enable it next.`))
          act(x.id, "approve", { sha256: r.sha256 }, "Installed (disabled).");
      } }, "Approve & install (disabled)"),
      el("button", { type: "button", onclick: () => act(x.id, "reject", {}, "Draft discarded.") }, "Reject")), files);
    return box;
  });
  const installed = d.installed.map((s) => {
    const caps = s.requested.map((c) => {
      const cb = el("input", { type: "checkbox", value: c });
      cb.checked = (s.grants.capabilities || []).includes(c);
      return el("label", { class: "small", style: "margin-right:12px" }, cb, " " + capLabel(c));
    });
    const hasActions = s.tools.some((t) => t.permission === "action");
    const actCb = el("input", { type: "checkbox" }); actCb.checked = !!s.grants.actions;
    const save = el("button", { type: "button", onclick: () => act(s.id, "grant", {
      capabilities: caps.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value), actions: actCb.checked }, "Grants saved.") }, "Save grants");
    return el("div", { class: "card", style: "margin-bottom:12px" },
      el("div", { class: "row" }, el("button", { type: "button", class: "switch", "aria-pressed": s.enabled ? "true" : "false",
        "aria-label": `${s.title}: ${s.enabled ? "on" : "off"}`, onclick: () => act(s.id, s.enabled ? "disable" : "enable", {}, s.enabled ? "Disabled." : "Enabled.") },
        s.enabled ? "On" : "Off"), el("b", {}, s.title), pill("plain", "v" + s.version), el("span", { class: "muted small" }, s.description)),
      el("div", { dir: "auto", class: "small", style: "margin-top:6px" }, s.summary_he),
      s.has_code ? el("div", { class: "small", style: "margin-top:6px;color:#b45309" },
        "⚠ Runs Python code; on Windows it is NOT OS-sandboxed.") : null,
      el("div", { class: "small", style: "margin-top:6px" }, "Tools: ", s.tools.map((t) => el("code", { style: "margin-right:8px" }, `${t.name} (${t.permission})`))),
      el("div", { style: "margin-top:8px" }, el("b", { class: "small" }, "Grants: "), caps.length ? caps : el("span", { class: "muted small" }, "requests nothing "),
        hasActions ? el("label", { class: "small" }, actCb, " allow its action tools") : null, " ", save),
      s.last_error ? el("div", { class: "small" }, `Last error (${s.last_error.tool}): ${s.last_error.error}`) : null,
      el("div", { class: "row", style: "margin-top:8px" },
        el("button", { type: "button", disabled: s.versions.length < 2, onclick: () => act(s.id, "rollback", {}, "Rolled back (disabled).") }, "Roll back to previous version"),
        el("button", { type: "button", class: "ghost", onclick: () => { if (confirm(`Uninstall ${s.title}? (kept in skills/removed)`)) act(s.id, "uninstall", {}, "Uninstalled."); } }, "Uninstall")));
  });
  render(header("New Skills", "Skills JARVES drafted for you. Nothing runs before you approve it; it installs OFF, then you grant only what it needs and switch it on. Off means off for voice, schedules, dashboard and the AI."),
    el("div", { class: "row", style: "margin-bottom:10px" }, el("span", { class: "small" }, "Windows: "), platformCell(d.targets.windows),
      el("span", { class: "small" }, "Raspberry Pi: "), platformCell(d.targets.linux_arm64_pi)),
    el("h2", {}, `Waiting for your review (${d.drafts.length})`), drafts.length ? drafts : el("div", { class: "card empty" }, "No drafts. Ask JARVES for a new ability, e.g. \"תכין לי כישור של רשימת קניות\"."),
    el("h2", {}, `Installed (${d.installed.length})`), installed.length ? installed : el("div", { class: "card empty" }, "No generated Skills installed."), msg);
};
