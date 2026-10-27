"""`jarvis release status` / `sheet` (docs/plans/SIMPLE_RELEASES_2026-10-26.md): pure checks, reduced sheet, CLI.
Offline: GitHub data is injected, the clock is frozen, temp dirs only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from jarves import release_cmd, release_flow as rf, release_signoff as so
from jarves.config import Config

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40
GREEN = {"head_sha": SHA, "conclusion": "success", "status": "completed", "event": "push", "created_at": "2026-10-26T10:00:00Z"}


def fetcher(checks=(GREEN,), issues=()):
    data = {"checks": list(checks), "issues": list(issues)}
    return lambda kind: data[kind]


def test_main_green_and_no_nightly_check():
    assert rf.check_main([GREEN], SHA).ok
    assert not rf.check_main([{**GREEN, "conclusion": "failure"}], SHA).ok
    assert not rf.check_main([GREEN], "b" * 40).ok
    assert not hasattr(rf, "check_nightly")


def test_issues_use_live_labels_only():
    p1 = {"number": 7, "state": "open", "labels": [{"name": "P1"}]}
    later = {"number": 8, "state": "open", "labels": [{"name": "P2"}, {"name": "v6"}]}
    pr = {"number": 9, "pull_request": {}, "labels": [{"name": "P0"}]}
    assert not rf.check_issues([p1]).ok and "#7 P1" in rf.check_issues([p1]).detail
    assert rf.check_issues([later, pr]).ok


def test_reduced_sheet_keeps_smoke_pending_and_accepts_the_rest(tmp_path):
    full = so.Sheet("4.1.0", [so.Row("C1", "Changed", "x", "y"), so.Row("OV-01", "Standing", "x", "y"),
                              so.Row("S1", "Smoke", "keep", "y")])
    sh = rf.reduced_sheet("4.1.0", full)
    ids = [r.id for r in sh.rows]
    assert ids[:5] == list(rf.SMOKE_IDS) and "C1" in ids and ids.count("S1") == 1
    assert all(r.result == "pending" for r in sh.rows if r.id in rf.SMOKE_IDS)
    c1 = sh.row("C1")
    assert c1.result == "accepted-blocked" and "accepted by automation" in c1.note and c1.problem() is None
    assert len(sh.open_rows()) == 5                      # only the smoke rows are left for the owner
    assert so.Sheet.parse(sh.render()).rows[0].id == "S1"   # the format the gate reads


def test_write_sheet_refuses_to_clobber_results(tmp_path):
    rf.write_sheet(tmp_path, "4.1.0")
    sh = so.load(tmp_path, "4.1.0")
    sh.set_result("S1", "pass")
    so.save(tmp_path, sh)
    try:
        rf.write_sheet(tmp_path, "4.1.0")
    except so.SheetError:
        pass
    else:
        raise AssertionError("must refuse")
    rf.write_sheet(tmp_path, "4.1.0", force=True)
    assert so.load(tmp_path, "4.1.0").row("S1").result == "pending"


def test_status_lists_what_remains_then_ready(tmp_path):
    checks = rf.collect(tmp_path, "4.1.0", fetch=fetcher(), head=SHA, now=NOW)
    todo = rf.remaining(checks)
    assert [c.name for c in checks if c.ok] == ["main green", "zero open P0-P2"]
    assert any("owner sheet" in t and t.startswith("[you]") for t in todo)
    assert "## What remains" in rf.report("4.1.0", checks, today="2026-10-26")
    rf.write_sheet(tmp_path, "4.1.0")
    sh = so.load(tmp_path, "4.1.0")
    for i in rf.SMOKE_IDS:
        sh.set_result(i, "pass")
    sh.sign("4.1.0-beta.3", SHA, today="2026-10-26", owner="Owner")
    so.save(tmp_path, sh)
    (tmp_path / "docs" / "release").mkdir(parents=True)
    assert rf.remaining(rf.collect(tmp_path, "4.1.0", fetch=fetcher(), head=SHA, now=NOW)) == []


def test_network_failure_is_a_todo_never_a_pass(tmp_path):
    def boom(kind):
        raise OSError("offline")
    checks = rf.collect(tmp_path, "4.1.0", fetch=boom, now=NOW)
    assert not any(c.ok for c in checks[:2])


def test_cli_sheet_and_status(tmp_path, home, monkeypatch, capsys):
    (tmp_path / "docs" / "reviews").mkdir(parents=True)
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "pyproject.toml").write_text('[project]\nversion = "4.1.0"\n')
    monkeypatch.setattr(rf, "github_fetch", fetcher())
    ap = argparse.ArgumentParser()
    release_cmd.add_cli(ap.add_subparsers(dest="cmd"))
    run = lambda *a: release_cmd.cmd_release(ap.parse_args(["release", "--repo", str(tmp_path), *a]), Config(), home)  # noqa: E731
    assert run("sheet") == 0 and (tmp_path / "docs/reviews/MANUAL_TEST_4.1.0.md").is_file()
    assert run("status", "--report") == 1
    out = capsys.readouterr().out
    assert "What remains" in out and (tmp_path / "docs/release/EVIDENCE_4.1.0.md").is_file()


def _iss(n, *labels, **kw):
    return {"number": n, "title": f"t{n}", "state": "open", "labels": [{"name": x} for x in labels], **kw}


def _load_gate():
    import importlib.util
    import sys
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("release_gate_t", Path(__file__).resolve().parents[1] / "tools" / "release_gate.py")
    gate = importlib.util.module_from_spec(spec)
    sys.modules["release_gate_t"] = gate
    spec.loader.exec_module(gate)
    return gate


def test_status_and_gate_share_one_issue_rule():
    """#535 / #518 item 5: known-issue-4.5 is skipped and printed, a bug without it counts, planned-work labels never skip a bug."""
    gate = _load_gate()
    accepted = [_iss(1, "P1", "bug", "known-issue-4.5"), _iss(2, "P2", "known-issue-4.5")]
    c = rf.check_issues(accepted)
    assert c.ok and "#1 P1" in c.detail and "known-issue-4.5" in c.detail
    mixed = accepted + [_iss(3, "P1", "bug"), _iss(4, "P1", "bug", "v7"), _iss(5, "P2", "bug", "4.5.1"),
                        _iss(6, "P0", "bug", "enhancement"), _iss(7, "P2", "v6"), _iss(8, "P2", "4.5.1"),
                        _iss(9, "P3", "bug"), _iss(10, "P1", pull_request={}), {**_iss(11, "P1"), "state": "closed"}]
    st = rf.check_issues(mixed)
    assert not st.ok
    blockers = st.detail.split("; accepted")[0]
    for n, s in ((3, "P1"), (4, "P1"), (5, "P2"), (6, "P0")):
        assert f"#{n} {s}" in blockers
    for n in (7, 8, 9, 10, 11):
        assert f"#{n} " not in blockers
    # the gate sees exactly the same blockers and accepted set
    assert [p.split(" issue ")[1].split(":")[0] for p in gate.issue_problems(mixed)] == ["#3", "#4", "#5", "#6"]
    assert gate.accepted_issues(mixed) == ["#1 (P1): t1", "#2 (P2): t2"]


def test_status_gh_failure_refuses_and_signed_sheet_required(tmp_path):
    def boom(kind):
        raise OSError("gh down")
    checks = rf.collect(tmp_path, "4.5.0", fetch=boom, head=SHA, now=NOW)
    assert not checks[1].ok and "could not read GitHub" in checks[1].detail
    ok = rf.collect(tmp_path, "4.5.0", fetch=fetcher(), head=SHA, now=NOW)
    sheet = [c for c in ok if c.name == "owner sheet"][0]
    assert not sheet.ok and "missing" in sheet.detail            # no signed sheet: not ready, whatever the issues say
    assert any(t.startswith("[you] owner sheet") for t in rf.remaining(ok))


def test_status_ignores_legacy_bug_tables_and_checklists(tmp_path):
    rev = tmp_path / "docs" / "reviews"
    rev.mkdir(parents=True)
    (rev / "X_BUG_TABLE.md").write_text("| id | sev | status |\n|---|---|---|\n| B2 | P1 | open |\n")
    (rev / "RELEASE_CHECKLIST_4.5.0.md").write_text("| 3 | thing | open |\n")
    checks = rf.collect(tmp_path, "4.5.0", fetch=fetcher(), head=SHA, now=NOW)
    assert [c.name for c in checks if c.ok] == ["main green", "zero open P0-P2"]
