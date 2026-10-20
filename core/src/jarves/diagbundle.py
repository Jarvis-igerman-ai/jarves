"""`jarvis diag`: a sanitized, size-bounded support bundle (SW-V2 M5, spec section 12).

For SHARING, so stricter than the log file: personal content is masked even when the log was written with
privacy off, credentials are redacted (logs.redact + credential-named fields), e-mail addresses are masked, and a
final self-check scans every member for key / JWT / bearer patterns before the zip is renamed into place. Never
included: secrets.env, sign-in token folders, databases, transcripts, audio, workspace or memory text.

Members: info.json (version, platform, redacted config, which keys are PRESENT - never their values),
health.json, features.json, usage.json, backups.json, store_summary.json (counts + open issue titles), files.json
(data-dir inventory: names and sizes), recent.log (newest lines first to fit the budget, rotated logs included),
manifest.json (what was included, cut or left out). Total size <= max_bytes (default 5 MB) by trimming the log.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, logs

MAX_BYTES = 5 * 1024 * 1024
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+")   # lookbehind: linear on long words
_LEAK = re.compile(r"sk-[A-Za-z0-9_\-]{12,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.|(?i:bearer\s+(?!\*\*\*)[A-Za-z0-9._\-]{8,})")


def sanitize(text: str) -> str:
    return _EMAIL.sub("<email>", logs.redact(text))


_TOKEN_LIKE = re.compile(r"^[A-Za-z0-9_.:+\-]{0,64}$")      # codes, states, model ids, numbers, ISO times


def _strict(v, depth: int = 0):
    """Issue #15: free-form text (the "msg" sentence, provider error messages, nested payloads) is never shared.
    Only identifier-like strings survive; everything else becomes its length. Numbers and booleans stay."""
    if depth > 8:
        return "<nested>"
    if isinstance(v, dict):
        return {str(k): _strict(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_strict(x, depth + 1) for x in v]
    if isinstance(v, str):
        return v if _TOKEN_LIKE.match(v) else f"<{len(v)} chars>"
    return v


def private_line(line: str) -> str:
    """One log line for sharing: structured fields only (code, level, component, timings, ids); every sentence,
    whatever its field, is reduced to its length, even if the log was written with privacy off. A line that is not
    JSON is dropped to its length too (no guessing which words are private)."""
    try:
        d = json.loads(line)
    except ValueError:
        return json.dumps({"unparsed": f"<{len(line)} chars>"})
    return sanitize(json.dumps(_strict(logs.scrub(d, private=True)), ensure_ascii=False))


def _dump(obj) -> str:
    return sanitize(json.dumps(logs.scrub(obj, private=True), indent=1, ensure_ascii=False, default=str))


def _log_lines(home: Path, max_lines: int) -> list[str]:
    """Newest last; the current log first, then jarves.log.1, .2 ... until max_lines."""
    d = home / "logs"
    files = [d / "jarves.log"] + sorted(d.glob("jarves.log.*"), key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 99)
    out: list[str] = []
    for f in files:
        if len(out) >= max_lines:
            break
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        out = lines[-(max_lines - len(out)):] + out
    return out[-max_lines:]


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return {"error": type(e).__name__} if default is None else default


def _store_summary(home: Path) -> dict:
    p = home / "store.db"
    if not p.is_file():
        return {"present": False}
    from .store import Store
    st = Store(p)
    try:
        n = {t: st.rows(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in ("sessions", "turns", "tool_calls", "events")}
        issues = [{k: i[k] for k in ("kind", "severity", "title", "count", "status")} for i in st.issues("open")[:30]]
        codes = st.rows("SELECT code, COUNT(*) n FROM events WHERE level IN ('WARNING','ERROR') GROUP BY code "
                        "ORDER BY n DESC LIMIT 30")
        return {"present": True, "counts": n, "open_issues": issues, "warning_codes": codes}
    finally:
        st.close()


def _inventory(home: Path) -> list[dict]:
    out = []
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home).as_posix()
        if p.is_file() and not rel.startswith(("backups/", "logs/", "profiles/")) and rel.count("/") < 3:
            out.append({"name": rel, "size": p.stat().st_size})
        if len(out) >= 300:
            break
    return out


def build(home: Path, cfg, out: Path, *, lines: int = 500, max_bytes: int = MAX_BYTES, status: dict | None = None,
          commit: str = "unknown") -> dict:
    """Write the bundle; returns its manifest. Raises OSError only when the file cannot be written."""
    home, out = Path(home), Path(out)
    from .config import openai_key_present
    info = {"version": __version__, "commit": commit, "created": datetime.now(timezone.utc).isoformat(),
            "python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
            "keys_present": {"openai_api": openai_key_present(),           # (names avoid credential words:
                             "openai_admin": bool(os.environ.get("OPENAI_ADMIN_KEY", "").strip()),   # scrub masks)
                             "chatgpt_sign_in": (home / "chatgpt").is_dir(), "google_sign_in": (home / "google").is_dir()},
            "config": json.loads(cfg.model_dump_json())}

    def features_list():
        from .features import Features
        return [{k: s[k] for k in ("id", "enabled", "available", "active", "reason")} for s in Features(cfg, home).list()]

    def usage_view():
        from .usage import Monitor
        return [{k: v for k, v in m.to_dict().items() if k not in ("note",)} for m in Monitor(home, cfg).meters()]

    def backups_view():
        from .backup import list_backups, read_state, verify
        items = list_backups(home)
        newest = verify(home / "backups" / items[0]["name"]) if items else None
        return {"backups": items[:20], "state": read_state(home),
                "newest_verified": None if newest is None else {"name": newest["name"], "ok": newest["ok"],
                                                                "errors": newest["errors"][:5]}}
    members = {"info.json": _dump(info), "health.json": _dump(status or {}),
               "features.json": _dump(_safe(features_list)), "usage.json": _dump(_safe(usage_view)),
               "backups.json": _dump(_safe(backups_view)), "store_summary.json": _dump(_safe(lambda: _store_summary(home))),
               "files.json": _dump(_safe(lambda: _inventory(home)))}
    for k, v in list(members.items()):                  # each small member capped too
        if len(v.encode("utf-8")) > max_bytes // 10:
            members[k] = v.encode("utf-8")[:max_bytes // 10].decode("utf-8", "ignore") + "\n<cut: size limit>"
    budget = max_bytes - sum(len(v.encode("utf-8")) for v in members.values()) - 4096
    log = [private_line(line) for line in _log_lines(home, lines)]
    cut = 0
    while log and sum(len(x.encode("utf-8")) + 1 for x in log) > budget:
        drop = max(1, len(log) // 10)
        log, cut = log[drop:], cut + drop                 # the oldest lines go first
    members["recent.log"] = "\n".join(log)
    leaks = [k for k, v in members.items() if _LEAK.search(v)]
    for k in leaks:                                       # belt and braces: never ship a key-shaped string
        members[k] = _LEAK.sub("<redacted>", members[k])
    manifest = {"created": info["created"], "version": __version__, "members": sorted(members), "log_lines": len(log),
                "log_lines_cut_for_size": cut, "max_bytes": max_bytes, "leak_patterns_redacted_in": leaks,
                "never_included": ["secrets.env", "chatgpt/ and google/ sign-in tokens", "databases", "transcripts",
                                   "audio", "workspace and memory text", "backups"]}
    tmp = out.with_name(out.name + ".partial")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in members.items():
            z.writestr(k, v)
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
    os.replace(tmp, out)
    manifest["bytes"] = out.stat().st_size
    return manifest
