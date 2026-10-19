"""Usage monitor (SW-V2 M5, spec section 10, D10/D13): Unknown stays Unknown, metrics never mixed, 75/90/100 alerts
once per meter/window and persisted across a restart, provider data labelled as such, Costs API only with an admin
key, the voice tool, the dashboard API and the plan-limit path (no fallback to the paid API)."""
import json
import time
from datetime import datetime

import httpx
import pytest

from jarves import backup, features, usage  # noqa: F401  (backup registers its feature)
from jarves.config import Config
from jarves.memory import MemoryStore
from jarves.store import Store


@pytest.fixture
def sink(monkeypatch):
    got = []
    monkeypatch.setattr(usage, "ALERT_SINK", got.append)
    monkeypatch.delenv(usage.ADMIN_ENV, raising=False)
    return got


def set_usage(home, kind, n, day=None):
    m = MemoryStore(home / "memory.db")
    m._x("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) ON CONFLICT(day, kind) DO UPDATE SET count = "
         "excluded.count", (day or datetime.now().date().isoformat(), kind, n))
    m.close()


def by_id(ms):
    return {m.id: m for m in ms}


def test_fresh_install_reports_unknown_not_a_fake_percent(home, sink):
    mon = usage.Monitor(home, Config())
    ms = by_id(mon.meters())
    q = ms["chatgpt_plan.quota"]
    assert q.category == "unavailable" and q.percent() is None and q.used is None and q.limit is None
    d = q.to_dict()
    assert d["used_text"] == d["limit_text"] == d["percent_text"] == d["reset_text"] == "Unknown"
    assert ms["openai_api.spend_month"].category == "estimate"       # labelled, never shown as the bill
    assert ms["chatgpt_plan.jarves_requests"].scope == "this_install"
    assert mon.check_alerts() == [] and sink == []
    f = mon.footer()
    assert f["chatgpt_plan"]["meter"]["percent_text"] == "Unknown"
    assert "לא יודע" in mon.summary_he()


def test_metrics_are_never_mixed(home, sink):
    units = {m.id: m.unit for m in usage.Monitor(home, Config()).meters()}
    assert units["openai_api.spend_month"] == "usd"
    assert units["openai_api.voice_minutes"] == "minutes"
    assert units["jarves.ai_requests"] == "requests"
    assert units["chatgpt_plan.quota"] == "percent"
    for m in usage.Monitor(home, Config()).meters():
        assert m.category in usage.CATEGORIES


def test_thresholds_fire_once_per_window_and_survive_restart(home, sink):
    cfg = Config()
    cfg.ai.daily_request_limit = 100
    set_usage(home, "ai_requests", 76)
    assert [a["threshold"] for a in usage.Monitor(home, cfg).check_alerts()] == [75]
    assert usage.Monitor(home, cfg).check_alerts() == []                       # no storm
    set_usage(home, "ai_requests", 100)                                        # 90 and 100 crossed together
    fired = usage.Monitor(home, cfg).check_alerts()
    assert [a["threshold"] for a in fired] == [100] and fired[0]["priority"] == "high"
    assert usage.Monitor(home, cfg).check_alerts() == []                       # "reboot": a new instance
    hist = {(h["meter"], h["threshold"]) for h in usage.Monitor(home, cfg).alert_history()}
    assert {("jarves.ai_requests", t) for t in (75, 90, 100)} <= hist
    assert len(sink) == 2
    # a real new window (tomorrow) may alert again; the clock is the only thing that changes the window
    tomorrow = time.time() + 86400
    set_usage(home, "ai_requests", 80, day=datetime.fromtimestamp(tomorrow).date().isoformat())
    again = usage.Monitor(home, cfg, clock=lambda: tomorrow).check_alerts()
    assert [(a["meter"], a["threshold"]) for a in again] == [("jarves.ai_requests", 75)]


def test_custom_thresholds_and_validation(home, sink):
    assert usage.set_settings(home, {"thresholds": "50, 100"})["thresholds"] == [50, 100]
    for bad in ({"thresholds": "0,200"}, {"thresholds": "x"}, {"api_monthly_budget_usd": -3}, {"nope": 1},
                {"costs_api": "maybe"}):
        with pytest.raises(ValueError):
            usage.set_settings(home, bad)
    cfg = Config()
    cfg.ai.daily_realtime_minutes = 10
    set_usage(home, "voice_seconds", 6 * 60)
    fired = usage.Monitor(home, cfg).check_alerts()
    assert [(a["meter"], a["threshold"]) for a in fired] == [("openai_api.voice_minutes", 50)]


def test_budget_gives_the_estimate_a_percent_only_when_the_user_set_one(home, sink, monkeypatch):
    monkeypatch.setattr(usage.Monitor, "_estimate_month_usd", lambda self: 4.0)
    m = by_id(usage.Monitor(home, Config()).meters())["openai_api.spend_month"]
    assert m.limit is None and m.percent() is None
    usage.set_settings(home, {"api_monthly_budget_usd": 5})
    m = by_id(usage.Monitor(home, Config()).meters())["openai_api.spend_month"]
    assert m.percent() == 80.0 and m.limit_source == "your setting" and m.category == "estimate"
    assert [a["threshold"] for a in usage.Monitor(home, Config()).check_alerts()] == [75]


def test_plan_limit_error_is_authoritative_and_clears_only_on_evidence(home, sink):
    st = Store(home / "store.db")
    t0 = time.time() - 60
    st.ingest({"ts": t0, "level": "WARNING", "component": "chatgpt", "code": usage.PLAN_LIMIT_CODE,
               "msg": "ChatGPT plan request failed"})
    st.commit()
    mon = usage.Monitor(home, Config())
    q = by_id(mon.meters())["chatgpt_plan.quota"]
    assert q.state == "limit_reached" and q.category == "provider_authoritative" and q.reset_at is None
    fired = mon.check_alerts()
    assert [a["threshold"] for a in fired] == [100] and usage.Monitor(home, Config()).check_alerts() == []
    assert "מגבלת השימוש" in mon.summary_he()
    st.ingest({"ts": t0 + 30, "level": "INFO", "component": "ai", "code": "AI_REPLY", "msg": "ChatGPT plan replied"})
    st.commit()
    st.close()
    q = by_id(usage.Monitor(home, Config()).meters())["chatgpt_plan.quota"]
    assert q.category == "unavailable" and q.state == "unknown"


def test_rate_limit_headers_are_provider_data_and_never_alert(home, sink):
    usage.observe_headers("openai_api", {"x-ratelimit-limit-requests": "500", "x-ratelimit-remaining-requests": "499",
                                         "x-ratelimit-reset-requests": "120ms", "x-ratelimit-limit-tokens": "30000",
                                         "x-ratelimit-remaining-tokens": "1000", "x-ratelimit-reset-tokens": "6m0s",
                                         "set-cookie": "secret=1"}, home=home)
    ms = by_id(usage.Monitor(home, Config()).meters())
    r, t = ms["openai_api.rate_api_requests"], ms["openai_api.rate_api_tokens"]
    assert r.used == 1 and r.limit == 500 and r.unit == "requests" and r.category == "provider_authoritative"
    assert t.percent() > 90 and not t.alerts
    assert usage.Monitor(home, Config()).check_alerts() == []
    raw = json.dumps(usage._get_obs(usage._connect(home), "openai_api.headers")[0])
    assert "cookie" not in raw and "secret" not in raw
    assert usage._duration_s("1h2m3s") == 3723 and usage._duration_s("20ms") == 0.02
    usage.observe_realtime_rate_limits([{"name": "tokens", "limit": 1000, "remaining": 900, "reset_seconds": 5}], home)
    assert by_id(usage.Monitor(home, Config()).meters())["openai_api.rate_realtime_tokens"].used == 100


def test_plan_window_headers_and_no_invented_reset(home, sink):
    now = [time.time()]
    hdr = {"x-codex-primary-used-percent": "80", "x-codex-primary-window-minutes": "300",
           "x-codex-primary-reset-at": str(now[0] + 3600)}
    usage.observe_headers("chatgpt_plan", hdr, home=home)
    mon = usage.Monitor(home, Config(), clock=lambda: now[0])
    q = by_id(mon.meters())["chatgpt_plan.quota"]
    assert q.percent() == 80 and q.confidence == "medium" and q.window == "300 min"
    assert [a["threshold"] for a in mon.check_alerts()] == [75]
    hdr["x-codex-primary-reset-at"] = str(now[0] + 3590)     # jitter: same window
    usage.observe_headers("chatgpt_plan", hdr, home=home)
    assert mon.check_alerts() == []
    hdr["x-codex-primary-used-percent"] = "10"                # a drop alone is NOT a reset
    hdr["x-codex-primary-reset-at"] = str(now[0] + 7 * 3600)
    usage.observe_headers("chatgpt_plan", hdr, home=home)
    k1 = by_id(mon.meters())["chatgpt_plan.quota"].window_key
    now[0] += 3700                                           # the reported reset really passed
    hdr["x-codex-primary-used-percent"] = "77"
    usage.observe_headers("chatgpt_plan", hdr, home=home)
    k2 = by_id(mon.meters())["chatgpt_plan.quota"].window_key
    assert k1 != k2 and [a["threshold"] for a in mon.check_alerts()] == [75]


def test_costs_api_only_with_admin_key(home, sink, monkeypatch):
    def never(*a):
        raise AssertionError("no network without an admin key")
    assert usage.Monitor(home, Config(), http=never).refresh_costs() is None
    monkeypatch.setenv(usage.ADMIN_ENV, "sk-admin-test-not-real")
    calls = []

    def fake(url, headers, params):
        calls.append(params)
        if "page" not in params:
            return 200, {"data": [{"results": [{"amount": {"value": 1.25, "currency": "usd"}}]}], "has_more": True,
                         "next_page": "p2"}
        return 200, {"data": [{"results": [{"amount": {"value": 0.75, "currency": "usd"}}]}], "has_more": False}
    usage.set_settings(home, {"api_monthly_budget_usd": 4})
    mon = usage.Monitor(home, Config(), http=fake)
    assert mon.refresh_costs()["usd"] == 2.0 and len(calls) == 2
    mon.refresh_costs()
    assert len(calls) == 2                                   # cached: at most hourly
    m = by_id(mon.meters())["openai_api.spend_month"]
    assert m.category == "provider_authoritative" and m.percent() == 50.0 and m.scope == "organization"
    bad = usage.Monitor(home, Config(), http=lambda *a: (500, None))
    assert bad.refresh_costs(force=True)["stale_error"] == "HTTP 500"
    m = by_id(bad.meters())["openai_api.spend_month"]
    assert m.used == 2.0 and m.state == "stale"              # last good number kept, marked stale
    usage.set_settings(home, {"costs_api": "off"})
    assert by_id(usage.Monitor(home, Config()).meters())["openai_api.spend_month"].category == "estimate"


async def test_voice_tool_answers_in_hebrew(home, sink):
    from jarves.skills import SkillRegistry
    reg = SkillRegistry()
    reg.register(usage.make_skill(home, Config()))
    r = await reg.call("usage_status", {})
    assert r.ok and "ChatGPT" in r.message and r.data["meters"][0]["percent_text"] == "Unknown"


def test_features_registered_with_windows_and_pi_status():
    for fid in ("usage_monitor", "backup"):
        f = features.CATALOG[fid]
        assert set(f.targets) == {"windows", "linux_arm64_pi"}
        assert f.targets["linux_arm64_pi"]["status"] in ("implemented_unverified", "planned", "blocked")
        assert f.targets["linux_arm64_pi"].get("blocker")
    assert features.Features().owner("usage", "usage_status") == "usage_monitor"


async def test_plan_route_headers_are_observed(tmp_path, sink, monkeypatch):
    from jarves.chatgpt_auth import PLAN_SCOPE, ChatGPTAuth
    from jarves.chatgpt_brain import ChatGPTBrain
    from jarves.skills import SkillRegistry
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    usage.attach(tmp_path)

    def handler(req):
        body = "data: " + json.dumps({"type": "response.completed", "response": {"output": [
            {"type": "message", "content": [{"type": "output_text", "text": "שלום"}]}]}}) + "\n\n"
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream",
                                                                   "x-codex-primary-used-percent": "42"})
    auth = ChatGPTAuth(tmp_path)
    auth._save({"client_id": "c", "access_token": "plan-at", "refresh_token": "rt", "scopes": [PLAN_SCOPE],
                "expires_at": time.time() + 3600, "email": "", "id_token": "x"})
    cfg = Config()
    cfg.ai.chatgpt_model = "gpt-6-luna"
    b = ChatGPTBrain(cfg.ai, SkillRegistry(), MemoryStore(tmp_path / "memory.db"), auth,
                     transport=httpx.MockTransport(handler))
    try:
        await b._stream({"model": "gpt-6-luna", "input": []})
    finally:
        usage.attach(None)
    q = by_id(usage.Monitor(tmp_path, Config()).meters())["chatgpt_plan.quota"]
    assert q.percent() == 42 and q.category == "provider_authoritative"
