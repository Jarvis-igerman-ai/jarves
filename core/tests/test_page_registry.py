"""pages.d/<page>.toml: a dashboard page is added by its own file, not by edits to index.html."""
from __future__ import annotations

import re
from pathlib import Path

from jarves import page_registry as pr

from test_dashboard import dash, req  # noqa: F401  (fixture + helper)

STATIC = Path(pr.__file__).parent / "dashboard_static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def _page(tmp_path, name, body):
    (tmp_path / f"{name}.toml").write_text(body, encoding="utf-8")


def test_compose_adds_nav_links_and_tags_at_the_markers(tmp_path):
    _page(tmp_path, "b", 'page = "pb"\ngroup = "system"\nhe = "ב"\nen = "Page <B>"\nscripts = ["/static/b.js"]\nstyles = ["/static/b.css"]\n')
    _page(tmp_path, "a", 'page = "pa"\ngroup = "system"\nhe = "א"\nen = "Page A"\n')
    html = pr.compose(INDEX, tmp_path)
    assert html.index('data-page="pa"') < html.index('data-page="pb"') < html.index("<!--jarves:nav:system-->")
    assert html.index('data-page="settings"') < html.index('data-page="pa"')              # after the built-in pages
    assert 'data-he="ב" data-en="Page &lt;B&gt;"' in html and 'href="#/pb"' in html
    assert html.index('<link rel="stylesheet" href="/static/b.css">') < html.index('<script src="/static/b.js" defer>') \
        < html.index('<script src="/static/shell.js"')
    assert pr.compose(INDEX, tmp_path / "none") == INDEX


def test_unknown_group_is_an_error(tmp_path):
    _page(tmp_path, "x", 'page = "px"\ngroup = "nope"\nhe = "x"\nen = "x"\n')
    try:
        pr.compose(INDEX, tmp_path)
    except ValueError as e:
        assert "nope" in str(e)
    else:
        raise AssertionError("an unknown nav group must fail loudly")


def test_index_has_a_marker_for_every_nav_group_and_the_scripts():
    groups = re.findall(r'<div class="nav-group" data-group="(\w+)">', INDEX)
    assert groups and all(f"<!--jarves:nav:{g}-->" in INDEX for g in groups)
    assert INDEX.count("<!--jarves:scripts-->") == 1 and INDEX.index("<!--jarves:scripts-->") < INDEX.index("shell.js")


def test_the_page_files_in_the_repository_are_valid():
    seen = set(re.findall(r'data-page="(\w+)"', INDEX))
    for p in pr.pages():
        assert {"page", "group", "he", "en"} <= set(p), p
        assert p["page"] not in seen, f"{p['page']} is already written into index.html"
        seen.add(p["page"])
        for src in [*p.get("scripts", []), *p.get("styles", [])]:
            assert src.startswith("/static/") and (STATIC / src.removeprefix("/static/")).is_file(), src
    pr.compose(INDEX)                                                                    # every group exists


def test_the_server_serves_the_composed_shell(dash, tmp_path, monkeypatch):
    _page(tmp_path, "served", 'page = "served"\ngroup = "dev"\nhe = "מוגש"\nen = "Served page"\n')
    monkeypatch.setattr(pr, "DIR", tmp_path)
    r, body = req(dash, "GET", "/")
    assert r.status == 200 and b'data-page="served"' in body and b"<!--jarves:" in body
