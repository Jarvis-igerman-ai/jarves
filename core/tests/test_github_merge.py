"""GitHub writes class 3 (V7 G5, #412): auto-merge on a merge-queue branch only. FakeGitHub, fake approvals, no network.
The fake API proves: no direct-merge call exists, bypass is never sent, the mutation is bound to the approved commit and base."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarves import github_client as gc
from jarves import github_merge as gm
from jarves import github_write as gw
from test_github_issues import REPO, TOKEN, mk, run

SHA = "a" * 40
DOC = ("mutation($id:ID!,$sha:GitObjectID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,"
       "expectedHeadOid:$sha}){pullRequest{number autoMergeRequest{enabledAt} mergeQueueEntry{id}}}}")


def writes(fake):
    """Real writes only: GraphQL QUERIES (the merge-queue check) are POSTs but change nothing."""
    return [("POST", "/graphql") for b in fake.graphql_bodies if "mutation" in b["query"]] +            [c for c in fake.calls if c[0] != "GET" and c[1] != "/graphql"]


class Feats:
    def __init__(self, *on):
        self.on = set(on) or {gw.FEATURE, gw.MERGE_FEATURE}

    def active(self, fid):
        return fid in self.on


def go(w, number="2", sha=SHA, repo=REPO, origin="user", base="main"):
    return run(w.execute(gm.action(repo, number, sha, base), origin=origin))


def writer(tmp_path, *on, **kw):
    w, ap, fake = mk(tmp_path, **kw)
    w.feats = Feats(*on)
    return w, ap, fake


def test_switch_is_beta_and_off_by_default():
    f = gw.feat.CATALOG[gm.FEATURE]
    assert f.default_enabled is False and gw.feat.stage_of(gm.FEATURE) == "beta" and "github_write" in f.needs


def test_happy_path_sends_only_the_auto_merge_mutation_bound_to_the_head_commit(tmp_path):
    w, ap, fake = writer(tmp_path)
    r = go(w)
    assert (r.ok, r.data) == (True, {"number": 2, "auto_merge": "enabled"})
    assert writes(fake) == [("POST", "/graphql")]
    mut = [b for b in fake.graphql_bodies if "mutation" in b["query"]]
    assert len(mut) == 1 and mut[0]["query"] == DOC
    assert mut[0]["variables"] == {"id": "PR_kwDOabcdefgh", "sha": SHA}
    p, kw = ap.asked[0]
    assert kw["risk"] == "high" and "AUTO-MERGE" in p and "No direct merge, no bypass" in p and SHA[:10] in p
    assert "LATER push" in p and "does NOT cancel" in p and "cancels it" not in p.replace("does NOT cancel", "")
    assert "check runs on this commit (may be partial)" in p and "by octo" in p and "branch fix-1" in p and "into main" in p


def test_the_mutation_list_is_closed_and_has_no_merge_method_or_bypass():
    assert set(gc._MUTATIONS) == {"enable_auto_merge"}
    assert gc._MUTATIONS["enable_auto_merge"][0] == DOC
    src = Path(gm.__file__).read_text(encoding="utf-8") + Path(gc.__file__).read_text(encoding="utf-8")
    for bad in ("mergePullRequest", "bypass_rules", "bypassRules", "mergeMethod", "enqueuePullRequest", "mergeBranch", "updatePullRequestBranch"):
        assert bad not in src, bad
    assert not gc._write_allowed("PUT", "/repos/octo/r/pulls/2/merge")
    assert not gc._write_allowed("POST", "/repos/octo/r/pulls/2/merge")
    assert not gc._write_allowed("POST", "/graphql")          # the REST write door never takes GraphQL
    for kind in ("pr.merge", "merge", "merge.direct", "merge.squash", "merge.bypass", "merge.force", "merge.enqueue.x"):
        assert gw.forbidden(kind) or kind not in gw.KIND_PERMS
    assert not gw.forbidden("merge.enqueue")


def test_graphql_door_refuses_other_documents_and_bad_variables():
    c = gc.GitHubClient(token=TOKEN, transport=gc.FakeGitHub().transport)
    for name, v in (("mergePullRequest", {"id": "PR_kwDOabcdefgh", "sha": SHA}), ("enable_auto_merge", {"id": "PR_kwDOabcdefgh"}),
                    ("enable_auto_merge", {"id": "PR_kwDOabcdefgh", "sha": SHA, "bypass": True}),
                    ("enable_auto_merge", {"id": "x y", "sha": SHA}), ("enable_auto_merge", {"id": "MDExOlB1bGxSZXF1ZXN0MQ==", "sha": SHA}),
                    ("enable_auto_merge", {"id": "PR_kwDOabcdefgh", "sha": "abc"})):
        with pytest.raises(gc.GitHubError) as e:
            c.graphql_write(name, v)
        assert e.value.code == "GITHUB_BAD_ARGUMENT"


def test_own_switch_off_refuses_before_any_popup(tmp_path):
    w, ap, fake = writer(tmp_path, gw.FEATURE)
    r = go(w)
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_MERGE_OFF") and not ap.asked and not writes(fake)


def test_model_route_is_refused(tmp_path):
    w, ap, fake = writer(tmp_path)
    r = go(w, origin="model")
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_FORBIDDEN") and not ap.asked and not writes(fake)


def test_denied_or_untrusted_writes_nothing(tmp_path):
    w, ap, fake = writer(tmp_path, answer=False)
    assert go(w).code == "GITHUB_WRITE_DENIED" and not writes(fake)
    w, ap, fake = writer(tmp_path)
    r = run(w.execute(gm.action(REPO, "2", SHA), origin="user", untrusted=True))
    assert r.code == "GITHUB_WRITE_UNTRUSTED" and not writes(fake)


@pytest.mark.parametrize("info,why", [({"draft": True}, "a draft"), ({"state": "closed"}, "not open"), ({"merged": True}, "not open"),
                                      ({"head": {"sha": "b" * 40, "ref": "x", "repo": {"full_name": REPO}}}, "newer commit"),
                                      ({"base": {"ref": "release", "repo": {"full_name": REPO}}}, "different branch"),
                                      ({"base": {"ref": "main", "repo": {"full_name": "evil/r"}}}, "does not belong")])
def test_a_pull_request_that_cannot_be_queued_is_warned_then_refused(tmp_path, info, why):
    fake = gc.FakeGitHub()
    fake.pull_info = info
    w, ap, fake = writer(tmp_path, fake=fake)
    r = go(w)
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_BAD_ARGUMENT") and not writes(fake)
    assert "WARNING" in ap.asked[0][0] and why in ap.asked[0][0]


def test_a_fork_is_flagged_in_the_popup(tmp_path):
    fake = gc.FakeGitHub()
    fake.pull_info = {"head": {"sha": SHA, "ref": "patch-1", "repo": {"full_name": "stranger/r"}}}
    w, ap, fake = writer(tmp_path, fake=fake)
    assert go(w).ok
    assert "(FORK)" in ap.asked[0][0] and "WARNING: this pull request comes from a FORK" in ap.asked[0][0]


def test_a_branch_without_a_merge_queue_is_warned_then_refused(tmp_path):
    fake = gc.FakeGitHub()
    fake.merge_queue = False
    w, ap, fake = writer(tmp_path, fake=fake)
    r = go(w)
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_BAD_ARGUMENT") and "no merge queue" in r.message and not writes(fake)
    assert "NO merge queue" in ap.asked[0][0]


@pytest.mark.parametrize("what", ["sha", "base"])
def test_a_change_after_the_approval_is_refused_fresh(tmp_path, what):
    fake = gc.FakeGitHub()
    w, ap, fake = writer(tmp_path, fake=fake)
    orig = ap.ask

    async def ask(*a, **k):
        fake.pull_info = ({"head": {"sha": "c" * 40, "ref": "x", "repo": {"full_name": REPO}}} if what == "sha"
                          else {"base": {"ref": "other", "repo": {"full_name": REPO}}})
        return await orig(*a, **k)
    ap.ask = ask
    r = go(w)
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_BAD_ARGUMENT") and not writes(fake)


def test_github_refusal_is_reported_in_fixed_words(tmp_path):
    fake = gc.FakeGitHub()
    fake.graphql_errors = True
    w, ap, fake = writer(tmp_path, fake=fake)
    r = go(w)
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_REFUSED") and "auto-merge" in r.message


@pytest.mark.parametrize("answer,code", [
    ({"errors": [{"type": "FORBIDDEN", "message": "x"}]}, "GITHUB_WRITE_PERMISSION"),
    ({"errors": [{"type": "RATE_LIMITED", "message": "x"}]}, "GITHUB_RATE_LIMIT"),
    ({"data": None}, "GITHUB_WRITE_UNKNOWN"),
    ({"data": {"enablePullRequestAutoMerge": {"pullRequest": {"number": 2, "autoMergeRequest": None}}}}, "GITHUB_WRITE_UNKNOWN"),
    ({"data": {"enablePullRequestAutoMerge": {"pullRequest": {"number": 2, "autoMergeRequest": {"enabledAt": "x"}}}},
      "errors": [{"type": "X", "message": "nested"}]}, "GITHUB_WRITE_UNKNOWN"),
])
def test_odd_answers_never_report_success(tmp_path, answer, code):
    fake = gc.FakeGitHub()
    fake.graphql_answer = answer
    w, ap, fake = writer(tmp_path, fake=fake)
    r = go(w)
    assert (r.ok, r.code) == (False, code)


def test_an_immediate_queue_entry_counts_as_confirmed(tmp_path):
    fake = gc.FakeGitHub()
    fake.graphql_answer = {"data": {"enablePullRequestAutoMerge": {"pullRequest": {"number": 2, "autoMergeRequest": None,
                                                                                 "mergeQueueEntry": {"id": "MQE_1"}}}}}
    w, ap, fake = writer(tmp_path, fake=fake)
    assert go(w).ok
    assert "does not cancel it either" in ap.asked[0][0]


def test_an_unknown_result_blocks_an_immediate_retry(tmp_path):
    fake = gc.FakeGitHub()
    fake.graphql_answer = {"data": None}
    w, ap, fake = writer(tmp_path, fake=fake)
    assert go(w).code == "GITHUB_WRITE_UNKNOWN"
    assert go(w).code == "GITHUB_WRITE_DUPLICATE"


@pytest.mark.parametrize("number,sha,base", [("", SHA, "main"), ("0", SHA, "main"), ("2", "", "main"), ("2", "ABC", "main"),
                                             ("2", "g" * 40, "main"), ("2", SHA[:39], "main"), ("2\n", SHA, "main"),
                                             ("2", SHA, "-x"), ("2", SHA, "a..b"), ("2", SHA, "")])
def test_bad_target_commit_or_base_is_refused_before_the_popup(tmp_path, number, sha, base):
    w, ap, fake = writer(tmp_path)
    a = gw.WriteAction(kind=gm.KIND, repo=REPO, target=number, payload={"head_sha": sha, "base": base}, preview="x")
    r = run(w.execute(a, origin="user"))
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_BAD_ARGUMENT") and not ap.asked


def test_extra_payload_fields_are_refused(tmp_path):
    w, ap, fake = writer(tmp_path)
    a = gw.WriteAction(kind=gm.KIND, repo=REPO, target="2", payload={"head_sha": SHA, "base": "main", "bypass": True}, preview="x")
    assert run(w.execute(a, origin="user")).code == "GITHUB_WRITE_BAD_ARGUMENT" and not writes(fake)


def test_the_batch_path_keeps_every_gate(tmp_path):
    w, ap, fake = writer(tmp_path, gw.FEATURE)                    # own switch off
    r = run(w.approve_batch([gm.action(REPO, "2", SHA)], origin="user"))
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_MERGE_OFF") and not ap.asked
    w, ap, fake = writer(tmp_path)
    r = run(w.approve_batch([gm.action(REPO, "2", SHA)], origin="model"))
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_FORBIDDEN")


def test_audit_holds_codes_not_the_commit_or_token(tmp_path):
    w, ap, fake = writer(tmp_path)
    assert go(w).ok
    blobs = [p.read_text(encoding="utf-8", errors="ignore") for p in tmp_path.rglob("*") if p.is_file() and "audit" in p.name]
    assert blobs and all(TOKEN not in b and SHA not in b for b in blobs)


def test_unreadable_pull_request_has_its_own_words(tmp_path, monkeypatch):
    def boom(self, o, r, n):
        raise gc.GitHubError("GITHUB_PROVIDER_ERROR", "x")
    monkeypatch.setattr(gc.GitHubClient, "pull", boom)
    w, ap, fake = writer(tmp_path)
    r = go(w)
    assert not r.ok and r.code == "GITHUB_WRITE_UNREADABLE" and "pull request" in r.message and not ap.asked


def test_cli_usage_and_wiring(capsys):
    from types import SimpleNamespace as NS
    a = NS(repo="", number="2", head_sha="short", write_action="enqueue", base=None)
    assert gm.cmd_github_merge(a, NS(github_repos=[REPO]), Path(".")) == 2
    assert "usage: jarvis github pr enqueue" in capsys.readouterr().out
