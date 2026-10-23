"""SW-V4 F surfaces: `jarvis whatsapp ...` (CLI) and the dashboard WhatsApp page over real HTTP, both against the fake
bridge; the Core's event watcher (health + red alert on account.warning) and the health mapping."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from jarves import cli, dashboard
from jarves import whatsapp_bridge as wab
from jarves.config import Config
from jarves.health import Health
from test_dashboard import req  # noqa: F401
from whatsapp_fixtures import BOB, DANA, bridge, clock, wait_until  # noqa: F401  (home = conftest's, JARVES_HOME)


def run(capsys, *argv) -> tuple[int, str]:
    rc = cli.main(["whatsapp", *argv])
    return rc, capsys.readouterr().out


def test_cli_status_mode_allow_inbox_kill_purge_retention(bridge, home, capsys, monkeypatch):
    bridge.phone_last_seen = None                     # the seeded clock is weeks before the real "now" of health
    rc, out = run(capsys, "status", "--json")
    d = json.loads(out)
    assert rc == 0 and d["status"] == "ok" and d["bridge"]["engine"] == "fake" and d["bridge"]["mode"] == "read"
    assert "דנה" not in out and "972541112222" not in out
    rc, out = run(capsys, "mode", "off")
    assert rc == 0 and bridge.policy["mode"] == "off"
    run(capsys, "mode", "read")
    monkeypatch.setattr("builtins.input", lambda _p: "SEND")
    rc, out = run(capsys, "mode", "send")
    assert rc == 1 and "WA_WATCH_WINDOW" in out and bridge.policy["mode"] == "read"          # 14 days first
    monkeypatch.setattr("builtins.input", lambda _p: "nope")
    rc, out = run(capsys, "mode", "send", "--override-watch")
    assert rc == 1 and bridge.policy["mode"] == "read"                                         # typed SEND needed
    rc, out = run(capsys, "allow", "send", "Bob", "Smith")
    assert rc == 0 and bridge.policy["send_allow"] == [BOB]
    rc, out = run(capsys, "allow", "send", "דנה")
    assert rc == 1 and "WA_UNKNOWN_CONTACT" in out                                             # ambiguous: refused
    rc, out = run(capsys, "remove", "send", "+972 54-333-4444")
    assert rc == 0 and bridge.policy["send_allow"] == []
    rc, out = run(capsys, "inbox")
    assert rc == 0 and "Bob Smith" in out
    rc, out = run(capsys, "inbox", "Bob", "Smith")
    assert "Meeting moved" in out and "not opened" in out
    rc, out = run(capsys, "kill")
    assert rc == 0 and bridge.killed and (home / "whatsapp_bridge" / "KILL").exists()
    rc, out = run(capsys, "kill", "--release")
    assert rc == 0 and not bridge.killed
    rc, out = run(capsys, "retention", "7", "--keep-nothing", "off")
    assert rc == 0 and bridge.policy["retention_days"] == 7
    rc, out = run(capsys, "purge", "--yes")
    assert rc == 0 and json.loads(out)["deleted"] > 0
    rc, out = run(capsys, "audit", "--json")
    assert rc == 0 and json.loads(out) == {"lines": []}


def test_cli_kill_works_when_the_bridge_is_down(home, capsys):
    rc, out = run(capsys, "kill")
    d = json.loads(out)
    assert rc == 0 and d["file"] is True and d["bridge"] is False and (home / "whatsapp_bridge" / "KILL").exists()
    rc, out = run(capsys, "status")
    assert rc == 1 and "bridge not running" in out


@pytest.mark.windows_only
def test_cli_send_goes_through_the_approval_popup(bridge, home, monkeypatch):
    """The real popup process (invisible test window that presses Approve itself)."""
    from jarves import whatsapp_admin
    bridge.policy.update(mode="send", send_allow=[BOB])
    import time as _t
    bridge._clock = _t.time                                   # the popup and the CLI mint on the real clock
    bridge.hour = lambda: 12
    monkeypatch.setattr("jarves.whatsapp_policy.local_hour", lambda ts=None: 12)
    r = whatsapp_admin.send(home, "Bob Smith", "from the CLI", test_invoke="approve")
    assert r["id"] and bridge.outbox[-1]["text"] == "from the CLI"
    with pytest.raises(wab.BridgeError) as e:
        whatsapp_admin.send(home, "Bob Smith", "denied one", test_invoke="deny")
    assert e.value.code == "WA_APPROVAL_MISSING" and len(bridge.outbox) == 1


@pytest.fixture
def dash(home, bridge):
    bridge.phone_last_seen = None
    d = dashboard.Dashboard(home, Config()).start()
    yield d
    d.stop()


def test_dashboard_page_and_local_writes(dash, bridge):
    r, body = req(dash, "GET", "/api/whatsapp")
    d = json.loads(body)
    assert r.status == 200 and d["health"]["status"] == "ok" and d["local"] is True
    assert d["status"]["mode"] == "read" and "error" not in d, d.get("error")
    assert any(c["id"] == DANA.replace("@", "~") for c in d["chats"]), d["chats"]
    assert d["feature"]["id"] == "whatsapp_bridge" and "Terms" in d["tos"]
    r, body = req(dash, "GET", f"/api/whatsapp/messages?chat={DANA.replace('@', '~')}")
    m = json.loads(body)
    assert r.status == 200 and m["untrusted"] is True and m["messages"][0]["text"].startswith("היי")
    h = {"X-Jarves": "1"}
    r, _ = req(dash, "POST", "/api/whatsapp/allow", headers=h, body={"list": "send", "op": "add", "chat": "Bob Smith"})
    assert r.status == 200 and bridge.policy["send_allow"] == [BOB]
    r, _ = req(dash, "POST", "/api/whatsapp/kill", headers=h, body={"on": True})
    assert r.status == 200 and bridge.killed
    r, _ = req(dash, "POST", "/api/whatsapp/kill", headers=h, body={"on": False})
    assert r.status == 200 and not bridge.killed
    r, body = req(dash, "POST", "/api/whatsapp/purge", headers=h, body={})
    assert r.status == 400                                                 # needs {"confirm": true}
    r, body = req(dash, "POST", "/api/whatsapp/mode", headers=h, body={"mode": "off"})
    assert r.status == 200 and bridge.policy["mode"] == "off"
    js = (dashboard.STATIC / "whatsapp.js").read_text(encoding="utf-8")
    assert "PAGES.whatsapp" in js and "innerHTML" not in js


def test_health_view_states(clock):
    base = {"linked": True, "connection": "connected", "mode": "read", "counts": {"unread": 2}, "rate": {}}
    assert wab.health_view(base, clock())[0] == "ok"
    assert wab.health_view({**base, "mode": "off"})[0] == "disabled"
    assert wab.health_view({**base, "killed": True, "kill_reason": "owner_cli"})[1] == "killed (owner_cli)"
    assert wab.health_view({**base, "connection": "logged_out"})[0] == "unavailable"
    assert wab.health_view({**base, "linked": False, "connection": "not_linked"})[0] == "degraded"
    s, detail, _ = wab.health_view({**base, "phone_last_seen": clock() - 11 * 86400}, clock())
    assert s == "degraded" and "11 days" in detail
    assert wab.health_view({**base, "rate": {"hour": 10}}, clock())[0] == "degraded"


async def test_watcher_follows_events_and_raises_the_red_alert(bridge, home):
    alerts: list[str] = []
    health = Health()
    bridge.phone_last_seen = None
    w = wab.Watcher(home, health, alerts.append)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    try:
        await asyncio.wait_for(w.connected.wait(), 20)
        await asyncio.to_thread(wait_until, lambda: health.components.get("whatsapp_bridge"))
        assert health.components["whatsapp_bridge"].status == "ok"
        await asyncio.to_thread(bridge.warn, "account_at_risk")
        await asyncio.to_thread(wait_until, lambda: "account_warning" in alerts)
        await asyncio.to_thread(wait_until, lambda: health.components["whatsapp_bridge"].status == "unavailable")
        assert "account warning" in health.components["whatsapp_bridge"].detail
    finally:
        stop.set()
        await asyncio.wait_for(task, 20)


def test_send_feature_only_active_in_send_mode(bridge, home):
    from jarves import features as feat
    f = feat.Features(Config(), home)
    f.set("whatsapp_bridge", True, by="test")
    f.set("whatsapp_send", True, by="test")
    assert f.active("whatsapp_bridge") is True
    assert f.active("whatsapp_send") is False and "send mode" in f.reason("whatsapp_send")
    bridge.policy["mode"] = "send"
    f2 = feat.Features(Config(), home)
    assert f2.active("whatsapp_send") is True


def test_instruction_lines_follow_the_switch(home):
    from jarves import features as feat
    f = feat.Features(Config(), home)
    agent = SimpleNamespace(instructions=lambda: "base")
    wab.augment_instructions(agent, f)
    assert agent.instructions() == "base"
    live = SimpleNamespace(session_config=lambda: {"instructions": "live"})
    wab.attach_live(live, SimpleNamespace(active=lambda fid: True))
    assert "WhatsApp" in live.session_config()["instructions"]


# ---- no bridge program: say so plainly, disable Link/Start, one shared setup flow (issue #17)
def test_missing_bridge_program_is_reported_everywhere(home, capsys, monkeypatch):
    monkeypatch.setattr(wab, "bridge_command", lambda h: None)
    monkeypatch.setattr(wab, "status_or_none", lambda h: None)              # nothing running either
    assert wab.bridge_installed(home) is False
    dash = dashboard.Dashboard(home, Config()).start()
    try:
        r, body = req(dash, "GET", "/api/whatsapp")
        d = json.loads(body)
        assert d["bridge_installed"] is False and d["status"] is None
        assert "reinstall JARVES" in d["not_installed"]["en"] and d["not_installed"]["he"]
        h = {"X-Jarves": "1"}
        r, body = req(dash, "POST", "/api/whatsapp/setup", headers=h, body={})
        s = json.loads(body)
        assert r.status == 200 and s["step"] == "not_installed" and s["ok"] is False and s["message_he"]
        for path in ("start", "link"):
            r, body = req(dash, "POST", f"/api/whatsapp/{path}", headers=h, body={})
            assert r.status == 503 and json.loads(body)["code"] == "WA_NOT_INSTALLED"
        r, body = req(dash, "POST", "/api/connect/run", headers=h, body={"id": "whatsapp"})
        c = json.loads(body)
        assert r.status == 200 and c["started"] is False and "reinstall JARVES" in c["message_en"]
    finally:
        dash.stop()
    rc, out = run(capsys, "status", "--json")
    assert json.loads(out)["bridge_installed"] is False
    rc, out = run(capsys, "setup")
    assert rc == 1 and "not installed" in out
    js = (dashboard.STATIC / "whatsapp.js").read_text(encoding="utf-8")
    assert "cantRun" in js and "bridge_installed" in js and '"setup"' in js


def test_setup_flow_starts_links_and_never_scans_for_you(dash, bridge, home):
    h = {"X-Jarves": "1"}
    mode_before = bridge.policy["mode"]
    r, body = req(dash, "POST", "/api/whatsapp/setup", headers=h, body={})
    s = json.loads(body)
    assert r.status == 200 and s["ok"] is True and s["step"] in ("awaiting_scan", "already_linked")
    r, body = req(dash, "POST", "/api/connect/run", headers=h, body={"id": "whatsapp"})
    c = json.loads(body)
    assert r.status == 200 and c["started"] is True and c["open"] == "#/whatsapp"
    assert bridge.policy["mode"] == mode_before                             # setup itself changes no mode


# ---- #17: answering YES to read-only also turns the reading switch on (one backend), NO leaves it off
def _feat(home, fid):
    from jarves import features as feat
    return feat.Features(Config(), home).enabled(fid)


def test_enable_reading_yes_turns_switch_on_and_never_send(bridge, home):
    from jarves import whatsapp_admin as wad
    bridge.policy["mode"] = "off"
    assert _feat(home, "whatsapp_bridge") is False
    r = wad.enable_reading(home, by="test")
    assert bridge.policy["mode"] == "read" and r["feature_on"] is True
    assert _feat(home, "whatsapp_bridge") is True and _feat(home, "whatsapp_send") is False
    assert "it never sends" in r["message"] and r["message_he"]


def test_cli_setup_yes_and_no(bridge, home, monkeypatch, capsys):
    from jarves import whatsapp_admin as wad
    monkeypatch.setattr(wad, "_link", lambda h, a: 0)
    args = SimpleNamespace()
    bridge.policy["mode"] = "off"
    monkeypatch.setattr(wad, "_confirm", lambda *a, **k: False)
    assert wad._setup(home, args) == 0
    out = capsys.readouterr().out
    assert bridge.policy["mode"] == "off" and not _feat(home, "whatsapp_bridge")
    assert "jarvis features on whatsapp_bridge" in out
    monkeypatch.setattr(wad, "_confirm", lambda *a, **k: True)
    assert wad._setup(home, args) == 0
    assert "never sends" in capsys.readouterr().out
    assert bridge.policy["mode"] == "read" and _feat(home, "whatsapp_bridge") and not _feat(home, "whatsapp_send")


def test_status_and_mode_read_show_the_off_hint(bridge, home, capsys):
    bridge.phone_last_seen = None
    rc, out = run(capsys, "status")
    assert "reading switch: off" in out or "מתג הקריאה כבוי" in out
    assert json.loads(run(capsys, "status", "--json")[1])["reading_switch"] == "off"
    rc, out = run(capsys, "mode", "read")
    assert "jarvis features on whatsapp_bridge" in out
    from jarves import features as feat
    feat.Features(Config(), home).set("whatsapp_bridge", True, by="test")
    rc, out = run(capsys, "mode", "read")
    assert "features on" not in out


def test_dashboard_yes_flag_and_off_hint(dash, bridge, home):
    h = {"X-Jarves": "1"}
    d = json.loads(req(dash, "GET", "/api/whatsapp")[1])
    assert d["reading_switch"]["on"] is False and "features on whatsapp_bridge" in d["reading_switch"]["fix"]
    req(dash, "POST", "/api/whatsapp/mode", headers=h, body={"mode": "read"})          # plain mode: switch untouched
    assert not _feat(home, "whatsapp_bridge")
    r, body = req(dash, "POST", "/api/whatsapp/mode", headers=h, body={"mode": "read", "enable_reading": True})
    assert r.status == 200 and json.loads(body)["feature_on"] is True
    assert _feat(home, "whatsapp_bridge") and not _feat(home, "whatsapp_send")
