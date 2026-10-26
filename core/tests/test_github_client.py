"""Read-only GitHub client (V7 G1, #408): FakeGitHub over httpx.MockTransport. No network, no real token."""
from __future__ import annotations

import inspect
import json
import os
import stat

import pytest

from jarves import features as feat
from jarves import github_client as gh

TOKEN = "github_pat_FAKE1234567890abcdef"


def client(fake, **kw):
    return gh.GitHubClient(token=TOKEN, transport=fake.transport, **kw)


def test_reads_and_shapes_and_token_header():
    f = gh.FakeGitHub()
    c = client(f)
    assert c.me() == {"login": "octo"}
    assert c.issues("octo", "r") == [{"number": 1, "title": "bug", "state": "open", "labels": ["P1"]}]  # PRs filtered out
    assert c.pulls("octo", "r")[0]["head"] == "abc123"
    assert c.check_runs("octo", "r", "abc123") == [{"name": "ci", "status": "completed", "conclusion": "success"}]
    assert c.workflow_runs("octo", "r")[0]["name"] == "CI"
    assert c.merge_queue("octo", "r") == [{"position": 1, "state": "QUEUED", "number": 2}]
    assert set(f.auth) == {f"Bearer {TOKEN}"}
    assert {m for m, p in f.calls if p != "/graphql"} == {"GET"}          # the only POST is the read-only GraphQL query
    assert c.rate["remaining"] == "4999"


def test_pagination_is_capped():
    f = gh.FakeGitHub()
    assert len(client(f).repos()) == gh.PER_PAGE * gh.MAX_PAGES
    assert len([1 for _, p in f.calls if p == "/user/repos"]) == gh.MAX_PAGES


@pytest.mark.parametrize("status,headers,code", [
    (401, {}, "GITHUB_AUTH"), (403, {}, "GITHUB_FORBIDDEN"), (403, {"x-ratelimit-remaining": "0"}, "GITHUB_RATE_LIMIT"),
    (429, {}, "GITHUB_RATE_LIMIT"), (404, {}, "GITHUB_NOT_FOUND"), (500, {}, "GITHUB_PROVIDER_ERROR")])
def test_error_mapping(status, headers, code):
    f = gh.FakeGitHub()
    f.fail = (status, headers)
    with pytest.raises(gh.GitHubError) as e:
        client(f).me()
    assert e.value.code == code and TOKEN not in str(e.value)


def test_not_connected_and_bad_arguments(tmp_path):
    with pytest.raises(gh.GitHubError) as e:
        gh.GitHubClient(tmp_path, transport=gh.FakeGitHub().transport).me()
    assert e.value.code == "GITHUB_NOT_CONNECTED"
    c = client(gh.FakeGitHub())
    for call in (lambda: c.issues("a/b", "r"), lambda: c.issues("a", "r", "bogus"), lambda: c.check_runs("a", "r", "x y")):
        with pytest.raises(gh.GitHubError) as e:
            call()
        assert e.value.code == "GITHUB_BAD_ARGUMENT"


def test_offline_maps_without_leaking():
    import httpx

    def boom(req):
        raise httpx.ConnectError(f"failed with {TOKEN}")
    with pytest.raises(gh.GitHubError) as e:
        gh.GitHubClient(token=TOKEN, transport=httpx.MockTransport(boom)).me()
    assert e.value.code == "GITHUB_OFFLINE" and TOKEN not in str(e.value) and TOKEN not in repr(e.value.__cause__)


def test_redact():
    for t in (TOKEN, "ghp_abcdefghijk123", "Authorization: Bearer abcdefgh12345678"):
        assert "abcdef" not in gh.redact(f"x {t} y") or "[REDACTED]" in gh.redact(f"x {t} y")
    assert TOKEN not in gh.redact(f"boom {TOKEN}")


def test_token_store_private_and_status_hides_token(tmp_path):
    s = gh.TokenStore(tmp_path)
    assert s.status()["connected"] is False
    s.save(f"  {TOKEN}\n")
    assert s.token() == TOKEN and TOKEN not in json.dumps(s.status())
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(s.file).st_mode) == 0o600
    with pytest.raises(gh.GitHubError):
        s.save("two words")
    s.clear()
    assert not s.status()["connected"]


def test_one_write_door_with_a_fixed_endpoint_family():
    src = inspect.getsource(gh)
    assert '"PUT"' not in src and src.count("write=True") == 1                    # only write() passes write=True
    c = gh.GitHubClient(token=TOKEN, transport=gh.FakeGitHub().transport)
    for m, p in [("PUT", "/repos/o/r/issues"), ("DELETE", "/repos/o/r"), ("POST", "/repos/o/r/pulls"),
                 ("POST", "/repos/o/r/issues/1/../../merge"), ("POST", "/repos/o/r/issues/x"), ("PATCH", "/user"),
                 ("DELETE", "/repos/o/r/issues/1/labels"), ("DELETE", "/repos/o/r/issues/1/labels/."),     # = remove ALL
                 ("DELETE", "/repos/o/r/issues/1/labels/.."), ("DELETE", "/repos/o/r/issues/1/labels/%2E%2e"),
                 ("DELETE", "/repos/o/r/issues/1"), ("DELETE", "/repos/o/r/issues"), ("PATCH", "/repos/o/r/issues"),
                 ("POST", "/repos/o/r/issues/1"), ("PATCH", "/repos/o/r/issues/1/comments"), ("POST", "/repos/../../issues"),
                 ("POST", "/repos/o/r/issues/0/comments"), ("POST", "/repos/o/r/issues?x=1"), ("post", "/repos/o/r/issues")]:
        with pytest.raises(gh.GitHubError) as e:
            c.write(m, p, {})
        assert e.value.code == "GITHUB_BAD_ARGUMENT"
    with pytest.raises(gh.GitHubError):
        gh.GitHubClient(token=TOKEN, base="https://evil.example")             # the token never goes to another host


def test_feature_off_by_default_high_risk_and_probe(home):
    f = feat.CATALOG["github"]
    assert f.default_enabled is False and f.risk == "high"
    assert f.targets["windows"]["status"] == "implemented_unverified"
    assert gh.connected(home) is False


def test_cli_status(tmp_path, capsys):
    from types import SimpleNamespace as NS
    assert gh.cmd_github(NS(action="status"), None, tmp_path) == 1
    gh.TokenStore(tmp_path).save(TOKEN)
    assert gh.cmd_github(NS(action="status"), None, tmp_path) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out and '"read_only": true' in out
    assert gh.cmd_github(NS(action="off"), None, tmp_path) == 0 and not gh.connected(tmp_path)


def test_security_hardening():
    c = client(gh.FakeGitHub())
    for bad in ("..", ".", ".x"):
        with pytest.raises(gh.GitHubError):
            c.issues(bad, "r")
    for ref in ("../../x", "/a", "a//b", "a/"):
        with pytest.raises(gh.GitHubError):
            c.check_runs("a", "r", ref)
    with pytest.raises(gh.GitHubError):
        gh.GitHubClient(token=TOKEN, base="https://evil.example")
    assert TOKEN not in repr(c)
    f = gh.FakeGitHub()
    f.fail = (302, {"location": "https://evil.example"})
    with pytest.raises(gh.GitHubError) as e:
        client(f).me()
    assert e.value.code == "GITHUB_PROVIDER_ERROR"
    assert "mutation" not in inspect.getsource(gh.GitHubClient.merge_queue)
    assert gh.redact("a " + "0123456789abcdef" * 2 + "01234567 b") == "a [REDACTED] b"
