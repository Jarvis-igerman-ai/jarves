"""SW-V3 P0.2 dashboard redesign: English-first multi-language shell (P0.2c), grouped navigation that keeps every page, the locale /
search / status scripts are served and safe, and /api/runtime names the host device for the header chip."""
from __future__ import annotations

import re
from pathlib import Path

from test_dashboard import dash, get, req  # noqa: F401  (fixture + helpers)

STATIC = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static"


def registered_pages() -> set[str]:
    """Every page a script registers (PAGES.x = / PAGES["x"] = / Object.assign(PAGES, {x: ...}))."""
    pages = set()
    for js in STATIC.glob("*.js"):
        src = js.read_text(encoding="utf-8")
        pages |= set(re.findall(r"PAGES\.([a-z]+)\s*=", src))
        for block in re.findall(r"Object\.assign\(PAGES,\s*\{([^}]*)\}", src):
            pages |= set(re.findall(r"([a-z]+):", block))
    return pages


def test_shell_is_english_first_and_serves_the_new_scripts(dash):
    r, html = req(dash, "GET", "/")
    assert r.status == 200 and b'<html lang="en" dir="ltr">' in html
    for f in ("i18n.js", "shell.js", "shell.css"):
        assert f"/static/{f}".encode() in html
        r, body = req(dash, "GET", "/static/" + f)
        assert r.status == 200 and b".innerHTML" not in body and b"eval(" not in body
    assert html.index(b"/static/i18n.js") < html.index(b"/static/app.js")      # the locale loads first
    assert b'id="gsearch"' in html and b'id="langSlot"' in html and b'id="statusChips"' in html


def test_grouped_navigation_keeps_every_page():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    nav = set(re.findall(r'data-page="([a-z]+)"', html))
    # pages reached from other pages, not from the side bar
    sub = {"session", "raw", "note", "guide", "context"}
    missing = registered_pages() - nav - sub - {"guide-for"}
    assert not missing, missing
    for page in ("runtime", "costs", "issues", "memory", "skills", "workspace", "features", "settings"):  # app.js routes
        assert page in nav
    assert html.count('class="nav-group"') >= 6
    # one language per label: every nav entry carries both texts for the switch, none mixes them
    for he, en in re.findall(r'data-he="([^"]+)" data-en="([^"]+)"', html):
        assert re.search(r"[֐-׿]", he) and not re.search(r"[֐-׿]", en)


def test_runtime_reports_the_host_device(dash):
    d = get(dash, "/api/runtime")
    assert d["host"]["os"] and "machine" in d["host"]
