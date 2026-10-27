# Browser pilot: what a real driver must do (design note for #566 / #574, 27 Oct 2026)

Status: design note. `core/src/jarves/browser_pilot.py` (B1) is a session with caps, receipts and URL vetting, tested with fake
drivers only. No real browser is wired to it. This note lists what must be true before one is, because the session logic cannot
prove that a browser honours the pins (#574 review of PR 571).

## The contract the session already enforces (when a session is made)

`open`, `text`, `screenshot`, `close`, `pin_hosts(mapping)`, `current_url` (property or callable). The session resolves every name
ONCE, vets all answers (private, loopback, link-local, CGNAT, metadata, SIIT, site-local refused), hands only vetted addresses to
`pin_hosts`, and re-checks the final URL after `open`. A driver that merely has these names but ignores them is not detected: `hasattr` only.

## What the real driver must add (all items are acceptance criteria for the driver PR)

| # | Gap | Required in the driver | Test it needs |
|---|---|---|---|
| 1 | Chromium `--host-resolver-rules` is a launch flag: a host first seen in a redirect or subresource cannot be pinned later; Playwright has no runtime resolver API | A small local forward proxy owned by the pilot that connects ONLY to the vetted IP of a name (and resolves each new name through `request_allowed` first), with the browser set to use it. Fallback: `MAP * ~NOTFOUND` plus a relaunch per new host | A fake upstream with two answers for one name (public then private): the proxy connects to the first |
| 2 | Hosts missing from the pin map | The proxy refuses any host not in the map (default deny); `localhost` is mapped explicitly to loopback, never resolved | A request to an unmapped name is refused |
| 3 | `page.route` does not see WebSocket, WebRTC/STUN, service-worker fetches, some prefetch/preconnect | Playwright `context.route_web_socket` for sockets; browser launched with WebRTC disabled (`--disable-features=WebRtc...` / forced proxy) ; `service_workers="block"` on the context; `--disable-features=NetworkPrediction`; the proxy is the safety net for anything that bypasses `route` | A page that opens a WebSocket / RTCPeerConnection to a private address is blocked |
| 4 | An exception from `pin_hosts` | Session: catch, stop the session, refuse (fail closed); route handler: always call `route.abort()` on any exception | `pin_hosts` raising stops the session |
| 5 | Each timed-out `getaddrinfo` leaves a thread | One shared resolver executor with a cap | Many timeouts do not grow threads past the cap |
| 6 | The final-URL check after `open` is post-hoc | The driver calls `request_allowed` for every request including the document and each redirect hop BEFORE sending it (route handler), so a private redirect target is never fetched; the post-check only blocks reading | A redirect chain public -> private never reaches the private fake server |
| 7 | `vet_url` ignores the port | Allow only 80/443 (and an explicit local port range for loopback pages the owner points to); refuse other ports on public hosts (`example.com:22`) | Port 22 on a public name is refused |

Items 4, 5 and 7 are small and independent of a driver: they can land in `browser_pilot.py` before the driver. Items 1 to 3 and 6 are
the driver itself and need a real-browser test job (the existing real-browser CI job can run a loopback test server with two names).

## Order

1. After the 4.5 code freeze: PR A = items 4, 5, 7 (session code and fake-driver tests).
2. PR B = the proxy (item 1, 2, 6) as its own module with tests against local fake upstreams, no browser.
3. PR C = the Playwright driver (item 3) using PR B, behind the existing `browser_pilot` switch (OFF, beta), one real-browser test.
4. Independent security review of each; never wire the driver without the proxy.
