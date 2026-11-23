"""Stable release gate: manual test sign-off sheet (round trip, sign-off, CLI), tools/manual_test_sheet.py rows and
tools/release_gate.py decisions. Offline, temp dirs, no git, no network."""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
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
    again.sign("4.1.0-beta.1", SHA, "2026-10-21", owner="Owner")
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


SIGNED = "owner: Igal\ndate: 2026-10-21\ncommit: " + SHA + "\n"


def _signed_root(tmp_path, sha=SHA):
    s = _sheet()
    s.set_result("C1", "pass")
    s.set_result("OI-02", "pass")
    s.sign("4.1.0-beta.3", sha, "2026-10-21", owner="Owner")
    so.save(tmp_path, s)
    return tmp_path


def _gate(tmp_path, **over):
    kw = dict(tag="v4.1.0", sha=SHA, verified_sha=SHA, build_version="4.1.0.77", root=_signed_root(tmp_path),
              checklist_text="| 1 | thing | done |\n", bug_tables=[],
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
    """#539: the tag commit (sha) differs from the tested commit (verified_sha = the sheet's commit)."""
    other = "b" * 40
    assert any("code changed" in p for p in _gate(tmp_path, sha=other, changed_files=lambda a, b: ["core/a.py"]))
    assert _gate(tmp_path, sha=other, changed_files=lambda a, b: ["docs/x.md"]) == []


def test_gate_blocks_on_sha_mismatch_open_p0_p2_and_tag_shape(tmp_path):
    assert any("differs from the verified" in p for p in _gate(tmp_path, verified_sha="c" * 40))
    # legacy checklist and bug-table rows are retired from the gate (owner 26 Oct, OD-S2): information only
    assert _gate(tmp_path, checklist_text="- P1 bug still open\n| 2 | thing | pending |\n") == []
    assert _gate(tmp_path, checklist_text=None) == []
    named = ("X_BUG_TABLE.md", "| id | sev | status |\n|---|---|---|\n| B2 | P1 | open |\n")
    assert _gate(tmp_path, bug_tables=["| B1 | P2 | open | x |\n", named]) == []
    shown = gate.legacy_report("| 2 | thing | pending |\n", [named])
    assert any("checklist row 2" in x for x in shown) and any(x.startswith("X_BUG_TABLE.md: bug B2 (P1)") for x in shown)
    assert any("not a stable tag" in p for p in _gate(tmp_path, tag="v4.1.0-beta.1"))
    assert any("no green jarves-checks" in p for p in _gate(tmp_path, checks_runs=[]))


def test_the_gate_has_no_nightly_requirement(tmp_path):
    """Owner 26 Oct: no nightly check at all, not even a stale one."""
    assert not hasattr(gate, "nightly_problems")
    assert _gate(tmp_path) == []


def _issue(n, *labels, **kw):
    return {"number": n, "title": f"t{n}", "state": "open", "labels": [{"name": x} for x in labels], **kw}


def test_gate_blocks_on_open_p0_and_p1_only_whatever_its_other_labels(tmp_path):
    # not blockers: P2, P3, pull requests. P0 and P1 block, even when labelled v7/enhancement/4.5.1 (owner rule 3 Nov)
    assert _gate(tmp_path, issues=[_issue(1, "P3", "bug"), _issue(4, "P1", "bug", pull_request={}), _issue(6, "P2", "bug")]) == []
    planned = _gate(tmp_path, issues=[_issue(2, "P1", "v7"), _issue(3, "P2", "enhancement"), _issue(7, "P1", "4.5.1")])
    assert [p for p in planned if "open P" in p] == ["open P1 issue #2: t2", "open P1 issue #7: t7"]
    both = _gate(tmp_path, issues=[_issue(8, "P1", "bug", "v7"), _issue(9, "P2", "bug", "4.5.1"), _issue(10, "P0", "bug", "enhancement")])
    assert [p for p in both if "open P" in p] == ["open P1 issue #8: t8", "open P0 issue #10: t10"]
    probs = _gate(tmp_path, issues=[_issue(5, "P0", "bug"), _issue(6, "P2", "ci")])
    assert [p for p in probs if "open P" in p] == ["open P0 issue #5: t5"]


def test_open_p2_p3_are_carried_in_the_report_not_blocking(capsys):
    iss = [_issue(1, "P2", "bug"), _issue(2, "P3"), _issue(3, "P1", pull_request={})]
    assert gate.carried_list(iss) == ["#1 (P2): t1", "#2 (P3): t2"]
    gate._print_carried(gate.carried_list(iss))
    assert "known issues carried into this release (2" in capsys.readouterr().out


def test_main_red_still_blocks_with_only_p2(tmp_path):
    red = [{"head_sha": SHA, "conclusion": "failure", "status": "completed"}]
    probs = _gate(tmp_path, issues=[_issue(1, "P2", "bug")], checks_runs=red)
    assert probs and not any("open P" in p for p in probs)


def test_known_issue_label_skips_only_with_exact_label_and_is_listed(tmp_path, capsys):
    acc = [_issue(1, "P1", "bug", "known-issue-4.5"), _issue(2, "P1", "bug", " Known-Issue-4.5 ")]
    assert [p for p in _gate(tmp_path, issues=acc) if "open P" in p] == []
    assert gate.accepted_issues(acc) == ["#1 (P1): t1", "#2 (P1): t2"]
    assert [p for p in _gate(tmp_path, issues=[_issue(9, "P0", "known-issue-4.5")]) if "open P" in p] == ["open P0 issue #9: t9"]
    # a bug without it counts; planned-work labels alone never skip a bug; near-miss labels do not skip
    rest = [_issue(3, "P1", "bug"), _issue(4, "P1", "bug", "v5"), _issue(5, "P1", "bug", "4.5.1"),
            _issue(6, "P1", "bug", "known-issue-4.5.1"), _issue(7, "P1", "bug", "known-issue")]
    assert [p for p in _gate(tmp_path, issues=acc + rest) if "open P" in p] == [
        f"open {s} issue #{n}: t{n}" for n, s in ((3, "P1"), (4, "P1"), (5, "P1"), (6, "P1"), (7, "P1"))]
    assert gate.accepted_issues(rest) == []
    gate._print_accepted(gate.accepted_issues(acc))
    assert "accepted known issues (2" in capsys.readouterr().out


def test_gate_refuses_when_gh_issues_cannot_be_read(tmp_path, monkeypatch, capsys):
    def boom(repo):
        raise OSError("gh down")
    monkeypatch.setattr(gate, "gh_issues", boom)
    monkeypatch.setattr(gate, "gh_runs", lambda *a: [])
    rc = gate.main(["--tag", "v4.5.0", "--sha", SHA, "--repo-root", str(tmp_path)])
    assert rc == 1 and "cannot read open issues" in capsys.readouterr().out


def test_one_signature_the_signed_sheet_is_enough_and_unsigned_is_refused(tmp_path):
    """Owner 26 Oct: ONE signature. There is no SIGNED_<version> file any more; the signed manual sheet is the signature."""
    assert not hasattr(gate, "signed_problems")
    assert _gate(tmp_path) == []
    assert any("sign-off" in p for p in gate.signoff_problems(tmp_path / "none", "4.1.0", SHA, lambda a, b: True, lambda a, b: []))


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


def test_a_later_version_needs_its_own_checklist_not_the_4_0_0_one(tmp_path):
    """The 4.0.0 checklist (SWV4_RELEASE_CHECKLIST.md) answered for every later version, so a 4.0.1 tag inherited its
    stale 'needs-owner' rows instead of being told its own checklist is missing (#89)."""
    rev = tmp_path / "docs" / "reviews"
    rev.mkdir(parents=True)
    (rev / "SWV4_RELEASE_CHECKLIST.md").write_text("| 1 | thing | needs-owner |\n", encoding="utf-8")
    assert gate.find_checklist(tmp_path, "4.0.0", None) == rev / "SWV4_RELEASE_CHECKLIST.md"
    assert gate.find_checklist(tmp_path, "4.0.1", None) is None
    (rev / "RELEASE_CHECKLIST_4.0.1.md").write_text("| 1 | thing | done |\n", encoding="utf-8")
    assert gate.find_checklist(tmp_path, "4.0.1", None) == rev / "RELEASE_CHECKLIST_4.0.1.md"
    assert _gate(tmp_path, checklist_text=None) == []          # a missing checklist no longer gates (retired)


def test_manual_sheet_is_one_row_per_entry_and_skips_what_the_app_cannot_show():
    """The 4.0.1 sheet had 84 rows: every sentence of a changelog entry became a row ("See docs/x.md."), and every
    changed guide and dashboard file had its own row although CI runs all of them. Now about 30 rows (#89)."""
    changelog = ("## 4.0.1 (unreleased)\n"
                 "- Added: release notes. Dashboard page Release notes in English and Hebrew. Local only, no network.\n"
                 "- Added: project manager for git projects. Real-agent runs unverified. See docs/plans/PM.md.\n"
                 "- Changed: CI runs the Windows tests in 3 shards.\n- Added: SECURITY.md, Dependabot, CodeQL.\n"
                 "- Docs: release and test system plan.\n")
    files = [f"core/src/jarves/guides/data/{g}.toml" for g in ("a", "b", "c")] + \
            [f"core/src/jarves/dashboard_static/{p}.js" for p in ("chat", "bugs", "app")]
    sh = sheet_tool.build_sheet("4.0.1", "win-v4.0.0.153", changelog, "", files)
    todo = {r.id: r.todo for r in sh.rows}
    assert sorted(todo) == ["C1", "C2", "G1", "P1"]
    assert todo["C1"].endswith("Added: release notes. Dashboard page Release notes in English and Hebrew.")
    assert todo["C2"].endswith("Added: project manager for git projects.")
    assert "3 changed guide(s)" in todo["G1"] and "a, b, c" in todo["G1"]
    assert todo["P1"].endswith("bugs, chat")                                  # app.js is the shell, not a page


# ---------------------------------------------------------------- manual_test_sheet: --since, --extra-rows, fallback
CL_REL = ("## 4.0.1 (unreleased)\n- Added: the project manager for git projects, off by default.\n\n"
          "## 4.0.0 (release candidate, not yet released)\n- Added: privacy hardening and the redaction audit.\n\n"
          "## 3.x\n- Added: something very old and long enough to be a row.\n")


def test_unknown_version_falls_back_to_previous_sections_with_a_note():
    items, note = sheet_tool.changelog_items_note(CL_REL, "5.0.0")
    assert items == ["Added: the project manager for git projects, off by default."]   # newest section below 5.0.0, not all
    assert "5.0.0" in note and "4.0.1" in note
    assert sheet_tool.changelog_items_note(CL_REL, "4.0.0")[1] == ""                    # exact match: no note
    sh = sheet_tool.build_sheet("5.0.0", None, CL_REL, "", [])
    assert "no CHANGELOG section" in sh.intro and sh.rows[0].id == "C1" and len(sh.rows) == 1


def test_since_version_takes_every_section_after_it():
    items, note = sheet_tool.changelog_items_note(CL_REL, "5.0.0", since="v3.0.0")
    assert len(items) == 2 and "privacy hardening" in items[1]
    assert len(sheet_tool.changelog_items_note(CL_REL, "5.0.0", since="4.0.0")[0]) == 1


def test_resolve_since_uses_nearest_tag_at_or_below():
    tags = ["v3.0.0", "v4.0.0", "win-v4.0.0.153"]
    assert sheet_tool.resolve_since(tags, "4.0.0") == ("v4.0.0", "")
    assert sheet_tool.resolve_since(tags, "v3.0.0")[0] == "v3.0.0"
    ref, note = sheet_tool.resolve_since(tags, "5.0.0")
    assert ref == "v4.0.0" and "5.0.0" in note
    ref, note = sheet_tool.resolve_since(tags, "1.0.0")
    assert ref is None and "baseline" in note
    assert sheet_tool.resolve_since(tags, "win-v4.0.0.153") == ("win-v4.0.0.153", "")


def test_extra_rows_numbered_and_table_are_marked_owner_only(tmp_path):
    f = tmp_path / "phone.md"
    f.write_text("# Phone\n\n1. On the PC run the thing.\n2. Optional: add to Home screen.\n\n"
                 "| Step | Emulated | Real |\n|---|---|---|\n| 1 | ok | camera |\n\n"
                 "| ID | Area | Do | Expected |\n|---|---|---|---|\n| Z9 | Phone | Scan | Pairs |\n", encoding="utf-8")
    rows = sheet_tool.extra_rows(f.read_text(encoding="utf-8"))
    assert [r.id for r in rows] == ["X01", "X02", "Z9"]
    assert all("real device" in r.todo.lower() or r.id == "Z9" for r in rows) and rows[0].area == "Owner-only"
    assert rows[2].area == "Owner-only" and rows[2].todo.endswith("Scan")
    with pytest.raises(ValueError):
        sheet_tool.extra_rows("1. a thing\n| X01 | A | b | c |\n")                    # duplicate id


def test_extra_rows_survive_the_sheet_round_trip_and_the_gate_reads_them(tmp_path):
    sh = sheet_tool.build_sheet("5.0.0", None, CL_REL, "", [],
                                extra=sheet_tool.extra_rows("1. Scan the QR code with the phone camera.\n"))
    assert sh.rows[-1].id == "X01"
    so.save(tmp_path, sh)
    again = so.load(tmp_path, "5.0.0")
    assert [r.id for r in again.rows] == ["C1", "X01"] and any("X01" in p for p in again.problems())


def test_main_baseline_without_resolvable_since_lists_tree_and_since_adds_rows(tmp_path):
    g = tmp_path / "core" / "src" / "jarves" / "guides" / "data"
    g.mkdir(parents=True)
    for n in ("a", "b"):
        (g / f"{n}.toml").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CL_REL, encoding="utf-8")
    (tmp_path / "ph.md").write_text("1. Scan the QR code.\n", encoding="utf-8")
    assert sheet_tool.main(["--version", "5.0.0", "--since", "1.0.0", "--extra-rows", str(tmp_path / "ph.md"),
                            "--repo-root", str(tmp_path)]) == 0
    sh = so.load(tmp_path, "5.0.0")
    ids = [r.id for r in sh.rows]
    assert "G1" in ids and "X01" in ids and "baseline run" in so.sheet_path(tmp_path, "5.0.0").read_text(encoding="utf-8")
    assert "2 changed guide(s)" in next(r.todo for r in sh.rows if r.id == "G1")


def test_cli_bug_tables_option_warns_and_legacy_never_gates(tmp_path, monkeypatch, capsys):
    """Slice 3: legacy rows are printed as 'legacy, not gating'; live issues are the only issue source."""
    (tmp_path / "docs" / "reviews").mkdir(parents=True)
    (tmp_path / "docs" / "reviews" / "X_BUG_TABLE.md").write_text("| id | sev | status |\n|---|---|---|\n| B2 | P1 | open |\n")
    monkeypatch.setattr(gate, "gh_issues", lambda repo: [_issue(1, "P1", "bug", "known-issue-4.5"), _issue(2, "P2", "bug")])
    monkeypatch.setattr(gate, "gh_runs", lambda *a: [])
    rc = gate.main(["--tag", "v4.5.0", "--sha", SHA, "--repo-root", str(tmp_path), "--bug-tables", "docs/reviews/*BUG_TABLE*.md"])
    out = capsys.readouterr().out
    assert rc == 1                                            # no sheet and no green run still gate
    assert "--bug-tables no longer gates" in out and "legacy, not gating" in out and "bug B2 (P1)" in out
    assert "accepted known issues (1" in out and "known issues carried into this release (1" in out
    assert "open P1 issue" not in out


def test_508_sign_needs_an_owner_and_a_removed_owner_or_hash_blocks_the_gate(tmp_path):
    from jarves import release_signoff as so2
    s = so2.Sheet(version="4.1.0", rows=[so2.Row("A1", "area", "todo", "expected", "pass")])
    with pytest.raises(so2.SheetError):
        s.sign("4.1.0-beta.1", SHA, "2026-10-21")                       # no owner
    s.sign("4.1.0-beta.1", SHA, "2026-10-21", owner="  The   Owner ")
    assert s.owner == "The Owner" and not s.problems()
    again = so2.Sheet.parse(s.render())
    assert again.owner == "The Owner" and again.rows_hash == s.rows_hash and not again.problems()
    again.owner = ""
    assert any("Owner" in p for p in again.problems())
    forged = so2.Sheet.parse(s.render().replace("expected", "changed"))
    assert any("rows changed after signing" in p for p in forged.problems())
    other = so2.Sheet.parse(s.render().replace("- Owner: The Owner", "- Owner: Someone Else"))
    assert any("rows changed after signing" in p for p in other.problems())         # sign-off fields are hashed too


def test_508_note_typed_in_the_finishing_run_survives_save_and_load(tmp_path):
    from jarves import release_signoff as so2
    so2.save(tmp_path, so2.Sheet(version="4.1.0", rows=[so2.Row("A1", "area", "todo", "expected")]))
    s = so2.load(tmp_path, "4.1.0")
    s.set_result("A1", "accepted-blocked", "no  phone\nhere | today")              # same run as --finish
    s.sign("4.1.0-beta.1", SHA, "2026-10-21", owner="Owner")
    so2.save(tmp_path, s)
    assert so2.load(tmp_path, "4.1.0").problems() == []


def test_606_owner_must_be_printable_notes_with_backslashes_and_nfd_text_round_trip(tmp_path):
    from jarves import release_signoff as so2
    bs = chr(92)
    row = so2.Row("A1", "area", "todo", "expected", "pass", "ends with a backslash" + bs)
    s = so2.Sheet(version="4.1.0", rows=[row, so2.Row("A2", "a", "t", "e", "pass", "pipe | and " + bs + "| both")])
    for bad in ("Own" + chr(0x202E) + "er", "Own" + chr(0x200B) + "er", "Own" + chr(0x7F) + "er"):
        with pytest.raises(so2.SheetError):
            s.sign("4.1.0-beta.1", SHA, "2026-10-21", owner=bad)
    s.sign("4.1.0-beta.1", SHA, "2026-10-21", owner="Igal")
    again = so2.Sheet.parse(s.render())
    assert again.rows[0].note == "ends with a backslash" + bs and again.rows[1].note == "pipe | and " + bs + "| both"
    assert again.rows_hash == s.rows_hash and not again.problems()
    import unicodedata
    row.note = "caf" + chr(0xE9)                                                  # an accented note, NFC
    s2 = so2.Sheet(version="4.1.0", rows=[row])
    s2.sign("4.1.0-beta.1", SHA, "2026-10-21", owner="Igal")
    nfd_text = unicodedata.normalize("NFD", s2.render())
    assert not so2.Sheet.parse(nfd_text).problems()                              # a rewrite in NFD does not void the signature


def test_gate_out_of_band_rows_hash(tmp_path):
    """#605: a hash recorded outside the sheet (tag annotation) must match; a recomputed hash in an edited sheet fails."""
    root = _signed_root(tmp_path)
    good = so.load(root, "4.1.0").rows_hash
    assert _gate(tmp_path, root=root, expect_rows_hash=good.upper()) == []
    bad = _gate(tmp_path, root=root, expect_rows_hash="0" * 64)
    assert any("differs from the hash recorded outside the sheet" in p for p in bad)
    assert any("not a sha256" in p for p in _gate(tmp_path, root=root, expect_rows_hash="xyz"))
    forged = _signed_root(tmp_path / "forged")
    s = so.load(forged, "4.1.0")
    s.set_result("C1", "fail")
    s.rows_hash = s.compute_rows_hash()                      # an editor recomputes the in-sheet hash: still caught
    so.save(forged, s)
    assert any("differs from the hash recorded outside" in p for p in _gate(tmp_path, root=forged, expect_rows_hash=good))


def test_release_workflow_publishes_only_inside_the_protected_environment():
    """#544: the secrets are read only by a step of a job that names environment stable-release for real publishes."""
    from pathlib import Path
    wf = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "environment: ${{" in wf and "'stable-release'" in wf
    env_line = next(ln for ln in wf.splitlines() if ln.strip().startswith("environment:"))
    assert "github.ref_type == 'tag'" in env_line and "dry_run == 'false'" in env_line
    assert "JARVES_EXPECT_ROWS_HASH" in wf and "JARVES_REQUIRE_ROWS_HASH=1" in wf and "git fetch --force origin" in wf


TAG_OID = "c" * 40
ROWS = "a" * 64
TAG_BODY = f"object {SHA}\ntype commit\ntag v4.1.0\ntagger o <o@x> 1 +0000\n\nRows hash: {ROWS}\n-----BEGIN SSH SIGNATURE-----\nU1NIU0lH\n-----END SSH SIGNATURE-----\n"


class _Run:
    def __init__(self, code=0, err="", target=SHA, body=TAG_BODY):
        self.code, self.err, self.target, self.calls, self.listed, self.envs = code, err, target, [], None, []
        self.body = body

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        self.envs.append(kw.get("env") or {})
        if "rev-parse" in cmd and cmd[-1].endswith("^{commit}"):  # the commit the verified tag object points at
            return subprocess.CompletedProcess(cmd, 0, self.target + "\n", "")
        if "cat-file" in cmd:                                    # the verified tag object's own name + message
            return subprocess.CompletedProcess(cmd, 0, self.body, "")
        if "rev-parse" in cmd:                                   # the tag object id, resolved once (review R2)
            return subprocess.CompletedProcess(cmd, 0, TAG_OID + "\n", "")
        f = next(c.split("=", 1)[1] for c in cmd if c.startswith("gpg.ssh.allowedSignersFile="))
        self.listed = Path(f).read_text(encoding="utf-8")       # the owner's lines reach git through a temp file
        return subprocess.CompletedProcess(cmd, self.code, "", self.err)


def test_605_verify_env_drops_injected_git_config(tmp_path, monkeypatch):
    """#605 review R3: inherited GIT_CONFIG_* variables never reach verify-tag; system git config is ignored."""
    for k, v in (("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "gpg.ssh.program"), ("GIT_CONFIG_VALUE_0", "/bin/true"),
                 ("GIT_CONFIG_PARAMETERS", "'gpg.minTrustLevel'='undefined'"), ("GIT_CONFIG_GLOBAL", "/tmp/x")):
        monkeypatch.setenv(k, v)
    ok = _Run(0, 'Good "git" signature for owner with ED25519 key SHA256:x\n')
    assert gate.tag_signature_problems(tmp_path, "v4.1.0", "owner ssh-ed25519 AAAA", SHA, run=ok, verifier=sys.executable) == []
    assert ok.envs and all(e.get("GIT_CONFIG_NOSYSTEM") == "1" for e in ok.envs)
    assert all(e.get("GIT_CONFIG_GLOBAL") == os.devnull for e in ok.envs)         # PR #1643 review R3: no ~/.gitconfig
    assert not [k for e in ok.envs for k in e if k.startswith("GIT_CONFIG") and k not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")]


def test_605_rows_hash_line_is_anchored(tmp_path):
    """PR #1643 review R2: the gate and release.yml read 'Rows hash:' by the same rule, a whole line of 64 hex
    (any case, trailing blanks allowed); a longer hex run or trailing text is no hash."""
    ok_err = 'Good "git" signature for owner with ED25519 key SHA256:x\n'
    for tail, passes in (("  ", True), ("0", False), (" extra", False)):
        run = _Run(0, ok_err, body=TAG_BODY.replace(f"Rows hash: {ROWS}", f"Rows hash: {ROWS.upper()}{tail}"))
        got = gate.tag_signature_problems(tmp_path, "v4.1.0", "o ssh-ed25519 A", SHA, run=run, verifier=sys.executable,
                                          rows_hash=ROWS)
        assert (got == []) is passes, (tail, got)
    wf = (Path(__file__).resolve().parents[2] / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "grep -m1 -iE '^Rows hash: [0-9a-f]{64}[[:blank:]]*$'" in wf


def test_605_releasing_doc_signing_example_uses_a_fido_key():
    """PR #1643 review R1: the copy-paste signing example must not set up the plain key file the custody paragraph
    says an agent can use to pass the gate."""
    doc = (Path(__file__).resolve().parents[2] / "docs/RELEASING.md").read_text(encoding="utf-8")
    assert "user.signingkey ~/.ssh/id_ed25519.pub" not in doc
    assert "ssh-keygen -t ed25519-sk" in doc and "user.signingkey ~/.ssh/id_ed25519_sk.pub" in doc


@pytest.mark.linux_only      # runs release.yml's own grep pipeline in bash (the release job runs on ubuntu)
def test_605_workflow_rows_hash_extractor_is_anchored():
    """PR #1643 review R2: release.yml's extractor accepts exactly the lines the gate accepts."""
    wf = (Path(__file__).resolve().parents[2] / ".github/workflows/release.yml").read_text(encoding="utf-8")
    pipe = re.search(r"\| (grep -m1 -iE .*?) \|\| true\)", wf).group(1)
    for tail, want in (("  ", ROWS), ("0", ""), (" extra", "")):
        r = subprocess.run(["bash", "-c", f"printf 'x\\nRows hash: %s\\n' \"$H\" | {pipe} || true"], capture_output=True,
                           text=True, env={"PATH": os.environ.get("PATH", ""), "H": ROWS.upper() + tail})
        assert r.stdout.strip() == want, (tail, r.stdout, r.stderr)


def test_605_tag_signature_by_owner_key(tmp_path):
    """#605: the tag's 'Rows hash' is authentic only when the tag is signed by a key in the owner's allowed-signers
    lines (kept outside the repo). A bad tag name, missing verifier, git error or bad/unlisted signature refuse."""
    v, lines = sys.executable, "owner ssh-ed25519 AAAA"          # an existing file stands in for ssh-keygen here
    ok = _Run(0, 'Good "git" signature for owner with ED25519 key SHA256:x\n')
    assert gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=ok, verifier=v) == []
    cmd = ok.calls[1]
    assert ok.calls[0][-1] == "refs/tags/v4.1.0" and all(e.get("LC_ALL") == "C" for e in ok.envs)
    assert cmd[-2:] == ["verify-tag", TAG_OID] and ok.listed == lines + "\n"      # R2: the ref is resolved once,
    assert ok.calls[2][-1] == TAG_OID + "^{commit}" and ok.calls[3][1:] == ["cat-file", "tag", TAG_OID]  # same object
    assert "gpg.minTrustLevel=fully" in cmd and f"gpg.ssh.program={v}" in cmd and gate.SSH_KEYGEN == "/usr/bin/ssh-keygen"
    assert "gpg.openpgp.program=/nonexistent" in cmd and "gpg.x509.program=/nonexistent" in cmd   # only SSH verifies
    absent = gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=ok, verifier=str(tmp_path / "none"))
    assert len(absent) == 1 and "verifier" in absent[0] and len(ok.calls) == 4        # refused before running git
    stranger = 'Good "git" signature with ED25519 key SHA256:y\nNo principal matched.\n'     # exit 0, unlisted key
    for run in (_Run(0, stranger), _Run(1, "error: no signature found\n" + "x" * 900)):
        bad = gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=run, verifier=v)
        assert len(bad) == 1 and "not signed by the owner" in bad[0] and len(bad[0]) < 500
        assert len(run.calls) == 2                                   # no commit rev-parse after a failed verify-tag
    moved = gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=_Run(0, ok.err, "b" * 40), verifier=v)
    assert len(moved) == 1 and "not at the gated commit" in moved[0]              # owner-signed, other commit
    assert "not a stable tag" in gate.tag_signature_problems(tmp_path, "main;rm", lines, SHA, run=ok, verifier=v)[0]

    def boom(cmd, **kw):
        raise FileNotFoundError("git")
    assert "cannot verify" in gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=boom, verifier=v)[0]

    def late_boom(cmd, **kw):                                         # verify-tag passes, rev-parse cannot run
        if cmd[-1].endswith("^{commit}"):
            raise subprocess.TimeoutExpired(cmd, 60)
        return ok(cmd, **kw)
    assert "cannot resolve the signed tag" in gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA,
                                                                          run=late_boom, verifier=v)[0]
    assert gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=ok, verifier=v, rows_hash=ROWS.upper()) == []
    other = gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=ok, verifier=v, rows_hash="b" * 64)
    assert len(other) == 1 and "differs from the one given" in other[0]          # review R2: hash from the signed object
    nohash = _Run(0, ok.err, body=TAG_BODY.replace("Rows hash: ", "Rows: "))
    assert "(none)" in gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=nohash, verifier=v, rows_hash=ROWS)[0]
    for body in (TAG_BODY + "Rows hash: " + "d" * 64 + "\n", TAG_BODY.split("-----BEGIN")[0]):  # appended / no block
        out = gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=_Run(0, ok.err, body=body), verifier=v)
        assert len(out) == 1 and "outside its signed part" in out[0], out
    renamed = _Run(0, ok.err, body=TAG_BODY.replace("tag v4.1.0", "tag v4.0.9"))  # signed for another version
    assert "named 'v4.0.9', not v4.1.0" in gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=renamed, verifier=v)[0]

    def cat_boom(cmd, **kw):
        if "cat-file" in cmd:
            raise subprocess.TimeoutExpired(cmd, 60)
        return ok(cmd, **kw)
    assert "cannot read the signed tag" in gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=cat_boom, verifier=v)[0]
    missing = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, "", "fatal: Needed a single revision")  # noqa: E731
    assert "does not exist" in gate.tag_signature_problems(tmp_path, "v4.1.0", lines, SHA, run=missing, verifier=v)[0]


def test_605_fallback_signers_variable_is_never_trusted(tmp_path, monkeypatch, capsys):
    """#605 review R1: vars.RELEASE_ALLOWED_SIGNERS falls back to a repository/organization variable when the
    stable-release one is missing. A no-environment job reports such a fallback; a real publish verifies the tag only
    when it reports exactly "false" (repository or org variable present, or the result missing: refused)."""
    monkeypatch.setattr(gate, "gh_issues", lambda repo: [])
    monkeypatch.setattr(gate, "gh_runs", lambda *a: [])
    seen = []
    monkeypatch.setattr(gate, "tag_signature_problems", lambda root, tag, s, sha, rows_hash=None: seen.append(s) or [])
    monkeypatch.setenv("JARVES_ALLOWED_SIGNERS", "repo-or-org ssh-ed25519 AAAA")
    argv = ["--tag", "v4.5.0", "--sha", SHA, "--repo-root", str(tmp_path), "--require-rows-hash"]
    for fb in ("true", "", " TRUE ", "False"):
        monkeypatch.setenv("JARVES_SIGNERS_FALLBACK", fb)
        assert gate.main(argv) == 1 and "repository or organization variable" in capsys.readouterr().out and seen == []
    gate.main(argv[:-1])                                                           # dry run: a note, never verified
    assert "tag signature not checked" in capsys.readouterr().out and seen == []
    monkeypatch.setenv("JARVES_SIGNERS_FALLBACK", "false")
    gate.main(argv)
    assert "repository or organization variable" not in capsys.readouterr().out and seen == ["repo-or-org ssh-ed25519 AAAA"]
    import yaml
    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    fj = doc["jobs"]["signers-fallback"]
    assert "environment" not in fj and fj["permissions"] == {}                    # sees only repository/org variables
    check = fj["steps"][0]
    assert check["env"]["FALLBACK"] == "${{ vars.RELEASE_ALLOWED_SIGNERS }}"
    assert fj["outputs"]["present"] == "${{ steps.check.outputs.present }}" and check["id"] == "check"
    assert doc["jobs"]["stable"]["needs"] == "signers-fallback"
    gate_step = next(s for s in doc["jobs"]["stable"]["steps"] if "release_gate.py" in s.get("run", ""))
    assert gate_step["env"]["JARVES_SIGNERS_FALLBACK"] == "${{ needs.signers-fallback.outputs.present }}"


@pytest.mark.linux_only      # runs the job's bash line; the job runs on ubuntu (review R1: no WSL bash on Windows)
def test_605_fallback_job_line_reports_presence(tmp_path):
    """#605 review R1: the signers-fallback job's own shell line writes present=true/false to GITHUB_OUTPUT."""
    import yaml                                    # pyyaml is a declared core dev dependency (core/pyproject.toml)
    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    check = doc["jobs"]["signers-fallback"]["steps"][0]
    for val, want in (("x", "true"), ("", "false")):
        out = tmp_path / "o"
        out.write_text("", encoding="utf-8")
        r = subprocess.run(["bash", "-c", check["run"]], capture_output=True, text=True,
                           env={"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                                "FALLBACK": val, "GITHUB_OUTPUT": str(out)})
        assert r.returncode == 0 and out.read_text(encoding="utf-8").strip() == f"present={want}", r.stderr


def test_605_real_publish_requires_owner_signed_tag(tmp_path, monkeypatch, capsys):
    """#605: a real publish refuses without the owner's signers list (fail closed); with it the gate checks the tag;
    the workflow passes the owner-only variable to the gate step, on a tag ref only."""
    monkeypatch.setattr(gate, "gh_issues", lambda repo: [])
    monkeypatch.setattr(gate, "gh_runs", lambda *a: [])
    seen = []
    monkeypatch.setattr(gate, "tag_signature_problems", lambda root, tag, s, sha, rows_hash=None: seen.append((s, sha)) or ["sign-off: SIGFAIL"])
    argv = ["--tag", "v4.5.0", "--sha", SHA, "--repo-root", str(tmp_path)]
    monkeypatch.setenv("JARVES_ALLOWED_SIGNERS", "  ")                             # blank = unset
    assert gate.main(argv + ["--require-rows-hash"]) == 1
    assert "sign-off: no owner allowed-signers list" in capsys.readouterr().out and seen == []
    gate.main(argv)                                                                # dry run: a note, not silence
    out = capsys.readouterr().out
    assert "tag signature not checked" in out and "no owner allowed-signers list (stable" not in out
    monkeypatch.setenv("JARVES_ALLOWED_SIGNERS", "owner ssh-ed25519 AAAA")
    monkeypatch.setenv("JARVES_SIGNERS_FALLBACK", "false")
    assert gate.main(argv) == 1 and "SIGFAIL" in capsys.readouterr().out and seen == [("owner ssh-ed25519 AAAA", SHA)]
    import yaml
    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    step = next(s for j in doc["jobs"].values() for s in j.get("steps", []) if "release_gate.py" in s.get("run", ""))
    assert step["env"]["JARVES_ALLOWED_SIGNERS"] == "${{ github.ref_type == 'tag' && vars.RELEASE_ALLOWED_SIGNERS || '' }}"
    job = next(j for j in doc["jobs"].values() if step in j.get("steps", []))     # the gate runs in the job that uses
    assert "'stable-release'" in job["environment"]                               # the owner-approved environment


@pytest.mark.linux_only      # the release gate runs on ubuntu-24.04 (release.yml); CI Core shards run it
def test_605_real_ssh_tag_signature_owner_key_only(tmp_path, monkeypatch):  # needs git >= 2.32 (GIT_CONFIG_GLOBAL)
    """#605: with real git + ssh-keygen a tag signed by the owner's listed key passes; a good signature by an
    unlisted key (git verify-tag can exit 0 with "No principal matched") is refused."""
    for k, val in (("HOME", str(tmp_path)), ("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_CONFIG_GLOBAL", str(tmp_path / "gc"))):
        monkeypatch.setenv(k, val)                                   # no runner/user git config leaks in

    def sh(*cmd, cwd=tmp_path):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        assert r.returncode == 0, f"{cmd[:2]}: {r.stderr[-400:]}"
    for who in ("owner", "stranger"):
        sh(gate.SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", who, "-f", str(tmp_path / who))
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ("git", "-c", "user.name=o", "-c", "user.email=o@x")
    sh("git", "init", "-q", cwd=repo)
    sh(*git, "commit", "-q", "--allow-empty", "-m", "c", cwd=repo)
    for who, tag in (("owner", "v4.1.0"), ("stranger", "v4.1.1")):
        sh(*git, "-c", "gpg.format=ssh", "-c", f"gpg.ssh.program={gate.SSH_KEYGEN}", "-c",
           f"user.signingkey={tmp_path / who}", "tag", "-s", tag, "-m", "Rows hash: " + "a" * 64, cwd=repo)
    sh("git", "tag", "v4.1.2", cwd=repo)                                          # lightweight: no signature
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    sh(*git, "commit", "-q", "--allow-empty", "-m", "later", cwd=repo)            # owner-signed tag on ANOTHER commit
    sh(*git, "-c", "gpg.format=ssh", "-c", f"gpg.ssh.program={gate.SSH_KEYGEN}", "-c",
       f"user.signingkey={tmp_path / 'owner'}", "tag", "-s", "v4.1.3", "-m", "Rows hash: " + "b" * 64, cwd=repo)
    lines = 'owner namespaces="git" ' + (tmp_path / "owner.pub").read_text(encoding="utf-8")
    assert gate.tag_signature_problems(repo, "v4.1.0", lines, head) == []
    assert gate.tag_signature_problems(repo, "v4.1.0", lines, head, rows_hash="a" * 64) == []
    assert "differs from the one given" in gate.tag_signature_problems(repo, "v4.1.0", lines, head, rows_hash="c" * 64)[0]
    for tag in ("v4.1.1", "v4.1.2"):
        bad = gate.tag_signature_problems(repo, tag, lines, head)
        assert len(bad) == 1 and "not signed by the owner" in bad[0], bad
    import yaml                                     # the workflow's own extraction still reads the signed tag message
    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    run = next(s["run"] for j in doc["jobs"].values() for s in j.get("steps", []) if "Rows hash: [0-9a-f]{64}" in s.get("run", ""))
    extract = next(ln.strip() for ln in run.splitlines() if ln.strip().startswith("h=$(git for-each-ref"))
    out = subprocess.run(["bash", "-c", extract + '; printf %s "$h"'], cwd=repo, capture_output=True, text=True,
                         env={**os.environ, "TAG": "v4.1.0"})
    assert out.returncode == 0 and out.stdout == "a" * 64, out
    moved = gate.tag_signature_problems(repo, "v4.1.3", lines, head)
    assert len(moved) == 1 and "not at the gated commit" in moved[0], moved
    # PR #1643 council blocker: a line appended AFTER the signature block is not signed; verify-tag still passes
    sh(*git, "-c", "gpg.format=ssh", "-c", f"gpg.ssh.program={gate.SSH_KEYGEN}", "-c",
       f"user.signingkey={tmp_path / 'owner'}", "tag", "-s", "v4.1.4", "-m", "no hash here", head, cwd=repo)
    obj = subprocess.run(["git", "cat-file", "tag", "v4.1.4"], cwd=repo, capture_output=True, text=True).stdout
    forged = subprocess.run(["git", "hash-object", "-t", "tag", "-w", "--stdin"], cwd=repo, capture_output=True,
                            text=True, input=obj + "Rows hash: " + "d" * 64 + "\n").stdout.strip()
    sh("git", "update-ref", "refs/tags/v4.1.4", forged, cwd=repo)
    bad = gate.tag_signature_problems(repo, "v4.1.4", lines, head, rows_hash="d" * 64)
    assert len(bad) == 1 and "signed tag" in bad[0], bad


@pytest.mark.linux_only      # the release gate runs on ubuntu-24.04 (release.yml), like the real-ssh test above
@pytest.mark.parametrize("dry", ["false", "true"])
def test_605_gate_step_requires_rows_hash_on_a_real_publish(tmp_path, monkeypatch, capsys, dry):
    """#605 review R2: run the workflow's own rows-hash step line, load what it writes to $GITHUB_ENV into the
    gate's process, call the gate with the gate step's flags only: a real publish (DRY_RUN=false) resolves
    require_rows_hash and refuses without the owner's signers list; a dry run only prints a note."""
    import yaml
    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    steps = [s for j in doc["jobs"].values() for s in j.get("steps", [])]
    rows = next(s["run"] for s in steps if "JARVES_REQUIRE_ROWS_HASH=1" in s.get("run", ""))
    line = next(ln.strip() for ln in rows.splitlines() if "JARVES_REQUIRE_ROWS_HASH=1" in ln)
    gate_step = next(s for s in steps if "release_gate.py" in s.get("run", ""))
    assert "JARVES_REQUIRE_ROWS_HASH" not in (gate_step.get("env") or {})   # nothing in the gate step overrides it
    envf = tmp_path / "github_env"
    envf.write_text("", encoding="utf-8")
    r = subprocess.run(["bash", "-c", line], capture_output=True, text=True,
                       env={"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                            "DRY_RUN": dry, "GITHUB_ENV": str(envf)})
    assert r.returncode == 0, r.stderr
    monkeypatch.delenv("JARVES_REQUIRE_ROWS_HASH", raising=False)
    for kv in envf.read_text(encoding="utf-8").split():
        monkeypatch.setenv(*kv.split("=", 1))
    monkeypatch.setenv("JARVES_ALLOWED_SIGNERS", "")
    monkeypatch.setattr(gate, "gh_issues", lambda repo: [])
    monkeypatch.setattr(gate, "gh_runs", lambda *a: [])
    flags = [a for a in gate_step["run"].split() if a.startswith("--")]
    assert "--require-rows-hash" not in flags and "--dry-run" not in flags          # the env value is the only switch
    code = gate.main(["--tag", "v4.5.0", "--sha", SHA, "--repo-root", str(tmp_path)])
    out = capsys.readouterr().out
    if dry == "false":
        assert code == 1 and "sign-off: no owner allowed-signers list" in out
    else:
        assert "tag signature not checked" in out and "no owner allowed-signers list (stable" not in out
