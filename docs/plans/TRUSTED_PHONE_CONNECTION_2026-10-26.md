# Trusted phone connection (issue #513, follows #510 and #194)

Status: design, 26 Oct 2026. Target 4.5.1. Optional, default OFF, beta via the gate (feature `phone_trust`).
Needs an independent Opus security review before it is queued: it creates a certificate authority and touches phone auth.

## Headline
Today the phone page is served with a self-signed certificate. Chrome on Android then refuses the service worker and the
WebAPK install ("Add to Home screen" becomes a plain shortcut). Two optional ways to a TRUSTED https connection:

- Path A (no third party): JARVES makes its own small LOCAL certificate authority (CA). The phone installs the CA once.
- Path B: Tailscale gives a real certificate (`tailscale cert`) for the PC's tailnet name; nothing to install on the phone.

## Path A design
- Shape (after the security review of PR #676): a throwaway ROOT and one name-constrained INTERMEDIATE. The root key exists
  only in memory: it signs the intermediate once and is dropped, never written. Reason: many verifiers (Android's platform verifier
  among them) ignore name constraints in a trust anchor, while every RFC 5280 verifier enforces them in an intermediate. With the
  root key gone, nothing but this intermediate can ever chain to the root.
- Files (JARVES home, `mobile_tls/ca/`): `root.crt` (public, the file the owner installs on the phone), `ca.crt` (intermediate,
  public) and `ca.key` (intermediate key, private). The key never leaves the PC, is never served, never logged, never in a backup
  bundle or diagnostics, is written 0600 via a temp file and `os.replace`, and (Windows) gets an owner-only ACL by SID (`icacls`,
  the folders first). A failed ACL is recorded and `status` says "permissions not tightened".
- Root: EC P-256, at most 730 days, `BasicConstraints(ca=True, path_length=1)`, `KeyUsage(keyCertSign, cRLSign)`, no constraints.
- Intermediate: EC P-256, same validity, `BasicConstraints(ca=True, path_length=0)`, `KeyUsage(keyCertSign, cRLSign)`,
  `ExtendedKeyUsage(serverAuth)`, critical `NameConstraints` that PERMIT only: IP ranges 127.0.0.1/32, 10.0.0.0/8,
  172.16.0.0/12, 192.168.0.0/16; DNS `localhost`, `local`, `home.arpa` (RFC 5280 form, no leading dot; `lan` dropped: not a
  reserved name); e-mail and URI only in the reserved `invalid` domain (= none). Public names are refused by the chain itself, so
  even a stolen intermediate key cannot mint a trusted certificate for them. Honest limit: it CAN vouch for any private address or
  local name, on any Wi-Fi the phone joins. Tailnet names (100.64/10, `*.ts.net`) are not permitted: Path B covers those.
- Leaf certificate: EC P-256, 30 days (limit 398), empty subject + critical SAN = localhost, 127.0.0.1, the one home-network
  address the server prints (`lan_ip`) and `<computer>.local` (not every adapter: Hyper-V, WSL, VPN); `KeyUsage(digitalSignature)`,
  `ExtendedKeyUsage(serverAuth)`, not a CA, a fresh key per leaf. `leaf.crt` holds the leaf AND the intermediate (both are sent).
  Renewed at phone-server start and every 6 hours while it runs (under 10 days left, or the SAN set changed). A renewed leaf goes
  into a NEW `SSLContext`; the listening context only has an `sni_callback` (set once before serving) that hands each new
  handshake the current context, so a live context is never mutated. Every renewal outcome (time, error) is recorded;
  `jarvis phone trust status` and `jarvis doctor` warn when the leaf has < 7 days, the CA < 30 days, or renewal is failing.
- Order of TLS sources at start: Tailscale cert, then the local CA leaf (only when `phone_trust` is on and a CA exists), then the
  old self-signed certificate (unchanged fallback).
- Delivering the CA certificate to the phone: only to an ALREADY PAIRED phone, `GET /api/m/trust/ca` (device token required,
  rate limited, public certificate only). The first pairing happens over the old self-signed warning, as today. A one-time-code QR
  is slice 2 if the owner wants it.
- Android steps (shown in the card and the guide): Settings > Security (or Security and privacy > More security) > Install a
  certificate (Encryption and credentials) > CA certificate > "Install anyway" > pick the downloaded file. Android shows a
  warning that the network may be monitored: that is the generic text for ANY user CA. We explain it (below).
- Verify: `jarvis phone trust verify` does a TLS handshake to the running phone listener from the PC with ONLY the JARVES root as trust
  anchor and checks chain, host name and dates (cryptography `x509.verification`, no internet). Slice 2 adds the on-phone check
  (the page registers its service worker only when the connection is trusted).
- Revoke/remove: `jarvis phone trust revoke` deletes the key, both CA certificates and the leaf, so nothing can be signed any more;
  a running server keeps its current leaf until it restarts, then falls back to self-signed. The phone still holds the public CA certificate: the command prints the removal steps
  (Settings > Security > Encryption and credentials > User credentials > JARVES local CA > Remove). A public certificate without its
  key is harmless.

## What trusting a CA means (said honestly in the card and guide)
Anyone who holds the CA key could make certificates this phone accepts. Android's generic warning is about that. Here the key stays on
this PC, and the intermediate may only sign private-network names (name constraints), so it cannot be used against the phone's internet traffic,
provided the phone enforces name constraints. Chrome and Android's system verifier do; some apps and older Android versions may not,
which is why the card says: install only on your own phone, remove when you sell or lose the PC (or run revoke), and prefer Path B
if you already use Tailscale. If the PC is compromised, the CA key is too: revoke and remove from the phone.

## Threat model
| Threat | Mitigation |
|---|---|
| Another local user reads the CA key | 0600 plus owner-only Windows ACL; key only in the JARVES home; never served or logged |
| Malware as the same user steals the key | out of scope (it already owns the dashboard token); mitigated by name constraints and 2-year life; revoke |
| Key stolen, used for public sites | name constraints exclude public names; chain validation test proves it |
| Someone on the LAN downloads the CA cert | it is public; the route needs a paired device token anyway |
| Someone on the LAN swaps the CA during delivery | delivery is over the already paired authenticated connection; the card shows the SHA-256 fingerprint to compare |
| Leaf outlives a change of address | 30 days, SAN check at start, auto renew |
| Forgotten CA stays trusted | 2-year cap, `status` shows expiry, `revoke` |
| Trust confusion (owner thinks it is a Tailscale/third-party thing) | wording in guide and card |
| Phone auth bypass through the new route | only GET, public certificate, same rate limit and token check as other `/api/m/` routes |

## Path B design (guidance in slice 1)
`jarvis phone status` already detects Tailscale. Guided steps: install on PC and phone, sign in (the owner, in Tailscale's own app:
JARVES never sees credentials or API keys), enable HTTPS certificates in the Tailscale admin console, then `tailscale cert` for the
machine's tailnet name (already in `tailnet.tailnet_cert`), `phone bind tailnet`, verify. Nothing new is stored.

## Slices
1. Done (branch a2-trust): server side of path A: `phone_trust.py`, `jarvis phone trust status|enable|rotate|renew|verify|revoke|ca-path`
   (enable, rotate and revoke need a typed yes or `--yes`), the paired-phone CA route, TLS source order, live leaf renewal
   (`keep_fresh` renews every 6 hours and hands new handshakes a new context), feature `phone_trust` (beta, OFF, risk high,
   SAFE_OFF in the wizard), capabilities row, guide EN+HE, CLI parity, hermetic tests. Zero prompt additions.
   Path B guidance: `jarvis phone trust tailscale` (detection only; says the next step). Phone page: a text-only hint on
   "This phone" when the connection is not trusted (server source not tailscale/localca, or the browser refused the service
   worker), with a "Download the certificate" button only when a CA exists and the feature is on (fetch with the device token,
   saved as a file; the owner installs it himself).
   Leaf profile (Chrome/Android): EC P-256, empty subject + critical SAN, KeyUsage digitalSignature, EKU serverAuth, 30 days
   (limit 398), AKI/SKI, sent with the intermediate. CA: a throwaway root plus a name-constrained intermediate (see Path A).
2. The Home setup card (#457 pattern), the dashboard Phone page buttons, the on-phone verify, one-time-code QR if asked,
   and the manifest/service worker/icon fix of branch `phone-ui` (#510) so the install criteria pass once trusted.

## Owner questions (defaults in OWNER_OPENS.md)
1. Ship Path A at all, or only Path B? Default: ship both, both OFF, beta.
2. One-time-code QR for the CA download, in addition to the paired page? Default: no (smaller attack surface).
