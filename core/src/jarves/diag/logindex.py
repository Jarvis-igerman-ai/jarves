"""Root-cause hints: log code -> source location and commit, from log_codes.json (tools/build_log_index.py writes
it at build time; git is never called at runtime). A missing index means "no hints", never an error.

`hint(code)` gives file:line, function, commit and a GitHub permalink on the public repo, plus the matching guide
(a guide whose error_codes list the code). `context_lines(home, code)` gives the 10 log lines around the last
occurrence with every content / personal / secret field removed: each line is the time, level, component, code,
the call's message TEMPLATE from the index (never the runtime message) and only numeric or boolean fields.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .. import logs

INDEX_FILE = Path(__file__).with_name("log_codes.json")
SAFE_FIELD_TYPES = (int, float, bool)
MAX_FIELDS = 6


class LogIndex:
    def __init__(self, data: dict | None = None, path: Path | None = None) -> None:
        if data is None:
            try:
                data = json.loads(Path(path or INDEX_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        self.data = data if isinstance(data, dict) else {}
        self.codes: dict[str, list[dict]] = self.data.get("codes") or {}
        self.repo = self.data.get("repo") or "igalgerman/jarves"
        self.prefix = self.data.get("src_prefix") or "core/src"

    @property
    def built(self) -> bool:
        return bool(self.codes)

    def sites(self, code: str) -> list[dict]:
        return list(self.codes.get(code) or [])

    def template(self, code: str) -> str:
        s = self.sites(code)
        return s[0].get("msg", "") if s else ""

    def permalink(self, site: dict) -> str:
        ref = site.get("commit") or self.data.get("head") or "main"
        return f"https://github.com/{self.repo}/blob/{ref}/{self.prefix}/{site['file']}#L{site['line']}"

    def hint(self, code: str) -> dict | None:
        s = self.sites(code)
        if not s:
            return None
        lead = sorted(s, key=lambda x: {"ERROR": 0, "WARNING": 1}.get(x.get("level"), 2))[0]
        out = {"code": code, "file": lead["file"], "line": lead["line"], "function": lead.get("function", ""),
               "commit": lead.get("commit", ""), "level": lead.get("level", ""), "url": self.permalink(lead),
               "sites": len(s)}
        g = guide_for(code)
        if g:
            out["guide"] = g
        return out


def guide_for(code: str) -> str | None:
    """The guide id whose error_codes mention this code (dashboard link #/guide/<id>)."""
    try:
        from .. import guides
        g = guides.guide_for_code(code)
        return g.id if g is not None else None
    except Exception:
        return None


def safe_record(r: dict, index: LogIndex | None = None) -> str:
    """One redacted context line: no message text, no content / personal / secret field, numbers only."""
    ts = r.get("_t") or r.get("ts")
    when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, (int, float)) else "?"
    code = str(r.get("code") or "-")
    tpl = (index.template(code) if index is not None else "") or "-"
    fields = []
    for k, v in r.items():
        if k in ("ts", "_t", "level", "component", "code", "msg", "cid", "session", "exc"):
            continue
        if logs.is_sensitive(k, True) or logs.is_credential(k):
            continue
        if isinstance(v, SAFE_FIELD_TYPES):
            fields.append(f"{k}={round(v, 3) if isinstance(v, float) else v}")
        if len(fields) >= MAX_FIELDS:
            break
    return f"{when} {r.get('level', '?')} {str(r.get('component') or '-')[:20]} {code[:64]}: {tpl[:100]}" + \
        (f" [{' '.join(fields)}]" if fields else "")


def context_lines(home: Path, code: str, *, since: float, until: float | None = None, n: int = 10,
                  index: LogIndex | None = None) -> list[str]:
    """The n log lines around the LAST occurrence of `code` in [since, until], each through safe_record."""
    from ..selfcheck import read_logs
    recs = read_logs(Path(home), since, until)
    last = max((i for i, r in enumerate(recs) if r.get("code") == code), default=None)
    if last is None:
        return []
    a = max(0, last - n // 2)
    return [safe_record(r, index) for r in recs[a:a + n]]
