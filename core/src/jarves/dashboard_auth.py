"""Dashboard access control (SW-V2 D02, spec 6 + 12): loopback by default, opt-in home-LAN mode with device pairing.

Loopback (default, unchanged): the server binds 127.0.0.1; the private token printed by `jarvis dashboard` (valid for this run) becomes an
HttpOnly SameSite=Strict cookie; Host must be a loopback name (DNS rebinding); writes need the X-Jarves header.

LAN mode (`jarvis dashboard --lan`, needs the `dashboard_lan` switch, default OFF):
- binds 0.0.0.0 on a fixed port, but answers ONLY clients from private/loopback addresses (RFC 1918, link-local,
  ULA): a port-forward from the internet arrives with a public source address and is refused. No internet exposure,
  no UPnP, no relay.
- Host must be loopback or this machine's LAN IP (a rebinding DNS name is refused); on writes the Origin (when
  sent) must be exactly http://<Host>, and Sec-Fetch-Site must not be cross-site.
- pairing: on THIS computer the owner clicks "Pair a phone" -> an 8-digit code valid 5 minutes, single use. The
  phone opens http://<lan-ip>:<port>/pair, enters it -> a random device secret (only its SHA-256 is stored) in an
  HttpOnly SameSite=Strict cookie (30 days idle). Wrong codes: 5 per address and 20 in total per code, then the code
  is burnt.
- device sessions: every write needs X-Jarves + X-CSRF-Token (per-device token from GET /api/session).
- least privilege: devices cannot pair other devices, list/revoke others, read the audit log or switch LAN mode.
  They may manage scheduler jobs (never approve a tool job), flip low-risk switches and commit calendar
  create/update (see _DEVICE_WRITE_OK and device_body_ok).
- rate limit: 240 requests/minute per client address (429).
- audit: <state_dir>/dashboard_audit.jsonl (size-bounded): pairing, revocation, refused pairing, every write
  (principal, method, path, status; never bodies or secrets).
Known limit: plain HTTP on the home LAN (no TLS): someone already on your Wi-Fi could sniff the session cookie.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from collections import deque
from pathlib import Path

from . import logs

DEVICE_COOKIE = "jarves_dev"
DEVICES_FILE = "dashboard_devices.json"
AUDIT_FILE = "dashboard_audit.jsonl"
AUDIT_MAX_BYTES = 512_000
LAN_PORT = 8766
CODE_TTL_S = 300
CODE_TRIES_PER_IP = 5
CODE_TRIES_TOTAL = 20
DEVICE_IDLE_S = 30 * 86400
RATE_PER_MIN = 240
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# reachable without a session in LAN mode (the pairing page itself)
PUBLIC_PATHS = {("GET", "/pair"), ("POST", "/api/pair"), ("GET", "/static/pair.js"), ("GET", "/static/style.css"),
                ("GET", "/static/m4.css")}
ADMIN_ONLY = ("/api/devices", "/api/audit", "/api/mobile", "/api/features/dashboard_lan")
# A paired phone (device role) gets an ALLOWLIST, never a denylist: reads only, plus the explicit low-risk writes
# below. Everything a denylist used to let through by omission - approving/granting/enabling a generated Skill,
# installing an update, adding a folder grant or reading a file, switching a risky feature (camera, gmail_send...)
# on, stopping the Core, deleting/restoring data, editing config.toml, running a backup/restore - is now refused
# by default because it is not on the list. These stay the computer's own job (loopback session).
DEVICE_READ_DENY = ADMIN_ONLY + ("/api/files", "/api/backup")       # not even readable from a phone
_DEVICE_WRITE_OK = re.compile(
    r"/api/diagnose"                                               # run self-diagnosis
    r"|/api/issues/\d+"                                            # change an issue's status
    r"|/api/memory|/api/memory/\d+|/api/memory/undo|/api/memory/forget-session"   # memory facts (all reversible)
    r"|/api/notes|/api/notes/\d+"                                  # notes
    r"|/api/alerts/(?:pause|resume|test|dismiss_all|\d+)"          # alert controls
    r"|/api/session/[^/]+/review"                                  # ask for an AI review of one conversation
    # owner decision 20 Oct (A): a paired phone may also manage scheduler jobs, flip LOW-risk switches and commit
    # calendar create/update. Method + path are matched here; the body-dependent limits live in device_body_ok().
    r"|/api/jobs|/api/jobs/[0-9a-f]{1,40}|/api/schedule/parse"
    r"|/api/features/[a-z0-9_]+"
    r"|/api/calendar/(?:preview|commit)")
_DEVICE_JOB_OPS = ("pause", "resume", "approve", "delete")           # never run_now / edit from a phone


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def lan_ip() -> str | None:
    """This machine's private LAN address (no packet is sent: UDP connect only picks the route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip if a.is_private and not a.is_loopback else None


def client_allowed(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_loopback or a.is_link_local or (a.is_private and not a.is_global)


def is_loopback(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        return a.is_loopback or bool(getattr(a, "ipv4_mapped", None) and a.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def session_cookie(home: Path) -> str:
    """The owner's login cookie name: "jarves_dash" for the default profile (unchanged), "jarves_dash_<name>" for
    others. Cookies are shared by every port on 127.0.0.1, so one name for all profiles would log the others out."""
    from .profiles import DEFAULT, info
    name = info(home)["name"]
    return "jarves_dash" if name == DEFAULT else f"jarves_dash_{name}"


class Access:
    def __init__(self, home: Path, admin_token: str, *, lan: bool = False, lan_host: str | None = None,
                 clock=time.time) -> None:
        self.home, self.admin_token, self.lan, self.lan_host = home, admin_token, lan, lan_host
        self.mobile = False                            # V5: set by Dashboard when the phone paths are on
        self.clock = clock
        self._lock = threading.RLock()
        self.path = home / DEVICES_FILE
        self.audit_path = home / AUDIT_FILE
        self.local_csrf = secrets.token_urlsafe(18)
        self.cookie = session_cookie(home)        # per profile: two dashboards on 127.0.0.1 keep their own login
        self._code: dict | None = None
        self._hits: dict[str, deque] = {}

    # ------------------------------------------------------------------ devices
    def _devices(self) -> list[dict]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d.get("devices", []) if isinstance(d, dict) else []
        except (OSError, ValueError):
            return []

    def _save(self, devices: list[dict]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "devices": devices}, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def devices(self) -> list[dict]:
        """Safe view (no hashes)."""
        return [{k: d.get(k) for k in ("id", "name", "created", "last_seen", "last_ip", "revoked")}
                for d in self._devices()]

    def revoke(self, device_id: str, by: str) -> bool:
        with self._lock:
            devs = self._devices()
            hit = False
            for d in devs:
                if d["id"] == device_id and not d.get("revoked"):
                    d["revoked"], d["token_hash"], hit = self.clock(), "", True
            if hit:
                self._save(devs)
        if hit:
            self.audit("device_revoked", by=by, device=device_id)
        return hit

    def device_for(self, cookie_value: str) -> dict | None:
        dev_id, _, secret = cookie_value.partition(".")
        if not dev_id or not secret:
            return None
        h = _hash(secret)
        with self._lock:
            devs = self._devices()
            for d in devs:
                if d["id"] == dev_id and not d.get("revoked") and d.get("token_hash") \
                        and secrets.compare_digest(d["token_hash"], h):
                    if self.clock() - (d.get("last_seen") or d["created"]) > DEVICE_IDLE_S:
                        return None
                    if self.clock() - (d.get("last_seen") or 0) > 60:
                        d["last_seen"] = self.clock()
                        self._save(devs)
                    return d
        return None

    # ------------------------------------------------------------------ pairing
    def new_code(self) -> dict:
        code = f"{secrets.randbelow(10 ** 8):08d}"
        with self._lock:
            self._code = {"hash": _hash(code), "expires": self.clock() + CODE_TTL_S, "tries": {}, "total": 0}
        self.audit("pairing_code_created", by="local")
        return {"code": code, "expires_s": CODE_TTL_S}

    def pair(self, code: str, name: str, ip: str) -> dict | None:
        """A device secret for a correct code (single use), else None."""
        name = " ".join(str(name or "phone").split())[:40] or "phone"
        with self._lock:
            c = self._code
            if c is None or self.clock() > c["expires"]:
                self.audit("pairing_refused", ip=ip, reason="no active code")
                return None
            if c["tries"].get(ip, 0) >= CODE_TRIES_PER_IP or c["total"] >= CODE_TRIES_TOTAL:
                self.audit("pairing_refused", ip=ip, reason="too many attempts")
                return None
            if not secrets.compare_digest(_hash(str(code).strip()), c["hash"]):
                c["tries"][ip] = c["tries"].get(ip, 0) + 1
                c["total"] += 1
                if c["total"] >= CODE_TRIES_TOTAL:
                    self._code = None                 # burnt: the owner makes a new one
                self.audit("pairing_refused", ip=ip, reason="wrong code")
                return None
            self._code = None                         # single use
            secret = secrets.token_urlsafe(32)
            dev = {"id": secrets.token_hex(6), "name": name, "token_hash": _hash(secret),
                   "csrf": secrets.token_urlsafe(18), "created": self.clock(), "last_seen": self.clock(),
                   "last_ip": ip, "revoked": None}
            devs = self._devices()
            devs.append(dev)
            self._save(devs)
        self.audit("device_paired", device=dev["id"], name=name, ip=ip)
        logs.info("dashboard", "DASHBOARD_DEVICE_PAIRED", "a device was paired with the dashboard", device=dev["id"])
        return {"device": dev["id"], "cookie": f"{dev['id']}.{secret}"}

    # ------------------------------------------------------------------ audit
    def audit(self, event: str, **fields) -> None:
        rec = {"ts": round(self.clock(), 3), "event": event,
               **{k: (str(v)[:120] if v is not None else None) for k, v in fields.items()}}
        with self._lock:
            try:
                self.home.mkdir(parents=True, exist_ok=True)
                if self.audit_path.exists() and self.audit_path.stat().st_size > AUDIT_MAX_BYTES:
                    os.replace(self.audit_path, self.audit_path.with_suffix(".1.jsonl"))
                with self.audit_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError:
                logs.warn("dashboard", "AUDIT_WRITE_FAILED", "could not write the dashboard audit log")

    def audit_tail(self, n: int = 200) -> list[dict]:
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()[-n:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    # ------------------------------------------------------------------ per-request gate
    def rate_ok(self, ip: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault(ip, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= RATE_PER_MIN:
                return False
            q.append(now)
        return True

    def host_ok(self, host_header: str) -> bool:
        host = (host_header or "").strip().lower()
        if host.startswith("["):
            name = host[1:].split("]")[0]
        else:
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if name in LOOPBACK_HOSTS:
            return True
        return bool(self.lan_host and name == self.lan_host and (self.lan or self.mobile))

    @staticmethod
    def origin_ok(headers, method: str) -> bool:
        if method in ("GET", "HEAD"):
            return True
        site = (headers.get("Sec-Fetch-Site") or "").lower()
        if site and site not in ("same-origin", "none"):
            return False
        origin = headers.get("Origin")
        if origin is None:
            return True                               # non-browser client; X-Jarves/CSRF still required
        return origin.lower() == f"http://{(headers.get('Host') or '').lower()}"

    def principal(self, cookies: dict, ip: str) -> dict | None:
        """{"kind": "local"} for the owner's loopback session, {"kind": "device", ...} for a paired device."""
        tok = cookies.get(self.cookie)
        if tok and is_loopback(ip) and secrets.compare_digest(tok, self.admin_token):
            return {"kind": "local", "id": "local", "csrf": self.local_csrf}
        dv = cookies.get(DEVICE_COOKIE)
        if dv and self.lan:
            d = self.device_for(dv)
            if d is not None:
                return {"kind": "device", "id": d["id"], "name": d["name"], "csrf": d["csrf"]}
        return None

    def allowed(self, who: dict, method: str, path: str) -> bool:
        """Least privilege for paired devices: an ALLOWLIST (reads + a few safe writes), default-deny."""
        if who["kind"] == "local":
            return True                               # the owner on this computer (loopback) can do anything
        if path == f"/api/devices/{who['id']}/revoke" and method == "POST":
            return True                               # a phone may un-pair itself
        if any(path == p or path.startswith(p + "/") for p in DEVICE_READ_DENY):
            return False                              # device/audit/LAN admin, file browser and backups: never
        if method in ("GET", "HEAD"):
            return True                               # any other read
        return bool(_DEVICE_WRITE_OK.fullmatch(path))  # only the explicitly safe writes; everything else refused


def device_body_ok(ctx, method: str, path: str, body) -> bool:
    """Body-dependent limits for a paired phone, checked after the allowlist (the owner on loopback is exempt).
    - scheduler job ops: only pause/resume/approve/delete; a TOOL job (side effects) is never activated from a phone,
      it needs the on-screen approval on the computer. Reminder/research jobs are fine.
    - feature switches: only features whose risk is 'low', never dashboard_lan (also in ADMIN_ONLY).
    - calendar: preview/commit only, create/update only (there is no delete; calendar_write must be on)."""
    b = body if isinstance(body, dict) else {}
    m = re.fullmatch(r"/api/jobs/([0-9a-f]{1,40})", path)
    if method == "POST" and m:
        op = b.get("op")
        if op not in _DEVICE_JOB_OPS:
            return False
        if op in ("approve", "resume"):
            from .scheduler import _ctx_sched
            try:
                job = _ctx_sched(ctx).job(m.group(1))
            except Exception:
                return False
            if job is not None and job.get("action") == "tool":
                return False
        return True
    m = re.fullmatch(r"/api/features/([a-z0-9_]+)", path)
    if method == "POST" and m:
        from .features import CATALOG
        f = CATALOG.get(m.group(1))
        return f is not None and f.risk == "low" and f.id != "dashboard_lan"
    if method == "POST" and path == "/api/calendar/preview":
        return b.get("op", "create") in ("create", "update")
    return True


def parse_cookies(header: str) -> dict:
    out = {}
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k:
            out[k] = v
    return out


# ---------------------------------------------------------------------- dashboard routes (devices, audit)
def dashboard_routes(register) -> None:
    def access(ctx) -> Access:
        return ctx.access

    def session(ctx, _m, _b):
        who = getattr(ctx, "principal", None) or {}
        a = access(ctx)
        return 200, {"kind": who.get("kind"), "device": who.get("id"), "name": who.get("name"),
                     "csrf": who.get("csrf"), "lan": a.lan, "lan_url": f"http://{a.lan_host}:{ctx.port}/pair"
                     if a.lan and a.lan_host else None}

    def devices(ctx, _m, _b):
        a = access(ctx)
        return 200, {"lan": a.lan, "lan_host": a.lan_host, "devices": a.devices(),
                     "pair_url": f"http://{a.lan_host}:{ctx.port}/pair" if a.lan and a.lan_host else None,
                     "how": "jarvis features on dashboard_lan, then jarvis dashboard --lan"}

    def code(ctx, _m, _b):
        a = access(ctx)
        if not a.lan:
            return 409, {"error": "LAN mode is off: run `jarvis dashboard --lan` (switch dashboard_lan on first)"}
        return 200, {**a.new_code(), "pair_url": f"http://{a.lan_host}:{ctx.port}/pair"}

    def revoke(ctx, m, _b):
        who = getattr(ctx, "principal", None) or {}
        ok = access(ctx).revoke(m.group(1), by=who.get("id", "?"))
        return (200, {"ok": True}) if ok else (404, {"error": "no such active device"})

    def audit(ctx, _m, _b):
        return 200, {"entries": access(ctx).audit_tail()}
    register("GET", r"/api/session", session)
    register("GET", r"/api/devices", devices)
    register("POST", r"/api/devices/pair-code", code)
    register("POST", r"/api/devices/([0-9a-f]{12})/revoke", revoke)
    register("GET", r"/api/audit", audit)
