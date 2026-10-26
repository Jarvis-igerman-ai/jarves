// App-shell cache only: the phone opens instantly and shows an honest "computer unreachable" state. API calls are
// never cached (conversation text and approvals must always come from the PC).
"use strict";
const SHELL = "jarves-m-v2-r1";
const FILES = ["/m/", "/m/app.js", "/m/style.css", "/m/m_theme.css", "/m/m_shell.js", "/m/icon.svg", "/m/manifest.webmanifest"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES))); self.skipWaiting(); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL).map((k) => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || !u.pathname.startsWith("/m/")) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
