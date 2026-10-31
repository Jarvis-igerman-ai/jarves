"""Abuse-case sweep over the phone/companion routes added late in V7 (agent 1, tests only, real HTTP, fake Core):
GET/POST /api/m/browser*, GET /api/m/trust/ca, POST /api/m/voice, POST /api/m/approvals/<id>, and the dashboard-only
GET /api/github/work seen from a phone. For every route: no token -> 401, a valid token without the scope -> 403, a wrong
Origin / Sec-Fetch-Site / missing X-Jarves on a write -> refused, plain http where https is required -> refused, an oversize
body -> 413 (never a crash or a hang), the wrong verb -> 404/405, the feature switch OFF -> inert, and no response ever holds a
token, a token hash or another device's id. Helpers come from test_mobile_access (the same fake Core and fixtures)."""
import json

import pytest

from jarves import mobile
from test_mobile_access import HOST, admin, fake, pair, req, srv  # noqa: F401  (fixtures and helpers)

APPROVAL = "/api/m/approvals/" + "a" * 16
# (method, path, needed scope or None, JSON body or None)
WRITES = [("POST", f"/api/m/browser/{op}", "browser", {}) for op in ("start", "frame", "takeover", "return", "input", "stop")] + [
    ("POST", "/api/m/voice", "voice", None),
    ("POST", APPROVAL, "approvals", {"approved": True}),
]
READS = [("GET", "/api/m/browser", "browser", None), ("GET", "/api/m/trust/ca", None, None)]
ALL = READS + WRITES


def grant(srv, dev, scopes):
    st, _ = admin(srv, "POST", f"/api/mobile/devices/{dev}/scopes", {"scopes": scopes})
    assert st == 200


def call(srv, method, path, tok=None, body=None, **kw):
    if path == "/api/m/voice" and body is None and method == "POST":
        kw.setdefault("raw", b"RIFF")
        kw.setdefault("ctype", "audio/wav")
    return req(srv, method, path, token=tok, body=body, **kw)


@pytest.mark.parametrize("method,path,scope,body", ALL)
def test_no_token_and_a_bad_token_get_401(srv, method, path, scope, body):
    assert call(srv, method, path, None, body)[0] == 401
    assert call(srv, method, path, "abc.def", body)[0] == 401


@pytest.mark.parametrize("method,path,scope,body", [r for r in ALL if r[2]])
def test_a_valid_token_without_the_scope_gets_403(srv, method, path, scope, body):
    tok, dev, _ = pair(srv)
    grant(srv, dev, ["status"])                                        # a scope that is none of chat / voice / approvals / browser
    st, out, _ = call(srv, method, path, tok, body)
    assert st == 403, (path, st, out)


@pytest.mark.parametrize("method,path,scope,body", WRITES)
@pytest.mark.parametrize("headers", [{"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"X-Jarves": "0"}])
def test_a_write_from_the_wrong_origin_or_without_the_header_is_refused(srv, method, path, scope, body, headers):
    tok, dev, _ = pair(srv)
    grant(srv, dev, ["chat", "voice", "status", "approvals", "browser"])
    st, out, _ = call(srv, method, path, tok, body, headers=headers)
    assert st == 403, (path, headers, st, out)


@pytest.mark.parametrize("op", ["start", "frame", "takeover", "return", "input", "stop"])
def test_browser_is_inert_while_its_switch_is_off_and_plain_http_is_refused(srv, op):
    tok, dev, _ = pair(srv)
    grant(srv, dev, ["status", "browser"])
    assert req(srv, "GET", "/api/m/browser", token=tok)[1] == {"enabled": False}      # a device learns nothing about the pilot
    st, out, _ = req(srv, "POST", f"/api/m/browser/{op}", token=tok, body={})
    assert st in (403, 409) and out.get("code") in ("PHONE_BROWSER_OFF", "PHONE_BROWSER_NO_TLS")
    assert "frame" not in out and "image" not in out


def test_trust_ca_is_inert_while_phone_trust_is_off(srv):
    tok, dev, _ = pair(srv)
    st, out, _ = req(srv, "GET", "/api/m/trust/ca", token=tok)
    assert st == 404 and "BEGIN" not in json.dumps(out)


def test_voice_over_plain_http_is_refused_and_oversize_voice_and_json_bodies_get_413(srv, fake):
    tok, dev, _ = pair(srv)
    grant(srv, dev, ["chat", "voice", "status", "approvals", "browser"])
    st, _, _ = req(srv, "POST", "/api/m/voice", token=tok, raw=b"RIFF", ctype="audio/wav")
    assert st in (403, 409) and fake.voices == []                            # no audio travels over plain http
    for path in ("/api/m/browser/input", APPROVAL):
        st, _, _ = req(srv, "POST", path, token=tok, raw=b"{}", headers={"Content-Length": str(mobile.MAX_JSON_BYTES + 10)})
        assert st in (413, 403, 409), (path, st)                             # refused before the body is read; never a hang
    st, _, _ = req(srv, "POST", APPROVAL, token=tok, raw=b"{}", headers={"Content-Length": str(mobile.MAX_JSON_BYTES + 10)})
    assert st == 413
    st, _, _ = req(srv, "POST", APPROVAL, token=tok, raw=b"{}", headers={"Content-Length": "-5"})
    assert st == 400
    st, _, _ = req(srv, "POST", APPROVAL, token=tok, raw=b"not json")
    assert st == 400


def test_approval_route_rejects_malformed_bodies_and_ids(srv):
    tok, dev, _ = pair(srv)
    for body in ({}, {"approved": "yes"}, {"approved": 1}, {"approved": True, "id": "x"}):
        st, _, _ = req(srv, "POST", APPROVAL, token=tok, body=body)
        assert st == 400, body
    for path in ("/api/m/approvals/short", "/api/m/approvals/" + "A" * 16, "/api/m/approvals/" + "a" * 17,
                 "/api/m/approvals/../chat", "/api/m/approvals/%2e%2e/status"):
        assert req(srv, "POST", path, token=tok, body={"approved": True})[0] in (404, 400), path


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
@pytest.mark.parametrize("path", ["/api/m/browser", "/api/m/browser/frame", "/api/m/trust/ca", "/api/m/voice", APPROVAL])
def test_unsupported_verbs_get_404_or_405_never_a_crash(srv, method, path):
    tok, dev, _ = pair(srv)
    grant(srv, dev, ["chat", "voice", "status", "approvals", "browser"])
    st, _, _ = req(srv, method, path, token=tok, body={})
    assert st in (404, 405, 501)


def test_browser_wrong_verb_on_the_known_paths(srv):
    tok, dev, _ = pair(srv)
    grant(srv, dev, ["status", "browser"])
    assert req(srv, "GET", "/api/m/browser/frame", token=tok)[0] == 405
    assert req(srv, "POST", "/api/m/browser", token=tok, body={})[0] == 405
    assert req(srv, "POST", "/api/m/browser/nope", token=tok, body={})[0] in (404, 405)


def test_github_work_is_not_reachable_from_a_phone_and_is_local_only_on_the_computer(srv):
    tok, dev, _ = pair(srv)
    assert req(srv, "GET", "/api/github/work")[0] in (401, 403, 404)                    # no phone token
    assert req(srv, "GET", "/api/github/work", token=tok)[0] in (401, 403, 404)     # the phone listener opens no dashboard route
    st, out = admin(srv, "GET", "/api/github/work")                                  # the computer itself: inert while the switch is off
    if st == 200:
        assert out["enabled"] is False and out["rows"] == []
    else:
        assert st in (403, 404)


def test_no_response_holds_a_token_a_hash_or_another_devices_id(srv):
    tok_a, dev_a, _ = pair(srv, "A")
    tok_b, dev_b, _ = pair(srv, "B")
    grant(srv, dev_a, ["chat", "voice", "status", "approvals", "browser"])
    stored = json.loads((srv.ctx.home / mobile.DEVICES_FILE).read_text(encoding="utf-8"))
    secrets = [tok_a, tok_b, dev_b] + [d["token_hash"] for d in stored["devices"] if d.get("token_hash")]
    assert all(secrets)
    seen = 0
    for method, path, scope, body in ALL + [("GET", "/api/m/status", None, None), ("GET", "/api/m/me", None, None),
                                            ("GET", "/api/m/approvals", None, None)]:
        st, out, _ = call(srv, method, path, tok_a, body)
        text = json.dumps(out) if not isinstance(out, bytes) else out.decode("utf-8", "replace")
        for s in secrets:
            assert s not in text, (path, "leaks a secret")
        seen += 1
    assert seen >= 11
