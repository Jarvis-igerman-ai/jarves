"""Hermetic tests for core/tools/factory_watchdog.py: fake gh runner, frozen clock, no network."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import factory_watchdog as fw  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
OK = [{"name": "CI result", "status": "COMPLETED", "conclusion": "SUCCESS"},
      {"name": "Auto-label policy", "status": "COMPLETED", "conclusion": "SUCCESS"}]


def pr(n, rollup=OK, labels=(), **kw):
    d = {"number": n, "title": f"pr {n}", "isDraft": False, "labels": [{"name": x} for x in labels],
         "mergeStateStatus": "CLEAN", "autoMergeRequest": None, "isInMergeQueue": False, "statusCheckRollup": rollup}
    d.update(kw)
    return d


class FakeGh(fw.GhRunner):
    def __init__(self, prs, issues=(), main=None, rels=(), dry_run=True):
        super().__init__(dry_run=dry_run)
        self.prs, self.issues, self.main_run, self.rels = prs, list(issues), main, list(rels)
        self.calls: list[list[str]] = []

    def run(self, args):
        self.calls.append([str(a) for a in args])
        if self.check(args):
            if self.dry_run:
                return ""
            self.writes.append([str(a) for a in args])
            return ""
        return ""

    def json(self, args):
        self.calls.append([str(a) for a in args])
        self.check(args)
        a = [str(x) for x in args]
        if a[0] == "pr" and "merged" in a:
            return []
        if a[0] == "pr":
            return self.prs
        if a[0] == "run" and "--branch" in a:
            return [self.main_run] if self.main_run else []
        if a[0] == "run":
            return [{"attempt": 2}, {"attempt": 1}]
        if a[0] == "issue":
            return self.issues
        return self.rels


def issues(**counts):
    out, i = [], 0
    for k, v in counts.items():
        for _ in range(v):
            i += 1
            out.append({"number": i, "labels": [{"name": k}]})
    return out


def run(gh, tmp_path, *flags):
    sf = tmp_path / "STATE.md"
    code = fw.main([*flags, "--state-file", str(sf)], gh=gh, now=NOW)
    return code, sf


def test_requeue_dropped_green_pr_apply(tmp_path):
    gh = FakeGh([pr(1)], dry_run=False)
    code, sf = run(gh, tmp_path, "--apply")
    assert ["pr", "merge", "1", "--auto", "--repo=" + fw.DEFAULT_REPO] in gh.writes
    assert code == 0
    assert "| 2026-10-27 |" in sf.read_text(encoding="utf-8")


def test_dry_run_changes_nothing(tmp_path):
    gh = FakeGh([pr(1), pr(2, rollup=OK[:1])])
    code, sf = run(gh, tmp_path)
    assert gh.writes == [] and not sf.exists() and not (tmp_path / "STATE.watchdog.json").exists()


def test_no_automerge_label_skipped_for_queue(tmp_path):
    gh = FakeGh([pr(1, labels=["no-automerge"])], dry_run=False)
    run(gh, tmp_path, "--apply")
    assert gh.writes == []


def test_missing_policy_check_toggles_label(tmp_path):
    gh = FakeGh([pr(5, rollup=OK[:1])], dry_run=False)
    run(gh, tmp_path, "--apply")
    assert gh.writes == [["pr", "edit", "5", "--add-label", "no-automerge", "--repo=" + fw.DEFAULT_REPO],
                         ["pr", "edit", "5", "--remove-label", "no-automerge", "--repo=" + fw.DEFAULT_REPO]]


def test_queued_draft_red_pending_not_requeued(tmp_path):
    red = [{"name": "CI result", "status": "COMPLETED", "conclusion": "FAILURE", "completedAt": "2026-10-27T11:00:00Z"}]
    pend = [{"name": "CI result", "status": "IN_PROGRESS", "conclusion": ""}]
    gh = FakeGh([pr(1, autoMergeRequest={"x": 1}), pr(2, isDraft=True), pr(3, rollup=red), pr(4, rollup=pend)], dry_run=False)
    code, sf = run(gh, tmp_path, "--apply")
    assert gh.writes == [] and code == 0


def test_alerts_exit_1(tmp_path):
    red = [{"name": "CI result", "status": "COMPLETED", "conclusion": "FAILURE", "completedAt": "2026-10-27T08:00:00Z"}]
    main = {"status": "completed", "conclusion": "failure", "updatedAt": "2026-10-27T11:00:00Z"}
    gh = FakeGh([pr(3, rollup=red), pr(4, mergeStateStatus="BLOCKED", autoMergeRequest={"x": 1})],
                issues=issues(P1=4, P2=3), main=main)
    code, _ = run(gh, tmp_path)
    assert code == 1
    text = "\n".join(fw.analyse(fw.collect(gh, fw.DEFAULT_REPO, NOW.date()), NOW, {})["alerts"])
    for s in ("main is red", "PR #3 red for more than", "PR #4 stuck BLOCKED", "open P0-P2 is 7"):
        assert s in text


def test_no_beta_after_green_main_alert():
    main = {"status": "completed", "conclusion": "success", "updatedAt": "2026-10-27T10:00:00Z"}
    old = [{"tagName": "v1-beta.1", "isPrerelease": True, "publishedAt": "2026-10-26T10:00:00Z"}]
    res = fw.analyse(fw.collect(FakeGh([], main=main, rels=old), fw.DEFAULT_REPO, NOW.date()), NOW, {})
    assert any("no beta newer" in a for a in res["alerts"])
    new = old + [{"tagName": "v1-beta.2", "isPrerelease": True, "publishedAt": "2026-10-27T10:30:00Z"}]
    res = fw.analyse(fw.collect(FakeGh([], main=main, rels=new), fw.DEFAULT_REPO, NOW.date()), NOW, {})
    assert res["alerts"] == [] and res["hours_beta"] == 0.5


def test_requeue_three_times_alert_and_idempotent(tmp_path):
    gh = FakeGh([pr(1)], dry_run=False)
    for _ in range(3):
        run(gh, tmp_path, "--apply")
    assert len(gh.writes) == 3
    code, sf = run(gh, tmp_path, "--apply")
    assert len(gh.writes) == 3 and code == 1  # not re-queued a fourth time, alert raised
    text = sf.read_text(encoding="utf-8")
    assert text.count("| 2026-10-27 |") == 1 and text.count(fw.NOW_START) == 1


def test_second_run_idempotent_state(tmp_path):
    gh = FakeGh([pr(1, autoMergeRequest={"x": 1})], dry_run=False)
    _, sf = run(gh, tmp_path, "--apply")
    first = sf.read_text(encoding="utf-8")
    run(gh, tmp_path, "--apply")
    assert sf.read_text(encoding="utf-8") == first
    assert first.startswith(fw.NOW_START)


@pytest.mark.parametrize("argv", [
    ["pr", "merge", "1", "--squash", "--repo=x"], ["pr", "merge", "1", "--auto"], ["pr", "close", "1"],
    ["pr", "reopen", "1"], ["run", "rerun", "5"], ["issue", "edit", "3", "--add-label", "P0"],
    ["issue", "close", "3"], ["release", "delete", "v1"], ["api", "-X", "DELETE", "x"],
    ["pr", "edit", "1", "--add-label", "P0", "--repo=x"], ["workflow", "run", "x"], ["repo", "edit"],
    ["pr", "comment", "1"], ["pr", "merge", "1", "--admin", "--auto", "--repo=x"],
])
def test_never_act_list(argv):
    with pytest.raises(fw.NeverActError):
        fw.GhRunner(dry_run=False, exe="definitely-not-gh").run(argv)


def test_read_failure_exits_1(tmp_path, capsys):
    class Boom(FakeGh):
        def json(self, args):
            raise RuntimeError("no network")
    code, _ = run(Boom([]), tmp_path)
    assert code == 1 and "NEEDS: gh read failed" in capsys.readouterr().out
