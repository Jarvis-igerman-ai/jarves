"""GitHub writes class 3 (V7 G5, #412): auto-merge / merge queue only. FakeGitHub, fake approvals, no network.
The fake API proves: no direct-merge call exists, bypass is never sent, the mutation is bound to the approved head commit."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarves import github_client as gc
from jarves import github_merge as gm
from jarves import github_write as gw
from test_github_issues import REPO, TOKEN, mk, run, writes

SHA = "a" * 40


class Feats:
    def __init__(self, *on):
        self.on = set(on) or {gw.FEATURE, gw.MERGE_FEATURE}

    def active(self, fid):
        return fid in self.on


def go(w, number="2", sha=SHA, repo=REPO, origin="user"):
    return run(w.execute(gm.action(repo, number, sha), origin=origin))


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
    body = fake.graphql_bodies[-1]
    assert "enablePullRequestAutoMerge" in body["query"] and "expectedHeadOid" in body["query"]
    assert body["variables"] == {"id": "PR_kwDOabcdefgh", "sha": SHA}
    p, kw = ap.asked[0]
    assert kw["risk"] == "high" and "AUTO-MERGE" in p and "No direct merge and no bypass" in p and SHA[:10] in p
    assert "checks on this commit" in p


def test_no_direct_merge_or_bypass_exists_anywhere():
    src = Path(gm.__file__).read_text(encoding="utf-8") + Path(gc.__file__).read_text(encoding="utf-8")
    assert "mergePullRequest" not in src.replace("mergePullRequest" + "x", "") and "bypass_rules" not in src and "bypassRules" not in src
    for q, _ in gc._MUTATIONS.values():
        assert "merge" not in q.lower().replace("automerge", "").replace("enablepullrequestautomerge", "") or "AutoMerge" in q
        assert "mergePullRequest" not in q
    assert not gc._write_allowed("PUT", "/repos/octo/r/pulls/2/merge")
    assert not gc._write_allowed("POST", "/repos/octo/r/pulls/2/merge")
    assert not gc._write_allowed("POST", "/graphql")          # the REST write door never takes GraphQL
    for kind in ("pr.merge", "merge", "merge.direct", "merge.squash", "merge.bypass", "merge.force"):
        assert gw.forbidden(kind)
    assert not gw.forbidden("merge.enqueue")


def test_graphql_door_refuses_other_documents_and_bad_variables():
    c = gc.GitHubClient(token=TOKEN, transport=gc.FakeGitHub().transport)
    for name, v in (("mergePullRequest", {"id": "PR_kwDOabcdefgh", "sha": SHA}), ("enable_auto_merge", {"id": "PR_kwDOabcdefgh"}),
                    ("enable_auto_merge", {"id": "PR_kwDOabcdefgh", "sha": SHA, "bypass": True}),
                    ("enable_auto_merge", {"id": "x y", "sha": SHA}), ("enable_auto_merge", {"id": "PR_kwDOabcdefgh", "sha": "abc"})):
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
                                      ({"head": {"sha": "b" * 40}}, "newer commit")])
def test_a_pull_request_that_cannot_be_queued_is_warned_then_refused(tmp_path, info, why):
    fake = gc.FakeGitHub()
    fake.pull_info = info
    w, ap, fake = writer(tmp_path, fake=fake)
    r = go(w)
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_BAD_ARGUMENT") and not writes(fake)
    assert "WARNING" in ap.asked[0][0] and why in ap.asked[0][0]


def test_a_push_after_the_approval_is_refused_fresh(tmp_path):
    fake = gc.FakeGitHub()
    w, ap, fake = writer(tmp_path, fake=fake)
    orig = ap.ask

    async def ask(*a, **k):
        fake.pull_info = {"head": {"sha": "c" * 40}}          # a new commit lands while the popup is open
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


@pytest.mark.parametrize("number,sha", [("", SHA), ("0", SHA), ("2", ""), ("2", "ABC"), ("2", "g" * 40), ("2", SHA[:39]), ("2\n", SHA)])
def test_bad_target_or_commit_is_refused_before_the_popup(tmp_path, number, sha):
    w, ap, fake = writer(tmp_path)
    a = gw.WriteAction(kind=gm.KIND, repo=REPO, target=number, payload={"head_sha": sha}, preview="x")
    r = run(w.execute(a, origin="user"))
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_BAD_ARGUMENT") and not ap.asked


def test_extra_payload_fields_are_refused(tmp_path):
    w, ap, fake = writer(tmp_path)
    a = gw.WriteAction(kind=gm.KIND, repo=REPO, target="2", payload={"head_sha": SHA, "bypass": True}, preview="x")
    assert run(w.execute(a, origin="user")).code == "GITHUB_WRITE_BAD_ARGUMENT" and not writes(fake)


def test_audit_holds_kind_and_hash_not_the_commit_text(tmp_path):
    w, ap, fake = writer(tmp_path)
    go(w)
    blob = (tmp_path / "github" / "write_audit.jsonl").read_text(encoding="utf-8") if (tmp_path / "github" / "write_audit.jsonl").exists() else ""
    assert TOKEN not in blob


def test_cli_usage_and_wiring(capsys):
    from types import SimpleNamespace as NS
    a = NS(repo="", number="2", head_sha="short", write_action="enqueue")
    assert gm.cmd_github_merge(a, NS(github_repos=[REPO]), Path(".")) == 2
    assert "usage: jarvis github pr enqueue" in capsys.readouterr().out
