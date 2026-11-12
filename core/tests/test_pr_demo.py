"""JAR-251: the PR demo planner, terminal image and pr-demo workflow (pure parts; the browser run is CI's pr-demo job)."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import pr_demo  # noqa: E402

FAKE = {
    "core/src/jarves/dashboard_static/agents.js": "PAGES.agents = function(){}; PAGES.agent = x;",
    "core/src/jarves/dashboard_static/guides.js": 'PAGES.guides = a; PAGES["guide-for"] = b;',
    "core/src/jarves/pages.d/camera.toml": 'page = "camera"\ngroup = "assistant"\n',
}
KNOWN = ["home", "settings", "agents", "guides", "camera"]


def read(p):
    if p not in FAKE:
        raise FileNotFoundError(p)
    return FAKE[p]


def test_page_script_maps_to_the_pages_it_registers_and_unknown_detail_pages_are_dropped():
    pl = pr_demo.plan(["core/src/jarves/dashboard_static/agents.js"], read, KNOWN)
    assert pl["user_facing"] and pl["pages"] == ["agents"]          # "agent" is a detail page, not in the nav
    assert pr_demo.registered_pages(FAKE["core/src/jarves/dashboard_static/guides.js"]) == ["guides", "guide-for"]


def test_shared_shell_locales_modules_guides_phone_and_cli():
    pl = pr_demo.plan(["core/src/jarves/dashboard_static/style.css", "core/src/jarves/dashboard_static/locales/he.json",
                       "core/src/jarves/pages.d/camera.toml", "core/src/jarves/guides/data/timers.toml",
                       "core/src/jarves/dashboard_static/mobile/app.js", "core/src/jarves/phone_cli.py"], read, KNOWN)
    assert pl["pages"] == ["home", "settings", "camera", "guide/timers"]
    assert pl["phone"] is True
    assert pl["cli"] == ["phone --help", "--help"]


def test_deleted_page_script_falls_back_to_the_overview_and_pages_are_capped():
    assert pr_demo.plan(["core/src/jarves/dashboard_static/gone.js"], read, KNOWN)["pages"] == ["home", "settings"]
    many = [f"core/src/jarves/guides/data/g{i}.toml" for i in range(7)]
    pl = pr_demo.plan(many, read, KNOWN)
    assert len(pl["pages"]) == pr_demo.MAX_PAGES and len(pl["more_pages"]) == 3


def test_tests_docs_and_backend_are_not_user_facing():
    pl = pr_demo.plan(["core/tests/test_x.py", "docs/NOW.md", "core/src/jarves/store.py", ".github/workflows/a.yml"],
                      read, KNOWN)
    assert pl == {"user_facing": False, "pages": [], "more_pages": [], "phone": False, "cli": [], "reasons": []}


def test_what_changed_prefers_the_pr_body_line_and_strips_ticket_prefixes():
    body = "Summary\n\n**What changed for you:** the timers page shows the time left\n"
    assert pr_demo.what_changed("JAR-9: timers", body) == "the timers page shows the time left"
    assert pr_demo.what_changed("JAR-9: show time left on timers", "") == "show time left on timers"
    assert pr_demo.what_changed("feat(ui): bigger buttons", "Now you can: tap bigger buttons") == "tap bigger buttons"


def test_terminal_svg_escapes_scrubs_and_cuts_long_output():
    text = "\x1b[31mred\x1b[0m <b>&\napi_key=sk-123 visible\n" + "\n".join(f"line {i}" for i in range(80))
    svg = pr_demo.terminal_svg("$ jarvis --help", text, max_lines=10)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "&lt;b&gt;&amp;" in svg and "\x1b" not in svg and "sk-123" not in svg and "[redacted]" in svg
    assert "more lines" in svg and svg.count("<text ") == 1 + 10


def test_summary_lists_checks_notes_and_the_artifact_name():
    demo = {"pr": "12", "what_changed": "bigger buttons", "title": "t", "ok": False,
            "checks": [{"name": "home: no browser errors", "ok": False}], "notes": ["x"], "files": ["home-after.png"]}
    md = pr_demo.summary_md(demo)
    assert "demo-12" in md and "FAIL: home: no browser errors" in md and "PROBLEMS FOUND" in md and "home-after.png" in md


def test_pr_demo_workflow_is_read_only_pr_only_and_never_puts_pr_text_in_a_script():
    path = ROOT.parent / ".github" / "workflows" / "pr-demo.yml"
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    on = doc.get("on", doc.get(True))
    assert list(on) == ["pull_request"]                              # never pull_request_target: no secrets, fork-safe
    assert doc["permissions"] == {"contents": "read"}
    job = doc["jobs"]["demo"]
    assert job["timeout-minutes"] <= 30
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "github.event.pull_request.title" not in runs and "github.event.pull_request.body" not in runs
    up = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert up["with"]["name"] == "demo-${{ github.event.pull_request.number }}"
    assert "secrets." not in text
