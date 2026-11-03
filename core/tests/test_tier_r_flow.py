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
         "head_sha": SHA, "authors": ["author1"], "author_agents": ["agent-impl"], "checks_green": True, "checks_pending": False,
         "merge_state": "CLEAN", "queued": False, "comments": [],
         "ledger": {"ok": True, "why": "", "review_task": "T-9", "review_agent": "agent-rev"}}
    d.update(k)
    return d


def c(body, assoc="MEMBER", login="igalgerman"):
    return {"body": body, "login": login, "association": assoc}


def so(sha=SHA, who="agent-rev", model="opus-5-5", task="T-9", assoc="MEMBER"):
    return c(f"SIGN-OFF {sha}\nReview-Head: {sha}\nReview-Task: {task}\nReviewer-Agent: {who}\nReviewer-Model: {model}", assoc)


def cr(sha, assoc="MEMBER"):
    return c(f"CHANGES REQUESTED {sha}\nReview-Head: {sha}\nReview-Task: T-8\nReviewer-Agent: agent-rev\nReviewer-Model: opus-5-5", assoc)


def act(**k):
    return t.decide(pr(**k))["action"]


def test_green_without_verdict_gets_review():
    assert act() == "review"


def test_verdict_for_other_sha_does_not_count():
    assert act(comments=[so(OLD)]) == "review"


def test_signoff_on_head_queues():
    assert act(comments=[so()]) == "queue"


def test_untrusted_commenter_cannot_sign_off():
    assert act(comments=[so(assoc="NONE")]) == "review"
    assert act(comments=[so(assoc="CONTRIBUTOR")]) == "review"
    assert act(comments=[so(assoc="FIRST_TIME_CONTRIBUTOR")]) == "review"
    assert act(comments=["SIGN-OFF " + SHA + " Reviewer-Agent: x"]) == "review"      # a bare string has no author: ignored


def test_untrusted_changes_requested_does_not_cancel_or_count():
    assert act(comments=[so(), cr(SHA, assoc="NONE")]) == "queue"
    assert act(comments=[cr(OLD, "NONE"), cr(OLDER, "NONE")]) == "review"


def test_later_changes_requested_cancels_earlier_signoff():
    assert act(comments=[so(), cr(SHA)]) == "fix"
    assert act(comments=[cr(SHA), so()]) == "queue"          # the latest verdict on the head wins


def test_quoted_or_buried_verdict_is_ignored():
    assert act(comments=[c("> SIGN-OFF " + SHA + "\nReview-Head: " + SHA)]) == "review"
    assert act(comments=[c("I reviewed it.\nSIGN-OFF " + SHA)]) == "review"
    assert act(comments=[c("SIGN-OFF " + SHA[:12])]) == "review"                    # short sha is not machine-readable


def test_signoff_needs_markers_opus_and_independent_agent():
    assert act(comments=[so(model="sonnet-5-5")]) == "owner"
    assert act(comments=[c("SIGN-OFF " + SHA + "\nReviewer-Model: opus-5-5")]) == "owner"          # no head/task/agent markers
    assert act(comments=[c(f"SIGN-OFF {SHA}\nReview-Head: {OLD}\nReview-Task: T\nReviewer-Agent: r\nReviewer-Model: opus")]) == "owner"
    assert act(comments=[so(who="agent-impl")]) == "owner"                           # reviewer is the declared author agent
    assert act(comments=[so(who="author1")]) == "owner"                              # or a commit author login
    assert act(author_agents=[], comments=[so()]) == "owner"                         # no Author-Agent declared: cannot check


def test_changes_requested_then_fix_then_rereview():
    assert act(comments=[cr(SHA)]) == "fix"
    assert act(comments=[cr(OLD)]) == "review"


def test_two_rounds_then_owner():
    assert act(comments=[cr(OLD), cr(SHA)]) == "owner"
    assert act(comments=[cr(OLDER), cr(OLD)]) == "owner"
    # a sign-off in a third round does not reopen the flow
    assert act(comments=[cr(OLDER), cr(OLD), so()]) == "owner"
    assert "2 review rounds" in t.decide(pr(comments=[cr(OLDER), cr(OLD)]))["reasons"][0]
    assert act(comments=[cr(OLD)]) == "review"                                       # one round used: still allowed


def test_red_conflict_pending():
    assert act(checks_green=False) == "fix"
    assert act(merge_state="DIRTY") == "fix"
    assert act(checks_pending=True, checks_green=False) == "wait"


def test_main_red_and_queued_wait():
    assert t.decide(pr(comments=[so()]), main_red=True)["action"] == "wait"
    assert act(queued=True, comments=[so()]) == "wait"


def test_excluded_always_owner():
    for f in (".github/workflows/ci.yml", "CLAUDE.md", "core/tools/release_gate.py", "core/src/jarves/secrets_x.py",
              "core/src/jarves/approvals.py", ".claude/settings.json",
              "core/src/jarves/release_cmd.py", "core/src/jarves/release_flow.py", "core/src/jarves/release_run.py",
              "core/src/jarves/release_signoff.py", "core/src/jarves/approval_keys.py", "core/src/jarves/approval_popup.py",
              "core/src/jarves/drive_mode.py", "core/src/jarves/skills/__init__.py", "core/tools/release_notes.py",
              "core/src/jarves/dashboard_static/signoff_steps.js"):
        assert act(files=[f], comments=[so()]) == "owner", f
    assert act(title="stable release 6.0", comments=[so()]) == "owner"
    assert act(files_truncated=True, comments=[so()]) == "owner"


def test_unknown_fails_closed():
    assert act(files=[], comments=[so()]) == "owner"
    assert act(files=None, comments=[so()]) == "owner"


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


def test_excluded_case_insensitive_and_variants():
    for f in ("app/src/ApprovalsUi.kt", "docs/RELEASING.md", "tools/GitHub-Approve.py", "core/src/jarves/SignOff.py",
              "core/src/jarves/Gate.py", "CORE/TOOLS/Release_Gate.py", "core\\src\\Approvals.py"):
        assert act(files=[f], comments=[so()]) == "owner", f


def test_author_agent_first_token_and_roles_unknown():
    assert t.AUTHOR_AGENT.findall("Author-Agent: workflow-bugs-sonnet (Tier R flow author)") == ["workflow-bugs-sonnet"]
    assert t.author_agents(["workflow-bugs-sonnet", "Agent-X (note)"]) == ["agent-x", "workflow-bugs-sonnet"]
    assert t.author_agents(["workflow-bugs", "Claude", "unknown", ""]) == []
    assert act(author_agents=["workflow-bugs"], comments=[so()]) == "owner"
    assert act(author_agents=["workflow-bugs-sonnet"], comments=[so()]) == "queue"
    assert act(author_agents=["agent-rev (extra)"], comments=[so()]) == "owner"


def test_verdict_forms():
    tail = f"\nReview-Head: {SHA}\nReview-Task: T-9\nReviewer-Agent: agent-rev\nReviewer-Model: opus-5-5"
    for first in (f"SIGN-OFF {SHA}", f"VERDICT: SIGN-OFF {SHA}"):
        assert act(comments=[c(first + tail)]) == "queue", first
    for first in (f"CHANGES REQUESTED {SHA}", f"VERDICT: CHANGES REQUESTED {SHA}"):
        assert act(comments=[c(first + tail)]) == "fix", first
    assert act(comments=[c(f"VERDICT: SIGN-OFF {SHA[:12]}" + tail)]) == "review"
    assert act(comments=[c("note\nVERDICT: SIGN-OFF " + SHA + tail)]) == "review"


def _wf(task, owners):
    def wf(args, home):
        if args[0] == "task":
            if task is None:
                raise RuntimeError("not found")
            return task
        return owners
    return wf


def test_ledger_unverified_signoff_goes_to_owner():
    assert act(comments=[so()], ledger=None) == "owner"
    assert act(comments=[so()], ledger={"ok": False, "why": "x"}) == "owner"
    assert act(comments=[so(task="T-1")]) == "owner"          # ledger facts are for another task
    assert act(comments=[so(who="other-rev")]) == "owner"     # ledger agent differs from the declared reviewer


def test_ledger_facts_checks_task_agent_and_claims():
    ok = t.ledger_facts(5, "b", "T-9", "Agent-Rev", wf=_wf({"agent_id": "agent-rev"}, []))
    assert ok["ok"] and ok["review_agent"] == "agent-rev"
    assert not t.ledger_facts(5, "b", "T-9", "agent-rev", wf=_wf(None, []))["ok"]
    assert not t.ledger_facts(5, "b", "T-9", "agent-rev", wf=_wf({"agent_id": "someone"}, []))["ok"]
    assert not t.ledger_facts(5, "b", "T-9", "agent-rev", wf=_wf({"agent_id": "agent-rev"}, [{"agent_id": "agent-rev"}]))["ok"]
    assert not t.ledger_facts(5, "b", "", "agent-rev", wf=_wf({"agent_id": "agent-rev"}, []))["ok"]
