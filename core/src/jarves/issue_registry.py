"""Issues registry: what JARVES itself finds wrong, as a visible, trackable, content-free list (owner request 21 Oct 2026).

One small local file (issues_registry.json in the state folder, atomic replace, written only when something happens).
Every record is DE-DUPLICATED (id = JI-<8 hex of area|code>, the same on every install, so the developer can group
reports) and holds ONLY content-free evidence:

    id, severity P1-P3, area, code, title, first_seen / last_seen, count, versions seen, status
    (new | acknowledged | fixed (+ fixed_in version) | ignored), evidence = numbers, booleans and identifiers
    (codes, counts, timings, versions, function names); never text of a conversation, a path, an address or a key.

The privacy guarantee is structural: `clean_evidence` keeps a value only when it is a number / bool or a short
identifier-shaped string (no spaces, no slashes, no @) that is not a secret; everything else is dropped and counted.

Feeders (all automatic, none needs the network): the self-test and the speed test (selfspeed.History.append), the
Self-Diagnostics findings and the self-check crash finding (diag.service), repeated tool failures
(TOOL_RESULT ok=false codes above a threshold) and crashes (CRASH / ERROR records with an exception) through a log
sink, dashboard JS errors reported by the page (rate-limited; error class + script name + line only), and manual
`jarvis issues add`.

Reporting to the developer is ONLY ever done after the owner approved the exact bundle shown (`bundle`); with a GitHub
token it files (or comments on) an issue labelled `from-diagnostics` on igalgerman/jarves, without one it saves the
bundle in issue_reports/ for the owner to attach. Nothing leaves the computer before the approval.

Fixed-in tracking: when the installed version changes and the CHANGELOG / release notes of that version list the id or
the code on a "Fixes" line (or under a "Fixes" heading), the record becomes `fixed` in that version and the owner is told once.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from . import changelog_parts
from . import features as feat
from . import logs
from .skills import expected_tool_refusal

FEATURE = "issue_reports"
FILE = "issues_registry.json"
REPORT_DIR = "issue_reports"
REPO = "igalgerman/jarves"
LABEL = "from-diagnostics"
API_BASE = "https://api.github.com"              # tests point this at a local fake endpoint
STATUSES = ("new", "acknowledged", "fixed", "ignored")
OPEN = ("new", "acknowledged")
SEVERITIES = ("P1", "P2", "P3")
MAX_RECORDS = 300
TOOL_FAIL_THRESHOLD = 5                          # failures of one tool + code ...
TOOL_FAIL_WINDOW_S = 3600.0                      # ... within this window become an issue
WRITE_EVERY_S = 30.0                             # count-only updates of a known issue are batched
CLIENT_ERRORS_PER_WINDOW = 20                    # dashboard JS errors accepted per window (all pages together)
CLIENT_WINDOW_S = 600.0
SKIP_SELFTEST = {"version", "connections"}       # an available update / a not-yet-connected feature is not a bug
AREAS = {"selftest": ("Self-test", "בדיקה עצמית"), "speed": ("Speed", "מהירות"), "health": ("Health", "תקינות"),
         "tools": ("Tools", "כלים"), "core": ("Core", "ליבה"), "dashboard": ("Dashboard", "לוח הבקרה"),
         "manual": ("Manual", "ידני"), "crash": ("Crash", "קריסה")}

_ID = re.compile(r"^JI-[0-9a-f]{8}$")
_IDENT = re.compile(r"^[A-Za-z0-9_.:+\-]{1,64}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_TITLE = re.compile(r"^[^\\/@<>`\x00-\x1f]{1,100}$")
_AREA = re.compile(r"^[a-z][a-z0-9_]{0,19}$")
_LOCKS: dict[str, threading.RLock] = {}


def _lock(path: Path) -> threading.RLock:
    return _LOCKS.setdefault(str(path), threading.RLock())


# ---------------------------------------------------------------------- privacy: what may be kept
def clean_value(v: object):
    """A number, a bool or a short identifier-shaped string, else None (dropped). Never free text."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 3) if isinstance(v, float) else v
    if isinstance(v, str) and _IDENT.match(v) and not logs.has_secret(v) and not re.fullmatch(r"[A-Za-z0-9_\-]{32,}", v):
        return v
    return None


def clean_evidence(ev: object) -> tuple[dict, int]:
    """(kept, dropped): keys are snake_case words; values are scalars or short lists of scalars."""
    kept: dict = {}
    dropped = 0
    for k, v in (ev.items() if isinstance(ev, dict) else []):
        if not isinstance(k, str) or not _KEY.match(k) or logs.is_credential(k):
            dropped += 1
            continue
        if isinstance(v, (list, tuple)):
            items = [clean_value(x) for x in list(v)[:8]]
            ok = [x for x in items if x is not None]
            dropped += len(list(v)) - len(ok)
            if ok:
                kept[k] = ok
            continue
        c = clean_value(v)
        if c is None and v is not None:
            dropped += 1
        elif c is not None or v is None:
            kept[k] = c
    return kept, dropped


def clean_title(t: object, fallback: str) -> str:
    s = logs.redact(str(t or "")).strip()
    return s if _TITLE.match(s) and not logs.has_secret(s) else fallback


def clean_code(c: object) -> str:
    return re.sub(r"[^A-Za-z0-9_.:+\-]", "_", str(c or "UNKNOWN"))[:64] or "UNKNOWN"


def issue_id(area: str, code: str) -> str:
    return "JI-" + hashlib.sha256(f"{area}|{code}".encode()).hexdigest()[:8]


def frame_names(frames: object) -> list[str]:
    """'jarves/x.py:12 in func' -> 'x.func' (no line numbers, no paths, no locals)."""
    out: list[str] = []
    for f in (frames if isinstance(frames, (list, tuple)) else [])[-6:]:
        m = re.match(r"^(?:.*/)?([A-Za-z0-9_]+)\.py:\d+ in ([A-Za-z0-9_<>]+)$", str(f))
        if m:
            out.append(f"{m.group(1)}.{m.group(2)}".replace("<", "").replace(">", ""))
    return out


def _vt(v: str):
    from .updates import version_tuple
    return version_tuple(str(v or ""))


def app_version() -> tuple[str, str]:
    """(version, commit-or-empty) of this copy."""
    try:
        from .updates import build_info
        b = build_info()
        return str(b.get("version") or ""), str(b.get("commit") or "")[:12]
    except Exception:
        from . import __version__
        return __version__, ""


# ---------------------------------------------------------------------- the registry
class Registry:
    def __init__(self, home: Path, *, clock: Callable[[], float] = time.time, version: str | None = None) -> None:
        self.home, self.clock = Path(home), clock
        self.path = self.home / FILE
        self.lock = _lock(self.path)
        self.version = version if version is not None else app_version()[0]

    # ---- storage
    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict) or not isinstance(d.get("issues"), dict):
            d = {"v": 1, "app_version": d.get("app_version", "") if isinstance(d, dict) else "", "issues": {}}
        return d

    def _save(self, d: dict) -> None:
        iss = d["issues"]
        if len(iss) > MAX_RECORDS:            # drop the oldest closed ones first
            keep = sorted(iss.values(), key=lambda r: (r["status"] in OPEN, r["last_seen"]))[-MAX_RECORDS:]
            d["issues"] = {r["id"]: r for r in keep}
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            logs.warn("issues", "ISSUES_SAVE_FAILED", "issues registry not saved")

    # ---- recording
    def record(self, area: str, code: str, severity: str = "P3", evidence: dict | None = None, *, title: str = "",
               title_he: str = "", source: str = "", n: int = 1, now: float | None = None,
               observation: str = "") -> dict | None:
        area = area if _AREA.match(area or "") else "core"
        code = clean_code(code)
        severity = severity if severity in SEVERITIES else "P3"
        iid = issue_id(area, code)
        now = self.clock() if now is None else now
        ev, dropped = clean_evidence(evidence or {})
        # Findings are snapshots, not a fresh failure every time the health timer
        # reads them. Persist only a hash, never the source's private text.
        observed = hashlib.sha256(observation.encode()).hexdigest() if observation else ""
        with self.lock:
            d = self.load()
            r = d["issues"].get(iid)
            new = r is None
            if r is not None and observed:
                if r.get("observation") == observed:
                    return r
                if "observation" not in r and r.get("evidence") == ev and r["severity"] <= severity:
                    # Upgrade an existing aggregate without reporting it anew.
                    r["observation"] = observed
                    self._save(d)
                    return r
            if new:
                r = {"id": iid, "area": area, "code": code, "severity": severity, "status": "new", "first_seen": now,
                     "last_seen": now, "count": 0, "versions": [], "fixed_in": "", "evidence": {}, "dropped": 0,
                     "title": clean_title(title, code), "title_he": clean_title(title_he, ""), "sources": [],
                     "reported_url": "", "reported_at": 0, "bundle_saved": "", "fixed_notice": False}
                d["issues"][iid] = r
            if observed:
                r["observation"] = observed
            r["count"] += n
            r["last_seen"] = now
            r["severity"] = min(r["severity"], severity)                  # "P1" < "P2": the worse one stays
            if ev:
                r["evidence"] = ev
            r["dropped"] = max(r.get("dropped", 0), dropped)
            if source and source not in r["sources"]:
                r["sources"] = (r["sources"] + [clean_code(source)])[-5:]
            if self.version and self.version not in r["versions"]:
                r["versions"] = (r["versions"] + [self.version])[-5:]
            regressed = False
            if r["status"] == "fixed" and r["fixed_in"] and self.version:
                a, b = _vt(self.version), _vt(r["fixed_in"])
                if a is not None and b is not None and a >= b:            # seen again in a version that should have the fix
                    r["status"], r["fixed_notice"], regressed = "new", False, True
            self._save(d)
        if new:
            logs.info("issues", "ISSUE_NEW", "a new issue was recorded", item=iid, source=source or "-")
        elif regressed:
            logs.warn("issues", "ISSUE_REGRESSED", "a fixed issue came back", item=iid)
        return r

    # ---- reading
    def list(self, status: str | None = None, severity: str | None = None, area: str | None = None) -> list[dict]:
        rows = list(self.load()["issues"].values())
        if status == "open":
            rows = [r for r in rows if r["status"] in OPEN]
        elif status:
            rows = [r for r in rows if r["status"] == status]
        if severity:
            rows = [r for r in rows if r["severity"] == severity]
        if area:
            rows = [r for r in rows if r["area"] == area]
        return sorted(rows, key=lambda r: (r["status"] not in OPEN, r["severity"], -r["count"], -r["last_seen"]))

    def get(self, iid: str) -> dict | None:
        return self.load()["issues"].get(iid)

    def counts(self) -> dict:
        rows = self.load()["issues"].values()
        op = [r for r in rows if r["status"] in OPEN]
        return {"open": len(op), "p1": sum(r["severity"] == "P1" for r in op), "p2": sum(r["severity"] == "P2" for r in op),
                "p3": sum(r["severity"] == "P3" for r in op), "new": sum(r["status"] == "new" for r in op),
                "total": len(rows)}

    # ---- changes by the owner
    def set_status(self, iid: str, status: str, *, version: str = "") -> dict | None:
        if status not in STATUSES:
            raise ValueError("status must be new, acknowledged, fixed or ignored")
        with self.lock:
            d = self.load()
            r = d["issues"].get(iid)
            if r is None:
                return None
            r["status"] = status
            if status == "fixed":
                r["fixed_in"] = version or self.version or r["fixed_in"]
                r["fixed_notice"] = True            # the owner did it: nothing to announce
            elif status in ("new", "acknowledged"):
                r["fixed_in"] = ""
            self._save(d)
        logs.info("issues", "ISSUE_STATUS", "issue status changed", item=iid, status=status)
        return r

    def mark_reported(self, iid: str, url: str = "", saved: str = "") -> None:
        with self.lock:
            d = self.load()
            r = d["issues"].get(iid)
            if r is None:
                return
            if url:
                r["reported_url"], r["reported_at"] = url, self.clock()
            if saved:
                r["bundle_saved"] = saved
            if r["status"] == "new":
                r["status"] = "acknowledged"
            self._save(d)

    # ---- fixed-in tracking
    def check_updated(self, notes: str, *, version: str | None = None) -> list[dict]:
        """If the installed version changed since the last look, mark every open issue whose id or code is listed on a
        Fixes line of `notes` as fixed in that version. Returns the records newly marked (to tell the owner ONCE)."""
        ver = version if version is not None else self.version
        with self.lock:
            d = self.load()
            if not ver:
                return []
            changed = d.get("app_version", "") != ver
            d["app_version"] = ver
            hit: list[dict] = []
            if changed or any(r["status"] == "fixed" and not r["fixed_notice"] for r in d["issues"].values()):
                ids, codes = fixes_in(notes)
                for r in d["issues"].values():
                    if r["status"] in OPEN and (r["id"] in ids or r["code"] in codes):
                        r["status"], r["fixed_in"], r["fixed_notice"] = "fixed", ver, False
                        hit.append(r)
            if changed or hit:
                self._save(d)
        for r in hit:
            logs.info("issues", "ISSUE_FIXED_IN", "an issue is fixed in this version", item=r["id"], version=ver)
        return hit

    def take_unannounced(self) -> list[dict]:
        """Fixed-in records the owner has not been told about yet; marks them told."""
        with self.lock:
            d = self.load()
            out = [r for r in d["issues"].values() if r["status"] == "fixed" and not r["fixed_notice"]]
            for r in out:
                r["fixed_notice"] = True
            if out:
                self._save(d)
        return out


_FIX_LINE = re.compile(r"\bfix(?:es|ed)?\b", re.I)
_TOKEN = re.compile(r"\bJI-[0-9a-f]{8}\b|\b[A-Z][A-Z0-9]*_[A-Za-z0-9_.:+\-]{1,62}\b|\b[A-Z][A-Z0-9.:+\-]{3,63}\b")
_ID_TOKEN = re.compile(r"\bJI-[0-9a-f]{8}\b")


def fixes_in(text: str) -> tuple[set[str], set[str]]:
    """(ids, codes) listed on 'Fixes: ...' lines or under a 'Fixes' heading of a changelog / release notes text."""
    ids: set[str] = set()
    codes: set[str] = set()
    in_section = False
    for line in str(text or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            in_section = bool(_FIX_LINE.search(s))
            continue
        if not s:
            continue
        if _FIX_LINE.search(s) or in_section:
            for t in _TOKEN.findall(s):
                (ids if _ID_TOKEN.fullmatch(t) else codes).add(t)
    return ids, codes


def changelog_text(extra: str = "") -> str:
    """The CHANGELOG shipped with this copy (packaged next to the package, or the checkout's) plus any release notes."""
    parts = [extra]
    here = Path(__file__).resolve()
    for p in (here.with_name("CHANGELOG.md"), *[a / "CHANGELOG.md" for a in here.parents[1:4]]):
        try:
            if p.is_file():
                parts.append(changelog_parts.read(p))
                break
        except OSError:
            continue
    return "\n".join(x for x in parts if x)


# ---------------------------------------------------------------------- feeders
def ingest_selftest(reg: Registry, d: dict) -> int:
    n = 0
    for c in d.get("checks", []):
        st = c.get("status")
        if st not in ("warn", "fail") or c.get("id") in SKIP_SELFTEST:
            continue
        sev = "P1" if st == "fail" and c.get("id") in ("config", "databases") else "P2" if st == "fail" else "P3"
        reg.record("selftest", f"SELFTEST_{clean_code(c.get('id')).upper()}", sev,
                   {"check": c.get("id"), "status": st, "ms": c.get("ms"), "deep": bool(c.get("deep"))},
                   title=str(c.get("title") or ""), title_he=str(c.get("title_he") or ""), source="selftest")
        n += 1
    return n


def ingest_speedtest(reg: Registry, d: dict) -> int:
    n = 0
    for m in d.get("metrics", []):
        st = m.get("status")
        if st not in ("warn", "fail"):
            continue
        reg.record("speed", f"SPEED_{clean_code(m.get('name')).upper()}", "P2" if st == "fail" else "P3",
                   {"metric": m.get("name"), "status": st, "value": m.get("value"), "unit": m.get("unit"),
                    "max": m.get("max"), "trend_pct": m.get("trend_pct"), "class": d.get("class")},
                   title=f"Speed: {m.get('name')}", title_he=f"מהירות: {m.get('name')}", source="speedtest")
        n += 1
    return n


def ingest_findings(reg: Registry, found: list) -> int:
    """A full snapshot of active findings, including the self-check crash finding.

    Absence ends an observation, so an identical finding after recovery is a new
    occurrence. Merely polling the same active snapshot is not.
    """
    n = 0
    seen = set()
    for f in found:
        g = (lambda k, dflt="": getattr(f, k, None) if not isinstance(f, dict) else f.get(k, dflt))
        fp = str(g("fingerprint") or "")
        if not fp:
            continue
        sev = {"high": "P1", "medium": "P2"}.get(str(g("severity")), "P3")
        area = "crash" if g("category") == "crash" else "health"
        seen.add(issue_id(area, f"FINDING_{clean_code(fp)}"))
        evidence = {"category": g("category"), "count": g("count") or 1, "finding": fp}
        detail, _ = clean_evidence(g("evidence", {}))
        observation = json.dumps({"summary": evidence, "severity": sev, "detail": detail,
                                  "run": g("observation_run", None)}, sort_keys=True, default=str)
        reg.record(area, f"FINDING_{clean_code(fp)}", sev, evidence,
                   title=str(g("title") or ""), title_he=str(g("title_he") or ""), source="diag",
                   observation=observation)
        n += 1
    with reg.lock:
        snapshot = reg.load()
        ended = [r for iid, r in snapshot["issues"].items()
                 if iid not in seen and "diag" in r.get("sources", []) and r.get("observation")]
        for r in ended:
            r["observation"] = ""
        if ended:
            reg._save(snapshot)
    return n


_EXPECTED_FAIL = ("SELFTEST_", "SPEEDTEST_")


class IssueSink(logging.Handler):
    """Log sink inside the Core: repeated tool failures and crashes become issues. Never logs from emit()."""

    def __init__(self, reg: Registry, *, threshold: int = TOOL_FAIL_THRESHOLD, window_s: float = TOOL_FAIL_WINDOW_S) -> None:
        super().__init__(logging.WARNING)
        self.reg, self.threshold, self.window = reg, threshold, window_s
        self._fails: dict[tuple[str, str], deque] = {}
        self._busy = threading.local()
        self._last: dict[str, float] = {}                 # repeat storms write at most once per WRITE_EVERY_S per issue
        self._pend: dict[str, tuple[int, tuple, dict]] = {}

    def _note(self, area: str, code: str, *args, **kw) -> None:
        key = issue_id(area, clean_code(code))
        n, _, _ = self._pend.get(key, (0, (), {}))
        self._pend[key] = (n + 1, (area, code, *args), kw)
        now = self.reg.clock()
        if now - self._last.get(key, -1e18) >= WRITE_EVERY_S:
            self.flush(key)

    def flush(self, only: str | None = None) -> None:
        for key in [only] if only else list(self._pend):
            n, args, kw = self._pend.pop(key, (0, (), {}))
            if n:
                self._last[key] = self.reg.clock()
                self.reg.record(*args, n=n, **kw)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._busy, "on", False):
            return
        self._busy.on = True
        try:
            code = str(getattr(record, "code", "-"))
            f = getattr(record, "fields", None) or {}
            if code.startswith("ISSUE") or code == "ISSUES_SAVE_FAILED":
                return
            if code == "TOOL_RESULT":
                self._tool(f)
            elif code == "CRASH" or (record.levelno >= logging.ERROR and (record.exc_info or f.get("exc_type"))):
                self._crash(code, str(getattr(record, "component", "core")), f, record)
        except Exception:
            pass
        finally:
            self._busy.on = False

    def _tool(self, f: dict) -> None:
        if f.get("ok") is not False:
            return
        tool = clean_code(f.get("tool") or "unknown")[:40]
        rc = clean_code(f.get("result_code") or "TOOL_FAILED")
        if expected_tool_refusal(rc, f.get("origin")) or rc.startswith(_EXPECTED_FAIL) or rc.endswith("_SKIPPED"):
            return
        now = self.reg.clock()
        q = self._fails.setdefault((tool, rc), deque())
        q.append(now)
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.threshold:
            self._note("tools", f"TOOL_{tool}_{rc}", "P2", {"tool": tool, "result_code": rc, "failures": len(q),
                                                           "window_s": self.window}, title=f"Tool {tool} keeps failing ({rc})",
                       title_he=f"הכלי {tool} נכשל שוב ושוב ({rc})", source="tool_failures")

    def _crash(self, code: str, component: str, f: dict, record: logging.LogRecord) -> None:
        frames = f.get("exc_frames")
        if not frames and record.exc_info:
            frames = logs.frames(record.exc_info[2])
        names = frame_names(frames)
        et = clean_code(f.get("exc_type") or (record.exc_info[0].__name__ if record.exc_info else "Error"))
        crash = code == "CRASH"
        comp = clean_code(component)[:20]
        self._note("crash" if crash else "core", f"{code}_{et}_{names[-1] if names else comp}"[:64],
                   "P1" if crash else "P2",
                   {"log_code": code, "exc_type": et, "component": comp, "frames": names, "crash_kind": f.get("crash_kind")},
                   title=f"{'Crash' if crash else 'Error'}: {et} in {names[-1] if names else comp}",
                   title_he=f"{'קריסה' if crash else 'שגיאה'}: {et} ב-{names[-1] if names else comp}",
                   source="crash" if crash else "errors")


def client_error(reg: Registry, body: dict, *, state: dict) -> tuple[int, dict]:
    """A dashboard JS error reported by the page: only the error class, the page, the script name, line and column.
    The message and the stack text (which can hold what the owner typed) are neither sent by the page nor read here."""
    now = reg.clock()
    q: deque = state.setdefault("q", deque())
    while q and now - q[0] > CLIENT_WINDOW_S:
        q.popleft()
    if len(q) >= CLIENT_ERRORS_PER_WINDOW:
        return 429, {"error": "rate limited"}
    b = body or {}
    name, file, page = str(b.get("name") or ""), str(b.get("file") or ""), str(b.get("page") or "")
    line, col = b.get("line"), b.get("col")
    if not (re.fullmatch(r"[A-Za-z]{1,40}", name) and re.fullmatch(r"[a-z0-9_.\-]{1,40}\.js", file)
            and re.fullmatch(r"[a-z0-9_\-]{0,30}", page) and isinstance(line, int) and not isinstance(line, bool)
            and 0 <= line < 1_000_000 and (col is None or (isinstance(col, int) and 0 <= col < 100_000))):
        return 400, {"error": "not a valid error report"}
    q.append(now)
    r = reg.record("dashboard", f"JS_{name}_{file}_{line}", "P3", {"error_class": name, "script": file, "line": line,
                                                                   "col": col, "page": page or "home"},
                   title=f"Dashboard script error: {name} in {file}", title_he=f"שגיאת סקריפט בלוח הבקרה: {name} ב-{file}",
                   source="dashboard")
    return 200, {"id": r["id"] if r else ""}


# ---------------------------------------------------------------------- the bundle (exactly what would be sent)
def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts or 0))


def bundle(r: dict, *, platform: str | None = None) -> dict:
    """{title, body, labels}: the content-free report. `body` is the exact text that is shown and, after approval, sent."""
    ver, commit = app_version()
    ev = r.get("evidence") or {}
    lines = [f"**Issue:** `{r['id']}` · **Severity:** {r['severity']} · **Area:** {r['area']} · **Code:** `{r['code']}`", "",
             f"**Version:** {ver or '?'}" + (f" · **Commit:** `{commit}`" if commit else "") + f" · **Platform:** {platform or sys.platform}",
             f"**First seen:** {_day(r['first_seen'])} · **Last seen:** {_day(r['last_seen'])} · **Count:** {r['count']}"
             + (f" · **Versions seen:** {', '.join(r.get('versions') or [])}" if r.get("versions") else ""), "",
             "**Evidence (content-free: codes, counts, timings, versions, function names):**"]
    lines += [f"- `{k}`: {json.dumps(v, ensure_ascii=False)}" for k, v in sorted(ev.items())] or ["- (none)"]
    lines += ["", "No conversation text, names, addresses, paths or keys are included.",
              "Reported by JARVES Self-Diagnostics after the owner approved this exact text.",
              f"<!-- jarves-issue: {r['id']} -->"]
    return {"id": r["id"], "title": f"[{r['severity']}] {r['title']}"[:120], "body": "\n".join(lines), "labels": [LABEL]}


GH_CLI = "gh"                                    # tests point this at nothing so a developer's own login is never used


def find_token() -> str:
    """A GitHub token from the environment (JARVES_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN) or the GitHub CLI's own login.
    Local lookups only; no network."""
    for k in ("JARVES_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    try:
        import subprocess
        p = subprocess.run([GH_CLI, "auth", "token"], capture_output=True, text=True, timeout=15)
        return (p.stdout or "").strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def _http(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "jarves-issue-report",
        "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def save_bundle(home: Path, b: dict) -> Path:
    d = Path(home) / REPORT_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{b['id']}.md"
    p.write_text(f"# {b['title']}\n\nLabel: {LABEL}\n\n{b['body']}\n", encoding="utf-8")
    return p


async def report(reg: Registry, iid: str, *, ask, token_fn: Callable[[], str] | None = None, by: str = "dashboard") -> dict:
    """Show-then-ask-then-send. `ask(prompt, **kw)` is the approvals flow; NOTHING touches the network before it says yes."""
    r = reg.get(iid)
    if r is None:
        raise KeyError(iid)
    b = bundle(r)
    approved = False
    if ask is not None:
        try:
            approved = bool(await ask(f"Send this report to the JARVES developer ({REPO}, label {LABEL})?\n\n# {b['title']}\n\n{b['body']}"[:2000],
                                      title="Report an issue to the developer", why="Self-Diagnostics issue report",
                                      risk="medium", kind="message", details={"repo": REPO, "issue": iid}))
        except Exception:
            approved = False
    if not approved:
        logs.info("issues", "ISSUE_REPORT_DENIED", "issue report not approved: nothing sent", item=iid)
        return {"id": iid, "status": "declined", "reason": "not approved; nothing sent"}
    token = (token_fn or find_token)()
    if not token:
        p = save_bundle(reg.home, b)
        reg.mark_reported(iid, saved=p.name)
        logs.info("issues", "ISSUE_REPORT_SAVED", "no GitHub token: report saved for the owner", item=iid)
        return {"id": iid, "status": "saved", "path": str(p),
                "reason": "no GitHub token on this computer; the report is saved so you can attach it to an issue"}
    code, listing = await asyncio.to_thread(_http, "GET", f"{API_BASE}/repos/{REPO}/issues?labels={LABEL}&state=open&per_page=100", token)
    dup = None
    if code == 200 and isinstance(listing, list):
        dup = next((i for i in listing if f"jarves-issue: {iid}" in str(i.get("body") or "")), None)
    if dup:
        code, out = await asyncio.to_thread(_http, "POST", f"{API_BASE}/repos/{REPO}/issues/{dup['number']}/comments", token,
                                            {"body": f"Seen again ({_day(r['last_seen'])}), count {r['count']}.\n\n{b['body']}"})
        url, status = str(dup.get("html_url") or ""), "commented"
    else:
        code, out = await asyncio.to_thread(_http, "POST", f"{API_BASE}/repos/{REPO}/issues", token,
                                            {"title": b["title"], "body": b["body"], "labels": b["labels"]})
        url, status = (str((out or {}).get("html_url") or "") if isinstance(out, dict) else ""), "filed"
    if code not in (200, 201):
        p = save_bundle(reg.home, b)
        reg.mark_reported(iid, saved=p.name)
        logs.warn("issues", "ISSUE_REPORT_FAILED", "GitHub did not accept the report: saved for the owner", item=iid, rc=code)
        return {"id": iid, "status": "saved", "path": str(p), "reason": f"GitHub answered {code}; the report is saved instead"}
    reg.mark_reported(iid, url=url)
    logs.info("issues", "ISSUE_REPORTED", "issue report sent", item=iid, decision=status)
    return {"id": iid, "status": status, "issue_url": url}


# ---------------------------------------------------------------------- voice tool
def spoken(reg: Registry, lang: str) -> str:
    c = reg.counts()
    top = next(iter(reg.list("open")), None)
    he = lang == "he"
    if not c["open"]:
        return "אין כרגע תקלות פתוחות. פרטים בעמוד דיווחי תקלות." if he else "I have no open issues right now. Details on the Bug reports page."
    if he:
        return (f"יש {c['open']} תקלות פתוחות, {c['p1']} בחומרה גבוהה; הראשונה: {top['title_he'] or top['title']}. "
                "פרטים בעמוד דיווחי תקלות.")
    return (f"I have {c['open']} open issue{'s' if c['open'] != 1 else ''}, {c['p1']} serious; first: {top['title']}. "
            "Details on the Bug reports page.")


class _NoArgs(BaseModel):
    pass


def make_skill(home: Path, lang: Callable[[], str]):
    from .skills import Skill, Tool, ToolResult

    class IssuesSkill(Skill):
        name = "issues"
        description = "known problems of the app"

        def tools(self) -> list:
            async def list_(_: _NoArgs) -> ToolResult:
                reg = Registry(home)
                return ToolResult(True, spoken(reg, lang()), {"counts": reg.counts(), "dashboard": "#/bugs"}, code="ISSUES_LISTED")
            return [Tool("list_issues", "Known problems: 'what issues do you have', 'אילו תקלות יש לך'.", list_, _NoArgs, permission="read")]

    return IssuesSkill()


INSTRUCTION = "To answer 'what issues do you have?' ('אילו תקלות יש לך?') call list_issues and say its one sentence."


def tell_fixed(ops, fixed: list[dict], lang: str = "he") -> None:
    """Tell the owner ONCE (an alert, not speech) that updating fixed known issues."""
    alerts = getattr(ops, "alerts", None)
    if not fixed or alerts is None:
        return
    v = fixed[0]["fixed_in"]
    try:
        alerts.raise_("issues", f"הגרסה {v} תיקנה {len(fixed)} תקלות שזוהו אצלך", "; ".join(f["title_he"] or f["title"] for f in fixed[:3])[:300],
                      priority="normal", kind="health", channel="popup", dedupe_key=f"issues-fixed:{v}", ref="#/bugs")
    except Exception:
        logs.warn("issues", "ISSUES_FIXED_ALERT_FAILED", "fixed-in alert not raised")


def attach(core, ops) -> Registry:
    """Called by operations.start: the log sink, the voice tool, the fixed-in check for this version."""
    reg = Registry(core.home)

    def lang() -> str:
        return getattr(core.cfg.voice, "language", "he")

    core.registry.register(make_skill(core.home, lang))
    sink = IssueSink(reg)
    logs.add_sink(sink)
    reg.sink = sink                                                  # type: ignore[attr-defined]
    agent = getattr(core, "agent", None)
    if agent is not None and hasattr(agent, "instructions"):
        base = agent.instructions

        def instructions() -> str:
            try:
                on = core.features.active(FEATURE)
            except Exception:
                on = False
            return f"{base()}\n\n{INSTRUCTION}" if on else base()
        agent.instructions = instructions
    try:
        reg.check_updated(changelog_text())
        tell_fixed(ops, reg.take_unannounced(), lang())
    except Exception:
        logs.error("issues", "ISSUES_FIXED_CHECK_FAILED", "fixed-in check failed", exc=True)
    return reg


def detach(reg: Registry) -> None:
    sink = getattr(reg, "sink", None)
    if sink is not None:
        logs.remove_sink(sink)
        sink.flush()


# ---------------------------------------------------------------------- feature
def _targets() -> dict:
    from .capabilities import PI_ROADMAP
    return {"windows": {"status": "implemented_unverified",
                        "evidence": "UNIT test_issue_registry.py (fake Core, fake GitHub endpoint, planted faults, real dashboard HTTP)",
                        "blocker": "no real report filed yet with an owner token"},
            "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                               "blocker": "pure Python; never run on a Raspberry Pi (no device in the test setup)"}}


feat.register(FEATURE, "דיווח תקלות למפתח", "Issue reports", True, skills=["issues"],
              about="collects the problems he finds in himself into one list and, only after you approve the exact text, "
                    "reports them to the developer so the next version fixes them",
              targets=_targets(), permissions=["read"],
              settings="dashboard Bug reports page, jarvis issues, header badge")


# ---------------------------------------------------------------------- dashboard
_CLIENT_STATE: dict = {}


def _reg(ctx) -> Registry:
    return Registry(ctx.home, clock=getattr(ctx, "clock", time.time))


def _view(r: dict) -> dict:
    return {**r, "area_label": AREAS.get(r["area"], (r["area"], r["area"]))[0],
            "area_label_he": AREAS.get(r["area"], (r["area"], r["area"]))[1]}


def api_list(ctx, m, body, q=None):
    def one(k):
        v = (q or {}).get(k)
        return (v[0] if isinstance(v, list) else v) or None
    reg = _reg(ctx)
    return 200, {"issues": [_view(r) for r in reg.list(one("status"), one("severity"), one("area"))], "counts": reg.counts(),
                 "repo": REPO, "label": LABEL, "areas": sorted(AREAS)}


def api_count(ctx, m, body, q=None):
    return 200, _reg(ctx).counts()


def api_get(ctx, m, body, q=None):
    reg = _reg(ctx)
    r = reg.get(m.group(1))
    if r is None:
        return 404, {"error": "no such issue"}
    return 200, {"issue": _view(r), "bundle": bundle(r), "token": bool(find_token())}


def api_set(ctx, m, body):
    st = str((body or {}).get("status") or "")
    try:
        r = _reg(ctx).set_status(m.group(1), st)
    except ValueError as e:
        return 400, {"error": str(e)}
    return (200, {"issue": _view(r)}) if r else (404, {"error": "no such issue"})


def api_report(ctx, m, body):
    from .approvals import ask_owner

    async def ask(prompt, **kw):
        ok, _ = await asyncio.to_thread(ask_owner, ctx.home, prompt, title=kw.get("title", ""), why=kw.get("why", ""),
                                        kind=kw.get("kind", "message"), risk=kw.get("risk", "medium"),
                                        details=kw.get("details"), timeout=300)
        return ok
    try:
        return 200, asyncio.run(report(_reg(ctx), m.group(1), ask=ask, by="dashboard"))
    except KeyError:
        return 404, {"error": "no such issue"}


def api_client_error(ctx, m, body):
    return client_error(_reg(ctx), body or {}, state=_CLIENT_STATE)


def dashboard_routes(register) -> None:
    register("GET", r"/api/bugs", api_list)
    register("GET", r"/api/bugs/count", api_count)
    register("POST", r"/api/bugs/client-error", api_client_error)
    register("GET", r"/api/bugs/(JI-[0-9a-f]{8})", api_get)
    register("POST", r"/api/bugs/(JI-[0-9a-f]{8})/status", api_set)
    register("POST", r"/api/bugs/(JI-[0-9a-f]{8})/report", api_report)


# ---------------------------------------------------------------------- CLI: jarvis issues [list|show|report|ignore|fixed|ack|add]
def add_cli(sub) -> None:
    p = sub.add_parser("issues", aliases=["issue"], help="the problems JARVES found in himself: list | show ID | report ID "
                                                          "(asks first, then files on the developer's repo) | ignore ID | fixed ID | add CODE")
    p.add_argument("action", nargs="?", default="list", choices=["list", "show", "report", "ignore", "ack", "fixed", "add"])
    p.add_argument("target", nargs="?", default="", help="the issue id (JI-...) or, for add, a CODE like MY_PROBLEM")
    p.add_argument("--all", action="store_true", help="list: include fixed and ignored")
    p.add_argument("--severity", choices=list(SEVERITIES), default=None, help="add: P1 | P2 | P3 (default P3)")
    p.add_argument("--yes", action="store_true", help="report: approve without the typed confirmation")
    p.add_argument("--json", action="store_true")


def _line(r: dict) -> str:
    fx = f" in {r['fixed_in']}" if r["status"] == "fixed" and r["fixed_in"] else ""
    rep = " reported" if r.get("reported_url") else (" saved" if r.get("bundle_saved") else "")
    return f"{r['id']}  {r['severity']}  {r['status'] + fx:<16} x{r['count']:<4} {r['area']:<9} {r['title']}{rep}"


def cmd_issues(a, cfg, home: Path, *, out=print, ask=input) -> int:
    reg = Registry(home)
    act, tgt = a.action, (a.target or "").strip()
    if act == "list":
        rows = reg.list(None if a.all else "open")
        if a.json:
            out(json.dumps({"issues": rows, "counts": reg.counts()}, ensure_ascii=False))
        else:
            out(f"{reg.counts()['open']} open issue(s)" + ("" if rows else " - nothing to show"))
            for r in rows:
                out("  " + _line(r))
        return 0
    if act == "add":
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,40}", tgt):
            out("usage: jarvis issues add CODE [--severity P1|P2|P3]   (CODE: letters, digits, underscores; no free text)")
            return 2
        r = reg.record("manual", tgt.upper(), a.severity or "P3", {"entered": "cli"}, title=f"Manual report: {tgt.upper()}",
                       title_he=f"דיווח ידני: {tgt.upper()}", source="manual")
        out(json.dumps(r, ensure_ascii=False) if a.json else f"recorded {r['id']} ({r['code']}); jarvis issues report {r['id']} sends it after you approve")
        return 0
    r = reg.get(tgt) if _ID.match(tgt) else None
    if r is None:
        out("no such issue; list them with: jarvis issues list --all")
        return 1
    if act == "show":
        out(json.dumps({"issue": r, "bundle": bundle(r)}, ensure_ascii=False) if a.json else
            f"{_line(r)}\n\nThis is exactly what a report would contain:\n\n# {bundle(r)['title']}\n\n{bundle(r)['body']}")
        return 0
    if act in ("ignore", "ack", "fixed"):
        reg.set_status(tgt, {"ignore": "ignored", "ack": "acknowledged", "fixed": "fixed"}[act])
        out(f"{tgt}: {reg.get(tgt)['status']}")
        return 0

    async def confirm(prompt: str, **kw) -> bool:
        if a.yes:
            return True
        if not sys.stdin or not sys.stdin.isatty():
            out("approval needed: add --yes to approve sending exactly the text above")
            return False
        return str(ask(f"{prompt.splitlines()[0]} type yes: ")).strip().lower() in ("yes", "y", "כן")
    b = bundle(r)
    out(f"This exact text would be sent to {REPO} (label {LABEL}):\n\n# {b['title']}\n\n{b['body']}\n")
    res = asyncio.run(report(reg, tgt, ask=confirm, by="cli"))
    out(f"report: {res['status']} {res.get('issue_url') or res.get('path') or res.get('reason') or ''}")
    return 0 if res["status"] in ("filed", "commented", "saved") else 1


COMMANDS = {"issues": cmd_issues, "issue": cmd_issues}
