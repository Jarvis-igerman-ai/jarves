"""V7 M2 (#416): OpenRouter adapter against a FAKE server (stdlib http.server on 127.0.0.1). No real network, no real key."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace as NS

import pytest

from jarves import features as feat
from jarves import openrouter as orx
from jarves.model_policy import DEFAULT_MODELS, ModelConfigError, ModelInfo, ModelRegistry, SpendPolicy

KEY = "sk-or-v1-FAKEKEY1234567890abcdef"
FREE = "meta-llama/fake-8b:free"
REG = ModelRegistry(list(DEFAULT_MODELS) + [
    ModelInfo(FREE, "openrouter", paid=False, quality=4),
    ModelInfo("vendor/paid-1", "openrouter", input_per_1m=1.0, output_per_1m=2.0, quality=7),
    ModelInfo("vendor/sneaky", "openrouter", paid=False, quality=4)])      # free flag but no :free suffix


class Fake:
    """Documented shape: POST /api/v1/chat/completions -> {model, choices[0].message.content, usage{prompt_tokens,
    completion_tokens, cost}}."""

    def __init__(self):
        self.reqs, self.auth = [], []
        self.status, self.headers, self.body = 200, {}, None
        self.resolved, self.cost = FREE, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                outer.reqs.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
                outer.auth.append(self.headers.get("authorization", ""))
                body = outer.body if outer.body is not None else {
                    "id": "gen-1", "model": outer.resolved, "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "cost": outer.cost}}
                raw = json.dumps(body).encode()
                self.send_response(outer.status)
                for k, v in outer.headers.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}/api/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.close()


class Pub(orx.OpenRouterClient):
    """Tests send public text unless they say otherwise (the production default is private = refused)."""

    def chat(self, messages, model=orx.FREE_ROUTER, **kw):
        kw.setdefault("content_class", "public")
        return super().chat(messages, model, **kw)


def client(fake, policy=None):
    return Pub(policy or SpendPolicy(), REG, key=KEY, base=fake.base, _test_base=True)


MSG = [{"role": "user", "content": "hello"}]


def test_free_call_parses_usage_and_logs_resolved_model(fake):
    fake.resolved = "other/model-x:free"
    r = client(fake).chat(MSG, orx.FREE_ROUTER)
    assert (r.text, r.requested_model, r.resolved_model, r.tokens_in, r.tokens_out, r.cost_usd) == (
        "hi", "openrouter/free", "other/model-x:free", 7, 3, 0.0)
    assert "not private" in r.privacy
    path, body = fake.reqs[0]
    assert path == "/api/v1/chat/completions" and body["model"] == "openrouter/free" and body["usage"] == {"include": True}
    assert fake.auth == [f"Bearer {KEY}"]


def test_free_only_blocks_every_non_free_model_before_any_request(fake):
    c = client(fake, SpendPolicy(mode="free-only", daily_cap_usd=9, task_cap_usd=9, allowed_providers={"": ["openrouter"]}))
    for m in ("vendor/paid-1", "vendor/sneaky", "unknown/x:free", "gpt-6-sol", "local-small", "../x"):
        with pytest.raises(orx.PolicyBlocked) as e:
            c.chat(MSG, m)
        assert e.value.exception.wanted
    assert fake.reqs == []                                # nothing was ever sent


def test_paid_only_with_explicit_caps_and_cost_recorded(fake):
    p = SpendPolicy(mode="balanced", daily_cap_usd=1, task_cap_usd=1, allowed_providers={"p": ["openrouter"]})
    fake.resolved, fake.cost = "vendor/paid-1", 0.0004
    r = client(fake, p).chat(MSG, "vendor/paid-1", project="p", agent="a")
    assert r.cost_usd == 0.0004 and p.spent_today == pytest.approx(0.0004) and p.spent_by_agent["a"] == pytest.approx(0.0004)
    with pytest.raises(orx.PolicyBlocked):                 # no allow-list for this project: paid refused
        client(fake, p).chat(MSG, "vendor/paid-1", project="other")


def test_unexpected_charge_or_paid_resolution_in_free_only(fake):
    p = SpendPolicy()
    fake.cost = 0.01
    with pytest.raises(orx.OpenRouterError) as e:
        client(fake, p).chat(MSG, FREE)
    assert e.value.code == "OPENROUTER_UNEXPECTED_CHARGE" and p.spent_today == pytest.approx(0.01)       # recorded
    fake.cost, fake.resolved = 0, "vendor/paid-1"
    with pytest.raises(orx.OpenRouterError) as e:
        client(fake).chat(MSG, orx.FREE_ROUTER)
    assert e.value.code == "OPENROUTER_UNEXPECTED_CHARGE"


@pytest.mark.parametrize("cls", ["private", "code"])
def test_private_content_needs_explicit_project_approval(fake, cls):
    for p in (SpendPolicy(), SpendPolicy(allowed_providers={"proj": ["local"]}), SpendPolicy(allowed_providers={"proj": []})):
        with pytest.raises(orx.PolicyBlocked) as e:
            client(fake, p).chat(MSG, FREE, project="proj", content_class=cls)
        assert "never goes to OpenRouter" in e.value.exception.reason
    assert fake.reqs == []
    ok = SpendPolicy(allowed_providers={"proj": ["openrouter"]})
    assert client(fake, ok).chat(MSG, FREE, project="proj", content_class=cls).text == "hi"
    with pytest.raises(orx.PolicyBlocked):
        client(fake, ok).chat(MSG, FREE, project="proj", content_class="secret")           # unknown class: fail closed


@pytest.mark.parametrize("status,code", [(401, "OPENROUTER_AUTH"), (402, "OPENROUTER_CREDITS"), (429, "OPENROUTER_RATE_LIMIT"),
                                         (400, "OPENROUTER_BAD_REQUEST"), (503, "OPENROUTER_PROVIDER_ERROR"),
                                         (302, "OPENROUTER_REDIRECT")])
def test_error_mapping(fake, status, code):
    fake.status, fake.headers = status, ({"location": "http://evil.example/"} if status == 302 else {"retry-after": "7"})
    with pytest.raises(orx.OpenRouterError) as e:
        client(fake).chat(MSG, FREE)
    assert e.value.code == code
    if status == 429:
        assert "7" in e.value.message
    assert len(fake.reqs) == 1                             # the redirect was not followed


def test_error_inside_200_and_bad_shapes(fake):
    fake.body = {"error": {"code": 429, "message": "upstream"}}
    with pytest.raises(orx.OpenRouterError) as e:
        client(fake).chat(MSG, FREE)
    assert e.value.code == "OPENROUTER_RATE_LIMIT"
    fake.body = {"choices": []}
    with pytest.raises(orx.OpenRouterError) as e:
        client(fake).chat(MSG, FREE)
    assert e.value.code == "OPENROUTER_PROVIDER_ERROR"


def test_offline_and_missing_key(fake):
    c = client(fake)
    fake.close()
    with pytest.raises(orx.OpenRouterError) as e:
        c.chat(MSG, FREE)
    assert e.value.code == "OPENROUTER_OFFLINE"
    nokey = orx.OpenRouterClient(SpendPolicy(), REG, base="http://127.0.0.1:9/api/v1", _test_base=True)
    os.environ.pop(orx.KEY_ENV, None)
    with pytest.raises(orx.OpenRouterError) as e:
        nokey.chat(MSG, FREE, content_class="public")
    assert e.value.code == "OPENROUTER_NOT_CONNECTED"


def test_host_allow_list_and_key_never_in_text(fake):
    for bad in ("http://openrouter.ai/api/v1", "https://openrouter.ai.evil.example/api/v1", "https://evil.example/api/v1",
                "https://openrouter.ai@evil.example/api/v1", "https://openrouter.ai:8443/api/v1"):
        with pytest.raises(orx.OpenRouterError):
            orx.OpenRouterClient(SpendPolicy(), REG, key=KEY, base=bad)
    assert orx.OpenRouterClient(SpendPolicy(), REG, key=KEY).base == orx.API
    assert KEY not in repr(client(fake)) and KEY not in orx.redact(f"Authorization: Bearer {KEY}")
    assert KEY not in str(orx.OpenRouterError("X", f"bad {KEY}"))


def test_default_class_is_private_and_free_only_sends_price_ceiling(fake):
    with pytest.raises(orx.PolicyBlocked):
        orx.OpenRouterClient(SpendPolicy(), REG, key=KEY, base=fake.base, _test_base=True).chat(MSG, FREE)
    assert fake.reqs == []
    client(fake).chat(MSG, FREE)
    assert fake.reqs[0][1]["provider"] == {"max_price": {"prompt": 0, "completion": 0}, "allow_fallbacks": False}


def test_understated_cost_is_raised_to_the_computed_one(fake):
    p = SpendPolicy(mode="balanced", daily_cap_usd=1, task_cap_usd=1, allowed_providers={"p": ["openrouter"]})
    fake.resolved, fake.cost = "vendor/paid-1", 0
    r = client(fake, p).chat(MSG, "vendor/paid-1", project="p")
    assert r.cost_usd > 0 and p.spent_today == pytest.approx(r.cost_usd)


def test_bad_arguments(fake):
    c = client(fake)
    for msgs in ([], "hi", [{"role": "user"}], [{"role": "tool", "content": "x"}]):
        with pytest.raises(orx.OpenRouterError):
            c.chat(msgs, FREE)
    for n in (0, True, 10**6):
        with pytest.raises(orx.OpenRouterError):
            c.chat(MSG, FREE, max_tokens=n)


def test_m1_nits_449():
    for bad in (10**400, -10**400):
        with pytest.raises(ModelConfigError):
            SpendPolicy(daily_cap_usd=bad)
        with pytest.raises(ModelConfigError):
            SpendPolicy(agent_cap_usd={"a": bad})
        with pytest.raises(ModelConfigError):
            ModelInfo("x", "p", input_per_1m=bad, output_per_1m=1)
    for bad in ([1, 2], "x", 5, [("a", 1)]):
        with pytest.raises(ModelConfigError):
            SpendPolicy(agent_cap_usd=bad)
        with pytest.raises(ModelConfigError):
            SpendPolicy(allowed_providers=bad)
    assert SpendPolicy(agent_cap_usd={}, allowed_providers={}).agent_cap_usd == {}


def test_feature_is_beta_off_and_cli(tmp_path, capsys):
    f = feat.CATALOG["provider_openrouter"]
    assert f.default_enabled is False and feat.stage_of("provider_openrouter") == "beta" and f.risk == "high"
    assert orx.cmd_models(NS(action="list", json=True), None, tmp_path) == 0
    rows = json.loads(capsys.readouterr().out)
    assert any(r["id"] == "openrouter/free" and "not private" in r["privacy"] for r in rows)
    assert orx.cmd_models(NS(action="status", json=True), None, tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["remote_is_private"] is False
