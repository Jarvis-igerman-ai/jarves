"""Dashboard pages in one small file per page (pages.d/<page>.toml) instead of edits to index.html.

A new page used to add its navigation link and its <script>/<link> tags to the same few places in index.html, so every
PR that added a page conflicted there (41 of ~150 merges). Now the page ships `pages.d/<page>.toml`:

    page    = "mypage"                    # the route #/mypage and data-page
    group   = "system"                    # a nav-group of index.html (top, everyday, ..., system)
    he      = "העמוד שלי"
    en      = "My page"
    scripts = ["/static/mypage.js"]       # loaded before shell.js, in this order
    styles  = ["/static/mypage.css"]

`compose(html)` fills the `<!--jarves:nav:GROUP-->` and `<!--jarves:scripts-->` markers of index.html with the entries,
in file-name order after the pages that are written into index.html itself. The server composes on every request.
"""
from __future__ import annotations

import html as _html
import tomllib
from pathlib import Path

DIR = Path(__file__).with_name("pages.d")


def pages(directory: Path | None = None) -> list[dict]:
    return [tomllib.loads(f.read_text(encoding="utf-8")) for f in sorted((directory or DIR).glob("*.toml"))]


def _link(p: dict) -> str:
    he, en, page = (_html.escape(str(p[k]), quote=True) for k in ("he", "en", "page"))
    return (f'<a href="#/{page}" data-page="{page}" role="treeitem" tabindex="-1">'
            f'<span data-he="{he}" data-en="{en}">{en}</span></a>')


def compose(index: str, directory: Path | None = None) -> str:
    """index.html with every page file's nav link and script / style tags inserted at the markers."""
    ps = pages(directory)
    for g in sorted({p["group"] for p in ps}):
        links = "".join("\n        " + _link(p) for p in ps if p["group"] == g)
        marker = f"<!--jarves:nav:{g}-->"
        if marker not in index:
            raise ValueError(f"pages.d: unknown nav group {g!r}")
        index = index.replace(marker, links.lstrip("\n ") + "\n        " + marker, 1)
    tags = []
    for p in ps:
        tags += [f'<link rel="stylesheet" href="{_html.escape(s, quote=True)}">' for s in p.get("styles", [])]
        tags += [f'<script src="{_html.escape(s, quote=True)}" defer></script>' for s in p.get("scripts", [])]
    return index.replace("<!--jarves:scripts-->", "\n".join(tags + ["<!--jarves:scripts-->"]), 1)
