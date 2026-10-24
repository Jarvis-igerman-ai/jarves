"""Auto-update (issue #17): the default follows the channel, an explicit choice wins, installs only at a quiet moment
through the SAME signed path (signed manifest, SHA256 + size), notices before/after, back-off and give-up, channel
respected, and nothing ever runs a real installer. Fake release server on loopback, fake clock, fake launcher."""
import json
import sys

import pytest
from release_fakes import BASE, FakeReleaseServer, Keys, installer_bytes, manifest, seed_legacy, snapshot

from jarves import auto_update, features, updates
from jarves.auto_update import GIVE_UP_AFTER, NOTICE_GRACE_S, AutoUpdater
from jarves.config import Config
from jarves.updates import UpdateChecker

OLD, NEW = "0.1.0.57", "3.0.0.1"


@pytest.fixture
def keys():
    return Keys()


@pytest.fixture
def server():
    s = FakeReleaseServer()
    yield s
    s.close()


_WARM: list = []


@pytest.fixture(autouse=True)
def windows(monkeypatch):
    if not _WARM:                                   # urllib imports parts of itself on the first request: do it under the real platform
        _WARM.append(1)
        w = FakeReleaseServer()
        try:
            w.http()(BASE + "/warm-up.json", {})
        finally:
            w.close()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(updates, "build_info", lambda: {"version": OLD})


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    seed_legacy(h)
    return h


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, home, server, keys, *, on=True, busy=()):
        self.home, self.server, self.keys = home, server, keys
        self.clock, self.notices, self.launched = Clock(), [], []
        self.on, self.busy = on, list(busy)
        self.au = AutoUpdater(home, checker=self.checker, enabled=lambda: self.on, busy=lambda: list(self.busy),
                              notify=self.notices.append, launch=self.launch, now=self.clock, current=lambda: OLD)

    def checker(self):
        return UpdateChecker(self.home, http=self.server.http(), current=OLD, base_url=BASE,
                             public_keys=[self.keys.public], now=self.clock)

    def launch(self, path, **kw):
        self.launched.append((path, kw))
        updates.launch_installer(path, popen=lambda argv, **k: None, **kw)

    def tick(self, advance=0):
        self.clock.t += advance
        return self.au.tick()

    def ready(self):                               # announce, then let the grace time pass
        assert self.tick() == "announced"
        self.clock.t += NOTICE_GRACE_S + 1
        self.server.status.pop("/latest.json", None)


def kinds(rig):
    return [n["kind"] for n in rig.notices]


# ---------------------------------------------------------------------- the default follows the channel
def test_beta_default_on_stable_default_off(home):
    f = features.Features(Config(), home)
    assert f.enabled("auto_update") is False and auto_update.is_enabled(Config(), home) is False
    updates.set_channel(home, Config(), "beta")
    f = features.Features(Config(), home)
    assert f.enabled("auto_update") is True and auto_update.is_enabled(Config(), home) is True
    updates.set_channel(home, Config(), "stable")
    assert features.Features(Config(), home).enabled("auto_update") is False


def test_explicit_choice_wins_over_channel_and_survives_upgrade(home):
    features.Features(Config(), home).set("auto_update", False, by="cli")
    updates.set_channel(home, Config(), "beta")
    assert features.Features(Config(), home).enabled("auto_update") is False            # beta default does not override
    updates.set_channel(home, Config(), "stable")
    features.Features(Config(), home).set("auto_update", True, by="cli")
    assert features.Features(Config(), home).enabled("auto_update") is True             # stable default does not override
    updates.set_channel(home, Config(), "beta")
    updates.set_channel(home, Config(), "stable")
    # an "upgrade" is a new process reading the same files: nothing is rewritten
    assert features.Features(Config(), home).enabled("auto_update") is True
    f = features.Features(Config(), home)
    assert f.status("auto_update")["enabled"] is True


def test_reset_goes_back_to_following_the_channel(home):
    updates.set_channel(home, Config(), "beta")
    features.Features(Config(), home).set("auto_update", False)
    features.Features(Config(), home).reset("auto_update")
    assert features.Features(Config(), home).enabled("auto_update") is True


def test_first_run_prompt_only_asks_and_never_switches_the_channel(home, server, keys):
    r = Rig(home, server, keys)
    r.tick()
    assert kinds(r)[0] == "choose" and "stable" in r.notices[0]["text_en"]
    assert not features.Features(Config(), home).enabled("beta_channel")               # still stable
    r.tick()
    assert kinds(r).count("choose") == 1                                               # asked once
    assert not r.au.status()["channel_chosen"]


def test_upgrade_with_a_chosen_channel_is_not_asked_and_keeps_it(home, server, keys):
    updates.set_channel(home, Config(), "beta")                                        # chosen before the upgrade
    r = Rig(home, server, keys)
    r.tick()
    assert "choose" not in kinds(r)
    assert features.Features(Config(), home).enabled("beta_channel")                   # the upgrade kept beta


def test_silent_install_path_never_changes_channel_or_switch(home, server, keys):
    server.publish(keys, NEW)
    u = UpdateChecker(home, http=server.http(), current=OLD, base_url=BASE, public_keys=[keys.public])
    u.check(force=True)
    r = u.prepare_install(approved=True)
    argv = []
    updates.launch_installer(r["installer"], verified=r, silent=True, restart=True, popen=lambda a, **k: argv.extend(a))
    assert not any("beta" in a.lower() or "channel" in a.lower() for a in argv)
    f = features.Features(Config(), home)
    assert not f.enabled("beta_channel") and not f.enabled("auto_update")


def test_choose_channel_checkbox_defaults_and_overrides(home):
    r = auto_update.choose_channel(Config(), home, "beta", True)                       # checkbox = beta default
    assert r == {"channel": "beta", "auto_update": True}
    assert "auto_update" not in json.loads((home / "features.json").read_text("utf-8")).get("enabled", {})   # still a default
    r = auto_update.choose_channel(Config(), home, "stable", True)                     # stable + ticked = explicit
    assert r["auto_update"] is True and features.Features(Config(), home).default("auto_update")[0] is False
    r = auto_update.choose_channel(Config(), home, "beta", False)
    assert r["auto_update"] is False


# ---------------------------------------------------------------------- on / off
def test_off_does_nothing_no_network(home, server, keys):
    server.publish(keys, NEW)
    r = Rig(home, server, keys, on=False)
    assert r.tick() == "off" and server.hits == [] and r.launched == []


def test_turning_on_needs_the_update_check(home):
    features.Features(Config(), home).set("update_check", False)
    with pytest.raises(updates.UpdateError) as e:
        auto_update.set_switch(Config(), home, True, "cli")
    assert e.value.code == "AUTO_UPDATE_NEEDS_CHECK"
    features.Features(Config(), home).set("update_check", True)
    auto_update.set_switch(Config(), home, True, "cli")
    assert auto_update.is_enabled(Config(), home)


# ---------------------------------------------------------------------- notices and the quiet moment
def test_notice_before_then_waits_then_installs_signed_then_notice_after(home, server, keys):
    exe = server.publish(keys, NEW)
    r = Rig(home, server, keys)
    assert r.tick() == "announced"
    n = [x for x in r.notices if x["kind"] == "before"][0]
    assert n["title_he"] and n["title_en"] and "now" in n["text_en"] and "later" in n["text_en"] and "off" in n["text_en"]
    assert "עכשיו" in n["text_he"] and r.launched == []
    assert r.tick(60) == "waiting_grace" and r.launched == []
    assert r.tick(NOTICE_GRACE_S) == "installing"
    path, kw = r.launched[0]
    assert kw["silent"] is True and kw["restart"] is True and kw["verified"]["signed"] is True
    assert open(path, "rb").read() == exe
    pend = json.loads((home / "updates" / "pending.json").read_text("utf-8"))
    assert pend["from"] == OLD and pend["to"] == NEW and updates.read_previous(home)["version"] == OLD   # backup + rollback kept
    # the new version starts and becomes healthy: the "after" notice, once
    r.au.current = lambda: NEW
    updates.note_start(home, NEW)
    updates.mark_healthy(home, NEW)
    assert r.au.after_start() == "after"
    a = [x for x in r.notices if x["kind"] == "after"]
    assert len(a) == 1 and "--rollback" in a[0]["text_en"] and "--rollback" in a[0]["text_he"]
    r.au.after_start()
    assert len([x for x in r.notices if x["kind"] == "after"]) == 1


@pytest.mark.parametrize("reason", ["voice_session", "listening", "pending_approval", "coding_agent", "quiet_hours"])
def test_never_installs_in_the_middle_of_use(home, server, keys, reason):
    server.publish(keys, NEW)
    r = Rig(home, server, keys, busy=[reason])
    r.ready()
    assert r.tick() == "busy" and r.launched == []
    assert reason in r.au.status()["busy"]
    r.busy.clear()
    assert r.tick(60) == "installing" and len(r.launched) == 1


def test_later_postpones_a_day_now_installs_at_once_off_turns_it_off(home, server, keys):
    server.publish(keys, NEW)
    r = Rig(home, server, keys)
    r.ready()
    r.au.answer("later")
    assert r.tick() == "snoozed" and r.tick(3600) == "snoozed" and r.launched == []
    assert r.tick(24 * 3600) == "installing"
    seen = []
    r.au.answer("off", set_switch=seen.append)
    assert seen == [False]


def test_update_now_is_the_owners_explicit_choice_even_when_busy(home, server, keys):
    server.publish(keys, NEW)
    r = Rig(home, server, keys, busy=["voice_session"])
    r.ready()
    assert r.tick() == "busy"
    r.au.answer("now")
    assert r.tick() == "installing"


# ---------------------------------------------------------------------- trust: signed only, newer only, channel
def test_unsigned_or_wrongly_signed_manifest_is_refused(home, server, keys):
    server.publish(keys, NEW, tamper=lambda m: m.update(sha256="0" * 64))              # edited after signing
    r = Rig(home, server, keys)
    assert r.tick() == "check_failed" and r.launched == []
    other = Keys()                                                                    # signed by a key the app does not trust
    server.publish(other, NEW)
    r = Rig(home, server, keys)
    assert r.tick() == "check_failed" and r.launched == []


def test_a_github_style_unsigned_release_is_never_auto_installed(home, server, keys):
    class Unsigned(UpdateChecker):
        def status(self):
            s = super().status()
            s.update(available=True, latest={"version": NEW, "source": "github", "asset": {}})
            return s
    r = Rig(home, server, keys)
    r.au.checker = lambda: Unsigned(home, http=server.http(), current=OLD, base_url=BASE, public_keys=[keys.public])
    server.publish(keys, NEW)
    assert r.tick() == "unsigned_refused" and r.launched == []


def test_older_or_same_version_is_refused(home, server, keys):
    server.publish(keys, "0.1.0.40")
    r = Rig(home, server, keys)
    assert r.tick() == "no_update"
    server.publish(keys, OLD)
    assert r.tick(NOTICE_GRACE_S * 2) in ("no_update",) and r.launched == []


def test_stable_ignores_beta_and_beta_ignores_stable(home, server, keys):
    exe = installer_bytes("4.1.0.5")
    env = keys.sign(manifest("4.1.0.5", exe, channel="beta", beta=1, display_version="4.1.0-beta.1"))
    server.files["/latest-beta.json"] = json.dumps(env).encode()
    server.files["/JARVES-Setup-4.1.0.5.exe"] = exe
    r = Rig(home, server, keys)                                                       # stable user
    assert r.tick() == "check_failed" and r.launched == []                             # no stable manifest, beta ignored
    assert "/latest-beta.json" not in server.hits
    updates.set_channel(home, Config(), "beta")                                       # owner switches: nothing installs
    assert r.launched == []
    r2 = Rig(home, server, keys)
    assert r2.tick() == "announced"
    assert r2.au.status()["target_display"] == "4.1.0-beta.1" and r2.au.status()["channel"] == "beta"
    server.files.pop("/latest-beta.json")
    server.publish(keys, "4.2.0.1")                                                    # a stable build: beta user ignores it
    r2.clock.t += 2 * 24 * 3600
    r2.server.files.pop("/latest-beta.json", None)
    assert r2.tick() in ("check_failed", "no_update") and r2.launched == []


def test_channel_switch_restarts_the_announcement(home, server, keys):
    server.publish(keys, NEW)
    r = Rig(home, server, keys)
    r.ready()
    st = r.au.state()
    st["channel"] = "beta"                                                            # state says it was announced for beta
    auto_update._save(home, st)
    assert r.tick() == "announced"                                                    # stable now: asked again, not installed


# ---------------------------------------------------------------------- failure safety, back-off, give-up
@pytest.mark.parametrize("failure", ["wrong_sha", "http_500", "truncated"])
def test_failed_update_changes_nothing_and_backs_off(home, server, keys, failure):
    exe = server.publish(keys, NEW)
    r = Rig(home, server, keys)
    r.ready()
    before = snapshot(home)
    path = f"/JARVES-Setup-{NEW}.exe"
    if failure == "wrong_sha":
        server.files[path] = bytes([exe[0] ^ 1]) + exe[1:]
    elif failure == "http_500":
        server.status[path] = 500
    else:
        server.cut[path] = 100
    assert r.tick() == "failed" and r.launched == []
    assert snapshot(home) == before and updates.read_previous(home) == {}
    assert not (home / "updates" / "pending.json").exists()
    assert r.tick(60) == "backoff"                                                    # no retry loop
    assert r.au.status()["failures"] == 1 and r.au.status()["next_try"]


def test_gives_up_after_a_few_tries_tells_once_and_a_newer_version_restarts(home, server, keys):
    server.publish(keys, NEW)
    server.status[f"/JARVES-Setup-{NEW}.exe"] = 500
    r = Rig(home, server, keys)
    r.ready()
    codes = []
    for _ in range(GIVE_UP_AFTER):
        codes.append(r.tick())
        r.clock.t += 13 * 3600                                                        # past every back-off
    assert codes[-1] == "gave_up" and kinds(r).count("gave_up") == 1
    assert r.tick(24 * 3600) == "gave_up" and kinds(r).count("gave_up") == 1 and r.launched == []
    server.status.clear()
    server.publish(keys, "3.0.0.2")                                                   # a newer version: a fresh start
    assert r.tick(24 * 3600) == "announced"
    r.au.answer("now")
    assert r.tick() == "installing"


def test_setup_that_never_ran_counts_as_a_failure_and_the_old_version_works(home, server, keys):
    server.publish(keys, NEW)
    r = Rig(home, server, keys)
    r.ready()
    assert r.tick() == "installing"
    assert updates.note_start(home, OLD)["state"] == "not_applied"                    # setup cancelled; OLD starts again
    assert r.au.after_start() != "after"
    assert r.au.status()["failures"] == 1 and r.tick(60) == "backoff"


def test_logs_hold_codes_and_versions_only(home, server, keys, caplog):
    server.publish(keys, NEW)
    r = Rig(home, server, keys)
    r.ready()
    r.tick()
    text = (home / "updates" / "auto_update.json").read_text("utf-8")
    assert "sha256" not in text.lower() and str(home) not in text


# ---------------------------------------------------------------------- the Core's quiet-moment probe + dashboard API
def test_core_busy_reads_the_core_state():
    class Eng:
        def __init__(self, quiet=False):
            self.q = quiet

        def quiet_now(self):
            return self.q

        def paused(self):
            return False

    class Appr:
        def __init__(self, p):
            self.p = p

        def has_pending(self):
            return self.p

    class Core:
        live, listening, stopping = None, False, False
        approvals = Appr(False)
        chat = type("C", (), {"turns": {}})()
        ops = type("O", (), {"alerts": Eng()})()
    c = Core()
    assert auto_update.core_busy(c) == []
    c.live, c.listening, c.approvals = object(), True, Appr(True)
    c.chat = type("C", (), {"turns": {"x": 1}})()
    c.ops = type("O", (), {"alerts": Eng(True)})()
    assert set(auto_update.core_busy(c)) == {"voice_session", "listening", "pending_approval", "chat_turn", "quiet_hours"}


def test_dashboard_api_switch_answer_and_channel(home):
    class Ctx:
        cfg = Config()

        def __init__(self, h):
            self.home = h
    ctx = Ctx(home)
    st, body = auto_update.api_auto(ctx, None, None)
    assert st == 200 and body["enabled"] is False and body["default_from_channel"] == "stable"
    st, body = auto_update.api_auto_channel(ctx, None, {"channel": "beta", "auto": True})
    assert st == 200 and body["enabled"] is True and body["default_from_channel"] == "beta" and body["channel_chosen"]
    st, body = auto_update.api_auto_set(ctx, None, {"enabled": False})
    assert st == 200 and body["enabled"] is False and body["default_from_channel"] is None      # explicit now
    assert auto_update.api_auto_set(ctx, None, {"enabled": "yes"})[0] == 409
    assert auto_update.api_auto_answer(ctx, None, {"choice": "bogus"})[0] == 409
    assert auto_update.api_auto_channel(ctx, None, {"channel": "nightly"})[0] == 409


def test_cli_update_auto(home, monkeypatch, capsys):
    from jarves import cli
    monkeypatch.setenv("JARVES_HOME", str(home))
    monkeypatch.setattr(sys, "platform", "linux")           # the CLI imports half the app; none of it is Windows-only here
    monkeypatch.setattr(updates, "build_info", lambda: {"version": OLD, "channel": "main", "commit": None, "built": None})
    assert cli.main(["update", "auto", "status"]) == 0
    assert "Automatic updates: off" in capsys.readouterr().out
    assert cli.main(["update", "auto", "on"]) == 0
    assert "Automatic updates: on" in capsys.readouterr().out
    assert cli.main(["update", "auto", "off"]) == 0
    assert "Automatic updates: off" in capsys.readouterr().out


def test_a_health_rollback_by_setup_tells_the_owner_once_in_both_languages(home, server, keys):
    """#164: setup put the previous program back after the new Core never became ready; the old Core says so once."""
    r = Rig(home, server, keys)
    (home / "updates").mkdir(exist_ok=True)
    (home / "updates" / "health.json").write_text(json.dumps(
        {"status": "rolled_back", "from": OLD, "to": NEW, "at": 1, "notified": False}), "utf-8")
    assert r.au.after_start() == "rolled_back"
    n = [x for x in r.notices if x["kind"] == "rolled_back"]
    assert len(n) == 1 and OLD in n[0]["text_en"] and "data" in n[0]["text_en"] and n[0]["text_he"] and n[0]["title_he"]
    r.au.after_start()
    assert len([x for x in r.notices if x["kind"] == "rolled_back"]) == 1
    assert updates.take_health_notice(home) is None


def test_a_failed_health_rollback_says_how_to_go_back_by_hand(home, server, keys):
    r = Rig(home, server, keys)
    (home / "updates").mkdir(exist_ok=True)
    (home / "updates" / "health.json").write_text(json.dumps({"status": "rollback_failed", "to": NEW}), "utf-8")
    assert r.au.after_start() == "rollback_failed"
    n = r.notices[-1]
    assert "jarvis update --rollback" in n["text_en"] and "jarvis update --rollback" in n["text_he"]
