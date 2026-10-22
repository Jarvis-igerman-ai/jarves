# Phone access over Tailscale (setup guide, English)

Hebrew: [PHONE_ACCESS_TAILSCALE_HE.md](PHONE_ACCESS_TAILSCALE_HE.md). Owner decision 2026-10-21: the phone connects over the home Wi-Fi **and** over Tailscale. Phone access stays **OFF by default**; nothing is exposed until you turn on the `mobile_access` switch.

## What JARVES does and does not do
- JARVES only **detects** Tailscale (an address in `100.64.0.0/10`, optionally the `tailscale` command). It never installs Tailscale, never signs in, never changes your tailnet.
- Tailscale clients can reach **only** the phone paths (`/m/...`, `/api/m/...`). The rest of the dashboard stays on the PC.
- Pairing, per-phone tokens, scopes, rate limits and the audit log are the same as on the home network.

## Setup (you do these steps)
1. Install Tailscale on the PC and on the phone, and sign in to the **same account** (https://tailscale.com/download).
2. `jarvis features on mobile_access`, then start JARVES (or `jarvis dashboard --mobile`).
3. Check: `jarvis phone status` shows `Tailscale: ready (100.x.y.z, name)`. The dashboard **Phone** page shows the same status row, the **tailnet address** and a **separate QR code** (button "Pair over Tailscale").
4. Scan the tailnet QR with the phone (Tailscale connected on the phone).

## Listen on the tailnet only
Phone page switch "Listen on the Tailscale address only", or `jarvis phone bind tailnet` (back: `jarvis phone bind lan`). The server then binds to the 100.x address, **never** to all interfaces; if no Tailscale address exists it refuses to start instead of falling back. Applies at the next start. CLI one-off: `jarvis dashboard --mobile --tailnet-only`.

## Secure connection (needed for voice)
- If the tailnet has HTTPS certificates enabled (Tailscale admin console, DNS page), JARVES runs `tailscale cert` for the machine name and serves HTTPS with a trusted certificate. Voice then works with no warning.
- Otherwise it uses the existing self-signed certificate (`jarvis dashboard --mobile --tls-selfsigned`, one warning on the phone) or plain HTTP (text only).
- With a Tailscale certificate, use the tailnet address; the home-network address then shows a name-mismatch warning.

## Phone approvals and a lost phone
Off by default. When you switch it on (Phone page, confirmation with a warning, or `jarvis phone approvals on`) a paired phone with the `approvals` scope may approve **every risk level except typed-code requests** (those stay on the PC). Each phone answer is written to the audit log (device, decision, risk, kind; no text). Lost phone: **Revoke all phones** on the Phone page or `jarvis phone revoke-all`.

## Troubleshooting
- "not installed" / "not connected": install or sign in to Tailscale yourself, then restart JARVES.
- Phone cannot reach the PC: Tailscale must be connected on both; Windows firewall must allow the JARVES port for the Tailscale network.
