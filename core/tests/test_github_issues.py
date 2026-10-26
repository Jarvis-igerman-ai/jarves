"""GitHub writes class 1 (V7 G3, #410): issue create/edit/comment, labels. FakeGitHub for POST/PATCH/DELETE, no network."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from jarves import github_client as gc
from jarves import github_issues as gi
from jarves import github_write as gw

TOKEN = "github_pat_FAKE1234567890abcdef"
REPO = "octo/r"


class Feats:
    def active(self, fid):
        return fid == gw.FEATURE


class Approvals:
    def __init__(self, answer=True):
        self.answer, self.asked, self.last_resolution = answer, [], ""

    async def ask(self, prompt, **kw):
        self.asked.append((prompt, kw))
        return self.answer


def mk(tmp_path, *, answer=True, fake=None, repos=(REPO,), **kw):
    fake = fake or gc.FakeGitHub()
    ap = Approvals(answer)
    return (gw.GitHubWriter(tmp_path, Feats(), ap, repos=list(repos), client=lambda: gc.GitHubClient(token=TOKEN, transport=fake.transport),
                            **kw), ap, fake)


def run(c):
    return asyncio.run(c)


def writes(fake):
    return [c for c in fake.calls if c[0] != "GET"]


def go(w, kind, number="", repo=REPO, origin="user", **payload):
    return run(w.execute(gi.action(kind, repo, number, **payload), origin=origin))


def test_create_issue_posts_exactly_the_approved_payload(tmp_path):
    w, ap, fake = mk(tmp_path)
    r = go(w, "issue.create", title="Bug", body="steps", labels=["bug"], assignees=["octocat"])
    assert (r.ok, r.code) == (True, "OK") and r.data == {"number": 7, "url": "https://github.com/octo/r/issues/7"}
    assert writes(fake) == [("POST", "/repos/octo/r/issues")]
    assert fake.bodies == [{"title": "Bug", "body": "steps", "labels": ["bug"], "assignees": ["octocat"]}]
    p, kw = ap.asked[0]
    assert '"title": "Bug"' in p and kw["risk"] == "high" and "mention" not in p
    assert fake.auth[-1] == f"Bearer {TOKEN}"


def test_edit_comment_and_labels(tmp_path):
    w, ap, fake = mk(tmp_path)
    assert go(w, "issue.edit", "7", state="closed", title="New").ok
    assert go(w, "issue.comment", "7", body="thanks").data["url"].endswith("#c55")
    assert go(w, "labels.add", "7", labels=["bug", "p1"]).ok                   # case-insensitive match of existing labels
    assert go(w, "labels.remove", "7", labels=["bug"]).ok
    assert [c for c in writes(fake)] == [("PATCH", "/repos/octo/r/issues/7"), ("POST", "/repos/octo/r/issues/7/comments"),
                                         ("POST", "/repos/octo/r/issues/7/labels"), ("DELETE", "/repos/octo/r/issues/7/labels/bug")]
    assert fake.bodies[0] == {"state": "closed", "title": "New"} and fake.bodies[2] == {"labels": ["bug", "p1"]}
    assert len(ap.asked) == 4                                                  # approval for each


def test_label_that_does_not_exist_is_never_created(tmp_path):
    w, _, fake = mk(tmp_path)
    r = go(w, "labels.add", "7", labels=["brand-new"])
    assert r.code == "GITHUB_WRITE_BAD_ARGUMENT" and not writes(fake)
    assert go(w, "issue.create", title="t", labels=["nope"]).code == "GITHUB_WRITE_BAD_ARGUMENT" and not writes(fake)


@pytest.mark.parametrize("kind,num,payload", [
    ("issue.create", "", {}), ("issue.create", "", {"body": "x"}), ("issue.create", "", {"title": "t", "milestone": 1}),
    ("issue.create", "", {"title": "t" * 201}), ("issue.create", "", {"title": "t", "body": "b" * 801}),
    ("issue.create", "", {"title": "  "}), ("issue.create", "", {"title": 5}), ("issue.create", "3", {"title": "t"}),
    ("issue.create", "", {"title": "t", "labels": ["a"] * 2}), ("issue.create", "", {"title": "t", "labels": ["x"] * 11}),
    ("issue.create", "", {"title": "t", "assignees": ["bad login"]}), ("issue.create", "", {"title": "t\x00"}),
    ("issue.edit", "", {"title": "t"}), ("issue.edit", "abc", {"title": "t"}), ("issue.edit", "7", {}),
    ("issue.edit", "7", {"state": "merged"}), ("issue.edit", "7", {"locked": True}), ("issue.edit", "7", {"milestone": 2}),
    ("issue.comment", "7", {"body": ""}), ("issue.comment", "7", {"title": "t"}), ("issue.comment", "0", {"body": "b"}),
    ("labels.add", "7", {"labels": []}), ("labels.add", "7", {"labels": [".."]}), ("labels.remove", "7", {"labels": ["a/b"]}),
    ("labels.remove", "7", {"labels": ["a?b"]}), ("labels.add", "7", {"labels": "bug"}), ("labels.add", "7", {"labels": ["x" * 51]})])
def test_payload_schema_refused_before_asking(tmp_path, kind, num, payload):
    w, ap, fake = mk(tmp_path)
    r = go(w, kind, num, **payload)
    assert r.code == "GITHUB_WRITE_BAD_ARGUMENT" and not ap.asked and not fake.calls


@pytest.mark.parametrize("body", ["hi @everyone", "ping @all now", "cc @octo-org/security", "@channel", "@here please"])
def test_everyone_and_team_mentions_are_refused(tmp_path, body):
    w, ap, _ = mk(tmp_path)
    assert go(w, "issue.comment", "7", body=body).code == "GITHUB_WRITE_BAD_ARGUMENT" and not ap.asked


def test_person_mention_and_links_are_allowed_with_a_warning_in_the_approval(tmp_path):
    fake = gc.FakeGitHub()
    fake.scopes = "public_repo"
    w, ap, fake = mk(tmp_path, fake=fake)
    assert go(w, "issue.comment", "7", body="thanks @octocat, see https://example.com/x").ok
    p = ap.asked[0][0]
    assert p.count("WARNING") == 2 and "mention" in p and "link" in p
    assert p.index("WARNING") < p.index("Exact payload")                       # at the top, before the long text


def test_approval_denied_writes_nothing(tmp_path):
    w, ap, fake = mk(tmp_path, answer=False)
    assert go(w, "issue.create", title="t").code == "GITHUB_WRITE_DENIED" and not writes(fake)


def test_untrusted_turn_and_switch_off_and_unlisted_kinds(tmp_path):
    w, ap, fake = mk(tmp_path)
    a = gi.action("issue.create", REPO, title="t")
    assert run(w.execute(a, origin="model", untrusted=True)).code == "GITHUB_WRITE_UNTRUSTED"
    w.feats = NS(active=lambda f: False)
    assert run(w.execute(a, origin="user")).code == "GITHUB_WRITE_OFF"
    w.feats = Feats()
    for kind in ("issue.delete", "issue.lock", "labels.create", "label.create", "issue.transfer", "milestone.create"):
        bad = gw.WriteAction(kind=kind, repo=REPO, payload={"title": "t"}, preview="p")
        assert run(w.execute(bad, origin="user")).code == "GITHUB_WRITE_FORBIDDEN"
    assert not ap.asked and not writes(fake)


def test_repo_not_in_allow_list_and_forged_repo(tmp_path):
    w, ap, fake = mk(tmp_path)
    for repo in ("octo/other", "../x/y", "octo/r/../../orgs", "octo/r?x=1"):
        a = gw.WriteAction(kind="issue.create", repo=repo, payload={"title": "t"}, preview="p")
        assert run(w.execute(a, origin="user")).code == "GITHUB_WRITE_REPO"
    assert not fake.calls and not ap.asked


def test_renamed_or_transferred_repo_is_refused_before_approval(tmp_path):
    fake = gc.FakeGitHub()
    fake.rename = "newowner/r"
    w, ap, _ = mk(tmp_path, fake=fake)
    r = go(w, "issue.create", title="t")
    assert r.code == "GITHUB_WRITE_SCOPE" and "renamed" in r.message and not ap.asked and not writes(fake)


def test_family_probe_403_refused_before_approval(tmp_path):
    fake = gc.FakeGitHub()
    w, ap, _ = mk(tmp_path, fake=fake)
    fake.fail = (403, {})
    assert go(w, "issue.create", title="t").code == "GITHUB_WRITE_SCOPE" and not ap.asked


@pytest.mark.parametrize("status,headers,code", [
    (403, {}, "GITHUB_WRITE_PERMISSION"), (404, {}, "GITHUB_NOT_FOUND"), (422, {}, "GITHUB_WRITE_INVALID"),
    (429, {}, "GITHUB_RATE_LIMIT"), (403, {"retry-after": "60"}, "GITHUB_RATE_LIMIT"),
    (403, {"x-ratelimit-remaining": "0"}, "GITHUB_RATE_LIMIT"), (401, {}, "GITHUB_AUTH"),
    (301, {"location": "https://api.github.com/x"}, "GITHUB_WRITE_REDIRECT"), (500, {}, "GITHUB_PROVIDER_ERROR")])
def test_write_errors_map_to_clear_codes_and_403_after_approval_is_authoritative(tmp_path, status, headers, code):
    fake = gc.FakeGitHub()
    fake.write_fail = (status, headers)
    w, ap, _ = mk(tmp_path, fake=fake)
    r = go(w, "issue.comment", "7", body="hello")
    assert (r.ok, r.code) == (False, code) and len(ap.asked) == 1
    assert TOKEN not in r.message and "x" != r.message
    if code == "GITHUB_WRITE_PERMISSION":
        assert "Issues: write" in r.message
    assert json.loads((tmp_path / "github" / "write_audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])["result"] == code


def test_idempotent_retry_does_not_double_post(tmp_path):
    t = [1000.0]
    w, ap, fake = mk(tmp_path, clock=lambda: t[0])
    assert go(w, "issue.create", title="once").ok
    r = go(w, "issue.create", title="once")                                    # the same approved action retried
    assert r.code == "GITHUB_WRITE_DUPLICATE" and len(writes(fake)) == 1 and len(ap.asked) == 1
    assert go(w, "issue.create", title="twice").ok                              # a different text is a different action
    t[0] += gw.DUP_WINDOW_S + 1
    assert go(w, "issue.create", title="once").ok and len(writes(fake)) == 3


def test_lost_answer_blocks_a_blind_retry(tmp_path):
    import httpx
    fake = gc.FakeGitHub()
    real = fake.transport

    def flaky(req):
        if req.method == "POST":
            raise httpx.ReadTimeout("slow")
        return real.handle_request(req)
    w = gw.GitHubWriter(tmp_path, Feats(), Approvals(), repos=[REPO],
                        client=lambda: gc.GitHubClient(token=TOKEN, transport=httpx.MockTransport(flaky)))
    assert go(w, "issue.create", title="t").code == "GITHUB_WRITE_UNKNOWN"
    assert go(w, "issue.create", title="t").code == "GITHUB_WRITE_DUPLICATE"


def test_batch_approval_covers_exactly_the_listed_actions(tmp_path):
    w, ap, fake = mk(tmp_path)
    acts = [gi.action("issue.comment", REPO, "7", body=f"c{i}") for i in range(3)]
    assert run(w.approve_batch(acts, origin="user")).ok and len(ap.asked) == 1
    assert all(run(w.execute(a, origin="user")).ok for a in acts) and len(ap.asked) == 1 and len(writes(fake)) == 3
    assert go(w, "issue.comment", "7", body="other").ok and len(ap.asked) == 2


def test_approval_is_never_truncated(tmp_path):
    w, ap, _ = mk(tmp_path)                                                    # worst case incl. both warnings + token warning
    assert go(w, "issue.create", title="t" * 200, body="@a https://a.com " + "b" * 780).ok
    assert len(ap.asked[0][0]) < 1990 and ap.asked[0][0].rstrip().rsplit("Ref ", 1)[1].strip()
    big = gw.WriteAction(kind="pr.open", repo=REPO, payload={"body": "b" * 1150}, preview="p" * 600)
    assert run(w.execute(big, origin="user")).code == "GITHUB_WRITE_BAD_ARGUMENT" and len(ap.asked) == 1


@pytest.mark.parametrize("scopes,warn", [("repo, read:org", True), ("admin:org,repo", True), ("delete_repo,repo", True),
                                          ("workflow,repo", True), ("public_repo", False)])
def test_broad_classic_token_warned_in_approval_and_status(tmp_path, scopes, warn):
    fake = gc.FakeGitHub()
    fake.scopes = scopes
    w, ap, _ = mk(tmp_path, fake=fake)
    go(w, "issue.create", title="t")
    assert ("broad scopes" in ap.asked[0][0]) is warn
    gc.TokenStore(tmp_path).save(TOKEN)
    assert bool(w.status()["token_warnings"]) is warn


def test_fine_grained_token_has_no_broad_warning(tmp_path):
    fake = gc.FakeGitHub()
    fake.scopes, fake.push = None, True
    w, ap, _ = mk(tmp_path, fake=fake)
    assert go(w, "issue.create", title="t").ok and "broad" not in ap.asked[0][0]


def test_audit_has_no_text_or_token(tmp_path):
    w, _, _ = mk(tmp_path)
    go(w, "issue.comment", "7", body="SECRET-BODY-9917")
    raw = (tmp_path / "github" / "write_audit.jsonl").read_text(encoding="utf-8")
    assert "SECRET-BODY-9917" not in raw and TOKEN not in raw and '"result": "OK"' in raw


def test_label_name_is_url_quoted_in_delete(tmp_path):
    fake = gc.FakeGitHub()
    fake.labels = ["good first issue"]
    w, _, _ = mk(tmp_path, fake=fake)
    assert go(w, "labels.remove", "7", labels=["good first issue"]).ok
    assert fake.calls[-1] == ("DELETE", "/repos/octo/r/issues/7/labels/good first issue")   # httpx decodes the path


def test_cli_usage_and_run(tmp_path, monkeypatch, capsys):
    from jarves.config import Config
    a = NS(write_action="create", number=None, repo="", title="t", body=None, state=None, label=None, assignee=None)
    assert gc.cmd_github(NS(action="issue", **vars(a)), Config(github_repos=[REPO, "a/b"]), tmp_path) == 2     # two repos: --repo needed
    assert "usage" in capsys.readouterr().out
    assert gc.cmd_github(NS(action="issue", **{**vars(a), "write_action": "comment"}), Config(github_repos=[REPO]), tmp_path) == 2
    from jarves import cli
    monkeypatch.setattr(cli, "_cli_features", lambda cfg, home: Feats())
    fake = gc.FakeGitHub()
    monkeypatch.setattr(gc.GitHubClient, "__init__", lambda s, home=None, **k: gc.GitHubClient.__dict__["__init__"].__wrapped__(s)
                        if False else _init(s, fake))
    monkeypatch.setattr("jarves.approvals.ApprovalService", lambda home: Approvals(True))
    assert gc.cmd_github(NS(action="issue", **vars(a)), Config(github_repos=[REPO]), tmp_path) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["number"] == 7 and fake.bodies == [{"title": "t"}]


_REAL_INIT = gc.GitHubClient.__init__


def _init(s, fake):
    _REAL_INIT(s, token=TOKEN, transport=fake.transport)


def test_cli_parser_accepts_the_new_commands():
    import argparse
    p = argparse.ArgumentParser()
    gc.add_cli(p.add_subparsers(dest="cmd"))
    n = p.parse_args(["github", "issue", "comment", "7", "--repo", REPO, "--body", "hi", "--label", "bug", "--label", "x"])
    assert (n.action, n.write_action, n.number, n.label) == ("issue", "comment", "7", ["bug", "x"])
