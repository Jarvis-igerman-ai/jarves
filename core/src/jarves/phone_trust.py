"""Trusted phone connection, path A (issue #513): a small LOCAL certificate authority (CA) so the phone PWA gets a trusted https
connection and Chrome lets it register its service worker / install as an app. OFF by default (switch `phone_trust`, beta).

Shape (security review of PR #676): a ROOT certificate (what the owner installs on the phone) whose key exists only in memory
for one signature and is NEVER written anywhere, and one NAME-CONSTRAINED INTERMEDIATE CA signed by that root. Only the
intermediate's key stays on disk (owner-only). Leaf certificates are signed by the intermediate and served together with it.
Why: many verifiers (Android's platform verifier among them) ignore name constraints in a trust anchor, but every RFC 5280
verifier enforces them in an intermediate. Since the root key is gone, nothing but this intermediate can ever chain to the root.
Files in <home>/mobile_tls/ca/: `root.crt` (public, the phone installs it), `ca.crt` (intermediate, public), `ca.key`
(intermediate key, private). Safety rules (see docs/plans/TRUSTED_PHONE_CONNECTION_2026-10-26.md):
  * the key is written owner-only (0600 and, on Windows, an owner-only ACL by SID), never served, never logged, never copied;
  * the intermediate is NAME-CONSTRAINED (critical): private IPv4 ranges and local names only, no e-mail or URI names, so even a
    stolen key cannot make a certificate a verifier accepts for a public site;
  * CA life <= 730 days, leaf life 30 days, renewed automatically (also while the server runs); `revoke` deletes the key;
  * no network access and no third party; the clock is injectable (`now`) so the tests use a fake clock.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from . import logs

TLS_DIR = "mobile_tls"
CA_DIR = "ca"
CA_COMMON_NAME = "JARVES local CA"
ISSUER_COMMON_NAME = "JARVES local CA (private network only)"
CA_MAX_DAYS = 730
LEAF_DAYS = 30
RENEW_BEFORE_DAYS = 10
WARN_LEAF_DAYS = 7
WARN_CA_DAYS = 30
RENEW_CHECK_S = 6 * 3600                                  # a running phone server re-checks its leaf this often
CA_BACKDATE = _dt.timedelta(days=1)                       # clock skew between PC and phone
PERMITTED_NETS = ("127.0.0.1/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
# RFC 5280 form (no leading dot): "local" permits "local" and every name under it (pc.local). Only reserved names: "localhost"
# (RFC 6761), "local" (mDNS, RFC 6762) and "home.arpa" (RFC 8375). "lan" is not reserved, so it is not permitted.
PERMITTED_DNS = ("localhost", "local", "home.arpa")
# e-mail and URI names: a permitted subtree in the reserved "invalid" domain (RFC 6761) = no real name of that kind is allowed.
NOTHING = "invalid"
LEAF_NAME = "leaf"
STATE_FILE = "trust_state.json"
CA_ROUTE = "/api/m/trust/ca"


def tls_dir(home: Path) -> Path:
    return Path(home) / TLS_DIR


def ca_dir(home: Path) -> Path:
    return tls_dir(home) / CA_DIR


def ca_paths(home: Path) -> tuple[Path, Path]:
    """(the PUBLIC root certificate the phone installs, the PRIVATE intermediate key)."""
    d = ca_dir(home)
    return d / "root.crt", d / "ca.key"


def issuer_paths(home: Path) -> tuple[Path, Path]:
    """(the intermediate certificate, its private key)."""
    d = ca_dir(home)
    return d / "ca.crt", d / "ca.key"


def leaf_paths(home: Path) -> tuple[Path, Path]:
    d = tls_dir(home)
    return d / f"{LEAF_NAME}.crt", d / f"{LEAF_NAME}.key"


def _utc(now: _dt.datetime | None) -> _dt.datetime:
    n = now or _dt.datetime.now(_dt.timezone.utc)
    return n if n.tzinfo else n.replace(tzinfo=_dt.timezone.utc)


# ---------------------------------------------------------------------- state (public facts only, never key material)
def _state_path(home: Path) -> Path:
    return tls_dir(home) / STATE_FILE


def _state(home: Path) -> dict:
    try:
        d = json.loads(_state_path(home).read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(home: Path, **kw) -> None:
    try:
        st = _state(home)
        st.update(kw)
        p = _state_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(st, sort_keys=True), "utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


# ---------------------------------------------------------------------- files
_SID: list[str | None] = []


def _current_sid() -> str | None:
    """The current user's SID (`whoami /user`): an ACL by SID cannot be confused by a user or group with the same name."""
    if _SID:
        return _SID[0]
    sid = None
    try:
        r = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=15, check=False)
        m = re.search(r"(S-1-[0-9-]+)", r.stdout or "")
        sid = m.group(1) if (r.returncode == 0 and m) else None
    except (OSError, subprocess.SubprocessError):
        sid = None
    _SID.append(sid)
    return sid


def _owner_only_acl(path: Path, run: Callable[[list[str]], object] | None = None) -> bool:
    """Windows: drop inherited rights and grant only the current user (by SID; chmod 0600 does little there). Never raises.
    Returns True when the rights were tightened (or there is nothing to do off Windows); a failure is recorded so `status`
    can say "permissions not tightened". `run` is injectable so the test checks the command without touching the real ACL."""
    if sys.platform != "win32" and run is None:
        return True
    sid = _current_sid()
    who = f"*{sid}" if sid else (os.environ.get("USERNAME") or "")
    ok = False
    if who:
        grant = f"{who}:(F)" if path.is_file() else f"{who}:(OI)(CI)F"
        cmd = ["icacls", str(path), "/inheritance:r", "/grant:r", grant]
        try:
            r = (run or (lambda c: subprocess.run(c, capture_output=True, timeout=15, check=False)))(cmd)
            ok = getattr(r, "returncode", 0) == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    if not ok:
        logs.warn("mobile", "PHONE_TRUST_ACL_FAILED", "could not tighten the access rights of a trust file")
    return ok


def _write_private(path: Path, data: bytes, run=None) -> bool:
    """Owner-only from the first byte (0600 at create, ACL before the rename), then an atomic replace."""
    from .mobile import write_private
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    write_private(tmp, data)
    ok = _owner_only_acl(tmp, run)
    os.replace(tmp, path)
    return ok


def _write_public(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------------- the CA
def _name_constraints():
    from cryptography import x509
    nets = [x509.IPAddress(ipaddress.ip_network(n)) for n in PERMITTED_NETS]
    dns = [x509.DNSName(d) for d in PERMITTED_DNS]
    nothing = [x509.RFC822Name(NOTHING), x509.UniformResourceIdentifier(NOTHING)]
    return x509.NameConstraints(permitted_subtrees=nets + dns + nothing, excluded_subtrees=None)


def _ca_key_usage():
    from cryptography import x509
    return x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False, data_encipherment=False,
                         key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False)


def create_ca(home: Path, now: _dt.datetime | None = None, run=None) -> dict:
    """Create the CA when there is none (idempotent: an existing CA is kept). Returns status().
    The root key lives only in this function's memory: it signs the intermediate once and is dropped."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    root_p, key_p = ca_paths(home)
    inter_p, _ = issuer_paths(home)
    if root_p.is_file() and inter_p.is_file() and key_p.is_file():
        return status(home, now)
    n = _utc(now)
    start, end = n - CA_BACKDATE, n + _dt.timedelta(days=CA_MAX_DAYS)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
                           x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVES (this computer only)")])
    root = (x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name).public_key(root_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
            .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
            .add_extension(_ca_key_usage(), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False)
            .sign(root_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    inter_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ISSUER_COMMON_NAME),
                            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVES (this computer only)")])
    inter = (x509.CertificateBuilder().subject_name(inter_name).issuer_name(root_name).public_key(key.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
             .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
             .add_extension(_ca_key_usage(), critical=True)
             .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
             .add_extension(_name_constraints(), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), critical=False)
             .sign(root_key, hashes.SHA256()))
    del root_key                                          # never written, never returned: nothing else can chain to the root
    ca_dir(home).mkdir(parents=True, exist_ok=True)
    acl = [_owner_only_acl(tls_dir(home), run), _owner_only_acl(ca_dir(home), run)]
    acl.append(_write_private(key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()), run))
    _write_public(inter_p, inter.public_bytes(serialization.Encoding.PEM))
    _write_public(root_p, root.public_bytes(serialization.Encoding.PEM))
    _record(home, acl_failed=not all(acl))            # shown by status as "permissions not tightened"
    logs.info("mobile", "PHONE_TRUST_CA_CREATED", "a local phone certificate authority was created",
              fingerprint=fingerprint(home))
    return status(home, now)


def _load(p: Path):
    from cryptography import x509
    try:
        return x509.load_pem_x509_certificate(p.read_bytes())
    except (OSError, ValueError):
        return None


def _load_ca_cert(home: Path):
    """The root (what the phone trusts)."""
    return _load(ca_paths(home)[0])


def _load_issuer_cert(home: Path):
    """The name-constrained intermediate (what signs leaves)."""
    return _load(issuer_paths(home)[0])


def fingerprint(home: Path) -> str | None:
    """SHA-256 of the ROOT certificate (DER), 'AB:CD:..': the owner compares it with the phone's certificate details."""
    der = ca_certificate_der(home)
    if der is None:
        return None
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def ca_certificate_pem(home: Path) -> bytes | None:
    """The PUBLIC root certificate only (what a paired phone may download). No key file is ever read here."""
    from cryptography.hazmat.primitives import serialization
    c = _load_ca_cert(home)
    return c.public_bytes(serialization.Encoding.PEM) if c is not None else None


def ca_certificate_der(home: Path) -> bytes | None:
    from cryptography.hazmat.primitives import serialization
    c = _load_ca_cert(home)
    return c.public_bytes(serialization.Encoding.DER) if c is not None else None


def _ca_ready(home: Path) -> bool:
    root_p, key_p = ca_paths(home)
    return root_p.is_file() and key_p.is_file() and issuer_paths(home)[0].is_file()


# ---------------------------------------------------------------------- leaf certificates
_DNS_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def permitted(host: str) -> bool:
    """Would the CA's name constraints allow this name or address?"""
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in ipaddress.ip_network(n) for n in PERMITTED_NETS)
    except ValueError:
        pass
    h = host.rstrip(".").lower()
    if not _DNS_NAME.match(h):
        return False
    return any(h == d or h.endswith("." + d) for d in PERMITTED_DNS)


def _host_local_name() -> str | None:
    """'<computer name>.local' (mDNS) when the computer name is a valid DNS label; None otherwise."""
    try:
        n = socket.gethostname().strip().lower()
    except OSError:
        return None
    n = n.split(".")[0]
    return f"{n}.local" if n and _DNS_NAME.match(n) else None


def lan_hosts(extra: list[str] | None = None) -> list[str]:
    """The names the phone uses: localhost, 127.0.0.1, the ONE home-network address the phone server prints (lan_ip, no
    packet sent) and '<computer>.local'. Not every adapter address (Hyper-V, WSL or VPN adapters would widen the SAN for
    nothing). Only names the CA may vouch for are kept."""
    out: list[str] = ["localhost", "127.0.0.1"]
    try:
        from .dashboard_auth import lan_ip
        ip = lan_ip()
        if ip:
            out.append(ip)
    except Exception:
        pass
    name = _host_local_name()
    if name:
        out.append(name)
    out += list(extra or [])
    seen: list[str] = []
    for h in out:
        h = str(h).strip().lower()
        if h and h not in seen and permitted(h):
            seen.append(h)
    return seen


def _sans(hosts: list[str]) -> list:
    from cryptography import x509
    out = []
    for h in hosts:
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            out.append(x509.DNSName(h))
    return out


def issue_leaf(home: Path, hosts: list[str], now: _dt.datetime | None = None, run=None) -> tuple[Path, Path]:
    """Sign a fresh 30-day server certificate (new key) for `hosts` with the intermediate. leaf.crt holds the leaf AND the
    intermediate (the server sends both). Raises RuntimeError when there is no CA or no host the CA may vouch for."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID
    inter_p, key_p_ca = issuer_paths(home)
    try:
        ca = x509.load_pem_x509_certificate(inter_p.read_bytes())
        ca_key = serialization.load_pem_private_key(key_p_ca.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as e:
        raise RuntimeError("no JARVES local CA: run jarvis phone trust enable") from e
    hosts = [h for h in hosts if permitted(h)]
    if not hosts:
        raise RuntimeError("no private-network name or address to vouch for")
    n = _utc(now)
    if ca.not_valid_after_utc - n <= _dt.timedelta(days=1):
        raise RuntimeError("the JARVES local CA has expired: run jarvis phone trust rotate")
    not_after = min(n + _dt.timedelta(days=LEAF_DAYS), ca.not_valid_after_utc)
    key = ec.generate_private_key(ec.SECP256R1())
    # Empty subject + CRITICAL SAN (RFC 5280 4.2.1.6): browsers match only the SAN, and an IP in the common name would be read
    # as a DNS name by some verifiers and fail the name constraints.
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([])).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(n - CA_BACKDATE).not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName(_sans(hosts)), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    crt_p, key_p = leaf_paths(home)
    acl = _owner_only_acl(tls_dir(home), run)          # the folder first, so nothing in it is ever readable by others
    acl = _write_private(key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()), run) and acl   # the key first, then the cert
    if not acl:
        _record(home, acl_failed=True)
    _write_public(crt_p, cert.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    logs.info("mobile", "PHONE_TRUST_LEAF_ISSUED", "a phone server certificate was issued", count=len(hosts))
    return crt_p, key_p


def _signed_by(cert, ca) -> bool:
    try:
        cert.verify_directly_issued_by(ca)
        return True
    except Exception:
        return False


def leaf_info(home: Path) -> dict | None:
    from cryptography import x509
    crt_p, _ = leaf_paths(home)
    try:
        c = x509.load_pem_x509_certificate(crt_p.read_bytes())          # the first certificate in the file: the leaf
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, x509.ExtensionNotFound):
        return None
    ca = _load_issuer_cert(home)
    return {"not_after": c.not_valid_after_utc, "hosts": sorted(str(x.value) for x in san),
            "issued_by_current_ca": ca is not None and _signed_by(c, ca)}


def ensure_leaf(home: Path, hosts: list[str] | None = None, now: _dt.datetime | None = None,
                run=None) -> tuple[Path, Path] | None:
    """The leaf to serve: reuse it while it has more than RENEW_BEFORE_DAYS left, covers every wanted host and was signed by the
    CURRENT CA; otherwise issue a new one. None when there is no CA or it could not be issued (the caller falls back to the
    self-signed certificate). Every outcome is recorded (time, error) for `status` and `doctor`."""
    if not _ca_ready(home):
        return None
    n = _utc(now)
    try:
        want = sorted({h for h in (hosts if hosts is not None else lan_hosts()) if permitted(h)})
        info = leaf_info(home)
        lc, lk = leaf_paths(home)
        if (info and lc.is_file() and lk.is_file() and info["issued_by_current_ca"]
                and info["not_after"] - n > _dt.timedelta(days=RENEW_BEFORE_DAYS) and set(want) <= set(info["hosts"])):
            got = (lc, lk)
        else:
            got = issue_leaf(home, want, n, run)
    except (RuntimeError, OSError, ValueError) as e:
        logs.warn("mobile", "PHONE_TRUST_LEAF_FAILED", "could not issue a phone server certificate", error=str(e)[:200])
        _record(home, renew_checked=n.isoformat(), renew_error=str(e)[:200])
        return None
    _record(home, renew_checked=n.isoformat(), renew_ok=n.isoformat(), renew_error=None)
    return got


_FEATS: dict[str, object] = {}


def trust_on(home: Path) -> bool:
    """The beta feature `phone_trust` (default OFF; the beta gate applies). Any failure to read it means OFF. One Features
    object per home is kept (it re-reads its file when it changes), so the phone page's polling does not rebuild it."""
    try:
        from .features import Features
        k = str(Path(home))
        f = _FEATS.get(k)
        if f is None:
            f = _FEATS[k] = Features(None, Path(home))
        return f.enabled(FEATURE)
    except Exception:
        return False


def tls_pair(home: Path, enabled: bool, now: _dt.datetime | None = None) -> tuple[Path, Path] | None:
    """Used by tailnet.choose_tls: the local-CA leaf, only when the owner switched `phone_trust` on and a CA exists."""
    if not enabled:
        return None
    return ensure_leaf(home, None, now)


def phone_view(home: Path, tls_source: str) -> dict:
    """What the paired phone page needs for its hint (#513): how this server proves itself, and whether a CA certificate can be
    downloaded. Public facts only (never a path or key material)."""
    src = tls_source if tls_source in ("tailscale", "localca", "selfsigned", "none") else "none"
    try:
        ca = trust_on(home) and _ca_ready(home)
    except OSError:
        ca = False
    return {"source": src, "trusted_source": src in ("tailscale", "localca"), "ca_download": bool(ca)}


# ---------------------------------------------------------------------- verify
def verify_chain(leaf_pem: bytes, ca_pem: bytes, host: str, now: _dt.datetime | None = None) -> tuple[bool, str]:
    """Validate `leaf_pem` (the leaf, then any intermediates) against ONLY `ca_pem` (the root) for `host` at `now`: chain,
    name constraints, dates, host name, serverAuth. No network. Returns (ok, reason)."""
    from cryptography import x509
    from cryptography.x509 import verification as v
    try:
        chain = x509.load_pem_x509_certificates(leaf_pem)
        ca = x509.load_pem_x509_certificate(ca_pem)
        try:
            subject = v.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            subject = v.DNSName(host)
        verifier = v.PolicyBuilder().store(v.Store([ca])).time(_utc(now).replace(tzinfo=None)).build_server_verifier(subject)
        verifier.verify(chain[0], chain[1:])
        return True, "ok"
    except v.VerificationError as e:
        return False, str(e)
    except (ValueError, TypeError) as e:
        return False, f"bad certificate: {e}"


def verify_listener(home: Path, host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Handshake with the running phone listener from this PC, trusting ONLY the JARVES root (no system store, no internet)."""
    crt_p, _ = ca_paths(home)
    if not crt_p.is_file():
        return False, "no JARVES local CA"
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    ctx.load_verify_locations(cadata=crt_p.read_text("ascii"))
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host):
                return True, "the phone page is served with a certificate the JARVES CA signed"
    except ssl.SSLCertVerificationError as e:
        return False, f"not trusted: {e.verify_message}"
    except (OSError, ssl.SSLError) as e:
        return False, f"could not connect to {host}:{port}: {e}"


# ---------------------------------------------------------------------- status / revoke
def status(home: Path, now: _dt.datetime | None = None) -> dict:
    """What `jarvis phone trust status` and `doctor` show. Public facts only: the key's PRESENCE, never its path or bytes.
    `warnings`: leaf_expiring (< 7 days), ca_expiring (< 30 days), ca_expired, renew_failing, permissions_not_tightened."""
    root = _load_ca_cert(home)
    inter = _load_issuer_cert(home)
    n = _utc(now)
    leaf = leaf_info(home)
    has_key = ca_paths(home)[1].is_file()
    st = _state(home)
    ready = root is not None and inter is not None and has_key
    end = inter.not_valid_after_utc if inter is not None else (root.not_valid_after_utc if root is not None else None)
    expired = bool(end and end <= n)
    warn: list[str] = []
    if ready and expired:
        warn.append("ca_expired")
    elif ready and end - n < _dt.timedelta(days=WARN_CA_DAYS):
        warn.append("ca_expiring")
    if ready and leaf and leaf["not_after"] - n < _dt.timedelta(days=WARN_LEAF_DAYS):
        warn.append("leaf_expiring")
    if ready and st.get("renew_error"):
        warn.append("renew_failing")
    if ready and st.get("acl_failed"):
        warn.append("permissions_not_tightened")
    return {"ca": ready, "usable": ready and not expired, "ca_cert_without_key": root is not None and not has_key,
            "ca_expires": end.date().isoformat() if end else None, "ca_expired": expired,
            "ca_days_left": (end - n).days if end else None, "fingerprint": fingerprint(home),
            "leaf_expires": leaf["not_after"].date().isoformat() if leaf else None,
            "leaf_days_left": (leaf["not_after"] - n).days if leaf else None,
            "leaf_hosts": leaf["hosts"] if leaf else [], "ca_file": str(ca_paths(home)[0]),
            "renew_checked": st.get("renew_checked"), "renew_ok": st.get("renew_ok"), "renew_error": st.get("renew_error"),
            "permissions_tightened": not st.get("acl_failed"), "warnings": warn}


WARNING_TEXT = {
    "ca_expired": ("תוקף הרשות פג: הריצו \"jarvis phone trust rotate\"", "the CA has expired: run \"jarvis phone trust rotate\""),
    "ca_expiring": ("תוקף הרשות יפוג בקרוב (פחות מ-30 יום): הריצו \"jarvis phone trust rotate\" והתקינו מחדש בטלפון",
                    "the CA expires in less than 30 days: run jarvis phone trust rotate and install it again on the phone"),
    "leaf_expiring": ("אישור השרת יפוג בפחות מ-7 ימים והחידוש לא קרה: הריצו \"jarvis phone trust renew\"",
                      "the server certificate expires in less than 7 days and was not renewed: run jarvis phone trust renew"),
    "renew_failing": ("החידוש האוטומטי של אישור השרת נכשל: הריצו \"jarvis phone trust renew\" וקראו את השגיאה",
                      "the automatic server certificate renewal is failing: run jarvis phone trust renew and read the error"),
    "permissions_not_tightened": ("ההרשאות של קובצי האישור לא הוגבלו לבעלים בלבד: הריצו \"jarvis phone trust rotate\"",
                                  "the trust files' permissions were not tightened to the owner only: run jarvis phone trust rotate"),
}


def doctor_rows(home: Path, now: _dt.datetime | None = None) -> list[tuple[str, str, str]]:
    """Rows for `jarvis doctor` (level, what, detail): nothing when there is no CA."""
    st = status(home, now)
    if not st["ca"]:
        return []
    if not st["warnings"]:
        return [("ok", f"trusted phone connection: CA valid until {st['ca_expires']}, server certificate until "
                       f"{st['leaf_expires'] or 'the next phone server start'}", "")]
    level = "fail" if "ca_expired" in st["warnings"] else "warn"
    return [(level, "trusted phone connection: " + WARNING_TEXT[w][1], "") for w in st["warnings"]]


def revoke(home: Path) -> dict:
    """Delete the CA key, the certificates and the leaf (nothing can be signed afterwards); the server falls back to the
    self-signed certificate at its next start. The phone keeps the PUBLIC root until the owner removes it there."""
    removed = 0
    for p in (*ca_paths(home), issuer_paths(home)[0], *leaf_paths(home)):
        try:
            if p.is_file():
                p.unlink()
                removed += 1
        except OSError:
            pass
    for p in (_state_path(home),):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        ca_dir(home).rmdir()
    except OSError:
        pass
    logs.warn("mobile", "PHONE_TRUST_REVOKED", "the local phone certificate authority was removed", files=removed)
    return {"removed": removed}


def rotate(home: Path, now: _dt.datetime | None = None, run=None) -> dict:
    """A NEW CA (new root, new key, new fingerprint) in place of the old one, plus a fresh leaf. The phone must remove the old
    certificate and install the new one. Returns status() with the old fingerprint as `previous`."""
    old = fingerprint(home)
    revoke(home)
    create_ca(home, now, run)
    ensure_leaf(home, None, now, run)
    st = status(home, now)
    st["previous"] = old
    logs.warn("mobile", "PHONE_TRUST_ROTATED", "the local phone certificate authority was replaced")
    return st


# ---------------------------------------------------------------------- live renewal (no mutation of a live context)
def server_context(cert: Path, key: Path) -> ssl.SSLContext:
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    c.minimum_version = ssl.TLSVersion.TLSv1_2
    c.load_cert_chain(str(cert), str(key))
    return c


class LeafSelector:
    """Per-connection choice of the server context. A renewed leaf goes into a NEW SSLContext; the one listening context only
    gets an `sni_callback` (set once, before the server serves) that points each new handshake at the current context. It runs
    for every ClientHello, also without SNI (a phone that opens an IP address sends none)."""

    def __init__(self, base: ssl.SSLContext) -> None:
        self._lock = threading.Lock()
        self._current = base
        base.sni_callback = self._pick

    @property
    def current(self) -> ssl.SSLContext:
        with self._lock:
            return self._current

    def swap(self, ctx: ssl.SSLContext) -> None:
        with self._lock:
            self._current = ctx

    def _pick(self, sslobj, _name, _ctx) -> None:
        cur = self.current
        if sslobj.context is not cur:
            sslobj.context = cur
        return None


def keep_fresh(home: Path, sctx: ssl.SSLContext, interval_s: float = RENEW_CHECK_S,
               stop: threading.Event | None = None, hosts: Callable[[], list[str]] | None = None) -> threading.Thread:
    """A running phone server lives longer than a 30-day leaf: every `interval_s` re-run ensure_leaf and, when a new leaf was
    issued, build a NEW context with it and hand new handshakes to it (LeafSelector). Call it BEFORE the server starts serving
    (it sets the listening context's sni_callback once). Daemon thread, no network, never dies on an error (logged and
    recorded for status/doctor). `t.stop.set()` ends it; `t.selector` is the selector."""
    stop = stop or threading.Event()
    sel = LeafSelector(sctx)

    def stamp() -> int:
        try:
            return leaf_paths(home)[0].stat().st_mtime_ns
        except OSError:
            return 0

    def loop() -> None:
        last = stamp()
        while not stop.wait(interval_s):
            try:
                if not trust_on(home):
                    continue
                got = ensure_leaf(home, hosts() if hosts else None)
                if got is None or stamp() == last:
                    continue
                sel.swap(server_context(got[0], got[1]))
                last = stamp()
                logs.info("mobile", "PHONE_TRUST_LEAF_RELOADED", "the phone server now uses a renewed certificate")
            except Exception as e:                        # never let the renewer die silently
                logs.warn("mobile", "PHONE_TRUST_RELOAD_FAILED", "could not load the renewed certificate",
                          error=type(e).__name__)
                _record(home, renew_error=f"reload: {type(e).__name__}")

    t = threading.Thread(target=loop, name="jarves-phone-trust-renew", daemon=True)
    t.stop = stop  # type: ignore[attr-defined]
    t.selector = sel  # type: ignore[attr-defined]
    t.start()
    return t


# ---------------------------------------------------------------------- catalog row (feature `phone_trust`, beta, OFF)
from . import capabilities  # noqa: E402
from . import features as feat  # noqa: E402

FEATURE = "phone_trust"
EVIDENCE = ("UNIT test_phone_trust.py (fake clock, no network: chain validation with cryptography, name constraints, renewal, "
            "rotate, revoke, backup leaves the keys out, the paired-phone download route)")
BLOCKER = "NOT-RUN: never installed on a real Android phone; Chrome install criteria not yet seen to pass (owner-gated)"
ABOUT = ("a trusted https connection for the phone app, so Chrome lets the phone page install as an app. Path A: JARVES makes its "
         "own small local certificate authority (name-constrained to private-network addresses, key stays on this PC, you can "
         "revoke it) and you install its public certificate on your phone yourself. Path B: guided steps for a real Tailscale "
         "certificate. Optional and off by default")
PI = "the phone page is hosted by the PC in V6; there is no Pi path"
PERMISSION = "you install the JARVES certificate on your own phone (Android asks for your screen lock)"
# Hebrew for these texts: hebrew_parts/2026-10-27-phone-trust.json and the dashboard locale part of the same name.
feat.register(FEATURE, "חיבור מהימן לטלפון", "Trusted phone connection", False, risk="high", about=ABOUT,
              needs=["mobile_access"], stage="beta",
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=[PERMISSION],
              settings="jarvis features on phone_trust, then jarvis phone trust enable")
capabilities.declare_extra(FEATURE, phone={"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER,
                                           "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/513"})
