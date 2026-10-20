"""Profiles (Issue #17, profiles.py): several users + a TEST profile. Real Cores in temp dirs (in-process and as real
processes), two profiles side by side, no cross-leak of memory / conversations / costs / settings / keys / sign-ins.
Offline, silent: no microphone, speaker, camera, network or real processes beyond the Cores started here."""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import queue
import re
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from jarves import backup, capabilities, cli, profiles
from jarves import features as feat
from jarves.config import Config, load_config, state_dir
from jarves.memory import MemoryStore
from jarves.runtime import autostart as au
from jarves.runtime.lock import AlreadyRunning, InstanceLock
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio

from conftest import FakeSTT, FakeTTS, open_face

HE = re.compile(r"[֐-׿]")
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def base(home, monkeypatch):
    """JARVES_HOME = a temp dir (the default profile); no profile chosen; no inherited keys."""
    keep = {k: os.environ.get(k) for k in (profiles.ENV, profiles.SECRETS_FROM_ENV, "OPENAI_API_KEY",
                                           "TELEGRAM_BOT_TOKEN", "OPENAI_ADMIN_KEY", "HOME_ASSISTANT_TOKEN")}
    for k in keep:
        os.environ.pop(k, None)
    yield home
    for k, v in keep.items():           # `--profile` / load_secrets write os.environ directly: never leak to other tests
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(profiles, "SPAWN", lambda name, args: calls.append((name, list(args))))
    return calls


# ---------------------------------------------------------------------- names, places, ports
def test_default_profile_keeps_todays_location(base):
    assert state_dir() == base
    assert profiles.current_name() == "default" and profiles.home_for("default") == base
    rows = profiles.list_profiles()
    assert [r["name"] for r in rows] == ["default"] and rows[0]["current"] and rows[0]["active"]
    assert profiles.info(base) == {**profiles.info(base), "name": "default", "test": False}


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "CON", "nul", "x" * 25, "profiles", "-x", "דנה", "a b"])
def test_names_are_validated(base, bad):
    with pytest.raises(profiles.ProfileError):
        profiles.create(bad)


def test_profiles_get_distinct_free_ports_and_conflicts_are_found(base):
    a = profiles.create("alice")
    blocked = {a["port"] + 1}
    port = profiles.allocate_port(bindable=lambda p: p not in blocked)       # a port in use by something else
    assert port not in blocked and port != a["port"] and port != 8765
    t = profiles.create("test")
    ports = {8765, a["port"], t["port"]}
    assert len(ports) == 3
    for name, p in (("alice", a["port"]), ("test", t["port"])):
        cfg, _ = load_config(profiles.home_for(name) / "config.toml")
        assert cfg.link.port == p and profiles.port_conflicts(name, p) == []
    assert profiles.port_conflicts("alice", 8765) == ["default"]


def test_test_profile_has_fake_offline_defaults_and_its_own_budget(base):
    r = profiles.create("test")
    assert r["test"] and r["kind"] == "test"
    home = profiles.home_for("test")
    cfg, _ = load_config(home / "config.toml")
    assert (cfg.ai.provider, cfg.voice.mode, cfg.music.provider) == ("offline", "off", "none")
    assert cfg.ai.daily_request_limit == 50 and cfg.assistant_name == "JARVES TEST"
    f = feat.Features(cfg, home)
    for fid in ("camera", "gmail_send", "telegram", "autostart", "tray_icon", "web_search", "dashboard_lan"):
        if fid in feat.CATALOG:
            assert not f.enabled(fid), fid
    from jarves import usage
    assert usage.get_settings(home)["api_monthly_budget_usd"] == profiles.TEST_MONTHLY_BUDGET_USD
    assert (home / "setup_done").exists()                                    # no first-run wizard for tests
    assert "TEST PROFILE" in (home / "workspace" / "USER.md").read_text(encoding="utf-8")
    v = profiles.view(home)
    assert v["test"] and "TEST PROFILE" in v["banner"]["en"] and HE.search(v["banner"]["he"])
    assert profiles.view(base)["banner"] is None
    assert profiles.create("qa", test=True)["test"]                             # --test makes any name a test profile


def test_select_env_use_and_delete_move_the_active_profile(base, monkeypatch):
    profiles.create("alice")
    profiles.create("test")
    profiles.select("alice")
    assert os.environ[profiles.ENV] == "alice" and state_dir() == base / "profiles" / "alice"
    os.environ.pop(profiles.ENV)
    profiles.use("test")
    assert profiles.current_name() == "test" and state_dir() == base / "profiles" / "test"
    with pytest.raises(profiles.ProfileError):
        profiles.delete("test", "tset")                                       # typed confirmation must match
    assert profiles.exists("test")
    profiles.delete("test", "test")
    assert not (base / "profiles" / "test").exists() and profiles.current_name() == "default"
    with pytest.raises(profiles.ProfileError):
        profiles.delete("default", "default")
    with pytest.raises(profiles.ProfileError):
        profiles.select("nosuch")


def test_delete_refuses_a_profile_whose_core_runs(base):
    profiles.create("alice")
    lock = InstanceLock(profiles.home_for("alice"))
    lock.acquire("test")
    try:
        with pytest.raises(profiles.ProfileError, match="running"):
            profiles.delete("alice", "alice")
        assert profiles.exists("alice")
    finally:
        lock.release()
    profiles.delete("alice", "alice")
    assert not profiles.exists("alice")


def test_one_instance_lock_per_profile(base):
    profiles.create("alice")
    a, t = InstanceLock(profiles.home_for("alice")), InstanceLock(base)
    a.acquire("x")
    t.acquire("x")                      # another profile's Core may run at the same time
    try:
        with pytest.raises(AlreadyRunning):
            InstanceLock(profiles.home_for("alice")).acquire("x")
    finally:
        a.release()
        t.release()


# ---------------------------------------------------------------------- copying: settings yes, secrets never
def _fill_default(base: Path) -> None:
    (base / "secrets.env").write_text("OPENAI_API_KEY=sk-test-parent-0000000000000000\n", encoding="utf-8")
    for d, f in (("google", "token.json"), ("chatgpt", "auth.json"), ("telegram", "pair.json")):
        (base / d).mkdir()
        (base / d / f).write_text('{"refresh_token": "1//secret"}', encoding="utf-8")
    (base / "link_token").write_text("link-secret", encoding="utf-8")
    (base / "faces.db").write_bytes(b"biometric")
    (base / "store.db").write_bytes(b"conversations")
    (base / "dashboard_devices.json").write_text("[]", encoding="utf-8")
    (base / "config.toml").write_text('assistant_name = "Max"\n[link]\nport = 8765\n', encoding="utf-8")
    (base / "features.json").write_text(json.dumps({"version": 1, "enabled": {"music": False}}), encoding="utf-8")
    (base / "wake").mkdir()
    (base / "wake" / "hey_max.npz").write_bytes(b"npz")
    (base / "identity.json").write_text(json.dumps({"display_name": "Max", "wake": {
        "model": str((base / "wake" / "hey_max.npz").resolve()), "phrase": "hey max", "threshold": 0.4}}),
        encoding="utf-8")
    ws = base / "workspace"
    ws.mkdir()
    for n in ("SOUL.md", "AGENTS.md", "USER.md", "MEMORY.md"):
        (ws / n).write_text(f"# {n} of the owner", encoding="utf-8")
    m = MemoryStore(base / "memory.db")
    m.add("the owner likes black coffee")
    m.set_setting("override:voice.voice", "cedar")
    m.bump_usage("ai_requests", 7)
    m.close()


def test_create_from_copies_settings_but_never_secrets_or_data(base):
    _fill_default(base)
    r = profiles.create("kid", from_="default")
    kid = profiles.home_for("kid")
    for gone in ("secrets.env", "google", "chatgpt", "telegram", "link_token", "faces.db", "store.db",
                 "dashboard_devices.json", "workspace/USER.md", "workspace/MEMORY.md"):
        assert not (kid / gone).exists(), gone
    assert (kid / "workspace" / "SOUL.md").read_text(encoding="utf-8") == "# SOUL.md of the owner"
    cfg, _ = load_config(kid / "config.toml")
    assert cfg.assistant_name == "Max" and cfg.link.port == r["port"] != 8765
    ident = json.loads((kid / "identity.json").read_text(encoding="utf-8"))
    assert Path(ident["wake"]["model"]).parent == (kid / "wake").resolve()       # its own copy, not the owner's file
    m = MemoryStore(kid / "memory.db")
    try:
        assert m.count() == 0 and m.get_setting("override:voice.voice") == "cedar" and m.usage_today("ai_requests") == 0
    finally:
        m.close()
    raw = (kid / "memory.db").read_bytes()
    assert b"black coffee" not in raw                                          # deleted AND vacuumed
    assert "secrets.env" in " ".join(r["never_copied"])


def test_copy_memory_and_copy_secrets_only_when_asked(base):
    _fill_default(base)
    profiles.create("twin", from_="default", copy_memory=True)
    twin = profiles.home_for("twin")
    m = MemoryStore(twin / "memory.db")
    try:
        assert [x["text"] for x in m.list()] == ["the owner likes black coffee"]
    finally:
        m.close()
    assert (twin / "workspace" / "USER.md").exists() and not (twin / "secrets.env").exists()
    profiles.create("keys", from_="default", copy_secrets=True)
    assert "sk-test-parent" in (profiles.home_for("keys") / "secrets.env").read_text(encoding="utf-8")
    assert not (profiles.home_for("keys") / "google").exists()                  # sign-ins are never copied
    with pytest.raises(profiles.ProfileError):
        profiles.create("bad", copy_memory=True)                                 # needs --from


def test_non_default_profile_never_inherits_the_parents_keys(base, monkeypatch):
    profiles.create("alice")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-should-not-leak")
    assert profiles.prepare_env("default") == [] and os.environ["OPENAI_API_KEY"]
    assert profiles.prepare_env("alice") == ["OPENAI_API_KEY"] and "OPENAI_API_KEY" not in os.environ
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-should-not-leak")
    assert cli.main(["--profile", "alice", "profile", "show"]) == 0
    assert "OPENAI_API_KEY" not in os.environ                                   # dropped, alice has no secrets.env
    (profiles.home_for("alice") / "secrets.env").write_text("OPENAI_API_KEY=sk-alice-own\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-should-not-leak")
    assert cli.main(["--profile", "alice", "profile", "show"]) == 0
    assert os.environ["OPENAI_API_KEY"] == "sk-alice-own"                         # its OWN key
    monkeypatch.setenv(profiles.SECRETS_FROM_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-explicit")
    assert profiles.prepare_env("alice") == [] and os.environ["OPENAI_API_KEY"] == "sk-explicit"
    child = profiles._child_env("alice")
    assert "OPENAI_API_KEY" not in child and child[profiles.ENV] == "alice"


def test_default_backup_and_diagnostics_leave_other_profiles_out(base):
    profiles.create("alice")
    MemoryStore(profiles.home_for("alice") / "memory.db").close()
    (profiles.home_for("alice") / "workspace").mkdir()
    (profiles.home_for("alice") / "workspace" / "USER.md").write_text("alice private", encoding="utf-8")
    take, _ = backup.plan(base)
    assert not [rel for _, rel in take if rel.startswith("profiles") or rel == "profiles.json"]
    take, _ = backup.plan(profiles.home_for("alice"))
    assert "workspace/USER.md" in [rel for _, rel in take]
    from jarves import diagbundle
    assert not [x for x in diagbundle._inventory(base) if x["name"].startswith("profiles/")]


def test_export_is_a_verified_archive_without_credentials(base, tmp_path_factory):
    profiles.create("alice")
    alice = profiles.home_for("alice")
    (alice / "secrets.env").write_text("OPENAI_API_KEY=sk-alice\n", encoding="utf-8")
    MemoryStore(alice / "memory.db").close()
    out = tmp_path_factory.mktemp("exp") / "alice.zip"
    assert cli.main(["profile", "export", "alice", "--out", str(out)]) == 0
    names = zipfile.ZipFile(out).namelist()
    assert "manifest.json" in names and "data/memory.db" in names
    assert not [n for n in names if "secrets" in n or "token" in n]
    assert backup.verify(out)["ok"]


def test_autostart_entry_and_background_command_per_profile(base, monkeypatch):
    assert au.core_command()[1:] == ["-m", "jarves", "run", "--background"] and au.entry_name("JARVES") == "JARVES"
    profiles.create("alice")
    monkeypatch.setenv(profiles.ENV, "alice")
    assert au.core_command(["--show"])[1:] == ["-m", "jarves", "--profile", "alice", "run", "--background", "--show"]
    assert au.entry_name("JARVES") == "JARVES-alice" and au.entry_name("jarves.service") == "jarves-alice.service"
    reg = {}

    class Reg(dict):
        def set(self, k, v):
            reg[k] = v

        def get(self, k, d=None):
            return reg.get(k, d)

        def delete(self, k):
            return reg.pop(k, None) is not None
    a = au.WindowsAutostart(Reg())
    a.enable()
    assert list(reg) == ["JARVES-alice"] and "--profile alice" in reg["JARVES-alice"]


# ---------------------------------------------------------------------- CLI
def test_cli_profile_commands(base, capsys, monkeypatch):
    assert cli.main(["profile", "create", "bob"]) == 0
    assert cli.main(["profile", "create", "bob"]) == 2                        # exists
    assert cli.main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    assert "bob" in out and "default" in out
    assert cli.main(["--profile", "nosuch", "status"]) == 2
    assert "no profile 'nosuch'" in capsys.readouterr().err
    assert cli.main(["--profile", "bob", "profile", "show"]) == 0
    assert "profile bob" in capsys.readouterr().out
    os.environ.pop(profiles.ENV)                    # `--profile` holds for that process (and its children) only
    assert cli.main(["profile", "delete", "bob", "--confirm", "nope"]) == 2 and profiles.exists("bob")
    monkeypatch.setattr("builtins.input", lambda prompt="": "bob")             # typed confirmation at the prompt
    assert cli.main(["profile", "delete", "bob"]) == 0 and not profiles.exists("bob")
    assert cli.main(["profile", "delete", "default", "--confirm", "default"]) == 2
    assert cli.main(["profile", "create", "test"]) == 0 and cli.main(["profile", "use", "test"]) == 0
    assert profiles.current_name() == "test"


def test_root_help_lists_the_profile_commands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "--profile" in out and "profile" in out


def test_run_refuses_a_port_another_profile_uses(base, capsys):
    profiles.create("alice")
    a = type("A", (), {"background": False, "desktop": False})()
    c = Config()
    c.link.port = profiles.link_port(profiles.home_for("alice"))
    assert cli.cmd_run(a, c, base) == 2                                       # default on alice's port
    assert "also uses" in capsys.readouterr().err


# ---------------------------------------------------------------------- feature, guide, matrix, Hebrew
def test_registered_feature_guide_bundle_wizard_group_and_matrix():
    from jarves import guides, setup_wizard
    assert "profiles" in feat.CATALOG and feat.CATALOG["profiles"].risk == "medium"
    assert capabilities.problems("profiles") == []
    assert "profiles" in [fid for g in setup_wizard.groups() for fid in g[3]]
    assert setup_wizard.BUNDLE["profiles"] and all(ok for ok, _ in (c() for c in setup_wizard.BUNDLE["profiles"]))
    g = guides.for_feature("profiles")
    assert g is not None and HE.search(g.title_he) and HE.search(g.what_he)
    assert "jarves.profiles" in __import__("jarves.dashboard", fromlist=["x"]).PLUGIN_MODULES


def test_hebrew_texts_for_every_fixed_profile_text():
    t = capabilities.view("profiles")["targets"]
    fixed = [feat.CATALOG["profiles"].about] + [t[k][f] for k in t for f in ("evidence", "blocker") if t[k][f]]
    for s in fixed:
        assert s in profiles.HE and HE.search(profiles.HE[s]), s
    try:
        from jarves import hebrew                                             # once SW-V3 P0.2b is on main
    except ImportError:
        return
    for s in fixed:
        assert HE.search(hebrew.he(s) or ""), s


def test_dashboard_js_has_the_switcher_banner_and_page():
    js = (SRC / "jarves" / "dashboard_static" / "profiles.js").read_text(encoding="utf-8")
    html = (SRC / "jarves" / "dashboard_static" / "index.html").read_text(encoding="utf-8")
    assert "PAGES.profiles" in js and "profileBanner" in js and "profileChip" in js and ".innerHTML" not in js
    assert '/static/profiles.js' in html and 'data-page="profiles"' in html
    assert html.index("profiles.js") < html.index("shell.js")


# ---------------------------------------------------------------------- dashboard
def _req(d, method, path, body=None, cookie=True):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=60)
    h = {"Host": f"127.0.0.1:{d.port}"}
    if cookie:
        h["Cookie"] = f"{d.access.cookie}={d.url.split('t=')[1]}"
    if method != "GET":
        h["X-Jarves"] = "1"
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data and data[:1] in b"{[" else data)


def test_dashboard_per_profile_login_page_and_management(base, spawned):
    from jarves import dashboard
    profiles.create("alice")
    alice = profiles.home_for("alice")
    MemoryStore(base / "memory.db").add("only the owner knows this")
    da = dashboard.Dashboard(alice, Config()).start()
    dd = dashboard.Dashboard(base, Config()).start()
    try:
        assert dd.access.cookie == "jarves_dash" and da.access.cookie == "jarves_dash_alice"
        c = http.client.HTTPConnection("127.0.0.1", da.port, timeout=60)
        c.request("GET", "/?t=" + da.url.split("t=")[1], headers={"Host": f"127.0.0.1:{da.port}"})
        assert c.getresponse().getheader("Set-Cookie").startswith("jarves_dash_alice=")
        c.close()
        # the owner's login cookie does not open alice's dashboard (and the other way round)
        h = {"Host": f"127.0.0.1:{da.port}", "Cookie": f"jarves_dash={dd.url.split('t=')[1]}"}
        c = http.client.HTTPConnection("127.0.0.1", da.port, timeout=60)
        c.request("GET", "/api/profiles", headers=h)
        assert c.getresponse().status == 403
        c.close()
        st, v = _req(da, "GET", "/api/profiles")
        assert st == 200 and v["current"] == "alice" and {p["name"] for p in v["profiles"]} == {"default", "alice"}
        assert all("home" not in p for p in v["profiles"])                     # names, never another profile's folder
        st, mem = _req(da, "GET", "/api/memory")
        assert st == 200 and not [i for i in mem["items"] if "owner knows" in i["text"]]
        st, r = _req(dd, "POST", "/api/profiles", {"name": "zed", "from": "default", "copy_memory": False})
        assert st == 200 and r["profile"]["name"] == "zed" and profiles.exists("zed")
        st, r = _req(dd, "POST", "/api/profiles", {"name": "keys", "from": "default", "copy_secrets": True})
        assert st == 400 and not profiles.exists("keys")
        st, r = _req(dd, "POST", "/api/profiles/zed/delete", {"confirm": "zd"})
        assert st == 400 and profiles.exists("zed")
        st, r = _req(dd, "POST", "/api/profiles/zed/delete", {"confirm": "zed"})
        assert st == 200 and not profiles.exists("zed")
        st, r = _req(da, "POST", "/api/profiles/alice/delete", {"confirm": "alice"})
        assert st == 409 and profiles.exists("alice")                          # not its own profile
        st, r = _req(dd, "POST", "/api/profiles/alice/open", {})
        assert st == 200 and spawned == [("alice", ["dashboard"])]
        st, r = _req(dd, "POST", "/api/profiles/alice/use", {})
        assert st == 200 and profiles.active() == "alice"
    finally:
        da.stop()
        dd.stop()


# ---------------------------------------------------------------------- real Cores
def _core_cfg(home: Path) -> Config:
    cfg, _ = load_config(home / "config.toml")
    cfg.voice.greeting = False
    cfg.voice.greeting_delay_s = 0
    return cfg


def _make_core(home: Path):
    from jarves.app import Core
    return Core(_core_cfg(home), home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(),
                console_logs=False, use_real_audio=False)


async def test_two_cores_side_by_side_no_cross_leak(base):
    profiles.create("alice")
    profiles.create("test")
    ha, ht = profiles.home_for("alice"), profiles.home_for("test")
    a, t = _make_core(ha), _make_core(ht)
    await a.start()
    await t.start()
    try:
        assert a.cfg.link.port != t.cfg.link.port
        fa, ft = await open_face(a), await open_face(t)
        await fa.send("status.request")
        await ft.send("status.request")
        sa = await fa.wait("status", lambda p: "profile" in p)
        stt = await ft.wait("status", lambda p: "profile" in p)
        assert sa["profile"]["name"] == "alice" and not sa["profile"]["test"]
        assert stt["profile"] == {**stt["profile"], "name": "test", "test": True}
        # memory, budget, switches and identity of one profile never reach the other
        a.memory.add("alice's secret plan")
        a.memory.bump_usage("ai_requests", 5)
        a.features.set("greeting", True, by="test")
        a.identity.set_names(display_name="AliceBot")
        assert t.memory.count() == 0 and t.memory.usage_today("ai_requests") == 0
        assert t.snapshot()["ai_requests_today"] == 0 and a.snapshot()["ai_requests_today"] == 5
        assert "greeting" not in json.loads((ht / "features.json").read_text(encoding="utf-8"))["enabled"]
        assert t.identity.names()["display_name"] == "JARVES TEST"
        assert a.link.token != t.link.token
        r = await t.registry.call("recall", {"query": "secret plan"}, origin="user")
        assert "secret plan" not in r.message
        # the voice tool lists both, and the test Core knows it is the test profile
        r = await t.registry.call("profile_list", {}, origin="model")
        assert r.ok and r.data["current"] == "test" and {"alice", "test", "default"} <= {p["name"] for p in r.data["profiles"]}
        await fa.ws.close()
        await ft.ws.close()
    finally:
        await a.stop()
        await t.stop()
    # each profile's conversations / costs store is its own file
    assert (ha / "store.db").exists() and (ht / "store.db").exists()
    assert not (base / "memory.db").exists() or MemoryStore(base / "memory.db").count() == 0


async def test_voice_switch_needs_on_screen_approval(base, spawned):
    profiles.create("test")
    core = _make_core(base)
    await core.start()
    try:
        face = await open_face(core)
        r = await core.registry.call("profile_switch", {"name": "nosuch"}, origin="model")
        assert not r.ok and r.code == "PROFILE_UNKNOWN" and not spawned
        r = await core.registry.call("profile_switch", {"name": "default"}, origin="model")
        assert r.code == "PROFILE_ALREADY"
        # the screen says no
        call = asyncio.create_task(core.registry.call("profile_switch", {"name": "test"}, origin="model"))
        req = await face.wait("confirm.request")
        assert "test" in req["prompt"] and HE.search(req["prompt"])
        await face.send("confirm.reply", {"id": req["id"], "approved": False})
        r = await call
        assert not r.ok and r.code == "PROFILE_SWITCH_NOT_APPROVED" and not spawned
        assert profiles.active() == "default" and not core.stop_event.is_set()
        # the owner asks again (a NEW turn: approvals.py denies a just-denied identical request without asking
        # until the owner speaks), and the screen says yes: the test profile's Core starts, this one quits
        core.approvals.note_user_turn()
        call = asyncio.create_task(core.registry.call("profile_switch", {"name": "test"}, origin="model"))
        req = await face.wait("confirm.request")
        await face.send("confirm.reply", {"id": req["id"], "approved": True})
        r = await call
        assert r.ok and r.code == "PROFILE_SWITCHING"
        assert spawned == [("test", ["run", "--background", "--show"])] and profiles.active() == "test"
        await asyncio.wait_for(core.stop_event.wait(), 10)
        await face.ws.close()
    finally:
        await core.stop()


async def test_voice_switch_without_a_screen_is_refused(base, spawned):
    profiles.create("test")
    core = _make_core(base)
    await core.start()
    try:
        r = await core.registry.call("profile_switch", {"name": "test"}, origin="model")   # no face connected
        assert not r.ok and r.code == "PROFILE_SWITCH_NOT_APPROVED" and not spawned
    finally:
        await core.stop()


def _lines(stream, q: queue.Queue) -> None:
    for line in iter(stream.readline, ""):
        q.put(line)
    q.put(None)


def _wait_for(q: queue.Queue, needle: str, timeout: float) -> list[str]:
    seen = []
    while True:
        line = q.get(timeout=timeout)          # blocks on the next line (event-driven), raises Empty on timeout
        if line is None:
            raise AssertionError("process ended before: " + needle + "\n" + "".join(seen[-30:]))
        seen.append(line)
        if needle in line:
            return seen


def test_two_real_core_processes_side_by_side(base):
    """`jarvis --profile alice run` and `jarvis --profile test run` as real processes at the same time: separate
    locks, ports and status; a second alice is refused (exit 3); stopping alice leaves test running."""
    profiles.create("test")
    profiles.create("alice")
    alice = profiles.home_for("alice")
    for k, v in (("voice", ("mode", '"off"')), ("ai", ("provider", '"offline"')), ("music", ("provider", '"none"'))):
        cli.set_toml_value(alice / "config.toml", k, v[0], v[1])               # silent: no mic, speaker or cloud
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", profiles.ENV)}
    env.update(JARVES_HOME=str(base), PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    procs, queues = {}, {}
    try:
        for name in ("test", "alice"):
            p = subprocess.Popen([sys.executable, "-m", "jarves", "--profile", name, "run"], env=env, cwd=str(base),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace")
            q: queue.Queue = queue.Queue()
            threading.Thread(target=_lines, args=(p.stderr, q), daemon=True).start()
            procs[name], queues[name] = p, q
        for name in procs:
            _wait_for(queues[name], "CORE_READY", 120)
        rows = {r["name"]: r for r in profiles.list_profiles()}
        assert rows["alice"]["running"] and rows["test"]["running"] and not rows["default"]["running"]
        for name in ("alice", "test"):
            st = cli._ws_call(profiles.home_for(name), profiles.link_port(profiles.home_for(name)),
                              {"type": "status.request", "payload": {}}, "status")
            assert st["profile"]["name"] == name and st["profile"]["test"] == (name == "test")
        again = subprocess.run([sys.executable, "-m", "jarves", "--profile", "alice", "run"], env=env, cwd=str(base),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        assert again.returncode == cli.EXIT_ALREADY_RUNNING and "profile alice" in again.stderr
        stop = subprocess.run([sys.executable, "-m", "jarves", "--profile", "alice", "stop"], env=env, cwd=str(base),
                              capture_output=True, text=True, timeout=60)
        assert stop.returncode == 0
        assert procs["alice"].wait(timeout=60) == 0
        st = cli._ws_call(profiles.home_for("test"), profiles.link_port(profiles.home_for("test")),
                          {"type": "status.request", "payload": {}}, "status")
        assert st is not None and st["profile"]["name"] == "test"
        # nothing of alice's run is in the test profile (logs, conversations and settings stay in each folder)
        assert (alice / "logs" / "jarves.log").exists() and "alice" not in \
            (profiles.home_for("test") / "logs" / "jarves.log").read_text(encoding="utf-8", errors="replace")
    finally:
        for name, p in procs.items():
            if p.poll() is None:
                subprocess.run([sys.executable, "-m", "jarves", "--profile", name, "stop"], env=env, cwd=str(base),
                               capture_output=True, timeout=60)
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
