"""Trusted phone connection, path A (issue #513): a small LOCAL certificate authority (CA) so the phone PWA gets a trusted https
connection and Chrome lets it register its service worker / install as an app. OFF by default (switch `phone_trust`, beta).

What it is: JARVES makes a CA once (`ca.crt` public, `ca.key` private, in <home>/mobile_tls/ca/), then signs short-lived "leaf"
certificates for this PC's LAN names and addresses. The phone installs ONLY the public `ca.crt`.
Safety rules (security review required, see docs/plans/TRUSTED_PHONE_CONNECTION_2026-10-26.md):
  * the CA key is written owner-only (0600 and, on Windows, an owner-only ACL), never served, never logged, never copied;
  * the CA is NAME-CONSTRAINED (critical): it may only vouch for private-network addresses and local names, so even a stolen key
    cannot make a certificate for a public site;
  * CA life <= 730 days, leaf life 30 days, renewed automatically; `revoke` deletes the key (nothing can be signed afterwards);
  * no network access and no third party; the clock is injectable (`now`) so the tests use a fake clock.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
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
CA_MAX_DAYS = 730
LEAF_DAYS = 30
RENEW_BEFORE_DAYS = 10
RENEW_CHECK_S = 6 * 3600                                  # a running phone server re-checks its leaf this often
CA_BACKDATE = _dt.timedelta(days=1)                       # clock skew between PC and phone
PERMITTED_NETS = ("127.0.0.1/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
# RFC 5280 form (no leading dot): "local" permits "local" and every name under it (pc.local). Every verifier reads it the same
# way; the leading-dot form is an OpenSSL/Chromium extension that some Android verifiers treat differently.
PERMITTED_DNS = ("localhost", "local", "lan", "home.arpa")
LEAF_NAME = "leaf"
CA_ROUTE = "/api/m/trust/ca.crt"


def ca_dir(home: Path) -> Path:
    return Path(home) / TLS_DIR / CA_DIR


def ca_paths(home: Path) -> tuple[Path, Path]:
    d = ca_dir(home)
    return d / "ca.crt", d / "ca.key"


def leaf_paths(home: Path) -> tuple[Path, Path]:
    d = Path(home) / TLS_DIR
    return d / f"{LEAF_NAME}.crt", d / f"{LEAF_NAME}.key"


def _utc(now: _dt.datetime | None) -> _dt.datetime:
    n = now or _dt.datetime.now(_dt.timezone.utc)
    return n if n.tzinfo else n.replace(tzinfo=_dt.timezone.utc)


# ---------------------------------------------------------------------- files
def _owner_only_acl(path: Path, run: Callable[[list[str]], object] | None = None) -> None:
    """Windows: drop inherited rights and grant only the current user (chmod 0600 does little there). Best effort, never raises.
    `run` is injectable so the test checks the command without touching the real ACL."""
    if sys.platform != "win32" and run is None:
        return
    user = os.environ.get("USERNAME") or ""
    if not user:
        return
    grant = f"{user}:(F)" if path.is_file() else f"{user}:(OI)(CI)F"
    cmd = ["icacls", str(path), "/inheritance:r", "/grant:r", grant]
    try:
        (run or (lambda c: subprocess.run(c, capture_output=True, timeout=15, check=False)))(cmd)
    except (OSError, subprocess.SubprocessError):
        logs.warn("mobile", "PHONE_TRUST_ACL_FAILED", "could not tighten the access rights of a trust file")


def _write_private(path: Path, data: bytes, run=None) -> None:
    from .mobile import write_private
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, data)
    _owner_only_acl(path, run)


# ---------------------------------------------------------------------- the CA
def _name_constraints():
    from cryptography import x509
    nets = [x509.IPAddress(ipaddress.ip_network(n)) for n in PERMITTED_NETS]
    dns = [x509.DNSName(d) for d in PERMITTED_DNS]
    return x509.NameConstraints(permitted_subtrees=nets + dns, excluded_subtrees=None)


def create_ca(home: Path, now: _dt.datetime | None = None, run=None) -> dict:
    """Create the CA when there is none (idempotent: an existing CA is kept). Returns status()."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    crt_p, key_p = ca_paths(home)
    if crt_p.is_file() and key_p.is_file():
        return status(home, now)
    n = _utc(now)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVES (this computer only)")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(n - CA_BACKDATE).not_valid_after(n + _dt.timedelta(days=CA_MAX_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(_name_constraints(), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    crt_p.parent.mkdir(parents=True, exist_ok=True)
    _owner_only_acl(crt_p.parent, run)
    _write_private(key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()), run)
    crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    logs.info("mobile", "PHONE_TRUST_CA_CREATED", "a local phone certificate authority was created",
              fingerprint=fingerprint(home))
    return status(home, now)


def _load_ca_cert(home: Path):
    from cryptography import x509
    crt_p, _ = ca_paths(home)
    try:
        return x509.load_pem_x509_certificate(crt_p.read_bytes())
    except (OSError, ValueError):
        return None


def fingerprint(home: Path) -> str | None:
    """SHA-256 of the CA certificate (DER), 'AB:CD:..': the owner compares it with the phone's certificate details."""
    der = ca_certificate_der(home)
    if der is None:
        return None
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def ca_certificate_pem(home: Path) -> bytes | None:
    """The PUBLIC CA certificate only (what a paired phone may download). The key file is never read here."""
    from cryptography.hazmat.primitives import serialization
    c = _load_ca_cert(home)
    return c.public_bytes(serialization.Encoding.PEM) if c is not None else None


def ca_certificate_der(home: Path) -> bytes | None:
    from cryptography.hazmat.primitives import serialization
    c = _load_ca_cert(home)
    return c.public_bytes(serialization.Encoding.DER) if c is not None else None


# ---------------------------------------------------------------------- leaf certificates
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


_DNS_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def _host_local_name() -> str | None:
    """'<computer name>.local' (mDNS) when the computer name is a valid DNS label; None otherwise."""
    try:
        n = socket.gethostname().strip().lower()
    except OSError:
        return None
    n = n.split(".")[0]
    return f"{n}.local" if n and _DNS_NAME.match(n) else None


def lan_hosts(extra: list[str] | None = None) -> list[str]:
    """The names and addresses the phone may use: localhost, 127.0.0.1 and this PC's private IPv4 addresses. Only names the CA
    may vouch for are kept (others are dropped, never signed)."""
    out: list[str] = ["localhost", "127.0.0.1"]
    try:
        from .dashboard_auth import lan_ip                # the address the phone server prints (no packet is sent)
        ip = lan_ip()
        if ip:
            out.append(ip)
    except Exception:
        pass
    try:
        from .tailnet import local_addresses
        out += local_addresses()
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
    """Sign a fresh 30-day server certificate (new key) for `hosts` with the CA. Raises RuntimeError when there is no CA or
    no host the CA may vouch for."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID
    ca_crt, ca_key_p = ca_paths(home)
    try:
        ca = x509.load_pem_x509_certificate(ca_crt.read_bytes())
        ca_key = serialization.load_pem_private_key(ca_key_p.read_bytes(), password=None)
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
    _write_private(key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()), run)
    crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
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
        c = x509.load_pem_x509_certificate(crt_p.read_bytes())
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, x509.ExtensionNotFound):
        return None
    ca = _load_ca_cert(home)
    return {"not_after": c.not_valid_after_utc, "hosts": sorted(str(x.value) for x in san),
            "issued_by_current_ca": ca is not None and _signed_by(c, ca)}


def ensure_leaf(home: Path, hosts: list[str] | None = None, now: _dt.datetime | None = None,
                run=None) -> tuple[Path, Path] | None:
    """The leaf to serve: reuse it while it has more than RENEW_BEFORE_DAYS left, covers every wanted host and was signed by the
    CURRENT CA; otherwise issue a new one. None when there is no CA (the caller falls back to the self-signed certificate)."""
    crt_p, key_p = ca_paths(home)
    if not (crt_p.is_file() and key_p.is_file()):
        return None
    want = sorted({h for h in (hosts if hosts is not None else lan_hosts()) if permitted(h)})
    info = leaf_info(home)
    n = _utc(now)
    lc, lk = leaf_paths(home)
    if (info and lc.is_file() and lk.is_file() and info["issued_by_current_ca"]
            and info["not_after"] - n > _dt.timedelta(days=RENEW_BEFORE_DAYS) and set(want) <= set(info["hosts"])):
        return lc, lk
    try:
        return issue_leaf(home, want, n, run)
    except RuntimeError as e:
        logs.warn("mobile", "PHONE_TRUST_LEAF_FAILED", "could not issue a phone server certificate", error=str(e))
        return None


def trust_on(home: Path) -> bool:
    """The beta feature `phone_trust` (default OFF; the beta gate applies). Any failure to read it means OFF."""
    try:
        from .features import Features
        return Features(None, Path(home)).enabled(FEATURE)
    except Exception:
        return False


def tls_pair(home: Path, enabled: bool, now: _dt.datetime | None = None) -> tuple[Path, Path] | None:
    """Used by tailnet.choose_tls: the local-CA leaf, only when the owner switched `phone_trust` on and a CA exists."""
    if not enabled:
        return None
    return ensure_leaf(home, None, now)


# ---------------------------------------------------------------------- verify
def verify_chain(leaf_pem: bytes, ca_pem: bytes, host: str, now: _dt.datetime | None = None) -> tuple[bool, str]:
    """Validate `leaf_pem` against ONLY `ca_pem` for `host` at `now` (chain, name constraints, dates, host name, serverAuth).
    No network. Returns (ok, reason)."""
    from cryptography import x509
    from cryptography.x509 import verification as v
    try:
        leaf = x509.load_pem_x509_certificate(leaf_pem)
        ca = x509.load_pem_x509_certificate(ca_pem)
        try:
            subject = v.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            subject = v.DNSName(host)
        verifier = v.PolicyBuilder().store(v.Store([ca])).time(_utc(now).replace(tzinfo=None)).build_server_verifier(subject)
        verifier.verify(leaf, [])
        return True, "ok"
    except v.VerificationError as e:
        return False, str(e)
    except (ValueError, TypeError) as e:
        return False, f"bad certificate: {e}"


def verify_listener(home: Path, host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Handshake with the running phone listener from this PC, trusting ONLY the JARVES CA (no system store, no internet)."""
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
    """What `jarvis phone trust status` shows. Public facts only: the key's PRESENCE, never its path or bytes."""
    ca = _load_ca_cert(home)
    n = _utc(now)
    leaf = leaf_info(home)
    has_key = ca_paths(home)[1].is_file()
    return {"ca": ca is not None and has_key, "ca_cert_without_key": ca is not None and not has_key,
            "ca_expires": ca.not_valid_after_utc.date().isoformat() if ca else None,
            "ca_expired": bool(ca and ca.not_valid_after_utc <= n), "fingerprint": fingerprint(home),
            "leaf_expires": leaf["not_after"].date().isoformat() if leaf else None,
            "leaf_hosts": leaf["hosts"] if leaf else [], "ca_file": str(ca_paths(home)[0])}


def revoke(home: Path) -> dict:
    """Delete the CA key, the CA certificate and the leaf (nothing can be signed afterwards); the server falls back to the
    self-signed certificate. The phone keeps the PUBLIC certificate until the owner removes it there (steps in the guide)."""
    removed = 0
    for p in (*ca_paths(home), *leaf_paths(home)):
        try:
            if p.is_file():
                p.unlink()
                removed += 1
        except OSError:
            pass
    try:
        ca_dir(home).rmdir()
    except OSError:
        pass
    logs.warn("mobile", "PHONE_TRUST_REVOKED", "the local phone certificate authority was removed", files=removed)
    return {"removed": removed}


def rotate(home: Path, now: _dt.datetime | None = None, run=None) -> dict:
    """A NEW CA (new key, new fingerprint) in place of the old one, plus a fresh leaf. The phone must remove the old
    certificate and install the new one. Returns status() with the old fingerprint as `previous`."""
    old = fingerprint(home)
    revoke(home)
    create_ca(home, now, run)
    ensure_leaf(home, None, now, run)
    st = status(home, now)
    st["previous"] = old
    logs.warn("mobile", "PHONE_TRUST_ROTATED", "the local phone certificate authority was replaced")
    return st


def keep_fresh(home: Path, sctx: ssl.SSLContext, interval_s: float = RENEW_CHECK_S,
               stop: threading.Event | None = None, hosts: Callable[[], list[str]] | None = None) -> threading.Thread:
    """A running phone server lives longer than a 30-day leaf: every `interval_s` re-run ensure_leaf and, when a new leaf was
    issued, load it into the SAME server context (new handshakes get it; open connections keep the old one). Daemon thread,
    no network; it does nothing while the feature is off or the CA is revoked. `t.stop.set()` ends it."""
    stop = stop or threading.Event()

    def stamp() -> int:
        p = leaf_paths(home)[0]
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0

    def loop() -> None:
        last = stamp()
        while not stop.wait(interval_s):
            if not trust_on(home):
                continue
            got = ensure_leaf(home, hosts() if hosts else None)
            if got is None or stamp() == last:
                continue
            try:
                sctx.load_cert_chain(str(got[0]), str(got[1]))
                last = stamp()
                logs.info("mobile", "PHONE_TRUST_LEAF_RELOADED", "the phone server now uses a renewed certificate")
            except (OSError, ssl.SSLError) as e:
                logs.warn("mobile", "PHONE_TRUST_RELOAD_FAILED", "could not load the renewed certificate",
                          error=type(e).__name__)

    t = threading.Thread(target=loop, name="jarves-phone-trust-renew", daemon=True)
    t.stop = stop  # type: ignore[attr-defined]
    t.start()
    return t


# ---------------------------------------------------------------------- catalog row (feature `phone_trust`, beta, OFF)
from . import capabilities  # noqa: E402
from . import features as feat  # noqa: E402
from . import hebrew  # noqa: E402

FEATURE = "phone_trust"
EVIDENCE = ("UNIT test_phone_trust.py (fake clock, no network: chain validation with cryptography, name constraints, renewal, "
            "revoke, the paired-phone download route)")
BLOCKER = "NOT-RUN: never installed on a real Android phone; Chrome install criteria not yet seen to pass (owner-gated)"
ABOUT = ("a trusted https connection for the phone app: JARVES makes its own small local certificate authority (name-constrained to "
         "private-network addresses, key stays on this PC, you can revoke it) so Chrome lets the phone page install as an app. "
         "Optional and off by default; you install one certificate on your phone yourself")
PI = "the phone page is hosted by the PC in V6; there is no Pi path"
hebrew.TEXT.update({
    ABOUT: "חיבור https מהימן לאפליקציית הטלפון: JARVES יוצר רשות אישורים מקומית קטנה משלו (מוגבלת לכתובות ברשת פרטית, המפתח נשאר "
           "במחשב הזה, אפשר לבטל) כדי ש-Chrome יאפשר להתקין את עמוד הטלפון כאפליקציה. אופציונלי וכבוי כברירת מחדל; "
           "את האישור בטלפון מתקינים בעצמכם",
    EVIDENCE: "בדיקות יחידה test_phone_trust.py (שעון מדומה, בלי רשת: אימות שרשרת עם cryptography, הגבלות שמות, חידוש, ביטול, "
              "מסלול ההורדה לטלפון מצומד)",
    BLOCKER: "לא הורץ: לא הותקן בטלפון אנדרואיד אמיתי; קריטריוני ההתקנה של Chrome עוד לא נראו עוברים (בידי הבעלים)",
    PI: "עמוד הטלפון מתארח במחשב ב-V6; אין נתיב ל-Pi",
})
feat.register(FEATURE, "חיבור מהימן לטלפון", "Trusted phone connection", False, risk="high", about=ABOUT,
              needs=["mobile_access"], stage="beta",
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["you install the JARVES certificate on your own phone (Android asks for your screen lock)"],
              settings="jarvis features on phone_trust, then jarvis phone trust enable")
capabilities.declare_extra(FEATURE, phone={"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER,
                                           "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/513"})
