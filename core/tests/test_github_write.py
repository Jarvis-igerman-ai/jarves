"""GitHub write framework (V7 G2, #409): every gate, approval binding, audit, rate limit. Fake GitHub + fake approvals."""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace as NS

import pytest

from jarves import features as feat
from jarves import github_client as gc
from jarves import github_write as gw
from jarves.approvals import ApprovalService

TOKEN = "github_pat_FAKE1234567890abcdef"
REPO = "octo/r"


class FakeFeats:
    def __init__(self, on=True):
        self.on = on

    def active(self, fid):
        return self.on and fid == gw.FEATURE


class FakeApprovals:
    def __init__(self, answer=True, resolution=""):
        self.answer, self.asked, self.last_resolution = answer, [], resolution

    async def ask(self, prompt, **kw):
        self.asked.append((prompt, kw))
        return self.answer


def mk(tmp_path, *, on=True, answer=True, repos=(REPO,), fake=None, resolution="", **kw):
    fake = fake or gc.FakeGitHub()
    ap = FakeApprovals(answer, resolution)
    w = gw.GitHubWriter(tmp_path, FakeFeats(on), ap, repos=list(repos),
                        client=lambda: gc.GitHubClient(token=TOKEN, transport=fake.transport), **kw)
    return w, ap, fake


def act(kind="pr.open", repo=REPO, target="", payload=None, preview="open an issue"):
    return gw.WriteAction(kind=kind, repo=repo, target=target, payload=payload or {"title": "t"}, preview=preview)


def run(c):
    return asyncio.run(c)


def audit(tmp_path):
    f = tmp_path / "github" / "write_audit.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_passing_every_gate_is_not_implemented_and_nothing_is_written(tmp_path):
    w, ap, fake = mk(tmp_path)
    r = run(w.execute(act(), origin="user"))
    assert (r.ok, r.code) == (False, "GITHUB_WRITE_NOT_IMPLEMENTED")
    assert len(ap.asked) == 1 and {m for m, _ in fake.calls} == {"GET"}      # only the read probe
    p, kw = ap.asked[0]
    assert "pr.open" in p and "octo/r" in p and '"title": "t"' in p and kw["risk"] == "high"
    assert kw["details"]["action_hashes"] == act().hash()[:16]


def test_feature_registration():
    f = feat.CATALOG[gw.FEATURE]
    assert f.default_enabled is False and f.risk == "high" and f.needs == ["github"]
    assert feat.stage_of(gw.FEATURE) == "beta"                                # beta via the generic gate
    assert f.tools == []                                                      # G2 adds no model tool


def test_gate_switch_off(tmp_path):
    w, ap, fake = mk(tmp_path, on=False)
    assert run(w.execute(act())).code == "GITHUB_WRITE_OFF" and not ap.asked and not fake.calls


@pytest.mark.parametrize("kind", ["repo.delete", "delete_repo", "repo.visibility", "settings.update", "secrets.set",
                                  "org.invite", "permissions.grant", "branch_protection.update", "ruleset.create",
                                  "push.force", "git.force-push", "pr.merge", "merge", "merge.direct", " ", "Repo.Delete"])
def test_forbidden_kinds_refused_even_with_everything_on(tmp_path, kind):
    w, ap, fake = mk(tmp_path)
    assert run(w.execute(act(kind=kind), origin="user")).code == "GITHUB_WRITE_FORBIDDEN"
    assert not ap.asked and not fake.calls
    assert not gw.forbidden("merge.enqueue") and not gw.forbidden("issue.comment")


def test_gate_untrusted_turn_and_unknown_origin(tmp_path):
    w, ap, _ = mk(tmp_path)
    assert run(w.execute(act(), origin="model", untrusted=True)).code == "GITHUB_WRITE_UNTRUSTED"
    assert run(w.execute(act(), origin="scheduler")).code == "GITHUB_WRITE_UNTRUSTED" and not ap.asked


def test_gate_repo_allow_list_default_empty(tmp_path):
    w, ap, fake = mk(tmp_path, repos=())
    assert run(w.execute(act())).code == "GITHUB_WRITE_REPO"
    w, ap, _ = mk(tmp_path, repos=["octo/other", "bad repo/x"])
    assert run(w.execute(act())).code == "GITHUB_WRITE_REPO" and not ap.asked
    w, ap, _ = mk(tmp_path, repos=["OCTO/R"])                                # case-insensitive match
    assert run(w.execute(act())).code == "GITHUB_WRITE_NOT_IMPLEMENTED"


@pytest.mark.parametrize("scopes,push", [("read:user", None), ("", None), (None, False), (None, None)])
def test_gate_scope_fails_closed(tmp_path, scopes, push):
    fake = gc.FakeGitHub()
    fake.scopes, fake.push = scopes, push
    w, ap, _ = mk(tmp_path, fake=fake)
    assert run(w.execute(act())).code == "GITHUB_WRITE_SCOPE" and not ap.asked


def test_scope_fine_grained_push_ok_and_probe_error_fails_closed(tmp_path):
    fake = gc.FakeGitHub()
    fake.scopes, fake.push = None, True
    assert run(mk(tmp_path, fake=fake)[0].execute(act())).code == "GITHUB_WRITE_NOT_IMPLEMENTED"
    fake = gc.FakeGitHub()
    fake.fail = (500, {})
    assert run(mk(tmp_path, fake=fake)[0].execute(act())).code == "GITHUB_WRITE_SCOPE"


@pytest.mark.parametrize("res,code", [("denied", "GITHUB_WRITE_DENIED"), ("timeout", "GITHUB_WRITE_TIMEOUT")])
def test_approval_denied_or_timeout(tmp_path, res, code):
    w, ap, _ = mk(tmp_path, answer=False, resolution=res)
    assert run(w.execute(act())).code == code
    assert audit(tmp_path)[-1]["result"] == code


def test_approval_cannot_be_answered_from_a_tool(tmp_path):
    from jarves import skills
    svc = ApprovalService(tmp_path)
    tok = skills.IN_TOOL.set("github_write")
    try:
        assert svc.answer("x" * 16, True, channel="dashboard")[0] is False
    finally:
        skills.IN_TOOL.reset(tok)


def test_no_channel_means_denied(tmp_path):                                    # real ApprovalService, nobody to ask
    fake = gc.FakeGitHub()

    async def go():
        svc = ApprovalService(tmp_path)
        w = gw.GitHubWriter(tmp_path, FakeFeats(), svc, repos=[REPO],
                            client=lambda: gc.GitHubClient(token=TOKEN, transport=fake.transport))
        return await w.execute(act(), origin="user")
    assert run(go()).code == "GITHUB_WRITE_DENIED"


def test_approval_bound_to_the_action_hash(tmp_path):
    w, ap, _ = mk(tmp_path)
    a, b = act(payload={"title": "A"}), act(payload={"title": "B"})
    assert len({a.hash(), b.hash(), act(preview="other words").hash()}) == 3
    assert run(w.approve_batch([a], origin="user")).code == "GITHUB_WRITE_APPROVED" and len(ap.asked) == 1
    run(w.execute(a, origin="user"))
    assert len(ap.asked) == 1                                                  # A used its approval
    run(w.execute(b, origin="user"))
    assert len(ap.asked) == 2                                                  # B was NOT covered: asks again
    run(w.execute(a, origin="user"))
    assert len(ap.asked) == 3                                                  # and A's approval is single use


def test_batch_one_prompt_shows_all_and_gates_still_apply(tmp_path):
    w, ap, _ = mk(tmp_path)
    acts = [act(payload={"title": f"t{i}"}) for i in range(3)]
    assert run(w.approve_batch(acts, origin="user")).ok
    assert len(ap.asked) == 1 and all(f'"t{i}"' in ap.asked[0][0] for i in range(3))
    assert run(w.approve_batch(acts + [act(repo="octo/x")])).code == "GITHUB_WRITE_BAD_ARGUMENT"
    assert run(w.approve_batch([act(kind="pr.merge")])).code == "GITHUB_WRITE_FORBIDDEN"
    w.feats.on = False                                                         # switch turned off after approval
    assert run(w.execute(acts[0], origin="user")).code == "GITHUB_WRITE_OFF"


def test_batch_denied_grants_nothing_and_grant_expires(tmp_path):
    w, ap, _ = mk(tmp_path, answer=False)
    assert run(w.approve_batch([act()], origin="user")).code == "GITHUB_WRITE_DENIED" and w._grants == {}
    t = [1000.0]
    w, ap, _ = mk(tmp_path, clock=lambda: t[0])
    run(w.approve_batch([act()], origin="user"))
    t[0] += gw.GRANT_TTL_S + 1
    run(w.execute(act(), origin="user"))
    assert len(ap.asked) == 2


def test_rate_limit(tmp_path):
    t = [1000.0]
    w, ap, _ = mk(tmp_path, clock=lambda: t[0], limit=(2, 60.0))
    assert run(w.execute(act())).code == "GITHUB_WRITE_NOT_IMPLEMENTED"
    assert run(w.execute(act())).code == "GITHUB_WRITE_NOT_IMPLEMENTED"
    assert run(w.execute(act())).code == "GITHUB_WRITE_RATE_LIMIT" and len(ap.asked) == 2
    t[0] += 61
    assert run(w.execute(act())).code == "GITHUB_WRITE_NOT_IMPLEMENTED"


def test_audit_has_codes_not_payload_or_token(tmp_path):
    secret = "SECRET-BODY-TEXT-9917"
    w, ap, _ = mk(tmp_path)
    run(w.execute(act(payload={"body": secret}, preview="comment"), origin="user"))
    run(w.execute(act(kind="repo.delete"), origin="user"))
    rows = audit(tmp_path)
    assert [r["result"] for r in rows] == ["GITHUB_WRITE_RESERVED", "GITHUB_WRITE_NOT_IMPLEMENTED", "GITHUB_WRITE_FORBIDDEN"]
    assert {"ts", "kind", "repo", "target", "hash", "origin", "result"} <= set(rows[0]) and rows[0]["repo"] == REPO
    raw = (tmp_path / "github" / "write_audit.jsonl").read_text(encoding="utf-8")
    assert secret not in raw and TOKEN not in raw


def test_token_never_in_results_or_status(tmp_path, caplog):
    fake = gc.FakeGitHub()
    fake.fail = (401, {})
    w, ap, _ = mk(tmp_path, fake=fake)
    r = run(w.execute(act()))
    assert r.code == "GITHUB_WRITE_SCOPE" and TOKEN not in r.message and TOKEN not in repr(r)
    assert TOKEN not in caplog.text and TOKEN not in json.dumps(w.status())


def test_handler_path_and_no_write_verbs(tmp_path, monkeypatch):
    async def h(a, client):
        return {"number": 7}
    monkeypatch.setitem(gw.HANDLERS, "pr.open", h)
    w, ap, _ = mk(tmp_path)
    r = run(w.execute(act(), origin="user"))
    assert r.ok and r.data == {"number": 7} and audit(tmp_path)[-1]["result"] == "OK"
    assert '"PUT"' not in inspect.getsource(gc)


def test_cli_write_status(tmp_path, capsys):
    from jarves.config import Config
    cfg = Config(github_repos=[REPO])
    assert cfg.github_repos == [REPO] and Config().github_repos == []
    assert gc.cmd_github(NS(action="write", write_action=None), cfg, tmp_path) == 0
    j = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert j["switch_on"] is False and j["allowed_repos"] == [REPO] and j["implemented_kinds"] == sorted(gw.HANDLERS) and "issue.create" in j["implemented_kinds"]
    assert "Issues: write" in j["token_permissions_per_class"]


def test_unknown_kind_and_oversized_payload_refused_before_asking(tmp_path):
    w, ap, fake = mk(tmp_path)
    assert run(w.execute(act(kind="repo.create"), origin="user")).code == "GITHUB_WRITE_FORBIDDEN"
    big = act(payload={"body": "x" * 5000})
    assert run(w.execute(big, origin="user")).code == "GITHUB_WRITE_BAD_ARGUMENT" and not ap.asked and not fake.calls


def test_classic_scope_without_push_on_this_repo_is_refused(tmp_path):
    fake = gc.FakeGitHub()
    fake.scopes, fake.push = "repo", False
    w, ap, _ = mk(tmp_path, fake=fake)
    assert run(w.execute(act(), origin="user")).code == "GITHUB_WRITE_SCOPE" and not ap.asked


def test_payload_changed_during_approval_is_refused(tmp_path):
    w, ap, _ = mk(tmp_path)
    a = act(payload={"title": "t"})
    orig = ap.ask

    async def mutate(prompt, **kw):
        a.payload["title"] = "evil"
        return await orig(prompt, **kw)
    ap.ask = mutate
    assert run(w.execute(a, origin="user")).code == "GITHUB_WRITE_DENIED"
