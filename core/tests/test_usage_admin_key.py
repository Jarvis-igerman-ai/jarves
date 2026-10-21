"""Optional read-only OpenAI admin key (docs/OPENAI_ADMIN_KEY.md): saved through the secrets path with hidden input,
never echoed or logged, sent only to the usage/costs endpoint. A local fake endpoint stands in for OpenAI (no network)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarves import connect, features, guides, usage
from jarves.config import Config, secrets_file
from test_connect import make
from test_dashboard import dash, req  # noqa: F401  (fixture + helper)

KEY = "sk-admin-TESTKEY-0123456789-not-real"


@pytest.fixture
def fake_costs(monkeypatch):
    seen: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
            ok = self.headers.get("Authorization") == f"Bearer {KEY}"
            body = json.dumps({"data": [{"results": [{"amount": {"value": 4.25, "currency": "usd"}}]}],
                               "has_more": False} if ok else {"error": "bad key"}).encode()
            self.send_response(200 if ok else 401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(usage, "COSTS_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1/organization/costs")
    yield seen
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(usage.ADMIN_ENV, raising=False)


def test_validation_save_remove_and_no_secret_in_error(home):
    for bad in ("", "sk-proj-abc", "sk-admin-", "sk-admin-a b", "hello"):
        with pytest.raises(ValueError) as e:
            usage.save_admin_key(home, bad)
        assert "Nothing saved" in str(e.value) and "a b" not in str(e.value)
    assert not usage.admin_key_saved(home)
    usage.save_admin_key(home, "  " + KEY + "  ")
    assert usage.admin_key_saved(home) and KEY in secrets_file(home).read_text(encoding="utf-8")
    usage.remove_admin_key(home)
    assert not usage.admin_key_saved(home) and KEY not in secrets_file(home).read_text(encoding="utf-8")


def test_real_http_path_reaches_only_the_costs_endpoint_with_the_key(home, fake_costs):
    usage.save_admin_key(home, KEY)
    d = usage.Monitor(home, Config()).refresh_costs(force=True)
    assert d["usd"] == 4.25
    assert len(fake_costs) == 1 and fake_costs[0]["path"].startswith("/v1/organization/costs")
    assert fake_costs[0]["auth"] == f"Bearer {KEY}"
    usage.remove_admin_key(home)
    assert usage.Monitor(home, Config()).refresh_costs(force=True) is None and len(fake_costs) == 1


def test_wrong_key_is_an_honest_error_not_a_number(home, fake_costs):
    usage.save_admin_key(home, "sk-admin-WRONG-0000000000")
    d = usage.Monitor(home, Config()).refresh_costs(force=True)
    assert d["error"] == "HTTP 401" and "usd" not in d


def test_key_never_logged_or_in_diagnostics(home, caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        usage.save_admin_key(home, KEY)
        usage.remove_admin_key(home)
    assert KEY not in caplog.text


def test_connect_openai_admin_hidden_paste_saves_and_skips(home):
    assert "openai-admin" in connect.ONLY
    conn, out = make(home, ["", KEY], only="openai-admin", yes=False)
    conn.run()
    assert usage.admin_key_saved(home) and conn.results["openai-admin"] == "connected"
    assert KEY not in "\n".join(out)                               # never echoed
    usage.remove_admin_key(home)
    conn, out = make(home, ["", "s"], only="openai-admin")         # Enter = continue, then skip at the prompt
    assert conn.run() == 0 and conn.results["openai-admin"] == "skipped" and not usage.admin_key_saved(home)
    conn, out = make(home, ["", "sk-proj-not-admin"], only="openai-admin")
    assert conn.run() == 1 and conn.results["openai-admin"] == "failed" and not usage.admin_key_saved(home)
    conn, _ = make(home, [], only="openai-admin", yes=True)         # --yes never asks for a key
    assert conn.run() == 0 and conn.results["openai-admin"] == "skipped"


def test_connect_cli_positional_item(home):
    import argparse
    sub = argparse.ArgumentParser().add_subparsers()
    connect.add_cli(sub)
    ns = sub.choices["connect"].parse_args(["openai-admin"])
    assert ns.item == "openai-admin"


def test_status_item_follows_saved_key(home):
    it = {i.id: i for i in connect.status_items(home)}["openai-admin"]
    assert it.state == "missing" and it.optional and "openai-admin" in it.fix_en
    usage.save_admin_key(home, KEY)
    assert {i.id: i for i in connect.status_items(home)}["openai-admin"].state == "connected"


def test_dashboard_save_never_echoes_and_remove(dash, fake_costs):  # noqa: F811
    h = {"X-Jarves": "1"}
    r, body = req(dash, "POST", "/api/usage/admin-key", headers=h, body={"key": "sk-proj-nope"})
    assert r.status == 400 and b"nope" not in body
    r, body = req(dash, "POST", "/api/usage/admin-key", headers=h, body={"key": KEY})
    assert r.status == 200 and json.loads(body) == {"key_present": True} and KEY.encode() not in body
    r, body = req(dash, "GET", "/api/usage")
    assert json.loads(body)["costs_api"]["key_present"] is True and KEY.encode() not in body
    r, body = req(dash, "POST", "/api/usage/refresh", headers=h, body={})
    assert json.loads(body)["costs"]["usd"] == 4.25 and KEY.encode() not in body
    r, body = req(dash, "POST", "/api/usage/admin-key", headers=h, body={"remove": True})
    assert json.loads(body) == {"key_present": False}
    r, _ = req(dash, "POST", "/api/usage/admin-key", cookie=False, headers=h, body={"key": KEY})
    assert r.status in (401, 403)


def test_dashboard_page_and_catalogs_have_the_setting():
    js = (usage.__file__.rsplit("jarves", 1)[0] + "jarves/dashboard_static/usage.js").replace("\\", "/")
    text = open(js, encoding="utf-8").read()
    assert "/api/usage/admin-key" in text and 'type: "password"' in text and "#/guide/api_bill" in text


def test_guide_has_both_languages_steps_and_the_feature_row():
    g = guides.load()["api_bill"]
    assert "usage_admin_key" in g.features
    steps = " ".join(s.get("en", "") + s.get("he", "") if isinstance(s, dict) else s.en + s.he for s in g.steps)
    assert "jarvis connect openai-admin" in steps and "Read only" in steps
    f = features.CATALOG["usage_admin_key"]
    assert f.title_en == "Usage checks (admin key)"
    assert f.targets["windows"]["status"] == "implemented_unverified" and "no real" in f.targets["windows"]["evidence"]
