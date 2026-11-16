"""Hermetic dashboard first-load payload meter (no network, no server): per entry page, the files it references,
raw and gzip(6) bytes (what dashboard.py sends with Accept-Encoding: gzip), plus request count.

    python core/tools/dashboard_payload.py [--md]
"""
from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

JARVES = Path(__file__).resolve().parents[1] / "src" / "jarves"
STATIC = JARVES / "dashboard_static"
MOBILE = STATIC / "mobile"
SHARED = ("chatview.js", "chatview.css")        # mobile.SHARED_FILES: served from dashboard_static/
_REF = re.compile(r"""<(?:script|link)\b[^>]*?(?:src|href)=["'](/(?:static|m)/[^"']+)["']""")

# page -> (html file, resolver of a referenced URL to a file)
PAGES = {"dashboard (/)": "index.html", "pair (/pair)": "pair.html", "phone PWA (/m/)": "mobile/index.html"}


def resolve(url: str) -> Path:
    if url.startswith("/static/"):
        return STATIC / url.removeprefix("/static/")
    name = url.removeprefix("/m/")
    return STATIC / name if name in SHARED else MOBILE / name


def measure_page(html_rel: str) -> dict:
    html = STATIC / html_rel
    files = [html] + [resolve(u) for u in _REF.findall(html.read_text("utf-8"))]
    rows = []
    for f in files:
        raw = f.read_bytes()
        rows.append({"file": f.relative_to(STATIC).as_posix(), "raw": len(raw), "gzip": len(gzip.compress(raw, 6, mtime=0))})
    return {"requests": len(rows), "raw": sum(r["raw"] for r in rows), "gzip": sum(r["gzip"] for r in rows), "files": rows}


def measure() -> dict:
    return {name: measure_page(rel) for name, rel in PAGES.items()}


def main() -> int:
    res = measure()
    print("| page | first-load requests | raw bytes | gzip bytes |\n|---|---|---|---|")
    for n, r in res.items():
        print(f"| {n} | {r['requests']} | {r['raw']} | {r['gzip']} |")
    if "--files" in sys.argv:
        for n, r in res.items():
            print(f"\n{n}")
            for f in sorted(r["files"], key=lambda x: -x["raw"]):
                print(f"  {f['raw']:>8} {f['gzip']:>8}  {f['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
