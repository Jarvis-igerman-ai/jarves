"""V5 mobile access: a phone talks to the owner's PC Core (docs/JARVES_SW_V5_WORK_PACKAGE.md).

Default OFF (feature switch `mobile_access`). When off, nothing about the phone API is served and the dashboard
stays on 127.0.0.1. When the owner turns it on (`jarvis dashboard --mobile`):
- the dashboard server also listens on the home network, but a non-loopback client may reach ONLY the phone paths
  (`/m/...` PWA files, `/api/m/...`). Public internet source addresses are refused (see dashboard_auth.client_allowed).
- pairing: the owner presses "Pair a phone" on THIS computer -> an 8-digit one-time code (5 minutes) plus a QR code
  that carries the same code in the URL fragment (never sent to a server). Wrong codes: 5 per address, 20 in total.
- a paired phone holds a per-device bearer token (256-bit; only its SHA-256 is stored). The owner revokes a device
  from the dashboard or `jarvis`; the token stops working at once.
- scope: chat text, voice push-to-talk, approvals list/answer, status. Nothing else of the dashboard (files,
  settings, memory, backups, skills, other devices, audit) is reachable with a phone token.
- approvals are answered on the PC popup unless the owner turns on "phone approvals"; typed-code approvals are always
  PC-only.
- privacy: the audit log and logs.* hold device ids, paths, statuses and counts - never message text, audio, tokens.
- TLS: optional (`mobile_tls/cert.pem` + `key.pem`, or `--tls-selfsigned`). Without TLS the phone browser refuses the
  microphone (secure-context rule), so voice needs TLS; text, approvals and status work over plain home Wi-Fi.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import logs, mobile_drive, qrmini  # noqa: F401  (mobile_drive registers the phone_drive row)
from .continuity import Continuity

FEATURE = "mobile_access"
DEVICES_FILE = "mobile_devices.json"
AUDIT_FILE = "mobile_audit.jsonl"
SETTINGS_FILE = "mobile_settings.json"
TLS_DIR = "mobile_tls"
AUDIT_MAX_BYTES = 512_000
CODE_TTL_S = 300
CODE_TRIES_PER_IP = 5
CODE_TRIES_TOTAL = 20
DEVICE_IDLE_S = 30 * 86400
IP_RATE_PER_MIN = 120          # every phone-API request per client address (before authentication)
DEVICE_RATE_PER_MIN = 120      # authenticated requests per device
CHAT_RATE_PER_MIN = 20
VOICE_RATE_PER_MIN = 10
AUTH_FAILS_PER_MIN = 10        # bad tokens per address before a 429
MAX_JSON_BYTES = 8192
MAX_CHAT_CHARS = 1000
MAX_AUDIO_BYTES = 2_000_000    # about 60 s of 16 kHz 16-bit mono WAV
REPLY_TTL_S = 600                  # how long a chat answer is kept for a resend with the same client message id
REPLY_KEEP = 200
MSG_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")
DEFAULT_SCOPES = ("chat", "voice", "status", "approvals")
ALL_SCOPES = DEFAULT_SCOPES + ("tasks", "prefs", "drive")   # tasks: goal actions + proactive snooze/stop; prefs: Learned preferences; drive: the read-mostly Drive tab (all off until granted)
# Pages a paired device may be given (the owner picks per device; default all four). Everything else is PC-only and is
# refused by the server with a fixed code, whatever the page shows: PAGE_PC_ONLY (never grantable) and
# PAGE_NOT_ALLOWED (a remote page this device was not given).
PAGES = ("home", "chat", "approvals", "health")
DEFAULT_PAGES = PAGES
PC_ONLY_PAGES = ("settings", "secrets", "files", "devices")
PC_ONLY_SEGMENTS = frozenset({"settings", "secrets", "files", "workspace", "memory", "config", "devices", "device",
                              "mobile", "backup", "features", "connect", "logs", "revoke", "keys", "profiles",
                              "identity", "google", "telegram", "whatsapp", "updates", "dashboard", "skills"})
EVENTS_PER_MIN = 600           # chat long-poll: one request per streamed burst, a bucket of its own
STATIC_FILES = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                "style.css": "text/css; charset=utf-8", "sw.js": "text/javascript; charset=utf-8",
                "manifest.webmanifest": "application/manifest+json", "icon.svg": "image/svg+xml",
                "chatview.js": "text/javascript; charset=utf-8", "chatview.css": "text/css; charset=utf-8",
                "indicator.js": "text/javascript; charset=utf-8",
                "plans.js": "text/javascript; charset=utf-8",
                "prefs.js": "text/javascript; charset=utf-8",
                "drive.js": "text/javascript; charset=utf-8"}
SHARED_FILES = ("chatview.js", "chatview.css")      # one chat view for the dashboard and the phone page (dashboard_static/)


def is_mobile_path(path: str) -> bool:
    return path == "/m" or path.startswith("/m/") or path.startswith("/api/m/")


def network_ok(ip: str, *, lan: bool, mobile_on: bool, path: str, tailnet: bool = False) -> bool:
    """Who may talk to the server at all. Public addresses never. A home-network address only in LAN mode (whole
    dashboard, paired devices) or, in phone mode, only on the phone paths. Loopback always (the owner's own PC).
    A tailnet address (100.64.0.0/10) only when the owner uses Tailscale (`tailnet`), phone mode is on and only on
    the phone paths: the rest of the dashboard stays PC-only."""
    from .dashboard_auth import client_allowed, is_loopback
    from .tailnet import is_tailnet_ip
    if tailnet and mobile_on and is_tailnet_ip(ip) and is_mobile_path(path):
        return True
    if not client_allowed(ip):
        return False
    return is_loopback(ip) or lan or (mobile_on and is_mobile_path(path))


def write_private(path: Path, data: bytes) -> None:
    """Create the file already owner-only (0600): there is no moment when a token store or a TLS key is readable by
    other users (write-then-chmod left one)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _pages_of(d: dict) -> list[str]:
    p = d.get("pages")
    return [x for x in PAGES if x in p] if isinstance(p, list) else list(DEFAULT_PAGES)


def page_of(path: str) -> str:
    """The remote page an API path belongs to ('' = none: /me, /pages, /unpair, /pair)."""
    seg = path.split("/")[3] if path.startswith("/api/m/") and path.count("/") >= 3 else ""
    return {"chat": "chat", "voice": "chat", "approvals": "approvals", "status": "health", "health": "health",
            "home": "home"}.get(seg, "")


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@dataclass
class Bridge:
    """What the phone API needs from the running Core. Tests pass fakes; `core_bridge` adapts the real Core."""
    chat: Callable[[str, str], dict]                # (text, device id) -> {"reply": str, "ok": bool}
    voice: Callable[[bytes, str], dict]             # (wav bytes, device id) -> {"transcript": str, "reply": str, "ok": bool}
    status: Callable[[], dict]                      # -> {"core": str, "components": {name: status}}
    voice_available: Callable[[], bool] = field(default=lambda: True)
    goals: Callable[[], object] = field(default=lambda: None)              # -> goals.Goals (the one goal store) or None
    desktop_present: Callable[[], bool] = field(default=lambda: False)     # the owner is at the PC right now
    audio: Callable[[], dict] = field(default=lambda: {})                  # the computer's mic / cloud-audio state (privacy.state)
    plans: Callable[[], object] = field(default=lambda: None)              # -> goal_plans.PlanStore (None: open memory.db here)
    plans_on: Callable[[], bool] = field(default=lambda: True)             # the "goal plans" feature switch
    prefs: Callable[[], object] = field(default=lambda: None)              # -> preferences.Preferences (None: open memory.db here)
    prefs_in_prompt: Callable[[], bool] = field(default=lambda: True)      # the "use learned preferences" switch
    drive_features: Callable[[], object] = field(default=lambda: None)     # -> features.Features (None: open the one in the home folder)
    drive_passenger: Callable[[], str] = field(default=lambda: "ask")      # the PERSISTENT drive_briefing.passenger setting (read-only here)
    drive_briefing: Callable[[str], dict] | None = None                    # (alone | present) -> {"ok", "sections": [...]} (None: not available)


class MobileAccess:
    """Device store (hashed tokens), pairing codes, rate limits, settings and audit for the phone API."""

    def __init__(self, home: Path, *, clock=time.time) -> None:
        self.home, self.clock = Path(home), clock
        self.path = self.home / DEVICES_FILE
        self.audit_path = self.home / AUDIT_FILE
        self.settings_path = self.home / SETTINGS_FILE
        self._lock = threading.RLock()
        self._code: dict | None = None
        self._hits: dict[str, deque] = {}
        # serving state, set by the Dashboard when the owner enabled mobile access
        self._enabled = False
        self.gate: Callable[[], bool] | None = None   # live feature switch: turning mobile_access OFF stops serving at once
        self._noisy: dict[str, list] = {}
        self._busy: set[str] = set()
        self._replies: dict[tuple[str, str], tuple[float, dict]] = {}   # (device, client message id) -> answered turn
        self.bridge: Bridge | None = None
        self.chat = None                           # chat_engine.WebChat (the shared web conversation), set by the Dashboard
        self._cont: Continuity | None = None
        self.quiet: Callable[[float], bool] | None = None     # calm.quiet_hours_now bound to the user DB (set by the Core)
        self.scheme = "http"
        self.host: str | None = None
        self.port = 0
        self.tailnet: dict = {}                  # tailnet.detect() result of THIS run (empty = Tailscale not used)
        self.tls_source = "none"                 # tailscale | selfsigned | none
        self.bind: str | None = None             # the address the server listens on

    @property
    def enabled(self) -> bool:
        if not self._enabled:
            return False
        try:
            return self.gate() if self.gate else True
        except Exception:                         # an unreadable switch fails closed
            return False

    @enabled.setter
    def enabled(self, on: bool) -> None:
        self._enabled = bool(on)

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict:
        try:
            d = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        return {"phone_approvals": bool(d.get("phone_approvals")), "bind_tailnet_only": bool(d.get("bind_tailnet_only"))}

    def set_phone_approvals(self, on: bool, by: str = "local") -> None:
        with self._lock:
            self._write(self.settings_path, {**self.settings(), "phone_approvals": bool(on)})
        self.audit("phone_approvals_changed", by=by, on=bool(on))
        logs.info("mobile", "MOBILE_PHONE_APPROVALS", "phone approvals setting changed", enabled=bool(on))

    def set_bind_tailnet_only(self, on: bool, by: str = "local") -> None:
        """Listen on the tailnet address only (takes effect when the phone server starts the next time)."""
        with self._lock:
            self._write(self.settings_path, {**self.settings(), "bind_tailnet_only": bool(on)})
        self.audit("bind_changed", by=by, on=bool(on))
        logs.info("mobile", "MOBILE_BIND_CHANGED", "phone server bind setting changed", bind_tailnet_only=bool(on))

    def revoke_all(self, by: str) -> int:
        """One click: every paired phone is revoked at once (a lost phone)."""
        with self._lock:
            devs = self._devices()
            n = 0
            for d in devs:
                if not d.get("revoked"):
                    d["revoked"], d["token_hash"] = self.clock(), ""
                    n += 1
            if n:
                self._write(self.path, {"version": 1, "devices": devs})
        self.audit("all_devices_revoked", by=by, count=n)
        logs.warn("mobile", "MOBILE_ALL_REVOKED", "every paired phone was revoked", count=n)
        return n

    # ------------------------------------------------------------------ storage
    def _write(self, path: Path, obj) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{secrets.token_hex(3)}.tmp")
        write_private(tmp, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))
        os.replace(tmp, path)

    def _devices(self) -> list[dict]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d.get("devices", []) if isinstance(d, dict) else []
        except (OSError, ValueError):
            return []

    def devices(self) -> list[dict]:
        """Safe view (never the hash)."""
        return [{**{k: d.get(k) for k in ("id", "name", "created", "last_seen", "last_ip", "revoked", "scopes")},
                 "pages": _pages_of(d)} for d in self._devices()]

    def revoke(self, device_id: str, by: str) -> bool:
        with self._lock:
            devs = self._devices()
            hit = False
            for d in devs:
                if d.get("id") == device_id and not d.get("revoked"):
                    d["revoked"], d["token_hash"], hit = self.clock(), "", True
            if hit:
                self._write(self.path, {"version": 1, "devices": devs})
        if hit:
            self.audit("device_revoked", by=by, device=device_id)
            logs.info("mobile", "MOBILE_DEVICE_REVOKED", "a phone was revoked", device=device_id)
        return hit

    def set_scopes(self, device_id: str, scopes: list[str], by: str) -> bool:
        scopes = [s for s in ALL_SCOPES if s in scopes]
        with self._lock:
            devs = self._devices()
            hit = False
            for d in devs:
                if d.get("id") == device_id and not d.get("revoked"):
                    d["scopes"], hit = scopes, True
            if hit:
                self._write(self.path, {"version": 1, "devices": devs})
        if hit:
            self.audit("device_scopes", by=by, device=device_id, scopes=",".join(scopes))
        return hit

    def set_pages(self, device_id: str, pages: list[str], by: str) -> bool:
        """The owner's per-device 'allowed pages'. PC-only pages cannot be granted (they are filtered out)."""
        pages = [p for p in PAGES if p in pages]
        with self._lock:
            devs = self._devices()
            hit = False
            for d in devs:
                if d.get("id") == device_id and not d.get("revoked"):
                    d["pages"], hit = pages, True
            if hit:
                self._write(self.path, {"version": 1, "devices": devs})
        if hit:
            self.audit("device_pages", by=by, device=device_id, pages=",".join(pages))
        return hit

    def authenticate(self, token: str) -> dict | None:
        dev_id, _, secret = (token or "").partition(".")
        if not dev_id or not secret or len(token) > 200:
            return None
        h = _hash(secret)
        with self._lock:
            devs = self._devices()
            for d in devs:
                if d.get("id") == dev_id and not d.get("revoked") and d.get("token_hash") \
                        and secrets.compare_digest(d["token_hash"], h):
                    now = self.clock()
                    if now - (d.get("last_seen") or d["created"]) > DEVICE_IDLE_S:
                        return None
                    if now - (d.get("last_seen") or 0) > 60:
                        d["last_seen"] = now
                        self._write(self.path, {"version": 1, "devices": devs})
                    return {"id": d["id"], "name": d.get("name", ""), "scopes": list(DEFAULT_SCOPES if d.get("scopes") is None else d["scopes"]),
                            "pages": _pages_of(d)}
        return None

    # ------------------------------------------------------------------ pairing
    def pair_url(self, code: str) -> str:
        host = self.host or "127.0.0.1"
        return f"{self.scheme}://{host}:{self.port}/m/#c={code}"

    def tailnet_host(self) -> str | None:
        """The name the phone uses over Tailscale: the tailnet DNS name with a Tailscale certificate, else the 100.x address."""
        t = self.tailnet or {}
        if not t.get("present"):
            return None
        return t.get("name") if (self.tls_source == "tailscale" and t.get("name")) else t.get("ip")

    def tailnet_url(self, code: str) -> str | None:
        h = self.tailnet_host()
        return f"{self.scheme}://{h}:{self.port}/m/#c={code}" if h else None

    def new_code(self, by: str = "local") -> dict:
        code = f"{secrets.randbelow(10 ** 8):08d}"
        with self._lock:
            self._code = {"hash": _hash(code), "expires": self.clock() + CODE_TTL_S, "tries": {}, "total": 0}
        self.audit("pairing_code_created", by=by)
        url = self.pair_url(code)
        out = {"code": code, "expires_s": CODE_TTL_S, "url": url, "qr": qrmini.rows(url)}
        turl = self.tailnet_url(code)
        if turl:
            out.update(tailnet_url=turl, tailnet_qr=qrmini.rows(turl))
        return out

    def pair(self, code: str, name: str, ip: str) -> dict | None:
        """A bearer token for a correct code (single use), else None."""
        name = " ".join(str(name or "phone").split())[:40] or "phone"
        code = re.sub(r"\D", "", str(code))[:12]
        with self._lock:
            c = self._code
            if c is None or self.clock() > c["expires"]:
                reason = "no active code"
            elif c["tries"].get(ip, 0) >= CODE_TRIES_PER_IP or c["total"] >= CODE_TRIES_TOTAL:
                reason = "too many attempts"
            elif not secrets.compare_digest(_hash(code), c["hash"]):
                c["tries"][ip] = c["tries"].get(ip, 0) + 1
                c["total"] += 1
                if c["total"] >= CODE_TRIES_TOTAL:
                    self._code = None                 # burnt: the owner makes a new one
                reason = "wrong code"
            else:
                reason = ""
            if reason:
                self.audit_noisy("pairing_refused", ip, reason=reason)
                logs.warn("mobile", "MOBILE_PAIR_REFUSED", "a phone pairing attempt was refused", reason=reason)
                return None
            self._code = None                         # single use
            secret = secrets.token_urlsafe(32)
            dev = {"id": secrets.token_hex(6), "name": name, "token_hash": _hash(secret), "created": self.clock(),
                   "last_seen": self.clock(), "last_ip": ip, "revoked": None, "scopes": list(DEFAULT_SCOPES)}
            devs = self._devices()
            devs.append(dev)
            self._write(self.path, {"version": 1, "devices": devs})
        self.audit("device_paired", device=dev["id"], name=name, ip=ip)
        logs.info("mobile", "MOBILE_DEVICE_PAIRED", "a phone was paired", device=dev["id"])
        return {"device": dev["id"], "token": f"{dev['id']}.{secret}", "name": name}

    # ------------------------------------------------------------------ audit + limits
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
                logs.warn("mobile", "MOBILE_AUDIT_WRITE_FAILED", "could not write the mobile audit log")

    def audit_noisy(self, event: str, ip: str, **fields) -> None:
        """Audit for events anyone on the network can cause without a token (refused pairing/auth, rate limits):
        at most 5 per address and event per minute, then a count of what was left out. A flood cannot rotate the real
        history (paired, revoked, approvals) out of the size-bounded log."""
        now, key = self.clock(), f"{event}:{ip}"
        with self._lock:
            w = self._noisy.get(key)
            if w is None or now - w[0] > 60:
                skipped = w[2] if w else 0
                if len(self._noisy) > 500:
                    self._noisy = {k: v for k, v in self._noisy.items() if now - v[0] <= 60}
                self._noisy[key] = w = [now, 0, 0]
                if skipped:
                    fields["suppressed"] = skipped
            w[1] += 1
            if w[1] > 5:
                w[2] += 1
                return
        self.audit(event, ip=ip, **fields)

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

    def rate_ok(self, key: str, limit: int) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 2000:                # bounded: drop idle keys
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
                    self._hits.pop(k, None)
        return True

    @property
    def cont(self) -> Continuity:
        """The continuity store (ledger, shared conversation, presence, notices)."""
        if self._cont is None:
            self._cont = Continuity(self.home, clock=self.clock, quiet=self.quiet)
        return self._cont

    def begin(self, device: str) -> bool:
        """One chat/voice turn per phone at a time: a double tap or a retry after a Wi-Fi blip cannot run the same
        message twice in parallel or pile up Brain calls (the phone shows the busy answer and waits)."""
        with self._lock:
            if device in self._busy:
                return False
            self._busy.add(device)
            return True

    def replay(self, device: str, mid: str) -> dict | None:
        """Idempotent chat: a phone that lost the answer (Wi-Fi blip, reconnect) resends the SAME client message id and
        gets the stored answer; the Core never runs the message a second time."""
        with self._lock:
            hit = self._replies.get((device, mid))
            if hit and self.clock() - hit[0] <= REPLY_TTL_S:
                return hit[1]
        try:
            return self.cont.seen(device, mid, REPLY_TTL_S)          # survives a PC restart (the in-memory copy is gone then)
        except Exception:
            return None

    def remember(self, device: str, mid: str, reply: dict) -> None:
        now = self.clock()
        with self._lock:
            self._replies[(device, mid)] = (now, reply)
            for k in [k for k, v in self._replies.items() if now - v[0] > REPLY_TTL_S]:
                self._replies.pop(k, None)
            while len(self._replies) > REPLY_KEEP:               # bounded: oldest out first
                self._replies.pop(next(iter(self._replies)))
        try:
            self.cont.store(device, mid, "chat", 200, reply)
        except Exception:
            logs.warn("mobile", "MOBILE_LEDGER_FAILED", "the command ledger could not be written")

    def end(self, device: str) -> None:
        with self._lock:
            self._busy.discard(device)

    def auth_failures_ok(self, ip: str) -> bool:
        """False once an address produced AUTH_FAILS_PER_MIN bad tokens in the last minute."""
        now = self.clock()
        with self._lock:
            q = self._hits.get("fail:" + ip)
            if not q:
                return True
            while q and now - q[0] > 60:
                q.popleft()
            return len(q) < AUTH_FAILS_PER_MIN

    def note_auth_failure(self, ip: str) -> None:
        with self._lock:
            self._hits.setdefault("fail:" + ip, deque()).append(self.clock())


# ---------------------------------------------------------------------- the Core adapter
def core_bridge(core, loop) -> Bridge:
    """Adapt the running Core: text and speech go through the same Brain as the desktop (shared memory and
    history); the answer goes back to the phone, never to the PC speaker. It does NOT use the spoken-approval path, so
    "yes" typed or spoken on the phone cannot answer a PC approval."""
    import asyncio

    def run(coro, timeout=150):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    async def _respond(text: str) -> dict:
        # Core.respond_remote: same Brain + typed-history logging + curation as a Windows turn (one conversation)
        reply = await core.respond_remote(text)
        return {"reply": reply.text, "ok": bool(reply.ok)}

    def chat(text: str, device: str) -> dict:
        return run(_respond(text))

    def voice(wav: bytes, device: str) -> dict:
        import numpy as np
        from .voice.tts import wav_to_np
        audio, rate = wav_to_np(wav)
        if rate != 16000 and len(audio):
            n = max(1, int(len(audio) * 16000 / rate))
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)

        async def go() -> dict:
            text = (await core.stt.transcribe(audio)).strip()
            if not text:
                return {"transcript": "", "reply": "", "ok": False, "code": "NO_SPEECH"}
            return {"transcript": text, **await _respond(text)}
        return run(go())

    def status() -> dict:
        comps = core.health.components_view()
        return {"core": getattr(core.health, "core", "running"),
                "components": {k: str(v.get("status")) for k, v in comps.items()}}

    def goals():
        return getattr(getattr(core, "ops", None), "goals", None)

    def desktop_present() -> bool:
        from .calm import owner_at_pc
        try:
            return bool(owner_at_pc(core))
        except Exception:
            return False
    def audio() -> dict:
        from . import mobile_audio
        from .runtime.privacy_state import derive
        return mobile_audio.from_privacy_state(derive(core))
    def plans():
        return getattr(getattr(core, "ops", None), "plans", None)

    def plans_on() -> bool:
        from .goal_plans import FEATURE
        f = getattr(core, "features", None)
        return bool(f.enabled(FEATURE)) if f is not None else True
    def prefs():
        return getattr(getattr(core, "ops", None), "prefs", None)

    def prefs_in_prompt() -> bool:
        from .preferences import FEATURE
        f = getattr(core, "features", None)
        return bool(f.enabled(FEATURE)) if f is not None else True
    def drive_passenger() -> str:
        return str(core.ops.db.setting("drive_briefing.passenger") or "ask")

    def drive_briefing(mode: str) -> dict:
        """The Core's own pre-drive briefing tool, called directly (read-only; the passenger filter lives in the skill)."""
        from .drive_briefing import DriveBriefingArgs
        skill = core.registry.skills["drive_briefing"]
        tool = skill.tools()[0]

        async def go() -> dict:
            from .drive_mode import PHONE_SCREEN
            from .skills import ROUTE
            token = ROUTE.set(PHONE_SCREEN)        # Core code sets the route: the Gate allows plain reads here, nothing else
            try:
                core.registry.new_turn(PHONE_SCREEN)
                r = await tool.handler(DriveBriefingArgs(passenger=mode))
                return {"ok": bool(r.ok), "text": r.message, "sections": (r.data or {}).get("sections") or []}
            finally:
                core.registry.clear_taint(PHONE_SCREEN)
                ROUTE.reset(token)
        return run(go(), 60)
    return Bridge(chat=chat, voice=voice, status=status, voice_available=lambda: getattr(core, "stt", None) is not None,
                  goals=goals, desktop_present=desktop_present, audio=audio, plans=plans, plans_on=plans_on, prefs=prefs, prefs_in_prompt=prefs_in_prompt,
                  drive_features=lambda: getattr(core, "features", None), drive_passenger=drive_passenger, drive_briefing=drive_briefing)


# ---------------------------------------------------------------------- HTTP (called by dashboard.Handler)
STATIC_DIR = Path(__file__).parent / "dashboard_static" / "mobile"


def _body(h, limit: int) -> bytes | None:
    try:
        n = int(h.headers.get("Content-Length") or 0)
    except ValueError:
        n = -1
    if n < 0 or n > limit:
        h._json(413 if n > limit else 400, {"error": "too large" if n > limit else "bad Content-Length"})
        return None
    return h.rfile.read(n) if n else b""


def _json_body(h) -> dict | None:
    raw = _body(h, MAX_JSON_BYTES)
    if raw is None:
        return None
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        h._json(400, {"error": "invalid JSON"})
        return None
    if not isinstance(d, dict):
        h._json(400, {"error": "JSON object expected"})
        return None
    return d


def _origin_ok(h) -> bool:
    site = (h.headers.get("Sec-Fetch-Site") or "").lower()
    if site and site not in ("same-origin", "none"):
        return False
    origin = h.headers.get("Origin")
    if origin is None:
        return True
    scheme = "https" if getattr(h.server, "tls", False) else "http"
    return origin.lower() == f"{scheme}://{(h.headers.get('Host') or '').lower()}"


def _static_file(name: str) -> Path | None:
    """CodeQL #6/#7: the phone-supplied name is only COMPARED with the fixed STATIC_FILES keys; the path is built from
    the matching constant key, never from the request, so encoded `..`, backslashes, absolute or unknown names cannot
    reach the filesystem. The resolved file must also stay inside its static folder (a symlink pointing out is refused)."""
    key = next((k for k in STATIC_FILES if k == name), None)
    if key is None:
        return None
    base = (STATIC_DIR.parent if key in SHARED_FILES else STATIC_DIR).resolve()
    f = (base / key).resolve()
    if f.parent != base or f.name != key or not f.is_file():
        return None
    return f


def serve(h, mob: MobileAccess, ip: str) -> None:
    """Handle one phone request. `h` is the dashboard Handler (already past the network and Host checks)."""
    url = urlsplit(h.path)
    path, method = url.path, h.command
    if not mob.enabled:
        return h._json(404, {"error": "not found"})
    # ---- the PWA files: public app code, no secrets
    if path == "/m":
        return h._send(302, b"", "text/plain", {"Location": "/m/"})
    if path == "/m/" or path.startswith("/m/"):
        if method not in ("GET", "HEAD"):
            return h._send(405, b"", "text/plain")
        f = _static_file(path[3:] or "index.html")
        if f is None:
            return h._send(404, b"not found", "text/plain")
        try:
            data = f.read_bytes()
        except FileNotFoundError:                     # vanished after the check: a plain 404, never a path
            return h._send(404, b"not found", "text/plain")
        except OSError:
            return h._send(500, b"error", "text/plain")
        return h._send(200, data, STATIC_FILES[f.name],
                       {"Service-Worker-Allowed": "/m/"} if f.name == "sw.js" else None)
    # ---- the API
    events = path == "/api/m/chat/events"            # the chat long-poll has its own, larger bucket
    if not mob.rate_ok(("ipev:" if events else "ip:") + ip, EVENTS_PER_MIN if events else IP_RATE_PER_MIN):
        mob.audit_noisy("rate_limited", ip, path=path, scope="address")
        logs.warn("mobile", "MOBILE_RATE_LIMITED", "phone API rate limit hit", path=path)
        return h._json_extra(429, {"error": "too many requests"}, {"Retry-After": "60"})
    if method != "GET" and (h.headers.get("X-Jarves") != "1" or not _origin_ok(h)):
        mob.audit_noisy("refused_cross_origin", ip, path=path)
        return h._json(403, {"error": "cross-origin or missing X-Jarves header"})
    if path == "/api/m/pair" and method == "POST":
        d = _json_body(h)
        if d is None:
            return
        got = mob.pair(str(d.get("code", "")), str(d.get("name", "")), ip)
        if got is None:
            return h._json(403, {"error": "wrong or expired code: make a new one on the computer"})
        # not h._json: the dashboard's output cleaner masks credential-named keys, and this body IS the credential
        return h._send(200, json.dumps({"ok": True, **got}).encode("utf-8"), "application/json; charset=utf-8")
    # ---- everything below needs a device token
    if not mob.auth_failures_ok(ip):
        mob.audit_noisy("rate_limited", ip, path=path, scope="bad tokens")
        return h._json_extra(429, {"error": "too many bad tokens"}, {"Retry-After": "60"})
    auth = h.headers.get("Authorization") or ""
    dev = mob.authenticate(auth[7:].strip()) if auth[:7].lower() == "bearer " else None
    if dev is None:
        mob.note_auth_failure(ip)
        mob.audit_noisy("auth_refused", ip, path=path)
        logs.warn("mobile", "MOBILE_AUTH_FAILED", "phone API request without a valid token", path=path)
        return h._json(401, {"error": "not paired or revoked: pair again on the computer"})
    if not mob.rate_ok(("devev:" if events else "dev:") + dev["id"], EVENTS_PER_MIN if events else DEVICE_RATE_PER_MIN):
        mob.audit("rate_limited", device=dev["id"], path=path, scope="device")
        return h._json_extra(429, {"error": "too many requests"}, {"Retry-After": "60"})

    def need(scope: str) -> bool:
        if scope in dev["scopes"]:
            return True
        mob.audit("refused_scope", device=dev["id"], path=path, reason=scope)
        h._json(403, {"error": f"this device may not use {scope}"})
        return False

    def done(status: int, obj, **extra) -> None:
        if method != "GET":
            mob.audit("request", device=dev["id"], method=method, path=path, status=status, **extra)
        return h._json(status, obj)

    def need_page(page: str) -> bool:
        if page in dev["pages"]:
            return True
        mob.audit("refused_page", device=dev["id"], path=path, reason=page)
        h._json(403, {"error": f"this device was not given the {page} page: ask on the computer",
                      "code": "PAGE_NOT_ALLOWED", "page": page})
        return False

    seg = path.split("/")[3] if path.count("/") >= 3 else ""
    if seg in PC_ONLY_SEGMENTS:                   # settings, secrets, files, devices...: never from a phone or a remote PC
        mob.audit("refused_pc_only", device=dev["id"], path=path, reason=seg)
        logs.warn("mobile", "MOBILE_PC_ONLY_REFUSED", "a device asked for a computer-only page", path=path)
        return h._json(403, {"error": "this can only be done on the computer itself", "code": "PAGE_PC_ONLY", "page": seg})
    m = re.fullmatch(r"/api/m/pages/([a-z_]{1,20})", path)
    if m and method == "GET":
        name = m.group(1)
        if name in PC_ONLY_PAGES:
            mob.audit("refused_pc_only", device=dev["id"], path=path, reason=name)
            return h._json(403, {"error": "this can only be done on the computer itself", "code": "PAGE_PC_ONLY", "page": name})
        if name not in PAGES:
            return h._json(404, {"error": "no such page", "code": "PAGE_UNKNOWN"})
        if not need_page(name):
            return
        return h._json(200, {"page": name, "allowed": True})
    if path == "/api/m/pages" and method == "GET":
        return h._json(200, {"allowed": dev["pages"], "remote_pages": list(PAGES), "pc_only": list(PC_ONLY_PAGES)})
    if path == "/api/m/home" and method == "GET":
        if not need_page("home"):
            return
        try:
            st = mob.bridge.status() if mob.bridge else {"core": "unknown", "components": {}}
        except Exception:
            st = {"core": "unreachable", "components": {}}
        from .approvals import read_pending_file
        return h._json(200, {"name": dev["name"], "core": st.get("core"), "waiting": len(read_pending_file(mob.home, reveal=False)),
                             "pages": dev["pages"], "chat": mob.chat is not None})
    if path == "/api/m/health" and method == "GET":
        if not need_page("health"):
            return
        return h._json(200, health_view(mob))
    if path.startswith("/api/m/chat/") and seg == "chat":
        return _chat_routes(h, mob, dev, path, method, done, need, need_page)
    if path == "/api/m/me" and method == "GET":
        return h._json(200, {"device": dev["id"], "name": dev["name"], "scopes": dev["scopes"], "pages": dev["pages"],
                             "phone_approvals": mob.settings()["phone_approvals"],
                             "voice": bool(mob.bridge and mob.bridge.voice_available() and mob.scheme == "https")})
    if path == "/api/m/unpair" and method == "POST":
        mob.revoke(dev["id"], by=dev["id"])
        return h._json(200, {"ok": True})
    if path == "/api/m/status" and method == "GET":
        if not need("status") or not need_page("health"):
            return
        try:
            st = mob.bridge.status() if mob.bridge else {"core": "unknown", "components": {}}
        except Exception as e:
            logs.warn("mobile", "MOBILE_STATUS_FAILED", "status could not be read", error_type=type(e).__name__)
            st = {"core": "unreachable", "components": {}}
        return h._json(200, {**st, "tls": mob.scheme == "https"})
    if path == "/api/m/chat" and method == "POST":
        if not need("chat") or not need_page("chat"):
            return
        d = _json_body(h)
        if d is None:
            return
        mid = d.get("id")
        if mid is not None and not (isinstance(mid, str) and MSG_ID_RE.match(mid)):
            return h._json(400, {"error": "message id must be 8-64 letters, digits or _ . : -"})
        if mid:
            again = mob.replay(dev["id"], mid)
            if again is not None:                                # a resend: answer from memory, no second run
                return done(200, {**again, "replay": True}, replay="yes")
            if mob.cont.stale(d.get("age_s")):                   # queued while offline for too long: never run it late
                return done(410, {"error": "this message waited too long while offline: send it again", "code": "STALE"})
        if not mob.rate_ok("chat:" + dev["id"], CHAT_RATE_PER_MIN):
            return h._json_extra(429, {"error": "too many messages, slow down"}, {"Retry-After": "30"})
        text = str(d.get("text", "")).strip()[:MAX_CHAT_CHARS]
        if not text:
            return h._json(400, {"error": "text is empty"})
        if mob.bridge is None:
            return done(503, {"error": "the Core is not connected"}, chars=len(text))
        if not mob.begin(dev["id"]):
            return h._json(409, {"error": "the previous message is still being answered", "code": "BUSY"})
        try:
            r = mob.bridge.chat(text, dev["id"])
        except Exception as e:
            logs.warn("mobile", "MOBILE_CHAT_FAILED", "a phone message could not be answered", error_type=type(e).__name__)
            return done(502, {"error": "JARVES could not answer right now"}, chars=len(text))
        finally:
            mob.end(dev["id"])
        out = {"reply": str(r.get("reply", "")), "ok": bool(r.get("ok", True))}
        if mid:
            mob.remember(dev["id"], mid, out)
        try:                                                     # the one shared conversation (desktop + web read it too)
            mob.cont.add_turn(dev["id"], "user", text, mid)
            mob.cont.add_turn(dev["id"], "assistant", out["reply"], mid)
            mob.cont.beat(dev["id"], used=True)
        except Exception:
            logs.warn("mobile", "MOBILE_JOURNAL_FAILED", "the shared conversation could not be written")
        return done(200, out, chars=len(text))
    if path == "/api/m/voice" and method == "POST":
        if not need("voice") or not need_page("chat"):
            return
        if mob.scheme != "https" or mob.bridge is None or not mob.bridge.voice_available():
            return h._json(409, {"error": "voice needs TLS (https) and the voice pipeline"})
        if not mob.rate_ok("voice:" + dev["id"], VOICE_RATE_PER_MIN):
            return h._json_extra(429, {"error": "too many voice requests"}, {"Retry-After": "30"})
        raw = _body(h, MAX_AUDIO_BYTES)
        if raw is None:
            return
        if raw[:4] != b"RIFF":
            return h._json(415, {"error": "send a 16-bit PCM WAV"})
        if not mob.begin(dev["id"]):
            return h._json(409, {"error": "the previous request is still being answered", "code": "BUSY"})
        try:
            r = mob.bridge.voice(raw, dev["id"])
        except Exception as e:
            logs.warn("mobile", "MOBILE_VOICE_FAILED", "a phone voice request failed", error_type=type(e).__name__)
            return done(502, {"error": "JARVES could not understand the audio"}, bytes=len(raw))
        finally:
            mob.end(dev["id"])
        return done(200, {"transcript": str(r.get("transcript", "")), "reply": str(r.get("reply", "")),
                          "ok": bool(r.get("ok", False))}, bytes=len(raw))
    # ---- continuity: one state for every client, notices routed to one device, goal actions, proactive controls
    if path == "/api/m/sync" and method == "GET":
        if not need("status"):
            return
        q = dict(p.split("=", 1) for p in url.query.split("&") if "=" in p)
        try:
            since = max(0, int(q.get("since", "0")))
        except ValueError:
            since = 0
        c = mob.cont
        c.beat(dev["id"], "phone")
        goals = mob.bridge.goals() if mob.bridge else None
        want_goals = "tasks" in dev["scopes"] and goals is not None
        return h._json(200, {"head": c.head(), "turns": c.turns(since, 50) if "chat" in dev["scopes"] else [],
                             "notices": c.fetch_notices(dev["id"]), "desktop_here": bool(mob.bridge and mob.bridge.desktop_present()),
                             "goals": [_goal_brief(g) for g in goals.list("active")] if want_goals else [],
                             "approvals_waiting": len(approvals_view(mob)["pending"]) if "approvals" in dev["scopes"] else 0,
                             "signals": c.signals_state() if ("tasks" in dev["scopes"] or "status" in dev["scopes"]) else None})
    m = re.fullmatch(r"/api/m/notices/([0-9]{1,9})/ack", path)
    if m and method == "POST":
        if not need("status"):
            return
        return h._json(200, {"ok": mob.cont.ack_notice(dev["id"], int(m.group(1)))})
    if path == "/api/m/goals/op" and method == "POST":
        if not need("tasks"):
            return
        d = _json_body(h)
        if d is None:
            return
        cid = d.get("id")
        if not (isinstance(cid, str) and MSG_ID_RE.match(cid)):
            return h._json(400, {"error": "command id (8-64 letters, digits or _ . : -) is required"})
        mob.cont.beat(dev["id"], used=True)
        status, obj, again = mob.cont.execute(dev["id"], cid, "goal", lambda: goal_action(mob, d), age_s=d.get("age_s"))
        return done(status, {**obj, "replay": True} if again else obj, op=str(d.get("op", ""))[:20])
    if path == "/api/m/audio" and method == "GET":        # mic / cloud-audio indicator (V5 G5): true state, read-only
        if not need("status"):
            return
        from . import mobile_audio
        return h._json(200, mobile_audio.view(mob))
    if path == "/api/m/plans" and method == "GET":        # Goal plans (V5 G4): read-only steps and status
        if not need("tasks"):
            return
        from . import mobile_plans
        try:
            return h._json(200, mobile_plans.view(mob))
        except Exception as e:
            logs.warn("mobile", "MOBILE_PLANS_FAILED", "plans could not be read", error_type=type(e).__name__)
            return h._json(503, {"error": "plans are not available right now"})
    if path == "/api/m/plans/op" and method == "POST":
        if not need("tasks"):
            return
        from . import mobile_plans
        d = _json_body(h)
        if d is None:
            return
        cid = d.get("id")
        if not (isinstance(cid, str) and MSG_ID_RE.match(cid)):
            return h._json(400, {"error": "command id (8-64 letters, digits or _ . : -) is required"})
        status, obj, again = mob.cont.execute(dev["id"], cid, "plan", lambda: mobile_plans.action(mob, d, dev["id"]),
                                              age_s=d.get("age_s"))
        return done(status, {**obj, "replay": True} if again else obj, op=str(d.get("op", ""))[:20], plan=str(d.get("plan", ""))[:9])
    if path == "/api/m/prefs" and method == "GET":        # Learned preferences (V5 G3): the same store as the dashboard page
        if not need("prefs"):
            return
        from . import mobile_prefs
        try:
            return h._json(200, mobile_prefs.view(mob))
        except Exception as e:
            logs.warn("mobile", "MOBILE_PREFS_FAILED", "preferences could not be read", error_type=type(e).__name__)
            return h._json(503, {"error": "preferences are not available right now"})
    if path == "/api/m/prefs/op" and method == "POST":
        if not need("prefs"):
            return
        from . import mobile_prefs
        d = _json_body(h)
        if d is None:
            return
        cid = d.get("id")
        if not (isinstance(cid, str) and MSG_ID_RE.match(cid)):
            return h._json(400, {"error": "command id (8-64 letters, digits or _ . : -) is required"})
        status, obj, again = mob.cont.execute(dev["id"], cid, "pref", lambda: mobile_prefs.action(mob, d, dev["id"]),
                                              age_s=d.get("age_s"))
        return done(status, {**obj, "replay": True} if again else obj, op=str(d.get("op", ""))[:20], pref=str(d.get("pref", ""))[:9])
    if path == "/api/m/drive" and method == "GET":        # Drive tab (V6 #218): drive mode read-only, queue COUNT + tool names
        if not need("drive"):
            return
        from . import mobile_drive
        try:
            return h._json(200, mobile_drive.view(mob))
        except Exception as e:
            logs.warn("mobile", "MOBILE_DRIVE_FAILED", "drive state could not be read", error_type=type(e).__name__)
            return h._json(503, {"error": "drive state is not available right now"})
    if path == "/api/m/drive/briefing" and method == "GET":       # the phone ANSWERS the passenger question; nothing is stored
        if not need("drive"):
            return
        from . import mobile_drive
        from urllib.parse import parse_qs
        ans = (parse_qs(url.query).get("passenger") or [""])[0]
        st, obj = mobile_drive.briefing(mob, ans)
        return done(st, obj) if st != 200 else h._json(st, obj)
    if path == "/api/m/drive/journey" and method == "POST":       # typed places in, estimate + Maps/Waze links out (no location, no auto-open)
        if not need("drive"):
            return
        from . import mobile_drive
        if not mob.rate_ok("drive-journey:" + dev["id"], mobile_drive.JOURNEY_RATE_PER_MIN):
            mob.audit("rate_limited", device=dev["id"], path=path, scope="journey")
            return h._json_extra(429, {"error": "too many trip requests: wait a minute"}, {"Retry-After": "60"})
        d = _json_body(h)
        if d is None:
            return
        st, obj = mobile_drive.journey(mob, d)
        return done(st, obj, op="journey")
    if path == "/api/m/signals" and method == "POST":
        if "tasks" not in dev["scopes"] and not need("status"):     # stop / snooze only reduce activity: status is enough
            return
        d = _json_body(h)
        if d is None:
            return
        cid = d.get("id")
        if not (isinstance(cid, str) and MSG_ID_RE.match(cid)):
            return h._json(400, {"error": "command id (8-64 letters, digits or _ . : -) is required"})
        status, obj, again = mob.cont.execute(dev["id"], cid, "signals", lambda: signals_action(mob, d, dev["id"]),
                                              age_s=d.get("age_s"))
        return done(status, {**obj, "replay": True} if again else obj, op=str(d.get("op", ""))[:20])
    if path == "/api/m/approvals" and method == "GET":
        if not need("approvals") or not need_page("approvals"):
            return
        return h._json(200, approvals_view(mob))
    m = re.fullmatch(r"/api/m/approvals/([0-9a-f]{16})", path)
    if m and method == "POST":
        if not need("approvals") or not need_page("approvals"):
            return
        d = _json_body(h)
        if d is None:
            return
        if not isinstance(d.get("approved"), bool):
            return h._json(400, {"error": "body needs {\"approved\": true|false}"})
        cid = d.get("id")
        if cid is not None and not (isinstance(cid, str) and MSG_ID_RE.match(cid)):
            return h._json(400, {"error": "command id must be 8-64 letters, digits or _ . : -"})
        mob.cont.beat(dev["id"], used=True)
        status, obj, again = mob.cont.execute(
            dev["id"], cid, "approval", lambda: answer_approval(mob, m.group(1), d["approved"], dev["id"]),
            age_s=d.get("age_s"))
        return done(status, {**obj, "replay": True} if again else obj, decision="approve" if d["approved"] else "deny")
    return h._json(404, {"error": "not found"})


def health_view(mob: MobileAccess) -> dict:
    """Read-only 'Health & speed' for a device: component status plus the latest self-test / speed-test sentences."""
    try:
        st = mob.bridge.status() if mob.bridge else {"core": "unknown", "components": {}}
    except Exception as e:
        logs.warn("mobile", "MOBILE_STATUS_FAILED", "status could not be read", error_type=type(e).__name__)
        st = {"core": "unreachable", "components": {}}
    out = {**st, "tls": mob.scheme == "https", "tests": {}}
    try:
        from . import selfspeed
        hist = selfspeed.History(mob.home).view()
        for kind, d in hist.get("latest", {}).items():
            out["tests"][kind] = {"status": d.get("status"), "ts": d.get("ts"),
                                  "en": selfspeed._summary(kind, d, "en"), "he": selfspeed._summary(kind, d, "he")}
    except Exception:
        pass                                      # no test history yet, or the feature is not installed
    return out


def _chat_routes(h, mob, dev, path, method, done, need, need_page) -> None:
    """The streaming web chat for a paired device: state / send / events (chat_engine.WebChat). Needs the chat scope AND
    the chat page; rate limits, message-id idempotency and the content-free audit are the same as the older /chat."""
    from urllib.parse import parse_qs
    from . import chat_engine
    if not need("chat") or not need_page("chat"):
        return
    q = parse_qs(urlsplit(h.path).query)
    if path == "/api/m/chat/state" and method == "GET":
        st, obj = chat_engine.handle_state(mob.chat, q, device=True, home=mob.home)
        return h._json(st, {**obj, "phone_approvals": mob.settings()["phone_approvals"]})
    if path == "/api/m/chat/events" and method == "GET":
        st, obj = chat_engine.handle_events(mob.chat, q, device=True, home=mob.home)
        return h._json(st, {**obj, "phone_approvals": mob.settings()["phone_approvals"]})
    if path == "/api/m/chat/send" and method == "POST":
        d = _json_body(h)
        if d is None:
            return
        if not mob.rate_ok("chat:" + dev["id"], CHAT_RATE_PER_MIN):
            return h._json_extra(429, {"error": "too many messages, slow down", "code": "RATE_LIMITED"}, {"Retry-After": "30"})
        text = str(d.get("text", ""))
        st, obj = chat_engine.handle_send(mob.chat, {**d, "text": text}, client=dev["id"], device=True)
        return done(st, obj, chars=len(text))
    return h._json(404, {"error": "not found"})


def _goal_brief(g: dict) -> dict:
    return {"id": g["id"], "title": str(g["title"])[:120], "status": g["status"], "percent": g.get("progress"),
            "steps": [{"id": x["id"], "text": str(x["text"])[:120], "done": bool(x.get("done"))} for x in g.get("steps", [])]}


GOAL_OPS = ("status", "step_set")


def goal_action(mob: MobileAccess, d: dict) -> tuple[int, dict]:
    """A phone may pause/resume/drop/finish a goal and tick a step; it may NOT delete, edit or add (PC only)."""
    goals = mob.bridge.goals() if mob.bridge else None
    if goals is None:
        return 503, {"error": "goals are not available right now"}
    op = d.get("op")
    try:
        if op == "status":
            if d.get("status") not in ("active", "paused", "dropped", "done"):
                return 400, {"error": "status must be active, paused, dropped or done"}
            return 200, {"goal": _goal_brief(goals.set_status(int(d.get("goal", 0)), d["status"], by="phone"))}
        if op == "step_set":
            return 200, {"goal": _goal_brief(goals.set_step(int(d.get("step", 0)), bool(d.get("done", True)), by="phone"))}
    except KeyError:
        return 404, {"error": "no such goal"}
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}
    return 400, {"error": "op must be " + " or ".join(GOAL_OPS)}


def signals_action(mob: MobileAccess, d: dict, device: str) -> tuple[int, dict]:
    c, op = mob.cont, d.get("op")
    if op == "snooze":
        try:
            until = c.snooze(float(d.get("minutes", 60)), by=device)
        except (TypeError, ValueError):
            return 400, {"error": "minutes must be a number"}
        return 200, {"snoozed_until": until}
    if op == "stop":
        c.stop_signals(by=device)
        return 200, {"stopped": True}
    return 400, {"error": "op must be snooze or stop (switching on is done on the computer)"}


def approvals_view(mob: MobileAccess) -> dict:
    from .approvals import read_pending_file
    now = time.time()
    rows = []
    for r in read_pending_file(mob.home, reveal=False):
        rows.append({"id": r.get("id"), "title": str(r.get("title") or "")[:120], "kind": r.get("kind"),
                     "risk": r.get("risk"), "seconds_left": max(0, int(float(r.get("expires") or 0) - now)),
                     "typed": bool(r.get("typed")), "pc_only": bool(r.get("pc_only"))})
    on = mob.settings()["phone_approvals"]
    if on:                                   # a phone that may answer counts as a place to answer (no auto-deny)
        try:
            (mob.home / "approvals").mkdir(parents=True, exist_ok=True)
            (mob.home / "approvals" / "dashboard_seen").write_text(str(now), encoding="utf-8")
        except OSError:
            pass
    return {"pending": rows, "phone_approvals": on,
            "note": "Answer on the computer." if not on else "Typed-code and plan approvals are answered on the computer only."}


def answer_approval(mob: MobileAccess, aid: str, approved: bool, device: str) -> tuple[int, dict]:
    from .approvals import read_pending_file, submit_answer
    if not mob.settings()["phone_approvals"]:
        return 403, {"error": "phone approvals are off: answer on the computer", "code": "PHONE_APPROVALS_OFF"}
    row = next((r for r in read_pending_file(mob.home, reveal=False) if r.get("id") == aid), None)
    if row is not None and row.get("typed") and approved:
        return 403, {"error": "this request needs the code shown on the computer", "code": "TYPED_PC_ONLY"}
    if row is not None and row.get("pc_only") and approved:
        return 403, {"error": "this approval can only be answered on the computer", "code": "PC_ONLY"}
    ok, why = submit_answer(mob.home, aid, approved, channel="phone")
    if ok:
        mob.audit("phone_approval", device=device, decision="approve" if approved else "deny",
                  risk=(row or {}).get("risk"), kind=(row or {}).get("kind"))    # no title, no details
        logs.info("mobile", "MOBILE_APPROVAL_ANSWERED", "an approval was answered from a phone", device=device,
                  decision="approve" if approved else "deny")
        return 200, {"ok": True}
    from .approvals import refusal_code
    return 409, {"error": why, "code": refusal_code(why)}


# ---------------------------------------------------------------------- TLS helpers
def tls_paths(home: Path) -> tuple[Path, Path]:
    return Path(home) / TLS_DIR / "cert.pem", Path(home) / TLS_DIR / "key.pem"


def make_selfsigned(home: Path, host: str | None) -> tuple[Path, Path]:
    """A self-signed certificate for the PC's LAN address (key mode 0600). The phone shows a one-time warning."""
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    cert_p, key_p = tls_paths(home)
    cert_p.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "JARVES")])
    sans: list = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    if host:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            sans.append(x509.DNSName(host))
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .sign(key, hashes.SHA256()))
    write_private(key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_p, key_p


# ---------------------------------------------------------------------- owner (loopback) dashboard routes
def dashboard_routes(register) -> None:
    def mob(ctx) -> MobileAccess:
        return ctx.mobile

    def info(ctx, _m, _b):
        a = mob(ctx)
        t = a.tailnet or {}
        return 200, {"enabled": a.enabled, "host": a.host, "port": a.port, "tls": a.scheme == "https",
                     "tailnet": {"state": t.get("state", "unused"), "present": bool(t.get("present")), "ip": t.get("ip"),
                                 "name": t.get("name"), "host": a.tailnet_host(), "bind": a.bind,
                                 "url": f"{a.scheme}://{a.tailnet_host()}:{a.port}/m/" if a.tailnet_host() else None,
                                 "tls_source": a.tls_source},
                     "reachable_from_phone": bool(a.enabled and a.host), **a.settings(), "devices": a.devices(),
                     "how": "jarvis features on mobile_access, then jarvis dashboard --mobile"}

    def code(ctx, _m, _b):
        a = mob(ctx)
        if not a.enabled:
            return 409, {"error": "mobile access is off: switch mobile_access on and run `jarvis dashboard --mobile`"}
        return 200, a.new_code()

    def revoke(ctx, m, _b):
        who = getattr(ctx, "principal", None) or {}
        ok = mob(ctx).revoke(m.group(1), by=who.get("id", "local"))
        return (200, {"ok": True}) if ok else (404, {"error": "no such active phone"})

    def setting(ctx, _m, body):
        body = body or {}
        keys = [k for k in ("phone_approvals", "bind_tailnet_only") if k in body]
        if not keys or any(not isinstance(body[k], bool) for k in keys):
            return 400, {"error": "body needs {\"phone_approvals\": true|false} and/or {\"bind_tailnet_only\": true|false}"}
        if "phone_approvals" in body:
            mob(ctx).set_phone_approvals(body["phone_approvals"], by="local")
        if "bind_tailnet_only" in body:
            mob(ctx).set_bind_tailnet_only(body["bind_tailnet_only"], by="local")
        return 200, {"ok": True, **mob(ctx).settings()}

    def revoke_all(ctx, _m, _b):
        return 200, {"ok": True, "revoked": mob(ctx).revoke_all(by="local")}

    def scopes(ctx, m, body):
        sc = (body or {}).get("scopes")
        if not isinstance(sc, list):
            return 400, {"error": "body needs {\"scopes\": [...]}"}
        ok = mob(ctx).set_scopes(m.group(1), [str(s) for s in sc], by="local")
        return (200, {"ok": True}) if ok else (404, {"error": "no such active phone"})

    def pages(ctx, m, body):
        pg = (body or {}).get("pages")
        if not isinstance(pg, list):
            return 400, {"error": "body needs {\"pages\": [...]}", "pc_only": list(PC_ONLY_PAGES)}
        if any(str(p) in PC_ONLY_PAGES for p in pg):
            return 403, {"error": "settings, secrets, files and devices stay on the computer", "code": "PAGE_PC_ONLY"}
        ok = mob(ctx).set_pages(m.group(1), [str(p) for p in pg], by="local")
        return (200, {"ok": True}) if ok else (404, {"error": "no such active phone"})

    def audit(ctx, _m, _b):
        return 200, {"entries": mob(ctx).audit_tail()}
    register("GET", r"/api/mobile", info)
    register("POST", r"/api/mobile/code", code)
    register("POST", r"/api/mobile/settings", setting)
    register("POST", r"/api/mobile/revoke-all", revoke_all)
    register("POST", r"/api/mobile/devices/([0-9a-f]{12})/revoke", revoke)
    register("POST", r"/api/mobile/devices/([0-9a-f]{12})/scopes", scopes)
    register("POST", r"/api/mobile/devices/([0-9a-f]{12})/pages", pages)
    register("GET", r"/api/mobile/audit", audit)
