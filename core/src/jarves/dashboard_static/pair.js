// Pairing page for a phone on the home network (dashboard_auth.py). No framework, textContent only.
"use strict";
document.getElementById("pairForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = document.getElementById("msg");
  const code = document.getElementById("code").value.replace(/\D/g, "");
  msg.textContent = "Pairing…";
  try {
    const r = await fetch("/api/pair", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Jarves": "1" },
      body: JSON.stringify({ code, name: document.getElementById("name").value }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { msg.textContent = d.error || "Not paired."; return; }
    msg.textContent = "Paired. Opening the dashboard…";
    location.href = "/";
  } catch (e) { msg.textContent = "No connection to JARVES."; }
});
