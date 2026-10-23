"""Release notes: a read-only view of CHANGELOG.md per version.

One source (CHANGELOG.md, shipped next to the program by the installer) feeds three surfaces: the dashboard page
"Release notes" (pick a version), `jarvis release-notes [--version V] [--open]` and a standalone HTML file that the
installer finish page opens right after an install or update. Stdlib only, offline, no personal data; every string
that reaches HTML is escaped.
"""
from __future__ import annotations

import html
import re
import webbrowser
from pathlib import Path

from . import __version__, clilang

HEAD = re.compile(r"^##\s+(\d+(?:\.\d+)+[^\s]*)\s*(?:\((.*?)\))?\s*$|^##\s+(.+?)\s*$")
KIND = re.compile(r"^(Added|Changed|Fixed|Docs|Version|Security|Removed)(?:\s*\(([^)]*)\))?:\s*(.*)$", re.S)


def stable_of(v: str) -> str:
    m = re.match(r"\d+\.\d+\.\d+", v or "")
    return m.group(0) if m else str(v or "")


def find_changelog() -> str:
    """CHANGELOG.md of this copy: the installed program folder, a source checkout or the current folder; '' if none."""
    for d in (Path.cwd(), *Path(__file__).resolve().parents):
        f = d / "CHANGELOG.md"
        if f.is_file():
            return f.read_text(encoding="utf-8", errors="replace")
    return ""


def parse(text: str) -> list[dict]:
    """[{version, status, intro, items:[{kind, scope, text}]}] newest first (file order)."""
    out: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            m = HEAD.match(line)
            title = (m.group(1) or m.group(3) or "").strip() if m else line[3:].strip()
            status = (m.group(2) or "").strip() if m else ""
            cur = {"version": title, "status": status, "intro": "", "items": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("- "):
            body = s[2:].strip()
            k = KIND.match(body)
            cur["items"].append({"kind": k.group(1) if k else "Note", "scope": (k.group(2) or "") if k else "",
                                 "text": (k.group(3) if k else body).strip()})
        elif s and cur["items"]:
            cur["items"][-1]["text"] += " " + s
        elif s:
            cur["intro"] = (cur["intro"] + " " + s).strip()
    return out


def sections(text: str | None = None) -> list[dict]:
    return parse(find_changelog() if text is None else text)


def pick(secs: list[dict], version: str | None) -> dict | None:
    """The section for `version` (a beta like 4.1.0-beta.7 shows the notes of 4.1.0); newest when version is None."""
    if not secs:
        return None
    if not version:
        return secs[0]
    want = stable_of(version)
    for s in secs:
        if s["version"] == version or stable_of(s["version"]) == want:
            return s
    return None


def api_release_notes(ctx, m, body, q=None):
    secs = sections()
    cur = stable_of(__version__)
    want = ((q or {}).get("version") or [""])[0] or None
    if want and pick(secs, want) is None:
        return 404, {"error": f"no release notes for version {want[:40]}"}
    return 200, {"current": cur, "versions": [{"version": s["version"], "status": s["status"], "count": len(s["items"])}
                                              for s in secs],
                 "selected": pick(secs, want or cur) or (secs[0] if secs else None)}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/release-notes", api_release_notes)


# ---------------------------------------------------------------------- standalone HTML page
CSS = ("body{font:16px/1.55 system-ui,'Segoe UI',Arial,sans-serif;margin:0;background:#0f1419;color:#e6e9ee}"
       "main{max-width:820px;margin:0 auto;padding:28px 20px 56px}h1{margin:0 0 4px;font-size:1.7rem}"
       ".muted{color:#9aa4b2}h2{margin:28px 0 8px;font-size:1.15rem;border-bottom:1px solid #2a3340;padding-bottom:6px}"
       "ul{padding-inline-start:1.2rem}li{margin:8px 0}.tag{display:inline-block;min-width:4.6em;margin-inline-end:8px;"
       "padding:1px 8px;border-radius:999px;font-size:.78rem;font-weight:600;background:#223;color:#9cf}"
       ".Fixed{background:#1d3a2a;color:#8fe3a8}.Added{background:#1c2f4a;color:#9cc7ff}.Changed{background:#3d3220;color:#f1c67a}"
       "details{margin-top:16px}summary{cursor:pointer;color:#9cc7ff}"
       "@media (prefers-color-scheme:light){body{background:#fff;color:#1b2430}.muted{color:#56606e}"
       "h2{border-color:#d9dee6}.tag{background:#eef}.Fixed{background:#dff3e6;color:#17633a}"
       ".Added{background:#e1edff;color:#1f4f99}.Changed{background:#fbefd5;color:#7a5410}}")
KIND_HE = {"Added": "נוסף", "Changed": "שונה", "Fixed": "תוקן", "Docs": "תיעוד", "Version": "גרסה", "Security": "אבטחה",
           "Removed": "הוסר", "Note": "הערה"}


def _items(sec: dict, he: bool) -> str:
    if not sec["items"]:
        return "<p class=muted>—</p>"
    lis = []
    for it in sec["items"]:
        kind = KIND_HE.get(it["kind"], it["kind"]) if he else it["kind"]
        scope = f" ({html.escape(it['scope'])})" if it["scope"] else ""
        lis.append(f"<li><span class='tag {html.escape(it['kind'])}'>{html.escape(kind)}{scope}</span>"
                   f"{html.escape(it['text'])}</li>")
    return "<ul>" + "".join(lis) + "</ul>"


def render_html(secs: list[dict], version: str | None = None, lang: str = "en") -> str:
    he = lang == "he"
    sec = pick(secs, version) or (secs[0] if secs else None)
    title = "מה חדש ב-JARVES" if he else "What's new in JARVES"
    parts = [f"<!doctype html><html lang='{'he' if he else 'en'}' dir='{'rtl' if he else 'ltr'}'><head><meta charset=utf-8>"
             f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
             f"<style>{CSS}</style></head><body><main><h1>{html.escape(title)}</h1>"]
    if sec is None:
        parts.append("<p class=muted>" + ("אין הערות גרסה בהתקנה הזו." if he else "No release notes found in this install.") + "</p>")
    else:
        st = f" ({html.escape(sec['status'])})" if sec["status"] else ""
        parts.append(f"<p class=muted>{'גרסה' if he else 'Version'} {html.escape(sec['version'])}{st}</p>")
        if sec["intro"]:
            parts.append(f"<p>{html.escape(sec['intro'])}</p>")
        parts.append(_items(sec, he))
        older = [s for s in secs if s is not sec]
        if older:
            parts.append(f"<h2>{'גרסאות קודמות' if he else 'Earlier versions'}</h2>")
            for s in older:
                st = f" ({html.escape(s['status'])})" if s["status"] else ""
                parts.append(f"<details><summary>{html.escape(s['version'])}{st}</summary>{_items(s, he)}</details>")
    parts.append("</main></body></html>")
    return "".join(parts)


def write_html(home: Path, version: str | None, lang: str = "en") -> Path:
    secs = sections()
    sec = pick(secs, version) or (secs[0] if secs else None)
    name = re.sub(r"[^0-9A-Za-z._-]", "_", (sec["version"] if sec else "none"))[:40]
    d = Path(home) / "release-notes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"release-notes-{name}.html"
    p.write_text(render_html(secs, version, lang), encoding="utf-8", newline="\n")
    return p


# ---------------------------------------------------------------------- CLI
def cmd_notes(a, cfg, home) -> int:
    lang = "he" if getattr(a, "he", False) else "en"
    secs = sections()
    if getattr(a, "list", False):
        for s in secs:
            print(f"{s['version']}" + (f"  ({s['status']})" if s["status"] else "") + f"  {len(s['items'])}")
        return 0 if secs else 1
    version = getattr(a, "version", None) or stable_of(__version__)
    if secs and pick(secs, version) is None:
        print(clilang.fmt(f"אין הערות גרסה ל-{version}.", f"No release notes for {version}."))
        return 1
    p = write_html(home, version, lang)
    print(clilang.fmt(f"הערות הגרסה נשמרו: {p}", f"Release notes written: {p}"))
    if getattr(a, "open", False):
        webbrowser.open(p.as_uri())
    return 0


def add_cli(sub) -> None:
    rn = sub.add_parser("release-notes", help="what changed in each version; writes an HTML page (--open shows it)")
    rn.add_argument("--version", help="the version to show (default: this one; a beta shows its stable version)")
    rn.add_argument("--list", action="store_true", help="list the versions that have notes")
    rn.add_argument("--open", action="store_true", help="open the page in your browser")
    rn.add_argument("--he", action="store_true", help="Hebrew page")


COMMANDS = {"release-notes": cmd_notes}
