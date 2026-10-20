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
_EMAIL = logs._EMAIL
_LEAK = re.compile(r"sk-[A-Za-z0-9_\-]{12,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.|(?i:bearer\s+(?!\*\*\*)[A-Za-z0-9._\-]{8,})")


def sanitize(text: str) -> str:
    return logs.redact_pii(logs.redact(text))


_SALT = os.urandom(16)          # module default; build() uses a fresh salt per bundle


def private_line(line: str, salt: bytes | None = None) -> str | None:
    """One log line for sharing (SW-V4 B bundle rule), whatever log.privacy was when it was written: content
    fields stripped to their length, pii hashed (per-bundle salt), secrets dropped, stack-trace text dropped
    (frames kept), nested JSON walked with the same registry. The msg of a v2 line is a developer template and is
    kept; an older line's free-text msg becomes its length. A line that is not a JSON object returns None: the
    caller drops and counts it (never copied, not even in part)."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    return sanitize(json.dumps(logs.bundle_record(d, salt or _SALT), ensure_ascii=False, default=str))


def _member(v, salt: bytes, depth: int = 0):
    """Non-log members (config, health, features, usage, store export): registry classes apply (secret dropped,
    content stripped, pii hashed; unknown keys stay safe here); a safe string is kept when it is plain ASCII
    developer text without personal identifiers, else reduced to its length."""
    if depth > 10:
        return "<nested>"
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            c = logs.field_class(k, default=logs.SAFE)
            out[str(k)] = _member(x, salt, depth + 1) if c == logs.SAFE else logs._bundle_value(x, c, salt, depth)
        return out
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_member(x, salt, depth + 1) for x in v]
    if isinstance(v, str):
        s = sanitize(v)
        return s if s.isascii() and len(s) <= 200 else f"<{len(s)} chars>"
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return _member(str(v), salt, depth)


def _dump(obj, salt: bytes | None = None) -> str:
    return sanitize(json.dumps(_member(json.loads(json.dumps(obj, default=str)), salt or _SALT), indent=1,
                               ensure_ascii=False))

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
        issues = [{k: i[k] for k in ("kind", "severity", "title", "count", "status", "fingerprint")}
                  for i in st.issues("open")[:30]]
        codes = st.rows("SELECT code, COUNT(*) n FROM events WHERE level IN ('WARNING','ERROR') GROUP BY code "
                        "ORDER BY n DESC LIMIT 30")
        return {"present": True, "counts": n, "open_issues": issues, "warning_codes": codes}
    finally:
        st.close()


# File names JARVES itself creates are shown; any other name (a user's file, a contact or project name in a path)
# is a per-bundle pseudonym with its extension (SW-V4 B: a file name can be personal).
_KNOWN_FILE = re.compile(r"^(config\.toml|(store|memory|faces|userdata|budget|usage)\.db(-wal|-shm|-journal)?|"
                         r"[a-z_]+\.json|[a-z_]+\.jsonl|link_token|pear_client_id(\.paired)?|secrets\.env|"
                         r"(approvals|wake|google|chatgpt|camera|guides|runtime|skills|cache)/[A-Za-z0-9_.-]+|"
                         r"workspace/[A-Z_]+\.md)$")


def _inventory(home: Path, salt: bytes | None = None) -> list[dict]:
    out = []
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home).as_posix()
        if p.is_file() and not rel.startswith(("backups/", "logs/", "profiles/")) and rel.count("/") < 3:
            shown = rel if _KNOWN_FILE.match(rel) else (
                f"{rel.split('/')[0] + '/' if '/' in rel else ''}{logs.pii_hash(rel, salt or _SALT)}{p.suffix[:8]}")
            out.append({"file": shown, "size": p.stat().st_size})
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
    salt = os.urandom(16)                               # pii pseudonyms correlate inside THIS bundle only
    members = {"info.json": _dump(info, salt), "health.json": _dump(status or {}, salt),
               "features.json": _dump(_safe(features_list), salt), "usage.json": _dump(_safe(usage_view), salt),
               "backups.json": _dump(_safe(backups_view), salt),
               "store_summary.json": _dump(_safe(lambda: _store_summary(home)), salt),
               "files.json": _dump(_safe(lambda: _inventory(home, salt)), salt)}
    for k, v in list(members.items()):                  # each small member capped too
        if len(v.encode("utf-8")) > max_bytes // 10:
            members[k] = v.encode("utf-8")[:max_bytes // 10].decode("utf-8", "ignore") + "\n<cut: size limit>"
    budget = max_bytes - sum(len(v.encode("utf-8")) for v in members.values()) - 4096
    log, malformed = [], 0
    for line in _log_lines(home, lines):
        x = private_line(line, salt) if line.strip() else ""
        if x is None:
            malformed += 1                                # dropped and counted, never copied
        elif x:
            log.append(x)
    cut = 0
    while log and sum(len(x.encode("utf-8")) + 1 for x in log) > budget:
        drop = max(1, len(log) // 10)
        log, cut = log[drop:], cut + drop                 # the oldest lines go first
    members["recent.log"] = "\n".join(log)
    leaks = []
    for k, v in members.items():                          # belt and braces: never ship a key-shaped string
        w = _LEAK.sub("<redacted>", logs.redact(v))
        if w != v:
            members[k] = w
            leaks.append(k)
    manifest = {"created": info["created"], "version": __version__, "members": sorted(members), "log_lines": len(log),
                "log_lines_cut_for_size": cut, "log_lines_dropped_malformed": malformed, "max_bytes": max_bytes,
                "leak_patterns_redacted_in": leaks,
                "rule": "content stripped, pii hashed (per-bundle salt), secrets dropped, whatever log.privacy says",
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
