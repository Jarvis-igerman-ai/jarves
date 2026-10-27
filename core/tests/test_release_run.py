"""`jarvis release run`: guarded sign-off + next commands. Fakes only: GitHub data injected, frozen clock, temp dirs,
no git, no network, nothing is tagged or pushed."""
from __future__ import annotations

import argparse
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarves import release_cmd, release_flow as rf, release_run as rr, release_signoff as so

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
SHA = "a" * 40
V = "5.0.0"
GREEN = {"head_sha": SHA, "conclusion": "success", "status": "completed", "event": "push", "created_at": "2026-10-27T08:00:00Z"}


def fetcher(checks=(GREEN,), issues=()):
    data = {"checks": list(checks), "issues": list(issues)}
    return lambda kind: data[kind]


def finish(sheet, owner):
    sheet.sign(f"{V}-beta.3", SHA, "2026-10-27", owner)


def make_sheet(root: Path, results=("pass", "accepted-blocked")):
    rows = [so.Row("S1", "Smoke", "x", "y", results[0], ""), so.Row("A1", "Auto", "x", "y", results[1],
                                                                     "accepted by automation: CI")]
    so.save(root, so.Sheet(version=V, rows=rows, intro="t"))


def go(root, **kw):
    kw.setdefault("fetch", fetcher())
    kw.setdefault("finish", finish)
    kw.setdefault("now", lambda: NOW)
    return rr.run(root, V, **kw)


def text(res):
    return "\n".join(res.lines)


def test_without_approval_nothing_is_signed(tmp_path):
    make_sheet(tmp_path)
    res = go(tmp_path)
    assert res.code == 1 and not res.signed_now and "--approved-by-owner" in text(res)
    assert not so.load(tmp_path, V).signed and not (tmp_path / "docs" / "release").exists()


def test_approved_signs_records_approval_and_prints_commands_only(tmp_path):
    make_sheet(tmp_path)
    res = go(tmp_path, approved_by="OK sign it, chat 2026-10-27 08:55 UTC")
    assert res.code == 0 and res.signed_now
    sh = so.load(tmp_path, V)
    assert sh.signed and sh.beta == f"{V}-beta.3" and "chat approval" in sh.owner
    ev = (tmp_path / "docs" / "release" / f"EVIDENCE_{V}.md").read_text(encoding="utf-8")
    assert "Approved by owner in chat: OK sign it, chat 2026-10-27 08:55 UTC" in ev and "2026-10-27 09:00 UTC" in ev
    out = text(res)
    assert "git tag -a v5.0.0" in out and "release_gate.py --tag v5.0.0" in out and "gh pr create" in out
    assert "does not tag or push" in out
    # a second run does not sign again
    again = go(tmp_path, approved_by="OK sign it, chat 2026-10-27 08:55 UTC")
    assert again.code == 0 and not again.signed_now and "already signed" in text(again)


def test_refuses_when_main_is_red(tmp_path):
    make_sheet(tmp_path)
    res = go(tmp_path, approved_by="chat approval now", fetch=fetcher(checks=({**GREEN, "conclusion": "failure"},)))
    assert res.code == 2 and any("main green" in x for x in res.refusals) and not so.load(tmp_path, V).signed


def test_refuses_on_open_p0_p2_but_not_on_known_issue(tmp_path):
    make_sheet(tmp_path)
    bad = {"number": 7, "state": "open", "labels": [{"name": "P1"}, {"name": "bug"}], "title": "x"}
    res = go(tmp_path, approved_by="chat approval now", fetch=fetcher(issues=[bad]))
    assert res.code == 2 and any("#7 P1" in x for x in res.refusals) and not so.load(tmp_path, V).signed
    ok = {**bad, "labels": [{"name": "P1"}, {"name": "known-issue-4.5"}]}
    assert go(tmp_path, approved_by="chat approval now", fetch=fetcher(issues=[ok])).code == 0


def test_refuses_when_sheet_missing_or_not_signable(tmp_path):
    res = go(tmp_path, approved_by="chat approval now")
    assert res.code == 2 and any("owner sheet" in x for x in res.refusals)
    make_sheet(tmp_path, results=("pending", "accepted-blocked"))
    res = go(tmp_path, approved_by="chat approval now")
    assert res.code == 2 and any("not signable" in x for x in res.refusals) and not so.load(tmp_path, V).signed


def test_github_trouble_fails_closed(tmp_path):
    make_sheet(tmp_path)

    def boom(kind):
        raise OSError("offline")
    res = go(tmp_path, approved_by="chat approval now", fetch=boom)
    assert res.code == 2 and not so.load(tmp_path, V).signed


def test_sign_failure_is_a_refusal(tmp_path):
    make_sheet(tmp_path)

    def bad_finish(sheet, owner):
        raise so.SheetError("the tested beta must look like 5.0.0-beta.N")
    res = go(tmp_path, approved_by="chat approval now", finish=bad_finish)
    assert res.code == 2 and not so.load(tmp_path, V).signed


def test_dry_run_changes_nothing(tmp_path):
    make_sheet(tmp_path)
    before = (tmp_path / "docs" / "reviews" / f"MANUAL_TEST_{V}.md").read_bytes()
    res = go(tmp_path, approved_by="chat approval now", dry_run=True)
    assert res.code == 0 and "DRY RUN" in text(res) and "git tag -a v5.0.0" in text(res)
    assert (tmp_path / "docs" / "reviews" / f"MANUAL_TEST_{V}.md").read_bytes() == before
    assert not (tmp_path / "docs" / "release").exists()


def test_approval_text_is_validated(tmp_path):
    make_sheet(tmp_path)
    with pytest.raises(so.SheetError):
        go(tmp_path, approved_by="ok")
    with pytest.raises(so.SheetError):
        go(tmp_path, approved_by="bad\x00text here")


def test_module_never_runs_git_tags_or_pushes():
    src = inspect.getsource(rr)
    assert "subprocess" not in src and "os.system" not in src and "Popen" not in src
    assert "git tag" in src and "git push" in src          # printed for the owner, never executed


def test_cli_registration():
    sub = argparse.ArgumentParser().add_subparsers(dest="cmd")
    release_cmd.add_cli(sub)
    ns = sub.choices["release"].parse_args(["run", "--version", V, "--approved-by-owner", "chat ok now", "--dry-run"])
    assert ns.action == "run" and ns.approved_by_owner == "chat ok now" and ns.dry_run is True
    assert sub.choices["release"].parse_args(["run"]).approved_by_owner is None
    assert rf.REPO in "".join(rr.next_commands(V, "a", "b"))


def test_cmd_release_run_goes_through_cmd_run(tmp_path, monkeypatch, capsys):
    (tmp_path / "core").mkdir()
    make_sheet(tmp_path)
    monkeypatch.setattr(rf, "github_fetch", fetcher())
    ns = argparse.Namespace(action="run", version=V, repo=str(tmp_path), approved_by_owner=None, dry_run=False,
                            beta=f"{V}-beta.3", commit=SHA, owner=None, row=None, finish=False, report=False, force=False)
    assert release_cmd.cmd_release(ns, None, tmp_path) == 1          # no approval: nothing signed
    assert "--approved-by-owner" in capsys.readouterr().out and not so.load(tmp_path, V).signed
    ns.approved_by_owner = "ok"                                       # too short: clean error, not a traceback
    assert release_cmd.cmd_release(ns, None, tmp_path) == 2 and "RELEASE_RUN" in capsys.readouterr().out
    ns.approved_by_owner = "chat approval 2026-10-27 09:00 UTC"
    assert release_cmd.cmd_release(ns, None, tmp_path) == 0
    out = capsys.readouterr().out
    assert "git tag -a v5.0.0" in out and so.load(tmp_path, V).signed
