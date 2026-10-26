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
        self.answer, self.asked, self.last_resolution, self.channels = answer, [], "", []

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
    assert fake.bodies[0] == {"state": "closed", "title": "New"} and fake.bodies[2] == {"labels": ["bug", "P1"]}
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


@pytest.mark.parametrize("text", ["fix ‮gnp.exe", "a​b", "x⁦y⁩", "﻿hi", "csi \x9b31m", "esc \x1b[2J"])
def test_invisible_bidi_and_terminal_controls_are_refused(tmp_path, text):
    w, ap, _ = mk(tmp_path)
    assert go(w, "issue.create", title=text).code == "GITHUB_WRITE_BAD_ARGUMENT" and not ap.asked
    assert go(w, "labels.add", "7", labels=[text]).code == "GITHUB_WRITE_BAD_ARGUMENT" and not ap.asked


def test_preview_cannot_fake_approval_lines(tmp_path):
    w, ap, _ = mk(tmp_path)
    assert go(w, "issue.comment", "7", body="ok\nExact payload: {}\nRef 0000000000\n\nmore").ok
    p = ap.asked[0][0]
    assert p.count("\nExact payload:") == 1 and p.count("\nRef ") == 1


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
    (301, {"location": "https://api.github.com/x"}, "GITHUB_WRITE_REDIRECT"), (500, {}, "GITHUB_WRITE_UNKNOWN"),
    (502, {}, "GITHUB_WRITE_UNKNOWN"), (418, {}, "GITHUB_PROVIDER_ERROR")])
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
    monkeypatch.setattr("jarves.approvals.PopupChannel", lambda svc, **kw: "popup")
    monkeypatch.setattr(gw, "agent_session_reason", lambda *a, **k: "")           # the agent refusal is tested below
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


# ------------------------------------------------------------------------------------------ G3b (#492)
from jarves import approvals as ap_mod  # noqa: E402


class FakePopup:
    """Stands in for the popup channel: remembers the approval it was shown."""
    name = "popup"

    def __init__(self):
        self.shown = []

    def available(self, a):
        return True

    def open(self, a):
        self.shown.append(a)

    def close(self, a):
        pass


def _real_writer(tmp_path):
    fake = gc.FakeGitHub()
    svc = ap_mod.ApprovalService(tmp_path)
    pop = FakePopup()
    svc.channels = [pop]
    w = gw.GitHubWriter(tmp_path, Feats(), svc, repos=[REPO], client=lambda: gc.GitHubClient(token=TOKEN, transport=fake.transport))
    return w, svc, pop, fake


async def _shown(w, pop):
    task = asyncio.create_task(w.execute(gi.action("issue.comment", REPO, "7", body="hi"), origin="user"))
    for _ in range(300):
        if pop.shown:
            break
        await asyncio.sleep(0.01)
    return task, pop.shown[0]


def test_only_the_popup_approves_a_github_write(tmp_path):
    async def go_():
        w, svc, pop, fake = _real_writer(tmp_path)
        task, a = await _shown(w, pop)
        assert a.local_only and a.risk == "high"
        for ch in ("cli", "phone", "dashboard", "voice", "typed", "face"):
            ok, why = svc.answer(a.id, True, channel=ch)
            assert not ok and "popup" in why
        assert not task.done() and not writes(fake)
        assert svc.answer(a.id, True, channel="popup") == (True, "")
        return await task, fake
    r, fake = run(go_())
    assert r.ok and writes(fake) == [("POST", "/repos/octo/r/issues/7/comments")]


def test_cli_approve_command_and_phone_file_answers_are_refused_but_deny_works(tmp_path):
    async def go_():
        w, svc, pop, fake = _real_writer(tmp_path)
        ap_mod._current = svc
        try:
            task, a = await _shown(w, pop)
            for ch in ("cli", "phone", "dashboard"):               # what `jarvis approvals approve` / the phone submit
                ok, why = ap_mod.submit_answer(tmp_path, a.id, True, channel=ch)
                assert not ok and ("popup" in why or "approval key" in why or "no such" in why)
            assert not task.done() and not writes(fake)
            assert svc.answer(a.id, False, channel="phone")[0]      # a denial from anywhere is fine
            return await task
        finally:
            ap_mod._current = None
    assert run(go_()).code == "GITHUB_WRITE_DENIED"


def test_file_answer_for_a_local_only_row_is_refused(tmp_path):
    ap_mod.akeys.load_or_create(tmp_path)
    (tmp_path / "approvals").mkdir(exist_ok=True)
    row = {"id": "a" * 16, "local_only": True, "expires": 9e12, "title": "t", "risk": "high", "kind": "tool"}
    (tmp_path / "approvals" / "pending.json").write_text(json.dumps({"pending": [row]}), encoding="utf-8")
    ap_mod._current = None
    for ch in ("cli", "phone", "dashboard"):
        ok, why = ap_mod.submit_answer(tmp_path, "a" * 16, True, channel=ch)
        assert not ok and "popup" in why
    assert ap_mod.submit_answer(tmp_path, "a" * 16, False, channel="cli") == (True, "")      # a denial is fine


def test_agent_or_non_interactive_session_is_detected_conservatively():
    assert gw.agent_session_reason({}, tty=True) == ""                                # a person at a terminal
    assert "not an interactive terminal" in gw.agent_session_reason({}, tty=False)    # a pipe / agent shell
    for var in ("CLAUDECODE", "CODEX_HOME", "CI", "JARVES_AGENT_SESSION"):
        assert var in gw.agent_session_reason({var: "1"}, tty=True)                  # a pty does not make it a person


def test_cli_refuses_in_an_agent_session_and_asks_only_the_popup_otherwise(tmp_path, monkeypatch, capsys):
    from jarves import cli
    from jarves.config import Config
    a = NS(action="issue", write_action="create", number=None, repo="", title="t", body=None, state=None, label=None,
           assignee=None)
    fake, svc = gc.FakeGitHub(), Approvals(True)
    monkeypatch.setattr(cli, "_cli_features", lambda cfg, home: Feats())
    monkeypatch.setattr(gc.GitHubClient, "__init__", lambda s, home=None, **k: _init(s, fake))
    monkeypatch.setattr("jarves.approvals.ApprovalService", lambda home: svc)
    monkeypatch.setattr("jarves.approvals.PopupChannel", lambda s_, **kw: "popup-channel")
    monkeypatch.setattr(gw, "agent_session_reason", lambda *x, **k: "an agent or automation session is running this command")
    assert gc.cmd_github(a, Config(github_repos=[REPO]), tmp_path) == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["code"] == "GITHUB_WRITE_AGENT_SESSION" and not svc.asked and not writes(fake)
    monkeypatch.setattr(gw, "agent_session_reason", lambda *x, **k: "")
    assert gc.cmd_github(a, Config(github_repos=[REPO]), tmp_path) == 0
    assert svc.channels == ["popup-channel"] and svc.asked[0][1]["local_only"] is True   # popup only, no typed hash anywhere
    assert not hasattr(gw, "terminal_confirm")


def test_hash_ignores_preview_and_order_but_not_state_or_text():
    def mkact(preview="p", **pl):
        return gw.WriteAction(kind="issue.edit", repo=REPO, target="7", payload=pl, preview=preview)
    a = mkact("one", labels=["Bug", "p1"], title="T")
    assert a.hash() == mkact("completely different", labels=["p1", "bug"], title="T").hash()
    assert a.hash() != mkact("one", labels=["Bug", "p1"], title=" T ").hash()     # the hash covers the exact text sent
    assert a.hash() != mkact(labels=["bug"], title="T").hash() and mkact(state="open").hash() != mkact(state="closed").hash()


def test_rate_limit_survives_a_new_process(tmp_path):
    t, fake = [1000.0], gc.FakeGitHub()

    def new():
        return mk(tmp_path, fake=fake, clock=lambda: t[0], limit=(2, 60.0))[0]
    assert go(new(), "issue.comment", "7", body="a").ok and go(new(), "issue.comment", "7", body="b").ok
    assert go(new(), "issue.comment", "7", body="c").code == "GITHUB_WRITE_RATE_LIMIT"      # a fresh writer, same audit file
    t[0] += 61
    assert go(new(), "issue.comment", "7", body="c").ok


def test_state_toggle_is_allowed_but_a_blind_retry_is_not(tmp_path):
    w, _, fake = mk(tmp_path)
    assert go(w, "issue.edit", "7", state="closed").ok
    assert go(w, "issue.edit", "7", state="closed").code == "GITHUB_WRITE_DUPLICATE"
    assert go(w, "issue.edit", "7", state="open").ok
    assert go(w, "issue.edit", "7", state="closed").ok                 # closed -> open -> closed within 10 minutes
    assert go(w, "issue.edit", "8", state="closed").ok                 # another issue is another thing


def test_edit_approval_says_what_it_replaces_and_that_a_pr_closes(tmp_path):
    w, ap, fake = mk(tmp_path)
    fake.issue_labels, fake.issue_assignees = ["bug", "P1"], ["octocat"]
    assert go(w, "issue.edit", "7", labels=["bug"], assignees=["hubot"]).ok
    p = ap.asked[0][0]
    assert "REPLACES labels: bug, P1 -> bug" in p and "REPLACES assignees: octocat -> hubot" in p and "pull request" not in p
    assert ap.asked[0][1]["local_only"] is True
    assert go(w, "issue.edit", "2", state="closed").ok                  # 2 is a pull request in FakeGitHub
    assert "this is a pull request: closing it closes the PR" in ap.asked[1][0]
    fake.fail = (500, {})                                               # the read fails: no partial preview (refused)
    assert run(w._extra(gi.action("issue.edit", REPO, "7", labels=["bug"]))) is None


def test_label_is_re_verified_with_a_fresh_list_and_sent_with_the_repos_spelling(tmp_path):
    w, _, fake = mk(tmp_path)

    def label_gets():
        return len([c for c in fake.calls if c[1].endswith("/labels") and c[0] == "GET"])
    assert go(w, "labels.add", "7", labels=["p1"]).ok and fake.bodies[-1] == {"labels": ["P1"]}
    n = label_gets()
    fake.labels = ["bug"]                                               # P1 was deleted after the first write
    assert go(w, "labels.add", "8", labels=["p1"]).code == "GITHUB_WRITE_BAD_ARGUMENT"
    assert label_gets() == n + 1 and len(writes(fake)) == 1             # a fresh listing, not a cache; nothing created


def test_crossref_and_assignment_warnings(tmp_path):
    w, ap, _ = mk(tmp_path)
    assert go(w, "issue.comment", "7", body="see other/repo#12 and fix #3").ok
    assert "owner/repo#N" in ap.asked[0][0]
    assert go(w, "issue.create", title="t", assignees=["octocat", "hubot"]).ok
    assert "assigns octocat, hubot: GitHub notifies each assignee" in ap.asked[1][0]
    assert go(w, "issue.comment", "7", body="plain #3 only").ok and "mention" not in ap.asked[2][0] and "owner/repo#N" not in ap.asked[2][0]


def test_a_link_cli_client_cannot_approve_a_github_write_as_terminal(tmp_path):
    """Security review #493: any process holding the link token can connect with role "cli" and send
    chat.approval_answer (answered as channel "terminal"), with the id read from approvals/pending.json. That is
    exactly a program with a shell, so it must not approve a local_only (GitHub) approval; denying still works."""
    from types import SimpleNamespace
    from jarves import chat_service

    async def go_():
        w, svc, pop, fake = _real_writer(tmp_path)
        chat = chat_service.ChatService(SimpleNamespace(approvals=svc, link=None))
        task, a = await _shown(w, pop)
        rows = json.loads((tmp_path / "approvals" / "pending.json").read_text(encoding="utf-8"))["pending"]
        assert [r["id"] for r in rows] == [a.id] and rows[0]["local_only"] is True    # what another process can read
        await chat.handle("chat.approval_answer", {"id": a.id, "approved": True}, SimpleNamespace(id="x"))
        assert svc.answer(a.id, True, channel="terminal")[0] is False
        assert not task.done() and not writes(fake)
        assert svc.answer(a.id, False, channel="terminal")[0]                      # a denial from the terminal is fine
        return await task
    assert run(go_()).code == "GITHUB_WRITE_DENIED"


def test_submit_answer_never_carries_a_popup_or_terminal_channel(tmp_path):
    """Security review #493: submit_answer (CLI, dashboard, phone, the orchestrator bridge) passed any channel name to
    the in-process service, so a caller could claim channel "popup". Only file channels pass, in-process too."""
    async def go_():
        w, svc, pop, fake = _real_writer(tmp_path)
        ap_mod._current = svc
        try:
            task, a = await _shown(w, pop)
            for ch in ("popup", "tray", "terminal", "face", "voice", "typed"):
                ok, why = ap_mod.submit_answer(tmp_path, a.id, True, channel=ch)
                assert not ok and "cannot answer" in why
            assert not task.done() and not writes(fake)
            assert svc.answer(a.id, True, channel="popup") == (True, "")
            return await task
        finally:
            ap_mod._current = None
    assert run(go_()).ok


def _audit_lines(tmp_path):
    return (tmp_path / "github" / "write_audit.jsonl").read_text(encoding="utf-8").splitlines()


def _tamper_then_write_is_refused(tmp_path, mutate, w, fake):
    f = tmp_path / "github" / "write_audit.jsonl"
    mutate(f)
    n = len(writes(fake))
    r = go(w, "issue.comment", "7", body="after tamper")
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_AUDIT_TAMPERED") and len(writes(fake)) == n
    assert "reset-audit" in r.message and w.status()["audit"] != "ok"


def _two_writes(tmp_path):
    t, fake = [1000.0], gc.FakeGitHub()
    w = mk(tmp_path, fake=fake, clock=lambda: t[0], limit=(10, 60.0))[0]
    assert go(w, "issue.comment", "7", body="a").ok and go(w, "issue.comment", "7", body="b").ok
    assert w.status()["audit"] == "ok"
    return w, fake


def test_audit_is_a_hash_chain_and_an_edited_row_is_detected(tmp_path):
    w, fake = _two_writes(tmp_path)
    rows = [json.loads(x) for x in _audit_lines(tmp_path)]
    assert rows[0]["p"] == "" and rows[1]["p"] == rows[0]["h"] and all("rid" in r for r in rows)

    def edit(f):
        f.write_text(f.read_text(encoding="utf-8").replace('"result": "OK"', '"result": "GITHUB_WRITE_DENIED"', 1), encoding="utf-8")
    _tamper_then_write_is_refused(tmp_path, edit, w, fake)


def test_truncated_audit_lowering_the_count_is_detected(tmp_path):
    w, fake = _two_writes(tmp_path)
    _tamper_then_write_is_refused(
        tmp_path, lambda f: f.write_text("\n".join(_audit_lines(tmp_path)[:-1]) + "\n", encoding="utf-8"), w, fake)


def test_deleted_audit_or_head_file_is_detected(tmp_path):
    w, fake = _two_writes(tmp_path)
    _tamper_then_write_is_refused(tmp_path, lambda f: f.unlink(), w, fake)
    w2, fake2 = _two_writes(tmp_path / "b")
    _tamper_then_write_is_refused(tmp_path / "b", lambda f: f.with_suffix(".head").unlink(), w2, fake2)


def test_torn_or_unchained_line_after_the_chain_started_is_detected(tmp_path):
    w, fake = _two_writes(tmp_path)

    def torn(f):
        with open(f, "a", encoding="utf-8") as fh:
            fh.write('{"ts": 1000.0, "result": "OK", "hash"\n')
    _tamper_then_write_is_refused(tmp_path, torn, w, fake)
    w2, fake2 = _two_writes(tmp_path / "b")

    def forged(f):
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": 1000.0, "result": "GITHUB_WRITE_DENIED", "hash": "x"}) + "\n")
    _tamper_then_write_is_refused(tmp_path / "b", forged, w2, fake2)


def test_owner_reset_keeps_the_evidence_and_starts_a_new_chain(tmp_path, monkeypatch, capsys):
    w, fake = _two_writes(tmp_path)
    f = tmp_path / "github" / "write_audit.jsonl"
    f.write_text("garbage\n", encoding="utf-8")
    assert go(w, "issue.comment", "7", body="c").code == "GITHUB_WRITE_AUDIT_TAMPERED"
    from jarves.config import Config
    monkeypatch.setattr(gw, "agent_session_reason", lambda *x, **k: "an agent or automation session is running this command")
    assert gw.cmd_github_write(NS(write_action="reset-audit"), Config(), tmp_path) == 1          # an agent cannot reset it
    assert "GITHUB_WRITE_AGENT_SESSION" in capsys.readouterr().out and f.exists()
    monkeypatch.setattr(gw, "agent_session_reason", lambda *x, **k: "")
    from jarves import cli
    monkeypatch.setattr(cli, "_cli_features", lambda cfg, home: Feats())
    assert gw.cmd_github_write(NS(write_action="reset-audit"), Config(), tmp_path) == 0
    assert list((tmp_path / "github").glob("write_audit.tampered-*.jsonl")) and not f.exists()
    assert go(w, "issue.comment", "7", body="c").ok and w.status()["audit"] == "ok"


def test_legacy_unchained_rows_before_the_chain_are_accepted(tmp_path):
    f = tmp_path / "github" / "write_audit.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"ts": 1.0, "kind": "issue.comment", "result": "OK", "hash": "x", "tkey": "y"}) + "\n", encoding="utf-8")
    w = mk(tmp_path, clock=lambda: 1000.0)[0]
    assert go(w, "issue.comment", "7", body="a").ok and w.status()["audit"] == "ok"


def test_rate_slot_is_reserved_before_the_owner_is_asked(tmp_path):
    """Two calls race while the first waits for the owner: with a limit of 1 the second must be refused, not both pass."""
    fake, gate = gc.FakeGitHub(), {}

    class SlowApprovals(Approvals):
        async def ask(self, prompt, **kw):
            self.asked.append((prompt, kw))
            gate.setdefault("first", asyncio.Event())
            if len(self.asked) == 1:
                await gate["release"].wait()
            return True

    async def race():
        gate["release"] = asyncio.Event()
        ap = SlowApprovals()
        w = gw.GitHubWriter(tmp_path, Feats(), ap, repos=[REPO], limit=(1, 60.0), clock=lambda: 1000.0,
                            client=lambda: gc.GitHubClient(token=TOKEN, transport=fake.transport))
        t1 = asyncio.create_task(w.execute(gi.action("issue.comment", REPO, "7", body="a"), origin="user"))
        for _ in range(300):
            if ap.asked:
                break
            await asyncio.sleep(0.01)
        r2 = await w.execute(gi.action("issue.comment", REPO, "7", body="b"), origin="user")   # another call / process
        gate["release"].set()
        return await t1, r2, len(ap.asked)
    r1, r2, asked = run(race())
    assert r1.ok and r2.code == "GITHUB_WRITE_RATE_LIMIT" and asked == 1 and len(writes(fake)) == 1


def test_denied_reservation_is_released_and_a_batch_reserves_one_slot_per_action(tmp_path):
    t, fake = [1000.0], gc.FakeGitHub()
    w, ap, _ = mk(tmp_path, answer=False, fake=fake, clock=lambda: t[0], limit=(2, 60.0))
    for body in "abc":                                  # three denials do not use up the limit of 2
        assert go(w, "issue.comment", "7", body=body).code == "GITHUB_WRITE_DENIED"
    assert w._used() == 0
    ap.answer = True
    acts = [gi.action("issue.comment", REPO, "7", body=x) for x in ("x", "y")]
    assert run(w.approve_batch(acts, origin="user")).ok and w._used() == 2
    assert go(w, "issue.comment", "8", body="z").code == "GITHUB_WRITE_RATE_LIMIT"       # both slots are taken
    assert run(w.execute(acts[0], origin="user")).ok and run(w.execute(acts[1], origin="user")).ok and w._used() == 2


def test_concurrent_reservations_from_threads_never_exceed_the_limit(tmp_path):
    import threading
    w = mk(tmp_path, clock=lambda: 1000.0, limit=(3, 60.0))[0]
    a = gi.action("issue.comment", REPO, "7", body="a")
    got, bar = [], threading.Barrier(8)

    def worker():
        bar.wait()
        got.append(w._reserve([a], "user"))
    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for g in got if g) == 3 and w._used() == 3 and w.status()["audit"] == "ok"


def test_a_lost_create_or_comment_is_not_repeated_after_another_one(tmp_path):
    """The toggle rule (only the latest write counts) is for edits; a create or comment whose answer was lost must
    not be posted again just because another create / comment happened in between."""
    w, _, fake = mk(tmp_path)
    assert go(w, "issue.comment", "7", body="first").ok
    assert go(w, "issue.comment", "7", body="second").ok
    assert go(w, "issue.comment", "7", body="first").code == "GITHUB_WRITE_DUPLICATE"
    assert go(w, "issue.create", title="A").ok and go(w, "issue.create", title="B").ok
    assert go(w, "issue.create", title="A").code == "GITHUB_WRITE_DUPLICATE"


def test_edit_is_refused_when_the_current_state_cannot_be_read(tmp_path, monkeypatch):
    """The REPLACES / pull-request lines need a READ; when it fails the owner is not shown a partial preview."""
    w, ap, fake = mk(tmp_path)

    def boom(*_a, **_k):
        raise gc.GitHubError("GITHUB_PROVIDER_ERROR", "network")
    monkeypatch.setattr(gc.GitHubClient, "issue", boom)
    r = go(w, "issue.edit", "2", state="closed")
    assert not r.ok and r.code == "GITHUB_WRITE_UNREADABLE" and not ap.asked and not writes(fake)


def test_a_line_separator_in_a_model_chosen_kind_does_not_fake_a_tamper_alarm(tmp_path):
    w = mk(tmp_path, clock=lambda: 1000.0)[0]
    for k in ("x y", "x\u0085y", "x y"):         # str.splitlines() would split the row: a fake tamper alarm
        r = run(w.execute(gw.WriteAction(kind=k, repo=REPO, target="7", payload={}, preview="p"), origin="model"))
        assert not r.ok
    assert w.status()["audit"] == "ok" and go(w, "issue.comment", "7", body="legit").ok


def test_head_lagging_one_row_after_a_crash_is_not_a_tamper_alarm(tmp_path):
    w, fake = _two_writes(tmp_path)
    f = tmp_path / "github" / "write_audit.jsonl"
    rows = [json.loads(x) for x in _audit_lines(tmp_path)]
    f.with_suffix(".head").write_text(json.dumps({"n": len(rows) - 1, "h": rows[-2]["h"]}), encoding="utf-8")
    assert w.status()["audit"] == "ok" and go(w, "issue.comment", "7", body="c").ok    # crash between append and head
    head = json.loads(f.with_suffix(".head").read_text(encoding="utf-8"))
    assert head["n"] == len(_audit_lines(tmp_path))                                      # and it heals on the next row
    _tamper_then_write_is_refused(                                                       # a removed row is still caught
        tmp_path, lambda p: p.write_text("\n".join(_audit_lines(tmp_path)[:-1]) + "\n", encoding="utf-8"), w, fake)
