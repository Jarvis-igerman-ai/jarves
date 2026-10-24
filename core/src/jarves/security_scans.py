"""Recurring security and complaint-prevention scans (V4.5 workstream C; owner decision 24 Oct 2026).

Cadence: a daily LIGHT scan and a weekly FULL scan (a full scan day replaces that day's light scan, because it
covers it). The owner can pick other weekdays and times (`jarvis scans set`). `auto_fix` is OFF and cannot be turned
on here: a scan only READS (alert inventories, workflow run status, the local issues registry) and may at most
create or update ONE tracker issue on igalgerman/jarves. It never merges, dismisses an alert, deploys, changes a
secret or sends anything anywhere else. It makes no model (LLM) calls at all and no paid API calls.

Two places run it, with the SAME code (this module, called by `jarvis scans run` and by core/tools/security_scans.py):
- hosted: .github/workflows/security-scans.yml (GitHub cron, UTC, runs even when the owner's PC is off; the token
  is the workflow's own, so Dependabot / secret-scanning alerts it cannot read are reported as coverage
  "incomplete", never as zero). It keeps no state: the tracker issue carries the last signature.
- PC-local: `jarvis scans run --if-due` (for Windows Task Scheduler or any timer); it keeps
  security_scans.json (settings) and security_scans_state.json (history) in the JARVES home, uses the owner's own
  GitHub login, honours the timezone (DST aware), quiet hours and the run budget, and retries after a failure.
  The Core's scheduler is not used: its jobs call voice tools only, so a job here would need a new tool for no gain.

Output: findings are de-duplicated by fingerprint = source | rule | location | app version. ONE tracker issue
(marker below) is created only when there is a finding, and updated only when the set of fingerprints changed
(no spam; an unchanged scan writes nothing). A finding disappears from the issue ("resolved") only when the
source of that finding was read completely in this scan. A closed tracker is respected (never reopened);
two trackers or a lookup that is not certain make the scan fail closed. Public issue text holds only identifiers
(rule ids, advisory ids, package names, repo-relative paths, counts, severities): never a secret value, a
message, a private path or an address.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FEATURE = "security_scans"
CONFIG_FILE = "security_scans.json"
STATE_FILE = "security_scans_state.json"
REPO = "igalgerman/jarves"
LABEL = "from-diagnostics"
API_BASE = "https://api.github.com"                     # tests inject a fake client instead
TRACKER_MARKER = "<!-- jarves-security-scans:v1 -->"
TRACKER_TITLE = "[Security] JARVES recurring scan summary"
_SIG = re.compile(r"<!-- sig:([0-9a-f]{16}) -->")
MAX_HISTORY = 30
MAX_LISTING = 100                                       # a full page of issues = lookup not certain: fail closed
RECURRING_MIN = 3                                       # a local issue seen this often counts as recurring
RETRY_BACKOFF_S = (1800, 7200, 21600)                   # after the 1st, 2nd, 3rd+ failure in a row
CODEQL_STALE_DAYS = 10
KINDS = ("light", "full")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULTS: dict = {"enabled": False, "tz": "Asia/Jerusalem", "light_days": list(range(7)), "light_time": "07:30",
                  "full_days": [6], "full_time": "08:00", "quiet": "22:00-07:00", "max_runs_per_day": 2,
                  "report_github": False, "dry_run": False, "auto_fix": False}

_IDENT = re.compile(r"^[A-Za-z0-9_.:+\-]{1,80}$")
_RULE = re.compile(r"^[A-Za-z0-9_.:+/\-]{1,80}$")
_PATH = re.compile(r"^[A-Za-z0-9_./\-]{1,120}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_SECRETISH = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{12,}|[A-Za-z0-9_\-]{32,})")


class Offline(Exception):
    """No network or GitHub unreachable (retry later; never counted as a finding)."""


# ---------------------------------------------------------------------- settings and state
def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _days(v, default: list[int]) -> list[int]:
    if not isinstance(v, list):
        return list(default)
    out = sorted({d for d in v if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6})
    return out or list(default)


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def parse_quiet(q: str) -> tuple[str, str] | None:
    m = re.fullmatch(r"(\d\d:\d\d)-(\d\d:\d\d)", str(q or ""))
    return (m.group(1), m.group(2)) if m and _HHMM.match(m.group(1)) and _HHMM.match(m.group(2)) else None


def load_config(home: Path) -> dict:
    raw = _read(Path(home) / CONFIG_FILE)
    c = dict(DEFAULTS)
    c["enabled"] = raw.get("enabled") is True
    if isinstance(raw.get("tz"), str) and _zone(raw["tz"]):
        c["tz"] = raw["tz"]
    c["light_days"] = _days(raw.get("light_days"), DEFAULTS["light_days"])
    c["full_days"] = _days(raw.get("full_days"), DEFAULTS["full_days"])
    for k in ("light_time", "full_time"):
        if isinstance(raw.get(k), str) and _HHMM.match(raw[k]):
            c[k] = raw[k]
    if parse_quiet(raw.get("quiet")) or raw.get("quiet") == "":
        c["quiet"] = raw["quiet"]
    n = raw.get("max_runs_per_day")
    if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 24:
        c["max_runs_per_day"] = n
    c["report_github"] = raw.get("report_github") is True
    c["dry_run"] = raw.get("dry_run") is True
    c["auto_fix"] = False                                # fixed: a scan never repairs anything by itself
    return c


def save_config(home: Path, **changes) -> dict:
    """Validate and store. Raises ValueError with a readable reason; auto_fix can never be set."""
    c = load_config(home)
    for k, v in changes.items():
        if v is None:
            continue
        if k == "auto_fix":
            if v:
                raise ValueError("auto_fix stays off: a scan may only draft or update one issue")
            continue
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        if k == "tz" and not _zone(v):
            raise ValueError(f"unknown timezone {v!r} (use a name like Asia/Jerusalem)")
        if k in ("light_time", "full_time") and not _HHMM.match(str(v)):
            raise ValueError(f"{k} must be HH:MM")
        if k == "quiet" and v != "" and not parse_quiet(v):
            raise ValueError("quiet must be HH:MM-HH:MM or empty")
        if k in ("light_days", "full_days") and (not isinstance(v, list) or not v
                                                 or any(not isinstance(d, int) or not 0 <= d <= 6 for d in v)):
            raise ValueError(f"{k} must be a list of weekdays")
        if k == "max_runs_per_day" and (not isinstance(v, int) or not 1 <= v <= 24):
            raise ValueError("max_runs_per_day must be 1-24")
        c[k] = sorted(set(v)) if k.endswith("_days") else v
    _write(Path(home) / CONFIG_FILE, c)
    return c


def load_state(home: Path) -> dict:
    s = _read(Path(home) / STATE_FILE)
    return {"last_success": s.get("last_success") if isinstance(s.get("last_success"), dict) else {},
            "last_failure": s.get("last_failure") if isinstance(s.get("last_failure"), dict) else {},
            "fail_count": int(s.get("fail_count") or 0), "retry_at": float(s.get("retry_at") or 0),
            "signature": str(s.get("signature") or ""),
            "fingerprints": s.get("fingerprints") if isinstance(s.get("fingerprints"), dict) else {},
            "tracker_url": str(s.get("tracker_url") or ""),
            "history": [h for h in s.get("history", []) if isinstance(h, dict)][-MAX_HISTORY:]}


def save_state(home: Path, state: dict) -> None:
    state["history"] = state["history"][-MAX_HISTORY:]
    _write(Path(home) / STATE_FILE, state)


# ---------------------------------------------------------------------- schedule (timezone / DST aware)
def local_to_utc(tz, d: date, hhmm: str) -> datetime:
    """A wall-clock time in `tz` as UTC. A time that does not exist (spring forward gap) moves to the first valid
    minute after the gap; a time that happens twice (fall back) means its FIRST occurrence (fold=0)."""
    h, m = int(hhmm[:2]), int(hhmm[3:])
    naive = datetime(d.year, d.month, d.day, h, m)
    aware = naive.replace(tzinfo=tz, fold=0)
    utc = aware.astimezone(timezone.utc)
    back = utc.astimezone(tz).replace(tzinfo=None)
    if back != naive:                                    # in the gap: UTC offset before the gap applied -> shifted
        utc = back.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
    return utc


def slots(cfg: dict, start: datetime, end: datetime) -> list[tuple[datetime, str]]:
    """(utc time, kind) of every scheduled scan in [start, end], sorted. A full day has only the full slot."""
    tz = _zone(cfg["tz"]) or timezone.utc
    out: list[tuple[datetime, str]] = []
    d = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date() + timedelta(days=1)
    while d <= last:
        if d.weekday() in cfg["full_days"]:
            out.append((local_to_utc(tz, d, cfg["full_time"]), "full"))
        elif d.weekday() in cfg["light_days"]:
            out.append((local_to_utc(tz, d, cfg["light_time"]), "light"))
        d += timedelta(days=1)
    return sorted(s for s in out if start <= s[0] <= end)


def _covered(state: dict, slot: datetime, kind: str) -> bool:
    ts = slot.timestamp()
    ls = state["last_success"]
    got = float(ls.get("full") or 0) if kind == "full" else max(float(ls.get("light") or 0), float(ls.get("full") or 0))
    return got >= ts


def due_slot(cfg: dict, state: dict, now: datetime) -> tuple[datetime, str] | None:
    """The newest slot in the last 8 days that no successful scan covers (at most ONE catch-up run, never a burst)."""
    past = slots(cfg, now - timedelta(days=8), now)
    if past and not _covered(state, *past[-1]):
        return past[-1]
    return None                                          # the newest slot is covered: nothing older matters


def _in_quiet(cfg: dict, t: datetime) -> datetime | None:
    """If `t` falls in quiet hours return when they end (UTC), else None."""
    q = parse_quiet(cfg.get("quiet"))
    if not q:
        return None
    tz = _zone(cfg["tz"]) or timezone.utc
    loc = t.astimezone(tz)
    cur = loc.hour * 60 + loc.minute
    a, b = (int(q[0][:2]) * 60 + int(q[0][3:]), int(q[1][:2]) * 60 + int(q[1][3:]))
    if a == b:
        return None
    inside = (a <= cur < b) if a < b else (cur >= a or cur < b)
    if not inside:
        return None
    end_day = loc.date() + timedelta(days=1) if (a > b and cur >= a) else loc.date()
    return local_to_utc(tz, end_day, q[1])


def runs_today(cfg: dict, state: dict, now: datetime) -> int:
    tz = _zone(cfg["tz"]) or timezone.utc
    today = now.astimezone(tz).date()
    return sum(1 for h in state["history"] if h.get("trigger") == "auto"
               and datetime.fromtimestamp(float(h.get("t") or 0), timezone.utc).astimezone(tz).date() == today)


def plan(cfg: dict, state: dict, now: datetime) -> dict:
    """What the schedule says: {kind, when (UTC), due, why}. `when` is the actual next execution: after the slot,
    a retry back-off, quiet hours and the daily run budget are all applied."""
    tz = _zone(cfg["tz"]) or timezone.utc
    d = due_slot(cfg, state, now)
    why = ""
    if d:
        when, kind = max(now, d[0]), d[1]
        why = "catch_up" if (now - d[0]).total_seconds() > 3600 else "due"
    else:
        fut = slots(cfg, now + timedelta(seconds=1), now + timedelta(days=9))
        if not fut:
            return {"kind": None, "when": None, "due": False, "why": "no_slot"}
        when, kind = fut[0]
    if state["retry_at"] and when.timestamp() < state["retry_at"]:
        when, why = datetime.fromtimestamp(state["retry_at"], timezone.utc), "retry_backoff"
    for _ in range(4):                                   # budget and quiet hours may push each other
        if runs_today(cfg, state, when) >= cfg["max_runs_per_day"]:
            nxt = local_to_utc(tz, when.astimezone(tz).date() + timedelta(days=1), "00:00")
            when, why = max(when, nxt), "budget"
        q = _in_quiet(cfg, when)
        if q:
            when, why = q, "quiet_hours"
        else:
            break
    return {"kind": kind, "when": when, "due": bool(d) and when <= now, "why": why}


def status(home: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg, st = load_config(home), load_state(home)
    p = plan(cfg, st, now) if cfg["enabled"] else {"kind": None, "when": None, "due": False, "why": "off"}
    tz = _zone(cfg["tz"]) or timezone.utc
    return {"enabled": cfg["enabled"], "config": cfg, "next_kind": p["kind"], "why": p["why"], "due": p["due"],
            "next_run_utc": p["when"].isoformat() if p["when"] else "",
            "next_run_local": p["when"].astimezone(tz).strftime("%Y-%m-%d %H:%M %Z") if p["when"] else "",
            "last_success": st["last_success"], "last_failure": st["last_failure"], "fail_count": st["fail_count"],
            "tracker_url": st["tracker_url"], "history": st["history"][-10:]}


# ---------------------------------------------------------------------- GitHub (read-mostly)
def find_token(env_only: bool = False) -> str:
    for k in ("JARVES_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    if env_only:
        return ""
    try:
        import subprocess
        p = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
        return (p.stdout or "").strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def http_client(token: str, repo: str = REPO) -> Callable:
    """client(method, path, payload=None) -> (status, json-or-None). Raises Offline when GitHub is unreachable."""
    def call(method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{API_BASE}/repos/{repo}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "User-Agent": "jarves-security-scans", "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                code, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read() if hasattr(e, "read") else b""
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise Offline(type(e).__name__) from None
        try:
            return code, json.loads(body.decode("utf-8")) if body else None
        except ValueError:
            return code, None
    return call


def _get(client: Callable, path: str, sleep: Callable[[float], None], tries: int = 3):
    """GET with a bounded retry on 429 / 5xx; Offline propagates after the same bounded retries."""
    last = (0, None)
    for i in range(tries):
        try:
            last = client("GET", path)
        except Offline:
            if i == tries - 1:
                raise
            sleep(2 ** i)
            continue
        if last[0] in (429, 500, 502, 503, 504) and i < tries - 1:
            sleep(2 ** i)
            continue
        return last
    return last


# ---------------------------------------------------------------------- findings
SEVERITY_RANK = {"P1": 0, "P2": 1, "P3": 2}


def _sev(level: object, default: str = "P3") -> str:
    return {"critical": "P1", "high": "P1", "error": "P1", "medium": "P2", "moderate": "P2", "warning": "P2",
            "low": "P3", "note": "P3"}.get(str(level or "").lower(), default)


def finding(source: str, rule: object, location: object, severity: str, version: str, *, number: object = None) -> dict | None:
    """A public-safe finding, or None when a field could carry something private (then it is not published at all)."""
    rule, loc = str(rule or ""), str(location or "-")
    if not _RULE.match(rule) or not _PATH.match(loc) or _SECRETISH.fullmatch(rule) or _SECRETISH.fullmatch(loc):
        return None
    fp = hashlib.sha256(f"{source}|{rule}|{loc}|{version}".encode()).hexdigest()[:16]
    return {"fp": fp, "source": source, "rule": rule, "location": loc, "severity": severity,
            "number": number if isinstance(number, int) and not isinstance(number, bool) else None}


def _loc_of(alert: dict) -> str:
    inst = alert.get("most_recent_instance") or {}
    loc = inst.get("location") or {}
    return str(loc.get("path") or "-")                    # the file only: a line number would change on every refactor


def collect_code_scanning(client, version: str, sleep) -> dict:
    code, data = _get(client, "/code-scanning/alerts?state=open&per_page=100&ref=refs/heads/main", sleep)
    if code != 200 or not isinstance(data, list):
        return {"coverage": "incomplete", "reason": f"http_{code}", "findings": []}
    out = []
    for a in data:
        r = a.get("rule") or {}
        f = finding("codeql", r.get("id"), _loc_of(a),
                    _sev(r.get("security_severity_level") or r.get("severity")), version, number=a.get("number"))
        if f:
            out.append(f)
    return {"coverage": "complete" if len(data) < MAX_LISTING else "incomplete", "reason": "", "findings": out}


def collect_dependabot(client, version: str, sleep) -> dict:
    code, data = _get(client, "/dependabot/alerts?state=open&per_page=100", sleep)
    if code != 200 or not isinstance(data, list):
        return {"coverage": "incomplete", "reason": f"http_{code}", "findings": []}
    out = []
    for a in data:
        adv = a.get("security_advisory") or {}
        pkg = ((a.get("dependency") or {}).get("package") or {}).get("name")
        f = finding("dependabot", adv.get("ghsa_id"), pkg, _sev(adv.get("severity") or (a.get("security_vulnerability") or {}).get("severity")),
                    version, number=a.get("number"))
        if f:
            out.append(f)
    return {"coverage": "complete" if len(data) < MAX_LISTING else "incomplete", "reason": "", "findings": out}


def collect_secret_scanning(client, version: str, sleep) -> dict:
    """Signal only: the alert's secret TYPE and number. No location, no value, ever."""
    code, data = _get(client, "/secret-scanning/alerts?state=open&per_page=100", sleep)
    if code != 200 or not isinstance(data, list):
        return {"coverage": "incomplete", "reason": f"http_{code}", "findings": []}
    out = []
    for a in data:
        f = finding("secret_scanning", a.get("secret_type"), "-", "P1", version, number=a.get("number"))
        if f:
            out.append(f)
    return {"coverage": "complete" if len(data) < MAX_LISTING else "incomplete", "reason": "", "findings": out}


def _last_run(client, workflow: str, sleep) -> tuple[int, dict | None]:
    code, data = _get(client, f"/actions/workflows/{workflow}/runs?branch=main&status=completed&per_page=1", sleep)
    runs = (data or {}).get("workflow_runs") if isinstance(data, dict) else None
    return code, (runs[0] if runs else None)


def collect_main_red(client, version: str, sleep) -> dict:
    code, run = _last_run(client, "jarves-checks.yml", sleep)
    if code != 200:
        return {"coverage": "incomplete", "reason": f"http_{code}", "findings": []}
    red = run is not None and run.get("conclusion") == "failure"
    f = finding("ci", "main_red", "jarves-checks", "P1", version) if red else None
    return {"coverage": "complete", "reason": "", "findings": [f] if f else []}


def collect_nightly(client, version: str, sleep) -> dict:
    """New failing tests since the last green night = the last completed nightly run failed."""
    code, run = _last_run(client, "nightly.yml", sleep)
    if code != 200:
        return {"coverage": "incomplete", "reason": f"http_{code}", "findings": []}
    bad = run is not None and run.get("conclusion") == "failure"
    f = finding("ci", "nightly_failed", "jarves-nightly", "P2", version) if bad else None
    return {"coverage": "complete", "reason": "", "findings": [f] if f else []}


def collect_codeql_run(client, version: str, sleep, now: datetime) -> dict:
    code, run = _last_run(client, "codeql.yml", sleep)
    if code != 200:
        return {"coverage": "incomplete", "reason": f"http_{code}", "findings": []}
    fs = []
    if run is None:
        fs.append(finding("codeql_run", "codeql_never_ran", "codeql", "P2", version))
    elif run.get("conclusion") == "failure":
        fs.append(finding("codeql_run", "codeql_run_failed", "codeql", "P2", version))
    else:
        try:
            ts = datetime.fromisoformat(str(run.get("created_at")).replace("Z", "+00:00"))
            if now - ts > timedelta(days=CODEQL_STALE_DAYS):
                fs.append(finding("codeql_run", "codeql_stale", "codeql", "P3", version))
        except ValueError:
            pass
    return {"coverage": "complete", "reason": "", "findings": [f for f in fs if f]}


def collect_recurring_local(home: Path | None, state: dict, version: str) -> dict:
    """Recurring diagnostics since the last success, from the local issues registry (offline). Nothing without a home."""
    if home is None:
        return {"coverage": "skipped", "reason": "no_local_registry", "findings": []}
    try:
        from . import issue_registry as ir
        since = max(float(state["last_success"].get("light") or 0), float(state["last_success"].get("full") or 0)) or None
        rows = ir.Registry(Path(home)).list("open", since=since)
    except Exception:
        return {"coverage": "incomplete", "reason": "registry_unreadable", "findings": []}
    out = []
    for r in rows:
        if r.get("count", 0) >= RECURRING_MIN and r.get("severity") in ("P1", "P2"):
            f = finding("diagnostics", r.get("code"), r.get("area"), r["severity"], version)
            if f:
                out.append(f)
    return {"coverage": "complete", "reason": "", "findings": out}


def collect_boundary(runner: Callable[[], dict] | None, version: str) -> dict:
    """Boundary regressions summary (approvals / browser / mobile tests), from an injected runner. No runner = not run."""
    if runner is None:
        return {"coverage": "skipped", "reason": "not_run", "findings": []}
    try:
        r = runner()
    except Exception:
        return {"coverage": "incomplete", "reason": "runner_failed", "findings": []}
    if not isinstance(r, dict) or r.get("not_run"):
        return {"coverage": "incomplete", "reason": "not_run", "findings": []}
    fs = [finding("boundary", "boundary_test_failed", name, "P2", version)
          for name in (r.get("failed_files") or [])[:20] if isinstance(name, str)]
    if not fs and r.get("failed"):
        fs = [finding("boundary", "boundary_test_failed", "boundary-tests", "P2", version)]
    return {"coverage": "complete", "reason": "", "findings": [f for f in fs if f]}


def run_collectors(kind: str, client, version: str, *, home=None, state=None, now: datetime, sleep=time.sleep,
                   boundary: Callable[[], dict] | None = None) -> dict:
    steps = [("code_scanning", lambda: collect_code_scanning(client, version, sleep)),
             ("dependabot", lambda: collect_dependabot(client, version, sleep)),
             ("secret_scanning", lambda: collect_secret_scanning(client, version, sleep)),
             ("main_ci", lambda: collect_main_red(client, version, sleep)),
             ("nightly", lambda: collect_nightly(client, version, sleep)),
             ("local_diagnostics", lambda: collect_recurring_local(home, state or {"last_success": {}}, version))]
    if kind == "full":
        steps += [("codeql_run", lambda: collect_codeql_run(client, version, sleep, now)),
                  ("boundary", lambda: collect_boundary(boundary, version))]
    coverage, found = {}, {}
    for name, fn in steps:
        r = fn()                                         # Offline propagates: the whole scan is retried later
        coverage[name] = r["coverage"] if not r["reason"] else f"{r['coverage']}:{r['reason']}"
        for f in r["findings"]:
            found.setdefault(f["fp"], f)                 # de-duplicated by fingerprint
    return {"findings": sorted(found.values(), key=lambda f: (SEVERITY_RANK[f["severity"]], f["source"], f["rule"], f["location"])),
            "coverage": coverage}


def signature(findings: list[dict], complete: bool) -> str:
    return hashlib.sha256((("c" if complete else "i") + "|" + ",".join(sorted(f["fp"] for f in findings))).encode()).hexdigest()[:16]


def _complete(coverage: dict) -> bool:
    return all(v.split(":")[0] in ("complete", "skipped") for v in coverage.values())


# ---------------------------------------------------------------------- the ONE issue
def tracker_body(findings: list[dict], coverage: dict, resolved: int, sig: str, kind: str) -> str:
    lines = ["## JARVES recurring security scan", "",
             "Written by the daily light / weekly full scan. Read-only inventory: nothing is merged, dismissed, deployed or sent.",
             "This is ONE tracker; it is updated only when the set of findings changes.", ""]
    if findings:
        by = {s: sum(f["severity"] == s for f in findings) for s in ("P1", "P2", "P3")}
        lines += [f"**Open findings: {len(findings)}** (P1 {by['P1']}, P2 {by['P2']}, P3 {by['P3']})", "",
                  "| Severity | Source | Rule | Location | Alert |", "| --- | --- | --- | --- | --- |"]
        for f in findings[:60]:
            n = f"#{f['number']}" if f["number"] else "-"
            lines.append(f"| {f['severity']} | {f['source']} | `{f['rule']}` | `{f['location']}` | {n} |")
        if len(findings) > 60:
            lines.append(f"... and {len(findings) - 60} more (see the Security tab).")
    else:
        lines.append("**No open findings in the sources that were read completely.**")
    lines += ["", f"Resolved since the previous scan (source read completely): {resolved}", ""]
    gaps = {k: v for k, v in coverage.items() if v.split(":")[0] not in ("complete",)}
    lines.append(f"Coverage: {'complete' if _complete(coverage) else 'INCOMPLETE'} (scan: {kind}). "
                 + ("Sources not fully read: " + ", ".join(f"{k} ({v})" for k, v in sorted(gaps.items())) if gaps else ""))
    if not _complete(coverage):
        lines.append("Incomplete coverage is never read as zero findings.")
    lines += ["", "Only identifiers, counts and severities are published: no secret values, messages or private paths.",
              TRACKER_MARKER, f"<!-- sig:{sig} -->"]
    return "\n".join(lines)


def update_tracker(client, body: str, sig: str, have_findings: bool, sleep) -> dict:
    code, listing = _get(client, f"/issues?labels={LABEL}&state=all&per_page=100", sleep)
    if code != 200 or not isinstance(listing, list) or len(listing) >= MAX_LISTING:
        return {"status": "lookup_unavailable"}
    trackers = [x for x in listing if TRACKER_MARKER in str(x.get("body") or "") and not x.get("pull_request")]
    if len(trackers) > 1:
        return {"status": "duplicate_trackers"}
    if trackers:
        t = trackers[0]
        m = _SIG.search(str(t.get("body") or ""))
        if m and m.group(1) == sig:
            return {"status": "unchanged", "url": str(t.get("html_url") or "")}
        if t.get("state") == "closed":
            return {"status": "tracker_closed"}          # the owner's decision: never reopened here
        if not isinstance(t.get("number"), int):
            return {"status": "invalid_tracker"}
        rc, out = client("PATCH", f"/issues/{t['number']}", {"body": body})
        op, url = "updated", str(t.get("html_url") or "")
    else:
        if not have_findings:
            return {"status": "no_finding"}              # a clean scan creates nothing
        rc, out = client("POST", "/issues", {"title": TRACKER_TITLE, "body": body, "labels": [LABEL]})
        op, url = "filed", ""
    if rc not in (200, 201) or not isinstance(out, dict):
        return {"status": "send_failed", "http_status": rc}
    url = str(out.get("html_url") or url)
    if not url.startswith(f"https://github.com/{REPO}/issues/"):
        return {"status": "invalid_response"}
    return {"status": op, "url": url}


# ---------------------------------------------------------------------- one scan
def run_scan(home: Path | None, kind: str, *, trigger: str = "manual", client: Callable | None = None, now: datetime | None = None,
             report: bool | None = None, dry_run: bool | None = None, boundary: Callable[[], dict] | None = None,
             version: str = "", sleep: Callable[[float], None] = time.sleep) -> dict:
    """Run one scan. `home` None = stateless (hosted run: no history file, the tracker issue holds the signature).
    Never raises for a network problem: the result says offline / failed and the schedule retries with a back-off."""
    if kind not in KINDS:
        raise ValueError("kind must be light or full")
    now = now or datetime.now(timezone.utc)
    cfg = load_config(home) if home else dict(DEFAULTS)
    state = load_state(home) if home else {"last_success": {}, "last_failure": {}, "fail_count": 0, "retry_at": 0.0,
                                         "signature": "", "fingerprints": {}, "tracker_url": "", "history": []}
    if not version:
        try:
            from . import __version__ as version
        except Exception:
            version = "unknown"
    report = cfg["report_github"] if report is None else report
    dry = cfg["dry_run"] if dry_run is None else dry_run
    entry = {"t": now.timestamp(), "kind": kind, "trigger": trigger, "status": "", "findings": 0, "p1": 0, "p2": 0, "p3": 0,
             "coverage": "", "issue": "", "llm_calls": 0}
    result = {"kind": kind, "status": "", "llm_calls": 0, "findings": [], "coverage": {}, "issue": {}}
    try:
        if client is None:
            tok = find_token(env_only=home is None)
            if not tok:
                raise Offline("no_token")
            client = http_client(tok)
        got = run_collectors(kind, client, version, home=home, state=state, now=now, sleep=sleep, boundary=boundary)
    except Offline as e:
        return _fail(home, state, entry, result, now, "offline", str(e))
    except Exception as e:                               # a scan must never crash its caller; only the type is kept
        return _fail(home, state, entry, result, now, "failed", type(e).__name__)
    findings, coverage = got["findings"], got["coverage"]
    complete = _complete(coverage)
    sig = signature(findings, complete)
    now_fps = {f["fp"] for f in findings}
    resolved = sum(1 for fp, src in state["fingerprints"].items()
                   if fp not in now_fps and _source_complete(src, coverage))
    changed = sig != state["signature"]
    entry.update(findings=len(findings), p1=sum(f["severity"] == "P1" for f in findings),
                 p2=sum(f["severity"] == "P2" for f in findings), p3=sum(f["severity"] == "P3" for f in findings),
                 coverage="complete" if complete else "incomplete")
    result.update(findings=findings, coverage=coverage, resolved=resolved, changed=changed, signature=sig)
    issue: dict = {"status": "not_requested"}
    if dry:
        issue = {"status": "dry_run"}
    elif report:
        try:
            issue = update_tracker(client, tracker_body(findings, coverage, resolved, sig, kind), sig, bool(findings), sleep)
        except Offline as e:
            return _fail(home, state, entry, result, now, "offline", str(e))
    result["issue"] = issue
    entry["issue"] = issue["status"]
    if issue["status"] in ("lookup_unavailable", "send_failed", "invalid_response", "invalid_tracker"):
        return _fail(home, state, entry, result, now, "report_failed", issue["status"])
    entry["status"], result["status"] = "ok", "ok"
    state["last_success"][kind] = now.timestamp()
    state.update(fail_count=0, retry_at=0.0, signature=sig, fingerprints={f["fp"]: f["source"] for f in findings})
    if issue.get("url"):
        state["tracker_url"] = issue["url"]
    state["history"].append(entry)
    if home:
        save_state(home, state)
    return result


def _source_complete(source: str, coverage: dict) -> bool:
    key = {"codeql": "code_scanning", "dependabot": "dependabot", "secret_scanning": "secret_scanning",
           "ci": "main_ci", "diagnostics": "local_diagnostics", "codeql_run": "codeql_run", "boundary": "boundary"}.get(source, source)
    if source == "ci":
        return coverage.get("main_ci", "").split(":")[0] == "complete" and coverage.get("nightly", "complete").split(":")[0] == "complete"
    return coverage.get(key, "").split(":")[0] == "complete"


def _fail(home, state, entry, result, now: datetime, status: str, reason: str) -> dict:
    n = state["fail_count"] + 1
    wait = RETRY_BACKOFF_S[min(n, len(RETRY_BACKOFF_S)) - 1]
    entry.update(status=status, issue=entry["issue"] or "-", reason=reason[:40] if _IDENT.match(reason[:40]) else "-")
    state["fail_count"], state["retry_at"] = n, now.timestamp() + wait
    state["last_failure"] = {"t": now.timestamp(), "kind": entry["kind"], "status": status, "reason": entry["reason"]}
    state["history"].append(entry)
    if home:
        save_state(home, state)
    result.update(status=status, reason=entry["reason"], retry_in_s=wait)
    return result


def run_if_due(home: Path, *, now: datetime | None = None, client=None, boundary=None, version: str = "",
               sleep: Callable[[float], None] = time.sleep) -> dict:
    """For a timer: run at most one due scan, only when enabled, not in quiet hours, inside the daily budget and
    after a retry back-off. Cheap and offline when nothing is due."""
    now = now or datetime.now(timezone.utc)
    cfg, st = load_config(home), load_state(home)
    if not cfg["enabled"]:
        return {"status": "off"}
    p = plan(cfg, st, now)
    if not p["due"]:
        return {"status": "waiting", "why": p["why"], "next_run_utc": p["when"].isoformat() if p["when"] else ""}
    return run_scan(home, p["kind"], trigger="auto", client=client, now=now, boundary=boundary, version=version, sleep=sleep)


# ---------------------------------------------------------------------- CLI
def _parse_days(s: str) -> list[int]:
    if s.strip().lower() == "daily":
        return list(range(7))
    out = []
    for part in re.split(r"[,\s]+", s.strip().lower()):
        if part[:3] not in DAYS:
            raise ValueError(f"unknown weekday {part!r} (use mon,tue,... or daily)")
        out.append(DAYS.index(part[:3]))
    return sorted(set(out))


def add_cli(sub) -> None:
    p = sub.add_parser("scans", help="recurring security scans: status | run | on | off | set | history "
                                     "(daily light + weekly full; read-only; at most ONE tracker issue)")
    p.add_argument("action", nargs="?", default="status", choices=["status", "run", "on", "off", "set", "history"])
    p.add_argument("--kind", choices=KINDS, help="run: light (default) or full")
    p.add_argument("--if-due", action="store_true", help="run: only when the schedule says a scan is due (for a timer)")
    p.add_argument("--report", choices=["on", "off"], help="run/set: create or update the ONE tracker issue (set: store it)")
    p.add_argument("--dry-run", choices=["on", "off"], help="run/set: scan but never write to GitHub")
    p.add_argument("--tz", help="set: timezone name, e.g. Asia/Jerusalem")
    p.add_argument("--light-days", help="set: daily or mon,wed,fri")
    p.add_argument("--light-time", help="set: HH:MM local")
    p.add_argument("--full-days", help="set: sun or sat,wed")
    p.add_argument("--full-time", help="set: HH:MM local")
    p.add_argument("--quiet", help="set: HH:MM-HH:MM (no automatic run), or none")
    p.add_argument("--budget", type=int, help="set: most automatic runs per day (default 2)")
    p.add_argument("--auto-fix", choices=["on", "off"], help="set: stays off; on is refused")
    p.add_argument("--json", action="store_true")


def _line(h: dict, tz) -> str:
    when = datetime.fromtimestamp(float(h.get("t") or 0), timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M")
    return (f"{when}  {h.get('kind', '?'):5} {h.get('trigger', '?'):6} {h.get('status', '?'):10} "
            f"findings {h.get('findings', 0)} (P1 {h.get('p1', 0)}) coverage {h.get('coverage') or '-'} issue {h.get('issue') or '-'}")


def cmd_scans(a, cfg, home) -> int:
    from . import clilang
    say = clilang.say
    home = Path(home)
    try:
        if a.action in ("on", "off"):
            c = save_config(home, enabled=(a.action == "on"))
            say("סריקות אבטחה " + ("פועלות" if c["enabled"] else "כבויות"),
                "Security scans are " + ("ON" if c["enabled"] else "OFF"))
            return 0
        if a.action == "set":
            changes = {"tz": a.tz, "light_time": a.light_time, "full_time": a.full_time, "max_runs_per_day": a.budget}
            if a.light_days:
                changes["light_days"] = _parse_days(a.light_days)
            if a.full_days:
                changes["full_days"] = _parse_days(a.full_days)
            if a.quiet is not None:
                changes["quiet"] = "" if a.quiet.lower() == "none" else a.quiet
            if a.report:
                changes["report_github"] = a.report == "on"
            if a.dry_run:
                changes["dry_run"] = a.dry_run == "on"
            if a.auto_fix:
                changes["auto_fix"] = a.auto_fix == "on"
            save_config(home, **changes)
            a.action = "status"
    except ValueError as e:
        print(f"SCANS ERROR: {e}", file=sys.stderr)
        return 2
    if a.action == "run":
        client_, now = None, datetime.now(timezone.utc)
        if a.if_due:
            r = run_if_due(home, now=now)
        else:
            r = run_scan(home, a.kind or "light", trigger="manual", now=now,
                         report={"on": True, "off": False}.get(a.report), dry_run={"on": True, "off": False}.get(a.dry_run))
        if a.json:
            print(json.dumps({k: v for k, v in r.items() if k != "findings"} | {"findings": len(r.get("findings", []))}, indent=2, default=str))
        else:
            say(f"סריקה: {r['status']}", f"Scan: {r['status']}" + (f" ({r.get('why') or r.get('reason')})" if r.get("why") or r.get("reason") else ""))
            if r.get("findings") is not None and r["status"] == "ok":
                say(f"ממצאים: {len(r['findings'])}", f"Findings: {len(r['findings'])}; coverage "
                    + ("complete" if _complete(r["coverage"]) else "INCOMPLETE") + f"; issue: {r['issue'].get('status')}")
        return 0 if r["status"] in ("ok", "off", "waiting") else 1
    s = status(home)
    tz = _zone(s["config"]["tz"]) or timezone.utc
    if a.action == "history":
        rows = load_state(home)["history"]
        print(json.dumps(rows, indent=2) if a.json else "\n".join(_line(h, tz) for h in rows) or "no scans yet")
        return 0
    if a.json:
        print(json.dumps(s, indent=2, default=str))
        return 0
    c = s["config"]
    say("סריקות אבטחה: " + ("פועלות" if s["enabled"] else "כבויות"), "Security scans: " + ("ON" if s["enabled"] else "OFF"))
    print(f"  light: {','.join(DAYS[d] for d in c['light_days'])} at {c['light_time']}   full: "
          f"{','.join(DAYS[d] for d in c['full_days'])} at {c['full_time']}   tz {c['tz']}   quiet {c['quiet'] or 'none'}   "
          f"budget {c['max_runs_per_day']}/day   report {'on' if c['report_github'] else 'off'}   auto_fix off")
    for k in KINDS:
        t = s["last_success"].get(k)
        print(f"  last {k} success: " + (datetime.fromtimestamp(float(t), timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M %Z") if t else "never"))
    lf = s["last_failure"]
    if lf:
        print(f"  last failure: {lf.get('status')} ({lf.get('reason')}), {s['fail_count']} in a row")
    print("  next: " + (f"{s['next_kind']} at {s['next_run_local']} ({s['why'] or 'scheduled'})" if s["next_run_local"] else "-"))
    if s["tracker_url"]:
        print(f"  tracker issue: {s['tracker_url']}")
    for h in s["history"][-5:]:
        print("  " + _line(h, tz))
    return 0


COMMANDS = {"scans": cmd_scans}


# ---------------------------------------------------------------------- feature (registered at import, like selfspeed)
def _register() -> None:
    from . import features as feat
    feat.register(FEATURE, "סריקות אבטחה חוזרות", "Recurring security scans", False, skills=[],
                  about="a daily light and a weekly full read-only scan of security alerts, main health and recurring "
                        "diagnostics; at most ONE tracker issue; auto-fix is off; off until you turn it on",
                  targets={"windows": {"status": "implemented_unverified",
                                       "evidence": "UNIT core/tests/test_security_scans.py (stub GitHub client, fake clock, DST, "
                                                   "offline, retry); never run against the real repository alerts yet"},
                           "linux_arm64_pi": {"status": "implemented_unverified",
                                              "roadmap_issue": "https://github.com/igalgerman/jarves/issues/3",
                                              "blocker": "pure Python with an optional timer; not run on a Pi"}},
                  permissions=["network"],
                  settings="jarvis scans status|run|on|off|set; hosted: .github/workflows/security-scans.yml")


_register()
