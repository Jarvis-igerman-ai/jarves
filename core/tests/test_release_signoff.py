"""Stable release gate: manual test sign-off sheet (round trip, sign-off, CLI), tools/manual_test_sheet.py rows and
tools/release_gate.py decisions. Offline, temp dirs, no git, no network."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jarves import release_cmd, release_signoff as so
from jarves.config import Config

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "core" / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


gate = _tool("release_gate")
sheet_tool = _tool("manual_test_sheet")


def _sheet(version="4.1.0"):
    return so.Sheet(version=version, intro="x", rows=[so.Row("C1", "Changed", "Try a | pipe", "works"),
                                                      so.Row("OI-02", "Standing", "install", "ok")])


def test_sheet_round_trip_and_sign_off(tmp_path):
    s = _sheet()
    so.save(tmp_path, s)
    again = so.load(tmp_path, "v4.1.0")
    assert [r.todo for r in again.rows] == ["Try a | pipe", "install"] and again.problems()
    with pytest.raises(so.SheetError):
        again.sign("4.1.0-beta.1", SHA)                       # rows still pending
    again.set_result("c1", "pass")
    with pytest.raises(so.SheetError):
        again.set_result("OI-02", "accepted-blocked")        # needs a note
    again.set_result("OI-02", "accepted-blocked", "no second PC")
    with pytest.raises(so.SheetError):
        again.sign("4.1.0", SHA)                              # not a beta label
    with pytest.raises(so.SheetError):
        again.sign("4.1.0-beta.1", "abc")                     # not a full SHA
    again.sign("4.1.0-beta.1", SHA, "2026-10-21")
    so.save(tmp_path, again)
    done = so.load(tmp_path, "4.1.0")
    assert done.problems() == [] and done.commit == SHA and done.date == "2026-10-21"
    done.set_result("C1", "fail")
    assert done.signed is False and done.problems()           # any later change voids the signature


def test_fail_and_blocked_stop_the_release():
    s = _sheet()
    s.set_result("C1", "fail")
    s.set_result("OI-02", "blocked")
    assert len(s.open_rows()) == 2


def test_bad_versions_are_rejected():
    for v in ("4.1", "latest", "4.1.0.5", ""):
        with pytest.raises(so.SheetError):
            so.normalise_version(v)
    assert so.normalise_version("win-v4.1.0") == "4.1.0"


def test_tested_sha_rules():
    anc = lambda a, b: True  # noqa: E731
    assert so.tested_sha_ok(SHA, SHA, anc, lambda a, b: ["core/x.py"]) is None
    assert so.tested_sha_ok(SHA, "b" * 40, anc, lambda a, b: ["docs/a.md", "README.md"]) is None
    assert "code changed" in so.tested_sha_ok(SHA, "b" * 40, anc, lambda a, b: ["docs/a.md", "core/x.py"])
    assert "not an ancestor" in so.tested_sha_ok(SHA, "b" * 40, lambda a, b: False, lambda a, b: [])


def test_manual_sheet_rows_from_changes_and_standing_rows():
    wp = "| OI-02 | install | steps here | expected here | x |\n| OV-01 | voice | say hi | hears it | x |\n"
    changelog = "## 4.1.0\n- Added the beta channel.\n- Fixed the timer.\n\n## 4.0.0\n- old\n"
    sh = sheet_tool.build_sheet("4.1.0", "v4.0.0", changelog, wp,
                                ["core/src/jarves/guides/data/beta_channel.toml", "core/src/jarves/dashboard_static/updates.js",
                                 "core/src/jarves/dashboard_static/app.js"])
    ids = [r.id for r in sh.rows]
    assert "C1" in ids and "G1" in ids and "P1" in ids and "OI-02" in ids and "OV-01" in ids
    assert not any("old" in r.todo for r in sh.rows)


def test_release_cli_records_rows_and_finishes(tmp_path, capsys, monkeypatch):
    (tmp_path / "core").mkdir()
    so.save(tmp_path, _sheet())
    home = tmp_path / "home"
    home.mkdir()
    ap = argparse.ArgumentParser()
    release_cmd.add_cli(ap.add_subparsers(dest="cmd"))
    run = lambda *a: release_cmd.cmd_release(ap.parse_args(["release", "signoff", "--repo", str(tmp_path), *a]), Config(), home)  # noqa: E731
    assert run("--version", "4.1.0") == 1                                        # nothing done yet
    assert run("--version", "4.1.0", "--row", "C1", "--result", "pass") == 1
    assert run("--version", "4.1.0", "--row", "OI-02", "--result", "accepted-blocked", "--note", "n/a here") == 1      # still open: finish not asked
    assert run("--version", "4.1.0", "--finish", "--beta", "4.1.0-beta.2", "--commit", SHA) == 0
    assert so.load(tmp_path, "4.1.0").problems() == []
    assert run("--version", "9.9.9") == 1


# ---------------------------------------------------------------------- the gate
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def _nights(n=5, bad=None):
    return [{"event": "schedule", "status": "completed", "conclusion": "failure" if i == bad else "success",
             "created_at": (NOW - timedelta(days=i, hours=10)).isoformat()} for i in range(n)]


def _signed_root(tmp_path, sha=SHA):
    s = _sheet()
    s.set_result("C1", "pass")
    s.set_result("OI-02", "pass")
    s.sign("4.1.0-beta.3", sha, "2026-10-21")
    so.save(tmp_path, s)
    return tmp_path


def _gate(tmp_path, **over):
    kw = dict(tag="v4.1.0", sha=SHA, verified_sha=SHA, build_version="4.1.0.77", root=_signed_root(tmp_path),
              checklist_text="| 1 | thing | done |\n", bug_tables=[], nightly_runs=_nights(),
              checks_runs=[{"head_sha": SHA, "conclusion": "success", "event": "push"}],
              is_ancestor=lambda a, b: True, changed_files=lambda a, b: [], now=NOW)
    kw.update(over)
    return gate.run_gate(**kw)


def test_gate_passes_when_everything_is_in_place(tmp_path):
    assert _gate(tmp_path) == []


def test_gate_refuses_without_manual_sign_off(tmp_path):
    assert any("no sign-off sheet" in p for p in _gate(tmp_path, root=tmp_path / "empty"))
    s = _sheet()
    so.save(tmp_path / "u", s)
    assert any("sign-off" in p for p in _gate(tmp_path, root=tmp_path / "u"))


def test_gate_requires_tested_beta_sha_equals_tag_or_docs_only(tmp_path):
    other = "b" * 40
    assert any("code changed" in p for p in _gate(tmp_path, sha=other, verified_sha=other,
                                                  checks_runs=[{"head_sha": other, "conclusion": "success"}],
                                                  changed_files=lambda a, b: ["core/a.py"]))
    assert _gate(tmp_path, sha=other, verified_sha=other, checks_runs=[{"head_sha": other, "conclusion": "success"}],
                 changed_files=lambda a, b: ["docs/x.md"]) == []


def test_gate_blocks_on_sha_mismatch_open_p0_p2_nightly_and_tag_shape(tmp_path):
    assert any("differs from the verified" in p for p in _gate(tmp_path, verified_sha="c" * 40))
    assert any("open P0-P2" in p for p in _gate(tmp_path, checklist_text="- P1 bug still open\n"))
    assert _gate(tmp_path, checklist_text="- zero open P0-P2\n") == []
    assert any("checklist row 2" in p for p in _gate(tmp_path, checklist_text="| 2 | thing | pending |\n"))
    assert any("bug B1" in p for p in _gate(tmp_path, bug_tables=["| B1 | P2 | open | x |\n"]))
    named = _gate(tmp_path, bug_tables=[("X_BUG_TABLE.md", "| id | sev | status |\n|---|---|---|\n| B2 | P1 | open |\n")])
    assert any(p.startswith("X_BUG_TABLE.md: bug B2 (P1)") for p in named)
    assert any("nightly not green" in p for p in _gate(tmp_path, nightly_runs=_nights(bad=2)))
    assert any("only 3" in p for p in _gate(tmp_path, nightly_runs=_nights(3)))
    assert any("not a stable tag" in p for p in _gate(tmp_path, tag="v4.1.0-beta.1"))
    assert any("no green jarves-checks" in p for p in _gate(tmp_path, checks_runs=[]))


# The bug tables in docs/reviews use different column orders. A fixed `| id | sev | status |` position read the area,
# the scenario or the symptom as the status, so the stable gate listed 41 "open" P0-P2 rows (6 real) and could never pass.
COLUMNS_BY_HEADER = """\
| id | sev | area | status | fix PR |
|---|---|---|---|---|
| A1 | P1 | self-check shutdown | fixed (main) | #9 |
| A2 | P2 | repo names | open | - |

| ID | Sev | Scenario | Finding | Status |
|---|---|---|---|---|
| C1 | P2 | 29 log_privacy | facts logged | FIXED (curator.py); test |
| C2 | P2 (test harness) | 11-14 | tools shown as 'a tool' | fixed-awaiting-retest |
| C3 | P1 | 30 | still wrong | prompt fixed; needs live verification |

| ID | Sev | Where | Symptom | Fix | Retest |
|---|---|---|---|---|---|
| D1 | P1 | Home | says off | health loop | Home status follows. |
| D2 | P2 | Jobs | English | m3.js | |
| D3 | P2 | Undo | lost | pages.js | FAIL: undo still disabled |
| D4 | P3 | icons | csp | svg | |

| Class | Meaning here |
|---|---|
| P1 | not a bug row: this table has no severity column |
"""


def test_bug_tables_are_read_by_their_header_not_by_position():
    found = gate.bug_table_open(COLUMNS_BY_HEADER, "T.md")
    assert found == ["T.md: bug A2 (P2) is 'open'",
                     "T.md: bug C3 (P1) is 'prompt fixed; needs live verification'",
                     "T.md: bug D2 (P2) is ''",
                     "T.md: bug D3 (P2) is 'FAIL: undo still disabled'"]
    assert gate.bug_table_open("| B1 | P2 | open | x |\n") == ["bug B1 (P2) is 'open'"]     # header-less: old order
    no_status = "| id | sev | area |\n|---|---|---|\n| E1 | P0 | crash |\n"
    assert gate.bug_table_open(no_status) == ["bug E1 (P0) has no status column to show it is closed"]


def test_every_bug_table_in_the_repo_has_a_status_the_gate_can_read():
    """A new bug table must keep a Sev and a Status (or Retest) column, or the stable gate cannot see it is closed."""
    tables = sorted((ROOT / "docs" / "reviews").glob("*BUG_TABLE*.md"))
    assert tables
    unreadable = [p for t in tables for p in gate.bug_table_open(t.read_text(encoding="utf-8"), t.name)
                  if "has no status column" in p]
    assert unreadable == []
