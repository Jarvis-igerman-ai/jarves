"""Conversational dashboard navigation: open_dashboard(page=...) opens that page and returns its controls.
Offline: a fake opener, no browser, no model."""
import re
from pathlib import Path

from jarves import dashboard_pages as pages
from jarves.dashboard import DashboardSkill
import jarves.dashboard as dash_mod


def _tool(opened):
    async def opener(page):
        opened.append(page)
        return "http://127.0.0.1:1234/?t=secret"
    return next(t for t in DashboardSkill(opener).tools() if t.name == "open_dashboard")


def test_every_sidebar_page_has_a_directory_entry():
    html = (Path(dash_mod.__file__).parent / "dashboard_static" / "index.html").read_text(encoding="utf-8")
    nav = set(re.findall(r'data-page="([a-z_]+)"', html)) - {"agent"}      # "agent" needs an id: not a page to open by name
    assert nav <= set(pages.ids())
    for pid in pages.ids():
        d = pages.describe(pid)
        assert d["about"] and d["about_he"] and d["controls"] and d["controls_he"]


def test_resolve_by_id_title_hebrew_and_synonym():
    assert pages.resolve("costs") == pages.resolve("עלויות") == pages.resolve("#/costs") == pages.resolve("spending") == "costs"
    assert pages.resolve("Timers & alarms") == "timers"
    assert pages.resolve("no such page") is None


async def test_open_named_page_returns_controls_without_the_key():
    opened = []
    t = _tool(opened)
    res = await t.handler(t.args(page="features"))
    assert res.ok and opened == ["#/features"]
    assert res.data["url"] == "http://127.0.0.1:1234/" and res.data["page"] == "features" and "secret" not in str(res.data)
    assert res.data["sensitive"] and "never do it for them" in res.data["note"]


async def test_unknown_page_opens_nothing_and_default_is_home():
    opened = []
    t = _tool(opened)
    bad = await t.handler(t.args(page="xyzzy"))
    assert not bad.ok and bad.code == "DASHBOARD_PAGE_UNKNOWN" and opened == []
    ok = await t.handler(t.args())
    assert ok.ok and opened == ["#/home"]


async def test_dashboard_page_reports_what_the_open_tab_last_sent():
    from jarves.dashboard import Ctx, api_ui_page
    seen = {}
    async def opener(page):
        return "http://127.0.0.1:1/"
    tools = {t.name: t for t in DashboardSkill(opener, lambda: seen.get("ui")).tools()}
    none = await tools["dashboard_page"].handler(None)
    assert not none.ok and none.code == "DASHBOARD_NOT_OPEN"

    class C:
        ui_page = None
        clock = staticmethod(lambda: __import__("time").time())
    c = C()
    assert api_ui_page(c, None, {"page": "settings"})[0] == 200
    seen["ui"] = c.ui_page
    res = await tools["dashboard_page"].handler(None)
    assert res.ok and res.data["page"] == "settings" and res.data["sensitive"] and res.data["seen_s_ago"] < 5
    api_ui_page(c, None, {"page": "<script>"})              # only a known page id is ever stored
    assert c.ui_page is None
