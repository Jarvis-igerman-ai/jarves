"""#60 slice: Release sign-off step by step (feature `signoff_steps`, beta, OFF). Same sheet and API as the table view;
the owner judges only his own rows, resumes at the first open one, and nothing is recorded or signed on his behalf."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from types import SimpleNamespace

from jarves import features, release_cmd, release_flow as rf, release_signoff as so
from jarves.config import Config

STATIC = Path(release_cmd.__file__).with_name("dashboard_static")
SHA = "a" * 40


def _root(tmp_path, monkeypatch):
    (tmp_path / "core").mkdir()
    (tmp_path / "docs" / "reviews").mkdir(parents=True)
    full = so.Sheet(version="4.1.0", rows=[so.Row("C1", "Voice", "Say hello", "Answers")])
    so.save(tmp_path, rf.reduced_sheet("4.1.0", full))          # S1-S5 for the owner + C1 accepted by automation
    monkeypatch.setenv("JARVES_REPO_DIR", str(tmp_path))
    return tmp_path


def test_owner_steps_skip_automation_rows_and_resume_at_first_open(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    sheet = so.load(root, "4.1.0")
    assert [r.id for r in release_cmd.owner_steps(sheet)] == list(rf.SMOKE_IDS)
    assert release_cmd.is_auto(sheet.row("C1")) and not release_cmd.is_auto(sheet.row("S1"))
    assert release_cmd.next_step(sheet)[:2] == (1, 5)
    sheet.set_result("S1", "pass")
    sheet.set_result("S2", "blocked")                                # blocked stays open: resume comes back to it
    assert release_cmd.next_step(sheet)[2].id == "S2"
    # an owner's own accepted-blocked note is NOT an automation row: it stays one of his steps
    sheet.set_result("S2", "accepted-blocked", "no microphone today")
    assert "S2" in [r.id for r in release_cmd.owner_steps(sheet)]
    assert release_cmd.next_step(sheet)[2].id == "S3"


def test_feature_is_beta_off_by_default_and_needs_release_signoff(tmp_path):
    f = features.CATALOG["signoff_steps"]
    assert f.default_enabled is False and f.stage == "beta" and f.needs == ["release_signoff"]
    assert features.Features(Config(), tmp_path).enabled("signoff_steps") is False


def test_api_reports_steps_flag_next_row_and_never_marks_rows(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    ctx = SimpleNamespace(home=tmp_path / "home", cfg=Config())
    on = {"v": False}
    monkeypatch.setattr(features, "_ctx_features", lambda c: SimpleNamespace(enabled=lambda fid: on["v"]))
    code, d = release_cmd.api_signoff(ctx, None, None, {"version": ["4.1.0"]})
    assert code == 200 and d["steps_view"] is False and d["next"] == "S1" and d["steps"] == list(rf.SMOKE_IDS)
    assert [r["id"] for r in d["rows"] if r["auto"]] == ["C1"]
    on["v"] = True
    _, d = release_cmd.api_signoff(ctx, None, None, {"version": ["4.1.0"]})
    assert d["steps_view"] is True and d["installed"] == ""          # no beta installed here: shown as unknown
    assert all(r["result"] == "pending" for r in d["rows"] if not r["auto"])   # reading never records anything
    code, _ = release_cmd.api_signoff_set(ctx, None, {"version": "4.1.0", "row": "S1", "result": "pass"})
    assert code == 200 and so.load(root, "4.1.0").row("S1").result == "pass"
    code, err = release_cmd.api_signoff_set(ctx, None, {"version": "4.1.0", "finish": True, "beta": "4.1.0-beta.2",
                                                        "commit": SHA, "owner": "Owner"})
    assert code == 409 and "rows still open" in err["error"] and not so.load(root, "4.1.0").signed


def test_cli_prints_the_same_next_step(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    ap = argparse.ArgumentParser()
    release_cmd.add_cli(ap.add_subparsers(dest="cmd"))
    run = lambda *a: release_cmd.cmd_release(ap.parse_args(["release", "signoff", "--repo", str(root), *a]), Config(), home)  # noqa: E731
    assert run("--version", "4.1.0", "--row", "S1", "--result", "pass") == 1
    assert "Next step (2 of 5): S2:" in capsys.readouterr().out


def test_page_script_loads_after_the_table_page_and_posts_only_single_rows_or_finish():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/updates.js") < html.index("/static/signoff_steps.js")
    js = (STATIC / "signoff_steps.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    posts = re.findall(r"post\(\{([^}]*)\}", js)
    assert posts and all(p.strip().startswith(("row: r.id", "finish: true")) for p in posts), posts
    assert "forEach" not in js.split("const record")[1].split("const failBox")[0]   # one press = one row, never bulk
