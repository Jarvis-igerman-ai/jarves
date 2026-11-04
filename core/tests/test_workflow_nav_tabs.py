"""#789 owner rule 5: five primary tabs plus a "עוד" menu; phone bottom bar of four big targets."""
import re
import unittest
from pathlib import Path

from jarves.workflow.server import STATIC


def _read(name: str) -> str:
    return (Path(STATIC) / name).read_text(encoding="utf-8")


class NavTabsContractTests(unittest.TestCase):
    def setUp(self):
        self.html = _read("index.html")
        self.app = _read("app.js")
        self.css = _read("style.css")
        nav = re.search(r'<nav class="main-nav"[^>]*>(.*?)</nav>', self.html, re.S)
        self.assertIsNotNone(nav)
        self.nav = nav.group(1)
        self.menu = re.search(r'<div class="nav-more-menu">(.*?)</div>', self.nav, re.S).group(1)
        self.primary = self.nav.replace(self.menu, "")

    def test_five_primary_tabs_and_more_menu(self):
        primary = re.findall(r'<a href="#(\w+)" data-view="(\w+)"', self.primary)
        self.assertEqual([view for _, view in primary], ["overview", "questions", "work", "health", "blocked"])
        self.assertIn('<details class="nav-more" id="nav-more">', self.nav)
        self.assertIn("<span>עוד</span></summary>", self.nav)
        menu = [view for view in re.findall(r'data-view="(\w+)"', self.menu)]
        self.assertEqual(menu, ["health", "blocked", "roadmap", "trace"])
        # Health and timeline are merged as one "מצב" tab; a sub-tab row switches between them.
        self.assertIn('data-view="health" data-also="timeline" class="nav-link nav-wide"><span class="nav-icon" aria-hidden="true">◉</span><span>מצב</span>', self.primary)
        self.assertIn('<nav class="subnav"', self.html)
        self.assertIn('body[data-view="health"] .subnav,body[data-view="timeline"] .subnav{display:flex}', self.css)

    def test_phone_has_four_targets_and_no_horizontal_scroll(self):
        tail = self.css[self.css.index("/* #789 rule 5"):]
        phone = tail[tail.index("@media(max-width:760px){"):]
        self.assertIn("grid-template-columns:repeat(4,minmax(0,1fr))", phone)
        self.assertIn("overflow:visible", phone)
        self.assertNotIn("overflow-x:auto", phone)
        self.assertIn(".main-nav>.nav-wide{display:none}", phone)
        self.assertIn(".nav-phone{display:flex}", phone)
        self.assertIn("min-height:60px", phone)
        # Four direct children on the phone: overview, questions, work and the "עוד" button.
        visible = re.findall(r'<a href="#\w+" data-view="\w+"(?: data-also="\w+")? class="nav-link(?: active)?">', self.primary)
        self.assertEqual(len(visible) + self.primary.count('class="nav-more"'), 4)

    def test_blocked_tab_reuses_plan_with_blocked_only_filter(self):
        self.assertIn('views.blocked = ["תקוע"', self.app)
        self.assertIn('if (filter === "blocked") tasks = tasks.filter(task => task.state === "blocked");', self.app)
        self.assertIn('const section = view === "blocked" ? "roadmap" : view;', self.app)
        self.assertIn('link.dataset.view === view || link.dataset.also === view', self.app)
        self.assertIn('"health", "timeline", "blocked"]', _read("overview.js"))
        # Badge hidden at zero, menu closes on navigation, outside click and Escape.
        self.assertIn("badge.hidden = !stuck", self.app)
        self.assertIn("if (more) more.open = false;", self.app)
        self.assertIn('event.key === "Escape" && more?.open', self.app)


if __name__ == "__main__":
    unittest.main()
