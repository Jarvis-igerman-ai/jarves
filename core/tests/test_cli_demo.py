"""cli_demo: CLI help rendered as a terminal SVG image plus demo.json (JAR-251). Hermetic: no child process runs."""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import cli_demo  # noqa: E402


def test_svg_is_valid_escaped_and_strips_ansi():
    svg = cli_demo.render_svg("\x1b[1musage:\x1b[0m jarvis <x> & 'y'\n  --help", title="jarvis --help")
    root = ET.fromstring(svg)  # well-formed XML, so '<', '&' were escaped
    texts = [t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text")]
    assert "$ jarvis --help" in texts
    assert "usage: jarvis <x> & 'y'" in texts and "  --help" in texts
    assert "\x1b" not in svg


def test_svg_is_bounded():
    svg = cli_demo.render_svg("\n".join("x" * 500 for _ in range(500)), title="t")
    texts = [t.text or "" for t in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]
    assert len(texts) == cli_demo.MAX_LINES + 2  # prompt + lines + "… (N more lines)"
    assert texts[-1] == f"… ({500 - cli_demo.MAX_LINES} more lines)"
    assert max(len(t) for t in texts) <= cli_demo.MAX_COLS


def test_write_demo_atomic_json(tmp_path):
    (tmp_path / "cli-help.svg").write_text("<svg/>", encoding="utf-8")
    p = cli_demo.write_demo(tmp_path, pr=7, title="T", what_changed="New help line", files=["cli-help.svg"])
    assert json.loads(p.read_text(encoding="utf-8")) == {
        "pr": 7, "title": "T", "what_changed": "New help line", "files": ["cli-help.svg"]}
    assert [x.name for x in tmp_path.iterdir() if x.name.startswith(".")] == []  # no temp file left


@pytest.mark.parametrize("kw, msg", [
    ({"pr": 0}, "pr"), ({"pr": "7"}, "pr"), ({"title": ""}, "title"), ({"what_changed": "a\nb"}, "what_changed"),
    ({"what_changed": "x" * 300}, "what_changed"), ({"files": ["../x.svg"]}, "file"), ({"files": ["missing.svg"]}, "file"),
])
def test_write_demo_rejects_bad_input(tmp_path, kw, msg):
    args = {"pr": 7, "title": "T", "what_changed": "W", "files": [], **kw}
    with pytest.raises(ValueError, match=msg):
        cli_demo.write_demo(tmp_path, **args)
    assert not (tmp_path / "demo.json").exists()


def test_capture_help_runs_only_fixed_help_command():
    seen = []

    def fake_run(cmd, **kw):
        seen.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout="usage: jarvis agents\n", stderr="")

    assert cli_demo.capture_help("agents", run=fake_run) == "usage: jarvis agents\n"
    cmd, kw = seen[0]
    assert cmd == [sys.executable, "-m", "jarves", "agents", "--help"] and kw["timeout"] and not kw.get("shell")
    for bad in ["agents; rm -rf /", "--config=x", "A", "x" * 40]:
        with pytest.raises(ValueError, match="group"):
            cli_demo.capture_help(bad, run=fake_run)
    assert len(seen) == 1
    cli_demo.capture_help("", run=fake_run)  # top-level help
    assert seen[1][0] == [sys.executable, "-m", "jarves", "--help"]


def test_capture_help_failure_is_reported():
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=2, stdout="", stderr="error: invalid choice\n")
    with pytest.raises(RuntimeError, match="exit 2.*invalid choice"):
        cli_demo.capture_help("nope", run=fake_run)


def test_main_writes_svg_and_demo_json(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_demo, "capture_help", lambda g, run=None: f"usage: jarvis {g or ''}\n")
    rc = cli_demo.main(["--pr", "9", "--title", "T", "--what", "W", "--out", str(tmp_path), "--group", "agents", "--group", ""])
    assert rc == 0
    demo = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert demo["files"] == ["cli-agents.svg", "cli-help.svg"]
    assert "jarvis agents --help" in (tmp_path / "cli-agents.svg").read_text(encoding="utf-8")


def test_main_bad_input_exits_2(tmp_path, capsys):
    assert cli_demo.main(["--pr", "9", "--title", "T", "--what", "W", "--out", str(tmp_path), "--group", "a;b"]) == 2
    assert "group" in capsys.readouterr().err
    assert not (tmp_path / "demo.json").exists()
