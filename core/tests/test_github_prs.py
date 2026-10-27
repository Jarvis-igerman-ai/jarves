"""GitHub writes class 2 (V7 G4, #411): PR open/update/comment/review, CI re-run. FakeGitHub, fake approvals, no network."""
from __future__ import annotations

import pathlib
from types import SimpleNamespace as NS

import pytest

from jarves import github_client as gc
from jarves import github_prs as gp
from jarves import github_write as gw
from test_github_issues import REPO, TOKEN, Feats, mk, run, writes


def go(w, kind, number="", repo=REPO, origin="user", **payload):
    return run(w.execute(gp.action(kind, repo, number, **payload), origin=origin))


OPEN = dict(title="Fix", body="why", head="fix-1", base="main")


def test_open_pr_posts_exact_payload_and_never_merges(tmp_path):
    w, ap, fake = mk(tmp_path)
    r = go(w, "pr.open", draft=True, **OPEN)
    assert (r.ok, r.data) == (True, {"number": 8, "url": "https://github.com/octo/r/pull/8"})
    assert writes(fake) == [("POST", "/repos/octo/r/pulls")]
    assert fake.bodies == [{**OPEN, "draft": True}]
    p, kw = ap.asked[0]
    assert "(DRAFT)" in p and "never merged" in p and kw["risk"] == "high"


def test_update_comment_review_rerun_hit_only_their_endpoints(tmp_path):
    w, ap, fake = mk(tmp_path)
    w.feats = type("F", (), {"active": lambda self, f: f in (gw.FEATURE, gw.APPROVE_FEATURE)})()
    assert go(w, "pr.update", "2", title="New", state="closed").ok
    assert go(w, "pr.comment", "2", body="hi").ok
    assert go(w, "pr.review", "2", event="COMMENT", body="looks ok").ok
    assert go(w, "pr.review", "2", event="APPROVE").ok
    assert go(w, "ci.rerun", "12345678901", failed_only=True).ok
    assert go(w, "ci.rerun", "12345678901", failed_only=False).ok
    assert writes(fake) == [("PATCH", "/repos/octo/r/pulls/2"), ("POST", "/repos/octo/r/issues/2/comments"),
                            ("POST", "/repos/octo/r/pulls/2/reviews"), ("POST", "/repos/octo/r/pulls/2/reviews"),
                            ("POST", "/repos/octo/r/actions/runs/12345678901/rerun-failed-jobs"),
                            ("POST", "/repos/octo/r/actions/runs/12345678901/rerun")]
    assert "closes the pull request" in ap.asked[0][0] and "APPROVE can satisfy" in ap.asked[3][0]


def test_issue_number_is_refused_as_pr_target_after_approval(tmp_path):
    w, ap, fake = mk(tmp_path)
    r = go(w, "pr.comment", "5", body="hi")              # 5 is a plain issue
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_BAD_ARGUMENT") and not writes(fake)
    assert "NOT a pull request" in ap.asked[0][0]


@pytest.mark.parametrize("kind,number,payload", [
    ("pr.open", "", {"title": "t", "head": "a", "base": "a"}),                       # same branch
    ("pr.open", "", {"title": "t", "head": "a:b", "base": "main"}),                  # fork syntax
    ("pr.open", "", {"title": "t", "head": "-x", "base": "main"}),
    ("pr.open", "", {"title": "t", "head": "a"}),                                    # base missing
    ("pr.open", "", {**OPEN, "maintainer_can_modify": True}),                        # unknown field
    ("pr.open", "", {**OPEN, "draft": "yes"}),
    ("pr.open", "", {**OPEN, "body": "x" * 801}),
    ("pr.open", "", {**OPEN, "body": "hi @everyone"}),
    ("pr.open", "", {**OPEN, "title": "a‮b"}),
    ("pr.open", "3", OPEN),
    ("pr.update", "", {"title": "t"}),
    ("pr.update", "2", {"state": "merged"}),
    ("pr.update", "2", {"base": "main"}),
    ("pr.update", "2", {"merge_commit_sha": "x"}),
    ("pr.review", "2", {"event": "MERGE", "body": "x"}),
    ("pr.review", "2", {"event": "COMMENT"}),
    ("pr.review", "2", {"event": "COMMENT", "body": "x", "comments": [{"path": "a"}]}),
    ("pr.review", "2", {"event": "COMMENT", "body": "x", "commit_id": "abc"}),
    ("ci.rerun", "12", {}),
    ("ci.rerun", "12", {"failed_only": "true"}),
    ("ci.rerun", "../1", {"failed_only": True}),
    ("ci.rerun", "1234567890123456", {"failed_only": True}),
    ("pr.comment", "2", {"body": ""}),
])
def test_bad_payloads_refused_before_the_owner_is_asked(tmp_path, kind, number, payload):
    w, ap, fake = mk(tmp_path)
    r = run(w.execute(gp.action(kind, REPO, number, **payload), origin="user"))
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_BAD_ARGUMENT") and not ap.asked and not writes(fake)


@pytest.mark.parametrize("kind", ["pr.merge", "pr.merge.direct", "merge", "merge.squash", "pr.force", "push.force",
                                  "branch.protection", "ruleset.edit", "secrets.set", "org.invite", "permissions.edit",
                                  "ci.dispatch.secrets", "pr.admin_merge"])
def test_forbidden_kinds_never_reach_the_owner_or_github(tmp_path, kind):
    w, ap, fake = mk(tmp_path)
    r = run(w.execute(gw.WriteAction(kind=kind, repo=REPO, target="2", payload={"a": 1}, preview="x"), origin="user"))
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_FORBIDDEN") and not ap.asked and not fake.calls


def test_write_door_has_no_merge_protection_secret_or_dispatch_endpoint():
    ok = gc._write_allowed
    for m, p in [("PUT", "/repos/o/r/pulls/2/merge"), ("POST", "/repos/o/r/pulls/2/merge"),
                 ("PUT", "/repos/o/r/pulls/2/update-branch"), ("POST", "/repos/o/r/merges"), ("POST", "/graphql"),
                 ("PUT", "/repos/o/r/branches/main/protection"), ("PUT", "/repos/o/r/actions/secrets/X"),
                 ("POST", "/repos/o/r/actions/workflows/1/dispatches"), ("POST", "/repos/o/r/actions/runs/1/cancel"),
                 ("POST", "/repos/o/r/actions/runs/1/force-cancel"), ("DELETE", "/repos/o/r/pulls/2"),
                 ("DELETE", "/repos/o/r/git/refs/heads/x"), ("POST", "/repos/o/r/git/refs"),
                 ("PATCH", "/repos/o/r/git/refs/heads/main"), ("POST", "/repos/o/r/pulls/2/reviews/9/dismissals"),
                 ("PUT", "/repos/o/r/pulls/2/reviews/9"), ("POST", "/repos/o/r/pulls/2/comments"),
                 ("POST", "/repos/o/r/pulls/2/requested_reviewers"), ("PATCH", "/repos/o/r/pulls/../branches"),
                 ("POST", "/repos/o/r/actions/runs/%2e/rerun"), ("POST", "/repos/o/r/actions/runs/1/rerun/x"),
                 ("PATCH", "/repos/o/r"), ("POST", "/orgs/o/repos")]:
        assert not ok(m, p), (m, p)
    for m, p in [("POST", "/repos/o/r/pulls"), ("PATCH", "/repos/o/r/pulls/2"), ("POST", "/repos/o/r/pulls/2/reviews"),
                 ("POST", "/repos/o/r/actions/runs/123/rerun"), ("POST", "/repos/o/r/actions/runs/123/rerun-failed-jobs")]:
        assert ok(m, p), (m, p)


def test_client_write_rejects_merge_even_if_a_handler_asked_for_it():
    c = gc.GitHubClient(token=TOKEN, transport=gc.FakeGitHub().transport)
    with pytest.raises(gc.GitHubError) as e:
        c.write("PUT", "/repos/o/r/pulls/2/merge", {"merge_method": "squash"})
    assert e.value.code == "GITHUB_BAD_ARGUMENT"


def test_all_gates_apply_to_pr_kinds(tmp_path):
    w, ap, fake = mk(tmp_path, repos=())
    assert go(w, "pr.open", **OPEN).code == "GITHUB_WRITE_REPO"
    w, ap, fake = mk(tmp_path / "b")
    r = run(w.execute(gp.action("pr.open", REPO, **OPEN), origin="user", untrusted=True))
    assert r.code == "GITHUB_WRITE_UNTRUSTED" and not writes(fake)
    w, ap, fake = mk(tmp_path / "c", answer=False)
    assert go(w, "pr.open", **OPEN).code == "GITHUB_WRITE_DENIED" and not writes(fake)
    fk = gc.FakeGitHub()
    fk.push = False
    w, ap, fake = mk(tmp_path / "d", fake=fk)
    assert go(w, "pr.open", **OPEN).code == "GITHUB_WRITE_SCOPE" and not ap.asked


def test_switch_off_refuses(tmp_path):
    w, ap, fake = mk(tmp_path)
    w.feats = type("Off", (Feats,), {"active": lambda self, fid: False})()
    assert go(w, "ci.rerun", "9", failed_only=True).code == "GITHUB_WRITE_OFF" and not fake.calls


def test_duplicate_and_rate_limit_and_audit_chain(tmp_path):
    w, ap, fake = mk(tmp_path, clock=lambda: 1_790_000_000.0)
    assert go(w, "pr.comment", "2", body="x").ok
    assert go(w, "pr.comment", "2", body="x").code == "GITHUB_WRITE_DUPLICATE"
    for i in range(gw.MAX_WRITES - 1):
        assert go(w, "pr.comment", "2", body=f"c{i}").ok
    assert go(w, "pr.comment", "2", body="over").code == "GITHUB_WRITE_RATE_LIMIT"
    rows, _ = gw.read_chain(w._path)
    assert len(rows) >= gw.MAX_WRITES and "over" not in str(rows)


def test_oversized_action_refused_not_truncated(tmp_path):
    w, ap, fake = mk(tmp_path)
    r = go(w, "pr.open", title="t" * 200, body="b" * 800, head="h" * 190, base="b" * 190)
    assert not r.ok and not writes(fake)


def test_github_permission_denied_after_approval_names_the_needed_permission(tmp_path):
    fk = gc.FakeGitHub()
    w, ap, fake = mk(tmp_path, fake=fk)
    fk.write_fail = (403, {})
    r = go(w, "ci.rerun", "9", failed_only=False)
    assert r.code == "GITHUB_WRITE_PERMISSION" and "Actions: write" in r.message


def test_cli_refuses_in_agent_session_and_bad_usage(tmp_path, monkeypatch, capsys):
    a = NS(write_action="open", number=None, repo=REPO, title="t", body=None, state=None, head="x", base="main", event=None,
           draft=False, failed_only=False)
    cfg = NS(github_repos=[REPO])
    monkeypatch.setattr(gw, "agent_session_reason", lambda *x, **k: "agent")
    assert gp.cmd_github_pr(a, cfg, tmp_path) == 1
    assert "GITHUB_WRITE_AGENT_SESSION" in capsys.readouterr().out
    assert gp.cmd_github_pr(NS(**{**a.__dict__, "write_action": "merge"}), cfg, tmp_path) == 2
    assert gp.cmd_github_pr(NS(**{**a.__dict__, "number": "2"}), cfg, tmp_path) == 2     # open takes no number
    assert gp.cmd_github_pr(NS(**{**a.__dict__, "write_action": "rerun"}), cfg, tmp_path) == 2


def test_no_model_tool_uses_pr_writes():
    src = pathlib.Path(gp.__file__).parent
    allowed = {"github_prs.py", "github_write.py", "github_issues.py", "github_client.py", "setup_wizard.py"}
    for f in src.rglob("*.py"):
        if f.name not in allowed:
            assert "github_prs" not in f.read_text(encoding="utf-8", errors="ignore"), f.name


# ---------------------------------------------------------------------------------------------- #578 hardening
def _pr_writer(tmp_path, **kw):
    from test_github_write import mk
    return mk(tmp_path, **kw)


def test_approve_needs_its_own_switch_comments_do_not(tmp_path):
    from test_github_write import FakeFeats, run
    w, ap, fake = _pr_writer(tmp_path)
    on = {gw.FEATURE: True}
    w.feats = type("F", (), {"active": lambda self, f: on.get(f, False)})()
    appr = gp.action("pr.review", "octo/r", "2", event="APPROVE")
    r = run(w.execute(appr, origin="user"))
    assert r.code == "GITHUB_WRITE_APPROVE_OFF" and ap.asked == [] and not fake.bodies
    assert run(w.execute(gp.action("pr.review", "octo/r", "2", event="COMMENT", body="looks fine"), origin="user")).ok
    assert run(w.execute(gp.action("pr.review", "octo/r", "2", event="REQUEST_CHANGES", body="fix it"), origin="user")).ok
    on[gw.APPROVE_FEATURE] = True
    assert run(w.execute(appr, origin="user")).ok
    assert "APPROVE can satisfy a required review" in ap.asked[-1][0]


def test_approve_switch_is_registered_off_beta_and_safe_off():
    from jarves import features as feat
    from jarves import setup_wizard
    assert feat.stage_of(gw.APPROVE_FEATURE) == "beta" and gw.APPROVE_FEATURE in setup_wizard.SAFE_OFF


@pytest.mark.parametrize("kind,target,payload", [("pr.review", "2", {"event": "COMMENT", "body": "x"}), ("pr.comment", "2", {"body": "x"}),
                                                 ("pr.open", "", {"title": "t", "head": "a", "base": "b"}), ("ci.rerun", "9", {"failed_only": True})])
def test_pr_and_ci_kinds_are_cli_only_in_the_gate(tmp_path, kind, target, payload):
    from test_github_write import run
    w, ap, fake = _pr_writer(tmp_path)
    for origin in ("model", "other"):
        r = run(w.execute(gp.action(kind, "octo/r", target, **payload), origin=origin))
        assert r.code in ("GITHUB_WRITE_FORBIDDEN", "GITHUB_WRITE_UNTRUSTED") and ap.asked == [] and not fake.bodies
    assert "command line only" in run(w.execute(gp.action(kind, "octo/r", target, **payload), origin="model")).message


@pytest.mark.parametrize("bad", ["2\n", "2 ", "\n2", "٢"])
def test_trailing_newline_and_non_ascii_digits_are_rejected(tmp_path, bad):
    a = gp.action("pr.comment", "octo/r", bad, body="x")
    assert gp.validate(a) != ""
    assert gp.validate(gp.action("ci.rerun", "octo/r", bad, failed_only=True)) != ""
    assert gp.validate(gp.action("pr.open", "octo/r", "", title="t", head="feature\n", base="main")) != ""
    assert gc._write_allowed("POST", "/repos/o/r/issues/2/comments\n") is False
    assert gc._write_allowed("DELETE", "/repos/o/r/issues/2/labels/bug\n") is False


def test_a_non_github_error_after_approval_gets_a_final_audit_row_not_a_traceback(tmp_path, monkeypatch):
    import httpx
    from test_github_write import audit, run
    w, ap, fake = _pr_writer(tmp_path)

    async def boom(a, c):
        raise httpx.InvalidURL("bad url")
    monkeypatch.setitem(gw.HANDLERS, "pr.comment", boom)
    r = run(w.execute(gp.action("pr.comment", "octo/r", "2", body="x"), origin="user"))
    assert r.code == "GITHUB_WRITE_UNKNOWN" and "InvalidURL" in r.message
    rows = audit(tmp_path)
    assert rows[-1]["result"] == "GITHUB_WRITE_UNKNOWN" and rows[-1].get("rid") == rows[-2].get("rid")


def test_ci_rerun_popup_shows_workflow_branch_and_fork_warning(tmp_path):
    from test_github_write import run
    w, ap, fake = _pr_writer(tmp_path)
    fake.run_info = {"name": "Deploy", "head_branch": "evil-branch", "event": "pull_request_target",
                     "head_repository": {"full_name": "mallory/r", "fork": True}}
    assert run(w.execute(gp.action("ci.rerun", "octo/r", "9", failed_only=False), origin="user")).ok
    shown = ap.asked[-1][0]
    assert "Deploy" in shown and "evil-branch" in shown and "FORK" in shown and "mallory/r" in shown and "pull_request_target" in shown


def test_ci_rerun_fails_closed_when_the_run_cannot_be_read(tmp_path):
    from test_github_write import run
    w, ap, fake = _pr_writer(tmp_path)
    fake.run_info = None                                           # makes the fake answer garbage below
    orig = fake.run_json
    fake.run_json = lambda repo, rid: (_ for _ in ()).throw(RuntimeError("x"))
    r = run(w.execute(gp.action("ci.rerun", "octo/r", "9", failed_only=False), origin="user"))
    assert not r.ok and ap.asked == [] and not fake.bodies


def test_pr_update_close_reopen_close_is_not_a_duplicate(tmp_path):
    from test_github_write import run
    t = [1000.0]
    w, ap, fake = _pr_writer(tmp_path, clock=lambda: t[0])
    for state in ("closed", "open", "closed"):
        t[0] += 5
        assert run(w.execute(gp.action("pr.update", "octo/r", "2", state=state), origin="user")).ok


def test_ci_rerun_popup_strips_control_and_bidi_characters_from_github_text(tmp_path):
    """#581: workflow name, branch and actor come from a fork owner: one safe line each, capped."""
    from test_github_write import run
    w, ap, fake = _pr_writer(tmp_path)
    fake.run_info = {"name": "Deploy‮\nFAKE LINE: approved by owner​" + "x" * 300, "head_branch": "br⁦anch\r\n",
                     "actor": "mal‮lory"}
    assert run(w.execute(gp.action("ci.rerun", "octo/r", "9", failed_only=False), origin="user")).ok
    shown = ap.asked[-1][0]
    assert "‮" not in shown and "​" not in shown and "⁦" not in shown
    assert "\nFAKE LINE" not in shown and "x" * 150 not in shown


def test_unreadable_run_says_workflow_run_not_issue(tmp_path):
    from test_github_write import run
    w, ap, fake = _pr_writer(tmp_path)
    fake.run_json = lambda repo, rid: (_ for _ in ()).throw(RuntimeError("x"))
    r = run(w.execute(gp.action("ci.rerun", "octo/r", "9", failed_only=False), origin="user"))
    assert r.code == "GITHUB_WRITE_UNREADABLE" and "workflow run" in r.message and "issue" not in r.message


def test_approve_batch_shows_the_same_preview_as_a_single_action(tmp_path):
    """#580: a batch with APPROVE and a CI re-run carries the warnings and the run facts; an unreadable run fails closed."""
    from test_github_write import run
    w, ap, fake = _pr_writer(tmp_path)
    w.feats = type("F", (), {"active": lambda self, f: f in (gw.FEATURE, gw.APPROVE_FEATURE)})()
    fake.run_info = {"name": "Deploy", "event": "pull_request_target", "head_repository": {"full_name": "mallory/r", "fork": True}}
    acts = [gp.action("pr.review", "octo/r", "2", event="APPROVE"), gp.action("ci.rerun", "octo/r", "9", failed_only=False)]
    assert run(w.approve_batch(acts, origin="user")).ok
    shown = ap.asked[-1][0]
    assert "APPROVE can satisfy a required review" in shown and "FORK" in shown and "Deploy" in shown
    w2, ap2, fake2 = _pr_writer(tmp_path / "b")
    fake2.run_json = lambda repo, rid: (_ for _ in ()).throw(RuntimeError("x"))
    r = run(w2.approve_batch([gp.action("ci.rerun", "octo/r", "9", failed_only=False)], origin="user"))
    assert r.code == "GITHUB_WRITE_UNREADABLE" and ap2.asked == []
