"""Hermetic tests for core/tools/tier_r_flow.py (no network, no clock)."""
import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "tools" / "tier_r_flow.py"
_s = importlib.util.spec_from_file_location("tier_r_flow_t", _p)
t = importlib.util.module_from_spec(_s)
_s.loader.exec_module(t)

SHA = "a" * 40
OLD = "b" * 40
OLDER = "c" * 40


def pr(**k):
    d = {"number": 1, "title": "fix thing", "tier": "R", "draft": False, "state": "OPEN", "files": ["core/src/jarves/browser_x.py"],
         "head_sha": SHA, "authors": ["author1"], "checks_green": True, "checks_pending": False,
         "merge_state": "CLEAN", "queued": False, "comments": []}
    d.update(k)
    return d


def so(sha=SHA, who="rev1", model="opus-5-5"):
    return f"SIGN-OFF head {sha[:12]} reviewer {who} model {model}"


def cr(sha):
    return f"CHANGES REQUESTED head {sha[:12]} reviewer rev1 model opus-5-5"


def act(**k):
    return t.decide(pr(**k))["action"]


def test_green_without_verdict_gets_review():
    assert act() == "review"


def test_verdict_for_other_sha_does_not_count():
    assert act(comments=[so(OLD)]) == "review"


def test_signoff_on_head_queues():
    assert act(comments=[so()]) == "queue"


def test_changes_requested_then_fix_then_rereview():
    assert act(comments=[cr(SHA)]) == "fix"
    assert act(comments=[cr(OLD)]) == "review"


def test_two_rounds_then_owner():
    assert act(comments=[cr(OLD), cr(SHA)]) == "owner"
    assert act(comments=[cr(OLDER), cr(OLD)]) == "owner"


def test_red_conflict_pending():
    assert act(checks_green=False) == "fix"
    assert act(merge_state="DIRTY") == "fix"
    assert act(checks_pending=True, checks_green=False) == "wait"


def test_reviewer_rules():
    assert act(comments=[so(model="sonnet-5-5")]) == "owner"
    assert act(comments=[so(who="author1")]) == "owner"
    assert act(comments=["SIGN-OFF head " + SHA[:12]]) == "owner"


def test_main_red_and_queued_wait():
    assert t.decide(pr(comments=[so()]), main_red=True)["action"] == "wait"
    assert act(queued=True, comments=[so()]) == "wait"


def test_excluded_always_owner():
    for f in (".github/workflows/ci.yml", "CLAUDE.md", "core/tools/release_gate.py", "core/src/jarves/secrets_x.py",
              "core/src/jarves/approvals.py", ".claude/settings.json"):
        assert act(files=[f], comments=[so()]) == "owner", f
    assert act(title="stable release 6.0", comments=[so()]) == "owner"
    assert act(files_truncated=True, comments=[so()]) == "owner"


def test_non_tier_r_is_not_this_flow():
    assert act(tier="N") == "wait"


def test_backlog_thresholds():
    many = [pr(number=i, checks_green=False) for i in range(8)]
    b = t.backlog(many)
    assert b["backlog"] and b["line"] == "merge backlog: 8 PRs" and b["pause_new_feature_work"]
    assert not t.backlog(many[:7])["backlog"]
    assert t.backlog([pr(number=i) for i in range(3)])["backlog"]
    assert not t.backlog([pr(number=i, queued=True) for i in range(3)])["backlog"]
    assert not t.backlog([pr(number=i, draft=True) for i in range(9)])["backlog"]


def test_focus_respects_worker_limit_and_order():
    prs = [pr(number=1), pr(number=2, comments=[so()]), pr(number=3, checks_green=False), pr(number=4)]
    f = t.focus(prs, max_workers=2)
    assert f["backlog"] and len(f["merge_pipeline_work"]) == 2
    assert [w["action"] for w in f["merge_pipeline_work"]] == ["queue", "fix"]
    assert f["priority"][0].startswith("P0/P1")
    quiet = t.focus([pr()], 2)
    assert quiet["merge_pipeline_work"] == [] and not quiet["pause_new_feature_work"]
