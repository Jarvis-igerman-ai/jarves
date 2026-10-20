"""Read-only log viewer for the dashboard: jarves.log and its rotations (JSON lines), filtered and paged.

Filters: minimum level, module (component), event code (exact or prefix glob like LIVE_* / TASK_*), time range,
free text, Core session id and conversation (store.db session -> its Core session and time window), and a switch
that hides noisy codes. Lines were already masked when they were written (credentials always, content in privacy
mode); when log.privacy is on NOW, content fields are masked again at read time, so an older full-content line is
not shown either. Parsed files are cached by (mtime, size): rotated files are parsed once.
"""
from __future__ import annotations

import fnmatch
import json
import re
import threading
from pathlib import Path

from . import logs
from .store import parse_ts

LEVEL_RANK = {"VERBOSE": 0, "DEBUG": 1, "INFO": 2, "WARNING": 3, "ERROR": 4, "CRITICAL": 5}
# periodic / per-message codes that bury the interesting lines (hidden with "hide noisy")
NOISY = {"WAKE_HEALTH", "WAKE_NEAR_MISS", "MSG_IN", "MSG_OUT", "STARTUP_STAGE", "SPEECH_CACHE_HIT", "LIVE_MIC",
         "LIVE_AUDIO_IN", "LIVE_AUDIO_OUT", "HEALTH", "HEALTH_SNAPSHOT", "LINK_PING", "LIVE_CLOCK_NOTE",
         "LIVE_BACKEND_WEB_SEARCH", "LIVE_BACKEND_WRITING", "TASK_BACKEND_WRITING"}
MAX_PAGE = 2000
MAX_EXPORT = 50_000
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def log_files(home: Path) -> list[Path]:
    """jarves.log and its rotations, oldest first."""
    base = Path(home) / "logs" / "jarves.log"
    rotated = sorted(base.parent.glob("jarves.log.*"), key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0,
                     reverse=True)
    return [p for p in rotated + [base] if p.is_file()]


class LogReader:
    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self._cache: dict[str, tuple[tuple[int, int], list[dict]]] = {}
        self._lock = threading.Lock()

    def files(self) -> list[dict]:
        return [{"name": p.name, "size": p.stat().st_size} for p in log_files(self.home)]

    def _parse(self, p: Path) -> list[dict]:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = self._cache.get(p.name)
            if hit and hit[0] == key:
                return hit[1]
        out = []
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                d["_ts"] = parse_ts(d.get("ts"))
                d["_file"], d["_line"] = p.name, i + 1
                out.append(d)
        with self._lock:
            self._cache[p.name] = (key, out)
        return out

    def records(self, file: str | None = None) -> list[dict]:
        """All records oldest first (one file, or every file in order)."""
        out: list[dict] = []
        for p in log_files(self.home):
            if file is None or p.name == file:
                out += self._parse(p)
        return out


def _codes_match(code: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(code, p) if any(c in p for c in "*?[") else code == p for p in patterns)


def _split(v) -> list[str]:
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def query(reader: LogReader, f: dict, *, privacy: bool = False, conv_window=None) -> dict:
    """Filtered records, NEWEST first, paged: f keys (all optional): level, module, code, exclude, since, until,
    q, session, file, noisy ("1" = show noisy codes), limit, before (an opaque cursor from a previous page)."""
    min_rank = LEVEL_RANK.get(str(f.get("level") or "").upper(), 0)
    modules = [m.lower() for m in _split(f.get("module"))]
    codes, exclude = [c.upper() for c in _split(f.get("code"))], [c.upper() for c in _split(f.get("exclude"))]
    since = parse_ts(f["since"]) if f.get("since") else None
    until = parse_ts(f["until"]) if f.get("until") else None
    q = str(f.get("q") or "").strip().lower()
    session = str(f.get("session") or "").strip()
    show_noisy = str(f.get("noisy") or "") in ("1", "true", "on")
    limit = max(1, min(int(f.get("limit") or 300), MAX_EXPORT if f.get("export") else MAX_PAGE))
    before = str(f.get("before") or "")
    if conv_window is not None:                     # one conversation: its Core session and time window
        session = conv_window[0] or session
        since = max(since or 0, conv_window[1] - 1)
        until = min(until or 1e18, conv_window[2] + 1)
    recs = reader.records(f.get("file") or None)
    out, total, cursor = [], 0, None
    skipping = bool(before)
    for d in reversed(recs):
        ref = f"{d['_file']}:{d['_line']}"
        if skipping:
            if ref == before:
                skipping = False
            continue
        code = str(d.get("code") or "-").upper()
        if LEVEL_RANK.get(str(d.get("level") or "INFO").upper(), 2) < min_rank:
            continue
        if modules and str(d.get("component") or "").lower() not in modules:
            continue
        if codes and not _codes_match(code, codes):
            continue
        if exclude and _codes_match(code, exclude):
            continue
        if not show_noisy and not codes and code in NOISY:
            continue
        if since is not None and d["_ts"] < since or until is not None and d["_ts"] > until:
            continue
        if session and str(d.get("session") or "") != session:
            continue
        if q and q not in json.dumps({k: v for k, v in d.items() if not k.startswith("_")}, ensure_ascii=False).lower():
            continue
        total += 1
        if len(out) < limit:
            out.append(d)
            cursor = ref
    return {"lines": [present(d, privacy) for d in out], "matched": total, "more": total > len(out),
            "cursor": cursor if total > len(out) else None}


def present(d: dict, privacy: bool) -> dict:
    """A record as served: masked again when privacy is on now; secrets and e-mail addresses never leave."""
    body = {k: v for k, v in d.items() if not k.startswith("_")}
    # privacy on now: the field registry re-applied (an unregistered field and an old free-text msg are content)
    body = logs.private_record(body) if privacy else logs.scrub(body, private=False)
    body = json.loads(_EMAIL.sub("<email>", json.dumps(body, ensure_ascii=False, default=str)))
    return {**body, "ts": d["_ts"], "ts_utc": body.get("ts"), "ref": f"{d['_file']}:{d['_line']}"}


def facets(reader: LogReader) -> dict:
    """Modules, code prefixes and levels present (for the filter dropdowns)."""
    mods, codes = {}, {}
    for d in reader.records():
        m = str(d.get("component") or "-")
        mods[m] = mods.get(m, 0) + 1
        c = str(d.get("code") or "-")
        codes[c] = codes.get(c, 0) + 1
    prefixes: dict[str, int] = {}
    for c, n in codes.items():
        p = c.split("_", 1)[0] + "_*" if "_" in c else c
        prefixes[p] = prefixes.get(p, 0) + n
    return {"modules": dict(sorted(mods.items())), "codes": dict(sorted(codes.items())),
            "prefixes": dict(sorted(prefixes.items())), "levels": list(LEVEL_RANK)[:5], "noisy": sorted(NOISY)}
