"""Sign in with ChatGPT + ChatGPT-plan brain, fully offline (mock OAuth/Responses servers, local RSA key)."""
import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from jarves.brain import Brain, BrainUnavailable
from jarves.chatgpt_auth import DYNAMIC_CLIENT, ISSUER, PLAN_SCOPE, ChatGPTAuth, ChatGPTAuthError, pkce_pair
from jarves.chatgpt_brain import ChatGPTBrain, parse_sse
from jarves.config import Config
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import ClockSkill, MemorySkill
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio

from conftest import FakeSTT, FakeTTS

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CLIENT = "oaiapp_test123"
SCOPE_ALL = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"


class FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": KEY.public_key()})()


def id_token(nonce, aud=CLIENT, sub="user-1", exp=None):
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "aud": aud, "sub": sub, "email": "me@example.com", "nonce": nonce,
                       "iat": now, "exp": exp or now + 3600}, KEY, algorithm="RS256")


class OAuthServer:
    """Mock auth.openai.com token + revoke endpoints."""

    def __init__(self, scope=SCOPE_ALL):
        self.scope, self.forms, self.refresh_error = scope, [], None
        self.nonce = ""
        self.n = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        self.forms.append((req.url.path, form))
        if req.url.path.endswith("/revoke"):
            return httpx.Response(200)
        if form["grant_type"] == "refresh_token" and self.refresh_error:
            return httpx.Response(400, json={"error": self.refresh_error})
        self.n += 1
        return httpx.Response(200, json={"access_token": f"at-{self.n}", "refresh_token": f"rt-{self.n}",
                                         "id_token": id_token(self.nonce), "token_type": "Bearer",
                                         "expires_in": 3600, "scope": self.scope})


def make_auth(tmp_path, server, **kw):
    return ChatGPTAuth(tmp_path, transport=httpx.MockTransport(server.handler), jwks_client=FakeJWKS(), **kw)


def test_pkce_s256():
    v, c = pkce_pair()
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert len(v) >= 43 and pkce_pair()[0] != v


def test_authorize_url_first_and_returning(tmp_path):
    a = ChatGPTAuth(tmp_path)
    q = parse_qs(urlparse(a.authorize_url("http://127.0.0.1:1455/auth/callback", "s", "n", "c")).query)
    assert q["client_id"] == [DYNAMIC_CLIENT] and q["agent_name_hint"] == ["JARVES"]
    assert PLAN_SCOPE in q["scope"][0] and q["resource"] == ["https://api.openai.com/v1"]
    assert q["code_challenge_method"] == ["S256"] and q["ext_agent_host_id"][0].startswith("urn:uuid:")
    hid = q["ext_agent_host_id"][0]
    a._save({"client_id": CLIENT, "id_token": "old.id.token", "email": "me@example.com"})
    q2 = parse_qs(urlparse(a.authorize_url("http://127.0.0.1:1455/auth/callback", "s", "n", "c")).query)
    assert q2["client_id"] == [CLIENT] and "agent_name_hint" not in q2
    assert q2["id_token_hint"] == ["old.id.token"] and q2["ext_agent_host_id"] == [hid]   # stable host ID


async def test_full_loopback_sign_in(tmp_path):
    srv = OAuthServer()
    seen = {}

    def browser(url):                       # plays the user's browser: consent -> redirect to the loopback
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)
        srv.nonce = q["nonce"]

        async def hit():
            async with httpx.AsyncClient() as c:
                r = await c.get(q["redirect_uri"], params={"code": "code-1", "state": q["state"], "client_id": CLIENT,
                                                           "scope": SCOPE_ALL})
                seen["page"] = r.text
        asyncio.get_running_loop().create_task(hit())
        return True
    auth = make_auth(tmp_path, srv, open_browser=browser)
    st = await auth.sign_in(timeout_s=10, port=0)
    assert st == {"signed_in": True, "email": "me@example.com", "plan_usage": True, "access_expires_in_s": st["access_expires_in_s"]}
    assert seen["redirect_uri"].startswith("http://127.0.0.1:") and seen["redirect_uri"].endswith("/auth/callback")
    path, form = srv.forms[0]
    assert form["grant_type"] == "authorization_code" and form["client_id"] == CLIENT
    assert form["redirect_uri"] == seen["redirect_uri"] and "code_verifier" in form and "client_secret" not in form
    assert "connected" in seen["page"]
    saved = auth.load()
    assert saved["client_id"] == CLIENT and saved["subject"] == "user-1" and saved["refresh_token"] == "rt-1"
    assert await auth.access_token() == "at-1"


async def _complete(auth, srv, params, nonce="n1"):
    srv.nonce = nonce
    return await auth.complete({"state": "s1", **params}, "s1", nonce, "verifier", "http://127.0.0.1:1455/auth/callback")


async def test_callback_rejections(tmp_path):
    srv = OAuthServer()
    auth = make_auth(tmp_path, srv)
    with pytest.raises(ChatGPTAuthError) as e:
        await auth.complete({"state": "evil", "code": "c", "client_id": CLIENT}, "s1", "n", "v", "r")
    assert e.value.code == "CHATGPT_BAD_STATE"
    with pytest.raises(ChatGPTAuthError) as e:
        await _complete(auth, srv, {"error": "access_denied"})
    assert e.value.code == "CHATGPT_CONSENT_DENIED" and srv.forms == []       # no code exchange on denial
    await _complete(auth, srv, {"code": "c", "client_id": CLIENT})
    with pytest.raises(ChatGPTAuthError) as e:                                  # reauth with another client ID
        await _complete(auth, srv, {"code": "c", "client_id": "oaiapp_other"})
    assert e.value.code == "CHATGPT_CLIENT_MISMATCH"


async def test_id_token_nonce_and_audience_checked(tmp_path):
    srv = OAuthServer()
    auth = make_auth(tmp_path, srv)
    srv.nonce = "other-nonce"
    with pytest.raises(ChatGPTAuthError) as e:
        await auth.complete({"state": "s1", "code": "c", "client_id": CLIENT}, "s1", "n1", "v", "r")
    assert e.value.code == "CHATGPT_BAD_ID_TOKEN" and not auth.status()["signed_in"]
    with pytest.raises(ChatGPTAuthError):
        await auth._verify_id_token(id_token("n1", aud="someone-else"), CLIENT, "n1")
    with pytest.raises(ChatGPTAuthError):
        await auth._verify_id_token(id_token("n1", exp=int(time.time()) - 600), CLIENT, "n1")


async def test_missing_plan_scope_blocks_inference(tmp_path):
    srv = OAuthServer(scope="openid profile email offline_access")
    auth = make_auth(tmp_path, srv)
    st = await _complete(auth, srv, {"code": "c", "client_id": CLIENT})
    assert st["signed_in"] and not st["plan_usage"]
    with pytest.raises(ChatGPTAuthError) as e:
        await auth.access_token()
    assert e.value.code == "CHATGPT_NO_PLAN_USAGE"


async def test_refresh_rotates_and_terminal_error_signs_out(tmp_path):
    srv = OAuthServer()
    auth = make_auth(tmp_path, srv)
    await _complete(auth, srv, {"code": "c", "client_id": CLIENT})
    a = auth.load()
    a["expires_at"] = time.time() + 10           # inside the refresh margin
    auth._save(a)
    assert await auth.access_token() == "at-2"
    path, form = srv.forms[-1]
    assert form == {"grant_type": "refresh_token", "client_id": CLIENT, "refresh_token": "rt-1",
                    "resource": "https://api.openai.com/v1"}
    assert auth.load()["refresh_token"] == "rt-2" and not (tmp_path / "chatgpt" / "refresh.lock").exists()
    a = auth.load()
    a["expires_at"] = 0
    auth._save(a)
    srv.refresh_error = "refresh_token_reused"
    with pytest.raises(ChatGPTAuthError) as e:
        await auth.access_token()
    assert e.value.code == "CHATGPT_SIGNED_OUT"
    left = auth.load()
    assert "refresh_token" not in left and left["client_id"] == CLIENT and left["id_token"]   # kept for re-sign-in


async def test_sign_out_revokes(tmp_path):
    srv = OAuthServer()
    auth = make_auth(tmp_path, srv)
    await _complete(auth, srv, {"code": "c", "client_id": CLIENT})
    assert await auth.sign_out() is True
    path, form = srv.forms[-1]
    assert path.endswith("/revoke") and form == {"token": "rt-1", "token_type_hint": "refresh_token", "client_id": CLIENT}
    assert not auth.status()["signed_in"] and auth.load()["client_id"] == CLIENT


def test_tokens_never_logged(tmp_path):
    from jarves import logs
    tok = id_token("n")
    assert tok not in logs.redact(f"Authorization: Bearer {tok}") and tok not in logs.redact(f"token={tok}")


# ------------------------------------------------------------------ ChatGPT-plan brain
def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def completed(output):
    return {"type": "response.completed", "response": {"id": "r", "status": "completed", "output": output}}


def msg(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


class ResponsesServer:
    def __init__(self, replies):
        self.replies, self.bodies, self.auth_headers = list(replies), [], []

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"models": [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "visibility": "list"},
                                                        {"slug": "hidden", "visibility": "hide"}]})
        assert req.url.path == "/v1/responses"
        self.bodies.append(json.loads(req.content))
        self.auth_headers.append(req.headers["authorization"])
        status, content = self.replies.pop(0)
        if isinstance(content, dict):
            return httpx.Response(status, json=content)
        return httpx.Response(status, content=content, headers={"content-type": "text/event-stream"})


def signed_in_auth(tmp_path, scopes=(PLAN_SCOPE,)):
    auth = ChatGPTAuth(tmp_path)
    auth._save({"client_id": CLIENT, "access_token": "plan-at", "refresh_token": "rt", "scopes": list(scopes),
                "expires_at": time.time() + 3600, "email": "me@example.com", "id_token": "x"})
    return auth


def brain_with(tmp_path, server, **ai):
    cfg = Config()
    for k, v in ai.items():
        setattr(cfg.ai, k, v)
    mem = MemoryStore(tmp_path / "m.db")
    reg = SkillRegistry()
    reg.register(ClockSkill())
    reg.register(MemorySkill(mem))
    b = ChatGPTBrain(cfg.ai, reg, mem, signed_in_auth(tmp_path), transport=httpx.MockTransport(server.handler))
    return b, mem


def test_parse_sse():
    evs = parse_sse(["event: x", 'data: {"type":"a"}', "", "data: [DONE]", "", 'data: {"type":"b"}'])
    assert [e["type"] for e in evs] == ["a", "b"]


async def test_plan_brain_tool_round_trip(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    srv = ResponsesServer([
        (200, sse({"type": "response.created"},
                  completed([{"type": "reasoning", "id": "rs", "summary": []},
                             {"type": "function_call", "call_id": "c1", "name": "get_time", "namespace": "jarves",
                              "arguments": "{}"}]))),
        (200, sse({"type": "response.output_text.delta", "delta": "השעה"}, completed([msg("השעה עכשיו 10:00")]))),
        (200, sse(completed([msg("בכיף")]))),
    ])
    b, mem = brain_with(tmp_path, srv)
    assert b.available() == (True, "ok", "")
    assert await b.reply("כמה זמן נשאר עד הערב?") == "השעה עכשיו 10:00"
    first, second = srv.bodies[0], srv.bodies[1]
    for body in srv.bodies:
        assert body["store"] is False and body["stream"] is True and body["model"] == "gpt-6.1-sol"
        assert not {"temperature", "max_output_tokens", "previous_response_id", "metadata", "user"} & body.keys()
        assert all(i.get("role") != "system" for i in body["input"])
    ns = first["tools"][0]
    assert ns["type"] == "namespace" and {t["name"] for t in ns["tools"]} >= {"get_time", "remember"}
    assert {"type": "web_search"} in first["tools"]
    out = [i for i in second["input"] if i.get("type") == "function_call_output"]
    assert out and out[0]["call_id"] == "c1" and json.loads(out[0]["output"])["ok"] is True
    assert not any(i.get("type") == "reasoning" for i in second["input"])
    assert srv.auth_headers[0] == "Bearer plan-at"
    await b.reply("תודה")                                     # history carried statelessly in input
    assert srv.bodies[2]["input"][0]["content"] == "כמה זמן נשאר עד הערב?"
    assert mem.usage_today("ai_requests") == 3
    assert b.auth.models()[0]["slug"] == "gpt-6.1-sol" and "models" not in b.auth.load()   # no models beside secrets


async def test_plan_limit_pauses_without_api_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")      # present, but must NOT be used
    srv = ResponsesServer([(200, sse({"type": "response.failed", "response": {"error": {
        "code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}}))])
    b, _ = brain_with(tmp_path, srv, chatgpt_model="gpt-6-luna")
    with pytest.raises(BrainUnavailable) as e:
        await b.reply("שאלה")
    assert e.value.code == "CHATGPT_LIMIT"
    assert b.available()[1] == "CHATGPT_LIMIT"                   # no more plan requests until restart
    r = await Brain(b.registry, b).respond("ספר לי בדיחה")
    assert r.source == "offline" and r.code == "CHATGPT_LIMIT" and len(srv.bodies) == 1
    assert srv.auth_headers == ["Bearer plan-at"]


async def test_web_search_dropped_when_plan_disallows(tmp_path):
    srv = ResponsesServer([
        (400, {"error": {"code": "subscription_sharing_unsupported_capability", "param": "tools[1].web_search",
                         "message": "unsupported"}}),
        (200, sse(completed([msg("בסדר")]))),
    ])
    b, _ = brain_with(tmp_path, srv, chatgpt_model="gpt-6-luna")
    assert await b.reply("מה מזג האוויר?") == "בסדר"
    assert {"type": "web_search"} not in srv.bodies[1]["tools"] and "web search off" in b.describe()


@pytest.mark.parametrize("status,body,code", [
    (403, {"error": {"code": "subscription_sharing_user_not_eligible"}}, "CHATGPT_NOT_ELIGIBLE"),
    (401, {"detail": "nope"}, "CHATGPT_AUTH"),
    (400, {"error": {"code": "subscription_sharing_unsupported_capability", "param": "model"}}, "CHATGPT_UNSUPPORTED"),
])
async def test_plan_errors_mapped(tmp_path, status, body, code):
    srv = ResponsesServer([(status, body)])
    b, _ = brain_with(tmp_path, srv, chatgpt_model="gpt-6-luna", web_search=False)
    with pytest.raises(BrainUnavailable) as e:
        await b.reply("x")
    assert e.value.code == code and len(srv.bodies) == 1          # no blind retries


async def test_unavailable_retries_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    srv = ResponsesServer([(503, {"detail": "busy"})] * 3)
    b, _ = brain_with(tmp_path, srv, chatgpt_model="gpt-6-luna")
    with pytest.raises(BrainUnavailable) as e:
        await b.reply("x")
    assert e.value.code == "CHATGPT_UNAVAILABLE" and len(srv.bodies) == 3


async def _nosleep(_s):
    return None


def test_signed_out_and_no_scope_unavailable(tmp_path):
    cfg = Config()
    b = ChatGPTBrain(cfg.ai, SkillRegistry(), MemoryStore(tmp_path / "m.db"), ChatGPTAuth(tmp_path))
    assert b.available()[1] == "CHATGPT_SIGNED_OUT"
    b.auth = signed_in_auth(tmp_path, scopes=())
    assert b.available()[1] == "CHATGPT_NO_PLAN_USAGE"


# ------------------------------------------------------------------ Core connection modes
async def _core(home, cfg, connection):
    from jarves.app import Core
    cfg.ai.connection = connection
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    return c


async def test_chatgpt_connection_never_uses_api_voice(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    c = await _core(home, cfg, "chatgpt")
    try:
        assert c.voice_mode == "push_to_talk"                     # plan has no live voice; key is ignored
        assert type(c.agent).__name__ == "ChatGPTBrain"
        snap = c.snapshot()
        assert snap["ai_connection"] == "chatgpt" and snap["components"]["ai"]["status"] == "unavailable"
        assert "CHATGPT_SIGNED_OUT" in snap["components"]["ai"]["detail"]
    finally:
        await c.stop()
    from jarves.voice.tts import tts_chain
    assert all(e.quality != "cloud" for e in tts_chain(cfg.voice, allow_cloud=False))


async def test_combined_connection_live_voice_labelled_billed(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    c = await _core(home, cfg, "combined")
    try:
        assert c.voice_mode == "live" and type(c.agent).__name__ == "ChatGPTBrain"
        assert "billed to your API key" in c.snapshot()["components"]["voice"]["detail"]
    finally:
        await c.stop()


async def test_combined_without_key_falls_back_to_local_voice(home, cfg):
    c = await _core(home, cfg, "combined")
    try:
        assert c.voice_mode == "push_to_talk"
    finally:
        await c.stop()


def test_setup_writes_connection_keeping_other_settings(tmp_path):
    import tomllib
    from jarves.cli import set_connection
    from jarves.config import load_config
    p = tmp_path / "config.toml"
    set_connection(p, "chatgpt")
    assert load_config(p)[0].ai.connection == "chatgpt"
    p.write_text('assistant_name = "J"\n\n[ai]\nweb_search = false\n\n[voice]\nvoice = "cedar"\n', encoding="utf-8")
    set_connection(p, "combined")
    set_connection(p, "api_key")
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    assert raw["ai"] == {"connection": "api_key", "web_search": False} and raw["voice"]["voice"] == "cedar"


@pytest.mark.parametrize("connection,typed,spoken,backend", [
    ("api_key", "api_key", "api_key", "api_key"),
    ("chatgpt", "chatgpt_plan", "local", "chatgpt_plan"),
    ("combined", "chatgpt_plan", "api_key", "api_key"),       # CR-04: spoken turns are billed to the key
])
async def test_status_reports_who_pays(home, cfg, monkeypatch, connection, typed, spoken, backend):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    c = await _core(home, cfg, connection)
    try:
        b = c.snapshot()["billing"]
        assert (b["typed_text"], b["spoken_voice"], b["voice_backend"]) == (typed, spoken, backend)
    finally:
        await c.stop()
