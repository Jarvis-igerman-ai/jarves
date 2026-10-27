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


def test_gate_blocks_on_open_p0_p2_github_issues_but_not_planned_work(tmp_path):
    assert _gate(tmp_path, issues=[_issue(1, "P3", "bug"), _issue(2, "P1", "v7"), _issue(3, "P2", "enhancement"),
                                   _issue(4, "P1", "bug", pull_request={}), _issue(7, "P1", "4.5.1")]) == []
    # #499: a real defect that also carries a planned-work label is NOT skipped
    both = _gate(tmp_path, issues=[_issue(8, "P1", "bug", "v7"), _issue(9, "P2", "bug", "4.5.1"), _issue(10, "P0", "bug", "enhancement")])
    assert [p for p in both if "open P" in p] == ["open P1 issue #8: t8", "open P2 issue #9: t9", "open P0 issue #10: t10"]
    probs = _gate(tmp_path, issues=[_issue(5, "P0", "bug"), _issue(6, "P2", "ci")])
    assert [p for p in probs if "open P" in p] == ["open P0 issue #5: t5", "open P2 issue #6: t6"]


def test_known_issue_label_skips_only_with_exact_label_and_is_listed(tmp_path, capsys):
    acc = [_issue(1, "P2", "bug", "known-issue-4.5"), _issue(2, "P1", "bug", " Known-Issue-4.5 ")]
    assert [p for p in _gate(tmp_path, issues=acc) if "open P" in p] == []
    assert gate.accepted_issues(acc) == ["#1 (P2): t1", "#2 (P1): t2"]
    # a bug without it counts; planned-work labels alone never skip a bug; near-miss labels do not skip
    rest = [_issue(3, "P1", "bug"), _issue(4, "P1", "bug", "v5"), _issue(5, "P2", "bug", "4.5.1"),
            _issue(6, "P2", "bug", "known-issue-4.5.1"), _issue(7, "P2", "bug", "known-issue")]
    assert [p for p in _gate(tmp_path, issues=acc + rest) if "open P" in p] == [
        f"open {s} issue #{n}: t{n}" for n, s in ((3, "P1"), (4, "P1"), (5, "P2"), (6, "P2"), (7, "P2"))]
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
    monkeypatch.setattr(gate, "gh_issues", lambda repo: [_issue(1, "P2", "bug", "known-issue-4.5")])
    monkeypatch.setattr(gate, "gh_runs", lambda *a: [])
    rc = gate.main(["--tag", "v4.5.0", "--sha", SHA, "--repo-root", str(tmp_path), "--bug-tables", "docs/reviews/*BUG_TABLE*.md"])
    out = capsys.readouterr().out
    assert rc == 1                                            # no sheet and no green run still gate
    assert "--bug-tables no longer gates" in out and "legacy, not gating" in out and "bug B2 (P1)" in out
    assert "accepted known issues (1" in out
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
