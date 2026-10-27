"""Tailscale support for phone access (owner decision 2026-10-21, V5). DETECTION AND GUIDANCE ONLY.

JARVES never installs Tailscale, never logs in to it and never changes the tailnet. It only
  - recognises a tailnet address on this computer (100.64.0.0/10, what Tailscale hands out),
  - optionally asks the `tailscale` command for its own name (`tailscale status --json`) and for a certificate
    (`tailscale cert`, which works only when the owner enabled HTTPS certificates in the Tailscale admin console),
  - lets the phone server bind to that address only (never 0.0.0.0 in that mode).
Everything that touches the system is injectable (`addresses`, `run`, `which`), so the tests use fake interfaces and a
fake command: no network, no real Tailscale.
"""
from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Callable

from . import logs

TAILNET_NET = ipaddress.ip_network("100.64.0.0/10")
CMD_TIMEOUT_S = 15
CERT_NAME = "tailnet"


def is_tailnet_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    return a.version == 4 and a in TAILNET_NET


def local_addresses() -> list[str]:
    """IPv4 addresses of this computer (no packet is sent)."""
    out: list[str] = []
    try:
        for fam, _t, _p, _c, sa in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if sa[0] not in out:
                out.append(sa[0])
    except OSError:
        pass
    return out


def _run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CMD_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def detect(addresses: list[str] | None = None, run: Callable[[list[str]], str | None] | None = None,
           which: Callable[[str], str | None] | None = None) -> dict:
    """{present, ip, name, cli, certs_possible, how}. `present` = a tailnet address exists on this computer."""
    addrs = local_addresses() if addresses is None else list(addresses)
    which = which or shutil.which
    run = run or _run
    cli = which("tailscale")
    ip = next((a for a in addrs if is_tailnet_ip(a)), None)
    name = None
    if cli:
        if ip is None:                                   # the CLI knows the address even when the adapter list does not
            out = run([cli, "ip", "-4"])
            first = (out or "").split()[:1]
            ip = first[0] if first and is_tailnet_ip(first[0]) else None
        if ip is not None:
            out = run([cli, "status", "--json"])
            try:
                dns = ((json.loads(out) if out else {}).get("Self") or {}).get("DNSName") or ""
            except (ValueError, AttributeError):
                dns = ""
            name = dns.rstrip(".").lower() or None
    present = ip is not None
    how = ("ready" if present else "not-installed" if not cli else "not-connected")
    return {"present": present, "ip": ip, "name": name, "cli": bool(cli), "certs_possible": bool(cli and name),
            "state": how}


def tailnet_cert(home: Path, name: str | None, run: Callable[[list[str]], str | None] | None = None,
                 which: Callable[[str], str | None] | None = None) -> tuple[Path, Path] | None:
    """`tailscale cert` for this machine's tailnet name into <home>/mobile_tls/. None when it is not available (no CLI,
    HTTPS certificates not enabled for the tailnet, ...): the caller then uses the self-signed path."""
    which = which or shutil.which
    cli = which("tailscale")
    if not (cli and name):
        return None
    d = Path(home) / "mobile_tls"
    d.mkdir(parents=True, exist_ok=True)
    cert, key = d / f"{CERT_NAME}.crt", d / f"{CERT_NAME}.key"
    run = run or _run
    out = run([cli, "cert", "--cert-file", str(cert), "--key-file", str(key), name])
    if out is None or not (cert.is_file() and key.is_file()):
        logs.warn("mobile", "MOBILE_TAILNET_CERT_UNAVAILABLE", "tailscale cert is not available, using the self-signed path",
                  tailnet=True)
        return None
    try:
        key.chmod(0o600)
    except OSError:
        pass
    return cert, key


def choose_tls(home: Path, info: dict, run=None, which=None,
               trust: bool | None = None) -> tuple[tuple[Path, Path] | None, str]:
    """(cert and key | None, source). Order: `tailscale cert` when the tailnet is ready -> the JARVES local-CA leaf when the
    owner switched `phone_trust` on and made a CA (#513) -> the certificate already in mobile_tls (own files or the
    self-signed one) -> none (plain http, text only)."""
    if info.get("present") and info.get("name"):
        got = tailnet_cert(home, info["name"], run, which)
        if got:
            return got, "tailscale"
    from . import phone_trust
    leaf = phone_trust.tls_pair(home, phone_trust.trust_on(home) if trust is None else trust)
    if leaf:
        return leaf, "localca"
    from .mobile import tls_paths
    cert, key = tls_paths(home)
    if cert.is_file() and key.is_file():
        return (cert, key), "selfsigned"
    return None, "none"
