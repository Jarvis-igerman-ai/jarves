"""JAR-141: the phone page's tab pattern is complete (static checks, hermetic)."""
from __future__ import annotations

import re
from pathlib import Path

MOBILE = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static" / "mobile"


def test_every_tab_controls_a_labelled_tabpanel():
    html = (MOBILE / "index.html").read_text(encoding="utf-8")
    tabs = re.findall(r'role="tab" id="(tabbtn-\w+)" data-tab="\w+" aria-controls="(tab-\w+)"', html)
    assert len(tabs) == 9 and len(re.findall(r'role="tab"', html)) == 9
    for btn, panel in tabs:
        assert re.search(rf'<section id="{panel}" class="panel" role="tabpanel" aria-labelledby="{btn}"', html), panel


def test_drawer_traps_tab_and_supports_arrow_keys():
    js = (MOBILE / "m_shell.js").read_text(encoding="utf-8")
    for key in ('"ArrowDown"', '"ArrowUp"', '"Home"', '"End"', '"Tab"'):
        assert key in js
