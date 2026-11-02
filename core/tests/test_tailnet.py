"""Owner decisions 2026-10-21 (V5): Tailscale detection and tailnet-only bind (fake interfaces and a fake `tailscale`
command: no network, no real Tailscale), phone approvals (every risk level except typed codes, audited, revoke-all),
the 24 hour "full text on request" switch and the new-install defaults (client delegation, privacy on)."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from jarves import approvals as appr
from jarves import config, config_edit, dashboard, logs, mobile, privacy, tailnet
from jarves.config import Config

from test_mobile_access import FakeCore, admin, dashboard_open, free_port, next_request, pair, req

TAIL_IP = "100.101.102.103"
TAIL_NAME = "pc.tail1234.ts.net"


def fake_cli(certs=True, status_ok=True, record=None):
    """A fake `tailscale` command: answers ip / status / cert from the arguments, writes cert files like the real one."""
    def run(cmd):
        if record is not None:
            record.append(cmd)
        sub = cmd[1]
        if sub == "ip":
            return TAIL_IP + "\n"
        if sub == "status":
            return json.dumps({"Self": {"DNSName": TAIL_NAME + "."}}) if status_ok else None
        if sub == "cert":
            if not certs:
                return None
            open(cmd[cmd.index("--cert-file") + 1], "w").write("CERT")
            open(cmd[cmd.index("--key-file") + 1], "w").write("KEY")
            return ""
        return None
    return run


# ---------------------------------------------------------------- detection
def test_tailnet_range():
    assert tailnet.is_tailnet_ip("100.64.0.1") and tailnet.is_tailnet_ip("100.127.255.254")
    assert not tailnet.is_tailnet_ip("100.128.0.1") and not tailnet.is_tailnet_ip("192.168.1.5")
    assert not tailnet.is_tailnet_ip("8.8.8.8") and not tailnet.is_tailnet_ip("garbage")


def test_detect_from_interfaces_without_the_command():
    d = tailnet.detect(addresses=["192.168.1.9", TAIL_IP], which=lambda _n: None)
    assert d["present"] and d["ip"] == TAIL_IP and d["name"] is None and not d["cli"] and not d["certs_possible"]
    assert d["state"] == "ready"


def test_detect_not_installed_and_not_connected():
    assert tailnet.detect(addresses=["192.168.1.9"], which=lambda _n: None)["state"] == "not-installed"
    calls = []
    run = lambda cmd: calls.append(cmd) or None          # the command exists but knows no address
    d = tailnet.detect(addresses=["192.168.1.9"], run=run, which=lambda _n: "/bin/tailscale")
    assert d["state"] == "not-connected" and not d["present"] and calls == [["/bin/tailscale", "ip", "-4"]]


def test_detect_with_the_command_gives_the_name():
    d = tailnet.detect(addresses=[TAIL_IP], run=fake_cli(), which=lambda _n: "/bin/tailscale")
    assert d["name"] == TAIL_NAME and d["certs_possible"]
    d = tailnet.detect(addresses=["192.168.1.9"], run=fake_cli(), which=lambda _n: "/bin/tailscale")
    assert d["present"] and d["ip"] == TAIL_IP                           # the command knew it, the adapter list did not


def test_detection_never_installs_or_logs_in(monkeypatch):
    seen = []
    tailnet.detect(addresses=[TAIL_IP], run=fake_cli(record=seen), which=lambda _n: "/bin/tailscale")
    assert {c[1] for c in seen} <= {"ip", "status"}                      # read-only questions only (no up / login / install)


# ---------------------------------------------------------------- TLS choice
def test_tls_prefers_tailscale_cert_then_selfsigned_then_none(home):
    info = tailnet.detect(addresses=[TAIL_IP], run=fake_cli(), which=lambda _n: "/bin/tailscale")
    got, src = tailnet.choose_tls(home, info, run=fake_cli(), which=lambda _n: "/bin/tailscale")
    assert src == "tailscale" and got[0].name == "tailnet.crt" and got[0].is_file()
    # certificates not enabled for the tailnet: the command fails, nothing is written, no self-signed file yet
    (home / "other").mkdir()
    home2 = home / "other"
    got, src = tailnet.choose_tls(home2, info, run=fake_cli(certs=False), which=lambda _n: "/bin/tailscale")
    assert (got, src) == (None, "none")
    mobile.make_selfsigned(home2, "192.168.1.5")                         # the existing self-signed path
    got, src = tailnet.choose_tls(home2, info, run=fake_cli(certs=False), which=lambda _n: "/bin/tailscale")
    assert src == "selfsigned" and got[0].name == "cert.pem"


# ---------------------------------------------------------------- network gate and bind
def test_tailnet_clients_reach_only_phone_paths_and_only_when_tailscale_is_used():
    n = dict(lan=False, mobile_on=True)
    assert mobile.network_ok("100.90.1.2", path="/m/", tailnet=True, **n)
    assert mobile.network_ok("100.90.1.2", path="/api/m/chat", tailnet=True, **n)
    assert not mobile.network_ok("100.90.1.2", path="/api/sessions", tailnet=True, **n)      # the dashboard stays PC-only
    assert not mobile.network_ok("100.90.1.2", path="/m/", tailnet=False, **n)               # Tailscale not in use
    assert not mobile.network_ok("100.90.1.2", path="/m/", lan=False, mobile_on=False, tailnet=True)
    assert not mobile.network_ok("8.8.8.8", path="/m/", tailnet=True, **n)


def tail_dash(home, **kw):
    info = {"present": True, "ip": "127.0.0.1", "name": TAIL_NAME, "state": "ready"}   # loopback stands in for the tailnet NIC
    kw.setdefault("lan_host", "127.0.0.1")                  # no routing probe (the offline test guard blocks it)
    return dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, tailnet=info,
                               mobile_bridge=FakeCore().bridge(), **kw)


def test_tailnet_only_binds_the_tailnet_address_never_everything(home):
    d = tail_dash(home, bind_tailnet_only=True, tls_source="tailscale").start()
    try:
        assert d.httpd.server_address[0] == "127.0.0.1" != "0.0.0.0"
        assert d.mobile.bind == "127.0.0.1" and d.mobile.host == TAIL_NAME     # no LAN address in this mode
        assert d.access.lan_host is None
    finally:
        d.stop()


def test_tailnet_only_without_a_tailnet_address_refuses_to_start(home):
    with pytest.raises(OSError):
        dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, bind_tailnet_only=True,
                            tailnet={"present": False})
    with pytest.raises(OSError):                                               # and never falls back to 0.0.0.0
        dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, bind_tailnet_only=True)


def test_default_still_binds_the_lan_and_tailnet_host_names_are_accepted(home):
    info = {"present": True, "ip": TAIL_IP, "name": TAIL_NAME, "state": "ready"}
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host="192.168.50.7", tailnet=info,
                            mobile_bridge=FakeCore().bridge()).start()
    try:
        assert d.httpd.server_address[0] == "0.0.0.0"
        for host in (TAIL_NAME, TAIL_IP, "192.168.50.7"):
            st, _, _ = req(d, "GET", "/api/m/status", headers={"Host": f"{host}:{d.port}"})
            assert st == 401, host                                             # reached the phone API (no token)
        st, _, _ = req(d, "GET", "/api/m/status", headers={"Host": f"evil.example:{d.port}"})
        assert st == 403
    finally:
        d.stop()


def test_pairing_code_offers_the_tailnet_url_and_qr(home):
    d = tail_dash(home, tls_source="tailscale").start()
    try:
        d.mobile.scheme = "https"
        st, code = admin(d, "POST", "/api/mobile/code", {})
        assert st == 200
        assert code["tailnet_url"] == f"https://{TAIL_NAME}:{d.port}/m/#c={code['code']}"
        assert code["tailnet_qr"] and code["qr"]
        info = admin(d, "GET", "/api/mobile")[1]["tailnet"]
        assert info["state"] == "ready" and info["tls_source"] == "tailscale" and info["host"] == TAIL_NAME
        assert info["url"] == f"https://{TAIL_NAME}:{d.port}/m/"
    finally:
        d.stop()


def test_no_tailscale_means_no_tailnet_fields(home):
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host="192.168.50.7").start()
    try:
        assert "tailnet_url" not in admin(d, "POST", "/api/mobile/code", {})[1]
        assert admin(d, "GET", "/api/mobile")[1]["tailnet"]["state"] == "unused"
    finally:
        d.stop()


def test_bind_setting_roundtrip_and_audit(home):
    d = tail_dash(home).start()
    try:
        assert admin(d, "GET", "/api/mobile")[1]["bind_tailnet_only"] is False
        st, body = admin(d, "POST", "/api/mobile/settings", {"bind_tailnet_only": True})
        assert st == 200 and body["bind_tailnet_only"] is True and body["phone_approvals"] is False
        assert admin(d, "POST", "/api/mobile/settings", {"bind_tailnet_only": "x"})[0] == 400
        assert admin(d, "POST", "/api/mobile/settings", {})[0] == 400
        assert "bind_changed" in {e["event"] for e in admin(d, "GET", "/api/mobile/audit")[1]["entries"]}
    finally:
        d.stop()


# ---------------------------------------------------------------- phone approvals
@pytest.fixture
def srv(home):
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host="192.168.50.7",
                            mobile_bridge=FakeCore().bridge()).start()
    yield d
    d.stop()


@pytest.mark.parametrize("risk", ["low", "medium", "high"])
async def test_phone_may_approve_every_risk_level_and_it_is_audited(srv, home, risk):
    tok, dev, _ = pair(srv)
    await asyncio.to_thread(admin, srv, "POST", "/api/mobile/settings", {"phone_approvals": True})
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Do the secret thing?", title="Secret title", kind="message", risk=risk))
    a = await next_request(svc)
    st, body, _ = await asyncio.to_thread(req, srv, "POST", f"/api/m/approvals/{a.id}", token=tok, body={"approved": True})
    assert st == 200 and await asyncio.wait_for(task, 30) is True
    raw = (home / mobile.AUDIT_FILE).read_text(encoding="utf-8")
    rec = [json.loads(x) for x in raw.splitlines() if '"phone_approval"' in x][0]
    assert rec["risk"] == risk and rec["decision"] == "approve" and rec["device"] == dev
    assert "Secret" not in raw and "secret thing" not in raw                    # audited without content


async def test_phone_approval_needs_the_approvals_scope(srv, home):
    tok, dev, _ = pair(srv)
    await asyncio.to_thread(admin, srv, "POST", "/api/mobile/settings", {"phone_approvals": True})
    admin(srv, "POST", f"/api/mobile/devices/{dev}/scopes", {"scopes": ["chat"]})
    svc = appr.ApprovalService(home)
    dashboard_open(home)
    task = asyncio.create_task(svc.ask("Install?", title="Install", kind="message", risk="high"))
    a = await next_request(svc)
    st, body, _ = await asyncio.to_thread(req, srv, "POST", f"/api/m/approvals/{a.id}", token=tok, body={"approved": True})
    assert st == 403 and not task.done()
    task.cancel()


def test_revoke_all_stops_every_phone_at_once(srv):
    toks = [pair(srv, f"Phone {i}")[0] for i in range(3)]
    assert all(req(srv, "GET", "/api/m/me", token=t)[0] == 200 for t in toks)
    st, body = admin(srv, "POST", "/api/mobile/revoke-all", {})
    assert st == 200 and body["revoked"] == 3
    assert all(req(srv, "GET", "/api/m/me", token=t)[0] == 401 for t in toks)
    assert "all_devices_revoked" in {e["event"] for e in admin(srv, "GET", "/api/mobile/audit")[1]["entries"]}
    assert admin(srv, "POST", "/api/mobile/revoke-all", {})[1]["revoked"] == 0


def test_phone_cannot_use_the_owner_routes(srv):
    tok, _, _ = pair(srv)
    for path in ("/api/mobile/revoke-all", "/api/mobile/settings"):
        st, _, _ = req(srv, "POST", path, token=tok, body={})
        assert st in (403, 404)                                               # phone tokens open /api/m/* only
    assert req(srv, "GET", "/api/m/me", token=tok)[0] == 200                  # and nothing was revoked or changed
    assert srv.mobile.settings()["phone_approvals"] is False


# ---------------------------------------------------------------- full text on request (24 hours)
@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(logs, "_clock", lambda: now[0])
    yield now
    logs._until = 0.0
    logs.set_privacy(True)


def test_full_text_lasts_24_hours_then_reverts_by_itself(home, clock):
    logs.setup(home / "logs", "INFO", console=False, privacy=True)
    cfg = Config()
    cfg.log.privacy = True
    ctx = SimpleNamespace(home=home, cfg=cfg, config_path=home / "config.toml")
    st = privacy.set_full_text(ctx, True)
    assert st["privacy"] is False and st["full_text_active"] and st["full_text_until"]
    assert abs(st["full_text_until"] - (time.time() + 24 * 3600)) < 5
    assert logs.privacy() is False
    clock[0] += 24 * 3600 - 60
    logs._poll_privacy(force=True)
    assert logs.privacy() is False                                            # one minute left
    clock[0] += 120
    logs._poll_privacy(force=True)
    assert logs.privacy() is True and logs.full_text_until() == 0.0
    flag = json.loads((home / "logs" / logs.PRIVACY_FILE).read_text(encoding="utf-8"))
    assert flag["privacy"] is True and "until" not in flag                   # every other process follows the file
    assert privacy.state(ctx)["full_text_active"] is False and ctx.cfg.log.privacy is True
    text = (home / "logs" / "jarves.log").read_text(encoding="utf-8")
    assert "PRIVACY_FULL_TEXT_ON" in text and "PRIVACY_FULL_TEXT_EXPIRED" in text


def test_full_text_off_now_and_other_process_sees_expiry(home, clock):
    logs.setup(home / "logs", "INFO", console=False, privacy=True)
    ctx = SimpleNamespace(home=home, cfg=Config(), config_path=home / "config.toml")
    ctx.cfg.log.privacy = True
    privacy.set_full_text(ctx, True)
    assert privacy.set_full_text(ctx, False)["privacy"] is True and logs.privacy() is True
    privacy.set_full_text(ctx, True)
    flag = home / "logs" / logs.PRIVACY_FILE
    d = json.loads(flag.read_text(encoding="utf-8"))
    d["until"] = time.time() - 5                                              # a stale window in the file (other process)
    flag.write_text(json.dumps(d), encoding="utf-8")
    assert privacy._window_until(home) == 0.0                                 # CLI / dashboard read: already over


def test_full_text_http_route_and_cli(home, clock, capsys):
    logs.setup(home / "logs", "INFO", console=False, privacy=True)
    cfg = Config()
    cfg.log.privacy = True
    d = dashboard.Dashboard(home, cfg, config_path=home / "config.toml").start()
    try:
        st, body = admin(d, "POST", "/api/privacy/full-text", {"full_text": True})
        assert st == 200 and body["full_text_active"] and "ends at" in body["full_text_note_en"]
        assert admin(d, "POST", "/api/privacy/full-text", {"full_text": "yes"})[0] == 400
        from jarves import cli
        note = cli._privacy_note(cfg, home)
        assert note and "until" in note.lower()
        assert admin(d, "POST", "/api/privacy/full-text", {"full_text": False})[1]["privacy"] is True
        assert cli._privacy_note(cfg, home) == ""
    finally:
        d.stop()
    assert privacy.cmd_privacy(SimpleNamespace(action="full-text", state="on"), cfg, home) == 0
    assert "ends at" in capsys.readouterr().out
    assert privacy.cmd_privacy(SimpleNamespace(action="status"), cfg, home) == 0
    assert "ends at" in capsys.readouterr().out                               # another process sees the same window
    assert privacy.cmd_privacy(SimpleNamespace(action="full-text", state="off"), cfg, home) == 0
    assert "Privacy is on" in capsys.readouterr().out


# ---------------------------------------------------------------- new-install defaults (OD-12, OD-15)
def test_new_install_defaults(tmp_path):
    cfg, path = config.load_config(tmp_path / "config.toml")
    assert cfg.log.privacy is True and cfg.ai.delegation_mode == "client"
    from jarves.features import Features
    assert Features(cfg, tmp_path).enabled("client_delegation") is True


def test_existing_config_is_never_changed(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[voice]\nmode = \"live\"\n", encoding="utf-8")
    cfg, _ = config.load_config(p)
    assert cfg.log.privacy is False and cfg.ai.delegation_mode == "server"


def test_first_save_keeps_new_install_values(tmp_path):
    p = tmp_path / "config.toml"
    config_edit.apply(p, {"log.level": "DEBUG"})
    cfg, _ = config.load_config(p)
    assert cfg.log.level == "DEBUG" and cfg.log.privacy is True and cfg.ai.delegation_mode == "client"


def test_cli_phone_commands(home, capsys, monkeypatch):
    from jarves import phone_cli, tailnet
    monkeypatch.setattr(tailnet, "local_addresses", lambda: [])        # no hostname lookup (offline test guard)
    cfg = Config()
    ns = lambda **k: SimpleNamespace(json=False, **k)
    assert phone_cli.cmd_phone(ns(action="approvals", state="on"), cfg, home) == 0
    assert mobile.MobileAccess(home).settings()["phone_approvals"] is True
    assert phone_cli.cmd_phone(ns(action="bind", where="tailnet"), cfg, home) == 0
    assert mobile.MobileAccess(home).settings()["bind_tailnet_only"] is True
    assert phone_cli.cmd_phone(ns(action="revoke-all"), cfg, home) == 0
    assert phone_cli.cmd_phone(ns(action="status"), cfg, home) == 0
    out = capsys.readouterr().out
    assert "Tailscale" in out and "listening: nothing" in out      # switch OFF: never claims a phone listener


def test_cli_phone_status_names_the_listener_only_when_on(home, capsys, monkeypatch):
    from jarves import phone_cli, tailnet
    monkeypatch.setattr(tailnet, "local_addresses", lambda: [])
    monkeypatch.setattr(tailnet, "detect", lambda: {"state": "not-installed", "ip": "", "name": "", "certs_possible": False})
    monkeypatch.setattr(phone_cli, "status_view", lambda cfg, h: {
        "enabled": True, "phone_approvals": False, "bind_tailnet_only": False, "paired_phones": 0,
        "tailscale": {"state": "not-installed", "ip": "", "name": "", "certs_possible": False}})
    assert phone_cli.cmd_phone(SimpleNamespace(json=False, action="status"), Config(), home) == 0
    assert "listening: home network" in capsys.readouterr().out
