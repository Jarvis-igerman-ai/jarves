"""JAR-251: the PR demo planner, terminal image and pr-demo workflow (pure parts; the browser run is CI's pr-demo job)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("pr_demo", ROOT / "tools" / "pr_demo.py")   # no global sys.path change
pr_demo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pr_demo)

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


def test_asset_only_change_is_shown_on_the_overview():
    pl = pr_demo.plan(["core/src/jarves/dashboard_static/icons/mic.svg",
                       "core/src/jarves/dashboard_static/fonts/a.woff2"], read, KNOWN)
    assert pl["user_facing"] and pl["pages"] == ["home", "settings"]


def test_deleted_module_is_resolved_from_the_base_side_else_the_overview():
    base = {"core/src/jarves/pages.d/radio.toml": 'page = "radio"\n'}
    pl = pr_demo.plan(["core/src/jarves/pages.d/radio.toml"], read, KNOWN, read_base=lambda p: base[p])
    assert pl["user_facing"] and pl["pages"] == ["radio"]           # before shows it, after shows it gone
    assert pr_demo.plan(["core/src/jarves/pages.d/x.toml"], read, KNOWN)["pages"] == ["home", "settings"]


def test_cli_files_with_underscores_map_to_their_subcommand():
    assert pr_demo.plan(["core/src/jarves/smart_home_cli.py"], read, KNOWN)["cli"] == ["smart-home --help", "--help"]


def test_known_pages_of_leaves_sys_path_and_modules_alone(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "all_pages_check.py").write_text("def nav_pages():\n    return ['home']\n")
    before = list(sys.path)
    assert pr_demo.known_pages_of(tmp_path) == ["home"]
    assert sys.path == before and "all_pages_check" not in sys.modules


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


def test_workflow_renders_the_pr_head_and_watches_cli_parity():
    doc = yaml.safe_load((ROOT.parent / ".github" / "workflows" / "pr-demo.yml").read_text(encoding="utf-8"))
    on = doc.get("on", doc.get(True))
    assert "core/src/jarves/cli_parity.py" in on["pull_request"]["paths"]
    co = next(s for s in doc["jobs"]["demo"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert co["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"      # not the synthetic merge commit
    assert co["with"]["persist-credentials"] is False                            # PR code never sees the token
    up = next(s for s in doc["jobs"]["demo"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert up["if"] == "always()"                                                # a crash still keeps partial files


def test_browser_guard_aborts_every_host_but_this_machine():
    calls = []

    class Ctx:
        def route(self, pattern, handler):
            calls.append((pattern, handler))

        def route_web_socket(self, pattern, handler):
            calls.append(("ws", handler))

    class Route:
        def __init__(self, url):
            self.request, self.done = type("R", (), {"url": url})(), ""

        def continue_(self):
            self.done = "continue"

        def abort(self):
            self.done = "abort"

    pr_demo.guard(Ctx())
    assert [c[0] for c in calls] == ["**/*", "ws"]
    for url, want in [("http://127.0.0.1:8123/#/home", "continue"), ("file:///tmp/x.png", "continue"),
                      ("https://example.com/x.js", "abort"), ("http://169.254.169.254/latest", "abort"),
                      ("http://127.0.0.1.evil.test/", "abort")]:
        r = Route(url)
        calls[0][1](r)
        assert r.done == want, url


def test_websockets_to_other_hosts_are_closed_and_service_workers_blocked():
    class Ws:
        def __init__(self, url):
            self.url, self.done = url, ""

        def connect_to_server(self):
            self.done = "connect"

    for url, want in [("ws://127.0.0.1:8123/api/ws", "connect"), ("wss://evil.test/x", ""),
                      ("ws://169.254.169.254/", "")]:
        w = Ws(url)
        pr_demo._ws(w)
        assert w.done == want, url
    seen = {}

    class Ctx:
        def route(self, *a):
            seen["route"] = True

        def route_web_socket(self, *a):
            seen["ws"] = True

    class B:
        def new_context(self, **kw):
            seen.update(kw)
            return Ctx()
    pr_demo.new_ctx(B(), viewport=pr_demo.PHONE)
    assert seen["service_workers"] == "block" and seen["route"] and seen["ws"]
    src = (ROOT / "tools" / "pr_demo.py").read_text(encoding="utf-8")
    assert src.count("b.new_context(") == 1                          # every capture context goes through new_ctx


def _ci(tmp_path, monkeypatch, results, files):
    seen = []

    def child(core, args):
        seen.append(args)
        return results[(args[args.index("--label") + 1], args[0])]
    monkeypatch.setattr(pr_demo, "_child", child)
    monkeypatch.setattr(pr_demo, "compare", lambda out, pages: [])
    monkeypatch.setattr(pr_demo, "svg_to_png", lambda out: None)
    base = tmp_path / "base"
    (base / "src").mkdir(parents=True)
    out = tmp_path / "demo"
    out.mkdir()
    (out / "home-after.png").write_bytes(b"x")
    demo = pr_demo.ci("main", base, "7", out, "JAR-9: bigger buttons", "", files)
    assert json.loads((out / "demo.json").read_text(encoding="utf-8")) == demo
    return demo, seen


OKCAP = {"errors": {"home": [], "phone": []}}


def test_ci_after_failure_fails_before_failure_only_notes_and_phone_is_captured(tmp_path, monkeypatch):
    files = ["core/src/jarves/dashboard_static/style.css", "core/src/jarves/dashboard_static/mobile/app.js"]
    demo, seen = _ci(tmp_path, monkeypatch, {("after", "capture"): OKCAP, ("before", "capture"): {"failed": True}}, files)
    assert demo["ok"] and demo["files"] == ["home-after.png"]
    assert any(n.startswith("before: page capture failed") for n in demo["notes"])
    assert "--phone" in seen[0] and "--video" in seen[0] and "--video" not in seen[1]
    assert {c["name"] for c in demo["checks"]} >= {"home: no browser errors", "phone: no browser errors"}
    demo, _ = _ci(tmp_path / "2", monkeypatch, {("after", "capture"): {"failed": True, "log": "boom"},
                                                ("before", "capture"): OKCAP}, files)
    assert demo["ok"] is False and any(n.startswith("after: page capture failed: boom") for n in demo["notes"])


def test_ci_phone_only_change_fails_truthfully_without_a_phone_image(tmp_path, monkeypatch):
    bad = {"errors": {"phone": ["no screenshot"]}}
    demo, seen = _ci(tmp_path, monkeypatch, {("after", "capture"): bad, ("before", "capture"): OKCAP},
                     ["core/src/jarves/dashboard_static/mobile/app.js"])
    assert "--phone" in seen[0] and "--video" not in seen[0] and demo["ok"] is False


def test_ci_cli_exit_code_and_missing_base(tmp_path, monkeypatch):
    monkeypatch.setattr(pr_demo, "_child", lambda core, args: {"cli": [{"cmd": "--help", "rc": 2, "file": "c.svg"}]})
    monkeypatch.setattr(pr_demo, "svg_to_png", lambda out: None)
    demo = pr_demo.ci("main", None, "7", tmp_path, "t", "", ["core/src/jarves/cli.py"])
    assert demo["ok"] is False and any("No base checkout" in n for n in demo["notes"])
    assert demo["checks"] == [{"name": "jarvis --help exits 0", "ok": False, "detail": []}]


def test_child_timeout_is_a_failed_side_not_a_crash(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)
    monkeypatch.setattr(pr_demo.subprocess, "run", boom)
    assert pr_demo._child(ROOT, ["cli"])["failed"] is True


def test_a_failed_walkthrough_keeps_the_screenshots(tmp_path, monkeypatch):
    def no_ffmpeg(*a):
        raise RuntimeError("Video rendering requires ffmpeg binary")
    monkeypatch.setattr(pr_demo, "walkthrough", no_ffmpeg)
    res = {"video": ""}
    pr_demo.record_video(res, None, [("home", "u", {})], tmp_path, "en")
    assert res["video"] == "" and "ffmpeg" in res["video_error"]


def test_cli_only_change_renders_terminal_images_through_the_real_child(tmp_path, monkeypatch):
    monkeypatch.setattr(pr_demo, "svg_to_png", lambda out: None)             # the PNG copy needs a browser
    demo = pr_demo.ci("main", None, "7", tmp_path, "t", "", ["core/src/jarves/cli_parity.py"])
    assert demo["plan"]["cli"] == ["--help"]
    assert "cli-1-after.svg" in demo["files"], demo["notes"]
    assert demo["checks"] == [{"name": "jarvis --help exits 0", "ok": True, "detail": []}]


def test_ci_cli_only_failed_after_child_is_not_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(pr_demo, "_child", lambda core, args: {"failed": True, "rc": 1, "log": "boom"})
    monkeypatch.setattr(pr_demo, "svg_to_png", lambda out: None)
    demo = pr_demo.ci("main", None, "7", tmp_path, "t", "", ["core/src/jarves/cli.py"])
    assert demo["ok"] is False and any(n.startswith("after: CLI capture failed: boom") for n in demo["notes"])
    assert demo["checks"] == [{"name": "terminal images rendered", "ok": False, "detail": []}]
