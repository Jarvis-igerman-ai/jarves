"""JAR-147: unit suffixes in Hebrew UI go through tx(); no bare English " s" / " KB" glued to a number."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static"
BARE = re.compile(r'\+\s*"\s?(s|KB)"')
FIXED = ["browser.js", "modellab.js", "usage.js"]


def test_no_bare_english_units_in_fixed_pages():
    for name in FIXED:
        text = (STATIC / name).read_text(encoding="utf-8")
        assert not BARE.search(text), f"{name}: bare English unit literal; wrap it in tx(he, en)"


def test_unit_labels_have_hebrew():
    expect = {"browser.js": "שנ׳", "modellab.js": "שנ׳", "usage.js": "ק״ב"}
    for name, he in expect.items():
        assert he in (STATIC / name).read_text(encoding="utf-8")
