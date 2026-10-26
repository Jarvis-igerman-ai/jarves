"""V7 D1 (#404): the read-only OpenCode adapter against a FAKE server (never a real one)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fakes"))
import fake_opencode as fo  # noqa: E402

from jarves import features  # noqa: E402
from agents_helpers import agents_cfg  # noqa: E402
from jarves.orchestrator import opencode as oc  # noqa: E402
from jarves.orchestrator import service as svc  # noqa: E402
from jarves.orchestrator.envelope import CODES, TEXTS  # noqa: E402


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", fo.PASSWORD)
    s = fo.FakeOpenCode()
    yield s
    s.close()


def test_health_sessions_events_are_gets_only(server):
    c = oc.Client(server.url)
    assert c.health() == {"healthy": True, "version": "1.0.0"}
    by = {r["provider_session_id"]: r for r in c.sessions()}
    assert by["ses_aaa111"]["state"] == "RUNNING" and by["ses_bbb222"]["state"] == "IDLE"
    assert by["ses_bbb222"]["kind"] == "subagent" and by["ses_aaa111"]["started_at"] == 1761470000.0
    assert "‮" not in by["ses_bbb222"]["name"]                       # untrusted title is cleaned
    ev = c.events(10, timeout=3)
    assert [e["type"] for e in ev] == ["server.connected", "session.status", "message.updated"]
    assert ev[2]["session"] == "ses_aaa111" and "text" not in ev[2]      # event text is never passed on
    assert {m for m, _ in server.requests} == {"GET"}


def test_auth_required_without_password_and_with_wrong_one(server, monkeypatch):
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD")
    with pytest.raises(oc.OpenCodeError) as e:
        oc.Client(server.url)
    assert e.value.code == "AUTH_REQUIRED" and not server.requests        # no request without a password
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "wrong")
    with pytest.raises(oc.OpenCodeError) as e:
        oc.Client(server.url).health()
    assert e.value.code == "AUTH_REQUIRED" and "wrong" not in str(e.value)


@pytest.mark.parametrize("url", ["http://192.168.1.5:4096", "http://example.com", "https://127.0.0.1:4096",
                                 "http://u:p@127.0.0.1:4096", "http://0.0.0.0:4096"])
def test_not_loopback_is_refused_before_any_connection(url, monkeypatch):
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", fo.PASSWORD)
    with pytest.raises(oc.OpenCodeError) as e:
        oc.Client(url)
    assert e.value.code == "NOT_LOOPBACK"
    assert oc.check_loopback("http://localhost:1")[1] == 1 and oc.check_loopback("http://[::1]:7")[1] == 7


def test_no_server_and_server_error_codes(server):
    s2 = fo.FakeOpenCode()
    try:
        with pytest.raises(oc.OpenCodeError) as e:
            oc.Client(s2.url).get_json("/nope")
        assert e.value.code == "SERVER_ERROR"
    finally:
        s2.close()
    server.close()
    with pytest.raises(oc.OpenCodeError) as e:
        oc.Client(server.url, timeout=2).health()
    assert e.value.code == "PROVIDER_MISSING"


def test_every_code_has_english_and_hebrew():
    for code in ("NOT_LOOPBACK", "SERVER_ERROR", "AUTH_REQUIRED", "PROVIDER_MISSING"):
        assert code in CODES and all(TEXTS[code])


def _orch(tmp_path, server, monkeypatch, on=True):
    monkeypatch.setenv("FAKE_CLAUDE_DIR", str(tmp_path / "fake_claude"))      # never the owner's real claude / codex
    cfg = agents_cfg(opencode_url=server.url, codex_observe=False)
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    server.sessions[:] = [dict(s, directory=str(proj).replace("\\", "/")) for s in server.sessions]
    o = svc.Orchestrator(home, cfg, serve=False)
    o.projects_op("add", str(proj), name="proj")
    if on:
        features.Features(cfg, home).set(oc.FEATURE, True)
    return o


def test_switch_off_by_default_no_request(tmp_path, server, monkeypatch):
    f = features.CATALOG[oc.FEATURE]
    assert f.default_enabled is False and f.risk == "high"
    o = _orch(tmp_path, server, monkeypatch, on=False)
    r = o.call("opencode", action="status")
    assert not r["ok"] and "off" in r["text"] and not server.requests
    o.list(force=True)
    assert not server.requests
    o.db.close()


def test_sessions_enter_registry_observe_only_and_writes_refused(tmp_path, server, monkeypatch):
    o = _orch(tmp_path, server, monkeypatch)
    rows = [s for s in o.list(force=True)["sessions"] if s["provider"] == "opencode"]
    assert {s["session"] for s in rows} == {"ses_aaa111", "ses_bbb222"}
    assert all(s["attach"] == "observe_only" and s["capabilities"] == ["observe"] for s in rows)
    t = o.tell("ses_aaa111", "do something")
    assert not t["ok"] and t["code"] == "UNSUPPORTED_ATTACH"
    assert o.start_session("opencode", "proj", "x", approved=True)["code"] == "PROVIDER_MISSING"
    assert o.call("opencode", action="sessions")["ok"] and o.call("opencode", action="events")["ok"]
    assert {m for m, _ in server.requests} == {"GET"}
    assert any(c["check"] == "opencode_server" and c["ok"] for c in o.call("doctor")["checks"])
    o.db.close()


def test_password_never_in_results_or_logs(tmp_path, server, monkeypatch, caplog):
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "hunter2-planted")
    o = _orch(tmp_path, server, monkeypatch)
    r = o.call("opencode", action="status")
    assert r["code"] == "AUTH_REQUIRED" and "hunter2-planted" not in repr(r) + caplog.text
    o.db.close()
